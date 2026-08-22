"""Static analysis of the LoC marc2bibframe2 XSLT.

Parses every ``xsl:template`` in the included module set whose ``@match``
mentions a MARC source node (``marc:datafield``, ``marc:controlfield``,
or ``marc:leader``) and emits a :class:`~.model.TemplateFact` per match.

Determinism: regex over attribute literals, no XPath evaluation, no
runtime side-effects. The output depends only on the on-disk XSLT.
"""

from __future__ import annotations

import re
import subprocess
from collections import deque
from pathlib import Path
from typing import Final

from lxml import etree

from bffi_pipeline.diagnostic.xslt_coverage.model import (
    IndicatorSlot,
    OutputKind,
    OutputTerm,
    ParseReport,
    TemplateFact,
)

XSL_NS: Final[str] = "http://www.w3.org/1999/XSL/Transform"
MARC_NS: Final[str] = "http://www.loc.gov/MARC21/slim"

#: Element prefixes whose presence in a template body counts as a
#: BIBFRAME-side emission. ``rdf`` is intentionally excluded — the
#: ``rdf:type`` / ``rdf:resource`` markers don't name a BIBFRAME class
#: by themselves, the prefixed sibling does.
_OUTPUT_PREFIXES: Final[frozenset[str]] = frozenset({"bf", "bflc", "madsrdf", "bibframe"})

# --- match-attribute parsing -----------------------------------------------

_DATAFIELD_TAG_RE = re.compile(r"marc:datafield\[@tag='([^']+)'\]")
_DATAFIELD_880_RE = re.compile(
    r"@tag='([^']+)'\s+or\s+\(@tag='880'\s+and\s+"
    r"substring\(marc:subfield\[@code='6'\],1,3\)='([^']+)'\)"
)
_CONTROLFIELD_TAG_RE = re.compile(r"marc:controlfield\[@tag='([^']+)'\]")
_LEADER_RE = re.compile(r"\bmarc:leader\b")


def _extract_all_marc_references(
    text: str,
) -> tuple[set[str], set[tuple[IndicatorSlot, str]], set[str]]:
    """Extract all MARC tags, indicator tests, and subfield codes from a string."""
    tags: set[str] = set()
    indicators: set[tuple[IndicatorSlot, str]] = set()
    subfields: set[str] = set()

    # Tags
    for tag in _DATAFIELD_TAG_RE.findall(text):
        tags.add(tag)
    for tag in _CONTROLFIELD_TAG_RE.findall(text):
        tags.add(tag)
    if _LEADER_RE.search(text):
        tags.add("leader")

    # 880 dispatch
    for tag, _ in _DATAFIELD_880_RE.findall(text):
        tags.add(tag)
        tags.add("880")

    # Indicators
    for slot, value in _INDICATOR_TEST_RE.findall(text):
        indicators.add((slot, value))

    # Subfields
    for code in _SUBFIELD_CODE_RE.findall(text):
        subfields.add(code)
    for predicate in _SUBFIELD_OR_CHAIN_RE.findall(text):
        subfields.update(_SUBFIELD_CODE_INNER_RE.findall(predicate))

    return tags, indicators, subfields


# Indicator literal equality test inside any @test / @select. We allow
# `@ind1 = '0'` (any whitespace around the equals), and capture the slot
# (ind1/ind2) plus the single-character value. The value may be a literal
# space (the MARC blank indicator).
_INDICATOR_TEST_RE = re.compile(r"@(ind[12])\s*=\s*'(.)'")
_INDICATOR_REF_RE = re.compile(r"@(ind[12])\b")

#: Single literal code: ``marc:subfield[@code='a']``.
_SUBFIELD_CODE_RE = re.compile(r"marc:subfield\[@code='([^']+)'\]")
#: OR-chained codes: ``marc:subfield[@code='a' or @code='b' or @code='c']``.
#: marc2bibframe2 uses these heavily on title-field families to collect a
#: composite of several subfields in document order. We harvest every
#: ``@code='X'`` literal inside the predicate brackets.
_SUBFIELD_OR_CHAIN_RE = re.compile(
    r"marc:subfield\[([^\]]*?@code\s*=\s*'[^']+'(?:\s+or\s+@code\s*=\s*'[^']+')+[^\]]*?)\]"
)
_SUBFIELD_CODE_INNER_RE = re.compile(r"@code\s*=\s*'([^']+)'")

