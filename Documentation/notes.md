# Notes

## Quick commands

uv run python scripts/clean_test_entries.py --apply ~/.archon/history/sessions/*.md
uv run python main.py

uvx code-review-graph build

cd ~/Documents/development/archon && git merge --ff-only worktree-tidy-knitting-pizza
git -C /Users/manczg/Documents/development/archon merge worktree-tidy-knitting-pizza --ff-only

find . -name '*.py' -not -path "./.claude/*" -not -path "./.venv*" | sed 's/.*/"&"/' | xargs  wc -l

---

## Features and bugfix requests 

## Real intentions

- What is the real goal fo the user? What wants really the user?

---

- Pseudo code generation as plan from user intention, then python code generation which will can invoke the archon's services (the pipeline could start it?)
- devils advocate review the pseudo code and python code and then fix until no issues is found, also check: does it cover the user's intetnion and will it satisfy the user's request?
- Run the script (which can contain multiple agetn run, split merge, anything)
The concept: if the logic of the pseudo code reflects right the user's original intention and then it is transformed well to the python script then the result will be excellent even in hard tasks too
- add more checks to this process to verify the intermediate steps to ensure the result will be correct

---

### Search

- search should handle any size of pdf, docx, xlsx, etc. Currently the PDF is limited to 1MB.
- multilanguage support donload a bigger module for this?
- video trans-scripting with video frame linking. Finf the word or expression and show me the related video image (or seek the video there and pause it (and the user can play it))

---

~ add task estimation and measurement logic for the decomposer (router) with learning ability.
decompose the task into atomic steps. websearch 1 step. read the result 1 step.

---

- drive syncs index them, remove the file form the disk but keep it on the drive and let check the search with the rag is it usable ot not (check drive online to check file cahnge, then pull index and remove local copy?)
- log this mcp too
- Indexing apple notes app content with automatic reindexing feature

---

- add playwright as default?

---

- Add the ability to run scheduled task on user request. Run the xy scheduled task now. In this case the system should start the scheduled task in exactly the same way as it would run on scheduled, so it must be run on the same code path.

---

# Pointer-based memory plus retrieval keys

Description. Store full artefacts (transcripts, notes, KB entries) externally. Inject into the LLM prompt only:

- a compact query,
- constraints (time range, project, participants),
- top‑k pointers (document IDs + minimal metadata),
- optionally 1–3 verbatim “evidence” spans.

This can beat any dialect because you stop paying repeated tokens for the same history.

This is aligned with the MemPalace architecture itself: it stores verbatim content and uses summaries/metadata primarily as a routing layer; AAAK is explicitly framed as a separate compression layer, not the storage default. 

Expected reduction. Effective reduction is dominated by “how much text you don’t send”. In steady-state agent systems, 10×–1000× reductions vs naïvely pasting full history are common in principle (highly workload-dependent).

- Pros. Best token economy; high fidelity if retrieval is correct; supports audits with verbatim evidence.

- Cons. Requires retrieval infra; failure mode is “missed evidence” rather than “bad compression”.

- Complexity. Medium–High.

- Compatibility. High with tool calling / RAG pipelines.

- Recommended use cases. Meeting transcripts; large KB; long-term agent memory; compliance contexts.

## Implementation steps.

Normalise artefacts into segments (turns/paragraphs) with stable IDs.
Index with embeddings + metadata filters (project/date/participants).
At query time: retrieve top‑k segments; optionally re-rank.
Provide LLM with (a) IDs + (b) minimal snippets.
Only fetch verbatim spans after the model commits to which IDs are needed.
Example encoding (prompt injection).

text
Copy



