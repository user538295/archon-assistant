# FEAT-039 — Search Evaluation Harness
**Purpose**: Add a deterministic offline evaluation harness for the extracted Search package so every retrieval or routing change can be measured against a committed benchmark before release.
**Audience**: Maintainers of `packages/archon-search/` who change retrieval, reranking, routing, or performance-sensitive search internals.
**Status**: To Do

---

## Background

FEAT-037 identified evaluation discipline as a prerequisite for making Search a standalone product. The current verified codebase has strong unit and integration coverage under `tests/search/`, but it does not expose a first-class retrieval benchmark loop with committed relevance labels, metric computation, or CI gating.

This feature must land after FEAT-038, because the brief intentionally places all new code inside the extracted package boundary: `packages/archon-search/archon_search/` and `packages/archon-search/tests/`. The current Archon implementation is still the source of truth for how the future package should behave: hybrid retrieval is implemented in `archon/search/store.py`, reranking in `archon/search/reranker.py`, routing in `archon/search/router.py`, and end-to-end search orchestration in `archon/search/pipeline.py`.

The practical gap is simple: maintainers can change RRF weighting, reranker behavior, routing thresholds, or search latency characteristics today without a reproducible signal showing whether the change helped or regressed quality. This plan closes that gap with a synthetic offline corpus, deterministic mock backends, metric computation, and pytest-based release gating.

## Goal

After FEAT-039 is complete, a maintainer can run an evaluation-only pytest slice inside `packages/archon-search/` and receive a stable report covering recall@k, MRR, nDCG@k, reranker lift, routing accuracy, and latency percentiles. The same suite runs in release CI, enforces threshold floors, and fails visibly when a ranking or routing change regresses the benchmark.

---

## Scope

### In Scope
- Deterministic evaluation-only models, fixtures, and pytest infrastructure inside `packages/archon-search/`
- Synthetic benchmark corpus with 50-100 committed documents across 2-3 collections
- Query and relevance-label fixture format, including optional graded judgments
- Score decomposition for search results so pre-rerank and post-rerank metrics are computable
- Metric computation for recall@1/3/5, MRR, nDCG@5/10, reranker lift, routing accuracy, and latency percentiles
- Threshold-based assertions and structured per-run output for CI diagnostics
- Documentation for how maintainers update the corpus, labels, and thresholds

### Out of Scope
- Live query logging from real user traffic
- Automatic relevance-label generation with LLMs
- A standalone `archon-search eval` CLI command
- Public REST endpoints for evaluation runs
- Multi-tenant or authenticated evaluation services
- Fast-suite CI gating; eval remains a dedicated slower test slice

---

## Acceptance criteria
- [ ] `packages/archon-search/tests/` contains an `eval` pytest slice that runs entirely with deterministic mock embedder and reranker backends
- [ ] The committed fixture corpus contains 50-100 documents split across at least 2 distinct collections and 25-30 benchmark queries with explicit relevance labels
- [ ] Search results expose enough decomposed scoring data to compute fused-score and reranker-lift metrics without reverse-engineering internal rankings
- [ ] The harness measures `recall@1`, `recall@3`, `recall@5`, `MRR`, `nDCG@5`, `nDCG@10`, `reranker lift`, `routing accuracy`, `latency p50`, and `latency p95`
- [ ] Collections that bypass routing via pinning are excluded from routing-accuracy assertions and are reported separately
- [ ] Eval thresholds are loaded from committed config, compared against actual run output, and cause pytest failures when any gated metric drops below its floor
- [ ] Pytest failure output shows metric deltas and the specific metric(s) that failed
- [ ] Eval tests are excluded from the fast/default test slice and included in release CI
- [ ] Harness documentation explains fixture structure, label semantics, and the rule for updating thresholds from measured baselines rather than invented targets

---

## What does NOT change
- The main Archon daemon does not gain runtime evaluation behavior
- Production search requests do not execute benchmark logic
- Search does not require real model downloads in tests
- Public APIs, auth, namespace isolation, and job control remain FEAT-038 and later concerns
- This feature does not introduce online experimentation or live traffic capture

