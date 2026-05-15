"""Suite 4 — Pipeline Integration with Mocked HTTP (FEAT-038 Task 2.1).

Happy-path tests H4.1–H4.5: verifies that SearchContextProvider integrates correctly
with Pipeline.send() using a real SearchContextProvider instance but with the
SearchClient and _search_collection fan-out mocked via patch.object / patch.
"""
from __future__ import annotations

import json
from typing import AsyncGenerator
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from archon.ai.classification import Classification
from archon.ai.classifier import ClassifierResult
from archon.ai.decomposer import TaskOutput
from archon.ai.event_mapper import Response
from archon.ai.pipeline import Pipeline
from archon_search.types import RouteResponse


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────


def _make_classifier(intent: str = "task", confidence: float = 0.9) -> MagicMock:
    """Return a minimal mock Classifier."""
    clf = MagicMock()
    clf.start = AsyncMock()
    clf.stop = AsyncMock()
    clf.model = "claude-haiku-mock"
    clf.usage_stats = None
    clf.classify = AsyncMock(
        return_value=ClassifierResult(
            classification=Classification(intent=intent, confidence=confidence),
            raw_response=json.dumps({"intent": intent, "confidence": confidence}),
            duration_s=0.01,
        )
    )
    return clf


def _make_decomposer(
    route_task_result: TaskOutput | None = None,
) -> MagicMock:
    """Return a minimal mock Decomposer."""
    from tests.conftest import _RouteTaskGenMock

    decomposer = MagicMock()
    decomposer.start = AsyncMock()
    decomposer.stop = AsyncMock()
    decomposer.is_processing = False
    decomposer.processing_seconds = None
    decomposer.idle_seconds = 5.0
    decomposer.send_count = 0
    decomposer.usage_stats = None
    decomposer.diagnostics = {"is_alive": True}
    decomposer.model = "claude-sonnet-mock"
    decomposer.is_alive = True
    decomposer.context_summary = ""
    decomposer.reminder = None

    if route_task_result is None:
        route_task_result = TaskOutput(scope="small", prompt="do it")

    async def _answer(prompt: str) -> AsyncGenerator:
        yield Response(content="Done.")

    decomposer.answer = _answer
    decomposer.route_task = _RouteTaskGenMock(route_task_result)
    decomposer.inject_context = MagicMock()
    decomposer.flush_pending_context = MagicMock()
    decomposer.track_context = MagicMock()
    decomposer.recent_events = MagicMock(return_value=[])
    decomposer.force_kill_for_recovery = MagicMock()
    decomposer.restart_session = AsyncMock()
    decomposer.recover_session = AsyncMock()
    return decomposer


def _make_search_config(
    *,
    enabled: bool = True,
    max_parallel_collections: int = 3,
    top_k_return: int = 5,
) -> MagicMock:
    """Return a SearchConfig-like mock."""
    cfg = MagicMock()
    cfg.enabled = enabled
    cfg.max_parallel_collections = max_parallel_collections
    cfg.top_k_return = top_k_return
    return cfg


def _route_response(
    *,
    pre_context: str | None = None,
    pinned_names: list[str] | None = None,
    routable_names: list[str] | None = None,
    decomposer_invoked: bool = False,
) -> RouteResponse:
    return RouteResponse(
        pre_context=pre_context,
        pinned_names=pinned_names or [],
        routable_names=routable_names or [],
        decomposer_invoked=decomposer_invoked,
    )


def _mock_route_http_response(route_resp: RouteResponse) -> MagicMock:
    """Build a mock httpx Response for the /route POST call."""
    resp = MagicMock()
    resp.status_code = 200
    resp.raise_for_status = MagicMock()
    resp.json = MagicMock(
        return_value={
            "pre_context": route_resp.pre_context,
            "pinned_names": list(route_resp.pinned_names),
            "routable_names": list(route_resp.routable_names),
            "decomposer_invoked": route_resp.decomposer_invoked,
        }
    )
    return resp


