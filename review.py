"""검수 — 이 알림 문구를 내보내도 되는지 검사 넷(C1~C4)으로 판정한다. 모델을 부르지 않는다.

    uv run python review.py --request alert-2026-08-31-1a2b3c4d   요청 폴더의 문구(requests/<id>/draft.json)를 검수. 흐름이 부른다
    uv run python review.py --case case-01-breakout             저장된 답(samples/reference/)을 검수. 끝날 때 신호가 곧 판정

블로그의 검사(G1~G5)와 **같은 모양, 다른 검사**다.

    블로그   출처가 있나 · 인용이 원문 그대로인가 · 빠진 항목이 없나
    알림     **숫자가 관측값과 같은가** · 걸린 조건을 빠뜨리거나 더했나 · 전망 낱말을 썼나

끝날 때 신호
    0  내보내도 된다(승인 요청으로 간다)
    1  거부 — C1 형식 위반도 여기다. 흐름도가 작성(drafter)으로 되돌린다
    2  문구 파일을 읽을 수 없다(JSON 이 아니다) — 흐름도가 failed 로 끝낸다
    64 요청 폴더에 걸린 조건(hits.json)이 없다

"검사기 자체가 옳게 도는가" 는 다른 질문이다. `test_checks.py` 가 답한다.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "lib"))

from _utf8 import force_utf8  # noqa: E402
from case import Case, find_cases  # noqa: E402
from checks import GuardResult, c1_schema, c2_hit_coverage, c3_value_exact, c4_no_forecast, verdict  # noqa: E402

CHECKS = [c1_schema, c2_hit_coverage, c3_value_exact, c4_no_forecast]

# 되돌려 보낼 때 **무엇을 고칠지** 함께 준다. 안 그러면 같은 요청을 다시 뽑는 것과 같다.
HOW = {
    "schema_valid": "출력 형식이 계약과 다르다. schema.json 을 정확히 지켜라.",
    "hit_coverage": "걸린 조건과 문장이 맞지 않는다. 빠뜨리거나 더하지 마라.",
    "value_exact": "관측값을 바꿨다. 주어진 숫자를 그대로 넣어라.",
    "no_explicit_forecast_terms": "전망·판단 표현을 썼다. 일어난 일만 적어라.",
}


def run_checks(message: dict, hits: list[dict]) -> list[GuardResult]:
    results = []
    for check in CHECKS:
        r = check.check(message, hits)
        results.append(r)
        if r.guard == "C1" and not r.passed:
            break  # 형식이 깨졌으면 나머지는 볼 것도 없다
    return results


def case_hits(case: Case) -> list[dict]:
    """케이스의 걸린 조건 — 수집(scripts/collect.py)과 같은 판정을 번호표 기록 없이 한다."""
    quotes = case.quotes()
    hits = []
    for c in case.conditions:
        q = quotes.get(c.symbol)
        if q is not None and c.hits(q):
            hits.append({"condition_id": c.id, "symbol": c.symbol, "kind": c.kind, "threshold": c.threshold,
                         "value": q.volume if c.kind == "volume_above" else q.last, "describe": c.describe()})
    return hits


def gate(name: str) -> int:
    """저장된 답 하나를 검수한다. 끝날 때 신호가 곧 판정이다."""
    paths = [p for p in find_cases(ROOT / "cases") if p.stem == name]
    if not paths:
        raise SystemExit(f"그런 케이스가 없다: {name}")
    sample = ROOT / "samples" / "reference" / f"{name}.json"
    if not sample.is_file():
        raise SystemExit(f"저장된 답이 없다: samples/reference/{sample.name}")
    message = json.loads(sample.read_text(encoding="utf-8"))
    message.pop("_note", None)
    results = run_checks(message, case_hits(Case.load(paths[0])))
    ok = verdict(results)
    print(" · ".join(f"{r.guard} {'pass' if r.passed else 'rejected'}" for r in results))
    print(f"{name}: {'내보내도 됨' if ok else '거부'}")
    for r in results:
        for line in r.detail:
            print(f"   {r.guard}  {line}")
    return 0 if ok else 1


def review_request(rid: str) -> int:
    """요청 폴더의 문구를 검수한다. 걸린 조건은 수집 단계가 적어 둔 hits.json."""
    d = ROOT / "requests" / rid
    try:
        hits = json.loads((d / "hits.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        print(f"review: 걸린 조건을 읽을 수 없다 ({type(e).__name__})")
        return 64
    try:
        message = json.loads((d / "draft.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        print(f"review: 형식 위반 — draft.json 을 읽을 수 없다 ({type(e).__name__})")
        return 2
    if not isinstance(message, dict):
        message = {"_not_an_object": message}   # C1 이 거부한다
    results = run_checks(message, hits)
    (d / "review.json").write_text(json.dumps(
        {r.guard: {"pass": r.passed, "metric": r.metric_name, "detail": r.detail} for r in results},
        ensure_ascii=False, indent=2), encoding="utf-8")
    print(" · ".join(f"{r.guard} {'pass' if r.passed else 'rejected'}" for r in results))
    bad = [r for r in results if not r.passed]
    for r in bad:
        for line in r.detail:
            print(f"review: {r.guard} rejected — {line}")
        print(f"review: {r.guard} rejected — {HOW.get(r.metric_name, r.metric_name)}")
    return 1 if bad else 0


def main() -> int:
    force_utf8()
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--request", help="요청 폴더의 문구를 검수한다(흐름이 부른다)")
    parser.add_argument("--case", help="저장된 답 하나를 검수한다")
    args = parser.parse_args()
    if args.request:
        return review_request(args.request)
    if args.case:
        return gate(args.case)
    parser.print_help()
    return 64


if __name__ == "__main__":
    sys.exit(main())
