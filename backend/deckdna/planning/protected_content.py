"""Internal content invariants; existing ContentUnit contract, see ADR-019."""
from __future__ import annotations

import re


def is_equation(text: str) -> bool:
    """An explicit equality/inequality is content, never a prose shortening target."""
    return bool(re.search(r"\S\s*(?:=|≤|≥|≠)\s*\S", text))


def display_math(text: str) -> str:
    """Plain editable text for common TeX notation, preserving unknown commands.

    This is deliberately not a TeX renderer. No expression is evaluated or
    simplified, and unrecognised notation is retained rather than discarded.
    """
    symbols = {
        "times": "×", "cdot": "·", "sigma": "σ", "alpha": "α", "beta": "β",
        "gamma": "γ", "delta": "δ", "Delta": "Δ", "mu": "μ", "pi": "π",
        "sum": "∑", "leq": "≤", "geq": "≥", "neq": "≠", "infty": "∞",
    }
    text = re.sub(r"\\([A-Za-z]+)\b", lambda m: symbols.get(m[1], m[0]), text)
    text = re.sub(r"\\(?:mathrm|text|operatorname)\{([^{}]*)\}", r"\1", text)
    # Keep the variable names and explicit sub/superscript operators.
    text = re.sub(r"([_^])\{([^{}]*)\}", r"\1\2", text)
    return re.sub(r"\s+", " ", text).strip()
