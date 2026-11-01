"""흐름 점검 — 모델 없이, 저장된 초안으로 요청 하나를 끝까지 따라가 본다. 판정(judge.py)이 쓴다.

    uv run python scripts/flow_check.py --case case-01-breakout                       검수 통과 → needs_approval
    uv run python scripts/flow_check.py --case case-01-breakout --sample bad          세 번 거부 → needs_human
    uv run python scripts/flow_check.py --case case-02-quiet                          걸린 조건이 없다 → quiet
    uv run python scripts/flow_check.py --case case-01-breakout --result-json requests/judge-14.json

이 폴더를 임시 폴더에 복사한 뒤, Claude Code 가 하는 일을 이 점검이 대신 한다.
  · .claude/settings.json 에 **등록된** 차단 장치만, Claude Code 와 같은 모양의 JSON 으로 부른다
    (등록이 없으면 부르지 않는다 — 그래서 짓기 전과 지은 뒤의 결과가 갈린다)
  · 모델이 할 일(drafter 가 문구 쓰기)은 samples/<reference|bad>/<케이스>.json 의 저장된 문구를 쓰는 것으로 대신한다.
    다시 쓰라고 하면 같은 문구를 다시 쓴다 — 그래서 틀린 문구는 끝까지 틀린다
  · 보내지는 않는다. 흐름은 승인 대기(needs_approval)에서 멈추고, 보내기는 사람이 scripts/approve.sh 로 한다
끝날 때 신호: 0 quiet(요청을 열지 않음) · 3 needs_approval · needs_human · 1 failed.

보장하지 않는 것: 실제 Claude Code 가 이 순서로 장치를 부른다는 것, 실제 모델이 거부 이유대로 고친다는 것.
그것은 scripts/request.sh 로 실제 실행해 본다.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

SRC = Path(__file__).resolve().parents[1]
IGNORE = shutil.ignore_patterns("requests", "claims", "outbox", "logs", ".tick.lock", "__pycache__", ".git")


def registered(root: Path, event: str, tool: str = "") -> list[list[str]]:
    """settings.json 에서 이 때(event)·이 도구(tool)에 걸리는 장치 명령들."""
    p = root / ".claude" / "settings.json"
    if not p.is_file():
        return []
    try:
        entries = json.loads(p.read_text(encoding="utf-8")).get("hooks", {}).get(event, [])
    except ValueError:
        return []
    out = []
    for e in entries:
        m = e.get("matcher", "")
        if tool and m not in ("", "*") and not re.fullmatch(m, tool):
            continue
        for h in e.get("hooks", []):
            if h.get("type") != "command":
                continue
            if "args" in h:
                argv = [h["command"], *h["args"]]
            else:
                argv = shlex.split(h["command"])
            argv = [a.replace("${CLAUDE_PROJECT_DIR}", str(root)).replace("$CLAUDE_PROJECT_DIR", str(root)) for a in argv]
            if argv[:2] == ["uv", "run"]:       # uv run python … — 이 점검이 이미 그 가상환경의 파이썬이다
                argv = argv[2:]
            if argv and argv[0] in ("python", "python3"):
                argv[0] = sys.executable
            out.append(argv)
    return out


def fire(root: Path, event: str, payload: dict, tool: str = "") -> tuple[bool, str]:
    """등록된 장치를 부른다. 하나라도 막으면(신호 2 또는 decision: block) (True, 이유)."""
    env = dict(os.environ, CLAUDE_PROJECT_DIR=str(root), PYTHONUTF8="1")
    env.pop("REQUEST_ID", None)
    body = json.dumps({"hook_event_name": event, "cwd": str(root), "session_id": "flow-check", **payload},
                      ensure_ascii=False)
    for argv in registered(root, event, tool):
        p = subprocess.run(argv, cwd=root, input=body, capture_output=True, text=True, encoding="utf-8", env=env)
        if p.returncode == 2:
            return True, p.stderr.strip()
        try:
            out = json.loads(p.stdout) if p.stdout.strip() else {}
        except ValueError:
            out = {}
        if out.get("decision") == "block":
            return True, out.get("reason", "")
    return False, ""


def run(root: Path, *args: str) -> tuple[int, str]:
    p = subprocess.run([sys.executable, *args], cwd=root, capture_output=True, text=True, encoding="utf-8",
                       env=dict(os.environ, CLAUDE_PROJECT_DIR=str(root), PYTHONUTF8="1"))
    return p.returncode, p.stdout + p.stderr


def main() -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--case", required=True)
    ap.add_argument("--sample", default="reference", choices=("reference", "bad"), help="저장된 문구의 폴더")
    ap.add_argument("--result-json", help="끝 상태를 이 파일에 적는다(이 폴더 기준)")
    a = ap.parse_args()
    sample = SRC / "samples" / a.sample / f"{a.case}.json"
    draft_body = None
    if sample.is_file():
        message = json.loads(sample.read_text(encoding="utf-8"))
        message.pop("_note", None)
        draft_body = json.dumps(message, ensure_ascii=False, indent=2)

    with tempfile.TemporaryDirectory(prefix="flow-check-") as t:
        root = Path(t) / "alert"
        shutil.copytree(SRC, root, ignore=IGNORE)
        code, out = run(root, "scripts/open_request.py", "--tick", "--case", a.case)
        rid = next((l.split("=", 1)[1] for l in out.splitlines() if l.startswith("REQUEST_ID=")), None)
        print(out.strip().splitlines()[0] if out.strip() else "요청을 열지 못했다")
        if code == 11:                               # quiet — 요청을 열지 않았다. Claude 도 부르지 않는다
            if a.result_json:
                _save(a.result_json, {"request_id": None, "terminal": "quiet", "reason": "", "visits": {},
                                      "exit_code": 0})
            return 0
        if rid and code == 0 and draft_body is None:
            print(f"저장된 문구가 없다: samples/{a.sample}/{sample.name}")
            return 1
        if rid and code == 0:
            fire(root, "SessionStart", {"source": "startup"})
            for _ in range(6):                       # drafter 가 문구를 쓰는 한 번
                target = root / "requests" / rid / "draft.json"
                ti = {"file_path": str(target), "content": draft_body}
                blocked, why = fire(root, "PreToolUse", {"tool_name": "Write", "tool_input": ti}, "Write")
                if blocked:
                    print(f"문구 쓰기가 막혔다: {why}")
                    break
                time.sleep(0.01)
                target.write_text(draft_body, encoding="utf-8")
                again, _ = fire(root, "PostToolUse", {"tool_name": "Write", "tool_input": ti,
                                                      "tool_response": {"success": True}}, "Write")
                if again:
                    continue                          # 검수가 거부 → drafter 가 같은 자리에서 다시 쓴다
                again, _ = fire(root, "Stop", {"stop_hook_active": False})
                if not again:
                    break                             # 멈춰도 된다
        code, out = run(root, "scripts/open_request.py", "--finish", rid) if rid else (1, "")
        print(out.strip())
        st = json.loads((root / "requests" / rid / "state.json").read_text(encoding="utf-8")) if rid else {}
    print(f"도착  {st.get('terminal')}" + (f" — {st['reason']}" if st.get("reason") else ""))
    if a.result_json:
        _save(a.result_json, {"request_id": rid, "terminal": st.get("terminal"), "reason": st.get("reason", ""),
                              "visits": st.get("visits", {}), "exit_code": code})
    return code


def _save(rel: str, data: dict) -> None:
    dst = SRC / rel
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    sys.exit(main())
