"""Bind a reflexive damage trigger to its applied static self-prevention component."""

from ..damage_modifier_state import DamagePreventionScope
from ..damage_source import DamageError
from ..prevention_triggers import (
    DealDamagePreventionTrigger,
    PREVENTION_OTHER_TARGET_FIELD as TARGET_FIELD,
    PreventionTriggeredAbility,
    prevention_trigger_stack_item,
)
from ..replacement.immutable import FrozenMap
from .damage_snapshots import source_snapshot


HANDLER = "prevention.damage.static-self-triggered-reflection.v1"
CAPABILITY = "damage.prevention.static_self_triggered_reflection"
DEPENDENCIES = (
    CAPABILITY, "damage.prevention.persistent_amount", "damage.prevention.triggered_results",
    "target.revalidate_resolution",
)


def static_prevention_trigger_descriptor():
    return {
        "handler_id": HANDLER, "schema_version": 1, "event": "damage",
        "condition": {"scopes": [{
            "damage_kind": "any", "source_controller_turn_only": False,
            "scope": DamagePreventionScope(target_ref="$source", target_kinds=("permanent",)).to_dict(),
        }]},
        "modification": {"amount": "all"},
    }


def other_damage_target_schema():
    return {
        "zones": ["player", "battlefield"], "categories": ["player", "permanent"],
        "predicate": "damageable", "count": 1, TARGET_FIELD: True,
    }


def applied_static_prevention_trigger(host, effect):
    if not effect.effect_id.startswith(HANDLER + ":"):
        return None
    from ..semantic_runtime.damage_replacements import collect_damage_replacement_effects

    source = next((card for card in host.state.cards.values() if card.ref == effect.source_id), None)
    if (source is None or source.zone != "battlefield" or source.phased_out
            or effect not in collect_damage_replacement_effects(host, sources=(source,))):
        raise DamageError("The applied static prevention lost its current trusted source component")
    snapshot = source_snapshot(host, source.ref, source_card=source, controller=source.controller)
    return PreventionTriggeredAbility(
        controller=source.controller, source=snapshot, label="When damage is prevented this way",
        results=(DealDamagePreventionTrigger(source=snapshot, recipient_kind="selected_target", per_prevented=1),),
        target_schema=FrozenMap(other_damage_target_schema()),
    )


def targeted_prevention_source_binding(occurrence):
    """Retain the original incarnation and protection facts for a scoped other target."""
    schema = occurrence.ability.target_schema
    if TARGET_FIELD not in schema:
        return {}
    from ..target_source import BOUND_TARGETING_SOURCE_VERSION, TARGETING_SOURCE_CONTEXT, TargetingSourceBinding

    if (not occurrence.effect_id.startswith(HANDLER + ":")
            or schema != FrozenMap(other_damage_target_schema()) or schema[TARGET_FIELD] is not True):
        raise DamageError("A static prevention trigger needs its complete other-target scope")
    source = occurrence.ability.source
    type_line = " ".join((*source.supertypes, *source.types))
    if source.subtypes:
        type_line += " — " + " ".join(source.subtypes)
    binding = TargetingSourceBinding(
        object_id=source.object_id, object_ref=source.ref, logical_object_id=source.logical_object_id,
        zone=source.zone, controller=source.controller, type_line=type_line, colors=source.colors,
        mana_value=source.mana_value, schema_version=BOUND_TARGETING_SOURCE_VERSION,
        is_commander=source.is_commander,
    )
    return {"source_logical_object_id": source.logical_object_id, TARGETING_SOURCE_CONTEXT: binding.to_dict()}


def materialize_prevention_trigger_stack_item(occurrence, *, ref, stack_id, visibility):
    return prevention_trigger_stack_item(
        occurrence, ref=ref, stack_id=stack_id, visibility=visibility,
        target_source_context=targeted_prevention_source_binding(occurrence),
    )
