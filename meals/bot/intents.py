"""Parse text into immutable intents without tools (PLAN.md Phase 4; ADR-0001, Ownership).

The pantry names, aliases and message are untrusted data. One bounded runner call validates
IntentReply; ClaudeRunnerError propagates to the bot worker for a reason-only reply and log.
"""

import json

from meals import claude_runner
from meals.contracts import Contract, Intent, PantryItem

PARSE_TIMEOUT_S = 120


class IntentReply(Contract):
    """Typed interpretations and verbatim fragments that need clarification."""

    intents: tuple[Intent, ...]
    unclear: tuple[str, ...]


def parse(text: str, *, items: tuple[PantryItem, ...]) -> IntentReply:
    """Interpret one message using only the supplied pantry vocabulary."""
    names = [{"name": item.name, "aliases": item.aliases} for item in items]
    prompt = claude_runner.load_prompt(
        "intents",
        "parse",
        items=json.dumps(names, ensure_ascii=False),
        text=json.dumps(text, ensure_ascii=False),
    )
    return claude_runner.run(prompt, schema=IntentReply, timeout=PARSE_TIMEOUT_S, web=False)
