"""Model registry: immutable metadata and cost math for all active Claude models.

All pricing and IDs verified against:
  https://platform.claude.com/docs/en/docs/about-claude/models on 2026-09-02
  https://platform.claude.com/docs/en/docs/build-with-claude/prompt-caching on 2026-09-02
"""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ModelSpec:
    """Immutable metadata for one Claude model.

    Attributes:
        id: Exact API model ID string passed to the messages endpoint.
        input_usd_per_mtok: Base cost per million input tokens (USD).
        output_usd_per_mtok: Base cost per million output tokens (USD).
        cache_write_multiplier: Multiplier on input cost for 5-minute cache-write tokens.
        cache_write_1h_multiplier: Multiplier on input cost for 1-hour cache-write tokens.
        cache_read_multiplier: Multiplier on input cost for cache-read tokens.
        context_window: Max tokens the model accepts as input.
        max_output_tokens: Max tokens the model can generate in one turn.
        supports_extended_thinking: Whether this model supports any thinking mode.
        thinking_mode: "adaptive" (4.7+) or "extended" (budget_tokens, 4.5/haiku) or None.
        family: Broad capability tier: "fable" | "opus" | "sonnet" | "haiku".
        display_name: Human-readable label for UI and logs.
    """

    id: str
    input_usd_per_mtok: float
    output_usd_per_mtok: float
    cache_write_multiplier: float
    cache_write_1h_multiplier: float
    cache_read_multiplier: float
    context_window: int
    max_output_tokens: int
    supports_extended_thinking: bool
    thinking_mode: str | None
    family: str
    display_name: str


# Verified against platform.claude.com/docs/en/docs/about-claude/models on 2026-09-02
MODELS: dict[str, ModelSpec] = {
    # claude-fable-5-1: flagship reasoning model, adaptive thinking always on
    # Verified against platform.claude.com/docs/en/docs/about-claude/models on 2026-09-02
    "fable_current": ModelSpec(
        id="claude-fable-5-1",
        input_usd_per_mtok=10.00,
        output_usd_per_mtok=50.00,
        cache_write_multiplier=1.25,
        cache_write_1h_multiplier=2.00,
        cache_read_multiplier=0.025,  # 2.5% for fable-5-1 (vs 10% for others)
        context_window=1_000_000,
        max_output_tokens=128_000,
        supports_extended_thinking=True,
        thinking_mode="adaptive",
        family="fable",
        display_name="Claude Fable 5.1",
    ),
    # claude-opus-5: complex agentic coding and enterprise work
    # Verified against platform.claude.com/docs/en/docs/about-claude/models on 2026-09-02
    "opus_current": ModelSpec(
        id="claude-opus-5",
        input_usd_per_mtok=5.00,
        output_usd_per_mtok=25.00,
        cache_write_multiplier=1.25,
        cache_write_1h_multiplier=2.00,
        cache_read_multiplier=0.10,
        context_window=1_000_000,
        max_output_tokens=128_000,
        supports_extended_thinking=True,
        thinking_mode="adaptive",
        family="opus",
        display_name="Claude Opus 5",
    ),
    # claude-sonnet-5: best balance of speed and intelligence
    # Verified against platform.claude.com/docs/en/docs/about-claude/models on 2026-09-02
    "sonnet_current": ModelSpec(
        id="claude-sonnet-5",
        input_usd_per_mtok=2.00,
        output_usd_per_mtok=10.00,
        cache_write_multiplier=1.25,
        cache_write_1h_multiplier=2.00,
        cache_read_multiplier=0.10,
        context_window=1_000_000,
        max_output_tokens=128_000,
        supports_extended_thinking=True,
        thinking_mode="adaptive",
        family="sonnet",
        display_name="Claude Sonnet 5",
    ),
    # claude-haiku-4-5: fastest model; uses extended thinking (budget_tokens), not adaptive
    # Verified against platform.claude.com/docs/en/docs/about-claude/models on 2026-09-02
    "haiku_current": ModelSpec(
        id="claude-haiku-4-5-20251001",
        input_usd_per_mtok=1.00,
        output_usd_per_mtok=5.00,
        cache_write_multiplier=1.25,
        cache_write_1h_multiplier=2.00,
        cache_read_multiplier=0.10,
        context_window=200_000,
        max_output_tokens=64_000,
        supports_extended_thinking=True,
        thinking_mode="extended",  # uses thinking: {type: "enabled", budget_tokens: N}
        family="haiku",
        display_name="Claude Haiku 4.5",
    ),
}

# Default model for notebooks that don't need thinking or large context
DEFAULT_MODEL_KEY = "haiku_current"


def cost_for_call(
    model_key: str,
    input_tokens: int,
    output_tokens: int,
    cache_write_tokens: int = 0,
    cache_read_tokens: int = 0,
    cache_write_1h_tokens: int = 0,
) -> float:
    """Compute USD cost for a single Messages API call.

    Preconditions:
      - model_key exists in MODELS.
      - All token counts are non-negative integers.

    Postconditions:
      - Returns sum of input_cost + output_cost + cache_write_cost
        + cache_write_1h_cost + cache_read_cost (in USD).
      - Uses per-MTok rates and the model's cache multipliers.
      - cache_write_tokens uses the 5-minute multiplier.
      - cache_write_1h_tokens uses the 1-hour multiplier.

    Raises:
      - KeyError: model_key not in MODELS.
    """
    spec = MODELS[model_key]
    per_tok_in = spec.input_usd_per_mtok / 1_000_000
    per_tok_out = spec.output_usd_per_mtok / 1_000_000

    input_cost = input_tokens * per_tok_in
    output_cost = output_tokens * per_tok_out
    cache_write_cost = cache_write_tokens * per_tok_in * spec.cache_write_multiplier
    cache_write_1h_cost = cache_write_1h_tokens * per_tok_in * spec.cache_write_1h_multiplier
    cache_read_cost = cache_read_tokens * per_tok_in * spec.cache_read_multiplier

    return input_cost + output_cost + cache_write_cost + cache_write_1h_cost + cache_read_cost


def get_model(key: str = DEFAULT_MODEL_KEY) -> ModelSpec:
    """Return a ModelSpec by registry key.

    Preconditions:
      - key is a string.

    Postconditions:
      - Returns the ModelSpec for that key.

    Raises:
      - KeyError: key not in MODELS.
    """
    return MODELS[key]
