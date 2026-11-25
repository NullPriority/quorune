"""Capture and compare the original Evolve entrant's public characteristics."""

from dataclasses import replace

from ..errors import GameRuleError
from ..evolve import (
    EVOLVE_ENTRANT_CONTEXT,
    EVOLVE_ENTRANT_LKI_VERSION_FIELD,
    EvolveCharacteristics,
    EvolveEntrantObservation,
    EvolveError,
    evolve_condition_holds,
    evolve_entrant_lki_version,
)
from ..trigger_processing import pin_trigger_context_entry


def evolve_characteristics(host, card):
    types, _, _ = host._type_parts(str(host._effective_card_data(card).get("type_line") or ""))
    return EvolveCharacteristics(
        is_creature="creature" in types,
        power=host._numeric_stat(card.object_id, "power"),
        toughness=host._numeric_stat(card.object_id, "toughness"),
    )


def evolve_entrant_observation_enabled(state):
    return evolve_entrant_lki_version(getattr(state, EVOLVE_ENTRANT_LKI_VERSION_FIELD, None)) is not None


def evolve_entrant_trigger_context(host, context):
    if not evolve_entrant_observation_enabled(host.state):
        return {}
    try:
        entrant = EvolveEntrantObservation.from_context(context, use_observation=False)
    except EvolveError as exc:
        raise GameRuleError(str(exc)) from exc
    return {EVOLVE_ENTRANT_CONTEXT: entrant.to_dict()}


def evolve_event_condition_holds(host, source, context):
    expected_source = context.get("source_logical_object_id")
    resolving = expected_source is not None
    if resolving and (type(expected_source) is not str or not expected_source):
        raise GameRuleError("Evolve source logical identity must be a nonempty string")
    if (source.zone != "battlefield" or source.phased_out
            or resolving and source.logical_object_id != expected_source):
        return False
    try:
        use_observation = resolving and evolve_entrant_observation_enabled(host.state)
        observation = EvolveEntrantObservation.from_context(context, use_observation=use_observation)
    except EvolveError as exc:
        raise GameRuleError(str(exc)) from exc
    if not resolving and context.get("controller") != source.controller:
        return False
    entered = next((card for card in host.state.cards.values() if observation.identifies(card)), None)
    if entered is not None and entered.zone == "battlefield" and not entered.phased_out:
        recipient = evolve_characteristics(host, entered)
    else:
        recipient = observation.last_known
    return recipient is not None and evolve_condition_holds(evolve_characteristics(host, source), recipient)


def pin_evolve_entrant_departures(host, cards, *, error_type=GameRuleError):
    if not evolve_entrant_observation_enabled(host.state):
        return
    departing = {(card.ref, card.zone_change_counter): card
                 for card in cards if card.zone == "battlefield" and not card.phased_out}
    if not departing:
        return

    def capture(context):
        entrant = EvolveEntrantObservation.from_context(context, use_observation=True)
        card = departing.get((entrant.reference, entrant.zone_change_counter))
        if card is None or entrant.last_known is not None:
            return None
        return replace(entrant, last_known=evolve_characteristics(host, card)).to_dict()

    try:
        pin_trigger_context_entry(host, EVOLVE_ENTRANT_CONTEXT, capture)
    except (KeyError, TypeError, ValueError) as exc:
        raise error_type(str(exc)) from exc
