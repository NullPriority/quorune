from __future__ import annotations

from ..apnap_object_payment import DECLINED_LIFE_LOSS, apnap_object_payment_dependencies

"""Capability shapes for fixed affected-player hand choices."""

from typing import Any, Iterable, Mapping, Sequence

from ..library_order_model import (
    AFFECTED_HAND_TOP_CAPABILITY, AFFECTED_HAND_TOP_MECHANIC, HAND_TOP_APNAP_RESULT,
)

from ..compiler.affected_player_discard_templates import (
    FIXED_AFFECTED_PLAYER_DISCARD_CAPABILITY,
    FIXED_AFFECTED_PLAYER_DISCARD_MECHANIC,
    FIXED_AFFECTED_PLAYER_HAND_EXILE_CAPABILITY,
    FIXED_AFFECTED_PLAYER_HAND_EXILE_MECHANIC,
    fixed_affected_player_discard_predicate_is_closed,
    FIXED_CONTROLLER_DISCARD_MECHANIC,
    FIXED_CONTROLLER_DISCARD_CAPABILITY,
    FIXED_CONTEXT_PLAYER_DISCARD_MECHANIC,
    FIXED_CONTEXT_PLAYER_DISCARD_CAPABILITY,
    EQUIPPED_DAMAGE_DISCARD_UNTAP_MECHANIC,
)


def _literal_hand_choice_count_is_closed(value, controller):
    return type(value) is int and value in ({1, 2, 3, 4} if controller else {1, 2, 3})


def _fixed_affected_player_hand_choice_node_capabilities(
    *,
    effects: Sequence[Mapping[str, Any]],
    target_schema: Mapping[str, Any] | None,
    mechanic_ids: Iterable[str],
    allow_controller: bool,
    action: str,
    mechanic: str,
    capability: str,
) -> tuple[str, ...]:
    mechanics = {str(value).casefold() for value in mechanic_ids}
    if not {
        mechanic,
        "cr-402-hand",
    }.issubset(mechanics) or len(effects) != 1:
        return ()
    effect = effects[0]
    try:
        payment_dependencies = apnap_object_payment_dependencies(effect)
    except ValueError:
        return ()
    targeted = effect.get("players") == ["$target.0"]
    controller = effect.get("players") == ["$controller"]
    if controller and not allow_controller:
        return ()
    expected_fields = {
        "op",
        "actor",
        "players",
        "zone",
        "predicate",
        "count",
        "then",
        "hidden",
        "prompt",
        *((DECLINED_LIFE_LOSS,) if payment_dependencies else ()),
        *(("target",) if targeted else ()),
    }
    if (
        set(effect) != expected_fields
        or effect.get("op") != "choose_cards_apnap"
        or effect.get("actor") != "$controller"
        or effect.get("zone") != "hand"
        or not _literal_hand_choice_count_is_closed(effect.get("count"), controller)
        or effect.get("then") != action
        or effect.get("hidden") is not True
        or type(effect.get("prompt")) is not str
        or not str(effect.get("prompt")).strip()
        or not isinstance(effect.get("predicate"), Mapping)
        or not fixed_affected_player_discard_predicate_is_closed(
            effect["predicate"]
        )
    ):
        return ()
    dependencies = {
        capability,
        "zone.change.destination_replacement",
        *payment_dependencies,
    }
    if targeted:
        if (
            effect.get("target") != "$target.0"
            or "cr-115-targets" not in mechanics
            or dict(target_schema or {})
            not in (
                {
                    "zones": ["player"],
                    "categories": ["player"],
                    "player_relation": "any",
                    "count": 1,
                },
                {
                    "zones": ["player"],
                    "categories": ["player"],
                    "player_relation": "opponent",
                    "count": 1,
                },
            )
        ):
            return ()
        dependencies.add("target.revalidate_resolution")
    elif target_schema is not None:
        return ()
    elif controller and effect.get("players") != ["$controller"]:
        return ()
    elif not controller and effect.get("players") not in ("all", "opponents"):
        return ()
    return tuple(sorted(dependencies))


