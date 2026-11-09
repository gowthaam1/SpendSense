"""Categorize the transactions the rules couldn't handle, using Claude.

Three things make this reliable enough to trust with financial data:

1. Structured output. The model must call a "tool" whose input follows a JSON
   schema, so we always get {id, category, confidence} back instead of prose.
2. Validation. Every result is checked with Pydantic. A made-up category, a
   missing id or a confidence outside 0-1 is rejected rather than trusted.
3. Fallback. If there's no API key, the call fails, or a result is invalid,
   the transaction is marked "Other" with confidence 0 so it lands in the
   review queue. The app never crashes because the AI misbehaved.
"""

from __future__ import annotations

import logging
import os

from pydantic import BaseModel, Field, ValidationError, field_validator

from . import CATEGORIES

log = logging.getLogger(__name__)

DEFAULT_MODEL = os.environ.get("SPENDSENSE_MODEL", "claude-haiku-5-5")
BATCH_SIZE = 40

SYSTEM_PROMPT = f"""You categorize Canadian personal bank transactions.
Use only these categories: {", ".join(CATEGORIES)}.
Personal details have been replaced with tags like [PERSON] or [NUMBER]; ignore them.
Confidence is your probability (0 to 1) that the category is correct. Use a low
confidence (below 0.6) when the merchant is unfamiliar or could reasonably fit
more than one category. Never invent a category outside the list."""

CATEGORIZE_TOOL = {
    "name": "record_categories",
    "description": "Record a category for every transaction you were given.",
    "input_schema": {
        "type": "object",
        "properties": {
            "results": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "integer"},
                        "category": {"type": "string", "enum": CATEGORIES},
                        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                    },
                    "required": ["id", "category", "confidence"],
                },
            }
        },
        "required": ["results"],
    },
}


class CategoryResult(BaseModel):
    id: int
    category: str
    confidence: float = Field(ge=0, le=1)

    @field_validator("category")
    @classmethod
    def known_category(cls, value: str) -> str:
        if value not in CATEGORIES:
            raise ValueError(f"unknown category {value!r}")
        return value


FALLBACK = {"category": "Other", "confidence": 0.0, "source": "fallback"}


def get_client(api_key: str | None = None):
    """Return an Anthropic client, or None if no key is configured."""
    key = api_key or os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        return None
    import anthropic  # imported lazily so the app works without the package configured
    return anthropic.Anthropic(api_key=key)


def parse_tool_results(raw_results: list, expected_ids: set[int]) -> dict[int, dict]:
    """Validate the model's output. Anything invalid or missing gets the fallback."""
    parsed: dict[int, dict] = {}
    for item in raw_results:
        try:
            result = CategoryResult.model_validate(item)
        except ValidationError as err:
            log.warning("Rejected invalid model output %r: %s", item, err)
            continue
        if result.id in expected_ids:
            parsed[result.id] = {"category": result.category,
                                 "confidence": result.confidence, "source": "ai"}
    for missing in expected_ids - parsed.keys():
        parsed[missing] = dict(FALLBACK)
    return parsed


def categorize_with_llm(items: list[tuple[int, str]], client=None,
                        model: str = DEFAULT_MODEL) -> dict[int, dict]:
    """Categorize (id, masked_description) pairs. Returns {id: result}."""
    if not items:
        return {}
    if client is None:
        return {i: dict(FALLBACK) for i, _ in items}

    results: dict[int, dict] = {}
    for start in range(0, len(items), BATCH_SIZE):
        batch = items[start:start + BATCH_SIZE]
        lines = "\n".join(f"{i}: {desc}" for i, desc in batch)
        ids = {i for i, _ in batch}
        try:
            response = client.messages.create(
                model=model,
                max_tokens=4096,
                system=SYSTEM_PROMPT,
                tools=[CATEGORIZE_TOOL],
                tool_choice={"type": "tool", "name": CATEGORIZE_TOOL["name"]},
                messages=[{"role": "user", "content": f"Categorize these transactions:\n{lines}"}],
            )
            tool_input = next(
                (b.input for b in response.content if getattr(b, "type", "") == "tool_use"), {}
            )
            results.update(parse_tool_results(tool_input.get("results", []), ids))
        except Exception as err:  # network, auth, rate limit, malformed response...
            log.warning("AI categorization failed for a batch, using fallback: %s", err)
            results.update({i: dict(FALLBACK) for i in ids})
    return results
