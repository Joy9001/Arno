"""Part D: Code Mode integration with FunctionModel (no API keys, no LLM calls).

Questions: (1) ModelRetry from a nested tool, (2) nested ToolReturn metadata in run_code's return and in the
VercelAIAdapter stream, (3) 200-page doc vs max_tool_calls=100, (4) Code-Mode-written script replayed in raw Monty.
"""
import sys as _s, pathlib as _p
_s.path.insert(0, str(_p.Path(__file__).resolve().parent))   # lets `python -I` (isolated mode) still import sibling modules
import asyncio
import json
import sys
import warnings

from pydantic_ai import Agent, ModelRetry
from pydantic_ai.messages import ModelResponse, RetryPromptPart, TextPart, ToolCallPart, ToolReturn, ToolReturnPart
from pydantic_ai.models.function import AgentInfo, DeltaToolCall, FunctionModel
from pydantic_ai.toolsets import FunctionToolset
from pydantic_ai_harness import CodeMode

from common import FIX, PARSERS
from hostfns import Doc, check_statement as host_check

warnings.filterwarnings("ignore")
PRELUDE = (PARSERS / "_prelude.py").read_text(encoding="utf-8")


def make_toolset(doc, mode="plain"):
    """Host functions as PydanticAI tools. mode: plain | retry (doc_words raises ModelRetry on a bad page) | toolreturn."""
    async def doc_info() -> dict:
        """Describe the open document: kind, pages, text-layer flags, sheets."""
        return doc.info()

    async def doc_words(page: int) -> list[dict]:
        """Positioned words of one page: text, x0, x1, top, bottom."""
        if mode == "retry" and not (0 <= page < doc.info().get("pages", 0)):
            raise ModelRetry(f"page {page} is out of range; the document has {doc.info().get('pages', 0)} page(s), numbered from 0")
        return doc.words(page)

    async def doc_words_range(first: int, last: int) -> list[list[dict]]:
        """Words for pages first..last inclusive, one list per page (batch form of doc_words)."""
        return [doc.words(p) for p in range(first, min(last, doc.info().get("pages", 1) - 1) + 1)]

    async def doc_cells(sheet: int | str = 0) -> list[list]:
        """Rows of cells of one sheet/table (strings, numbers, None)."""
        return doc.cells(sheet)

    async def check_statement(rows: list[dict], opening: int, closing: int):
        """Balance check on integer paise. Returns status VERIFIED / DISCREPANCY / FAILED."""
        res = host_check(rows, opening, closing)
        if mode == "toolreturn":
            return ToolReturn(return_value=res, content=f"[check card] {res['status']} for {res['rows']} rows",
                              metadata={"check_id": "chk-001", "sources": ["doc:a1", "statement:2026-01"], "status": res["status"]})
        return res

    return FunctionToolset(tools=[doc_info, doc_words, doc_words_range, doc_cells, check_statement]).with_metadata(code_mode=True)


def scripted_model(codes):
    """A model that emits run_code with each of `codes` in turn (one per model request), then a final text."""
    state = {"i": 0, "seen": []}

    def fn(messages, info: AgentInfo):
        state["seen"].append(messages[-1])
        if state["i"] < len(codes):
            code = codes[state["i"]]
            state["i"] += 1
            return ModelResponse(parts=[ToolCallPart("run_code", {"code": code})])
        return ModelResponse(parts=[TextPart("done")])
    async def sfn(messages, info: AgentInfo):
        state["seen"].append(messages[-1])
        if state["i"] < len(codes):
            code = codes[state["i"]]
            state["i"] += 1
            yield {0: DeltaToolCall(name="run_code", json_args=json.dumps({"code": code}), tool_call_id=f"call_{state['i']}")}
        else:
            yield "done"
    state["fn"] = FunctionModel(fn, stream_function=sfn)
    return state


def build(doc, codes, mode="plain", **cm_kw):
    st = scripted_model(codes)
    agent = Agent(st["fn"], toolsets=[make_toolset(doc, mode)], capabilities=[CodeMode(tools={"code_mode": True}, **cm_kw)])
    return agent, st


def summarize_run(result):
    out = []
    for m in result.all_messages():
        for p in getattr(m, "parts", []):
            if isinstance(p, ToolReturnPart):
                out.append(("ToolReturn", p.tool_name, str(p.content)[:350].replace("\n", " "), sorted((p.metadata or {}).keys())))
            elif isinstance(p, RetryPromptPart):
                out.append(("RetryPrompt", p.tool_name, str(p.content)[:500].replace("\n", " ")))
    return out


