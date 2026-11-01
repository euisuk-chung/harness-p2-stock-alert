"""C1 — 형식이 계약을 지켰나.

여기서 깨지면 나머지를 볼 것도 없다. 그래서 검수(`review.py`)가 여기서 끊는다.
"""

from __future__ import annotations

import json
from pathlib import Path

from . import GuardResult

SCHEMA = Path(__file__).resolve().parents[1] / "schema.json"


def check(message: dict, hits: list[dict]) -> GuardResult:
    import jsonschema

    try:
        jsonschema.validate(message, json.loads(SCHEMA.read_text(encoding="utf-8")))
    except jsonschema.ValidationError as exc:
        where = "/".join(str(p) for p in exc.absolute_path) or "(최상위)"
        return GuardResult("C1", "schema_valid", False, False, [f"{where}: {exc.message}"])
    return GuardResult("C1", "schema_valid", True, True)
