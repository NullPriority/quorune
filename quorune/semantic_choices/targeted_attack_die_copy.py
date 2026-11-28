"""Continue one committed attack roll through token entry and the optional repeat."""

from dataclasses import dataclass

from ..replacement.immutable import FrozenMap
from ..rules.targeted_attack_die_copy import ROLL_CAPABILITY, ROLL_OPERATION, rolled_copy_effects
from .model import AutoContinue, SemanticChoiceError, SemanticChoicePreparation


@dataclass(frozen=True, slots=True)
class TargetedAttackDieCopyHandler:
    operation: str = ROLL_OPERATION
    handler_id: str = "choice.effect.targeted-attack-die-copy.v1"
    schema_version: int = 1
    rule_references: tuple[str, ...] = ("CR 508.4", "CR 608.2c", "CR 706.1", "CR 707.9")
    capability_dependencies: tuple[str, ...] = (ROLL_CAPABILITY,)
    continuation_fields: tuple[str, ...] = ("player", "copy_of", "copy_snapshot", "stack_ref",
                                          "instruction_pointer", "ordinal", "face")
    private_data: tuple[str, ...] = ()
    projected_fields: tuple[str, ...] = ()
    mutation_path: tuple[str, ...] = ("AutoContinue.prepend_effects",)
    replay_fixture: str = "targeted-attack-optional-d20-copy"
    test_modules: tuple[str, ...] = ("tests.test_single_d20_draw",)

    def prepare(self, effect, context):
        if effect.get("player") != context.actor or effect.get("stack_ref") != context.stack_ref:
            raise SemanticChoiceError("The die result must remain with its resolving controller.")
        try:
            effects = rolled_copy_effects(effect)
        except (TypeError, ValueError, KeyError) as exc:
            raise SemanticChoiceError(str(exc)) from exc
        return SemanticChoicePreparation(
            request=None, continuation_effect=FrozenMap(effect),
            auto_continue=AutoContinue("Carry out the rolled token result.", prepend_effects=effects),
        )

    def complete(self, continuation, response, query):
        raise SemanticChoiceError("A committed roll does not issue a separate response.")