---

## Known limitations / accepted trade-offs
- The first benchmark corpus is synthetic, so it measures regression consistency rather than full real-world relevance coverage
- In-process latency numbers reflect deterministic mock backends and local LanceDB behavior, not production model latency
- Thresholds in v1 are conservative and baseline-derived; they are meant to detect regressions, not certify world-class quality
- Routing accuracy is measured as gold-collection inclusion in the shortlist, not full ranking precision across every collection
- The initial harness is pytest-only; maintainers who want ad hoc local eval against custom corpora will need a later CLI feature

---

## Architecture

- New modules under `packages/archon-search/archon_search/eval/`:
  - `fixtures.py`
    - `EvalDocument(doc_id: str, collection: str, text: str, metadata: dict[str, str] | None = None)` dataclass
    - `EvalQuery(query_id: str, text: str, collection: str | None = None, pinned: bool = False)` dataclass
    - `RelevanceLabel(query_id: str, doc_id: str, grade: int = 1)` dataclass
    - `EvalCorpus(documents: list[EvalDocument], queries: list[EvalQuery], labels: list[RelevanceLabel])` dataclass
    - `load_eval_corpus(root: Path) -> EvalCorpus`
  - `types.py`
    - `SearchScoreBreakdown(vector_score: float | None, fts_score: float | None, rrf_score: float, reranker_score: float | None)` dataclass
    - `EvalSearchResult(doc_id: str, chunk_id: str, text: str, score: float, source_path: str, collection: str, scores: SearchScoreBreakdown)` dataclass
    - `QueryEvalTrace(query: EvalQuery, pre_rerank: list[EvalSearchResult], post_rerank: list[EvalSearchResult], routed_collections: list[str], latency_ms: float)` dataclass
    - `EvalMetrics(recall_at_1: float, recall_at_3: float, recall_at_5: float, mrr: float, ndcg_at_5: float, ndcg_at_10: float, reranker_lift: float, routing_accuracy: float | None, latency_p50_ms: float, latency_p95_ms: float)` dataclass
  - `metrics.py`
    - `compute_recall_at_k(traces: list[QueryEvalTrace], labels: dict[str, dict[str, int]], k: int) -> float`
    - `compute_mrr(traces: list[QueryEvalTrace], labels: dict[str, dict[str, int]]) -> float`
    - `compute_ndcg_at_k(traces: list[QueryEvalTrace], labels: dict[str, dict[str, int]], k: int, *, use_prerank: bool = False) -> float`
    - `compute_reranker_lift(traces: list[QueryEvalTrace], labels: dict[str, dict[str, int]], k: int = 10) -> float`
    - `compute_routing_accuracy(traces: list[QueryEvalTrace], labels: dict[str, dict[str, int]]) -> float | None`
    - `compute_latency_percentiles(latencies_ms: list[float]) -> tuple[float, float]`
  - `runner.py`
    - `EvalThresholds(recall_at_1: float, recall_at_3: float, recall_at_5: float, mrr: float, ndcg_at_5: float, ndcg_at_10: float, reranker_lift: float, routing_accuracy: float | None, latency_p50_ms: float, latency_p95_ms: float)` dataclass
    - `EvalReport(metrics: EvalMetrics, thresholds: EvalThresholds, query_count: int, document_count: int, skipped_routing_queries: int, notes: list[str])` dataclass
    - `async run_eval_suite(corpus_root: Path, config_path: Path) -> EvalReport`
    - `assert_thresholds(report: EvalReport) -> None`
    - `render_report(report: EvalReport) -> str`
