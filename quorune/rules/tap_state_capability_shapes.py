from __future__ import annotations

"""Closed CardProgram capability shapes for single-object tap state."""

from typing import Iterable, Mapping, Sequence

from ..attachment_references import (
    AttachmentReferenceError,
    AttachmentReferenceSpec,
)
from ..compiler.fixed_source_effect_sequences import SOURCE_ZONE_OBJECT
from ..damage_recipient_model import DAMAGE_PERMANENT_CAPABILITY, DAMAGE_PERMANENT_OBJECT
from ..event_counter_reference import BLOCK_COUNTERPART_OBJECT
from ..source_tap_effect import SOURCE_TAP_UNTAP_CAPABILITY, split_source_tap_untap_restraint
from ..tap_state import (
    ATTACKING_CREATURES_NEXT_UNTAP_FIELD, CONTROLLER_UNTAP_STEP_COUNT, TAP_TARGET_SET_UNTAP_FIELD,
    two_controller_untaps_effect, validate_attacking_creatures_next_untap,
)
from ..next_player_untap_model import (
    decode_next_player_untap_instruction, is_next_player_untap_instruction,
    next_player_untap_target_schema,
)
from .effect_shape_walkers import nested_effects
from .permanent_predicate_capability_shapes import (
    direct_permanent_target_schema_is_closed,
    direct_target_predicate_capabilities,
)


def _operation_capability(value: object) -> str | None:
    return {
        "tap": "permanent.tap.effect",
        "untap": "permanent.untap.effect",
        "prohibit_next_untap": "untap.step.prohibit_next_controller",
    }.get(value)


def target_set_next_untap_dependencies(values: object) -> tuple[str, ...]:
    if isinstance(values, Mapping):
        if TAP_TARGET_SET_UNTAP_FIELD in values:
            if values.get("op") != "tap_targets" or values[TAP_TARGET_SET_UNTAP_FIELD] is not True:
                raise ValueError("A next-untap instruction requires a tap target set")
            return ("untap.step.prohibit_next_controller",)
        values = tuple(values.values())
    if isinstance(values, (list, tuple)):
        return tuple(dict.fromkeys(cap for value in values for cap in target_set_next_untap_dependencies(value)))
    return ()


def event_next_untap_node_capabilities(*, effects, target_schema, mechanic_ids):
    if (target_schema is not None or len(effects) != 2 or not isinstance(effects[0], Mapping)
            or 'tap-and-untap' not in mechanic_ids):
        return ()
    reference = effects[0].get('card')
    if reference == DAMAGE_PERMANENT_OBJECT:
        binding = DAMAGE_PERMANENT_CAPABILITY
    elif reference == BLOCK_COUNTERPART_OBJECT:
        binding = 'trigger.event.block_pair_counterpart'
    else:
        return ()
    if tuple(effects) != ({'op': 'tap', 'card': reference}, {'op': 'prohibit_next_untap', 'card': reference}):
        return ()
    return ('permanent.tap.effect', 'untap.step.prohibit_next_controller', binding)


def _next_player_untap_capabilities(effect, target_schema, mechanics):
    try:
        instruction = decode_next_player_untap_instruction(effect)
    except (TypeError, ValueError, KeyError):
        return ()
    if (not {"tap-and-untap", "cr-115-targets"} <= mechanics or instruction.player != "$target.0"
            or not isinstance(target_schema, Mapping) or type(target_schema.get("count")) is not int
            or target_schema != next_player_untap_target_schema(instruction.scope)):
        return ()
    return ("untap.step.prohibit_next_controller", "target.revalidate_resolution")


def _combat_prevention_attackers_untap_capabilities(effects, target_schema, mechanics):
    if (target_schema is not None or len(effects) != 2
            or not {"tap-and-untap", "cr-615-prevention-effects"} <= mechanics):
        return ()
    try:
        validate_attacking_creatures_next_untap(effects[1])
    except (TypeError, ValueError):
        return ()
    if effects[0] != {
        "op": "create_damage_prevention_shield", "source": "$source", "subject": "*",
        "mode": "all", "duration": "until_end_of_turn", "damage_kind": "combat",
    }:
        return ()
    return ("damage.prevention.persistent_amount", "untap.step.prohibit_next_controller")


