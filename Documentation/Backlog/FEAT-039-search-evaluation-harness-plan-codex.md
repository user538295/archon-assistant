**Purpose**: Add a deterministic offline evaluation harness for the extracted Search package so retrieval and Search-owned routing changes can be measured against a committed benchmark before release.
**Audience**: Maintainers of `packages/archon-search/` who change retrieval, reranking, routing, or performance-sensitive search internals.
**Status**: Draft
**Last reviewed**: 2026-05-02
**Next review**: 2026-11-02

# FEAT-039 — Search Offline Evaluation Harness v1
**Implementation status**: To Do

---

## Background

FEAT-037 identified evaluation discipline and a later data-collection loop as prerequisites for making Search a standalone product. This FEAT-039 plan intentionally covers the offline evaluation harness v1 only. Live query logging, user feedback capture, privacy policy, and online experimentation remain a required follow-up feature so FEAT-037 item 4 is not considered fully complete after FEAT-039 alone.

The current verified codebase has strong unit and integration coverage under `tests/search/`, but it does not expose a first-class retrieval benchmark loop with committed relevance labels, metric computation, or CI gating.

This feature must land after FEAT-038, because the brief intentionally places all new code inside the extracted package boundary: `packages/archon-search/archon_search/` and `packages/archon-search/tests/`. The current Archon implementation is still the source of truth for how the future package should behave: hybrid retrieval is implemented in `archon/search/store.py`, reranking in `archon/search/reranker.py`, routing in `archon/search/router.py`, and end-to-end search orchestration in `archon/search/pipeline.py`.

The practical gap is simple: maintainers can change RRF weighting, reranker behavior, routing thresholds, or search latency characteristics today without a reproducible signal showing whether the change helped or regressed quality. This plan closes that gap with a synthetic offline corpus, query-sensitive deterministic eval backends, metric computation, and pytest-based release gating.

## Post-FEAT-038 boundary assumptions

This plan is written for the package layout that FEAT-038 creates. At the time this plan was reviewed, the current checkout still has Search under `archon/search/` and has no root `packages/` or `.github/` directory. Therefore:

- `packages/archon-search/` means the package root created by FEAT-038, not a path that exists before FEAT-038 lands.
- CI and release-gate tasks must update the actual post-FEAT-038 package release entrypoint. If FEAT-038 extracts Search into its own repository, that is the package repository's root workflow. If FEAT-038 keeps it in this monorepo, that is the repository-level workflow or release script that owns `packages/archon-search/`.
- The offline harness must not change Archon's daemon runtime path, Telegram behavior, or public MCP/REST response contract.
- Search-owned routing metrics are conditional. FEAT-039 may gate `routing_shortlist_accuracy` only if FEAT-038 defines a deterministic Search-owned routing contract that can be exercised without Archon's decomposer. Archon-specific decomposer prompting, pinned collection resolution, and `SearchContextProvider` behavior are outside v1 unless FEAT-038 moves that exact behavior into the Search service contract.
- Before FEAT-039 implementation starts, FEAT-038 must provide or explicitly document these minimum prerequisites: `packages/archon-search/` package root, `archon_search` import path, stable public `SearchResult` response contract, package-local pytest configuration, package release entrypoint, dependency extras used by Search/eval tests, and whether routing is a Search-owned contract. At review time no tracked FEAT-038 backlog artifact exists in `Documentation/`; if one is added later, link it here and reconcile this inline prerequisite list with that artifact.
- FEAT-039 starts with a Phase 0 gate. No implementation task may begin until the accepted FEAT-038 contract is linked from this plan or copied into the Phase 0 checklist with exact package paths, release command, pytest config, dependency install command, and routing ownership decision.

## Goal

After FEAT-039 is complete, a maintainer can run an evaluation-only pytest slice inside `packages/archon-search/` and receive a stable report covering recall@k, MRR, nDCG@k, reranker lift, latency percentiles, and Search routing shortlist accuracy when FEAT-038 makes routing a Search-owned contract. If routing is not Search-owned, the report must explicitly skip routing metrics instead of fabricating them. The same suite runs in release CI, enforces quality floors and optional latency ceilings, and fails visibly when a ranking or eligible routing change regresses the committed benchmark.

---

## Scope

### In Scope
- Deterministic evaluation-only models, fixtures, and pytest infrastructure inside `packages/archon-search/`
- Query-sensitive deterministic eval backends that are corpus-aware but label-blind; the existing all-zero embedding and uniform-reranker test fakes are not sufficient for benchmark signal
- Synthetic retrieval benchmark corpus with 50-100 committed documents across 2-3 retrieval collections; if routing is gated, add a separate routing fixture with enough collections to exercise the shortlist tier
- Manifest-backed document, query, and relevance-label fixture format, including optional graded judgments
- Document-level metric computation with explicit chunk-result deduplication
- Eval-only score and rank decomposition so pre-rerank and post-rerank metrics are computable without leaking private trace fields into public result APIs
- Metric computation for recall@1/3/5, MRR, nDCG@5/10, report-only reranker lift, conditional Search routing shortlist accuracy, and latency percentiles
- Committed eval runtime config that sets retrieval depth high enough for all metrics, including `nDCG@10`
- Threshold-based assertions for quality floors, optional latency ceiling checks, and structured per-run output for CI diagnostics
- Documentation for how maintainers update the corpus, labels, required baseline reports, and thresholds

### Out of Scope
- Live query logging from real user traffic
- Relevance feedback capture and privacy policy for online data collection
- Automatic relevance-label generation with LLMs
- A standalone `archon-search eval` CLI command
- Public REST endpoints for evaluation runs
- Multi-tenant or authenticated evaluation services
- Fast-suite CI gating; eval remains a dedicated slower test slice. This is an explicit v1 downgrade from the brief's broader PR-gating language unless FEAT-038 defines a Search-specific path-filtered PR workflow that can run the same eval slice without slowing unrelated Archon changes.

---

## Acceptance criteria
- [ ] `packages/archon-search/tests/` contains an `eval` pytest slice that runs entirely with deterministic eval-specific, label-blind embedder and reranker backends
- [ ] Phase 0 links the accepted FEAT-038 Search package contract and confirms exact package root, import path, release entrypoint, dependency install command, pytest config, and routing ownership before code implementation begins
- [ ] The committed fixture corpus contains 50-100 manifest-listed documents split across at least 2 distinct retrieval collections and 25-30 benchmark queries with explicit relevance labels
- [ ] If routing is gated, a committed routing fixture has more routable collections than `routing_shortlist_size` so it exercises the Search-owned shortlist path rather than only the search-all tier
- [ ] Eval runtime config is committed separately from thresholds and uses eval-specific depth names: `candidate_depth`, `return_depth`, and `metric_depth`; `metric_depth >= 10`, `return_depth >= metric_depth`, and `candidate_depth > return_depth`
- [ ] Fixture labels use stable fixture document IDs, and the eval runner maps runtime path-derived document IDs back to fixture IDs before computing metrics
- [ ] Chunk-level search results are deduplicated to document-level rankings before computing document-level recall, MRR, and nDCG
- [ ] After chunk deduplication, each scored query has at least 10 unique document IDs when the corpus has enough candidate documents; duplicate top chunks cannot silently make `nDCG@10` a shallower metric
- [ ] Eval trace output exposes vector rank, vector score, FTS rank, FTS score, fused/RRF score, and reranker score without adding those fields to the normal public `SearchResult` response contract
- [ ] The harness measures `recall@1`, `recall@3`, `recall@5`, `MRR`, `nDCG@5`, `nDCG@10`, report-only `reranker lift`, conditional `routing shortlist accuracy`, `latency p50`, and `latency p95`
- [ ] Routing-shortlist assertions are skipped with an explicit note unless a Search-owned routing contract exists; when gated, bypassed, disabled, Tier 1 search-all, and Tier 2 all-routable-fit routing states are reported separately from Tier 3 shortlist traces and cannot satisfy the gated shortlist metric by themselves
- [ ] Report-only calibration can run without thresholds; release gating is enabled only after a machine-readable baseline, a rendered baseline report, and `thresholds.toml` are committed together
- [ ] Quality thresholds are loaded from committed config, compared against actual run output, and cause pytest failures when any gated quality metric drops below its floor
- [ ] Latency percentiles are reported every run; latency ceilings are report-only by default in v1 unless an explicit committed ceiling enables gating
- [ ] A rendered baseline report and machine-readable baseline metadata are committed before release gating is enabled; quality floors must be derived from or intentionally below that baseline with rationale
- [ ] Pytest failure output shows metric deltas against both the saved baseline and configured floor/ceiling, using floor semantics for quality metrics and ceiling semantics for gated latency metrics
- [ ] Slow eval tests are excluded from the fast/default test slice and included in release CI; pure fixture and metric unit tests stay in the default package test suite unless they run the full corpus
- [ ] Release CI installs the extracted package with Search/eval dependencies, runs under the package pytest config, enforces `archon_search` coverage for unmarked eval unit code, and fails if zero eval tests are collected or all selected eval tests are skipped or xfailed
- [ ] The full eval suite is deterministic across two fresh temporary stores for quality metrics and result rankings; latency is excluded from byte-for-byte determinism checks
- [ ] Harness documentation explains fixture structure, label semantics, baseline report storage, and the rule for updating thresholds from measured baselines rather than invented targets

---

## What does NOT change
- The main Archon daemon does not gain runtime evaluation behavior
- Production search requests do not execute benchmark logic
- Search does not require real model downloads in tests
- Public APIs, auth, namespace isolation, and job control remain FEAT-038 and later concerns
- Normal `SearchResult` public response payloads do not gain eval-only score provenance fields
- This feature does not introduce online experimentation or live traffic capture

---

