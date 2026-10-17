"""실습 자료를 **실수로 고치는 것**을 조기에 잡는다.

    PreToolUse 훅. 도구가 실행되기 전에 판정한다. 종료 코드 2 면 막힌다.

## ⚠️ 이건 **경계가 아니라 가드레일이다**

무엇을 못 막는지 먼저 적는다.

| 우회 | 왜 못 막나 |
|---|---|
| **훅을 끄는 것** | `.claude/settings.json` 을 고치면 이 훅이 안 돈다. (그건 막는다 — 아래) |
| **다른 도구** | MCP 파일 도구, 다른 에디터, 터미널에서 직접 |
| **symlink · 별칭 경로** | 다른 이름으로 같은 파일에 닿을 수 있다 |
| **차단 장치를 건너뛰는 실행** | `claude -p --bare` 처럼 차단 장치를 읽지 않는 실행에서는 **이 장치가 아예 안 걸린다** |

**진짜 경계가 필요하면** 보호 대상을 에이전트 작업공간 밖이나 읽기 전용 마운트에 두고,
CI 가 해시로 대조해야 한다. 이 훅은 거기까지 하지 않는다.

## 그래도 값어치가 있는 이유

학생이 대화형으로 Claude Code 를 쓰다가 **자료 원문을 고치는 흔한 실수**를 잡는다.
그게 왜 위험한지가 이 프로젝트의 주제와 직접 닿는다.

    인용 검사(G3)는 quote 가 cases/sources/ 원문에 그대로 있는지 본다.
    원문을 고칠 수 있으면 **지어낸 문장을 원문에 써 넣고** 통과할 수 있다.
    baseline.json 도 같다 — 기준을 낮추면 회귀가 사라진다.

> **검사를 세우면 그 검사를 피하는 길이 생긴다.** 여기서는 그 길이 채점표를 고치는 것이다.

## 훅 형식은 도구가 정한다

Claude Code 는 `.claude/settings.json`, Codex 는 `.codex/hooks.json` 이고
입력 필드 이름과 도구 이름도 다르다. 우리가 형식을 만들면 도구가 읽지 못한다.
"""

from __future__ import annotations

import json
import shlex
import sys
from pathlib import Path, PurePosixPath

# 에이전트가 고치면 안 되는 것. **경로 경계 단위로** 본다.
PROTECTED_DIRS = {
    "cases/sources": "자료 원문. 여기를 고칠 수 있으면 인용 검사(G3)가 무의미해진다",
    "samples/reference": "회귀 비교의 고정 입력. 바꾸면 어제와 비교할 수 없다",
    ".claude": "차단 장치 자신. 여기를 고칠 수 있으면 나머지 보호가 전부 무의미하다",
    # 12차시에 더한 한 줄 — 작성 담당이 늘 지킬 규칙(스킬). 7차시에 Claude 와 지은 뒤 여기서 잠근다.
    ".claude/skills": "작성 담당이 늘 따를 지시. 여기를 고칠 수 있으면 대화 창의 AI 가 그 지시를 스스로 바꿀 수 있다",
}
BUILDABLE = {"commands", "agents", "skills"}   # .claude/ 안에서 막지 않는 하위 폴더

PROTECTED_FILES = {
    "baseline.json": "회귀 기준. 여기를 낮추면 하락이 사라진다",
}

# 파일을 쓰는 도구들. Notebook 은 **경로 필드 이름이 다르다.**
WRITERS = {"Write", "Edit", "NotebookEdit"}
PATH_FIELDS = ("file_path", "notebook_path", "path")

# 셸로도 파일을 고칠 수 있다. 완전히 막지는 못하지만 흔한 형태는 잡는다.
SHELL_TOOLS = {"Bash"}  # Windows 셸 명령도 Claude Code의 Bash 도구 이벤트로 들어온다.

# 지우거나 옮기는 명령. **맨 이름**을 대상으로 볼지 여기서 가른다.
#
# ⚠️ 왜 필요한가: `claims` 처럼 보호 디렉터리 이름이 **흔한 낱말**이면
#    맨 이름을 무조건 대상으로 보다가 `git commit -m claims` 를 막는다.
#    실제로 막았고, test_hook.py 가 잡았다. 그래서 맨 이름은
#    **지우는 명령일 때만** 본다. 경로처럼 생긴 것(`claims/`)은 늘 본다.
DESTRUCTIVE = {
    "rm", "rmdir", "unlink", "shred", "truncate", "mv",
    "del", "erase", "rd",
    "Remove-Item", "ri", "rmdir.exe", "Move-Item", "mi", "Clear-Content", "clc",
}

# 지우지는 않지만 **쓰는** 명령. 이게 보일 때만 경로를 검사한다 (정확 토큰 일치).
WRITE_MARKERS = {"tee", "cp", "Set-Content", "Add-Content", "Out-File", "Copy-Item"}


