"""수집 — 시세를 읽고 조건을 판정한다. **결정적이다. 모델을 부르지 않는다.**

    uv run python scripts/collect.py <요청 id>          요청 폴더에 quotes.json · hits.json · brief.json 을 쓴다(흐름이 부른다)
    uv run python scripts/collect.py --case <케이스>    판정만 찍어 본다(아무것도 쓰지 않는다)

실습에서는 케이스(cases/*.yaml)에 적힌 고정 시세를 읽는다. 실무라면 여기가 시세 API 다.
**바뀌는 것은 시세를 읽는 한 줄**이고, 뒤의 판정·검수·번호표는 그대로다.

## 왜 모델에 맡기지 않나

*"AAPL 이 200 을 넘었나"* 는 비교 연산이다. 모델에 물으면 세 가지가 나빠진다.

    검사할 게 없어진다   정답을 우리가 아는데 모델 답을 무엇으로 검증하나
    값이 흔들린다        같은 시세에 다른 답이 나올 수 있다
    비싸다              비교 하나에 모델 호출 — 5분마다라면 하루 수십 번

> **기계적으로 판정할 수 있는 것은 코드로 먼저 고정한다.**
> 모델은 그 결과를 **사람이 읽을 문장으로** 옮기는 데만 쓴다(drafter).

조건은 **사람이 적는다**(cases/*.yaml). 모델이 정하지 않는다.

## 이미 보낸 것은 여기서 미리 거른다

번호표를 받는 일(claim)은 뒤의 전달(scripts/deliver.py)에 있어서, 그냥 두면
**중복인 걸 알기 전에 Claude 를 부른다.** 낭비다. 그래서 여기서 번호표 기록을 **읽기만** 해 본다.

    읽기 (여기)      싸다 · 되돌릴 게 없다
    번호표 (전달)    파일을 만든다 · 실패하면 풀어 줘야 한다

> **읽기로 거르고, 찍기는 늦게 한다.** 읽기는 경합에 안전하지 않지만
> 어차피 전달이 번호표로 다시 판정한다. 여기 것은 **아끼기이지 보장이 아니다.**
> DONE(다 보냈다)만 뺀다. SENDING·CLAIMED 은 여기서 거르면 애매한 건이 조용히 사라진다.

끝날 때 신호 (요청 id 로 부를 때)
  0 걸린 조건을 적었다 · 2 조건이 가리키는 종목의 시세가 없다 · 64 시세 파일이 없거나 보낼 것이 없다
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))
from case import Case  # noqa: E402
from idempotency import IdempotencyStore, Phase, claims_root  # noqa: E402


def alert_key(condition_id: str, symbol: str, observed_date: str) -> str:
    """이 알림을 가리키는 이름(번호표 이름). **효과의 뜻**으로 만든다.

    실행 id 를 넣지 않는다 — 5분마다 다른 이름이 되어 아무것도 안 막힌다.
    날짜를 넣는다 — 빼면 한 번 보낸 뒤로 영영 안 나간다.
    """
    return f"alert:{symbol}:{condition_id}:{observed_date}"


def evaluate(case: Case) -> dict:
    """시세를 읽고 조건을 판정한다. 같은 시세·같은 조건·같은 번호표 기록이면 늘 같은 답이다.

    돌려주는 것: observed_date · quotes · hits(보낼 것) · already(이미 보낸 조건 id) · missing(시세 없는 종목)
    """
    quotes = case.quotes()
    hits, missing = [], []
    for condition in case.conditions:
        quote = quotes.get(condition.symbol)
        if quote is None:
            missing.append(condition.symbol)
            continue
        if condition.hits(quote):
            hits.append({
                "condition_id": condition.id,
                "symbol": condition.symbol,
                "kind": condition.kind,
                "threshold": condition.threshold,
                # 그 조건이 실제로 본 값. **검수(C3)가 이걸 문장과 대조한다.**
                "value": quote.volume if condition.kind == "volume_above" else quote.last,
                "describe": condition.describe(),
            })

    # 이미 보낸 것을 뺀다. **읽기만** 한다 — 찍는 것은 전달의 일이다.
    store = IdempotencyStore(claims_root(ROOT / "claims"))
    fresh, already = [], []
    for hit in hits:
        record = store.read(alert_key(hit["condition_id"], hit["symbol"], case.observed_date))
        if record is not None and record.phase == Phase.DONE.value:
            already.append(hit["condition_id"])
        else:
            fresh.append(hit)

    return {
        "observed_date": case.observed_date,
        "quotes": {s: {"last": q.last, "prev_close": q.prev_close, "volume": q.volume}
                   for s, q in sorted(quotes.items())},
        "hits": fresh,
        "already": already,
        "missing": sorted(set(missing)),
    }


def request_id(observed_date: str, hits: list[dict]) -> str:
    """요청 id — 관측 날짜 + 걸린 알림 이름들의 지문 8자리.

    같은 날 같은 조건들이 걸리면 늘 같은 id 다. 그래서 5분 뒤 같은 tick 은
    요청 폴더를 새로 만들지 못하고 건너뛴다(scripts/open_request.py).
    """
    keys = sorted(alert_key(h["condition_id"], h["symbol"], observed_date) for h in hits)
    return f"alert-{observed_date}-{hashlib.sha256(chr(10).join(keys).encode('utf-8')).hexdigest()[:8]}"


def load_case(name: str) -> Case:
    path = ROOT / "cases" / f"{name}.yaml"
    if not path.is_file():
        raise FileNotFoundError(f"그런 케이스가 없다: {name}")
    return Case.load(path)


def write_request(rid: str) -> int:
    d = ROOT / "requests" / rid
    state = json.loads((d / "state.json").read_text(encoding="utf-8"))
    try:
        case = load_case(state["case"])
        ev = evaluate(case)
    except FileNotFoundError as exc:
        print(f"collect: {exc}")
        return 64
    if not ev["quotes"]:
        print("collect: 시세가 비었다 — cases/data/ 를 본다")
        return 64
    if ev["missing"]:
        print(f"collect: 조건이 가리키는 종목의 시세가 없다: {', '.join(ev['missing'])}")
        return 2
    date = ev["observed_date"]
    (d / "quotes.json").write_text(json.dumps(
        {"observed_date": date, "quotes": ev["quotes"]}, ensure_ascii=False, indent=2), encoding="utf-8")
    (d / "hits.json").write_text(json.dumps(ev["hits"], ensure_ascii=False, indent=2), encoding="utf-8")
    if not ev["hits"]:
        # 요청을 열 때는 걸렸는데 지금은 보낼 것이 없다 — 그사이 다른 요청이 보냈다.
        print("collect: 보낼 것이 없다 — 요청을 연 뒤 이미 보낸 알림이다")
        return 64
    brief = {
        "observed_date": date,
        "hits": [{"condition_id": h["condition_id"], "symbol": h["symbol"],
                  "describe": h["describe"], "value": h["value"]} for h in ev["hits"]],
        "write_to": f"requests/{rid}/draft.json",
    }
    (d / "brief.json").write_text(json.dumps(brief, ensure_ascii=False, indent=2), encoding="utf-8")
    ids = ", ".join(h["condition_id"] for h in ev["hits"])
    print(f"collect: {len(ev['quotes'])}종목 · {date} · 걸린 조건 {len(ev['hits'])}개: {ids}")
    return 0


def main() -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass
    if len(sys.argv) == 3 and sys.argv[1] == "--case":
        try:
            ev = evaluate(load_case(sys.argv[2]))
        except FileNotFoundError as exc:
            print(f"collect: {exc}")
            return 64
        print(json.dumps({k: ev[k] for k in ("observed_date", "hits", "already", "missing")},
                         ensure_ascii=False, indent=2))
        return 0
    if len(sys.argv) != 2 or sys.argv[1].startswith("-"):
        print(__doc__)
        return 64
    return write_request(sys.argv[1])


if __name__ == "__main__":
    sys.exit(main())
