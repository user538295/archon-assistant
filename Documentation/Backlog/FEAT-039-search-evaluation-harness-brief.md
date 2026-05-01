# Feature Brief: Search Evaluation Harness

**Roadmap reference**: FEAT-037 item #4 — "Build an evaluation harness and data-collection loop"
**Depends on**: FEAT-038 (Search Product Separation) — must land first; all code goes in `packages/archon-search/`

---

## Problem

Every ranking change to the search pipeline — reranker tuning, RRF weight adjustment, routing threshold changes — is currently unmeasurable. There is no way to verify that a change improves retrieval quality or to detect a regression before it ships.

## Goal

Every ranking change can be compared against a fixed benchmark. CI reports metric deltas (recall@k, MRR, nDCG@k, reranker lift, routing accuracy, latency percentiles) for every PR that touches the retrieval pipeline.

## Users & Context

Maintainers modifying search internals (store, reranker, router, pipeline). They need confidence that a change improves — or at least doesn't regress — retrieval quality before merging.

## Core Flow

1. Maintainer runs `uv run pytest packages/archon-search/tests/ -m eval` locally or CI triggers on PR.
2. Pytest loads the committed synthetic corpus (fixture files in `packages/archon-search/tests/eval/`).
3. Harness ingests the corpus into an in-memory LanceDB instance using mock embedder and reranker backends.
4. Harness runs each benchmark query through the full pipeline (embed → hybrid search → rerank → route).
5. Pipeline returns `EvalResult` objects with decomposed component scores alongside the final result.
6. Harness computes all 6 metric categories against relevance labels.
7. Pytest assertions enforce minimum thresholds; failures are reported as test failures with metric deltas.

## In Scope

- **Score decomposition**: extend `SearchResult` (or add `EvalResult` wrapper) with `vector_score`, `fts_score`, `rrf_score`, `reranker_score` fields — prerequisite for reranker lift and nDCG.
- **Synthetic benchmark corpus**: 50–100 documents, 25–30 queries with relevance labels, organized into 2–3 thematically distinct collections (e.g. "code", "prose", "mixed"). Committed to `packages/archon-search/tests/eval/`.
- **Relevance label format**: JSONL or JSON file mapping `query_id → list[relevant_doc_id]` with optional grade (binary or graded).
- **Metrics implemented**:
  - `recall@k` (k=1, 3, 5)
  - `MRR` (mean reciprocal rank)
  - `nDCG@k` (k=5, 10)
  - `reranker lift` (nDCG before vs. after reranking)
  - `routing accuracy` (did the router include the collection containing the gold chunk?)
  - `latency percentiles` (p50, p95 end-to-end per query, measured in-process)
- **pytest integration**: `@pytest.mark.eval` marker; excluded from fast CI (`-m 'not eval'`), included in release CI.
- **Minimum threshold assertions**: configurable per metric; PR fails if any metric drops below floor.
- All code lives in `packages/archon-search/tests/eval/` and `packages/archon-search/archon_search/eval/`.

## Out of Scope

- **Online data collection** (query logging, relevance feedback, judgment capture from real usage) — different system, different privacy surface; deferred.
- **A/B experimentation framework** — deferred until offline harness is proven useful.
- **`archon-search eval` CLI command** — pytest is sufficient for v1; CLI can follow when operators want to run eval against their own data.
- **LLM-generated relevance labels** — labels are hand-authored for the fixture corpus; automation is a future iteration.
- **Auth, namespace isolation, public REST API** — covered by FEAT-038 and later items.
- **MRR/nDCG as CI gate in fast test suite** — eval tests are slow-marked; they gate release CI only.

## Key Decisions

- **Post-FEAT-038 only**: evaluation code goes directly into `packages/archon-search/` — no migration cost, clean package boundary from day one.
- **Score decomposition in this feature**: adding `vector_score`, `fts_score`, `rrf_score`, `reranker_score` is small (one dataclass change + wiring) and is the prerequisite for half the metrics. It belongs here, not in a separate ticket.
- **Synthetic corpus, not user data**: a committed fixture corpus is the only CI-safe, reproducible approach. Evaluation against real user indices is a future CLI feature.
- **Routing accuracy via gold-collection recall**: router is "correct" if it includes the collection containing the relevant document in its shortlist. Falls out of relevance labels for free; routing precision deferred.
- **All 6 metric categories in v1**: the roadmap requires all of them; decomposed scores make them all computable from the same pipeline pass.
- **pytest only, no new tooling**: `@pytest.mark.eval` marker reuses existing infrastructure with zero new entrypoints to maintain.

## Edge Cases & Constraints

- **Mock backends required**: eval tests must use mock embedder and reranker backends (same pattern as existing tests) to avoid model downloads in CI. Latency measurements will reflect mock speed, not production — document this explicitly in the harness output.
- **Score decomposition is additive, not breaking**: existing callers that only use `score` are unaffected; new fields default to `None` when not computed (e.g. when reranker is disabled).
- **FTS graceful degradation**: eval harness must handle collections with no FTS index (FTS score = None); metrics must not crash on missing component scores.
- **Routing accuracy with pinned collections**: pinned collections bypass routing — queries against pinned collections should be excluded from routing accuracy measurement or flagged separately.
- **LanceDB in-memory per test module**: use module-scoped fixture (same pattern as existing `connected_store`) to avoid Tokio thread pool overhead per test.
- **Threshold floors are informational in v1**: initial thresholds should be set from the first run's actual numbers (no invented baselines); tighten them as the system improves.

## Open Questions

- What corpus domain best represents real Archon usage? (code + documentation + prose seems right, but maintainer call.)
- Should graded relevance (0/1/2) or binary (0/1) labels be used? Binary is simpler; graded enables more sensitive nDCG. Decision deferred to plan phase.
- Should the harness emit a structured JSON report artifact (for trend tracking over time) or just pytest output? Deferred to plan phase.

## Future Iterations

- **Online query logging**: opt-in logging of real queries + results to a local file for offline labeling.
- **`archon-search eval` CLI**: run the harness against a live index with user-provided query/label files.
- **Judgment capture UI**: lightweight way to thumbs-up/down results from Telegram for label collection.
- **Trend tracking**: store metric snapshots per commit/release and surface regressions over time.
- **LLM-assisted label generation**: auto-generate relevance labels from the ingested corpus using Claude.

## Recommendation

This is the right feature to build now — immediately after FEAT-038 lands. Without it, every retrieval improvement in items 7–14 of the roadmap is untestable. The hardest part is authoring a synthetic corpus that's representative enough to produce meaningful signal; invest time there rather than in metric complexity. The one thing that must not be compromised is CI enforceability — if eval tests are optional, they will be ignored, and the harness will rot.
