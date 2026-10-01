from __future__ import annotations

"""Compile explicit token abilities into source-pinned typed programs."""

import copy
from dataclasses import replace
import re
from typing import Any, Callable, Mapping, Sequence

from ..ability_fragments import ability_fragment_to_dict
from ..characteristic_evaluation import type_parts
from ..fixed_token_production import TYPED_TOKEN_ABILITY_MECHANIC_ID
from ..rules.capabilities import (
    CapabilityRegistry,
    capability_dependencies_for_node,
)
from ..semantic_runtime.ability_fragments import fragments_from_descriptors
from .closed_static_nodes import closed_static_or_replacement_node
from .dependency_gate import dependency_gate
from .ir_model import OracleNode, SourceSpan


CompileInner = Callable[..., OracleNode | None]
TriggerNode = Callable[..., OracleNode | None]
EffectTemplate = Callable[..., tuple[Any, ...]]
GrantEffectTemplates = Callable[..., tuple[Any, Any]]

_QUOTED = re.compile(r'"(?P<ability>[^"]+)"')
_POSTPOSED = re.compile(
    r'\.\s+(?:It|They)\s+(?:has|have)\s+"[^"]+"\.?',
    re.IGNORECASE,
)
_AND_QUOTED = re.compile(r'\s+and\s+"[^"]+"', re.IGNORECASE)
_WITH_QUOTED = re.compile(r'\s+with\s+"[^"]+"', re.IGNORECASE)
_ABILITY_WORD = re.compile(r"^[A-Za-z][A-Za-z ']+\s+[—-]\s+(?P<body>.+)$")
_PERMANENT_CARD_TYPES = frozenset(
    {"artifact", "battle", "creature", "enchantment", "land", "planeswalker"}
)
_SPELL_CARD_TYPES = frozenset({"instant", "sorcery"})


def _token_ability_shell(text: str) -> tuple[str, tuple[str, ...]] | None:
    """Remove only explicit quoted abilities attached to a created token."""

    normalized = text.strip()
    matches = tuple(_QUOTED.finditer(normalized))
    if not matches or len(matches) > 2:
        return None
    prefix = normalized[: matches[0].start()]
    if (
        re.search(r"\bcreate\b", prefix, re.IGNORECASE) is None
        or re.search(r"\btokens?\b", prefix, re.IGNORECASE) is None
    ):
        return None
    shell = _POSTPOSED.sub(".", normalized)
    shell = _AND_QUOTED.sub("", shell)
    shell = _WITH_QUOTED.sub("", shell)
    shell = re.sub(r"\s+\.", ".", shell).strip()
    if shell == normalized or '"' in shell:
        return None
    return shell, tuple(match.group("ability") for match in matches)


def _source_descriptor(type_line: str) -> str:
    lowered = type_line.casefold()
    if "creature" in lowered:
        return "this creature"
    if "artifact" in lowered:
        return "this artifact"
    if "enchantment" in lowered:
        return "this enchantment"
    return "this permanent"


def _compile_token_ability_text(text: str, *, type_line: str) -> str:
    return re.sub(
        r"\bthis token\b",
        _source_descriptor(type_line),
        text,
        flags=re.IGNORECASE,
    )


def _created_token_effects(value: Any) -> tuple[Mapping[str, Any], ...]:
    found: list[Mapping[str, Any]] = []
    if isinstance(value, Mapping):
        if value.get("op") == "create_token":
            found.append(value)
        for child in value.values():
            found.extend(_created_token_effects(child))
    elif isinstance(value, (list, tuple)):
        for child in value:
            found.extend(_created_token_effects(child))
    return tuple(found)


def _with_token_fragments(
    value: Any,
    *,
    fragments: Sequence[Mapping[str, Any]],
) -> tuple[Any, int]:
    if isinstance(value, Mapping):
        result = {str(key): copy.deepcopy(child) for key, child in value.items()}
        if result.get("op") == "create_token":
            characteristics = dict(result.get("characteristics") or {})
            characteristics["ability_fragments"] = [
                *copy.deepcopy(list(characteristics.get("ability_fragments") or ())),
                *(copy.deepcopy(dict(fragment)) for fragment in fragments),
            ]
            result["characteristics"] = characteristics
            return result, 1
        changed = 0
        for key, child in tuple(result.items()):
            result[key], count = _with_token_fragments(
                child, fragments=fragments
            )
            changed += count
        return result, changed
    if isinstance(value, (list, tuple)):
        result = []
        changed = 0
        for child in value:
            updated, count = _with_token_fragments(child, fragments=fragments)
            result.append(updated)
            changed += count
        return (tuple(result) if isinstance(value, tuple) else result), changed
    return copy.deepcopy(value), 0


