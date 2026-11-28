"""Let the original targeted opponent choose a public set of creatures."""

from dataclasses import dataclass

from ..chosen_attack_turn_model import CAPABILITY, CHOOSE, COMMIT
from ..continuous_effect_model import ContinuousObjectIdentity
from ..replacement.immutable import FrozenMap
from .model import (
    AutoContinue, ObjectChoice, SemanticChoiceCompletion, SemanticChoiceError, SemanticChoicePreparation,
    SemanticChoiceRequest,
)


def _candidates(effect, query, stack_ref, controller):
    item = query.stack_object(stack_ref)
    player = effect.get("player")
    if (effect.get("op") != CHOOSE or type(effect.get("schema_version")) is not int
            or effect["schema_version"] != 1 or player == controller or player not in query.active_seats
            or item is None or item.kind != "activated_ability" or item.controller != controller
            or item.targets != (player,)):
        raise SemanticChoiceError("Chosen attackers require their one original targeted opponent")
    objects = sorted(query.objects(zones=("battlefield",), controller=player), key=lambda row: row.ref)
    return {
        row.ref: ContinuousObjectIdentity(row.object_id, row.logical_object_id).to_dict()
        for row in objects if "creature" in row.types
    }


def _commit(effect, stack_ref, selected):
    return FrozenMap({"op": COMMIT, "stack_ref": stack_ref, "player": effect["player"], "selected": selected})


@dataclass(frozen=True, slots=True)
class ChosenAttackTurnHandler:
    operation: str = CHOOSE
    handler_id: str = "choice.effect.chosen_attack_turn.v1"
    schema_version: int = 1
    capability_dependencies: tuple[str, ...] = (CAPABILITY,)
    rule_references: tuple[str, ...] = ("400.7", "508.1c", "508.1d", "608.2d")
    continuation_fields: tuple[str, ...] = ("schema_version", "player", "creatures")
    private_data: tuple[str, ...] = ()
    projected_fields: tuple[str, ...] = ()
    mutation_path: tuple[str, ...] = ("apply_chosen_attack_turn",)
    replay_fixture: str = "chosen-attack-turn"
    test_modules: tuple[str, ...] = ("tests.test_resolved_attack_requirements",)

    def prepare(self, effect, context):
        if set(effect) != {"op", "schema_version", "player"} or effect.get("player") != context.actor:
            raise SemanticChoiceError("The chosen-attacker selection changed chooser or scope")
        choices = _candidates(effect, context.query, context.stack_ref, context.stack_controller)
        if not choices:
            return SemanticChoicePreparation(
                request=None, continuation_effect=FrozenMap(effect),
                auto_continue=AutoContinue("The targeted opponent has no creatures to select.",
                                           prepend_effects=(_commit(effect, context.stack_ref, ()),)),
            )
        return SemanticChoicePreparation(
            request=SemanticChoiceRequest(
                prompt="Choose any number of creatures you control to attack next turn.",
                choice=ObjectChoice(field_name="cards", legal_refs=tuple(choices), zones=("battlefield",),
                                    minimum=0, maximum=len(choices), optional=True, controller_relation="actor"),
            ),
            continuation_effect=FrozenMap({**effect, "creatures": choices}),
        )

    def complete(self, continuation, response, query):
        effect = continuation.effect
        if set(effect) != {"op", "schema_version", "player", "creatures"}:
            raise SemanticChoiceError("The chosen-attacker continuation changed its fields")
        choices = _candidates(effect, query, continuation.stack_ref, continuation.semantic_frame.controller)
        refs = response.get("cards")
        if (FrozenMap(choices) != effect["creatures"] or not isinstance(refs, (list, tuple))
                or any(type(ref) is not str or ref not in choices for ref in refs)
                or len(refs) != len(set(refs))):
            raise SemanticChoiceError("Choose a distinct subset of the originally available creatures")
        selected = [identity for ref, identity in choices.items() if ref in refs]
        return SemanticChoiceCompletion(prepend_effects=(_commit(effect, continuation.stack_ref, selected),))