def protected(target: str) -> str | None:
    """보호 대상이면 이유를, 아니면 None.

    ⚠️ 부분 문자열로 보지 않는다. `cases/sources-old/` 처럼 **다른 폴더**를
    잘못 막으면 실습이 안 된다. 경로 조각 단위로 본다.
    """
    if not target:
        return None
    parts = PurePosixPath(target.replace("\\", "/")).parts
    for i in range(len(parts)):
        for depth in (1, 2):
            if i + depth > len(parts):
                continue
            fragment = "/".join(parts[i:i + depth])
            # ⚠️ `< len(parts)` 였다. 그러면 **뒤에 뭔가 더 있을 때만** 막아서
            #    `rm -rf claims/` 처럼 **통째로 지우는 것**이 빠져나갔다.
            #    안의 파일 하나보다 디렉터리째가 더 나쁜데 그쪽이 안 막혔다.
            if fragment in PROTECTED_DIRS and i + depth <= len(parts):
                # .claude/ 안에서 명령·에이전트·스킬 파일은 이 과정에서 Claude 가 짓는 대상이라 여기서는 넘긴다.
                # 지키는 것은 설정(settings.json)과 차단 장치 코드(hooks/) — 이것을 고치면 나머지 보호가 무의미해진다.
                # 다 지은 스킬은 위 목록에 ".claude/skills" 한 줄을 더해 잠근다(12차시) — 다음 바퀴(두 조각 깊이)에서 막힌다.
                if fragment == ".claude" and i + 1 < len(parts) and parts[i + 1] in BUILDABLE:
                    continue
                return PROTECTED_DIRS[fragment]
    if parts and parts[-1] in PROTECTED_FILES:
        return PROTECTED_FILES[parts[-1]]
    return None


def targets(tool: str, payload: dict) -> list[str]:
    """이 호출이 건드리려는 경로들."""
    tool_input = payload.get("tool_input") or {}

    if tool in WRITERS:
        # Notebook 은 file_path 가 아니라 notebook_path 를 쓴다.
        return [str(tool_input[f]) for f in PATH_FIELDS if tool_input.get(f)]

    if tool in SHELL_TOOLS:
        # 셸은 무엇이든 할 수 있어서 정확히 판정할 수 없다.
        # **쓰기 의도가 보일 때만** 경로를 본다. 읽기(cat · ls · git diff · python …)까지
        # 막으면 강사가 훅 파일을 보여 주지도 못한다 — 실제로 그랬다 (2026-09-04 리뷰 N1).
        command = str(tool_input.get("command") or "")
        try:
            words = shlex.split(command)
        except ValueError:
            words = command.split()
        if not _writes(words):
            return []
        # 경로처럼 생긴 것은 늘 본다. 맨 이름은 — 보호 **파일**이면 늘,
        # 보호 **디렉터리**면 지우는 명령일 때만 (`git commit -m claims` 오탐 방지).
        # ⚠️ 슬래시만 보면 `rm baseline.json` 도 `rm -rf claims` 도 지나갔다.
        wiping = any(Path(w).name in DESTRUCTIVE for w in words)
        return [w.lstrip(">") for w in words
                if "/" in w or "\\" in w
                or w in PROTECTED_FILES
                or (wiping and w in PROTECTED_DIRS)]

    return []


def _writes(words: list[str]) -> bool:
    """이 명령에 **쓰기 의도**가 보이는가.

    **모든 토큰**을 본다. 첫 토큰만 보면 `cd nodes && rm -rf ../cases/…` 가 빠져나간다.
    `>` 는 shlex 가 `>cases/x` 처럼 경로에 붙여 두기도 하므로 부분 일치로 본다.
    `2>&1` 같은 stderr 리다이렉션은 쓰기가 아니다.
    """
    for i, w in enumerate(words):
        if Path(w).name in DESTRUCTIVE or w in WRITE_MARKERS:
            return True
        if ">" in w and not w.startswith("2>"):
            return True
        if w == "sed" and i + 1 < len(words) and words[i + 1].startswith("-i"):
            return True
    return False


def main() -> int:
    # Windows 에서 파이프로 나가는 stderr 는 cp949 가 된다. Claude Code 는 UTF-8 로 읽어
    # 차단 메시지의 한글이 깨진다 — 2026-09-04 Windows live smoke 에서 실측. 여기서 고정한다.
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure:
            reconfigure(encoding="utf-8", errors="replace")
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        # 읽을 수 없으면 막지 않는다 — **fail-open** 을 고른 것이다.
        # Claude Code 는 exit 2 만 "차단" 으로 보고, 그 밖의 0 아닌 코드는 **진행**이다.
        # 즉 fail-closed 는 도구가 주는 기본값이 아니라, 훅이 모든 예외를 잡아
        # 2 로 끝내야 얻는 성질이다. 이 훅은 반대를 고른다: **훅이 학생의 작업을 세우면 안 된다.**
        # 그래서 이건 경계가 아니다.
        return 0

    tool = payload.get("tool_name") or ""
    if tool not in WRITERS and tool not in SHELL_TOOLS:
        return 0

    for target in targets(tool, payload):
        why = protected(target)
        if why:
            print(
                f"{target} 는 고칠 수 없다.\n"
                f"  {why}\n"
                f"  사람이 직접 고쳐야 하는 파일이다. .claude/hooks/protect_sources.py 를 본다.",
                file=sys.stderr,
            )
            return 2   # 2 = 도구 실행을 막고 이 메시지를 모델에게 돌려준다
    return 0


if __name__ == "__main__":
    sys.exit(main())
