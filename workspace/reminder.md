# Context Reminder

Edit this file to define the constraints and instructions that Archon will
periodically re-inject into Claude sessions to prevent context drift.

The content below is wrapped in a `<system_reminder>` XML tag and sent as a
user turn whenever the configured message-count or token threshold is crossed.

Enable periodic injection in config.toml:

    [reminder]
    enabled = true
    interval_messages = 20   # inject every 20 messages
    interval_tokens = 10000  # or every 10 000 tokens — whichever comes first

---

## Your project constraints

<!-- Replace this section with your own instructions. Examples: -->

- Working directory: ~/.archon/workspace
- Always write tests before implementation (TDD).
- Prefer simple, direct solutions (KISS).
- Never delete files without explicit confirmation.
