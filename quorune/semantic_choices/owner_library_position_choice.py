"""Bind the live targeted permanent to its owner's choice of library position."""

from collections.abc import Mapping
from dataclasses import dataclass

from ..public_zone_moves import (
    FIXED_OWNER_ZONE_MOVE_CAPABILITY, OWNER_LIBRARY_POSITION_CAPABILITY, OWNER_LIBRARY_POSITION_OPERATION,
)
from ..replacement.immutable import FrozenMap
from ..target_controller_information import TARGET_OWNER_INFORMATION_CAPABILITY
from .model import (
    ScalarChoice, SemanticChoiceCompletion, SemanticChoiceError,
    SemanticChoicePreparation, SemanticChoiceRequest,
)


_INSTRUCTION_FIELDS = {"op", "schema_version", "card", "player"}
_BINDING_FIELDS = {"target_object_id", "target_logical_object_id", "controller"}


def _current_target(query, ref, owner):
    if type(ref) is not str or not ref or type(owner) is not str or not owner:
        raise SemanticChoiceError("Library position requires one target and its owner")
    card = query.object(ref, zones=("battlefield",))
    if (card is None or card.ref != ref or card.zone != "battlefield" or card.owner != owner
            or card.phased_out or not card.object_id or not card.logical_object_id):
        raise SemanticChoiceError("The selected library position lost its original target or owner")
    return card


@dataclass(frozen=True, slots=True)
class OwnerLibraryPositionChoiceHandler:
    operation: str = OWNER_LIBRARY_POSITION_OPERATION
    handler_id: str = "choice.zone.target-owner-library-position.v1"
    schema_version: int = 1
    rule_references: tuple[str, ...] = ("CR 109.5", "CR 400.3", "CR 608.2b", "CR 608.2c")
    capability_dependencies: tuple[str, ...] = (
        OWNER_LIBRARY_POSITION_CAPABILITY, FIXED_OWNER_ZONE_MOVE_CAPABILITY,
        TARGET_OWNER_INFORMATION_CAPABILITY, "target.revalidate_resolution",
    )
    continuation_fields: tuple[str, ...] = (
        "schema_version", "card", "player", "target_object_id", "target_logical_object_id", "controller",
    )
    private_data: tuple[str, ...] = ("target_object_id", "target_logical_object_id")
    projected_fields: tuple[str, ...] = ("prompt", "options", "legal_actions.choice_schema.legal_values")
    mutation_path: tuple[str, ...] = (
        "SemanticChoiceCompletion.prepend_effects", "CommanderEngine._continue_resolution", "ZoneTransitionOwner.move_card",
    )
    replay_fixture: str = "target-owner-library-position"
    test_modules: tuple[str, ...] = ("tests.test_owner_library_position_choice",)

    def prepare(self, effect, context):
        if (set(effect) != _INSTRUCTION_FIELDS or effect["op"] != self.operation
                or type(effect["schema_version"]) is not int or effect["schema_version"] != 1
                or effect["player"] != context.actor):
            raise SemanticChoiceError("Library position requires the targeted object's owner")
        card = _current_target(context.query, effect["card"], context.actor)
        return SemanticChoicePreparation(
            request=SemanticChoiceRequest(
                prompt="Choose where to put this permanent in your library.",
                choice=ScalarChoice("choice", ("top", "bottom")),
                public_context=FrozenMap({
                    "stack": context.stack_ref, "card": card.ref,
                    "options": [{"id": "top", "label": "On top"}, {"id": "bottom", "label": "On the bottom"}],
                }),
            ),
            continuation_effect=FrozenMap({
                **effect, "target_object_id": card.object_id,
                "target_logical_object_id": card.logical_object_id, "controller": context.stack_controller,
            }),
        )

    def complete(self, continuation, response, query):
        effect = continuation.effect
        if (set(effect) != _INSTRUCTION_FIELDS | _BINDING_FIELDS or effect["op"] != self.operation
                or type(effect["schema_version"]) is not int or effect["schema_version"] != 1
                or effect["controller"] != continuation.semantic_frame.controller
                or set(response) - {"action", "choice", "choice_schema"} or "choice" not in response
                or response.get("action", "choose") != "choose"):
            raise SemanticChoiceError("Library position response or continuation is malformed")
        choice = ScalarChoice("choice", ("top", "bottom"))
        if "choice_schema" in response and (
            not isinstance(response["choice_schema"], Mapping)
            or FrozenMap(response["choice_schema"]) != FrozenMap(choice.choice_schema())
        ):
            raise SemanticChoiceError("Library position response changed its issued choice schema")
        position = response["choice"]
        if type(position) is not str or position not in choice.legal_values:
            raise SemanticChoiceError("Choose the top or bottom of the target owner's library")
        card = _current_target(query, effect["card"], effect["player"])
        if (card.object_id, card.logical_object_id) != (effect["target_object_id"], effect["target_logical_object_id"]):
            raise SemanticChoiceError("The targeted permanent changed before its owner chose a position")
        return SemanticChoiceCompletion(prepend_effects=(
            FrozenMap({"op": "move", "card": card.ref, "destination": "library", "position": position}),
        ))
