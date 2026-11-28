from __future__ import annotations

"""Comparison of complete fixed activation results through their typed owners."""

from ..compiler.hand_entry_templates import FixedPrivateHandEntryTemplate
from ..replacement.immutable import thaw_value
from ..object_predicate import ObjectQuerySpec
from ..public_zone_moves import (
    PublicZoneMoveSetSpec, PublicZoneOrigin, PublicZoneDestination,
    PublicZoneRelationAxis, PublicZoneSeatRelation,
)
from ..rules.public_zone_move_capability_shapes import (
    fixed_private_hand_entry_node_capabilities, fixed_public_zone_move_set_node_capabilities,
)
from ..rules.reanimation_capability_shapes import fixed_target_reanimation_node_capabilities
from ..rules.node_capability_shapes import fixed_counter_placement_target_set_node_capabilities
from ..continuous_effect_model import ContinuousEffectDuration
from ..resolution_characteristic_model import (
    TARGET_CARD_TYPE_ADDITION_VERSION, fixed_resolution_characteristic_instruction,
)
from ..rules.resolution_characteristic_shapes import fixed_resolution_characteristic_capabilities
from .activation_target_equivalence import (
    _fixed_graveyard_artifact_target, _optional_creature_target, _optional_tapped_creature_target,
)


def _owned(program, validator):
    required = validator(effects=program.effects, target_schema=program.target_schema,
        mechanic_ids=program.coverage)
    return bool(required) and set(required) <= set(program.capability_dependencies)


def _artifact_from_hand(current, old):
    if current.target_schema is not None or old.target_schema is not None:return False
    expected = FixedPrivateHandEntryTemplate(ObjectQuerySpec(zones=('hand',), types_all=('artifact',)))
    return (list(old.effects) == [{'op': 'put_artifact_from_hand', 'player': '$controller'}]
        and list(current.effects) == list(expected.effects)
        and _owned(current, fixed_private_hand_entry_node_capabilities))


def _graveyard_return(current, old):
    if _fixed_graveyard_artifact_target(current.target_schema) is None:return False
    if len(old.effects) != 1 or len(current.effects) != 1:return False
    effect = dict(old.effects[0])
    if 'reason' in effect:
        if type(effect.pop('reason')) is not str:return False
    if effect != {'op': 'move', 'card': '$target.0', 'destination': 'battlefield', 'controller': '$controller'}:return False
    return (dict(current.effects[0]) == {'op': 'reanimate', 'card': '$target.0',
        'controller': '$controller', 'tapped': False}
        and _owned(current, fixed_target_reanimation_node_capabilities))


def _opponent_graveyards(current, old):
    if current.target_schema is not None or old.target_schema is not None:return False
    if list(old.effects) != [{'op': 'exile_opponent_graveyards'}] or len(current.effects) != 1:return False
    effect = current.effects[0]
    if set(effect) != {'op', 'source', 'set'} or effect['op'] != 'move_public_zone_set' or effect['source'] != '$source':return False
    expected = PublicZoneMoveSetSpec(query=ObjectQuerySpec(zones=('graveyard',)),
        origin=PublicZoneOrigin.GRAVEYARD, destination=PublicZoneDestination.EXILE,
        relation_axis=PublicZoneRelationAxis.OWNER, seat_relation=PublicZoneSeatRelation.OPPONENTS)
    try:actual = PublicZoneMoveSetSpec.from_dict(effect['set'])
    except (TypeError, ValueError, KeyError):return False
    return actual == expected and _owned(current, fixed_public_zone_move_set_node_capabilities)


