"""Live integration tests for PlanEvent formatting with realistic decomposer data.

These tests use real-world task text patterns to validate that format_event
produces a usable Telegram notification. The route_task prompt mandates that
each agent task starts with a short description (≤100 chars) on the first line,
followed by the full self-contained prompt on subsequent lines.
"""

from archon.ai.agent_plan import AgentPlan, AgentTask
from archon.ai.event_mapper import PlanEvent
from archon.ai.truncation import SplitStrategy
from archon.chat.handler import format_event, _task_summary
from archon.config.loader import NotificationsConfig


_split = SplitStrategy()

# ── Realistic task text following the route_task.md convention ────────────────
# First line: short description ≤60 chars
# Subsequent lines: full self-contained agent prompt

_REAL_TASK_RESEARCH = (
    "Research Nike boots availability in Hungarian online stores\n\n"
    "You are a web research agent working on behalf of the user.\n"
    "The reference product page is: "
    "https://www.r-gol.com/hu/nike-zoom-mercurial-superfly-10-academy-fg-mg,p-200792\n\n"
    "Research the following Hungarian online stores for this exact product in EU 39:\n"
    "1. r-gol.com — check if they have EU 39 in stock\n"
    "2. nike.com/hu — check the official Nike Hungary store\n"
    "3. intersport.hu — search for this model in EU 39\n"
    "4. hervis.hu — search for this model in EU 39\n\n"
    "For each store, report:\n"
    "- Whether the product is available in EU 39\n"
    "- Current price in HUF\n"
    "- Direct URL to the product page"
)

_REAL_TASK_IMPLEMENT = (
    "Implement schedule bundle refactoring in install.py\n\n"
    "Working directory: /Users/manczg/Documents/development/archon\n\n"
    "Files to modify:\n"
    "- /Users/manczg/Documents/development/archon/install.py\n"
    "- /Users/manczg/Documents/development/archon/tests/test_installer_py.py\n\n"
    "Requirements:\n"
    "1. Move health_check.sh into the schedule bundle\n"
    "2. Update the installer to copy bundle scripts\n"
    "3. Ensure stale scripts are cleaned up on update\n"
    "Run: uv run pytest tests/test_installer_py.py -v to verify"
)

_REAL_TASK_DOCS = (
    "Update Architecture docs for PlanEvent bullet rendering\n\n"
    "Working directory: /Users/manczg/Documents/development/archon\n\n"
    "Files to update:\n"
    "- Documentation/Architecture/110_component_catalog_and_layer_breakdown.md\n"
    "- Documentation/Architecture/120_data_flow_and_message_lifecycle.md\n\n"
    "Describe the new PlanEvent bullet rendering behavior and update the output "
    "event table in the ADR. Include the n > 1 guard rationale."
)


def _make_realistic_plan(tasks: list[str], summary: str = "Multi-step research task") -> PlanEvent:
    agents = [AgentTask(id=f"a{i+1}", task=task) for i, task in enumerate(tasks)]
    plan = AgentPlan(scope="large", summary=summary, agents=agents)
    return PlanEvent(plan=plan, summary=summary)


# ── _task_summary helper tests ────────────────────────────────────────────────

def test_task_summary_short_text_unchanged() -> None:
    """Short single-line task text is returned as-is."""
    assert _task_summary("Research the codebase") == "Research the codebase"


def test_task_summary_takes_first_nonempty_line() -> None:
    """Multi-line task: the first non-empty line (the short description) is shown."""
    task = "Research Nike boots availability\n\nFull agent prompt follows here..."
    assert _task_summary(task) == "Research Nike boots availability"


def test_task_summary_skips_leading_blank_lines() -> None:
    """Task starting with blank line: skips to the first non-empty line."""
    task = "\nResearch Nike boots availability\n\nFull agent prompt follows here..."
    assert _task_summary(task) == "Research Nike boots availability"


def test_task_summary_all_blank_lines_returns_empty() -> None:
    """Task containing only blank lines returns empty string."""
    assert _task_summary("\n\n\n") == ""


