"""Resolve one token for each current qualifying controller before ordinary destruction."""

from collections.abc import Mapping

from ..affected_permanents import AffectedPermanentSetSpec
from ..fixed_token_production import FIXED_DISTRIBUTED_TOKEN_OPERATION, FixedTokenCreatorScope
from ..object_predicate import ObjectQuerySpec
from ..replacement.immutable import FrozenMap, thaw_value
from .committed_quantity_followups import fixed_quantity_effect
from .effect_shape_walkers import nested_effects


QUALIFIED_TOKEN_CREATORS_CAPABILITY = "token.creation.per_qualified_controller_before_destruction"
CREATOR_QUERY_FIELD = "creator_query"
CREATOR_SEATS_FIELD = "creator_seats"
_QUALIFYING_OBJECTS = ObjectQuerySpec(zones=("battlefield",), types_any=("artifact", "enchantment"))
_DESTROY_QUALIFYING = FrozenMap({
    "op": "destroy_all", "source": "$source", "set": AffectedPermanentSetSpec(_QUALIFYING_OBJECTS).to_dict(),
})


def uses_qualified_token_creators(value):
    return isinstance(value, Mapping) and CREATOR_QUERY_FIELD in value


def _creator_query():
    return {"schema_version": 1, "predicate": _QUALIFYING_OBJECTS.to_dict()}


def qualified_creator_token_effect(token):
    token = fixed_quantity_effect(token)
    if (token.get("op") != "create_token" or "tapped" in token
            or not token["characteristics"].get("type_line", "").startswith("Token Creature — ")):
        raise ValueError("Qualified token creators require one ordinary fixed creature token")
    return {"op": FIXED_DISTRIBUTED_TOKEN_OPERATION, "controller": "$controller",
            "token": thaw_value(token), CREATOR_QUERY_FIELD: _creator_query()}


def _validate(effect, *, resolved=False):
    allowed = {"op", "controller", "token", CREATOR_QUERY_FIELD}
    if resolved:
        allowed.add(CREATOR_SEATS_FIELD)
    token = {**effect["token"], "controller": "$controller"}
    expected = qualified_creator_token_effect(token)
    if (set(effect) != allowed or effect["op"] != expected["op"]
            or not resolved and (effect["controller"] != "$controller" or effect["token"]["controller"] != "$controller")):
        raise ValueError("Qualified token creator instruction has a closed source shape")
    query = effect[CREATOR_QUERY_FIELD]
    if (not isinstance(query, Mapping) or set(query) != {"schema_version", "predicate"}
            or type(query["schema_version"]) is not int or query["schema_version"] != 1
            or ObjectQuerySpec.from_dict(query["predicate"]) != _QUALIFYING_OBJECTS):
        raise ValueError("Qualified token creators require the current artifact-or-enchantment set")


def project_qualified_token_creators(effect):
    """Validate the qualified leaf before deriving its ordinary token dependencies."""
    _validate(effect)
    return {"op": FIXED_DISTRIBUTED_TOKEN_OPERATION, "controller": "$controller",
            "token": effect["token"], "scope": FixedTokenCreatorScope.EACH_PLAYER.value}


def qualified_token_creator_node_capabilities(effects, target_schema, mechanic_ids):
    if target_schema is not None or len(effects) != 2 or FrozenMap(effects[1]) != _DESTROY_QUALIFYING:
        return ()
    try:
        projected = project_qualified_token_creators(effects[0])
    except (TypeError, ValueError, KeyError):
        return ()
    from .bound_effect_program_shapes import bound_effect_program_node_capabilities
    owned = bound_effect_program_node_capabilities(
        effects=(projected, effects[1]), target_schema=None, mechanic_ids=mechanic_ids,
    )
    return (*owned, QUALIFIED_TOKEN_CREATORS_CAPABILITY) if owned else ()


def qualified_token_creator_program_dependencies(program):
    marked = tuple(effect for effect in nested_effects((program.effects, program.handlers))
                   if uses_qualified_token_creators(effect))
    if not marked and QUALIFIED_TOKEN_CREATORS_CAPABILITY not in program.capability_dependencies:
        return ()
    if (len(marked) != 1 or tuple(program.effects[:1]) != marked or program.handlers
            or (program.event, program.active_zone) != ("resolve", "stack") or program.cost_schema is not None):
        raise ValueError("Qualified token creators require their complete original spell resolution")
    owned = qualified_token_creator_node_capabilities(program.effects, program.target_schema, program.coverage)
    if not owned:
        raise ValueError("Qualified tokens must precede the original artifact-and-enchantment destruction")
    return owned


def bind_qualified_token_creators(host, effect):
    if not uses_qualified_token_creators(effect):
        return effect
    if CREATOR_SEATS_FIELD in effect:
        return effect
    _validate(effect)
    from ..continuous_effect_state import matching_battlefield_objects

    controllers = {card.controller for card in matching_battlefield_objects(host, _QUALIFYING_OBJECTS)}
    creators = [seat for seat in host.apnap_order() if seat in controllers]
    return {**effect, CREATOR_SEATS_FIELD: creators} if creators else None


def resolved_qualified_token_creators(effect, apnap):
    _validate(effect, resolved=True)
    seats = effect[CREATOR_SEATS_FIELD]
    if (not isinstance(seats, (list, tuple)) or not seats
            or any(type(seat) is not str for seat in seats)
            or tuple(seats) != tuple(seat for seat in apnap if seat in seats)):
        raise ValueError("Qualified token creation requires its distinct active captured creators")
    return tuple(seats)
