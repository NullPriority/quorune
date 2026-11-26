"""A single token definition uses the number of committed APNAP sacrifices."""

from collections.abc import Mapping

from ..apnap_object_payment import OTHER_PLAYER_SACRIFICE_SCOPE
from ..object_predicate import ObjectQuerySpec
from ..replacement.immutable import FrozenMap
from .committed_quantity_followups import fixed_followup_capabilities, fixed_quantity_effect, quantity_followup_from_count
from .effect_shape_walkers import nested_effects


COUNTED_SACRIFICE_TOKEN_FIELD = "committed_sacrifice_token"
COUNTED_SACRIFICE_TOKEN_CAPABILITY = "token.creation.committed_sacrifice_count"
COUNTED_SACRIFICE_TOKEN_TEMPLATE = "committed-apnap-sacrifice-token-v1"
_CREATURES = ObjectQuerySpec(zones=("battlefield",), types_all=("creature",))


def counted_sacrifice_tokens_effect(players, token, *, actor="$controller"):
    return {
        "op": "choose_cards_apnap", "actor": actor, "players": players, "zone": "battlefield",
        "predicate": _CREATURES.to_dict(), "count": 1, "then": "sacrifice",
        "prompt": "Choose the required permanent(s) to sacrifice.",
        COUNTED_SACRIFICE_TOKEN_FIELD: {"schema_version": 1, "token": token},
    }


def _validated_token(token, *, actor="$controller"):
    if (not isinstance(token, Mapping) or token.get("controller") != actor
            or token.get("name") not in ("Food Token", "Zombie Token") or "tapped" in token):
        raise ValueError("Sacrifices must supply the printed controller's fixed ordinary token")
    canonical = fixed_quantity_effect({**token, "controller": "$controller"})
    if canonical["name"] == "Zombie Token":
        characteristics = canonical["characteristics"]
        if (characteristics.get("power") != "2" or characteristics.get("toughness") != "2"
                or characteristics.get("colors") != ("B",)):
            raise ValueError("The committed sacrifice Zombie requires its fixed black 2/2 definition")
    return canonical


def counted_sacrifice_token_parts(effect, *, actor="$controller"):
    if not isinstance(effect, Mapping):
        raise ValueError("Counted sacrifice token requires the represented APNAP effect")
    spec = effect.get(COUNTED_SACRIFICE_TOKEN_FIELD)
    if (not isinstance(spec, Mapping) or set(spec) != {"schema_version", "token"}
            or type(spec["schema_version"]) is not int or spec["schema_version"] != 1):
        raise ValueError("Counted sacrifice token requires the exact committed result version")
    token = _validated_token(spec["token"], actor=actor)
    players = effect.get("players")
    expected = OTHER_PLAYER_SACRIFICE_SCOPE if token["name"] == "Zombie Token" else "opponents"
    if (players != expected or type(effect.get("count")) is not int
            or FrozenMap(effect) != FrozenMap(counted_sacrifice_tokens_effect(players, spec["token"], actor=actor))):
        raise ValueError("Counted token must preserve all printed eligible sacrifice participants")
    return players, token


def counted_sacrifice_token_commit_fields(effect, state, *, actor, selected_refs, incarnation_field):
    if COUNTED_SACRIFICE_TOKEN_FIELD not in effect:
        return {}
    _players, token = counted_sacrifice_token_parts(effect, actor=actor)
    if not selected_refs:
        return {}
    origins = state[incarnation_field]
    retained = {ref: origins[ref] for ref in selected_refs}
    return {COUNTED_SACRIFICE_TOKEN_FIELD: {
        "schema_version": 1, "token": token, "selected_incarnations": retained,
    }}


def counted_sacrifice_token_capabilities(effect, target_schema):
    if target_schema is not None:
        raise ValueError("Counted sacrifice tokens require the original untargeted group")
    players, token = counted_sacrifice_token_parts(effect)
    return (COUNTED_SACRIFICE_TOKEN_CAPABILITY, "choice.affected_player.fixed_sacrifice",
            "zone.change.destination_replacement", *fixed_followup_capabilities(token),
            *(("trigger.event.normalized_zone_change", "trigger.placement.apnap") if players == "opponents" else ()))


def counted_sacrifice_token_runtime_dependencies(program):
    marked = tuple(effect for effect in nested_effects((*program.effects, *program.handlers))
                   if COUNTED_SACRIFICE_TOKEN_FIELD in effect)
    if not marked and COUNTED_SACRIFICE_TOKEN_CAPABILITY not in program.capability_dependencies:
        return ()
    if len(marked) != 1 or tuple(program.effects) != marked or program.handlers or program.cost_schema is not None:
        raise ValueError("Counted sacrifice tokens require their complete single source result")
    players, _token = counted_sacrifice_token_parts(marked[0])
    original = ("resolve", "stack") if players == OTHER_PLAYER_SACRIFICE_SCOPE else ("permanent.enter.self", "battlefield")
    if (program.event, program.active_zone) != original or program.event_condition is not None:
        raise ValueError("Counted sacrifice tokens require the original spell or self entry")
    return counted_sacrifice_token_capabilities(marked[0], program.target_schema)


def counted_sacrifice_token_commit_parts(payload, refs, *, actor):
    if (not isinstance(payload, Mapping) or set(payload) != {"schema_version", "token", "selected_incarnations"}
            or type(payload["schema_version"]) is not int or payload["schema_version"] != 1
            or not isinstance(payload["selected_incarnations"], Mapping)
            or not refs or set(payload["selected_incarnations"]) != set(refs)
            or any(type(value) is not str or not value for value in payload["selected_incarnations"].values())
            or len(set(payload["selected_incarnations"].values())) != len(refs)):
        raise ValueError("Counted sacrifice token requires every selected original incarnation")
    token = payload["token"]
    controller = token.get("controller") if isinstance(token, Mapping) else None
    if controller not in ("$controller", actor):
        raise ValueError("Counted sacrifice tokens lost their resolving controller")
    return _validated_token(token, actor=controller), FrozenMap(payload["selected_incarnations"])


def committed_sacrifice_count(host, origins):
    committed = 0
    for object_id, old in origins:
        current = host.state.cards.get(object_id)
        if current is None or current.logical_object_id != old:
            committed += 1
    return committed


def token_followup_after_sacrifices(host, token, origins):
    return quantity_followup_from_count(token, committed_sacrifice_count(host, origins))
