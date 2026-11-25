from __future__ import annotations

"""Ordered public graveyard identities and their committed copy inputs."""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from ..entry_copy import COPIABLE_FACE_FIELD, COPIABLE_ORACLE_FIELD, copiable_oracle_id
from ..errors import GameRuleError
from ..object_query import exact_numeric_characteristic
from ..replacement.immutable import FrozenMap


@dataclass(frozen=True, slots=True)
class GraveyardCopyCard:
    object_id: str
    incarnation: str
    ref: str
    owner: str

    def __post_init__(self) -> None:
        if any(type(value) is not str or not value or value != value.strip()
               for value in (self.object_id,self.incarnation,self.ref,self.owner)):
            raise ValueError("A graveyard copy card needs a complete public incarnation")

    def to_dict(self) -> dict[str, str]:
        return {"object_id":self.object_id,"incarnation":self.incarnation,"ref":self.ref,"owner":self.owner}

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "GraveyardCopyCard":
        if not isinstance(raw, Mapping) or set(raw) != {"object_id","incarnation","ref","owner"}:
            raise ValueError("A graveyard copy card has a closed identity")
        return cls(raw["object_id"],raw["incarnation"],raw["ref"],raw["owner"])

    def matches(self, card) -> bool:
        return (card is not None and card.is_card_object and card.zone == "graveyard"
                and not card.face_down and card.object_id == self.object_id
                and card.logical_object_id == self.incarnation and card.ref == self.ref
                and card.owner == self.owner)


@dataclass(frozen=True, slots=True)
class GraveyardCopyPair:
    controller: str
    copy_card: GraveyardCopyCard
    power_card: GraveyardCopyCard

    def __post_init__(self) -> None:
        if type(self.controller) is not str or not self.controller:
            raise ValueError("A graveyard copy pair needs its entering controller")
        if any(not isinstance(card, GraveyardCopyCard) for card in self.cards):
            raise ValueError("A graveyard copy pair needs two captured cards")
        if any(len({getattr(card, key) for card in self.cards}) != 2
               for key in ("object_id","incarnation","ref")):
            raise ValueError("The copy and power roles require distinct cards")

    @property
    def cards(self) -> tuple[GraveyardCopyCard, GraveyardCopyCard]:
        return self.copy_card, self.power_card

    def to_dict(self) -> dict[str, Any]:
        return {"schema_version":1,"controller":self.controller,
                "copy_card":self.copy_card.to_dict(),"power_card":self.power_card.to_dict()}

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "GraveyardCopyPair":
        if (not isinstance(raw, Mapping) or set(raw) != {"schema_version","controller","copy_card","power_card"}
                or type(raw["schema_version"]) is not int or raw["schema_version"] != 1):
            raise ValueError("A graveyard copy pair has a closed version-one shape")
        return cls(raw["controller"],GraveyardCopyCard.from_dict(raw["copy_card"]),GraveyardCopyCard.from_dict(raw["power_card"]))


@dataclass(frozen=True, slots=True)
class GraveyardCopyOutcome:
    pair: GraveyardCopyPair
    copied_values: FrozenMap
    additional_counters: int

    def __post_init__(self) -> None:
        if (not isinstance(self.pair, GraveyardCopyPair) or not isinstance(self.copied_values, FrozenMap)
                or any(type(self.copied_values.get(name)) is not str or not self.copied_values[name]
                       for name in (COPIABLE_ORACLE_FIELD,COPIABLE_FACE_FIELD))
                or type(self.additional_counters) is not int or self.additional_counters < 0):
            raise ValueError("A committed graveyard copy requires a typed result and counter quantity")


def _is_graveyard_creature(host, card) -> bool:
    if not card.is_card_object or card.zone != "graveyard" or card.face_down:
        return False
    effective = host._effective_card_data(card)
    return "creature" in host._type_parts(str(effective.get("type_line") or ""))[0]


def graveyard_copy_candidates(host, *, reserved=()) -> tuple[GraveyardCopyCard, ...]:
    unavailable = set(reserved)
    choices = (GraveyardCopyCard(card.object_id,card.logical_object_id,card.ref,card.owner)
               for card in host._semantic_event_sources(zones={"graveyard"})
               if card.object_id not in unavailable and _is_graveyard_creature(host,card))
    return tuple(sorted(choices,key=lambda card:card.ref))