# Position reads: substring against another controlfield by tag, OR
# substring against the current node (``.``) — the latter only makes
# sense when the template is rooted on a controlfield or leader.
_SUBSTRING_TAGGED_RE = re.compile(
    r"substring\s*\(\s*marc:controlfield\[@tag='([^']+)'\]\s*,\s*(\d+)\s*,\s*(\d+)\s*\)"
)
_SUBSTRING_LEADER_TAGGED_RE = re.compile(
    r"substring\s*\(\s*(?:\.\./|/marc:record/|/\*/|)marc:leader\s*,\s*(\d+)\s*,\s*(\d+)\s*\)"
)
_SUBSTRING_SELF_RE = re.compile(r"substring\s*\(\s*\.\s*,\s*(\d+)\s*,\s*(\d+)\s*\)")

_DYNAMIC_VAR_RE = re.compile(r"\{\s*(\$[A-Za-z_][\w]*)\s*\}")

_CONTROLFIELD_TAGS: Final[frozenset[str]] = frozenset(
    {"001", "003", "005", "006", "007", "008", "009"}
)


# --- public entry point -----------------------------------------------------


def parse_xslt_corpus(entry_point: Path) -> ParseReport:
    """Parse the XSLT include graph rooted at ``entry_point``.

    The entry point and every module it transitively includes via
    ``xsl:include`` is walked; ``xsl:import`` and non-sibling hrefs raise
    :exc:`NotImplementedError` (marc2bibframe2 does not use them today —
    raising loudly catches a future divergence rather than silently
    missing templates).
    """
    modules = _walk_includes(entry_point)
    templates: list[TemplateFact] = []
    for module_path in modules:
        templates.extend(_extract_templates_from_module(module_path))
    sha = _resolve_xslt_commit_sha(entry_point)
    return ParseReport(
        templates=tuple(templates),
        parsed_modules=tuple(module.name for module in modules),
        xslt_commit_sha=sha,
    )


# --- include-graph traversal ------------------------------------------------


def _walk_includes(entry: Path) -> list[Path]:
    """Breadth-first traversal of ``xsl:include`` hrefs.

    Returns paths in BFS order, deduped by resolved real path. Modules
    that don't exist on disk (a typo in ``href``) raise
    :exc:`FileNotFoundError`.
    """
    seen: set[Path] = set()
    order: list[Path] = []
    queue: deque[Path] = deque([entry.resolve()])
    while queue:
        current = queue.popleft()
        if current in seen:
            continue
        if not current.is_file():
            raise FileNotFoundError(f"XSLT module not found: {current}")
        seen.add(current)
        order.append(current)

        tree = etree.parse(str(current))
        root = tree.getroot()
        # xsl:import is rare and has different precedence semantics —
        # marc2bibframe2 doesn't use it; raise if we ever see it.
        for imp in root.findall(f"{{{XSL_NS}}}import"):
            href = imp.get("href", "")
            msg = f"xsl:import not supported by the analyzer (in {current.name}, href={href!r})"
            raise NotImplementedError(msg)
        for inc in root.iter(f"{{{XSL_NS}}}include"):
            href = inc.get("href")
            if not href:
                continue
            if href.startswith(("http://", "https://", "file://")) or href.startswith(".."):
                msg = (
                    f"xsl:include with non-sibling href not supported "
                    f"(in {current.name}, href={href!r})"
                )
                raise NotImplementedError(msg)
            included = (current.parent / href).resolve()
            queue.append(included)
    return order


# --- per-template extraction ------------------------------------------------


