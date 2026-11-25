from __future__ import annotations

"""A single qualifying spell target and its untargeted token copy."""

from collections.abc import Mapping

from ..replacement.immutable import FrozenMap
from ..token_copy_recipes import TokenCopyExceptionSpec, TokenCopyRecipeSpec


CAST_TARGET_COPY_CAPABILITY = "token.creation.cast_target_permanent_copy"
CAST_TARGET_COPY_ORIGIN = "cast_target"
CAST_TARGET_COPY_CONDITION_FIELD = "sole_controlled_artifact_or_creature_cast_target"
CAST_TARGET_COPY_RECIPE_VERSION = 5

_PAYLOAD_DEPENDENCIES = (CAST_TARGET_COPY_CAPABILITY, "token.creation.fixed_copy")
_TRIGGER_DEPENDENCIES = (*_PAYLOAD_DEPENDENCIES, "trigger.event.normalized_spell_cast",
                         "trigger.effect.fixed_event", "trigger.placement.apnap")


def cast_target_copy_condition():
    return {"field": CAST_TARGET_COPY_CONDITION_FIELD, "op": "truthy", "value": True}


def cast_target_copy_effect():
    recipe = TokenCopyRecipeSpec(
        CAST_TARGET_COPY_ORIGIN, TokenCopyExceptionSpec(remove_legendary=True),
        schema_version=CAST_TARGET_COPY_RECIPE_VERSION,
    )
    return {"op": "create_token", "controller": "$controller", "quantity": 1, "copy_spec": recipe.to_dict()}


def represents_cast_target_copy(value):
    if not isinstance(value, Mapping):
        return False
    spec = value.get("copy_spec")
    return isinstance(spec, Mapping) and (
        spec.get("origin") == CAST_TARGET_COPY_ORIGIN
        or spec.get("schema_version") == CAST_TARGET_COPY_RECIPE_VERSION
    )


def cast_target_copy_payload_dependencies(value):
    if not represents_cast_target_copy(value):
        return ()
    TokenCopyRecipeSpec.from_dict(value["copy_spec"])
    if type(value.get("quantity")) is not int or FrozenMap(value) != FrozenMap(cast_target_copy_effect()):
        raise ValueError("Cast target copy creates exactly one nonlegendary token for the trigger controller")
    return _PAYLOAD_DEPENDENCIES


def _contains_cast_target_copy(value):
    if isinstance(value, Mapping):
        return represents_cast_target_copy(value) or any(_contains_cast_target_copy(row) for row in value.values())
    if isinstance(value, (tuple, list)):
        return any(_contains_cast_target_copy(row) for row in value)
    return False


def cast_target_copy_runtime_dependencies(program):
    if not _contains_cast_target_copy(program.effects):
        return ()
    from ..continuous_conditions import FIXED_PUBLIC_STATE_INTERVENING_COVERAGE

    if (
        len(program.effects) != 1 or not cast_target_copy_payload_dependencies(program.effects[0])
        or program.event != "spell.cast" or program.active_zone != "battlefield" or program.target_schema is not None
        or FrozenMap(program.event_condition or {}) != FrozenMap(cast_target_copy_condition())
        or {FIXED_PUBLIC_STATE_INTERVENING_COVERAGE, "intervening_condition"}.intersection(program.coverage)
    ):
        raise ValueError("Cast target copy requires its original untargeted cast trigger")
    return _TRIGGER_DEPENDENCIES


def _sole_announced_target(targets):
    if not isinstance(targets, (tuple, list)) or any(type(ref) is not str or not ref for ref in targets):
        return None
    distinct = set(targets)
    return next(iter(distinct)) if len(distinct) == 1 else None


def sole_cast_target(host, context):
    """Return the current original battlefield incarnation of a sole cast target."""
    caster = context.get("controller")
    if type(caster) is not str or not caster:
        return None
    ref = _sole_announced_target(context.get("targets"))
    if ref is None:
        return None
    spell = next((item for item in host.state.stack if item.ref == context.get("stack")), None)
    if spell is None or spell.kind not in {"spell", "spell_copy"} or spell.controller != caster:
        return None
    cast_card = host.state.cards.get(spell.card_object_id)
    if (
        cast_card is None or cast_card.zone != "stack" or cast_card.logical_object_id != context.get("logical_object_id")
        or tuple(spell.targets) != tuple(context["targets"])
    ):
        return None
    target = spell.context.get("target_snapshots", {}).get(ref)
    if not isinstance(target, Mapping) or target.get("zone") != "battlefield":
        return None
    card = host.state.cards.get(target.get("object_id"))
    if (
        card is None or card.zone != "battlefield" or card.phased_out
        or not host._target_identity_matches_snapshot(ref, target)
    ):
        return None
    return card


def controlled_cast_copy_target(host, context, *, controller):
    """Require the caster's sole original target to be an artifact or creature they control."""
    if context.get("controller") != controller:
        return None
    card = sole_cast_target(host, context)
    if card is None or card.controller != controller:
        return None
    types, _, _ = host._type_parts(str(host._effective_card_data(card).get("type_line") or ""))
    return card if {"artifact", "creature"}.intersection(types) else None


def cast_target_copy_condition_holds(host, condition, context, source):
    if condition.get("value") is not True or dict(condition) != cast_target_copy_condition():
        raise ValueError("Cast target copy requires its closed sole-target predicate")
    return controlled_cast_copy_target(host, context, controller=source.controller) is not None


def validate_cast_target_copy_reference(item, reference):
    # A later copy of the triggered ability can have a different controller.
    # The cast occurrence and its selected reference retain the original identity.
    ref = _sole_announced_target(item.context.get("targets"))
    if item.context.get("event") != "spell.cast" or item.targets or ref is None or not isinstance(reference, Mapping):
        raise ValueError("Cast target copy requires one original untargeted cast occurrence")
    object_id = reference.get("object_id")
    logical_id = reference.get("logical_object_id")
    if (
        reference.get("zone") != "battlefield" or reference.get("ref") != ref
        or type(object_id) is not str or not object_id or type(logical_id) is not str
    ):
        raise ValueError("Cast target copy lost its original battlefield reference")
    identity, _, incarnation = logical_id.rpartition("@")
    if identity != object_id or not incarnation.isdecimal():
        raise ValueError("Cast target copy requires the original object incarnation")
