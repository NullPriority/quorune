from __future__ import annotations

"""Versioned follow-up on one successful private hand entry."""

from collections.abc import Mapping
from dataclasses import asdict, dataclass

from .reanimation_followup import BattlefieldEntryFollowup
from .attacking_card_entry import ATTACKING_CARD_ENTRY_CAPABILITY, decode_hand_attacking_entry


HAND_ENTRY_FOLLOWUP_CAPABILITY = "zone.move.fixed_private_hand_haste_and_sacrifice"
HAND_ENTRY_FOLLOWUP_VERSION = 2
HAND_ENTRY_SUBTYPE_LIFE_VERSION = 4
HAND_ENTRY_SUBTYPE_LIFE_CAPABILITIES = (
    "state_query.permanent.public_state_predicate",
    "life.change.effect",
)
HAND_ENTRY_FOLLOWUP_FIELDS = frozenset({"schema_version", "followup"})
VERSIONED_HAND_ENTRY_FIELDS = HAND_ENTRY_FOLLOWUP_FIELDS | {"attacking_entry"}


@dataclass(frozen=True, slots=True)
class HandEntrySubtypeLife:
    land_subtype: str
    amount: int

    def __post_init__(self) -> None:
        if self.land_subtype != "cave" or type(self.amount) is not int or not 1 <= self.amount <= 99:
            raise ValueError("Private hand entry requires a Cave and fixed positive life gain")

    @classmethod
    def from_dict(cls, value: Mapping) -> HandEntrySubtypeLife:
        if not isinstance(value, Mapping) or set(value) != {"land_subtype", "amount"}:
            raise ValueError("Private hand entry subtype follow-up has a closed field set")
        return cls(**value)

    def to_dict(self) -> dict:
        return asdict(self)


def uses_committed_hand_entry_life(effect: Mapping) -> bool:
    return effect.get("op") == "move" and "hand_entry_query" in effect and (
        effect.get("schema_version") == HAND_ENTRY_SUBTYPE_LIFE_VERSION
    )


def _decode_subtype_life(effect: Mapping) -> HandEntrySubtypeLife:
    from ..object_predicate import ObjectQuerySpec
    from ..replacement.immutable import FrozenMap

    land = ObjectQuerySpec(zones=("hand",), types_all=("land",)).canonical_dict()
    query_field = "hand_entry_query" if effect.get("op") == "move" else "query"
    query = effect.get(query_field)
    if not isinstance(query, Mapping) or FrozenMap(query) != FrozenMap(land) or effect.get("tapped") is not False:
        raise ValueError("A subtype life follow-up requires an ordinary optional land entry")
    return HandEntrySubtypeLife.from_dict(effect["followup"])


def decode_hand_entry_followup(effect: Mapping) -> BattlefieldEntryFollowup | HandEntrySubtypeLife | None:
    if decode_hand_attacking_entry(effect):
        return None
    fields = HAND_ENTRY_FOLLOWUP_FIELDS.intersection(effect)
    if not fields:
        return None
    version = effect.get("schema_version")
    if fields != HAND_ENTRY_FOLLOWUP_FIELDS or type(version) is not int:
        raise ValueError("Private hand entry requires its complete versioned follow-up")
    if version == HAND_ENTRY_SUBTYPE_LIFE_VERSION:
        return _decode_subtype_life(effect)
    if version != HAND_ENTRY_FOLLOWUP_VERSION:
        raise ValueError("Unsupported private hand entry follow-up version")
    followup = BattlefieldEntryFollowup.from_dict(effect["followup"])
    if (followup.departure, followup.end_step, followup.power_boost) != ("sacrifice", "next", 0):
        raise ValueError("Private hand entry requires a haste grant and one next-end-step sacrifice")
    return followup


def committed_hand_entry_life(host, card, *, previous_zone_counter, actor, source, followup):
    from ..replacement.immutable import FrozenMap
    from .committed_quantity_followups import CommittedQuantityFollowup

    if card is None or card.zone != "battlefield" or card.zone_change_counter != previous_zone_counter + 1:
        return CommittedQuantityFollowup(())
    effective = host._effective_card_data(card)
    types, subtypes, _supertypes = host._type_parts(str(effective.get("type_line") or ""))
    if "land" not in types or followup.land_subtype not in subtypes:
        return CommittedQuantityFollowup(())
    return CommittedQuantityFollowup((FrozenMap({
        "op": "life",
        "player": actor,
        "delta": followup.amount,
        "source": source.card_ref or source.stack_ref,
    }),))


def hand_entry_followup_runtime_dependencies(program):
    """Recover the minimum runtime authority from nested effect payloads."""
    from .public_zone_move_capability_shapes import fixed_private_hand_entry_node_capabilities

    required = set()

    def visit(value):
        if isinstance(value, Mapping):
            if value.get("op") == "put_card_from_hand" and VERSIONED_HAND_ENTRY_FIELDS.intersection(value):
                owned = fixed_private_hand_entry_node_capabilities(effects=(value,), target_schema=None,
                    mechanic_ids=("fixed-private-hand-entry", "fixed-public-zone-move"))
                followup = decode_hand_entry_followup(value)
                valid = (
                    set(HAND_ENTRY_SUBTYPE_LIFE_CAPABILITIES).issubset(owned)
                    if isinstance(followup, HandEntrySubtypeLife)
                    else bool({HAND_ENTRY_FOLLOWUP_CAPABILITY, ATTACKING_CARD_ENTRY_CAPABILITY}.intersection(owned))
                )
                if not valid:
                    raise ValueError("Private hand entry lacks its closed follow-up authority")
                required.update(owned)
            for child in value.values():
                visit(child)
        elif isinstance(value, (tuple, list)):
            for child in value:
                visit(child)

    visit((program.effects, program.target_schema))
    return tuple(sorted(required))
