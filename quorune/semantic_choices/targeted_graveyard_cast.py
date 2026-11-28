from __future__ import annotations

"""A target-pinned cast-or-decline choice during the original resolution."""

from collections.abc import Mapping
from dataclasses import dataclass

from ..cast_timing import type_line_has_card_type
from ..errors import GameRuleError, StateInvariantError
from ..public_quantity_reductions import public_mana_value
from ..public_history_query_model import PublicHistoryQuerySpec, public_history_query_value
from ..rules.temporary_cast_restrictions import apply_temporary_cast_restriction
from ..temporary_cast_rule_model import TemporaryCastRestrictionSpec
from ..replacement.immutable import FrozenMap, thaw_value
from ..rules.casting.face_queries import offered_cast_methods, prospective_cast_card
from ..rules.casting.proposal import build_cast_offer
from ..rules.graveyard_card_targets import targeted_graveyard_cast_runtime_dependencies
from ..rules.resolving_cast_payments import commit_resolving_cast
from ..selection.target_query import target_identity_matches_snapshot
from ..targeted_graveyard_cast_model import (
    GRAVEYARD_CAST_CAPABILITY, GRAVEYARD_CAST_FIELDS, GRAVEYARD_CAST_OPERATION,
    GRAVEYARD_CAST_MAXIMUM, GraveyardCastIntent, graveyard_cast_options_fingerprint,
    is_bounded_graveyard_cast, validate_graveyard_cast_effect,
    FIRST_PAID_GRAVEYARD_CAST_FIELD, first_paid_graveyard_cast,
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
            or effect["owner_relation"] == "you" and card.owner != actor
            or not target_identity_matches_snapshot(host, ref, snapshot)):
        raise GameRuleError("The graveyard cast target incarnation changed")
    program = host.semantics.get(item.semantic_key)
    if (program is None or not host.semantic_program_is_current_trusted(program)
            or not any(row == {**effect, "card": "$target.0", "player": "$controller"} for row in program.effects)):
        raise GameRuleError("The current program does not authorize this graveyard cast")
    return card


def _instant_or_sorcery(type_line):
    return any(type_line_has_card_type(type_line, card_type) for card_type in ("instant", "sorcery"))


def _option_pairs(host, *, actor, card, paid=False):
    if paid and public_history_query_value(host.state, actor, PublicHistoryQuerySpec("spell_cast")) != 0:
        return ()
    record = host.card_record(card)
    if record is None:
        return ()
    options = []
    for face in host._cast_faces(record, required_face=None):
        face_name = face.get("name") if face else None
        if not paid and not _instant_or_sorcery(face.get("type_line", "") if face else record.type_line):
            continue
        result = build_cast_offer(host, actor, card, face_name=face_name,
                                  resolving_payment="paid" if paid else "free", resolving_origin="graveyard")
        if result.offer is None:
            continue
        offered = result.offer.to_dict(include_fingerprint=False)
        for price in offered["cost_options"]:
            canonical = {**price, "face": offered.get("face")}
            public_id = "graveyard-cast:" + graveyard_cast_options_fingerprint((canonical,))
            options.append(({**canonical, "id": public_id}, canonical))
    return tuple(options)


def _bounded_candidates(host, *, item, actor, effect):
    validate_graveyard_cast_effect(effect, resolved=True)
    if (item is None or item.kind != "triggered_ability" or item.controller != actor
            or effect["player"] != actor or actor not in host.active_seats or item.targets
            or item.context.get("currently_resolving") is not True):
        raise GameRuleError("The bounded graveyard cast lost the original resolving trigger")
    program = host.semantics.get(item.semantic_key)
    original = {key: value for key, value in effect.items() if key != "card"}
    original["player"] = "$controller"
    if (program is None or not host.semantic_program_is_current_trusted(program)
            or not targeted_graveyard_cast_runtime_dependencies(program)
            or program.effects[-1] != original):
        raise GameRuleError("The current program does not authorize this bounded graveyard cast")
    return tuple(sorted(
        (card for card in host.state.cards.values()
         if card.zone == "graveyard" and card.is_card_object and not card.face_down and card.owner == actor),
        key=lambda card: card.ref,
    ))


