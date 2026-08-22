"""ISBD trailing punctuation rules for MARC fields.

Applies ISBD (International Standard Bibliographic Description) trailing
punctuation to MARC subfields based on which subfields are present and
their order. Punctuation is applied dynamically at emit time, not stored
in the BFFI graph.

Rules are defined per MARC tag family. Each tag has a ``subfield_order``
defining the canonical ISBD order and ``punctuation_after`` mapping each
subfield to its trailing punctuation based on the next subfield (or
``last`` if it's the final subfield).

Usage::

    from bffi_pipeline.stages.bffi_to_marc.isbd import apply_isbd_punctuation

    # With punctuation enabled
    text = apply_isbd_punctuation(
        text="Moskva",
        tag="260",
        subfield_codes=("a", "b", "c"),
        enabled=True,
    )
    # Returns: "Moskva :"

    # With punctuation disabled (default)
    text = apply_isbd_punctuation(
        text="Moskva",
        tag="260",
        subfield_codes=("a", "b", "c"),
        enabled=False,
    )
    # Returns: "Moskva"

The toggle is controlled by the ``enabled`` parameter. When ``False``
(the default), the function returns the text unchanged — a fast path
that avoids rule lookups for runs that don't need ISBD punctuation.
"""

from __future__ import annotations

from typing import Any, Final

#: ISBD punctuation rules per MARC tag family.
#:
#: Each entry maps:
#:   - ``subfield_order``: canonical ISBD subfield order (used for validation)
#:   - ``punctuation_after``: for each subfield, a dict mapping the next
#:     subfield code to the trailing punctuation, plus a ``"last"`` key
#:     for the final subfield.
ISBD_RULES: Final[dict[str, dict[str, Any]]] = {
    "100": {
        "subfield_order": ["a", "b", "e", "f", "4"],
        "punctuation_after": {
            "a": {"last": "", "next": {"b": ",", "e": ",", "f": ","}},
            "b": {"last": ",", "next": {"e": ",", "f": ","}},
            "e": {"last": ".", "next": {"f": ","}},
            "f": {"last": ".", "next": {"4": ","}},
            "4": {"last": "."},
        },
    },
    "700": {
        # Same rules as 100 — ISBD punctuation is tag-agnostic
        "subfield_order": ["a", "b", "e", "f", "4"],
        "punctuation_after": {
            "a": {"last": "", "next": {"b": ",", "e": ",", "f": ","}},
            "b": {"last": ",", "next": {"e": ",", "f": ","}},
            "e": {"last": ".", "next": {"f": ","}},
            "f": {"last": ".", "next": {"4": ","}},
            "4": {"last": "."},
        },
    },
    "110": {
        "subfield_order": ["a", "e", "f", "4"],
        "punctuation_after": {
            "a": {"last": "", "next": {"e": ",", "f": ","}},
            "e": {"last": ".", "next": {"f": ","}},
            "f": {"last": ".", "next": {"4": ","}},
            "4": {"last": "."},
        },
    },
    "111": {
        "subfield_order": ["a", "e", "f", "4"],
        "punctuation_after": {
            "a": {"last": "", "next": {"e": ",", "f": ","}},
            "e": {"last": ".", "next": {"f": ","}},
            "f": {"last": ".", "next": {"4": ","}},
            "4": {"last": "."},
        },
    },
    "710": {
        # Same rules as 110 — ISBD punctuation is tag-agnostic
        "subfield_order": ["a", "e", "f", "4"],
        "punctuation_after": {
            "a": {"last": "", "next": {"e": ",", "f": ","}},
            "e": {"last": ".", "next": {"f": ","}},
            "f": {"last": ".", "next": {"4": ","}},
            "4": {"last": "."},
        },
    },
    "711": {
        # Same rules as 111 — ISBD punctuation is tag-agnostic
        "subfield_order": ["a", "e", "f", "4"],
        "punctuation_after": {
            "a": {"last": "", "next": {"e": ",", "f": ","}},
            "e": {"last": ".", "next": {"f": ","}},
            "f": {"last": ".", "next": {"4": ","}},
            "4": {"last": "."},
        },
    },
    "245": {
        "subfield_order": ["a", "b", "f", "g", "c"],
        "punctuation_after": {
            "a": {"last": "", "next": {"b": ":", "f": ":", "g": ":", "c": ":"}},
            "b": {"last": ".", "next": {"f": "/", "g": "/", "c": "/"}},
            "f": {"last": ".", "next": {"g": ",", "c": ","}},
            "g": {"last": ".", "next": {"c": ","}},
            "c": {"last": "."},
        },
    },
    "260": {
        "subfield_order": ["a", "b", "c"],
        "punctuation_after": {
            "a": {"last": "", "next": {"b": ":", "c": ","}},
            "b": {"last": "", "next": {"c": ","}},
            "c": {"last": "."},
        },
    },
    "264": {
        # Same rules as 260 — ISBD punctuation is tag-agnostic
        "subfield_order": ["a", "b", "c"],
        "punctuation_after": {
            "a": {"last": "", "next": {"b": ":", "c": ","}},
            "b": {"last": "", "next": {"c": ","}},
            "c": {"last": "."},
        },
    },
    "300": {
        "subfield_order": ["a", "b", "c", "e"],
        "punctuation_after": {
            "a": {"last": "", "next": {"b": ":", "c": ";", "e": "+"}},
            "b": {"last": ";", "next": {"c": ";", "e": "+"}},
            "c": {"last": "+", "next": {"e": "+"}},
            "e": {"last": "."},
        },
    },
    "500": {
        "subfield_order": ["a"],
        "punctuation_after": {
            "a": {"last": "."},
        },
    },
    "504": {
        "subfield_order": ["a"],
        "punctuation_after": {
            "a": {"last": "."},
        },
    },
    "511": {
        "subfield_order": ["a"],
        "punctuation_after": {
            "a": {"last": "."},
        },
    },
    "534": {
        "subfield_order": ["a"],
        "punctuation_after": {
            "a": {"last": "."},
        },
    },
    "546": {
        "subfield_order": ["a"],
        "punctuation_after": {
            "a": {"last": "."},
        },
    },
    "490": {
        "subfield_order": ["a", "v", "x"],
        "punctuation_after": {
            "a": {"last": ".", "next": {"v": ".", "x": "."}},
            "v": {"last": ".", "next": {"x": "."}},
            "x": {"last": "."},
        },
    },
    "650": {
        "subfield_order": ["a", "v", "x", "y", "0", "2"],
        "punctuation_after": {
            "a": {"last": ".", "next": {"v": ".", "x": ".", "y": ".", "0": ".", "2": "."}},
            "v": {"last": ".", "next": {"x": ".", "y": ".", "0": ".", "2": "."}},
            "x": {"last": ".", "next": {"y": ".", "0": ".", "2": "."}},
            "y": {"last": ".", "next": {"0": ".", "2": "."}},
            "0": {"last": ".", "next": {"2": "."}},
            "2": {"last": "."},
        },
    },
    "651": {
        "subfield_order": ["a", "v", "x", "y", "0", "2"],
        "punctuation_after": {
            "a": {"last": ".", "next": {"v": ".", "x": ".", "y": ".", "0": ".", "2": "."}},
            "v": {"last": ".", "next": {"x": ".", "y": ".", "0": ".", "2": "."}},
            "x": {"last": ".", "next": {"y": ".", "0": ".", "2": "."}},
            "y": {"last": ".", "next": {"0": ".", "2": "."}},
            "0": {"last": ".", "next": {"2": "."}},
            "2": {"last": "."},
        },
    },
}


