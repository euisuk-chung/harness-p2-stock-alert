"""승인 요청 — 검수를 통과한 알림 문구의 지문을 적고, 승인하는 법을 알린다. 모델을 부르지 않는다. **보내지 않는다.**

    uv run python scripts/request_approval.py <요청 id>

늘 3(사람 차례)으로 끝난다 — 보내는 것은 사람이 `scripts/approve.sh <요청 id> <지문>` 을 칠 때 일어난다.
문구 파일이 없거나 읽을 수 없으면 64.

## 왜 지문(SHA-256 64자리)인가

이 과정의 알림 장치를 처음 만들었을 때 승인은 예/아니오 한 칸이었고, 그러자 사람이 본 문구와
나간 문구가 달라질 수 있었다. 지금은 승인이 **사람이 읽은 파일 바이트 전체의 지문**이다.
파일이 한 글자라도 바뀌면 지문이 달라지고, 전달(scripts/deliver.py)이 지금 파일에서 지문을 다시 계산해
맞춰 보므로 승인이 통과하지 않는다. 자르지 않는다 — 짧을수록 다른 문구와 겹칠 여지가 생긴다.

## 이 단계가 사람에게 넘기는 까닭

검수 C4 는 뻔한 전망 낱말만 잡고 뜻은 못 본다("흐름이 나쁘지 않습니다" 는 통과한다).
모델이 쓴 자유 문장이 되돌릴 수 없는 곳으로 나가니, 뜻은 사람이 본다.

만드는 것: requests/<id>/approval.json (문구 파일 · 지문 · 문구 요약)
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WAIT = 3


def artifact_sha(path: Path) -> str:
    """사람이 확인한 파일 바이트 전체의 SHA-256."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass
    rid = sys.argv[1]
    d = ROOT / "requests" / rid
    path = d / "draft.json"
    try:
        message = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        print(f"approve: 문구를 읽을 수 없다 ({type(e).__name__})")
        return 64
    sha = artifact_sha(path)
    rel = path.relative_to(ROOT).as_posix()
    (d / "approval.json").write_text(json.dumps(
        {"request_id": rid, "file": rel, "sha256": sha,
         "headline": message.get("headline", ""),
         "lines": [l.get("text", "") for l in message.get("lines", [])]},
        ensure_ascii=False, indent=2), encoding="utf-8")
    print("approve: 모델이 쓴 문장이다. 승인 없이 보내지 않는다.")
    print(f"  {message.get('headline', '')}")
    for line in message.get("lines", []):
        print(f"  - {line.get('text', '')}")
    print("  C4 는 뻔한 전망 낱말만 잡는다 — 뜻은 사람이 본다.")
    print(f"approve: 승인하려면 scripts/approve.sh {rid} {sha}")
    return WAIT


if __name__ == "__main__":
    sys.exit(main())