def _make_search_result_json(text: str, score: float, collection: str) -> dict:
    """Build a single search result dict as the archon-search server returns."""
    return {
        "doc_id": f"{collection}-doc",
        "chunk_id": f"{collection}-chunk",
        "text": text,
        "score": score,
        "source_path": f"/docs/{collection}.md",
    }


def _mock_fanout_http_response(results: list[dict]) -> MagicMock:
    """Build a mock httpx Response for the FastMCP JSON-RPC search POST."""
    resp = MagicMock()
    resp.status_code = 200
    resp.raise_for_status = MagicMock()
    resp.json = MagicMock(
        return_value={
            "jsonrpc": "2.0",
            "id": 1,
            "result": {
                "content": [{"type": "text", "text": json.dumps(results)}]
            },
        }
    )
    return resp


async def _collect_events(pipeline: Pipeline, prompt: str = "what is X?") -> list:
    """Drive pipeline.send() to completion and return all emitted events."""
    events = []
    async for event in pipeline.send(prompt):
        events.append(event)
    return events


def _make_pipeline_with_search(
    *,
    decomposer: MagicMock | None = None,
    search_url: str = "http://localhost:8765",
    rag_config: MagicMock | None = None,
    search_client: MagicMock | None = None,
) -> tuple[Pipeline, MagicMock]:
    """Construct a Pipeline with a real SearchContextProvider but mocked internals.

    Returns (pipeline, decomposer_mock).
    """
    from archon.ai.search_context_provider import SearchContextProvider

    if rag_config is None:
        rag_config = _make_search_config()
    if decomposer is None:
        decomposer = _make_decomposer()

    classifier = _make_classifier()

    # Build a real SearchContextProvider — inject a mock SearchClient so
    # we can control route() output without HTTP.
    if search_client is None:
        search_client = MagicMock()
        search_client.route = AsyncMock(return_value=None)

    real_provider = SearchContextProvider(
        cfg=rag_config,
        search_client=search_client,
    )

    with patch("archon.ai.pipeline.Classifier", return_value=classifier):
        with patch("archon.ai.pipeline.Decomposer", return_value=decomposer):
            pipeline = Pipeline()

    # Replace internal _search_provider with our real (but client-mocked) provider
    pipeline._search_provider = real_provider

    return pipeline, decomposer


# ──────────────────────────────────────────────────────────────────────────────
# H4.1 — inject_context called with "search_retrieval" type
# ──────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_pipeline_injects_rag_context_when_search_enabled() -> None:
    """H4.1: When search is enabled and returns results, inject_context is called
    with injection_type='search_retrieval'."""
    from archon.ai.search_client import SearchClient

    route_resp = _route_response(
        pre_context=None,
        routable_names=["docs"],
        decomposer_invoked=False,
    )
    mock_search_client = MagicMock()
    mock_search_client.route = AsyncMock(return_value=route_resp)

    mock_search_client.search = AsyncMock(
        return_value=[_make_search_result_json("chunk text", 0.9, "docs")]
    )
    pipeline, decomposer = _make_pipeline_with_search(search_client=mock_search_client)

    await _collect_events(pipeline, "what is X?")

    # Verify inject_context was called with search_retrieval type
    decomposer.inject_context.assert_called_once()
    call_kwargs = decomposer.inject_context.call_args
    # inject_context(rag_text, injection_type=..., detail=...)
    assert call_kwargs.kwargs["injection_type"] == "search_retrieval"


