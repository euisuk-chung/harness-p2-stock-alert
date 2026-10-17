"""시작 스크립트 시험 — 가짜 claude 로, 모델을 부르지 않는다.

    uv run python tests/test_scripts.py

  1. request.sh 에 --bare 가 없다(붙이면 차단 장치가 모두 꺼진다)
  2. request.sh 가 이 프로젝트 폴더로 들어가고 --setting-sources project 로 개인 설정을, --strict-mcp-config 로 계정의 커넥터를 뺀다
  3. 겹친 tick 을 막는다(flock -n .tick.lock)
  4. --dry-run 은 부를 claude -p 줄만 찍고 요청을 열지 않는다
  5. 조건이 안 걸린 tick 은 quiet 로 끝나고 claude 를 부르지 않는다 — 요청 폴더도 만들지 않는다
  6. 같은 tick 을 두 번 하면 둘째는 Claude 를 부르지 않고 건너뛴다(요청 폴더를 먼저 만든 쪽 하나만)
  7. Claude 는 보낼 수 없다 — guard.py 가 보내는 명령·번호표 건드리기·밖으로 나가는 명령을 막는다
전부 맞으면 0.

보장하지 않는 것: 실제 claude 가 이 옵션으로 멈추지 않고 끝까지 간다는 것 — 그것은 실제 실행으로 본다.
"""

import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

SRC = Path(__file__).resolve().parents[1]
IGNORE = shutil.ignore_patterns("requests", "claims", "outbox", "logs", ".tick.lock", "__pycache__")
fails = []


def ok(cond: bool, what: str) -> None:
    print(f"{'OK  ' if cond else 'FAIL'} {what}")
    if not cond:
        fails.append(what)


def guard_blocks(root: Path, command: str) -> bool:
    """Claude 가 이 Bash 명령을 부르려 할 때 guard.py 가 막나(신호 2)."""
    body = json.dumps({"hook_event_name": "PreToolUse", "tool_name": "Bash", "tool_input": {"command": command}})
    env = {k: v for k, v in os.environ.items() if k != "REQUEST_ID"}
    env.update(CLAUDE_PROJECT_DIR=str(root), PYTHONUTF8="1")
    p = subprocess.run([sys.executable, str(root / ".claude" / "hooks" / "guard.py")], input=body, cwd=root,
                       capture_output=True, text=True, encoding="utf-8", env=env)
    return p.returncode == 2


def main() -> int:
    for s in (sys.stdout,):
        try:
            s.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass
    sh = (SRC / "scripts" / "request.sh").read_text(encoding="utf-8")
    code_lines = [l for l in sh.splitlines() if not l.lstrip().startswith("#")]
    ok(not any("--bare" in l for l in code_lines), "request.sh 에 --bare 가 없다")
    ok('cd "$(dirname "$0")/.."' in sh, "request.sh 가 이 프로젝트 폴더로 들어간다")
    ok("--setting-sources project" in sh, "request.sh 가 개인 설정을 뺀다(--setting-sources project)")
    ok("--strict-mcp-config" in sh, "request.sh 가 계정의 커넥터를 싣지 않는다(--strict-mcp-config)")
    ok(any("flock -n" in l for l in code_lines), "request.sh 가 겹친 tick 을 막는다(flock -n)")
    with tempfile.TemporaryDirectory(prefix="scripts-") as t:
        root = Path(t) / "alert"
        shutil.copytree(SRC, root, ignore=IGNORE)
        log = Path(t) / "claude-calls.log"
        fake = Path(t) / "claude"
        fake.write_text(f"#!/bin/sh\necho \"$@\" >> {log}\necho '{{}}'\n", encoding="utf-8")
        fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
        env = {k: v for k, v in os.environ.items() if k not in ("CLAUDE_PROJECT_DIR", "REQUEST_ID")}
        # 임시 폴더는 꾸러미 밖이라 uv 가 .venv 를 못 찾는다 — 지금 이 시험을 실행한 가상환경을 알려 준다
        env["VIRTUAL_ENV"] = sys.prefix
        env.update(CLAUDE_BIN=str(fake), PYTHONUTF8="1")
        run = lambda *a: subprocess.run(["bash", "scripts/request.sh", *a], cwd=root, env=env,
                                        capture_output=True, text=True, encoding="utf-8")
        calls = lambda: log.read_text(encoding="utf-8").count("/handle-request") if log.exists() else 0
        reqs = lambda: sorted(p.name for p in (root / "requests").glob("alert-*")) if (root / "requests").is_dir() else []

        p = run("--dry-run")
        ok(p.returncode == 0 and "claude" in p.stdout and "-p /handle-request" in p.stdout,
           "--dry-run 은 부를 줄만 찍는다")
        ok(not reqs(), "--dry-run 은 요청을 열지 않는다")

        p = run("--case", "case-02-quiet")
        ok(p.returncode == 0 and p.stdout.startswith("quiet:"), "조건이 안 걸린 tick 은 quiet 로 끝난다(신호 0)")
        ok(calls() == 0, "quiet tick 은 claude 를 부르지 않는다")
        ok(not reqs(), "quiet tick 은 요청 폴더를 만들지 않는다")

        p1 = run("--case", "case-01-breakout")
        calls1 = calls()
        p2 = run("--case", "case-01-breakout")
        calls2 = calls()
        rid = reqs()[0] if reqs() else "?"
        ok(calls1 == 1 and p1.stdout.startswith(f"open: {rid}"), "조건이 걸린 첫 tick 은 요청을 열고 claude 를 한 번 부른다")
        ok(calls2 == 1 and f"skip: already ran {rid}" in p2.stdout and p2.returncode == 0,
           f"같은 tick 둘째는 부르지 않고 건너뛴다 (skip: already ran {rid})")
        ok(len(reqs()) == 1, "요청 폴더는 하나뿐이다")

        for what, cmd in (("보내기(approve.sh)", f"scripts/approve.sh {rid} {'0' * 64}"),
                          ("보내기(deliver.py)", f"uv run python scripts/deliver.py {rid} {'0' * 64}"),
                          ("번호표 고치기(claims_cli.py)", "uv run python claims_cli.py mark-sent alert:AAPL:x:y e"),
                          ("밖으로 나가는 명령(curl)", "curl https://example.com"),
                          ("알림 내보내는 곳(sink) 직접 부르기", "python3 -c 'import sink'"),
                          ("번호표 기록 보기·건드리기(claims/)", "ls claims/")):
            ok(guard_blocks(root, cmd), f"Claude 는 할 수 없다: {what}")
        ok(not guard_blocks(root, f"uv run python scripts/open_request.py {rid}"), "요청을 여는 명령은 허용한다")
    print("\n스크립트 시험: " + ("통과" if not fails else f"{len(fails)}건 다름"))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