def _extract_templates_from_module(module_path: Path) -> list[TemplateFact]:

    tree = etree.parse(str(module_path))

    # 1. Collect all templates and their bodies
    all_templates: dict[str, tuple[etree._Element, str | None]] = {}
    template_elements: list[etree._Element] = []

    for template in tree.getroot().iter(f"{{{XSL_NS}}}template"):
        # Unique ID for the template: match string if present, otherwise name
        t_id = template.get("match") or template.get("name")
        if not t_id:
            continue
        all_templates[t_id] = (template, template.get("match"))
        template_elements.append(template)

    # 2. Build a simple call graph and aggregate references
    # We want to know: for each template, what MARC references does it contain,
    # and which other templates does it call?

    # template_id -> {tags, indicators, subfields}
    template_refs: dict[str, tuple[set[str], set[tuple[IndicatorSlot, str]], set[str]]] = {}
    # template_id -> set of called template_ids
    call_graph: dict[str, set[str]] = {}

    for t_id, (template, _) in all_templates.items():
        body_text = _serialize_template_body(template)
        tags, indicators, subfields = _extract_all_marc_references(body_text)
        template_refs[t_id] = (tags, indicators, subfields)

        # Find calls to other templates
        # Note: this is a simplification. We look for apply-templates/for-each
        # and try to guess the target.
        calls = set()
        for descendant in template.iter():
            if descendant.tag == f"{{{XSL_NS}}}apply-templates":
                # If mode is specified, it's a strong hint for a named template
                mode = descendant.get("mode")
                if mode:
                    # Search for templates with this mode.
                    # This is tricky because match attributes can also be related to mode.
                    # For now, we'll look for templates that have this mode.
                    for other_id, (other_tpl, _) in all_templates.items():
                        if other_tpl.get("mode") == mode:
                            calls.add(other_id)

                # Also check select for direct MARC references
                # (already handled by _extract_all_marc_references)
                # and potential named template calls (though la-based calls are more common)

        call_graph[t_id] = calls

    # 3. Propagate references up the graph (Fixed-point iteration)
    # We keep expanding the set of references for each template until no more changes occur.
    changed = True
    while changed:
        changed = False
        for t_id in all_templates:
            tags, indicators, subfields = template_refs[t_id]
            for called_id in call_graph[t_id]:
                c_tags, c_indicators, c_subfields = template_refs[called_id]

                if (
                    not c_tags.issubset(tags)
                    or not c_indicators.issubset(indicators)
                    or not c_subfields.issubset(subfields)
                ):
                    template_refs[t_id] = (
                        tags | c_tags,
                        indicators | c_indicators,
                        subfields | c_subfields,
                    )
                    changed = True

    # 4. Emit facts for templates that match MARC nodes
    facts: list[TemplateFact] = []
    for t_id, (template, match_attr) in all_templates.items():
        if not match_attr:
            continue  # Only emit facts for templates that are entry points for MARC nodes

        parsed = _parse_match_attr(match_attr)
        if not parsed:
            continue

        mode = template.get("mode")
        body_text = _serialize_template_body(template)

        # Get aggregated references for this template
        _, agg_indicators, agg_subfields = template_refs[t_id]

        for tag, alias_for in parsed:
            # We only care about the references that are relevant to this specific tag
            # if the template is a generic one. But the la-fact is "what does the
            # XSLT do when it hits tag X".
            # If the template matches tag X, any reference inside it (or its callees)
            # is part of the "handling" of tag X.

            # However, the current la-fact structure separates indicator_tests
            # and subfield_codes.

            # We use the aggregated set, but for indicators, we should only
            # keep those that were actually "tests" (i.e. @ind1 = '0').
            # The la-fact currently collects all @ind1 = 'X' from the body.

            # Since _extract_all_marc_references uses the same regexes,
            # the aggregated set is correct.

            facts.append(
                _build_template_fact(
                    module_path=module_path,
                    template=template,
                    body_text=body_text,  # Still used for la-fact's original body analysis
                    tag=tag,
                    mode=mode,
                    alias_for=alias_for,
                    # Pass in the aggregated references
                    override_indicators=frozenset(agg_indicators),
                    override_subfields=frozenset(agg_subfields),
                )
            )
    return facts


def _parse_match_attr(match_attr: str) -> list[tuple[str, str | None]]:
    """Parse a template ``@match`` value into ``(tag, alias_for)`` pairs.

    Returns:

    - ``[]`` if the attribute doesn't mention any MARC source node.
    - ``[("leader", None)]`` for ``marc:leader`` matches.
    - ``[("245", None)]`` for plain ``marc:datafield[@tag='245']``.
    - ``[("245", None), ("880", "245")]`` for the canonical
      ``marc:datafield[@tag='245' or (@tag='880' and substring(...)='245')]``
      shape — two facts share the body, but the 880 one is flagged as
      a $6 dispatch alias for the linked tag.
    """
    # Leader matches.
    if _LEADER_RE.search(match_attr) and "marc:" + "datafield" not in match_attr:
        return [("leader", None)]

    # 880 dispatch pair — must check before the plain datafield path so
    # we surface both tags.
    out: list[tuple[str, str | None]] = []
    for tag, alias in _DATAFIELD_880_RE.findall(match_attr):
        # ``tag`` is the linked tag, ``alias`` should equal ``tag`` per
        # marc2bibframe2's convention; trust the regex form.
        out.append((tag, None))
        out.append(("880", alias))
    if out:
        return out

    # Plain datafield tag(s).
    for tag in _DATAFIELD_TAG_RE.findall(match_attr):
        out.append((tag, None))

    # Plain controlfield tag(s).
    for tag in _CONTROLFIELD_TAG_RE.findall(match_attr):
        out.append((tag, None))
    return out


