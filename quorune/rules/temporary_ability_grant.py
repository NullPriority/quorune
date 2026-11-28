from __future__ import annotations

"""Closed temporary ability bundles over a revalidated creature incarnation."""

from typing import Mapping
from ..ability_fragments import ability_fragment_from_dict,ability_fragment_to_dict
from ..keyword_abilities import FIXED_CHARACTERISTIC_KEYWORD_CAPABILITIES
from ..replacement.immutable import FrozenMap
from .combat_prevention_draw import CAPABILITY as COMBAT_PREVENTION_DRAW_CAPABILITY, FRAGMENT_KIND as COMBAT_PREVENTION_DRAW_FRAGMENT

BACKUP_CAPABILITY='trigger.keyword.backup'
BACKUP_COVERAGE='printed_backup_ability_bundle'
BACKUP_CONTEXT='backup_source_printed'
OPERATION='apply_source_characteristics_until_end_of_turn'
TEMPORARY_QUOTED_GRANT_CAPABILITY = 'continuous.ability.temporary_quoted_grant'
LIFE_LOSS_SELF_DEATH_GRANT_TEMPLATE = 'fixed-life-loss-self-death-grant-v1'
TEMPORARY_QUOTED_GRANT_MECHANIC = 'temporary-quoted-ability-grant'
TEMPORARY_COMBAT_FIREBENDING_TEMPLATE = "temporary-combat-firebending-eot-v1"
_OPTIONAL_CREATURE_TARGET = FrozenMap({
    "zones": ["battlefield"], "categories": ["permanent"], "types_any": ["creature"], "up_to": 1,
})
_CONTROLLER_COMBAT = FrozenMap({"all": [
    {"field": "player", "op": "eq", "value": "$source.controller"},
    {"field": "step", "op": "eq", "value": "beginning_combat"},
]})
TEMPORARY_EACH_COLOR_PROTECTION_CAPABILITY = 'continuous.ability.temporary_each_color_protection'
TEMPORARY_LAND_MANA_GRANT_CAPABILITY = "continuous.ability.temporary_land_mana_grant"
_LAND_TARGET = FrozenMap({"zones": ["battlefield"], "categories": ["permanent"], "types_any": ["land"], "count": 1})
TEMPORARY_COMBAT_DRAW_UNBLOCKABLE_CAPABILITY = "continuous.ability.temporary_quoted_combat_draw_unblockable"
TEMPORARY_COMBAT_DRAW_UNBLOCKABLE_MECHANIC = "temporary-quoted-combat-draw-unblockable"
COMBAT_DRAW_UNBLOCKABLE_TARGET = {
    "zones": ["battlefield"], "categories": ["permanent"], "types_any": ["creature"], "count": 1,
}
_COMBAT_DRAW_UNBLOCKABLE_COST = {
    "text": "{3}, {T}",
    "mana": {"B": 0, "C": 0, "G": 0, "GENERIC": 3, "R": 0, "U": 0, "W": 0},
    "complex_symbols": [], "tap_source": True, "untap_source": False, "discard_source": False,
    "sacrifice_source": False, "exile_source": False, "life_payment": 0, "energy_payment": 0,
    "loyalty_delta": None, "source_counter_removal_cost": None, "choices": [], "uncompiled_costs": [],
}


def temporary_combat_draw_unblockable_rule():
    return {"op": "grant_declaration_restriction_until_end_of_turn", "card": "$target.0",
            "restriction": "unblockable"}


