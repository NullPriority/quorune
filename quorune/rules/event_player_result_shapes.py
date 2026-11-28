from __future__ import annotations

"""Capability closure for fixed untargeted event-addressed draw/life leaves."""

from ..compiler.event_player_results import (EVENT_PLAYER_RESULT_CAPABILITY,EVENT_PLAYER_RESULT_MECHANIC,
    EVENT_PLAYER_DAMAGE_CAPABILITY,EVENT_PLAYER_DAMAGE_MECHANIC)
from .mill_capability_shapes import fixed_mill_node_capabilities,mill_covered_mechanics
from .received_damage_events import is_creature_receiving_event
from ..event_player_result_values import (
    EquippedCombatHandCountSpec, SPELL_CAST_COUNT_HISTORY, SpellCastCountLifeSpec,
    event_result_value_capabilities, uses_equipped_combat_hand_count, uses_spell_cast_count_life,
)
from ..public_history_query_model import public_history_query_value
from .effect_shape_walkers import nested_effects
from .death_controller_opponents import (
    death_opponent_node_capabilities, death_opponents_program_dependencies, uses_death_opponent_results,
)
from ..fractional_quantity_model import HALF_LIFE_CAPABILITY,HALF_LIFE_MECHANIC
from collections.abc import Mapping

from ..scalar_effect_amount_model import (
    SCALAR_AMOUNT_CAPABILITY,
    SCALAR_AMOUNT_KIND,
    ScalarAmountOrigin,
    ScalarEffectAmountSpec,
)
from ..target_controller_information import (
    OCCURRENCE_CONTROLLER_ENTRY_EVENTS, OCCURRENCE_CONTROLLER_GRAVEYARD_EVENTS,
    TARGET_CONTROLLER_INFORMATION_CAPABILITY, is_occurrence_controller_reference,
)


from ..unblocked_attacker_model import (
    UNBLOCKED_ATTACKER_CAPABILITY, UNBLOCKED_ATTACKER_EVENT, UNBLOCKED_DEFENDER_POISON_CAPABILITY,
    UNBLOCKED_DEFENDER_POISON_MECHANIC, intrinsic_unblocked_condition,
    unblocked_defender_poison_effect,
)


def unblocked_defender_poison_node_capabilities(effects, target_schema):
    if (target_schema is not None or tuple(effects) != (unblocked_defender_poison_effect(),)
            or type(effects[0]["amount"]) is not int):
        return ()
    return (UNBLOCKED_DEFENDER_POISON_CAPABILITY, UNBLOCKED_ATTACKER_CAPABILITY,
            "counter.producer.fixed_player_effect")


def unblocked_defender_poison_program_capabilities(program):
    from ..replacement.immutable import FrozenMap

    required = unblocked_defender_poison_node_capabilities(program.effects, program.target_schema)
    if (required and program.event == UNBLOCKED_ATTACKER_EVENT and program.active_zone == "battlefield"
            and FrozenMap(program.event_condition or {}) == FrozenMap(intrinsic_unblocked_condition())):
        return required
    return ()


def _spell_cast_count_life_capabilities():
    return tuple(sorted({
        EVENT_PLAYER_RESULT_CAPABILITY, *SPELL_CAST_COUNT_HISTORY.capability_dependencies,
        "life.change.effect", "trigger.event.normalized_spell_cast", "trigger.placement.apnap",
    }))


def spell_cast_count_life_runtime_dependencies(program):
    effects = (*program.effects, *getattr(program, "handlers", ()))
    if not any(uses_spell_cast_count_life(effect) for effect in nested_effects(effects)):
        return ()
    if (
        (program.event, program.active_zone) != ("spell.cast", "battlefield")
        or program.target_schema is not None or getattr(program, "cost_schema", None) is not None
        or len(program.effects) != 1 or getattr(program, "handlers", ())
    ):
        raise ValueError("Spell-count life requires the complete battlefield cast trigger")
    spec = SpellCastCountLifeSpec.from_effect(program.effects[0])
    if program.event_condition != spec.event_condition:
        raise ValueError("Spell-count life requires its matching caster relation")
    return _spell_cast_count_life_capabilities()


