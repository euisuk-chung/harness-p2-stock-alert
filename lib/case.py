"""검사 케이스 — 시세와 조건을 읽는다.

**블로그의 `case.py` 와 다르다.** 같아야 할 이유가 없다 — 업무가 다르다.
같은 것은 차단 장치(.claude/hooks/)와 흐름도(graph.yaml)의 약속이지 케이스 형식이 아니다.

    블로그   sources · required          자료와 빠지면 안 될 항목
    알림     quotes · conditions         시세와 조건

이게 "무엇이 공유되고 무엇이 안 되나" 의 실물이다.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True)
class Quote:
    symbol: str
    last: float
    prev_close: float
    volume: int

    @property
    def change_pct(self) -> float:
        if not self.prev_close:
            return 0.0
        return (self.last - self.prev_close) / self.prev_close * 100


@dataclass(frozen=True)
class Condition:
    """조건은 **사람이 적는다.** 모델이 정하지 않는다.

    숫자 판정을 모델에 맡기면 무엇으로 검사할지가 사라진다.
    모델이 하는 일은 **문장을 쓰는 것**까지다.
    """

    id: str
    symbol: str
    kind: str          # above | below | volume_above
    threshold: float

    KINDS = ("above", "below", "volume_above")

    def hits(self, quote: Quote) -> bool:
        if self.kind == "above":
            return quote.last > self.threshold
        if self.kind == "below":
            return quote.last < self.threshold
        if self.kind == "volume_above":
            return quote.volume > self.threshold
        raise ValueError(f"모르는 조건 종류: {self.kind} (쓸 수 있는 것: {', '.join(self.KINDS)})")

    def describe(self) -> str:
        word = {"above": "위로 돌파", "below": "아래로 이탈", "volume_above": "거래량 급증"}
        # 2억 같은 큰 수가 2e+08 로 찍히지 않게 — 정수면 세 자리마다 쉼표, 소수면 그대로
        t = self.threshold
        num = f"{int(t):,}" if float(t).is_integer() else f"{t:,}"
        return f"{self.symbol} {num} {word[self.kind]}"


@dataclass
class Case:
    """검사 케이스 하나."""

    name: str
    root: Path
    expect: str = "alert"                      # alert | quiet | reject
    data_path: str = ""
    conditions: list[Condition] = field(default_factory=list)
    expected_hits: list[str] = field(default_factory=list)
    _observed: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def load(cls, path: Path) -> Case:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        conditions = [Condition(**c) for c in (raw.get("conditions") or [])]
        for c in conditions:
            if c.kind not in Condition.KINDS:
                raise ValueError(f"{path.name}: 모르는 조건 종류 '{c.kind}'")
        return cls(
            name=path.stem,
            root=path.parent,
            expect=(raw.get("expect") or "alert").strip(),
            data_path=raw.get("data") or "",
            conditions=conditions,
            expected_hits=list(raw.get("expected_hits") or []),
        )

    # ---------- 시세 ----------

    def observed(self) -> dict[str, Any]:
        if not self._observed:
            path = self.root / self.data_path
            if not path.is_file():
                raise FileNotFoundError(f"시세 파일이 없다: {path}")
            self._observed = json.loads(path.read_text(encoding="utf-8"))
        return self._observed

    def quotes(self) -> dict[str, Quote]:
        return {
            q["symbol"]: Quote(q["symbol"], q["last"], q["prev_close"], q["volume"])
            for q in self.observed().get("quotes", [])
        }

    @property
    def observed_date(self) -> str:
        """관측 날짜. **번호표 이름의 일부**가 된다 — 같은 날 같은 조건은 한 번이다."""
        return (self.observed().get("observed_at") or "")[:10]

    def condition(self, cid: str) -> Condition | None:
        return next((c for c in self.conditions if c.id == cid), None)


def find_cases(directory: Path) -> list[Path]:
    """`cases/` 안의 `case-*.yaml` 만 케이스다. `data/` 는 자료 폴더다."""
    if not directory.is_dir():
        return []
    return sorted(directory.glob("case-*.yaml"))
