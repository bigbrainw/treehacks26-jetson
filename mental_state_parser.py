"""
Parse Emotiv/Cortex met stream into MentalStateSnapshot.

Handles formats:
- {"met": [eng, interest, relaxation, stress, attention, ...], "time": t}
- {"met": [isActive, eng, isActive, exc, lex, isActive, str, ...], "time": t}  (alternating)
"""

from typing import Any, Optional

from data_schema import MentalStateSnapshot


# Emotiv met stream: pairs of (isActive, value) for eng, exc, lex, str, rel, int, attention
_MET_ORDER_PAIRS = [
    ("engagement", "eng"),
    ("excitement", "exc"),
    None,  # lex
    ("stress", "str"),
    ("relaxation", "rel"),
    ("interest", "int"),
    ("focus", "attention"),
]


def parse_met_to_mental_state(metrics: dict) -> MentalStateSnapshot:
    """
    Parse raw met stream into MentalStateSnapshot.

    Args:
        metrics: {"met": [...], "time": t} or {"met": {"eng": 0.5, "str": 0.4, ...}}

    Returns:
        MentalStateSnapshot with engagement, stress, relaxation, focus, etc.
    """
    met = metrics.get("met") if isinstance(metrics, dict) else metrics
    if met is None:
        return MentalStateSnapshot(metrics=metrics)

    # Already a dict of scalar values
    if isinstance(met, dict):
        key_map = {
            "eng": "engagement",
            "str": "stress",
            "rel": "relaxation",
            "attention": "focus",
            "int": "interest",
            "exc": "excitement",
        }
        result = {}
        for raw_k, v in met.items():
            if raw_k in key_map and isinstance(v, (int, float)):
                result[key_map[raw_k]] = float(v)
            elif raw_k in ("engagement", "stress", "relaxation", "focus", "excitement", "interest"):
                if isinstance(v, (int, float)):
                    result[raw_k] = float(v)
        return MentalStateSnapshot(
            engagement=result.get("engagement"),
            stress=result.get("stress"),
            relaxation=result.get("relaxation"),
            focus=result.get("focus"),
            excitement=result.get("excitement"),
            interest=result.get("interest"),
            metrics=metrics,
        )

    # List: Emotiv format (isActive, val) pairs: eng, (exc, lex), str, rel, int, attention
    # Indices: 1=eng, 3=exc, 6=str, 8=rel, 10=int, 12=attention
    arr = list(met) if isinstance(met, (list, tuple)) else []
    if len(arr) >= 13:
        def _f(i):
            return float(arr[i]) if i < len(arr) and isinstance(arr[i], (int, float)) else None
        return MentalStateSnapshot(
            engagement=_f(1),
            excitement=_f(3),
            stress=_f(6),
            relaxation=_f(8),
            interest=_f(10),
            focus=_f(12),
            metrics=metrics,
        )
    # Flat list: [eng, interest, relaxation, stress, attention, ...]
    numeric = [float(x) for x in arr if isinstance(x, (int, float)) and not isinstance(x, bool)]
    labels = ["engagement", "interest", "relaxation", "stress", "attention", "focus"]
    kw = {}
    for i, lb in enumerate(labels):
        if i < len(numeric):
            kw[lb if lb != "attention" else "focus"] = numeric[i]
    return MentalStateSnapshot(
        engagement=kw.get("engagement"),
        stress=kw.get("stress"),
        relaxation=kw.get("relaxation"),
        focus=kw.get("focus"),
        excitement=kw.get("excitement"),
        interest=kw.get("interest"),
        metrics=metrics,
    )
