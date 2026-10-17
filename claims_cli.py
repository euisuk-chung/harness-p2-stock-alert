"""대사(reconcile) — **보냈는지 모르는 것을 사람이 정리한다.**

    uv run python claims_cli.py list                        무엇이 걸려 있나
    uv run python claims_cli.py mark-sent <KEY> <EFFECT>    실제로 나갔더라 → 끝난 것으로
    uv run python claims_cli.py release-unsent <KEY>        안 나갔더라 → 풀어서 다시 보낼 수 있게

## 왜 명령이 필요한가

> ⚠️ 초판은 *"`claims/` 를 보고 안 나갔다고 확신하면 그 파일을 지운다"* 라고 안내했다.
> **그게 이 프로젝트가 가르치는 규칙을 사람이 우회하게 만든다.**
>
> 파일을 손으로 지우면 토큰 확인도, 단계 제한도, 기록도 없다.
> `DONE` 을 실수로 지우면 **이미 나간 알림이 다시 나간다.**

그래서 대사도 **같은 규칙 안에서** 한다.

    DONE 은 못 건드린다              이미 끝난 것이다
    SENDING 은 사람의 판정이 있어야   나갔는지 우리가 모른다
    모든 결정을 기록에 남긴다          누가 왜 그랬는지

## 판정은 사람이 한다

기계는 **무엇이 애매한지**까지만 말한다. 실제로 나갔는지는 **외부 발송 기록**을 봐야 알고,
그건 우리 쪽에 없다. `list` 가 무엇을 확인해야 하는지 알려 준다.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "lib"))

from _utf8 import force_utf8  # noqa: E402
from idempotency import IdempotencyStore, Phase, Record, _atomic_write, claims_root  # noqa: E402


def audit(action: str, **fields) -> None:
    """대사 결정을 남긴다. **지우지 않는다.** 기록도 claims 저장소 안에 둔다."""
    path = claims_root(ROOT / "claims") / "reconcile.log.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fp:
        fp.write(json.dumps(
            {"at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
             "action": action, **fields}, ensure_ascii=False) + "\n")


def store() -> IdempotencyStore:
    return IdempotencyStore(claims_root(ROOT / "claims"))


def cmd_list() -> int:
    s = store()
    stuck = s.stuck()
    corrupt = s.corrupt()

    if not stuck and not corrupt:
        print("걸려 있는 것이 없다.")
        return 0

    print(f"{'상태':<9} {'key':<44} 무엇을 확인하나")
    print("-" * 96)
    for r in stuck:
        what = ("**외부 발송 기록을 본다.** 나갔으면 mark-sent, 안 나갔으면 release-unsent"
                if r.phase == Phase.SENDING.value
                else "보내기 전에 죽었다. 확인 후 release-unsent 하면 다시 보낼 수 있다")
        print(f"{r.phase:<9} {r.key:<44} {what}")
    for p in corrupt:
        print(f"{'corrupt':<9} {p.name:<44} 읽을 수 없다. 파일을 직접 보고 판단한다")

    print()
    print("⚠️ **판정은 사람이 한다.** 실제로 나갔는지는 외부 발송 기록에만 있다.")
    return 0


def cmd_mark_sent(key: str, effect_ref: str) -> int:
    """실제로 나갔더라. 끝난 것으로 만든다."""
    s = store()
    current = s.read(key)
    if current is None:
        print(f"그런 key 가 없다: {key}")
        return 1
    if current.phase == Phase.DONE.value:
        print(f"이미 끝난 것이다: {key} → {current.effect_ref}")
        return 0

    done = Record(**{**current.to_dict(), "phase": Phase.DONE.value,
                     "completed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                     "effect_ref": effect_ref})
    _atomic_write(s._path(key), done.to_dict())
    audit("mark-sent", key=key, from_phase=current.phase, effect_ref=effect_ref)
    print(f"끝난 것으로 기록했다: {key} → {effect_ref}")
    print("이제 다음 실행이 이 알림을 다시 보내지 않는다.")
    return 0


def cmd_release_unsent(key: str) -> int:
    """안 나갔더라. 풀어서 다시 보낼 수 있게 한다."""
    s = store()
    current = s.read(key)
    if current is None:
        print(f"그런 key 가 없다: {key}")
        return 1

    if current.phase == Phase.DONE.value:
        # ⚠️ 여기가 손으로 파일을 지울 때 가장 위험한 지점이다.
        print(f"**끝난 것은 풀 수 없다**: {key}")
        print("  이미 나갔다고 기록돼 있다. 풀면 다음 실행이 또 보낸다.")
        print("  정말 안 나갔다고 확신하면, 외부 발송 기록으로 먼저 확인한다.")
        return 1

    if current.phase == Phase.SENDING.value:
        print(f"⚠️ '{key}' 는 **보내는 중에 죽은 것**이다.")
        print("  나갔는지 안 나갔는지 우리 기록만으로는 모른다.")
        print("  외부 발송 기록에서 **안 나갔음을 확인**했다면 --confirmed 를 붙인다.")
        if "--confirmed" not in sys.argv:
            return 1

    s._path(key).unlink(missing_ok=True)
    audit("release-unsent", key=key, from_phase=current.phase,
          confirmed="--confirmed" in sys.argv)
    print(f"풀었다: {key}")
    print("다음 실행이 이 알림을 다시 보낸다.")
    return 0


def main() -> int:
    force_utf8()
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list", help="걸려 있는 것을 보여 준다")
    m = sub.add_parser("mark-sent", help="실제로 나갔더라 → 끝난 것으로")
    m.add_argument("key")
    m.add_argument("effect_ref")
    r = sub.add_parser("release-unsent", help="안 나갔더라 → 풀어서 다시 보낼 수 있게")
    r.add_argument("key")
    r.add_argument("--confirmed", action="store_true",
                   help="SENDING 인 것을 풀 때 필요하다. **외부 기록으로 확인했다는 뜻**")
    args = parser.parse_args()

    if args.cmd == "list":
        return cmd_list()
    if args.cmd == "mark-sent":
        return cmd_mark_sent(args.key, args.effect_ref)
    return cmd_release_unsent(args.key)


if __name__ == "__main__":
    sys.exit(main())
