from __future__ import annotations

"""Select a represented result without a new choice or mutation path."""

from dataclasses import dataclass
from typing import Any, Mapping

from ..cast_origin_replacement import CAST_REPLACEMENT_VERSIONS, MAIN_PHASE_CAST_PREPARATION_VERSION
from ..ability_resolution_ordinal import is_ability_resolution_ordinal, validate_ability_resolution_ordinal
from ..other_spell_history_model import is_other_spell_draw, validate_other_spell_draw
from ..rules.entrant_power_branch import (
    ENTRANT_POWER_BRANCH_FIELD, resolved_entrant_power_branch, uses_entrant_power_branch,
)
from ..replacement.immutable import FrozenMap
from ..paid_sacrifice_conditions import PAID_SACRIFICE_CONDITION_VERSION
from ..resolution_conditions import (
    RESOLUTION_CONDITION_CAPABILITY,
    RESOLUTION_CONDITION_OPERATION,
    validate_resolution_condition_instruction,
)
from .context import SemanticChoiceContext, SemanticChoiceQuery
from .model import (
    AutoContinue, SemanticChoiceCompletion, SemanticChoiceContinuation,
    SemanticChoiceError, SemanticChoicePreparation,
)


def _prepare_controlled_artifact_token_branch(effect, context):
    from ..rules.controlled_artifact_token_result import controlled_artifact_token_effects

    try:
        effects = controlled_artifact_token_effects(effect, actor=context.actor, stack_controller=context.stack_controller)
    except (TypeError, ValueError) as exc:
        raise SemanticChoiceError(str(exc)) from exc
    return SemanticChoicePreparation(
        request=None, continuation_effect=FrozenMap(effect),
        auto_continue=AutoContinue("Compare the original artifact's controller.", prepend_effects=effects),
    )


@dataclass(frozen=True, slots=True)
class PublicResolutionConditionHandler:
    operation: str = RESOLUTION_CONDITION_OPERATION
    handler_id: str = "choice.effect.public-resolution-condition.v1"
    schema_version: int = 1
    rule_references: tuple[str, ...] = ("CR 109.5", "CR 608.2c", "CR 608.2h")
    capability_dependencies: tuple[str, ...] = (RESOLUTION_CONDITION_CAPABILITY,)
    continuation_fields: tuple[str, ...] = ("player", "condition", "effects", "mechanic_ids", "prefix_mechanic_ids", 'schema_version', 'cast_fact','otherwise_effects','otherwise_mechanic_ids','target_binding','source_binding','condition_target', 'resolution_ordinal',
        ENTRANT_POWER_BRANCH_FIELD, 'minimum', 'counter_count', 'power', 'entrant', 'source',
        'named_cast_win', 'win', 'card',
        'point_game_loss', 'target', 'remove_from', 'count')
    private_data: tuple[str, ...] = ()
    projected_fields: tuple[str, ...] = ()
    mutation_path: tuple[str, ...] = (
        "AutoContinue.prepend_effects", "CommanderEngine._continue_resolution",
    )
    replay_fixture: str = "fixed-resolution-public-condition"
    test_modules: tuple[str, ...] = ("tests.test_resolution_public_conditions",)

    def prepare(self, effect: Mapping[str, Any], context: SemanticChoiceContext) -> SemanticChoicePreparation:
        from ..rules.named_cast_win import uses_named_cast_win
        if uses_named_cast_win(effect):
            return _prepare_named_cast_win(effect, context)
        from ..rules.controlled_artifact_token_result import uses_controlled_artifact_token_branch
        if uses_controlled_artifact_token_branch(effect):
            return _prepare_controlled_artifact_token_branch(effect, context)
        if uses_entrant_power_branch(effect):
            try:
                result = resolved_entrant_power_branch(effect, context.actor, context.stack_controller)
            except (KeyError, TypeError, ValueError) as exc:
                raise SemanticChoiceError(str(exc)) from exc
            return SemanticChoicePreparation(
                request=None, continuation_effect=FrozenMap(effect),
                auto_continue=AutoContinue("Compare the original entrant's power.", prepend_effects=result),
            )
        from ..rules.combat_point_counters import uses_point_game_loss
        if uses_point_game_loss(effect):
            return _prepare_point_game_loss(effect, context)
        if is_other_spell_draw(effect):
            return _prepare_other_spell_draw(effect, context)
        if is_ability_resolution_ordinal(effect):
            return _prepare_ability_resolution_ordinal(effect, context)
        if effect.get('schema_version') == 18:
            from .paid_alternative_cast import prepare_paid_alternative_result
            return prepare_paid_alternative_result(effect,context)
        if effect.get('schema_version') == 17:
            from .target_replacements import prepare_current_target_replacement
            return prepare_current_target_replacement(effect, context)
        if effect.get('schema_version') == 16:
            from .conditional_target_preparation import prepare_conditional_target
            return prepare_conditional_target(effect, context)
        if effect.get('schema_version') == 12:
            from .declared_cost_conditions import prepare_declared_cost_condition
            return prepare_declared_cost_condition(effect, context)
        try:
            condition, effects = validate_resolution_condition_instruction(effect)
        except (ValueError, TypeError, KeyError) as exc:
            raise SemanticChoiceError(str(exc)) from exc
        if effect["player"] != context.actor or context.actor != context.stack_controller:
            raise SemanticChoiceError("Resolution conditions require the resolving controller")
        if effect.get('schema_version') in {10,11}:
            if type(effect['cast_fact']) is not bool:raise SemanticChoiceError('Kicked target decision is unavailable')
            branch=effects if effect['cast_fact'] else effect.get('otherwise_effects',())
            return SemanticChoicePreparation(request=None,continuation_effect=FrozenMap(effect),
                auto_continue=AutoContinue(reason='sealed Kicker target branch',
                    prepend_effects=tuple(FrozenMap(child) for child in branch)))
        otherwise=effect.get('otherwise_effects',())
        if effect.get("schema_version") == MAIN_PHASE_CAST_PREPARATION_VERSION:
            effects = tuple(child for child in effects if _available_preparation_instruction(child))
            otherwise = tuple(child for child in otherwise if _available_preparation_instruction(child))
        from .resolution_result_validation import validate_resolution_result_children
        validate_resolution_result_children(effect, effects, otherwise, context)
        try:
            version = effect.get("schema_version")
            if version in {6, 7}:
                matched = effect["cast_fact"] >= condition.minimum
            elif version in {2, 4, 5, PAID_SACRIFICE_CONDITION_VERSION, *CAST_REPLACEMENT_VERSIONS}:
                matched = effect["cast_fact"]
            else:
                matched = context.query.resolution_condition_matches(condition.to_dict())
            if type(matched) is not bool:
                raise ValueError('Resolution cast fact is unavailable')
        except (ValueError, TypeError, KeyError) as exc:
            raise SemanticChoiceError(str(exc)) from exc
        return SemanticChoicePreparation(
            request=None,
            continuation_effect=FrozenMap(effect),
            auto_continue=AutoContinue(
                reason="public resolution condition matched" if matched else "public resolution condition did not match",
                prepend_effects=tuple(FrozenMap(child) for child in (effects if matched else otherwise)),
            ),
        )

    def complete(self, continuation: SemanticChoiceContinuation, response: Mapping[str, Any], query: SemanticChoiceQuery) -> SemanticChoiceCompletion:
        raise SemanticChoiceError("Public resolution conditions issue no player choice")


