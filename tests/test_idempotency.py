"""**같은 알림이 두 번 나가지 않는가.** 이 프로젝트의 핵심 시험이다.

    uv run python tests/test_idempotency.py

말로 하지 않고 **줄 수로 확인한다.** 보낸 알림은 outbox/sent.jsonl 에 한 줄씩 붙으므로 세면 된다.
모델을 부르지 않는다 — 문구는 저장된 답(samples/reference/)을 쓰고, 나머지는 실제 스크립트 그대로다.

## ⚠️ 흉내 낸 경로를 시험하면 흉내가 옳은지만 알게 된다

이 과정의 알림 시험을 처음 만들었을 때는 `claim → send → complete` 를 흉내 내는 도우미를 두 번 불렀다.
도우미는 통과했는데 진짜 흐름은 멈춘 뒤 이어받을 때 알림을 두 번 보냈다.
그래서 §4·§5·§7 은 **실제 스크립트**를 부른다 — 요청 열기(scripts/open_request.py) ·
검수를 부르는 차단 장치(.claude/hooks/run_review.py) · 전달(scripts/deliver.py = scripts/approve.sh 가 부르는 것).
시험마다 이 폴더를 임시 폴더에 복사해서 부르므로 이 폴더의 claims/·outbox/·requests/ 는 건드리지 않는다.

## 동시 전이도 시험한다

`claim` 의 원자성만 보고 **전이의 원자성**을 안 보면 at-most-once 가 다시 넓어진다.
§6 이 다섯 프로세스를 같은 순간에 출발시킨다.

## 죽을 수 있는 구간이 셋이다

    claim 뒤 · begin_send 전   효과 없음.        풀고 다시 하면 된다
    begin_send 뒤 · 발송 전    효과 없음인데     **우리는 모른다** → 사람에게
    발송 뒤 · complete 전      **효과 있음**      → 사람에게

뒤의 둘을 구분할 방법이 없다. **at-most-once 를 고른다** — 애매하면 안 보낸다.
"""

from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "lib"))

from _utf8 import force_utf8                       # noqa: E402
from idempotency import Claim, IdempotencyStore, NotOwner, Phase  # noqa: E402
from sink import Sink                              # noqa: E402

IGNORE = shutil.ignore_patterns("requests", "claims", "outbox", "logs", ".tick.lock", "__pycache__", ".git")

failures: list[str] = []


def ok(label: str, cond: bool) -> None:
    if not cond:
        failures.append(label)
    print(f"   {'OK  ' if cond else 'FAIL'} {label}")


def raises(label: str, exc_type, fn) -> None:
    try:
        fn()
    except exc_type:
        ok(label, True)
        return
    except Exception as exc:  # noqa: BLE001
        failures.append(f"{label} — 다른 예외: {type(exc).__name__}: {exc}")
        print(f"   FAIL {label} — 다른 예외: {type(exc).__name__}")
        return
    failures.append(f"{label} — **예외가 안 났다**")
    print(f"   FAIL {label} — 예외가 안 났다")


KEY = "alert:AAPL:above-200:2026-08-31"


def fresh():
    tmp = tempfile.TemporaryDirectory()
    root = Path(tmp.name)
    return tmp, IdempotencyStore(root / "claims"), Sink(root / "sent.jsonl")


# ---------------------------------------------------------------- 1

def store_contract() -> None:
    print("1. 저장소 계약")
    tmp, store, _ = fresh()

    verdict, rec = store.claim(KEY, by="run-1")
    ok("처음 찍으면 내 것이다", verdict is Claim.CLAIMED)
    ok("토큰이 발급된다", bool(rec.token))

    again, _ = store.claim(KEY, by="run-2")
    ok("두 번째는 못 찍는다", again is Claim.HELD)

    raises("**남의 토큰 없이 끝낼 수 없다**", NotOwner,
           lambda: store.complete(KEY, "가짜토큰", "eff"))
    raises("**남의 토큰 없이 풀 수 없다**", NotOwner,
           lambda: store.release(KEY, "가짜토큰"))

    store.begin_send(KEY, rec.token)
    ok("보내는 중으로 넘어간다", store.read(KEY).phase == Phase.SENDING.value)

    sending, _ = store.claim(KEY, by="run-3")
    ok("**보내는 중이면 SENDING 을 돌려준다** (HELD 가 아니다)", sending is Claim.SENDING)

    raises("**보내는 중인 것은 풀 수 없다** (이미 나갔을 수 있다)", ValueError,
           lambda: store.release(KEY, rec.token))

    store.complete(KEY, rec.token, "eff-1")
    done, _ = store.claim(KEY, by="run-4")
    ok("끝난 것은 DONE", done is Claim.DONE)
    raises("**끝난 것도 풀 수 없다**", ValueError, lambda: store.release(KEY, rec.token))
    tmp.cleanup()


