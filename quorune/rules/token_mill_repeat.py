"""Closed compilation and committed follow-ups for one subtype-driven repeat."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from ..creature_subtypes import canonical_creature_subtype
from ..errors import GameRuleError
from ..replacement.immutable import FrozenMap, thaw_value
from .committed_quantity_followups import CommittedQuantityFollowup, fixed_followup_capabilities


TOKEN_MILL_REPEAT_OPERATION = "mill_and_repeat_token_on_subtype"
TOKEN_MILL_REPEAT_MECHANIC = "fixed-token-mill-subtype-repeat"
TOKEN_MILL_REPEAT_CAPABILITY = "zone.mill.repeat_token_on_subtype"
TOKEN_MILL_REPEAT_DEPENDENCIES = (
    "counter.producer.fixed_effect",
    "token.creation.fixed_definition",
    "zone.mill.fixed",
)


@dataclass(frozen=True, slots=True)
class TokenMillRepeatSpec:
    token: FrozenMap
    subtype: str

    def __post_init__(self) -> None:
        if (not isinstance(self.token, FrozenMap) or not self.subtype
                or canonical_creature_subtype(self.subtype) != self.subtype):
            raise ValueError("The repeat requires one fixed token and a canonical subtype")
        characteristics = self.token.get("characteristics")
        if (not isinstance(characteristics, Mapping)
                or set(characteristics) != {"type_line", "power", "toughness", "colors", "literal_token_name_version"}
                or not str(characteristics["type_line"]).startswith("Token Creature ")
                or "tapped" in self.token
                or not fixed_followup_capabilities(self.token)):
            raise ValueError("The repeat requires one ordinary fixed creature token definition")

    @classmethod
    def from_effect(cls, effect: Mapping[str, Any], *, actor: str | None = None) -> TokenMillRepeatSpec:
        if (not isinstance(effect, Mapping)
                or set(effect) != {"op", "schema_version", "token", "subtype"}
                or effect["op"] != TOKEN_MILL_REPEAT_OPERATION
                or type(effect["schema_version"]) is not int or effect["schema_version"] != 1
                or not isinstance(effect["token"], Mapping)):
            raise ValueError("The token mill repeat uses one exact effect schema")
        token = dict(effect["token"])
        permitted_controller = ("$controller",) if actor is None else ("$controller", actor)
        if token.get("controller") not in permitted_controller:
            raise ValueError("The token and mill must belong to the resolving ability's controller")
        token["controller"] = "$controller"
        return cls(FrozenMap(token), effect["subtype"])

    @property
    def capability_dependencies(self) -> tuple[str, ...]:
        return tuple(sorted({
            TOKEN_MILL_REPEAT_CAPABILITY,
            *TOKEN_MILL_REPEAT_DEPENDENCIES,
            *fixed_followup_capabilities(self.token),
        }))

    def to_effect(self) -> dict[str, Any]:
        return {"op": TOKEN_MILL_REPEAT_OPERATION, "schema_version": 1,
                "token": thaw_value(self.token), "subtype": self.subtype}


@dataclass(frozen=True, slots=True)
class TokenMillRepeatIntent:
    actor: str
    reason: str
    spec: TokenMillRepeatSpec
    source_id: str | None
    source_incarnation: str | None
    replacement_selections: tuple[str | FrozenMap, ...] = ()


def token_mill_repeat_node_capabilities(
    *, effects: Sequence[Mapping[str, Any]], target_schema: Mapping[str, Any] | None,
    mechanic_ids: Any,
) -> tuple[str, ...]:
    if (len(effects) != 2 or target_schema is not None
            or TOKEN_MILL_REPEAT_MECHANIC not in mechanic_ids
            or effects[1].get("op") != TOKEN_MILL_REPEAT_OPERATION):
        return ()
    try:
        spec = TokenMillRepeatSpec.from_effect(effects[1])
    except (TypeError, ValueError):
        return ()
    return spec.capability_dependencies if thaw_value(spec.token) == thaw_value(effects[0]) else ()


def is_closed_token_mill_repeat_program(program) -> bool:
    if (TOKEN_MILL_REPEAT_MECHANIC not in program.coverage
            or getattr(program, "event", None) != "activate"
            or getattr(program, "active_zone", None) != "battlefield"
            or getattr(program, "event_condition", None) is not None):
        return False
    required = token_mill_repeat_node_capabilities(
        effects=program.effects, target_schema=program.target_schema, mechanic_ids=program.coverage,
    )
    return bool(required) and set(required).issubset(program.capability_dependencies)


def has_public_milled_subtype(host, result, subtype: str) -> bool:
    from ..continuous_effect_model import Layer
    from ..zone_transition_model import PUBLIC_ZONES

    for outcome in result.cards:
        if outcome.destination not in PUBLIC_ZONES:
            continue
        card = host.state.cards.get(outcome.identity.object_id)
        if (card is None or card.face_down or card.zone != outcome.destination
                or card.logical_object_id != outcome.destination_logical_object_id):
            continue
        data = host._effective_card_data(card, maximum_layer=Layer.TYPE)
        _, subtypes, _ = host._type_parts(data["type_line"])
        if subtype in subtypes:
            return True
    return False


def execute_token_mill_repeat(host, intent: TokenMillRepeatIntent) -> CommittedQuantityFollowup:
    """Commit one native mill and enqueue only the ordered qualifying follow-up."""
    from ..milling import mill_cards, mill_request

    zones = host.state.players[intent.actor].zones
    initial_count = len(zones["library"])
    result = mill_cards(
        host, mill_request(host, actor=intent.actor, player=intent.actor, count=1, reason=intent.reason),
        replacement_selections=intent.replacement_selections,
    )
    if not has_public_milled_subtype(host, result, intent.spec.subtype):
        return CommittedQuantityFollowup(())
    if len(host.state.players[intent.actor].zones["library"]) >= initial_count:
        raise GameRuleError("The token mill repeat requires a committed departure from the library")
    source = host.state.cards.get(intent.source_id)
    continuation = (intent.spec.token, FrozenMap(intent.spec.to_effect()))
    if (source is None or source.zone != "battlefield" or source.phased_out
            or source.logical_object_id != intent.source_incarnation):
        return CommittedQuantityFollowup(continuation)
    counter = FrozenMap({"op": "place_counters", "card": "$source.zone_object", "counter": "loyalty",
                         "amount": 1, "source": "$source"})
    return CommittedQuantityFollowup((counter, *continuation))
