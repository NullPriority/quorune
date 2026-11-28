"""Authorized sticker selection and live-controller word placement."""
from dataclasses import dataclass

from ..replacement.immutable import FrozenMap
from ..rules.name_stickers import CAPABILITY, NameSticker, NameStickerPlacementIntent, OPERATION
from ..semantic_runtime.intents import AddManaIntent
from .model import (AutoContinue, ScalarChoice, SemanticChoiceCompletion,
                    SemanticChoiceError, SemanticChoicePreparation, SemanticChoiceRequest)

_INITIAL = {"op", "schema_version", "player"}
_POSITION = _INITIAL | {"sticker", "source", "payer"}
_TRANSPORT = {"choice", "action", "action_id", "a", "cap", "reason", "plan", "next", "choice_schema", "label"}


def _mode(effect):
    if type(effect.get("schema_version")) is not int or effect.get("schema_version") != 1 or effect.get("op") != OPERATION:
        raise SemanticChoiceError("Unknown name sticker instruction")
    if set(effect) == _INITIAL:
        return "sticker"
    if set(effect) == _POSITION:
        return "position"
    raise SemanticChoiceError("Closed name sticker instruction fields changed")


@dataclass(frozen=True, slots=True)
class NameStickerChoiceHandler:
    operation: str = OPERATION
    handler_id: str = "choice.name-sticker.source-entry.v1"
    schema_version: int = 1
    rule_references: tuple[str, ...] = ("123.3", "123.3b", "123.6b", "123.6e", "608.2d")
    capability_dependencies: tuple[str, ...] = (CAPABILITY,)
    continuation_fields: tuple[str, ...] = tuple(sorted(_POSITION))
    private_data: tuple[str, ...] = ()
    projected_fields: tuple[str, ...] = ("prompt", "legal_actions.choice_schema.legal_values", "stickers")
    mutation_path: tuple[str, ...] = ("NameStickerPlacementIntent", "AddManaIntent")
    replay_fixture: str = "actual-goblin-optional-name-sticker-and-mana"
    test_modules: tuple[str, ...] = ("tests.test_source_name_sticker",)

    def prepare(self, effect, context):
        mode = _mode(effect)
        facts = context.query.name_sticker_facts
        saved = FrozenMap(effect)
        if effect["player"] != context.actor:
            raise SemanticChoiceError("Name sticker decision belongs to another seat")
        if mode == "sticker":
            if context.actor != context.stack_controller:
                raise SemanticChoiceError("Only the resolving controller may select a name sticker")
            if not facts.get("stickers") or facts["controller"] not in context.query.active_seats:
                return SemanticChoicePreparation(None, saved, auto_continue=AutoContinue("No source name sticker is available"))
            options = tuple(facts["stickers"]) + ("decline",)
            prompt = "Choose one available name sticker, or decline."
            public = {"stickers": {key: NameSticker.parse(key).text for key in facts["stickers"]}}
        else:
            if (not facts or facts["source"] != effect["source"] or facts["controller"] != context.actor
                    or effect["sticker"] not in facts["stickers"] or effect["payer"] != context.stack_controller):
                raise SemanticChoiceError("The sticker placement lost its original source, chooser, or slot")
            options = tuple(str(i) for i in range(facts["positions"]))
            prompt = "Choose where to put the name sticker."
            public = {"sticker": NameSticker.parse(effect["sticker"]).text, "current_name": facts["name"]}
        return SemanticChoicePreparation(
            SemanticChoiceRequest(prompt, ScalarChoice("choice", options),
                                  FrozenMap({"stack": context.stack_ref, "operation": OPERATION, **public})),
            saved,
        )

    def complete(self, continuation, response, query):
        effect = continuation.effect
        mode = _mode(effect)
        if set(response) - _TRANSPORT or type(response.get("choice")) is not str:
            raise SemanticChoiceError("Name sticker decisions accept one offered option")
        selected = response["choice"]
        facts = query.name_sticker_facts
        payer = continuation.semantic_frame.controller
        if mode == "sticker":
            if effect["player"] != payer:
                raise SemanticChoiceError("Only the resolving controller may select a name sticker")
            if selected == "decline":
                return SemanticChoiceCompletion()
            if not facts or selected not in facts["stickers"]:
                raise SemanticChoiceError("The chosen name sticker is not available")
            return SemanticChoiceCompletion(prepend_effects=(FrozenMap({
                **effect, "player": facts["controller"], "sticker": selected, "source": facts["source"], "payer": payer,
            }),))
        if (not facts or effect["source"] != facts["source"] or effect["player"] != facts["controller"]
                or effect["payer"] != payer or effect["sticker"] not in facts["stickers"]
                or selected not in tuple(str(i) for i in range(facts["positions"]))):
            raise SemanticChoiceError("Name sticker placement must use the current controller and offered position")
        sticker = NameSticker.parse(effect["sticker"])
        placement = NameStickerPlacementIntent(
            effect["player"], payer, continuation.stack_ref, facts["object_id"], facts["source"],
            sticker, int(selected),
        )
        mana = (AddManaIntent(payer, "R", sticker.red_mana, payer, "Mana from the name sticker"),) if sticker.red_mana else ()
        return SemanticChoiceCompletion(intents=(placement, *mana))