def _available_preparation_instruction(effect):
    if effect["op"] == "creature_power_damage":
        return effect["source"] is not None and effect["target"] is not None
    if effect["op"] == "modify_stats_until_end_of_turn":
        return effect["card"] is not None
    return True


def _prepare_other_spell_draw(effect, context):
    try:
        results = validate_other_spell_draw(effect, bound=True)
    except (TypeError, ValueError) as exc:
        raise SemanticChoiceError(str(exc)) from exc
    if effect["player"] != context.actor or context.actor != context.stack_controller:
        raise SemanticChoiceError("An other-spell draw requires its resolving controller")
    return SemanticChoicePreparation(
        request=None, continuation_effect=FrozenMap(effect),
        auto_continue=AutoContinue(
            reason="Check the committed other-spell cast.",
            prepend_effects=tuple(FrozenMap(child) for child in results) if effect["cast_fact"] else (),
        ),
    )


def _prepare_ability_resolution_ordinal(effect, context):
    try:
        children = validate_ability_resolution_ordinal(effect, bound=True)
    except (TypeError, ValueError) as exc:
        raise SemanticChoiceError(str(exc)) from exc
    if effect["player"] != context.actor or context.actor != context.stack_controller:
        raise SemanticChoiceError("An ability-resolution result requires its resolving controller")
    return SemanticChoicePreparation(
        request=None, continuation_effect=FrozenMap(effect),
        auto_continue=AutoContinue(
            reason="Check the committed resolution ordinal.",
            prepend_effects=tuple(FrozenMap(child) for child in children)
            if effect["resolution_ordinal"] == effect["condition"]["ordinal"] else (),
        ),
    )


def _prepare_named_cast_win(effect, context):
    from ..rules.named_cast_win import named_cast_win_bound_results

    try:
        effects = named_cast_win_bound_results(effect, actor=context.actor, stack_controller=context.stack_controller)
    except (TypeError, ValueError) as exc:
        raise SemanticChoiceError(str(exc)) from exc
    return SemanticChoicePreparation(
        request=None, continuation_effect=FrozenMap(effect),
        auto_continue=AutoContinue("Check the committed named casts.", prepend_effects=effects),
    )


def _prepare_point_game_loss(effect, context):
    from ..rules.combat_point_counters import point_game_loss_bound_results

    try:
        results = point_game_loss_bound_results(effect, actor=context.actor, stack_controller=context.stack_controller)
    except (KeyError, TypeError, ValueError) as exc:
        raise SemanticChoiceError(str(exc)) from exc
    return SemanticChoicePreparation(
        request=None, continuation_effect=FrozenMap(effect),
        auto_continue=AutoContinue("Check the original permanent's point counters.", prepend_effects=results),
    )


PUBLIC_RESOLUTION_CONDITION_HANDLERS = (PublicResolutionConditionHandler(),)
