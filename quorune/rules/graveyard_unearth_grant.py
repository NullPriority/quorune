"""Typed Unearth grants for matching cards in the grant controller's graveyard."""

from collections.abc import Mapping
from dataclasses import dataclass

from ..object_predicate import ObjectQuerySpec
from ..unearth import OrdinaryUnearthAbilitySpec


def graveyard_unearth_recipient(card_type, *, subtype=None, party=False):
    """Build one reviewed recipient predicate."""
    if card_type == "artifact" and subtype is None and not party:
        return ObjectQuerySpec(zones=("graveyard",), types_all=("artifact",), token=False)
    if card_type != "creature" or subtype not in (None, "sliver") or (subtype and party):
        raise ValueError("Unearth grant recipients require a closed card type and subtype query")
    return ObjectQuerySpec(
        zones=("graveyard",), types_all=("creature",), token=False,
        subtypes_all=("sliver",) if subtype else (),
        subtypes_any=("cleric", "rogue", "warrior", "wizard") if party else (),
    )


_RECIPIENTS = (
    graveyard_unearth_recipient("creature"),
    graveyard_unearth_recipient("creature", subtype="sliver"),
    graveyard_unearth_recipient("creature", party=True),
    graveyard_unearth_recipient("artifact"),
)


def matches_graveyard_unearth_recipient(value):
    return value in _RECIPIENTS


@dataclass(frozen=True, slots=True)
class GraveyardUnearthGrant:
    recipient: ObjectQuerySpec
    ability: OrdinaryUnearthAbilitySpec
    schema_version: int = 1

    def __post_init__(self):
        if (type(self.schema_version) is not int or self.schema_version != 1
                or self.recipient not in _RECIPIENTS
                or not isinstance(self.ability, OrdinaryUnearthAbilitySpec)):
            raise ValueError("Unearth grant requires a known graveyard query and an ordinary fixed cost")

    @classmethod
    def from_dict(cls, value):
        if not isinstance(value, Mapping) or set(value) != {"schema_version", "recipient", "ability"}:
            raise ValueError("Malformed fixed graveyard Unearth grant")
        return cls(
            ObjectQuerySpec.from_dict(value["recipient"]),
            OrdinaryUnearthAbilitySpec.from_dict(value["ability"]),
            value["schema_version"],
        )

    def to_dict(self):
        return {"schema_version": self.schema_version, "recipient": self.recipient.to_dict(),
                "ability": self.ability.to_dict()}
