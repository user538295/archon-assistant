# ADR 07 — Pluggable Truncation Strategy via ABC

**Purpose**: Architecture decision record for the TruncationStrategy ABC pattern
**Audience**: Backend engineers
**Status**: Accepted
**Last reviewed**: 2026-02-26
**Next review**: 2026-05-26

---

## Status

Accepted

## Date

2026-02-26

## Context

Telegram enforces a hard 4096-character limit per message. Claude's responses — especially tool outputs and thinking blocks — frequently exceed this limit. The system needs a way to break long content into smaller deliverable chunks before sending to Telegram.

Two truncation strategies were immediately apparent:
1. **Split**: divide the text into N equal chunks, labelled `[1/N]`, `[2/N]`, …
2. **HeadTail**: keep the first and last portions of text (e.g. 1500 chars each), discarding the middle — useful for very long tool outputs where the start and end are most relevant

A third or fourth strategy might be needed in the future (e.g. summarise, collapse-middle). The correct approach is to make the strategy swappable without touching every call site.

## Decision

Define a `TruncationStrategy` abstract base class in `archon/ai/truncation.py` with a single abstract method:

```python
class TruncationStrategy(ABC):
    @abstractmethod
    def apply(self, text: str, max_len: int) -> list[str]:
        """Split text into chunks of at most max_len characters."""
```

Concrete implementations (`SplitStrategy`, future `HeadTailStrategy`) implement only this method. The active strategy is selected at startup via `config.toml` key `output.truncation_strategy` (string name, e.g. `"split"`). A registry dict `_STRATEGIES` maps name → class; `get_truncation_strategy(name)` returns an instance.

Although `truncation.py` exposes a `_STRATEGIES` registry and `get_truncation_strategy(name)` factory, `archon/gateway/gateway.py` does **not** use it. Instead, the gateway defines its own `_make_truncation(strategy)` function with a hardcoded `if strategy == "split"` check, raising `ConfigError` for any other value. Additionally, `archon/config/loader.py` has its own `_valid_truncation_strategies` tuple that validates the config value at load time. Both paths produce the same result for `"split"`, but adding a new strategy currently requires updating **three** locations: `_STRATEGIES` in `truncation.py`, `_make_truncation()` in `gateway.py`, and `_valid_truncation_strategies` in `loader.py`.

The MVP ships with `SplitStrategy` only. `HeadTailStrategy` is planned but not yet implemented. Note that `examples/config.toml.example` documents `"head_tail"` as a planned (but not yet implemented) strategy with `head_chars` / `tail_chars` parameters, and explicitly warns that setting `truncation_strategy = "head_tail"` will raise `ConfigError` at startup since the strategy is not yet registered.

**SplitStrategy algorithm** (as implemented in `archon/ai/truncation.py`):
1. If `len(text) <= max_len`, return `[text]` — no split.
2. Estimate N = ⌈len(text) / max_len⌉, compute label width `len("[N/N] ")`.
3. Derive `content_max = max_len − label_width`.
4. Recompute if N grows at a digit boundary.
5. Return `["[i/N] {chunk}" for each chunk]`.

## Consequences

### Positive

- Adding a new truncation mode requires changes in three places: `archon/ai/truncation.py` (`_STRATEGIES` registry), `archon/gateway/gateway.py` (`_make_truncation()`), and `archon/config/loader.py` (`_valid_truncation_strategies` validation tuple). The intent was a single-file change, but the gateway and loader each have independent checks that must also be updated (see Negative section).
- The strategy is injected at startup; all call sites receive the same interface.
- SplitStrategy's label format (`[i/N]`) lets Telegram users see the chunk count and navigate.
- The ABC makes it impossible to ship an incomplete implementation (missing `apply` raises `TypeError`).

### Negative

- The ABC adds indirection; a simple function would suffice for two strategies.
- `HeadTailStrategy` requires the `head_chars` / `tail_chars` config fields (`OutputConfig`) to already exist — they were added speculatively before the strategy was implemented.
- The name-based registry (`_STRATEGIES` dict) exists in `truncation.py` but is bypassed by both the gateway's `_make_truncation()` and the loader's `_valid_truncation_strategies` tuple — a three-way discrepancy that must be resolved when adding new strategies. A TOML typo raises `ConfigError` at startup (from the loader validation), which is intentional but may surprise users.

## Alternatives Considered

### Hardcoded if/else in the caller

The simplest approach: `if strategy == "split": ... elif strategy == "head_tail": ...` inline where messages are sent. Rejected because adding a third strategy would require finding every call site.

### Callable / function pointer

Pass a `Callable[[str, int], list[str]]` instead of an ABC instance. Simpler but loses the named type, making it harder to test, document, and enforce via mypy.

### Single configurable chunker with parameters

One class with a `mode` parameter (`"split"` or `"head_tail"`). Rejected because it conflates two distinct algorithms in one class, making unit tests and future extensions messy.

## Related Documents

- `archon/ai/truncation.py` — implementation
- [`Documentation/Architecture/500_development_workflows_and_conventions.md`](../Architecture/500_development_workflows_and_conventions.md) — KISS principle applied here
- [`Documentation/Architecture/110_component_catalog_and_layer_breakdown.md`](../Architecture/110_component_catalog_and_layer_breakdown.md) — TruncationStrategy in component inventory