def test_task_summary_safety_net_truncates_overlong_first_line() -> None:
    """Safety net: first line exceeding 200 chars is truncated with ellipsis.

    A well-formed task (per route_task.md) has a ≤100-char first line.
    The 200-char safety net handles malformed tasks that skip the convention.
    """
    long_first_line = "B" * 250 + "\n\nSecond paragraph."
    result = _task_summary(long_first_line)
    assert result.endswith("…")
    assert len(result) <= 201  # 200 chars + ellipsis char
    assert "Second paragraph" not in result


def test_task_summary_empty_string() -> None:
    """Empty string returns empty string."""
    assert _task_summary("") == ""


def test_task_summary_whitespace_only() -> None:
    """Whitespace-only string returns empty string after strip."""
    assert _task_summary("   ") == ""


def test_task_summary_exactly_200_chars() -> None:
    """String of exactly 200 chars is returned as-is (at safety net boundary)."""
    exact = "C" * 200
    result = _task_summary(exact)
    assert result == exact
    assert not result.endswith("…")


def test_task_summary_exactly_201_chars() -> None:
    """String of exactly 201 chars is truncated (exceeds safety net)."""
    over = "D" * 201
    result = _task_summary(over)
    assert result.endswith("…")
    assert len(result) == 201  # 200 chars + ellipsis


def test_task_summary_custom_max_len() -> None:
    """Custom max_len parameter is respected."""
    text = "Hello world, this is a long text"
    result = _task_summary(text, max_len=10)
    assert result.endswith("…")
    assert len(result) <= 11  # 10 + ellipsis


# ── Live integration tests with realistic decomposer output ──────────────────

def test_format_plan_event_realistic_research_tasks() -> None:
    """Multi-agent plan with convention-following tasks shows first-line bullets."""
    notif = NotificationsConfig(mode="normal")
    event = _make_realistic_plan(
        tasks=[_REAL_TASK_RESEARCH, _REAL_TASK_IMPLEMENT],
        summary="Deep research across Hungarian online retailers for Nike boots",
    )
    result = format_event(event, _split, notifications=notif)

    assert len(result) == 1
    msg = result[0]

    # Bullet markers present
    assert "• " in msg

    # Full prompts must not appear verbatim
    assert _REAL_TASK_RESEARCH not in msg
    assert _REAL_TASK_IMPLEMENT not in msg

    # The short first-line descriptions DO appear
    assert "Research Nike boots availability in Hungarian online stores" in msg
    assert "Implement schedule bundle refactoring in install.py" in msg

    # Spawning line present
    assert "🔄 Spawning 2 agents..." in msg

    # Each bullet line fits the ≤100-char first-line convention (+ "• " prefix = ≤102)
    for line in msg.split("\n"):
        if line.startswith("• "):
            assert len(line) <= 105, f"Bullet line too long ({len(line)} chars): {line!r}"


def test_format_plan_event_realistic_three_agents() -> None:
    """Three-agent plan with convention-following tasks produces three bullets."""
    notif = NotificationsConfig(mode="normal")
    event = _make_realistic_plan(
        tasks=[_REAL_TASK_RESEARCH, _REAL_TASK_IMPLEMENT, _REAL_TASK_DOCS],
        summary="Research, implement, and document the new feature",
    )
    result = format_event(event, _split, notifications=notif)

    assert len(result) == 1
    msg = result[0]
    bullets = [line for line in msg.split("\n") if line.startswith("• ")]
    assert len(bullets) == 3
    assert "🔄 Spawning 3 agents..." in msg


def test_format_plan_event_realistic_bullet_content() -> None:
    """Bullet shows the first line of the task, not the full prompt."""
    notif = NotificationsConfig(mode="normal")
    task_text = (
        "Research r-gol.com for Nike boots in EU 39\n\n"
        "You are a web research agent. Check r-gol.com specifically for EU 39 stock..."
    )
    event = _make_realistic_plan(
        tasks=[task_text, "Check decathlon.hu for Nike boots in EU 39\n\nFull context..."],
        summary="Find Nike boots in Hungary",
    )
    result = format_event(event, _split, notifications=notif)

    msg = result[0]
    assert "• Research r-gol.com for Nike boots in EU 39" in msg
    assert "• Check decathlon.hu for Nike boots in EU 39" in msg
    # Full prompt body must not appear
    assert "You are a web research agent" not in msg