def _bounded_card_options(host, *, card, actor, maximum):
    for method, name in offered_cast_methods(host, card):
        if method is not None:
            continue
        view = prospective_cast_card(host, card, name)
        effective = host._effective_card_data(view)
        if not _instant_or_sorcery(effective["type_line"]) or public_mana_value(effective) > maximum:
            continue
        result = build_cast_offer(host, actor, card, face_name=name, resolving_payment="free",
                                  resolving_origin="graveyard")
        if result.offer is None:
            continue
        offered = result.offer.to_dict(include_fingerprint=False)
        for price in offered["cost_options"]:
            canonical = {**price, "face": offered.get("face")}
            fingerprint = graveyard_cast_options_fingerprint((
                {"incarnation": card.logical_object_id, "option": canonical},
            ))
            yield ({**canonical, "id": f"graveyard-cast:{fingerprint}", "card": card.ref},
                   canonical, card)


def _bounded_options(host, *, item, actor, effect):
    return tuple(
        choice for card in _bounded_candidates(host, item=item, actor=actor, effect=effect)
        for choice in _bounded_card_options(host, card=card, actor=actor, maximum=effect[GRAVEYARD_CAST_MAXIMUM])
    )


def targeted_graveyard_cast_options(host, *, item, actor, effect):
    effect = _instruction(effect)
    if is_bounded_graveyard_cast(effect):
        return tuple(public for public, _, _ in _bounded_options(host, item=item, actor=actor, effect=effect))
    card = _live_target(host, item=item, actor=actor, effect=effect)
    return tuple(public for public, _ in _option_pairs(host, actor=actor, card=card, paid=first_paid_graveyard_cast(effect)))


def _bounded_cast_after_choice(host, *, item, actor, effect, intent):
    pairs = _bounded_options(host, item=item, actor=actor, effect=effect)
    if graveyard_cast_options_fingerprint(public for public, _, _ in pairs) != intent.options_fingerprint:
        raise GameRuleError("The current bounded graveyard cast options changed")
    selected = tuple((public, option, card) for public, option, card in pairs if public["card"] == effect["card"])
    if not selected:
        raise GameRuleError("The chosen card no longer has a legal bounded cast")
    return selected[0][2], tuple((public, option) for public, option, _ in selected)


def execute_targeted_graveyard_cast(host, intent):
    actor = intent.effect["player"]
    item = next((row for row in host.state.stack if row.ref == intent.stack_ref), None)
    effect = thaw_value(intent.effect)
    paid = first_paid_graveyard_cast(effect)
    if is_bounded_graveyard_cast(effect):
        card, pairs = _bounded_cast_after_choice(host, item=item, actor=actor, effect=effect, intent=intent)
    else:
        card = _live_target(host, item=item, actor=actor, effect=effect)
        pairs = _option_pairs(host, actor=actor, card=card, paid=paid)
        if graveyard_cast_options_fingerprint(public for public, _ in pairs) != intent.options_fingerprint:
            raise GameRuleError("The current graveyard cast options changed")
    if card.logical_object_id != intent.logical_object_id:
        raise GameRuleError("The exact card selected for the graveyard cast changed")
    response = thaw_value(intent.response)
    selected, face = response.get("cost_option"), response.get("face")
    matches = [option for public, option in pairs
               if (selected is None or selected == public["id"]) and (face is None or face == public["face"])]
    if (len(matches) != 1 or response.get("cast_method") is not None
            or response.get("card", card.ref) != card.ref or response.get("from", "graveyard") != "graveyard"
            or "x" in response and (type(response["x"]) is not int or response["x"] < 0
                                    or not paid and response["x"] != 0)):
        raise GameRuleError("Select one current graveyard cast option and a legal X value")
    option = matches[0]
    response.update(card=card.ref, cost_option=option["id"], auto_pay=True)
    response["from"] = "graveyard"
    if option["face"] is not None:
        response["face"] = option["face"]
    commit_resolving_cast(host, actor=actor, response=response, origin="graveyard",
                          payment_id=f"{item.ref}:targeted-graveyard:{intent.logical_object_id}",
                          replacement_selections=intent.replacement_selections, force_without_mana_cost=not paid)
    spell = host.state.cards[card.object_id]
    if (spell.zone != "stack" or spell.controller != actor or spell.logical_object_id == intent.logical_object_id
            or not any(row.kind == "spell" and row.card_object_id == spell.object_id for row in host.state.stack)):
        raise StateInvariantError("The graveyard cast created no spell")
    if paid:
        apply_temporary_cast_restriction(host, actor=actor, restriction=TemporaryCastRestrictionSpec("target"),
                                         target_player=actor, source_id=item.ref)
    elif intent.effect["exile_on_graveyard"]:
        from ..rules.cast_grant_riders import CastGrantRiderBinding
        from ..zone_object_state import mark_cast_grant_rider

        mark_cast_grant_rider(spell, CastGrantRiderBinding(
            "graveyard_exile", spell.logical_object_id, actor, item.ref, owner=spell.owner,
        ))


