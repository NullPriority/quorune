from __future__ import annotations

"""Compile a fixed-duration quoted ability through its independent child."""

from dataclasses import replace
from . import regex_working_set as re

from ..ability_fragments import CURRENT_ABILITY_FRAGMENT_COVERAGE
from ..rules.temporary_ability_grant import (
    TEMPORARY_COMBAT_DRAW_UNBLOCKABLE_CAPABILITY, TEMPORARY_COMBAT_DRAW_UNBLOCKABLE_MECHANIC,
    TEMPORARY_QUOTED_GRANT_CAPABILITY, TEMPORARY_QUOTED_GRANT_MECHANIC,
    temporary_combat_draw_unblockable_rule,
)
from .attached_granted_ability_nodes import attached_granted_ability_plan
from .activated_costs import (
    activated_ability_cost, activated_ability_cost_capabilities, parse_compiled_activation_line,
)
from .dependency_gate import explicit_capabilities_gate
from .direct_target import direct_permanent_target_spec
from .ir_model import OracleNode, SourceSpan, append_residual
from .oracle_source_text import without_ability_word_label
from .public_state_queries import fixed_characteristic_battlefield_query_subject
from .fixed_resolution_characteristic_queries import fixed_resolution_characteristic_query_is_closed


_LIFE_LOSS_SELF_DEATH_GRANT = re.compile(
    r'Choose target creature\. (?P<loss>You lose [1-9][0-9]* life\.) '
    r'Until end of turn, that creature gains "(?P<quoted>When this creature dies, '
    r'return it to the battlefield tapped under its owner[\'’]s control\.)"[.]?', re.I,
)


def _life_loss_self_death_grant_inner(inner, *, capability_registry, capability_profile):
    from ..event_card_return import EVENT_BATTLEFIELD_RETURN_CAPABILITY, EVENT_BATTLEFIELD_RETURN_MECHANIC
    from ..rules.granted_death_role_return import GRANTED_SELF_DEATH_RETURN_CAPABILITY, granted_self_death_return_effect

    dependencies = tuple(sorted({*inner.capability_dependencies, GRANTED_SELF_DEATH_RETURN_CAPABILITY}
                                - {EVENT_BATTLEFIELD_RETURN_CAPABILITY}))
    gate = explicit_capabilities_gate(dependencies, capability_registry=capability_registry,
                                      capability_profile=capability_profile)
    if gate.blockers:
        return None
    return replace(inner, event="permanent.graveyard.self", effects=(granted_self_death_return_effect(),),
                   template_id="granted-self-death-tapped-owner-return-v1",
                   mechanics=tuple(value for value in inner.mechanics if value != EVENT_BATTLEFIELD_RETURN_MECHANIC),
                   capability_dependencies=gate.capabilities,
                   capability_closure=gate.closure.reachable if gate.closure else (),
                   capability_profile=gate.closure.profile if gate.closure else None,
                   capability_fingerprint=gate.closure.fingerprint if gate.closure else None)


_LAND_MANA_MODE = re.compile(
    r'Target land gains "(?P<quoted>\{T\}: Add (?:\{[WUBRGC]\})+)" until end of turn\.', re.I,
)


def _targeted_grant_effect(fragment):
    return {'op': 'apply_source_characteristics_until_end_of_turn', 'schema_version': 7,
        'card': '$target.0', 'keywords': [], 'ability_fragments': [dict(fragment)],
        'exclude_source': False, 'printed_only': False}


def temporary_land_mana_mode_nodes(*, body, line, span, oracle_id, face_id, compile_inner,
        effect_template, trusted_mechanics, capability_registry, capability_profile):
    """Lower a modal branch and its independently executable quoted child."""
    match = _LAND_MANA_MODE.fullmatch(body)
    if match is None:
        return None
    quoted = match["quoted"]
    offset = line.find(quoted)
    if offset < 0:
        return None
    child_span = SourceSpan(span.start + offset, span.start + offset + len(quoted), span.line)
    child_id = f"{face_id}:n{span.line}:granted"
    statement = quoted + "."
    scratch = []
    inner = compile_inner(node_id=child_id, line=statement, material_line=statement, span=child_span,
        card_name="Granted land", type_line="Land", keywords=(), trusted_mechanics=trusted_mechanics,
        capability_registry=capability_registry, capability_profile=capability_profile,
        residuals=scratch, effect_template=effect_template)
    if inner is None or not inner.exact or scratch:
        return None
    plan = attached_granted_ability_plan(node=inner, quoted_text=statement, oracle_id=oracle_id,
        face_id=face_id, source_line=span.line, card_name="Granted land", keywords=())
    if plan is None or plan.node_kind != "granted_mana_ability":
        return None
    target = direct_permanent_target_spec("target land").to_target_schema()
    compiled = ("temporary-land-mana-grant-eot-v1", (_targeted_grant_effect(plan.fragment),),
                target, (TEMPORARY_QUOTED_GRANT_MECHANIC, "cr-611-continuous-effects", "cr-115-targets"))
    return compiled, replace(inner, kind=plan.node_kind, text=quoted, active_zone="battlefield")


