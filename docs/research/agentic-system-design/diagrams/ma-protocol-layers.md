# Protocol layering: A2A between agents, MCP below to tools

**What it shows:** The settled layered framing: MCP connects an agent to tools, data and prompts; A2A connects agents across ownership boundaries. Includes protocol status and the caveat that A2A is overkill inside one codebase.

**Why this diagram type:** A small graph with labelled protocol edges shows which layer each protocol occupies; status text goes in notes to avoid a table restatement. (Rendered with graphviz via Kroki; source: `ma-protocol-layers.dot`.)

**Design decisions:** Purple for external systems and protocol nodes, blue for agents. Two agents shown across a vendor boundary because that is where A2A is argued to pay off.

![Protocol layering: A2A between agents, MCP below to tools](./ma-protocol-layers.png)

**Assumptions / notes:** Status dates are as of 2026-10 per the file; counts are logo-level and AAIF/A2A umbrella membership is contested in sources (see the file).

**Illustrates:** multi-agent-systems.md, section 9 (Inter-agent and agent-tool protocols)
