from __future__ import annotations

"""Typed Oracle-IR lowering for combat declaration static abilities."""

import re
from typing import Any, Mapping

from ..ability_fragments import ability_fragment_to_dict
from ..declaration_costs import normalized_oracle_line, parse_declaration_cost_line
from ..declaration_fragments import (
    DECLARATION_COMPONENT_CAPABILITY_ID,
    DeclarationCostTemplate,
    DeclarationRequirementTemplate,
    DeclarationRestrictionTemplate,
)
from ..declaration_requirements import parse_declaration_requirement_line
from ..declaration_restrictions import parse_declaration_restriction_line
from ..rules.capabilities import CapabilityRegistry
from ..semantic_runtime.ability_fragments import (
    DECLARATION_COST_FRAGMENT_HANDLER_ID,
    DECLARATION_REQUIREMENT_FRAGMENT_HANDLER_ID,
    DECLARATION_RESTRICTION_FRAGMENT_HANDLER_ID,
)
from .dependency_gate import explicit_capability_gate
from .ir_model import append_residual, OracleNode, OracleResidual, SourceSpan
from .continuous_templates import (
    _ATTACHED_SUBJECT,
    _TRAILING_REMINDER,
    _attached_modifier,
    attached_fixed_characteristics_handler,
    fixed_query_keyword_grant_handler,
)
from .public_state_queries import fixed_characteristic_battlefield_query_subject


DeclarationTemplate = (
    DeclarationCostTemplate
    | DeclarationRequirementTemplate
    | DeclarationRestrictionTemplate
)


def fixed_declaration_fragment_sequence(
    text: str,
    *,
    card_name: str = "",
) -> tuple[
    DeclarationRequirementTemplate | DeclarationRestrictionTemplate, ...
]:
    """Parse one exact static declaration fragment or closed conjunction."""

    requirement = parse_declaration_requirement_line(text, card_name=card_name)
    if requirement is not None:
        return (requirement,)
    restriction = parse_declaration_restriction_line(text, card_name=card_name)
    if restriction.exact and restriction.template is not None:
        return (restriction.template,)

    normalized = normalized_oracle_line(text, card_name=card_name)
    component_lines: tuple[str, ...] = ()
    if normalized == "this creature can't block and can't be blocked.":
        component_lines = (
            "This creature can't block.",
            "This creature can't be blocked.",
        )
    elif normalized == "this creature attacks or blocks each combat if able.":
        component_lines = (
            "This creature attacks each combat if able.",
            "This creature blocks each combat if able.",
        )
    if not component_lines:
        return ()

    fragments: list[
        DeclarationRequirementTemplate | DeclarationRestrictionTemplate
    ] = []
    for component in component_lines:
        requirement = parse_declaration_requirement_line(component)
        if requirement is not None:
            fragments.append(requirement)
            continue
        restriction = parse_declaration_restriction_line(component)
        if not restriction.exact or restriction.template is None:
            return ()
        fragments.append(restriction.template)
    return tuple(fragments)


def _declaration_fragment_handler_id(template: DeclarationTemplate) -> str:
    if isinstance(template, DeclarationCostTemplate):
        return DECLARATION_COST_FRAGMENT_HANDLER_ID
    if isinstance(template, DeclarationRequirementTemplate):
        return DECLARATION_REQUIREMENT_FRAGMENT_HANDLER_ID
    return DECLARATION_RESTRICTION_FRAGMENT_HANDLER_ID


def _typed_declaration_node(
    *,
    node_id: str,
    line: str,
    span: SourceSpan,
    template: DeclarationTemplate,
    handler_id: str,
    residuals: list[OracleResidual],
    capability_registry: CapabilityRegistry | None,
    capability_profile: str,
    dependency_reason: str,
    cost: dict[str, Any] | None = None,
) -> OracleNode:
    dependencies = template.mechanics
    gate = explicit_capability_gate(
        DECLARATION_COMPONENT_CAPABILITY_ID,
        capability_registry=capability_registry,
        capability_profile=capability_profile,
    )
    missing = gate.blockers
    residual_ids = (
        (
            append_residual(
                residuals,
                kind="dependency_contract",
                text=line,
                span=span,
                reason=dependency_reason,
                blockers=missing,
            ),
        )
        if missing
        else ()
    )
    closure = gate.closure
    return OracleNode(
        node_id=node_id,
        kind="static_ability",
        text=line,
        span=span,
        active_zone="battlefield",
        event="combat.declaration",
        lowerable=True,
        exact=not missing,
        template_id=template.template_id,
        cost=cost,
        handlers=(
            {
                "handler_id": handler_id,
                "schema_version": 1,
                "event": "combat.declaration",
                "fragment": ability_fragment_to_dict(template),
            },
        ),
        mechanics=dependencies,
        residual_ids=residual_ids,
        capability_dependencies=gate.capabilities,
        capability_closure=closure.reachable if closure is not None else (),
        capability_profile=closure.profile if closure is not None else None,
        capability_fingerprint=(
            closure.fingerprint if closure is not None else None
        ),
    )


