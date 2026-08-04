"""Unit tests for the field-coverage corpus generator (p-061).

The corpus itself is deliberately not asserted on — pinning it would freeze
the very drift it exists to surface (see the plan's "Out of scope"). These
tests pin the generator's contract instead.
"""

from __future__ import annotations

from pathlib import Path

from lxml import etree

from bffi_pipeline.diagnostic.field_coverage import (
    CONTROL_008,
    EXCLUDED_SUBFIELDS,
    build_cases,
    record_id,
    regenerate_field_coverage_corpus,
    render_readme,
    render_record,
)

MARC_NS = "http://www.loc.gov/MARC21/slim"
M = f"{{{MARC_NS}}}"


def _cases_by_tag(variant: str = "minimal") -> dict[str, object]:
    """Cases of one variant, keyed by tag.

    Both variants share a tag, so the variant has to be selected — keying on
    tag alone silently kept whichever came last.
    """
    return {case.tag: case for case in build_cases() if case.variant == variant}


def test_every_probe_carries_the_boundary_one_minimum() -> None:
    """Each record needs leader + 001 + 245 $a or it fails Boundary 1 before
    the XSLT ever runs."""
    for case in build_cases()[:25]:
        root = etree.fromstring(render_record(case))
        assert root.find(f"{M}leader") is not None
        assert root.find(f"{M}controlfield[@tag='001']") is not None
        title = root.find(f"{M}datafield[@tag='245']/{M}subfield[@code='a']")
        assert title is not None and title.text


def test_bib_id_encodes_tag_and_variant_for_eval_pairing() -> None:
    """Minimal probes take the bare tag; maximal ones take a ``1`` prefix so
    both stay inside the bare-digits filename pattern Boundary 1 accepts."""
    minimal = _cases_by_tag("minimal")["655"]
    maximal = _cases_by_tag("maximal")["655"]
    assert record_id(minimal) == "655"  # type: ignore[arg-type]
    assert record_id(maximal) == "1655"  # type: ignore[arg-type]
    root = etree.fromstring(render_record(minimal))  # type: ignore[arg-type]
    assert root.find(f"{M}controlfield[@tag='001']").text == "655"  # type: ignore[union-attr]


def test_minimal_probe_carries_exactly_one_subfield() -> None:
    """The point of the minimal variant is isolation: one subfield, so a loss
    can't be blamed on a subfield combination."""
    for case in _cases_by_tag("minimal").values():
        if not case.is_controlfield:  # type: ignore[attr-defined]
            assert len(case.subfields) == 1  # type: ignore[attr-defined]


def test_maximal_probe_is_a_superset_of_the_minimal_one() -> None:
    minimal = _cases_by_tag("minimal")
    for tag, maximal in _cases_by_tag("maximal").items():
        mn = {c for c, _ in minimal[tag].subfields}  # type: ignore[attr-defined]
        mx = {c for c, _ in maximal.subfields}  # type: ignore[attr-defined]
        assert mn < mx, f"{tag}: maximal must add subfields, got {mx} vs {mn}"


def test_excluded_subfields_are_never_emitted() -> None:
    """``$6``/``$8`` are linkage subfields; emitting one bare would produce a
    dangling reference to an 880 that isn't there."""
    for case in build_cases():
        for code, _ in case.subfields:
            assert code not in EXCLUDED_SUBFIELDS


def test_free_text_subfields_carry_a_greppable_sentinel() -> None:
    case = _cases_by_tag()["500"]
    values = dict(case.subfields)  # type: ignore[attr-defined]
    assert values["a"] == "Z500a"


def test_coded_subfields_carry_valid_values_not_sentinels() -> None:
    """A sentinel in ``$2`` would make the vocabulary unresolvable and change
    what the conversion does."""
    # $2 only appears on the maximal probe; the minimal one carries the
    # primary subfield alone.
    maximal = _cases_by_tag("maximal")
    assert dict(maximal["336"].subfields)["2"] == "rdacontent"  # type: ignore[attr-defined]
    assert dict(maximal["655"].subfields)["2"] == "slm/fin"  # type: ignore[attr-defined]
    assert dict(maximal["041"].subfields)["a"] == "fin"  # type: ignore[attr-defined]


def test_control_fields_render_as_controlfield_not_datafield() -> None:
    case = _cases_by_tag()["007"]
    root = etree.fromstring(render_record(case))  # type: ignore[arg-type]
    assert root.find(f"{M}controlfield[@tag='007']") is not None
    assert root.find(f"{M}datafield[@tag='007']") is None


def test_008_is_forty_characters() -> None:
    """A short 008 makes the XSLT read positions off the end."""
    assert len(CONTROL_008) == 40


def test_material_specific_tags_get_a_matching_leader() -> None:
    """A music-only field on a book leader sends the XSLT down the wrong
    Work-class branch."""
    by_tag = _cases_by_tag()
    audio = etree.fromstring(render_record(by_tag["028"]))  # type: ignore[arg-type]
    assert audio.find(f"{M}leader").text[6] == "j"  # type: ignore[union-attr,index]
    book = etree.fromstring(render_record(by_tag["500"]))  # type: ignore[arg-type]
    assert book.find(f"{M}leader").text[6] == "a"  # type: ignore[union-attr,index]


def test_readme_lists_every_probe_with_its_predicted_verdict() -> None:
    cases = build_cases()
    readme = render_readme(cases)
    assert "not cataloguing examples" in readme
    for case in cases[:20]:
        assert f"| `{case.tag}` |" in readme
    assert f"{len(cases)} probe records." in readme


def test_regenerate_is_idempotent_and_check_reports_no_drift(tmp_path: Path) -> None:
    count, changed = regenerate_field_coverage_corpus(tmp_path)
    assert count > 100
    assert changed
    _, changed_again = regenerate_field_coverage_corpus(tmp_path)
    assert not changed_again
    _, drift = regenerate_field_coverage_corpus(tmp_path, check=True)
    assert not drift


def test_check_mode_does_not_write(tmp_path: Path) -> None:
    target = tmp_path / "corpus"
    regenerate_field_coverage_corpus(target, check=True)
    assert not target.exists()


def test_stale_files_are_removed_on_regenerate(tmp_path: Path) -> None:
    """A submodule bump that drops a tag must not leave its probe behind."""
    regenerate_field_coverage_corpus(tmp_path)
    stale = tmp_path / "999.xml"
    stale.write_text("<record/>", encoding="utf-8")
    regenerate_field_coverage_corpus(tmp_path)
    assert not stale.exists()
