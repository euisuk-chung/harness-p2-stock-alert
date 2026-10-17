"""명령·하위 에이전트 파일이 계약을 지키나 — 모델 없이 파일만 본다.

    uv run python scripts/lint_command.py                  /handle-request 명령과 drafter

보는 것(칸이 있는지만이 아니라 칸의 **값**)
  명령   .claude/commands/<이름>.md 가 있고, allowed-tools 에 Write·Edit 이 없고(본 세션은 쓰지 않는다),
         요청을 여는 줄(scripts/open_request.py)이 있고, drafter 에게 맡긴다고 적었나
  작성   .claude/agents/drafter.md 의 머리에 name: drafter, tools 가 비어 있지 않고 Bash 가 없고, model 이 있나
         (tools 를 비우면 본 세션의 도구를 전부 물려받는다)
  규칙   .claude/skills/drafter-rules/SKILL.md 가 있고, drafter 가 skills: 로 미리 싣고, / 메뉴에서 숨겼고
         (user-invocable: false), 미리 실을 수 있고(disable-model-invocation 없음), 규칙이 두 파일에 겹치지 않나
         거부 뒤에는 다시 쓴다고 적었나("못 고치면 그대로 둔다" 로만 끝나면 작성 담당이 다시 저장하지 않는다)
전부 맞으면 0, 하나라도 다르면 1.

보장하지 않는 것: 지시문이 잘 쓰였다는 것. 그것은 실제 실행과 검수 C1~C4 가 본다.
칸이 있는지만 보는 검사는 빈 내용으로도 통과해서, 여기서는 값까지 본다.
"""

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def front(path: Path) -> tuple[dict, str]:
    text = path.read_text(encoding="utf-8")
    m = re.match(r"^---\n(.*?)\n---\n(.*)$", text, re.S)
    if not m:
        return {}, text
    meta: dict[str, str] = {}
    key = None
    for line in m.group(1).splitlines():
        item = line.strip()
        if item.startswith("- ") and key and line[:1].isspace():   # skills:\n  - drafter-rules 같은 목록
            meta[key] = ", ".join(x for x in (meta[key], item[2:].strip()) if x)
            continue
        if ":" in line:
            k, v = line.split(":", 1)
            key = k.strip()
            meta[key] = v.strip()
    return meta, m.group(2)