## Known limitations / accepted trade-offs
- The first benchmark corpus is synthetic, so it measures regression consistency rather than full real-world relevance coverage
- In-process latency numbers reflect deterministic eval backends and local LanceDB behavior, not production model latency
- Latency metrics in v1 are primarily trend/reporting signals; they should not be treated as production SLAs
- Thresholds in v1 are conservative and baseline-derived; they are meant to detect regressions, not certify world-class quality
- Search routing shortlist accuracy is measured only when a Search-owned routing contract exists. An eligible query succeeds when the candidate shortlist intersects at least one gold collection derived from labels. This is not full Archon prompt-selection correctness or ranking precision across every collection.
- FEAT-039 does not close FEAT-037's data-collection-loop requirement; it creates the offline gate that later data collection can feed
- The initial harness is pytest-only; maintainers who want ad hoc local eval against custom corpora will need a later CLI feature
- v1 release gating is release-oriented. Path-filtered PR eval may be added by FEAT-039 only if the accepted FEAT-038 package workflow provides a concrete PR CI entrypoint; otherwise the remaining online/PR feedback loop stays a follow-up.

---

## Metric semantics

- All ranking metrics are computed over per-query rankings of unique fixture document IDs after chunk-result deduplication. When several chunks from the same document appear, the first-ranked chunk defines that document's rank.
- Aggregate quality metrics are macro-averages across benchmark queries; each query contributes one value regardless of how many relevant documents it has. Queries without relevance labels are invalid by fixture contract.
- Labels with `grade > 0` are relevant. Labels with `grade = 0` are explicit non-relevant judgments: they may appear in `labels.jsonl` for nDCG/IDCG bookkeeping, but they do not count as relevant for recall or MRR.
- `recall@k` is `relevant unique documents in the first k unique ranked documents / total relevant documents for that query`, then macro-averaged.
- `MRR` uses the reciprocal rank of the first relevant unique document after deduplication; queries with no relevant document in the ranking contribute `0`.
- `nDCG@k` supports binary and graded labels. Use gain `2^grade - 1`, discount `log2(rank + 1)`, and IDCG from all labels for that query sorted by grade. Missing result positions contribute `0`.
- The eval runner must either over-fetch raw chunk candidates or fail with an under-depth diagnostic so `nDCG@10` is computed against 10 unique document slots when the corpus has enough documents. `metric_depth >= 10` is the required unique-document depth, not the public Search `top_k_return` chunk count.
- `reranker_lift` is report-only in v1 and equals post-rerank `nDCG@10 - pre-rerank nDCG@10` over the same query set and candidate pool.
- Latency percentiles use the nearest-rank method on sorted per-query millisecond values: index `ceil(percentile / 100 * n) - 1`, clamped to the sample range. A single-sample run returns that sample for both p50 and p95.
- `routing_shortlist_accuracy` is computed only for Search-owned Tier 3 candidate-shortlist traces. Tier 1 search-all, Tier 2 all-routable-fit, disabled routing, and bypassed queries are reported separately and do not satisfy the gated shortlist metric.

---

## Architecture

- New modules under `packages/archon-search/archon_search/eval/`:
  - `fixtures.py`
    - `EvalDocument(doc_id: str, collection: str, relative_path: str, text: str, metadata: dict[str, str] | None = None)` dataclass
    - `EvalQuery(query_id: str, text: str, collection: str | None = None, routing_bypass: bool = False)` dataclass
    - `RelevanceLabel(query_id: str, doc_id: str, grade: int = 1)` dataclass
    - `EvalCorpus(documents: list[EvalDocument], queries: list[EvalQuery], labels: list[RelevanceLabel])` dataclass
    - `load_eval_corpus(root: Path) -> EvalCorpus`
    - `build_doc_collection_map(corpus: EvalCorpus) -> dict[str, str]`
  - `types.py`
    - `SearchScoreBreakdown(vector_rank: int | None, vector_score: float | None, fts_rank: int | None, fts_score: float | None, rrf_score: float, reranker_score: float | None)` dataclass
    - `EvalSearchResult(doc_id: str, runtime_doc_id: str, chunk_id: str, text: str, score: float, source_path: str, collection: str, scores: SearchScoreBreakdown)` dataclass
    - `SearchRoutingTrace(tier: str, routable_collections: list[str], candidate_shortlist_collections: list[str], skipped: bool, skip_reason: str | None = None, confidence_gate_reason: str | None = None)` dataclass with production-observable routing fields only
    - `RoutingEvalJudgment(gold_collections: list[str], eligible_for_shortlist_metric: bool, skip_reason: str | None = None)` dataclass with eval-derived gold-label fields only
    - `QueryEvalTrace(query: EvalQuery, pre_rerank: list[EvalSearchResult], post_rerank: list[EvalSearchResult], routing: SearchRoutingTrace | None, routing_judgment: RoutingEvalJudgment | None, latency_ms: float)` dataclass
    - `EvalMetrics(recall_at_1: float, recall_at_3: float, recall_at_5: float, mrr: float, ndcg_at_5: float, ndcg_at_10: float, reranker_lift: float, routing_shortlist_accuracy: float | None, latency_p50_ms: float, latency_p95_ms: float)` dataclass
  - `metrics.py`
    - `compute_recall_at_k(traces: list[QueryEvalTrace], labels: dict[str, dict[str, int]], k: int) -> float`
    - `compute_mrr(traces: list[QueryEvalTrace], labels: dict[str, dict[str, int]]) -> float`
    - `compute_ndcg_at_k(traces: list[QueryEvalTrace], labels: dict[str, dict[str, int]], k: int, *, use_prerank: bool = False) -> float`
    - `compute_reranker_lift(traces: list[QueryEvalTrace], labels: dict[str, dict[str, int]], k: int = 10) -> float`
    - `compute_routing_shortlist_accuracy(traces: list[QueryEvalTrace]) -> float | None`
    - `compute_latency_percentiles(latencies_ms: list[float]) -> tuple[float, float]`
  - `runner.py`
    - `EvalQualityFloors(recall_at_1: float, recall_at_3: float, recall_at_5: float, mrr: float, ndcg_at_5: float, ndcg_at_10: float, routing_shortlist_accuracy: float | None)` dataclass
    - `EvalLatencyConfig(gating_enabled: bool = False, p50_ceiling_ms: float | None = None, p95_ceiling_ms: float | None = None)` dataclass
    - `EvalThresholds(quality: EvalQualityFloors, latency: EvalLatencyConfig, max_floor_drop_without_waiver: float = 0.05)` dataclass
    - `EvalRuntimeConfig(candidate_depth: int, return_depth: int, metric_depth: int, routing_contract_enabled: bool, routing_min_routable_collections: int, routing_shortlist_size: int)` dataclass, where `metric_depth` is the required post-dedupe unique-document metric depth
    - `EvalBaseline(metrics: EvalMetrics, corpus_hash: str, labels_hash: str, runtime_config_hash: str, thresholds_hash: str | None, command: str, package_commit: str, dependency_versions: dict[str, str])` dataclass
    - `EvalReport(metrics: EvalMetrics, thresholds: EvalThresholds | None, baseline: EvalBaseline | None, query_count: int, document_count: int, skipped_routing_queries: int, notes: list[str])` dataclass
    - `load_runtime_config(config_path: Path) -> EvalRuntimeConfig`
    - `load_thresholds(config_path: Path) -> EvalThresholds`
    - `load_baseline(baseline_path: Path) -> EvalBaseline`
    - `async run_eval_suite(corpus_root: Path, runtime_config_path: Path, thresholds_path: Path | None = None, baseline_path: Path | None = None) -> EvalReport`
    - `assert_thresholds(report: EvalReport) -> None`
    - `render_report(report: EvalReport) -> str`
  - `backends.py`
    - deterministic eval-only embedder and reranker backends that are query-sensitive, corpus-aware, and label-blind
    - backends may use query text, document text, and non-gold metadata only; they must not read `labels.jsonl`, query IDs, fixture doc IDs as relevance hints, or gold collections
  - `_tracing.py`
    - private eval-only trace collection that copies or converts production results before reranking so pre-rerank fused scores are not mutated by reranker execution
- Search package changes outside `eval/` are allowed only for a private diagnostic trace contract used by eval and debugging. They are not public API additions and must stay out of normal response payloads:
  - `packages/archon-search/archon_search/_types.py`
    - keep the normal public `SearchResult` contract stable; do not add eval-only score fields to the public response dataclass
    - if a shared internal candidate type is needed, keep it private/internal and convert to public `SearchResult` at the package boundary
  - `packages/archon-search/archon_search/store.py`
    - `async hybrid_search(...) -> list[SearchResult]` keeps its public behavior
    - add a private internal trace helper, for example `_hybrid_search_with_trace(collection: str, query_vector: list[float], query_text: str, candidate_depth: int) -> list[ScoredSearchCandidate]`, that exposes vector rank/score, FTS rank/score, and final RRF score for eval use only
    - sorting must use deterministic tie-breaking, for example score descending then stable `doc_id`/`chunk_id`
  - `packages/archon-search/archon_search/reranker.py`
    - normal `async rerank(...) -> list[SearchResult]` keeps public behavior
    - add a private eval trace path, or make reranking non-mutating in the extracted package, so pre-rerank fused scores and post-rerank scores can both be observed without shared-object mutation
    - sorting must use deterministic tie-breaking, for example reranker score descending then stable `doc_id`/`chunk_id`
  - `packages/archon-search/archon_search/pipeline.py`
    - do not add eval behavior to the normal daemon-facing search flow
    - expose only an internal trace hook if the eval collector cannot reuse store/reranker components directly
  - `packages/archon-search/archon_search/router.py`
    - expose a structured `SearchRoutingTrace` only when routing is part of the standalone package contract
    - trace must include only production-observable routing data: tier, routable collections, candidate shortlist collections, skipped flag, skip reason, and confidence-gate reason
    - router trace APIs must not accept or expose labels, fixture IDs, or gold collections
    - the eval runner derives `gold_collections` from `labels.jsonl` and `doc_id -> collection` mapping in `RoutingEvalJudgment`, not in the production routing trace
    - routing-shortlist accuracy is computed against the Search-owned `candidate_shortlist_collections`, not decomposer-selected collections, pinned collections, or the actual searched collection set
    - do not claim this metric validates Archon's decomposer-selected collection behavior unless that behavior has moved into the package contract
