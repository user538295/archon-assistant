You are re-evaluating a classification that had low confidence.

Given the user message and the original classification, output an updated assessment.

Output ONLY valid JSON. No explanations, no markdown, no extra text.

Schema:
{"intent": "chat" | "task", "confidence": 0.0-1.0, "estimated_tools": <integer>}

Fields:
- "intent": your best judgment — "chat" for conversational, "task" for actionable
- "confidence": how confident you are in this assessment (0.0-1.0)
- "estimated_tools": how many distinct tools/steps would this task require (0 for chat)

Use your full understanding of the message to make a better decision than the fast classifier.