def _temporary_combat_draw_unblockable_dependencies(effects, target_schema, cost_schema):
    from ..scalar_effect_amount_model import SOURCE_COUNTER_KIND_COUNT_CAPABILITY

    if (len(effects) != 2 or target_schema != COMBAT_DRAW_UNBLOCKABLE_TARGET
            or type(target_schema["count"]) is not int or effects[0].get("schema_version") != 7
            or cost_schema != _COMBAT_DRAW_UNBLOCKABLE_COST
            or any(type(cost_schema[field]) is not type(value)
                   for field, value in _COMBAT_DRAW_UNBLOCKABLE_COST.items()
                   if isinstance(value, (bool, int)))
            or any(type(amount) is not int for amount in cost_schema["mana"].values())
            or effects[1] != temporary_combat_draw_unblockable_rule()):
        return ()
    ordinary = temporary_quoted_grant_node_capabilities(
        effects=effects[:1], target_schema=target_schema, mechanic_ids={TEMPORARY_QUOTED_GRANT_MECHANIC},
    )
    if not ordinary:
        return ()
    fragment = effects[0]["ability_fragments"][0]
    if (fragment["kind"] != "granted_triggered" or fragment["value"]["event"] != "damage.dealt.self"):
        return ()
    return (*ordinary, TEMPORARY_COMBAT_DRAW_UNBLOCKABLE_CAPABILITY, SOURCE_COUNTER_KIND_COUNT_CAPABILITY,
            "combat.declaration.typed_components", "continuous.resolution.declaration_rules_until_end_of_turn")


def temporary_combat_draw_unblockable_runtime_dependencies(program):
    if (TEMPORARY_COMBAT_DRAW_UNBLOCKABLE_MECHANIC not in program.coverage
            and TEMPORARY_COMBAT_DRAW_UNBLOCKABLE_CAPABILITY not in program.capability_dependencies):
        return ()
    dependencies = _temporary_combat_draw_unblockable_dependencies(
        program.effects, program.target_schema, program.cost_schema,
    )
    if not dependencies or (program.event, program.active_zone) != ("activate", "battlefield"):
        raise ValueError("A temporary combat draw and unblockable grant requires its exact activation and target")
    return dependencies


def validate_temporary_combat_draw_unblockable_child(activation, lookup):
    """Verify the separately compiled child behind the paid temporary grant."""
    if activation is None or not temporary_combat_draw_unblockable_runtime_dependencies(activation):
        return
    from .source_counter_kind_draw import source_counter_kind_draw_runtime_dependencies

    fragment = activation.effects[0]["ability_fragments"][0]["value"]
    child = lookup(fragment["semantic_key"])
    if (child is None or child.oracle_id != activation.oracle_id or child.ability_id != fragment["ability_id"]
            or "granted_triggered_ability" not in child.coverage):
        raise ValueError("The temporary combat draw requires its independently bound triggered child")
    dependencies = source_counter_kind_draw_runtime_dependencies(child)
    if not dependencies or not set(dependencies) <= set(child.capability_dependencies):
        raise ValueError("The temporary combat draw child requires its exact counter-kind result and authority")


def each_color_protection_fragments():
    from ..ability_fragments import ProtectionQualityKind, ProtectionSpec

    return [ability_fragment_to_dict(ProtectionSpec(ProtectionQualityKind.COLOR, color)) for color in "WUBRG"]


def each_color_protection_query(controller):
    from ..object_predicate import ObjectQuerySpec

    return ObjectQuerySpec(zones=("battlefield",), types_all=("creature",), controller=controller)


def each_color_protection_instruction(*, targeted):
    common = {"op": OPERATION, "ability_fragments": each_color_protection_fragments()}
    if targeted:
        schema = {"zones": ["battlefield"], "categories": ["permanent"], "types_any": ["creature"],
                  "count": 1, "controller_relation": "you"}
        return ({**common, "schema_version": 7, "card": "$target.0", "keywords": [],
                 "exclude_source": False, "printed_only": False}, schema)
    return ({**common, "schema_version": 8, "predicate": each_color_protection_query("$controller").to_dict()}, None)


def is_each_color_protection_bundle(fragments):
    return tuple(FrozenMap(fragment) for fragment in fragments) == tuple(
        FrozenMap(fragment) for fragment in each_color_protection_fragments())


def _each_color_protection_capabilities(grant, target_schema, mechanics):
    if "protection" not in mechanics or type(grant.get("schema_version")) is not int:
        return ()
    targeted = grant["schema_version"] == 7
    instruction, schema = each_color_protection_instruction(targeted=targeted)
    if FrozenMap(grant) != FrozenMap(instruction):
        return ()
    if targeted:
        if (not isinstance(target_schema, Mapping) or type(target_schema.get("count")) is not int
                or FrozenMap(target_schema) != FrozenMap(schema)):
            return ()
    elif target_schema is not None:
        return ()
    return (TEMPORARY_QUOTED_GRANT_CAPABILITY, TEMPORARY_EACH_COLOR_PROTECTION_CAPABILITY,
            "protection.typed.debt", "continuous.resolution.fixed_characteristics_until_end_of_turn",
            *(("target.revalidate_resolution",) if targeted else ()))


