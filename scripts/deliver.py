"""전달 — 사람이 승인한 알림을 **번호표로 한 번만** 보낸다. 여기를 지나면 되돌릴 수 없다.

    scripts/approve.sh <요청 id> <지문 64자리>      (이 스크립트를 부른다)

Claude 는 이 스크립트를 부르지 못한다 — policies.yaml 의 허용 명령에 없고, 막는 명령에 있고,
.claude/settings.json 의 permissions 도 막는다. 보내는 것은 늘 사람이 친 명령이다.

## 무엇을 확인하나 (넷이 다 맞아야 보낸다)

    1. 지문이 64자리 전부인가              잘라 쓴 지문은 받지 않는다
    2. 그 요청이 승인 대기(needs_approval)로 끝났나
    3. **지금 파일**의 지문이 사람이 준 값과 같나   승인 뒤에 한 글자라도 바뀌면 다르다
       — 그리고 검수를 통과했을 때 적어 둔 지문(approval.json)과도 같나
    4. 번호표 — 이미 보낸 알림은 다시 보내지 않는다

Claude 를 다시 부르지 않는다. 사람이 본 **그 파일**이 나간다.

## 순서가 설계다

    번호표 받기(claim) → 보내는 중 기록(begin_send) → 보내기(sink) → 다 보냈다 기록(complete)

번호표를 받으면 곧바로 requests/<id>/delivery.json 에 번호표 확인 값(토큰)을 적는다.
보내다 죽은 뒤 **같은 승인을 다시 주면** 이 토큰으로 이어받는다 — 그때 기록을 새로 읽어서

    DONE      이미 보냈다        건너뛴다
    SENDING   보내는 중이었다     **보내지 않는다.** 사람이 대사해야 한다(claims_cli.py)
    CLAIMED   아직 안 보냈다      보낸다

> **되돌릴 수 없는 일을 하는 스크립트는 두 번 불려도 한 번처럼 되도록 스스로 책임진다.**
> 손에 든 목록을 믿지 않고 매번 번호표 기록에 다시 묻는다.

**이 프로젝트는 at-most-once(많아야 한 번)를 고른다. 애매하면 보내지 않는다.**

끝날 때 신호
  0 sent(보냈다) · duplicate(이미 보냈다 — 정상) · 3 needs_human(지문이 다르거나 보냈는지 모르는 것이 있다)
  64 요청·지문 모양이 틀렸다
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / ".claude" / "hooks"))
from _state import event, load, rdir, save, utf8  # noqa: E402
from collect import alert_key  # noqa: E402
from idempotency import Claim, IdempotencyStore, Phase, claims_root  # noqa: E402
from request_approval import artifact_sha  # noqa: E402
from sink import Sink  # noqa: E402

NEEDS_HUMAN = 3


def _condition_of(key: str) -> str:
    """`alert:<symbol>:<condition_id>:<date>` 에서 조건 id."""
    parts = key.split(":")
    return parts[2] if len(parts) > 2 else ""


def check_approval(rid: str, given: str) -> tuple[int | None, Path | None]:
    """승인이 사람이 본 그 파일에 묶였나. 통과면 (None, 문구 파일)."""
    if len(given) != 64 or any(c not in "0123456789abcdef" for c in given):
        print("승인에는 자르지 않은 SHA-256 64자리가 필요하다")
        return 64, None
    st = load(rid)
    if not st:
        print(f"그런 요청이 없다: {rid}")
        return 64, None
    if st.get("terminal") != "needs_approval":
        print(f"승인 대기로 끝난 요청만 승인할 수 있다: {rid} ({st.get('terminal') or '진행 중'})")
        return 64, None
    d = rdir(rid)
    path = d / "draft.json"
    ap = d / "approval.json"
    if not path.is_file() or not ap.is_file():
        print(f"승인할 문구가 없다: requests/{rid}/")
        return 64, None
    actual = artifact_sha(path)
    recorded = json.loads(ap.read_text(encoding="utf-8"))["sha256"]
    if actual != given:
        print("⚠️ 승인한 문구가 아니다.")
        print(f"   승인  {given}")
        print(f"   파일  {actual}")
        print("   파일이 바뀌었거나 지문을 잘못 옮겼다. 아무것도 보내지 않는다.")
        return NEEDS_HUMAN, None
    if actual != recorded:
        print("⚠️ 검수를 통과한 뒤 문구 파일이 바뀌었다.")
        print(f"   검수 통과  {recorded}")
        print(f"   지금 파일  {actual}")
        print("   검수를 거치지 않은 문구는 보내지 않는다.")
        return NEEDS_HUMAN, None
    return None, path


def deliver(rid: str, given: str) -> int:
    code, path = check_approval(rid, given)
    if code is not None:
        return code
    d = rdir(rid)
    message = json.loads(path.read_text(encoding="utf-8"))
    hits = json.loads((d / "hits.json").read_text(encoding="utf-8"))
    date = json.loads((d / "quotes.json").read_text(encoding="utf-8"))["observed_date"]

    # 승인은 흐름 밖에서 일어난 결정이다. 누가·무엇을 승인했는지 남긴다.
    who = os.environ.get("ALERT_APPROVER") or os.environ.get("USER") or os.environ.get("USERNAME") or "human"
    event(rid, "approved", sha256=given, by=who)
    print(f"승인 확인됨: {given[:16]}…")
    print(f"requests/{rid}/draft.json 를 그대로 보낸다. 모델을 다시 부르지 않는다.")

    # ── 번호표 받기 ─────────────────────────────────────────
    record_path = d / "delivery.json"
    saved = json.loads(record_path.read_text(encoding="utf-8")) if record_path.is_file() else {}
    tokens: dict[str, str] = dict(saved.get("tokens") or {})
    store = IdempotencyStore(claims_root(ROOT / "claims"))

    mine, done, held, ambiguous_other, corrupt = [], [], [], [], []
    for hit in hits:
        key = alert_key(hit["condition_id"], hit["symbol"], date)
        verdict, record = store.claim(key, by=rid)
        if verdict is Claim.CLAIMED:
            tokens[key] = record.token
            mine.append(key)
        elif verdict is Claim.DONE:
            done.append(key)
        elif verdict is Claim.CORRUPT:
            corrupt.append(key)
        elif record is not None and tokens.get(key) == record.token:
            mine.append(key)                    # 앞서 이 요청이 받아 둔 번호표 — 이어받는다
        elif verdict is Claim.SENDING:
            ambiguous_other.append(key)
        else:
            held.append(key)
    # ⚠️ 받은 번호표를 **보내기 전에** 적는다. 안 적으면 보내다 죽었을 때 이어받을 길이 없다.
    record_path.write_text(json.dumps({"tokens": tokens}, ensure_ascii=False, indent=2), encoding="utf-8")

    lines: list[str] = []
    for key in done:
        lines.append(f"이미 보냈다: {key}")
    if held:
        lines.append(f"다른 요청이 번호표를 받아 두었다(아직 안 보냄): {', '.join(held)}")
    if ambiguous_other:
        lines.append(f"⚠️ 다른 요청이 보내는 중에 죽었던 것: {', '.join(ambiguous_other)}")
    if corrupt:
        lines.append(f"⚠️ 번호표 기록이 깨졌다: {', '.join(corrupt)}")
    if held or ambiguous_other or corrupt:
        # 보냈는지 모르는 것·남이 잡은 것이 있다. **아무것도 보내지 않는다.**
        lines.append("이번에는 아무것도 보내지 않는다. 대사: uv run python claims_cli.py list")
        return _end(rid, "needs_human", NEEDS_HUMAN, lines, tokens, [])

    # ── 보내기 ───────────────────────────────────────────
    sink = Sink(ROOT / "outbox" / "sent.jsonl")
    by_condition = {line["condition_id"]: line for line in message.get("lines", [])}
    sent, ambiguous = [], []
    for key in mine:
        record = store.read(key)                # 손에 든 목록을 믿지 않는다. 매번 다시 묻는다
        if record is None:
            lines.append(f"번호표 기록이 사라졌다: {key}")
            return _end(rid, "needs_human", NEEDS_HUMAN, lines, tokens, sent)
        if record.phase == Phase.DONE.value:
            lines.append(f"이미 보냈다: {key} → {record.effect_ref}")
            continue
        if record.phase == Phase.SENDING.value:
            ambiguous.append(key)
            lines.append(f"⚠️ 보내는 중에 죽었던 것: {key} — 보냈는지 모른다")
            continue
        line = by_condition.get(_condition_of(key))
        if line is None:
            lines.append(f"번호표를 받은 조건의 문장이 없다: {_condition_of(key)}")
            return _end(rid, "needs_human", NEEDS_HUMAN, lines, tokens, sent)
        store.begin_send(key, tokens[key])      # 보내기 직전에 "보내는 중" 을 적는다
        effect = sink.send(f"{message['headline']}\n- {line['text']}", dedupe_key=key)
        store.complete(key, tokens[key], effect)
        sent.append({"key": key, "effect_ref": effect})
        lines.append(f"보냈다: {key} → {effect}")

    if ambiguous:
        lines.append("실제 발송 기록과 대사(reconcile)해야 한다. 자동으로 다시 보내지 않는다 — uv run python claims_cli.py list")
        return _end(rid, "needs_human", NEEDS_HUMAN, lines, tokens, sent)
    if not sent:
        return _end(rid, "duplicate", 0, lines + ["이미 보낸 알림이다 — 두 번 보내지 않는다"], tokens, sent)
    return _end(rid, "sent", 0, lines + [f"알림 {len(sent)}건을 보냈다 (outbox/sent.jsonl)"], tokens, sent)


def _end(rid: str, outcome: str, code: int, lines: list[str], tokens: dict, sent: list) -> int:
    d = rdir(rid)
    (d / "delivery.json").write_text(json.dumps(
        {"tokens": tokens, "outcome": outcome, "sent": sent}, ensure_ascii=False, indent=2), encoding="utf-8")
    st = load(rid)
    st["delivered"] = outcome
    st["history"].append({"step": "deliver", "signal": code, "note": f"{outcome}: {lines[-1]}"})
    save(rid, st)
    event(rid, "delivered", outcome=outcome, signal=code, sent=len(sent))
    for line in lines:
        print(f"  {line}")
    print(f"deliver → {outcome}")
    return code


def main() -> int:
    utf8()
    if len(sys.argv) != 3:
        print("쓰는 법: scripts/approve.sh <요청 id> <지문 64자리>")
        return 64
    return deliver(sys.argv[1], sys.argv[2].strip().lower())


if __name__ == "__main__":
    sys.exit(main())
