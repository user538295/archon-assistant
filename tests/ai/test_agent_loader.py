"""Tests for AgentLoader — S12.1.

Covers:
  - Agent dataclass / is_archon property
  - AgentLoader.load_all() — happy paths, ordering, caching
  - AgentLoader.load_all() — edge cases and error handling
  - AgentLoader.get()
  - _build_sdk_agents() conversion helper (in session_manager)
  - SessionManager accepts agent_loader
"""
import logging
from pathlib import Path
from unittest.mock import patch

import pytest

from archon.ai.agent_loader import Agent, AgentLoader
from archon.ai.session_manager import SessionManager, _build_sdk_agents


# ──────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────


def _write_agent(
    agents_dir: Path,
    name: str,
    description: str,
    body: str,
    tools: str = "",
    model: str | None = None,
    extra_fields: str = "",
) -> Path:
    """Create <agents_dir>/<name>.md with YAML frontmatter + body."""
    lines = ["---", f"name: {name}", f"description: {description}"]
    if tools:
        lines.append(f"tools: {tools}")
    if model:
        lines.append(f"model: {model}")
    if extra_fields:
        lines.append(extra_fields)
    lines += ["---", "", body]
    agent_file = agents_dir / f"{name}.md"
    agent_file.write_text("\n".join(lines), encoding="utf-8")
    return agent_file


def _write_raw(agents_dir: Path, filename: str, content: str) -> Path:
    """Write arbitrary raw content to <agents_dir>/<filename>."""
    f = agents_dir / filename
    f.write_text(content, encoding="utf-8")
    return f


# ──────────────────────────────────────────────────────────────────
# Agent dataclass — is_archon property
# ──────────────────────────────────────────────────────────────────


def test_is_archon_true_when_name_ends_with_archon_suffix() -> None:
    a = Agent(name="researcher-archon", description="d", prompt="p", tools=[], model=None)
    assert a.is_archon is True


def test_is_archon_true_for_any_archon_suffix() -> None:
    a = Agent(name="test-archon", description="d", prompt="p", tools=[], model=None)
    assert a.is_archon is True


def test_is_archon_false_when_no_suffix() -> None:
    a = Agent(name="devils-advocate", description="d", prompt="p", tools=[], model=None)
    assert a.is_archon is False


def test_is_archon_false_when_archon_in_middle() -> None:
    """'my-archon-tool' does NOT end with '-archon', so is_archon=False."""
    a = Agent(name="my-archon-tool", description="d", prompt="p", tools=[], model=None)
    assert a.is_archon is False


def test_is_archon_false_for_plain_archon_name() -> None:
    """'archon' without a dash prefix is not considered an archon agent."""
    a = Agent(name="archon", description="d", prompt="p", tools=[], model=None)
    assert a.is_archon is False


# ──────────────────────────────────────────────────────────────────
# load_all — happy paths: parsing
# ──────────────────────────────────────────────────────────────────


def test_empty_dir_returns_empty_list(tmp_path: Path) -> None:
    loader = AgentLoader(agents_dir=tmp_path)
    assert loader.load_all() == []


def test_single_archon_agent_is_parsed(tmp_path: Path) -> None:
    _write_agent(tmp_path, "coder-archon", "Expert coder", "You are a coder.", tools="Bash, Read")
    loader = AgentLoader(agents_dir=tmp_path)
    agents = loader.load_all()
    assert len(agents) == 1
    a = agents[0]
    assert isinstance(a, Agent)
    assert a.name == "coder-archon"
    assert a.description == "Expert coder"
    assert a.prompt == "You are a coder."
    assert a.tools == ["Bash", "Read"]
    assert a.model is None
    assert a.is_archon is True


def test_single_non_archon_agent_is_parsed(tmp_path: Path) -> None:
    _write_agent(tmp_path, "devils-advocate", "Critical analyst", "Challenge everything.", model="opus")
    loader = AgentLoader(agents_dir=tmp_path)
    agents = loader.load_all()
    assert len(agents) == 1
    a = agents[0]
    assert a.name == "devils-advocate"
    assert a.model == "opus"
    assert a.is_archon is False