def temporary_quoted_grant_node_capabilities(*, effects, target_schema, mechanic_ids, cost_schema=None):
    from .permanent_predicate_capability_shapes import direct_permanent_target_schema_is_closed, direct_target_predicate_capabilities
    if TEMPORARY_COMBAT_DRAW_UNBLOCKABLE_MECHANIC in mechanic_ids:
        return _temporary_combat_draw_unblockable_dependencies(effects, target_schema, cost_schema)
    if len(effects) == 2 and effects[0].get("op") == "lose_life" and cost_schema is None:
        from .fixed_effect_clause_shapes import FIXED_EFFECT_CLAUSE_SEQUENCE_MECHANIC, FIXED_EFFECT_CLAUSE_SEQUENCE_CAPABILITY

        if FIXED_EFFECT_CLAUSE_SEQUENCE_MECHANIC in mechanic_ids:
            required = life_loss_temporary_trigger_capabilities(effects, target_schema, mechanic_ids)
            return (FIXED_EFFECT_CLAUSE_SEQUENCE_CAPABILITY, *required) if required else ()
    if TEMPORARY_QUOTED_GRANT_MECHANIC not in mechanic_ids or len(effects) != 1:
        return ()
    grant = effects[0]
    from .blocked_damage_cast import grant_dependencies, represents_grant
    from .commander_lifelink import commander_lifelink_node_capabilities

    if represents_grant(grant):
        return grant_dependencies(effects, target_schema) if cost_schema is None else ()

    if lifelink := commander_lifelink_node_capabilities(grant, target_schema, mechanic_ids, cost_schema):
        return lifelink
    if protection := _each_color_protection_capabilities(grant, target_schema, mechanic_ids):
        return protection
    if isinstance(target_schema, Mapping) and FrozenMap(target_schema) == _LAND_TARGET:
        return _temporary_land_mana_grant_capabilities(grant)
    optional_creature = False
    if grant.get('schema_version') == 9:
        optional_creature = FrozenMap(target_schema or {}) == _OPTIONAL_CREATURE_TARGET
        if optional_creature:
            if type(target_schema["up_to"]) is not int or cost_schema is not None:
                return ()
            target_schema = COMBAT_DRAW_UNBLOCKABLE_TARGET
        if (set(grant) != {'op', 'schema_version', 'card', 'power', 'toughness', 'ability_fragments'}
                or type(grant['power']) is not int or type(grant['toughness']) is not int):
            return ()
        grant = {'op': grant['op'], 'schema_version': 7, 'card': grant['card'], 'keywords': [],
            'ability_fragments': grant['ability_fragments'], 'exclude_source': False, 'printed_only': False}
    if grant.get('schema_version') == 8:
        from ..object_predicate import ObjectQuerySpec
        from ..compiler.fixed_resolution_characteristic_queries import fixed_resolution_characteristic_query_is_closed
        if set(grant) != {'op', 'schema_version', 'predicate', 'ability_fragments'} or target_schema is not None:
            return ()
        try:
            query = ObjectQuerySpec.from_dict(grant['predicate'])
        except (TypeError, ValueError):
            return ()
        if (query.to_dict() != grant['predicate'] or query.keywords_none or query.colors_none
                or not fixed_resolution_characteristic_query_is_closed(query, target_schema=None)):
            return ()
        return _temporary_query_grant_capabilities(grant, query)
    if not direct_permanent_target_schema_is_closed(target_schema) or target_schema.get('types_any') != ['creature']:
        return ()
    try:
        keywords, fragments = decode_temporary_ability_grant(grant)
    except (TypeError, ValueError, KeyError):
        return ()
    if (grant['card'] != '$target.0' or grant['printed_only'] or grant['exclude_source']
            or keywords or len(fragments) != 1):
        return ()
    if optional_creature and (fragments[0]["kind"] != "granted_triggered"
                              or fragments[0]["value"]["event"] != "creature.attacks"):
        return ()
    if fragments[0]["kind"] == COMBAT_PREVENTION_DRAW_FRAGMENT:
        if target_schema != {"zones": ["battlefield"], "categories": ["permanent"], "types_any": ["creature"], "count": 1}:
            return ()
        return (COMBAT_PREVENTION_DRAW_CAPABILITY, TEMPORARY_QUOTED_GRANT_CAPABILITY, "target.revalidate_resolution")
    if fragments[0]["kind"] not in {"granted_activated", "granted_triggered"}:
        return ()
    return (TEMPORARY_QUOTED_GRANT_CAPABILITY, 'target.revalidate_resolution',
        *direct_target_predicate_capabilities(target_schema))