# ──────────────────────────────────────────────────────────────────────────────
# H4.2 — pre_context from route() is passed to route_task
# ──────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_pipeline_pre_context_passed_to_route_task() -> None:
    """H4.2: route() returns pre_context='hint' → it is forwarded to route_task()
    as search_pre_context kwarg."""
    route_resp = _route_response(
        pre_context="hint",
        routable_names=["col1"],
        decomposer_invoked=False,
    )
    mock_search_client = MagicMock()
    mock_search_client.route = AsyncMock(return_value=route_resp)

    pipeline, decomposer = _make_pipeline_with_search(search_client=mock_search_client)
    provider = pipeline._search_provider
    assert provider is not None

    # Wrap route_task to capture search_pre_context kwarg
    _original_rt = decomposer.route_task
    _captured_pre_context: list[str | None] = []

    def _capturing_rt(prompt: str, search_pre_context: str | None = None):
        _captured_pre_context.append(search_pre_context)
        return _original_rt(prompt, search_pre_context=search_pre_context)

    decomposer.route_task = _capturing_rt
    mock_search_client.search = AsyncMock(
        return_value=[_make_search_result_json("text", 0.8, "col1")]
    )

    await _collect_events(pipeline, "what is X?")

    # Verify search_pre_context was forwarded to route_task
    assert len(_captured_pre_context) == 1
    assert _captured_pre_context[0] == "hint"
    # Secondary verification via provider's internal state
    assert provider._route_response is not None
    assert provider._route_response.pre_context == "hint"


# ──────────────────────────────────────────────────────────────────────────────
# H4.3 — Tier 1: decomposer_invoked=False → all routable collections searched
# ──────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_pipeline_tier1_searches_all_routable_collections() -> None:
    """H4.3: decomposer_invoked=False (Tier 1) → all routable+pinned collections searched."""
    route_resp = _route_response(
        pre_context=None,
        pinned_names=["pinned"],
        routable_names=["col1", "col2"],
        decomposer_invoked=False,
    )
    mock_search_client = MagicMock()
    mock_search_client.route = AsyncMock(return_value=route_resp)

    rag_config = _make_search_config(max_parallel_collections=5)
    pipeline, decomposer = _make_pipeline_with_search(
        search_client=mock_search_client,
        rag_config=rag_config,
    )
    provider = pipeline._search_provider
    assert provider is not None

    searched_collections: list[str] = []

    async def _mock_search(collection: str, query: str, top_k: int) -> list[dict]:
        searched_collections.append(collection)
        return [_make_search_result_json("text", 0.8, collection)]

    mock_search_client.search = AsyncMock(side_effect=_mock_search)
    await _collect_events(pipeline, "find X")

    # Tier 1: all routable + all pinned must be searched — exact set
    assert set(searched_collections) == {"pinned", "col1", "col2"}


# ──────────────────────────────────────────────────────────────────────────────
# H4.4 — Tier 3: decomposer selects specific collections
# ──────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_pipeline_tier3_uses_decomposer_selected_collections() -> None:
    """H4.4: decomposer selects specific collections → only those (+ pinned) are searched."""
    route_resp = _route_response(
        pre_context=None,
        pinned_names=["always"],
        routable_names=["col1", "col2", "col3"],
        decomposer_invoked=True,
    )
    mock_search_client = MagicMock()
    mock_search_client.route = AsyncMock(return_value=route_resp)

    # Decomposer selects col2 only
    route_task_result = TaskOutput(scope="small", prompt="do it")
    route_task_result.selected_collections = ["col2"]
    decomposer = _make_decomposer(route_task_result=route_task_result)

    pipeline, _ = _make_pipeline_with_search(
        decomposer=decomposer,
        search_client=mock_search_client,
    )
    provider = pipeline._search_provider
    assert provider is not None

    searched_collections: list[str] = []

    async def _mock_search(collection: str, query: str, top_k: int) -> list[dict]:
        searched_collections.append(collection)
        return [_make_search_result_json("text", 0.8, collection)]

    mock_search_client.search = AsyncMock(side_effect=_mock_search)
    await _collect_events(pipeline, "find X")

    # col2 (decomposer-selected) and always (pinned) must be searched; col1/col3 must not
    assert "col2" in searched_collections
    assert "always" in searched_collections
    assert "col1" not in searched_collections
    assert "col3" not in searched_collections