def fixed_affected_player_discard_node_capabilities(
    *, effects, target_schema, mechanic_ids, allow_controller: bool = False,
) -> tuple[str, ...]:
    return _fixed_affected_player_hand_choice_node_capabilities(
        effects=effects, target_schema=target_schema, mechanic_ids=mechanic_ids,
        allow_controller=allow_controller, action="discard",
        mechanic=FIXED_AFFECTED_PLAYER_DISCARD_MECHANIC, capability=FIXED_AFFECTED_PLAYER_DISCARD_CAPABILITY,
    )


def fixed_affected_player_hand_exile_node_capabilities(
    *, effects, target_schema, mechanic_ids,
) -> tuple[str, ...]:
    return _fixed_affected_player_hand_choice_node_capabilities(
        effects=effects, target_schema=target_schema, mechanic_ids=mechanic_ids,
        allow_controller=False, action="exile",
        mechanic=FIXED_AFFECTED_PLAYER_HAND_EXILE_MECHANIC, capability=FIXED_AFFECTED_PLAYER_HAND_EXILE_CAPABILITY,
    )


def fixed_affected_player_hand_top_node_capabilities(*, effects, target_schema, mechanic_ids):
    return _fixed_affected_player_hand_choice_node_capabilities(
        effects=effects, target_schema=target_schema, mechanic_ids=mechanic_ids,
        allow_controller=False, action=HAND_TOP_APNAP_RESULT,
        mechanic=AFFECTED_HAND_TOP_MECHANIC, capability=AFFECTED_HAND_TOP_CAPABILITY,
    )


def fixed_hand_top_apnap_payload_dependencies(effect: Mapping[str, Any]) -> tuple[str, ...]:
    target_schema = (
        {"zones": ["player"], "categories": ["player"], "player_relation": "any", "count": 1}
        if effect.get("target") == "$target.0" else None
    )
    required = fixed_affected_player_hand_top_node_capabilities(
        effects=(effect,), target_schema=target_schema,
        mechanic_ids=(AFFECTED_HAND_TOP_MECHANIC, "cr-402-hand", "cr-115-targets"),
    )
    if not required:
        raise ValueError("Hand-to-top order requires a closed affected-player instruction")
    return required


def fixed_hand_exile_payload_dependencies(effect: Mapping[str, Any]) -> tuple[str, ...]:
    """Infer a typed hand-exile minimum even without a coverage annotation."""
    from ..linked_exile_return_model import LINKED_FADING_HAND_BINDING

    if LINKED_FADING_HAND_BINDING in effect:
        from .linked_exile_return_shapes import linked_fading_hand_payload_dependencies

        required = linked_fading_hand_payload_dependencies(effect)
        if not required:
            raise ValueError("Linked hand exile lacks its closed affected-opponent choice")
        return required
    target_schema = (
        {"zones": ["player"], "categories": ["player"], "player_relation": "any", "count": 1}
        if effect.get("target") == "$target.0" else None
    )
    required = fixed_affected_player_hand_exile_node_capabilities(
        effects=(effect,), target_schema=target_schema,
        mechanic_ids=(FIXED_AFFECTED_PLAYER_HAND_EXILE_MECHANIC, "cr-402-hand", "cr-115-targets"),
    )
    if not required:
        raise ValueError("Typed hand exile lacks a closed affected-player instruction")
    return required


def fixed_controller_discard_node_capabilities(*, effects, target_schema, mechanic_ids):
    if FIXED_CONTROLLER_DISCARD_MECHANIC not in set(mechanic_ids) or len(effects) != 1:
        return ()
    effect = effects[0]
    if effect.get("players") != ["$controller"] or type(effect.get("count")) is not int:
        return ()
    required = fixed_affected_player_discard_node_capabilities(
        effects=effects, target_schema=target_schema,
        mechanic_ids=mechanic_ids, allow_controller=True,
    )
    return tuple(sorted({*required, FIXED_CONTROLLER_DISCARD_CAPABILITY})) if required else ()