def _serialize_template_body(template: etree._Element) -> str:
    """Concatenate the relevant attribute values + text for regex scans.

    Position reads, indicator tests, and subfield-code references live
    inside the ``@select`` and ``@test`` attributes of XSLT instructions
    (``xsl:if``, ``xsl:when``, ``xsl:value-of``, ``xsl:for-each``,
    ``xsl:variable``, etc.) — so we don't need to scan element text or
    serialize the whole subtree. Concatenating these attribute values
    gives a body string the regexes above can sweep in one pass.
    """
    parts: list[str] = []
    for descendant in template.iter():
        for attr_name in ("select", "test", "match"):
            value = descendant.get(attr_name)
            if value:
                parts.append(value)
    return "\n".join(parts)


def _build_template_fact(
    *,
    module_path: Path,
    template: etree._Element,
    body_text: str,
    tag: str,
    mode: str | None,
    alias_for: str | None,
    override_indicators: frozenset[tuple[IndicatorSlot, str]] | None = None,
    override_subfields: frozenset[str] | None = None,
) -> TemplateFact:
    is_leader = tag == "leader"
    is_controlfield = tag in _CONTROLFIELD_TAGS
    is_datafield = not (is_leader or is_controlfield)

    if override_indicators is not None:
        indicator_tests = override_indicators
        # We can't easily determine if it's "projected" from an aggregated set,
        # so we fall back to scanning the local body for projection signals.
        indicator_projected = bool(_INDICATOR_REF_RE.search(body_text))
    else:
        empty_set: tuple[frozenset[tuple[IndicatorSlot, str]], bool] = (frozenset(), False)
        indicator_tests, indicator_projected = (
            _collect_indicators(body_text) if is_datafield else empty_set
        )

    if override_subfields is not None:
        subfields = override_subfields
    else:
        subfields = _collect_subfields(body_text) if is_datafield else frozenset()

    cf_positions, leader_positions = _collect_positions(body_text, tag, is_controlfield, is_leader)
    output_terms, dynamic_vars = _collect_output_terms(template)

    return TemplateFact(
        source_file=module_path.name,
        start_line=template.sourceline if isinstance(template.sourceline, int) else 0,
        tag=tag,
        mode=mode,
        is_880_alias_for=alias_for,
        indicator_tests=indicator_tests,
        indicator_projected=indicator_projected,
        subfield_codes=subfields,
        controlfield_position_reads=cf_positions,
        leader_position_reads=leader_positions,
        output_terms=frozenset(output_terms),
        dynamic_element_constructors=frozenset(dynamic_vars),
    )


def _collect_indicators(body_text: str) -> tuple[frozenset[tuple[IndicatorSlot, str]], bool]:
    indicator_tests: set[tuple[IndicatorSlot, str]] = set()
    for slot, value in _INDICATOR_TEST_RE.findall(body_text):
        indicator_tests.add((slot, value))
    if not indicator_tests:
        projected = bool(_INDICATOR_REF_RE.search(body_text))
    else:
        stripped = _INDICATOR_TEST_RE.sub("", body_text)
        projected = bool(_INDICATOR_REF_RE.search(stripped))
    return frozenset(indicator_tests), projected


def _collect_subfields(body_text: str) -> frozenset[str]:
    subfields: set[str] = set(_SUBFIELD_CODE_RE.findall(body_text))
    for predicate in _SUBFIELD_OR_CHAIN_RE.findall(body_text):
        subfields.update(_SUBFIELD_CODE_INNER_RE.findall(predicate))
    return frozenset(subfields)


def _collect_positions(
    body_text: str,
    tag: str,
    is_controlfield: bool,
    is_leader: bool,
) -> tuple[frozenset[tuple[int, int]], frozenset[tuple[int, int]]]:
    cf_positions: set[tuple[int, int]] = set()
    leader_positions: set[tuple[int, int]] = set()

    if is_controlfield:
        for start, length in _SUBSTRING_SELF_RE.findall(body_text):
            cf_positions.add((int(start), int(length)))
        for ref_tag, start, length in _SUBSTRING_TAGGED_RE.findall(body_text):
            if ref_tag == tag:
                cf_positions.add((int(start), int(length)))
    elif is_leader:
        for start, length in _SUBSTRING_SELF_RE.findall(body_text):
            leader_positions.add((int(start), int(length)))
        for start, length in _SUBSTRING_LEADER_TAGGED_RE.findall(body_text):
            leader_positions.add((int(start), int(length)))
    return frozenset(cf_positions), frozenset(leader_positions)


