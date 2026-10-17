# 프로젝트 2 — 주가 조건 알림 (완성 코드)

장중 5분마다 시세를 보고, 사람이 적어 둔 조건이 걸린 날에만 알림을 **한 번** 보내는 자동화의 완성 코드입니다. 블로그 자동화와 같은 뼈대(요청 → 수집 → 작성 → 검수 → 승인 → 전달)로 되어 있고, 시세는 지어낸 고정 파일이라 실제 매매나 발송은 없습니다.

## 어디에 두나

이 저장소는 혼자 쓰지 않고, 14차시 첫머리에 내 실습 저장소 맨 위에서 `alert` 폴더로 받아 씁니다.

```bash
git clone https://github.com/euisuk-chung/harness-p2-stock-alert.git alert
```

내 저장소의 `.gitignore` 가 `alert/` 를 빼 두어서, 받은 코드는 내 저장소에 저장되지 않습니다. 이 폴더에는 제 `pyproject.toml` 이 있어서, 여기서 처음 `uv run` 을 치면 폴더 안에 제 `.venv` 가 생깁니다.

## 이 저장소의 지도

| 역할 | 파일 |
|---|---|
| Claude 를 부르는 곳 | `scripts/request.sh` — `claude -p "/handle-request <요청 id>"` 를 부르는 곳은 여기 한 곳 |
| 시작 계기 | `scripts/cron.example` — 장중 5분마다 `scripts/request.sh` |
| 지시 | `CLAUDE.md`(프로젝트 지침) · `.claude/commands/handle-request.md`(본 세션) · `.claude/agents/drafter.md`(작성 담당, 이 업무의 규칙) · `.claude/skills/drafter-rules/SKILL.md`(늘 같은 규칙) · `graph.yaml` 의 `ask`·`retry_hint`(다시 쓰라는 글) |
| 차단 장치 | `.claude/hooks/` — 등록은 `.claude/settings.json` |
| 막는 규칙 | `policies.yaml` · `.claude/settings.json` 의 `permissions.deny` |
| 흐름 단계 (모델 없음) | `scripts/open_request.py` · `scripts/collect.py` · `review.py` 와 `checks/` · `scripts/request_approval.py` · `scripts/deliver.py` |
| 사람이 치는 것 | `scripts/approve.sh`(승인) · `claims_cli.py`(번호표 대사) · `review.py`(검사) |
| 업무 모듈 | `lib/` — 번호표 `idempotency.py` · 보내기 `sink.py` · 케이스 읽기 `case.py` |
| 시험 | `tests/` |
| 입력 견본 | `cases/` |

## 해 보기

`alert` 폴더에서 모델을 부르지 않는 연습 모드로:

```bash
uv run python scripts/collect.py --case case-01-breakout
uv run python scripts/flow_check.py --case case-01-breakout
uv run python tests/test_idempotency.py
```

업무 말과 파일 이름의 대응은 [`CLAUDE.md`](./CLAUDE.md) 의 "이 프로젝트의 선언" 표에 있습니다. 코드는 고치지 않고 실행하며 관찰합니다.
