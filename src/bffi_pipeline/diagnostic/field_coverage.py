"""Generate a synthetic MARCXML corpus covering every handled field.

One record per MARC tag, isolated: leader + ``001`` + ``008`` + a ``245``
(so Boundary-1 minimum content passes) + *only the tag under test*. When a
field is lost in the round-trip, exactly one record shows it — no
cross-field interference and no invalid field combinations.

The tag surface is read from the two coverage sources rather than typed by
hand, so the corpus tracks the code instead of rotting beside it:

* :func:`parse_xslt_corpus` — the tags the vendored XSLT reads, with their
  subfield codes and indicator tests.
* ``MARC_EMIT_REGISTRY`` — the tags the reverse converter emits.

See ``docs/plans/p-061-field-coverage-corpus.md``.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Final

from lxml import etree

from bffi_pipeline.diagnostic.xslt_coverage import (
    cross_check,
    merge_templates_to_rows,
    parse_xslt_corpus,
)
from bffi_pipeline.diagnostic.xslt_coverage.regenerator import DEFAULT_XSLT_ENTRY_POINT
from bffi_pipeline.stages.bffi_to_marc.runner import MARC_EMIT_REGISTRY

_MARC_NS: Final[str] = "http://www.loc.gov/MARC21/slim"
_MARC: Final[str] = f"{{{_MARC_NS}}}"

#: Default output directory for the corpus.
DEFAULT_CORPUS_DIR: Final[Path] = (
    Path(__file__).resolve().parents[3] / "tests" / "data" / "sample-marcxml" / "field-coverage"
)

#: Subfields deliberately never emitted. ``$6`` is the 880 alternate-script
#: linkage and is meaningless without a paired 880 (the
#: ``non-latin-translations`` fixtures cover that); ``$8`` is field linking.
EXCLUDED_SUBFIELDS: Final[frozenset[str]] = frozenset({"6", "8"})

#: Control fields are emitted as ``<controlfield>``, never ``<datafield>``.
_CONTROLFIELDS: Final[frozenset[str]] = frozenset({"001", "003", "005", "006", "007", "008"})

#: Tags the generator owns on every record and therefore never emits as the
#: tag-under-test payload.
_STRUCTURAL: Final[frozenset[str]] = frozenset({"leader", "001", "008", "245"})

#: A 24-character MARC leader for a monograph (``06=a`` text, ``07=m``).
LEADER_BOOK: Final[str] = "00000nam a2200000 a 4500"

#: Leaders for tags that only make sense on another material type. Keyed by
#: tag; anything absent uses :data:`LEADER_BOOK`.
LEADER_BY_TAG: Final[dict[str, str]] = {
    # Music score / notated music (06=c).
    "254": "00000ncm a2200000 a 4500",
    "383": "00000ncm a2200000 a 4500",
    "384": "00000ncm a2200000 a 4500",
    "048": "00000ncm a2200000 a 4500",
    # Music sound recording (06=j).
    "028": "00000njm a2200000 a 4500",
    "306": "00000njm a2200000 a 4500",
    "344": "00000njm a2200000 a 4500",
    "511": "00000njm a2200000 a 4500",
    "518": "00000njm a2200000 a 4500",
    "382": "00000njm a2200000 a 4500",
    # Cartographic (06=e).
    "034": "00000nem a2200000 a 4500",
    "052": "00000nem a2200000 a 4500",
    "255": "00000nem a2200000 a 4500",
    "342": "00000nem a2200000 a 4500",
    "343": "00000nem a2200000 a 4500",
    # Moving image (06=g).
    "345": "00000ngm a2200000 a 4500",
    "346": "00000ngm a2200000 a 4500",
    "508": "00000ngm a2200000 a 4500",
    # Continuing resource (07=s).
    "022": "00000nas a2200000 a 4500",
    "310": "00000nas a2200000 a 4500",
    "321": "00000nas a2200000 a 4500",
    "362": "00000nas a2200000 a 4500",
    "780": "00000nas a2200000 a 4500",
    "785": "00000nas a2200000 a 4500",
}

#: A 40-character MARC 008 for a Finnish-language monograph.
CONTROL_008: Final[str] = "230101s2023    fi ||||| |||| 00| 0 fin d"

#: Values for subfields that must be syntactically valid rather than a
#: sentinel. Keyed ``(tag, code)`` first, then ``code`` as the fallback.
SUBFIELD_OVERRIDES: Final[dict[str, str]] = {
    "0": "http://www.yso.fi/onto/yso/p1234",
    "2": "yso/fin",
    "4": "aut",
    "5": "FI-Test",
    "d": "1970-2020",
    "w": "(FI-Test)000000001",
}
SUBFIELD_OVERRIDES_BY_TAG: Final[dict[tuple[str, str], str]] = {
    ("336", "2"): "rdacontent",
    ("336", "b"): "txt",
    ("337", "2"): "rdamedia",
    ("337", "b"): "n",
    ("338", "2"): "rdacarrier",
    ("338", "b"): "nc",
    ("655", "2"): "slm/fin",
    ("084", "2"): "ykl",
    ("041", "a"): "fin",
    ("041", "h"): "eng",
    ("020", "a"): "9789511234567",
    ("022", "a"): "1234-5678",
    ("008", ""): CONTROL_008,
}


@dataclass(frozen=True)
class FieldCase:
    """One tag's worth of synthetic record content.

    Emitted in two variants. ``minimal`` carries only the tag's primary
    subfield; ``maximal`` carries the full union of subfields both directions
    know about. The pair is the diagnostic: a tag that round-trips minimally
    but not maximally is being broken by a subfield combination, not by
    missing support — MARC 020 with a spurious ``$z``/``$q``/``$0`` loses its
    ISBN entirely, while ``020 $a`` alone round-trips perfectly.
    """

    variant: str
    tag: str
    is_controlfield: bool
    indicators: tuple[str, str]
    subfields: tuple[tuple[str, str], ...]
    #: ``cross_check``'s predicted round-trip verdict for this tag.
    verdict: str


#: Numeric prefix for the maximal variant's bib id, keeping filenames inside
#: the bare-digits pattern Boundary 1 accepts (``1020.xml`` = maximal 020).
MAXIMAL_ID_PREFIX: Final[str] = "1"


def record_id(case: FieldCase) -> str:
    """Bib id (and filename stem) for one case."""
    return case.tag if case.variant == "minimal" else f"{MAXIMAL_ID_PREFIX}{case.tag}"


def _sentinel(tag: str, code: str) -> str:
    """Greppable placeholder for a free-text subfield."""
    return f"Z{tag}{code}"


def _value_for(tag: str, code: str) -> str:
    override = SUBFIELD_OVERRIDES_BY_TAG.get((tag, code))
    if override is not None:
        return override
    if code in SUBFIELD_OVERRIDES:
        return SUBFIELD_OVERRIDES[code]
    return _sentinel(tag, code)


def _pick_indicators(
    forward_tests: frozenset[tuple[str, str]], reverse: tuple[str, ...] | None
) -> tuple[str, str]:
    """Choose a concrete indicator pair for the tag.

    Prefers a value the XSLT actually tests for (so the conversion takes a
    real branch rather than a fallthrough), then whatever the reverse
    converter emits, then blanks.

    Candidates longer than one character are dropped from both sources.
    ``MARC_EMIT_REGISTRY`` documents an indicator *range* where the emit
    picks by scheme (MARC 028 carries ``("0-6", "0-3")``), which reads
    correctly in the generated mapping table but is not a MARC indicator:
    MARC21slim's XSD constrains an indicator to one character, so copying
    the range verbatim produced two probes (``028.xml``, ``1028.xml``) that
    failed their own Boundary-1 XSD check. Found by wiring validation — see
    p-062.
    """
    ind1 = sorted(v for slot, v in forward_tests if slot == "ind1" and v != "#" and len(v) == 1)
    ind2 = sorted(v for slot, v in forward_tests if slot == "ind2" and v != "#" and len(v) == 1)
    # Blank out the unusable entries in place rather than filtering them:
    # dropping ``("0-3", "1")[0]`` would shift the ind2 value into the ind1
    # slot.
    fallback = tuple(v if len(v) == 1 else " " for v in (reverse or ()))
    first = ind1[0] if ind1 else (fallback[0] if len(fallback) > 0 else " ")
    second = ind2[0] if ind2 else (fallback[1] if len(fallback) > 1 else " ")
    return (first or " ", second or " ")


def build_cases(xslt_entry_point: Path | None = None) -> list[FieldCase]:
    """Build one :class:`FieldCase` per tag in the union of both surfaces."""
    report = parse_xslt_corpus(xslt_entry_point or DEFAULT_XSLT_ENTRY_POINT)
    forward = {row.tag: row for row in merge_templates_to_rows(report)}
    reverse: dict[str, tuple[tuple[str, ...], tuple[tuple[str, str], ...]]] = {}
    for entry in MARC_EMIT_REGISTRY:
        rev_codes = tuple((code, label) for code, label in entry.subfields)
        reverse.setdefault(entry.tag, (entry.indicators, rev_codes))

    verdicts = {row.tag: row.verdict for row in cross_check(report, MARC_EMIT_REGISTRY).rows}

    cases: list[FieldCase] = []
    for tag in sorted(set(forward) | set(reverse)):
        if tag in _STRUCTURAL:
            continue
        fwd = forward.get(tag)
        rev = reverse.get(tag)
        codes: set[str] = set(fwd.subfield_codes) if fwd is not None else set()
        if rev is not None:
            codes |= {code for code, _ in rev[1]}
        codes -= EXCLUDED_SUBFIELDS
        is_control = tag in _CONTROLFIELDS or (fwd is not None and fwd.field_kind == "controlfield")
        subfields: tuple[tuple[str, str], ...]
        if is_control:
            # Control fields have no subfields; the whole field is one value.
            subfields = (("", _value_for(tag, "")),)
        elif not codes:
            # Datafield the XSLT reads only via indicators / position reads.
            subfields = (("a", _sentinel(tag, "a")),)
        else:
            subfields = tuple((code, _value_for(tag, code)) for code in sorted(codes))
        indicators = _pick_indicators(
            fwd.indicator_tests if fwd is not None else frozenset(),
            rev[0] if rev is not None else None,
        )
        verdict = verdicts.get(tag, "not cross-checked")
        # Minimal: the subfield the reverse converter names first (its
        # primary), else $a, else the lowest-sorted code.
        primary: tuple[tuple[str, str], ...]
        if is_control:
            primary = subfields
        else:
            preferred = rev[1][0][0] if rev is not None and rev[1] else "a"
            codes_present = [c for c, _ in subfields]
            pick = preferred if preferred in codes_present else codes_present[0]
            primary = tuple((c, v) for c, v in subfields if c == pick)
        for variant, subs in (("minimal", primary), ("maximal", subfields)):
            if variant == "maximal" and subs == primary:
                # Nothing extra to test; one probe is enough.
                continue
            cases.append(
                FieldCase(
                    variant=variant,
                    tag=tag,
                    is_controlfield=is_control,
                    indicators=indicators,
                    subfields=subs,
                    verdict=verdict,
                )
            )
    return cases


def render_record(case: FieldCase) -> bytes:
    """Render one synthetic MARCXML record for ``case``."""
    root = etree.Element(f"{_MARC}record", nsmap={None: _MARC_NS})  # type: ignore[dict-item]
    leader = etree.SubElement(root, f"{_MARC}leader")
    leader.text = LEADER_BY_TAG.get(case.tag, LEADER_BOOK)

    def controlfield(tag: str, text: str) -> None:
        el = etree.SubElement(root, f"{_MARC}controlfield", tag=tag)
        el.text = text

    controlfield("001", record_id(case))
    if case.tag != "008":
        controlfield("008", CONTROL_008)

    if case.is_controlfield and case.tag not in {"001"}:
        controlfield(case.tag, case.subfields[0][1])
    elif not case.is_controlfield:
        df = etree.SubElement(
            root,
            f"{_MARC}datafield",
            tag=case.tag,
            ind1=case.indicators[0],
            ind2=case.indicators[1],
        )
        for code, value in case.subfields:
            sf = etree.SubElement(df, f"{_MARC}subfield", code=code)
            sf.text = value

    # Minimum content: every record needs a 245 $a to clear Boundary 1.
    title = etree.SubElement(root, f"{_MARC}datafield", tag="245", ind1="0", ind2="0")
    sf = etree.SubElement(title, f"{_MARC}subfield", code="a")
    sf.text = f"Field-coverage probe {case.tag} ({case.variant})"

    return etree.tostring(root, xml_declaration=True, encoding="UTF-8", pretty_print=True)


_README_HEADER: Final[str] = """# Field-coverage corpus — synthetic, generated

