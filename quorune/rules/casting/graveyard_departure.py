"""Capture the opted-in lookback at cast announcement and expose it on commit."""

from dataclasses import dataclass

from ...errors import GameRuleError
from ...replacement.immutable import FrozenMap
from ...rules.event_subscriptions import GRAVEYARD_DEPARTURE_LOOKBACK_EVENT
from ...rules.grouped_graveyard_departure import COVERAGE
from ...trigger_discovery import dispatch_semantic_event
from ...zone_trigger_events import ZoneChangeOccurrence, normalized_zone_trigger_events, sealed_public_characteristic_facts
from ...zone_trigger_processing import DepartureTriggerSnapshot, capture_departure_trigger_sources


@dataclass(frozen=True, slots=True)
class GraveyardCastDeparture:
    logical_object_id: str
    zone_change_counter: int
    controller: str
    characteristics: FrozenMap
    sources: DepartureTriggerSnapshot


def capture_graveyard_cast_departure(host, card):
    if card.zone != "graveyard" or not card.is_card_object or card.is_token:
        return None
    if not any(COVERAGE in program.coverage
               for program in host.semantics.programs_for_event(GRAVEYARD_DEPARTURE_LOOKBACK_EVENT)):
        return None
    characteristics = FrozenMap(host._effective_card_data(card))
    if "creature" not in sealed_public_characteristic_facts(characteristics)["types"]:
        return None
    return GraveyardCastDeparture(
        card.logical_object_id, card.zone_change_counter, card.controller, characteristics,
        capture_departure_trigger_sources(host, semantic_events=True, origin="graveyard"),
    )


def collect_graveyard_cast_departure(host, card, departure, trigger_batch):
    if departure is None:
        return
    if card.zone != "stack" or card.zone_change_counter != departure.zone_change_counter + 1:
        raise GameRuleError("Graveyard cast departure is not the committed stack incarnation")
    occurrence = ZoneChangeOccurrence(
        object_id=card.object_id,
        card_ref=card.ref,
        owner=card.owner,
        origin="graveyard",
        destination="stack",
        previous_controller=departure.controller,
        current_controller=card.controller,
        previous_logical_object_id=departure.logical_object_id,
        current_logical_object_id=card.logical_object_id,
        zone_change_counter=card.zone_change_counter,
        token=False,
        card_object=True,
        previous_characteristics=departure.characteristics,
        current_characteristics=FrozenMap(host._effective_card_data(card)),
        cause="cast",
    )
    event, = normalized_zone_trigger_events(occurrence)
    context = {**event.context, "event_id": f"graveyard.cast:{departure.logical_object_id}"}
    sources = departure.sources
    dispatch_semantic_event(
        host, GRAVEYARD_DEPARTURE_LOOKBACK_EVENT, context,
        sources=sources.sources, source_zones=sources.source_zones,
        source_characteristics=sources.source_characteristics, trigger_batch=trigger_batch,
    )
