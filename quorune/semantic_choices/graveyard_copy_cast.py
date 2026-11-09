from __future__ import annotations

"""Commit exile and copy creation before choosing the resolving casts."""

from collections.abc import Mapping
from dataclasses import dataclass

from ..discover_casting import discover_cast_options, require_discover_resolution
from ..discover_model import DiscoverCastIntent, ExileCastProgress, discover_options_fingerprint
from ..errors import GameRuleError
from ..replacement.immutable import FrozenMap
from ..rules.graveyard_copy_cast_model import (
    ExileGraveyardCopiesIntent, GRAVEYARD_COPY_CAPABILITY, GRAVEYARD_COPY_OFFER, GRAVEYARD_COPY_OPERATION,
    GRAVEYARD_COPY_RULES, validate_graveyard_copy_effect,
)
from ..rules.library_selection import LibrarySelectionObjectIdentity
from ..selection.target_query import target_identity_matches_snapshot
from .model import (
    AutoContinue, CastOrDeclineChoice, IntentResultObjects, SemanticChoiceCompletion,
    SemanticChoiceError, SemanticChoicePreparation, SemanticChoiceRequest,
)


def _offer(actor, scope, cards):
    return FrozenMap({"op": GRAVEYARD_COPY_OFFER, "player": actor, "scope": scope, "cards": cards})


def _require_parent(host, *, item, actor, scope):
    if item is None:
        raise GameRuleError("The graveyard copy resolution disappeared")
    require_discover_resolution(host, actor=actor, stack_ref=item.ref)
    program = host.semantics.get(item.semantic_key)
    if program is None or not host.semantic_program_is_current_trusted(program):
        raise GameRuleError("The graveyard copy program is no longer trusted")
    effects = [effect for effect in program.effects
               if effect.get("op") == GRAVEYARD_COPY_OPERATION and effect.get("scope") == scope]
    if len(effects) != 1:
        raise GameRuleError("The resolving program does not authorize the copy scope")
    validate_graveyard_copy_effect(effects[0])
    return effects[0]


def _current_own_spells(host, actor):
    return {
        card.ref for card in host.state.cards.values()
        if card.zone == "graveyard" and card.owner == actor and card.is_card_object
        and {"instant", "sorcery"}.intersection(host._type_parts(host._effective_card_data(card)["type_line"])[0])
    }


def execute_exile_graveyard_copies(host, intent):
    item = next((row for row in host.state.stack if row.ref == intent.stack_ref), None)
    instruction = _require_parent(host, item=item, actor=intent.actor, scope=intent.scope)
    if instruction["copies"] != intent.copies:
        raise GameRuleError("The resolving copy count changed")
    refs = tuple(identity.ref for identity in intent.originals)
    if intent.scope == "own_all":
        if set(refs) != _current_own_spells(host, intent.actor):
            raise GameRuleError("The all-graveyard copy set changed")
    elif refs != tuple(ref for ref in item.targets if ref is not None):
        raise GameRuleError("The surviving graveyard copy targets changed")
    for identity in intent.originals:
        card = host.state.cards.get(identity.object_id)
        if (card is None or card.ref != identity.ref or card.logical_object_id != identity.logical_object_id
                or card.zone != "graveyard" or not card.is_card_object):
            raise GameRuleError("An original graveyard copy target changed incarnation")
        if intent.scope != "own_all":
            snapshot = item.context.get("target_snapshots", {}).get(identity.ref, {})
            if (not isinstance(snapshot, Mapping) or type(snapshot.get("zone_change_counter")) is not int
                    or not target_identity_matches_snapshot(host, identity.ref, snapshot)):
                raise GameRuleError("The original card is no longer the announced graveyard target")
    moved = host._move_cards_simultaneously(
        [(identity.object_id, "exile") for identity in intent.originals],
        reason=item.label, replacement_selections=intent.replacement_selections,
    )
    from ..zone_object_state import mark_resolving_graveyard_copy

    copies = []
    original_ids = {identity.object_id for identity in intent.originals}
    for original in moved:
        if original.object_id not in original_ids or original.zone != "exile":
            continue
        for _ in range(intent.copies):
            copied = host.create_card_copy(intent.actor, original.ref, zone="exile")
            mark_resolving_graveyard_copy(copied, item.ref)
            copies.append(copied.ref)
    return tuple(copies)