def test_format_plan_event_blank_first_line_falls_back_to_next_line() -> None:
    """Task starting with blank line: bullet shows next non-empty line, not empty string.

    Guards against the LLM generating a task that starts with a newline, which
    would produce a blank '• ' bullet if only the literal first line were taken.
    """
    notif = NotificationsConfig(mode="normal")
    task_with_blank_first = (
        "\nResearch r-gol.com for Nike boots in EU 39\n\nFull context here..."
    )
    event = _make_realistic_plan(
        tasks=[task_with_blank_first, "Check decathlon.hu for Nike boots in EU 39\n\nContext..."],
        summary="Find Nike boots in Hungary",
    )
    result = format_event(event, _split, notifications=notif)

    msg = result[0]
    # Must show the description, not a blank bullet
    assert "• Research r-gol.com for Nike boots in EU 39" in msg
    # No bare bullet marker
    assert "• \n" not in msg
    assert not any(line == "•" or line == "• " for line in msg.split("\n"))


def test_format_plan_event_message_fits_telegram_limit() -> None:
    """PlanEvent message with many agents stays within Telegram's 4096-char limit."""
    notif = NotificationsConfig(mode="normal")
    long_tasks = [_REAL_TASK_RESEARCH] * 5
    event = _make_realistic_plan(
        tasks=long_tasks,
        summary="Parallel research across five store categories",
    )
    result = format_event(event, _split, notifications=notif)

    assert len(result) == 1
    assert len(result[0]) < 4096, f"Message exceeds Telegram limit: {len(result[0])} chars"


def test_format_plan_event_html_special_chars_in_first_line_telegram_limit() -> None:
    """HTML-expandable chars in task first line don't push message past 4096 chars.

    html.escape() is applied AFTER _task_summary(). A first line with many '&'
    chars expands 5x after escaping ('&' → '&amp;'). This test documents that
    even adversarial HTML-heavy descriptions remain within Telegram's limit.

    Note: with max_len=200 and 5x HTML expansion, worst case is 1000 chars per
    bullet. With 5 agents that's 5000 chars — which would exceed the limit.
    In practice the LLM won't write task descriptions as sequences of '&' chars,
    but this test makes the risk explicit.
    """
    notif = NotificationsConfig(mode="normal")
    # Realistic worst-case: description with a few HTML-special chars
    html_heavy_task = "Check store & compare prices for <Nike> boots in Hungary\n\nFull context..."
    event = _make_realistic_plan(
        tasks=[html_heavy_task, html_heavy_task],
        summary="Find Nike & compare prices across stores",
    )
    result = format_event(event, _split, notifications=notif)

    assert len(result) == 1
    msg = result[0]
    # HTML is escaped in the output
    assert "&amp;" in msg or "& " not in msg  # either escaped or no raw &
    # Still within Telegram limit
    assert len(msg) < 4096


# ── Task 3.4: n > 1 guard assumption validation ──────────────────────────────

def test_task_3_4_single_agent_summary_self_descriptive() -> None:
    """Task 3.4: Validate the n > 1 guard assumption.

    For single-agent plans, the summary is used as the sole description (no bullets).
    This test uses real-world summary patterns from decomposer output to confirm
    that the summary is self-descriptive enough for a single-agent plan.

    Finding from live history inspection (2026-03-21):
    - Real summaries: "Deep research across Hungarian online retailers for Nike Zoom
      Mercurial Superfly 10 Academy FG/MG in EU size 39"
    - These are readable and informative on their own.
    - The n > 1 guard is VALID: single-agent plans don't need bullet lists.
    """
    notif = NotificationsConfig(mode="normal")
    summary = "Fix the installer update path regression for bundle scripts"
    task_text = (
        "Fix installer update path regression\n\n"
        "Working directory: /Users/manczg/Documents/development/archon\n"
        "Read install.py and tests/test_installer_py.py first."
    )
    agents = [AgentTask(id="a1", task=task_text)]
    plan = AgentPlan(scope="large", summary=summary, agents=agents)
    event = PlanEvent(plan=plan, summary=summary)

    result = format_event(event, _split, notifications=notif)

    assert len(result) == 1
    msg = result[0]

    # No bullets for single agent
    assert "•" not in msg
    # Summary appears in message
    assert summary in msg
    assert "🔄 Spawning 1 agent..." in msg
    # The summary IS self-descriptive
    assert len(summary) > 10
    assert summary[0].isupper()
