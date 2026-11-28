"""Commit a public reveal, then act on that exact top card."""

from dataclasses import dataclass

from ..replacement.immutable import FrozenMap
from ..rules.library_selection import LibrarySelectionArrangement, LibrarySelectionObjectIdentity
from ..rules.one_revealed_card_placement import (
    ENTER_REVEALED_TOP_OPERATION, OneRevealedCardPlacement, PLACE_REVEALED_TOP_OPERATION,
    REVEAL_ONE_TOP_CAPABILITIES, REVEAL_ONE_TOP_OPERATION,
)
from ..semantic_runtime.intents import LibrarySelectionIntent, RevealLibraryCardsIntent
from .fixed_library_selection import _current_library_top, _identity
from .model import (
    AutoContinue, ScalarChoice, SemanticChoiceCompletion, SemanticChoiceError,
    SemanticChoicePreparation, SemanticChoiceRequest,
)


def _unpack_revealed(effect, query, actor):
    payload = dict(effect)
    if payload.pop("op", None) != PLACE_REVEALED_TOP_OPERATION:
        raise SemanticChoiceError("A top-card placement requires its owned reveal result")
    try:
        identity = LibrarySelectionObjectIdentity.from_dict(payload.pop("_revealed_card"))
        reason = payload.pop("_stack_label")
        after_entry = payload.pop("_after_entry", False)
        spec = OneRevealedCardPlacement.from_effect({**payload, "op": REVEAL_ONE_TOP_OPERATION}, player=actor)
    except (KeyError, TypeError, ValueError) as exc:
        raise SemanticChoiceError("The public reveal result is malformed") from exc
    if type(reason) is not str or not reason or type(after_entry) is not bool or after_entry and not spec.entry_optional:
        raise SemanticChoiceError("The public reveal result is malformed")
    row = _current_library_top(query, actor)
    if row is None or _identity(row) != identity:
        if after_entry:
            return spec, None, identity, reason, after_entry
        raise SemanticChoiceError("The revealed top-card incarnation changed")
    return spec, row, identity, reason, after_entry


def _result_options(spec, row, *, after_entry, entry_possible):
    if after_entry or spec.entry_optional and not entry_possible:
        return ("put_on_bottom", "keep")
    if spec.matches(row.types):
        # Declining the optional permanent entry may still take its printed bottom result.
        return ("put_onto_battlefield", "put_on_bottom", "keep") if spec.entry_optional else ("put_onto_battlefield",)
    if spec.fallback == "hand":
        return ("put_into_hand",)
    return ("put_on_bottom", "keep") if spec.fallback == "optional_bottom" else ("put_into_graveyard",)


def _movement(choice, actor, stack_ref, row, identity, reason):
    if choice == "keep":
        return ()
    bottom = choice == "put_on_bottom"
    hand = choice == "put_into_hand"
    plan = LibrarySelectionArrangement(
        looked=(identity,),
        selected_refs=(row.ref,) if hand else (),
        remainder_refs=() if hand else (row.ref,),
        remainder_destination="library_bottom" if bottom or hand else "graveyard",
        remainder_order="chosen",
    )
    return (LibrarySelectionIntent(
        actor=actor, player=actor, arrangement=plan, reason=reason,
        source_stack_ref=stack_ref, looked_are_public=bottom, selected_are_public=hand,
    ),)


def _entry_effect(actor, identity):
    return FrozenMap({"op": ENTER_REVEALED_TOP_OPERATION, "player": actor, "revealed_card": identity.to_dict()})


def revealed_card_entry_choice_facts(host, actor, effect):
    if (
        effect.get("op") != PLACE_REVEALED_TOP_OPERATION
        or effect.get("entry_optional") is not True
        or effect.get("_after_entry") is True
    ):
        return {}
    library = host.state.players[actor].zones["library"]
    if not library:
        return {}
    from ..aura import legal_aura_target_refs
    from ..aura.runtime import is_aura_type_line
    from ..entry_counters import capture_prospective_entry_characteristics
    from ..errors import GameRuleError
    from ..replacement_effects import ReplacementChoiceRequired
    from ..semantic_runtime.zone_replacements import prepare_zone_change_replacement

    card = host.state.cards[library[-1]]
    characteristics, type_line = capture_prospective_entry_characteristics(host, card=card, enter_face=None)
    if not is_aura_type_line(type_line):
        return {}
    try:
        entry = prepare_zone_change_replacement(
            host, card, "battlefield", destination_controller=actor,
            entry_characteristics=characteristics, requested_tapped=False, error_type=GameRuleError,
        )
    except ReplacementChoiceRequired:
        return {}
    if entry.destination != "battlefield":
        return {}
    spec = host._compiled_enchant_spec(card)
    return {"revealed_card_entry_possible": spec is not None and bool(
        legal_aura_target_refs(host, card, spec, controller=actor, as_target=False)
    )}