**These are not cataloguing examples.** Each record is a minimal synthetic
probe: leader + `001` + `008` + a `245` + exactly one tag under test. They
exist to answer "for every MARC field the pipeline claims to handle, what
actually survives a round-trip?" — not to model real bibliographic data.

Generated by `bffi-pipeline regenerate-field-coverage-corpus`; see
`docs/plans/p-061-field-coverage-corpus.md`. Do not hand-edit.

Filename and `001` are both the tag, so `roundtrip-eval`'s 001 pairing
lines up one probe with one reconstruction. Free-text subfields carry a
greppable sentinel (`Z655a` = tag 655, subfield `$a`); coded subfields
carry syntactically valid values.

The **predicted** column is `cross_check`'s verdict from the two coverage
sources. Running the corpus shows where reality departs from it — that gap
is the point of this fixture set.

Two probes per tag where they differ: `<tag>.xml` carries only the tag's
**primary** subfield; `1<tag>.xml` carries the **maximal** union of subfields
both directions know about. The pair is the diagnostic — a tag that survives
minimally but not maximally is being broken by a subfield *combination*, not
by missing support. MARC `020 $a` alone round-trips; add a spurious
`$z`/`$q`/`$0` and the ISBN vanishes.

| Bib id | Tag | Variant | Kind | Indicators | Subfields emitted | Predicted round-trip |
|---|---|---|---|---|---|---|
"""


def render_readme(cases: list[FieldCase]) -> str:
    """Render the manifest that makes the corpus self-documenting."""
    lines = [_README_HEADER]
    for case in cases:
        kind = "controlfield" if case.is_controlfield else "datafield"
        pair = f"{case.indicators[0]}{case.indicators[1]}".replace(" ", "#")
        inds = "n/a" if case.is_controlfield else f"`{pair}`"
        subs = (
            "whole field"
            if case.is_controlfield
            else " ".join(f"`${code}`" for code, _ in case.subfields)
        )
        lines.append(
            f"| `{record_id(case)}` | `{case.tag}` | {case.variant} | {kind} | "
            f"{inds} | {subs} | {case.verdict} |"
        )
    lines.append("")
    lines.append(f"{len(cases)} probe records.")
    lines.append("")
    return "\n".join(lines)


def regenerate_field_coverage_corpus(
    corpus_dir: Path | None = None,
    *,
    check: bool = False,
    xslt_entry_point: Path | None = None,
) -> tuple[int, bool]:
    """Write (or verify) the corpus. Returns ``(record_count, changed)``."""
    target = corpus_dir or DEFAULT_CORPUS_DIR
    cases = build_cases(xslt_entry_point)
    wanted: dict[str, bytes] = {f"{record_id(case)}.xml": render_record(case) for case in cases}
    wanted["README.md"] = render_readme(cases).encode("utf-8")

    existing: dict[str, bytes] = {}
    if target.is_dir():
        for path in target.iterdir():
            if path.is_file():
                existing[path.name] = path.read_bytes()

    changed = existing != wanted
    if check:
        return len(cases), changed

    target.mkdir(parents=True, exist_ok=True)
    for name in set(existing) - set(wanted):
        (target / name).unlink()
    for name, payload in sorted(wanted.items()):
        path = target / name
        if existing.get(name) != payload:
            path.write_bytes(payload)
    return len(cases), changed


__all__ = [
    "CONTROL_008",
    "DEFAULT_CORPUS_DIR",
    "EXCLUDED_SUBFIELDS",
    "LEADER_BOOK",
    "MAXIMAL_ID_PREFIX",
    "FieldCase",
    "build_cases",
    "record_id",
    "regenerate_field_coverage_corpus",
    "render_readme",
    "render_record",
]