EQUIPPED_DAMAGE_DISCARD_PLAYER_FIELD = "equipped_damage_discard_player_version"


def bind_equipped_damage_discard_player(state, raw_effect, effect):
    if EQUIPPED_DAMAGE_DISCARD_PLAYER_FIELD not in raw_effect:
        return effect
    version = raw_effect[EQUIPPED_DAMAGE_DISCARD_PLAYER_FIELD]
    players = effect.get("players")
    if (type(version) is not int or version != 1 or effect.get("op") != "choose_cards_apnap"
            or tuple(raw_effect.get("players", ())) != ("$context.target",)
            or not isinstance(players, (list, tuple)) or len(players) != 1
            or type(players[0]) is not str or players[0] not in state.players):
        raise ValueError("Equipped combat discard requires the exact damaged player")
    if players[0] not in state.active_seats():
        return None
    return {key: value for key, value in effect.items() if key != EQUIPPED_DAMAGE_DISCARD_PLAYER_FIELD}


def _equipped_damage_discard_untap_node_capabilities(effects, target_schema, mechanics):
    from ..affected_permanents import AffectedPermanentSetSpec, PermanentControllerRelation
    from ..object_predicate import ObjectQuerySpec
    from ..replacement.immutable import FrozenMap
    from .public_zone_move_capability_shapes import fixed_public_tap_state_set_node_capabilities

    if len(effects) != 2 or target_schema is not None:
        return ()
    discard, untap = effects
    version = discard.get(EQUIPPED_DAMAGE_DISCARD_PLAYER_FIELD)
    if (discard.get("players") != ["$context.target"] or discard.get("count") != 1
            or untap.get("tapped") is not False or type(version) is not int or version != 1):
        return ()
    expected = {"op": "set_public_tap_state", "source": "$source", "tapped": False,
                "set": AffectedPermanentSetSpec(
                    query=ObjectQuerySpec(zones=("battlefield",), types_all=("land",)),
                    controller_relation=PermanentControllerRelation.ACTOR,
                ).to_dict()}
    if FrozenMap(untap) != FrozenMap(expected):
        return ()
    first = fixed_context_player_discard_node_capabilities(
        effects=({key: value for key, value in discard.items() if key != EQUIPPED_DAMAGE_DISCARD_PLAYER_FIELD},),
        target_schema=None, mechanic_ids=mechanics - {EQUIPPED_DAMAGE_DISCARD_UNTAP_MECHANIC},
    )
    last = fixed_public_tap_state_set_node_capabilities(
        effects=(untap,), target_schema=None, mechanic_ids=mechanics,
    )
    return tuple(sorted({*first, *last})) if first and last else ()


def equipped_damage_discard_untap_runtime_dependencies(program):
    from ..replacement.immutable import FrozenMap
    from .effect_shape_walkers import nested_effects

    mechanics = set(program.coverage)
    marked = EQUIPPED_DAMAGE_DISCARD_UNTAP_MECHANIC in mechanics
    uses_player = any(EQUIPPED_DAMAGE_DISCARD_PLAYER_FIELD in effect
                      for effect in nested_effects((program.effects, program.handlers)))
    if not marked and not uses_player:
        return ()
    if not marked:
        raise ValueError("Equipped combat discard requires its complete two-result scope")
    expected_condition = {"all": [
        {"field": "source_attachment_target_ref", "op": "eq", "value": "$context.source"},
        {"field": "target_kind", "op": "eq", "value": "player"},
        {"field": "combat", "op": "truthy", "value": True},
    ]}
    required = _equipped_damage_discard_untap_node_capabilities(program.effects, program.target_schema, mechanics)
    if (not required or (program.event, program.active_zone) != ("damage.dealt", "battlefield")
            or FrozenMap(program.event_condition or {}) != FrozenMap(expected_condition)
            or program.cost_schema is not None or program.handlers):
        raise ValueError("The two results require the exact original equipped combat-damage occurrence")
    return (*required, "attachment.reference.current_or_lki", "trigger.event.normalized_damage",
            "trigger.effect.fixed_event", "trigger.placement.apnap")


