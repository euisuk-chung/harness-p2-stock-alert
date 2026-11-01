"""C4 — **뻔한 전망 낱말이 들어갔나.** 의미를 보는 검사가 아니다.

알림은 **일어난 일**만 말해야 한다. *"더 오를 것으로 보인다"* 는
우리가 책임질 수 없는 말이고, 자동으로 나가면 안 된다.

## ⚠️ 이름을 `no_forecast` 라고 하면 안 된다

*"전망이 없다"* 는 **보증처럼 들린다.** 이 검사는 그걸 보증하지 못한다.
그래서 지표 이름이 `no_explicit_forecast_terms` 다 —
**"뻔한 전망 낱말이 없다"** 까지가 이 검사가 말할 수 있는 전부다.

    "더 오를 것으로 예상됩니다"   → 잡는다
    "흐름이 나쁘지 않습니다"      → **못 잡는다**

낱말을 늘려도 해결되지 않는다. 늘릴수록 멀쩡한 문장이 걸린다.
**알려진 통과 사례**를 `test_checks.py` 가 고정해 둔다 — 못 잡는다는 것을 잊지 않게.

> 이건 정밀도 높은 lint 이지 안전 보증이 아니다.
> 자유 문장이 사람에게 나가는 경로에는 **사람 승인**이 함께 있어야 한다.
"""

from __future__ import annotations

from . import GuardResult

# 명백한 것만 둔다. 늘리면 오탐이 는다.
FORECAST = (
    "전망", "예상", "기대", "추천", "매수", "매도", "목표가",
    "오를 것", "내릴 것", "상승할", "하락할", "유망",
)


def check(message: dict, hits: list[dict]) -> GuardResult:
    texts = [message.get("headline", "")] + [l.get("text", "") for l in message.get("lines", [])]
    found = sorted({w for t in texts for w in FORECAST if w in t})
    detail = [f"전망·판단으로 읽히는 표현: {', '.join(found)}"] if found else []
    return GuardResult("C4", "no_explicit_forecast_terms", not found, not found, detail)