def bind_spell_cast_count_life_effect(state, item, effect):
    if not uses_spell_cast_count_life(effect):
        return effect
    spec = SpellCastCountLifeSpec.from_effect(effect)
    if item.context.get("event") != "spell.cast":
        raise ValueError("Spell-count life lost its sealed cast occurrence")
    seat = item.controller if spec.recipient == "controller" else item.context.get("player")
    if type(seat) is not str or seat not in state.players:
        raise ValueError("Spell-count life lost its represented recipient")
    if not state.players[seat].in_game:
        return None
    count = public_history_query_value(state, seat, SPELL_CAST_COUNT_HISTORY)
    if type(count) is not int or count < 0:
        raise ValueError("Current spell-cast history is unavailable")
    if count == 0:
        return None
    return {"op": effect["op"], "player": seat, spec.amount_field: count}


_DRAWING_PLAYER_ONE_DAMAGE = {"op": "damage", "source": "$source", "target": "$context.player", "amount": 1}


def _is_drawing_player_one_damage(effect):
    return isinstance(effect, Mapping) and effect == _DRAWING_PLAYER_ONE_DAMAGE and type(effect["amount"]) is int


def is_optional_drawing_player_damage(effect):
    if (not isinstance(effect, Mapping) or set(effect) != {"op", "player", "effects"}
            or effect.get("op") != "offer_optional_effect" or effect.get("player") != "$controller"):
        return False
    effects = effect["effects"]
    return isinstance(effects, (tuple, list)) and len(effects) == 1 and _is_drawing_player_one_damage(effects[0])


def _optional_drawing_player_damage_runtime_dependencies(program):
    from ..characteristic_fragments import CharacteristicQuantityScope, CharacteristicQuantitySpec
    from ..continuous_conditions import (
        FIXED_PUBLIC_STATE_INTERVENING_CONDITION_FIELD, FIXED_PUBLIC_STATE_INTERVENING_COVERAGE,
        FixedPublicStateConditionKind, FixedPublicStateConditionSpec,
    )
    from ..object_predicate import ObjectQuerySpec
    from ..replacement.immutable import thaw_value

    red_permanent = FixedPublicStateConditionSpec(
        FixedPublicStateConditionKind.QUERY_COUNT_AT_LEAST, amount=1, schema_version=2,
        quantity=CharacteristicQuantitySpec(
            scope=CharacteristicQuantityScope.CONTROLLER_ZONE,
            query=ObjectQuerySpec(zones=("battlefield",), colors_all=("R",)),
        ),
    )
    expected = {"all": [
        {"field": "player", "op": "ne", "value": "$source.controller"},
        {"field": FIXED_PUBLIC_STATE_INTERVENING_CONDITION_FIELD, "op": "truthy", "value": True,
         "condition": red_permanent.to_dict()},
    ]}
    condition = thaw_value(program.event_condition)
    if (program.event != "card.drawn" or program.active_zone != "battlefield"
            or len(program.effects) != 1 or not is_optional_drawing_player_damage(program.effects[0])
            or program.target_schema is not None or program.cost_schema is not None or program.handlers
            or condition != expected or condition["all"][1]["value"] is not True
            or not {"intervening_condition", FIXED_PUBLIC_STATE_INTERVENING_COVERAGE} <= set(program.coverage)):
        raise ValueError("Optional draw damage requires the exact opponent draw and intervening red permanent")
    from ..compiler.optional_effect_templates import FIXED_OPTIONAL_EFFECT_CAPABILITY

    return (FIXED_OPTIONAL_EFFECT_CAPABILITY, "trigger.event.normalized_card_draw",
            "trigger.condition.fixed_public_state")


def _closed_draw_damage_has_recipient(item, effect):
    if item.context.get("event") != "card.drawn":
        return False
    return (is_optional_drawing_player_damage(effect)
            or item.context.get("intervening_condition") == {} and _is_drawing_player_one_damage(effect))