def _fixed_target_counter(current, old):
    if _optional_tapped_creature_target(current.target_schema) is None:return False
    if len(current.effects) != 1 or len(old.effects) != 1:return False
    a, b = (dict(p.effects[0]) for p in (current, old))
    if 'reason' in b:
        if type(b.pop('reason')) is not str:return False
    if (set(a) != {'op', 'cards', 'maximum_targets', 'counter', 'amount', 'source'}
            or a['op'] != 'place_counters_on_targets' or a['cards'] != '$targets'
            or a['source'] != '$source' or type(a['maximum_targets']) is not int or a['maximum_targets'] != 1
            or type(a['counter']) is not str or not a['counter'] or type(a['amount']) is not int or a['amount'] <= 0):return False
    expected = {'op': 'add_counter_selected', 'cards': '$targets', 'counter': a['counter'], 'amount': a['amount']}
    required = {'counter.producer.fixed_permanent_target_set_effect', 'target.revalidate_resolution'}
    return (type(b.get('amount')) is int and type(b.get('counter')) is str
        and b == expected and required <= set(current.capability_dependencies)
        and _owned(current, fixed_counter_placement_target_set_node_capabilities))


def _temporary_graveyard_copy_matches_reviewed(current, old):
    from ..rules.temporary_self_copy import (
        is_closed_temporary_self_copy_program, temporary_graveyard_copy_effect, temporary_graveyard_copy_target_schema,
    )

    return (list(current.effects) == [temporary_graveyard_copy_effect()]
            and current.target_schema == old.target_schema == temporary_graveyard_copy_target_schema()
            and list(old.effects) == [{"op": "copy_until_end_of_turn", "source": "$source", "target": "$target.0"}]
            and {"delirium", "temporary_copy"} <= set(old.coverage)
            and is_closed_temporary_self_copy_program(current))


def _source_permanent_return(current, old):
    from ..rules.self_return_capability_shapes import fixed_self_return_node_capabilities
    from .reviewed_overlay import _canonical_effects

    if (current.active_zone != "battlefield" or old.target_schema is not None
            or list(current.effects) != [{"op": "bounce", "card": "$source.zone_object"}]
            or not _owned(current, fixed_self_return_node_capabilities)):
        return False
    previous = _canonical_effects(old)
    if len(previous) != 1:
        return False
    reason = previous[0].pop("reason", None)
    return ((reason is None or isinstance(reason, str))
            and previous == [{"op": "move", "card": "$source", "destination": "hand"}])


def _optional_creature_untap(current, old):
    from ..rules.fixed_homogeneous_target_set_capability_shapes import fixed_homogeneous_target_set_node_capabilities

    return (
        _optional_creature_target(current.target_schema) is not None
        and list(old.effects) == [{"op": "untap", "card": "$target.0"}]
        and list(current.effects) == [{"op": "untap_targets", "cards": "$targets", "maximum_targets": 1}]
        and type(current.effects[0]["maximum_targets"]) is int
        and _owned(current, fixed_homogeneous_target_set_node_capabilities)
    )


def _mill_then_bounded_battlefield_return(current, old):
    from ..rules.fixed_effect_clause_shapes import fixed_effect_clause_sequence_node_capabilities
    from ..rules.graveyard_return_choices import validate_return_choice
    from ..replacement.immutable import thaw_value

    if current.target_schema is not None or len(current.effects) != 2 or len(old.effects) != 2:
        return False
    mill, return_choice = current.effects
    previous_mill, previous_choice = (thaw_value(effect) for effect in old.effects)
    count = mill.get("count")
    if (type(count) is not int or type(previous_mill.get("count")) is not int
            or dict(mill) != {"op": "mill", "player": "$controller", "count": count}
            or dict(mill) != previous_mill):
        return False
    try:
        selection = validate_return_choice(return_choice)
    except (TypeError, ValueError):
        return False
    if not selection.returns_to_battlefield or set(previous_choice) != {"op", "player", "prompt", "selector", "then"}:
        return False
    maximum = selection.mana_value_max
    previous_moves = previous_choice["then"]
    selector = previous_choice["selector"]
    if not isinstance(previous_moves, list) or len(previous_moves) != 1 or not isinstance(previous_moves[0], dict):
        return False
    move, = previous_moves
    reason = move.pop("reason", None)
    return (
        (reason is None or isinstance(reason, str))
        and move == {"op": "move_selected", "destination": "battlefield"}
        and previous_choice["op"] == "choose_objects" and previous_choice["player"] == "$controller"
        and previous_choice["prompt"] == f"You may return a creature card with mana value {maximum} or less."
        and selector == {"zones": ["graveyard"], "categories": ["card"], "owner_relation": "you",
                        "creature": True, "mana_value_max": maximum, "min": 0, "max": 1}
        and selector["creature"] is True
        and all(type(selector[key]) is int for key in ("mana_value_max", "min", "max"))
        and _owned(current, fixed_effect_clause_sequence_node_capabilities)
    )