def test_description_surrounding_quotes_are_stripped(tmp_path: Path) -> None:
    """Descriptions wrapped in double quotes (YAML convention) have quotes removed."""
    _write_raw(
        tmp_path,
        "quoted-agent.md",
        '---\nname: quoted-agent\ndescription: "A quoted description"\n---\n\nbody',
    )
    loader = AgentLoader(agents_dir=tmp_path)
    agents = loader.load_all()
    assert agents[0].description == "A quoted description"


def test_tools_parsed_from_comma_separated_string(tmp_path: Path) -> None:
    _write_agent(tmp_path, "searcher-archon", "Searcher", "body", tools="WebSearch, Read, Write")
    loader = AgentLoader(agents_dir=tmp_path)
    assert loader.load_all()[0].tools == ["WebSearch", "Read", "Write"]


def test_tools_whitespace_trimmed_around_commas(tmp_path: Path) -> None:
    _write_agent(tmp_path, "trimmer-archon", "Trimmer", "body", tools="tool1,  tool2 ,tool3")
    loader = AgentLoader(agents_dir=tmp_path)
    assert loader.load_all()[0].tools == ["tool1", "tool2", "tool3"]


def test_tools_empty_list_when_field_absent(tmp_path: Path) -> None:
    _write_agent(tmp_path, "no-tools-archon", "No tools agent", "body")
    loader = AgentLoader(agents_dir=tmp_path)
    assert loader.load_all()[0].tools == []


def test_tools_empty_list_when_field_is_empty_string(tmp_path: Path) -> None:
    _write_raw(
        tmp_path,
        "empty-tools-archon.md",
        "---\nname: empty-tools-archon\ndescription: desc\ntools: \n---\n\nbody",
    )
    loader = AgentLoader(agents_dir=tmp_path)
    assert loader.load_all()[0].tools == []


def test_model_parsed_when_present(tmp_path: Path) -> None:
    _write_agent(tmp_path, "modelled-archon", "Agent with model", "body", model="claude-sonnet-4-5")
    loader = AgentLoader(agents_dir=tmp_path)
    assert loader.load_all()[0].model == "claude-sonnet-4-5"


def test_model_is_none_when_field_absent(tmp_path: Path) -> None:
    _write_agent(tmp_path, "no-model-archon", "No model", "body")
    loader = AgentLoader(agents_dir=tmp_path)
    assert loader.load_all()[0].model is None


def test_prompt_is_body_with_frontmatter_stripped(tmp_path: Path) -> None:
    _write_agent(tmp_path, "body-archon", "desc", "# System\nYou are a helper.")
    loader = AgentLoader(agents_dir=tmp_path)
    prompt = loader.load_all()[0].prompt
    assert "name: body-archon" not in prompt
    assert "---" not in prompt
    assert "# System" in prompt
    assert "You are a helper." in prompt


def test_extra_frontmatter_fields_are_ignored(tmp_path: Path) -> None:
    """Fields like 'color' and 'memory' in frontmatter are silently ignored."""
    _write_raw(
        tmp_path,
        "extra-archon.md",
        "---\nname: extra-archon\ndescription: desc\ncolor: red\nmemory: user\n---\n\nbody",
    )
    loader = AgentLoader(agents_dir=tmp_path)
    agents = loader.load_all()
    assert len(agents) == 1
    assert agents[0].name == "extra-archon"


# ──────────────────────────────────────────────────────────────────
# load_all — happy paths: ordering
# ──────────────────────────────────────────────────────────────────


def test_archon_agents_appear_before_non_archon_agents(tmp_path: Path) -> None:
    _write_agent(tmp_path, "zzz-archon", "Last alpha archon", "body")
    _write_agent(tmp_path, "aaa-regular", "First alpha regular", "body")
    loader = AgentLoader(agents_dir=tmp_path)
    agents = loader.load_all()
    assert agents[0].name == "zzz-archon"
    assert agents[1].name == "aaa-regular"