def _spell_outer_node(
    *,
    node_id: str,
    line: str,
    shell: str,
    span: SourceSpan,
    source_name: str,
    effect_template: EffectTemplate,
    trusted_mechanics: frozenset[str],
    capability_registry: CapabilityRegistry,
    capability_profile: str,
) -> OracleNode | None:
    ability_word = _ABILITY_WORD.fullmatch(shell)
    body = ability_word.group("body") if ability_word is not None else shell
    template, effects, target_schema, mechanics = effect_template(
        body, card_name=source_name
    )
    if template is None:
        return None
    gate = dependency_gate(
        mechanics=mechanics,
        effects=effects,
        target_schema=target_schema,
        trusted_mechanics=trusted_mechanics,
        capability_registry=capability_registry,
        capability_profile=capability_profile,
    )
    if gate.blockers or gate.closure is None:
        return None
    return OracleNode(
        node_id=node_id,
        kind="spell_ability",
        text=line,
        span=span,
        active_zone="stack",
        event="resolve",
        lowerable=True,
        exact=True,
        template_id=template,
        effects=effects,
        target_schema=target_schema,
        mechanics=mechanics,
        capability_dependencies=gate.capabilities,
        capability_closure=gate.closure.reachable,
        capability_profile=gate.closure.profile,
        capability_fingerprint=gate.closure.fingerprint,
    )


def _outer_nodes(
    *,
    node_id: str,
    line: str,
    shell: str,
    span: SourceSpan,
    source_name: str,
    printed_card_types: tuple[str, ...],
    keywords: Sequence[str],
    source_attachment_relation: Any,
    effect_template: EffectTemplate,
    compile_inner: CompileInner,
    trigger_node: TriggerNode,
    grant_effect_templates: GrantEffectTemplates,
    trusted_mechanics: frozenset[str],
    capability_registry: CapabilityRegistry,
    capability_profile: str,
) -> tuple[OracleNode, ...] | None:
    if set(printed_card_types).intersection(_SPELL_CARD_TYPES):
        node = _spell_outer_node(
            node_id=node_id,
            line=line,
            shell=shell,
            span=span,
            source_name=source_name,
            effect_template=effect_template,
            trusted_mechanics=trusted_mechanics,
            capability_registry=capability_registry,
            capability_profile=capability_profile,
        )
        return (node,) if node is not None else None
    residuals: list[Any] = []
    source_effect, source_trigger_effect = grant_effect_templates(
        True,
        tuple(printed_card_types),
        source_attachment_relation,
    )
    node = compile_inner(
        node_id=node_id,
        line=shell,
        material_line=shell,
        span=span,
        card_name=source_name,
        type_line=" ".join(value.title() for value in printed_card_types),
        keywords=keywords,
        trusted_mechanics=trusted_mechanics,
        capability_registry=capability_registry,
        capability_profile=capability_profile,
        residuals=residuals,
        effect_template=source_effect,
        trigger_effect_template=source_trigger_effect,
    )
    if node is None:
        node = trigger_node(
            node_id=node_id,
            line=shell,
            material_line=shell,
            span=span,
            card_name=source_name,
            trusted_mechanics=trusted_mechanics,
            capability_registry=capability_registry,
            capability_profile=capability_profile,
            residuals=residuals,
            effect_template=source_trigger_effect,
        )
    return (
        (replace(node, text=line, span=span),)
        if node is not None and node.exact and not residuals
        else None
    )