def fixed_context_player_discard_node_capabilities(*, effects, target_schema, mechanic_ids):
    mechanics = set(mechanic_ids)
    if EQUIPPED_DAMAGE_DISCARD_UNTAP_MECHANIC in mechanics:
        return _equipped_damage_discard_untap_node_capabilities(effects, target_schema, mechanics)
    if FIXED_CONTEXT_PLAYER_DISCARD_MECHANIC not in mechanics or len(effects) != 1 or target_schema is not None:
        return ()
    effect = effects[0]
    if effect.get("players") not in (["$context.player"], ["$context.target"],["$context.defending_player"]) or type(effect.get("count")) is not int or effect.get("count") not in {1, 2, 3}:
        return ()
    restored = {**effect, "players": ["$controller"]}
    required = fixed_affected_player_discard_node_capabilities(
        effects=(restored,), target_schema=None,
        mechanic_ids=mechanics, allow_controller=True,
    )
    return tuple(sorted({*required, FIXED_CONTEXT_PLAYER_DISCARD_CAPABILITY})) if required else ()


def is_closed_fixed_bound_discard_program(program):
    """Admit only a closed producer whose occurrence defines its player slot."""
    if EQUIPPED_DAMAGE_DISCARD_UNTAP_MECHANIC in program.coverage:
        try:
            equipped = equipped_damage_discard_untap_runtime_dependencies(program)
        except (TypeError, ValueError):
            return False
        return bool(equipped) and set(equipped).issubset(program.capability_dependencies)
    arguments = dict(effects=program.effects, target_schema=program.target_schema,
        mechanic_ids=program.coverage)
    required = fixed_controller_discard_node_capabilities(**arguments)
    if required:
        return set(required).issubset(program.capability_dependencies)
    required = fixed_context_player_discard_node_capabilities(**arguments)
    if not required or not set(required).issubset(program.capability_dependencies):
        return False

    def guarantees(condition, field, value):
        if not isinstance(condition, Mapping):
            return False
        if condition == {"field": field, "op": "eq", "value": value}:
            return True
        children = condition.get("all", ())
        return isinstance(children, (list, tuple)) and any(guarantees(child, field, value) for child in children)

    players = program.effects[0]["players"]
    if players==['$context.defending_player']:
        from .event_player_result_shapes import _event_player_reference_is_closed
        return _event_player_reference_is_closed(program,'$context.defending_player')
    if players == ["$context.target"]:
        return program.event in {"damage.dealt", "damage.dealt.self"} and guarantees(program.event_condition, "target_kind", "player")
    return program.event == "step.begin" and guarantees(program.event_condition, "step", "upkeep")


def is_closed_fixed_affected_player_discard_program(program):
    required=fixed_affected_player_discard_node_capabilities(effects=program.effects,
        target_schema=program.target_schema,mechanic_ids=program.coverage)
    return bool(required) and set(required).issubset(program.capability_dependencies)


def is_closed_fixed_affected_player_hand_exile_program(program):
    required = fixed_affected_player_hand_exile_node_capabilities(
        effects=program.effects, target_schema=program.target_schema, mechanic_ids=program.coverage,
    )
    return bool(required) and set(required).issubset(program.capability_dependencies)


__all__ = ["fixed_affected_player_discard_node_capabilities", "fixed_controller_discard_node_capabilities", "fixed_context_player_discard_node_capabilities", "is_closed_fixed_bound_discard_program"]