_PAID_COMBAT_DRAW_UNBLOCKABLE = re.compile(
    r"\{3\}, \{T\}: (?P<grant>Until end of turn, target creature gains \"[^\"\n]+\") "
    r"and it can['’]t be blocked this turn\.?",
    re.IGNORECASE,
)


def _compose_life_loss_self_death_grant(loss_text, grant, mechanics, line, quoted, span):
    from .fixed_effect_clause_sequences import FIXED_EFFECT_CLAUSE_SEQUENCE_MECHANIC
    from .life_templates import fixed_life_effect_template
    from ..rules.temporary_ability_grant import LIFE_LOSS_SELF_DEATH_GRANT_TEMPLATE

    loss = fixed_life_effect_template(loss_text)
    if loss is None:
        return None
    offset = line.find(quoted)
    child_span = SourceSpan(span.start + offset, span.start + offset + len(quoted), span.line) if offset >= 0 else span
    return (LIFE_LOSS_SELF_DEATH_GRANT_TEMPLATE, (loss.effect, grant),
            (*mechanics, *loss.mechanic_ids, FIXED_EFFECT_CLAUSE_SEQUENCE_MECHANIC), child_span)


def _temporary_grant_outer_node(*, node_id, line, span, paid, template, cost, effects,
                                schema, mechanics, dependencies, capability_registry,
                                capability_profile, residuals):
    gate = explicit_capabilities_gate(dependencies,
        capability_registry=capability_registry, capability_profile=capability_profile)
    ids = (append_residual(residuals, kind='dependency_contract', text=line, span=span,
        reason='Temporary quoted grant requires its complete independent ability and duration owners.',
        blockers=gate.blockers),) if gate.blockers else ()
    outer = OracleNode(node_id=node_id, kind='activated_ability' if paid else 'spell_ability',
        text=line, span=span, active_zone='battlefield' if paid else 'stack',
        event='activate' if paid else 'resolve', lowerable=True, exact=not gate.blockers,
        template_id=template,
        cost=cost, effects=effects,
        target_schema=schema, mechanics=mechanics, residual_ids=ids,
        runtime_coverage=(CURRENT_ABILITY_FRAGMENT_COVERAGE,),
        capability_dependencies=gate.capabilities,
        capability_closure=gate.closure.reachable if gate.closure else (),
        capability_profile=gate.closure.profile if gate.closure else None,
        capability_fingerprint=gate.closure.fingerprint if gate.closure else None)
    return outer


