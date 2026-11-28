from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence

from ..fractional_quantity_model import HALF_MILL_CAPABILITY, HALF_MILL_MECHANIC, HALF_QUANTITY_KIND, HalfQuantitySpec
from ..query_effect_amount_model import (
    OPPONENT_HAND_MILL_CAPABILITY, OPPONENT_HAND_MILL_KIND, OPPONENT_HAND_MILL_MECHANIC, OpponentHandMillCount,
)
from ..compiler.mill_templates import (
    ATTACKER_CONTROLLER_MILL_MECHANIC, EVENT_MILL_RECIPIENT_CAPABILITY, EVENT_MILL_RECIPIENT_MECHANIC,
    MILL_CAPABILITY_ID, MILL_MECHANIC_ID, EQUIPPED_WOLF_MILL_MECHANIC,
)
from ..target_controller_information import (
    TARGET_CONTROLLER_INFORMATION_CAPABILITY, is_occurrence_controller_reference,
)


def _player_target_schema_is_closed(
    target_schema: Mapping[str, object] | None,
) -> bool:
    if not isinstance(target_schema, Mapping) or type(target_schema.get("count")) is not int:
        return False
    required = {
        "zones": ["player"],
        "categories": ["player"],
        "count": 1,
    }
    if any(
        target_schema.get(key) != value
        for key, value in required.items()
    ):
        return False
    fields = set(target_schema)
    return fields in (
        set(required),
        {*required, "player_relation"},
    ) and target_schema.get("player_relation", "any") in {
        "any",
        "opponent",
    }


def _equipped_wolf_mill_node_capabilities(effects, target_schema, mechanics):
    from ..replacement.immutable import FrozenMap
    from .token_creation_capability_shapes import token_definition_payload_dependencies

    if target_schema is not None or len(effects) != 2:
        return ()
    token, mill = effects
    expected_token = {"op": "create_token", "controller": "$controller", "name": "Wolf Token", "quantity": 1,
                      "characteristics": {"type_line": "Token Creature — Wolf", "literal_token_name_version": 1,
                                          "colors": ["G"], "power": "2", "toughness": "2"}}
    expected_mill = {"op": "mill", "player": "$context.target", "count": 10}
    if (FrozenMap(token) != FrozenMap(expected_token) or type(token.get("quantity")) is not int
            or FrozenMap(mill) != FrozenMap(expected_mill) or type(mill.get("count")) is not int):
        return ()
    if type(token["characteristics"]["literal_token_name_version"]) is not int:
        return ()
    first = token_definition_payload_dependencies(token, mechanics)
    last = fixed_mill_node_capabilities(
        effects=(mill,), target_schema=None, mechanic_ids=mechanics - {EQUIPPED_WOLF_MILL_MECHANIC},
    )
    return tuple(sorted({*first, *last})) if first and last else ()


def equipped_wolf_mill_runtime_dependencies(program):
    from ..replacement.immutable import FrozenMap

    mechanics = set(getattr(program, "coverage", ()))
    if EQUIPPED_WOLF_MILL_MECHANIC not in mechanics:
        return ()
    expected_condition = {"all": [
        {"field": "source_attachment_target_ref", "op": "eq", "value": "$context.source"},
        {"field": "target_kind", "op": "eq", "value": "player"},
        {"field": "combat", "op": "truthy", "value": True},
    ]}
    required = _equipped_wolf_mill_node_capabilities(program.effects, program.target_schema, mechanics)
    if (not required or (program.event, program.active_zone) != ("damage.dealt", "battlefield")
            or FrozenMap(program.event_condition or {}) != FrozenMap(expected_condition)
            or program.cost_schema is not None or program.handlers):
        raise ValueError("Wolf and Mill requires the exact original equipped combat-damage occurrence and both results")
    return (*required, "attachment.reference.current_or_lki", "trigger.event.normalized_damage",
            "trigger.effect.fixed_event", "trigger.placement.apnap")


