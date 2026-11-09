from __future__ import annotations

"""A target-pinned cast-or-decline choice during the original resolution."""

from collections.abc import Mapping
from dataclasses import dataclass

from ..cast_timing import type_line_has_card_type
from ..errors import GameRuleError, StateInvariantError
from ..replacement.immutable import FrozenMap, thaw_value
from ..rules.casting.proposal import build_cast_offer
from ..rules.resolving_cast_payments import commit_resolving_cast
from ..selection.target_query import target_identity_matches_snapshot
from ..targeted_graveyard_cast_model import (
    GRAVEYARD_CAST_CAPABILITY, GRAVEYARD_CAST_FIELDS, GRAVEYARD_CAST_OPERATION,
    GraveyardCastIntent, graveyard_cast_options_fingerprint, validate_graveyard_cast_effect,
)
from .model import (
    AutoContinue, CastOrDeclineChoice, SemanticChoiceCompletion, SemanticChoiceError,
    SemanticChoicePreparation, SemanticChoiceRequest,
)


_CONTINUATION_FIELDS = ("_logical_object_id", "_options_fingerprint")


def _instruction(effect):
    return {key: value for key, value in effect.items() if key not in _CONTINUATION_FIELDS}


def _live_target(host, *, item, actor, effect):
    validate_graveyard_cast_effect(effect, resolved=True)
    ref = effect["card"]
    if (item is None or item.controller != actor or effect["player"] != actor
            or actor not in host.active_seats or not item.context.get("currently_resolving")
            or tuple(item.targets) != (ref,)):
        raise GameRuleError("The graveyard cast lost its resolving target or controller")
    snapshot = item.context.get("target_snapshots", {}).get(ref, {})
    if not isinstance(snapshot, Mapping) or type(snapshot.get("zone_change_counter")) is not int:
        raise GameRuleError("The graveyard cast lacks the announced target incarnation")
    card = host.state.cards.get(snapshot.get("object_id"))
    if (card is None or card.zone != "graveyard" or not card.is_card_object or card.face_down
            or card.owner != snapshot.get("owner") or card.owner not in host.active_seats
            or effect["owner_relation"] == "opponent" and card.owner == actor
            or not target_identity_matches_snapshot(host, ref, snapshot)):
        raise GameRuleError("The graveyard cast target incarnation changed")
    program = host.semantics.get(item.semantic_key)
    if (program is None or not host.semantic_program_is_current_trusted(program)
            or not any(row == {**effect, "card": "$target.0", "player": "$controller"} for row in program.effects)):
        raise GameRuleError("The current program does not authorize this graveyard cast")
    return card


def _instant_or_sorcery(type_line):
    return any(type_line_has_card_type(type_line, card_type) for card_type in ("instant", "sorcery"))


def _option_pairs(host, *, actor, card):
    record = host.card_record(card)
    if record is None:
        return ()
    options = []
    for face in host._cast_faces(record, required_face=None):
        face_name = face.get("name") if face else None
        if not _instant_or_sorcery(face.get("type_line", "") if face else record.type_line):
            continue
        result = build_cast_offer(host, actor, card, face_name=face_name,
                                  resolving_payment="free", resolving_origin="graveyard")
        if result.offer is None:
            continue
        offered = result.offer.to_dict(include_fingerprint=False)
        for price in offered["cost_options"]:
            canonical = {**price, "face": offered.get("face")}
            public_id = "graveyard-cast:" + graveyard_cast_options_fingerprint((canonical,))
            options.append(({**canonical, "id": public_id}, canonical))
    return tuple(options)


def targeted_graveyard_cast_options(host, *, item, actor, effect):
    card = _live_target(host, item=item, actor=actor, effect=_instruction(effect))
    return tuple(public for public, _ in _option_pairs(host, actor=actor, card=card))