def select_graveyard_copy_pair(controller, candidates, references) -> GraveyardCopyPair:
    if not isinstance(references,(list,tuple)) or len(references)!=2:
        raise ValueError("Choose one copy card followed by a separate power card")
    by_ref = {card.ref:card for card in candidates}
    if any(type(ref) is not str or ref not in by_ref for ref in references):
        raise ValueError("Both graveyard roles must use offered public cards")
    return GraveyardCopyPair(controller, *(by_ref[ref] for ref in references))


def current_graveyard_pair_cards(host, pair):
    if not isinstance(pair, GraveyardCopyPair) or pair.controller not in host.active_seats:
        raise GameRuleError("Graveyard copy requires its captured active controller")
    current = tuple(host.state.cards.get(choice.object_id) for choice in pair.cards)
    if any(not captured.matches(card) or not _is_graveyard_creature(host,card)
           for captured,card in zip(pair.cards,current,strict=True)):
        raise GameRuleError("A selected graveyard copy incarnation changed")
    return current


def _copy_values(host, card) -> FrozenMap:
    from ..card_program_faces import selected_face_id
    from ..defined_token_faces import single_face_copy_values

    record = host.card_record(card)
    values = single_face_copy_values(host._copyable_characteristics(card))
    values[COPIABLE_ORACLE_FIELD] = copiable_oracle_id(card)
    values[COPIABLE_FACE_FIELD] = selected_face_id(record,card) if record is not None else "front"
    return FrozenMap(values)


def _power(host, card):
    return exact_numeric_characteristic(card,host._effective_card_data(card),"power")


@dataclass(frozen=True, slots=True)
class PreparedGraveyardCopyInputs:
    outcome: GraveyardCopyOutcome
    exiles: tuple[Any, ...]
    consumed_selections: int = 0

    def __post_init__(self) -> None:
        from ..semantic_runtime.zone_replacement_model import PreparedZoneChange

        if not isinstance(self.outcome, GraveyardCopyOutcome) or type(self.exiles) is not tuple or len(self.exiles)!=2:
            raise ValueError("A prepared graveyard copy requires both ordered cost moves")
        for captured, plan in zip(self.outcome.pair.cards,self.exiles,strict=True):
            if (not isinstance(plan,PreparedZoneChange) or plan.origin!="graveyard"
                    or plan.requested_destination!="exile" or plan.object_id!=captured.object_id
                    or plan.logical_object_id!=captured.incarnation or plan.event.payload.get("object_ref")!=captured.ref):
                raise ValueError("A prepared exile does not match its captured graveyard role")
        if type(self.consumed_selections) is not int or self.consumed_selections<0:
            raise ValueError("The graveyard exile preparation has a nonnegative choice count")
        if self.exiles[0].state_revision!=self.exiles[1].state_revision:
            raise ValueError("The prepared exiles must use one captured revision")


def _after_exile_read_view(host, plan_batch):
    from collections import ChainMap
    from dataclasses import replace
    from types import MappingProxyType
    from ..characteristic_read_frames import CharacteristicReadHost, CharacteristicStateView
    from ..model import CardInstance
    from ..zone_object_state import reset_card_after_zone_change

    cards, players = {}, {}
    for plan in plan_batch:
        before = host.state.cards[plan.object_id]
        if plan.destination == before.zone:
            continue
        if plan.destination in {"battlefield","stack"}:
            raise GameRuleError("An entry cost redirected to battlefield or stack requires its own prepared view")
        after = CardInstance.from_dict(before.to_dict())
        reset_card_after_zone_change(after,destination=plan.destination,stack_to_battlefield=False)
        after.zone = plan.destination
        after.zone_change_counter += 1
        after.controller = plan.destination_controller or after.owner
        cards[after.object_id] = after
        player = players.get(after.owner)
        if player is None:
            original = host.state.players[after.owner]
            player = replace(original,zones={zone:list(ids) for zone,ids in original.zones.items()})
            players[after.owner] = player
        player.zones[before.zone].remove(after.object_id)
        player.zones[after.zone].append(after.object_id)
    state=CharacteristicStateView(host.state,ChainMap(MappingProxyType(cards),host.state.cards),
        ChainMap(MappingProxyType(players),host.state.players))
    return CharacteristicReadHost(host,state)


def _copy_outcome(before, after, pair, first, second):
    copy = after.state.cards[pair.copy_card.object_id]
    power = after.state.cards[pair.power_card.object_id]
    copy_host, copy_card = (after,copy) if copy.zone=="exile" and not copy.face_down else (before,first)
    power_host, power_card = (after,power) if power.zone=="exile" and not power.face_down else (before,second)
    amount=_power(power_host,power_card)
    if type(amount) is not int:
        raise GameRuleError("The power card lacks a represented public power")
    return GraveyardCopyOutcome(pair,_copy_values(copy_host,copy_card),max(0,amount))


