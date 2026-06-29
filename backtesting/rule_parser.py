"""
Rule parser (Phase 2) — the brain of the backtester.

Converts a Strategy Card's plain-English entry/exit conditions into Python
boolean expressions that operate on a single DataFrame row (`df`) and the
previous row (`prev`). Uses the Claude API (claude-opus-4-8); every generated
expression is validated with `ast.parse` plus an AST whitelist before use, so
only safe, recognised column references survive.

Design goals (per spec):
- Self-contained: improve the prompt here without touching other files.
- Robust: if Claude is unavailable or returns junk, fall back to simple,
  known-good RSI rules so a backtest can still run.
"""

from __future__ import annotations

import ast
from typing import Optional

from models.strategy_card import StrategyCard
from utils.helpers import extract_json, get_env

# Claude model required by the spec for all Phase 2 API calls.
_MODEL = "claude-opus-4-8"

# Columns the generated expressions are allowed to reference. Anything else is
# treated as a hallucinated/invalid column and rejected.
ALLOWED_COLUMNS = {
    "open", "high", "low", "close", "volume",
    "rsi_14",
    "sma_20", "sma_50", "sma_200",
    "ema_20", "ema_50", "ema_200",
    "macd", "macd_signal", "macd_hist",
    "bb_upper", "bb_mid", "bb_lower",
    "adx_14", "volume_sma_20",
    "stochrsi_k", "stochrsi_d",
}
ALLOWED_NAMES = {"df", "prev"}

# Safe fallback rules — a classic RSI mean-reversion strategy.
FALLBACK_ENTRY = "df['rsi_14'] < 30"
FALLBACK_EXIT = "df['rsi_14'] > 70"

PROMPT_TEMPLATE = """\
You are a trading strategy code generator.
Convert these trading rules into Python boolean expressions.
You can only use these variables (already calculated in df):
- df['close'] - closing price
- df['rsi_14'] - RSI 14 period
- df['sma_20'], df['sma_50'], df['sma_200'] - Simple MAs
- df['ema_20'], df['ema_50'], df['ema_200'] - Exponential MAs
- df['macd'], df['macd_signal'], df['macd_hist'] - MACD
- df['bb_upper'], df['bb_lower'], df['bb_mid'] - Bollinger Bands
- df['adx_14'] - ADX
- df['volume'] - Volume
- df['volume_sma_20'] - Volume 20 period SMA
- prev (previous row values, same columns)

Return ONLY valid JSON. No explanation. No markdown:
{
  "entry_rule": "python boolean expression using df and prev",
  "exit_rule": "python boolean expression using df and prev",
  "notes": "any assumptions made"
}

Entry conditions to convert:
[ENTRY_CONDITIONS]

Exit conditions to convert:
[EXIT_CONDITIONS]
"""

# Extra guidance appended on the retry attempt.
_RETRY_SUFFIX = """

IMPORTANT (retry): Your previous answer was not valid. Respond with ONLY the
JSON object. Each rule must be a single valid Python boolean expression using
ONLY the exact variable names listed above (e.g. df['rsi_14'], prev['close']).
Do not invent column names. Do not call functions. Use and/or/not and
comparison operators only.
"""


# --- AST validation ------------------------------------------------------

class _ExprValidator(ast.NodeVisitor):
    """Reject any node/name/column not on the whitelist."""

    _ALLOWED_NODES = (
        ast.Expression, ast.BoolOp, ast.And, ast.Or, ast.UnaryOp, ast.Not,
        ast.USub, ast.UAdd, ast.BinOp, ast.Add, ast.Sub, ast.Mult, ast.Div,
        ast.Compare, ast.Lt, ast.Gt, ast.LtE, ast.GtE, ast.Eq, ast.NotEq,
        ast.Name, ast.Load, ast.Subscript, ast.Constant,
    )

    def __init__(self) -> None:
        self.error: Optional[str] = None

    def visit(self, node: ast.AST):  # noqa: D401
        if self.error:
            return
        if not isinstance(node, self._ALLOWED_NODES):
            self.error = f"disallowed syntax: {type(node).__name__}"
            return
        super().visit(node)

    def visit_Name(self, node: ast.Name) -> None:
        if node.id not in ALLOWED_NAMES:
            self.error = f"unknown variable '{node.id}'"
            return
        self.generic_visit(node)

    def visit_Subscript(self, node: ast.Subscript) -> None:
        # Must look like df['column'] / prev['column'].
        if not isinstance(node.value, ast.Name):
            self.error = "subscript must target df or prev"
            return
        key = node.slice
        # py3.9+: slice is the Constant directly.
        if isinstance(key, ast.Constant) and isinstance(key.value, str):
            if key.value not in ALLOWED_COLUMNS:
                self.error = f"unknown column '{key.value}'"
                return
        else:
            self.error = "subscript key must be a known column string"
            return
        self.generic_visit(node)


