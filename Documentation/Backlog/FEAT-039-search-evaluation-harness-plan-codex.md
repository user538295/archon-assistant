
**Purpose**: Add a deterministic offline evaluation harness for the extracted Search package so retrieval and Search-owned routing changes can be measured against a committed benchmark before merge and release.
**Audience**: Maintainers of `packages/archon-search/` who change retrieval, reranking, routing, or performance-sensitive search internals.
**Status**: Draft
**Last reviewed**: 2026-05-03
**Next review**: 2026-11-02

# FEAT-039 — Search Offline Evaluation Harness v1
**Implementation status**: To Do

---

## Background

FEAT-037 identified evaluation discipline and a later data-collection loop as prerequisites for making Search a standalone product. FEAT-039 covers the offline evaluation harness only (roadmap item 4, v1). Live query logging, user feedback capture, judgment collection, and safe controlled experiments are required by the roadmap but deferred to a separate **FEAT-039b** (online data-collection loop). FEAT-039b must be created as a tracked backlog item and completed before roadmap item 4 is fully closed. This plan (FEAT-039) does not close FEAT-037 item 4 alone.

The current verified codebase has strong unit and integration coverage under `tests/search/`, but it does not expose a first-class retrieval benchmark loop with committed relevance labels, metric computation, or CI gating.

This feature must land after FEAT-038, because the brief intentionally places all new code inside the extracted package boundary: `packages/archon-search/archon_search/` and `packages/archon-search/tests/`.

The practical gap is simple: maintainers can change RRF weighting, reranker behavior, routing thresholds, or search latency characteristics today without a reproducible signal showing whether the change helped or regressed quality. This plan closes that gap with a synthetic offline corpus, query-sensitive deterministic eval backends, metric computation, and pytest-based PR/release gating.

## Post-FEAT-038 boundary assumptions

FEAT-038 delivered all three roadmap prerequisites that FEAT-039 depends on. The following are confirmed facts verified against the current codebase:

- **Package root**: `packages/archon-search/` exists in the monorepo. Search code is no longer under `archon/search/`. The import path is `archon_search`.
- **Canonical service contract + job model (roadmap item 2)**: `archon_search.types` defines `Query`, `Collection`, `CollectionDetail`, `Chunk`, `RouteResponse`, `IngestJob`, `ReindexJob`, `DeleteJob`, and `JobStatus`. The async job model with `job_id`, `status`, persistence, and cancel support is implemented in `archon_search/jobs/`.
- **Real metadata schema (roadmap item 3)**: `Chunk` carries `metadata: dict[str, str]` (filterable), `custom_score: float | None` (ranking), `ingested_by: str` (audit), and `updated_at: str` (audit). Schema evolution policy is additive-only.
- **Routing is Search-owned**: `POST /route` is implemented in `archon_search/server/routes_route.py` and returns `RouteResponse` (pinned names, routable names, pre-context, decomposer flag). Routing can be exercised without Archon's decomposer. `routing_contract_enabled = true` for all FEAT-039 eval configuration.
- **HTTP REST control plane**: `POST /ingest`, `GET /jobs/{id}`, `DELETE /jobs/{id}`, `GET /health`, `GET /status`, `GET /indexing-state`, `GET|POST|DELETE /collections/*` are all live.
- **Package pytest config and release entrypoint**: confirm exact values in `packages/archon-search/pyproject.toml` during Phase 0 Task 0.1 before writing any eval code.
- The offline harness must not change Archon's daemon runtime path, Telegram behavior, or public MCP/REST response contract.
- FEAT-039 is not complete until a path-filtered PR eval gate runs for retrieval, reranking, routing, and eval-package changes. Release-only gating is not enough to close FEAT-039.

## Goal

After FEAT-039 is complete, a maintainer can run an evaluation-only pytest slice inside `packages/archon-search/` and receive a stable report covering recall@k, MRR, nDCG@k, reranker lift, latency percentiles, and routing accuracy. Routing accuracy is computable because `POST /route` is a Search-owned contract (confirmed by FEAT-038). The same suite runs in a path-filtered PR gate for retrieval, reranking, routing, and eval-package changes, runs before package release mutation, enforces quality floors and optional latency ceilings, and fails visibly when a ranking or routing change regresses the committed benchmark.

---

## Scope

### In Scope
- Deterministic evaluation-only models, fixtures, and pytest infrastructure inside `packages/archon-search/`
- Query-sensitive deterministic eval backends that are corpus-aware but label-blind; the existing all-zero embedding and uniform-reranker test fakes are not sufficient for benchmark signal
- Synthetic retrieval benchmark corpus with 50-100 committed documents across 2-3 retrieval collections
- Manifest-backed document, query, and relevance-label fixture format, including optional graded judgments
- Document-level metric computation with explicit chunk-result deduplication
- Eval-only score and rank decomposition so pre-rerank and post-rerank metrics are computable without leaking private trace fields into public result APIs
- Metric computation for recall@1/3/5, MRR, nDCG@5/10, report-only reranker lift, conditional routing accuracy, and latency percentiles
- Committed eval runtime config that sets retrieval depth high enough for all metrics, including `nDCG@10`
- Threshold-based assertions for quality floors, optional latency ceiling checks, and structured per-run output for CI diagnostics
- Simple routing accuracy metric: whether the router shortlist includes the collection containing the gold document; queries with disabled or bypassed routing are excluded and counted separately
- Path-filtered PR gating for changes to the full retrieval pipeline: parser, chunker, embedder, store, pipeline, reranker, Search-owned routing, metadata/schema/config, eval fixtures, eval code, eval runtime config, thresholds, baselines, package dependency manifests, lockfiles, eval extras/dependency groups, package pytest config, and package release/CI gates
- Documentation for how maintainers update the corpus, labels, required baseline reports, and thresholds

### Out of Scope
- Live query logging from real user traffic
- Relevance feedback capture and privacy policy for online data collection
- Automatic relevance-label generation with LLMs
- A standalone `archon-search eval` CLI command
- Public REST endpoints for evaluation runs
- Multi-tenant or authenticated evaluation services
- Unrelated fast-suite CI gating; eval remains a dedicated slower path-filtered PR and release test slice rather than part of every Archon test run.

---

## Acceptance criteria
- [ ] `packages/archon-search/tests/` contains an `eval` pytest slice that runs entirely with deterministic eval-specific, label-blind embedder and reranker backends
- [ ] Phase 0 links the accepted FEAT-038 artifacts and confirms exact package root, import path, canonical service contract, metadata schema/versioning rules, release entrypoint, dependency install command, pytest config, and routing contract (confirmed Search-owned via `POST /route`) before code implementation begins
- [ ] A path-filtered PR eval gate runs the complete gated eval slice for parser, chunker, embedder, store, pipeline, reranker, Search-owned routing, metadata/schema/config, eval fixture/code, eval runtime, threshold, baseline, package dependency manifest, lockfile, eval extra/dependency group, package pytest, and CI/release gate changes; release-only gating is recorded as partial fulfillment and cannot close FEAT-039
- [ ] Phase 0 records whether routing is Search-owned; if not, it links an Archon-owned routing-evaluation follow-up and the FEAT-039 report explicitly leaves routing accuracy skipped/incomplete
- [ ] When routing is enabled in the package contract, routing_accuracy is computed as the fraction of non-bypassed queries where the router's candidate shortlist intersects the gold collection set (derived from positive retrieval labels); bypassed and routing-disabled queries are reported separately and excluded from the metric
- [ ] The committed fixture corpus contains 50-100 manifest-listed documents split across at least 2 distinct retrieval collections and 25-30 benchmark queries with at least one explicit positive relevance label (`grade > 0`) per query
- [ ] For retrieval-scored queries that target a single collection, every positive relevance label belongs to that query's searched collection; unreachable positives fail fixture validation
- [ ] Eval runtime config is committed separately from thresholds and uses eval-specific depth names: `candidate_depth`, `return_depth`, and `metric_depth`; `metric_depth >= 10`, `return_depth >= metric_depth`, and `candidate_depth > return_depth`
- [ ] Fixture labels use stable fixture document IDs, and the eval runner maps runtime path-derived document IDs back to fixture IDs before computing metrics
- [x] Chunk-level search results are deduplicated to document-level rankings before computing document-level recall, MRR, and nDCG
- [x] After chunk deduplication, each scored query has at least 10 unique document IDs when the corpus has enough candidate documents; duplicate top chunks cannot silently make `nDCG@10` a shallower metric
- [ ] Eval trace output exposes vector rank, vector raw score/distance, FTS rank, FTS raw score, fused/RRF score, and reranker score without adding eval-only provenance fields to the normal public `SearchResult` response contract or MCP `search` / `search_with_context` payload keys
- [ ] The harness measures `recall@1`, `recall@3`, `recall@5`, `MRR`, `nDCG@5`, `nDCG@10`, report-only `reranker lift`, conditional `routing accuracy`, `latency p50`, and `latency p95`
- [ ] Routing accuracy assertions are skipped with an explicit note unless a Search-owned routing contract exists (`[routing].contract_enabled = true`); bypassed and routing-disabled queries are reported separately and excluded from the metric
- [ ] Report-only calibration can run without thresholds; PR/release gating is enabled only after a machine-readable baseline, a rendered baseline report, and `thresholds.toml` are committed together
- [ ] Quality thresholds are loaded from committed config, compared against actual run output, and cause pytest failures when any gated quality metric drops below its floor
- [ ] Latency percentiles are reported every run; latency ceilings are report-only by default in v1 unless an explicit committed ceiling enables gating
- [ ] A rendered baseline report and machine-readable baseline metadata are committed before PR/release gating is enabled; quality floors must be derived from or intentionally below that baseline with rationale
- [ ] Pytest failure output shows metric deltas against both the saved baseline and configured floor/ceiling, using floor semantics for quality metrics and ceiling semantics for gated latency metrics
- [ ] Slow eval tests are excluded from the unrelated fast/default test slice, included in path-filtered PR CI, and included in release CI; pure fixture and metric unit tests stay in the default package test suite unless they run the full corpus
- [ ] Package CI has a clean-environment install smoke test for the Search/eval dependency extra or dependency group, including pytest plugins needed by the coverage gate
- [ ] PR and release CI install the extracted package with Search/eval dependencies, run under the package pytest config, run both the package default suite and the complete eval marker slice with `archon_search` coverage enabled, and fail if zero eval tests are collected or any selected eval test is skipped, xfailed, or xpassed without an explicit reviewed allowlist
- [ ] Package CI coverage is a gate, not just a report: the combined default-suite plus eval-slice coverage for `archon_search` must fail below the project floor (`85%` unless the package sets a different floor in `pyproject.toml`)
- [ ] If CI runs default and eval slices as separate pytest invocations, intermediate invocations cannot apply `--cov-fail-under`; coverage is combined first and the fail-under check runs exactly once after both slices complete
- [ ] Any skip/xfail/xpass allowlist is committed in a narrow machine-readable file with exact test node IDs, issue ID, reviewer, reason, and expiry date; broad globs and expired entries fail CI
- [ ] The full eval suite is deterministic across two fresh temporary stores for quality metrics and result rankings; latency is excluded from byte-for-byte determinism checks
- [ ] Deterministic ordering covers hybrid retrieval, reranking, routing traces, and any Search-owned cross-collection merge: ties use stable secondary keys such as collection name, source path, fixture document ID, and chunk ID
- [ ] Harness documentation explains fixture structure, label semantics, baseline report storage, and the rule for updating thresholds from measured baselines rather than invented targets

---

## Query Collection and Privacy Boundaries (v1 Definition)

This section satisfies roadmap item 4 acceptance criterion: "Query collection, labeling, and privacy boundaries are explicitly defined." Implementation of the online collection loop is deferred to FEAT-039b.

- **Synthetic corpus labels**: Hand-authored document-level relevance judgments. No real user data in v1. Labels committed to the repository alongside fixture documents.
- **Future query logging policy** (FEAT-039b scope): Opt-in only. Queries logged locally to a user-controlled directory (`~/.archon/search-logs/`). No external transmission without explicit user consent. Log format: timestamp, anonymized query text, collection searched, result count. No user identity stored.
- **Privacy boundaries**: Real user query text must never be committed to the eval corpus without a manual anonymization review. The labeling pipeline must separate raw query logs from label files before any repository commit or sharing. Queries containing PII must be excluded from the corpus.
- **Label file privacy**: Relevance label files contain only query IDs, document IDs, and grade values — no user identity, session context, or raw query text.
- **Consent boundary**: Any expansion of logging scope, external storage, or automated label generation requires explicit user opt-in and a privacy policy update before implementation.

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
- Routing accuracy is measured only when a Search-owned routing contract exists (`[routing].contract_enabled = true`). A query contributes 1.0 if the router's candidate shortlist intersects the gold collection set (derived from positive retrieval labels), 0.0 otherwise. Bypassed and routing-disabled queries are excluded and counted separately. This is not full Archon prompt-selection correctness or ranking precision across every collection.
- FEAT-039 does not close FEAT-037's data-collection-loop requirement; it creates the offline gate that later data collection can feed
- Changes to `eval/backends.py` are included in the `eval_hash` staleness gate. Any backend change automatically invalidates the stored baseline and requires a new calibration run before PR/release gating can pass.
- The initial harness is pytest-only; maintainers who want ad hoc local eval against custom corpora will need a later CLI feature
- v1 release gating is not sufficient by itself. Path-filtered PR eval is part of FEAT-039 completion; if the Search package workflow cannot support path-filtered PR gating, this plan must remain marked partial and link the missing PR-gating follow-up.

---

## Metric semantics