def key_collisions() -> None:
    print("\n2. 서로 다른 key 가 서로를 막지 않는가")
    tmp, store, _ = fresh()

    a, _ = store.claim("alert:A:cond:2026-08-31", by="r")
    b, _ = store.claim("alert:A:cond:2026-09-01", by="r")
    c, _ = store.claim("alert:B:cond:2026-08-31", by="r")
    ok("종목·조건·날짜가 다르면 각각 찍힌다",
       a is Claim.CLAIMED and b is Claim.CLAIMED and c is Claim.CLAIMED)

    # 초판은 허용되지 않는 문자를 전부 `_` 로 바꿔 파일 이름을 만들었다.
    # 그러면 `a:b` 와 `a_b` 가 같은 파일이 됐다 — 서로 다른 알림이 서로를 막았다.
    x, _ = store.claim("alert:x:y:z", by="r")
    y, _ = store.claim("alert_x_y_z", by="r")
    ok("**구분자만 다른 key 가 같은 파일이 되지 않는다**",
       x is Claim.CLAIMED and y is Claim.CLAIMED)

    again, _ = store.claim("alert:A:cond:2026-08-31", by="r2")
    ok("같은 것만 막힌다", again is Claim.HELD)
    tmp.cleanup()


def corrupt_record() -> None:
    print("\n3. 기록이 깨졌을 때")
    tmp, store, _ = fresh()
    store.claim(KEY, by="run-1")
    path = next(store.root.glob("*.json"))
    path.write_text("이건 JSON 이 아니다", encoding="utf-8")

    verdict, rec = store.claim(KEY, by="run-2")
    ok("**깨진 기록은 CORRUPT 로 보수적으로 막는다**", verdict is Claim.CORRUPT)
    ok("**사람 목록에 드러난다** (막으면서 숨기지 않는다)", len(store.corrupt()) == 1)
    tmp.cleanup()


# ---------------------------------------------------------------- 실제 스크립트로 요청 하나를 승인 대기까지