def bind_event_player_count_effect(state, item, effect):
    from .artifact_mana_tap_control import bind_artifact_mana_tap_control, uses_artifact_mana_tap_control

    if uses_artifact_mana_tap_control(effect):
        return bind_artifact_mana_tap_control(state, item, effect)
    from .opposing_owner_entry_life import bind_opposing_owner_entry_life, uses_opposing_owner_entry_life

    if uses_opposing_owner_entry_life(effect):
        return bind_opposing_owner_entry_life(state, item, effect)
    if _closed_draw_damage_has_recipient(item, effect):
        player = state.players.get(item.context.get("player"))
        if player is None:
            raise ValueError("Draw damage is missing its committed drawing player")
        return effect if player.in_game else None
    from ..land_entry_condition_model import OTHER_LAND_ARRIVAL_EVENT, other_land_arrival_damage_effect

    if (item.context.get("event") == OTHER_LAND_ARRIVAL_EVENT and effect == other_land_arrival_damage_effect()
            and item.context.get("intervening_condition") is not None):
        from ..land_entry_condition_model import uses_other_land_arrival_condition

        if uses_other_land_arrival_condition(item.context["intervening_condition"]):
            player = state.players.get(item.context.get("controller"))
            if player is None:
                raise ValueError("Land entry history damage lost its entering player")
            return effect if player.in_game else None
    from ..triggered_mana_model import LAND_MANA_TAP_EVENT, land_mana_tap_damage_effect

    if item.context.get("event") == LAND_MANA_TAP_EVENT and effect == land_mana_tap_damage_effect():
        player = state.players.get(item.context.get("player"))
        if player is None or type(item.context.get("land_basic")) is not bool:
            raise ValueError("Land mana tap damage lost its recorded producer")
        return effect if player.in_game else None
    if not uses_equipped_combat_hand_count(effect):
        return bind_spell_cast_count_life_effect(state, item, effect)
    spec = EquippedCombatHandCountSpec.from_effect(effect)
    if (item.context.get("event") != "damage.dealt" or item.context.get("combat") is not True
            or item.context.get("target_kind") != "player"):
        raise ValueError("Hand-size results require the sealed combat player damage occurrence")
    seat = item.context.get("target") if spec.role == "damaged_player" else item.controller
    if type(seat) is not str or seat not in state.players:
        raise ValueError("Hand size lost its original player")
    player = state.players[seat]
    if not player.in_game:
        return None
    hand = player.zones.get("hand")
    if not isinstance(hand, list):
        raise ValueError("The role player has no hand-size information")
    if not hand:
        return None
    return {**effect, spec.amount_field: len(hand)}


def fixed_event_player_result_node_capabilities(*,effects,target_schema,mechanic_ids):
    if UNBLOCKED_DEFENDER_POISON_MECHANIC in mechanic_ids:
        return unblocked_defender_poison_node_capabilities(effects, target_schema)
    if EVENT_PLAYER_DAMAGE_MECHANIC in set(mechanic_ids):
        return fixed_event_player_damage_node_capabilities(effects=effects,target_schema=target_schema,mechanic_ids=mechanic_ids)
    if EVENT_PLAYER_RESULT_MECHANIC not in set(mechanic_ids) or target_schema is not None or not 1<=len(effects)<=2:return ()
    if uses_death_opponent_results(effects):
        required = death_opponent_node_capabilities(effects, target_schema)
        return (*required, EVENT_PLAYER_RESULT_CAPABILITY) if required else ()
    if len(effects) == 1 and uses_spell_cast_count_life(effects[0]):
        try:
            SpellCastCountLifeSpec.from_effect(effects[0])
        except (TypeError, ValueError, KeyError):
            return ()
        return _spell_cast_count_life_capabilities()
    if not any(effect.get('player')!='$controller' for effect in effects):return ()
    layouts={'draw':('count',{'op','player','count','private'},'zone.draw.library_to_hand'),
        'life':('delta',{'op','player','delta'},'life.change.effect'),
        'lose_life':('amount',{'op','player','amount'},'life.change.effect')}
    capabilities={EVENT_PLAYER_RESULT_CAPABILITY}
    for index, effect in enumerate(effects):
        if effect.get('op') not in layouts:return ()
        field,fields,capability=layouts[effect['op']]
        extra=event_result_value_capabilities(effect['op'],field,effect.get(field))
        player = effect.get('player')
        occurrence = is_occurrence_controller_reference(player)
        if occurrence:
            if index != 0 or effect['op'] != 'lose_life' or type(effect.get(field)) is not int:
                return ()
            if len(effects) == 2:
                gain = effects[1]
                if (gain.get('op') != 'life' or gain.get('player') != '$controller'
                        or type(gain.get('delta')) is not int or gain['delta'] <= 0):
                    return ()
        if (set(effect)!=fields or not occurrence and player not in ('$controller','$context.player','$context.target','$context.defending_player','$context.previous_controller','$context.controller')
                or extra is None or HALF_LIFE_CAPABILITY in extra and HALF_LIFE_MECHANIC not in set(mechanic_ids)
                or effect['op']=='draw' and effect['private'] is not True):return ()
        capabilities.update((capability,*extra))
        if occurrence:
            capabilities.add(TARGET_CONTROLLER_INFORMATION_CAPABILITY)
    return tuple(sorted(capabilities))


