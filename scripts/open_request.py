"""요청 열기·마무리 — 모델을 부르지 않는다.

    uv run python scripts/open_request.py --tick [--case case-01-breakout]   5분마다 한 번(cron 의 한 tick)
    uv run python scripts/open_request.py <요청 id>                           이미 연 요청의 지금 안내
    uv run python scripts/open_request.py --finish <요청 id>                  claude 가 끝난 뒤 마무리

## tick 하나가 하는 일

먼저 **모델 없이** 시세를 읽고 조건을 본다(scripts/collect.py 의 evaluate).
아무것도 안 걸렸거나 걸린 것을 모두 이미 보냈으면 `quiet` 를 찍고 끝난다 — 요청 폴더도 만들지 않고
Claude 도 부르지 않는다. 5분마다 도는데 대부분의 tick 은 할 일이 없다. 그때마다 모델을 부르면 낭비다.

걸린 것이 있으면 요청 id(`alert-<관측 날짜>-<걸린 알림 이름들의 지문 8자리>`)로 요청 폴더를
**먼저 만드는(mkdir) 쪽 하나만** 이긴다. 5분 뒤 같은 조건이 또 걸리면 같은 id 라 만들지 못하고 `skip` 이다 —
승인을 기다리는 동안 Claude 를 또 부르지 않는다.

끝날 때 신호
  열기     0 작성이 필요하다(claude 를 부른다) · 10 이미 연 요청(건너뜀) · 11 quiet(보낼 것 없음) · 64 케이스·시세 문제
  마무리   3 needs_approval · needs_human(사람 차례) · 1 failed
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / ".claude" / "hooks"))
sys.path.insert(0, str(ROOT / "scripts"))
from _state import REQ, advance, event, load, rdir, save, utf8  # noqa: E402
from collect import evaluate, load_case, request_id  # noqa: E402

SKIP = 10
QUIET = 11
DEFAULT_CASE = "case-01-breakout"


def new_state(rid: str, case: str, trigger: str) -> dict:
    return {"id": rid, "case": case, "trigger": trigger, "at": "collect", "visits": {}, "history": [],
            "tool_calls": 0, "terminal": None, "reason": ""}


def start(rid: str, case: str, trigger: str, headline: str) -> int:
    save(rid, new_state(rid, case, trigger))
    event(rid, "triggered", trigger=trigger, case=case)
    (REQ / ".current").write_text(rid, encoding="utf-8")
    print(headline)
    r = advance(rid, "open")
    print(r["message"])
    print(f"REQUEST_ID={rid}")
    return 0 if r["need"] else finish_code(load(rid))


def open_tick(case_name: str) -> int:
    try:
        ev = evaluate(load_case(case_name))
    except FileNotFoundError as exc:
        print(f"collect: {exc}")
        return 64
    if ev["missing"]:
        print(f"collect: 조건이 가리키는 종목의 시세가 없다: {', '.join(ev['missing'])}")
        return 64
    date = ev["observed_date"]
    if not ev["hits"]:
        if ev["already"]:
            print(f"quiet: {date} 이미 보낸 알림이다({', '.join(ev['already'])}) — Claude 를 부르기 전에 걸러냈다")
        else:
            print(f"quiet: {date} 걸린 조건이 없다 — 알림을 보내지 않는다(Claude 를 부르지 않았다)")
        return QUIET
    rid = request_id(date, ev["hits"])
    REQ.mkdir(exist_ok=True)
    try:
        rdir(rid).mkdir()                      # 먼저 만드는 쪽 하나만 이긴다
    except FileExistsError:
        st = load(rid)
        if st.get("terminal"):
            print(f"skip: already ran {rid} ({st['terminal']})")
            return SKIP
        if not st:
            print(f"skip: {rid} 은 다른 tick 이 여는 중이다")
            return SKIP
        print(f"resume: {rid} 은 {st.get('at')} 단계에서 멈춰 있다")
        return reopen(rid)
    ids = ", ".join(h["condition_id"] for h in ev["hits"])
    return start(rid, case_name, f"schedule:5min:{case_name}:{date}",
                 f"open: {rid} — {date} 걸린 조건 {len(ev['hits'])}개: {ids}")


def reopen(rid: str) -> int:
    if not load(rid):
        print(f"그런 요청이 없다: {rid}")
        return 64
    (REQ / ".current").write_text(rid, encoding="utf-8")
    r = advance(rid, "open")
    print(r["message"])
    print(f"REQUEST_ID={rid}")
    return 0 if r["need"] else finish_code(load(rid))


def finish_code(st: dict) -> int:
    return 3 if st.get("terminal") in ("needs_approval", "needs_human") else 1


def path_lines(st: dict) -> list[str]:
    """지나온 길을 화면 줄로. 검수가 거부한 뒤 다시 작성한 것은 retry n/m 으로 적는다."""
    hist = st.get("history", [])
    out, first, retry, cap, i = [], [], 0, 3, 0
    while i < len(hist) and not (hist[i]["step"] == "review" and hist[i]["signal"] != 0):
        first.append(hist[i]["step"])
        i += 1
    if i >= len(hist):
        out.append(" → ".join(first + [st.get("terminal") or "?"]))
    else:
        first.append("review")
        out.append(" → ".join(first))
        out.append(f"review: {hist[i]['note'].replace('review: ', '')}")
        i += 1
        while i < len(hist):
            h = hist[i]
            if h["step"] == "draft":
                retry += 1
                nxt = hist[i + 1] if i + 1 < len(hist) else None
                tail = ""
                if nxt and nxt["step"] == "review":
                    tail = " → review: " + ("pass" if nxt["signal"] == 0 else nxt["note"].replace("review: ", ""))
                    i += 1
                out.append(f"draft (retry {retry}/{cap - 1}){tail}")
            else:
                out.append(h["step"])
            i += 1
    if st.get("terminal") == "needs_approval":
        out.append("done: needs_approval — 모델이 쓴 문장이라 사람이 읽고 승인해야 나간다")
    else:
        out.append(f"done: {st.get('terminal')}" + (f" — {st['reason']}" if st.get("reason") else ""))
    return out


def finish(rid: str) -> int:
    st = load(rid)
    if not st:
        print(f"그런 요청이 없다: {rid}")
        return 1
    if not st.get("terminal"):
        advance(rid, "finish")
        st = load(rid)
    for line in path_lines(st):
        print(line)
    ap = rdir(rid) / "approval.json"
    if st.get("terminal") == "needs_approval" and ap.is_file():
        a = json.loads(ap.read_text(encoding="utf-8"))
        print(f"문구: {a['file']}")
        print(f"승인하려면: scripts/approve.sh {rid} {a['sha256']}")
    cur = REQ / ".current"
    if cur.is_file() and cur.read_text(encoding="utf-8").strip() == rid:
        cur.unlink()
    return finish_code(st)


def main() -> int:
    utf8()
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("rid", nargs="?", help="이미 연 요청 id")
    p.add_argument("--tick", action="store_true", help="cron 의 한 tick — 시세를 보고 걸렸을 때만 요청을 연다")
    p.add_argument("--case", default=DEFAULT_CASE, help=f"시세·조건 케이스 (기본 {DEFAULT_CASE})")
    p.add_argument("--finish", metavar="요청_ID", help="claude 가 끝난 뒤 마무리")
    a = p.parse_args()
    if a.finish:
        return finish(a.finish)
    if a.tick:
        return open_tick(a.case)
    if a.rid:
        return reopen(a.rid)
    p.print_help()
    return 64


if __name__ == "__main__":
    sys.exit(main())