- Fixture files under `packages/archon-search/tests/eval/`:
  - `documents.jsonl` manifest with `doc_id`, `collection`, and `relative_path`
  - `corpus/` directory with per-collection fixture documents
  - `queries.jsonl`
  - `labels.jsonl`
  - `runtime.toml`
  - `thresholds.toml` added in Task 4.3 before release gating, not required for report-only calibration
- Required baseline artifact under `packages/archon-search/tests/eval/baselines/`:
  - first accepted rendered report (`baseline.md`) and machine-readable metadata (`baseline.json`), committed alongside threshold values before release gating is enabled
  - `baseline.json` records metrics, corpus hash, labels hash, runtime config hash, thresholds hash when thresholds exist, eval command, package commit, and dependency versions
- Data flow:
  - fixture loader reads `documents.jsonl`, corpus files, queries, and labels
  - eval fixture ingests corpus into a module-scoped temporary LanceDB store with deterministic eval backends
  - runner maps path-derived runtime document IDs back to stable fixture document IDs before metrics are computed
  - runner executes each query through Search-owned routing only when the package-owned routing contract exists; otherwise routing metrics are `None` and explicitly skipped
  - runner executes each query through private eval trace paths using `candidate_depth` raw chunk retrieval, `return_depth` post-rerank chunk output, and `metric_depth` post-dedupe unique-document scoring
  - runner deduplicates chunk results to unique fixture document IDs and fails with an under-depth diagnostic when `metric_depth` unique documents cannot be produced from an otherwise sufficient corpus
  - metric functions deduplicate chunk results to document rankings before document-level scoring
  - metrics module computes aggregate scores
  - threshold assertion converts regressions into pytest failures only when thresholds are loaded; report-only calibration renders metrics and baseline metadata without asserting floors
- New package pytest config:
  - package `pyproject.toml` or equivalent sets `testpaths = ["tests"]` under `packages/archon-search/`
  - coverage targets `archon_search`, not root `archon`
  - marker registration includes `eval`
  - default package addopts exclude slow eval tests, for example `-m "not live and not eval"`
- New pytest marker:
  - `eval`: slow deterministic full-corpus benchmark slice excluded from default runs
  - pure fixture-loader and metric unit tests remain unmarked unless they run the full corpus
- Schema for `packages/archon-search/tests/eval/runtime.toml`:
  - `[search].candidate_depth` is an integer raw chunk candidate depth and must be greater than `return_depth`
  - `[search].return_depth` is an integer raw post-rerank chunk depth and must be at least `metric_depth`
  - `[search].metric_depth` is an integer post-dedupe unique-document metric depth and must be at least `10`
  - `[routing].contract_enabled` is a boolean and means a Search-owned routing contract exists; it is not just an eval toggle
  - `[routing].min_routable_collections` is an integer
  - `[routing].shortlist_size` is an integer
- Example `runtime.toml`:
  ```toml
  [search]
  candidate_depth = 40
  return_depth = 20
  metric_depth = 10

  [routing]
  contract_enabled = false
  min_routable_collections = 0
  shortlist_size = 8
  ```
- Schema for `packages/archon-search/tests/eval/thresholds.toml`:
  - `[latency].gating_enabled` is an optional boolean; default `false` when omitted
  - `[latency].p50_ceiling_ms` is an optional float; omit when not gated
  - `[latency].p95_ceiling_ms` is an optional float; omit when not gated
  - `[quality_floors].recall_at_1` is a float
  - `[quality_floors].recall_at_3` is a float
  - `[quality_floors].recall_at_5` is a float
  - `[quality_floors].mrr` is a float
  - `[quality_floors].ndcg_at_5` is a float
  - `[quality_floors].ndcg_at_10` is a float
  - `[quality_floors].routing_shortlist_accuracy` is an optional float; omit when routing is not gated
  - `[policy].max_floor_drop_without_waiver` is an optional float, default `0.05`; floor reductions larger than this require a reviewed waiver or issue ID in baseline metadata
- Example `thresholds.toml` (illustrative only; committed values must come from the first accepted measured baseline):
  ```toml
  [quality_floors]
  recall_at_1 = 0.60
  recall_at_3 = 0.75
  recall_at_5 = 0.85
  mrr = 0.70
  ndcg_at_5 = 0.75
  ndcg_at_10 = 0.80

  [latency]
  gating_enabled = false

  [policy]
  max_floor_drop_without_waiver = 0.05
  ```
- Default policy:
  - quality thresholds are copied from the first accepted measured baseline after FEAT-038, not guessed in advance
  - the first eval run is calibration-only and may omit `thresholds.toml`; release gating starts only after `baseline.json`, `baseline.md`, and `thresholds.toml` are committed together
  - reranker lift is report-only in v1; final post-rerank quality metrics are the primary gates
  - latency is report-only unless `[latency].gating_enabled = true`; when enabled, p50/p95 are maximum ceilings, not minimum floors
  - threshold lowering must include a rationale tied to a corpus change or intentional algorithm trade-off, and the previous rendered report must remain reviewable in history
  - floor reductions larger than `[policy].max_floor_drop_without_waiver` fail unless baseline metadata names a reviewed waiver or issue ID
  - a configured routing floor plus `routing_shortlist_accuracy = None` fails with an actionable configuration message; an omitted routing floor plus `None` metric skips routing gating with a note

---

## Tests

- **`test_load_eval_corpus_reads_documents_queries_and_labels`** (unit): fixture loader returns the expected document, query, and label counts
- **`test_load_eval_corpus_rejects_unknown_label_doc_id`** (unit): labels referencing missing documents fail fast
- **`test_load_eval_corpus_rejects_duplicate_query_ids`** (unit): duplicate query identifiers are rejected
- **`test_load_eval_corpus_rejects_duplicate_document_ids`** (unit): manifest document IDs must be unique
- **`test_load_eval_corpus_rejects_missing_manifest_file`** (unit): manifest rows pointing at absent corpus files fail fast
- **`test_runtime_doc_ids_map_to_fixture_doc_ids`** (unit): path-derived runtime IDs are converted back to stable fixture IDs before metrics run
- **`test_load_runtime_config_rejects_metric_depth_below_metric_k`** (unit): eval config cannot make `nDCG@10` uncomputable
- **`test_compute_recall_at_k`** (unit): recall is computed correctly for known miniature traces
- **`test_compute_mrr`** (unit): reciprocal-rank calculation matches expected values
- **`test_recall_and_mrr_ignore_zero_grade_labels`** (unit): explicit `grade = 0` labels do not count as relevant hits
- **`test_compute_ndcg_at_k_binary_labels`** (unit): binary-label nDCG math is correct
- **`test_compute_ndcg_at_k_graded_labels`** (unit): graded-label nDCG math is correct
- **`test_metrics_macro_average_multi_relevant_queries`** (unit): multi-label queries use the documented per-query macro-average semantics
- **`test_metrics_dedupe_chunks_to_document_rankings`** (unit): multiple chunks from one relevant document do not inflate document-level metrics
- **`test_metrics_reject_under_depth_after_chunk_dedupe`** (unit): duplicate chunks cannot make `nDCG@10` silently score fewer than 10 unique documents
- **`test_compute_reranker_lift`** (unit): post-rerank nDCG minus pre-rerank nDCG is computed correctly
- **`test_compute_routing_shortlist_accuracy_uses_gold_collections_without_retrieval_hit`** (unit): routing shortlist accuracy can be scored even when the relevant document was not retrieved
- **`test_compute_routing_shortlist_accuracy_skips_bypassed_queries`** (unit): bypassed queries are excluded from routing shortlist accuracy
- **`test_compute_routing_shortlist_accuracy_skips_non_tier3_traces`** (unit): Tier 1 and Tier 2 routing traces cannot satisfy the shortlist gate
- **`test_compute_latency_percentiles`** (unit): percentile math returns stable p50 and p95 values
- **`test_compute_latency_percentiles_nearest_rank_small_n`** (unit): small samples use the documented nearest-rank percentile method
- **`test_eval_backend_ranking_changes_with_query_terms`** (unit): deterministic eval backends produce query-sensitive ranking signal
- **`test_eval_backends_do_not_receive_labels_query_ids_doc_ids_or_gold_ids`** (unit): deterministic backends are label-blind and cannot score from benchmark identifiers
- **`test_hybrid_search_trace_exposes_score_breakdown`** (integration): private eval trace returns vector rank, vector score, FTS rank, FTS score, and RRF fields needed by eval
- **`test_rerank_trace_preserves_prerank_scores_and_adds_reranker_score`** (integration): reranking does not discard fused-score provenance or mutate pre-rerank trace objects
- **`test_eval_trace_returns_pre_and_post_rerank_results`** (integration): eval trace path returns both result lists in a single query run
- **`test_eval_runner_executes_full_corpus`** (integration): runner ingests corpus and produces one trace per query
- **`test_eval_suite_is_deterministic_except_latency`** (e2e): two fresh-store runs produce identical quality metrics and rankings
- **`test_eval_runner_records_candidate_shortlist_when_routing_contract_exists`** (integration): Search-owned candidate shortlist names are captured for routing metrics when a routing contract exists
- **`test_eval_runner_skips_routing_metric_without_search_owned_routing_contract`** (integration): routing metrics are explicit opt-in
- **`test_eval_runner_rejects_collectionless_query_without_routing_contract`** (integration): `collection=None` cannot be executed by using labels to choose a collection
- **`test_run_eval_suite_report_only_without_thresholds`** (integration): calibration runs can render reports before thresholds exist
- **`test_assert_thresholds_requires_thresholds_for_gating`** (integration): release gating fails clearly when thresholds are absent
- **`test_assert_thresholds_fails_when_required_metric_is_none`** (integration): configured floors cannot silently pass with missing metrics
- **`test_assert_thresholds_reports_quality_floor_regressions`** (integration): failure message includes metric deltas against baseline and failing quality floors
- **`test_assert_thresholds_reports_latency_ceiling_regressions_when_enabled`** (integration): latency checks use ceiling semantics only when gating is enabled
- **`test_eval_pytest_marker_excluded_from_default_run`** (integration): default test selection excludes `eval`
- **`test_baseline_metadata_hashes_match_fixture_inputs`** (integration): baseline metadata matches corpus, labels, runtime config, thresholds, command, commit, and dependency versions
- **`test_eval_suite_report_only_smoke`** (e2e): full eval slice can render a calibration report before thresholds exist
- **`test_eval_suite_gated_smoke`** (e2e): full eval slice asserts thresholds after baseline and thresholds exist
- **`test_release_gate_includes_eval_slice`** (integration): the actual executable post-FEAT-038 release gate runs the eval slice
- **`test_release_gate_fails_when_eval_collection_is_empty_or_skipped`** (integration): release gate cannot pass when eval tests are uncollected, skipped, or xfailed