def fixed_mill_node_capabilities(
    *,
    effects: Sequence[Mapping[str, object]],
    target_schema: Mapping[str, object] | None,
    mechanic_ids: Iterable[str],
) -> tuple[str, ...]:
    """Own one mandatory Mill with a closed count and recipient."""
    mechanics = {str(value).casefold() for value in mechanic_ids}
    if EQUIPPED_WOLF_MILL_MECHANIC in mechanics:
        return _equipped_wolf_mill_node_capabilities(effects, target_schema, mechanics)
    if MILL_MECHANIC_ID not in mechanics or len(effects) != 1:
        return ()
    effect = effects[0]
    if effect.get("op") != "mill":
        return ()
    from .milled_permanent_recovery import milled_permanent_recovery_node_capabilities, uses_milled_permanent_recovery

    if uses_milled_permanent_recovery(effect):
        return milled_permanent_recovery_node_capabilities(effect, target_schema)
    if "followup" in effect:
        from .milled_creature_land_to_hand import mill_hand_node_capabilities, uses_milled_creature_land_to_hand

        if uses_milled_creature_land_to_hand(effect):
            return mill_hand_node_capabilities(effects, target_schema)
        from .committed_mill_cards import committed_mill_card_node_capabilities, uses_committed_mill_result
        from .mill_paid_hand_return import mill_paid_hand_node_capabilities, uses_mill_paid_hand_return
        from .milled_creature_tokens import milled_creature_token_node_capabilities
        from .milled_permanent_battlefield import (
            milled_permanent_battlefield_node_capabilities, uses_milled_permanent_battlefield,
        )

        if uses_mill_paid_hand_return(effect):
            return mill_paid_hand_node_capabilities(effects, target_schema)
        if uses_milled_permanent_battlefield(effect):
            return milled_permanent_battlefield_node_capabilities(effects, target_schema)
        if uses_committed_mill_result(effect):
            return committed_mill_card_node_capabilities(effects, target_schema)
        return milled_creature_token_node_capabilities(effects, target_schema)
    count = effect.get("count")
    caps = (MILL_CAPABILITY_ID,)
    if isinstance(count, Mapping) and count.get("kind") == OPPONENT_HAND_MILL_KIND:
        if target_schema is not None or OPPONENT_HAND_MILL_MECHANIC not in mechanics:
            return ()
        try:
            return opponent_hand_mill_payload_dependencies(effect)
        except (TypeError, ValueError):
            return ()
    if HALF_MILL_MECHANIC in mechanics and isinstance(count, Mapping):
        try:
            HalfQuantitySpec.from_dict(count)
        except (TypeError, ValueError):
            return ()
        caps += (HALF_MILL_CAPABILITY,)
    elif type(count) is not int or count <= 0:
        return ()
    if set(effect) == {"op", "scope", "count"}:
        return caps if target_schema is None and effect.get("scope") in {
            "all_active_players", "controller_opponents",
        } else ()
    if set(effect) != {"op", "player", "count"}:
        return ()
    player = effect.get("player")
    if ATTACKER_CONTROLLER_MILL_MECHANIC in mechanics:
        if (target_schema is None and EVENT_MILL_RECIPIENT_MECHANIC in mechanics
                and type(count) is int and is_occurrence_controller_reference(player)):
            return (*caps, EVENT_MILL_RECIPIENT_CAPABILITY, TARGET_CONTROLLER_INFORMATION_CAPABILITY)
        return ()
    if target_schema is None and EVENT_MILL_RECIPIENT_MECHANIC in mechanics and isinstance(player, str) and player in {
            "$context.target", "$context.player", "$context.defending_player"}:
        return (*caps, EVENT_MILL_RECIPIENT_CAPABILITY)
    if target_schema is None and player == "$controller":
        return caps
    if "cr-115-targets" in mechanics and player == "$target.0" and _player_target_schema_is_closed(target_schema):
        return (*caps, "target.revalidate_resolution")
    return ()


def opponent_hand_mill_payload_dependencies(effect):
    count = effect.get("count")
    if effect.get("op") != "mill" or not isinstance(count, Mapping) or count.get("kind") != OPPONENT_HAND_MILL_KIND:
        return ()
    if set(effect) != {"op", "scope", "count"}:
        raise ValueError("Hand-count Mill cannot name or target individual players")
    OpponentHandMillCount.from_dict(count, scope=effect["scope"])
    return (MILL_CAPABILITY_ID, OPPONENT_HAND_MILL_CAPABILITY)