def prepare_graveyard_copy_inputs(
    host, pair, *, occurrence, replacement_selections=(),
) -> PreparedGraveyardCopyInputs:
    """Resolve the two cost moves and read their detached, simultaneous outcome."""
    from ..semantic_runtime.zone_replacements import capture_zone_change_replacement_snapshot

    if type(occurrence) is not str or not occurrence:
        raise ValueError("The graveyard exile pair needs its exact entry occurrence")
    first,second=current_graveyard_pair_cards(host,pair)
    changes=tuple((role.object_id,"exile") for role in pair.cards)
    snapshot=capture_zone_change_replacement_snapshot(host,changes,error_type=GameRuleError)
    event_ids={role.ref:f"{occurrence}:exile:{role.ref}" for role in pair.cards}
    plans, consumed = _prepare_exile_prefix(snapshot, event_ids, replacement_selections)
    ordered=tuple(plans[role.object_id] for role in pair.cards)
    reader=_after_exile_read_view(host,ordered)
    return PreparedGraveyardCopyInputs(_copy_outcome(host,reader,pair,first,second),ordered,consumed)


def _prepare_exile_prefix(snapshot, event_ids, selections):
    from dataclasses import replace
    from ..replacement import ReplacementChoiceRequired, ReplacementEventBatch, advance_replacement_batch
    from ..semantic_runtime.zone_preparation_results import prepared_from_zone_event
    from ..semantic_runtime.zone_replacement_inputs import zone_change_snapshot_events

    events=tuple(replace(event,event_id=event_ids[event.payload["object_ref"]])
                 for event in zone_change_snapshot_events(snapshot))
    batch=ReplacementEventBatch(f"{events[0].event_id}:batch",events,snapshot.apnap_order)
    result=advance_replacement_batch(batch,snapshot.effects,selections=selections,
        require_all_selections=False,allow_bound_single_event=True)
    if result.pending is not None:
        raise ReplacementChoiceRequired(batch=result.batch,effects=snapshot.effects,pending=result.pending)
    by_id={event.affected_object.object_id:event for event in result.batch.events}
    return {sub.object_id:prepared_from_zone_event(sub,by_id[sub.object_id],
        state_revision=snapshot.revision,event_sequence=snapshot.event_sequence,effects=snapshot.effects,
        journal=tuple(row for row in result.batch.journal if row.event_id==by_id[sub.object_id].event_id))
        for sub in snapshot.subjects},result.consumed_selections


GRAVEYARD_COPY_PAIRS_FIELD = "graveyard_copy_pairs"


def apply_graveyard_copy_pair_selection(event, payload, scope, operation, objects):
    """Seal two public reservations. The zone owner supplies their committed results."""
    if (event.kind != "zone.change" or payload.get("destination") != "battlefield" or scope is None
            or payload.get("destination_controller") != operation.controller or event.affected_object is None):
        raise ValueError("Graveyard copy choice requires its affected battlefield entry")
    pair=select_graveyard_copy_pair(operation.controller,operation.eligible_cards(event),objects)
    captured=tuple(payload.get(GRAVEYARD_COPY_PAIRS_FIELD,()))
    if any(row["component_id"]==operation.component_id for row in captured):
        raise ValueError("A graveyard copy occurrence cannot reserve the same cost twice")
    payload[GRAVEYARD_COPY_PAIRS_FIELD]=[*captured,{"component_id":operation.component_id,"pair":pair.to_dict()}]
    return scope.reserve_zone_changes(card.object_id for card in pair.cards)


def validate_graveyard_copy_resume(host, batch, effects, selection, *, error_type):
    """Revalidate saved pair identities without granting authority to new objects."""
    from ..replacement.operations import GraveyardCopyEntryOperation

    selected=[]
    if isinstance(selection,Mapping) and selection.get("objects"):
        effect=next((row for row in effects if row.effect_id==selection["effect_id"]),None)
        if effect is not None:
            for operation in effect.operations:
                if isinstance(operation,GraveyardCopyEntryOperation):
                    try:
                        selected.append(select_graveyard_copy_pair(operation.controller,operation.candidates,selection["objects"]))
                    except ValueError as exc:
                        raise error_type(str(exc)) from exc
    for event in batch.events:
        for raw in event.payload.get(GRAVEYARD_COPY_PAIRS_FIELD,()):
            try:
                selected.append(GraveyardCopyPair.from_dict(raw["pair"]))
            except ValueError as exc:
                raise error_type(str(exc)) from exc
    for pair in selected:
        try:
            current_graveyard_pair_cards(host,pair)
        except GameRuleError as exc:
            raise error_type(str(exc)) from exc