- All ranking metrics are computed over per-query rankings of unique fixture document IDs after chunk-result deduplication. When several chunks from the same document appear, the first-ranked chunk defines that document's rank.
- Aggregate quality metrics are macro-averages across benchmark queries; each query contributes one value regardless of how many relevant documents it has. Queries without relevance labels, or with only `grade = 0` labels, are invalid by fixture contract.
- Labels with `grade > 0` are relevant. Labels with `grade = 0` are explicit non-relevant judgments: they may appear in `labels.jsonl` as explicit confirmation that a document is non-relevant; they do not count as relevant for recall, MRR, or IDCG computation. IDCG is computed from grade>0 labels only.
- `recall@k` is `relevant unique documents in the first k unique ranked documents / total relevant documents for that query`, then macro-averaged.
- `MRR` uses the reciprocal rank of the first relevant unique document after deduplication; queries with no relevant document in the ranking contribute `0`.
- `nDCG@k` supports binary and graded labels. Use gain `2^grade - 1`, discount `log2(rank + 1)`, and IDCG from the ideal top-`k` labels for that query sorted by grade. Missing result positions contribute `0`; relevant labels beyond `k` do not appear in the `IDCG@k` denominator. When a query has fewer than k relevant documents, IDCG is computed from all relevant documents placed in ideal positions 1..n_rel, with remaining positions contributing 0 gain. For example, with 3 relevant documents at k=10, IDCG@10 uses gains at positions 1, 2, 3 only. This means a perfect ranking of those 3 documents yields nDCG@10 = 1.0.
- Fetch `candidate_depth` raw chunk candidates, deduplicate chunk results to unique fixture document IDs, and fail with an under-depth diagnostic when the resulting unique-document count is below `metric_depth` despite the **searched collection** having at least `metric_depth` unique documents. The per-collection unique document count is read from the fixture corpus (`EvalCorpus.documents` filtered by `collection`), not from the LanceDB store — this avoids false under-depth failures from partial ingestion while still producing an accurate threshold against the fixture-defined corpus size. Do not use total corpus size for this check — the diagnostic must reflect the actual retrievable depth from the collection being searched. Do not use iterative depth-increasing loops — `candidate_depth` is a fixed config value and the diagnostic is the correct response to insufficient unique-document depth. `metric_depth >= 10` is the required unique-document depth, not the public Search `top_k_return` chunk count.
- `reranker_lift` is report-only in v1 and equals post-rerank `nDCG@10` minus pre-rerank `nDCG@10`, both computed at `return_depth` depth. **Candidate pool semantics**: the reranker receives all `candidate_depth` raw chunk candidates and returns the top `return_depth` by reranker score. Pre-rerank nDCG is computed over the top-`return_depth` candidates sorted by RRF score (i.e., the pre-rerank ranked list truncated at `return_depth`). Post-rerank nDCG is computed over the top-`return_depth` candidates sorted by reranker score — a different set that may include candidates ranked below `return_depth` by RRF. Both lists have the same length (`return_depth`) and are scored at `nDCG@10` (with chunk-level dedup to unique documents). Using equal depth isolates reranker quality from the truncation level, even though the two lists may contain different documents due to reranker promotion.
- Latency percentiles use the nearest-rank method on sorted per-query millisecond values: index `ceil(percentile / 100 * n) - 1`, clamped to the sample range. A single-sample run returns that sample for both p50 and p95. When `percentile / 100 * n` is an exact integer, ceiling returns that integer unchanged (not plus one); the clamped range `[0, n-1]` prevents out-of-bounds access. Implementations using floating-point arithmetic must round to avoid off-by-one from floating-point representation of exact integers.
`routing_accuracy` measures whether the router's candidate shortlist includes at least one collection containing a positively-labeled document for that query. A query contributes 1.0 if the shortlist intersects the gold collection set, 0.0 otherwise. Queries where routing is disabled or bypassed (pinned collections) are excluded from the metric and counted separately. `routing_accuracy` is `None` when no routing contract exists in the package (default in v1 until product separation defines the routing API). Macro-averaged across all non-excluded queries.

---

## Architecture

- New private diagnostic types under `packages/archon-search/archon_search/_diagnostics.py`:
  - `SearchScoreBreakdown(vector_rank: int | None, vector_score: float | None, vector_score_kind: Literal["distance", "similarity", "score"] | None, fts_rank: int | None, fts_score: float | None, fts_score_kind: Literal["bm25", "score"] | None, rrf_score: float, reranker_score: float | None)` dataclass. `vector_score` is the raw backend value from the vector result row, normally LanceDB `_distance` where lower is better; `fts_score` is the raw backend value from the FTS result row, normally LanceDB/BM25 `_score` where higher is better. Missing backend fields are `None` and must not be normalized into a different semantic without changing the `*_score_kind`.
    - `vector_score_kind: Literal["distance", "similarity", "score"] | None` — `"distance"` means lower is better (e.g., LanceDB `_distance`); `"similarity"` means higher is better (e.g., cosine similarity); `"score"` is a catch-all for other signed score semantics. Implementers must not use `None` when the kind is known — `None` means the backend did not produce a raw vector score.
    - `fts_score_kind: Literal["bm25", "score"] | None` — `"bm25"` means higher is better (LanceDB FTS `_score`); `"score"` is a catch-all for FTS backends that don't use BM25 scoring; adding a new FTS kind is a breaking change and must update all consumers. `None` means no FTS score was produced for this candidate.
    - Score-kind values must accurately reflect the polarity of the corresponding score. If the package backend uses a different scoring convention, the kind field must be updated — using the wrong kind will silently invert relative comparisons in debugging and future metric extensions.
  - **Abstraction note**: `vector_score_kind` and `fts_score_kind` values describe semantic polarity, not backend implementation names. The LanceDB adapter populates these from `_distance` and `_score` column names respectively, but the type system must not embed LanceDB-specific names. When the store backend changes, only the adapter needs updating — not all `SearchScoreBreakdown` consumers. Adding a new score kind is not automatically a breaking change if the new kind's polarity is clearly documented and consumers handle it via explicit dispatch rather than `if kind == "distance"` chains.
  - `ScoredSearchCandidate(doc_id: str, chunk_id: str, text: str, score: float, source_path: str, scores: SearchScoreBreakdown)` dataclass used by private store/reranker trace helpers
  - This module is private package infrastructure, not an eval public API and not a public MCP/REST payload. Production `store.py`, `reranker.py`, and `router.py` may import it without depending on `archon_search.eval`.
- New modules under `packages/archon-search/archon_search/eval/`:
  - `fixtures.py`
    - `EvalDocument(doc_id: str, collection: str, relative_path: str, text: str, metadata: dict[str, str] | None = None)` dataclass
    - `EvalQuery(query_id: str, text: str, collection: str | None = None, routing_bypass: bool = False, metric_scope: Literal["retrieval", "routing"] = "retrieval")` dataclass where `metric_scope` is `"retrieval"` or `"routing"`
      > **Collection constraint**: `collection=None` is valid ONLY when `metric_scope="routing"`. Retrieval queries (`metric_scope="retrieval"`) MUST specify an explicit collection; cross-collection retrieval is Archon-owned and outside v1 scope. The fixture loader must reject `metric_scope="retrieval"` with `collection=None` at load time. Conversely, `metric_scope="routing"` queries MAY specify `collection=None` OR an explicit collection — when a collection is specified on a routing-only query, it is IGNORED during routing evaluation (gold collections are derived from labels only). The fixture loader should emit a warning when `metric_scope="routing"` is paired with an explicit collection, as this is likely a fixture authoring error.
    - `RelevanceLabel(query_id: str, doc_id: str, grade: int = 1)` dataclass
    - `EvalCorpus(documents: list[EvalDocument], queries: list[EvalQuery], labels: list[RelevanceLabel])` dataclass
    - `load_eval_corpus(root: Path) -> EvalCorpus`
    - `build_doc_collection_map(corpus: EvalCorpus) -> dict[str, str]`
      > Runtime document IDs are path-derived values (`source_path`) stored in LanceDB rows. The fixture loader builds an inverse map from each document's `relative_path` (normalized with POSIX separators and no leading slash) to its stable `doc_id`. At eval time, the runner normalizes each result's `source_path` to match this form before lookup. Results whose normalized `source_path` cannot be found in the map fail with an explicit error listing the unmapped path and available fixture paths — they are not silently dropped.
  - `types.py`
    - `EvalSearchResult(doc_id: str, runtime_doc_id: str, chunk_id: str, text: str, score: float, source_path: str, collection: str, scores: SearchScoreBreakdown)` dataclass
    - `QueryEvalTrace(query: EvalQuery, pre_rerank: list[EvalSearchResult], post_rerank: list[EvalSearchResult], router_correct: bool | None, latency_ms: float)` dataclass where `router_correct` is `None` when routing is disabled or the query bypasses routing
    - `EvalMetrics(recall_at_1: float, recall_at_3: float, recall_at_5: float, mrr: float, ndcg_at_5: float, ndcg_at_10: float, reranker_lift: float, routing_accuracy: float | None, latency_p50_ms: float, latency_p95_ms: float)` dataclass
  - `metrics.py`

    - `compute_recall_at_k(traces: list[QueryEvalTrace], labels: dict[str, dict[str, int]], k: int) -> float`
    - `compute_mrr(traces: list[QueryEvalTrace], labels: dict[str, dict[str, int]]) -> float`
    - `compute_ndcg_at_k(traces: list[QueryEvalTrace], labels: dict[str, dict[str, int]], k: int, *, use_prerank: bool = False) -> float`
    - `compute_reranker_lift(traces: list[QueryEvalTrace], labels: dict[str, dict[str, int]], k: int = 10) -> float` — pre-rerank and post-rerank nDCG@k are computed at equal depth (return_depth candidates). The use_prerank=True flag in compute_ndcg_at_k applies to the pre-rerank result list which contains return_depth items.
    - `is_routing_correct(shortlist: list[str], gold_collections: list[str]) -> bool` — pure function used by the eval runner to determine per-query routing correctness; not in `router.py` since `gold_collections` is an eval concept derived from labels
    - `compute_routing_accuracy(traces: list[QueryEvalTrace], routing_contract_enabled: bool) -> float | None` — when `routing_contract_enabled=False`, returns `None` immediately with no trace inspection; simple fraction of non-bypassed queries where `router_correct=True`; when `True` and no non-bypassed routing queries are found, returns `None`
    - `compute_latency_percentiles(latencies_ms: list[float]) -> tuple[float, float]` — empty input (zero queries) returns `(0.0, 0.0)` and emits a report warning; it does not raise
  - `runner.py`
    - `EvalQualityFloors(recall_at_1: float, recall_at_3: float, recall_at_5: float, mrr: float, ndcg_at_5: float, ndcg_at_10: float, routing_accuracy: float | None)` dataclass
    - `EvalLatencyCeilings(latency_p50_ms: float | None = None, latency_p95_ms: float | None = None)` dataclass — latency ceilings are optional; when `None`, that percentile is not gated
    - `EvalThresholds(quality: EvalQualityFloors, latency: EvalLatencyCeilings = field(default_factory=EvalLatencyCeilings), max_floor_drop_without_waiver: float = 0.05)` dataclass — `gating_enabled` is implicit: a latency ceiling gates when its field is not `None`
    - `EvalRuntimeConfig(candidate_depth: int, return_depth: int, metric_depth: int, routing_contract_enabled: bool)` dataclass, where `metric_depth` is the required post-dedupe unique-document metric depth
    - `EvalBaseline(metrics: EvalMetrics, eval_hash: str, runtime_config_hash: str, thresholds_hash: str | None, command: str, waiver_ids: dict[str, str] = field(default_factory=dict))` dataclass — `waiver_ids` maps metric name (e.g., `"ndcg_at_10"`) to a reviewed waiver ID or issue ID when the floor was intentionally set more than `max_floor_drop_without_waiver` below the baseline metric; floor-drop enforcement checks this map before failing
      > Transition rule: when `baseline.thresholds_hash` is `None` (baseline was created during calibration-only mode) and a `thresholds.toml` now exists, the staleness check must treat this as a staleness failure and require a baseline refresh. A calibration-mode baseline cannot serve as the approved baseline for a gated eval run.
    - `EvalReport(metrics: EvalMetrics, thresholds: EvalThresholds | None, baseline: EvalBaseline | None, query_count: int, document_count: int, routing_disabled_queries: int, routing_bypassed_queries: int, traces: list[QueryEvalTrace], notes: list[str])` dataclass — `routing_disabled_queries`: count of queries not scored for routing because `routing_contract_enabled=False`; `routing_bypassed_queries`: count of queries that bypassed routing via `routing_bypass=True` while routing was enabled
    - `load_runtime_config(config_path: Path) -> EvalRuntimeConfig`
    - `load_thresholds(config_path: Path) -> EvalThresholds`
    - `load_baseline(baseline_path: Path) -> EvalBaseline`
    - `async run_eval_suite(corpus_root: Path, runtime_config_path: Path, thresholds_path: Path | None = None, baseline_path: Path | None = None) -> EvalReport`
    - `assert_thresholds(report: EvalReport) -> None`
    - `render_report(report: EvalReport) -> str`
  - `backends.py`
    - deterministic eval-only embedder and reranker backends that are query-sensitive, corpus-aware, and label-blind
    - backends may use query text and document text by default; they must not read `labels.jsonl`, query IDs, fixture doc IDs as relevance hints, fixture paths, fixture metadata, or gold collections
  - `_tracing.py`
    - private eval-only trace collection that copies or converts production results before reranking so pre-rerank fused scores are not mutated by reranker execution
- Search package changes outside `eval/` are allowed only for a private diagnostic trace contract used by eval and debugging. They are not public API additions and must stay out of normal response payloads:
  - `packages/archon-search/archon_search/_types.py`
    - keep the normal public `SearchResult` contract stable; do not add eval-only score fields to the public response dataclass
    - if a shared internal candidate type is needed, keep it private/internal and convert to public `SearchResult` at the package boundary
  - `packages/archon-search/archon_search/server.py` or the package's public serialization boundary
    - MCP `search` result dictionaries keep the public response contract: `doc_id`, `chunk_id`, `text`, `score`, `source_path` (five-field shape, confirmed in `archon_search/_types.py::SearchResult`). Tests must assert this documented public schema.
    - MCP `search_with_context` keeps the documented top-level public contract, and nested result/chunk payloads must not expose eval-only provenance
    - private trace helpers must not be exported from the package's public `__init__` or advertised as public MCP/REST fields
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
    - eval trace integration: `search()` accepts an optional `_trace_sink: "TraceCollector | None" = None` keyword-only parameter (no-op when `None`); this is the ONLY accepted trace injection mechanism — monkey-patching, context vars, and parallel pipeline implementations are forbidden
  - `packages/archon-search/archon_search/router.py`
    - Routing is Search-owned via `POST /route`. No eval-specific helpers are added to `router.py`; production routing logic is not aware of eval semantics.
  - Cross-collection merge remains in Archon's `SearchContextProvider` — it is outside FEAT-039 v1 scope. Eval queries target a single collection for retrieval metrics; routing accuracy is measured via `POST /route` shortlist intersection only.