---

## Documentation update
- [ ] `Documentation/Backlog/FEAT-037-search-world-class-roadmap.md`, section `Priority 0: Product Separation / 4. Build an evaluation harness and data-collection loop`, path: `Documentation/Backlog/FEAT-037-search-world-class-roadmap.md`
- [ ] `Documentation/Architecture/180_search_architecture.md`, section `Search Architecture`, path: `Documentation/Architecture/180_search_architecture.md`
- [ ] `packages/archon-search/README.md`, section `Evaluation`, path: `packages/archon-search/README.md`
- [ ] `packages/archon-search/tests/eval/README.md`, section `Fixture format and threshold maintenance`, path: `packages/archon-search/tests/eval/README.md`

---

## Task breakdown

### Phase 0 — FEAT-038 prerequisite gate
> **Releasable**: when Task 0.2 is complete; FEAT-039 has a verified package boundary and executable release/test context before implementation starts

#### Task 0.1 — Link accepted Search package contract
- [ ] **File**: `Documentation/Backlog/FEAT-039-search-evaluation-harness-plan-codex.md`
- **Depends on**: FEAT-038 accepted plan or implementation artifact
- **Description**:
  - Link the accepted FEAT-038 contract if it exists as a tracked document; otherwise copy the exact accepted contract summary into this Phase 0 section before implementation starts.
  - Verify the package root, import path, package-local pytest configuration, package release entrypoint, Search/eval dependency install command, public `SearchResult` contract, and routing ownership decision.
  - If routing is not a Search-owned contract, set `[routing].contract_enabled = false` in the committed eval runtime config and omit the routing floor from thresholds.
  - If FEAT-038 changes any package path or release entrypoint assumed by this plan, update the rest of FEAT-039 before starting Task 1.1.
- **Releasable**: FEAT-039 no longer depends on an implicit future package shape
- **Tests (TDD)** — `packages/archon-search/tests/eval/test_phase0_contract.py` after extraction:
  - Integration: `test_phase0_contract_links_existing_feat_038_artifact_or_inline_contract` — the implementation plan names the accepted contract source
  - Integration: `test_phase0_contract_matches_package_paths` — documented package root and import path match the extracted package
  - Integration: `test_phase0_contract_records_routing_ownership` — routing metrics cannot be enabled without a Search-owned routing contract

#### Task 0.2 — Verify executable package test and release context
- [ ] **Files**: accepted post-FEAT-038 release entrypoint and package pytest config
- **Depends on**: Task 0.1
- **Description**:
  - Confirm the command that installs Search/eval dependencies in CI.
  - Confirm the command that runs tests under package pytest config, either by `cd packages/archon-search` or by passing the package config explicitly with `pytest -c packages/archon-search/pyproject.toml`.
  - Confirm the release entrypoint that can fail before tag, publish, or release creation.
  - Confirm whether a Search-specific path-filtered PR workflow exists. If not, document v1 as release-gated only.
- **Releasable**: all later CI-gate tasks target executable files, not assumed paths
- **Tests (TDD)** — `packages/archon-search/tests/eval/test_phase0_contract.py` after extraction:
  - Integration: `test_phase0_contract_names_dependency_install_command` — Search/eval dependencies are not assumed
  - Integration: `test_phase0_contract_names_package_pytest_config_command` — release tests cannot accidentally use root Archon pytest config
  - Integration: `test_phase0_contract_names_release_entrypoint` — release gate target is executable

### Phase 1 — Eval domain model and fixture contract
> **Releasable**: when Task 1.5 is complete; fixture files and runtime config can be loaded and validated end-to-end, thresholds have a schema, but no search execution happens yet

#### Task 1.1 — Eval fixture dataclasses
- [ ] **File**: `packages/archon-search/archon_search/eval/fixtures.py`
- **Depends on**: nothing
- **Description**:
  - Add `EvalDocument`, `EvalQuery`, `RelevanceLabel`, and `EvalCorpus` dataclasses with the signatures listed in the Architecture section.
  - `EvalDocument.doc_id` is the stable fixture ID used by labels; `relative_path` points to the file under `corpus/`.
  - `EvalQuery.collection` is optional only for routed queries when `runtime.toml` sets `routing.contract_enabled = true`; non-routed eval queries must name an explicit retrieval collection.
  - The runner must never use `labels.jsonl`, `gold_collections`, or relevant fixture documents to choose the collection searched for a query.
  - `RelevanceLabel.grade` defaults to `1`; reject grades `< 0`.
  - Keep these dataclasses free of runtime search dependencies so metric tests can construct them without LanceDB.
- **Releasable**: eval fixtures have a canonical in-package schema
- **Tests (TDD)** — `packages/archon-search/tests/eval/test_fixtures.py`:
  - Unit: `test_relevance_label_default_grade` — omitted grade defaults to `1`
  - Unit: `test_relevance_label_rejects_negative_grade` — invalid grade raises `ValueError`
  - Unit: `test_eval_document_requires_relative_path` — fixture documents carry the manifest path needed for ID mapping
  - Unit: `test_eval_query_supports_optional_collection` — routed queries allow `collection=None`
  - Unit: `test_eval_query_collection_none_is_routing_only` — collectionless queries are documented as invalid for non-routed execution
  - Checkpoint: `cd packages/archon-search && uv run pytest --no-cov tests/eval/test_fixtures.py -k "grade or collection" -v`

#### Task 1.2 — Fixture loader and file-format parser
- [ ] **File**: `packages/archon-search/archon_search/eval/fixtures.py`
- **Depends on**: Task 1.1
- **Description**:
  - Implement `load_eval_corpus(root: Path) -> EvalCorpus`.
  - Read `documents.jsonl`; each row must contain `doc_id`, `collection`, and `relative_path`.
  - Read document text from `root / "corpus" / relative_path`; do not infer stable document IDs from temporary filesystem paths.
  - Read `queries.jsonl` and `labels.jsonl`.
  - Accept binary or graded labels; normalize missing grade to `1`.
  - Fail fast on malformed JSONL, duplicate document IDs, duplicate query IDs, duplicate `relative_path` values, missing manifest files, orphan corpus files, invalid collection names, absolute paths, `..` path traversal, unknown `doc_id`, or labels for unknown queries.
  - Add a helper that maps runtime `source_path` / path-derived document IDs back to stable fixture `doc_id` values before metrics run.
- **Releasable**: a committed corpus can be loaded into validated Python objects
- **Tests (TDD)** — `packages/archon-search/tests/eval/test_fixtures.py`:
  - Unit: `test_load_eval_corpus_reads_documents_queries_and_labels` — valid tree loads successfully
  - Unit: `test_load_eval_corpus_rejects_unknown_label_doc_id` — missing doc reference raises `ValueError`
  - Unit: `test_load_eval_corpus_rejects_duplicate_document_ids` — duplicate manifest IDs raise `ValueError`
  - Unit: `test_load_eval_corpus_rejects_duplicate_query_ids` — duplicate query IDs raise `ValueError`
  - Unit: `test_load_eval_corpus_rejects_unknown_query_id_in_labels` — orphan label rows raise `ValueError`
  - Unit: `test_load_eval_corpus_rejects_missing_manifest_file` — manifest rows pointing at absent corpus files raise `ValueError`
  - Unit: `test_load_eval_corpus_rejects_invalid_collection_name` — fixture collection names follow Search collection rules
  - Unit: `test_load_eval_corpus_rejects_path_escape` — absolute paths and `..` traversal are rejected
  - Unit: `test_load_eval_corpus_rejects_duplicate_relative_path` — one corpus file cannot represent multiple fixture docs
  - Unit: `test_runtime_doc_ids_map_to_fixture_doc_ids` — path-derived runtime IDs are converted back to stable fixture IDs
  - Checkpoint: `cd packages/archon-search && uv run pytest --no-cov tests/eval/test_fixtures.py -k "load_eval_corpus" -v`

#### Task 1.3 — Committed synthetic corpus and labels
- [ ] **File**: `packages/archon-search/tests/eval/documents.jsonl`
- **Depends on**: Task 1.2
- **Description**:
  - Add the committed manifest `documents.jsonl` with explicit stable `doc_id`, `collection`, and `relative_path` fields.
  - Add the committed corpus tree under `packages/archon-search/tests/eval/corpus/`.
  - Create 50-100 concise documents across 2-3 collections representing code, documentation/prose, and mixed content.
  - If `runtime.toml` sets `[routing].contract_enabled = true`, add a routing fixture corpus or routing manifest with more routable collections than `[routing].shortlist_size`.
  - Add 25-30 benchmark queries in `queries.jsonl`.
  - Add corresponding `labels.jsonl` with at least one relevant document per query.
  - Use document-level relevance labels; metric functions deduplicate chunk results by `doc_id` before scoring.
