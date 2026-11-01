"""차단 장치와 스크립트가 같이 쓰는 도우미 — 요청 폴더·상태 파일·기록·흐름도.

요청 폴더 requests/<요청 id>/ 에 셋이 쌓인다.
  state.json    기계가 읽는 정본(단계별 신호·작성 횟수·끝 상태). 차단 장치와 스크립트만 쓴다
  events.jsonl  한 줄씩 덧붙이는 기록(시작 계기·도구 호출·막힘·신호)
  STATUS.md     사람이 읽는 진행표. state.json 에서 만든다 — 손으로 고치지 않는다

지금 어느 요청을 처리하는 중인지는 환경 변수 REQUEST_ID(scripts/request.sh 가 넘긴다),
없으면 requests/.current(요청을 연 open_request.py 가 적는다)로 안다.

시계·난수·네트워크를 쓰지 않는다. 같은 파일에는 늘 같은 답을 낸다.
(events.jsonl 의 순번은 줄 수로 센다 — 시각을 적지 않는다.)
"""

from __future__ import annotations

import fnmatch
import hashlib
import json
import os
import shlex
import subprocess
import sys
from pathlib import Path

ROOT = Path(os.environ.get("CLAUDE_PROJECT_DIR") or Path(__file__).resolve().parents[2])
REQ = ROOT / "requests"


def utf8() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass


def load_yaml(name: str) -> dict:
    import yaml
    return yaml.safe_load((ROOT / name).read_text(encoding="utf-8"))


def current_id() -> str | None:
    rid = os.environ.get("REQUEST_ID")
    if rid:
        return rid
    cur = REQ / ".current"
    if cur.is_file():
        return cur.read_text(encoding="utf-8").strip() or None
    return None


def rdir(rid: str) -> Path:
    return REQ / rid


def load(rid: str) -> dict:
    p = rdir(rid) / "state.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.is_file() else {}


def save(rid: str, st: dict) -> None:
    d = rdir(rid)
    tmp = d / "state.json.tmp"
    tmp.write_text(json.dumps(st, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, d / "state.json")
    (d / "STATUS.md").write_text(status_md(st), encoding="utf-8")


def event(rid: str, kind: str, **fields) -> None:
    p = rdir(rid) / "events.jsonl"
    n = sum(1 for _ in p.open(encoding="utf-8")) + 1 if p.is_file() else 1
    with p.open("a", encoding="utf-8") as f:
        f.write(json.dumps({"n": n, "event": kind, **fields}, ensure_ascii=False) + "\n")


def sha(path: Path) -> str | None:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:12] if path.is_file() else None


def written(path: Path) -> str | None:
    """초안이 '새로 쓰였나' 를 알아보는 표 — 내용 지문 + 쓴 시각(파일 정보).
    같은 내용을 다시 써도 한 번의 작성으로 센다. 시각은 판정에 쓰지 않고, 같은 쓰기를 두 번 세지 않는 데만 쓴다."""
    return f"{sha(path)}:{path.stat().st_mtime_ns}" if path.is_file() else None


def status_md(st: dict) -> str:
    lines = [f"# 요청 {st.get('id')}", "",
             f"- 시작 계기: {st.get('trigger', '')}",
             f"- 지금 단계: {st.get('at')}",
             f"- 작성 횟수: {st.get('visits', {}).get('draft', 0)}",
             f"- 끝 상태: {st.get('terminal') or '(진행 중)'}" + (f" — {st['reason']}" if st.get("reason") else ""),
             "", "| 단계 | 신호 | 메모 |", "|---|---|---|"]
    for h in st.get("history", []):
        lines.append(f"| {h['step']} | {h.get('signal', '')} | {h.get('note', '')} |")
    return "\n".join(lines) + "\n"


# ── 흐름 진행 ───────────────────────────────────────────────

EXHAUSTED = "'{step}' 를 {n}번 했는데도 안 됐다"


def _run(cmd: str, rid: str) -> tuple[int, str]:
    argv = shlex.split(cmd.format(id=rid))
    if argv[:2] == ["uv", "run"]:           # uv run python … — 차단 장치가 이미 그 가상환경의 파이썬으로 돈다
        argv = argv[2:]
    if argv and argv[0] in ("python", "python3"):
        argv[0] = sys.executable
    try:
        p = subprocess.run(argv, cwd=str(ROOT), capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=120)
    except (OSError, subprocess.TimeoutExpired) as e:
        return 64, str(e)
    return p.returncode, (p.stdout + p.stderr).strip()