- Search package changes outside `eval/`:
  - `packages/archon-search/archon_search/_types.py`
    - extend search result types with decomposed score fields, or add a dedicated eval-only wrapper that can preserve `vector_score`, `fts_score`, `rrf_score`, and `reranker_score`
  - `packages/archon-search/archon_search/store.py`
    - `async hybrid_search(...) -> list[SearchResult]` must return enough rank/score detail to expose vector, FTS, and fused scoring
  - `packages/archon-search/archon_search/reranker.py`
    - `async rerank(...) -> list[SearchResult]` must preserve incoming fused score and attach reranker scores without destroying evaluation visibility
  - `packages/archon-search/archon_search/pipeline.py`
    - add an eval-oriented execution path:
      - `async search_eval(query: str, collection: str) -> tuple[list[SearchResult], list[SearchResult]]`
      - returns `(pre_rerank, post_rerank)` for metric computation
  - `packages/archon-search/archon_search/router.py`
    - expose routed shortlist names for eval assertions via:
      - `async select(query: str) -> list[CollectionMeta]`
      - existing shortlist output is sufficient if the eval runner records it
- Fixture files under `packages/archon-search/tests/eval/`:
  - `corpus/` directory with per-collection fixture documents
  - `queries.jsonl`
  - `labels.jsonl`
  - `thresholds.toml`
- Data flow:
  - fixture loader reads corpus + labels
  - eval fixture ingests corpus into a module-scoped temporary LanceDB store with mock backends
  - runner executes each query through routing and search paths
  - metrics module computes aggregate scores
  - threshold assertion converts regressions into pytest failures
- New pytest marker and config:
  - `eval`: slow deterministic retrieval benchmark slice excluded from default runs
- New config keys in `packages/archon-search/tests/eval/thresholds.toml`:
  - `recall_at_1: float`
  - `recall_at_3: float`
  - `recall_at_5: float`
  - `mrr: float`
  - `ndcg_at_5: float`
  - `ndcg_at_10: float`
  - `reranker_lift: float`
  - `routing_accuracy: float | null`
  - `latency_p50_ms: float`
  - `latency_p95_ms: float`
- Default policy:
  - thresholds are copied from the first accepted measured baseline after FEAT-038, not guessed in advance

---

## Tests

- **`test_load_eval_corpus_reads_documents_queries_and_labels`** (unit): fixture loader returns the expected document, query, and label counts
- **`test_load_eval_corpus_rejects_unknown_label_doc_id`** (unit): labels referencing missing documents fail fast
- **`test_load_eval_corpus_rejects_duplicate_query_ids`** (unit): duplicate query identifiers are rejected
- **`test_compute_recall_at_k`** (unit): recall is computed correctly for known miniature traces
- **`test_compute_mrr`** (unit): reciprocal-rank calculation matches expected values
- **`test_compute_ndcg_at_k_binary_labels`** (unit): binary-label nDCG math is correct
- **`test_compute_ndcg_at_k_graded_labels`** (unit): graded-label nDCG math is correct
- **`test_compute_reranker_lift`** (unit): post-rerank nDCG minus pre-rerank nDCG is computed correctly
- **`test_compute_routing_accuracy_skips_pinned_queries`** (unit): pinned queries are excluded from routing accuracy
- **`test_compute_latency_percentiles`** (unit): percentile math returns stable p50 and p95 values
- **`test_hybrid_search_exposes_score_breakdown`** (integration): hybrid search returns vector, FTS, and RRF fields needed by eval
- **`test_rerank_preserves_prerank_scores_and_adds_reranker_score`** (integration): reranking does not discard fused-score provenance
- **`test_search_eval_returns_pre_and_post_rerank_results`** (integration): eval pipeline path returns both result lists in a single query run
- **`test_eval_runner_executes_full_corpus`** (integration): runner ingests corpus and produces one trace per query
- **`test_eval_runner_records_router_shortlist`** (integration): routed collection names are captured for routing metrics
- **`test_assert_thresholds_passes_on_baseline_report`** (integration): baseline-aligned report does not fail
- **`test_assert_thresholds_reports_metric_regressions`** (integration): failure message includes metric deltas and failing thresholds
- **`test_eval_pytest_marker_excluded_from_default_run`** (integration): default test selection excludes `eval`
- **`test_eval_suite_smoke`** (e2e): full eval slice runs from pytest against the committed corpus and emits a readable report
- **`test_release_ci_includes_eval_slice`** (integration): CI config references the eval slice in release coverage