- **Releasable**: the package contains a deterministic benchmark dataset that can be versioned and reviewed
- **Tests (TDD)** — `packages/archon-search/tests/eval/test_corpus_contract.py`:
  - Unit: `test_eval_corpus_document_count_range` — corpus contains 50-100 documents
  - Unit: `test_eval_corpus_query_count_range` — corpus contains 25-30 queries
  - Unit: `test_every_query_has_relevance_labels` — no unlabeled queries
  - Unit: `test_collections_cover_multiple_domains` — at least 2 distinct collections exist
  - Unit: `test_manifest_doc_ids_are_stable_and_unique` — every labelable document has a stable manifest ID
  - Unit: `test_routing_fixture_exceeds_shortlist_size_when_routing_enabled` — gated routing fixture exercises the shortlist tier
  - Checkpoint: `cd packages/archon-search && uv run pytest --no-cov tests/eval/test_corpus_contract.py -v`

#### Task 1.4 — Threshold config contract
- [ ] **File**: `packages/archon-search/archon_search/eval/runner.py`
- **Depends on**: Task 1.2
- **Description**:
  - Add `EvalQualityFloors`, `EvalLatencyConfig`, `EvalThresholds`, and `load_thresholds(config_path: Path) -> EvalThresholds`.
  - Parse `thresholds.toml`.
  - Require quality floors under `[quality_floors]` only when `thresholds.toml` exists and gating mode is enabled; report-only calibration must be able to run before thresholds exist.
  - Omit `routing_shortlist_accuracy` when routing is intentionally not gated or the Search-owned routing contract is absent.
  - Parse optional `[latency] gating_enabled`, `p50_ceiling_ms`, and `p95_ceiling_ms`; default omitted `gating_enabled` to `false` and reject negative latency ceilings.
  - Treat latency as report-only when `[latency].gating_enabled = false`.
  - Parse `[policy].max_floor_drop_without_waiver` and default it to `0.05`.
  - Reject missing required quality keys and malformed threshold types.
- **Releasable**: eval runs can be compared against committed floor values after calibration, while first-run calibration remains possible
- **Tests (TDD)** — `packages/archon-search/tests/eval/test_runner.py`:
  - Unit: `test_load_thresholds_reads_all_metrics` — valid TOML parses into `EvalThresholds`
  - Unit: `test_load_thresholds_allows_omitted_routing_shortlist_accuracy` — omitted optional routing floor accepted
  - Unit: `test_load_thresholds_rejects_missing_metric` — incomplete config raises `ValueError`
  - Unit: `test_load_thresholds_rejects_negative_latency_ceiling` — invalid latency ceiling raises `ValueError`
  - Unit: `test_load_thresholds_defaults_latency_to_report_only` — omitted gating flag leaves latency non-gating
  - Unit: `test_load_thresholds_requires_latency_table_shape` — latency gating keys are parsed from `[latency]`, not an ambiguous TOML location
  - Unit: `test_load_thresholds_reads_floor_drop_policy` — floor-drop policy parses and defaults correctly
  - Unit: `test_run_eval_suite_report_only_without_thresholds` — calibration mode accepts missing thresholds
  - Checkpoint: `cd packages/archon-search && uv run pytest --no-cov tests/eval/test_runner.py -k "threshold" -v`

#### Task 1.5 — Eval runtime config contract
- [ ] **File**: `packages/archon-search/archon_search/eval/runner.py`
- **Depends on**: Task 1.4
- **Description**:
  - Add `EvalRuntimeConfig` and `load_runtime_config(config_path: Path) -> EvalRuntimeConfig`.
  - Parse `runtime.toml` separately from `thresholds.toml`.
  - Require `metric_depth >= 10` because `nDCG@10` is part of the committed metric set.
  - Require `return_depth >= metric_depth` so post-rerank raw chunk output can produce the required unique-document metric depth after dedupe.
  - Require `candidate_depth > return_depth` so reranking has a candidate pool to improve.
  - When `routing_contract_enabled` is true, require `routing_min_routable_collections > routing_shortlist_size` so the fixture exercises the Search-owned shortlist path, not only the search-all tier.
  - Runner validation must compare the loaded fixture's routable collection count with the runtime config; the runtime parser alone is not sufficient.
- **Releasable**: eval execution has deterministic, metric-compatible runtime settings
- **Tests (TDD)** — `packages/archon-search/tests/eval/test_runner.py`:
  - Unit: `test_load_runtime_config_reads_search_depths` — valid TOML parses into `EvalRuntimeConfig`
  - Unit: `test_load_runtime_config_rejects_metric_depth_below_metric_k` — `metric_depth < 10` raises `ValueError`
  - Unit: `test_load_runtime_config_rejects_return_depth_below_metric_depth` — return depth must support metric depth
  - Unit: `test_load_runtime_config_rejects_candidate_depth_not_greater_than_return_depth` — candidate pool must be larger than return depth
  - Unit: `test_load_runtime_config_requires_shortlist_size_for_routing` — routing-gated configs define the shortlist size used for fixture-depth checks
  - Integration: `test_runner_rejects_routing_gated_fixture_without_shortlist_depth` — loaded fixture must exceed configured shortlist size
  - Checkpoint: `cd packages/archon-search && uv run pytest --no-cov tests/eval/test_runner.py -k "runtime_config" -v`

### Phase 2 — Score decomposition in the search stack
> **Releasable**: when Task 2.5 is complete; search internals expose pre-rerank and reranker scoring detail needed by the harness, and eval backends produce deterministic ranking signal

#### Task 2.1 — Score breakdown types
- [ ] **File**: `packages/archon-search/archon_search/eval/types.py`
- **Depends on**: nothing
- **Description**:
  - Add `SearchScoreBreakdown`, `EvalSearchResult`, `SearchRoutingTrace`, `RoutingEvalJudgment`, `QueryEvalTrace`, and `EvalMetrics` dataclasses with the signatures from the Architecture section.
  - Keep `EvalSearchResult` separate from the package’s normal public `SearchResult`; eval score provenance must not widen public API payloads.
  - Keep `SearchRoutingTrace` free of labels, fixture IDs, and gold collections; it may contain only production-observable routing facts.
  - Keep `RoutingEvalJudgment` as the eval-only type that carries gold collections derived from labels.
  - `collection` must be explicit on eval results so cross-collection traces do not rely on path parsing.
  - `SearchScoreBreakdown` must include rank fields because the current hybrid algorithm is rank-based RRF.
  - `EvalSearchResult.doc_id` is the stable fixture ID after runtime ID mapping; `runtime_doc_id` preserves the underlying path-derived store ID for diagnostics.
- **Releasable**: the eval layer has stable types for traces and aggregate metrics
- **Tests (TDD)** — `packages/archon-search/tests/eval/test_types.py`:
  - Unit: `test_eval_search_result_contains_score_breakdown` — nested score fields are preserved
  - Unit: `test_score_breakdown_contains_rank_and_score_fields` — vector/FTS rank and score are both represented
  - Unit: `test_query_eval_trace_carries_routing_tier` — search-all, shortlist, bypass, and disabled routing states are distinguishable
  - Unit: `test_search_routing_trace_contains_no_gold_fields` — production routing trace cannot carry labels or gold collections
  - Unit: `test_routing_eval_judgment_carries_gold_collections` — eval judgment carries label-derived fields separately
  - Unit: `test_query_eval_trace_accepts_empty_routing_lists` — empty routed collections remain valid for skipped routing
  - Checkpoint: `cd packages/archon-search && uv run pytest --no-cov tests/eval/test_types.py -v`

#### Task 2.2 — Hybrid-search trace provenance
- [ ] **File**: `packages/archon-search/archon_search/store.py`
- **Depends on**: Task 2.1
- **Description**:
  - Keep `async hybrid_search(...) -> list[SearchResult]` compatible with normal callers.
  - Add an internal trace helper such as `_hybrid_search_with_trace(collection, query_vector, query_text, candidate_depth) -> list[ScoredSearchCandidate]` to preserve vector rank/score, FTS rank/score, and final RRF score per returned raw chunk candidate.
  - Clearly document raw-score semantics: LanceDB vector scores/distances, FTS/BM25 scores, and normalized RRF score are not interchangeable.
  - When no FTS index exists, set `fts_score` to `None` instead of fabricating zero.
  - Keep the existing fused `score` field usable by non-eval callers.
  - Avoid breaking default production call sites; private eval trace data must be accessed only through the explicit trace helper.
- **Releasable**: pre-rerank results contain vector, FTS, and fused scoring detail
- **Tests (TDD)** — `packages/archon-search/tests/test_store.py`:
  - Integration: `test_hybrid_search_keeps_public_result_contract` — normal search results do not expose eval-only score fields
  - Integration: `test_hybrid_search_trace_exposes_score_breakdown` — trace candidates include vector rank/score, FTS rank/score, and RRF values
  - Integration: `test_hybrid_search_trace_sets_fts_score_none_without_index` — no FTS index yields `None`, not an exception
  - Unit: `test_hybrid_search_trace_orders_equal_scores_deterministically` — equal RRF scores use stable secondary ordering
  - Checkpoint: `cd packages/archon-search && uv run pytest --no-cov tests/test_store.py -k "trace or public_result_contract or fts_score_none" -v`