A1 = FIX / "a1_sep_cols_jan.pdf"

# ---- script styles
SCRIPT_BASIC = PRELUDE + """
info = await doc_info()
words = await doc_words(page=0)
chk = await check_statement(rows=[{'date': '2026-01-01', 'debit': 0, 'credit': 100, 'balance': 100}], opening=0, closing=100)
{'pages': info['pages'], 'n_words': len(words), 'check': chk['status']}
"""
SCRIPT_UNCAUGHT = "w = await doc_words(page=99)\nlen(w)"
SCRIPT_CAUGHT = """
try:
    w = await doc_words(page=99)
    out = 'no error'
except Exception as e:
    out = [type(e).__name__, str(e)]
out
"""
SCRIPT_TOOLRETURN = """
chk = await check_statement(rows=[{'date': '2026-01-01', 'debit': 0, 'credit': 100, 'balance': 100}], opening=0, closing=100)
chk
"""


def exp_basic_and_retry():
    doc = Doc(A1)
    print("== 1. run_code tool as the model sees it")
    agent, st = build(doc, [SCRIPT_BASIC])
    r = agent.run_sync("parse")
    for s in summarize_run(r):
        print("  ", s)
    # tool description / catalog
    captured = {}

    def peek(messages, info: AgentInfo):
        captured["tools"] = [(t.name, (t.description or "")[:300], list((t.parameters_json_schema or {}).get("properties", {}))) for t in info.function_tools]
        captured["instr"] = (info.instructions or "")[:1500] if hasattr(info, "instructions") else None
        return ModelResponse(parts=[TextPart("x")])
    a2 = Agent(FunctionModel(peek), toolsets=[make_toolset(doc)], capabilities=[CodeMode(tools={"code_mode": True})])
    a2.run_sync("hi")
    print("   tools shown to model:", [(n, props) for n, _, props in captured["tools"]])
    print("   run_code description:", captured["tools"][0][1].replace("\n", " ")[:300] if captured["tools"] else None)
    print("   instructions (catalog):\n" + (captured["instr"] or "")[:1300])

    print("== 2a. ModelRetry from nested tool, script does NOT catch")
    agent, st = build(doc, [SCRIPT_UNCAUGHT, SCRIPT_BASIC], mode="retry")
    r = agent.run_sync("parse")
    for s in summarize_run(r):
        print("  ", s)
    print("== 2b. ModelRetry from nested tool, script catches it")
    agent, st = build(doc, [SCRIPT_CAUGHT], mode="retry")
    r = agent.run_sync("parse")
    for s in summarize_run(r):
        print("  ", s)


def exp_toolreturn():
    doc = Doc(A1)
    print("== 3. nested ToolReturn metadata/content")
    agent, st = build(doc, [SCRIPT_TOOLRETURN], mode="toolreturn")
    r = agent.run_sync("check")
    for m in r.all_messages():
        for p in getattr(m, "parts", []):
            if isinstance(p, ToolReturnPart) and p.tool_name == "run_code":
                print("   run_code content (what the model sees):", str(p.content)[:300])
                md = p.metadata or {}
                print("   run_code metadata keys:", sorted(md))
                for cid, tr in (md.get("tool_returns") or {}).items():
                    print("   nested return", cid, "->", type(tr).__name__, "content:", str(tr.content)[:120], "| metadata:", tr.metadata)
        # user-content parts carrying the nested ToolReturn.content?
        for p in getattr(m, "parts", []):
            if p.__class__.__name__ == "UserPromptPart":
                print("   UserPromptPart content:", str(p.content)[:200])

    print("== 3b. through VercelAIAdapter (streamed events)")
    from pydantic_ai.ui.vercel_ai import VercelAIAdapter
    body = json.dumps({"id": "chat1", "trigger": "submit-message", "messages": [{"id": "m1", "role": "user", "parts": [{"type": "text", "text": "check"}]}]}).encode()

    async def go():
        agent, st = build(doc, [SCRIPT_TOOLRETURN], mode="toolreturn")
        adapter = VercelAIAdapter(agent=agent, run_input=VercelAIAdapter.build_run_input(body))
        events = []
        async for ev in adapter.run_stream():
            events.append(ev)
        return events, adapter
    events, adapter = asyncio.run(go())
    print(f"   {len(events)} events; types:", [type(e).__name__ for e in events])
    for e in events:
        if type(e).__name__ == "ErrorChunk":
            print("   ErrorChunk:", getattr(e, "error_text", e))
    blob = json.dumps([e.model_dump(mode="json", by_alias=True, exclude_none=True) if hasattr(e, "model_dump") else str(e) for e in events], default=str)
    for needle in ("chk-001", "doc:a1", "statement:2026-01", "[check card]", "tool_returns", "check_id"):
        print(f"   needle {needle!r} in stream: {needle in blob}")
    for e in events:
        n = type(e).__name__
        if "ToolOutput" in n or "ToolInput" in n or "Data" in n:
            print("   ", n, json.dumps(e.model_dump(mode="json", by_alias=True, exclude_none=True), default=str)[:400])