COMBAT_CREATURE_COUNTER_FIREBENDING_TEMPLATE = "temporary-combat-controlled-countered-firebending-v1"


def combat_creature_counter_firebending_query():
    from ..object_predicate import ObjectQuerySpec, PermanentStatePredicateSpec

    return ObjectQuerySpec(zones=("battlefield",), controller="$controller", types_all=("creature",),
                           state_predicate=PermanentStatePredicateSpec(minimum_total_counter_count=1))


def temporary_combat_firebending_runtime_dependencies(program):
    """Check the exact combat grant on the materialized program."""
    if program.provenance.get("template_id") == COMBAT_CREATURE_COUNTER_FIREBENDING_TEMPLATE:
        if ((program.event, program.active_zone) != ("step.begin", "battlefield")
                or FrozenMap(program.event_condition or {}) != _CONTROLLER_COMBAT
                or program.handlers or program.cost_schema is not None or program.target_schema is not None
                or len(program.effects) != 1
                or type(program.effects[0].get("schema_version")) is not int
                or program.effects[0]["schema_version"] != 8
                or FrozenMap(program.effects[0].get("predicate") or {}) != FrozenMap(combat_creature_counter_firebending_query().to_dict())):
            raise ValueError("Combat Firebending for creatures with counters requires its exact untargeted combat snapshot")
        dependencies = temporary_quoted_grant_node_capabilities(
            effects=program.effects, target_schema=None, mechanic_ids=program.coverage,
        )
        fragments = program.effects[0].get("ability_fragments", ())
        if (not dependencies or len(fragments) != 1 or fragments[0]["kind"] != "granted_triggered"
                or fragments[0]["value"]["event"] != "creature.attacks"):
            raise ValueError("Combat Firebending for creatures with counters requires the exact attack grant")
        return (*dependencies, "trigger.keyword.firebending.fixed")
    optional_bundle = (
        isinstance(program.target_schema, Mapping)
        and FrozenMap(program.target_schema) == _OPTIONAL_CREATURE_TARGET
        and any(effect.get("op") == OPERATION and effect.get("schema_version") == 9 for effect in program.effects)
    )
    if not optional_bundle and program.provenance.get("template_id") != TEMPORARY_COMBAT_FIREBENDING_TEMPLATE:
        return ()
    if (not optional_bundle or (program.event, program.active_zone) != ("step.begin", "battlefield")
            or FrozenMap(program.event_condition or {}) != _CONTROLLER_COMBAT or program.handlers):
        raise ValueError("Temporary combat Firebending requires the controller's beginning of combat and zero or one creature")
    dependencies = temporary_quoted_grant_node_capabilities(
        effects=program.effects, target_schema=program.target_schema, mechanic_ids=program.coverage,
        cost_schema=program.cost_schema,
    )
    if TEMPORARY_QUOTED_GRANT_CAPABILITY not in dependencies:
        raise ValueError("Temporary combat Firebending requires the exact coupled characteristic and attack grant")
    return (*dependencies, "trigger.keyword.firebending.fixed")


