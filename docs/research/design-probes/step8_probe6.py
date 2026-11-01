"""Step 8 probe 6: the failure save with the full config, a guard for a small backup, cut-off cards, and a pool across a DB restart."""
import asyncio
import os
import subprocess
import time
import uuid
import warnings
from pathlib import Path

import pydantic_ai_harness as h
import pydantic_ai_harness.compaction as c
from psycopg_pool import AsyncConnectionPool
from pydantic_ai import Agent, RunContext, capture_run_messages
from pydantic_ai.capabilities import AbstractCapability, ModelFallback
from pydantic_ai.exceptions import ModelAPIError
from pydantic_ai.messages import ModelMessagesTypeAdapter, ModelRequest, ModelResponse, TextPart, ToolReturnPart
from pydantic_ai.models.function import DeltaToolCall, FunctionModel
from pydantic_ai.profiles import ModelProfile
from pydantic_ai.ui.vercel_ai import VercelAIAdapter
from pydantic_ai.ui.vercel_ai.request_types import SubmitMessage, TextUIPart, UIMessage
from pydantic_ai.ui.vercel_ai.response_types import DataChunk
from pydantic_ai_harness.guardrails.detectors import personal_data, secret_data

warnings.filterwarnings("ignore")
seen: list = []


def stored(messages):
    return ModelMessagesTypeAdapter.validate_json(ModelMessagesTypeAdapter.dump_json(messages))


def has_return(messages):
    tail = messages[-1]
    return isinstance(tail, ModelRequest) and any(isinstance(p, ToolReturnPart) for p in tail.parts)


async def ok(messages, info):
    seen.append(messages)
    yield "ok"


async def write_then_die(messages, info):
    seen.append(messages)
    if not has_return(messages):
        yield {0: DeltaToolCall(name="record", json_args='{"amount": 5}', tool_call_id=f"call_{uuid.uuid4().hex[:6]}")}
        return
    yield "partial "
    raise ModelAPIError("m", "provider died")


async def die_at_once(messages, info):
    seen.append(messages)
    raise ModelAPIError("m", "provider down")
    yield ""  # noqa


def capabilities():
    return [
        h.InputGuardrail(guard=[secret_data(), personal_data(only=["credit_card"])]),
        h.SystemReminders(dynamic_reminders=[lambda ctx: "Today is 1 November."]),
        c.TieredCompaction(
            tiers=[
                c.ClearToolResults(max_tokens=1, keep_pairs=1),
                c.SlidingWindowCompaction(max_messages=1, keep_tokens=40, receipts=True),
            ],
            target_tokens=120,
        ),
    ]


def agent(stream):
    a = Agent(FunctionModel(stream_function=stream), capabilities=capabilities())

    @a.tool_plain
    async def record(amount: int) -> str:
        """Writes."""
        return f"saved {amount}"

    return a


async def turn(a, text, history, uid):
    run_id = uuid.uuid4().hex
    out = {"errors": [], "run_id": run_id}

    async def done(result):
        out["all"] = result.all_messages()
        out["new"] = result.new_messages()

    adapter = VercelAIAdapter(agent=a, run_input=SubmitMessage(id="t", messages=[UIMessage(id=uid, role="user", parts=[TextUIPart(text=text)])], trigger="submit-message"), manage_message_history="server", sdk_version=6)
    with capture_run_messages() as captured:
        async for ev in adapter.run_stream(message_history=history, on_complete=done, run_id=run_id):
            if type(ev).__name__ == "ErrorChunk":
                out["errors"].append(str(ev.error_text)[:80])
    out["captured"] = list(captured)
    return out


def kinds(messages):
    return [("req" if isinstance(m, ModelRequest) else "resp") + (f":{m.state}" if isinstance(m, ModelResponse) else "") for m in messages]


class WindowGuard(AbstractCapability):
    """Refuses to send a request to a model whose window it can't fit, rather than let a local server cut it silently."""

    def __init__(self, windows):
        self.windows = windows

    async def prepare_model_request(self, ctx, request_context):
        window = self.windows.get(request_context.model.model_name)
        if window and len(str(request_context.messages)) / 4 > window:
            raise ModelAPIError(request_context.model.model_name, f"history over this model's {window}-token window")
        return request_context