class Project:
    """이 폴더의 임시 사본. 실제 스크립트를 그 안에서 부른다. Claude 는 부르지 않는다.

    PATH 맨 앞에 **부르면 기록만 하는 가짜 claude** 를 둔다 — 전달이 모델을 다시 부르면 여기 남는다.
    """

    def __init__(self, tmp: str) -> None:
        self.root = Path(tmp) / "alert"
        shutil.copytree(ROOT, self.root, ignore=IGNORE)
        self.calls = Path(tmp) / "claude-calls.log"
        fake = Path(tmp) / "bin" / "claude"
        fake.parent.mkdir()
        fake.write_text(f"#!/bin/sh\necho \"$@\" >> {self.calls}\necho '{{}}'\n", encoding="utf-8")
        fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
        self.env = {k: v for k, v in os.environ.items()
                    if k not in ("CLAUDE_PROJECT_DIR", "REQUEST_ID", "ALERT_CLAIMS_DIR", "ALERT_CRASH_AFTER_SEND")}
        self.env.update(PYTHONUTF8="1", PATH=f"{fake.parent}{os.pathsep}{os.environ.get('PATH', '')}")
        self.rid = ""

    def run(self, *args: str, stdin: str | None = None, **extra: str) -> tuple[int, str]:
        p = subprocess.run([sys.executable, *args], cwd=self.root, input=stdin, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", env={**self.env, **extra})
        return p.returncode, p.stdout + p.stderr

    def tick(self) -> tuple[int, str]:
        return self.run("scripts/open_request.py", "--tick", "--case", "case-01-breakout")

    def open_to_approval(self) -> str:
        """tick → drafter 대신 저장된 답을 쓰고 → 검수를 부르는 차단 장치 → 승인 대기. 지문을 돌려준다."""
        code, out = self.tick()
        self.rid = next(l.split("=", 1)[1] for l in out.splitlines() if l.startswith("REQUEST_ID="))
        sample = json.loads((ROOT / "samples" / "reference" / "case-01-breakout.json").read_text(encoding="utf-8"))
        sample.pop("_note", None)
        draft = self.req / "draft.json"
        draft.write_text(json.dumps(sample, ensure_ascii=False, indent=2), encoding="utf-8")
        payload = json.dumps({"hook_event_name": "PostToolUse", "tool_name": "Write",
                              "tool_input": {"file_path": str(draft)}, "tool_response": {"success": True}})
        self.run(".claude/hooks/run_review.py", stdin=payload, CLAUDE_PROJECT_DIR=str(self.root))
        self.run("scripts/open_request.py", "--finish", self.rid)
        return json.loads((self.req / "approval.json").read_text(encoding="utf-8"))["sha256"]

    @property
    def req(self) -> Path:
        return self.root / "requests" / self.rid

    def state(self) -> dict:
        return json.loads((self.req / "state.json").read_text(encoding="utf-8"))

    def approve(self, sha: str, **extra: str) -> tuple[int, str]:
        """사람이 치는 scripts/approve.sh 와 같다(그 스크립트가 이것을 부른다)."""
        return self.run("scripts/deliver.py", self.rid, sha, **extra)

    @property
    def outbox(self) -> Path:
        return self.root / "outbox" / "sent.jsonl"

    def store(self) -> IdempotencyStore:
        return IdempotencyStore(self.root / "claims")


# ---------------------------------------------------------------- 4

def crash_then_reapprove() -> None:
    """**발송 직후 죽은 전달을, 같은 승인을 다시 주어 이어받는다.**

    이게 초판에 없어서 알림이 두 번 나갔다. 이어받은 쪽은 손에 든 번호표 목록을 믿지 않고
    번호표 기록을 다시 읽어야 한다.
    """
    print("\n4. **보낸 직후 죽었다가 같은 승인으로 이어받으면** ← 초판이 놓친 곳")

    with tempfile.TemporaryDirectory() as tmp:
        pj = Project(tmp)
        sha = pj.open_to_approval()

        code, out = pj.approve(sha, ALERT_CRASH_AFTER_SEND="1")
        ok("보낸 직후 죽는다 (교육용 crash)", code != 0 and "교육용 crash" in out)
        first = _count(pj.outbox)
        ok("한 건은 이미 나가 버렸다", first == 1)
        sending = [r for r in pj.store().records() if r.phase == Phase.SENDING.value]
        ok("번호표에 '보내는 중'(SENDING)이 남는다", len(sending) == 1)

        code, out = pj.approve(sha)
        counts = Counter(l["dedupe_key"] for l in _lines(pj.outbox))
        dupes = {k: c for k, c in counts.items() if c > 1}
        ok("**resume 해도 같은 알림이 두 번 나가지 않는다**", not dupes)
        ok("남은 것은 정상적으로 나간다", len(counts) == 2)
        ok("**사람에게 넘어간다** (보냈는지 모르므로)", code == 3 and "deliver → needs_human" in out)
        ok("무엇이 애매한지 말해 준다", "보내는 중에 죽었던 것" in out)


def rerun() -> None:
    """같은 요청을 **다시 승인**하고, 5분 뒤 tick 이 또 오면."""
    print("\n5. 같은 알림을 두 번 보내려 하면")

    with tempfile.TemporaryDirectory() as tmp:
        pj = Project(tmp)
        sha = pj.open_to_approval()

        code, out = pj.tick()
        ok("승인 전에 같은 tick 이 또 오면 요청을 다시 열지 않는다 (skip)",
           code == 10 and f"skip: already ran {pj.rid}" in out)

        code, out = pj.approve(sha)
        ok("첫 승인은 보낸다", code == 0 and "deliver → sent" in out)
        code, out = pj.approve(sha)
        ok("**두 번째는 조용히 끝난다** (duplicate)", code == 0 and "deliver → duplicate" in out)
        code, out = pj.tick()
        ok("**다음 tick 은 Claude 를 부르기 전에 걸러 quiet 로 끝난다**",
           code == 11 and out.startswith("quiet:") and "이미 보낸 알림" in out)
        ok("**sink 는 2줄뿐이다** (조건 2개 × 1회)", _count(pj.outbox) == 2)
        ok("Claude 를 부르지 않았다", not pj.calls.exists())


def approval_binds_to_artifact() -> None:
    """**승인이 사람이 본 파일에 묶이는가.**

    이 과정의 알림 장치를 처음 만들었을 때 승인은 예/아니오 한 칸이었고,
    두 번째 판은 지문을 요구했지만 새 실행이 모델을 다시 불렀다.
    그건 안전한 승인이 아니라 **안전하게 실패하는 재생성**이다.
    """
    print("\n7. **승인이 그 문구에 묶이는가**")

    with tempfile.TemporaryDirectory() as tmp:
        pj = Project(tmp)
        sha = pj.open_to_approval()
        ok("승인 없이는 멈춘다", pj.state().get("terminal") == "needs_approval")
        ok("아무것도 안 나갔다", _count(pj.outbox) == 0)
        ok("**해시는 full SHA-256 이다** (자르지 않는다)", len(sha) == 64)

        code, _ = pj.approve(sha[:12])
        ok("잘라 쓴 해시는 받지 않는다", code == 64 and _count(pj.outbox) == 0)

        # --- 틀린 해시 ---
        code, out = pj.approve("0" * 64)
        ok("**틀린 해시는 거부된다**", code == 3 and "승인한 문구가 아니다" in out)
        ok("**틀린 해시로는 아무것도 안 나간다**", _count(pj.outbox) == 0)

        # --- 문구를 몰래 바꾸면 ---
        draft = pj.req / "draft.json"
        original = draft.read_text(encoding="utf-8")
        tampered = json.loads(original)
        tampered["headline"] = "몰래 바꾼 문구"
        draft.write_text(json.dumps(tampered, ensure_ascii=False), encoding="utf-8")
        import hashlib
        after = hashlib.sha256(draft.read_bytes()).hexdigest()
        ok("**파일을 바꾸면 해시가 달라진다** (승인이 무효가 된다)", after != sha)
        code, _ = pj.approve(sha)
        ok("바뀐 파일은 원래 해시로 보낼 수 없다", code == 3 and _count(pj.outbox) == 0)
        code, out = pj.approve(after)
        ok("바뀐 파일의 새 해시로도 못 보낸다 (검수를 거치지 않았다)",
           code == 3 and "검수를 통과한 뒤" in out and _count(pj.outbox) == 0)
        draft.write_text(original, encoding="utf-8")

        # --- 맞는 해시 ---
        code, out = pj.approve(sha)
        ok("**맞는 해시면 보낸다** (sent)", code == 0 and "deliver → sent" in out)
        ok("**Claude 를 다시 부르지 않는다**", not pj.calls.exists())
        ok("**정확히 한 번 나간다**", _count(pj.outbox) == 2)   # 조건 2개 × 1회
        events = [json.loads(l) for l in (pj.req / "events.jsonl").read_text(encoding="utf-8").splitlines()]
        ok("승인 기록이 남는다 (누가·무엇을)",
           any(e["event"] == "approved" and e.get("sha256") == sha and e.get("by") for e in events))

        # --- 이미 보낸 요청을 다시 승인하면 ---
        code, out = pj.approve(sha)
        ok("**보낸 요청을 다시 승인해도 두 번 나가지 않는다**",
           "deliver → duplicate" in out and _count(pj.outbox) == 2)


def concurrent_transition() -> None:
    """**다섯 프로세스가 동시에 보내려 하면.**

    처음 판: `os.replace` 는 원자적이지만 읽기-비교-쓰기 전체는 아니었다.
    같은 key·같은 토큰으로 `begin_send` 가 **두 번 성공**했다.

    ⚠️ 순차 반복으로는 이걸 못 잡는다. 진짜 프로세스를 띄운다.
    """
    print("\n6. **다섯 프로세스가 동시에 보내려 하면**")

    import multiprocessing as mp

    import _concurrency as C

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "claims"
        store = IdempotencyStore(root)
        _, rec = store.claim(KEY, by="run")

        barrier = mp.Barrier(5)
        with mp.Pool(5, initializer=C._init, initargs=(barrier,)) as pool:
            results = pool.map(C.try_begin_send, [(str(root), KEY, rec.token)] * 5)

        ok(f"**하나만 이긴다** ({results.count('won')}/5)", results.count("won") == 1)
        ok("나머지는 보내지 않는다", results.count("won") + results.count("late")
           + results.count("contended") == 5)
        ok("상태는 SENDING 하나", store.read(KEY).phase == Phase.SENDING.value)


def stale_lock_judgment() -> None:
    """**잠금이 살아있는가 죽었는가** — 위 §6 이 터진 진짜 원인.

    ⚠️ §6 은 **레이스라 매번 터지지 않는다.** CI(리눅스)에서 2/5 로 한 번 터지고
    재실행에서는 통과했다. 윈도우에서는 아예 안 터졌다 — 고쳐져서가 아니라
    `os.replace` 가 열린 파일 이름을 못 바꿔 **마지막 단계에서 우연히 막혀서**다.

    간헐적 테스트만 두면 다음에 또 조용히 열린다. 그래서 **판정 자체**를
    결정적으로 고정한다. `_lock` 은 `O_EXCL` 로 파일을 만든 다음 줄에서 내용을
    쓰므로 **비어 있는 창**이 있다. 그 창의 잠금을 '오래된 것' 으로 보면
    남이 훔쳐 가고 둘이 임계 구역에 같이 들어간다.
    """
    print("\n8. **잠금 나이 판정** — 못 읽는 잠금은 오래된 게 아니라 방금 만든 것이다")

    with tempfile.TemporaryDirectory() as tmp:
        store = IdempotencyStore(Path(tmp) / "claims")
        store.claim(KEY, by="run")
        lock = store.root / f"{store._path(KEY).stem}.lock"

        def judge(content: str, age_seconds: float = 0.0) -> bool:
            lock.write_text(content, encoding="utf-8")
            if age_seconds:
                when = time.time() - age_seconds
                os.utime(lock, (when, when))
            verdict = store._reclaim_stale(lock, stale_after=60.0)
            lock.unlink(missing_ok=True)
            return verdict

        # 살아있다고 봐야 하는 것들 — 훔치면 상호배제가 깨진다
        ok("**방금 만든 빈 잠금은 안 훔친다** (O_EXCL 직후, 아직 안 씀)",
           judge("") is False)
        ok("**반쯤 쓰인 잠금도 안 훔친다**", judge('{"by": 1, "a') is False)
        ok("at 키가 없어도 안 훔친다", judge('{"by": 1}') is False)
        ok("방금 잡은 정상 잠금은 안 훔친다",
           judge(json.dumps({"by": 1, "at": time.time()})) is False)

        # 죽었다고 봐야 하는 것들 — 안 훔치면 영구 잠금이 된다
        ok("오래된 정상 잠금은 회수한다 (주인이 죽었다)",
           judge(json.dumps({"by": 1, "at": time.time() - 120})) is True)
        ok("**오래된 빈 잠금도 회수한다** (만들자마자 죽어도 영구 잠금이 안 된다)",
           judge("", age_seconds=120) is True)


def _lines(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]


def _count(path: Path) -> int:
    return len(_lines(path))


def main() -> int:
    force_utf8()
    store_contract()
    key_collisions()
    corrupt_record()
    crash_then_reapprove()
    rerun()
    approval_binds_to_artifact()
    concurrent_transition()
    stale_lock_judgment()

    print()
    if failures:
        print(f"실패 {len(failures)}건")
        for line in failures:
            print(f"   {line}")
        return 1
    print("같은 알림이 두 번 나가지 않는다 — **실제 전달 스크립트(scripts/deliver.py)로 확인했다.**")
    print()
    print("⚠️ 그렇다고 exactly-once 가 아니다. 4번을 보라 —")
    print("   이미 나간 알림을 **우리가 몰랐다.** 사람에게 넘겼을 뿐이다.")
    print("   진짜 해법은 받는 쪽(외부 알림 API)이 중복 방지 키를 받아 주는 것이다.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
