#!/usr/bin/env bash
# 알림 tick 하나 — cron 이 장중 5분마다 부른다.
#
#   scripts/request.sh                            기본 케이스(case-01-breakout)의 시세로 한 tick
#   scripts/request.sh --case case-02-quiet       다른 케이스
#   scripts/request.sh --dry-run                  부를 명령만 찍는다(시세도 보지 않는다)
#
# 순서: 시세를 읽고 조건을 본다(모델 없음) → 아무것도 안 걸렸으면 quiet 로 끝(Claude 를 부르지 않는다)
#       → 걸렸으면 요청 열기(같은 조건 묶음이 이미 열렸으면 skip) → claude -p "/handle-request <요청 id>" → 마무리.
# 끝날 때 신호: 0 quiet·skip · 3 사람 차례(승인 대기 또는 세 번 거부) · 1 실패.
# 보내는 것은 여기서 하지 않는다 — 사람이 문구를 읽고 scripts/approve.sh 를 친다.
#
# cron 에는 터미널도 로그인한 사람의 PATH 도 없다. 그래서 첫 줄에서 이 프로젝트 폴더로 들어가고
# (이 폴더의 .claude/ 만 걸리게), claude 를 찾을 곳을 PATH 에 채운다.
# 5분 안에 앞 tick 이 안 끝났으면 겹쳐 돌지 않는다(flock -n .tick.lock).
# --bare 는 붙이지 않는다 — 차단 장치(보호·검수·다음 단계)가 모두 꺼진다. tests/test_scripts.py 가 본다.
set -u
cd "$(dirname "$0")/.." || exit 64
export PATH="$HOME/.local/bin:/usr/local/bin:/usr/bin:/bin:$PATH"
# -p 실행에서는 하위 에이전트가 기본으로 백그라운드에서 돈다(공식 문서). 끝날 때까지 기다리게 끈다 — guard.py 도 막는다.
export CLAUDE_CODE_DISABLE_BACKGROUND_TASKS=1
CLAUDE_BIN="${CLAUDE_BIN:-claude}"
# 한 번 부른 Claude 가 10분을 넘기면 끊는다. 운영체제마다 다른 timeout 명령 대신 파이썬으로 건다(scripts/limited.py).

DRY=0; CASE="case-01-breakout"
while [ $# -gt 0 ]; do
  case "$1" in
    --dry-run) DRY=1 ;;
    --case) CASE="$2"; shift ;;
    *) echo "쓰는 법: scripts/request.sh [--dry-run] [--case 케이스]"; exit 64 ;;
  esac
  shift
done

CMD=handle-request
# Claude 를 부르는 옵션은 이 함수 한 곳에 둔다(네 프로젝트가 같은 모양). CMD 는 부를 명령 이름이다.
claude_args() {
  # 본 에이전트의 모델: 구독에 맞춰 환경 변수 ANTHROPIC_MODEL 로 고른다(Pro 는 sonnet, Max 는 opus). 없으면 sonnet.
  # 자동 실행은 --setting-sources project 라 대화 창에서 /model 로 저장한 값을 읽지 않는다 — 그래서 여기서 따로 받는다.
  # 하위 에이전트 drafter 는 .claude/agents/drafter.md 의 model: sonnet(가벼운 일은 가벼운 모델)을 그대로 쓴다.
  MODEL="${ANTHROPIC_MODEL:-sonnet}"
  # 계정에 연결한 커넥터(MCP — 메일·캘린더 같은)는 설정 파일이 아니라 계정에 붙어 있어 --setting-sources 로 빠지지 않는다.
  # 이 실행은 쓰지 않는데 도구 목록이 부를 때마다 문맥에 통째로 실리니 --strict-mcp-config 로 뺀다.
  CLAUDE_ARGS=(-p "/$CMD $1" --model "$MODEL" --setting-sources project --strict-mcp-config --permission-mode dontAsk
    --tools "Read,Write,Agent,Bash"
    --allowedTools "Read,Write,Agent,Task,Bash(uv run python scripts/open_request.py:*)"
    --output-format json)
}

if [ $DRY -eq 1 ]; then
  echo "(dry-run) 시세 보기·요청 열기: uv run python scripts/open_request.py --tick --case $CASE"
  claude_args "<요청 id>"
  echo "(dry-run) 걸렸으면: REQUEST_ID=<요청 id> uv run python scripts/limited.py 600 $CLAUDE_BIN ${CLAUDE_ARGS[*]}"
  echo "(dry-run) 마무리: uv run python scripts/open_request.py --finish <요청 id>"
  exit 0
fi

if command -v flock >/dev/null 2>&1; then
  exec 9> .tick.lock
  flock -n 9 || { echo "skip: 앞 tick 이 아직 끝나지 않았다 (.tick.lock)"; exit 0; }
fi

out=$(uv run python scripts/open_request.py --tick --case "$CASE"); code=$?
echo "$out" | grep -v '^REQUEST_ID=' | head -1
[ $code -eq 10 ] && exit 0                     # 이미 연 요청 — 승인을 기다리는 중이면 Claude 를 또 부르지 않는다
[ $code -eq 11 ] && exit 0                     # quiet — 보낼 것이 없다. 요청도 열지 않았다
echo "$out" | grep -q '^REQUEST_ID=' || exit "$code"
RID=$(echo "$out" | sed -n 's/^REQUEST_ID=//p')

if [ $code -eq 0 ]; then
  claude_args "$RID"
  REQUEST_ID="$RID" uv run python scripts/limited.py 600 "$CLAUDE_BIN" "${CLAUDE_ARGS[@]}" \
    > "requests/$RID/claude.json" 2> "requests/$RID/claude.err"
fi
uv run python scripts/open_request.py --finish "$RID"