# ──────────────────────────────────────────────────────────────────────────────
# H4.5 — detail string includes collection names
# ──────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_pipeline_rag_detail_string_includes_collection_names() -> None:
    """H4.5: inject_context detail string lists the names of searched collections."""
    route_resp = _route_response(
        pre_context=None,
        pinned_names=[],
        routable_names=["alpha", "beta"],
        decomposer_invoked=False,
    )
    mock_search_client = MagicMock()
    mock_search_client.route = AsyncMock(return_value=route_resp)

    pipeline, decomposer = _make_pipeline_with_search(search_client=mock_search_client)
    provider = pipeline._search_provider
    assert provider is not None

    async def _mock_search(collection: str, query: str, top_k: int) -> list[dict]:
        return [_make_search_result_json("relevant content", 0.9, collection)]

    mock_search_client.search = AsyncMock(side_effect=_mock_search)
    await _collect_events(pipeline, "tell me about alpha and beta")

    # inject_context must have been called
    decomposer.inject_context.assert_called_once()
    call_kwargs = decomposer.inject_context.call_args
    detail: str = call_kwargs.kwargs["detail"]

    # The detail must contain the searched collection names
    assert "alpha" in detail
    assert "beta" in detail


# ──────────────────────────────────────────────────────────────────────────────
# C4.1 — No RAG when search is disabled
# ──────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_pipeline_no_rag_when_search_disabled() -> None:
    """C4.1: SearchConfig(enabled=False) → inject_context never called."""
    disabled_config = _make_search_config(enabled=False)
    mock_search_client = MagicMock()
    mock_search_client.route = AsyncMock(return_value=None)

    pipeline, decomposer = _make_pipeline_with_search(
        rag_config=disabled_config,
        search_client=mock_search_client,
    )
    assert pipeline._search_provider is not None

    events = await _collect_events(pipeline, "what is X?")

    # route() should never be called because cfg.enabled=False exits early
    mock_search_client.route.assert_not_called()
    # inject_context must not have been called at all
    decomposer.inject_context.assert_not_called()
    # Pipeline must have completed: at least one Response event delivered
    response_events = [e for e in events if isinstance(e, Response)]
    assert len(response_events) >= 1


# ──────────────────────────────────────────────────────────────────────────────
# C4.2 — No RAG when _search_provider is None
# ──────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_pipeline_no_rag_when_search_provider_is_none() -> None:
    """C4.2: No search_url → _search_provider=None → inject_context never called."""
    decomposer = _make_decomposer()
    classifier = _make_classifier()

    with patch("archon.ai.pipeline.Classifier", return_value=classifier):
        with patch("archon.ai.pipeline.Decomposer", return_value=decomposer):
            # No search_url or rag_config → _search_provider stays None
            pipeline = Pipeline()

    assert pipeline._search_provider is None

    events = await _collect_events(pipeline, "what is X?")

    # Without a search provider, inject_context must never be called
    decomposer.inject_context.assert_not_called()
    # Pipeline must have completed: at least one Response event delivered
    response_events = [e for e in events if isinstance(e, Response)]
    assert len(response_events) >= 1


# ──────────────────────────────────────────────────────────────────────────────
# C4.3 — Pipeline completes normally when route() returns None
# ──────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_pipeline_completes_normally_when_route_fails() -> None:
    """C4.3: route() returns None → Pipeline continues, response delivered."""
    mock_search_client = MagicMock()
    mock_search_client.route = AsyncMock(return_value=None)

    pipeline, decomposer = _make_pipeline_with_search(search_client=mock_search_client)

    events = await _collect_events(pipeline, "what is X?")

    # Verify the code actually entered the search path and handled the None return
    mock_search_client.route.assert_called_once()

    # Pipeline must have completed: at least one Response event delivered
    response_events = [e for e in events if isinstance(e, Response)]
    assert len(response_events) >= 1

    # inject_context must not be called when route() returns None
    decomposer.inject_context.assert_not_called()


