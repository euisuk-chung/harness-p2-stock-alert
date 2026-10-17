"""Windows 콘솔 기본 인코딩(CP949)에서 한글·기호 출력이 깨지는 것을 막는다.

`PYTHONIOENCODING=utf-8` 을 매번 붙이게 하면 학생이 첫 실행에서 막힌다.
스크립트가 스스로 고정한다.
"""

import sys


def force_utf8() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="replace")
