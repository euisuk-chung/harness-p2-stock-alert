"""C3 — **숫자가 관측값과 같은가.** 이 프로젝트에서 가장 중요한 검사다.

모델이 값을 반올림하거나 지어내면 알림이 거짓이 된다.
*"214.6 돌파"* 가 *"215 돌파"* 로 바뀌면 사실이 아니다.

## 왜 정확히 같아야 하나

블로그의 인용 검사(G3)와 같은 논리다 — **판정을 부분 문자열/정확 비교로 만들면 공짜가 된다.**
"대충 비슷하면 통과" 로 하는 순간 무엇이 통과인지 아무도 모르게 된다.

부동소수 비교라 아주 작은 오차만 허용한다. 반올림은 오차가 아니다.
"""

from __future__ import annotations

from . import GuardResult

TOLERANCE = 1e-9


def check(message: dict, hits: list[dict]) -> GuardResult:
    observed = {h["condition_id"]: h["value"] for h in hits}
    lines = message.get("lines", [])
    if not lines:
        return GuardResult("C3", "value_exact", 0.0, False, ["문장이 없다"])

    wrong = []
    for line in lines:
        cid = line["condition_id"]
        if cid not in observed:
            continue          # C2 가 잡는다
        if abs(float(line["value"]) - float(observed[cid])) > TOLERANCE:
            wrong.append(f"{cid}: 관측 {observed[cid]} 인데 {line['value']} 라고 썼다")

    checked = [l for l in lines if l["condition_id"] in observed]
    score = (len(checked) - len(wrong)) / len(checked) if checked else 0.0
    return GuardResult("C3", "value_exact", score, not wrong, wrong)
