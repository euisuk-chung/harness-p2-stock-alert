"""검사기가 옳게 도는가.

    uv run python tests/test_checks.py

블로그의 `test_checks.py` 와 **같은 질문, 다른 검사**다.

    review.py --case X  이 알림을 내보내도 되나           (검수)
    test_checks.py      검사기가 제대로 잡고 통과시키나    (검사기 테스트)

**거부돼야 할 것이 거부되면 이 테스트는 성공이다.**
그래서 검사마다 **자기 실패를 일부러 먹인다.** 통과만 확인하는 테스트는 의미가 없다.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "lib"))

from _utf8 import force_utf8  # noqa: E402
from case import Case, Condition, Quote, find_cases  # noqa: E402
from checks import c1_schema, c2_hit_coverage, c3_value_exact, c4_no_forecast  # noqa: E402

failures: list[str] = []

HITS = [
    {"condition_id": "aapl-above-200", "symbol": "AAPL", "kind": "above",
     "threshold": 200, "value": 214.6, "describe": "AAPL 200 위로 돌파"},
    {"condition_id": "nvda-volume-spike", "symbol": "NVDA", "kind": "volume_above",
     "threshold": 200000000, "value": 233104000, "describe": "NVDA 거래량 급증"},
]


def good() -> dict:
    data = json.loads(
        (ROOT / "samples" / "reference" / "case-01-breakout.json").read_text(encoding="utf-8"))
    data.pop("_note", None)
    return data


def variant(fn):
    m = good()
    fn(m)
    return m


def expect(label: str, module, message: dict, should_pass: bool) -> None:
    r = module.check(message, HITS)
    ok = r.passed == should_pass
    if not ok:
        failures.append(f"{label}: {r.guard} 기대 {'통과' if should_pass else '거부'}, 실제 반대")
    want = "통과해야" if should_pass else "잡아야"
    print(f"   {'OK  ' if ok else 'FAIL'} {r.guard} {r.metric_name:<14} "
          f"{r.format_metric():>5}  {want} | {label}")


def conditions() -> None:
    print("1. 조건 판정이 결정론인가")
    q = Quote("AAPL", 214.6, 198.2, 84213000)
    cases = [
        ("200 위 → 걸린다", Condition("x", "AAPL", "above", 200), True),
        ("220 위 → 안 걸린다", Condition("x", "AAPL", "above", 220), False),
        ("경계값은 초과여야 걸린다", Condition("x", "AAPL", "above", 214.6), False),
        ("아래로 이탈", Condition("x", "AAPL", "below", 220), True),
        ("거래량", Condition("x", "AAPL", "volume_above", 80000000), True),
    ]
    for label, cond, want in cases:
        got = cond.hits(q)
        good_ = got == want
        if not good_:
            failures.append(f"조건 판정: {label}")
        print(f"   {'OK  ' if good_ else 'FAIL'} {label}")

    try:
        Condition("x", "AAPL", "sideways", 1).hits(q)
        failures.append("모르는 조건 종류가 통과했다")
        print("   FAIL 모르는 조건 종류는 거부한다")
    except ValueError:
        print("   OK   모르는 조건 종류는 거부한다")


def expect_cases() -> None:
    print("\n2. 케이스가 기대대로 판정되는가")
    for path in find_cases(ROOT / "cases"):
        case = Case.load(path)
        quotes = case.quotes()
        hit_ids = sorted(c.id for c in case.conditions
                         if c.symbol in quotes and c.hits(quotes[c.symbol]))
        want = sorted(case.expected_hits)
        ok = hit_ids == want
        if not ok:
            failures.append(f"{case.name}: 기대 {want}, 실제 {hit_ids}")
        print(f"   {'OK  ' if ok else 'FAIL'} {case.name:<20} 기대 {want} 실제 {hit_ids}")


def injected() -> None:
    print("\n3. 각 검사가 자기 실패를 잡는가")
    expect("정상 입력", c1_schema, good(), True)
    expect("없는 필드 추가", c1_schema,
           variant(lambda m: m.__setitem__("verdict", "buy")), False)
    expect("value 를 문자열로", c1_schema,
           variant(lambda m: m["lines"][0].__setitem__("value", "214.6")), False)

    expect("정상 입력", c2_hit_coverage, good(), True)
    expect("한 조건을 빠뜨렸다", c2_hit_coverage,
           variant(lambda m: m["lines"].pop()), False)
    expect("**걸리지 않은 조건을 지어냈다**", c2_hit_coverage,
           variant(lambda m: m["lines"].append(
               {"condition_id": "tsla-above-300", "text": "TSLA 돌파", "value": 310.0})), False)

    expect("정상 입력", c3_value_exact, good(), True)
    expect("**값을 반올림했다** (215 는 214.6 이 아니다)", c3_value_exact,
           variant(lambda m: m["lines"][0].__setitem__("value", 215.0)), False)
    expect("값을 지어냈다", c3_value_exact,
           variant(lambda m: m["lines"][1].__setitem__("value", 999999999)), False)

    expect("정상 입력", c4_no_forecast, good(), True)
    expect("뻔한 전망 낱말", c4_no_forecast,
           variant(lambda m: m["lines"][0].__setitem__(
               "text", "AAPL이 214.6에 거래되며 더 오를 것으로 예상됩니다.")), False)
    expect("매수 추천", c4_no_forecast,
           variant(lambda m: m.__setitem__("headline", "AAPL 매수 추천")), False)

    # ⚠️ **알려진 통과 사례.** 못 잡는다는 것을 잊지 않으려고 고정해 둔다.
    #    이건 결함이 아니라 이 검사의 범위다 — 낱말을 늘려도 해결되지 않는다.
    expect("**의미상 전망인데 낱말이 없다 — 못 잡는다**", c4_no_forecast,
           variant(lambda m: m["lines"][0].__setitem__(
               "text", "AAPL이 214.6에 거래되며 흐름이 나쁘지 않습니다.")), True)

    # 검사끼리 못 잡는 것을 보여 준다 — 블로그의 G2/G5 대비와 같은 자리
    forecast = variant(lambda m: m["lines"][0].__setitem__(
        "text", "AAPL이 214.6에 거래되며 더 오를 것으로 예상됩니다."))
    expect("〃 **같은 입력을 C3 이 보면**", c3_value_exact, forecast, True)


def main() -> int:
    force_utf8()
    conditions()
    expect_cases()
    injected()

    print()
    if failures:
        print(f"실패 {len(failures)}건")
        for line in failures:
            print(f"   {line}")
        return 1
    print("검사기가 기대대로 동작한다.")
    print("주목할 두 곳:")
    print("  · 숫자가 정확해도 전망을 쓸 수 있다. 그래서 C4 가 따로 있다.")
    print("  · **C4 도 못 잡는 것이 있다** — \"흐름이 나쁘지 않습니다\" 는 통과한다.")
    print()
    print("그래서 **모델이 쓴 자유 문장은 사람 승인 뒤에만 나간다** (scripts/request_approval.py → scripts/approve.sh).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