def validate_expression(expr: str) -> tuple[bool, str]:
    """Validate a generated expression.

    Returns (ok, reason). `ok` is True only when the string parses and uses
    nothing outside the whitelist.
    """
    expr = (expr or "").strip()
    if not expr:
        return False, "empty expression"
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError as exc:
        return False, f"syntax error: {exc.msg}"
    validator = _ExprValidator()
    validator.visit(tree)
    if validator.error:
        return False, validator.error
    return True, "ok"


# --- Public API ----------------------------------------------------------

def parse_strategy_rules(
    card: StrategyCard, *, use_cache: bool = True
) -> dict[str, str]:
    """Return {'entry_rule', 'exit_rule', 'notes'} for a Strategy Card.

    Uses cached rules on the card when present. Otherwise calls Claude (with one
    retry) and validates the result, falling back to simple RSI rules if needed.
    """
    # Smart-Money cards (Phase 6) don't use indicator-rule eval at all — the
    # smc_engine generates their signals. Skip parsing entirely (no LLM).
    if getattr(card, "engine", "generic") == "smc":
        return {"entry_rule": "", "exit_rule": "",
                "notes": f"Smart-Money engine ({card.engine_signal}); "
                         f"no rule parsing."}

    # 1) Use cached parsed rules if the card already has valid ones.
    if use_cache and card.entry_rule and card.exit_rule:
        ok_e, _ = validate_expression(card.entry_rule)
        ok_x, _ = validate_expression(card.exit_rule)
        if ok_e and ok_x:
            print("🧠 [RuleParser] Using cached parsed rules.")
            return {
                "entry_rule": card.entry_rule,
                "exit_rule": card.exit_rule,
                "notes": card.rule_notes or "cached",
            }

    entry_text = "\n".join(card.entry_conditions) or "(none provided)"
    exit_text = "\n".join(card.exit_conditions) or "(none provided)"

    api_key = get_env("CLAUDE_API_KEY")
    if not api_key:
        print("⚠️  [RuleParser] No CLAUDE_API_KEY — using fallback RSI rules.")
        return _fallback("no Claude API key configured")

    # 2) First attempt, then one retry with a sharper prompt.
    for attempt in (1, 2):
        print(f"🧠 [RuleParser] Asking Claude to convert rules (attempt {attempt})...")
        prompt = _build_prompt(entry_text, exit_text, retry=(attempt == 2))
        result = _call_claude(prompt, api_key)
        if result is None:
            continue

        entry_rule = str(result.get("entry_rule", "")).strip()
        exit_rule = str(result.get("exit_rule", "")).strip()
        notes = str(result.get("notes", "")).strip()

        ok_e, reason_e = validate_expression(entry_rule)
        ok_x, reason_x = validate_expression(exit_rule)
        if ok_e and ok_x:
            print("✅ [RuleParser] Rules parsed and validated.")
            return {"entry_rule": entry_rule, "exit_rule": exit_rule, "notes": notes}

        print(f"⚠️  [RuleParser] Invalid rules "
              f"(entry: {reason_e}; exit: {reason_x}). Retrying..." if attempt == 1
              else f"⚠️  [RuleParser] Still invalid (entry: {reason_e}; "
                   f"exit: {reason_x}).")

    print("⚠️  [RuleParser] Falling back to simple RSI rules.")
    return _fallback("Claude output failed validation after retry")


# --- Internals -----------------------------------------------------------

def _build_prompt(entry_text: str, exit_text: str, *, retry: bool) -> str:
    """Fill the prompt template; append retry guidance when retrying."""
    prompt = (
        PROMPT_TEMPLATE
        .replace("[ENTRY_CONDITIONS]", entry_text)
        .replace("[EXIT_CONDITIONS]", exit_text)
    )
    if retry:
        prompt += _RETRY_SUFFIX
    return prompt


def _call_claude(prompt: str, api_key: str) -> Optional[dict]:
    """Call Claude once and return the parsed JSON dict, or None on failure."""
    try:
        import anthropic
    except ImportError:
        print("❌ [RuleParser] 'anthropic' not installed.")
        return None
    try:
        client = anthropic.Anthropic(api_key=api_key)
        message = client.messages.create(
            model=_MODEL,
            max_tokens=1024,
            messages=[{"role": "user", "content": prompt}],
        )
        text = "".join(
            block.text for block in message.content
            if getattr(block, "type", None) == "text"
        )
    except Exception as exc:
        print(f"❌ [RuleParser] Claude API call failed: {exc}")
        return None
    return extract_json(text)


def _fallback(reason: str) -> dict[str, str]:
    """Return safe default RSI rules with an explanatory note."""
    return {
        "entry_rule": FALLBACK_ENTRY,
        "exit_rule": FALLBACK_EXIT,
        "notes": f"FALLBACK rules used ({reason}): buy RSI<30, sell RSI>70.",
    }
