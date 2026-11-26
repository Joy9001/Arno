# An import passes the checks before review

**What it shows:** How a statement becomes ledger rows. An upload is stored, opens an import card,
and is unlocked on the spot if you give the card its password. Extraction turns the file into rows:
one vision call for images and short PDFs, or a script the host runs in the sandbox for
spreadsheets, text formats, and long PDFs. The rows go to the checks, which are code. A failed
check goes back to extraction once with its reason. A pass goes to the review screen. Nothing is
saved for next month: the next statement is read the same way.

**Diagram type:** Flowchart. No semantic pattern matched.

**What the budget forced out:** Reading the file and the password card are in
[`../specs/import.md`](../specs/import.md). "Status" covers the progress and result the import card
shows. 6 nodes, 7 arrows; the accent marks the import card and extraction.

![An import passes the checks before review](./sd-import-pipeline.png)

**Source:** `sd-import-pipeline.html` (diagram-design skill, default skin, minimal light,
doc-inline preset).

**Illustrates:** [`../architecture.md`](../architecture.md), step 5.
