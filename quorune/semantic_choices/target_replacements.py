"""Choose exactly one replacement branch from the immutable current query."""

from ..replacement.immutable import FrozenMap
from ..rules.target_replacements import current_target_replacement_branch_capabilities, current_target_replacement_matches
from ..target_replacement_model import validate_current_target_replacement
from .model import AutoContinue, SemanticChoiceError, SemanticChoicePreparation


def prepare_current_target_replacement(effect, context):
    try:
        condition, effects = validate_current_target_replacement(effect)
        if effect["player"] != context.actor or context.actor != context.stack_controller:
            raise ValueError("Current target replacement requires its resolving controller")
        subject = effect["condition_target"]
        for branch, key in (("effects", "mechanic_ids"), ("otherwise_effects", "otherwise_mechanic_ids")):
            if not current_target_replacement_branch_capabilities(effect[branch], effect[key],
                    effect["target_binding"], subject=subject):
                raise ValueError("Current target replacement lacks a represented result owner")
        row = context.query.object(subject, zones=("battlefield",)) if subject is not None else None
        if row is None or row.phased_out:
            branch = ()
        else:
            matched = (context.query.resolution_condition_matches(condition.to_dict())
                       if condition.scope == "night" else current_target_replacement_matches(condition, row))
            branch = effects if matched else effect["otherwise_effects"]
        return SemanticChoicePreparation(request=None, continuation_effect=FrozenMap(effect),
            auto_continue=AutoContinue(reason="current target self-replacement",
                                      prepend_effects=tuple(FrozenMap(child) for child in branch)))
    except (TypeError, ValueError, KeyError) as exc:
        raise SemanticChoiceError(str(exc)) from exc