---

## Documentation update
- [ ] `Documentation/Backlog/FEAT-037-search-world-class-roadmap.md`, section `Priority 0: Product Separation / 4. Build an evaluation harness and data-collection loop`, path: `Documentation/Backlog/FEAT-037-search-world-class-roadmap.md`
- [ ] `Documentation/Architecture/180_search_architecture.md`, section `Search Architecture`, path: `Documentation/Architecture/180_search_architecture.md`
- [ ] `packages/archon-search/README.md`, section `Evaluation`, path: `packages/archon-search/README.md`
- [ ] `packages/archon-search/tests/eval/README.md`, section `Fixture format and threshold maintenance`, path: `packages/archon-search/tests/eval/README.md`

---

## Task breakdown

### Phase 1 — Eval domain model and fixture contract
> **Releasable**: when Task 1.4 is complete; fixture files can be loaded and validated end-to-end, but no search execution happens yet

#### Task 1.1 — Eval fixture dataclasses
- [ ] **File**: `packages/archon-search/archon_search/eval/fixtures.py`
- **Depends on**: nothing
- **Description**:
  - Add `EvalDocument`, `EvalQuery`, `RelevanceLabel`, and `EvalCorpus` dataclasses with the signatures listed in the Architecture section.
  - `EvalQuery.collection` is optional so routing queries can be unpinned while still carrying a gold collection indirectly through labels.
  - `RelevanceLabel.grade` defaults to `1`; reject grades `< 0`.
  - Keep these dataclasses free of runtime search dependencies so metric tests can construct them without LanceDB.
- **Releasable**: eval fixtures have a canonical in-package schema
- **Tests (TDD)** — `packages/archon-search/tests/eval/test_fixtures.py`:
  - Unit: `test_relevance_label_default_grade` — omitted grade defaults to `1`
  - Unit: `test_relevance_label_rejects_negative_grade` — invalid grade raises `ValueError`
  - Unit: `test_eval_query_supports_optional_collection` — unpinned queries allow `collection=None`
  - Checkpoint: `uv run pytest packages/archon-search/tests/eval/test_fixtures.py -k "grade or collection" -v`

#### Task 1.2 — Fixture loader and file-format parser
- [ ] **File**: `packages/archon-search/archon_search/eval/fixtures.py`
- **Depends on**: Task 1.1
- **Description**:
  - Implement `load_eval_corpus(root: Path) -> EvalCorpus`.
  - Read documents from `root / "corpus"` recursively; map relative collection directories to `EvalDocument.collection`.
  - Read `queries.jsonl` and `labels.jsonl`.
  - Accept binary or graded labels; normalize missing grade to `1`.
  - Fail fast on malformed JSONL, duplicate IDs, unknown `doc_id`, or labels for unknown queries.
- **Releasable**: a committed corpus can be loaded into validated Python objects
- **Tests (TDD)** — `packages/archon-search/tests/eval/test_fixtures.py`:
  - Unit: `test_load_eval_corpus_reads_documents_queries_and_labels` — valid tree loads successfully
  - Unit: `test_load_eval_corpus_rejects_unknown_label_doc_id` — missing doc reference raises `ValueError`
  - Unit: `test_load_eval_corpus_rejects_duplicate_query_ids` — duplicate query IDs raise `ValueError`
  - Unit: `test_load_eval_corpus_rejects_unknown_query_id_in_labels` — orphan label rows raise `ValueError`
  - Checkpoint: `uv run pytest packages/archon-search/tests/eval/test_fixtures.py -k "load_eval_corpus" -v`

#### Task 1.3 — Committed synthetic corpus and labels
- [ ] **File**: `packages/archon-search/tests/eval/queries.jsonl`
- **Depends on**: Task 1.2
- **Description**:
  - Add the committed corpus tree under `packages/archon-search/tests/eval/corpus/`.
  - Create 50-100 concise documents across 2-3 collections representing code, documentation/prose, and mixed content.
  - Add 25-30 benchmark queries in `queries.jsonl`.
  - Add corresponding `labels.jsonl` with at least one relevant document per query.
  - Keep doc IDs stable by explicitly assigning fixture IDs instead of deriving them from temporary paths.