def fixed_event_player_result_covered_mechanics(supplied):
    from .entry_player_counter_payment import (
        ENTRY_PLAYER_COUNTER_PAYMENT_CAPABILITY, ENTRY_PLAYER_COUNTER_PAYMENT_MECHANIC,
    )

    payment = {ENTRY_PLAYER_COUNTER_PAYMENT_MECHANIC} if ENTRY_PLAYER_COUNTER_PAYMENT_CAPABILITY in supplied else set()
    poison = {UNBLOCKED_DEFENDER_POISON_MECHANIC} if UNBLOCKED_DEFENDER_POISON_CAPABILITY in supplied else set()
    return payment | poison | ({EVENT_PLAYER_RESULT_MECHANIC} if EVENT_PLAYER_RESULT_CAPABILITY in supplied else set()) | mill_covered_mechanics(supplied) | (
        {EVENT_PLAYER_DAMAGE_MECHANIC} if EVENT_PLAYER_DAMAGE_CAPABILITY in supplied else set())


def is_closed_fixed_event_player_result_program(program):
    if UNBLOCKED_DEFENDER_POISON_MECHANIC in program.coverage:
        required = unblocked_defender_poison_program_capabilities(program)
        return bool(required) and set(required) <= set(program.capability_dependencies)
    required=fixed_event_player_result_node_capabilities(effects=program.effects,
        target_schema=program.target_schema,mechanic_ids=program.coverage)
    if not required or not set(required).issubset(program.capability_dependencies):return False
    if uses_death_opponent_results(program.effects):
        try:
            return set(death_opponents_program_dependencies(program)) <= set(program.capability_dependencies)
        except (TypeError, ValueError):
            return False
    return all(_event_player_reference_is_closed(program,
        effect['target'] if effect.get('op') == 'damage' else effect['player'], amount=effect.get('amount'))
        for effect in program.effects)


def _equipped_combat_hand_count_capabilities(effects, target_schema, mechanics):
    if (target_schema is not None or len(effects) != 2
            or not {EVENT_PLAYER_RESULT_MECHANIC, EVENT_PLAYER_DAMAGE_MECHANIC} <= set(mechanics)):
        return ()
    damage = effects[0]
    try:
        roles = tuple(EquippedCombatHandCountSpec.from_effect(row).role for row in effects)
    except (TypeError, ValueError):
        return ()
    if roles != ("damaged_player", "trigger_controller"):
        return ()
    projected_damage = fixed_event_player_damage_node_capabilities(
        effects=({**damage, "amount": 1},), target_schema=None, mechanic_ids=mechanics,
    )
    if not projected_damage:
        return ()
    return tuple(sorted({*projected_damage, SCALAR_AMOUNT_CAPABILITY, "life.change.effect",
                         "attachment.reference.current_or_lki", "trigger.event.normalized_damage",
                         "trigger.placement.apnap"}))


def fixed_event_player_damage_node_capabilities(*,effects,target_schema,mechanic_ids):
    if any(uses_equipped_combat_hand_count(effect) for effect in effects):
        return _equipped_combat_hand_count_capabilities(effects, target_schema, mechanic_ids)
    if not {EVENT_PLAYER_RESULT_MECHANIC,EVENT_PLAYER_DAMAGE_MECHANIC}<=set(mechanic_ids) or target_schema is not None or len(effects)!=1:return ()
    effect=effects[0]
    target = effect.get('target')
    occurrence = is_occurrence_controller_reference(target)
    extra = event_result_value_capabilities('damage', 'amount', effect.get('amount'),
        committed_damage=occurrence and type(effect.get('amount')) is not int)
    if (set(effect)!={'op','source','target','amount'} or effect.get('op')!='damage' or effect.get('source')!='$source'
            or not occurrence and target not in ('$context.player','$context.target','$context.defending_player',
                                                  '$context.previous_controller','$context.controller')
            or extra is None):return ()
    from .damage_capability_shapes import fixed_damage_node_capabilities
    amount=effect['amount'] if type(effect['amount']) is int else 2
    damage=fixed_damage_node_capabilities(effects=({**effect,'target':'$controller','amount':amount},),target_schema=None,mechanic_ids={'cr-120-damage'})
    information = (TARGET_CONTROLLER_INFORMATION_CAPABILITY,) if occurrence else ()
    return tuple(sorted({EVENT_PLAYER_RESULT_CAPABILITY,EVENT_PLAYER_DAMAGE_CAPABILITY,*damage,*extra,*information})) if damage else ()


