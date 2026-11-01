"""동시 전이 시험용 워커. **별도 모듈이어야 한다.**

Windows 의 `spawn` 방식은 자식 프로세스가 이 파일을 **다시 import** 한다.
그래서 워커 함수가 `__main__` 안에 있으면 안 된다 — 임시 스크립트로는 안 되는 이유다.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

_BARRIER = None


def _init(barrier) -> None:
    global _BARRIER
    _BARRIER = barrier


def try_begin_send(args: tuple[str, str, str]) -> str:
    """다섯이 **정확히 같은 순간에** `begin_send` 를 부른다. 하나만 이겨야 한다."""
    root, key, token = args
    from idempotency import Contended, IdempotencyStore, Stale

    store = IdempotencyStore(Path(root))
    if _BARRIER is not None:
        _BARRIER.wait()          # 여기서 다 같이 출발한다
    try:
        store.begin_send(key, token)
        return "won"
    except Stale:
        return "late"            # 다른 워커가 먼저 옮겼다 — **보내지 않는다**
    except Contended:
        return "contended"       # 잠금을 못 얻었다 — 역시 보내지 않는다
