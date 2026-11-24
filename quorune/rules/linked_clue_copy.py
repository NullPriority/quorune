"""Paired public graveyard exile and optional temporary copy receipts."""

from collections.abc import Mapping

from ..continuous_effect_model import ContinuousObjectIdentity
from ..fixed_token_production import clue_token_effect
from ..replacement.immutable import thaw_value
from .effect_shape_walkers import nested_effects
from .temporary_copy_subject import TEMPORARY_COPY_SUBJECT_OPERATION


LINKED_CLUE_COPY_VERSION = "linked_clue_copy_version"
LINKED_CLUE_COPY_CAPABILITY = "continuous.resolution.linked_clue_exile_copy"
LINKED_CLUE_COPY_PAIR = "attack-exile-clue-copy"
LINKED_CLUE_COPY_PAIR_CONTEXT = "linked_clue_copy_pair"
LINKED_CLUE_COPY_DEPENDENCIES = (
    LINKED_CLUE_COPY_CAPABILITY, "card.exile.public_graveyard", "effect.choice.optional_fixed",
    "keyword_action.investigate.fixed", "target.revalidate_resolution", "trigger.effect.fixed_event",
    "trigger.event.normalized_self_attack", "trigger.event.normalized_zone_change", "trigger.placement.apnap",
    "zone.exile.linked_ability_pair",
)


def linked_clue_attack_target():
    return {"zones": ["graveyard"], "categories": ["card"], "owner_relation": "any", "count": 1}


def linked_clue_attack_condition():
    return {"field": "card", "op": "eq", "value": "$source.ref"}


def linked_clue_sacrifice_condition():
    return {"all": [
        {"field": "previous_controller", "op": "eq", "value": "$source.controller"},
        {"field": "subtypes", "op": "contains_any", "value": ["clue"]},
    ]}


def linked_clue_attack_effects(*, linked=True):
    exile = {"op": "exile_public_graveyard_card", "card": "$target.0"}
    if linked:
        exile.update({LINKED_CLUE_COPY_VERSION: 1, "ability_pair": "$context." + LINKED_CLUE_COPY_PAIR_CONTEXT})
    return (exile, clue_token_effect(1))


def linked_clue_choice_effect():
    return {
        "op": TEMPORARY_COPY_SUBJECT_OPERATION, LINKED_CLUE_COPY_VERSION: 1,
        "player": "$controller", "ability_pair": "$context." + LINKED_CLUE_COPY_PAIR_CONTEXT,
    }


def has_linked_clue_copy_version(effect):
    return isinstance(effect, Mapping) and LINKED_CLUE_COPY_VERSION in effect


def require_linked_clue_copy_version(effect):
    version = effect.get(LINKED_CLUE_COPY_VERSION)
    if type(version) is not int or version != 1:
        raise ValueError("Linked exile copy requires its represented version")


def validate_linked_clue_pair(value):
    if value == LINKED_CLUE_COPY_PAIR:
        return value
    if type(value) is str:
        base, separator, suffix = value.partition(":")
        scope, delimiter, timestamp = suffix.partition(":")
        if (base == LINKED_CLUE_COPY_PAIR and separator and delimiter
                and scope in {"permanent", "end_of_turn", "face_down"}
                and timestamp.isascii() and timestamp.isdecimal()):
            return value
    raise ValueError("Linked exile copy requires its captured ability pair")


def linked_clue_copy_trigger_context(source, program):
    if LINKED_CLUE_COPY_CAPABILITY not in program.capability_dependencies:
        return {}
    from ..replacement.face_status_copy import current_copy_history

    history = current_copy_history(source)
    pair = LINKED_CLUE_COPY_PAIR
    if history.effective.values is not None:
        scope = history.ordered[-1][0].value if history.ordered else "permanent"
        pair = f"{pair}:{scope}:{history.effective.timestamp}"
    return {LINKED_CLUE_COPY_PAIR_CONTEXT: pair}


def linked_clue_copy_source_identity(source):
    if source is None:
        raise ValueError("Linked exile copy requires its original resolving source")
    return ContinuousObjectIdentity(source.object_id, source.logical_object_id)


def linked_clue_copy_runtime_dependencies(program):
    handlers = getattr(program, "handlers", ())
    if not any(has_linked_clue_copy_version(effect) for effect in nested_effects((*program.effects, *handlers))):
        return ()
    if getattr(program, "active_zone", None) != "battlefield" or program.cost_schema is not None or handlers:
        raise ValueError("Linked exile copy requires one complete battlefield trigger")
    effects = tuple(thaw_value(effect) for effect in program.effects)
    condition = thaw_value(program.event_condition)
    target = thaw_value(program.target_schema)
    if (program.event == "creature.attacks" and effects == linked_clue_attack_effects()
            and condition == linked_clue_attack_condition() and target == linked_clue_attack_target()):
        require_linked_clue_copy_version(effects[0])
        return LINKED_CLUE_COPY_DEPENDENCIES
    if (program.event == "permanent.sacrificed" and effects == (linked_clue_choice_effect(),)
            and condition == linked_clue_sacrifice_condition() and target is None):
        require_linked_clue_copy_version(effects[0])
        return LINKED_CLUE_COPY_DEPENDENCIES
    raise ValueError("Linked exile copy lost its exact event, instructions, or public target")


def is_closed_linked_clue_copy_program(program):
    try:
        required = linked_clue_copy_runtime_dependencies(program)
    except (TypeError, ValueError):
        return False
    return bool(required) and set(required).issubset(program.capability_dependencies)
