"""Read the activated abilities of current creature cards in one public exile link."""

from ..continuous_effect_model import ContinuousObjectIdentity
from ..fixed_token_production import food_token_effect
from ..granted_activation_model import LinkedExileActivatedGrant
from ..linked_ability_exile_state import current_linked_ability_exile
from ..linked_exile_activation_model import (
    LINKED_ACTIVATION_BINDING, LINKED_ACTIVATION_PAIR, LINKED_EXILE_ACTIVATION_CAPABILITY,
    validate_grant_pair,
)
from ..linked_exile_return_model import validate_linked_ability_binding
from ..replacement.face_status_copy import current_copy_history
from ..replacement.immutable import thaw_value
from .activation.query import bind_creature_donor_activations
from .effect_shape_walkers import nested_effects


LINKED_ACTIVATION_TRIGGER_DEPENDENCIES = (
    LINKED_EXILE_ACTIVATION_CAPABILITY,
    "card.exile.public_graveyard", "resolution.effect.fixed_homogeneous_target_set",
    "target.revalidate_resolution", "token.creation.fixed_definition",
    "trigger.effect.fixed_event", "trigger.event.normalized_public_action",
    "trigger.event.normalized_zone_change", "trigger.placement.apnap", "trigger.subscription.fixed_multi_event",
    "zone.exile.linked_ability_pair",
)


def exile_target_schema():
    return {"zones": ["graveyard"], "categories": ["card"], "owner_relation": "any", "up_to": 1}


def _source_subscriptions():
    return {"event_subscriptions": [
        {"event": event, "condition": {"field": "card", "op": "eq", "value": "$source.ref"}}
        for event in ("permanent.enter", "creature.attacks")
    ]}


def graveyard_exile_and_food_effects(binding=None):
    exile = {"op": "exile_public_graveyard_targets", "cards": "$targets", "maximum_targets": 1}
    if binding is not None:
        validate_linked_ability_binding(binding)
        exile.update({LINKED_ACTIVATION_BINDING: binding, LINKED_ACTIVATION_PAIR: "$context." + LINKED_ACTIVATION_PAIR})
    return (exile, food_token_effect(1))


def source_pair(source, binding):
    validate_linked_ability_binding(binding)
    history = current_copy_history(source)
    if history.effective.values is None:
        return binding
    scope = history.ordered[-1][0].value if history.ordered else "permanent"
    return f"{binding}:{scope}:{history.effective.timestamp}"


def linked_exile_activation_trigger_context(source, program):
    if LINKED_EXILE_ACTIVATION_CAPABILITY not in program.capability_dependencies or not program.effects:
        return {}
    binding = program.effects[0].get(LINKED_ACTIVATION_BINDING)
    if binding is None:
        return {}
    pair = LinkedExileActivatedGrant(source.object_id, source.logical_object_id, source_pair(source, binding))
    return {LINKED_ACTIVATION_PAIR: pair.to_dict()}


def is_graveyard_exile_and_food_trigger(program, *, event_condition):
    return (program.event == "permanent.enter" and program.active_zone == "battlefield"
            and not program.handlers and getattr(program, "cost_schema", getattr(program, "cost", None)) is None
            and thaw_value(program.target_schema) == exile_target_schema()
            and thaw_value(event_condition) == _source_subscriptions())


def linked_exile_activation_runtime_dependencies(program):
    if not any(LINKED_ACTIVATION_PAIR in row or LINKED_ACTIVATION_BINDING in row
               for row in nested_effects((*program.effects, *program.handlers))):
        return ()
    effects = tuple(thaw_value(row) for row in program.effects)
    binding = effects[0].get(LINKED_ACTIVATION_BINDING) if effects else None
    if (binding is None or effects != graveyard_exile_and_food_effects(binding)
            or not is_graveyard_exile_and_food_trigger(program, event_condition=program.event_condition)):
        raise ValueError("Linked activation exile requires the complete entry-or-attack trigger")
    return LINKED_ACTIVATION_TRIGGER_DEPENDENCIES


def is_closed_linked_exile_activation_program(program):
    try:
        required = linked_exile_activation_runtime_dependencies(program)
    except (TypeError, ValueError):
        return False
    return bool(required) and set(required) <= set(program.capability_dependencies)


def linked_exile_activated_abilities(host, recipient, fragments):
    """Use the ordinary activation authority with the Food as its actual source."""
    grants = tuple(fragment for fragment in fragments if isinstance(fragment, LinkedExileActivatedGrant))
    if not grants or recipient.zone != "battlefield" or recipient.phased_out:
        return ()
    result = []
    for grant in grants:
        source = ContinuousObjectIdentity(grant.source_object_id, grant.source_logical_object_id)
        for exiled in current_linked_ability_exile(host, source=source, ability_pair=validate_grant_pair(grant.pair)):
            donor = host.state.cards[exiled.object_id]
            if donor.face_down:
                continue
            result.extend(bind_creature_donor_activations(
                host, donor, binding_identity=f'{grant.source_logical_object_id}:{grant.pair}',
                ability_prefix='linked-exile', first_line_index=30_000 + len(result),
            ))
    return tuple(result)
