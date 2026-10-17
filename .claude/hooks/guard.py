"""PreToolUse — 막기. policies.yaml 대로 쓸 곳·칠 명령·도구 호출 상한을 본다.

두 겹으로 막는다.
  늘          deny_commands(되돌릴 수 없거나 밖으로 나가는 명령) · 하위 에이전트를 백그라운드로 띄우기
  요청 중에만  allow_tools · writable · allow_commands · max_tool_calls
              — 요청이 열려 있고(REQUEST_ID 또는 requests/.current) 아직 끝나지 않았을 때.
              대화 창에서 사람과 함께 조각을 지을 때는 이 칸들로 막지 않는다(검사 파일을 짓고 시험을 실행해야 하니까).

막으면 2 로 끝나고, 이유(표준 오류)가 Claude 에게 간다. 0·2 가 아닌 신호는 막지 못하니
(1 로 끝나면 조용히 통과한다) 예외가 나도 2 로 끝나게 감쌌다.
"""

from __future__ import annotations

import sys

from _state import current_id, event, hook_input, load, load_yaml, matches_any, save, utf8


def decide(inp: dict) -> str | None:
    pol = load_yaml("policies.yaml")
    tool = inp.get("tool_name", "")
    ti = inp.get("tool_input") or {}
    # 늘 막는 것
    if tool == "Bash":
        cmd = (ti.get("command") or "").strip()
        low = cmd.lower()
        for bad in pol.get("deny_commands", []):
            if bad.lower() in low:
                return f"이 명령은 막혀 있다({bad.strip()}): {cmd}"
    if tool in ("Agent", "Task") and ti.get("run_in_background"):
        return "하위 에이전트를 백그라운드로 띄우지 않는다. 끝날 때까지 기다리도록 다시 부르세요."
    # 요청 중에만 막는 것
    rid = current_id()
    st = load(rid) if rid else {}
    if not st or st.get("terminal"):
        return None
    st["tool_calls"] = st.get("tool_calls", 0) + 1
    save(rid, st)
    cap = pol.get("max_tool_calls", 20)
    if st["tool_calls"] > cap:
        return f"도구 호출 상한 {cap}회에 닿았다. 더 하지 말고 멈추세요."
    allowed = pol.get("allow_tools")
    if allowed and tool not in allowed:
        return f"이 요청에서는 {tool} 도구를 쓰지 않는다. 쓸 수 있는 도구: {', '.join(allowed)}"
    if tool in ("Write", "Edit", "MultiEdit", "NotebookEdit"):
        path = ti.get("file_path") or ti.get("notebook_path") or ""
        if not matches_any(path, pol.get("writable", [])):
            return f"{path} 에는 쓸 수 없다. 쓸 수 있는 곳: {', '.join(pol.get('writable', []))}"
    if tool == "Bash":
        cmd = (ti.get("command") or "").strip()
        if not any(cmd.startswith(ok) for ok in pol.get("allow_commands", [])):
            return f"허용 목록에 없는 명령이다: {cmd}"
    return None


def main() -> int:
    utf8()
    inp = hook_input()
    try:
        why = decide(inp)
    except Exception as e:  # 장치가 깨져서 조용히 통과하는 것보다 막는 편이 낫다
        why = f"guard.py 가 판단하지 못했다({type(e).__name__}). 안전하게 막는다."
    rid = current_id()
    st = load(rid) if rid else {}
    active = bool(st) and not st.get("terminal")      # 기록은 요청이 진행 중일 때만 남긴다
    if why:
        if active:
            event(rid, "blocked", by="guard", tool=inp.get("tool_name", ""), why=why)
        print(why, file=sys.stderr)
        return 2
    if active:
        event(rid, "tool", tool=inp.get("tool_name", ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