# ──────────────────────────────────────────────────────────────────────────────
# C4.4 — No context injected when all collection searches fail
# ──────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_pipeline_completes_normally_when_all_searches_fail() -> None:
    """C4.4: All collection searches fail → no context injected, pipeline still delivers response."""
    route_resp = _route_response(
        pre_context=None,
        routable_names=["col1", "col2"],
        decomposer_invoked=False,
    )
    mock_search_client = MagicMock()
    mock_search_client.route = AsyncMock(return_value=route_resp)

    pipeline, decomposer = _make_pipeline_with_search(search_client=mock_search_client)
    provider = pipeline._search_provider
    assert provider is not None

    searched_collections: list[str] = []

    async def _failing_search(collection: str, query: str, top_k: int) -> list[dict]:
        searched_collections.append(collection)
        raise RuntimeError(f"Search failed for {collection}")

    mock_search_client.search = AsyncMock(side_effect=_failing_search)
    events = await _collect_events(pipeline, "what is X?")

    # Both collections must have been attempted
    assert set(searched_collections) == {"col1", "col2"}

    # Pipeline must complete with at least one Response event
    response_events = [e for e in events if isinstance(e, Response)]
    assert len(response_events) >= 1

    # No context should be injected when all searches fail
    decomposer.inject_context.assert_not_called()


# ──────────────────────────────────────────────────────────────────────────────
# W4.1 — Pipeline logs WARNING when get_pre_context raises
# ──────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_pipeline_logs_warning_on_phase_a_exception(caplog) -> None:
    """W4.1: Exception in get_pre_context() → WARNING 'RAG get_pre_context failed'."""
    import logging

    mock_search_client = MagicMock()
    mock_search_client.route = AsyncMock(return_value=None)

    pipeline, decomposer = _make_pipeline_with_search(search_client=mock_search_client)
    provider = pipeline._search_provider
    assert provider is not None

    archon_logger = logging.getLogger("archon")
    orig_propagate = archon_logger.propagate
    try:
        archon_logger.propagate = True
        with patch.object(
            provider,
            "get_pre_context",
            new=AsyncMock(side_effect=RuntimeError("phase A exploded")),
        ):
            with caplog.at_level(logging.DEBUG, logger="archon"):
                events = await _collect_events(pipeline, "what is X?")
    finally:
        archon_logger.propagate = orig_propagate

    warning_messages = [
        r.message for r in caplog.records if r.levelno == logging.WARNING
    ]
    assert any("RAG get_pre_context failed" in m for m in warning_messages), (
        f"Expected WARNING 'RAG get_pre_context failed' in {warning_messages}"
    )
    response_events = [e for e in events if isinstance(e, Response)]
    assert len(response_events) >= 1, "Pipeline must complete despite get_pre_context failure"


# ──────────────────────────────────────────────────────────────────────────────
# W4.2 — Pipeline logs WARNING when search_and_prepare raises
# ──────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_pipeline_logs_warning_on_phase_b_exception(caplog) -> None:
    """W4.2: Exception in search_and_prepare() → WARNING 'RAG search_and_prepare failed'."""
    import logging

    route_resp = _route_response(
        pre_context=None,
        routable_names=["docs"],
        decomposer_invoked=False,
    )
    mock_search_client = MagicMock()
    mock_search_client.route = AsyncMock(return_value=route_resp)

    pipeline, decomposer = _make_pipeline_with_search(search_client=mock_search_client)
    provider = pipeline._search_provider
    assert provider is not None

    archon_logger = logging.getLogger("archon")
    orig_propagate = archon_logger.propagate
    try:
        archon_logger.propagate = True
        with patch.object(
            provider,
            "search_and_prepare",
            new=AsyncMock(side_effect=RuntimeError("phase B exploded")),
        ):
            with caplog.at_level(logging.DEBUG, logger="archon"):
                events = await _collect_events(pipeline, "what is X?")
    finally:
        archon_logger.propagate = orig_propagate

    warning_messages = [
        r.message for r in caplog.records if r.levelno == logging.WARNING
    ]
    assert any("RAG search_and_prepare failed" in m for m in warning_messages), (
        f"Expected WARNING 'RAG search_and_prepare failed' in {warning_messages}"
    )
    response_events = [e for e in events if isinstance(e, Response)]
    assert len(response_events) >= 1, "Pipeline must complete despite search_and_prepare failure"