async def exp_budget():
    doc = Doc(FIX / "a34_big_200p.pdf")
    script = PRELUDE + (PARSERS / "cm_pdf_sep_cols.py").read_text(encoding="utf-8")
    print("== 4. 182-page doc, one doc_words per page, max_tool_calls default (100)")
    agent, st = build(doc, [script, script], max_tool_calls=100)
    try:
        r = await agent.run("parse")
        for s in summarize_run(r):
            print("  ", s)
    except Exception as e:
        print("   run raised", type(e).__name__, str(e)[:200])
    print("== 4b. same with max_tool_calls=400 and max_suspensions raised")
    agent, st = build(doc, [script], max_tool_calls=400, resource_limits={"max_duration_secs": 60, "max_suspensions": 5000})
    try:
        r = await agent.run("parse")
        for s in summarize_run(r):
            print("  ", s[:3])
        for m in r.all_messages():
            for p in getattr(m, "parts", []):
                if isinstance(p, ToolReturnPart) and p.tool_name == "run_code":
                    md = p.metadata or {}
                    print("   nested calls recorded:", len(md.get("tool_calls", {})), "| content type:", type(p.content).__name__, "| size chars:", len(str(p.content)))
    except Exception as e:
        print("   run raised", type(e).__name__, str(e)[:300])
    print("== 4c. batch tool doc_words_range(first, last): 4 calls of 50 pages, default limits (100 calls / 1000 suspensions)")
    batch = script.replace("    words = await doc_words(page=pg)\n",
                           "    if pg % 50 == 0:\n        chunk = await doc_words_range(first=pg, last=pg + 49)\n    words = chunk[pg % 50]\n")
    agent, st = build(doc, [batch])
    try:
        r = await agent.run("parse")
        for m in r.all_messages():
            for p in getattr(m, "parts", []):
                if isinstance(p, ToolReturnPart) and p.tool_name == "run_code":
                    md = p.metadata or {}
                    print("   nested calls:", len(md.get("tool_calls", {})), "| rows:",
                          sum(len(a["rows"]) for a in p.content["accounts"]) if isinstance(p.content, dict) else str(p.content)[:300])
    except Exception as e:
        print("   run raised", type(e).__name__, str(e)[:300])


def _run_code_text(r):
    return [str(p.content)[:260].replace("\n", " ") for m in r.all_messages() for p in getattr(m, "parts", [])
            if isinstance(p, ToolReturnPart) and p.tool_name == "run_code"]


async def exp_susp():
    doc = Doc(A1)
    print("== 4d. suspensions consumed per nested tool call in Code Mode (default max_suspensions=1000)")
    for n in (100, 200, 300, 400, 500, 900):
        code = f"n = 0\nfor i in range({n}):\n    x = await doc_info()\n    n += 1\nn"
        agent, st = build(doc, [code], max_tool_calls=5000)
        r = await agent.run("x")
        msg = _run_code_text(r)
        print(f"   {n} sequential calls ->", msg[0] if msg else None)
    print("== 4e. asyncio.gather of 150 calls")
    code = "import asyncio\nr = await asyncio.gather(*[doc_info() for _ in range(150)])\nlen(r)"
    agent, st = build(doc, [code], max_tool_calls=5000)
    r = await agent.run("x")
    print("   ->", _run_code_text(r))



if __name__ == "__main__":
    which = sys.argv[1:] or ["basic", "toolreturn", "budget"]
    if "basic" in which: exp_basic_and_retry()
    if "toolreturn" in which: exp_toolreturn()
    if "budget" in which: asyncio.run(exp_budget())
    if "susp" in which: asyncio.run(exp_susp())