def temporary_quoted_ability_nodes(*, record, face_id, node_id, line, material_line,
        span, source_name, printed_card_types, trusted_mechanics, capability_registry,
        capability_profile, residuals, compile_inner, effect_template,
        trigger_effect_template, trigger_node, material_line_for):
    paid = None
    life_loss = _LIFE_LOSS_SELF_DEATH_GRANT.fullmatch(material_line)
    if life_loss is not None:
        material_line = f'Until end of turn, target creature gains "{life_loss["quoted"]}"'
    if material_line.endswith((" blocked this turn.", " blocked this turn")):
        paid = _PAID_COMBAT_DRAW_UNBLOCKABLE.fullmatch(without_ability_word_label(material_line) or "")
        if paid is not None:
            material_line = paid["grant"]
    death_return = re.fullmatch(r'(?P<subject>[^".]+?) gains? (?P<keyword>persist|undying) until end of turn\.?',
        material_line, re.I)
    if death_return is not None:
        material_line = death_return['subject'] + ' gains "' + death_return['keyword'] + '" until end of turn.'
    match = re.fullmatch(
        r'(?:Until end of turn, (?P<prefix>[^".]+?) gains? '
        r'"(?P<prefix_body>[^"\n]+)"|(?P<suffix>[^".]+?) gains? '
        r'"(?P<suffix_body>[^"\n]+)" until end of turn)\.?', material_line, re.I)
    if match is None:
        return None
    subject = match['prefix'] or match['suffix']
    quoted = match['prefix_body'] or match['suffix_body']
    stats = re.fullmatch(r'(?P<subject>.+?) gets? (?P<p>[+-][0-9]+)/(?P<t>[+-][0-9]+) and', subject, re.I)
    if stats is not None:
        subject = stats['subject']
    if re.search(r'\b(?:enchanted|equipped|this Aura|this Equipment)\b', quoted, re.I):
        return None
    if source_name and source_name.casefold() in quoted.casefold():
        return None
    target = direct_permanent_target_spec(subject)
    query = None
    if target is None:
        parsed = fixed_characteristic_battlefield_query_subject(subject)
        if parsed is None:
            return None
        relation, query, exclude_source = parsed
        if relation == 'source_controller':
            query = replace(query, controller='$controller')
        elif relation == 'source_opponents':
            query = replace(query, excluded_controllers=('$controller',))
        elif relation != 'any':
            return None
        if exclude_source:
            query = replace(query, exclude_ref='$source')
        if query.keywords_none or not fixed_resolution_characteristic_query_is_closed(query, target_schema=None):
            return None
    from .granted_death_role_return import compose_granted_death_role_inner, granted_death_role_base_text

    death_role_base = granted_death_role_base_text(quoted)
    inner_text = death_role_base or quoted
    inner_residuals = []
    inner_compiler = compile_inner
    if death_return is not None:
        from ..death_return import DeathReturnSpec
        from .granted_keyword_event_nodes import _keyword_inner_node
        spec = DeathReturnSpec.for_keyword(death_return['keyword'])
        inner_compiler = lambda **values: _keyword_inner_node(spec, **values)
    inner = inner_compiler(node_id=node_id + ':granted', line=inner_text,
        material_line=material_line_for(inner_text), span=span, card_name='Granted creature',
        type_line='Creature — Fixture', keywords=(), trusted_mechanics=trusted_mechanics,
        capability_registry=capability_registry, capability_profile=capability_profile,
        residuals=inner_residuals, effect_template=effect_template,
        trigger_effect_template=trigger_effect_template)
    if inner is None and not inner_residuals:
        inner = trigger_node(node_id=node_id + ':granted', line=inner_text,
            material_line=material_line_for(inner_text), span=span, card_name='Granted creature',
            trusted_mechanics=trusted_mechanics, capability_registry=capability_registry,
            capability_profile=capability_profile, residuals=inner_residuals,
            effect_template=trigger_effect_template)
    if inner is None or not inner.exact or inner_residuals:
        return None
    if life_loss is not None:
        inner = _life_loss_self_death_grant_inner(inner, capability_registry=capability_registry,
                                                  capability_profile=capability_profile)
        if inner is None:
            return None
    role_children = ()
    if death_role_base is not None:
        composed = compose_granted_death_role_inner(inner, record=record, face_id=face_id,
            node_id=node_id, source_text=line, span=span, capability_registry=capability_registry,
            capability_profile=capability_profile)
        if composed is None:
            return None
        inner, role_children = composed
    if paid is not None:
        from ..rules.source_counter_kind_draw import source_counter_kind_draw_effect
        if inner.effects != (source_counter_kind_draw_effect(),):
            return None
    plan = attached_granted_ability_plan(node=inner, quoted_text=quoted,
        oracle_id=record.oracle_id, face_id=face_id, source_line=span.line,
        card_name='Granted creature', keywords=())
    if plan is None or plan.node_kind not in {'granted_activated_ability', 'granted_mana_ability', 'granted_triggered_ability'}:
        return None
    effect = _targeted_grant_effect(plan.fragment)
    if query is not None:
        effect = {'op': effect['op'], 'schema_version': 8, 'predicate': query.to_dict(),
            'ability_fragments': effect['ability_fragments']}
    if stats is not None:
        if target is None:
            return None
        effect = {'op': effect['op'], 'schema_version': 9, 'card': '$target.0',
            'power': int(stats['p']), 'toughness': int(stats['t']),
            'ability_fragments': effect['ability_fragments']}
    from ..rules.capabilities import capability_dependencies_for_node
    mechanics = (TEMPORARY_QUOTED_GRANT_MECHANIC, 'cr-611-continuous-effects',
        *(['cr-115-targets'] if target is not None else []))
    schema = target.to_target_schema() if target is not None else None
    effects = (effect,)
    cost = None
    cost_capabilities = ()
    owner = TEMPORARY_QUOTED_GRANT_CAPABILITY
    template = 'temporary-quoted-ability-eot-v1'
    child_span = inner.span
    if life_loss is not None:
        composed = _compose_life_loss_self_death_grant(life_loss['loss'], effect, mechanics, line, quoted, span)
        if composed is None:
            return None
        template, effects, mechanics, child_span = composed
    if paid is not None:
        template = 'temporary-combat-draw-unblockable-eot-v1'
        abilities, timing = parse_compiled_activation_line(line, card_name=source_name, keywords=record.keywords)
        if len(abilities) != 1 or timing is not None or abilities[0].zones != ("battlefield",):
            return None
        cost = activated_ability_cost(abilities[0])
        cost_capabilities = activated_ability_cost_capabilities(abilities[0])
        effects = (*effects, temporary_combat_draw_unblockable_rule())
        mechanics = (*mechanics, TEMPORARY_COMBAT_DRAW_UNBLOCKABLE_MECHANIC)
        owner = TEMPORARY_COMBAT_DRAW_UNBLOCKABLE_CAPABILITY
    dependencies = capability_dependencies_for_node(effects=effects, target_schema=schema,
        mechanic_ids=mechanics, cost_schema=cost)
    if owner not in dependencies:
        return None
    if paid is None and not set(printed_card_types).intersection({'instant', 'sorcery'}):
        return None
    outer = _temporary_grant_outer_node(node_id=node_id, line=line, span=span, paid=paid is not None,
        template=template, cost=cost, effects=effects, schema=schema, mechanics=mechanics,
        dependencies=tuple(sorted({*dependencies, *inner.capability_dependencies, *cost_capabilities})),
        capability_registry=capability_registry, capability_profile=capability_profile, residuals=residuals)
    return (outer, replace(inner, node_id=node_id + ':granted', kind=plan.node_kind,
        text=quoted, span=child_span, active_zone='battlefield', residual_ids=()), *role_children)
