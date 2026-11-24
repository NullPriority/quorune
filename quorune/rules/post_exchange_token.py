from __future__ import annotations

"""Materialize one token from the legal players' life totals after a paired exchange."""

from collections.abc import Mapping

from ..fixed_token_production import predefined_token_identity
from ..replacement.immutable import thaw_value


POST_EXCHANGE_TOKEN_VERSION_FIELD = "post_exchange_life_gap_version"
POST_EXCHANGE_TOKEN_CAPABILITY = "token.creation.post_exchange_life_gap"


def post_exchange_token_instruction():
    name, characteristics = predefined_token_identity("Horror", {
        "type_line": "Token Artifact Creature — Horror",
        "colors": [],
        "power": "1",
        "toughness": "1",
    })
    return {
        "op": "create_token", "controller": "$controller", "quantity": 1,
        "name": name, "characteristics": characteristics, POST_EXCHANGE_TOKEN_VERSION_FIELD: 1,
    }


def validate_post_exchange_token_instruction(value):
    if (
        not isinstance(value, Mapping) or type(value.get(POST_EXCHANGE_TOKEN_VERSION_FIELD)) is not int
        or type(value.get("quantity")) is not int
        or thaw_value(value) != post_exchange_token_instruction()
    ):
        raise ValueError("Post-exchange creation requires its complete fixed token definition")


def materialize_post_exchange_token(controller, players, current_life):
    if len(players) != 2 or any(type(seat) is not str for seat in players):
        return None
    first, second = players
    if first == second or first not in current_life or second not in current_life:
        return None
    totals = current_life[first], current_life[second]
    if any(type(total) is not int for total in totals):
        raise ValueError("Post-exchange token size requires two exact live totals")
    size = str(abs(totals[0] - totals[1]))
    result = post_exchange_token_instruction()
    del result[POST_EXCHANGE_TOKEN_VERSION_FIELD]
    result["controller"] = controller
    result["characteristics"].update(power=size, toughness=size)
    return result


def post_exchange_token_node_capabilities(*, effects, target_schema, mechanic_ids, cost_schema=None):
    if not {"cr-119-life", "cr-115-targets", "cr-111-tokens"} <= set(mechanic_ids):
        return ()
    from .player_life_exchange import PlayerLifeExchangeSpec, player_life_exchange_instruction_dependencies

    try:
        exchange, token = effects
        if thaw_value(exchange) != PlayerLifeExchangeSpec("two_targets").to_instruction():
            return ()
        validate_post_exchange_token_instruction(token)
        dependencies = player_life_exchange_instruction_dependencies((exchange,), target_schema)
    except (TypeError, ValueError):
        return ()
    return (*dependencies, POST_EXCHANGE_TOKEN_CAPABILITY, "token.creation.fixed_definition")


def bind_post_exchange_token(host, item, raw_effect):
    from .player_life_exchange import PlayerLifeExchangeSpec, player_life_exchange_program_scope

    validate_post_exchange_token_instruction(raw_effect)
    if (
        item.kind not in {"spell", "spell_copy"} or item.context.get("currently_resolving") is not True
        or item.context.get("targets_revalidated") is not True
    ):
        raise ValueError("Post-exchange token creation requires its resolving targets")
    program = host.semantics.get(item.semantic_key)
    if program is None or program.event != "resolve" or not host.semantic_program_is_current_trusted(program):
        raise ValueError("Post-exchange token creation requires its trusted composition")
    expected, optional = player_life_exchange_program_scope(program)
    if optional or expected != PlayerLifeExchangeSpec("two_targets"):
        raise ValueError("Post-exchange token creation requires the same two target players")
    players = tuple(item.targets)
    current = {seat: host.state.players[seat].life for seat in host.active_seats if seat in players}
    return materialize_post_exchange_token(item.controller, players, current)


def is_closed_post_exchange_token_program(program):
    from .player_life_exchange import player_life_exchange_runtime_dependencies

    if (
        (program.event, program.active_zone) != ("resolve", "stack")
        or program.cost_schema is not None or program.handlers or program.event_condition is not None
    ):
        return False
    try:
        required = player_life_exchange_runtime_dependencies(program)
    except (TypeError, ValueError):
        return False
    return POST_EXCHANGE_TOKEN_CAPABILITY in required and set(required).issubset(program.capability_dependencies)