def _prepare_exile(effect, context):
    validate_graveyard_copy_effect(effect, resolved=True)
    if effect["player"] != context.actor or context.actor != context.stack_controller:
        raise SemanticChoiceError("Graveyard copy preparation belongs to another controller")
    scope = effect["scope"]
    if scope == "own_all":
        rows = tuple(sorted(
            (row for row in context.query.objects(zones=("graveyard",))
             if row.owner == context.actor and not row.token and {"instant", "sorcery"}.intersection(row.types)),
            key=lambda row: row.ref,
        ))
    else:
        rows = tuple(context.query.object(ref, zones=("graveyard",)) for ref in effect["cards"])
    if any(row is None or row.token or not row.logical_object_id for row in rows):
        raise SemanticChoiceError("Graveyard copy preparation requires current original cards")
    if not rows:
        return SemanticChoicePreparation(None, FrozenMap(effect), auto_continue=AutoContinue("There are no cards to copy"))
    originals = tuple(LibrarySelectionObjectIdentity(row.object_id, row.logical_object_id, row.ref) for row in rows)
    intent = ExileGraveyardCopiesIntent(context.actor, context.stack_ref, scope, effect["copies"], originals)
    return SemanticChoicePreparation(None, FrozenMap(effect), preparation_intents=(intent,), auto_continue=AutoContinue(
        reason="Exile the original cards and create their copies together",
        prepend_effects=(_offer(context.actor, scope, ()),), objects_from_result=IntentResultObjects(0, 0),
    ))


def _offer_refs(effect):
    if (not isinstance(effect, Mapping) or set(effect) != {"op", "player", "scope", "cards"}
            or effect["op"] != GRAVEYARD_COPY_OFFER or type(effect["player"]) is not str or not effect["player"]
            or not isinstance(effect["cards"], (list, tuple))
            or any(type(ref) is not str or not ref for ref in effect["cards"])
            or len(set(effect["cards"])) != len(effect["cards"])):
        raise SemanticChoiceError("The resolving copy offer fields changed")
    return tuple(effect["cards"])


def _copy_progress(card, actor, parent):
    identity = LibrarySelectionObjectIdentity(card.object_id, card.logical_object_id, card.ref)
    return ExileCastProgress(None, (identity,), identity.ref, actor, "free", parent)


def graveyard_copy_cast_options(host, *, item, actor, effect):
    offer = {key: value for key, value in effect.items() if key not in {"_stack_ref", "_options_fingerprint"}}
    refs = _offer_refs(offer)
    if offer["player"] != actor or item is None or effect.get("_stack_ref", item.ref) != item.ref:
        raise GameRuleError("The resolving copy offer lost its parent or controller")
    _require_parent(host, item=item, actor=actor, scope=offer["scope"])
    by_ref = {card.ref: card for card in host.state.cards.values() if card.is_card_copy and card.zone == "exile"}
    options = []
    for ref in refs:
        card = by_ref.get(ref)
        if card is None:
            raise GameRuleError("An uncast copy changed incarnation")
        options.extend(discover_cast_options(host, item=item, actor=actor,
            effect={"progress": _copy_progress(card, actor, item.ref).to_dict()}))
    return tuple(options)


def _prepare_offer(effect, context):
    _offer_refs(effect)
    if effect["player"] != context.actor or context.actor != context.stack_controller:
        raise SemanticChoiceError("Only the resolving controller can cast the copies")
    options = tuple(context.query.authorized_cast_options())
    if not options:
        return SemanticChoicePreparation(None, FrozenMap(effect), auto_continue=AutoContinue("No remaining copy can be cast"))
    return SemanticChoicePreparation(
        request=SemanticChoiceRequest(prompt="Choose the next copy to cast without paying its mana cost.",
            choice=CastOrDeclineChoice(options, allow_decline=_can_finish(effect, options)),
            public_context=FrozenMap({"stack": context.stack_ref, "operation": GRAVEYARD_COPY_OFFER, "cast_options": options})),
        continuation_effect=FrozenMap({**effect, "_stack_ref": context.stack_ref,
                                       "_options_fingerprint": discover_options_fingerprint(options)}),
    )


