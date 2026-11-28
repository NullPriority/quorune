"""Return the exact public arrivals from an enchanted permanent's departure."""

from collections.abc import Mapping

from ..characteristic_evaluation import type_parts
from ..continuous_effect_state import ResolutionEffectSource
from ..effect_contracts import REANIMATE_OPERATION
from ..entry_counters import capture_prospective_entry_characteristics
from ..errors import GameRuleError
from ..external_entry_state_model import PERMANENT_CARD_TYPES
from .death_battlefield_return import DeathBattlefieldArrival
from .reanimation_followup import current_linked_subject


ENCHANTED_DEPARTURE_RETURN_VERSION = 22
ENCHANTED_DEPARTURE_RETURN_CAPABILITY = "zone.return.enchanted_departure_linked_battlefield"
ENCHANTED_DEPARTURE_RETURN_MECHANIC = "enchanted-departure-linked-return"
ENCHANTED_DEPARTURE_ARRIVALS = "enchanted_departure_arrivals"
ENCHANTED_DEPARTURE_SCOPE = "enchanted_departure_scope"
ENCHANTED_DEPARTURE_OBSERVER = "enchanted_departure_observer"

_ARRIVAL_ZONES = {"graveyard", "exile"}
_RUNTIME_FIELDS = {"_runtime_source", "_replacement_selections", "_aura_target_refs"}


def enchanted_departure_return_effect():
    return {
        "op": REANIMATE_OPERATION,
        "schema_version": ENCHANTED_DEPARTURE_RETURN_VERSION,
        ENCHANTED_DEPARTURE_ARRIVALS: "$context." + ENCHANTED_DEPARTURE_ARRIVALS,
        "controller": "$controller",
    }


def is_enchanted_departure_return(effect):
    return effect.get("op") == REANIMATE_OPERATION and (
        effect.get("schema_version") == ENCHANTED_DEPARTURE_RETURN_VERSION
        or ENCHANTED_DEPARTURE_ARRIVALS in effect
    )


def _decode_arrivals(value):
    if not isinstance(value, (list, tuple)):
        raise ValueError("Enchanted departure requires sealed graveyard or exile arrivals")
    arrivals = []
    for row in value:
        if not isinstance(row, Mapping) or type(row.get("zone")) is not str or row["zone"] not in _ARRIVAL_ZONES:
            raise ValueError("Enchanted departure arrivals require their actual destination")
        identity = DeathBattlefieldArrival.from_dict({key: item for key, item in row.items() if key != "zone"})
        arrivals.append((identity, row["zone"]))
    if len({identity.object_id for identity, _ in arrivals}) != len(arrivals):
        raise ValueError("Enchanted departure cannot repeat a physical arrival")
    return tuple(arrivals)


def validate_enchanted_departure_return(effect, *, materialized=False):
    expected = enchanted_departure_return_effect()
    required = set(expected)
    allowed = required | (_RUNTIME_FIELDS if materialized else set())
    if (not isinstance(effect, Mapping) or not required <= set(effect) <= allowed
            or effect["op"] != REANIMATE_OPERATION or type(effect["schema_version"]) is not int
            or effect["schema_version"] != ENCHANTED_DEPARTURE_RETURN_VERSION):
        raise ValueError("Enchanted departure requires its exact linked return instruction")
    if not materialized:
        if effect != expected:
            raise ValueError("Enchanted departure must bind the event arrivals and trigger controller")
        return ()
    controller = effect["controller"]
    if type(controller) is not str or not controller or controller.startswith("$"):
        raise ValueError("Enchanted departure requires its resolving controller")
    if not isinstance(effect.get("_replacement_selections", ()), (list, tuple)):
        raise ValueError("Enchanted departure replacements require an ordered sequence")
    arrivals = _decode_arrivals(effect[ENCHANTED_DEPARTURE_ARRIVALS])
    targets = effect.get("_aura_target_refs", {})
    if (not isinstance(targets, Mapping) or not set(targets) <= {row.object_id for row, _ in arrivals}
            or any(type(ref) is not str or not ref for ref in targets.values())):
        raise ValueError("An Aura entry choice must belong to its original departure arrival")
    return arrivals


