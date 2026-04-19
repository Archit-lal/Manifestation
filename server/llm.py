"""
Claude Haiku 4.5 wrapper for reply suggestions.

Single API call per request returns:
  {intent: "new"|"continuation", reasoning: str, replies: [str, str, str, str]}

System prompt is prompt-cached for speed. JSON is parsed strictly; retries once
on parse failure with an explicit correction request.

History cutoff: last 20 messages OR last 2 hours, whichever is shorter. Hard
token cap of ~4000 chars on the history section.
"""

import json
import os
import time
from typing import Optional

import anthropic

from server.messages_db import Message

_client: Optional[anthropic.Anthropic] = None


def _get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        key = os.environ.get("ANTHROPIC_API_KEY")
        if not key:
            raise RuntimeError("ANTHROPIC_API_KEY environment variable not set.")
        _client = anthropic.Anthropic(api_key=key)
    return _client


SYSTEM_PROMPT = """You help a non-verbal user reply to iMessages using a Brain-Computer Interface (BCI). The user navigates choices by blinking and selects by clenching their jaw — so your suggestions must be short, natural, and meaningful.

Return ONLY valid JSON — no prose, no markdown, no code fences:

{
  "intent": "new" | "continuation",
  "reasoning": "<one sentence>",
  "replies": ["<reply 1>", "<reply 2>", "<reply 3>", "<reply 4>"]
}

INTENT RULES:
- "new": last message is from_me, OR last inbound message is >6 hours old, OR there are no messages in the thread yet.
- "continuation": there is a recent inbound message awaiting a reply.

REPLY RULES:
- 4 replies, each usually <12 words. Short enough to read at a glance on a BCI display.
- Meaningful variety: one short affirmative/acknowledgment, one question to the other person, one substantive or action-oriented reply, one tonal outlier (warm / playful / firm depending on the thread mood).
- Sound like casual spoken text, not email. No formality unless the thread is formal.
- Never invent facts about the user's schedule, location, or plans.
- Include emoji only if the other person used emoji in recent messages.
- Match the thread register: intimate with family/close friends, professional if needed.
- Never begin a reply with "As an AI" or any AI self-reference.
- If intent is "new", generate openers appropriate for starting fresh (check-ins, sharing something, asking about them).
"""

MODEL = "claude-haiku-4-5-20251001"
MAX_HISTORY_CHARS = 4000
MAX_MESSAGES = 20
CUTOFF_HOURS = 2


def _trim_history(messages: list[Message]) -> list[Message]:
    """Apply cutoff: last 20 msgs OR last 2 hours, always keep at least 1."""
    if not messages:
        return []
    now = time.time()
    cutoff_ts = now - CUTOFF_HOURS * 3600

    # Window: last 20 msgs within 2 hours
    recent = [m for m in messages[-MAX_MESSAGES:] if m.ts >= cutoff_ts]
    if not recent:
        recent = messages[-1:]  # always include at least 1

    # Hard char cap
    total = 0
    kept = []
    for m in reversed(recent):
        total += len(m.text)
        if total > MAX_HISTORY_CHARS and kept:
            break
        kept.append(m)
    return list(reversed(kept))


def _format_history(messages: list[Message]) -> str:
    if not messages:
        return "(No previous messages in this thread.)"
    lines = []
    for m in messages:
        role = "Me" if m.from_me else "Them"
        ts_str = time.strftime("%H:%M", time.localtime(m.ts))
        lines.append(f"[{ts_str}] {role}: {m.text}")
    return "\n".join(lines)


def _parse_response(text: str) -> dict:
    text = text.strip()
    # Strip accidental markdown fences
    if text.startswith("```"):
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]
    return json.loads(text.strip())


def suggest_replies(
    contact_name: str,
    thread: list[Message],
    extra_context: str = "",
) -> dict:
    """
    Returns {intent, reasoning, replies}.
    Raises on unrecoverable LLM or parse error.
    """
    client = _get_client()
    trimmed = _trim_history(thread)
    history_text = _format_history(trimmed)

    parts = [
        f"Contact: {contact_name}",
        f"Thread ({len(trimmed)} messages shown):\n{history_text}",
    ]
    if extra_context:
        parts.append(f"Additional context: {extra_context}")
    user_content = "\n\n".join(parts)

    messages = [{"role": "user", "content": user_content}]

    def _call(msgs):
        return client.messages.create(
            model=MODEL,
            max_tokens=512,
            system=[
                {
                    "type": "text",
                    "text": SYSTEM_PROMPT,
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            messages=msgs,
        )

    response = _call(messages)
    raw = response.content[0].text

    try:
        result = _parse_response(raw)
    except (json.JSONDecodeError, IndexError):
        # Retry with correction
        messages.append({"role": "assistant", "content": raw})
        messages.append({
            "role": "user",
            "content": (
                "Your response was not valid JSON. "
                "Return ONLY the JSON object, nothing else."
            ),
        })
        response = _call(messages)
        raw = response.content[0].text
        result = _parse_response(raw)

    # Validate shape
    intent = result.get("intent", "continuation")
    if intent not in ("new", "continuation"):
        intent = "continuation"
    replies = result.get("replies", [])
    if not isinstance(replies, list):
        replies = []
    replies = [str(r).strip() for r in replies if str(r).strip()][:4]
    if not replies:
        replies = ["OK", "Got it", "Sure", "Let me think"]

    return {
        "intent": intent,
        "reasoning": str(result.get("reasoning", "")),
        "replies": replies,
    }
