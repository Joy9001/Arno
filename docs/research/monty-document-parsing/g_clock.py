"""Part G6: clock, randomness and sleep. Raw Monty defaults vs OS policy vs Code Mode defaults."""
import sys as _s, pathlib as _p
_s.path.insert(0, str(_p.Path(__file__).resolve().parent))   # lets `python -I` (isolated mode) still import sibling modules
import datetime
import os
import time

from pydantic_ai import Agent
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart, ToolReturnPart
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai_harness import CodeMode
from pydantic_monty import Monty, ResourceLimits

os.environ["FIN_SECRET_TEST"] = "sk-not-a-real-key-123"
LIM = ResourceLimits(max_memory=256 * 1024 * 1024, max_feed_duration_secs=3)


def raw(pool, label, code, **checkout):
    t0 = time.perf_counter()
    try:
        with pool.checkout(**checkout) as s:
            r = s.feed_run(code)
        res = f"RETURNED {r!r}"
    except Exception as e:
        res = f"{type(e).__name__}: {str(e).strip().splitlines()[-1][:100]}"
    print(f"  {label:52s} {time.perf_counter() - t0:6.2f}s  {res}")


def code_mode(label, code):
    st = {"i": 0}

    def fn(messages, info: AgentInfo):
        if st["i"] == 0:
            st["i"] = 1
            return ModelResponse(parts=[ToolCallPart("run_code", {"code": code})])
        return ModelResponse(parts=[TextPart("done")])
    agent = Agent(FunctionModel(fn), capabilities=[CodeMode()])
    r = agent.run_sync("x")
    out = [str(p.content)[:170].replace("\n", " ") for m in r.all_messages() for p in getattr(m, "parts", []) if isinstance(p, ToolReturnPart)]
    print(f"  {label:52s} {out}")


if __name__ == "__main__":
    with Monty(request_timeout=20) as pool:
        raw(pool, "raw default: date.today()", "import datetime\nstr(datetime.date.today())", limits=LIM)
        raw(pool, "raw os_policy frozen clock + sleep zero", "import datetime, time\ntime.sleep(5)\nstr(datetime.date.today())",
            limits=LIM, os_policy={"datetime": datetime.datetime(2026, 1, 31), "sleep": "zero"})
        raw(pool, "raw max_total_sleep_secs=1, time.sleep(10)", "import time\ntime.sleep(10)",
            limits=ResourceLimits(max_memory=256 * 1024 * 1024, max_feed_duration_secs=3, max_total_sleep_secs=1))
    code_mode("Code Mode default: date.today()", "import datetime\nstr(datetime.date.today())")
    code_mode("Code Mode default: os.getenv('FIN_SECRET_TEST')", "import os\nos.getenv('FIN_SECRET_TEST')")
    code_mode("Code Mode default: open('/etc/passwd')", "open('/etc/passwd').read()")
    code_mode("Code Mode default: random.random()", "import random\nrandom.random()")
    code_mode("Code Mode default: time.sleep(2)", "import time\ntime.sleep(2)\n'slept'")
