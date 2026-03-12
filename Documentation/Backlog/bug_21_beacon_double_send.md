# Bug 21 — Background Agent Beacon Sent Twice in a Row

Status: OPEN

## Description

Background agent beacon messages (heartbeat notifications sent to Telegram to indicate an agent is still running) are received **twice in a row** by the user. They should be sent exactly once per configured interval (e.g., every X minutes).

## Observed in log: 2026-03-12

The user reported at 19:06:09 UTC:
> "There is another bug. There background agent beacon messages is sent twice in a row. It should be received only once in every x minutes."

## Expected Behaviour

For each running background agent, one beacon notification sent per `beacon_interval_minutes` (from `config.toml [background_agents] beacon_interval_minutes`). No duplicate beacons within the same interval.

## Actual Behaviour

The user receives two beacon messages back-to-back for the same running agent within the same interval window.

## Likely Root Causes (without source inspection)

1. **Double registration**: The beacon task for an agent may be started twice — once when the agent is first spawned and once during a reconnect, restart, or re-promotion event. Both tasks run independently and each fires at the configured interval.

2. **Race condition on completion**: If the beacon task for a completed agent is not cancelled promptly, it may fire one final time after the agent has already stopped. Combined with the beacon for the next run or a new agent, the user sees two messages.

3. **Task leak across waves**: If agents are spawned in multiple waves and beacon tasks are not scoped per-agent-run, old beacon tasks from a completed wave may keep firing alongside new ones.

## Symptoms

- User receives 2 consecutive beacon messages for the same agent
- Duplicate beacons appear within the same configured interval
- No apparent crash or error in logs

## Tasks

1. Read `archon/ai/background_agent_manager.py` — find beacon task creation and lifecycle
2. Identify where the beacon loop is started and ensure it is started exactly once per agent run
3. Verify beacon tasks are cancelled immediately when their agent completes or is cancelled
4. Write failing e2e tests confirming double-send bug
5. Fix the beacon task lifecycle to prevent double registration
6. Run full test suite