def main() -> int:
    name = sys.argv[1] if len(sys.argv) > 1 else "handle-request"
    fails = []

    def ok(cond: bool, what: str) -> None:
        print(f"{'OK  ' if cond else 'FAIL'} {what}")
        if not cond:
            fails.append(what)

    cmd = ROOT / ".claude" / "commands" / f"{name}.md"
    ok(cmd.is_file(), f"명령 파일이 있다: .claude/commands/{name}.md")
    if cmd.is_file():
        meta, body = front(cmd)
        tools = meta.get("allowed-tools", "")
        ok(bool(tools), "명령의 allowed-tools 가 적혀 있다")
        ok(not re.search(r"\b(Write|Edit|MultiEdit)\b", tools), "명령은 파일을 쓰지 않는다(allowed-tools 에 Write·Edit 없음)")
        ok("scripts/open_request.py" in body, "명령이 요청을 여는 줄로 시작한다(scripts/open_request.py)")
        ok("drafter" in body, "명령이 작성을 drafter 에게 맡긴다")
    ag = ROOT / ".claude" / "agents" / "drafter.md"
    ok(ag.is_file(), "작성 담당 파일이 있다: .claude/agents/drafter.md")
    if ag.is_file():
        meta, body = front(ag)
        tools = [t.strip() for t in meta.get("tools", "").split(",") if t.strip()]
        ok(meta.get("name") == "drafter", "name: drafter")
        ok(bool(tools), "tools 가 비어 있지 않다(비우면 도구를 전부 물려받는다)")
        ok("Bash" not in tools, "drafter 에게 Bash 가 없다")
        ok({"Read", "Write"} <= set(tools), "drafter 가 읽고 쓸 수 있다(Read · Write)")
        ok(bool(meta.get("model")), "model 이 적혀 있다")
        ok("draft.json" in body and "condition_id" in body and "value" in body,
           "형식(draft.json · condition_id · value)을 지시한다")
    # 늘 지킬 규칙은 스킬로 떼어 작성 담당이 시작할 때 미리 싣는다(7차시). 두 파일에 같은 규칙이 겹치면
    # 한쪽만 고쳐 어긋나기 쉽다. "사람만 부르기"(disable-model-invocation)를 켜면 미리 실리지 않는다(공식 문서).
    sk = ROOT / ".claude" / "skills" / "drafter-rules" / "SKILL.md"
    if ag.is_file():
        skills = [t.strip() for t in front(ag)[0].get("skills", "").split(",") if t.strip()]
        ok("drafter-rules" in skills, "drafter 가 늘 지킬 규칙(스킬 drafter-rules)을 미리 싣는다(skills:)")
    ok(sk.is_file(), "규칙 스킬 파일이 있다: .claude/skills/drafter-rules/SKILL.md")
    if sk.is_file():
        smeta, sbody = front(sk)
        ok(smeta.get("name") == "drafter-rules", "스킬 name: drafter-rules")
        ok(smeta.get("user-invocable") == "false", "스킬은 / 메뉴에 없다(user-invocable: false)")
        ok(smeta.get("disable-model-invocation", "") != "true",
           "스킬을 미리 실을 수 있다(disable-model-invocation 을 켜지 않았다)")
        if ag.is_file():
            def plain(t: str) -> str:
                return re.sub(r"[*`\s]", "", re.sub(r"^\s*(?:[-*]|\d+\.)\s*", "", t))
            agent_text = plain(front(ag)[1])
            twice = [ln.strip() for ln in sbody.splitlines() if len(plain(ln)) >= 12 and plain(ln) in agent_text]
            ok(not twice, "규칙이 작성 담당 파일에 겹쳐 적히지 않았다" + (f" — {twice[0][:40]}" if twice else ""))
        # 거부 뒤에는 초안을 다시 쓴다. "못 고치면 그대로 둔다" 로만 끝나면 작성 담당이 다시 저장하지 않아
        # 장치가 작성 횟수를 세지 못하고, 요청이 사람에게 넘어가지 못한 채 failed 로 끝난다(실제 실행에서 나왔다).
        # 뜻이 아니라 낱말을 본다 — 다르게 적어 FAIL 이면 "다시 쓴다" 를 넣게 하면 된다.
        rewrite = re.compile(r"다시\s*(?:쓰|씁|써|저장|작성)|재작성|덮어\s*쓰")
        keep = re.compile(r"그대로\s*(?:두|둡|둔|둬)")
        texts = [sbody] + ([front(ag)[1]] if ag.is_file() else [])
        # 줄(규칙 한 항목) 단위로 본다 — 같은 줄에 "다시 쓴다" 가 있으면 "그대로 둔다" 는 고칠 수 없는 곳의 이야기다.
        # 문장 단위로 가르면 굵은 표시(**)가 마침표 뒤에 붙었는지에 따라 결과가 뒤집혀서 줄로 본다
        items = [l.strip() for t in texts for l in t.splitlines() if l.strip()]
        stuck = [l for l in items if keep.search(l) and not rewrite.search(l)]
        # FAIL 줄에는 "그대로 둔다" 가 든 문장부터 찍는다(줄 머리부터 찍으면 걸린 곳이 잘려 안 보인다)
        snip = lambda l: l[l.rfind(". ", 0, keep.search(l).start()) + 2 if ". " in l[:keep.search(l).start()] else 0:][:50]
        said = "거부" not in sbody or bool(rewrite.search(sbody))
        ok(said and not stuck, "거부된 뒤에도 초안을 다시 쓴다(못 고친 곳을 그대로 두고 끝내지 않는다)"
           + (f" — …{snip(stuck[0])}" if stuck else "" if said else " — 거부 규칙에 '다시 쓴다' 가 없다"))
    print("\n명령 점검: " + ("통과" if not fails else f"{len(fails)}건 다름"))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
