"""Stop — Claude 가 멈추려 할 때 다음 단계를 정한다.

흐름도(graph.yaml)를 따라 스크립트 단계는 직접 실행하고, 작성할 차례면 멈추지 못하게 막고(decision: block)
무엇을 다시 작성해야 하는지 이유를 돌려준다. 끝 상태에 닿았으면 막지 않는다.

끝없이 막지 않는 것은 stop_hook_active 가 아니라 작성 횟수 상한(max_visits)과 단계 상한(max_steps)이 보장한다.
우리 되돌아가기는 일부러 여러 번 막으니, "한 번 막았으면 다음엔 놓아준다" 는 흔한 처방을 쓰면 두 번째 되돌아가기가 사라진다.

처리 중인 요청이 없으면 아무것도 하지 않는다(다른 대화를 방해하지 않게).
"""

from __future__ import annotations

import json
import sys

from _state import advance, current_id, hook_input, load, utf8


def main() -> int:
    utf8()
    hook_input()
    rid = current_id()
    if not rid or not load(rid):
        return 0
    r = advance(rid, "stop")
    if r["need"]:
        print(json.dumps({"decision": "block", "reason": r["message"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
