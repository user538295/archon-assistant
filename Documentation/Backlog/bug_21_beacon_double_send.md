# Bug 21 — Multiple Beacons Per Interval From Parallel Agents

Status: OPEN — batching fix was attempted and reverted (unauthorized design change); tracked via xfail tests in `tests/ai/test_bugs_e2e.py` and `tests/ai/test_bugs_live.py`

## Description

When multiple background agents run in parallel, each agent sends its own independent beacon notification per interval. This means N parallel agents produce N beacon messages back-to-back every interval, instead of one consolidated beacon.

For example: 2 agents running simultaneously produce 2 beacon messages every `beacon_interval_minutes`, 3 agents produce 3 messages, etc.

## Observed in log: 2026-03-12

The user reported at 19:06:09 UTC:
> "There is another bug. There background agent beacon messages is sent twice in a row. It should be received only once in every x minutes."

At the time, two background agents were running in parallel, each firing its own beacon per interval.

## Expected Behaviour

One consolidated beacon notification per `beacon_interval_minutes` (from `config.toml [background_agents] beacon_interval_minutes`), regardless of how many agents are running in parallel. The single beacon should summarize the status of all active agents.

## Actual Behaviour

Each running agent fires its own beacon independently, producing N messages per interval for N parallel agents.

## Root Cause

Each agent spawned by `BackgroundAgentManager._run_agent()` creates its own `beacon_task` — an `asyncio.Task` running `_agent_beacon_task()`. These per-agent loops fire independently at the configured interval, with no coordination or batching across agents. When multiple agents run simultaneously, their beacon loops each send a separate Telegram notification.

Relevant code path: `_run_agent()` → `asyncio.create_task(self._agent_beacon_task(...))` — one task per agent, each with its own sleep/send cycle.

## Symptoms

- User receives N consecutive beacon messages per interval when N agents are running
- No crash or error in logs — each individual beacon works correctly in isolation
- The issue only manifests when 2+ agents run in parallel

## Test Coverage

- `tests/ai/test_bugs_e2e.py::test_bug21_parallel_agents_each_send_own_beacon` — xfail (strict)
- `tests/ai/test_bugs_live.py::test_bug21_live_parallel_agents_send_duplicate_beacons` — xfail (strict)

Both tests verify that parallel agents produce N beacons instead of 1 batched beacon, and are marked `xfail(strict=True)` to track when the bug is fixed.

## Fix Direction

Replace per-agent beacon tasks with a single manager-level beacon loop that periodically collects all active agents and sends one consolidated status message. This requires a design decision on message format and batching strategy.