def _inner_node(
    *,
    node_id: str,
    quoted: str,
    token_type_line: str,
    token_keywords: tuple[str, ...],
    span: SourceSpan,
    compile_inner: CompileInner,
    trigger_node: TriggerNode,
    grant_effect_templates: GrantEffectTemplates,
    trusted_mechanics: frozenset[str],
    capability_registry: CapabilityRegistry,
    capability_profile: str,
) -> tuple[OracleNode, str] | None:
    compiled_text = _compile_token_ability_text(
        quoted, type_line=token_type_line
    )
    residuals: list[Any] = []
    token_types = tuple(sorted(type_parts(token_type_line)[0]))
    effect_template, trigger_effect_template = grant_effect_templates(
        True, token_types, None
    )
    node = compile_inner(
        node_id=node_id,
        line=compiled_text,
        material_line=compiled_text,
        span=span,
        card_name="Created Token",
        type_line=token_type_line,
        keywords=token_keywords,
        trusted_mechanics=trusted_mechanics,
        capability_registry=capability_registry,
        capability_profile=capability_profile,
        residuals=residuals,
        effect_template=effect_template,
        trigger_effect_template=trigger_effect_template,
    )
    if node is None:
        node = trigger_node(
            node_id=node_id,
            line=compiled_text,
            material_line=compiled_text,
            span=span,
            card_name="Created Token",
            trusted_mechanics=trusted_mechanics,
            capability_registry=capability_registry,
            capability_profile=capability_profile,
            residuals=residuals,
            effect_template=trigger_effect_template,
        )
    if node is None:
        static_residuals: list[Any] = []
        node = closed_static_or_replacement_node(
            node_id=node_id,
            line=quoted,
            material_line=compiled_text,
            span=span,
            source_name="Created Token",
            card_types=frozenset(token_types),
            permanent_card_types=_PERMANENT_CARD_TYPES,
            source_is_class=False,
            capability_registry=capability_registry,
            capability_profile=capability_profile,
            residuals=static_residuals,
        )
        residuals.extend(static_residuals)
    if node is None or not node.exact or residuals:
        return None
    return node, compiled_text


def _token_fragments_and_children(
    *,
    quoted_abilities: tuple[str, ...],
    characteristics: Mapping[str, Any],
    oracle_id: str,
    face_id: str,
    node_id: str,
    span: SourceSpan,
    compile_inner: CompileInner,
    trigger_node: TriggerNode,
    grant_effect_templates: GrantEffectTemplates,
    trusted_mechanics: frozenset[str],
    capability_registry: CapabilityRegistry,
    capability_profile: str,
) -> tuple[list[Mapping[str, Any]], list[OracleNode], list[str]] | None:
    from .attached_granted_ability_nodes import attached_granted_ability_plan

    token_type_line = str(characteristics.get("type_line") or "")
    if not token_type_line.startswith("Token "):
        return None
    token_keywords = tuple(
        str(value) for value in characteristics.get("keywords", ())
    )
    fragments: list[Mapping[str, Any]] = []
    children: list[OracleNode] = []
    direct_mechanics: list[str] = []
    for slot, quoted in enumerate(quoted_abilities, 1):
        child_id = f"{node_id}:token-ability:{slot}"
        compiled = _inner_node(
            node_id=child_id,
            quoted=quoted,
            token_type_line=token_type_line,
            token_keywords=token_keywords,
            span=span,
            compile_inner=compile_inner,
            trigger_node=trigger_node,
            grant_effect_templates=grant_effect_templates,
            trusted_mechanics=trusted_mechanics,
            capability_registry=capability_registry,
            capability_profile=capability_profile,
        )
        if compiled is None:
            return None
        inner, compiled_text = compiled
        if inner.kind == "static_ability":
            static_fragments = fragments_from_descriptors(inner.handlers)
            if not static_fragments:
                return None
            fragments.extend(
                ability_fragment_to_dict(fragment)
                for fragment in static_fragments
            )
            direct_mechanics.extend(inner.mechanics)
            continue
        plan = attached_granted_ability_plan(
            node=inner,
            quoted_text=compiled_text,
            oracle_id=oracle_id,
            face_id=face_id,
            source_line=span.line,
            card_name="Created Token",
            keywords=token_keywords,
            node_id=child_id,
            display_text=quoted,
        )
        if plan is None:
            return None
        fragments.append(plan.fragment)
        children.append(
            replace(
                inner,
                node_id=child_id,
                kind=plan.node_kind,
                text=quoted,
                span=span,
                residual_ids=(),
            )
        )
    return fragments, children, direct_mechanics