def execute_targeted_graveyard_cast(host, intent):
    actor = intent.effect["player"]
    item = next((row for row in host.state.stack if row.ref == intent.stack_ref), None)
    card = _live_target(host, item=item, actor=actor, effect=thaw_value(intent.effect))
    if card.logical_object_id != intent.logical_object_id:
        raise GameRuleError("The exact card selected for the graveyard cast changed")
    pairs = _option_pairs(host, actor=actor, card=card)
    if graveyard_cast_options_fingerprint(public for public, _ in pairs) != intent.options_fingerprint:
        raise GameRuleError("The current graveyard cast options changed")
    response = thaw_value(intent.response)
    selected, face = response.get("cost_option"), response.get("face")
    matches = [option for public, option in pairs
               if (selected is None or selected == public["id"]) and (face is None or face == public["face"])]
    if (len(matches) != 1 or response.get("cast_method") is not None
            or response.get("card", card.ref) != card.ref or response.get("from", "graveyard") != "graveyard"
            or "x" in response and (type(response["x"]) is not int or response["x"] != 0)):
        raise GameRuleError("Select one current graveyard cast option with X equal to zero")
    option = matches[0]
    response.update(card=card.ref, cost_option=option["id"], auto_pay=True)
    response["from"] = "graveyard"
    if option["face"] is not None:
        response["face"] = option["face"]
    commit_resolving_cast(host, actor=actor, response=response, origin="graveyard",
                          payment_id=f"{item.ref}:targeted-graveyard:{intent.logical_object_id}",
                          replacement_selections=intent.replacement_selections)
    spell = host.state.cards[card.object_id]
    if (spell.zone != "stack" or spell.controller != actor or spell.logical_object_id == intent.logical_object_id
            or not any(row.kind == "spell" and row.card_object_id == spell.object_id for row in host.state.stack)):
        raise StateInvariantError("The graveyard cast created no spell")
    if intent.effect["exile_on_graveyard"]:
        from ..rules.cast_grant_riders import CastGrantRiderBinding
        from ..zone_object_state import mark_cast_grant_rider

        mark_cast_grant_rider(spell, CastGrantRiderBinding(
            "graveyard_exile", spell.logical_object_id, actor, item.ref, owner=spell.owner,
        ))


@dataclass(frozen=True, slots=True)
class TargetedGraveyardCastChoiceHandler:
    operation: str = GRAVEYARD_CAST_OPERATION
    handler_id: str = "choice.cast.targeted-graveyard.v1"
    schema_version: int = 1
    rule_references: tuple[str, ...] = ("CR 400.7", "CR 601.2", "CR 608.2b", "CR 608.2g")
    capability_dependencies: tuple[str, ...] = (GRAVEYARD_CAST_CAPABILITY,)
    continuation_fields: tuple[str, ...] = ("player", "card", "owner_relation", "exile_on_graveyard", *_CONTINUATION_FIELDS)
    private_data: tuple[str, ...] = ()
    projected_fields: tuple[str, ...] = ("prompt", "card", "cast_options", "legal_actions.choice_schema")
    mutation_path: tuple[str, ...] = ("GraveyardCastIntent", "commit_resolving_cast")
    replay_fixture: str = "targeted-graveyard-cast"
    test_modules: tuple[str, ...] = ("tests.test_targeted_graveyard_cast",)

    def prepare(self, effect, context):
        validate_graveyard_cast_effect(effect, resolved=True)
        if effect["player"] != context.actor or context.actor != context.stack_controller:
            raise SemanticChoiceError("The graveyard choice lost its resolving controller")
        options = tuple(context.query.authorized_cast_options())
        if not options:
            return SemanticChoicePreparation(None, FrozenMap(effect), auto_continue=AutoContinue(
                reason="The graveyard target has no legal cast option",
            ))
        card = context.query.object(effect["card"], zones=("graveyard",))
        if card is None or not card.logical_object_id:
            raise SemanticChoiceError("The graveyard choice lost its target")
        return SemanticChoicePreparation(
            request=SemanticChoiceRequest(
                prompt="Cast the targeted card without paying its mana cost?",
                choice=CastOrDeclineChoice(options), public_context=FrozenMap({
                    "stack": context.stack_ref, "operation": self.operation, "card": card.ref, "cast_options": options,
                }),
            ),
            continuation_effect=FrozenMap({**effect, "_logical_object_id": card.logical_object_id,
                                            "_options_fingerprint": graveyard_cast_options_fingerprint(options)}),
        )

    def complete(self, continuation, response, query):
        effect = continuation.effect
        if set(effect) != GRAVEYARD_CAST_FIELDS.union(_CONTINUATION_FIELDS):
            raise SemanticChoiceError("The graveyard cast continuation fields changed")
        options = tuple(query.authorized_cast_options())
        if graveyard_cast_options_fingerprint(options) != effect["_options_fingerprint"]:
            raise SemanticChoiceError("The graveyard cast options changed")
        choice = response.get("choice")
        if choice == "decline":
            return SemanticChoiceCompletion()
        cast = response.get("cast", {})
        if choice != "cast" or not options or not isinstance(cast, Mapping):
            raise SemanticChoiceError("Select a current graveyard cast action")
        submitted = {**cast, **{key: value for key, value in response.items() if key not in {"action", "cast", "choice"}}}
        try:
            intent = GraveyardCastIntent(continuation.stack_ref, FrozenMap(_instruction(effect)), effect["_logical_object_id"],
                                        effect["_options_fingerprint"], FrozenMap(submitted))
        except ValueError as exc:
            raise SemanticChoiceError(str(exc)) from exc
        return SemanticChoiceCompletion(intents=(intent,))
