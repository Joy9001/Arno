"""Part D4: which Python types survive a Code Mode tool return (what the script sees)."""
import sys as _s, pathlib as _p
_s.path.insert(0, str(_p.Path(__file__).resolve().parent))   # lets `python -I` (isolated mode) still import sibling modules
import datetime as dt
import decimal
import uuid
from dataclasses import dataclass

from pydantic import BaseModel
from pydantic_ai import Agent
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart, ToolReturnPart
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.toolsets import FunctionToolset
from pydantic_ai_harness import CodeMode


@dataclass
class DC:
    a: int


class BM(BaseModel):
    x: int
    when: dt.date


async def sample() -> dict:
    """Return one value of each type."""
    return {"int": 5, "float": 1.5, "str": "x", "bytes": b"ab", "tuple": (1, 2), "set": {1}, "date": dt.date(2026, 1, 5),
            "datetime": dt.datetime(2026, 1, 5, 1, 2), "Decimal": decimal.Decimal("1234.50"), "UUID": uuid.UUID(int=1),
            "intkeys": {1: "a"}, "dataclass": DC(1), "pydantic": BM(x=1, when=dt.date(2026, 1, 5)), "none": None}


CODE = """
s = await sample()
{k: [type(v).__name__, repr(v)[:50]] for k, v in s.items()}
"""

st = {"i": 0}


def fn(messages, info: AgentInfo):
    if st["i"] == 0:
        st["i"] = 1
        return ModelResponse(parts=[ToolCallPart("run_code", {"code": CODE})])
    return ModelResponse(parts=[TextPart("done")])


agent = Agent(FunctionModel(fn), toolsets=[FunctionToolset(tools=[sample]).with_metadata(code_mode=True)],
              capabilities=[CodeMode(tools={"code_mode": True})])
r = agent.run_sync("x")
for m in r.all_messages():
    for p in getattr(m, "parts", []):
        if isinstance(p, ToolReturnPart) and p.tool_name == "run_code":
            c = p.content
            if isinstance(c, dict):
                for k, v in c.items():
                    print(f"  host {k:9s} -> script sees {v[0]:10s} {v[1]}")
            else:
                print(str(c)[:800])
