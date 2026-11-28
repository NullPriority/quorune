from __future__ import annotations

"""One resolving cast bound to an actual discard and the original source's counters."""

from collections.abc import Mapping
from dataclasses import dataclass

from ..discard_effect_origin import DISCARDED_CARD_CAST
from ..discover_model import discover_options_fingerprint
from ..errors import GameRuleError, StateInvariantError
from ..replacement.immutable import FrozenMap, freeze_value, thaw_value


DISCARDED_CARD_CAST_CAPABILITY = "casting.resolution.discarded_card_after_source_counters"
DISCARDED_CARD_CAST_MECHANIC = "discarded-card-cast-after-source-counters"
DISCARDED_CARD_CAST_OBSERVATION = "discard_cast_source_counters"
_DISCARD_FIELD = "_discarded"
_OPTIONS_FIELD = "_cast_options_fingerprint"
_PUBLIC_CAST_ZONES = ("graveyard", "exile")
_DEPENDENCIES = (
    DISCARDED_CARD_CAST_CAPABILITY, "counter.producer.fixed_effect", "counter.producer.fixed_event_trigger",
    "counter.placement.quantity_replacement", "zone.draw.library_to_hand",
    "zone.change.destination_replacement", "trigger.event.normalized_damage",
    "trigger.effect.fixed_event", "trigger.placement.apnap",
)


def discarded_card_cast_instruction(player="$controller"):
    return {"op": DISCARDED_CARD_CAST, "schema_version": 1, "player": player}


def discarded_card_cast_effects():
    return (
        {"op": "place_counters", "card": "$source.zone_object",
         "counter": "chorus", "amount": 1, "source": "$source"},
        {"op": "draw", "player": "$controller", "count": 1, "private": True},
        discarded_card_cast_instruction(),
    )


def discarded_card_cast_node_capabilities(effects, target_schema):
    if target_schema is not None or FrozenMap({"effects": effects}) != FrozenMap({"effects": discarded_card_cast_effects()}):
        return ()
    counter, draw, cast = effects
    if type(counter["amount"]) is not int or type(draw["count"]) is not int or draw["private"] is not True:
        return ()
    return _DEPENDENCIES if type(cast["schema_version"]) is int else ()


def discarded_card_cast_runtime_dependencies(program):
    from .combat_damage_library_access import COMBAT_DAMAGE_LIBRARY_CONDITION
    from .effect_shape_walkers import nested_effect_operations

    if DISCARDED_CARD_CAST not in nested_effect_operations((program.effects, program.handlers)):
        return ()
    deps = discarded_card_cast_node_capabilities(program.effects, program.target_schema)
    if (not deps or program.event != "damage.dealt.self" or program.active_zone != "battlefield"
            or program.cost_schema is not None or program.handlers
            or FrozenMap(program.event_condition or {}) != COMBAT_DAMAGE_LIBRARY_CONDITION):
        raise ValueError("Discard casting requires the exact source combat-damage trigger and ordered instructions")
    return deps


def discarded_card_cast_source_context(host, source, program):
    if not discarded_card_cast_runtime_dependencies(program):
        return {}
    current = host.state.cards.get(source.object_id)
    is_current = (current is not None and current.zone == "battlefield"
                  and current.logical_object_id == source.logical_object_id)
    return {
        "source_logical_object_id": source.logical_object_id,
        DISCARDED_CARD_CAST_OBSERVATION: {
            "counter": "chorus",
            "departure_count": None if is_current else source.counters.get("chorus", 0),
            "subject": {"reference": source.ref, "logical_object_id": source.logical_object_id},
        },
    }


def _source_observation(context):
    observed = context.get(DISCARDED_CARD_CAST_OBSERVATION)
    if not isinstance(observed, Mapping) or set(observed) != {"counter", "departure_count", "subject"}:
        raise ValueError("Discard casting lost its retained source observation")
    subject = observed["subject"]
    count = observed["departure_count"]
    if (observed["counter"] != "chorus"
            or not isinstance(subject, Mapping) or set(subject) != {"reference", "logical_object_id"}
            or any(type(value) is not str or not value for value in subject.values())
            or count is not None and (type(count) is not int or count < 0)):
        raise ValueError("Discard casting has malformed source facts")
    return observed, subject


def pin_discarded_card_cast_departure(host, cards, *, error_type):
    from .event_counter_threshold import pin_counter_observation_departures

    pin_counter_observation_departures(
        host, cards, context_field=DISCARDED_CARD_CAST_OBSERVATION,
        read_observation=_source_observation, error_type=error_type,
    )


def _chorus_count(host, item):
    observed, subject = _source_observation(item.context)
    original = subject["logical_object_id"]
    if (original != item.context.get("source_logical_object_id")
            or original.rpartition("@")[0] != item.source_object_id):
        raise ValueError("Discard casting no longer refers to the original source")
    card = host.state.cards.get(item.source_object_id)
    count = observed["departure_count"]
    if card is not None and card.zone == "battlefield" and card.logical_object_id == original:
        count = card.counters.get("chorus", 0)
    if type(count) is not int or count < 0:
        raise ValueError("The original source's chorus counters are unavailable")
    return count


def validate_discarded_card_identity(value):
    if not isinstance(value, Mapping) or set(value) != {"previous", "ref"}:
        raise ValueError("Discard casting requires one closed discard identity")
    old, ref = value["previous"], value["ref"]
    if any(type(field) is not str or not field for field in (old, ref)):
        raise ValueError("Discard casting requires the original hand incarnation and reference")
    object_id, _, count = old.rpartition("@")
    if not object_id or not count.isascii() or not count.isdecimal():
        raise ValueError("Discard casting has an invalid hand incarnation")
    return object_id, int(count)


