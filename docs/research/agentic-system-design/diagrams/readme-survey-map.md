# Survey map · Where each file sits in an agentic system

![Survey map · Where each file sits in an agentic system](./readme-survey-map.png)

**What it shows.** How the seven topic files of this survey map onto one agentic system: the
foundations file supplies the vocabulary; the request path, agent layer and retrieval/memory layer
are the system itself, inside a dashed boundary for the operations concerns that wrap them; case
studies and the landscape file sit outside the stack as evidence and tooling. Illustrates the index
in [`../README.md`](../README.md).

**Type.** Layer stack (diagram-design skill, default skin, minimal light, `doc-inline` 960×600).
The four rows are genuinely ordered from concepts to data, which is what the layer-stack type is for.
The two non-layer files are deliberately placed *outside* the stack as cards rather than forced in as
fake layers, which the type reference lists as an anti-pattern.

**Design decisions.** Accent on one focal layer only (the agent layer, the survey's subject). The
operations file is a dashed boundary rather than a fifth layer, because it wraps the other three
rather than sitting above or below them. File names are set in Geist Mono as technical content; layer
names in Geist sans. No connectors: every relationship is carried by position and containment.

**Assumptions.** The layering is a reading aid, not a claim that every system has all three layers;
many systems in the survey are pure workflows with no agent layer.

**Source.** [`readme-survey-map.html`](./readme-survey-map.html). PNG and SVG exported with the
skill's `export_png.py` (2×) and `export_svg.py`.