def fixed_activation_results_equal(current, old):
    return (_artifact_from_hand(current, old) or _graveyard_return(current, old)
        or _source_permanent_return(current, old)
        or _opponent_graveyards(current, old) or _fixed_target_counter(current, old)
        or _next_spell_improvise(current, old) or _next_cast_counter_protection(current, old)
        or _fixed_target_type_addition(current, old)
        or _temporary_graveyard_copy_matches_reviewed(current, old)
        or _activated_next_turn_protection(current, old)
        or _paid_artifact_search_replaces_reviewed(current, old)
        or _optional_creature_untap(current, old)
        or _mill_then_bounded_battlefield_return(current, old)
        or _mandatory_artifact_return_matches_reviewed(current, old)
        or _fixed_discard_draw_matches_reviewed(current, old)
        or _compiled_artifact_emblem_matches_reviewed(current, old))


def _fixed_discard_draw_matches_reviewed(current, old):
    from ..discard_result_model import FIXED_SUBSET_DISCARD_DRAW_CAPABILITY, validate_fixed_subset_discard_draw

    if (len(current.effects) != 1 or len(old.effects) != 1
            or FIXED_SUBSET_DISCARD_DRAW_CAPABILITY not in current.capability_dependencies
            or not {"private_discard", "private_draw"} <= set(old.coverage)):
        return False
    try:
        validate_fixed_subset_discard_draw(current.effects[0])
    except (TypeError, ValueError):
        return False
    legacy = old.effects[0]
    return (type(legacy.get("maximum")) is int
            and dict(legacy) == {"op": "discard_draw_up_to", "maximum": current.effects[0]["maximum"]})


def _compiled_artifact_emblem_matches_reviewed(current, old):
    from ..delayed_object_model import ARTIFACT_GRAVEYARD_RETURN_DEPENDENCIES, ARTIFACT_GRAVEYARD_RETURN_EVENT
    from ..emblem_program_model import CompiledEmblemSpec, is_closed_compiled_emblem_program

    if (len(old.effects) != 1 or not is_closed_compiled_emblem_program(current)
            or not {"emblem", "triggered_ability", "delayed_trigger"} <= set(old.coverage)):
        return False
    legacy = old.effects[0]
    if (set(legacy) != {"op", "controller", "abilities", "display_label", "semantic_key", "stats_counter"}
            or legacy["op"] != "create_emblem" or legacy["controller"] != "$controller"
            or legacy["semantic_key"] != "builtin:daretti-emblem"):
        return False
    children = CompiledEmblemSpec.from_dict(current.effects[0]["emblem"]).abilities
    return (len(children) == 1 and children[0].event == ARTIFACT_GRAVEYARD_RETURN_EVENT
            and children[0].kind == "triggered" and tuple(legacy["abilities"]) == (children[0].text,)
            and set(ARTIFACT_GRAVEYARD_RETURN_DEPENDENCIES) <= set(children[0].capabilities))