def _decorate_outer_nodes(
    *,
    outer_nodes: tuple[OracleNode, ...],
    fragments: Sequence[Mapping[str, Any]],
    direct_mechanics: Sequence[str],
    capability_registry: CapabilityRegistry,
    capability_profile: str,
) -> tuple[OracleNode, ...] | None:
    mechanics = tuple(
        dict.fromkeys(
            (
                *(mechanic for node in outer_nodes for mechanic in node.mechanics),
                TYPED_TOKEN_ABILITY_MECHANIC_ID,
                *direct_mechanics,
            )
        )
    )
    result: list[OracleNode] = []
    changed = 0
    for outer in outer_nodes:
        effects, count = _with_token_fragments(
            outer.effects, fragments=fragments
        )
        changed += count
        if not count:
            result.append(outer)
            continue
        dependencies = capability_dependencies_for_node(
            effects=effects,
            target_schema=outer.target_schema,
            mechanic_ids=mechanics,
            cost_schema=outer.cost,
        )
        if not dependencies:
            return None
        # Decorating the created token refines the body, not the subscription.
        # Retain explicit container/event owners already proven by the shell.
        dependencies = tuple(sorted(set(dependencies) | set(outer.capability_dependencies)))
        closure = capability_registry.closure(
            dependencies, profile=capability_profile
        )
        if not closure.trusted or closure.blockers:
            return None
        result.append(
            replace(
                outer,
                effects=effects,
                mechanics=mechanics,
                capability_dependencies=dependencies,
                capability_closure=closure.reachable,
                capability_profile=closure.profile,
                capability_fingerprint=closure.fingerprint,
            )
        )
    return tuple(result) if changed == 1 else None


def typed_token_ability_nodes(
    *,
    record: Any,
    face_id: str,
    node_id: str,
    line: str,
    material_line: str,
    span: SourceSpan,
    source_name: str,
    effect_template: EffectTemplate,
    keywords: Sequence[str],
    printed_card_types: tuple[str, ...],
    source_attachment_relation: Any,
    trusted_mechanics: frozenset[str],
    capability_registry: CapabilityRegistry | None,
    capability_profile: str,
    compile_inner: CompileInner,
    trigger_node: TriggerNode,
    grant_effect_templates: GrantEffectTemplates,
) -> tuple[OracleNode, ...] | None:
    """Compile one closed token shell plus independently exact abilities."""

    parsed = _token_ability_shell(material_line)
    if parsed is None or capability_registry is None:
        return None
    shell, quoted_abilities = parsed
    outer_nodes = _outer_nodes(
        node_id=node_id,
        line=line,
        shell=shell,
        span=span,
        source_name=source_name,
        printed_card_types=printed_card_types,
        keywords=keywords,
        source_attachment_relation=source_attachment_relation,
        effect_template=effect_template,
        compile_inner=compile_inner,
        trigger_node=trigger_node,
        grant_effect_templates=grant_effect_templates,
        trusted_mechanics=trusted_mechanics,
        capability_registry=capability_registry,
        capability_profile=capability_profile,
    )
    if outer_nodes is None:
        return None
    created = tuple(
        effect
        for outer in outer_nodes
        for effect in _created_token_effects(outer.effects)
    )
    if len(created) != 1 or not isinstance(
        created[0].get("characteristics"), Mapping
    ):
        return None
    compiled = _token_fragments_and_children(
        quoted_abilities=quoted_abilities,
        characteristics=created[0]["characteristics"],
        oracle_id=record.oracle_id,
        face_id=face_id,
        node_id=node_id,
        span=span,
        compile_inner=compile_inner,
        trigger_node=trigger_node,
        grant_effect_templates=grant_effect_templates,
        trusted_mechanics=trusted_mechanics,
        capability_registry=capability_registry,
        capability_profile=capability_profile,
    )
    if compiled is None:
        return None
    fragments, children, direct_mechanics = compiled
    decorated = _decorate_outer_nodes(
        outer_nodes=outer_nodes,
        fragments=fragments,
        direct_mechanics=direct_mechanics,
        capability_registry=capability_registry,
        capability_profile=capability_profile,
    )
    return (*decorated, *children) if decorated is not None else None


__all__ = ["typed_token_ability_nodes"]