def life_loss_temporary_trigger_capabilities(effects, target_schema, mechanics):
    """Close an unconditional printed loss followed by one creature-trigger grant."""
    from .fixed_controller_effect_shapes import fixed_life_node_capabilities

    if len(effects) != 2 or FrozenMap(target_schema or {}) != FrozenMap(COMBAT_DRAW_UNBLOCKABLE_TARGET):
        return ()
    loss, grant = effects
    if (set(loss) != {"op", "player", "amount"} or loss["op"] != "lose_life"
            or loss["player"] != "$controller" or type(loss["amount"]) is not int or loss["amount"] <= 0
            or type(grant.get("schema_version")) is not int or grant["schema_version"] != 7):
        return ()
    grant_deps = temporary_quoted_grant_node_capabilities(
        effects=(grant,), target_schema=target_schema, mechanic_ids=mechanics,
    )
    if not grant_deps or grant["ability_fragments"][0]["kind"] != "granted_triggered":
        return ()
    loss_deps = fixed_life_node_capabilities(effects=(loss,), target_schema=None, mechanic_ids=mechanics)
    return (*loss_deps, *grant_deps) if loss_deps else ()


def validate_life_loss_self_death_grant_child(program, lookup):
    if program.provenance.get("template_id") != LIFE_LOSS_SELF_DEATH_GRANT_TEMPLATE:
        return
    from .granted_death_role_return import granted_self_death_return_dependencies

    required = life_loss_temporary_trigger_capabilities(program.effects, program.target_schema, program.coverage)
    if (not required or (program.event, program.active_zone) != ("resolve", "stack")
            or program.cost_schema is not None or program.handlers
            or not set(required) <= set(program.capability_dependencies)):
        raise ValueError("The life loss and death grant require their complete targeted spell sequence")
    fragment = program.effects[1]["ability_fragments"][0]["value"]
    child = lookup(fragment["semantic_key"])
    if (child is None or child.oracle_id != program.oracle_id or child.ability_id != fragment["ability_id"]
            or child.event != fragment["event"] or "granted_triggered_ability" not in child.coverage):
        raise ValueError("The life loss and death grant require their independently bound trigger")
    child_deps = granted_self_death_return_dependencies(child, child.effects)
    if not child_deps or not set(child_deps) <= set(child.capability_dependencies):
        raise ValueError("The granted death trigger requires its exact tapped owner return")


def _temporary_land_mana_grant_capabilities(grant):
    from ..granted_activation_model import GrantedActivatedAbilitySpec

    try:
        keywords, fragments = decode_temporary_ability_grant(grant)
    except (TypeError, ValueError, KeyError):
        return ()
    if (grant["card"] != "$target.0" or grant["printed_only"] or grant["exclude_source"]
            or keywords or len(fragments) != 1 or fragments[0]["kind"] != "granted_activated"):
        return ()
    spec = ability_fragment_from_dict(fragments[0])
    if (not isinstance(spec, GrantedActivatedAbilitySpec) or spec.schema_version != 1
            or spec.cost_text != "{T}" or not spec.tap_source or not spec.mana_ability
            or spec.sorcery_speed or any(spec.mana_bundle.values()) or len(spec.fixed_mana_outputs) != 1):
        return ()
    return (TEMPORARY_LAND_MANA_GRANT_CAPABILITY, TEMPORARY_QUOTED_GRANT_CAPABILITY,
            "mana.activated.fixed_output", "target.revalidate_resolution")


def _temporary_query_grant_capabilities(grant, query):
    try:
        _, fragments = decode_temporary_ability_grant({
            'op': grant['op'], 'schema_version': 7, 'card': '$target.0', 'keywords': [],
            'ability_fragments': grant['ability_fragments'], 'exclude_source': False, 'printed_only': False})
    except (TypeError, ValueError, KeyError):
        return ()
    if len(fragments) != 1 or fragments[0]['kind'] not in {'granted_activated', 'granted_triggered'}:
        return ()
    return (TEMPORARY_QUOTED_GRANT_CAPABILITY,
        *(['state_query.permanent.public_state_predicate'] if query.state_predicate is not None else []))