def advance(rid: str, source: str = "stop") -> dict:
    """스크립트 단계는 직접 실행하며 흐름도를 따라간다. 모델이 할 차례(drafter)나 끝에서 멈춘다.

    source — 누가 불렀나. 작성을 맡긴 뒤 새 초안 없이 다시 불렸을 때 다르게 한다.
      open    요청을 여는 스크립트·명령 — 맡긴 작성을 그대로 다시 알린다(횟수를 올리지 않는다)
      post    초안이 쓰인 직후(PostToolUse) — 검수까지만 하고, 다음 작성은 맡기지 않는다(셈을 올리지 않는다)
      stop    Claude 가 멈추려 함(Stop) — 작성 없이 멈추려 한 것을 세고, 세 번이면 failed
      finish  claude -p 가 끝난 뒤 스크립트 — 작성이 남아 있으면 failed

    돌려주는 것: {"need": "draft"|None, "message": 사람·Claude 에게 보일 글, "terminal": …}
    같은 상태에서 다시 불러도 같은 결과다(같은 초안은 한 번만 검수한다).
    """
    g = load_yaml("graph.yaml")
    st = load(rid)
    if st.get("terminal"):
        return {"need": None, "terminal": st["terminal"], "message": f"끝: {st['terminal']}"}
    steps, term = g["steps"], set(g["terminal"])
    msgs: list[str] = []
    while True:
        at = st["at"]
        if at in term:
            st["terminal"] = at
            st.setdefault("reason", "")
            event(rid, "finished", terminal=at, reason=st["reason"])
            save(rid, st)
            return {"need": None, "terminal": at, "message": "\n".join(msgs + [f"끝: {at}"])}
        cap_calls = load_yaml("policies.yaml").get("max_tool_calls", 20)
        if st.get("tool_calls", 0) > cap_calls:
            st["at"] = "failed"; st["reason"] = f"도구 호출 상한 {cap_calls}회에 닿았다"
            continue
        if len(st["history"]) >= g.get("max_steps", 12):
            st["at"] = "failed"; st["reason"] = f"단계 상한 {g.get('max_steps', 12)}"
            continue
        spec = steps[at]
        if spec["by"] == "script":
            code, out = _run(spec["run"], rid)
            rejects = [l for l in out.splitlines() if l.startswith("review:")]
            note = rejects[0] if rejects else (out.splitlines()[-1] if out else "")
            st["history"].append({"step": at, "signal": code, "note": note})
            event(rid, "step", step=at, signal=code, note=note)
            msgs.append(f"{at}: 신호 {code}" + (f" — {note}" if note else ""))
            if at == "review" and code != 0:
                # 거부된 초안을 남긴다 — 다음 검수가 "되돌아온 초안이 할 일을 빼서 통과하려 했나" 를 본다
                d = rdir(rid)
                if (d / "draft.json").is_file():
                    (d / "draft.rejected.json").write_bytes((d / "draft.json").read_bytes())
                st["last_reject"] = "\n".join(l for l in out.splitlines() if l.startswith("review:"))
            nxt = spec["next"].get(code)
            if nxt is None:
                st["at"] = "failed"; st["reason"] = f"'{at}' 가 흐름도에 없는 신호 {code} 로 끝났다"
            else:
                st["at"] = nxt
            save(rid, st)
            continue
        # drafter — 새 초안이 쓰였으면 작성 끝으로 보고 다음으로
        cur = written(rdir(rid) / "draft.json")
        if cur and cur != st.get("draft_seen") and st.get("pending") == at:
            st["draft_seen"] = cur
            cur = cur.split(":")[0]
            st["pending"] = None
            st["history"].append({"step": at, "signal": 0, "note": f"draft.json {cur}"})
            event(rid, "step", step=at, signal=0, note=f"draft.json {cur}")
            st["at"] = spec["next"][0]
            save(rid, st)
            continue
        cap = spec.get("max_visits", 3)
        n = st["visits"].get(at, 0)
        if st.get("pending") == at:  # 이미 맡겼는데 새 초안이 없다
            if source == "finish":
                st["at"] = "failed"; st["reason"] = "Claude 가 작성을 끝내지 않고 멈췄다"
                continue
            if source == "stop":
                st["stops_without_draft"] = st.get("stops_without_draft", 0) + 1
                if st["stops_without_draft"] >= 3:
                    st["at"] = "failed"; st["reason"] = "작성 없이 세 번 멈추려 했다"
                    continue
                save(rid, st)
            return {"need": at, "message": "\n".join(msgs + [_ask(rid, spec, n, cap, st, source)])}
        if n >= cap:
            st["at"] = spec.get("when_exhausted", "needs_human")
            st["reason"] = EXHAUSTED.format(step=at, n=n)
            continue
        if source == "finish":
            st["at"] = "failed"; st["reason"] = "Claude 가 다시 작성하지 않고 끝났다"
            continue
        if source == "post":  # 작성을 맡기는 것은 멈추려 할 때(Stop)만 — 여기서는 셈을 올리지 않는다
            save(rid, st)
            return {"need": at, "message": "\n".join(msgs)}
        st["visits"][at] = n + 1
        st["pending"] = at
        event(rid, "need", step=at, visit=n + 1)
        save(rid, st)
        return {"need": at, "message": "\n".join(msgs + [_ask(rid, spec, n + 1, cap, st, source)])}


def _ask(rid: str, spec: dict, n: int, cap: int, st: dict, source: str) -> str:
    """작성을 맡기는 안내. 프로젝트마다 다른 말은 흐름도의 작성 단계 칸(ask · retry_hint)에 둔다
    — 그래서 이 파일은 세 프로젝트에서 같은 코드다."""
    why = st.get("last_reject", "")
    ask = spec.get("ask", "요청 {id} 의 초안을 requests/{id}/draft.json 에 쓰게 하세요.").format(id=rid)
    hint = spec.get("retry_hint", "").format(id=rid)
    head = (f"작성 {n}/{cap}: 하위 에이전트 drafter 를 불러(백그라운드로 띄우지 말고 끝날 때까지 기다려) {ask}")
    if why:
        head += f"\n직전 검수가 거부한 이유(drafter 에게 그대로 넘기세요):\n{why}"
        if hint:
            head += f"\n{hint}"
    return head


def hook_input() -> dict:
    try:
        return json.load(sys.stdin)
    except (ValueError, OSError):
        return {}


def matches_any(path: str, globs: list[str]) -> bool:
    rel = os.path.relpath(path, ROOT) if os.path.isabs(path) else path
    rel = rel.replace(os.sep, "/")
    return any(fnmatch.fnmatch(rel, g) for g in globs)