def half_mill_payload_dependencies(effect):
    """Require half-mill trust from its serialized amount and recipient."""
    count = effect.get("count")
    if effect.get("op") != "mill" or not isinstance(count, Mapping) or count.get("kind") != HALF_QUANTITY_KIND:
        return ()
    schema = {"zones": ["player"], "categories": ["player"], "count": 1} if effect.get("player") == "$target.0" else None
    owned = fixed_mill_node_capabilities(effects=(effect,), target_schema=schema,
        mechanic_ids=(MILL_MECHANIC_ID, HALF_MILL_MECHANIC, EVENT_MILL_RECIPIENT_MECHANIC, "cr-115-targets"))
    if HALF_MILL_CAPABILITY not in owned:
        raise ValueError("Malformed half-library Mill instruction")
    return owned


def validate_half_mill_program_scope(program):
    from .event_player_result_shapes import _event_player_reference_is_closed

    schema = program.target_schema
    modal = isinstance(schema, Mapping) and isinstance(schema.get("modes"), Mapping)
    pending = [program.effects, schema]
    while pending:
        value = pending.pop()
        if isinstance(value, Mapping):
            if value.get("players") != "$targets" and half_mill_payload_dependencies(value):
                player = value.get("player")
                if player == "$target.0" and not (modal or _player_target_schema_is_closed(schema)):
                    raise ValueError("Half-library Mill requires its closed player target")
                if isinstance(player, str) and player.startswith("$context.") and not _event_player_reference_is_closed(program, player):
                    raise ValueError("Half-library Mill requires a sealed event player")
            pending.extend(value.values())
        elif isinstance(value, (list, tuple)):
            pending.extend(value)


def attacker_controller_mill_program_dependencies(program):
    if equipped := equipped_wolf_mill_runtime_dependencies(program):
        return equipped
    from ..qualified_attack_model import QUALIFIED_ATTACK_CAPABILITY, defended_player_or_planeswalker_condition
    from ..replacement.immutable import thaw_value

    if ATTACKER_CONTROLLER_MILL_MECHANIC not in getattr(program, "coverage", ()):
        return ()
    owned = fixed_mill_node_capabilities(
        effects=program.effects, target_schema=program.target_schema, mechanic_ids=program.coverage,
    )
    if (
        not owned or (program.event, program.active_zone) != ("creature.attacks", "battlefield")
        or program.cost_schema is not None or program.handlers or "one_or_more_event_batch" in program.coverage
        or thaw_value(program.event_condition) != defended_player_or_planeswalker_condition()
    ):
        raise ValueError("Attacker-controller mill requires its exact declared defender and original occurrence")
    return (*owned, QUALIFIED_ATTACK_CAPABILITY, "trigger.event.normalized_public_action")


def mill_covered_mechanics(supplied):
    covered = {mechanic for cap,mechanic in ((MILL_CAPABILITY_ID,MILL_MECHANIC_ID),
        (EVENT_MILL_RECIPIENT_CAPABILITY,EVENT_MILL_RECIPIENT_MECHANIC),
        (HALF_MILL_CAPABILITY,HALF_MILL_MECHANIC),
        (OPPONENT_HAND_MILL_CAPABILITY,OPPONENT_HAND_MILL_MECHANIC)) if cap in supplied}
    if EVENT_MILL_RECIPIENT_CAPABILITY in supplied and MILL_CAPABILITY_ID in supplied:
        covered.add(EQUIPPED_WOLF_MILL_MECHANIC)
    if EVENT_MILL_RECIPIENT_CAPABILITY in supplied and TARGET_CONTROLLER_INFORMATION_CAPABILITY in supplied:
        covered.add(ATTACKER_CONTROLLER_MILL_MECHANIC)
    return covered


def committed_mill_followup_program_dependencies(program):
    from .milled_creature_land_to_hand import mill_hand_program_dependencies

    hand_dependencies = mill_hand_program_dependencies(program)
    if hand_dependencies:
        return hand_dependencies
    from .committed_mill_cards import committed_mill_card_program_dependencies
    from .mill_paid_hand_return import mill_paid_hand_program_dependencies
    from .milled_creature_tokens import milled_creature_token_program_dependencies
    from .milled_permanent_battlefield import milled_permanent_battlefield_program_dependencies

    return (
        mill_paid_hand_program_dependencies(program)
        or milled_permanent_battlefield_program_dependencies(program)
        or committed_mill_card_program_dependencies(program)
        or milled_creature_token_program_dependencies(program)
    )


__all__ = ["fixed_mill_node_capabilities","mill_covered_mechanics"]