- **Releasable**: the package contains a deterministic benchmark dataset that can be versioned and reviewed
- **Tests (TDD)** — `packages/archon-search/tests/eval/test_corpus_contract.py`:
  - Unit: `test_eval_corpus_document_count_range` — corpus contains 50-100 documents
  - Unit: `test_eval_corpus_query_count_range` — corpus contains 25-30 queries
  - Unit: `test_every_query_has_relevance_labels` — no unlabeled queries
  - Unit: `test_collections_cover_multiple_domains` — at least 2 distinct collections exist
  - Checkpoint: `uv run pytest packages/archon-search/tests/eval/test_corpus_contract.py -v`

#### Task 1.4 — Threshold config contract
- [ ] **File**: `packages/archon-search/archon_search/eval/runner.py`
- **Depends on**: Task 1.2
- **Description**:
  - Add `EvalThresholds` dataclass and `load_thresholds(config_path: Path) -> EvalThresholds`.
  - Parse `thresholds.toml`.
  - Allow `routing_accuracy = null` when the current corpus is entirely pinned or routing is intentionally not gated.
  - Reject missing required threshold keys and negative latency budgets.
- **Releasable**: eval runs can be compared against committed floor values
- **Tests (TDD)** — `packages/archon-search/tests/eval/test_runner.py`:
  - Unit: `test_load_thresholds_reads_all_metrics` — valid TOML parses into `EvalThresholds`
  - Unit: `test_load_thresholds_allows_null_routing_accuracy` — nullable routing floor accepted
  - Unit: `test_load_thresholds_rejects_missing_metric` — incomplete config raises `ValueError`
  - Checkpoint: `uv run pytest packages/archon-search/tests/eval/test_runner.py -k "threshold" -v`

### Phase 2 — Score decomposition in the search stack
> **Releasable**: when Task 2.4 is complete; search internals expose pre-rerank and reranker scoring detail needed by the harness

#### Task 2.1 — Score breakdown types
- [ ] **File**: `packages/archon-search/archon_search/eval/types.py`
- **Depends on**: nothing
- **Description**:
  - Add `SearchScoreBreakdown`, `EvalSearchResult`, `QueryEvalTrace`, and `EvalMetrics` dataclasses with the signatures from the Architecture section.
  - Keep `EvalSearchResult` separate from the package’s normal public `SearchResult` if that avoids widening the default API prematurely.
  - `collection` must be explicit on eval results so cross-collection traces do not rely on path parsing.
- **Releasable**: the eval layer has stable types for traces and aggregate metrics
- **Tests (TDD)** — `packages/archon-search/tests/eval/test_types.py`:
  - Unit: `test_eval_search_result_contains_score_breakdown` — nested score fields are preserved
  - Unit: `test_query_eval_trace_accepts_optional_routing` — empty routed collections remain valid
  - Checkpoint: `uv run pytest packages/archon-search/tests/eval/test_types.py -v`

#### Task 2.2 — Hybrid-search score provenance
- [ ] **File**: `packages/archon-search/archon_search/store.py`
- **Depends on**: Task 2.1
- **Description**:
  - Extend `async hybrid_search(...)` to preserve vector rank/score, FTS rank/score, and final RRF score per returned result.
  - When no FTS index exists, set `fts_score` to `None` instead of fabricating zero.
  - Keep the existing fused `score` field usable by non-eval callers.
  - Avoid breaking default production call sites; additive fields only.
- **Releasable**: prere-rank results contain vector, FTS, and fused scoring detail
- **Tests (TDD)** — `packages/archon-search/tests/test_store.py`:
  - Integration: `test_hybrid_search_exposes_score_breakdown` — results include vector, FTS, and RRF values
  - Integration: `test_hybrid_search_sets_fts_score_none_without_index` — no FTS index yields `None`, not an exception
  - Checkpoint: `uv run pytest packages/archon-search/tests/test_store.py -k "score_breakdown or fts_score_none" -v`