def test_archon_agents_sorted_alphabetically_within_group(tmp_path: Path) -> None:
    _write_agent(tmp_path, "zebra-archon", "Zebra", "body")
    _write_agent(tmp_path, "alpha-archon", "Alpha", "body")
    _write_agent(tmp_path, "mango-archon", "Mango", "body")
    loader = AgentLoader(agents_dir=tmp_path)
    archon = [a for a in loader.load_all() if a.is_archon]
    assert [a.name for a in archon] == ["alpha-archon", "mango-archon", "zebra-archon"]


def test_non_archon_agents_sorted_alphabetically_within_group(tmp_path: Path) -> None:
    _write_agent(tmp_path, "zebra", "Zebra", "body")
    _write_agent(tmp_path, "alpha", "Alpha", "body")
    _write_agent(tmp_path, "mango", "Mango", "body")
    loader = AgentLoader(agents_dir=tmp_path)
    others = [a for a in loader.load_all() if not a.is_archon]
    assert [a.name for a in others] == ["alpha", "mango", "zebra"]


def test_mix_of_archon_and_non_archon_sorted_correctly(tmp_path: Path) -> None:
    _write_agent(tmp_path, "beta", "Beta regular", "body")
    _write_agent(tmp_path, "zebra-archon", "Zebra archon", "body")
    _write_agent(tmp_path, "alpha", "Alpha regular", "body")
    _write_agent(tmp_path, "aardvark-archon", "Aardvark archon", "body")
    loader = AgentLoader(agents_dir=tmp_path)
    names = [a.name for a in loader.load_all()]
    # archon group first (alpha sorted), then others (alpha sorted)
    assert names == ["aardvark-archon", "zebra-archon", "alpha", "beta"]


# ──────────────────────────────────────────────────────────────────
# load_all — caching
# ──────────────────────────────────────────────────────────────────


def test_load_all_returns_same_list_object_on_second_call(tmp_path: Path) -> None:
    _write_agent(tmp_path, "cached-archon", "desc", "body")
    loader = AgentLoader(agents_dir=tmp_path)
    first = loader.load_all()
    second = loader.load_all()
    assert first is second  # same object — cached


# ──────────────────────────────────────────────────────────────────
# load_all — edge cases
# ──────────────────────────────────────────────────────────────────


