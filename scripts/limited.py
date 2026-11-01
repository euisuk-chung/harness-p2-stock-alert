"""명령 하나를 시간 상한을 걸어 실행한다 — request.sh 가 claude 를 부를 때 쓴다.

    uv run python scripts/limited.py 600 claude -p "/handle-request 2026-W36" …

운영체제마다 다른 timeout 명령에 기대지 않으려고 파이썬으로 건다. 표준 입출력과 환경 변수
(REQUEST_ID 같은)는 그대로 넘긴다. 상한을 넘기면 멈추고 124 로 끝난다(timeout 명령과 같은 숫자).
그 밖에는 부른 명령의 신호를 그대로 돌려준다.
"""

from __future__ import annotations

import subprocess
import sys


def main() -> int:
    if len(sys.argv) < 3:
        print("쓰는 법: uv run python scripts/limited.py <초> <명령> [인자 …]", file=sys.stderr)
        return 64
    seconds = float(sys.argv[1])
    try:
        return subprocess.run(sys.argv[2:], timeout=seconds).returncode
    except subprocess.TimeoutExpired:
        print(f"limited: {sys.argv[1]}초 상한에 닿아 멈췄다", file=sys.stderr)
        return 124
    except FileNotFoundError:
        print(f"limited: 명령을 찾을 수 없다: {sys.argv[2]}", file=sys.stderr)
        return 127


if __name__ == "__main__":
    sys.exit(main())