def _validate_departure_observer(item, source):
    context = item.context
    observer = context.get(ENCHANTED_DEPARTURE_OBSERVER)
    attachments = context.get("attachments")
    if (not isinstance(observer, Mapping)
            or set(observer) != {"object_id", "logical_object_id", "reference", "controller"}
            or any(type(value) is not str or not value for value in observer.values())
            or observer["object_id"] != item.source_object_id
            or observer["logical_object_id"] != context.get("source_logical_object_id")
            or not isinstance(attachments, (list, tuple)) or observer["reference"] not in attachments):
        raise ValueError("Enchanted departure lost its look-back Aura observer")
    if "copied_from_stack" in context:
        copied_from = context["copied_from_stack"]
        if type(copied_from) is not str or not copied_from or copied_from == item.ref:
            raise ValueError("An enchanted departure copy requires its original stack identity")
    elif observer["controller"] != item.controller:
        raise ValueError("An original enchanted departure must keep its recorded observer controller")
    if source.object_id is not None and (
        source.object_id != observer["object_id"] or source.logical_object_id != observer["logical_object_id"]
        or source.card_ref != observer["reference"]
    ):
        raise ValueError("Enchanted departure changed its resolving source")


def _resolving_departure(host, effect, actor, arrivals):
    source = ResolutionEffectSource.from_effect(effect)
    item = next((row for row in host.state.stack if row.ref == source.stack_ref), None)
    if (item is None or item.kind != "triggered_ability" or item.controller != actor or item.targets
            or effect["controller"] != actor or item.context.get("currently_resolving") is not True):
        raise ValueError("Enchanted departure requires its original resolving trigger and controller")
    context = item.context
    scope = context.get(ENCHANTED_DEPARTURE_SCOPE)
    if (context.get("event") != "permanent.leave" or context.get("from") != "battlefield"
            or context.get("to") not in _ARRIVAL_ZONES
            or not isinstance(scope, Mapping)
            or set(scope) != {"object_id", "reference", "previous_logical_object_id"}
            or any(type(value) is not str or not value for value in scope.values())
            or scope["reference"] != context.get("card")
            or scope["previous_logical_object_id"] != context.get("card_object_identity")
            or _decode_arrivals(context.get(ENCHANTED_DEPARTURE_ARRIVALS)) != arrivals):
        raise ValueError("Enchanted departure lost its exact battlefield occurrence and public arrivals")
    _validate_departure_observer(item, source)
    if "composition" not in context and any(
            row.object_id != scope["object_id"] or row.reference != scope["reference"]
            or row.zone_change_counter != context.get("card_zone_change_counter")
            or row.owner != context.get("owner") or zone != context["to"] or context.get("token") is True
            for row, zone in arrivals):
        raise ValueError("An ordinary departure must keep its original physical arrival")


def _current_arrival_cards(host, arrivals):
    cards = []
    for identity, zone in arrivals:
        card = current_linked_subject(host, {
            "object_id": identity.object_id,
            "ref": identity.reference,
            "zone_change_counter": identity.zone_change_counter,
        }, zone=zone)
        if (card is None or card.owner != identity.owner or card.owner not in host.active_seats
                or not card.is_card_object or card.is_token):
            continue
        _, entry_type_line = capture_prospective_entry_characteristics(host, card=card, enter_face=None)
        if type_parts(entry_type_line)[0] & PERMANENT_CARD_TYPES:
            cards.append(card)
    return tuple(cards)


def apply_enchanted_departure_return(host, effect, *, actor, reason):
    from ..zone_transitions import ZoneTransitionOwner

    try:
        arrivals = validate_enchanted_departure_return(effect, materialized=True)
        _resolving_departure(host, effect, actor, arrivals)
    except (TypeError, ValueError) as exc:
        raise GameRuleError(str(exc)) from exc
    cards = _current_arrival_cards(host, arrivals)
    if not cards:
        return ()
    controllers = {card.object_id: actor for card in cards}
    targets = effect.get("_aura_target_refs", {})
    moved = ZoneTransitionOwner(host).move_cards_simultaneously(
        tuple((card.object_id, "battlefield") for card in cards),
        reason=reason,
        destination_controllers=controllers,
        putting_players=controllers,
        replacement_selections=tuple(effect.get("_replacement_selections", ())),
        aura_target_refs={oid: targets[oid] for oid in controllers if oid in targets},
    )
    return tuple(card.ref for card in moved if card.zone == "battlefield")
