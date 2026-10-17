# 주가 조건 알림 — 장중 5분마다 시세를 보고, 걸린 조건만 한 번 알린다

이 폴더에서 대화 창을 연다: `cd alert` 뒤 `claude --add-dir ..`(모델은 3차시에 `/model` 로 저장해 둔 본 에이전트 모델) (한 층 위 PRD.md 를 읽으려고 `--add-dir ..`).
Claude Code 는 연 폴더의 `.claude/` 만 읽는다 — 다른 폴더에서 열면 아래 장치가 걸리지 않는다.
시세는 지어낸 고정 파일(`cases/data/`)이고 실제 매매는 없다.

## 이 프로젝트의 선언

| 업무 말 | 이름 | 이 프로젝트에서 |
|---|---|---|
| 요청 | `request` | cron 이 장중 5분마다 `scripts/request.sh` 를 부른다(`scripts/cron.example`). 먼저 모델 없이 시세를 보고 **아무것도 안 걸렸거나 이미 보냈으면 `quiet` — 요청을 열지 않고 Claude 도 부르지 않는다.** 걸렸으면 요청 id `alert-<관측 날짜>-<걸린 알림 이름들의 지문 8자리>`, 요청 폴더 `requests/<요청 id>/`. 같은 조건 묶음의 둘째 tick 은 `skip` |
| 수집 | `collect` | `scripts/collect.py` — 시세를 읽고 사람이 적은 조건(`cases/*.yaml`)과 코드로 비교한다. 모델 없음 |
| 작성 | `draft` | 하위 에이전트 `drafter`(`.claude/agents/drafter.md`, 도구 Read·Write) 가 걸린 조건만 보고 `requests/<id>/draft.json` 에 알림 문구를 쓴다 |
| 검수 | `review` | `review.py --request <id>` — 검사 넷 C1~C4(형식 · 걸린 조건과 1:1 · 값이 관측값 그대로 · 뻔한 전망 낱말 없음). 모델 없음 |
| 승인 | `approve` | `scripts/request_approval.py` — 문구 파일의 지문(SHA-256 64자리)을 적고 멈춘다(`needs_approval`). 사람이 문구를 읽고 `scripts/approve.sh <요청 id> <지문>` |
| 전달 | `deliver` | `scripts/deliver.py`(approve.sh 가 부른다) — 지문을 지금 파일과 다시 대 보고, 알림마다 번호표(`lib/idempotency.py`, 이름 `alert:<종목>:<조건>:<날짜>`)를 받은 것만 `lib/sink.py` 로 보낸다. **Claude 에게는 보내는 길이 없다** |

- 명령: `/handle-request <요청 id>`(요청 하나를 끝까지 — 보내기 전 승인 대기까지)
- 차단 장치(`.claude/settings.json`): `protect_sources.py`(시세·번호표 기록 보호) · `guard.py`(막기 — `policies.yaml`) · `run_review.py`(문구가 쓰이면 검수) · `next_step.py`(멈추려 할 때 다음 단계 — `graph.yaml`) · `show_status.py`(새 세션에 진행표). 이 가운데 `protect_sources.py` 는 지킬 폴더가 달라 고쳐 썼고, 나머지 넷(과 `_state.py`)은 블로그와 **같은 파일**이다. 그 밖에 다른 것은 설정 파일의 거부 목록 · `graph.yaml` · `policies.yaml` · 명령 · 에이전트다
- 흐름도 `graph.yaml` · 정책 `policies.yaml`
- 끝 상태: Claude 흐름은 `needs_approval` · `needs_human` · `failed`, 흐름 밖에서 `quiet`(request.sh) · `sent` · `duplicate` · `needs_human`(deliver.py)

## 지킬 것

- 문구는 drafter 만 쓴다. 본 세션은 진행만 맡고, **보내지 않는다** — `scripts/approve.sh`·`scripts/deliver.py` 는 사람이 친다.
- `cases/`(시세·조건)와 `claims/`(번호표 기록)·`outbox/`(보낸 알림)는 고치지 않는다. 번호표 기록을 지우면 이미 보낸 알림이 다시 나간다.
- `state.json`·`events.jsonl`·`STATUS.md`·`approval.json`·`delivery.json` 은 차단 장치와 스크립트가 쓴다. 고치지 않는다.
- 시험(모델 없음): `uv run python tests/test_checks.py` · `uv run python tests/test_scripts.py` · `uv run python scripts/flow_check.py --case case-01-breakout`