#### Task 2.3 — Reranker trace preservation
- [ ] **File**: `packages/archon-search/archon_search/reranker.py`
- **Depends on**: Task 2.2
- **Description**:
  - Keep normal `async rerank(query: str, candidates: list[SearchResult], top_k: int) -> list[SearchResult]` compatible with normal callers.
  - Add a private eval trace path, or make reranking non-mutating in the extracted package, so reranked results preserve incoming score provenance and attach `reranker_score` without changing the pre-rerank trace list.
  - Keep final sort order based on reranker output while retaining original fused score for eval reporting.
  - When reranking is disabled or candidates are empty, keep `reranker_score=None`.
  - Add a regression test for the current mutation hazard: pre-rerank trace objects must still contain fused/RRF scores after reranking runs.
- **Releasable**: post-rerank results can be compared to pre-rerank results without losing provenance
- **Tests (TDD)** — `packages/archon-search/tests/test_reranker.py`:
  - Integration: `test_rerank_trace_preserves_prerank_scores_and_adds_reranker_score` — reranked trace results keep fused score and add reranker score
  - Unit: `test_rerank_trace_does_not_mutate_prerank_results` — pre-rerank trace objects remain unchanged after reranking
  - Unit: `test_rerank_trace_orders_equal_scores_deterministically` — equal reranker scores use stable secondary ordering
  - Unit: `test_rerank_empty_candidates_keeps_no_scores` — empty candidate list returns cleanly
  - Checkpoint: `cd packages/archon-search && uv run pytest --no-cov tests/test_reranker.py -k "reranker_score or empty_candidates" -v`

#### Task 2.4 — Eval trace execution path
- [ ] **File**: `packages/archon-search/archon_search/eval/_tracing.py`
- **Depends on**: Task 2.2, Task 2.3
- **Description**:
  - Add an eval-only trace collector, for example `async collect_search_trace(pipeline: SearchPipeline, query: str, collections: list[str] | None, candidate_depth: int, return_depth: int, metric_depth: int) -> tuple[list[EvalSearchResult], list[EvalSearchResult]]`.
  - Reuse the same embedder, store, and reranker instances as normal search.
  - Return pre-rerank and post-rerank lists from a single query execution so timing and candidate sets stay aligned.
  - Treat `candidate_depth` and `return_depth` as raw chunk depths. Deduplicate to unique documents only in eval scoring and under-depth checks.
  - Do not change the behavior of the normal `search()` method.
- **Releasable**: eval code can obtain both ranking stages from one pipeline call
- **Tests (TDD)** — `packages/archon-search/tests/test_pipeline.py`:
  - Integration: `test_eval_trace_returns_pre_and_post_rerank_results` — both result lists are returned in order
  - Integration: `test_eval_trace_matches_search_final_order` — post-rerank output matches the normal `search()` result ordering
  - Integration: `test_eval_trace_does_not_change_public_search_response` — normal `search()` output stays unchanged
  - Checkpoint: `cd packages/archon-search && uv run pytest --no-cov tests/test_pipeline.py -k "eval_trace" -v`

#### Task 2.5 — Deterministic eval backends
- [ ] **File**: `packages/archon-search/archon_search/eval/backends.py`
- **Depends on**: Task 1.3
- **Description**:
  - Add eval-only embedder and reranker backends that are deterministic, query-sensitive, corpus-aware, and label-blind.
  - The backends may use query text, document text, and non-gold metadata only.
  - The backends must not read or receive `labels.jsonl`, query IDs, stable fixture doc IDs as relevance hints, gold collections, or any query-to-relevant-document mapping.
  - Do not reuse the existing unit-test fake pattern that returns all-zero embeddings and uniform reranker scores.
  - Use a simple, deterministic scoring strategy such as token-hash embeddings plus lexical/corpus-aware reranker scores.
  - Add deterministic tie-breaking so repeated runs on the same corpus produce identical rank order.
  - Ensure at least one benchmark query has a measurable reranker lift so the lift metric is not structurally always zero.
- **Releasable**: the committed corpus can produce meaningful offline ranking signal without model downloads
- **Tests (TDD)** — `packages/archon-search/tests/eval/test_backends.py`:
  - Unit: `test_eval_embedder_changes_vector_for_query_terms` — different query terms produce different deterministic vectors
  - Unit: `test_eval_reranker_scores_from_query_and_document_text` — reranker scores are content-sensitive, not label-driven
  - Unit: `test_eval_backends_do_not_receive_labels_query_ids_doc_ids_or_gold_ids` — labels, query IDs, stable fixture doc IDs, gold collections, and relevance maps are not passed into backend construction or scoring
  - Unit: `test_eval_backends_ignore_metadata_fields_that_look_like_gold_ids` — non-gold metadata filtering prevents accidental benchmark identifier leakage
  - Unit: `test_eval_backends_have_stable_tie_breaking` — equal scores resolve deterministically
  - Integration: `test_eval_backend_produces_nonzero_reranker_lift_case` — fixture query demonstrates measurable lift
  - Checkpoint: `cd packages/archon-search && uv run pytest --no-cov tests/eval/test_backends.py -v`

### Phase 3 — Metric computation and report generation
> **Releasable**: when Task 3.4 is complete; a complete eval report can be computed from traces, but pytest wiring is still optional

#### Task 3.1 — Ranking metric functions
- [ ] **File**: `packages/archon-search/archon_search/eval/metrics.py`
- **Depends on**: Task 2.1
- **Description**:
  - Implement `compute_recall_at_k`, `compute_mrr`, and `compute_ndcg_at_k`.
  - Metrics are document-level in v1: deduplicate ranked chunk results by stable fixture `doc_id` before scoring.
  - When multiple chunks from the same document appear, keep the first-ranked chunk as that document's position.
  - `compute_ndcg_at_k` must support binary and graded relevance by consuming the label-grade map directly.
  - Follow the Metric semantics section exactly for macro-averaging, multi-relevant query recall, graded nDCG gain/discount, and missing-result behavior.
  - Use post-rerank results by default; pre-rerank mode must be opt-in for reranker-lift computation.
- **Releasable**: core ranking-quality metrics are available for any collected traces
- **Tests (TDD)** — `packages/archon-search/tests/eval/test_metrics.py`:
  - Unit: `test_compute_recall_at_k` — miniature trace set returns expected recall values
  - Unit: `test_compute_recall_at_k_multi_relevant_fractional_macro` — multi-label queries use fractional per-query recall and macro aggregation
  - Unit: `test_compute_mrr` — reciprocal rank math matches a hand-worked example
  - Unit: `test_recall_and_mrr_ignore_zero_grade_labels` — explicit `grade = 0` labels are non-relevant for recall/MRR
  - Unit: `test_compute_ndcg_at_k_binary_labels` — binary labels produce the expected nDCG
  - Unit: `test_compute_ndcg_at_k_graded_labels` — graded labels produce the expected nDCG
  - Unit: `test_compute_ndcg_at_k_uses_documented_gain_discount_and_idcg` — nDCG implementation matches the documented formula
  - Unit: `test_metric_aggregation_macro_averages_queries` — query-level values are averaged equally rather than micro-averaged by label count
  - Unit: `test_metrics_dedupe_chunks_to_document_rankings` — duplicate chunks from one document do not inflate metrics
  - Unit: `test_metrics_reject_under_depth_after_chunk_dedupe` — insufficient unique-document depth fails clearly when the corpus has enough documents
  - Checkpoint: `cd packages/archon-search && uv run pytest --no-cov tests/eval/test_metrics.py -k "recall or mrr or ndcg" -v`

#### Task 3.2 — Routing, reranker-lift, and latency metrics
- [ ] **File**: `packages/archon-search/archon_search/eval/metrics.py`
- **Depends on**: Task 3.1
- **Description**:
  - Implement `compute_reranker_lift`, `compute_routing_shortlist_accuracy`, and `compute_latency_percentiles`.
  - `compute_routing_shortlist_accuracy` measures whether the Search-owned Tier 3 candidate shortlist intersects at least one gold collection derived from labels and the `doc_id -> collection` fixture map.
  - Compute routing shortlist accuracy only when `routing.contract_enabled = true` and Phase 0 confirms routing is Search-owned; otherwise return `None` and add a skip note.
  - Store gold collections on `RoutingEvalJudgment` before metric computation so routing shortlist accuracy remains computable even when the relevant document was not retrieved.
  - Include only Tier 3 candidate-shortlist traces in gated shortlist math. Exclude Tier 1 search-all, Tier 2 all-routable-fit, disabled routing, and bypassed queries; allow `None` when no Tier 3 traces are eligible.
  - Treat reranker lift as report-only in v1; final post-rerank quality metrics carry the gates.
  - Percentiles operate on milliseconds captured by the eval runner, not wall-clock strings, and use the nearest-rank method defined in Metric semantics.
- **Releasable**: all remaining acceptance-metric categories are computed
- **Tests (TDD)** — `packages/archon-search/tests/eval/test_metrics.py`:
  - Unit: `test_compute_reranker_lift` — lift equals post-rerank nDCG minus pre-rerank nDCG
  - Unit: `test_compute_routing_shortlist_accuracy_uses_gold_collections_without_retrieval_hit` — routing can be scored without a retrieval hit
  - Unit: `test_compute_routing_shortlist_accuracy_skips_bypassed_queries` — bypassed traces are excluded
  - Unit: `test_compute_routing_shortlist_accuracy_skips_non_tier3_traces` — Tier 1 search-all and Tier 2 all-routable-fit traces cannot satisfy the shortlist gate
  - Unit: `test_compute_routing_shortlist_accuracy_returns_none_when_not_enabled` — absent Search-owned routing contract is explicit
  - Unit: `test_compute_latency_percentiles` — p50 and p95 are stable for fixed inputs
  - Unit: `test_compute_latency_percentiles_nearest_rank_small_n` — small samples use the documented percentile method
  - Checkpoint: `cd packages/archon-search && uv run pytest --no-cov tests/eval/test_metrics.py -k "lift or routing or latency" -v`