def _typed_declaration_fragment_sequence_node(
    *,
    node_id: str,
    line: str,
    span: SourceSpan,
    templates: tuple[
        DeclarationRequirementTemplate | DeclarationRestrictionTemplate, ...
    ],
    residuals: list[OracleResidual],
    capability_registry: CapabilityRegistry | None,
    capability_profile: str,
) -> OracleNode:
    gate = explicit_capability_gate(
        DECLARATION_COMPONENT_CAPABILITY_ID,
        capability_registry=capability_registry,
        capability_profile=capability_profile,
    )
    missing = gate.blockers
    residual_ids = (
        (
            append_residual(
                residuals,
                kind="dependency_contract",
                text=line,
                span=span,
                reason=(
                    "declaration fragment composition depends on untrusted "
                    "mechanic or capability contracts"
                ),
                blockers=missing,
            ),
        )
        if missing
        else ()
    )
    closure = gate.closure
    return OracleNode(
        node_id=node_id,
        kind="static_ability",
        text=line,
        span=span,
        active_zone="battlefield",
        event="combat.declaration",
        lowerable=True,
        exact=not missing,
        template_id="intrinsic-compound-declaration-fragments-v1",
        handlers=tuple(
            {
                "handler_id": _declaration_fragment_handler_id(template),
                "schema_version": 1,
                "event": "combat.declaration",
                "fragment": ability_fragment_to_dict(template),
            }
            for template in templates
        ),
        mechanics=tuple(
            sorted(
                {
                    mechanic
                    for template in templates
                    for mechanic in template.mechanics
                }
            )
        ),
        residual_ids=residual_ids,
        capability_dependencies=gate.capabilities,
        capability_closure=closure.reachable if closure is not None else (),
        capability_profile=closure.profile if closure is not None else None,
        capability_fingerprint=(
            closure.fingerprint if closure is not None else None
        ),
    )


def declaration_static_node(
    *,
    node_id: str,
    line: str,
    card_name: str,
    span: SourceSpan,
    residuals: list[OracleResidual],
    capability_registry: CapabilityRegistry | None,
    capability_profile: str,
) -> OracleNode | None:
    """Compile one bounded declaration line or return ``None``."""

    fragments = fixed_declaration_fragment_sequence(line, card_name=card_name)
    if len(fragments) > 1:
        return _typed_declaration_fragment_sequence_node(
            node_id=node_id,
            line=line,
            span=span,
            templates=fragments,
            residuals=residuals,
            capability_registry=capability_registry,
            capability_profile=capability_profile,
        )

    requirement = parse_declaration_requirement_line(line, card_name=card_name)
    if requirement is not None:
        return _typed_declaration_node(
            node_id=node_id,
            line=line,
            span=span,
            template=requirement,
            handler_id=DECLARATION_REQUIREMENT_FRAGMENT_HANDLER_ID,
            residuals=residuals,
            capability_registry=capability_registry,
            capability_profile=capability_profile,
            dependency_reason=(
                "declaration requirement depends on untrusted mechanic or "
                "capability contracts"
            ),
        )

    cost_parse = parse_declaration_cost_line(line, card_name=card_name)
    if cost_parse.recognized:
        template = cost_parse.template
        if cost_parse.exact and template is not None:
            return _typed_declaration_node(
                node_id=node_id,
                line=line,
                span=span,
                template=template,
                handler_id=DECLARATION_COST_FRAGMENT_HANDLER_ID,
                residuals=residuals,
                capability_registry=capability_registry,
                capability_profile=capability_profile,
                dependency_reason=(
                    "declaration cost depends on untrusted mechanic or "
                    "capability contracts"
                ),
                cost={
                    "kind": "declaration_mana",
                    "declarations": list(template.declarations),
                    "scope": template.scope,
                    "mana": dict(template.mana),
                    "printed": template.printed_cost,
                    "source_condition": template.source_condition,
                    "includes_planeswalkers": template.includes_planeswalkers,
                },
            )
        residual_id = append_residual(
            residuals,
            kind="declaration_cost",
            text=line,
            span=span,
            reason=cost_parse.reason or "declaration cost grammar is unresolved",
            blockers=(
                "nonmana declaration costs",
                "variable and alternative mana declaration costs",
                "conditional declaration-cost grammar",
            ),
        )
        return OracleNode(
            node_id=node_id,
            kind="static_ability",
            text=line,
            span=span,
            active_zone="battlefield",
            event="continuous",
            lowerable=False,
            exact=False,
            mechanics=cost_parse.declarations,
            residual_ids=(residual_id,),
        )

    restriction_parse = parse_declaration_restriction_line(
        line, card_name=card_name
    )
    if not restriction_parse.recognized:
        return None
    template = restriction_parse.template
    if restriction_parse.exact and template is not None:
        return _typed_declaration_node(
            node_id=node_id,
            line=line,
            span=span,
            template=template,
            handler_id=DECLARATION_RESTRICTION_FRAGMENT_HANDLER_ID,
            residuals=residuals,
            capability_registry=capability_registry,
            capability_profile=capability_profile,
            dependency_reason=(
                "declaration restriction depends on untrusted mechanic or "
                "capability contracts"
            ),
        )
    dependencies = tuple(
        mechanic
        for declaration, mechanic in (
            ("attack", "cr-508-declare-attackers-step"),
            ("block", "cr-509-declare-blockers-step"),
        )
        if declaration in restriction_parse.declarations
    )
    residual_id = append_residual(
        residuals,
        kind="declaration_restriction",
        text=line,
        span=span,
        reason=(
            restriction_parse.reason
            or "declaration restriction grammar is unresolved"
        ),
        blockers=(
            "conditional declaration predicates",
            "temporary declaration restrictions",
            "broader evasion and group constraints",
        ),
    )
    return OracleNode(
        node_id=node_id,
        kind="static_ability",
        text=line,
        span=span,
        active_zone="battlefield",
        event="continuous",
        lowerable=False,
        exact=False,
        mechanics=dependencies,
        residual_ids=(residual_id,),
    )


