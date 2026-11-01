#!/usr/bin/env bash
# 승인 — 사람이 알림 문구를 읽고 그 지문으로 승인한다. 승인되면 번호표로 한 번만 보낸다.
#
#   scripts/approve.sh <요청 id> <지문 64자리>
#
# 지문은 요청이 승인 대기로 끝날 때 화면(과 requests/<요청 id>/approval.json)에 나온다.
# 이 명령은 사람이 친다. Claude 는 부르지 못한다(policies.yaml · .claude/settings.json 이 막는다).
# 끝날 때 신호: 0 보냈다 또는 이미 보냈다 · 3 사람이 볼 것(지문이 다르거나 보냈는지 모르는 것) · 64 쓰는 법이 틀렸다
set -u
cd "$(dirname "$0")/.." || exit 64
if [ $# -ne 2 ]; then
  echo "쓰는 법: scripts/approve.sh <요청 id> <지문 64자리>"
  exit 64
fi
exec uv run python scripts/deliver.py "$1" "$2"
