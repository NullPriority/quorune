"""Derive both printed token characteristics from one committed destruction count."""

from collections.abc import Mapping

from ..affected_permanents import AffectedPermanentSetSpec
from ..object_predicate import ObjectQuerySpec
from ..replacement.immutable import FrozenMap
from .committed_quantity_followups import CommittedQuantityFollowup


DESTRUCTION_SIZED_TOKEN_CAPABILITY = "permanent.destroy.creature_result_token_size"
DESTRUCTION_SIZED_TOKEN_VERSION = 5
DESTRUCTION_SIZED_TOKEN_SCOPE = AffectedPermanentSetSpec(
    query=ObjectQuerySpec(zones=("battlefield",), types_all=("creature",)),
)


def validate_destruction_sized_token(scope, token):
    definition = token.get("characteristics")
    if (scope != DESTRUCTION_SIZED_TOKEN_SCOPE or token.get("op") != "create_token"
            or "tapped" in token or not isinstance(definition, Mapping)
            or not str(definition.get("type_line", "")).startswith("Token Artifact Creature — ")
            or definition.get("colors") != () or definition.get("power") != "0"
            or definition.get("toughness") != "0"):
        raise ValueError("Destruction size requires ordinary all-creature destruction and one colorless artifact creature token")


def destruction_sized_token_result(token, count):
    if type(count) is not int or count < 0:
        raise ValueError("Destruction size requires a nonnegative committed creature count")
    definition = {**token["characteristics"], "power": str(count), "toughness": str(count)}
    return CommittedQuantityFollowup((FrozenMap({**token, "characteristics": definition}),))