DeclarationGrantFragment = (
    DeclarationRequirementTemplate | DeclarationRestrictionTemplate
)


def _declaration_grant_fragments(
    text: str,
    *,
    source_name: str,
) -> tuple[DeclarationGrantFragment, ...]:
    return fixed_declaration_fragment_sequence(text, card_name=source_name)


def _self_declaration_rule(text: str) -> str:
    normalized = text.strip().rstrip(".")
    normalized = re.sub(
        r"^attack each",
        "attacks each",
        normalized,
        flags=re.IGNORECASE,
    )
    normalized = re.sub(
        r"^block each",
        "blocks each",
        normalized,
        flags=re.IGNORECASE,
    )
    return f"This creature {normalized}."


def _declaration_grant_target(
    text: str,
    *,
    source_name: str,
) -> tuple[str, str, tuple[DeclarationGrantFragment, ...]] | None:
    words = text.strip().rstrip(".").split()
    for split in range(1, len(words)):
        subject = " ".join(words[:split])
        rest = " ".join(words[split:])
        attached = subject.casefold() in {
            "enchanted creature",
            "equipped creature",
        }
        inner = (
            f"This creature {rest}."
            if attached
            else _self_declaration_rule(rest)
        )
        fragments = _declaration_grant_fragments(
            inner,
            source_name=source_name,
        )
        if not fragments:
            continue
        if attached:
            return "attached", subject, fragments
        if fixed_characteristic_battlefield_query_subject(subject) is not None:
            return "query", subject, fragments
    return None


def _append_attached_declaration_fragments(
    compiled: tuple[str, Mapping[str, Any], tuple[str, ...]],
    fragments: tuple[DeclarationGrantFragment, ...],
) -> tuple[str, Mapping[str, Any], tuple[str, ...]]:
    _template_id, raw_handler, raw_capabilities = compiled
    handler = dict(raw_handler)
    modifier = {
        key: list(value) if isinstance(value, list) else value
        for key, value in dict(handler["modifier"]).items()
    }
    modifier["add_ability_fragments"].extend(
        ability_fragment_to_dict(fragment) for fragment in fragments
    )
    handler["modifier"] = modifier
    return (
        "continuous-attached-characteristics-declaration-grant-v1",
        handler,
        tuple(
            sorted(
                {
                    *raw_capabilities,
                    DECLARATION_COMPONENT_CAPABILITY_ID,
                }
            )
        ),
    )


def _direct_declaration_grant(
    text: str,
    *,
    source_name: str,
) -> tuple[str, Mapping[str, Any], tuple[str, ...]] | None:
    target = _declaration_grant_target(text, source_name=source_name)
    if target is None:
        return None
    relation, subject, fragments = target
    fragment_values = [
        ability_fragment_to_dict(fragment) for fragment in fragments
    ]
    if relation == "attached":
        modifier = _attached_modifier()
        modifier["add_ability_fragments"].extend(fragment_values)
        return (
            "continuous-attached-declaration-grant-v1",
            {
                "handler_id": "continuous.attached.fixed-characteristics.v1",
                "schema_version": 1,
                "event": "characteristics.evaluate",
                "condition": {
                    "relation": "source_attached_object",
                    "types_all": ["creature"],
                },
                "modifier": modifier,
            },
            tuple(
                sorted(
                    {
                        "continuous.attached.fixed_characteristics",
                        DECLARATION_COMPONENT_CAPABILITY_ID,
                    }
                )
            ),
        )
    parsed = fixed_characteristic_battlefield_query_subject(subject)
    assert parsed is not None
    target_controller, predicate, exclude_source = parsed
    return (
        "continuous-fixed-query-declaration-grant-v1",
        {
            "handler_id": "continuous.ability.fixed-query-grant.v1",
            "schema_version": 1,
            "event": "characteristics.evaluate",
            "condition": {
                "target_controller": target_controller,
                "predicate": predicate.to_dict(),
                "exclude_source": exclude_source,
            },
            "modifier": {"add_ability_fragments": fragment_values},
        },
        tuple(
            sorted(
                {
                    "continuous.ability.fixed_query_grant",
                    DECLARATION_COMPONENT_CAPABILITY_ID,
                }
            )
        ),
    )


