"""Bind the optional public graveyard target to an actual exile incarnation."""

from ..continuous_effect_model import ContinuousObjectIdentity
from ..granted_activation_model import LinkedExileActivatedGrant
from ..linked_exile_activation_model import LINKED_ACTIVATION_BINDING, LINKED_ACTIVATION_PAIR, validate_grant_pair
from ..linked_exile_return_model import validate_linked_ability_binding
from .context import SemanticNodeError
from .fixed_target_set_handlers import _fixed_target_fields, _target_set_plan
from .zone_move_intent import ExilePublicGraveyardCardIntent


def lower_linked_graveyard_exile(handler, effect, context):
    cards, maximum, reason, selections = _fixed_target_fields(
        effect, context, operation=handler.operation, replacements=True,
        additional_fields=(LINKED_ACTIVATION_BINDING, LINKED_ACTIVATION_PAIR),
    )
    try:
        binding = effect.get(LINKED_ACTIVATION_BINDING)
        validate_linked_ability_binding(binding)
        captured = LinkedExileActivatedGrant.from_dict(effect.get(LINKED_ACTIVATION_PAIR))
        validate_grant_pair(captured.pair)
        if (maximum != 1 or "announced_target_count" in effect or context.source is None
                or (captured.pair != binding and not captured.pair.startswith(binding + ":"))):
            raise ValueError("Linked graveyard exile requires its one optional target and captured pair")
        source = ContinuousObjectIdentity(captured.source_object_id, captured.source_logical_object_id)
        if context.source.object_id is not None and source != ContinuousObjectIdentity(
            context.source.object_id, context.source.logical_object_id,
        ):
            raise ValueError("Linked graveyard exile has a different source incarnation")
    except (TypeError, ValueError) as exc:
        raise SemanticNodeError(str(exc)) from exc
    intent = (ExilePublicGraveyardCardIntent(
        actor=context.actor, object_ref=cards[0], reason=reason, replacement_selections=selections,
        linked_source=source, linked_pair=captured.pair,
    ) if cards else None)
    return _target_set_plan(handler.operation, handler.handler_id, cards, intent)