#### Task 3.3 — Eval runner trace execution
- [ ] **File**: `packages/archon-search/archon_search/eval/runner.py`
- **Depends on**: Task 1.2, Task 1.5, Task 2.4, Task 2.5, Task 3.2
- **Description**:
  - Implement `async run_eval_suite(corpus_root: Path, runtime_config_path: Path, thresholds_path: Path | None = None, baseline_path: Path | None = None) -> EvalReport`.
  - Load `runtime.toml` from an explicit path and reject metric-incompatible runtime settings before any corpus execution.
  - When `thresholds_path` is omitted, run in report-only calibration mode and do not call `assert_thresholds()`.
  - When `baseline_path` is present, load machine-readable baseline metadata so reports can show deltas against the accepted baseline.
  - Ingest the committed corpus into a module-scoped temporary LanceDB store using deterministic eval backends.
  - Map runtime path-derived document IDs and source paths back to stable fixture `doc_id` values before scoring.
  - Reject `EvalQuery.collection is None` before execution unless `routing.contract_enabled = true` and Phase 0 confirms routing is Search-owned.
  - Forbid using labels, gold collections, or relevant fixture document collections to choose the retrieval collection for collectionless queries.
  - Execute Search-owned routing only when `routing.contract_enabled = true`; otherwise set routing metrics to `None` with an explicit report note.
  - Execute search trace paths per query, capture candidate shortlist names if applicable, gold collections, pre-rerank results, post-rerank results, and elapsed milliseconds.
  - Use `candidate_depth` for raw pre-rerank candidates, `return_depth` for raw post-rerank chunk output, and `metric_depth` for post-dedupe unique-document scoring.
  - Over-fetch raw chunk candidates until `metric_depth` unique fixture documents are available after deduplication, or fail with an under-depth diagnostic when the corpus has enough documents but search output cannot supply enough unique documents.
  - Record notes when routing is skipped because a query bypasses routing or because routing is disabled by configuration.
- **Releasable**: one function produces a full in-memory eval report for the committed corpus
- **Tests (TDD)** — `packages/archon-search/tests/eval/test_runner.py`:
  - Mark only full-corpus runner tests with `@pytest.mark.eval`; keep label-leakage, ID-mapping, routing-skip, config, and under-depth safety tests unmarked with miniature fixtures.
  - Eval integration: `test_eval_runner_executes_full_corpus` — one trace is produced per query
  - Eval integration: `test_eval_suite_is_deterministic_except_latency` — two fresh stores produce identical quality metrics and rankings, excluding latency
  - Unmarked integration: `test_eval_runner_records_candidate_shortlist_when_routing_contract_exists` — Search-owned candidate shortlist names are retained when a routing contract exists
  - Unmarked integration: `test_eval_runner_skips_routing_metric_without_search_owned_routing_contract` — routing metric is not silently fabricated
  - Unmarked integration: `test_eval_runner_rejects_collectionless_query_without_routing_contract` — `collection=None` fails before search when routing is disabled
  - Unmarked integration: `test_eval_runner_does_not_use_gold_labels_to_select_collection` — labels cannot determine the searched collection
  - Unmarked integration: `test_eval_runner_derives_gold_collections_from_labels_not_router` — gold collections are added by eval code, not router trace APIs
  - Unmarked integration: `test_eval_runner_records_routing_tier_and_confidence_gate_reason` — eval traces preserve the production-observable routing fields needed to skip non-shortlist tiers
  - Unmarked integration: `test_eval_runner_overfetches_until_unique_document_depth` — duplicate chunks do not make `metric_depth` shallower than the metric depth
  - Unmarked integration: `test_eval_runner_records_skipped_routing_queries` — bypassed queries increment skip accounting
  - Unmarked integration: `test_eval_runner_maps_runtime_doc_ids_to_fixture_doc_ids` — metrics consume stable fixture IDs
  - Checkpoint: `cd packages/archon-search && uv run pytest --no-cov tests/eval/test_runner.py -k "runner or routing or depth" -v`

#### Task 3.4 — Threshold assertion and human-readable reporting
- [ ] **File**: `packages/archon-search/archon_search/eval/runner.py`
- **Depends on**: Task 1.4, Task 3.3
- **Description**:
  - Implement `assert_thresholds(report: EvalReport) -> None`.
  - Implement `render_report(report: EvalReport) -> str`.
  - `assert_thresholds()` must fail clearly when called without thresholds; report-only calibration should render only.
  - Failure messages must include actual metric, threshold, baseline metric when available, delta from threshold, and delta from baseline for each failing gate.
  - Rendered reports must include baseline deltas when `EvalReport.baseline` is present.
  - Quality metrics use minimum-floor semantics; lower-than-floor fails.
  - Reranker lift is included in the rendered report but is not a v1 quality floor.
  - A metric value of `None` with a configured floor fails with an actionable configuration message; an omitted floor with a `None` metric skips that metric with a report note.
  - Latency metrics use maximum-ceiling semantics only when `[latency].gating_enabled = true`; higher-than-ceiling fails.
  - When latency gating is disabled, latency appears in the report but cannot fail the test.
  - Report output must call out that latency measurements use deterministic eval backends.
- **Releasable**: eval results can fail CI with actionable output
- **Tests (TDD)** — `packages/archon-search/tests/eval/test_runner.py`:
  - Integration: `test_run_eval_suite_report_only_without_thresholds` — calibration mode renders a report without thresholds
  - Integration: `test_assert_thresholds_requires_thresholds_for_gating` — calling assertion without thresholds fails with an actionable message
  - Integration: `test_assert_thresholds_passes_on_synthetic_passing_report` — an in-memory report with values at or above floors passes after thresholds are loaded
  - Integration: `test_assert_thresholds_reports_quality_floor_regressions` — failure message includes quality metric deltas
  - Integration: `test_render_report_shows_baseline_deltas` — report compares current metrics against the machine-readable baseline
  - Integration: `test_assert_thresholds_fails_when_required_metric_is_none` — configured floor plus missing metric fails clearly
  - Integration: `test_assert_thresholds_skips_omitted_floor_and_none_metric` — omitted optional metric floor can skip with a note
  - Integration: `test_assert_thresholds_reports_latency_ceiling_regressions_when_enabled` — gated latency uses ceiling semantics
  - Integration: `test_assert_thresholds_does_not_fail_report_only_latency` — latency cannot fail when gating is disabled
  - Unit: `test_render_report_mentions_eval_backend_latency` — rendered report documents deterministic-eval-backend latency context
  - Checkpoint: `cd packages/archon-search && uv run pytest --no-cov tests/eval/test_runner.py -k "assert_thresholds or render_report" -v`

### Phase 4 — Pytest integration, calibration, and CI gating
> **Releasable**: after each task; by Task 4.5 the evaluation harness is callable in local pytest and enforced in release CI

#### Task 4.1 — Eval fixtures and pytest marker wiring
- [ ] **Files**: `packages/archon-search/pyproject.toml`, `packages/archon-search/tests/eval/conftest.py`
- **Depends on**: Task 3.3
- **Description**:
  - Add or update the extracted package pytest configuration, not only the root Archon pytest config.
  - Set package `testpaths = ["tests"]`.
  - Register `eval` and any package-local live/integration markers.
  - Set default package addopts so slow eval tests are excluded from default runs, for example `-m "not live and not eval"`.
  - Ensure package coverage targets `archon_search`, not root `archon`.
  - Ensure the package default suite runs unmarked eval unit, parser, config, metric, and safety tests under `archon_search` coverage.
  - Add module-level model-download guards following the existing Archon import-time injection pattern, but route eval tests to the query-sensitive deterministic eval backends from `archon_search.eval.backends`.
  - Provide module-scoped fixtures for temporary LanceDB storage and the loaded eval corpus.
  - Mark only the slow full-corpus eval smoke/integration tests with `eval`; keep pure fixture-loader, threshold-parser, and metric unit tests in the default package test suite.
  - Ensure the default test selection excludes `eval` while direct unit-test checkpoint commands still run.
- **Releasable**: maintainers can run the eval slice locally without real model downloads
- **Tests (TDD)** — `packages/archon-search/tests/eval/test_pytest_integration.py`:
  - Integration: `test_eval_pytest_marker_excluded_from_default_run` — default selection excludes eval
  - Integration: `test_eval_conftest_uses_deterministic_eval_backends` — eval backends are active during eval tests
  - Integration: `test_eval_marker_only_marks_full_corpus_tests` — unit metric/fixture tests are not accidentally deselected
  - Integration: `test_package_pytest_config_targets_archon_search_coverage` — package coverage config does not use root `archon`
  - Integration: `test_package_default_suite_covers_unmarked_eval_units` — eval support code is not hidden behind `--no-cov` or `-m eval`
  - Checkpoint: `cd packages/archon-search && uv run pytest --no-cov tests/eval/test_pytest_integration.py -v`

#### Task 4.2 — End-to-end report-only eval smoke test
- [ ] **File**: `packages/archon-search/tests/eval/test_eval_suite.py`
- **Depends on**: Task 4.1
- **Description**:
  - Add `@pytest.mark.eval` smoke coverage that calls `run_eval_suite(corpus_root, runtime_config_path, thresholds_path=None, baseline_path=None)`, renders the report, and does not assert thresholds.
  - Use this task to bootstrap the first measured report without invented threshold values.
  - Keep the smoke test single-entry so CI sees one obvious failure point with full report output.
  - Ensure the test failure text is rich enough to diagnose regressions without rerunning locally first.
- **Releasable**: the full harness is executable from pytest before baseline and thresholds exist
- **Tests (TDD)** — `packages/archon-search/tests/eval/test_eval_suite.py`:
  - E2E: `test_eval_suite_report_only_smoke` — full eval harness runs against the committed corpus and produces a report string without thresholds
  - E2E: `test_eval_suite_report_only_does_not_assert_thresholds` — calibration mode never passes or fails from missing floors
  - Checkpoint: `cd packages/archon-search && uv run pytest --no-cov tests/eval/test_eval_suite.py -m eval -k "report_only" -v`

