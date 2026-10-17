"""SessionStart — 처리 중인 요청이 있으면 그 진행표(STATUS.md)를 대화 첫머리에 넣는다.

멈춘 요청을 새 세션이 이어받을 때, 대화 기록이 아니라 파일에서 어디까지 했는지를 읽게 하려는 것이다.
"""

from __future__ import annotations

import sys

from _state import current_id, load, rdir, utf8


def main() -> int:
    utf8()
    rid = current_id()
    if rid and load(rid):
        p = rdir(rid) / "STATUS.md"
        if p.is_file():
            print("처리 중인 요청의 진행표입니다(파일에서 읽음):\n")
            print(p.read_text(encoding="utf-8"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