- Fixture files under `packages/archon-search/tests/eval/`:
  - `documents.jsonl` manifest with `doc_id`, `collection`, and `relative_path`
  - `corpus/` directory with per-collection fixture documents
  - `queries.jsonl`
  - `labels.jsonl`
  - optional `routing/collections.jsonl` when routing is a Search-owned contract — one row per routable collection with `name` and optional `description`; no centroid or embedding model fields in v1
  - `runtime.toml`
  - `thresholds.toml` added in Task 4.3 before PR/release gating, not required for report-only calibration
- Required baseline artifact under `packages/archon-search/tests/eval/baselines/`:
  - first accepted rendered report (`baseline.md`) and machine-readable metadata (`baseline.json`), committed alongside threshold values before PR/release gating is enabled
  - `baseline.json` records metrics, eval hash, runtime config hash, thresholds hash when thresholds exist, eval command, and optional `waiver_ids` dict mapping metric names to reviewed waiver/issue IDs for intentionally-lowered floors
  - the eval hash covers every eval-determinism-defining input: `documents.jsonl`, corpus files, `queries.jsonl`, `labels.jsonl`, optional `routing/collections.jsonl`, and `eval/backends.py` (because backend scoring changes alter raw ranking inputs to all benchmark metrics)
  - **Intentional exclusion**: `metrics.py` and `runner.py` are NOT part of the eval hash. Metric computation algorithm changes (e.g., bug fixes to nDCG) are expected to be caught by unit tests in `test_metrics.py`; they should require a manual baseline refresh via a new calibration run rather than automatic staleness invalidation. A comment in `baseline.json` `command` field should record the metrics.py version or commit hash if a metric algorithm change necessitates a refresh.
  - eval hash, runtime config hash, and thresholds hash are staleness gates; any change to eval-determinism-defining inputs automatically invalidates the stored baseline and requires a new calibration run
- Data flow:
  - fixture loader reads `documents.jsonl`, corpus files, queries, and labels
  - eval fixture ingests corpus into a module-scoped temporary LanceDB store with deterministic eval backends
  - runner maps path-derived runtime document IDs back to stable fixture document IDs before metrics are computed
  - runner executes each query through Search-owned routing only when the package-owned routing contract exists; otherwise routing metrics are `None` and explicitly skipped
  - runner executes each query through the same package service/application query path used by production search, with trace collection attached to the shared implementation. Private trace helpers may expose intermediate candidates, but they must not reimplement a parallel ranking path that can diverge from the maintained search method.
  - runner uses `candidate_depth` raw chunk retrieval, `return_depth` post-rerank chunk output, and `metric_depth` post-dedupe unique-document scoring
  - runner deduplicates chunk results to unique fixture document IDs and fails with an under-depth diagnostic when `metric_depth` unique documents cannot be produced from an otherwise sufficient corpus
  - metric functions deduplicate chunk results to document rankings before document-level scoring
  - metrics module computes aggregate scores
  - threshold assertion converts regressions into pytest failures only when thresholds are loaded; report-only calibration renders metrics and baseline metadata without asserting floors
- New package pytest config:
  - package `pyproject.toml` or equivalent sets `testpaths = ["tests"]` under `packages/archon-search/`
  - coverage targets `archon_search`, not root `archon`
  - coverage floor is explicit and defaults to the project floor (`85%`) unless the package sets a different floor in `pyproject.toml`. If split CI invocations are used, the floor must be applied only after coverage data is combined, not from default addopts in each partial invocation.
  - marker registration includes `eval`
  - default package addopts include strict marker/config behavior and exclude slow eval tests, for example `--strict-markers --strict-config -m "not live and not eval"`
- New pytest marker:
  - `eval`: slow deterministic full-corpus benchmark slice excluded from default runs
  - pure fixture-loader and metric unit tests remain unmarked unless they run the full corpus
- Schema for `packages/archon-search/tests/eval/runtime.toml`:
  - `[search].candidate_depth` is an integer raw chunk candidate depth and must be greater than `return_depth`
  - `[search].return_depth` is an integer raw post-rerank chunk depth and must be at least `metric_depth`
  - `[search].metric_depth` is an integer post-dedupe unique-document metric depth and must be at least `10`
  - `[routing].contract_enabled` is a boolean and means a Search-owned routing contract exists; it is not just an eval toggle
- Example `runtime.toml`:
  ```toml
  [search]
  candidate_depth = 40
  return_depth = 20
  metric_depth = 10

  [routing]
  contract_enabled = true  # set to true after Phase 0 confirms routing is Search-owned (confirmed by FEAT-038)
  ```
- Schema for `packages/archon-search/tests/eval/thresholds.toml`:
  - `[quality_floors].recall_at_1` is a float
  - `[quality_floors].recall_at_3` is a float
  - `[quality_floors].recall_at_5` is a float
  - `[quality_floors].mrr` is a float
  - `[quality_floors].ndcg_at_5` is a float
  - `[quality_floors].ndcg_at_10` is a float
  - `[quality_floors].routing_accuracy` is required when `[routing].contract_enabled = true`; omit only when routing is not Search-owned and therefore not gated
  - `[policy].max_floor_drop_without_waiver` is an optional float, default `0.05`; floor reductions larger than this require a reviewed waiver or issue ID in baseline metadata
  - `[latency_ceilings].p50_ms` is an optional float; when present, latency p50 gating is enabled for this ceiling
  - `[latency_ceilings].p95_ms` is an optional float; when present, latency p95 gating is enabled for this ceiling
- Example `thresholds.toml` (illustrative only; committed values must come from the first accepted measured baseline):
  ```toml
  [quality_floors]
  recall_at_1 = 0.60
  recall_at_3 = 0.75
  recall_at_5 = 0.85
  mrr = 0.70
  ndcg_at_5 = 0.75
  ndcg_at_10 = 0.80
  routing_accuracy = 0.70  # required when [routing].contract_enabled = true

  [policy]
  max_floor_drop_without_waiver = 0.05

  # Optional: enable latency gating by uncommenting and setting ceilings
  # [latency_ceilings]
  # p50_ms = 500.0
  # p95_ms = 1000.0
  ```
- Default policy:
  - quality thresholds are copied from the first accepted measured baseline after FEAT-038, not guessed in advance
  - the first eval run is calibration-only and may omit `thresholds.toml`; PR/release gating starts only after `baseline.json`, `baseline.md`, and `thresholds.toml` are committed together
  - reranker lift is report-only in v1; final post-rerank quality metrics are the primary gates
  - latency values are always computed and included in the report; latency gating is optional and enabled only when explicit ceiling values are set in `[latency_ceilings]`; in v1 the committed `thresholds.toml` leaves ceiling fields absent by default, making latency report-only unless a maintainer explicitly configures ceilings
  - threshold lowering must include a rationale tied to a corpus change or intentional algorithm trade-off, and the previous rendered report must remain reviewable in history
  - floor reductions larger than `[policy].max_floor_drop_without_waiver` fail unless baseline metadata names a reviewed waiver or issue ID
  - when `[routing].contract_enabled = true`, `routing_accuracy` must be numeric, and a committed routing floor is required
  - a configured routing floor plus `routing_accuracy = None` fails with an actionable configuration message; an omitted routing floor plus `None` metric skips routing gating only when routing is explicitly not Search-owned

---

## Tests

- **`test_load_eval_corpus_reads_documents_queries_and_labels`** (unit): fixture loader returns the expected document, query, and label counts
- **`test_load_eval_corpus_rejects_unknown_label_doc_id`** (unit): labels referencing missing documents fail fast
- **`test_load_eval_corpus_rejects_query_without_positive_label`** (unit): every scored query has at least one `grade > 0` label
- **`test_load_eval_corpus_rejects_positive_label_outside_query_collection`** (unit): single-collection retrieval queries cannot label unreachable documents as positives
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
- **`test_compute_ndcg_at_k_uses_documented_gain_discount_and_top_k_idcg`** (unit): a perfect top-k ranking can reach `1.0` even when more than k documents are relevant
- **`test_metric_aggregation_macro_averages_queries`** (unit): multi-label queries use the documented per-query macro-average semantics
- **`test_metrics_dedupe_chunks_to_document_rankings`** (unit): multiple chunks from one relevant document do not inflate document-level metrics
- **`test_metrics_reject_under_depth_after_chunk_dedupe`** (unit): duplicate chunks cannot make `nDCG@10` silently score fewer than 10 unique documents
- **`test_compute_reranker_lift`** (unit): post-rerank nDCG@10 minus pre-rerank nDCG@10 at equal depth (return_depth) correctly isolates reranker quality from truncation
- **`test_compute_routing_accuracy_skips_bypassed_queries`** (unit): bypassed queries are excluded from routing accuracy
- **`test_compute_routing_accuracy_returns_none_when_not_enabled`** (unit): absent routing contract is explicit
- **`test_compute_latency_percentiles`** (unit): percentile math returns stable p50 and p95 values
- **`test_compute_latency_percentiles_nearest_rank_small_n`** (unit): small samples use the documented nearest-rank percentile method
- **`test_eval_embedder_changes_vector_for_query_terms`** (unit): different query terms produce different deterministic vectors
- **`test_eval_reranker_scores_from_query_and_document_text`** (unit): reranker scores are content-sensitive, not label-driven
- **`test_eval_backends_do_not_receive_labels_query_ids_doc_ids_or_gold_ids`** (unit): deterministic backends are label-blind and cannot score from benchmark identifiers
- **`test_eval_backends_do_not_receive_paths_or_fixture_metadata_by_default`** (unit): deterministic backends cannot rank from `relative_path`, `source_path`, fixture IDs, labels, or metadata fields that encode relevance hints
- **`test_hybrid_search_trace_exposes_score_breakdown`** (integration): private eval trace returns vector rank, vector raw score/distance, FTS rank, FTS raw score, raw-score kind, and RRF fields needed by eval
- **`test_mcp_search_response_schema_matches_public_contract_without_eval_provenance`** (integration): normal public search payloads match the Search package public schema and exclude eval-only score provenance
- **`test_mcp_search_with_context_response_schema_matches_public_contract_without_eval_provenance`** (integration): context payloads match the Search package public schema and do not leak eval-only provenance
- **`test_rerank_trace_preserves_prerank_scores_and_adds_reranker_score`** (integration): reranking does not discard fused-score provenance or mutate pre-rerank trace objects
- **`test_eval_trace_returns_pre_and_post_rerank_results`** (integration): eval trace path returns both result lists in a single query run
- **`test_eval_runner_executes_miniature_corpus`** (integration): runner ingests a miniature fixture and produces one trace per query
- **`test_eval_runner_uses_service_query_path_with_trace_enabled`** (integration): eval traces are collected from the same query path used by normal package search, not a parallel implementation
- **`test_eval_suite_is_deterministic_except_latency`** (e2e): two fresh-store runs produce identical quality metrics and rankings
- **`test_eval_runner_skips_routing_metric_without_search_owned_routing_contract`** (integration): routing metric is not silently fabricated
- **`test_eval_runner_excludes_bypassed_queries_from_routing_accuracy`** (integration): bypassed queries increment skip accounting
- **`test_eval_runner_rejects_collectionless_query_without_routing_contract`** (integration): `collection=None` cannot be executed by using labels to choose a collection
- **`test_run_eval_suite_report_only_without_thresholds`** (integration): calibration runs can render reports before thresholds exist
- **`test_assert_thresholds_requires_thresholds_for_gating`** (integration): PR/release gating fails clearly when thresholds are absent
- **`test_assert_thresholds_fails_when_required_metric_is_none`** (integration): configured floors cannot silently pass with missing metrics
- **`test_assert_thresholds_reports_quality_floor_regressions`** (integration): failure message includes metric deltas against baseline and failing quality floors
- **`test_assert_thresholds_reports_latency_ceiling_regressions_when_enabled`** (integration): latency checks use ceiling semantics only when gating is enabled
- **`test_eval_pytest_marker_excluded_from_default_run`** (integration): default test selection excludes `eval`
- **`test_baseline_metadata_hashes_match_benchmark_inputs`** (integration): baseline metadata matches eval-determinism-defining inputs (documents, corpus, queries, labels, optional routing/collections.jsonl, backends.py), runtime config, and thresholds
- **`test_ci_clean_install_includes_eval_dependencies_and_pytest_plugins`** (integration): CI dependency command installs Search/eval runtime dependencies and pytest coverage plugins in a clean environment
- **`test_eval_suite_report_only_smoke`** (e2e): full eval slice can render a calibration report before thresholds exist
- **`test_eval_suite_gated_smoke`** (e2e): full eval slice asserts thresholds after baseline and thresholds exist
- **`test_release_gate_includes_eval_slice`** (integration): the actual executable package release gate runs the eval slice
- **`test_ci_gates_fail_when_eval_collection_is_empty_or_any_eval_is_skipped_or_xfailed`** (integration): PR/release gates cannot pass when eval tests are uncollected, skipped, xfailed, or xpassed without an explicit reviewed allowlist
- **`test_ci_gates_run_default_suite_and_full_eval_slice`** (integration): PR/release gates run the default package suite as well as the complete eval marker slice
- **`test_pr_gate_runs_eval_for_retrieval_dependency_and_eval_changes`** (integration): path-filtered PR CI selects the complete gated eval slice for retrieval, reranking, Search-owned routing, eval, threshold, baseline, dependency manifest, lockfile, eval extra/dependency group, pytest, and CI/release gate changes
- **`test_skip_xfail_allowlist_requires_exact_unexpired_reviewed_nodeids`** (integration): skip/xfail/xpass exceptions cannot be broad, stale, or unreviewed