def _can_finish(effect, options):
    return effect["scope"] != "paired" or not any(option["required_cast"] for option in options)


def _complete_offer(continuation, response, query):
    saved = continuation.effect
    if saved.get("_stack_ref") != continuation.stack_ref or "_options_fingerprint" not in saved:
        raise SemanticChoiceError("The copy choice lost its original resolution")
    effect = {key: value for key, value in saved.items() if key not in {"_stack_ref", "_options_fingerprint"}}
    refs = _offer_refs(effect)
    options = tuple(query.authorized_cast_options())
    if discover_options_fingerprint(options) != saved["_options_fingerprint"]:
        raise SemanticChoiceError("The copy cast options changed")
    choice, cast = response.get("choice"), response.get("cast", {})
    if choice == "decline" and _can_finish(effect, options):
        return SemanticChoiceCompletion()
    if choice != "cast" or not isinstance(cast, Mapping):
        raise SemanticChoiceError("Choose a current cast for the resolving copies")
    selected = cast.get("cost_option")
    matches = [row for row in options if selected is None or row["id"] == selected]
    if len(matches) != 1:
        raise SemanticChoiceError("Select one current copy and cost")
    option = matches[0]
    ref = option["card"]
    card = query.object(ref, zones=("exile",))
    if (ref not in refs or card is None or cast.get("card", ref) != ref
            or "x" in cast and (type(cast["x"]) is not int or cast["x"] != 0)):
        raise SemanticChoiceError("The cast submission must retain the offered copy and zero X")
    actor = effect["player"]
    group = tuple(row for row in options if row["card"] == ref)
    intent = DiscoverCastIntent(actor, continuation.stack_ref, _copy_progress(card, actor, continuation.stack_ref),
        discover_options_fingerprint(group), FrozenMap({**cast, "cost_option": option["id"]}))
    remaining = tuple(row for row in refs if row != ref)
    return SemanticChoiceCompletion(
        intents=(intent,), prepend_effects=(_offer(actor, effect["scope"], remaining),) if remaining else (),
    )


@dataclass(frozen=True, slots=True)
class GraveyardCopyCastHandler:
    operation: str
    handler_id: str
    schema_version: int = 1
    rule_references: tuple[str, ...] = GRAVEYARD_COPY_RULES
    capability_dependencies: tuple[str, ...] = (GRAVEYARD_COPY_CAPABILITY,)
    continuation_fields: tuple[str, ...] = ("player", "scope", "cards", "copies", "_stack_ref", "_options_fingerprint")
    private_data: tuple[str, ...] = ()
    projected_fields: tuple[str, ...] = ("prompt", "cast_options")
    mutation_path: tuple[str, ...] = ("ExileGraveyardCopiesIntent", "DiscoverCastIntent")
    replay_fixture: str = "graveyard-copy-resolving-cast"
    test_modules: tuple[str, ...] = ("tests.test_graveyard_copy_cast",)

    def prepare(self, effect, context):
        if effect.get("op") != self.operation:
            raise SemanticChoiceError("The graveyard copy choice operation changed")
        try:
            return _prepare_exile(effect, context) if self.operation == GRAVEYARD_COPY_OPERATION else _prepare_offer(effect, context)
        except (ValueError, TypeError, KeyError) as exc:
            raise SemanticChoiceError(str(exc)) from exc

    def complete(self, continuation, response, query):
        if self.operation != GRAVEYARD_COPY_OFFER:
            raise SemanticChoiceError("The exile and copy preparation accepts no response")
        return _complete_offer(continuation, response, query)


GRAVEYARD_COPY_HANDLERS = (
    GraveyardCopyCastHandler(GRAVEYARD_COPY_OPERATION, "choice.graveyard.exile-copy.v1"),
    GraveyardCopyCastHandler(GRAVEYARD_COPY_OFFER, "choice.graveyard.cast-copy.v1"),
)
