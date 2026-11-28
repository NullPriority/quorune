"""Select from a revealed X-prefix and continue after actual simultaneous entry."""

from dataclasses import dataclass
from collections.abc import Mapping

from ..graveyard_arrival_order_model import GraveyardArrivalOrders
from ..public_zone_moves import PublicBattlefieldController
from ..replacement.immutable import FrozenMap
from ..rules.announced_permanent_reveal import (
    CAPABILITY, DEPENDENCIES, OPERATION, PERMANENT_TYPES, validate_announced_permanent_reveal,
)
from ..rules.library_partition import partition_refs
from ..rules.library_selection import LibrarySelectionObjectIdentity
from ..semantic_runtime.intents import RevealLibraryCardsIntent
from ..semantic_runtime.revealed_remainder_intent import RevealedBattlefieldMoveIntent, RevealedRemainderMoveIntent
from .revealed_entry_choices import (
    ENTRY_INPUTS, PENDING_ENTRY_CHOICE, complete_revealed_entry_choice, revealed_entry_inputs,
)
from .intent_replacement import serialized_replacement_selections
from .model import (
    AutoContinue, ObjectChoice, OrderingChoice, SemanticChoiceCompletion, SemanticChoiceError,
    SemanticChoicePreparation, SemanticChoiceRequest,
)

_STATE = "revealed_permanent_state_v1"
_ORDER = "revealed_remainder_graveyard_order"
_BASE_FIELDS = {"op", "schema_version", "player", "amount"}
_VISIBLE_ZONES = ("library", "battlefield", "graveyard", "exile", "command")


def _identity(row):
    return LibrarySelectionObjectIdentity(row.object_id, row.logical_object_id, row.ref)


def _snapshot(query, actor, count):
    refs = query.library_refs(actor, top_first=True)[:count]
    rows = tuple(query.object(ref, zones=("library",)) for ref in refs)
    if any(row is None or row.owner != actor or not row.physical_card for row in rows):
        raise SemanticChoiceError("The public reveal must pin the controller's physical library prefix")
    return rows


def _eligible(rows, count):
    return tuple(row.ref for row in rows if PERMANENT_TYPES.intersection(row.types) and row.mana_value <= count)


def _state(effect, *, actor):
    base = {key: value for key, value in effect.items() if key in _BASE_FIELDS}
    validate_announced_permanent_reveal(base, actor=actor)
    if set(effect) == _BASE_FIELDS:
        return None, (), ()
    if set(effect) - _BASE_FIELDS - {_STATE, _ORDER, ENTRY_INPUTS, PENDING_ENTRY_CHOICE}:
        raise SemanticChoiceError("Reveal continuation fields are outside the closed instruction")
    raw = effect.get(_STATE)
    if not isinstance(raw, Mapping) or set(raw) != {"phase", "looked", "selected"}:
        raise SemanticChoiceError("Reveal continuation lost its original selected set")
    phase = raw["phase"]
    if (phase not in ("select", "enter", "remainder") or _ORDER in effect and phase != "remainder"
            or {ENTRY_INPUTS, PENDING_ENTRY_CHOICE}.intersection(effect) and phase != "enter"):
        raise SemanticChoiceError("Reveal continuation phase is invalid")
    looked = tuple(LibrarySelectionObjectIdentity.from_dict(value) for value in raw["looked"])
    refs = tuple(identity.ref for identity in looked)
    if (not refs or len(refs) > effect["amount"] or len(refs) != len(set(refs))
            or len({identity.object_id for identity in looked}) != len(refs)):
        raise SemanticChoiceError("Reveal continuation lost its original public library group")
    selected = partition_refs(raw["selected"], field="selected revealed cards")
    if any(ref not in refs for ref in selected) or phase == "select" and selected:
        raise SemanticChoiceError("The selected entry set must belong to the original reveal")
    return phase, looked, selected


def _next(effect, phase, looked, selected=()):
    return FrozenMap({
        **{key: effect[key] for key in _BASE_FIELDS},
        _STATE: {"phase": phase, "looked": tuple(row.to_dict() for row in looked), "selected": selected},
    })


def _validate_entry_selection(effect, query, actor, looked, selected):
    rows = _snapshot(query, actor, effect["amount"])
    if tuple(_identity(row) for row in rows) != looked or set(selected) - set(_eligible(rows, effect["amount"])):
        raise SemanticChoiceError("Selected revealed cards no longer match their locked value or incarnation")


