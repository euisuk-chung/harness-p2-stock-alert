"""대사 도구가 **규칙 안에서** 움직이는가.

    uv run python tests/test_claims_cli.py

`claims_cli.py` 는 사람이 `claims/` 를 손으로 지우는 대신 쓰는 도구다.
그러니 도구 자신이 규칙을 어기면 안 된다 — DONE 은 못 풀고, SENDING 은 확인 없이 못 푼다.
실제 저장소를 건드리지 않으려고 `ALERT_CLAIMS_DIR` 로 임시 저장소를 준다.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "lib"))

from _utf8 import force_utf8                          # noqa: E402
from idempotency import IdempotencyStore, Phase       # noqa: E402

failures: list[str] = []
KEY = "alert:AAPL:above-200:2026-08-31"


def ok(label: str, cond: bool) -> None:
    if not cond:
        failures.append(label)
    print(f"   {'OK  ' if cond else 'FAIL'} {label}")


def cli(*args: str, env: dict) -> tuple[int, str]:
    proc = subprocess.run([sys.executable, str(ROOT / "claims_cli.py"), *args],
                          cwd=str(ROOT), text=True, capture_output=True,
                          encoding="utf-8", errors="replace", env={**os.environ, **env, "PYTHONUTF8": "1"})
    return proc.returncode, proc.stdout + proc.stderr


def main() -> int:
    force_utf8()
    with tempfile.TemporaryDirectory() as tmp:
        claims = Path(tmp) / "claims"
        env = {"ALERT_CLAIMS_DIR": str(claims)}
        store = IdempotencyStore(claims)

        print("1. 비어 있을 때")
        code, out = cli("list", env=env)
        ok("걸린 것이 없으면 0", code == 0 and "걸려 있는 것이 없다" in out)

        print("\n2. CLAIMED — 보내기 전에 죽은 것")
        _, rec = store.claim(KEY, by="run-1")
        code, out = cli("list", env=env)
        ok("list 에 claimed 로 보인다", code == 0 and "claimed" in out and KEY in out)
        code, out = cli("release-unsent", KEY, env=env)
        ok("**확인 없이 풀린다** (아직 안 보냈으니)", code == 0 and store.read(KEY) is None)
        ok("대사 기록이 남는다", (claims / "reconcile.log.jsonl").is_file())

        print("\n3. SENDING — 보내는 중에 죽은 것")
        _, rec = store.claim(KEY, by="run-2")
        store.begin_send(KEY, rec.token)
        code, out = cli("release-unsent", KEY, env=env)
        ok("**--confirmed 없이는 못 푼다** (나갔는지 모른다)", code == 1 and store.read(KEY).phase == Phase.SENDING.value)
        code, out = cli("release-unsent", KEY, "--confirmed", env=env)
        ok("외부 기록으로 확인했다면 푼다", code == 0 and store.read(KEY) is None)

        print("\n4. mark-sent — 실제로 나갔더라")
        _, rec = store.claim(KEY, by="run-3")
        store.begin_send(KEY, rec.token)
        code, out = cli("mark-sent", KEY, "eff-xyz", env=env)
        done = store.read(KEY)
        ok("DONE 으로 바뀌고 effect_ref 가 남는다", code == 0 and done.phase == Phase.DONE.value and done.effect_ref == "eff-xyz")
        code, out = cli("release-unsent", KEY, "--confirmed", env=env)
        ok("**DONE 은 --confirmed 로도 못 푼다** (풀면 또 나간다)", code == 1 and store.read(KEY).phase == Phase.DONE.value)
        code, out = cli("mark-sent", KEY, "eff-2", env=env)
        ok("DONE 을 다시 mark-sent 해도 바뀌지 않는다", code == 0 and store.read(KEY).effect_ref == "eff-xyz")

        print("\n5. 없는 key")
        code, out = cli("mark-sent", "alert:NOPE:x:y", "e", env=env)
        ok("없는 key 는 1", code == 1)

        log = [json.loads(l) for l in (claims / "reconcile.log.jsonl").read_text(encoding="utf-8").splitlines()]
        ok("모든 결정이 기록됐다 (release ×2 · mark-sent ×1)",
           [e["action"] for e in log] == ["release-unsent", "release-unsent", "mark-sent"])

    print()
    if failures:
        print(f"실패 {len(failures)}건")
        for line in failures:
            print(f"   {line}")
        return 1
    print("대사 도구가 규칙 안에서 움직인다 — **DONE 은 못 풀고, SENDING 은 확인이 있어야 푼다.**")
    return 0


if __name__ == "__main__":
    sys.exit(main())