def combat_prevention_draw_runtime_dependencies(program):
    from .effect_shape_walkers import nested_effects

    marked = [effect for effect in nested_effects(program.effects)
              if any(isinstance(fragment, Mapping) and fragment.get("kind") == COMBAT_PREVENTION_DRAW_FRAGMENT
                     for fragment in effect.get("ability_fragments", ()))]
    if not marked:
        return ()
    if ((program.event, program.active_zone) != ("activate", "battlefield") or len(marked) != 1
            or tuple(program.effects) != tuple(marked) or program.cost_schema is None):
        raise ValueError("Combat prevention draws require the one direct targeted battlefield activation")
    required = temporary_quoted_grant_node_capabilities(
        effects=marked, target_schema=program.target_schema, mechanic_ids={TEMPORARY_QUOTED_GRANT_MECHANIC},
    )
    if COMBAT_PREVENTION_DRAW_CAPABILITY not in required:
        raise ValueError("Combat prevention draws require the exact bound creature grant")
    return required


def decode_temporary_ability_grant(effect):
    fields={'op','schema_version','card','keywords','ability_fragments','exclude_source','printed_only'}
    if not isinstance(effect,Mapping) or set(effect)!=fields:raise ValueError('Temporary ability bundle fields are malformed')
    if effect['op']!=OPERATION or type(effect['schema_version']) is not int or effect['schema_version']!=7:raise ValueError('Temporary ability bundle version is unsupported')
    if type(effect['card']) is not str or not effect['card']:raise ValueError('Temporary ability bundle requires one creature reference')
    if type(effect['exclude_source']) is not bool or type(effect['printed_only']) is not bool:raise ValueError('Temporary ability bundle conditions must be Boolean')
    keywords=effect['keywords'];raw=effect['ability_fragments']
    if not isinstance(keywords,(list,tuple)) or any(type(k) is not str or k not in FIXED_CHARACTERISTIC_KEYWORD_CAPABILITIES for k in keywords) or len(set(keywords))!=len(keywords):raise ValueError('Temporary ability bundle keywords are unsupported')
    if not isinstance(raw,(list,tuple)) or len(raw)>12:raise ValueError('Temporary ability bundle fragments are malformed')
    allowed={'granted_activated','granted_triggered','declaration_restriction','declaration_requirement','protection','ward','toxic','toughness_assignment_rule','defender_attack_permission','as_unblocked_assignment',COMBAT_PREVENTION_DRAW_FRAGMENT}
    fragments=[]
    for value in raw:
        if not isinstance(value,Mapping) or value.get('kind') not in allowed:raise ValueError('Temporary ability bundle fragment kind is unsupported')
        fragments.append(ability_fragment_to_dict(ability_fragment_from_dict(value)))
    if any(fragment["kind"] == COMBAT_PREVENTION_DRAW_FRAGMENT for fragment in fragments) and (
        len(fragments) != 1 or keywords or effect["exclude_source"] or effect["printed_only"]
        or fragments[0]["value"] != {"schema_version": 1}
    ):
        raise ValueError("Combat prevention and draw requires its own exact targeted grant")
    # Separate granted triggered abilities retain rules-significant multiplicity.
    return tuple(keywords),tuple(fragments)


def backup_node_capabilities(*,effects,target_schema,mechanic_ids,cost_schema=None):
    if 'backup' not in mechanic_ids or cost_schema is not None or len(effects)!=2:return ()
    counter,grant=effects
    if not isinstance(counter,Mapping) or not isinstance(grant,Mapping) or not isinstance(target_schema,Mapping):return ()
    if dict(target_schema or {})!={'zones':['battlefield'],'categories':['permanent'],'types_any':['creature'],'count':1}:return ()
    if dict(counter)!={'op':'place_counters','card':'$target.0','counter':'+1/+1','amount':counter.get('amount'),'source':'$source'} or type(counter.get('amount')) is not int or not 1<=counter['amount']<=10:return ()
    try:keywords,fragments=decode_temporary_ability_grant(grant)
    except (TypeError,ValueError,KeyError):return ()
    if grant['card']!='$target.0' or grant['exclude_source'] is not True or grant['printed_only'] is not True:return ()
    return tuple(sorted({BACKUP_CAPABILITY,'counter.producer.fixed_effect','target.revalidate_resolution',*(cap for k in keywords for cap in FIXED_CHARACTERISTIC_KEYWORD_CAPABILITIES[k])}))