def _entry_intent(effect, query, actor, looked, selected, reason):
    _validate_entry_selection(effect, query, actor, looked, selected)
    retained = revealed_entry_inputs(effect, selected)
    return RevealedBattlefieldMoveIntent(
        actor=actor, object_refs=selected, expected_zones=("library",), destination="battlefield", reason=reason,
        owned_only=True, battlefield_controller=PublicBattlefieldController.ACTOR,
        aura_targets=FrozenMap(retained["aura_targets"]), battle_protectors=FrozenMap(retained["battle_protectors"]),
        replacement_selections=tuple(retained["replacement_selections"]),
    )


def _remainder_intent(query, actor, looked, selected, reason, *, orders=None, replacement_selections=()):
    refs, zones = [], set()
    for identity in looked:
        row = query.object(identity.ref, zones=_VISIBLE_ZONES)
        if identity.ref not in selected:
            if row is None or row.zone != "library" or _identity(row) != identity:
                raise SemanticChoiceError("An unselected revealed card changed before the graveyard instruction")
        if row is None:
            continue
        if row.object_id != identity.object_id or row.owner != actor:
            raise SemanticChoiceError("The graveyard instruction lost an original revealed card")
        if row.zone in ("battlefield", "graveyard"):
            continue
        refs.append(row.ref)
        zones.add(row.zone)
    return RevealedRemainderMoveIntent(
        actor=actor, object_refs=tuple(refs), expected_zones=tuple(sorted(zones)), destination="graveyard",
        reason=reason, owned_only=True, graveyard_orders=orders, replacement_selections=replacement_selections,
    ) if refs else None


def _choose_entry_request(rows, eligible, context):
    return SemanticChoiceRequest(
        "Choose any number of eligible revealed permanent cards to put onto the battlefield.",
        ObjectChoice(field_name="cards", legal_refs=eligible, zones=("library",), minimum=0,
                     maximum=len(eligible), optional=True, visibility="public", owner_relation="actor"),
        FrozenMap({"stack": context.stack_ref, "operation": OPERATION, "revealed": tuple(row.ref for row in rows),
                   "objects": tuple({"id": row.ref, "name": row.printed_name} for row in rows)}),
    )


def _order_choice(group):
    return OrderingChoice("cards", group.refs, visibility="actor_private",
                          schema_extras=FrozenMap({"order": "bottom_to_top"}))


def prepare_revealed_remainder_order(host, continuation, intent, required, *, index, count):
    orders = required.orders
    group = orders.current_group
    if index != 0 or count != 1 or group is None or len(orders.groups) != 1 or group.owner != intent.actor:
        raise SemanticChoiceError("Revealed remainder ordering requires its original owner's actual arrivals")
    objects = []
    for ref, identity in group.members:
        card = host.state.cards.get(identity.object_id)
        if (card is None or card.ref != ref or card.logical_object_id != identity.logical_object_id
                or card.owner != group.owner or ref not in intent.object_refs or card.zone not in intent.expected_zones):
            raise SemanticChoiceError("An actual graveyard arrival changed before its ordering choice")
        objects.append({"id": ref, "name": card.printed_name})
    retained = {
        "orders": orders.to_dict(), "replacement_selections": serialized_replacement_selections(intent.replacement_selections),
    }
    return group.owner, SemanticChoicePreparation(
        SemanticChoiceRequest(
            "Order the cards entering your graveyard from bottom to top.", _order_choice(group),
            FrozenMap({"operation": OPERATION, "stack": continuation.stack_ref, "objects": objects}),
        ),
        FrozenMap({**continuation.effect, _ORDER: retained}),
    )