---

## Documentation update
- [ ] `Documentation/Backlog/FEAT-037-search-world-class-roadmap.md`, section `Priority 0: Product Separation / 4. Build an evaluation harness and data-collection loop`, path: `Documentation/Backlog/FEAT-037-search-world-class-roadmap.md`
- [ ] `Documentation/Architecture/180_search_architecture.md`, section `Search Architecture`, path: `Documentation/Architecture/180_search_architecture.md`
- [ ] `Documentation/990_documentation_index_and_contribution_guide.md`, section `Backlog`, path: `Documentation/990_documentation_index_and_contribution_guide.md` if FEAT-039 creates or updates follow-up backlog artifacts
- [ ] `packages/archon-search/README.md`, section `Evaluation`, path: `packages/archon-search/README.md`
- [ ] `packages/archon-search/tests/eval/README.md`, section `Fixture format and threshold maintenance`, path: `packages/archon-search/tests/eval/README.md`
- Search is in the monorepo under `packages/archon-search/`. Package release CI validates only package-local documentation; Archon roadmap and architecture documentation updates are validated in the Archon repo workflow or Phase 0 doc checklist, not by package-local tests that cannot see Archon `Documentation/`.

---

## Task breakdown

### Phase 0 — FEAT-038 prerequisite gate
> **Releasable**: when Task 0.2 is complete; FEAT-039 has a verified package boundary and executable release/test context before implementation starts
> **Global dependency**: every package-path implementation task in Phases 1-5 depends on Task 0.2, even when the local task dependencies below list only the nearest technical predecessor.

#### Task 0.1 — Link accepted Search package contract
- [x] **File**: `Documentation/Backlog/FEAT-039-search-evaluation-harness-plan-codex.md`
- **Depends on**: FEAT-038 (product separation, roadmap items 1–3 — all confirmed complete)
- **Description**:
  - Roadmap items 1 (product separation), 2 (canonical service contract + job model), and 3 (real metadata schema) are all confirmed delivered by FEAT-038. Link the accepted artifacts from `Documentation/Completed/FEAT-038-search-product-separation.md` in the Phase 0 checklist.
  - Confirm the exact package root (`packages/archon-search/`), import path (`archon_search`), metadata schema/versioning rules, and public `SearchResult` or successor response contract as the frozen eval semantics baseline.
  - Routing is Search-owned via `POST /route` (`archon_search/server/routes_route.py`). Inline the typed route API contract: request inputs (`RouteRequest`), outputs (`RouteResponse`), confidence gate semantics, and pinned/available-slot ownership. Set `[routing].contract_enabled = true` in the committed eval runtime config.
  - If FEAT-039 creates or updates follow-up backlog artifacts, update `Documentation/990_documentation_index_and_contribution_guide.md` in the same change.
- **Verified contract** (recorded 2026-05-09):
  - **FEAT-038 artifact**: `Documentation/Completed/FEAT-038-search-product-separation.md` (all acceptance criteria checked — package extracted, import boundary enforced, canonical types defined).
  - **Package root**: `packages/archon-search/` (confirmed in monorepo).
  - **Import path**: `archon_search` (confirmed from `pyproject.toml` package name `archon-search`, directory `archon_search/`).
  - **Public `SearchResult` contract** (five-field shape, frozen as eval semantics baseline — `archon_search/_types.py`): `doc_id: str`, `chunk_id: str`, `text: str`, `score: float`, `source_path: str`. No eval-only score provenance fields in the public response. MCP `search` payload keys match this five-field shape.
  - **Metadata schema** (`ChunkRecord` in `archon_search/_types.py`): filterable `metadata: dict[str, str]`, ranking `custom_score: float | None`, audit `ingested_by: str`, audit `updated_at: str`. Schema evolution policy is additive-only.
  - **Routing is Search-owned** via `POST /route` (`archon_search/server/routes_route.py`). Typed route API contract:
    - `RouteRequest`: `query: str`, `slots: int | None = None` (available collection slots; defaults to `config.routing_shortlist_size`).
    - `RouteResponse`: `pre_context: str | None`, `pinned_names: list[str]`, `routable_names: list[str]`, `decomposer_invoked: bool`.
    - Confidence gate: controlled by `config.routing_confidence_threshold` inside `MultiCollectionRouter`. Pinned collections are always included; routable collections are shortlisted by embedding similarity above the threshold.
    - Pinned/available-slot ownership: `pinned_names` is populated from `config.pinned_collections`; `slots` (or `config.routing_shortlist_size`) controls how many routable collections fill the remaining capacity.
  - **`[routing].contract_enabled = true`** set in `packages/archon-search/tests/eval/runtime.toml`.
  - **FEAT-039b** (online data-collection loop) must be created as a tracked backlog item before FEAT-037 roadmap item 4 is fully closed. FEAT-039b covers live query logging, user feedback capture, and privacy policy for online data collection — all deferred from this plan.
  - **Archon doc validation owner**: when `archon-search` is extracted to a standalone package, Archon roadmap and architecture documentation updates are validated in the Archon repo workflow or Phase 0 doc checklist — not by package-local tests that cannot see Archon `Documentation/`.
- **Releasable**: FEAT-039 no longer depends on an implicit future package shape
> **Note**: Phase 0 contract tests validate process compliance — they assert that the plan was followed, that artifacts are discoverable, and that required decisions are recorded. They are NOT behavioral tests. Phase 1+ tests provide behavioral regression safety. Phase 0 tests fail fast if an implementer skips the prerequisite steps but cannot catch runtime bugs.
- **Tests (TDD)** — `packages/archon-search/tests/eval/test_phase0_contract.py` after extraction:
  - Integration: `test_phase0_contract_links_existing_feat_038_artifact_or_inline_contract` — the implementation plan names the accepted contract source
  - Integration: `test_phase0_contract_matches_package_paths` — documented package root and import path match the extracted package
  - Integration: `test_phase0_contract_names_canonical_service_and_metadata_contracts` — eval fixture/result semantics are grounded in the accepted Search product contract
  - Integration: `test_phase0_contract_records_routing_ownership` — routing metrics cannot be enabled without a Search-owned routing contract
  - Integration: `test_phase0_contract_requires_typed_route_api_when_routing_owned` — routing accuracy cannot be gated from decomposer side effects or undocumented trace strings
  - Integration: `test_phase0_contract_links_archon_routing_eval_followup_when_needed` — non-Search-owned routing cannot disappear from the roadmap
  - Integration: `test_phase0_contract_records_archon_doc_validation_owner_when_extracted` — split-repo package CI has a named Archon-doc validation owner
  - Integration: `test_phase0_contract_updates_documentation_index_for_followup_backlog_items` — new or changed backlog follow-ups remain discoverable

#### Task 0.2 — Verify executable package test and release context
- [x] **Files**: accepted package release entrypoint and package pytest config
- **Depends on**: Task 0.1
- **Description**:
  - Confirm the command that installs Search/eval dependencies in CI, including package-local eval extras or dependency groups and pytest plugins required by the coverage gate.
  - Confirm the clean-environment install smoke command that proves those dependencies are sufficient outside the developer's already-synced checkout.
  - Confirm the command that runs tests under package pytest config, either by `cd packages/archon-search` or by passing the package config explicitly with `pytest -c packages/archon-search/pyproject.toml`.
  - Confirm the release entrypoint that can fail before tag, publish, or release creation.
  - Confirm the Search-specific path-filtered PR workflow and the file patterns that select the complete gated eval slice. If no executable PR workflow exists, record that FEAT-039 is partial and cannot close the brief until the linked follow-up adds PR gating.
  - Confirm where the release gate runs relative to the executable release script's first mutation, metadata rewrite, commit, tag, publish, or release creation; the eval gate must run before the first release mutation.
- **Releasable**: all later CI-gate tasks target executable files, not assumed paths
- **Tests (TDD)** — `packages/archon-search/tests/eval/test_phase0_contract.py` after extraction:
  - Integration: `test_phase0_contract_names_dependency_install_command` — Search/eval dependencies are not assumed
  - Integration: `test_phase0_contract_names_clean_install_smoke_command` — dependency validation is executable in a clean environment
  - Integration: `test_phase0_contract_names_package_pytest_config_command` — release tests cannot accidentally use root Archon pytest config
  - Integration: `test_phase0_contract_names_release_entrypoint` — release gate target is executable
  - Integration: `test_phase0_contract_records_executable_pr_eval_gate` — FEAT-039 cannot be marked complete with release-only gating
  - Integration: `test_phase0_contract_places_eval_before_first_release_mutation` — release-gate placement is not after a metadata commit or publish step

### Phase 1 — Eval domain model and fixture contract
> **Releasable**: when Tasks 1.3 and 1.5 are complete; fixture files and runtime config can be loaded and validated end-to-end, thresholds have a schema, but no search execution happens yet

#### Task 1.1 — Eval fixture dataclasses
- [x] **File**: `packages/archon-search/archon_search/eval/fixtures.py`
- **Depends on**: Task 0.2
- **Description**:
  - Add `EvalDocument`, `EvalQuery`, `RelevanceLabel`, and `EvalCorpus` dataclasses with the signatures listed in the Architecture section.
  - `EvalDocument.doc_id` is the stable fixture ID used by labels; `relative_path` points to the file under `corpus/`.
  - `EvalQuery.collection` is `None` only when `metric_scope="routing"` — these queries exercise routing only and do not execute retrieval. Retrieval queries (`metric_scope="retrieval"`) must always name an explicit collection regardless of routing settings; cross-collection retrieval is out of v1 scope.
  - `EvalQuery.metric_scope = "retrieval"` queries are scored by recall/MRR/nDCG/reranker-lift and must name an explicit retrieval collection. Cross-collection merge remains Archon-owned and is outside v1 scope.
  - `EvalQuery.metric_scope = "routing"` queries are scored only by routing accuracy metrics (shortlist intersection with gold collection via `POST /route`). They do not contribute to retrieval metrics.
  - `EvalQuery.metric_scope` must be validated as one of `"retrieval"` or `"routing"` at construction time (use `Literal["retrieval", "routing"]` or a `__post_init__` validator). A typo in a fixture file should raise a clear `ValueError`, not silently exclude the query from all metrics.
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
  - Unit: `test_eval_query_metric_scope_separates_retrieval_from_routing` — routing-only queries cannot contribute to retrieval metrics unless final selection is Search-owned
  - Unit: `test_eval_query_rejects_invalid_metric_scope` — unknown metric_scope values raise ValueError at construction
  - Checkpoint: `cd packages/archon-search && uv run pytest --no-cov tests/eval/test_fixtures.py -k "grade or collection" -v`

#### Task 1.2 — Fixture loader and file-format parser
- [x] **File**: `packages/archon-search/archon_search/eval/fixtures.py`
- **Depends on**: Task 1.1
- **Description**:
  - Implement `load_eval_corpus(root: Path) -> EvalCorpus`.
  - Read `documents.jsonl`; each row must contain `doc_id`, `collection`, and `relative_path`.
  - Read document text from `root / "corpus" / relative_path`; do not infer stable document IDs from temporary filesystem paths.
  - Read `queries.jsonl` and `labels.jsonl`.
  - If `root / "routing"` exists and `routing/collections.jsonl` is present, read it; collection rows contain `name` and optional `description` (no centroid or embedding model fields in v1).
  - Accept binary or graded labels; normalize missing grade to `1`.
  - Fail fast on malformed JSONL, duplicate document IDs, duplicate query IDs, duplicate `relative_path` values, missing manifest files, orphan corpus files, invalid collection names, absolute paths, `..` path traversal, unknown `doc_id`, labels for unknown queries, queries with no labels, or queries with only `grade = 0` labels.
  - Add a helper that maps runtime `source_path` / path-derived document IDs back to stable fixture `doc_id` values before metrics run.
- **Releasable**: a committed corpus can be loaded into validated Python objects
- **Tests (TDD)** — `packages/archon-search/tests/eval/test_fixtures.py`:
  - Unit: `test_load_eval_corpus_reads_documents_queries_and_labels` — valid tree loads successfully
  - Unit: `test_load_eval_corpus_rejects_unknown_label_doc_id` — missing doc reference raises `ValueError`
  - Unit: `test_load_eval_corpus_rejects_duplicate_document_ids` — duplicate manifest IDs raise `ValueError`
  - Unit: `test_load_eval_corpus_rejects_duplicate_query_ids` — duplicate query IDs raise `ValueError`
  - Unit: `test_load_eval_corpus_rejects_unknown_query_id_in_labels` — orphan label rows raise `ValueError`
  - Unit: `test_load_eval_corpus_rejects_query_without_positive_label` — a query with no `grade > 0` labels raises `ValueError`
  - Unit: `test_load_eval_corpus_rejects_positive_label_outside_query_collection` — single-collection retrieval queries cannot label unreachable documents
  - Unit: `test_load_eval_corpus_rejects_missing_manifest_file` — manifest rows pointing at absent corpus files raise `ValueError`
  - Unit: `test_load_eval_corpus_rejects_invalid_collection_name` — fixture collection names follow Search collection rules
  - Unit: `test_load_eval_corpus_rejects_path_escape` — absolute paths and `..` traversal are rejected
  - Unit: `test_load_eval_corpus_rejects_duplicate_relative_path` — one corpus file cannot represent multiple fixture docs
  - Unit: `test_load_eval_corpus_rejects_orphan_corpus_files` — corpus files present under `corpus/` but not referenced by any `documents.jsonl` entry raise `ValueError`
  - Unit: `test_runtime_doc_ids_map_to_fixture_doc_ids` — path-derived runtime IDs are converted back to stable fixture IDs
  - Unit: `test_build_doc_collection_map_returns_correct_mapping` — function returns dict mapping each doc_id to its collection name
  - Unit: `test_build_doc_collection_map_with_multiple_collections` — documents across different collections are mapped correctly
  - Unit: `test_build_doc_collection_map_with_empty_corpus` — empty corpus returns empty dict
  - Checkpoint: `cd packages/archon-search && uv run pytest --no-cov tests/eval/test_fixtures.py -k "load_eval_corpus" -v`