def _target_combat_prevention_two_untaps_capabilities(effects, target_schema, mechanics):
    from ..compiler.direct_target import direct_permanent_target_spec

    expected_target = direct_permanent_target_spec("target creature").to_target_schema()
    if (len(effects) != 3 or target_schema != expected_target or type(target_schema["count"]) is not int
            or not {"tap-and-untap", "cr-115-targets", "cr-615-prevention-effects"} <= mechanics):
        return ()
    from ..replacement.immutable import thaw_value
    from .prevention_component_shapes import targeted_outgoing_combat_shield

    tap, prevention, restriction = effects
    if (tap != {"op": "tap", "card": "$target.0"}
            or thaw_value(prevention) != targeted_outgoing_combat_shield()
            or restriction != two_controller_untaps_effect()
            or type(restriction["schema_version"]) is not int
            or type(restriction[CONTROLLER_UNTAP_STEP_COUNT]) is not int):
        return ()
    return ("permanent.tap.effect", "damage.prevention.persistent_amount",
            "untap.step.prohibit_next_controller", "target.revalidate_resolution")


def untap_runtime_dependencies(program):
    from ..affected_permanents import TARGET_UNSPENT_MANA_FIELD

    required = target_set_next_untap_dependencies(program.effects)
    if any(CONTROLLER_UNTAP_STEP_COUNT in effect for effect in nested_effects((program.effects, program.handlers))):
        result = _target_combat_prevention_two_untaps_capabilities(
            program.effects, program.target_schema, set(program.coverage),
        )
        if (not result or (program.event, program.active_zone) != ("resolve", "stack") or program.handlers
                or program.cost_schema is not None or program.event_condition is not None):
            raise ValueError("Two controller untaps require the complete original targeted spell")
        return result
    if any(ATTACKING_CREATURES_NEXT_UNTAP_FIELD in effect
           for effect in nested_effects((program.effects, program.handlers))):
        if ((program.event, program.active_zone) != ("resolve", "stack") or program.handlers
                or program.cost_schema is not None or program.event_condition is not None):
            raise ValueError("Attacking-creature untap restriction requires the original resolving spell")
        attacker_result = _combat_prevention_attackers_untap_capabilities(
            program.effects, program.target_schema, set(program.coverage),
        )
        if not attacker_result:
            raise ValueError("Attacking-creature untap requires the ordered combat prevention and current attacking set")
        return attacker_result
    if any(TARGET_UNSPENT_MANA_FIELD in effect for effect in nested_effects((program.effects, program.handlers))):
        from .public_zone_move_capability_shapes import fixed_public_tap_state_set_node_capabilities

        if ((program.event, program.active_zone) != ("resolve", "stack") or program.handlers
                or program.cost_schema is not None or program.event_condition is not None
                or len(program.effects) != 1 or TARGET_UNSPENT_MANA_FIELD not in program.effects[0]):
            raise ValueError("Land tap and unspent mana loss require a single exact resolving spell")
        mana_result = fixed_public_tap_state_set_node_capabilities(
            effects=program.effects, target_schema=program.target_schema, mechanic_ids=program.coverage,
        )
        if not mana_result:
            raise ValueError("Land tap and unspent mana loss require the original player target and complete result")
        return mana_result
    if not any(is_next_player_untap_instruction(effect) for effect in nested_effects(program.effects)):
        return required
    next_step = (targeted_tap_state_node_capabilities(effects=program.effects,
        target_schema=program.target_schema, mechanic_ids=program.coverage)
        if len(program.effects) == 1 and is_next_player_untap_instruction(program.effects[0]) else ())
    if not next_step or (program.event, program.active_zone) != ("resolve", "stack"):
        raise ValueError("A next-player untap restriction requires the exact resolving spell and player target")
    return tuple(sorted({*required, *next_step}))