#### Task 4.3 — Baseline calibration before gating
- [ ] **Files**: `packages/archon-search/tests/eval/baselines/baseline.md`, `packages/archon-search/tests/eval/baselines/baseline.json`, `packages/archon-search/tests/eval/thresholds.toml`
- **Depends on**: Task 4.4
- **Description**:
  - Run the full eval suite once against the accepted corpus and runtime config.
  - Commit the rendered baseline report and machine-readable baseline metadata alongside `thresholds.toml`.
  - Record metrics, corpus hash, labels hash, runtime config hash, thresholds hash, exact eval command, package commit, and dependency versions in `baseline.json`.
  - Ensure every committed quality floor is derived from, or intentionally below, the saved baseline metric.
  - Require an explicit rationale in the baseline report or README for any floor set below the measured baseline.
  - Reject floor reductions larger than `[policy].max_floor_drop_without_waiver` unless the baseline metadata names a reviewed waiver or issue ID.
  - Do not enable release gating until this calibration artifact exists.
- **Releasable**: release gating has a reviewed, reproducible baseline rather than invented thresholds
- **Tests (TDD)** — `packages/archon-search/tests/eval/test_baseline_contract.py`:
  - Integration: `test_thresholds_have_matching_baseline_report` — committed thresholds require a saved baseline report
  - Integration: `test_baseline_metadata_hashes_match_fixture_inputs` — baseline metadata hashes match corpus, labels, runtime config, and thresholds
  - Integration: `test_baseline_metadata_records_command_commit_and_dependencies` — baseline can be reproduced from recorded context
  - Integration: `test_quality_floors_never_exceed_baseline` — configured floors cannot be higher than the measured baseline
  - Integration: `test_quality_floor_below_baseline_requires_rationale` — intentionally lowered floors need an explicit rationale
  - Integration: `test_quality_floor_drop_beyond_policy_requires_waiver` — large floor reductions require reviewed waiver metadata
  - Checkpoint: `cd packages/archon-search && uv run pytest --no-cov tests/eval/test_baseline_contract.py -v`

#### Task 4.4 — Gated eval smoke test
- [ ] **File**: `packages/archon-search/tests/eval/test_eval_suite.py`
- **Depends on**: Task 4.3
- **Description**:
  - Add `@pytest.mark.eval` smoke coverage that calls `run_eval_suite(corpus_root, runtime_config_path, thresholds_path, baseline_path)`, renders the report, and calls `assert_thresholds(report)`.
  - Keep this test distinct from the report-only smoke so calibration remains available without thresholds.
  - Fail if baseline metadata no longer matches the committed corpus, labels, runtime config, or thresholds.
  - Fail when quality metrics regress below floors or when gated latency exceeds ceilings.
- **Releasable**: the full harness has a single maintained gated test after calibration exists
- **Tests (TDD)** — `packages/archon-search/tests/eval/test_eval_suite.py`:
  - E2E: `test_eval_suite_gated_smoke` — full eval harness runs against committed corpus, baseline, and thresholds
  - E2E: `test_eval_suite_gated_smoke_reports_baseline_deltas` — report includes deltas against baseline and threshold
  - E2E: `test_eval_suite_gated_smoke_rejects_stale_baseline_hashes` — stale baseline metadata cannot pass
  - Checkpoint: `cd packages/archon-search && uv run pytest --no-cov tests/eval/test_eval_suite.py -m eval -k "gated" -v`

#### Task 4.5 — Release-CI inclusion
- [ ] **File**: executable post-FEAT-038 package release gate (`.github/workflows/release.yml`, `release.sh`, or the package repository's equivalent)
- **Depends on**: Task 4.4
- **Description**:
  - Update the actual executable extracted package release gate to run the eval slice explicitly and fail before tag, publish, or release creation.
  - If FEAT-038 creates a separate package repository, update that repository's root `.github/workflows/release.yml` or release script.
  - If FEAT-038 keeps `packages/archon-search/` inside this monorepo, update the executable repository release script or workflow that owns the package release, such as `release.sh` if it remains the release entrypoint. Also update `Documentation/release-process.md`, but documentation alone is not a release gate.
  - Install the extracted package with Search/eval dependencies before the gate; dependency-missing skips are forbidden in release mode.
  - Define the exact release-gate command in that executable entrypoint and run it under the package pytest config, either by `cd packages/archon-search` or by `pytest -c packages/archon-search/pyproject.toml`.
  - The release command must override the package default marker exclusion and run the gated eval smoke with package coverage enabled; do not use `--no-cov` in release mode.
  - The release command must fail if pytest collects zero eval tests, if all selected eval tests are skipped, or if selected eval tests are xfailed.
  - Do not place a workflow under `packages/archon-search/.github/workflows/` unless FEAT-038 makes `packages/archon-search/` the repository root; nested `.github` directories do not run in GitHub Actions.
  - Keep fast/default CI excluding `-m eval`.
  - Make the workflow surface the rendered report in logs when thresholds fail.
- **Releasable**: eval regressions block release CI for the search package
- **Tests (TDD)** — `packages/archon-search/tests/eval/test_ci_contract.py`:
  - Integration: `test_release_gate_includes_eval_slice` — the concrete executable post-FEAT-038 release entrypoint references and runs the eval marker or eval test path
  - Integration: `test_release_gate_uses_package_pytest_config` — release cannot accidentally run under root Archon pytest config
  - Integration: `test_release_gate_installs_search_eval_dependencies` — dependency setup is explicit before pytest
  - Integration: `test_release_gate_runs_with_package_coverage` — release gate enforces `archon_search` coverage instead of using `--no-cov`
  - Integration: `test_release_gate_fails_when_eval_collection_is_empty_or_skipped` — zero, skipped, or xfailed eval tests are release failures
  - Integration: `test_release_docs_reference_eval_slice_but_are_not_sufficient` — `Documentation/release-process.md` may document the gate but cannot be the only passing artifact
  - Integration: `test_release_script_runs_eval_before_publish_step` — monorepo `release.sh` fallback must run eval before tag, publish, or release creation if it remains the package release entrypoint
  - Integration: `test_fast_ci_excludes_eval_slice` — fast workflow or package pytest defaults still exclude eval
  - Integration: `test_nested_package_github_workflow_is_not_the_only_gate` — nested workflow files are not treated as sufficient unless the package is its own repo
  - Checkpoint: `cd packages/archon-search && uv run pytest --no-cov tests/eval/test_ci_contract.py -v`

### Phase 5 — Documentation and baseline hardening
> **Releasable**: after each task; by Task 5.2 the harness is documented and maintainable without oral history

#### Task 5.1 — Eval README and maintenance guide
- [ ] **File**: `packages/archon-search/tests/eval/README.md`
- **Depends on**: Task 4.2
- **Description**:
  - Document the corpus layout, `documents.jsonl` manifest, query schema, label schema, threshold semantics, baseline metadata schema, and the process for refreshing thresholds from measured baselines.
  - Document that labels are document-level and metrics deduplicate chunk results before scoring.
  - Require threshold changes to include a saved rendered report and a rationale when any quality floor is lowered.
  - Document the floor-drop waiver policy and the requirement for a reviewed issue or waiver ID when reductions exceed the configured tolerance.
  - Explicitly state that v1 latency metrics use deterministic eval backends and local LanceDB and should be interpreted as regression guards, not production SLAs.
  - Include the exact local commands for report-only calibration, gated eval, and default unmarked eval-unit tests.
- **Releasable**: maintainers can extend the harness without reverse-engineering the tests
- **Tests (TDD)** — `packages/archon-search/tests/eval/test_docs_contract.py`:
  - Integration: `test_eval_readme_mentions_threshold_baselines` — README documents baseline-derived thresholds
  - Integration: `test_eval_readme_mentions_machine_readable_baseline_metadata` — README documents `baseline.json`
  - Integration: `test_eval_readme_requires_threshold_lowering_rationale` — README documents anti-rot threshold policy
  - Integration: `test_eval_readme_mentions_floor_drop_waiver_policy` — README documents waiver requirements for large floor drops
  - Integration: `test_eval_readme_mentions_document_level_metrics` — README explains document-level deduplication
  - Integration: `test_eval_readme_mentions_eval_backend_latency_limits` — README explains latency caveat
  - Checkpoint: `cd packages/archon-search && uv run pytest --no-cov tests/eval/test_docs_contract.py -k "readme" -v`

#### Task 5.2 — Package and roadmap documentation updates
- [ ] **Files**: `packages/archon-search/README.md`, `Documentation/Backlog/FEAT-037-search-world-class-roadmap.md`, `Documentation/Architecture/180_search_architecture.md`
- **Depends on**: Task 5.1
- **Description**:
  - Add an Evaluation section to the package README.
  - Update the Search architecture and FEAT-037 roadmap docs so the extracted package’s evaluation harness is documented as the sanctioned regression gate.
  - Document whether v1 includes only release gating or also path-filtered PR eval, based on the accepted FEAT-038 package workflow.
  - Update FEAT-037 or add a follow-up backlog item making clear that live query logging, relevance feedback, privacy policy, and online data collection remain after FEAT-039.
  - Keep the wording aligned with the actual delivered metric set and pytest commands.
- **Releasable**: the harness is visible in both package-local docs and project planning docs
- **Tests (TDD)** — `packages/archon-search/tests/eval/test_docs_contract.py`:
  - Integration: `test_package_readme_mentions_eval_command` — README includes the maintained eval command
  - Integration: `test_roadmap_docs_reference_eval_harness` — roadmap/docs mention the delivered harness
  - Integration: `test_roadmap_docs_keep_data_collection_followup_open` — docs do not imply FEAT-039 completes the full data-collection loop
  - Checkpoint: `cd packages/archon-search && uv run pytest --no-cov tests/eval/test_docs_contract.py -k "package_readme or roadmap_docs" -v`
