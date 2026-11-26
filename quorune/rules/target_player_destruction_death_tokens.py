"""Two tokens per committed creature death for the original selected player."""

from dataclasses import replace
from itertools import repeat

from ..affected_permanents import AffectedPermanentSetSpec, PermanentControllerRelation
from ..object_predicate import ObjectQuerySpec
from .committed_quantity_followups import token_followup_from_committed_controllers


TARGET_PLAYER_DEATH_TOKEN_CAPABILITY = "permanent.destroy.target_player_death_tokens"
TARGET_PLAYER_DEATH_TOKEN_VERSION = 4
TARGET_PLAYER_DEATH_TOKEN_SCOPE = AffectedPermanentSetSpec(
    query=ObjectQuerySpec(zones=("battlefield",), types_all=("creature",)),
    controller_relation=PermanentControllerRelation.TARGET_PLAYER,
    target_controller="$target.0",
)


def validate_target_player_death_scope(spec):
    target = getattr(spec, "target_controller", None)
    if (type(target) is not str or not target or (target.startswith("$") and target != "$target.0")
            or not isinstance(spec, AffectedPermanentSetSpec)
            or spec != replace(TARGET_PLAYER_DEATH_TOKEN_SCOPE, target_controller=target)):
        raise ValueError("Death-conditioned tokens require all creatures controlled by the original target player")
    return target


def validate_target_player_death_token_definition(scope, token):
    definition = token.get("characteristics", {})
    type_line = definition.get("type_line", "")
    if (scope != TARGET_PLAYER_DEATH_TOKEN_SCOPE or token.get("op") != "create_token"
            or "tapped" in token or not type_line.startswith("Token Creature — ")):
        raise ValueError("Death-conditioned tokens require one untapped creature token definition and the original target")


def tokens_after_target_player_creature_deaths(spec, token, count):
    target = validate_target_player_death_scope(spec)
    if target == "$target.0" or type(count) is not int or count < 0:
        raise ValueError("Death-conditioned tokens require a bound recipient and a committed graveyard count")
    return token_followup_from_committed_controllers(token, repeat(target, 2 * count))