#### Task 1.3 — Committed synthetic corpus and labels
- [x] **File**: `packages/archon-search/tests/eval/documents.jsonl`
- **Depends on**: Task 1.2
- **Description**:
  - Add the committed manifest `documents.jsonl` with explicit stable `doc_id`, `collection`, and `relative_path` fields.
  - Add the committed corpus tree under `packages/archon-search/tests/eval/corpus/`.
  - Create 50-100 concise documents across 2-3 collections representing code, documentation/prose, and mixed content.
  - Add 25-30 benchmark queries in `queries.jsonl`.
  - Add corresponding `labels.jsonl` with at least one `grade > 0` relevant document per query.
  - For each single-collection retrieval query, ensure all positive labels point to documents in the query's searched collection; cross-collection positives are outside v1 scope (cross-collection merge remains Archon-owned).
  - Use document-level relevance labels; metric functions deduplicate chunk results by `doc_id` before scoring.
- **Releasable**: the package contains a deterministic benchmark dataset that can be versioned and reviewed
- **Tests (TDD)** — `packages/archon-search/tests/eval/test_corpus_contract.py`:
  - Unit: `test_eval_corpus_document_count_range` — corpus contains 50-100 documents
  - Unit: `test_eval_corpus_query_count_range` — corpus contains 25-30 queries
  - Unit: `test_every_query_has_positive_relevance_label` — every query has at least one `grade > 0` label
  - Unit: `test_positive_relevance_labels_are_reachable_from_query_collection` — fixture labels cannot make a query impossible to satisfy
  - Unit: `test_collections_cover_multiple_domains` — at least 2 distinct collections exist
  - Unit: `test_manifest_doc_ids_are_stable_and_unique` — every labelable document has a stable manifest ID
  - Checkpoint: `cd packages/archon-search && uv run pytest --no-cov tests/eval/test_corpus_contract.py -v`

#### Task 1.4 — Threshold config contract
- [x] **File**: `packages/archon-search/archon_search/eval/runner.py`
- **Depends on**: Task 1.2
- **Description**:
  - Add `EvalQualityFloors`, `EvalLatencyCeilings`, `EvalThresholds`, and `load_thresholds(config_path: Path) -> EvalThresholds`.
  - Parse `thresholds.toml`.
  - Require quality floors under `[quality_floors]` only when `thresholds.toml` exists and gating mode is enabled; report-only calibration must be able to run before thresholds exist.
  - Omit `routing_accuracy` only when routing is intentionally not gated because the Search-owned routing contract is absent.
  - `load_thresholds()` validates TOML shape and optional routing floor type only. The cross-file rule that `[routing].contract_enabled = true` requires a committed `routing_accuracy` floor belongs in runner validation after `runtime.toml` and `thresholds.toml` are both loaded.
  - `load_thresholds` parses the optional `[latency_ceilings]` section into `EvalLatencyCeilings` (with `None` fields when absent); latency ceiling enforcement is implicit: a ceiling gates only when its field is not `None`. In v1, the committed `thresholds.toml` leaves both ceiling fields absent by default, making latency report-only unless explicitly configured. Latency values (`latency_p50_ms`, `latency_p95_ms`) are always computed and reported.
  - Parse `[policy].max_floor_drop_without_waiver` and default it to `0.05`.
  - Reject missing required quality keys and malformed threshold types.
- **Releasable**: eval runs can be compared against committed floor values after calibration, while first-run calibration remains possible
- **Tests (TDD)** — `packages/archon-search/tests/eval/test_runner.py`:
  - Unit: `test_load_thresholds_reads_all_metrics` — valid TOML parses into `EvalThresholds`
  - Unit: `test_load_thresholds_allows_omitted_routing_accuracy` — omitted optional routing floor accepted
  - Unit: `test_load_thresholds_accepts_optional_routing_floor_shape` — parser validates optional routing floor type without needing runtime config
  - Unit: `test_load_thresholds_rejects_missing_metric` — incomplete config raises `ValueError`
  - Unit: `test_load_thresholds_reads_floor_drop_policy` — floor-drop policy parses and defaults correctly
  - Unit: `test_load_thresholds_rejects_malformed_toml_syntax` — TOML parse errors raise a clear `ValueError`, not `KeyError` or internal exceptions
  - Unit: `test_load_thresholds_rejects_wrong_type_for_routing_floor` — `routing_accuracy = "high"` (string instead of float) raises `ValueError`
  - Checkpoint: `cd packages/archon-search && uv run pytest --no-cov tests/eval/test_runner.py -k "threshold" -v`

#### Task 1.5 — Eval runtime config contract
- [x] **Files**: `packages/archon-search/archon_search/eval/runner.py`, `packages/archon-search/tests/eval/runtime.toml`
- **Depends on**: Task 1.4
- **Description**:
  - Add `EvalRuntimeConfig` and `load_runtime_config(config_path: Path) -> EvalRuntimeConfig`.
  - Parse `runtime.toml` separately from `thresholds.toml`.
  - Add a committed `tests/eval/runtime.toml` with `[search]` depth settings and `[routing]` ownership settings. Set `[routing].contract_enabled = true` — Phase 0 (Task 0.1) has already confirmed routing is Search-owned via `POST /route` in `archon_search/server/routes_route.py`.
  - Require `metric_depth >= 10` because `nDCG@10` is part of the committed metric set.
  - Require `return_depth >= metric_depth` so post-rerank raw chunk output can produce the required unique-document metric depth after dedupe.
  - Require `candidate_depth > return_depth` so reranking has a candidate pool to improve.
  - When `routing_contract_enabled` is true, require that `routing_accuracy` is a numeric value in the eval report.
- **Releasable**: eval execution has deterministic, metric-compatible runtime settings
- **Tests (TDD)** — `packages/archon-search/tests/eval/test_runner.py`:
  - Unit: `test_load_runtime_config_reads_search_depths` — valid TOML parses into `EvalRuntimeConfig`
  - Unit: `test_committed_runtime_toml_exists_and_loads` — the package contains a maintained eval runtime config
  - Unit: `test_committed_runtime_toml_uses_eval_depth_names` — config keys are `candidate_depth`, `return_depth`, and `metric_depth`, not production `top_k` aliases
  - Unit: `test_load_runtime_config_rejects_metric_depth_below_metric_k` — `metric_depth < 10` raises `ValueError`
  - Unit: `test_load_runtime_config_rejects_return_depth_below_metric_depth` — return depth must support metric depth
  - Unit: `test_load_runtime_config_rejects_candidate_depth_not_greater_than_return_depth` — `candidate_depth == return_depth` and `candidate_depth < return_depth` both raise `ValueError`; only `candidate_depth > return_depth` is valid
  - Integration: `test_runner_requires_routing_floor_when_routing_contract_enabled` — cross-file runtime/threshold validation gates Search-owned routing accuracy
  - Unit: `test_load_runtime_config_rejects_malformed_toml_syntax` — TOML parse errors raise a clear `ValueError`, not `KeyError` or internal exceptions
  - Unit: `test_load_runtime_config_rejects_missing_search_table` — missing `[search]` section raises `ValueError`
  - Unit: `test_load_runtime_config_rejects_wrong_type_for_depth_field` — non-integer value for `candidate_depth` raises `ValueError`

  - Checkpoint: `cd packages/archon-search && uv run pytest --no-cov tests/eval/test_runner.py -k "runtime_config" -v`

### Phase 2 — Score decomposition in the search stack
> **Releasable**: when Task 2.5 is complete; search internals expose pre-rerank and reranker scoring detail needed by the harness, and eval backends produce deterministic ranking signal

#### Task 2.1 — Score breakdown and trace types
- [x] **Files**: `packages/archon-search/archon_search/_diagnostics.py`, `packages/archon-search/archon_search/eval/types.py`
- **Depends on**: Task 0.2
- **Description**:
  - Add private production-observable diagnostic dataclasses in `_diagnostics.py`: `SearchScoreBreakdown` and `ScoredSearchCandidate`.
  - Add eval-only dataclasses in `eval/types.py`: `EvalSearchResult`, `QueryEvalTrace`, and `EvalMetrics`.
  - Keep `EvalSearchResult` separate from the package’s normal public `SearchResult`; eval score provenance must not widen public API payloads.
  - `collection` must be explicit on eval results so cross-collection traces do not rely on path parsing.
  - `SearchScoreBreakdown` must include rank fields because the current hybrid algorithm is rank-based RRF, and score-kind fields because vector distance, vector similarity, FTS/BM25 score, and fused RRF score are not interchangeable.
  - `EvalSearchResult.doc_id` is the stable fixture ID after runtime ID mapping; `runtime_doc_id` preserves the underlying path-derived store ID for diagnostics.
  - `QueryEvalTrace.router_correct` is `True`/`False` when routing is enabled and the query is non-bypassed; `None` when routing is disabled or the query bypasses routing.
- **Releasable**: the eval layer has stable types for traces and aggregate metrics
- **Tests (TDD)** — `packages/archon-search/tests/eval/test_types.py`:
  - Unit: `test_eval_search_result_contains_score_breakdown` — nested score fields are preserved
  - Unit: `test_score_breakdown_contains_rank_score_and_score_kind_fields` — vector/FTS rank, raw score, and raw-score semantics are represented
  - Unit: `test_production_trace_types_do_not_import_eval_package` — production diagnostics can be imported without loading `archon_search.eval`
  - Unit: `test_query_eval_trace_router_correct_is_none_when_routing_disabled` — routing disabled or bypassed queries have `router_correct = None`
  - Unit: `test_query_eval_trace_router_correct_bool_when_routing_enabled` — enabled non-bypassed queries have `router_correct` as `True` or `False`
  - Checkpoint: `cd packages/archon-search && uv run pytest --no-cov tests/eval/test_types.py -v`

#### Task 2.2 — Hybrid-search trace provenance
- [x] **File**: `packages/archon-search/archon_search/store.py`
- **Depends on**: Task 2.1
- **Description**:
  - Keep `async hybrid_search(...) -> list[SearchResult]` compatible with normal callers.
  - Add an internal trace helper such as `_hybrid_search_with_trace(collection, query_vector, query_text, candidate_depth) -> list[ScoredSearchCandidate]` to preserve vector rank/raw score or distance, FTS rank/raw score, raw-score kind, and final RRF score per returned raw chunk candidate.
  - Clearly document raw-score semantics: LanceDB vector result rows commonly expose `_distance` where lower is better, FTS rows commonly expose `_score` where higher is better, and normalized RRF score is a separate fused rank score. If the package backend uses different field names, the adapter must document and test the exact mapping.
  - When backend result rows omit raw vector or FTS score fields, set the corresponding score and score kind to `None` rather than fabricating zero or converting rank to score.
  - When no FTS index exists, set `fts_score` to `None` instead of fabricating zero.
  - Keep the existing fused `score` field usable by non-eval callers.
  - Avoid breaking default production call sites; private eval trace data must be accessed only through the explicit trace helper.
  - Add exact public serialization contract checks for MCP `search` and `search_with_context`; adding trace fields to `SearchResult` is not acceptable because current server serialization uses dataclass dictionaries.
  - Keep private trace helpers internal to eval/debug paths and out of public package exports.
- **Releasable**: pre-rerank results contain vector, FTS, and fused scoring detail
- **Tests (TDD)** — `packages/archon-search/tests/test_store.py`:
  - Integration: `test_hybrid_search_keeps_public_result_contract` — normal search results do not expose eval-only score fields
  - Integration: `test_hybrid_search_trace_exposes_score_breakdown` — trace candidates include vector rank/raw score, FTS rank/raw score, score-kind fields, and RRF values
  - Integration: `test_hybrid_search_trace_documents_backend_score_field_mapping` — trace extraction pins the backend row fields used for vector and FTS raw scores
  - Integration: `test_hybrid_search_trace_sets_missing_raw_scores_none` — missing backend raw-score fields remain `None`, not fabricated values
  - Integration: `test_hybrid_search_trace_sets_fts_score_none_without_index` — no FTS index yields `None`, not an exception
  - Unit: `test_hybrid_search_trace_orders_equal_scores_deterministically` — equal RRF scores use stable secondary ordering
  - Integration: `test_mcp_search_response_schema_matches_public_contract_without_eval_provenance` — serialized public search payloads match the Search package public contract and exclude eval-only provenance
  - Integration: `test_mcp_search_with_context_response_schema_matches_public_contract_without_eval_provenance` — context payloads match the Search package public contract and keep eval-only provenance out of nested public result/chunk dictionaries
  - Unit: `test_eval_trace_helpers_are_not_public_package_exports` — internal trace helpers are not imported from the public package surface
  - Integration: `test_hybrid_search_trace_score_kind_values_match_backend_polarity` — `vector_score_kind` is `"distance"` and `fts_score_kind` is `"bm25"` for the LanceDB backend; a lower `vector_score` maps to a better (lower-rank) position, a higher `fts_score` maps to a better position
  - Checkpoint: `cd packages/archon-search && uv run pytest --no-cov tests/test_store.py -k "trace or public_result_contract or fts_score_none" -v`

#### Task 2.3 — Reranker trace preservation
- [x] **File**: `packages/archon-search/archon_search/reranker.py`
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
- [x] **File**: `packages/archon-search/archon_search/eval/_tracing.py`
- **Depends on**: Task 2.2, Task 2.3
- **Description**:
  - Add an eval-only trace collector, for example `async collect_search_trace(pipeline: SearchPipeline, query: str, collections: list[str] | None, candidate_depth: int, return_depth: int, metric_depth: int) -> tuple[list[EvalSearchResult], list[EvalSearchResult]]`.
  - Reuse the same service/application query path, embedder, store, and reranker instances as normal search. Trace collection may attach to shared internals, but it must not maintain a separate ranking algorithm that can pass eval while normal package search regresses.
  - The eval runner MUST call the production search method with a trace sink attached. Eval-only orchestration paths that reimplement ranking logic separately from the maintained `search()` method are forbidden. The drift guard verifies object identity: the embedder, store, and reranker instances used by trace collection must be the same objects used by `pipeline.search()`. Tests that only compare results are insufficient — a parallel path could produce identical results on the benchmark corpus while diverging on production inputs.
  - Return pre-rerank and post-rerank lists from a single query execution so timing and candidate sets stay aligned.
  - Treat `candidate_depth` and `return_depth` as raw chunk depths. Deduplicate to unique documents only in eval scoring and under-depth checks.
  - Do not change the behavior of the normal `search()` method.
  - Add a drift guard that fails when trace-enabled search and normal search use different ranking code paths or components.
  - When comparing eval trace output with normal `search()` output, use matching retrieve/return depths or compare only the defined common prefix; otherwise per-call eval depths and constructor-fixed production depths can legitimately differ.
