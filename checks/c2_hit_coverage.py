"""C2 — 걸린 조건과 문장이 **1:1** 인가.

빠뜨리는 것도, 없는 걸 만드는 것도 잡는다.

    빠뜨림   중요한 알림이 안 간다
    더함     **일어나지 않은 일을 알린다** — 이쪽이 더 나쁘다
"""

from __future__ import annotations

from . import GuardResult


def check(message: dict, hits: list[dict]) -> GuardResult:
    expected = {h["condition_id"] for h in hits}
    actual = {line["condition_id"] for line in message.get("lines", [])}

    missed = sorted(expected - actual)
    invented = sorted(actual - expected)
    detail = []
    if missed:
        detail.append(f"빠뜨렸다: {', '.join(missed)}")
    if invented:
        detail.append(f"**걸리지 않은 조건을 썼다**: {', '.join(invented)}")

    matched = len(expected & actual)
    total = len(expected | actual) or 1
    score = matched / total
    return GuardResult("C2", "hit_coverage", score, not (missed or invented), detail)
