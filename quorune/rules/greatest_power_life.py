"""Read the targeted player's greatest current creature power for a life gain."""

from collections.abc import Mapping

from ..object_predicate import ObjectQuerySpec, object_query_capabilities
from ..object_query import exact_numeric_characteristic, query_objects
from ..replacement.immutable import FrozenMap


GREATEST_POWER_LIFE_KIND = "target_player_greatest_creature_power"
GREATEST_POWER_LIFE_CAPABILITY = "life.gain.target_player_greatest_creature_power"
GREATEST_POWER_LIFE_SCHEMA = {"zones": ["player"], "categories": ["player"], "player_relation": "any", "count": 1}


def greatest_power_life_effect():
    return {"op": "life", "player": "$target.0",
            "delta": {"kind": GREATEST_POWER_LIFE_KIND, "schema_version": 1, "player": "$target.0"}}


def uses_greatest_power_life_effect(effect):
    value = effect.get("delta") if isinstance(effect, Mapping) else None
    return isinstance(value, Mapping) and value.get("kind") == GREATEST_POWER_LIFE_KIND


def greatest_power_life_node_capabilities(*, effects, target_schema, mechanic_ids):
    if (len(effects) != 1 or not uses_greatest_power_life_effect(effects[0])
            or type(effects[0]["delta"].get("schema_version")) is not int
            or FrozenMap(effects[0]) != FrozenMap(greatest_power_life_effect())
            or FrozenMap(target_schema or {}) != FrozenMap(GREATEST_POWER_LIFE_SCHEMA)
            or not {"cr-115-targets", "cr-119-life"} <= set(mechanic_ids)):
        return ()
    query = ObjectQuerySpec(zones=("battlefield",), types_all=("creature",), controller="$target.0")
    return (GREATEST_POWER_LIFE_CAPABILITY, "target.revalidate_resolution", "life.change.effect",
            *object_query_capabilities(query))


def resolve_greatest_power_life_value(host, value, *, resolve):
    if (set(value) != {"kind", "schema_version", "player"}
            or type(value["schema_version"]) is not int or value["schema_version"] != 1
            or type(value["player"]) is not str or not value["player"].startswith("$target.")):
        raise ValueError("Greatest power life gain requires the original targeted player")
    player = resolve(value["player"])
    if player is None:
        return 0
    if player not in host.state.players:
        raise ValueError("Greatest power life gain lost its original player")
    query = ObjectQuerySpec(zones=("battlefield",), types_all=("creature",), controller=player)
    rows = query_objects(
        (host._public_object_query_result(card) for card in host.state.cards.values()
         if card.zone == "battlefield" and card.controller == player and not card.phased_out),
        query,
    )
    highest = 0
    for row in rows:
        card = host.state.cards[row.object_id]
        power = exact_numeric_characteristic(card, host._effective_card_data(card), "power")
        if type(power) is not int:
            raise ValueError("Greatest power life gain requires a represented creature power")
        highest = max(highest, power)
    return highest
