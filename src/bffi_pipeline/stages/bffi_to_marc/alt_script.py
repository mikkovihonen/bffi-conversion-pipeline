"""Alt-script 880 field reconstruction for the BFFI → MARC direction.

Detects language-tagged duplicate values in the BFFI graph and emits
MARC 880 fields (alt-script/structure linkage) to preserve non-Latin
script content that marc2bibframe2 folded into main fields.

Stage: BFFI → MARC reverse conversion.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from rdflib import BNode, Graph, Literal, Node, URIRef

# --- MARC script indicators (per MARC Code Lists for Script Codes) ----------

#: Script indicator → Unicode range mapping. Used to detect the script
#: of alt-script values and map them to MARC `$6` qualifiers.
#:
#: The indicator format is ``/(X`` where X is a single letter per the
#: MARC standard. The full indicator (with slash) is used in the `$6`
#: subfield.
_SCRIPT_INDICATORS: Final[dict[str, str]] = {
    "Cyrillic": "N",  # /(N — non-Roman
    "Greek": "G",  # /(G
    "Hebrew": "H",  # /(H
    "Arabic": "A",  # /(A
    "CJK": "C",  # /(C — Chinese, Japanese, Korean (general)
    "Hiragana": "J",  # /(J — Japanese Hiragana
    "Katakana": "J",  # /(J — Japanese Katakana (same indicator as Hiragana)
    "Hangul": "K",  # /(K — Korean
    "Devanagari": "D",  # /(D
    "Bengali": "B",  # /(B
    "Gurmukhi": "G",  # /(G (note: conflicts with Greek; use context)
    "Gujarati": "G",  # /(G (note: conflicts with Greek)
    "Odia": "O",  # /(O (other)
    "Tamil": "T",  # /(T
    "Telugu": "T",  # /(T
    "Kannada": "K",  # /(K (note: conflicts with Hangul; use context)
    "Malayalam": "M",  # /(M
    "Sinhala": "S",  # /(S
    "Tibetan": "T",  # /(T
    "Thai": "T",  # /(T
    "Lao": "L",  # /(L
    "Myanmar": "M",  # /(M
    "Khmer": "K",  # /(K (note: conflicts with Hangul/Kannada)
    "Mongolian": "M",  # /(M
    "Yi": "Y",  # /(Y
    "Limbu": "L",  # /(L
    "Saurashtra": "S",  # /(S
    "Kayah": "K",  # /(K
    "Rejang": "R",  # /(R
    "Sundanese": "S",  # /(S
    "Sundanese_suppl": "S",  # /(S
    "Batak": "B",  # /(B
    "Buginese": "B",  # /(B
    "Meetei_Mayek": "M",  # /(M
    "Other_non_Roman": "O",  # /(O — fallback
}

#: Unicode range → script name mapping for detection.
_UNICODE_SCRIPT_RANGES: Final[list[tuple[str, str, str]]] = [
    ("Cyrillic", "\u0400", "\u04ff"),
    ("Cyrillic_suppl", "\u0500", "\u052f"),
    ("Greek", "\u0370", "\u03ff"),
    ("Greek_ext", "\u1f00", "\u1fff"),
    ("Hebrew", "\u0590", "\u05ff"),
    ("Arabic", "\u0600", "\u06ff"),
    ("Arabic_ext", "\u0750", "\u077f"),
    ("CJK", "\u4e00", "\u9fff"),
    ("CJK_unified_I", "\u3400", "\u4dbf"),
    ("Hiragana", "\u3040", "\u309f"),
    ("Katakana", "\u30a0", "\u30ff"),
    ("Hangul", "\uac00", "\ud7af"),
    ("Hangul_syllables", "\u1100", "\u11ff"),
    ("Devanagari", "\u0900", "\u097f"),
    ("Bengali", "\u0980", "\u09ff"),
    ("Gurmukhi", "\u0a00", "\u0a7f"),
    ("Gujarati", "\u0a80", "\u0aff"),
    ("Odia", "\u0b00", "\u0b7f"),
    ("Tamil", "\u0b80", "\u0bff"),
    ("Telugu", "\u0c00", "\u0c7f"),
    ("Kannada", "\u0c80", "\u0cff"),
    ("Malayalam", "\u0d00", "\u0d7f"),
    ("Sinhala", "\u0d80", "\u0dff"),
    ("Thai", "\u0e00", "\u0e7f"),
    ("Lao", "\u0e80", "\u0eff"),
    ("Tibetan", "\u0f00", "\u0fff"),
    ("Myanmar", "\u1000", "\u109f"),
    ("Khmer", "\u1780", "\u17ff"),
    ("Mongolian", "\u1800", "\u18af"),
    ("Yi", "\ua000", "\ua48f"),
    ("Limbu", "\u1900", "\u194f"),
    ("Saurashtra", "\ua880", "\ua8df"),
    ("Kayah_rejang", "\ua900", "\ua92f"),
    ("Rejang", "\ua930", "\ua95f"),
    ("Sundanese", "\u1b80", "\u1bbf"),
    ("Batak", "\u1bc0", "\u1bff"),
    ("Buginese", "\u1a00", "\u1a1f"),
    ("Meetei_Mayek", "\uabc0", "\uabff"),
]


@dataclass(frozen=True)
class AltScriptInfo:
    """Information about a detected alt-script value.

    Attributes:
        lang: The `xml:lang` tag of the value (e.g., `"ru"`).
        value: The literal value.
        script_indicator: The MARC `$6` script indicator (e.g., `"/(N"`).
        extra_subfields: Additional ``(code, value)`` subfields to emit
            in the 880 field (e.g., ``("e", "kirjoittaja")`` for
            contributor relator terms).
    """

    lang: str
    value: str
    script_indicator: str
    extra_subfields: tuple[tuple[str, str], ...] = ()


def detect_alt_scripts(
    graph: Graph,
    entity: URIRef | BNode | Node,
    predicate: URIRef,
) -> list[AltScriptInfo]:
    """Detect language-tagged duplicate values on a BFFI predicate.

    Scans the BFFI graph for the given entity and predicate, looking for
    multiple literal values with different `xml:lang` tags. The value
    without an `xml:lang` tag (or with the record's primary language) is
    considered primary. All other language-tagged values are alt-script.

    Skips literals whose text is identical to the primary value — a date
    like ``[2025]`` tagged `@ru` is not a true alt-script, just a
    language-tagged copy of the romanized value.

    Args:
        graph: The BFFI graph.
        entity: The BFFI entity (URI or BNode).
        predicate: The BFFI predicate (e.g., ``BFFI.title``).

    Returns:
        List of :class:`AltScriptInfo` for alt-script values. Empty list
        if no alt-script values are detected.
    """
    alt_scripts: list[AltScriptInfo] = []

    # Collect the primary value (no xml:lang) first
    primary_text: str | None = None
    for value in graph.objects(entity, predicate):
        if isinstance(value, Literal) and not value.language:
            primary_text = str(value)
            break

    for value in graph.objects(entity, predicate):
        if not isinstance(value, Literal):
            continue

        # Skip the primary value (no xml:lang)
        if not value.language:
            continue

        text = str(value)

        # Skip if text is identical to the primary value — not a true
        # alt-script, just a language-tagged copy (e.g. date "[2025]"
        # tagged @ru when the romanized version is also "[2025]").
        if primary_text is not None and text == primary_text:
            continue

        # Detect the script and map to MARC indicator
        script_indicator = detect_script(text)

        alt_scripts.append(
            AltScriptInfo(
                lang=value.language,
                value=text,
                script_indicator=script_indicator,
            )
        )

    return alt_scripts


def detect_script(text: str) -> str:
    """Detect the Unicode script of text and return MARC indicator.

    Uses Unicode range detection with fallback to hardcoded mapping.
    Returns the MARC `$6` script indicator (e.g., `"/(N"` for Cyrillic).

    Args:
        text: The text to detect the script for.

    Returns:
        The MARC script indicator string (e.g., `"/(N"`, `"/(G"`).
        Falls back to `"/(O"` (other) if detection fails.
    """
    if not text:
        return "/(O"

    # Detect the dominant script by counting characters in each range
    script_counts: dict[str, int] = {}
    for char in text:
        for script_name, start, end in _UNICODE_SCRIPT_RANGES:
            if start <= char <= end:
                script_counts[script_name] = script_counts.get(script_name, 0) + 1
                break

    if not script_counts:
        return "/(O"

    # Find the dominant script
    dominant_script = max(script_counts, key=lambda k: script_counts[k])

    # Map script name to MARC indicator
    # Handle special cases (Hiragana/Katakana → J, etc.)
    indicator_map: dict[str, str] = {
        "Cyrillic": "N",
        "Cyrillic_suppl": "N",
        "Greek": "G",
        "Greek_ext": "G",
        "Hebrew": "H",
        "Arabic": "A",
        "Arabic_ext": "A",
        "CJK": "C",
        "CJK_unified_I": "C",
        "Hiragana": "J",
        "Katakana": "J",
        "Hangul": "K",
        "Hangul_syllables": "K",
        "Devanagari": "D",
        "Bengali": "B",
        "Gurmukhi": "G",
        "Gujarati": "G",
        "Odia": "O",
        "Tamil": "T",
        "Telugu": "T",
        "Kannada": "K",
        "Malayalam": "M",
        "Sinhala": "S",
        "Thai": "T",
        "Lao": "L",
        "Tibetan": "T",
        "Myanmar": "M",
        "Khmer": "K",
        "Mongolian": "M",
        "Yi": "Y",
        "Limbu": "L",
        "Saurashtra": "S",
        "Kayah_rejang": "K",
        "Rejang": "R",
        "Sundanese": "S",
        "Batak": "B",
        "Buginese": "B",
        "Meetei_Mayek": "M",
    }

    return f"/({indicator_map.get(dominant_script, 'O')}"


def is_folding_tag(tag: str) -> bool:
    """Check if marc2bibframe2 folds 880 data into this tag.

    Returns True for tags that marc2bibframe2 processes via the 880
    dispatch (both `convertLinked="false"` and `convertLinked="true"`).
    These tags need 880 reconstruction if their BFFI predicates have
    language-tagged duplicates.

    Args:
        tag: The MARC tag (e.g., `"100"`, `"245"`).

    Returns:
        True if the tag is handled by marc2bibframe2 via 880.
    """
    # Tags with convertLinked="false" (folded) or omitted from map880.xml
    FOLDING_TAGS = {
        # Names
        "100",
        "110",
        "111",
        "700",
        "710",
        "711",
        "800",
        "810",
        "811",
        "830",
        # Titles
        "210",
        "222",
        "242",
        "243",
        "245",
        "246",
        "247",
        # Publication
        "260",
        "264",
        # Classification
        "010",
        "015",
        "016",
        "017",
        "020",
        "022",
        "023",
        "024",
        "025",
        "026",
        "027",
        "028",
        "030",
        "032",
        "033",
        "034",
        "035",
        "036",
        "037",
        "040",
        "041",
        "042",
        "043",
        "045",
        "046",
        "047",
        "048",
        "050",
        "052",
        "055",
        "060",
        "070",
        "072",
        "074",
        "080",
        "082",
        "084",
        "086",
        "088",
        # Notes
        "500",
        "501",
        "504",
        "505",
        "506",
        "507",
        "513",
        "515",
        "516",
        "518",
        "520",
        "521",
        "522",
        "524",
        "525",
        "530",
        "532",
        "533",
        "534",
        "536",
        "538",
        "540",
        "541",
        "544",
        "545",
        "546",
        "547",
        "550",
        "555",
        "556",
        "561",
        "563",
        "580",
        "581",
        "583",
        "585",
        "586",
        "587",
        "588",
        # Subjects
        "600",
        "610",
        "611",
        "630",
        "648",
        "650",
        "651",
        "653",
        "655",
        "656",
        "662",
        # Other
        "038",
        "254",
        "255",
        "256",
        "257",
        "263",
        "265",
        "300",
        "306",
        "310",
        "321",
        "334",
        "336",
        "337",
        "338",
        "340",
        "341",
        "344",
        "345",
        "346",
        "347",
        "348",
        "351",
        "352",
        "353",
        "362",
        "370",
        "377",
        "380",
        "382",
        "383",
        "384",
        "385",
        "386",
        "720",
        "730",
        "740",
        "752",
        "753",
        "758",
        "760",
        "762",
        "765",
        "767",
        "770",
        "772",
        "773",
        "774",
        "775",
        "776",
        "777",
        "786",
        "787",
        "856",
        "859",
    }

    # Tags with convertLinked="true" (separately emitted)
    SEPARATELY_EMITTED_TAGS = {
        "210",
        "222",
        "242",
        "243",
        "506",
        "507",
        "510",
        "518",
        "521",
        "522",
        "524",
        "525",
        "532",
        "538",
        "540",
        "541",
        "561",
        "563",
        "583",
        "586",
    }

    return tag in FOLDING_TAGS or tag in SEPARATELY_EMITTED_TAGS