def _collect_output_terms(
    template: etree._Element,
) -> tuple[set[OutputTerm], set[str]]:
    """Walk the template subtree harvesting BIBFRAME element constructors.

    Two paths:

    - Literal prefixed elements: any descendant whose QName has a prefix
      in :data:`_OUTPUT_PREFIXES` is recorded as a literal-origin term.
    - ``<xsl:element name="...">``: literal QName names are recorded as
      ``xsl:element``-origin terms; dynamic ``{$var}`` or
      ``{concat(...)}`` names are recorded as unknown-kind placeholders
      and accumulated separately so the dynamic appendix can list them.
    """
    out: set[OutputTerm] = set()
    dynamic_vars: set[str] = set()
    nsmap_inv: dict[str, str] = {v: k for k, v in template.nsmap.items() if k}
    xsl_element = f"{{{XSL_NS}}}element"

    for descendant in template.iter():
        if not isinstance(descendant.tag, str):
            continue  # comments / processing-instructions

        # xsl:element name="..." — literal or dynamic
        if descendant.tag == xsl_element:
            name_attr = descendant.get("name", "")
            stripped = name_attr.strip()
            if not stripped:
                continue
            if "{" in stripped:
                # Dynamic: e.g. ``{$vTitleClass}`` or ``{concat('madsrdf:', $pMADSClass)}``
                match = _DYNAMIC_VAR_RE.search(stripped)
                if match:
                    dynamic_vars.add(match.group(1))
                else:
                    dynamic_vars.add(stripped)
                out.add(
                    OutputTerm(
                        qname=f"<dynamic:{stripped}>",
                        kind="unknown",
                        origin="xsl:element",
                    )
                )
                continue
            if ":" in stripped:
                prefix, local = stripped.split(":", 1)
                if prefix in _OUTPUT_PREFIXES:
                    out.add(
                        OutputTerm(
                            qname=stripped,
                            kind=_classify_kind(local),
                            origin="xsl:element",
                        )
                    )
            continue

        # Literal prefixed element. lxml expands ``bf:Work`` -> ``{NS}Work``
        # in ``.tag``; we recover the prefix via the inherited nsmap.
        if not descendant.tag.startswith("{"):
            continue
        ns_uri = descendant.tag[1:].split("}", 1)[0]
        literal_prefix = nsmap_inv.get(ns_uri)
        if literal_prefix is None or literal_prefix not in _OUTPUT_PREFIXES:
            continue
        local = descendant.tag.split("}", 1)[1]
        out.add(
            OutputTerm(
                qname=f"{literal_prefix}:{local}",
                kind=_classify_kind(local),
                origin="literal",
            )
        )
    return out, dynamic_vars


def _classify_kind(local_name: str) -> OutputKind:
    """Classes start with an uppercase letter, predicates lowercase.

    Matches the convention BIBFRAME uses rigorously (``bf:Work`` /
    ``bf:Instance`` are classes; ``bf:title`` / ``bf:hasInstance`` are
    predicates). Empty or unparseable names fall through to ``unknown``.
    """
    if not local_name:
        return "unknown"
    first = local_name[0]
    if first.isupper():
        return "class"
    if first.islower():
        return "predicate"
    return "unknown"


# --- vendored-submodule SHA -------------------------------------------------


def _resolve_xslt_commit_sha(entry_point: Path) -> str | None:
    """Best-effort: read the marc2bibframe2 submodule's HEAD SHA.

    Returns ``None`` if git is unavailable or the path isn't a git
    repository — the doc-generation must still work in those cases
    (e.g. when ``third_party/`` is a vendored tarball rather than a
    submodule).
    """
    submodule_root = entry_point.parent.parent  # xsl/ → submodule root
    try:
        proc = subprocess.run(
            ["git", "-C", str(submodule_root), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5.0,
            check=False,
        )
    except FileNotFoundError, subprocess.TimeoutExpired:
        return None
    if proc.returncode != 0:
        return None
    sha = proc.stdout.strip()
    return sha or None
