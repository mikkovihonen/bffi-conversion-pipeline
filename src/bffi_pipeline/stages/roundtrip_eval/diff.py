"""Per-record MARCXML diff classification.

Compares one source MARCXML record (the original source output) against
one reconstructed MARCXML record (the step-4 reverse converter's output)
and yields a per-field-instance verdict.

Statuses:

  - ``identical``  — same tag, same indicators, same subfields / value.
  - ``reordered``  — same tag, same indicators, same subfields in a
                     different order. A real fidelity difference (MARC
                     prescribes subfield order) but not a content one, so it
                     is neither ``identical`` nor ``changed``.
  - ``changed``    — same tag, content differs.
  - ``lost``       — present in source, no counterpart in reconstructed.
  - ``added``      — present in reconstructed, no counterpart in source.

Deferred to a follow-on:

  - ``tag-changed`` — same content under a different tag (e.g. MARC 260
    → 264; marc2bibframe2 collapses both source forms into the same
    ``bf:ProvisionActivity`` shape, so the reverse converter emits the
    RDA-modern 264 ind2=1 form for either source). Needs cross-tag
    content similarity; for v0 the operator reads paired ``lost`` +
    ``added`` rows as a hint instead.
  - ``marckey-bypass`` — doesn't apply
    until the reverse converter starts reading ``bflc:marcKey``.

Repeated tags (e.g. eleven 650s on one record) are paired **by content**,
never by position — see p-063. Positional pairing was the original
approach and it manufactured false ``changed`` rows: the reverse converter
emits repeated fields in sorted order, so zipping them against a source in
cataloguer order compares subject 1 with subject 7 and reports the
difference as a change of value. 20 of 30 repeated-tag groups in the
curated corpus were mispaired that way.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path
from typing import Final, Literal

from lxml import etree

DiffStatus = Literal["identical", "reordered", "changed", "lost", "added"]

#: MARCXML namespace per the LoC slim schema.
MARC21_NS: Final[str] = "http://www.loc.gov/MARC21/slim"
_MARC = f"{{{MARC21_NS}}}"


@dataclass(frozen=True)
class FieldRow:
    """One MARC field instance normalised for equality comparison."""

    tag: str
    #: ``None`` for controlfields (001-009); two-char string for datafields.
    ind1: str | None
    ind2: str | None
    #: Empty tuple for controlfields; for datafields the ordered list of
    #: ``(subfield_code, subfield_text)`` pairs.
    subfields: tuple[tuple[str, str], ...]
    #: For controlfields: the bare text value. ``None`` for datafields.
    text: str | None

    def display(self) -> str:
        """Pretty-print one field for the cataloguer-review HTML."""
        if self.text is not None:
            return f"{self.tag}  {self.text}"
        subs = "".join(f" ${code}{value}" for code, value in self.subfields)
        ind1 = self.ind1 if self.ind1 is not None else "_"
        ind2 = self.ind2 if self.ind2 is not None else "_"
        return f"{self.tag} {ind1}{ind2}{subs}"


@dataclass(frozen=True)
class FieldDiff:
    """One row of the per-record diff."""

    tag: str
    status: DiffStatus
    source: FieldRow | None
    reconstructed: FieldRow | None


@dataclass(frozen=True)
class RecordDiff:
    """Diff outcome for one (source, reconstructed) MARCXML pair."""

    bib_id: str
    fields: tuple[FieldDiff, ...]

    @property
    def status_counts(self) -> dict[DiffStatus, int]:
        counts: Counter[DiffStatus] = Counter()
        for field in self.fields:
            counts[field.status] += 1
        return dict(counts)


# --- parsing -------------------------------------------------------------


class MarcxmlParseError(RuntimeError):
    """The MARCXML file couldn't be parsed or didn't contain a record."""


def parse_record(marcxml_path: Path) -> tuple[str, tuple[FieldRow, ...]]:
    """Read one MARCXML file. Return ``(bib_id, fields)``.

    Accepts either ``<record>`` as the document root or
    ``<collection><record/></collection>``; the first ``<record>`` wins.
    Bib ID = ``controlfield tag="001"`` text. Raises
    :exc:`MarcxmlParseError` if either is missing.
    """
    try:
        tree = etree.parse(str(marcxml_path))
    except (etree.XMLSyntaxError, OSError) as exc:
        # lxml raises OSError("Invalid bytes"), not XMLSyntaxError, when the
        # file isn't decodable at all — e.g. a Latin-1 record. Both are the
        # same thing to the caller: this record can't be read.
        raise MarcxmlParseError(f"xml parse failed for {marcxml_path}: {exc}") from exc

    root = tree.getroot()
    record = root if root.tag == f"{_MARC}record" else root.find(f"{_MARC}record")
    if record is None:
        raise MarcxmlParseError(f"no <record> element in {marcxml_path}")

    bib_id_el = record.find(f"{_MARC}controlfield[@tag='001']")
    if bib_id_el is None or not bib_id_el.text:
        raise MarcxmlParseError(f"no controlfield 001 in {marcxml_path}")

    fields: list[FieldRow] = []
    for child in record:
        if child.tag == f"{_MARC}controlfield":
            tag = child.get("tag")
            if tag is None:
                continue
            fields.append(
                FieldRow(
                    tag=tag,
                    ind1=None,
                    ind2=None,
                    subfields=(),
                    text=child.text or "",
                )
            )
        elif child.tag == f"{_MARC}datafield":
            tag = child.get("tag")
            if tag is None:
                continue
            subfields: list[tuple[str, str]] = []
            for sf in child.findall(f"{_MARC}subfield"):
                code = sf.get("code") or ""
                subfields.append((code, sf.text or ""))
            fields.append(
                FieldRow(
                    tag=tag,
                    ind1=child.get("ind1") or "",
                    ind2=child.get("ind2") or "",
                    subfields=tuple(subfields),
                    text=None,
                )
            )

    return bib_id_el.text, tuple(fields)


# --- diff ----------------------------------------------------------------


def _unordered_key(
    row: FieldRow,
) -> tuple[str, str | None, str | None, tuple[str, ...], str | None]:
    """Identity of a field ignoring **subfield order** only.

    Indicators and the subfield multiset still have to match, so this pairs
    ``$a $2 $0`` with ``$a $0 $2`` and nothing looser.
    """
    return (
        row.tag,
        row.ind1,
        row.ind2,
        tuple(sorted(f"{c}{v}" for c, v in row.subfields)),
        row.text,
    )


#: How similar two fields' primary values must be to count as one changed
#: field rather than a lost one plus an added one. Calibrated against the
#: curated corpus: unrelated subjects score 0.17-0.63 (``hopeatyöt`` vs
#: ``hopeasepät`` is the closest false pair at 0.63), while the differences
#: the round-trip actually introduces score higher — 0.75 for a stripped
#: nonfiling article (``The Hobbit`` → ``Hobbit``), 0.97 for trailing
#: punctuation (``Puškin, Aleksandr,`` → ``Puškin, Aleksandr``).
_FUZZY_PAIR_THRESHOLD: Final[float] = 0.75

#: Pass 3a's floor: *any* shared subfield value is enough. Jaccard over a
#: field's `(code, value)` pairs can't return anything smaller than
#: 1/|union|, so this is simply "greater than zero" written as a bound
#: `_claim_best` can compare against.
_ANY_OVERLAP: Final[float] = 1e-9


def _primary_value(row: FieldRow) -> str | None:
    """The field's identifying value: its first ``$a``, or a controlfield's text.

    ``None`` when the field has neither, which makes it ineligible for fuzzy
    pairing — there is nothing to compare.
    """
    if row.text is not None:
        return row.text
    for code, value in row.subfields:
        if code == "a":
            return value
    return None


def _primary_similarity(left: FieldRow, right: FieldRow) -> float:
    """Character-level similarity of two fields' primary values, in ``[0, 1]``.

    The fallback for fields that share no subfield value exactly. Only the
    primary value is compared, deliberately: running this over ``$0``
    authority URIs would score two unrelated subjects at ~0.9 because their
    URIs differ in a few digits, which is how a fuzzy matcher ends up
    confidently pairing ``perhesalaisuudet`` with ``aikatasot``.
    """
    left_value = _primary_value(left)
    right_value = _primary_value(right)
    if left_value is None or right_value is None:
        return 0.0
    return SequenceMatcher(None, left_value, right_value).ratio()


def _similarity(left: FieldRow, right: FieldRow) -> float:
    """Jaccard overlap of two fields' ``(code, value)`` pairs, in ``[0, 1]``.

    The measure that makes a repeated 650 find *its own* counterpart rather
    than whichever one happens to sit at the same index. Order-blind by
    construction, so it can't re-introduce the distinction pass 2 already
    settled.

    Controlfields carry no subfields: they score 1.0 on equal text and 0.0
    otherwise. Equal text is already claimed by pass 1, so in practice a
    controlfield reaches pass 3 only via the single-instance rule.
    """
    if not left.subfields and not right.subfields:
        return 1.0 if left.text == right.text else 0.0
    left_pairs = set(left.subfields)
    right_pairs = set(right.subfields)
    union = left_pairs | right_pairs
    if not union:
        return 0.0
    return len(left_pairs & right_pairs) / len(union)


def _exact_match(left: FieldRow, right: FieldRow) -> float:
    """1.0 when the two fields are identical, 0.0 otherwise."""
    return 1.0 if left == right else 0.0


def _reordered_match(left: FieldRow, right: FieldRow) -> float:
    """1.0 when the two fields differ only in subfield order."""
    return 1.0 if _unordered_key(left) == _unordered_key(right) else 0.0


def _claim_best(
    row: FieldRow,
    candidates: list[FieldRow],
    score: Callable[[FieldRow, FieldRow], float],
    minimum: float,
) -> FieldRow | None:
    """Remove and return ``row``'s best candidate, if one clears ``minimum``.

    Ties break on the earliest index, so the same inputs always pair the same
    way — the review HTML is a conversion output and the determinism rule
    applies to it too.
    """
    if not candidates:
        return None
    best = max(range(len(candidates)), key=lambda i: (score(row, candidates[i]), -i))
    if score(row, candidates[best]) < minimum:
        return None
    return candidates.pop(best)


def _diff_one_tag(
    tag: str, source_rows: list[FieldRow], recon_rows: list[FieldRow]
) -> list[FieldDiff]:
    """Pair one tag's instances and classify each pairing.

    ``recon_rows`` is consumed as instances are claimed.
    """
    diffs: list[FieldDiff] = []
    # Whether the tag is repeated at all, judged before any pass consumes
    # rows: "one left over after pass 3a" is not the same thing as "one to
    # begin with", and only the latter earns the last-resort pairing below.
    not_repeated = len(source_rows) == 1 and len(recon_rows) == 1

    # Pass 1: exact matches, claimed regardless of instance order.
    after_exact: list[FieldRow] = []
    for s in source_rows:
        claimed = _claim_best(s, recon_rows, _exact_match, 1.0)
        if claimed is None:
            after_exact.append(s)
        else:
            diffs.append(FieldDiff(tag=tag, status="identical", source=s, reconstructed=claimed))

    # Pass 2: same content, subfields in a different order.
    after_reorder: list[FieldRow] = []
    for s in after_exact:
        claimed = _claim_best(s, recon_rows, _reordered_match, 1.0)
        if claimed is None:
            after_reorder.append(s)
        else:
            diffs.append(FieldDiff(tag=tag, status="reordered", source=s, reconstructed=claimed))

    # Pass 3a: pair rows that share at least one subfield value exactly,
    # strongest overlap first. This is what makes a repeated 650 find its own
    # counterpart — matching subjects share their `$0` authority URI verbatim
    # even when `$2` and subfield order differ.
    after_overlap: list[FieldRow] = []
    for s in after_reorder:
        claimed = _claim_best(s, recon_rows, _similarity, _ANY_OVERLAP)
        if claimed is None:
            after_overlap.append(s)
        else:
            diffs.append(FieldDiff(tag=tag, status="changed", source=s, reconstructed=claimed))

    # Pass 3b: rows sharing no value exactly may still be the same field with a
    # normalised value — a stripped nonfiling article, dropped trailing
    # punctuation. Pair those on primary-value similarity alone.
    #
    # Anything still unpaired stays unpaired: two 650s with nothing in common
    # are one lost subject and one added one, not a subject that changed. The
    # single exception is a tag that was never repeated, where `changed` beats
    # making the reader re-associate a lost row with an added one by eye.
    threshold = 0.0 if not_repeated else _FUZZY_PAIR_THRESHOLD
    for s in after_overlap:
        claimed = _claim_best(s, recon_rows, _primary_similarity, threshold)
        if claimed is None:
            diffs.append(FieldDiff(tag=tag, status="lost", source=s, reconstructed=None))
        else:
            diffs.append(FieldDiff(tag=tag, status="changed", source=s, reconstructed=claimed))

    # Pass 4: reconstructed overhang.
    diffs.extend(
        FieldDiff(tag=tag, status="added", source=None, reconstructed=r) for r in recon_rows
    )
    return diffs


def diff_fields(
    *, source: tuple[FieldRow, ...], reconstructed: tuple[FieldRow, ...]
) -> tuple[FieldDiff, ...]:
    """Per-field-instance diff between source and reconstructed records.

    Passes per tag (p-063), in :func:`_diff_one_tag`:

      1. exact equality, in any order → ``identical``
      2. equal but for subfield order → ``reordered``
      3a. greatest exact subfield-value overlap → ``changed``
      3b. primary values similar enough → ``changed``
      4. whatever is left → ``lost`` / ``added``
    """
    by_tag_source: dict[str, list[FieldRow]] = {}
    by_tag_recon: dict[str, list[FieldRow]] = {}
    for row in source:
        by_tag_source.setdefault(row.tag, []).append(row)
    for row in reconstructed:
        by_tag_recon.setdefault(row.tag, []).append(row)

    diffs: list[FieldDiff] = []
    for tag in sorted(set(by_tag_source) | set(by_tag_recon)):
        diffs.extend(
            _diff_one_tag(
                tag,
                list(by_tag_source.get(tag, ())),
                list(by_tag_recon.get(tag, ())),
            )
        )
    return tuple(diffs)


def diff_records(*, source_path: Path, reconstructed_path: Path) -> RecordDiff:
    """Top-level entry: parse both files, diff them, return a :class:`RecordDiff`."""
    bib_id, source_fields = parse_record(source_path)
    _, recon_fields = parse_record(reconstructed_path)
    return RecordDiff(
        bib_id=bib_id,
        fields=diff_fields(source=source_fields, reconstructed=recon_fields),
    )