def _blocking_controller_event_is_closed(program):
    return (program.event == 'creature.blocks' and program.active_zone == 'battlefield'
            and program.event_condition is None and program.target_schema is None
            and 'one_or_more_event_batch' not in program.coverage)


def _attached_combat_controller_event_is_closed(program):
    if (program.event != 'creature.attacks' or program.active_zone != 'battlefield'
            or program.target_schema is not None or 'one_or_more_event_batch' in program.coverage):
        return False
    from .event_subscriptions import FixedEventSubscriptionSet

    attachment = {'field': 'source_attachment_target_ref', 'op': 'eq', 'value': '$context.card'}
    subscriptions = FixedEventSubscriptionSet.from_condition(program.event_condition)
    if subscriptions is None:
        return program.event_condition == attachment
    return tuple((row.event, row.condition) for row in subscriptions.subscriptions) == (
        ('creature.attacks', attachment), ('creature.blocks', attachment),
    )


def attached_combat_controller_runtime_dependencies(program):
    if (not _attached_combat_controller_event_is_closed(program) or not program.effects
            or not is_occurrence_controller_reference(program.effects[0].get('player'))):
        return ()
    required = fixed_event_player_result_node_capabilities(
        effects=program.effects, target_schema=None, mechanic_ids={EVENT_PLAYER_RESULT_MECHANIC},
    )
    if not required:
        return ()
    subscription = ('trigger.subscription.fixed_multi_event',) if 'event_subscriptions' in program.event_condition else ()
    return (*required, 'trigger.event.normalized_public_action', 'attachment.reference.current_or_lki', *subscription)


def attack_occurrence_controller_runtime_dependencies(program):
    attached = attached_combat_controller_runtime_dependencies(program)
    if attached:
        return attached

    from .qualified_attacks import declared_defender_scope_is_closed, qualified_player_defense_is_closed
    from ..qualified_attack_model import QUALIFIED_ATTACK_CAPABILITY

    condition = program.event_condition
    if (program.event != 'creature.attacks' or program.active_zone != 'battlefield' or program.target_schema is not None
            or not (qualified_player_defense_is_closed(condition) or declared_defender_scope_is_closed(condition))
            or not program.effects
            or 'one_or_more_event_batch' in program.coverage
            or not is_occurrence_controller_reference(program.effects[0].get('player'))):
        return ()
    required = fixed_event_player_result_node_capabilities(
        effects=program.effects, target_schema=None, mechanic_ids=program.coverage,
    )
    return (*required, QUALIFIED_ATTACK_CAPABILITY, 'trigger.event.normalized_public_action') if required else ()


def blocking_controller_runtime_dependencies(program):
    if not _blocking_controller_event_is_closed(program) or len(program.effects) != 1:
        return ()
    effect, = program.effects
    damage = effect.get('op') == 'damage'
    if (not is_occurrence_controller_reference(effect.get('target' if damage else 'player'))
        or damage and type(effect.get('amount')) is not int):
        return ()
    mechanics = {EVENT_PLAYER_RESULT_MECHANIC, EVENT_PLAYER_DAMAGE_MECHANIC} if damage else {EVENT_PLAYER_RESULT_MECHANIC}
    required = fixed_event_player_result_node_capabilities(effects=program.effects, target_schema=None, mechanic_ids=mechanics)
    return (*required, 'trigger.event.normalized_public_action') if required else ()