def test_nonexistent_dir_returns_empty_list_and_logs_warning(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    loader = AgentLoader(agents_dir=tmp_path / "does-not-exist")
    with caplog.at_level(logging.WARNING, logger="archon"):
        result = loader.load_all()
    assert result == []
    assert any("does-not-exist" in r.message for r in caplog.records)


def test_file_without_frontmatter_skipped_with_warning(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    _write_raw(tmp_path, "no-fm-archon.md", "just plain text, no frontmatter at all")
    with caplog.at_level(logging.WARNING, logger="archon"):
        loader = AgentLoader(agents_dir=tmp_path)
        result = loader.load_all()
    assert result == []
    assert any("no-fm-archon" in r.message for r in caplog.records)


def test_file_missing_name_field_skipped_with_warning(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    _write_raw(
        tmp_path,
        "no-name.md",
        "---\ndescription: Some description\ntools: Bash\n---\n\nbody",
    )
    with caplog.at_level(logging.WARNING, logger="archon"):
        loader = AgentLoader(agents_dir=tmp_path)
        result = loader.load_all()
    assert result == []
    assert any("name" in r.message for r in caplog.records)


def test_file_missing_description_field_skipped_with_warning(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    _write_raw(
        tmp_path,
        "no-desc.md",
        "---\nname: no-desc-agent\ntools: Bash\n---\n\nbody",
    )
    with caplog.at_level(logging.WARNING, logger="archon"):
        loader = AgentLoader(agents_dir=tmp_path)
        result = loader.load_all()
    assert result == []
    assert any("description" in r.message for r in caplog.records)


def test_unclosed_frontmatter_fence_skipped_with_warning(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    _write_raw(
        tmp_path,
        "unclosed.md",
        "---\nname: unclosed-agent\ndescription: desc\nbody without closing fence",
    )
    with caplog.at_level(logging.WARNING, logger="archon"):
        loader = AgentLoader(agents_dir=tmp_path)
        result = loader.load_all()
    assert result == []


def test_non_md_file_in_dir_is_ignored(tmp_path: Path) -> None:
    """Files that don't end in .md are silently skipped."""
    _write_raw(tmp_path, "agent.txt", "---\nname: text-agent\ndescription: desc\n---\n\nbody")
    _write_raw(tmp_path, "agent.yaml", "name: yaml-agent")
    loader = AgentLoader(agents_dir=tmp_path)
    assert loader.load_all() == []


def test_directory_entry_in_agents_dir_is_ignored(tmp_path: Path) -> None:
    """Subdirectories inside agents_dir are not processed."""
    subdir = tmp_path / "subagent-archon"
    subdir.mkdir()
    (subdir / "AGENT.md").write_text("---\nname: sub\ndescription: d\n---\nbody")
    loader = AgentLoader(agents_dir=tmp_path)
    assert loader.load_all() == []


def test_unreadable_file_skipped_with_warning(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    _write_agent(tmp_path, "unreadable-archon", "desc", "body")
    with patch("pathlib.Path.read_text", side_effect=OSError("permission denied")):
        with caplog.at_level(logging.WARNING, logger="archon"):
            loader = AgentLoader(agents_dir=tmp_path)
            result = loader.load_all()
    assert result == []
    assert any(
        "permission denied" in r.message or "Could not read" in r.message
        for r in caplog.records
    )


def test_mixed_valid_and_invalid_files_returns_only_valid(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    _write_agent(tmp_path, "good-archon", "Good agent", "good body")
    _write_raw(tmp_path, "bad.md", "no frontmatter here")
    with caplog.at_level(logging.WARNING, logger="archon"):
        loader = AgentLoader(agents_dir=tmp_path)
        result = loader.load_all()
    assert len(result) == 1
    assert result[0].name == "good-archon"


# ──────────────────────────────────────────────────────────────────
# get
# ──────────────────────────────────────────────────────────────────


def test_get_returns_agent_by_exact_name(tmp_path: Path) -> None:
    _write_agent(tmp_path, "target-archon", "Target desc", "target body")
    _write_agent(tmp_path, "other-archon", "Other desc", "other body")
    loader = AgentLoader(agents_dir=tmp_path)
    agent = loader.get("target-archon")
    assert agent is not None
    assert agent.name == "target-archon"


def test_get_returns_none_for_unknown_name(tmp_path: Path) -> None:
    _write_agent(tmp_path, "existing-archon", "desc", "body")
    loader = AgentLoader(agents_dir=tmp_path)
    assert loader.get("nonexistent") is None


def test_get_returns_none_when_no_agents(tmp_path: Path) -> None:
    loader = AgentLoader(agents_dir=tmp_path)
    assert loader.get("anything") is None


# ──────────────────────────────────────────────────────────────────
# _build_sdk_agents — conversion from Agent list to SDK dict
# ──────────────────────────────────────────────────────────────────


def test_build_sdk_agents_none_input_returns_none() -> None:
    assert _build_sdk_agents(None) is None


def test_build_sdk_agents_empty_list_returns_none() -> None:
    assert _build_sdk_agents([]) is None


def test_build_sdk_agents_converts_agent_list_to_dict() -> None:
    agents = [
        Agent(
            name="researcher",
            description="Web researcher",
            prompt="You research the web.",
            tools=["WebSearch", "Read"],
            model="haiku",
        ),
        Agent(
            name="coder",
            description="Expert coder",
            prompt="You write code.",
            tools=[],
            model=None,
        ),
    ]
    result = _build_sdk_agents(agents)
    assert result is not None
    assert set(result.keys()) == {"researcher", "coder"}

    researcher = result["researcher"]
    assert researcher.description == "Web researcher"
    assert researcher.prompt == "You research the web."
    assert researcher.tools == ["WebSearch", "Read"]
    assert researcher.model == "haiku"

    coder = result["coder"]
    assert coder.tools is None  # empty list → None for SDK


def test_build_sdk_agents_empty_tools_passes_none_to_sdk() -> None:
    agents = [Agent(name="notoolz", description="d", prompt="p", tools=[], model=None)]
    result = _build_sdk_agents(agents)
    assert result is not None
    assert result["notoolz"].tools is None


def test_build_sdk_agents_model_none_passed_through() -> None:
    agents = [Agent(name="a", description="d", prompt="p", tools=[], model=None)]
    result = _build_sdk_agents(agents)
    assert result is not None
    assert result["a"].model is None


def test_build_sdk_agents_preserves_model_string() -> None:
    agents = [Agent(name="a", description="d", prompt="p", tools=[], model="claude-sonnet-4-5")]
    result = _build_sdk_agents(agents)
    assert result is not None
    assert result["a"].model == "claude-sonnet-4-5"


# ──────────────────────────────────────────────────────────────────
# SessionManager — agent_loader parameter
# ──────────────────────────────────────────────────────────────────


def test_session_manager_accepts_agent_loader(tmp_path: Path) -> None:
    loader = AgentLoader(agents_dir=tmp_path)
    mgr = SessionManager(timeout=60, agent_loader=loader)
    assert mgr._agent_loader is loader


def test_session_manager_agent_loader_defaults_to_none() -> None:
    mgr = SessionManager(timeout=60)
    assert mgr._agent_loader is None


# ──────────────────────────────────────────────────────────────────
# CRLF line endings
# ──────────────────────────────────────────────────────────────────


def test_crlf_frontmatter_parses_agent_correctly(tmp_path: Path) -> None:
    """Agent .md files with Windows CRLF line endings are parsed correctly."""
    crlf_content = (
        "---\r\n"
        "name: crlf-archon\r\n"
        "description: CRLF agent\r\n"
        "tools: Bash, Read\r\n"
        "---\r\n"
        "\r\n"
        "Agent body here."
    )
    _write_raw(tmp_path, "crlf-archon.md", crlf_content)
    loader = AgentLoader(agents_dir=tmp_path)
    agents = loader.load_all()
    assert len(agents) == 1
    a = agents[0]
    assert a.name == "crlf-archon"
    assert a.description == "CRLF agent"
    assert a.tools == ["Bash", "Read"]
    assert "Agent body here." in a.prompt


# ──────────────────────────────────────────────────────────────────
# Multiline YAML tools list — warning behaviour
# ──────────────────────────────────────────────────────────────────


def test_multiline_tools_list_produces_warning(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Multiline YAML list-style tools field is not parsed and triggers a warning."""
    _write_raw(
        tmp_path,
        "multitools-archon.md",
        "---\nname: multitools-archon\ndescription: desc\ntools:\n  - Read\n  - Write\n---\n\nbody",
    )
    with caplog.at_level(logging.WARNING, logger="archon"):
        loader = AgentLoader(agents_dir=tmp_path)
        agents = loader.load_all()
    # Agent is still loaded (tools defaults to empty), but a warning is emitted
    assert len(agents) == 1
    assert agents[0].tools == []
    assert any("tools" in r.message for r in caplog.records)


def test_tools_absent_does_not_produce_tools_warning(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """When 'tools:' key is not present at all, no tools warning is emitted."""
    _write_agent(tmp_path, "no-tools-warn-archon", "desc", "body")
    with caplog.at_level(logging.WARNING, logger="archon"):
        loader = AgentLoader(agents_dir=tmp_path)
        loader.load_all()
    assert not any(
        "multiline" in r.message or ("tools" in r.message and "format" in r.message)
        for r in caplog.records
    )


async def test_session_manager_default_factory_calls_load_all_on_agent_loader(
    tmp_path: Path,
) -> None:
    """Default factory must call agent_loader.load_all() when creating a session."""
    from unittest.mock import AsyncMock, MagicMock, patch

    mock_agent_loader = MagicMock()
    mock_agent_loader.load_all.return_value = []
    mock_agent_loader.build_sdk_agents.return_value = None

    mgr = SessionManager(timeout=60, agent_loader=mock_agent_loader)

    mock_session = MagicMock()
    mock_session.start = AsyncMock()
    with patch("archon.ai.session_manager.Pipeline", return_value=mock_session):
        await mgr.get_or_create(user_id=1)

    mock_agent_loader.load_all.assert_called_once()
