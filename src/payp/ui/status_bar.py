"""Rich status-line formatter for the payp CLI.

Printed once per REPL iteration, directly above the prompt, so the line
stays in scrollback between turns and reflects the latest tokens / cost /
ctx / connection after every response. We deliberately don't use
prompt_toolkit's bottom_toolbar: it only renders while `prompt()` is
blocking (so it vanishes during LLM streaming) and it stacked a duplicate
bar below the Rich footer.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from rich.console import Console


# Brand tints — mirror payp accents (#A8006F magenta, #B4E04C lime).
_STYLE_MODEL = "#A8006F bold"
_STYLE_TOK = "#c9d1d9"
_STYLE_COST = "#8b949e"
_STYLE_CTX_COLD = "#8b949e"
_STYLE_CTX_HOT = "#d29922 bold"   # amber once ctx usage crosses _CTX_HOT_PCT
_STYLE_DB = "#B4E04C"
_STYLE_SEP = "#484f58"

_SEP = " \u2502 "
_CTX_HOT_PCT = 70
_MODEL_LABEL_MAX = 20


def _fmt_tok(n: int) -> str:
    """Token count: 340, 1.2k, 12k, 1.2M."""
    if n < 1000:
        return str(n)
    if n < 1_000_000:
        v = n / 1000
        return f"{v:.1f}k" if v < 100 else f"{v:.0f}k"
    return f"{n / 1_000_000:.1f}M"


def _short_model(model: str) -> str:
    """Trim provider prefix and over-long names — 'openrouter/anthropic/claude-sonnet-4.6' → 'claude-sonnet-4.6'."""
    short = model.rsplit("/", 1)[-1] if "/" in model else model
    return short if len(short) <= _MODEL_LABEL_MAX else short[: _MODEL_LABEL_MAX - 3] + "..."


def _segments(
    model: str | None,
    total_tokens: int,
    total_cost: float,
    ctx_pct: int | None,
    connection: str | None,
) -> list[tuple[str, str]]:
    """(style, text) list. Empty segments are dropped so the separator count stays honest."""
    out: list[tuple[str, str]] = []
    if model:
        out.append((_STYLE_MODEL, _short_model(model)))
    if total_tokens > 0:
        out.append((_STYLE_TOK, f"{_fmt_tok(total_tokens)} tok"))
    if total_cost > 0:
        out.append((_STYLE_COST, f"${total_cost:.4f}"))
    if ctx_pct is not None:
        style = _STYLE_CTX_HOT if ctx_pct >= _CTX_HOT_PCT else _STYLE_CTX_COLD
        out.append((style, f"ctx {ctx_pct}%"))
    if connection:
        out.append((_STYLE_DB, connection))
    return out


def print_frozen_status(
    console: "Console",
    model: str | None,
    total_tokens: int,
    total_cost: float,
    ctx_pct: int | None,
    connection: str | None,
) -> None:
    """Rich-print the same line so it stays in scrollback between prompts.

    Called after each assistant turn completes. Keeping the format identical
    to the live toolbar means the display never blinks or reformats when the
    prompt hands off to the streaming response path and back.
    """
    from rich.text import Text

    segs = _segments(model, total_tokens, total_cost, ctx_pct, connection)
    if not segs:
        return
    text = Text()
    for i, (style, s) in enumerate(segs):
        if i > 0:
            text.append(_SEP, style=_STYLE_SEP)
        text.append(s, style=style)
    console.print(text)