@dataclass(frozen=True, slots=True)
class RevealedCardPlacementHandler:
    operation: str
    handler_id: str
    schema_version: int = 1
    rule_references: tuple[str, ...] = ("CR 110.4a", "CR 400.7", "CR 608.2c", "CR 608.2d", "CR 701.20a")
    capability_dependencies: tuple[str, ...] = REVEAL_ONE_TOP_CAPABILITIES
    continuation_fields: tuple[str, ...] = (
        "player", "schema_version", "required_type", "entry_optional", "fallback", "_revealed_card", "_stack_label", "_after_entry",
    )
    private_data: tuple[str, ...] = ("the controller's exact top card before the public reveal",)
    projected_fields: tuple[str, ...] = ("prompt", "objects", "legal_actions.choice_schema.legal_values")
    mutation_path: tuple[str, ...] = ("RevealLibraryCardsIntent", "LibrarySelectionIntent")
    replay_fixture: str = "public-top-card-placement"
    test_modules: tuple[str, ...] = ("tests.test_one_revealed_card_placement",)

    def prepare(self, effect, context):
        actor = context.actor
        if actor != context.stack_controller or actor not in context.query.active_seats:
            raise SemanticChoiceError("Revealed top-card placement belongs to the resolving controller")
        if self.operation == REVEAL_ONE_TOP_OPERATION:
            try:
                OneRevealedCardPlacement.from_effect(effect, player=actor)
            except (TypeError, ValueError) as exc:
                raise SemanticChoiceError(str(exc)) from exc
            row = _current_library_top(context.query, actor)
            if row is None:
                return SemanticChoicePreparation(
                    None, FrozenMap(effect), auto_continue=AutoContinue("The controller's library is empty."),
                )
            followup = FrozenMap({
                **effect, "op": PLACE_REVEALED_TOP_OPERATION, "_revealed_card": _identity(row).to_dict(),
                "_stack_label": context.stack_label,
            })
            return SemanticChoicePreparation(
                None, FrozenMap(effect),
                preparation_intents=(RevealLibraryCardsIntent(
                    actor, actor, actor, (row.ref,), context.stack_label, public=True, announce_public_top=True,
                ),),
                auto_continue=AutoContinue("Apply the printed result to the revealed card.", prepend_effects=(followup,)),
            )

        spec, row, identity, reason, after_entry = _unpack_revealed(effect, context.query, actor)
        if row is None:
            return SemanticChoicePreparation(
                None, FrozenMap(effect), auto_continue=AutoContinue("The revealed card left the library top."),
            )
        options = _result_options(
            spec, row, after_entry=after_entry, entry_possible=context.query.revealed_card_entry_possible,
        )
        if options == ("put_onto_battlefield",):
            return SemanticChoicePreparation(
                None, FrozenMap(effect),
                auto_continue=AutoContinue("Put the revealed card onto the battlefield.",
                                           prepend_effects=(_entry_effect(actor, identity),)),
            )
        if len(options) == 1:
            return SemanticChoicePreparation(
                None, FrozenMap(effect),
                preparation_intents=_movement(options[0], actor, context.stack_ref, row, identity, reason),
                auto_continue=AutoContinue("Apply the mandatory revealed-card result."),
            )
        return SemanticChoicePreparation(
            request=SemanticChoiceRequest(
                prompt="Choose what to do with the revealed top card.",
                choice=ScalarChoice("choice", options),
                public_context=FrozenMap({"stack": context.stack_ref, "objects": [{"id": row.ref, "name": row.printed_name}]}),
            ),
            continuation_effect=FrozenMap(effect),
        )

    def complete(self, continuation, response, query):
        actor = continuation.semantic_frame.controller
        if self.operation != PLACE_REVEALED_TOP_OPERATION or actor not in query.active_seats:
            raise SemanticChoiceError("A revealed-card response requires the original resolving choice")
        effect = continuation.effect
        spec, row, identity, reason, after_entry = _unpack_revealed(effect, query, actor)
        choice = response.get("choice")
        if row is None or choice not in _result_options(
            spec, row, after_entry=after_entry, entry_possible=query.revealed_card_entry_possible,
        ):
            raise SemanticChoiceError("Choose an offered result for the revealed card")
        if choice == "put_onto_battlefield":
            if not spec.entry_optional:
                raise SemanticChoiceError("Mandatory placement cannot be submitted as a choice")
            return SemanticChoiceCompletion(prepend_effects=(
                _entry_effect(actor, identity),
                FrozenMap({**effect, "_after_entry": True}),
            ))
        if choice in {"put_into_graveyard", "put_into_hand"}:
            raise SemanticChoiceError("Mandatory placement cannot be submitted as a choice")
        return SemanticChoiceCompletion(intents=_movement(choice, actor, continuation.stack_ref, row, identity, reason))


ONE_REVEALED_CARD_PLACEMENT_HANDLERS = (
    RevealedCardPlacementHandler(REVEAL_ONE_TOP_OPERATION, "choice.library.reveal-one-top-for-placement.v1"),
    RevealedCardPlacementHandler(PLACE_REVEALED_TOP_OPERATION, "choice.library.place-revealed-top.v1"),
)