- **Releasable**: eval code can obtain both ranking stages from one pipeline call
- **Tests (TDD)** — `packages/archon-search/tests/test_pipeline.py`:
  - Integration: `test_eval_trace_returns_pre_and_post_rerank_results` — both result lists are returned in order
  - Integration: `test_eval_trace_uses_service_query_path_with_trace_enabled` — eval traces are collected from the maintained package search path
  - Integration: `test_eval_trace_fails_if_trace_path_diverges_from_search_components` — guard verifies object identity of embedder, store, and reranker instances, not just result equality; eval cannot silently use a parallel ranking implementation
  - Integration: `test_eval_trace_matches_search_final_order_with_matching_depths` — post-rerank output matches normal `search()` ordering when retrieve/return depths are equal
  - Integration: `test_eval_trace_common_prefix_matches_search_when_depths_differ` — different eval depths compare only the defined common prefix
  - Integration: `test_eval_trace_does_not_change_public_search_response` — normal `search()` output stays unchanged
  - Checkpoint: `cd packages/archon-search && uv run pytest --no-cov tests/test_pipeline.py -k "eval_trace" -v`

#### Task 2.5 — Deterministic eval backends
- [x] **File**: `packages/archon-search/archon_search/eval/backends.py`
- **Depends on**: Task 1.3
- **Description**:
  - Add eval-only embedder and reranker backends that are deterministic, query-sensitive, corpus-aware, and label-blind.
  - The backends may use query text and document text by default.
  - Do not pass `relative_path`, `source_path`, fixture document IDs, query IDs, labels, gold collections, or fixture metadata to backend scoring unless a later reviewed task defines a narrow metadata allowlist. This prevents fixture authors from encoding relevance in paths or metadata.
  - The backends must not read or receive `labels.jsonl`, query IDs, stable fixture doc IDs as relevance hints, gold collections, or any query-to-relevant-document mapping.
  - Do not reuse the existing unit-test fake pattern that returns all-zero embeddings and uniform reranker scores.
  - Use a simple, process-deterministic scoring strategy such as SHA-256-based token hash embeddings (using `hashlib.sha256`, NOT Python's built-in `hash()` which is randomized by `PYTHONHASHSEED`) plus lexical/corpus-aware reranker scores. The implementation must not depend on `PYTHONHASHSEED` for determinism.
  - Add deterministic tie-breaking so repeated runs on the same corpus produce identical rank order.
  - Ensure at least one benchmark query has a measurable reranker lift so the lift metric is not structurally always zero.
- **Releasable**: the committed corpus can produce meaningful offline ranking signal without model downloads
- **Tests (TDD)** — `packages/archon-search/tests/eval/test_backends.py`:
  - Unit: `test_eval_embedder_changes_vector_for_query_terms` — different query terms produce different deterministic vectors
  - Unit: `test_eval_reranker_scores_from_query_and_document_text` — reranker scores are content-sensitive, not label-driven
  - Unit: `test_eval_backends_do_not_receive_labels_query_ids_doc_ids_or_gold_ids` — labels, query IDs, stable fixture doc IDs, gold collections, and relevance maps are not passed into backend construction or scoring
  - Unit: `test_eval_backends_do_not_receive_paths_or_fixture_metadata_by_default` — `relative_path`, `source_path`, fixture IDs, and metadata cannot drive benchmark ranking
  - Unit: `test_eval_backends_ignore_metadata_fields_that_look_like_gold_ids` — any future metadata allowlist still filters accidental benchmark identifier leakage
  - Unit: `test_eval_backends_have_stable_tie_breaking` — equal scores resolve deterministically
  - Integration: `test_eval_backend_produces_nonzero_reranker_lift_case` — fixture query demonstrates measurable lift
  - Unit: `test_eval_backends_produce_score_kind_consistent_with_polarity` — eval backends that produce `vector_score_kind="distance"` must assign lower scores to better-ranked documents; eval backends that produce `fts_score_kind="bm25"` must assign higher scores to better-ranked documents
  - Checkpoint: `cd packages/archon-search && uv run pytest --no-cov tests/eval/test_backends.py -v`

### Phase 3 — Metric computation and report generation
> **Releasable**: when Task 3.4 is complete; a complete eval report can be computed from traces, but pytest wiring is still optional

#### Task 3.1 — Ranking metric functions
- [x] **File**: `packages/archon-search/archon_search/eval/metrics.py`
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
  - Unit: `test_compute_ndcg_at_k_uses_documented_gain_discount_and_top_k_idcg` — nDCG implementation matches the documented formula and truncates IDCG to k
  - Unit: `test_metric_aggregation_macro_averages_queries` — query-level values are averaged equally rather than micro-averaged by label count
  - Unit: `test_metrics_dedupe_chunks_to_document_rankings` — duplicate chunks from one document do not inflate metrics
  - Unit: `test_metrics_reject_under_depth_after_chunk_dedupe` — insufficient unique-document depth fails clearly when the corpus has enough documents
  - Unit: `test_compute_ndcg_at_k_fewer_relevant_than_k` — perfect top-n_rel ranking achieves nDCG=1.0 when n_rel < k
  - Unit: `test_compute_ndcg_at_k_empty_result_list` — a query that returns zero documents after deduplication gets nDCG = 0.0, not a division-by-zero error
  - Unit: `test_compute_mrr_when_no_relevant_document_in_results` — when no relevant document appears in any ranked list across all queries, MRR = 0.0
  - Checkpoint: `cd packages/archon-search && uv run pytest --no-cov tests/eval/test_metrics.py -k "recall or mrr or ndcg" -v`

#### Task 3.2 — Routing, reranker-lift, and latency metrics
- [x] **File**: `packages/archon-search/archon_search/eval/metrics.py`
- **Depends on**: Task 3.1
- **Description**:
  - Implement `compute_reranker_lift`, `compute_routing_accuracy`, and `compute_latency_percentiles`.
  - `compute_routing_accuracy` is the simple fraction of non-bypassed queries where `router_correct=True`, across ALL non-bypassed queries regardless of `metric_scope` — both `metric_scope="retrieval"` queries with routing enabled AND `metric_scope="routing"` queries contribute to routing accuracy. `metric_scope="routing"` queries are the only inputs for retrieval metrics (recall/MRR/nDCG); they are NOT the only source for routing accuracy. Compute only when `routing.contract_enabled = true`; otherwise return `None` with a skip note.
  - Gold collections are derived by the eval runner from positive retrieval labels and the fixture `doc_id -> collection` map; they are passed to `is_routing_correct()` (in `eval/metrics.py`) at evaluation time.
  - Bypassed queries (where `QueryEvalTrace.router_correct is None`) are excluded from the metric and counted separately.
  - Treat reranker lift as report-only in v1; final post-rerank quality metrics carry the gates.
  - Percentiles operate on milliseconds captured by the eval runner, not wall-clock strings, and use the nearest-rank method defined in Metric semantics.
- **Releasable**: all remaining acceptance-metric categories are computed
- **Tests (TDD)** — `packages/archon-search/tests/eval/test_metrics.py`:
  - Unit: `test_compute_reranker_lift` — lift equals post-rerank nDCG minus pre-rerank nDCG
  - Unit: `test_compute_routing_accuracy_skips_bypassed_queries` — bypassed queries are excluded from routing accuracy
  - Unit: `test_compute_routing_accuracy_returns_none_when_not_enabled` — absent routing contract is explicit
  - Unit: `test_compute_routing_accuracy_zero_when_all_non_bypassed_queries_fail` — all non-bypassed queries with incorrect shortlist yield routing_accuracy = 0.0
  - Unit: `test_compute_routing_accuracy_one_when_all_non_bypassed_queries_pass` — all non-bypassed queries with correct shortlist yield routing_accuracy = 1.0
  - Unit: `test_compute_routing_accuracy_returns_none_when_all_queries_bypassed` — when routing is enabled but every query has routing_bypass=True, returns None (no non-bypassed queries to average)
  - Unit: `test_compute_routing_accuracy_mixed_pass_fail` — queries with different pass/fail states produce the correct fractional accuracy
  - Unit: `test_compute_reranker_lift_is_negative_when_reranking_hurts` — negative lift (post-rerank nDCG < pre-rerank nDCG) is correctly computed and reported without triggering a failure (report-only in v1)
  - Unit: `test_compute_latency_percentiles_single_sample` — n=1 returns that sample for both p50 and p95
  - Unit: `test_compute_latency_percentiles` — p50 and p95 are stable for fixed inputs
  - Unit: `test_compute_latency_percentiles_nearest_rank_small_n` — small samples use the documented percentile method
  - Unit: `test_compute_latency_percentiles_exact_integer_boundary` — p50 with n=2, p50 with n=4, AND p95 with n=20 (where `0.95 * 20 = 19.000000000000004` in float64) all produce the correct index without off-by-one from floating-point representation
  - Unit: `test_compute_latency_percentiles_empty_input` — empty latency list returns `(0.0, 0.0)` and emits a report warning per the documented behavior; it must not raise
  - Checkpoint: `cd packages/archon-search && uv run pytest --no-cov tests/eval/test_metrics.py -k "lift or routing or latency" -v`

#### Task 3.3 — Eval runner trace execution
- [x] **File**: `packages/archon-search/archon_search/eval/runner.py`
- **Depends on**: Task 1.2, Task 1.5, Task 2.4, Task 2.5, Task 3.2
- **Description**:
  - Implement `async run_eval_suite(corpus_root: Path, runtime_config_path: Path, thresholds_path: Path | None = None, baseline_path: Path | None = None) -> EvalReport`.
  - Load `runtime.toml` from an explicit path and reject metric-incompatible runtime settings before any corpus execution.
  - When `thresholds_path` is omitted, run in report-only calibration mode and do not call `assert_thresholds()`.
  - When `baseline_path` is present, load machine-readable baseline metadata so reports can show deltas against the accepted baseline.
  - Ingest the committed corpus into a module-scoped temporary LanceDB store using deterministic eval backends.
  - Map runtime path-derived document IDs and source paths back to stable fixture `doc_id` values before scoring.
  - Reject `EvalQuery.collection is None` before execution unless `metric_scope="routing"`. Retrieval queries must always have an explicit collection — using routing to select a collection for a retrieval query is outside v1 scope. The fixture loader should catch this at load time (Task 1.2), but the runner adds a defensive check.
  - Forbid using labels, gold collections, or relevant fixture document collections to choose the retrieval collection for collectionless queries.
  - Split query execution by `metric_scope`: retrieval queries feed recall/MRR/nDCG/reranker-lift; routing-only queries feed routing accuracy via `POST /route` shortlist intersection and are excluded from retrieval metrics.
  - Execute Search-owned routing only when `routing.contract_enabled = true`; otherwise set `routing_accuracy` to `None` with an explicit report note.
  - Execute the trace-enabled package search path per query, capture pre-rerank results, post-rerank results, `router_correct` result (when routing is enabled and query is non-bypassed), and elapsed milliseconds.
  - Use `candidate_depth` for raw pre-rerank candidates, `return_depth` for raw post-rerank chunk output, and `metric_depth` for post-dedupe unique-document scoring.
  - Fetch `candidate_depth` raw chunk candidates, deduplicate chunk results to unique fixture document IDs, and fail with an under-depth diagnostic when the resulting unique-document count is below `metric_depth` despite the **searched collection** having at least `metric_depth` unique documents. The per-collection unique document count is read from the fixture corpus (`EvalCorpus.documents` filtered by `collection`), not from the LanceDB store — this avoids false under-depth failures from partial ingestion while still producing an accurate threshold against the fixture-defined corpus size. Do not use total corpus size for this check — the diagnostic must reflect the actual retrievable depth from the collection being searched. Do not use iterative depth-increasing loops — `candidate_depth` is a fixed config value and the diagnostic is the correct response to insufficient unique-document depth.
  - Record notes and increment `routing_disabled_queries` when routing is disabled by configuration, and `routing_bypassed_queries` when a query bypasses routing via `routing_bypass=True` while routing is enabled.
  - If routing and cross-collection merge are Search-owned, apply deterministic tie-breaking to equal routing scores, equal reranker scores, equal normalized merge scores, and all-unscored routing states.
  - When a query execution raises an unexpected exception, `run_eval_suite` must surface the error and abort the suite — it must not silently skip the query and continue. Partial results that omit queries are silently incorrect baselines. Temporary LanceDB store cleanup must run even when the suite aborts.
- **Releasable**: one function produces a full in-memory eval report for the committed corpus
- **Tests (TDD)** — `packages/archon-search/tests/eval/test_runner.py`:
  - Do not introduce `@pytest.mark.eval` full-corpus tests in this task; Task 4.1 registers strict markers and default eval exclusion before full-corpus eval tests are added in Task 4.2.
  - Unmarked integration: `test_eval_runner_executes_miniature_corpus` — one trace is produced per miniature-fixture query
  - Unmarked integration: `test_eval_runner_full_chain_produces_all_metric_categories` — a miniature fixture run produces an `EvalReport` with all metric fields populated (recall@1/3/5, MRR, nDCG@5/10, reranker_lift, latency_p50_ms, latency_p95_ms); routing_accuracy is None or float depending on runtime config
  - Unmarked integration: `test_eval_runner_is_deterministic_except_latency_on_miniature_fixture` — two fresh stores produce identical quality metrics and rankings, excluding latency
  - Unmarked integration: `test_eval_runner_skips_routing_metric_without_search_owned_routing_contract` — routing metric is not silently fabricated
  - Unmarked integration: `test_eval_runner_excludes_bypassed_queries_from_routing_accuracy` — bypassed queries increment skip accounting
  - Unmarked integration: `test_eval_runner_rejects_collectionless_query_without_routing_contract` — `collection=None` fails before search when routing is disabled
  - Unmarked integration: `test_eval_runner_excludes_routing_only_queries_from_retrieval_metrics` — routing-only fixtures cannot distort recall/MRR/nDCG
  - Unmarked integration: `test_eval_runner_does_not_use_gold_labels_to_select_collection` — labels cannot determine the searched collection
  - Unmarked integration: `test_eval_runner_fails_under_depth_diagnostic_when_dedup_yields_insufficient_unique_documents` — when `candidate_depth` chunks deduplicate to fewer than `metric_depth` unique documents despite the corpus having at least `metric_depth` unique documents, the runner fails with a clear under-depth diagnostic — it does not silently return shallower metrics
  - Unmarked integration: `test_eval_runner_records_routing_disabled_and_bypassed_queries` — `routing_disabled_queries` and `routing_bypassed_queries` fields are incremented independently and correctly
  - Unmarked integration: `test_eval_runner_maps_runtime_doc_ids_to_fixture_doc_ids` — metrics consume stable fixture IDs
  - Unmarked integration: `test_eval_runner_fails_with_diagnostic_on_unmapped_source_path` — when a search result's `source_path` cannot be mapped to any fixture doc_id, the runner raises a clear error listing the unmapped path and available fixture paths; results are not silently dropped
  - Unmarked integration: `test_eval_runner_propagates_query_execution_errors` — a query that raises an exception aborts the suite with a clear error, not silent skip
  - Unmarked integration: `test_eval_runner_cleans_up_temp_store_on_error` — temporary LanceDB store is removed even when the eval suite raises mid-execution
  <!-- Unit tests for `load_baseline` — the runner calls this when `baseline_path` is present, so unit coverage belongs in this task. -->
  - Unit: `test_load_baseline_parses_valid_json` — valid `baseline.json` parses into `EvalBaseline` with correct field values
  - Unit: `test_load_baseline_rejects_malformed_json` — JSON parse errors raise `ValueError` with a clear message
  - Unit: `test_load_baseline_rejects_missing_required_fields` — missing `eval_hash`, `metrics`, or `runtime_config_hash` raises `ValueError`
  - Unit: `test_load_baseline_rejects_wrong_field_types` — `metrics` being a string instead of a nested object raises `ValueError`
  - Unit: `test_load_baseline_handles_none_thresholds_hash` — `thresholds_hash: null` in JSON loads as `None` without error
  - Integration: `test_baseline_json_survives_serialization_roundtrip` — `EvalBaseline` serialized to JSON and deserialized recovers identical field values including `float` precision for metric values and `None` for optional fields like `routing_accuracy` and `thresholds_hash`
  - Checkpoint: `cd packages/archon-search && uv run pytest --no-cov tests/eval/test_runner.py -k "runner or routing or depth" -v`

#### Task 3.4 — Threshold assertion and human-readable reporting
- [x] **File**: `packages/archon-search/archon_search/eval/runner.py`
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
  - Latency metrics use maximum-ceiling semantics only when the corresponding `EvalLatencyCeilings` field is not `None` (i.e., `[latency_ceilings].p50_ms` or `[latency_ceilings].p95_ms` is present in `thresholds.toml`); higher-than-ceiling fails.
  - When a latency ceiling field is `None`, that percentile appears in the report but cannot fail the test.
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
  - Integration: `test_assert_thresholds_enforces_max_floor_drop_policy` — `assert_thresholds` raises when a quality floor is set more than `max_floor_drop_without_waiver` below the baseline metric without a named waiver in baseline metadata
  - Integration: `test_assert_thresholds_rejects_calibration_only_baseline` — when `EvalReport.baseline.thresholds_hash` is `None` and `report.thresholds` is not None, `assert_thresholds()` raises with a message requiring a baseline refresh before gating can be enabled; a calibration-mode baseline must not silently serve as a gating baseline
  - Integration: `test_assert_thresholds_with_none_thresholds_hash_and_no_current_thresholds_renders_report_only` — when `baseline.thresholds_hash is None` and `report.thresholds is None`, render_report succeeds and no gating assertion is made; this is a valid calibration-only state
  - Integration: `test_render_report_includes_all_metric_categories` — rendered string contains recall@k, MRR, nDCG, reranker lift, routing accuracy (or explicit skip note), and latency percentile values
  - Checkpoint: `cd packages/archon-search && uv run pytest --no-cov tests/eval/test_runner.py -k "assert_thresholds or render_report" -v`

### Phase 4 — Pytest integration, calibration, and CI gating
> **Releasable**: after each task; by Task 4.5 the evaluation harness is callable in local pytest and enforced in path-filtered PR CI plus release CI

#### Task 4.1 — Eval fixtures and pytest marker wiring
- [x] **Files**: `packages/archon-search/pyproject.toml`, `packages/archon-search/tests/eval/conftest.py`
- **Depends on**: Task 3.3
- **Description**:
  - Add or update the extracted package pytest configuration, not only the root Archon pytest config.
  - Set package `testpaths = ["tests"]`.
  - Register `eval` and any package-local live/integration markers.
  - Set default package addopts so strict marker/config validation is enabled and slow eval tests are excluded from default runs, for example `--strict-markers --strict-config -m "not live and not eval"`.
  - Ensure package coverage targets `archon_search`, not root `archon`.
  - Set or document a package coverage fail-under threshold matching the project floor (`85%`) unless the package sets a different floor in `pyproject.toml`. If the release/PR gate uses separate default and eval pytest invocations, do not put `--cov-fail-under` in addopts for the intermediate invocations; apply it in the final combined coverage check instead.
  - Ensure the package default suite runs unmarked eval unit, parser, config, metric, and safety tests under `archon_search` coverage.
  - Add module-level model-download guards following the existing Archon import-time injection pattern, but route eval tests to the query-sensitive deterministic eval backends from `archon_search.eval.backends`.
  - Provide module-scoped fixtures for temporary LanceDB storage and the loaded eval corpus.
  - Register a `--thresholds-path` pytest CLI option in `conftest.py` that accepts a path to `thresholds.toml`. The gated smoke test receives this path via the option. The default value must not point to the committed `thresholds.toml` location by path discovery; the option must be explicitly passed by CI. Behavior when absent depends on context: in CI (detected via `CI` environment variable), the gated smoke test calls `pytest.fail("thresholds-path not provided in CI — pass --thresholds-path explicitly")` to prevent silent CI misconfiguration; locally (no `CI` env var), it raises `pytest.skip` with the message: "thresholds-path not provided; use -k 'not gated' for report-only mode or pass --thresholds-path for gated mode."
  - Mark only the slow full-corpus eval smoke/integration tests with `eval`; keep pure fixture-loader, threshold-parser, and metric unit tests in the default package test suite.
  - Ensure the default test selection excludes `eval` while direct unit-test checkpoint commands still run.
  - Document that local report-only eval commands may use `--no-cov` for fast metric calibration, while PR/release gates must run with coverage enabled and the combined fail-under check.
- **Releasable**: maintainers can run the eval slice locally without real model downloads
- **Tests (TDD)** — `packages/archon-search/tests/eval/test_pytest_integration.py`:
  - Integration: `test_eval_pytest_marker_excluded_from_default_run` — default selection excludes eval
  - Integration: `test_eval_conftest_uses_deterministic_eval_backends` — eval backends are active during eval tests
  - Integration: `test_eval_marker_only_marks_full_corpus_tests` — unit metric/fixture tests are not accidentally deselected
  - Integration: `test_package_pytest_config_uses_strict_markers_and_config` — marker typos and config drift fail fast
  - Integration: `test_package_pytest_config_targets_archon_search_coverage` — package coverage config does not use root `archon`
  - Integration: `test_package_pytest_config_sets_or_documents_coverage_fail_under` — package coverage floor is explicit without breaking split coverage runs
  - Integration: `test_split_coverage_gate_applies_fail_under_only_after_combine` — separate default/eval invocations cannot fail before coverage is combined
  - Integration: `test_package_default_suite_covers_unmarked_eval_units` — eval support code is not hidden behind `--no-cov` or `-m eval`
  - Integration: `test_local_metric_only_eval_command_is_distinct_from_ci_coverage_gate` — `--no-cov` is allowed only for local calibration commands, not PR/release gates
  - Checkpoint: `cd packages/archon-search && uv run pytest --no-cov tests/eval/test_pytest_integration.py -v`

#### Task 4.2 — End-to-end report-only eval smoke test
- [x] **File**: `packages/archon-search/tests/eval/test_eval_suite.py`
- **Depends on**: Task 3.4, Task 4.1
- **Description**:
  - Add `@pytest.mark.eval` smoke coverage that calls `run_eval_suite(corpus_root, runtime_config_path, thresholds_path=None, baseline_path=None)`, renders the report, and does not assert thresholds.
  - Use this task to bootstrap the first measured report without invented threshold values.
  - Keep the smoke test single-entry so CI sees one obvious failure point with full report output.
  - Ensure the test failure text is rich enough to diagnose regressions without rerunning locally first.
- **Releasable**: the full harness is executable from pytest before baseline and thresholds exist
- **Tests (TDD)** — `packages/archon-search/tests/eval/test_eval_suite.py`:
  - E2E: `test_eval_suite_report_only_smoke` — full eval harness runs against the committed corpus and produces a report string without thresholds
  - E2E: `test_eval_suite_report_only_does_not_assert_thresholds` — calibration mode never passes or fails from missing floors
  - E2E: `test_eval_suite_is_deterministic_except_latency` — two fresh stores produce identical full-corpus quality metrics and rankings, excluding latency
  - Checkpoint: `cd packages/archon-search && uv run pytest --no-cov tests/eval/test_eval_suite.py -m eval -k "report_only" -v`

#### Task 4.3 — Baseline calibration before gating
- [x] **Files**: `packages/archon-search/tests/eval/baselines/baseline.md`, `packages/archon-search/tests/eval/baselines/baseline.json`, `packages/archon-search/tests/eval/thresholds.toml`
- **Depends on**: Task 4.2
- **Description**:
  - Run the full eval suite once against the accepted corpus and runtime config.
  - Commit the rendered baseline report and machine-readable baseline metadata alongside `thresholds.toml`.
  - Record metrics, eval hash, runtime config hash, thresholds hash, and exact eval command in `baseline.json`.
  - Compute `eval_hash` from every eval-determinism-defining input: `documents.jsonl`, corpus files, `queries.jsonl`, `labels.jsonl`, optional `routing/collections.jsonl`, and `eval/backends.py`.
  - `metrics.py` and `runner.py` changes that alter metric computation require a manual baseline refresh even though they are excluded from `eval_hash`; include the commit hash in `baseline.json` `command` field when such changes precede calibration.
  - Treat eval hash, runtime config hash, and thresholds hash as the staleness gates that require baseline refresh before PR/release gating can pass. Any change to eval-determinism-defining inputs (including `backends.py`) automatically invalidates the stored baseline.
  - Ensure every committed quality floor is derived from, or intentionally below, the saved baseline metric.
  - Require an explicit rationale in the baseline report or README for any floor set below the measured baseline.
  - Reject floor reductions larger than `[policy].max_floor_drop_without_waiver` unless the baseline metadata names a reviewed waiver or issue ID.
  - Do not enable PR/release gating until this calibration artifact exists.
- **Releasable**: the later release gate has a reviewed, reproducible baseline rather than invented thresholds
- **Tests (TDD)** — `packages/archon-search/tests/eval/test_baseline_contract.py`:
  - Integration: `test_thresholds_have_matching_baseline_report` — committed thresholds require a saved baseline report
  - Integration: `test_baseline_metadata_hashes_match_benchmark_inputs` — baseline metadata hashes match documents, corpus files, queries, labels, optional routing/collections.jsonl, backends.py, runtime config, and thresholds
  - Integration: `test_baseline_metadata_records_eval_hash_and_command` — baseline records the eval hash and the exact command used to produce it
  - Integration: `test_quality_floors_never_exceed_baseline` — configured floors cannot be higher than the measured baseline
  - Integration: `test_quality_floor_below_baseline_requires_rationale` — intentionally lowered floors need an explicit rationale
  - Integration: `test_quality_floor_drop_beyond_policy_requires_waiver` — large floor reductions require reviewed waiver metadata
  - Unit: `test_eval_hash_is_stable_for_same_inputs` — same documents, corpus files, queries, labels, and backends.py produce identical eval hash across two calls
  - Unit: `test_eval_hash_changes_when_document_manifest_changes` — modifying `documents.jsonl` changes the eval hash
  - Unit: `test_eval_hash_changes_when_corpus_file_changes` — modifying a corpus file changes the eval hash
  - Unit: `test_eval_hash_changes_when_backends_py_changes` — modifying `eval/backends.py` changes the eval hash
  - Unit: `test_eval_hash_excludes_metrics_py` — modifying `metrics.py` does NOT change the eval hash (intentional exclusion per spec)
  - Unit: `test_runtime_config_hash_changes_when_runtime_toml_changes` — modifying `runtime.toml` changes the runtime config hash
  - Unit: `test_thresholds_hash_changes_when_thresholds_toml_changes` — modifying `thresholds.toml` changes the thresholds hash
  - Checkpoint: `cd packages/archon-search && uv run pytest --no-cov tests/eval/test_baseline_contract.py -v`

#### Task 4.4 — Gated eval smoke test
- [ ] **File**: `packages/archon-search/tests/eval/test_eval_suite.py`
- **Depends on**: Task 4.3
- **Description**:
  - Add `@pytest.mark.eval` smoke coverage that calls `run_eval_suite(corpus_root, runtime_config_path, thresholds_path, baseline_path)`, renders the report, and calls `assert_thresholds(report)`.
  - Keep this test distinct from the report-only smoke so calibration remains available without thresholds.
  - The gated smoke test must fail explicitly if `thresholds.toml` does not exist at the expected path. It must not fall back to report-only mode. The CI invocation must pass `--thresholds-path` explicitly; the eval suite must not discover the thresholds file by convention search.
  - When loading a baseline whose `thresholds_hash` is `None`, fail with an explicit message requiring a baseline refresh before gating can be enabled — do not silently treat the calibration baseline as an approved gating baseline.
  - Fail if baseline metadata no longer matches the committed benchmark inputs, runtime config, or thresholds.
  - Fail when eval hash, runtime config hash, or thresholds hash no longer matches the stored baseline; a stale baseline blocks gating.
  - Fail when quality metrics regress below floors or when gated latency exceeds ceilings.
- **Releasable**: the full harness has a single maintained gated test after calibration exists
- **Tests (TDD)** — `packages/archon-search/tests/eval/test_eval_suite.py`:
  - E2E: `test_eval_suite_gated_smoke` — full eval harness runs against committed corpus, baseline, and thresholds
  - E2E: `test_eval_suite_gated_smoke_reports_baseline_deltas` — report includes deltas against baseline and threshold
  - E2E: `test_eval_suite_gated_smoke_rejects_stale_benchmark_or_threshold_hashes` — stale benchmark, runtime, or threshold hashes cannot pass
  - E2E: `test_eval_suite_gated_smoke_rejects_calibration_only_baseline` — a baseline with `thresholds_hash = None` fails the gated smoke test with a clear message requiring refresh
  - E2E: `test_eval_suite_report_only_accepts_calibration_baseline_without_thresholds` — a baseline with `thresholds_hash=None` is valid when running in report-only mode (no thresholds path passed); the calibration baseline can be used for baseline delta display in report-only mode
  - E2E: `test_eval_suite_gated_smoke_rejects_stale_eval_hash` — a changed eval hash (backends.py, fixtures, or labels) forces baseline refresh before gating can pass
  - Checkpoint: `cd packages/archon-search && uv run pytest --no-cov tests/eval/test_eval_suite.py -m eval -k "gated" -v`

#### Task 4.5 — PR and release CI inclusion
- [ ] **Files**: executable package package PR gate and release gate (`.github/workflows/*.yml`, `release.sh`, or the package repository's equivalent)
- **Depends on**: Task 4.4
- **Description**:
  - Update the actual executable extracted package PR gate to run the gated eval slice for changes to the full retrieval pipeline: parser, chunker, embedder, store, pipeline, reranker, Search-owned router, collection metadata/schema/config, eval fixtures/code, runtime config, thresholds, baselines, package dependency manifests, lockfiles, eval extras/dependency groups, package pytest config, and CI/release gate files.
  - Update the actual executable extracted package release gate to run the eval slice explicitly and fail before the first release mutation, generated metadata rewrite, commit, tag, publish, or release creation.
  - `packages/archon-search/` is in this monorepo. Update the executable repository PR workflow and release script or workflow that owns the package release, such as `release.sh` if it remains the release entrypoint. Also update `Documentation/release-process.md`, but documentation alone is not a release gate.
  - All CI contract tests must assert file existence as a precondition. A test that reads a non-existent file and passes vacuously is a silent CI gap. Use `pytest.skip` with an explicit message if the expected file does not exist, so the skip is visible and tracked.
  - Install the extracted package with Search/eval dependencies before each gate; dependency-missing skips are forbidden in PR and release modes.
  - Add a clean-environment dependency smoke that installs the Search/eval extra or dependency group and verifies pytest plugins used by the coverage gate are importable and executable.
  - Define the exact PR and release gate commands in executable entrypoints and run them under the package pytest config, either by `cd packages/archon-search` or by `pytest -c packages/archon-search/pyproject.toml`.
  - Each CI command must run the package default suite with package coverage enabled so unmarked eval unit, parser, config, metric, and safety tests are part of the gate.
  - Each CI command must also override the package default marker exclusion and run the complete `eval` marker slice with package coverage enabled, including the gated smoke and determinism test; do not use `--no-cov` in PR or release mode.
  - If the default suite and eval slice run as separate pytest invocations, combine coverage with explicit `--cov-append`/coverage-combine semantics and evaluate `--cov-fail-under` only after both slices have contributed coverage. A single combined invocation is also acceptable. Intermediate invocations must disable or omit fail-under so partial coverage cannot fail before the eval slice contributes.
  - The eval-marker invocation, or an aggregate gate wrapper around split invocations, must fail if pytest collects zero eval tests, if any selected eval test is skipped, xfailed, or xpassed without an explicit reviewed allowlist, or if all eval tests are deselected. The default-suite invocation is allowed to deselect eval tests intentionally.
  - Use `--runxfail` plus a package pytest hook/report check, or an equivalent mechanism, so expected xfails and unexpected xpasses cannot count as a passing CI gate.
  - If an allowlist is unavoidable, store it in a committed machine-readable file such as `packages/archon-search/tests/eval/skip_xfail_allowlist.toml` with exact test node IDs only, issue ID, reviewer, reason, and expiry date. Wildcards, broad path prefixes, missing reviewers, missing issues, and expired entries fail the gate.
  - Do not place a workflow under `packages/archon-search/.github/workflows/`; `packages/archon-search/` is not the repository root and nested `.github` directories do not run in GitHub Actions.
  - Keep fast/default CI excluding `-m eval`.
  - Make the workflow surface the rendered report in logs when thresholds fail.
- **Releasable**: eval regressions block path-filtered PR CI and release CI for the search package
- **Tests (TDD)** — `packages/archon-search/tests/eval/test_ci_contract.py`:
  - Integration: `test_ci_gate_files_exist_after_feat_038_extraction` — PR workflow file, release gate file, and package pytest config file all exist on disk; all other CI contract tests in this task are gated on this test passing
  - Integration: `test_pr_gate_path_filters_include_full_retrieval_pipeline_dependency_eval_threshold_baseline_and_ci_files` — PR gating covers every file class that can change eval behavior, including parser/chunker/embedder/store/pipeline/reranker/router, metadata/schema/config, dependency manifests, and lockfiles
  - Integration: `test_pr_gate_runs_gated_eval_slice_for_matching_paths` — selected PR changes run the complete eval marker slice
  - Integration: `test_release_gate_passes_explicit_thresholds_path` — the release gate command passes an explicit thresholds path; omitting it or relying on file-discovery is a CI misconfiguration
  - Integration: `test_release_gate_includes_eval_slice` — the concrete executable package release entrypoint references and runs the eval marker or eval test path
  - Integration: `test_ci_gates_use_package_pytest_config` — PR and release cannot accidentally run under root Archon pytest config
  - Integration: `test_ci_clean_install_includes_eval_dependencies_and_pytest_plugins` — dependency setup is explicit and executable before pytest
  - Integration: `test_ci_gates_run_with_package_coverage` — PR and release gates enforce `archon_search` coverage instead of using `--no-cov`
  - Integration: `test_ci_gates_enforce_coverage_fail_under_after_default_and_eval_slices` — split test invocations combine coverage before the fail-under check
  - Integration: `test_ci_gates_do_not_apply_fail_under_to_intermediate_split_invocations` — default suite cannot fail from partial coverage before eval coverage is appended
  - Integration: `test_ci_gates_run_default_suite_and_full_eval_slice` — PR and release gates run both the package default suite and the complete eval marker slice
  - Integration: `test_ci_gates_fail_when_eval_collection_is_empty_or_any_eval_is_skipped_or_xfailed` — zero collection, skipped, xfailed, or xpassed eval tests are CI failures unless explicitly allowlisted
  - Integration: `test_ci_gates_use_runxfail_and_skip_xfail_report_check` — expected xfails and unexpected xpasses cannot silently pass selected eval tests in CI mode
  - Integration: `test_skip_xfail_allowlist_requires_exact_unexpired_reviewed_nodeids` — allowlists cannot use globs, stale entries, or unreviewed exceptions
  - Integration: `test_release_docs_reference_eval_slice_but_are_not_sufficient` — `Documentation/release-process.md` may document the gate but cannot be the only passing artifact
  - Integration: `test_release_script_runs_eval_before_first_mutation_or_publish_step` — monorepo `release.sh` fallback must run eval before generated metadata rewrites, release commits, tags, publish steps, or release creation if it remains the package release entrypoint
  - Integration: `test_fast_ci_excludes_eval_slice` — fast workflow or package pytest defaults still exclude eval
  - Integration: `test_nested_package_github_workflow_is_not_the_only_gate` — nested workflow files are not treated as sufficient unless the package is its own repo
  - Checkpoint: `cd packages/archon-search && uv run pytest --no-cov tests/eval/test_ci_contract.py -v`

### Phase 5 — Documentation and baseline hardening
> **Releasable**: after each task; by Task 5.2 the harness is documented and maintainable without oral history

#### Task 5.1 — Eval README and maintenance guide
- [ ] **File**: `packages/archon-search/tests/eval/README.md`
- **Depends on**: Task 4.4
- **Description**:
  - Document the corpus layout, `documents.jsonl` manifest, query schema, label schema, threshold semantics, baseline metadata schema, and the process for refreshing thresholds from measured baselines.
  - Document that labels are document-level and metrics deduplicate chunk results before scoring.
  - Require threshold changes to include a saved rendered report and a rationale when any quality floor is lowered.
  - Document the floor-drop waiver policy and the requirement for a reviewed issue or waiver ID when reductions exceed the configured tolerance.
  - Explicitly state that v1 latency metrics use deterministic eval backends and local LanceDB and should be interpreted as regression guards, not production SLAs.
  - Include the exact local commands for report-only calibration, gated eval, and default unmarked eval-unit tests.
- **Releasable**: maintainers can extend the harness without reverse-engineering the tests
> **Note**: Documentation contract tests are process-compliance smoke tests — they verify that required documentation sections were written (string-match level), not that the documentation is correct. They serve as "was this section added at all" guards. Behavioral regression safety comes from test_eval_suite.py and test_metrics.py.
- **Tests (TDD)** — `packages/archon-search/tests/eval/test_docs_contract.py`:
  - Integration: `test_eval_readme_mentions_threshold_baselines` — README documents baseline-derived thresholds
  - Integration: `test_eval_readme_mentions_machine_readable_baseline_metadata` — README documents `baseline.json`
  - Integration: `test_eval_readme_requires_threshold_lowering_rationale` — README documents anti-rot threshold policy
  - Integration: `test_eval_readme_mentions_floor_drop_waiver_policy` — README documents waiver requirements for large floor drops
  - Integration: `test_eval_readme_mentions_document_level_metrics` — README explains document-level deduplication
  - Integration: `test_eval_readme_mentions_eval_backend_latency_limits` — README explains latency caveat
  - Checkpoint: `cd packages/archon-search && uv run pytest --no-cov tests/eval/test_docs_contract.py -k "readme" -v`

#### Task 5.2 — Package and roadmap documentation updates
- [ ] **Files**: `packages/archon-search/README.md`, `Documentation/Backlog/FEAT-037-search-world-class-roadmap.md`, `Documentation/Architecture/180_search_architecture.md`, `Documentation/990_documentation_index_and_contribution_guide.md`
- **Depends on**: Task 4.5, Task 5.1
- **Description**:
  - Add an Evaluation section to the package README.
  - Update the Search architecture and FEAT-037 roadmap docs so the extracted package’s evaluation harness is documented as the sanctioned regression gate only after baseline calibration, gated smoke, path-filtered PR CI, and release CI inclusion are complete.
  - Search is in the monorepo. Keep package-local doc tests scoped to `packages/archon-search/README.md` and `tests/eval/README.md`; validate Archon roadmap/architecture references in the Archon repository workflow or an explicit Phase 0 documentation checklist.
  - Document the maintained path-filtered PR eval command and release eval command based on the confirmed Search package workflow.
  - Update FEAT-037 or add follow-up backlog items making clear that live query logging, relevance feedback, privacy policy, online data collection, and Archon-owned routing evaluation if routing remains outside the Search package all remain after FEAT-039.
  - If Phase 0 marked FEAT-039 partial because PR gating could not be implemented, docs must say FEAT-039 does not close the brief and must link the PR-gating follow-up.
  - Update `Documentation/990_documentation_index_and_contribution_guide.md` whenever this task adds or changes follow-up backlog artifacts.
  - Keep the wording aligned with the actual delivered metric set and pytest commands.
- **Releasable**: the harness is visible in both package-local docs and project planning docs
- **Tests (TDD)** — `packages/archon-search/tests/eval/test_docs_contract.py`:
  - Integration: `test_package_readme_mentions_eval_command` — README includes the maintained eval command
  - Integration: `test_package_doc_tests_do_not_require_archon_documentation_when_extracted` — package CI does not depend on absent Archon root docs
  - Archon-repo integration: `test_roadmap_docs_reference_eval_harness` — roadmap/docs mention the delivered harness when the Archon docs are present
  - Archon-repo integration: `test_roadmap_docs_keep_data_collection_followup_open` — docs do not imply FEAT-039 completes the full data-collection loop
  - Archon-repo integration: `test_roadmap_docs_document_path_filtered_pr_eval_gate` — docs include the maintained PR eval gate when FEAT-039 is complete
  - Archon-repo integration: `test_roadmap_docs_mark_feat_039_partial_if_pr_gate_missing` — release-only v1 does not satisfy the brief's PR-gating objective
  - Archon-repo integration: `test_roadmap_docs_keep_archon_routing_eval_followup_open_when_needed` — skipped routing metrics are tracked outside FEAT-039 when routing remains Archon-owned
  - Archon-repo integration: `test_documentation_index_includes_new_followup_backlog_items` — follow-up backlog artifacts remain discoverable from the documentation index
  - Package checkpoint: `cd packages/archon-search && uv run pytest --no-cov tests/eval/test_docs_contract.py -k "package_readme or eval_readme" -v`
  - Archon-repo checkpoint when Archon docs are present: `uv run pytest --no-cov packages/archon-search/tests/eval/test_docs_contract.py -k "roadmap_docs or documentation_index" -v`
