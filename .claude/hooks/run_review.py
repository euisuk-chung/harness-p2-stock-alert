"""PostToolUse — 초안이 쓰이면 곧바로 검수한다.

requests/<id>/draft.json 이 쓰였을 때만 움직인다. 흐름을 한 칸 밀어(advance) 검수를 실행하고,
통과면 그 뒤 스크립트 단계까지 간다. 결과는 참고로만 붙이고(additionalContext) **막지 않는다** —
다시 쓰게 하는 일은 늘 Claude 가 멈추려 할 때 next_step.py 가 흐름도대로 새 drafter 에게 맡긴다.
(초안을 쓴 하위 에이전트에게 바로 거부를 돌려주면, 고칠 수 없는 과제에서 그 에이전트가 끝을 내지 못한 일이
 실제 실행에서 있었다 — 되돌아가는 길을 하나로 둔다.)

이 장치가 걸리지 않아도 흐름은 빠지지 않는다 — next_step.py 가 같은 advance 를 부른다.
두 번 불러도 같은 초안은 한 번만 검수한다.
"""

from __future__ import annotations

import json
import sys

from _state import advance, current_id, hook_input, load, utf8


def main() -> int:
    utf8()
    inp = hook_input()
    path = (inp.get("tool_input") or {}).get("file_path", "")
    rid = current_id()
    if not rid or not load(rid) or not path.replace("\\", "/").endswith(f"requests/{rid}/draft.json"):
        return 0
    r = advance(rid, "post")
    note = r["message"] if not r["need"] else "검수 결과를 기록했다. 작성을 마쳤으면 멈추세요 — 다음 단계는 차단 장치가 정한다."
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "PostToolUse", "additionalContext": note}},
                     ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