#### Task 2.3 — Reranker-score preservation
- [ ] **File**: `packages/archon-search/archon_search/reranker.py`
- **Depends on**: Task 2.2
- **Description**:
  - Update `async rerank(query: str, candidates: list[SearchResult], top_k: int) -> list[SearchResult]` so reranked results preserve incoming score provenance and attach `reranker_score`.
  - Keep final sort order based on reranker output while retaining original fused score for eval reporting.
  - When reranking is disabled or candidates are empty, keep `reranker_score=None`.
- **Releasable**: post-rerank results can be compared to prere-rank results without losing provenance
- **Tests (TDD)** — `packages/archon-search/tests/test_reranker.py`:
  - Integration: `test_rerank_preserves_prerank_scores_and_adds_reranker_score` — reranked results keep fused score and add reranker score
  - Unit: `test_rerank_empty_candidates_keeps_no_scores` — empty candidate list returns cleanly
  - Checkpoint: `uv run pytest packages/archon-search/tests/test_reranker.py -k "reranker_score or empty_candidates" -v`

#### Task 2.4 — Eval pipeline execution path
- [ ] **File**: `packages/archon-search/archon_search/pipeline.py`
- **Depends on**: Task 2.2, Task 2.3
- **Description**:
  - Add `async search_eval(query: str, collection: str) -> tuple[list[SearchResult], list[SearchResult]]`.
  - Reuse the same embedder, store, and reranker instances as normal search.
  - Return prere-rank and post-rerank lists from a single query execution so timing and candidate sets stay aligned.
  - Do not change the behavior of the normal `search()` method.
- **Releasable**: eval code can obtain both ranking stages from one pipeline call
- **Tests (TDD)** — `packages/archon-search/tests/test_pipeline.py`:
  - Integration: `test_search_eval_returns_pre_and_post_rerank_results` — both result lists are returned in order
  - Integration: `test_search_eval_matches_search_final_order` — post-rerank output matches the normal `search()` result ordering
  - Checkpoint: `uv run pytest packages/archon-search/tests/test_pipeline.py -k "search_eval" -v`

### Phase 3 — Metric computation and report generation
> **Releasable**: when Task 3.4 is complete; a complete eval report can be computed from traces, but pytest wiring is still optional

#### Task 3.1 — Ranking metric functions
- [ ] **File**: `packages/archon-search/archon_search/eval/metrics.py`
- **Depends on**: Task 2.1
- **Description**:
  - Implement `compute_recall_at_k`, `compute_mrr`, and `compute_ndcg_at_k`.
  - `compute_ndcg_at_k` must support binary and graded relevance by consuming the label-grade map directly.
  - Use post-rerank results by default; prere-rank mode must be opt-in for reranker-lift computation.
- **Releasable**: core ranking-quality metrics are available for any collected traces
- **Tests (TDD)** — `packages/archon-search/tests/eval/test_metrics.py`:
  - Unit: `test_compute_recall_at_k` — miniature trace set returns expected recall values
  - Unit: `test_compute_mrr` — reciprocal rank math matches a hand-worked example
  - Unit: `test_compute_ndcg_at_k_binary_labels` — binary labels produce the expected nDCG
  - Unit: `test_compute_ndcg_at_k_graded_labels` — graded labels produce the expected nDCG
  - Checkpoint: `uv run pytest packages/archon-search/tests/eval/test_metrics.py -k "recall or mrr or ndcg" -v`

#### Task 3.2 — Routing, reranker-lift, and latency metrics
- [ ] **File**: `packages/archon-search/archon_search/eval/metrics.py`
- **Depends on**: Task 3.1
- **Description**:
  - Implement `compute_reranker_lift`, `compute_routing_accuracy`, and `compute_latency_percentiles`.
  - `compute_routing_accuracy` measures whether the routed shortlist included the collection containing a relevant document.
  - Exclude pinned queries from routing-accuracy math and allow `None` when all queries are skipped.
  - Percentiles operate on milliseconds captured by the eval runner, not wall-clock strings.
