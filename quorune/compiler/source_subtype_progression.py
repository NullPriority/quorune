"""Lower the closed source-subtype progression and its independently compiled child."""

from dataclasses import replace

from ..ability_fragments import CURRENT_ABILITY_FRAGMENT_COVERAGE
from ..rules.source_subtype_progression import progression_dependencies, progression_effect, uses_ring_damage_outcome
from . import regex_working_set as re
from .activated_costs import activated_ability_cost, activated_ability_cost_capabilities, parse_compiled_activation_line
from .activation_mana_costs import fixed_complex_activation_mana_cost
from .attached_granted_ability_nodes import attached_granted_ability_plan
from .dependency_gate import explicit_capabilities_gate
from .ir_model import OracleNode, SourceSpan, append_residual


_SCOUT = re.compile(
    r"\{W/B\}\{W/B\}: If (?P<source>[^,]+) is a Citizen, it becomes a Halfling Scout "
    r"with base power and toughness 2/3 and lifelink\.", re.I,
)
_ROGUE = re.compile(
    r'\{B\}\{B\}\{B\}: If (?P<source>[^,]+) is a Scout, it becomes a Halfling Rogue with "(?P<quoted>[^"]+)"',
    re.I,
)


def _ring_damage_child(record, face_id, node_id, line, span, matched, *, compile_inner, trigger_node,
                       effect_template, trigger_effect_template, material_line_for,
                       trusted_mechanics, capability_registry, capability_profile):
    quote = matched["quoted"]
    offset = line.find(quote)
    if offset < 0:
        return None
    child_span = SourceSpan(span.start + offset, span.start + offset + len(quote), span.line)
    residuals = []
    context = {
        "node_id": node_id + ":granted", "line": quote, "material_line": material_line_for(quote),
        "span": child_span, "card_name": "Granted creature", "trusted_mechanics": trusted_mechanics,
        "capability_registry": capability_registry, "capability_profile": capability_profile,
        "residuals": residuals,
    }
    inner = compile_inner(**context, type_line="Creature — Fixture", keywords=(),
                          effect_template=effect_template, trigger_effect_template=trigger_effect_template)
    if inner is None and not residuals:
        inner = trigger_node(**context, effect_template=trigger_effect_template)
    if inner is None or not inner.exact or residuals or len(inner.effects) != 1 or not uses_ring_damage_outcome(inner.effects[0]):
        return None
    plan = attached_granted_ability_plan(
        node=inner, quoted_text=quote, oracle_id=record.oracle_id, face_id=face_id,
        source_line=span.line, card_name="Granted creature", keywords=(),
    )
    if plan is None or plan.node_kind != "granted_triggered_ability":
        return None
    return plan.fragment, replace(inner, kind=plan.node_kind, active_zone="battlefield")


def source_subtype_progression_nodes(*, record, face_id, node_id, line, material_line, span, source_name,
                                    trusted_mechanics, capability_registry, capability_profile, residuals,
                                    compile_inner, trigger_node, effect_template, trigger_effect_template, material_line_for):
    scout = _SCOUT.fullmatch(material_line)
    rogue = _ROGUE.fullmatch(material_line)
    matched = scout or rogue
    if matched is None or matched["source"].casefold() not in {
        source_name.casefold(), source_name.split(",", 1)[0].casefold(), "this creature",
    }:
        return None
    abilities, timing = parse_compiled_activation_line(line, card_name=source_name, keywords=record.keywords)
    if len(abilities) != 1 or timing is not None or abilities[0].zones != ("battlefield",):
        return None
    ability = fixed_complex_activation_mana_cost(abilities[0])
    if not ability.compiled_cost:
        return None
    children = ()
    fragment = None
    if rogue:
        bound = _ring_damage_child(
            record, face_id, node_id, line, span, rogue, compile_inner=compile_inner, trigger_node=trigger_node,
            effect_template=effect_template, trigger_effect_template=trigger_effect_template, material_line_for=material_line_for,
            trusted_mechanics=trusted_mechanics, capability_registry=capability_registry,
            capability_profile=capability_profile,
        )
        if bound is None:
            return None
        fragment, child = bound
        children = (child,)
    stage = "scout" if scout else "rogue"
    effect = progression_effect(stage, fragment)
    deps = tuple(sorted({*progression_dependencies(effect), *activated_ability_cost_capabilities(ability),
                         *(dep for child in children for dep in child.capability_dependencies)}))
    gate = explicit_capabilities_gate(deps, capability_registry=capability_registry, capability_profile=capability_profile)
    ids = (append_residual(residuals, kind="dependency_contract", text=line, span=span,
        reason="Subtype progression requires its exact characteristic and granted ability owners.",
        blockers=gate.blockers),) if gate.blockers else ()
    outer = OracleNode(
        node_id=node_id, kind="activated_ability", text=line, span=span, active_zone="battlefield", event="activate",
        lowerable=True, exact=not gate.blockers, template_id=f"source-subtype-{stage}-progression-v1",
        cost=activated_ability_cost(ability), effects=(effect,), mechanics=("cr-611-continuous-effects",),
        residual_ids=ids, runtime_coverage=(CURRENT_ABILITY_FRAGMENT_COVERAGE,),
        capability_dependencies=gate.capabilities,
        capability_closure=gate.closure.reachable if gate.closure else (),
        capability_profile=gate.closure.profile if gate.closure else None,
        capability_fingerprint=gate.closure.fingerprint if gate.closure else None,
    )
    return (outer, *children)
