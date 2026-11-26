"""Lower a closed result recipe using existing fixed target validation."""

from ..rules.targeted_graveyard_outcomes import (
    TARGETED_GRAVEYARD_EXILE_OPERATION,
    TARGETED_GRAVEYARD_OUTCOME_FIELD,
    read_targeted_graveyard_outcome,
)
from .context import SemanticNodeError
from .intents import IntentPlan
from .targeted_graveyard_outcome_intent import TargetedGraveyardExileOutcomeIntent


def lower_targeted_graveyard_outcome(effect, context, *, handler_id: str) -> IntentPlan:
    from .fixed_target_set_handlers import _fixed_target_fields

    try:
        spec = read_targeted_graveyard_outcome(effect, resolved=True)
        extra = (TARGETED_GRAVEYARD_OUTCOME_FIELD, 'owner_seat') if spec.damage_linked else (TARGETED_GRAVEYARD_OUTCOME_FIELD,)
        cards, maximum, reason, selections = _fixed_target_fields(
            effect, context, operation=TARGETED_GRAVEYARD_EXILE_OPERATION,
            replacements=True, additional_fields=extra, variable_maximum=spec.damage_linked,
        )
        intent = TargetedGraveyardExileOutcomeIntent(
            actor=context.actor,
            object_refs=cards,
            expected_zones=('graveyard',),
            destination='exile',
            reason=reason,
            replacement_selections=selections,
            result=spec,
            damaged_player=effect['owner_seat'] if spec.damage_linked else None,
            damage_target_bound=maximum if spec.damage_linked else None,
        )
    except (TypeError, ValueError, KeyError) as exc:
        raise SemanticNodeError(str(exc)) from exc
    return IntentPlan(operation=TARGETED_GRAVEYARD_EXILE_OPERATION, handler_id=handler_id, intents=(intent,))