def _actual_discard(host, *, item, actor, discard):
    program = host.semantics.get(item.semantic_key) if item is not None and item.semantic_key else None
    if (item is None or item.kind != "triggered_ability" or item.controller != actor
            or actor not in host.active_seats or item.targets or not item.context.get("currently_resolving")
            or program is None or not host.semantic_program_is_current_trusted(program)
            or not discarded_card_cast_runtime_dependencies(program)):
        raise GameRuleError("Discard casting lost its original resolving trigger")
    if _chorus_count(host, item) < 4:
        return None
    object_id, before = validate_discarded_card_identity(discard)
    card = host.state.cards.get(object_id)
    if (card is None or card.ref != discard["ref"] or card.owner != actor
            or card.zone_change_counter != before + 1 or card.zone not in _PUBLIC_CAST_ZONES
            or card.face_down or not card.is_card_object):
        return None
    if not any(event.actor == actor and event.object_incarnation == discard["previous"]
               for event in host._current_turn_history("card_discarded")):
        return None
    return card


def _discarded_cast_pairs(host, *, item, actor, discard):
    from .casting.face_queries import offered_cast_methods
    from .casting.proposal import build_cast_offer

    card = _actual_discard(host, item=item, actor=actor, discard=discard)
    if card is None:
        return ()
    pairs = []
    for method, face in offered_cast_methods(host, card):
        if method is not None:
            continue
        proposal = build_cast_offer(host, actor, card, face_name=face,
                                    resolving_payment="free", resolving_origin=card.zone)
        if proposal.offer is None:
            continue
        offer = proposal.offer.to_dict(include_fingerprint=False)
        for price in offer["cost_options"]:
            canonical = {**price, "card": card.ref, "face": offer.get("face"), "from": card.zone}
            option_id = "discarded-card:" + discover_options_fingerprint((canonical,))
            pairs.append(({**canonical, "id": option_id}, canonical, card))
    return tuple(pairs)


def discarded_card_cast_options(host, *, item, actor, effect):
    return tuple(public for public, _, _ in
                 _discarded_cast_pairs(host, item=item, actor=actor, discard=effect[_DISCARD_FIELD]))


@dataclass(frozen=True, slots=True)
class DiscardedCardCastIntent:
    stack_ref: str
    actor: str
    discard: FrozenMap
    options_fingerprint: str
    response: FrozenMap
    replacement_selections: tuple[str | FrozenMap, ...] = ()

    def __post_init__(self):
        validate_discarded_card_identity(self.discard)
        for field in ("discard", "response"):
            value = getattr(self, field)
            if not isinstance(value, Mapping):
                raise ValueError("A discarded-card cast requires closed instruction and response data")
            object.__setattr__(self, field, FrozenMap(value))
        if (any(type(value) is not str or not value for value in
                (self.stack_ref, self.actor, self.options_fingerprint))
                or any(key.startswith("_") for key in self.response)):
            raise ValueError("The discarded-card cast intent is malformed")
        selections = freeze_value(self.replacement_selections, field="discarded-card cast replacements")
        if not isinstance(selections, tuple) or any(
            not isinstance(selection, FrozenMap) and (type(selection) is not str or not selection)
            for selection in selections
        ):
            raise ValueError("The discarded-card cast requires canonical replacement selections")
        object.__setattr__(self, "replacement_selections", selections)

    def identity(self):
        return {"stack_ref": self.stack_ref, "actor": self.actor, "discard": thaw_value(self.discard),
                "options_fingerprint": self.options_fingerprint, "response": thaw_value(self.response)}


def validate_discarded_card_cast_intent_identity(value):
    if (not isinstance(value, Mapping)
            or set(value) != {"stack_ref", "actor", "discard", "options_fingerprint", "response"}):
        raise ValueError("Discarded-card cast replacement identity is malformed")
    return DiscardedCardCastIntent(**value).identity()


def execute_discarded_card_cast(host, intent):
    from .resolving_cast_payments import commit_resolving_cast

    item = next((row for row in host.state.stack if row.ref == intent.stack_ref), None)
    pairs = _discarded_cast_pairs(host, item=item, actor=intent.actor, discard=intent.discard)
    if discover_options_fingerprint(public for public, _, _ in pairs) != intent.options_fingerprint:
        raise GameRuleError("The discarded card's legal casting options changed")
    response = thaw_value(intent.response)
    matches = [(option, card) for public, option, card in pairs
               if response.get("cost_option") in (None, public["id"])
               and response.get("card", card.ref) == card.ref
               and response.get("from", card.zone) == card.zone
               and response.get("face") in (None, option["face"])]
    if (len(matches) != 1 or response.get("cast_method") is not None
            or "x" in response and (type(response["x"]) is not int or response["x"] != 0)):
        raise GameRuleError("Select one offered cast of the exact discarded card with X equal to zero")
    option, card = matches[0]
    old = card.logical_object_id
    card_id = card.object_id
    origin = card.zone
    response.update(card=card.ref, cost_option=option["id"], auto_pay=True)
    response["from"] = origin
    if option["face"] is not None:
        response["face"] = option["face"]
    commit_resolving_cast(host, actor=intent.actor, response=response, origin=origin,
                          payment_id=f"{item.ref}:discarded-card:{old}",
                          replacement_selections=intent.replacement_selections)
    spell = host.state.cards[card_id]
    if (spell.zone != "stack" or spell.controller != intent.actor or spell.logical_object_id == old
            or not any(row.kind == "spell" and row.card_object_id == card_id for row in host.state.stack)):
        raise StateInvariantError("The selected discarded card created no spell")
