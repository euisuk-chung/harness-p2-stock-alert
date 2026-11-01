"""번호표 — 같은 알림을 두 번 보내지 않는다. **이 프로젝트가 소유한다.**

> 이 파일은 알림 폴더에만 있다. 블로그는 사람이 PR 을 합치고, 문의 분류는 큐에 덮어쓰기라
> 두 번 해도 한 번과 같다. 하나에서만 필요한 것을 셋이 같이 쓰는 차단 장치에 넣지 않는다.

## 왜 필요한가

요청 폴더의 `state.json` 은 **요청 하나**의 상태만 안다. *"이 알림을 이미 보냈나"* 는
**요청 사이**에 걸친 질문이다 — 5분마다 새 요청이 열린다. 둘을 섞으면 안 된다.

## 단계가 셋이다 — **초판은 둘이었고, 그게 틀렸다**

> ⚠️ 초판은 `claimed → done` 둘뿐이었다.
> 멈춘 실행을 이어받았더니 **알림이 두 번 나갔다.**
>
>     번호표 받음 → 발송 → **죽음** → 이어받기
>     이어받은 쪽은 손에 든 번호표 목록을 그대로 다시 보냈다.
>
> `claimed` 만으로는 *"보내기 전"* 과 *"보내는 중"* 을 구분할 수 없었다.

    CLAIMED   찍었다. 아직 안 보냈다        → 보내도 된다
    SENDING   **보내는 중이다**             → 죽었다면 **보냈는지 모른다**
    DONE      보냈고 기록도 됐다            → 하지 않는다

`SENDING` 을 **보내기 직전에** 기록한다. 그래서 그 뒤에 죽으면
다음 요청이든 같은 요청을 다시 승인한 이어받기든 **`SENDING` 을 보고 멈춘다.**

## 전이는 CAS 다 — 파일 교체만으로는 부족했다

> ⚠️ **2026-08-31 재수정.** `os.replace` 는 원자적이지만 **읽고-비교하고-쓰는 전체**는
> 아니었다. 리뷰가 같은 key·같은 토큰으로 `begin_send` 를 **두 번** 성공시켰다.
>
>     A: CLAIMED 확인 → B: CLAIMED 확인 → A: SENDING 쓰기 → B: SENDING 쓰기
>     둘 다 보낸다
>
> 토큰은 **남의 실행**을 막지만 **같은 실행의 중복 워커**는 막지 못한다.

지금은 key 별 잠금 파일(`O_EXCL`)을 잡고, 그 안에서 다시 읽어 기대 상태를 확인한 뒤 쓴다.

    transition(key, token, expected=CLAIMED, next=SENDING) → 이겼다 | 늦었다

둘 중 하나만 이긴다.

⚠️ 잠금 주인이 죽으면 파일이 남는다. **타임아웃은 그걸 풀어 주지 않는다** —
매번 기다렸다 실패하게 할 뿐이다. 그래서 오래된 잠금은 회수한다. `_lock` 머리말.

## 소유권 토큰

`complete` · `release` 는 **claim 할 때 받은 토큰**을 요구한다.
없으면 다른 실행이 남의 claim 을 끝냈다고 하거나 지워 버릴 수 있다.

특히 `release` 는 **`CLAIMED` 에서만** 된다. `SENDING` 은 못 푼다 —
이미 나갔을지 모르는 것을 풀면 다음 실행이 또 보낸다.

## ⚠️ 그래도 exactly-once 가 아니다

`SENDING` 을 기록한 뒤 발송 전에 죽어도 우리는 구분할 수 없다.
**at-most-once 를 고른다** — 애매하면 보내지 않고 사람에게 넘긴다.

진짜 해법은 우리 쪽이 아니라 **외부 API 가 멱등키를 받는 것**이다.
그게 없으면 transactional outbox 나 대사(reconciliation)가 필요하다.
"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import tempfile
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any


def claims_root(default: Path) -> Path:
    """claim 기록이 놓이는 곳. `ALERT_CLAIMS_DIR` 가 있으면 그쪽 — 실습·테스트가 깨끗한 저장소를 쓰려고.

    호출할 때마다 읽는다. 모듈 상수로 두면 테스트가 env 를 바꿔도 소용없다.
    """
    raw = os.environ.get("ALERT_CLAIMS_DIR")
    return Path(raw) if raw else default


class Phase(str, Enum):
    CLAIMED = "claimed"
    SENDING = "sending"
    DONE = "done"


class Claim(str, Enum):
    """`claim()` 의 답."""

    CLAIMED = "claimed"     # 내가 처음이다. 진행한다
    HELD = "held"           # 누가 찍었는데 아직 안 보냈다
    SENDING = "sending"     # **보내는 중이었다.** 보냈는지 모른다 — 사람이 봐야 한다
    DONE = "done"           # 이미 끝났다
    CORRUPT = "corrupt"     # 기록이 깨졌다. 보수적으로 멈춘다


class NotOwner(Exception):
    """남의 claim 을 건드리려 했다."""


class Stale(Exception):
    """기대한 상태가 아니었다. **다른 워커가 먼저 옮겼다.**"""


class Contended(Exception):
    """잠금을 얻지 못했다."""


@dataclass(frozen=True)
class Record:
    key: str
    phase: str
    token: str
    claimed_at: str
    claimed_by: str
    sending_at: str = ""
    completed_at: str = ""
    effect_ref: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key, "phase": self.phase, "token": self.token,
            "claimed_at": self.claimed_at, "claimed_by": self.claimed_by,
            "sending_at": self.sending_at, "completed_at": self.completed_at,
            "effect_ref": self.effect_ref,
        }


class IdempotencyStore:
    """key 하나에 파일 하나. 파일이 있으면 누가 이미 찍은 것이다.

    ⚠️ **로컬 파일이다.** 같은 기계에서만 통한다.
    hosted runner 는 매번 새 기계라 여기 쓴 것이 다음 실행에 남지 않는다.
    """

    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        """key → 파일 이름. **해시를 쓴다.**

        초판은 허용되지 않은 문자를 `_` 로 바꿨는데, 그러면
        `a:b` 와 `a_b` 가 **같은 파일**이 된다. 서로 다른 알림이 서로를 막는다.
        원본 key 는 레코드 안에 그대로 남긴다.
        """
        digest = hashlib.sha256(key.encode("utf-8")).hexdigest()[:32]
        return self.root / f"{digest}.json"

    # ---------- 세 단계 ----------

    def claim(self, key: str, by: str) -> tuple[Claim, Record | None]:
        """내 것으로 찍는다. **원자적이다.**

        `O_CREAT | O_EXCL` 은 파일이 이미 있으면 실패한다. 검사하고 만드는 게 아니라
        **만들기를 시도해서 결과를 본다** — 그 사이에 낄 틈이 없다.
        """
        path = self._path(key)
        record = Record(key=key, phase=Phase.CLAIMED.value,
                        token=secrets.token_hex(16), claimed_at=_now(), claimed_by=by)
        try:
            fd = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            existing = self.read(key)
            if existing is None:
                # 파일은 있는데 못 읽는다. **보수적으로 멈춘다.**
                return Claim.CORRUPT, None
            return {
                Phase.DONE.value: Claim.DONE,
                Phase.SENDING.value: Claim.SENDING,
            }.get(existing.phase, Claim.HELD), existing

        with os.fdopen(fd, "w", encoding="utf-8") as fp:
            json.dump(record.to_dict(), fp, ensure_ascii=False, indent=2)
        return Claim.CLAIMED, record

    @contextmanager
    def _lock(self, key: str, timeout: float = 5.0, stale_after: float = 60.0):
        """key 하나에 대한 잠금. **읽기-비교-쓰기 전체를 감싼다.**

        `O_EXCL` 로 잠금 파일을 만든다. 이미 있으면 잠깐 기다렸다 다시 시도한다.

        ## ⚠️ 잠금 주인이 죽으면 파일이 남는다

        > **2026-08-31 재수정.** 초판 주석은 *"타임아웃이 있으니 영영 막히지 않는다"* 였다.
        > **반대다.** 타임아웃은 영구 잠금을 풀어 주지 않고 **매번 기다렸다 실패하게** 한다.
        > 리뷰가 orphan `.lock` 을 만들어 획득이 계속 실패하는 것을 확인했다.

        그래서 잠금 파일에 **누가·언제** 잡았는지 적고, `stale_after` 를 넘긴 것은
        **한 번만** 회수한다. 회수도 원자적으로 한다 — 이름을 바꿔 본다.

        `os.replace` 는 원자적이라 **둘이 동시에 회수해도 하나만 성공**한다.
        진짜 해법은 OS 가 프로세스 종료 시 자동으로 푸는 advisory lock 이지만,
        그건 플랫폼마다 달라서 실습에서는 이 정도로 둔다.
        """
        path = self.root / f"{self._path(key).stem}.lock"
        deadline = time.monotonic() + timeout
        while True:
            try:
                fd = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.write(fd, json.dumps(
                    {"by": os.getpid(), "at": time.time()}).encode("utf-8"))
                break
            except FileExistsError:
                if self._reclaim_stale(path, stale_after):
                    continue          # 회수했다. 다시 잡아 본다
                if time.monotonic() > deadline:
                    raise Contended(
                        f"잠금을 얻지 못했다: {key} ({timeout}초)\n"
                        f"  {path.name} 을 누가 잡고 있다. {stale_after}초가 지나면 회수된다.\n"
                        f"  그 전에 확인하려면 그 파일을 열어 본다 — 누가·언제 잡았는지 적혀 있다."
                    )
                time.sleep(0.01)
        try:
            # 임계 구역 동안 fd 를 **열어 둔다.** 일찍 닫으면 우리 잠금을
            # 다른 프로세스가 지워 버릴 틈이 생긴다.
            yield
        finally:
            os.close(fd)
            # Windows 는 다른 프로세스가 연 파일을 못 지운다. 잠깐 기다렸다 다시 한다.
            for _ in range(50):
                try:
                    path.unlink(missing_ok=True)
                    break
                except PermissionError:
                    time.sleep(0.01)

    def _reclaim_stale(self, path: Path, stale_after: float) -> bool:
        """오래된 잠금을 **한 번만** 회수한다. 회수했으면 True.

        이름 바꾸기가 원자적이라 둘이 동시에 시도해도 하나만 성공한다.
        진 쪽은 그냥 다시 기다린다.

        ## ⚠️ 못 읽는 잠금을 **오래된 것으로 보면 안 된다**

        > **2026-09-01 재수정.** CI(리눅스)가 `test_idempotency.py` §6 에서
        > **다섯 중 둘이 이기는 것**을 잡았다. 윈도우에서는 안 나온다.

        `_lock` 은 `O_EXCL` 로 파일을 만든 **다음 줄**에서 내용을 쓴다.
        그 사이 잠금 파일은 **비어 있다.** 초판은 그 틈에 끼어든 쪽이

            raw.get("at", 0) → 0 → held_for = time.time() - 0 ≈ 17억 초

        를 계산해 **방금 잡힌 잠금을 1970년부터 잡고 있던 것**으로 판정했다.
        `except` 의 `held_for = stale_after + 1` 도 같은 방향으로 틀렸다 —
        *"아직 안 쓰였다"* 와 *"주인이 죽었다"* 를 구분하지 못한다.

        그래서 잠금을 훔치고, 둘이 임계 구역에 같이 들어가고, 둘 다
        `CLAIMED` 를 보고 둘 다 `SENDING` 을 쓴다. **알림이 두 번 나간다.**

        > 윈도우에서 안 터진 건 고쳐져서가 아니다. `os.replace` 가 열린 파일의
        > 이름을 못 바꿔 **마지막 단계에서 우연히 막혔을** 뿐이다.
        > POSIX 는 열린 파일도 rename 된다. **로컬 통과가 정답이 아니었다.**

        지금은 내용을 못 읽으면 **파일 시각(mtime)으로 나이를 잰다.** 방금 만든
        잠금은 나이가 0 이라 살아 있는 것으로 보고, 주인이 죽어 영영 빈 채로
        남은 잠금은 시간이 지나면 회수된다. 두 경우가 갈린다.
        """
        try:
            raw = json.loads(path.read_text(encoding="utf-8") or "{}")
            at = float(raw["at"])
        except (OSError, json.JSONDecodeError, ValueError, TypeError, KeyError):
            # 아직 안 쓰였거나 반쯤 쓰였다. 파일이 생긴 시각으로 나이를 잰다.
            try:
                at = path.stat().st_mtime
            except OSError:
                return False        # 사라졌다 — 남이 이미 치웠다
        held_for = time.time() - at

        if held_for < stale_after:
            return False

        grave = path.with_suffix(f".stale-{secrets.token_hex(4)}")
        try:
            os.replace(str(path), str(grave))   # ← 둘이 하면 하나만 이긴다
        except (FileNotFoundError, PermissionError):
            return False
        grave.unlink(missing_ok=True)
        return True

    def transition(self, key: str, token: str, *, expected: Phase, to: Phase,
                   **fields: Any) -> Record:
        """**기대한 상태일 때만** 다음 상태로 옮긴다 (compare-and-set).

        잠금 안에서 다시 읽는다. 그래서 두 워커가 동시에 불러도 **하나만 이긴다.**
        """
        with self._lock(key):
            current = self._owned(key, token)
            if current.phase != expected.value:
                raise Stale(
                    f"기대한 상태가 아니다: {key}\n"
                    f"  기대 {expected.value} · 실제 {current.phase}\n"
                    f"  다른 워커가 먼저 옮겼을 수 있다. **다시 보내지 않는다.**"
                )
            moved = Record(**{**current.to_dict(), "phase": to.value, **fields})
            _atomic_write(self._path(key), moved.to_dict())
            return moved

    def begin_send(self, key: str, token: str) -> Record:
        """**보내기 직전에** 부른다. `CLAIMED` 일 때만 넘어간다.

        이미 `SENDING` 이면 `Stale` 이다 — 다른 워커가 보내고 있다는 뜻이므로
        **우리는 보내지 않는다.**
        """
        return self.transition(key, token, expected=Phase.CLAIMED, to=Phase.SENDING,
                               sending_at=_now())

    def complete(self, key: str, token: str, effect_ref: str) -> Record:
        """효과가 나갔다고 기록한다. **발송 직후에 부른다.**"""
        return self.transition(key, token, expected=Phase.SENDING, to=Phase.DONE,
                               completed_at=_now(), effect_ref=effect_ref)

    def release(self, key: str, token: str) -> None:
        """claim 을 푼다. **아직 안 보냈다고 확신할 때만.**

        `SENDING` 이나 `DONE` 은 풀 수 없다. 이미 나갔을지 모르는 것을 풀면
        다음 실행이 또 보낸다. 확인과 삭제도 **잠금 안에서** 한다.
        """
        with self._lock(key):
            current = self._owned(key, token)
            if current.phase != Phase.CLAIMED.value:
                raise ValueError(
                    f"'{current.phase}' 상태는 풀 수 없다: {key}\n"
                    f"  이미 나갔을 수 있다. 실제 발송 기록과 대사한 뒤 사람이 정한다."
                )
            self._path(key).unlink(missing_ok=True)

    def _owned(self, key: str, token: str) -> Record:
        current = self.read(key)
        if current is None:
            raise KeyError(f"찍지 않은 key 다: {key}")
        if current.token != token:
            raise NotOwner(
                f"남의 claim 이다: {key}\n"
                f"  '{current.claimed_by}' 가 찍었다. 토큰이 맞아야 건드릴 수 있다."
            )
        return current

    # ---------- 보기 ----------

    def read(self, key: str) -> Record | None:
        return self._read_path(self._path(key))

    @staticmethod
    def _read_path(path: Path) -> Record | None:
        if not path.exists():
            return None
        try:
            return Record(**json.loads(path.read_text(encoding="utf-8")))
        except (json.JSONDecodeError, ValueError, TypeError):
            return None

    def records(self) -> list[Record]:
        return [r for p in sorted(self.root.glob("*.json")) if (r := self._read_path(p))]

    def corrupt(self) -> list[Path]:
        """읽을 수 없는 기록. **숨기지 않는다.**

        claim 은 이걸 보고 보수적으로 멈추는데, 사람 목록에서 사라지면
        아무도 손을 못 댄다. 막으면서 안 보여 주는 것이 가장 나쁘다.
        """
        return [p for p in sorted(self.root.glob("*.json")) if self._read_path(p) is None]

    def stuck(self) -> list[Record]:
        """끝나지 않은 것들. **사람이 대사해야 하는 목록이다.**

        `SENDING` 이 특히 위험하다 — 나갔는지 안 나갔는지 우리는 모른다.
        """
        return [r for r in self.records()
                if r.phase in (Phase.CLAIMED.value, Phase.SENDING.value)]


def _atomic_write(path: Path, payload: dict) -> None:
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".idem-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fp:
            json.dump(payload, fp, ensure_ascii=False, indent=2)
            fp.flush()
            os.fsync(fp.fileno())
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