def targeted_tap_state_node_capabilities(
    *,
    effects: Sequence[Mapping[str, object]],
    target_schema: Mapping[str, object] | None,
    mechanic_ids: Iterable[str],
) -> tuple[str, ...]:
    """Return ownership only for closed single-object tap-state shapes."""

    mechanics = {str(value).casefold() for value in mechanic_ids}
    if len(effects) == 3:
        return _target_combat_prevention_two_untaps_capabilities(effects, target_schema, mechanics)
    if target_schema is None and len(effects) == 2:
        if isinstance(effects[1], Mapping) and ATTACKING_CREATURES_NEXT_UNTAP_FIELD in effects[1]:
            return _combat_prevention_attackers_untap_capabilities(effects, target_schema, mechanics)
        return event_next_untap_node_capabilities(effects=effects, target_schema=target_schema, mechanic_ids=mechanics)
    if 'tap-and-untap' in mechanics and len(effects) == 2 and target_schema is not None:
        if (list(effects) != [{'op': 'tap', 'card': '$target.0'}, {'op': 'prohibit_next_untap', 'card': '$target.0'}]
                or 'cr-115-targets' not in mechanics or not direct_permanent_target_schema_is_closed(target_schema)):
            return ()
        return ('permanent.tap.effect', 'untap.step.prohibit_next_controller',
                *direct_target_predicate_capabilities(target_schema), 'target.revalidate_resolution')
    if "tap-and-untap" not in mechanics or len(effects) != 1:
        return ()
    if is_next_player_untap_instruction(effects[0]):
        return _next_player_untap_capabilities(effects[0], target_schema, mechanics)
    try:
        effect, restraint = split_source_tap_untap_restraint(effects[0])
    except ValueError:
        return ()
    if restraint and target_schema is None:
        return ()
    operation = effect.get("op")

    if target_schema is None:
        capability = _operation_capability(operation)
        if capability is None or set(effect) != {"op", "card"}:
            return ()
        reference = effect.get("card")
        if reference == SOURCE_ZONE_OBJECT:
            return (capability,)
        if not isinstance(reference, Mapping):
            return ()
        try:
            AttachmentReferenceSpec.from_dict(reference)
        except (AttachmentReferenceError, TypeError):
            return ()
        return (capability, "attachment.reference.current_or_lki")

    if (
        "cr-115-targets" not in mechanics
        or not direct_permanent_target_schema_is_closed(target_schema)
    ):
        return ()
    target_capabilities = direct_target_predicate_capabilities(target_schema)
    capability = _operation_capability(operation)
    if (
        capability is not None
        and set(effect) == {"op", "card"}
        and effect.get("card") == "$target.0"
    ):
        return (
            capability,
            *target_capabilities,
            "target.revalidate_resolution",
            *((SOURCE_TAP_UNTAP_CAPABILITY,) if restraint else ()),
        )

    if set(effect) != {
        "op",
        "player",
        "prompt",
        "options",
        "then_by_choice",
    } or operation != "choose_option":
        return ()
    options = effect.get("options")
    then_by_choice = effect.get("then_by_choice")
    choice_rows = (
        {key: list(value) for key, value in then_by_choice.items()}
        if isinstance(then_by_choice, Mapping)
        and all(
            isinstance(value, (list, tuple))
            for value in then_by_choice.values()
        )
        else None
    )
    if (
        effect.get("player") != "$controller"
        or effect.get("prompt")
        != "Tap, untap, or leave the target unchanged."
        or not isinstance(options, (list, tuple))
        or list(options)
        != [
            {"id": "tap", "label": "Tap"},
            {"id": "untap", "label": "Untap"},
            {"id": "decline", "label": "Leave unchanged"},
        ]
        or choice_rows is None
        or set(choice_rows) != {"tap", "untap", "decline"}
        or choice_rows["tap"]
        != [{"op": "tap", "card": "$target.0"}]
        or choice_rows["untap"]
        != [{"op": "untap", "card": "$target.0"}]
        or choice_rows["decline"] != []
    ):
        return ()
    return (
        "permanent.tap.effect",
        "permanent.tap_state.optional_choice",
        "permanent.untap.effect",
        *target_capabilities,
        "target.revalidate_resolution",
    )


__all__ = ["event_next_untap_node_capabilities", "targeted_tap_state_node_capabilities", "target_set_next_untap_dependencies"]