def _equipped_combat_hand_count_runtime_dependencies(program):
    from ..replacement.immutable import FrozenMap

    expected = {"all": [
        {"field": "source_attachment_target_ref", "op": "eq", "value": "$context.source"},
        {"field": "target_kind", "op": "eq", "value": "player"},
        {"field": "combat", "op": "truthy", "value": True},
    ]}
    required = _equipped_combat_hand_count_capabilities(program.effects, program.target_schema, program.coverage)
    if (not required or (program.event, program.active_zone) != ("damage.dealt", "battlefield")
            or FrozenMap(program.event_condition or {}) != FrozenMap(expected)
            or program.cost_schema is not None or program.handlers):
        raise ValueError("The ordered hand counts require the original equipped combat player damage")
    return required


def event_player_damage_runtime_dependencies(program):
    from .artifact_mana_tap_control import artifact_mana_tap_control_program_dependencies
    from ..land_entry_condition_model import OTHER_LAND_ARRIVAL_COVERAGE, uses_other_land_arrival_condition
    from ..triggered_mana_model import LAND_MANA_TAP_EVENT, SOURCE_MANA_TAP_DAMAGE_MECHANIC, SOURCE_MANA_TAP_EVENT

    if artifact_control := artifact_mana_tap_control_program_dependencies(program):
        return artifact_control
    if program.event == SOURCE_MANA_TAP_EVENT or SOURCE_MANA_TAP_DAMAGE_MECHANIC in program.coverage:
        return _source_mana_tap_damage_dependencies(program)
    if OTHER_LAND_ARRIVAL_COVERAGE in program.coverage or uses_other_land_arrival_condition(program.event_condition):
        return _opponent_other_land_arrival_dependencies(program)
    if program.event == LAND_MANA_TAP_EVENT:
        from .tap_state_capability_shapes import mana_tapped_land_untap_runtime_dependencies

        return mana_tapped_land_untap_runtime_dependencies(program) or _land_mana_tap_damage_dependencies(program)
    if any(uses_equipped_combat_hand_count(effect) for effect in nested_effects((program.effects, program.handlers))):
        return _equipped_combat_hand_count_runtime_dependencies(program)
    required=set();has_marker=EVENT_PLAYER_DAMAGE_MECHANIC in program.coverage
    energy=False
    def visit(node):
        nonlocal energy
        if isinstance(node,Mapping):
            if _is_scalar_player_counter(node):
                energy=True
            if (node.get('op') == 'damage' and node.get('source') == '$source' and node.get('target') != '$controller'
                    and _event_player_reference_is_closed(program, node.get('target'), amount=node.get('amount'))
                    and event_result_value_capabilities('damage', 'amount', node.get('amount'), committed_damage=(
                        is_occurrence_controller_reference(node.get('target')) and type(node.get('amount')) is not int)) is not None):
                caps=fixed_event_player_damage_node_capabilities(effects=(node,),target_schema=None,
                    mechanic_ids={EVENT_PLAYER_RESULT_MECHANIC,EVENT_PLAYER_DAMAGE_MECHANIC})
                if not caps:raise ValueError('Malformed fixed event-player damage instruction')
                required.update(caps)
            for child in node.values():visit(child)
        elif isinstance(node,(list,tuple)):
            for child in node:visit(child)
    visit(program.effects)
    if any(is_optional_drawing_player_damage(effect) for effect in nested_effects(program.effects)):
        required.update(_optional_drawing_player_damage_runtime_dependencies(program))
    if has_marker and not required:raise ValueError('Fixed event-player damage marker lacks a closed bound instruction')
    if energy:
        required.update(combat_damage_energy_runtime_dependencies(program))
    return tuple(sorted(required))


def _opponent_other_land_arrival_dependencies(program):
    from ..land_entry_condition_model import (
        OTHER_LAND_ARRIVAL_COVERAGE, OTHER_LAND_ARRIVAL_EVENT, opponent_other_land_arrival_condition,
        other_land_arrival_damage_effect, other_land_arrival_leaf_is_closed,
    )
    from ..public_history_query_model import PUBLIC_HISTORY_QUERY_CAPABILITY
    from ..replacement.immutable import thaw_value

    condition = thaw_value(program.event_condition)
    if (program.event != OTHER_LAND_ARRIVAL_EVENT or program.active_zone != "battlefield"
            or not {OTHER_LAND_ARRIVAL_COVERAGE, "intervening_condition"} <= set(program.coverage)
            or condition != opponent_other_land_arrival_condition()
            or not other_land_arrival_leaf_is_closed(condition["all"][1])
            or len(program.effects) != 1 or thaw_value(program.effects[0]) != other_land_arrival_damage_effect()
            or type(program.effects[0].get("amount")) is not int
            or program.target_schema is not None or program.cost_schema is not None or program.handlers):
        raise ValueError("Opponent land entry history requires the original committed other arrival and fixed damage")
    damage = fixed_event_player_damage_node_capabilities(
        effects=program.effects, target_schema=None,
        mechanic_ids={EVENT_PLAYER_RESULT_MECHANIC, EVENT_PLAYER_DAMAGE_MECHANIC},
    )
    return (*damage, PUBLIC_HISTORY_QUERY_CAPABILITY, "trigger.condition.fixed_public_state",
            "trigger.event.normalized_zone_change")


