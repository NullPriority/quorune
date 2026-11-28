from __future__ import annotations

"""Compile a fixed-duration quoted ability through its independent child."""

from dataclasses import replace
from . import regex_working_set as re

from ..ability_fragments import CURRENT_ABILITY_FRAGMENT_COVERAGE
from ..rules.temporary_ability_grant import (
    TEMPORARY_COMBAT_DRAW_UNBLOCKABLE_CAPABILITY, TEMPORARY_COMBAT_DRAW_UNBLOCKABLE_MECHANIC,
    TEMPORARY_COMBAT_FIREBENDING_TEMPLATE,
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


_TEMPORARY_CONTROLLED_FIREBENDING = re.compile(
    r'(?P<subject>Each creature you control) gains (?P<keyword>Firebending [1-9][0-9]*) until end of turn\.',
    re.IGNORECASE,
)
_TEMPORARY_DEATH_RETURN = re.compile(
    r'(?P<subject>[^".]+?) gains? (?P<keyword>persist|undying) until end of turn\.?', re.IGNORECASE,
)


def _unquoted_temporary_grant(material_line, line, span, compile_inner):
    firebending = _TEMPORARY_CONTROLLED_FIREBENDING.fullmatch(material_line)
    death_return = _TEMPORARY_DEATH_RETURN.fullmatch(material_line)
    match = firebending or death_return
    if match is None:
        return material_line, span, compile_inner

    from .granted_keyword_event_nodes import _keyword_inner_node

    keyword = match["keyword"]
    if firebending:
        from .keyword_event_effect_nodes import fixed_keyword_event_effect_spec

        offset = line.find(keyword)
        spec = fixed_keyword_event_effect_spec(keyword)
        if offset < 0 or spec is None:
            return material_line, span, compile_inner
        span = SourceSpan(span.start + offset, span.start + offset + len(keyword), span.line)
    else:
        from ..death_return import DeathReturnSpec

        spec = DeathReturnSpec.for_keyword(keyword)
    quoted = f'{match["subject"]} gains "{keyword}" until end of turn.'
    return quoted, span, lambda **values: _keyword_inner_node(spec, **values)


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
_PAID_RECEIVING_GRANT_DAMAGE = re.compile(
    r'\{T\}: (?P<grant>Until end of turn, target creature gains '
    r'"Whenever this creature is dealt damage, you draw that many cards and lose that much life\.") '
    r"That creature['’]s controller may have this artifact deal (?P<amount>[1-9][0-9]*) damage to it\. "
    r"Activate only as a sorcery\.", re.I,
)


def _paid_grant_parts(line, source_name, keywords, effects, mechanics, damage_offer):
    abilities, timing = parse_compiled_activation_line(line, card_name=source_name, keywords=keywords)
    if len(abilities) != 1 or timing is not None or abilities[0].zones != ("battlefield",):
        return None
    ability, = abilities
    if damage_offer is None:
        return (
            "temporary-combat-draw-unblockable-eot-v1", TEMPORARY_COMBAT_DRAW_UNBLOCKABLE_CAPABILITY,
            (*effects, temporary_combat_draw_unblockable_rule()),
            (*mechanics, TEMPORARY_COMBAT_DRAW_UNBLOCKABLE_MECHANIC),
            activated_ability_cost(ability), activated_ability_cost_capabilities(ability),
        )
    if not ability.tap_source or not ability.sorcery_speed:
        return None
    from ..rules.optional_source_damage import (
        OPTIONAL_GRANTED_CREATURE_DAMAGE_CAPABILITY, OPTIONAL_GRANTED_CREATURE_DAMAGE_TEMPLATE,
        OPTIONAL_SOURCE_DAMAGE_MECHANIC, optional_granted_creature_damage,
    )
    return (
        OPTIONAL_GRANTED_CREATURE_DAMAGE_TEMPLATE, OPTIONAL_GRANTED_CREATURE_DAMAGE_CAPABILITY,
        (*effects, optional_granted_creature_damage(int(damage_offer["amount"]))),
        (*mechanics, OPTIONAL_SOURCE_DAMAGE_MECHANIC, "cr-120-damage"),
        activated_ability_cost(ability), activated_ability_cost_capabilities(ability),
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


_PAID_FIREBENDING_GRANT = re.compile(
    r"(?:\{[0-9WUBRGC]+\})+, \{T\}: Target creature you control gains "
    r"(?P<keyword>Firebending [1-9][0-9]*) until end of turn\.",
    re.IGNORECASE,
)


_COMBAT_FIREBENDING_MODIFIER = re.compile(
    r"At the beginning of combat on your turn, up to one target creature gets "
    r"(?P<power>[+-][0-9]+)/(?P<toughness>[+-][0-9]+) and gains "
    r"(?P<keyword>Firebending [1-9][0-9]*) until end of turn\.",
    re.IGNORECASE,
)


_COMBAT_COUNTER_FIREBENDING = re.compile(
    r"At the beginning of combat on your turn, each creature you control with a counter on it "
    r"gains (?P<keyword>Firebending [1-9][0-9]*) until end of turn\.", re.IGNORECASE,
)


def _fixed_firebending_child(*, record, face_id, node_id, line, keyword, span,
                             capability_registry, capability_profile):
    from .granted_keyword_event_nodes import _keyword_inner_node
    from .keyword_event_effect_nodes import fixed_keyword_event_effect_spec

    spec = fixed_keyword_event_effect_spec(keyword)
    offset = line.find(keyword)
    if spec is None or offset < 0:
        return None
    child_span = SourceSpan(span.start + offset, span.start + offset + len(keyword), span.line)
    scratch = []
    inner = _keyword_inner_node(
        spec, node_id=node_id + ":granted", line=keyword, material_line=keyword, span=child_span,
        capability_registry=capability_registry, capability_profile=capability_profile, residuals=scratch,
    )
    if inner is None or not inner.exact or scratch:
        return None
    plan = attached_granted_ability_plan(
        node=inner, quoted_text=keyword, oracle_id=record.oracle_id, face_id=face_id,
        source_line=span.line, card_name="Granted creature", keywords=(),
    )
    if plan is None or plan.node_kind != "granted_triggered_ability":
        return None
    return replace(inner, kind=plan.node_kind, active_zone="battlefield"), plan.fragment


def _temporary_paid_firebending_nodes(*, record, face_id, node_id, line, material_line, span,
                                       source_name, capability_registry, capability_profile, residuals):
    from ..rules.capabilities import capability_dependencies_for_node

    match = _PAID_FIREBENDING_GRANT.fullmatch(material_line)
    if match is None:
        return None
    abilities, timing = parse_compiled_activation_line(line, card_name=source_name, keywords=record.keywords)
    if len(abilities) != 1 or timing is not None or abilities[0].zones != ("battlefield",):
        return None
    child = _fixed_firebending_child(
        record=record, face_id=face_id, node_id=node_id, line=line, keyword=match["keyword"],
        span=span, capability_registry=capability_registry, capability_profile=capability_profile,
    )
    if child is None:
        return None
    inner, fragment = child
    price = activated_ability_cost(abilities[0])
    effects = (_targeted_grant_effect(fragment),)
    schema = direct_permanent_target_spec("target creature you control").to_target_schema()
    mechanics = (TEMPORARY_QUOTED_GRANT_MECHANIC, "cr-611-continuous-effects", "cr-115-targets")
    required = capability_dependencies_for_node(
        effects=effects, target_schema=schema, mechanic_ids=mechanics, cost_schema=price,
    )
    if TEMPORARY_QUOTED_GRANT_CAPABILITY not in required:
        return None
    outer = _temporary_grant_outer_node(
        node_id=node_id, line=line, span=span, paid=True, template="temporary-paid-firebending-eot-v1",
        cost=price, effects=effects, schema=schema, mechanics=mechanics,
        dependencies=tuple(sorted({*required, *inner.capability_dependencies,
                                   *activated_ability_cost_capabilities(abilities[0])})),
        capability_registry=capability_registry, capability_profile=capability_profile, residuals=residuals,
    )
    return outer, inner


def _combat_firebending_nodes(*, record, face_id, node_id, line, material_line, span,
                              capability_registry, capability_profile, residuals):
    counter_grant = _COMBAT_COUNTER_FIREBENDING.fullmatch(material_line)
    match = counter_grant or _COMBAT_FIREBENDING_MODIFIER.fullmatch(material_line)
    if match is None:
        return None

    from ..rules.capabilities import capability_dependencies_for_node
    from .fixed_counter_trigger_nodes import fixed_counter_trigger_binding

    trigger = fixed_counter_trigger_binding(material_line)
    if trigger is None or trigger.variant != "combat on your turn":
        return None
    child = _fixed_firebending_child(
        record=record, face_id=face_id, node_id=node_id, line=line, keyword=match["keyword"],
        span=span, capability_registry=capability_registry, capability_profile=capability_profile,
    )
    if child is None:
        return None
    inner, fragment = child
    mechanics = (TEMPORARY_QUOTED_GRANT_MECHANIC, "cr-611-continuous-effects")
    if counter_grant is not None:
        from ..rules.temporary_ability_grant import (
            COMBAT_CREATURE_COUNTER_FIREBENDING_TEMPLATE, combat_creature_counter_firebending_query,
        )

        effect = {
            "op": "apply_source_characteristics_until_end_of_turn", "schema_version": 8,
            "predicate": combat_creature_counter_firebending_query().to_dict(), "ability_fragments": [dict(fragment)],
        }
        schema, template = None, COMBAT_CREATURE_COUNTER_FIREBENDING_TEMPLATE
    else:
        effect = {
            "op": "apply_source_characteristics_until_end_of_turn", "schema_version": 9,
            "card": "$target.0", "power": int(match["power"]), "toughness": int(match["toughness"]),
            "ability_fragments": [dict(fragment)],
        }
        schema = direct_permanent_target_spec("target creature").to_target_schema()
        del schema["count"]
        schema["up_to"] = 1
        template = TEMPORARY_COMBAT_FIREBENDING_TEMPLATE
        mechanics += ("cr-115-targets",)
    required = capability_dependencies_for_node(effects=(effect,), target_schema=schema, mechanic_ids=mechanics)
    if TEMPORARY_QUOTED_GRANT_CAPABILITY not in required:
        return None
    if counter_grant is not None:
        from ..object_predicate import object_query_capabilities

        required = (*required, *object_query_capabilities(effect["predicate"]))
    outer = _temporary_grant_outer_node(
        node_id=node_id, line=line, span=span, paid=False, template=template,
        cost=None, effects=(effect,), schema=schema, mechanics=mechanics,
        dependencies=tuple(sorted({*required, *inner.capability_dependencies})),
        capability_registry=capability_registry, capability_profile=capability_profile, residuals=residuals,
    )
    return (replace(outer, kind="triggered_ability", active_zone=trigger.active_zone,
                    event=trigger.event.value, event_condition=trigger.event_condition), inner)


def _temporary_firebending_nodes(*, source_name, **kwargs):
    paid = _temporary_paid_firebending_nodes(source_name=source_name, **kwargs)
    return paid if paid is not None else _combat_firebending_nodes(**kwargs)


def _temporary_quoted_grant_subject(material_line, source_name):
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
    return quoted, stats, target, query


def temporary_quoted_ability_nodes(*, record, face_id, node_id, line, material_line,
        span, source_name, printed_card_types, trusted_mechanics, capability_registry,
        capability_profile, residuals, compile_inner, effect_template,
        trigger_effect_template, trigger_node, material_line_for):
    if firebending := _temporary_firebending_nodes(
        record=record, face_id=face_id, node_id=node_id, line=line, material_line=material_line,
        span=span, source_name=source_name, capability_registry=capability_registry,
        capability_profile=capability_profile, residuals=residuals,
    ):
        return firebending
    paid = None
    damage_offer = _PAID_RECEIVING_GRANT_DAMAGE.fullmatch(line) if "artifact" in printed_card_types else None
    if damage_offer is not None:
        material_line = damage_offer["grant"]
    life_loss = _LIFE_LOSS_SELF_DEATH_GRANT.fullmatch(material_line)
    if life_loss is not None:
        material_line = f'Until end of turn, target creature gains "{life_loss["quoted"]}"'
    if material_line.endswith((" blocked this turn.", " blocked this turn")):
        paid = _PAID_COMBAT_DRAW_UNBLOCKABLE.fullmatch(without_ability_word_label(material_line) or "")
        if paid is not None:
            material_line = paid["grant"]
    material_line, granted_span, inner_compiler = _unquoted_temporary_grant(material_line, line, span, compile_inner)
    parsed = _temporary_quoted_grant_subject(material_line, source_name)
    if parsed is None:
        return None
    quoted, stats, target, query = parsed
    from .granted_death_role_return import compose_granted_death_role_inner, granted_death_role_base_text
    from .granted_death_treasure import compose_granted_death_treasure, granted_death_treasure_base_text

    death_role_base = granted_death_role_base_text(quoted)
    death_treasure_base = granted_death_treasure_base_text(quoted)
    if death_treasure_base is not None and (target is None or stats is None):
        return None
    inner_text = death_role_base or death_treasure_base or quoted
    inner_residuals = []
    inner = inner_compiler(node_id=node_id + ':granted', line=inner_text,
        material_line=material_line_for(inner_text), span=granted_span, card_name='Granted creature',
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
    if life_loss is not None or death_treasure_base is not None:
        inner = _life_loss_self_death_grant_inner(inner, capability_registry=capability_registry,
                                                  capability_profile=capability_profile)
        if inner is None:
            return None
    if death_treasure_base is not None:
        inner = compose_granted_death_treasure(inner, capability_registry=capability_registry,
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
    if death_treasure_base is not None:
        from ..rules.granted_death_treasure import PARENT_TEMPLATE
        template = PARENT_TEMPLATE
        offset = line.find(quoted)
        if offset >= 0:
            child_span = SourceSpan(span.start + offset, span.start + offset + len(quoted), span.line)
    if life_loss is not None:
        composed = _compose_life_loss_self_death_grant(life_loss['loss'], effect, mechanics, line, quoted, span)
        if composed is None:
            return None
        template, effects, mechanics, child_span = composed
    paid_activation = paid is not None or damage_offer is not None
    if paid_activation:
        parts = _paid_grant_parts(line, source_name, record.keywords, effects, mechanics, damage_offer)
        if parts is None:
            return None
        template, owner, effects, mechanics, cost, cost_capabilities = parts
    if damage_offer is not None:
        offset = line.index(quoted)
        child_span = SourceSpan(span.start + offset, span.start + offset + len(quoted), span.line)
    dependencies = capability_dependencies_for_node(effects=effects, target_schema=schema,
        mechanic_ids=mechanics, cost_schema=cost)
    if owner not in dependencies:
        return None
    if not paid_activation and not set(printed_card_types).intersection({'instant', 'sorcery'}):
        return None
    outer = _temporary_grant_outer_node(node_id=node_id, line=line, span=span, paid=paid_activation,
        template=template, cost=cost, effects=effects, schema=schema, mechanics=mechanics,
        dependencies=tuple(sorted({*dependencies, *inner.capability_dependencies, *cost_capabilities})),
        capability_registry=capability_registry, capability_profile=capability_profile, residuals=residuals)
    return (outer, replace(inner, node_id=node_id + ':granted', kind=plan.node_kind,
        text=quoted, span=child_span, active_zone='battlefield', residual_ids=()), *role_children)