def _mandatory_artifact_return_matches_reviewed(current, old):
    from ..rules.mandatory_sacrifice_reanimation import (
        MANDATORY_ARTIFACT_REANIMATION_VERSION, mandatory_sacrifice_reanimation_runtime_dependencies,
    )

    if (list(old.effects) != [{"op": "daretti_exchange", "card": "$target.0"}]
            or not {"resolution_sacrifice", "graveyard_target", "reanimation"} <= set(old.coverage)
            or current.target_schema != old.target_schema or len(current.effects) != 1
            or current.effects[0].get("payment", {}).get("schema_version") != MANDATORY_ARTIFACT_REANIMATION_VERSION):
        return False
    try:
        required = mandatory_sacrifice_reanimation_runtime_dependencies(current)
    except (TypeError, ValueError, KeyError):
        return False
    return bool(required) and set(required) <= set(current.capability_dependencies)


def _fixed_target_type_addition(current, old):
    if len(current.effects) != 1 or len(old.effects) != 1:
        return False
    effect = current.effects[0]
    if effect.get("schema_version") != TARGET_CARD_TYPE_ADDITION_VERSION:
        return False
    try:
        spec, card = fixed_resolution_characteristic_instruction(effect)
    except (TypeError, ValueError, KeyError):
        return False
    if (spec.duration is not ContinuousEffectDuration.UNTIL_END_OF_TURN or spec.power or spec.toughness
            or not _owned(current, fixed_resolution_characteristic_capabilities)):
        return False
    previous = dict(old.effects[0])
    if "reason" in previous and type(previous.pop("reason")) is not str:
        return False
    return previous == {"op": "add_type_until_end_of_turn", "card": card, "type": spec.card_type}


def _next_spell_improvise(current, old):
    from ..rules.next_spell_payment import next_spell_payment_kind, next_spell_payment_node_capabilities

    if (current.target_schema is not None or old.target_schema is not None or len(current.effects) != 1
            or list(old.effects) != [{"op": "next_spell_improvise", "player": "$controller"}]):
        return False
    try:
        kind = next_spell_payment_kind(current.effects[0])
    except (TypeError, ValueError):
        return False
    return kind == "improvise" and _owned(current, next_spell_payment_node_capabilities)


def _next_cast_counter_protection(current, old):
    from ..rules.next_cast_counter_protection import (
        NEXT_CAST_PROTECTION_OPERATION, NextCastProtectionScope, next_cast_protection_effect_scope,
        next_cast_protection_node_capabilities,
    )

    if (current.target_schema is not None or old.target_schema is not None or len(current.effects) != 1
            or list(old.effects) != [{"op": NEXT_CAST_PROTECTION_OPERATION, "player": "$controller"}]):
        return False
    try:
        scope = next_cast_protection_effect_scope(current.effects[0])
    except (TypeError, ValueError):
        return False
    return scope is NextCastProtectionScope.ANY and _owned(current, next_cast_protection_node_capabilities)


def _activated_next_turn_protection(current, old):
    from ..rules.next_personal_turn_protection import (
        PROTECTION_OPERATION, activated_protection_node_capabilities,
    )

    if list(old.effects) != [{"op": PROTECTION_OPERATION, "player": "$controller"}]:
        return False
    required = activated_protection_node_capabilities(
        effects=current.effects, target_schema=current.target_schema, mechanics=current.coverage,
        active_zone=current.active_zone, cost_schema=current.cost_schema,
    )
    return bool(required) and set(required) <= set(current.capability_dependencies)


def _paid_artifact_search_replaces_reviewed(current, old):
    """Prefer the typed payment and required shuffle over this reviewed search."""
    from ..rules.paid_artifact_library_search import is_closed_paid_artifact_search_program

    legacy = {
        "op": "search", "searching_player": "$controller", "zone": "library",
        "selector": {"types": ["Artifact"], "mana_value": {"equal": "$context.cost_mana_value_plus_one"}},
        "count": {"minimum": 0, "maximum": 1}, "destination": "battlefield",
        "reveal": False, "shuffle_after": True, "optional": True,
    }
    return len(old.effects) == 1 and thaw_value(old.effects[0]) == legacy and is_closed_paid_artifact_search_program(current)