def _source_mana_tap_damage_dependencies(program):
    from ..replacement.immutable import thaw_value
    from ..triggered_mana_model import (
        SOURCE_MANA_TAP_CAPABILITY, SOURCE_MANA_TAP_DAMAGE_MECHANIC, SOURCE_MANA_TAP_EVENT,
        source_mana_tap_condition, source_mana_tap_damage_effect,
    )
    from .damage_capability_shapes import fixed_damage_node_capabilities

    if (program.event != SOURCE_MANA_TAP_EVENT or program.active_zone != "battlefield"
            or SOURCE_MANA_TAP_DAMAGE_MECHANIC not in program.coverage
            or program.target_schema is not None or program.cost_schema is not None or program.handlers
            or thaw_value(program.event_condition) != source_mana_tap_condition()
            or len(program.effects) != 1 or thaw_value(program.effects[0]) != source_mana_tap_damage_effect()
            or type(program.effects[0].get("amount")) is not int):
        raise ValueError("Source mana tap damage requires the exact activating source and fixed opponent damage")
    damage = fixed_damage_node_capabilities(effects=program.effects, target_schema=None,
                                            mechanic_ids={"cr-120-damage"})
    return (SOURCE_MANA_TAP_CAPABILITY, "trigger.event.normalized_public_action", *damage)


def _land_mana_tap_damage_dependencies(program):
    from ..replacement.immutable import thaw_value
    from ..triggered_mana_model import land_mana_tap_condition, land_mana_tap_damage_effect

    if (program.active_zone != "battlefield" or program.target_schema is not None
            or program.cost_schema is not None or program.handlers
            or len(program.effects) != 1 or thaw_value(program.effects[0]) != land_mana_tap_damage_effect()
            or type(program.effects[0].get("amount")) is not int
            or thaw_value(program.event_condition) not in (None, land_mana_tap_condition(nonbasic=True))
            or program.event_condition is not None and program.event_condition.get("value") is not False):
        raise ValueError("Land mana tap damage requires the exact activating player and optional nonbasic filter")
    required = fixed_event_player_damage_node_capabilities(
        effects=program.effects, target_schema=None,
        mechanic_ids={EVENT_PLAYER_RESULT_MECHANIC, EVENT_PLAYER_DAMAGE_MECHANIC},
    )
    return (*required, "trigger.event.normalized_public_action")


def _event_player_reference_is_closed(program,player,*,amount=None):
    if player=='$controller':return True
    if program.event == 'creature.blocks' and is_occurrence_controller_reference(player):
        return _blocking_controller_event_is_closed(program) and (amount is None or type(amount) is int)
    from .event_subscriptions import FixedEventSubscriptionSet
    try:
        subscriptions=FixedEventSubscriptionSet.from_condition(program.event_condition)
    except (TypeError,ValueError):return False
    if is_occurrence_controller_reference(player):
        if program.event == 'creature.attacks':
            return bool(attack_occurrence_controller_runtime_dependencies(program))
        return (subscriptions is None and program.active_zone == 'battlefield' and program.target_schema is None
                and 'one_or_more_event_batch' not in program.coverage
                and (program.event in OCCURRENCE_CONTROLLER_ENTRY_EVENTS if type(amount) is int
                     else is_creature_receiving_event(program.event, program.event_condition)))
    occurrences=((program.event,program.event_condition),) if subscriptions is None else tuple(
        (entry.event,entry.condition) for entry in subscriptions.subscriptions)
    if program.event not in {event for event,_condition in occurrences}:return False
    return all(_occurrence_player_reference_is_closed(event,condition,program.coverage,player)
        for event,condition in occurrences)