GRAVEYARD_COPY_RESULTS_FIELD = "graveyard_copy_results"


def unprepared_graveyard_copy_occurrences(event):
    completed={row["component_id"] for row in event.payload.get(GRAVEYARD_COPY_RESULTS_FIELD,())}
    return tuple(row for row in event.payload.get(GRAVEYARD_COPY_PAIRS_FIELD,())
                 if row["component_id"] not in completed)


def materialize_graveyard_copy_entry(event, component_id, prepared):
    """Install the paid copy while retaining its separate, form-bound counter amount."""
    from dataclasses import replace
    from ..characteristic_evaluation import type_parts
    from ..replacement.face_status_copy import ENTRY_COPY_HISTORY_FIELD
    from ..replacement.immutable import immutable_fingerprint, thaw_value

    if not isinstance(prepared,PreparedGraveyardCopyInputs) or event.kind!="zone.change" or event.entry_scope is None:
        raise ValueError("A graveyard copy outcome requires its captured entry and prepared exiles")
    selected=next((row for row in unprepared_graveyard_copy_occurrences(event)
                   if row["component_id"]==component_id),None)
    pair=prepared.outcome.pair
    if (selected is None or GraveyardCopyPair.from_dict(selected["pair"])!=pair
            or pair.controller!=event.payload.get("destination_controller")
            or not {card.object_id for card in pair.cards}<=set(event.entry_scope.reserved_zone_changes)):
        raise ValueError("The prepared graveyard copy changed its ordered reserved pair")
    values=prepared.outcome.copied_values
    form=immutable_fingerprint({"occurrence":component_id,"pair":pair.to_dict(),"values":values})
    forms=(*event.payload.get(GRAVEYARD_COPY_RESULTS_FIELD,()),
           {"component_id":component_id,"form":form,"additional_counters":prepared.outcome.additional_counters})
    payload=thaw_value(event.payload)
    payload.update(entry_copy=thaw_value(values),entry_copy_source=pair.copy_card.ref,entry_copy_form=form,
        object_types=sorted(set().union(*type_parts(values["type_line"]))))
    payload.pop(ENTRY_COPY_HISTORY_FIELD,None)
    payload[GRAVEYARD_COPY_RESULTS_FIELD]=list(forms)
    return replace(event,payload=payload)


def current_graveyard_entry_copy_counter(event):
    from ..replacement.operations import CreateAffectedObjectCounter

    if event.payload.get("destination")!="battlefield":
        return None
    active=event.payload.get("entry_copy_form")
    for row in event.payload.get(GRAVEYARD_COPY_RESULTS_FIELD,()):
        if row["form"]==active and row["additional_counters"]:
            return CreateAffectedObjectCounter("+1/+1",row["additional_counters"],
                event.payload["destination_controller"],row["component_id"])
    return None


def expand_graveyard_copy_zone_plans(host, changes, prepared, kinds):
    from ..zone_trigger_events import ZoneTransitionKind

    if not any(prepared[oid].graveyard_copy_exiles for oid,_ in changes):
        return tuple(changes),prepared,kinds
    plans, transitions = dict(prepared), dict(kinds)
    reserved = {oid for oid,_ in changes}
    prefix = []
    for oid,_ in changes:
        parent=prepared[oid]
        for row in parent.event.payload.get(GRAVEYARD_COPY_PAIRS_FIELD,()):
            current_graveyard_pair_cards(host,GraveyardCopyPair.from_dict(row["pair"]))
        from .linked_graveyard_entry import current_linked_graveyard_selection, linked_graveyard_entry_choices

        for captured in linked_graveyard_entry_choices(parent.event):
            current_linked_graveyard_selection(host, captured)
        for exile in parent.graveyard_copy_exiles:
            if exile.object_id in reserved:
                raise GameRuleError("A prepared entry exile repeats a zone-change object")
            reserved.add(exile.object_id)
            prefix.append((exile.object_id,exile.requested_destination))
            plans[exile.object_id]=exile
            transitions[exile.object_id]=ZoneTransitionKind.ORDINARY
    return (*prefix,*changes),plans,transitions