def _attached_characteristic_declaration_grant(
    text: str,
    *,
    source_name: str,
) -> tuple[str, Mapping[str, Any], tuple[str, ...]] | None:
    subject_match = re.match(
        rf"^(?P<subject>{_ATTACHED_SUBJECT}) (?P<body>.+)$",
        text,
        re.IGNORECASE,
    )
    if subject_match is None:
        return None
    subject = subject_match.group("subject")
    body = subject_match.group("body").rstrip(".")
    separators = tuple(re.finditer(r",?\s+and\s+", body, re.IGNORECASE))
    for separator in reversed(separators):
        characteristic_body = body[: separator.start()].rstrip(", ")
        rule_body = body[separator.end() :]
        fragments = _declaration_grant_fragments(
            _self_declaration_rule(rule_body),
            source_name=source_name,
        )
        if not fragments:
            continue
        characteristic_body = re.sub(
            r"^(gets [+-]\d+/[+-]\d+), has ",
            r"\1 and has ",
            characteristic_body,
            flags=re.IGNORECASE,
        )
        characteristics = attached_fixed_characteristics_handler(
            f"{subject} {characteristic_body}.",
            source_name=source_name,
        )
        if characteristics is not None:
            return _append_attached_declaration_fragments(
                characteristics,
                fragments,
            )
    return None


def _query_keyword_declaration_grant(
    text: str,
    *,
    source_name: str,
) -> tuple[str, Mapping[str, Any], tuple[str, ...]] | None:
    separators = tuple(re.finditer(r",?\s+and\s+", text, re.IGNORECASE))
    for separator in reversed(separators):
        characteristic_text = text[: separator.start()].rstrip(", ")
        rule_body = text[separator.end() :].rstrip(".")
        subject_match = re.fullmatch(
            r"(?P<subject>.+?) ha(?:ve|s) .+",
            characteristic_text,
            re.IGNORECASE,
        )
        if subject_match is None:
            continue
        fragments = _declaration_grant_fragments(
            _self_declaration_rule(rule_body),
            source_name=source_name,
        )
        keywords = fixed_query_keyword_grant_handler(characteristic_text + ".")
        if not fragments or keywords is None:
            continue
        parsed = fixed_characteristic_battlefield_query_subject(
            subject_match.group("subject")
        )
        if parsed is None or keywords[1]["condition"] != {
            "target_controller": parsed[0],
            "predicate": parsed[1].to_dict(),
            "exclude_source": parsed[2],
        }:
            continue
        return (
            "continuous-fixed-query-keywords-declaration-grant-v1",
            {
                "handler_id": "continuous.ability.fixed-query-grant.v1",
                "schema_version": 1,
                "event": "characteristics.evaluate",
                "condition": dict(keywords[1]["condition"]),
                "modifier": {
                    "add_abilities": list(
                        keywords[1]["modifier"]["add_abilities"]
                    ),
                    "add_ability_fragments": [
                        ability_fragment_to_dict(fragment)
                        for fragment in fragments
                    ],
                },
            },
            tuple(
                sorted(
                    {
                        *keywords[2],
                        "continuous.ability.fixed_query_grant",
                        DECLARATION_COMPONENT_CAPABILITY_ID,
                    }
                )
            ),
        )
    return None


def fixed_static_declaration_grant_handler(
    oracle_line: str,
    *,
    source_name: str = "source",
) -> tuple[str, Mapping[str, Any], tuple[str, ...]] | None:
    """Grant exact declaration fragments to an attached or queried set."""

    text = _TRAILING_REMINDER.sub("", oracle_line.strip()).strip()
    return (
        _direct_declaration_grant(text, source_name=source_name)
        or _attached_characteristic_declaration_grant(
            text,
            source_name=source_name,
        )
        or _query_keyword_declaration_grant(text, source_name=source_name)
    )


__all__ = [
    "declaration_static_node",
    "fixed_declaration_fragment_sequence",
    "fixed_static_declaration_grant_handler",
]