def _source_combat_player_scope(condition):
    from collections.abc import Mapping
    if not isinstance(condition,Mapping):return False
    if condition=={'field':'card','op':'eq','value':'$source.ref'}:return True
    if set(condition)=={'all'} and isinstance(condition['all'],(list,tuple)):return any(_source_combat_player_scope(child) for child in condition['all'])
    if set(condition)=={'any'} and isinstance(condition['any'],(list,tuple)) and condition['any']:return all(_source_combat_player_scope(child) for child in condition['any'])
    return False


def _occurrence_player_reference_is_closed(event,condition,coverage,player):
    from .event_subscriptions import LAND_PLAY_EVENT
    if player=='$context.player':return event in {LAND_PLAY_EVENT,'spell.cast','card.drawn','player.scried','player.surveilled'}
    if player=='$context.previous_controller':return event in OCCURRENCE_CONTROLLER_GRAVEYARD_EVENTS | {
        'card.discarded', 'creature.dies.self', 'permanent.sacrificed',
    }
    if player=='$context.controller':return event in {'land.enter','creature.enter','permanent.enter','artifact.enter','enchantment.enter'}
    from ..compiler.event_player_results import _damage_guarantees_player
    if player=='$context.target':return event in {'damage.dealt','damage.dealt.self'} and _damage_guarantees_player(condition)
    if player=='$context.defending_player' and event in {'creature.becomes_blocked','creature.unblocked'}:
        return _source_combat_player_scope(condition) and 'one_or_more_event_batch'not in coverage
    return (player=='$context.defending_player' and event=='creature.attacks'
        and bool({'trigger-event-normalized-public-action','trigger-event-normalized-self-attack'} & set(coverage))
        and 'one_or_more_event_batch' not in coverage)


def combat_damage_energy_effect():
    """Use the committed scalar on the existing controller counter operation."""
    return {
        "op": "place_player_counters",
        "subjects": "controller",
        "counter": "energy",
        "amount": ScalarEffectAmountSpec(ScalarAmountOrigin.EVENT_AMOUNT).to_dict(),
        "source": "$source",
    }


def _is_scalar_player_counter(effect):
    amount = effect.get("amount")
    return (effect.get("op") == "place_player_counters" and isinstance(amount, Mapping)
            and amount.get("kind") == SCALAR_AMOUNT_KIND)


def _has_scalar_player_counter(value):
    if isinstance(value, Mapping):
        return _is_scalar_player_counter(value) or any(
            _has_scalar_player_counter(child) for child in value.values()
        )
    return isinstance(value, (list, tuple)) and any(_has_scalar_player_counter(child) for child in value)


def combat_damage_energy_fixed_projection(effect):
    """Prove that the scalar varies only the amount of controller energy."""
    if not _is_scalar_player_counter(effect):
        return None
    expected = combat_damage_energy_effect()
    if effect != expected or ScalarEffectAmountSpec.from_dict(effect["amount"]) != ScalarEffectAmountSpec(ScalarAmountOrigin.EVENT_AMOUNT):
        raise ValueError("Combat damage energy requires one unscaled committed amount and the ability controller")
    return {**expected, "amount": 2}


def combat_damage_energy_runtime_dependencies(program):
    """Independently require the source incarnation and actual player damage event."""
    if not _has_scalar_player_counter(program.effects):
        return ()
    if len(program.effects) != 1 or not combat_damage_energy_fixed_projection(program.effects[0]):
        raise ValueError("Combat damage energy must be the complete triggered result")
    condition = program.event_condition
    expected = (
        {"field": "target_kind", "op": "eq", "value": "player"},
        {"field": "combat", "op": "truthy", "value": True},
        {"field": "source_logical_object_id", "op": "eq", "value": "$source.logical_object_id"},
    )
    predicates = condition.get("all") if isinstance(condition, Mapping) and set(condition) == {"all"} else None
    if (program.event != "damage.dealt.self" or program.active_zone != "battlefield"
            or program.target_schema is not None or program.cost_schema is not None
            or not isinstance(predicates, (list, tuple)) or tuple(predicates) != expected
            or predicates[1]["value"] is not True):
        raise ValueError("Combat damage energy requires the original creature's combat damage to a player")
    return (SCALAR_AMOUNT_CAPABILITY, "counter.producer.fixed_player_effect",
            "trigger.event.normalized_damage", "trigger.placement.apnap")