- **Releasable**: all remaining acceptance-metric categories are computed
- **Tests (TDD)** — `packages/archon-search/tests/eval/test_metrics.py`:
  - Unit: `test_compute_reranker_lift` — lift equals post-rerank nDCG minus prere-rank nDCG
  - Unit: `test_compute_routing_accuracy_skips_pinned_queries` — pinned traces are excluded
  - Unit: `test_compute_latency_percentiles` — p50 and p95 are stable for fixed inputs
  - Checkpoint: `uv run pytest packages/archon-search/tests/eval/test_metrics.py -k "lift or routing or latency" -v`

#### Task 3.3 — Eval runner trace execution
- [ ] **File**: `packages/archon-search/archon_search/eval/runner.py`
- **Depends on**: Task 1.2, Task 2.4, Task 3.2
- **Description**:
  - Implement `async run_eval_suite(corpus_root: Path, config_path: Path) -> EvalReport`.
  - Ingest the committed corpus into a module-scoped temporary LanceDB store using deterministic mock backends.
  - Execute routing and search per query, capture routed collection names, prere-rank results, post-rerank results, and elapsed milliseconds.
  - Record notes when routing is skipped because a query is pinned or because routing is disabled by configuration.
- **Releasable**: one function produces a full in-memory eval report for the committed corpus
- **Tests (TDD)** — `packages/archon-search/tests/eval/test_runner.py`:
  - Integration: `test_eval_runner_executes_full_corpus` — one trace is produced per query
  - Integration: `test_eval_runner_records_router_shortlist` — routed shortlist names are retained
  - Integration: `test_eval_runner_records_skipped_routing_queries` — pinned queries increment skip accounting
  - Checkpoint: `uv run pytest packages/archon-search/tests/eval/test_runner.py -k "runner_executes or router_shortlist or skipped_routing" -v`

#### Task 3.4 — Threshold assertion and human-readable reporting
- [ ] **File**: `packages/archon-search/archon_search/eval/runner.py`
- **Depends on**: Task 1.4, Task 3.3
- **Description**:
  - Implement `assert_thresholds(report: EvalReport) -> None`.
  - Implement `render_report(report: EvalReport) -> str`.
  - Failure messages must include actual metric, threshold, and delta for each failing gate.
  - Report output must call out that latency measurements use mock backends.
- **Releasable**: eval results can fail CI with actionable output
- **Tests (TDD)** — `packages/archon-search/tests/eval/test_runner.py`:
  - Integration: `test_assert_thresholds_passes_on_baseline_report` — baseline report passes
  - Integration: `test_assert_thresholds_reports_metric_regressions` — failure message includes metric deltas
  - Unit: `test_render_report_mentions_mock_latency` — rendered report documents mock-backend latency context
  - Checkpoint: `uv run pytest packages/archon-search/tests/eval/test_runner.py -k "assert_thresholds or render_report" -v`

### Phase 4 — Pytest integration and CI gating
> **Releasable**: after each task; by Task 4.3 the evaluation harness is callable in local pytest and enforced in release CI

#### Task 4.1 — Eval fixtures and pytest marker wiring
- [ ] **File**: `packages/archon-search/tests/eval/conftest.py`
- **Depends on**: Task 3.3
- **Description**:
  - Add module-level fake-model injections following the existing Archon pattern from `tests/search/conftest.py`.
  - Provide module-scoped fixtures for temporary LanceDB storage and the loaded eval corpus.
  - Register `@pytest.mark.eval` in the package pytest configuration.
  - Ensure the default test selection excludes `eval`.
