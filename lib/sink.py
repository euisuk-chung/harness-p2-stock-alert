"""알림이 나가는 곳. **여기를 지나면 되돌릴 수 없다.**

> ⚠️ 이건 **멱등하지 않은 시험용 sink** 다. 중복을 *막는* 것이 아니라
> **중복을 눈에 보이게** 하려고 만들었다.

실습에서는 파일에 한 줄씩 붙인다. 실무라면 Slack·메일·SMS 다.
**바뀌는 건 이 파일 하나**이고, 번호표(`idempotency.py`) 설계는 그대로다.

## 왜 파일에 붙이나

**셀 수 있어야** 하기 때문이다. *"두 번 돌려도 한 번만 나갔다"* 를 말로 하지 않고
줄 수로 보인다. 실습의 확인 방법이 곧 검사다.

## ⚠️ 진짜 알림은 취소가 없다

파일은 지우면 되지만 메일은 못 지운다. 그래서 전달(`scripts/deliver.py`)은
번호표를 먼저 받고(claim) 그다음에 보낸다. 보내기 **전에** 찍는다.

> `idempotency.py` 머리말의 두 crash 구간을 본다.

**이 파일을 부르는 것은 `scripts/deliver.py` 하나뿐이다.** Claude 에게는 이걸 부를 길이 없다
(policies.yaml · .claude/settings.json).
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path


class Sink:
    """알림을 내보내는 곳."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def send(self, message: str, *, dedupe_key: str) -> str:
        """내보낸다. 돌아오는 것은 **효과의 이름**이다.

        실제 API 라면 상대가 준 메시지 id 다. 그걸 기록해 두어야 나중에 대사할 수 있다.

        ⚠️ **이 sink 는 `dedupe_key` 를 기록만 한다.** 같은 key 로 두 번 부르면
        **두 줄이 된다.** 막는 것은 `idempotency.py` 이지 여기가 아니다.

        > 상대가 **멱등키를 받는다면** 여기서 `dedupe_key` 를 함께 넘긴다.
        > 그러면 우리가 두 번 불러도 상대가 한 번만 처리한다 — **그게 가장 강한 해법이다.**
        > 그런 sink 를 흉내 내지 않았다. 흉내 내면 우리 쪽 장치가 맞는지 확인할 수 없다.
        """
        effect_ref = uuid.uuid4().hex[:12]
        record = {
            "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "effect_ref": effect_ref,
            "dedupe_key": dedupe_key,
            "message": message,
        }
        with self.path.open("a", encoding="utf-8") as fp:
            fp.write(json.dumps(record, ensure_ascii=False) + "\n")
        # 교육용: ALERT_CRASH_AFTER_SEND=1 이면 **첫 줄을 쓴 직후** 죽는다 (파일당 한 번).
        # "발송 뒤 · complete 전" 구간 — 효과는 나갔는데 기록이 안 된 상태를 만든다.
        if os.environ.get("ALERT_CRASH_AFTER_SEND") and self.count() == 1:
            raise RuntimeError(
                f"교육용 crash: {effect_ref} 를 보낸 직후, 기록 전에 죽었다 (ALERT_CRASH_AFTER_SEND).\n"
                f"  claims 에는 SENDING 이 남는다. 같은 승인을 다시 주면 이어받고, claims_cli.py list 로 대사한다."
            )
        return effect_ref

    # ---------- 세기 ----------

    def all(self) -> list[dict]:
        if not self.path.exists():
            return []
        return [json.loads(line) for line in
                self.path.read_text(encoding="utf-8").splitlines() if line.strip()]

    def count(self, dedupe_key: str | None = None) -> int:
        """몇 번 나갔나. **이게 실습의 확인 방법이다.**"""
        sent = self.all()
        if dedupe_key is None:
            return len(sent)
        return sum(1 for s in sent if s.get("dedupe_key") == dedupe_key)
