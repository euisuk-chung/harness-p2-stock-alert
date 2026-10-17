"""이 프로젝트의 검사와 그 결과 형식(GuardResult · verdict).

    C1  schema_valid    형식이 계약을 지켰나
    C2  hit_coverage    걸린 조건과 문장이 1:1 인가
    C3  value_exact     **숫자가 관측값과 같은가**
    C4  no_forecast     전망·투자 판단을 썼나

블로그의 G1~G5 와 **하나도 겹치지 않는다.** 업무가 다르기 때문이다.
겹치는 것은 `GuardResult` 형식과 "지표 하나에 검사 하나" 규칙뿐이다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class GuardResult:
    """검사 하나의 결과."""

    guard: str            # C1 … C4
    metric_name: str      # schema_valid, value_exact …
    metric: Any           # bool 또는 0.0~1.0
    passed: bool
    detail: list[str] = field(default_factory=list)

    def format_metric(self) -> str:
        if isinstance(self.metric, bool):
            return "ok" if self.metric else "fail"
        if isinstance(self.metric, (int, float)):
            return f"{self.metric:.2f}"
        return str(self.metric)


def verdict(results: list[GuardResult]) -> bool:
    """전부 통과해야 통과다. 하나라도 거부면 거부다."""
    return all(r.passed for r in results)


# 결과 형식을 먼저 정의한 뒤에 검사를 싣는다 — 검사가 `from . import GuardResult` 로 읽는다.
from . import c1_schema, c2_hit_coverage, c3_value_exact, c4_no_forecast

__all__ = ["GuardResult", "verdict",
           "c1_schema", "c2_hit_coverage", "c3_value_exact", "c4_no_forecast"]
