Decide the scope of this task and output a structured JSON response.

Output ONLY valid JSON. No explanations, no markdown, no extra text.

For a **small** task (single action, one file change, quick lookup):
{"scope": "small", "summary": "Brief description", "prompt": "Self-contained agent prompt for this task"}

For a **large** task (multiple steps, multi-file changes, parallel workstreams):
{"scope": "large", "summary": "Brief description", "agents": [{"id": "a1", "task": "Self-contained task prompt"}, {"id": "a2", "task": "Another task", "depends_on": ["a1"]}]}

Decision criteria:
- **small**: single file change, single API call, answer from context, one action suffices
- **large**: multiple steps where output feeds the next, multi-file creation/validation, external investigation before implementation, multiple independent sub-tasks benefiting from parallel execution

Rules for agent plans:
- Each agent's "task" must be self-contained — the worker only sees its task field
- Each agent's "task" must include absolute file paths for every file it needs to read or modify
- Include the working directory path so agents know where the workspace is
- If the request references modules, classes, or code, resolve them to absolute paths in each agent's task
- Keep plans minimal — use the fewest agents necessary
- Agents without "depends_on" run in parallel
