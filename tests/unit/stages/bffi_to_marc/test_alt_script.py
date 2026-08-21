"""Unit tests for alt-script 880 field reconstruction."""

from __future__ import annotations

from rdflib import BNode, Graph, Literal, URIRef
from rdflib.namespace import RDFS

from bffi_pipeline.provenance.vocab import BFFI, bind_canonical_prefixes
from bffi_pipeline.stages.bffi_to_marc.alt_script import (
    detect_alt_scripts,
    detect_script,
    is_folding_tag,
)

# --- detect_script --------------------------------------------------------


def test_detect_script_cyrillic() -> None:
    assert detect_script("Хиллер") == "/(N"


def test_detect_script_greek() -> None:
    assert detect_script("Αθήνα") == "/(G"


def test_detect_script_hebrew() -> None:
    assert detect_script("ירושלים") == "/(H"


def test_detect_script_arabic() -> None:
    assert detect_script("القاهرة") == "/(A"


def test_detect_script_cjk_chinese() -> None:
    assert detect_script("北京") == "/(C"


def test_detect_script_cjk_japanese() -> None:
    assert detect_script("東京") == "/(C"  # General CJK


def test_detect_script_cjk_korean() -> None:
    assert detect_script("서울") == "/(K"


def test_detect_script_latin() -> None:
    assert detect_script("Hiller") == "/(O"  # Latin → other


def test_detect_script_empty() -> None:
    assert detect_script("") == "/(O"


def test_detect_script_mixed_latin_cyrillic() -> None:
    # Mixed text: cyrillic should dominate if more characters
    result = detect_script("Hiller Хиллер")
    assert result in ("/(N", "/(O")  # Depends on character count


def test_detect_script_devanagari() -> None:
    assert detect_script("दिल्ली") == "/(D"


def test_detect_script_thai() -> None:
    assert detect_script("กรุงเทพมหานคร") == "/(T"


# --- detect_alt_scripts ---------------------------------------------------


def _build_graph_with_labels(*, primary: str, alt_lang: str, alt_value: str) -> Graph:
    """Build a BFFI graph with primary and alt-script labels."""
    g = Graph()
    bind_canonical_prefixes(g)
    entity = URIRef("http://example.org/agent1")
    g.add((entity, BFFI.agent, BNode("agent1")))
    agent = g.value(entity, BFFI.agent)
    g.add((agent, RDFS.label, Literal(primary)))
    g.add((agent, RDFS.label, Literal(alt_value, lang=alt_lang)))
    return g, agent


def test_detect_alt_scripts_single_alt() -> None:
    g, agent = _build_graph_with_labels(
        primary="Hiller",
        alt_lang="ru",
        alt_value="Хиллер",
    )
    result = detect_alt_scripts(g, agent, RDFS.label)
    assert len(result) == 1
    assert result[0].lang == "ru"
    assert result[0].value == "Хиллер"
    assert result[0].script_indicator == "/(N"


def test_detect_alt_scripts_multiple_alts() -> None:
    g = Graph()
    bind_canonical_prefixes(g)
    entity = URIRef("http://example.org/agent2")
    agent = BNode("agent2")
    g.add((entity, BFFI.agent, agent))
    g.add((agent, RDFS.label, Literal("Hiller")))
    g.add((agent, RDFS.label, Literal("Хиллер", lang="ru")))
    g.add((agent, RDFS.label, Literal("Αθήνα", lang="el")))
    result = detect_alt_scripts(g, agent, RDFS.label)
    assert len(result) == 2
    assert result[0].lang == "ru"
    assert result[0].script_indicator == "/(N"
    assert result[1].lang == "el"
    assert result[1].script_indicator == "/(G"


def test_detect_alt_scripts_no_alt() -> None:
    g = Graph()
    bind_canonical_prefixes(g)
    entity = URIRef("http://example.org/agent3")
    agent = BNode("agent3")
    g.add((entity, BFFI.agent, agent))
    g.add((agent, RDFS.label, Literal("Hiller")))
    result = detect_alt_scripts(g, agent, RDFS.label)
    assert len(result) == 0


def test_detect_alt_scripts_only_alt() -> None:
    """If only alt-script values exist (no primary), all are treated as alt."""
    g = Graph()
    bind_canonical_prefixes(g)
    entity = URIRef("http://example.org/agent4")
    agent = BNode("agent4")
    g.add((entity, BFFI.agent, agent))
    g.add((agent, RDFS.label, Literal("Хиллер", lang="ru")))
    result = detect_alt_scripts(g, agent, RDFS.label)
    assert len(result) == 1
    assert result[0].lang == "ru"
    assert result[0].value == "Хиллер"


# --- is_folding_tag -------------------------------------------------------


def test_is_folding_tag_100() -> None:
    assert is_folding_tag("100") is True


def test_is_folding_tag_245() -> None:
    assert is_folding_tag("245") is True


def test_is_folding_tag_264() -> None:
    assert is_folding_tag("264") is True


def test_is_folding_tag_700() -> None:
    assert is_folding_tag("700") is True


def test_is_folding_tag_500() -> None:
    assert is_folding_tag("500") is True


def test_is_folding_tag_001() -> None:
    assert is_folding_tag("001") is False


def test_is_folding_tag_008() -> None:
    assert is_folding_tag("008") is False