def _prepare_bounded_choice(effect, context, options):
    if "card" in effect:
        raise SemanticChoiceError("The bounded graveyard cast must start without a selection")
    return SemanticChoicePreparation(
        request=SemanticChoiceRequest(
            prompt="Cast an instant or sorcery from your graveyard without paying its mana cost?",
            choice=CastOrDeclineChoice(options), public_context=FrozenMap({
                "stack": context.stack_ref, "operation": GRAVEYARD_CAST_OPERATION, "cast_options": options,
            }),
        ),
        continuation_effect=FrozenMap({**effect, "_options_fingerprint": graveyard_cast_options_fingerprint(options)}),
    )


def _select_bounded_card(effect, options, response, query):
    if "card" in effect:
        raise SemanticChoiceError("The bounded cast continuation already contains a selection")
    matches = [
        row for row in options
        if response.get("cost_option") in (None, row["id"])
        and response.get("card") in (None, row["card"])
        and response.get("face") in (None, row["face"])
    ]
    if len(matches) != 1:
        raise SemanticChoiceError("Select one currently offered card and cost from your graveyard")
    row, = matches
    card = query.object(row["card"], zones=("graveyard",))
    if card is None or not card.logical_object_id:
        raise SemanticChoiceError("The chosen graveyard card changed")
    response.update(cost_option=row["id"], card=card.ref)
    return {**effect, "card": card.ref}, card.logical_object_id


@dataclass(frozen=True, slots=True)
class TargetedGraveyardCastChoiceHandler:
    operation: str = GRAVEYARD_CAST_OPERATION
    handler_id: str = "choice.cast.targeted-graveyard.v1"
    schema_version: int = 1
    rule_references: tuple[str, ...] = ("CR 400.7", "CR 601.2", "CR 608.2b", "CR 608.2g")
    capability_dependencies: tuple[str, ...] = (GRAVEYARD_CAST_CAPABILITY,)
    continuation_fields: tuple[str, ...] = ("player", "card", "owner_relation", "exile_on_graveyard",
                                            GRAVEYARD_CAST_MAXIMUM, FIRST_PAID_GRAVEYARD_CAST_FIELD, *_CONTINUATION_FIELDS)
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
        if is_bounded_graveyard_cast(effect):
            return _prepare_bounded_choice(effect, context, options)
        card = context.query.object(effect["card"], zones=("graveyard",))
        if card is None or not card.logical_object_id:
            raise SemanticChoiceError("The graveyard choice lost its target")
        return SemanticChoicePreparation(
            request=SemanticChoiceRequest(
                prompt=("Cast the targeted card and pay its costs?" if first_paid_graveyard_cast(effect)
                        else "Cast the targeted card without paying its mana cost?"),
                choice=CastOrDeclineChoice(options), public_context=FrozenMap({
                    "stack": context.stack_ref, "operation": self.operation, "card": card.ref, "cast_options": options,
                }),
            ),
            continuation_effect=FrozenMap({**effect, "_logical_object_id": card.logical_object_id,
                                            "_options_fingerprint": graveyard_cast_options_fingerprint(options)}),
        )

    def complete(self, continuation, response, query):
        effect = continuation.effect
        bounded = is_bounded_graveyard_cast(effect)
        expected = (GRAVEYARD_CAST_FIELDS - {"card"}) | {GRAVEYARD_CAST_MAXIMUM, "_options_fingerprint"} if bounded else (
            GRAVEYARD_CAST_FIELDS | set(_CONTINUATION_FIELDS)
        )
        if not bounded and first_paid_graveyard_cast(effect):
            expected.add(FIRST_PAID_GRAVEYARD_CAST_FIELD)
        if set(effect) != expected:
            raise SemanticChoiceError("The graveyard cast continuation fields changed")
        try:
            validate_graveyard_cast_effect(_instruction(effect), resolved=True)
        except ValueError as exc:
            raise SemanticChoiceError(str(exc)) from exc
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
            instruction = _instruction(effect)
            if bounded:
                instruction, logical_id = _select_bounded_card(instruction, options, submitted, query)
            else:
                logical_id = effect["_logical_object_id"]
            intent = GraveyardCastIntent(continuation.stack_ref, FrozenMap(instruction), logical_id,
                                        effect["_options_fingerprint"], FrozenMap(submitted))
        except ValueError as exc:
            raise SemanticChoiceError(str(exc)) from exc
        return SemanticChoiceCompletion(intents=(intent,))
