---
description: 알림 요청 하나를 끝까지 처리한다 — 수집·작성·검수·승인 요청 (보내기는 사람이 승인한 뒤 스크립트가 한다)
argument-hint: <요청 id, 예: alert-2026-08-31-1a2b3c4d>
allowed-tools: Read, Agent, Task, Bash(uv run python scripts/open_request.py:*)
---

## 요청을 연 결과

!`uv run python scripts/open_request.py $ARGUMENTS`

## 할 일

당신은 이 요청의 진행을 맡습니다. 알림 문구를 직접 쓰지 않고, 알림을 보내지 않습니다.

1. 위 결과가 "작성" 을 요구하면, 하위 에이전트 **drafter** 에게 맡깁니다. 백그라운드로 띄우지 말고 끝날 때까지 기다립니다. 요청 id 와 쓸 곳을 그대로 넘기고, 검수가 거부한 이유가 적혀 있으면 그 줄들도 그대로 넘깁니다.
2. drafter 가 끝나면 멈춥니다. 검수와 승인 요청(문구의 지문 적기)은 차단 장치가 합니다.
3. 멈추려 할 때 차단 장치가 다시 작성하라고 하면, drafter 를 **다시 불러** 그 안내를 그대로 넘깁니다. 기다리기 예약이나 다른 도구로 시간을 보내지 않습니다.
4. 그 밖의 일은 하지 않습니다. 보내는 명령(`scripts/approve.sh`)은 사람이 칩니다.
