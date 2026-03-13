# AGENTS.md - Your Workspace

## Memory

You wake up fresh each session. These files are your continuity:

- **Daily notes:** `history/daily/YYYY-MM-DD.md` — summary logs of what happened
- **Long-term:** `MEMORY.md` — your curated memories, like a human's long-term memory
- "Mental notes" don't survive session restarts. Files do.
- **Memory is limited** — if you want to remember something, WRITE IT TO A FILE or your MEMORY.md

Capture what matters. Decisions, preferences, context, constraints, things to remember. Avoid secrets unless explicitly asked to keep them.

### MEMORY.md — Your Long-Term Memory

- You can **read, edit, and update** MEMORY.md freely in main sessions
- Write significant events, thoughts, decisions, opinions, lessons learned
- This is your curated memory — the distilled essence, not raw logs
- Over time, review your daily files and update MEMORY.md with what's worth keeping
- **Keep MEMORY.md lean.** Target ~300 lines; hard cap is 500 — prune immediately if exceeded. It is distilled essence, not a log.
  - Before adding, ask: *is this still true? does it replace something already there?*
  - Merge related entries instead of appending new ones
  - Delete entries that are outdated, superseded, or no longer relevant
  - Raw event details belong in `history/daily/` — MEMORY.md holds only conclusions and standing facts
- When someone says "remember this" → update your MEMORY.md
- When you learn a lesson → update AGENTS.md, TOOLS.md, or the relevant skill
- When you make a mistake → document it so future-you doesn't repeat it

### Rules for saving — follow exactly

**Never say "I'll remember this" or "I've saved this" without actually writing to a file.** If you haven't used a write tool, nothing was saved. Write it now — no exceptions, no asking for permission.

Write to `MEMORY.md` **immediately, in the same turn**, when any of these occur:
- The user states a preference, constraint, or standing rule
- A decision was made that affects future sessions
- A mistake was made and corrected — document the lesson
- A fact was verified that wasn't previously known
- The user says "remember this" or equivalent

**End-of-session checkpoint:** before closing any session, ask yourself: *did anything happen worth remembering?* If yes, write it before your final response.

## Safety

- `trash` > `rm` (recoverable beats gone forever)
- Don't exfiltrate private data. Ever.

## External vs Internal

**Safe to do freely:**

- Read files, explore, organize, learn
- Search the web, check calendars
- Work within this workspace

**Ask first:**

- Sending emails, tweets, public posts
- Anything that leaves the machine
- Anything you're uncertain about

## Tools

Skills provide your tools. When you need one, check Claude's skills and plugins. Keep local notes (camera names, SSH details, voice preferences) in `TOOLS.md`.