@dataclass(frozen=True, slots=True)
class AnnouncedPermanentRevealHandler:
    operation: str = OPERATION
    handler_id: str = "choice.library.announced-permanent-reveal.v1"
    schema_version: int = 1
    rule_references: tuple[str, ...] = (
        "107.3a", "110.4a", "202.3e", "303.4f", "303.4g", "310.12a", "400.7j", "608.2c", "608.2f", "614.6", "701.20a",
    )
    capability_dependencies: tuple[str, ...] = DEPENDENCIES
    continuation_fields: tuple[str, ...] = ("player", "amount", _STATE, _ORDER, ENTRY_INPUTS, PENDING_ENTRY_CHOICE)
    private_data: tuple[str, ...] = (_STATE, _ORDER)
    projected_fields: tuple[str, ...] = ("stack", "operation", "revealed", "objects", "legal_actions")
    mutation_path: tuple[str, ...] = (
        "RevealLibraryCardsIntent", "MoveObjectsSimultaneouslyIntent", "RevealedRemainderMoveIntent",
    )
    replay_fixture: str = "announced-permanent-reveal"
    test_modules: tuple[str, ...] = ("tests.test_announced_permanent_reveal",)

    def prepare(self, effect, context):
        actor = context.actor
        if actor != context.stack_controller:
            raise SemanticChoiceError("Only the current resolving spell controller can make these choices")
        phase, looked, selected = _state(effect, actor=actor)
        if phase == "enter":
            intent = _entry_intent(effect, context.query, actor, looked, selected, context.stack_label)
            return SemanticChoicePreparation(
                None, FrozenMap(effect), preparation_intents=(intent,),
                auto_continue=AutoContinue("Finish the original public reveal after simultaneous entry.",
                                           (_next(effect, "remainder", looked, selected),)),
            )
        if phase == "remainder":
            intent = _remainder_intent(context.query, actor, looked, selected, context.stack_label)
            return SemanticChoicePreparation(
                None, FrozenMap(effect), preparation_intents=(intent,) if intent is not None else (),
                auto_continue=AutoContinue("Finish the actual graveyard remainder."),
            )
        if phase is not None:
            raise SemanticChoiceError("The original public reveal cannot be issued twice")
        rows = _snapshot(context.query, actor, effect["amount"])
        if not rows:
            return SemanticChoicePreparation(None, FrozenMap(effect), auto_continue=AutoContinue("No cards to reveal."))
        looked = tuple(_identity(row) for row in rows)
        eligible = _eligible(rows, effect["amount"])
        reveal = RevealLibraryCardsIntent(
            actor, actor, actor, tuple(row.ref for row in rows), context.stack_label, public=True, announce_public_prefix=True,
        )
        if not eligible:
            return SemanticChoicePreparation(
                None, FrozenMap(effect), preparation_intents=(reveal,),
                auto_continue=AutoContinue("None of the revealed cards qualify.",
                                           (_next(effect, "remainder", looked),)),
            )
        return SemanticChoicePreparation(
            _choose_entry_request(rows, eligible, context), _next(effect, "select", looked), preparation_intents=(reveal,),
        )

    def complete(self, continuation, response, query):
        effect = continuation.effect
        actor = continuation.semantic_frame.controller
        phase, looked, selected = _state(effect, actor=actor)
        if PENDING_ENTRY_CHOICE in effect:
            return complete_revealed_entry_choice(continuation, response, query, selected)
        if set(response) - {"action", "action_id", "choice_schema", "cards"} or "cards" not in response:
            raise SemanticChoiceError("Submit precisely the offered revealed card selection")
        if _ORDER in effect:
            retained = effect[_ORDER]
            if not isinstance(retained, Mapping) or set(retained) != {"orders", "replacement_selections"}:
                raise SemanticChoiceError("The sealed graveyard order did not retain its replacement decisions")
            orders = GraveyardArrivalOrders.from_dict(retained["orders"])
            group = orders.current_group
            if group is None or group.owner != actor or len(orders.groups) != 1:
                raise SemanticChoiceError("Only the original owner may order these arrivals")
            intent = _remainder_intent(
                query, actor, looked, selected, continuation.note or "Put the revealed remainder into the graveyard.",
                orders=orders.submit(actor, response["cards"]), replacement_selections=retained["replacement_selections"],
            )
            if intent is None:
                raise SemanticChoiceError("The original graveyard arrivals changed")
            return SemanticChoiceCompletion(intents=(intent,))
        if phase != "select":
            raise SemanticChoiceError("The revealed permanent selection is not current")
        selected = partition_refs(response["cards"], field="selected revealed cards")
        if selected:
            _validate_entry_selection(effect, query, actor, looked, selected)
        return SemanticChoiceCompletion(prepend_effects=(
            _next(effect, "enter" if selected else "remainder", looked, selected),
        ))