async def main():
    print("== A. Failure on the first request, card in the message, full config")
    seen.clear()
    seed = [ModelRequest(parts=[c.pin("SNAPSHOT")]), ModelResponse(parts=[TextPart("Snapshot read.")])]
    r = await turn(agent(die_at_once), "paid with 4111 1111 1111 1111", seed, "u1")
    text = str(r["captured"])
    print("   errors:", r["errors"], "| card reached the model:", "4111" in str(seen[-1]) if seen else None, "| card in captured:", "4111" in text, "| snapshot kept:", "SNAPSHOT" in text, "| kinds:", kinds(r["captured"]))
    ids = {m.run_id for m in r["captured"]}
    new = [m for m in r["captured"] if m.run_id == r["run_id"]]
    print("   run IDs on messages:", len(ids), "| this run's messages:", kinds(new))

    print("== B. A long thread, a write, compaction fires, then the provider dies")
    a_ok = agent(ok)
    h_ = seed
    for i in range(6):
        res = await turn(a_ok, f"turn {i} " + "x" * 200, h_, f"t{i}")
        h_ = stored(res["all"])
    size = len(h_)
    print("   compaction fired before this turn: oldest turn gone:", "turn 0 " not in str(h_), "| receipt present:", "compact" in str(h_).lower() or "removed" in str(h_).lower())
    seen.clear()
    r = await turn(agent(write_then_die), "log 5 on 5500 0000 0000 0004", h_, "u2")
    new = [m for m in r["captured"] if m.run_id == r["run_id"]]
    print("   history before:", size, "| captured:", len(r["captured"]), "| this run's:", kinds(new), "| card in captured:", "5500" in str(r["captured"]), "| snapshot kept:", "SNAPSHOT" in str(r["captured"]))
    print("   write's return kept:", "saved 5" in str(r["captured"]), "| errors:", r["errors"])
    r2 = await turn(agent(ok), "did it save?", stored(r["captured"]), "u3")
    print("   next turn answered:", "all" in r2, "| errors:", r2["errors"], "| the model can see the write:", "saved 5" in str(seen[-1]))

    print("== C. A backup smaller than the history")
    called = {"backup": 0}

    async def backup_stream(messages, info):
        called["backup"] += 1
        yield "answer from backup"

    primary = FunctionModel(stream_function=die_at_once, model_name="primary")
    backup = FunctionModel(stream_function=backup_stream, model_name="small-backup")
    for label, history in (("short thread", seed), ("long thread", seed + [ModelRequest.user_text_prompt("y" * 4000), ModelResponse(parts=[TextPart("z")])])):
        called["backup"] = 0
        a = Agent(primary, capabilities=[WindowGuard({"small-backup": 200}), ModelFallback(backup)])
        r = await turn(a, "hi", history, f"c{label}")
        print(f"   {label}: backup called: {called['backup']} | answered: {'all' in r} | errors: {r['errors']}")

    print("== D. on_complete adds a cut-off card to the stream")

    async def done_with_card(result):
        yield DataChunk(type="data-cut-off", data={"reason": "length"})

    adapter = VercelAIAdapter(agent=agent(ok), run_input=SubmitMessage(id="t", messages=[UIMessage(id="d", role="user", parts=[TextUIPart(text="hi")])], trigger="submit-message"), manage_message_history="server", sdk_version=6)
    chunks = [type(ev).__name__ + ":" + str(getattr(ev, "type", "")) async for ev in adapter.run_stream(on_complete=done_with_card)]
    print("   stream has it:", any("data-cut-off" in x for x in chunks), "| order:", chunks[-4:])

    print("== E. A pool across a database restart")
    dsn, data_dir = os.environ["DSN"], os.environ["PGDATA_DIR"]
    pool = AsyncConnectionPool(dsn, min_size=1, max_size=3, open=False, check=AsyncConnectionPool.check_connection, timeout=3, reconnect_timeout=30)
    await pool.open(wait=True)

    async def query():
        async with pool.connection() as conn:
            return (await (await conn.execute("SELECT 1")).fetchone())[0]

    print("   before:", await query())
    subprocess.run(["pg_ctl", "-D", data_dir, "-m", "fast", "-w", "stop"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    t = time.perf_counter()
    try:
        print("   while down:", await query())
    except Exception as e:  # noqa: BLE001
        print("   while down:", type(e).__name__, "after", round(time.perf_counter() - t, 1), "s")
    subprocess.run(["pg_ctl", "-D", data_dir, "-o", "-p 15433", "-l", str(Path(data_dir).parent / "pg8.log"), "-w", "start"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    t = time.perf_counter()
    for _ in range(40):
        try:
            print("   after restart:", await query(), "in", round(time.perf_counter() - t, 1), "s")
            break
        except Exception as e:  # noqa: BLE001
            print("   retry:", type(e).__name__)
            await asyncio.sleep(0.5)
    await pool.close()


asyncio.run(main(), loop_factory=asyncio.SelectorEventLoop)