# ──────────────────────────────────────────────────────────────────────────────
# W4.3 — SearchClient.route ConnectError → DEBUG (not WARNING)
# ──────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_pipeline_logs_debug_on_search_client_connect_error(caplog) -> None:
    """W4.3: ConnectError in SearchClient.route() → DEBUG log, no WARNING emitted."""
    import logging

    from archon.ai.search_client import SearchClient

    # Use a mock transport so no real socket is created (avoids resource leak)
    class _ConnectErrorTransport(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("conn refused")

    real_client = SearchClient(base_url="http://localhost:8765", transport=_ConnectErrorTransport())
    pipeline, decomposer = _make_pipeline_with_search(search_client=real_client)
    provider = pipeline._search_provider

    archon_logger = logging.getLogger("archon")
    orig_propagate = archon_logger.propagate
    try:
        archon_logger.propagate = True
        with caplog.at_level(logging.DEBUG, logger="archon"):
            events = await _collect_events(pipeline, "what is X?")
    finally:
        archon_logger.propagate = orig_propagate
        if provider is not None:
            await provider.close()
        await real_client.close()

    route_debug_messages = [
        r.message for r in caplog.records
        if r.levelno == logging.DEBUG and "SearchClient.route" in r.message
    ]
    warning_messages = [
        r.message for r in caplog.records if r.levelno == logging.WARNING
    ]

    assert route_debug_messages, (
        f"Expected at least one DEBUG record from SearchClient.route, got debug records: "
        f"{[r.message for r in caplog.records if r.levelno == logging.DEBUG]}"
    )
    assert not warning_messages, (
        f"Expected no WARNING for ConnectError (should be DEBUG only), got: {warning_messages}"
    )
    response_events = [e for e in events if isinstance(e, Response)]
    assert len(response_events) >= 1, "Pipeline must complete despite ConnectError"


# ──────────────────────────────────────────────────────────────────────────────
# W4.4 — SearchClient.route HTTP 500 → WARNING with status code
# ──────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_pipeline_logs_warning_on_route_500_response(caplog) -> None:
    """W4.4: /route returns HTTP 500 → WARNING log containing the status code."""
    import logging

    from archon.ai.search_client import SearchClient

    # Use a mock transport that returns HTTP 500 (no real socket, no resource leak)
    class _HTTP500Transport(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
            return httpx.Response(500, request=request)

    real_client = SearchClient(base_url="http://localhost:8765", transport=_HTTP500Transport())
    pipeline, decomposer = _make_pipeline_with_search(search_client=real_client)
    provider = pipeline._search_provider

    archon_logger = logging.getLogger("archon")
    orig_propagate = archon_logger.propagate
    try:
        archon_logger.propagate = True
        with caplog.at_level(logging.DEBUG, logger="archon"):
            events = await _collect_events(pipeline, "what is X?")
    finally:
        archon_logger.propagate = orig_propagate
        if provider is not None:
            await provider.close()
        await real_client.close()

    warning_messages = [
        r.message for r in caplog.records if r.levelno == logging.WARNING
    ]
    assert any("HTTP 500" in m for m in warning_messages), (
        f"Expected WARNING containing 'HTTP 500' in {warning_messages}"
    )
    response_events = [e for e in events if isinstance(e, Response)]
    assert len(response_events) >= 1, "Pipeline must complete despite HTTP 500 from /route"