- **Releasable**: maintainers can run the eval slice locally without real model downloads
- **Tests (TDD)** — `packages/archon-search/tests/eval/test_pytest_integration.py`:
  - Integration: `test_eval_pytest_marker_excluded_from_default_run` — default selection excludes eval
  - Integration: `test_eval_conftest_uses_mock_backends` — fake backends are active during eval tests
  - Checkpoint: `uv run pytest packages/archon-search/tests/eval/test_pytest_integration.py -v`

#### Task 4.2 — End-to-end eval smoke test
- [ ] **File**: `packages/archon-search/tests/eval/test_eval_suite.py`
- **Depends on**: Task 4.1
- **Description**:
  - Add `@pytest.mark.eval` smoke coverage that calls `run_eval_suite(...)`, renders the report, and asserts thresholds.
  - Keep the smoke test single-entry so CI sees one obvious failure point with full report output.
  - Ensure the test failure text is rich enough to diagnose regressions without rerunning locally first.
- **Releasable**: the full harness is executable from pytest as a single maintained workflow
- **Tests (TDD)** — `packages/archon-search/tests/eval/test_eval_suite.py`:
  - E2E: `test_eval_suite_smoke` — full eval harness runs against the committed corpus and produces a report string
  - Checkpoint: `uv run pytest packages/archon-search/tests/eval/test_eval_suite.py -m eval -v`

#### Task 4.3 — Release-CI inclusion
- [ ] **File**: `packages/archon-search/.github/workflows/release.yml`
- **Depends on**: Task 4.2
- **Description**:
  - Update the extracted package’s release workflow to run the eval slice explicitly.
  - Keep fast/default CI excluding `-m eval`.
  - Make the workflow surface the rendered report in logs when thresholds fail.
- **Releasable**: eval regressions block release CI for the search package
- **Tests (TDD)** — `packages/archon-search/tests/eval/test_ci_contract.py`:
  - Integration: `test_release_ci_includes_eval_slice` — workflow references the eval marker or eval test path
  - Integration: `test_fast_ci_excludes_eval_slice` — fast workflow configuration still excludes eval
  - Checkpoint: `uv run pytest packages/archon-search/tests/eval/test_ci_contract.py -v`

### Phase 5 — Documentation and baseline hardening
> **Releasable**: after each task; by Task 5.2 the harness is documented and maintainable without oral history

#### Task 5.1 — Eval README and maintenance guide
- [ ] **File**: `packages/archon-search/tests/eval/README.md`
- **Depends on**: Task 4.2
- **Description**:
  - Document the corpus layout, query and label schema, threshold semantics, and the process for refreshing thresholds from measured baselines.
  - Explicitly state that v1 latency metrics use mock backends and should be interpreted as regression guards, not production SLAs.
  - Include the exact local command for running the eval slice.
- **Releasable**: maintainers can extend the harness without reverse-engineering the tests
- **Tests (TDD)** — `packages/archon-search/tests/eval/test_docs_contract.py`:
  - Integration: `test_eval_readme_mentions_threshold_baselines` — README documents baseline-derived thresholds
  - Integration: `test_eval_readme_mentions_mock_latency_limits` — README explains latency caveat
  - Checkpoint: `uv run pytest packages/archon-search/tests/eval/test_docs_contract.py -k "readme" -v`

#### Task 5.2 — Package and roadmap documentation updates
- [ ] **File**: `packages/archon-search/README.md`
- **Depends on**: Task 5.1
- **Description**:
  - Add an Evaluation section to the package README.
  - Update the Search architecture and FEAT-037 roadmap docs so the extracted package’s evaluation harness is documented as the sanctioned regression gate.
  - Keep the wording aligned with the actual delivered metric set and pytest commands.
- **Releasable**: the harness is visible in both package-local docs and project planning docs
- **Tests (TDD)** — `packages/archon-search/tests/eval/test_docs_contract.py`:
  - Integration: `test_package_readme_mentions_eval_command` — README includes the maintained eval command
  - Integration: `test_roadmap_docs_reference_eval_harness` — roadmap/docs mention the delivered harness
  - Checkpoint: `uv run pytest packages/archon-search/tests/eval/test_docs_contract.py -k "package_readme or roadmap_docs" -v`