def get_isbd_punctuation(
    tag: str,
    subfield_code: str,
    next_subfield_code: str | None = None,
    enabled: bool = False,
) -> str:
    """Return the ISBD trailing punctuation for ``subfield_code`` in ``tag``.

    The punctuation depends on which subfield follows (or ``None`` if this
    is the last subfield). This matches the ISBD rule that trailing
    punctuation is determined by the subfield order.

    Args:
        tag: The 3-character MARC tag (e.g., ``"260"``).
        subfield_code: The subfield code (e.g., ``"a"``).
        next_subfield_code: The next subfield code in the field, or
            ``None`` if this is the last subfield.
        enabled: When ``False`` (default), returns empty string — a fast
            path that skips rule lookups.

    Returns:
        The ISBD trailing punctuation (e.g., ":" or ",") or empty string
        if ``enabled`` is ``False``, no rules exist for ``tag``, or no
        matching rule is found.
    """
    if not enabled:
        return ""

    rules = ISBD_RULES.get(tag)
    if not rules:
        return ""

    punct_rules = rules["punctuation_after"]
    code_rules = punct_rules.get(subfield_code, {})

    if next_subfield_code is None:
        # Last subfield — use "last" punctuation
        return str(code_rules.get("last", ""))

    # Not last — use "next" punctuation for the next subfield
    return str(code_rules.get("next", {}).get(next_subfield_code, ""))
