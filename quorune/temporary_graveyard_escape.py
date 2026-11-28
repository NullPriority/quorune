"""Resolve a printed-price Escape grant on one public graveyard incarnation."""

from collections.abc import Mapping
from dataclasses import dataclass

from .cast_lifecycles import GRANTED_ESCAPE_CAPABILITY, GRANTED_ESCAPE_EXILE_COUNT, GRANTED_ESCAPE_LIFECYCLE_VERSION, FixedCastLifecycleSpec
from .continuous_effect_model import ContinuousObjectIdentity
from .continuous_effect_state import ResolutionEffectSource
from .replacement.immutable import FrozenMap, thaw_value
from .rules.graveyard_card_targets import PublicGraveyardCardTargetKind, PublicGraveyardCardTargetSpec


TEMPORARY_ESCAPE_OPERATION = "grant_target_graveyard_escape"
TEMPORARY_ESCAPE_CAPABILITY = "casting.lifecycle.target_graveyard_escape_until_cleanup"
TEMPORARY_ESCAPE_MECHANIC = "target-graveyard-escape-until-cleanup"
TEMPORARY_ESCAPE_STATS_FIELD = "target_graveyard_escape_grants"
TARGET_SCHEMA = FrozenMap({
    **PublicGraveyardCardTargetSpec(PublicGraveyardCardTargetKind.CREATURE_CARD, owner_relation="you").to_target_schema(),
    "supertypes_any": ["legendary"],
})
INSTRUCTION = FrozenMap({"op": TEMPORARY_ESCAPE_OPERATION, "schema_version": 1, "card": "$target.0"})
REQUIREMENTS = (TEMPORARY_ESCAPE_CAPABILITY, GRANTED_ESCAPE_CAPABILITY, "target.revalidate_resolution")
PRINTED_PRICE = FixedCastLifecycleSpec.from_dict({
    "schema_version": GRANTED_ESCAPE_LIFECYCLE_VERSION, "kind": "escape", "exile_count": GRANTED_ESCAPE_EXILE_COUNT,
})


def targeted_escape_template(text):
    core = (
        "target legendary creature card in your graveyard gains escape until end of turn. "
        "the escape cost is equal to its mana cost plus exile three other cards from your graveyard."
    )
    reminder = " (you may cast it from your graveyard for its escape cost this turn.)"
    normalized = " ".join(text.casefold().split())
    if normalized not in (core, core + reminder):
        return None
    return ("activated-target-graveyard-escape-v1", (thaw_value(INSTRUCTION),),
            thaw_value(TARGET_SCHEMA), (TEMPORARY_ESCAPE_MECHANIC, "cr-115-targets"))


def targeted_escape_requirements(effects, target_schema, mechanic_ids):
    if (len(effects) != 1 or TEMPORARY_ESCAPE_MECHANIC not in mechanic_ids
            or FrozenMap(effects[0]) != INSTRUCTION or type(effects[0].get("schema_version")) is not int
            or not isinstance(target_schema, Mapping) or type(target_schema.get("count")) is not int
            or FrozenMap(target_schema) != TARGET_SCHEMA):
        return ()
    return REQUIREMENTS


@dataclass(frozen=True, slots=True)
class ResolvedGraveyardEscape:
    source: ResolutionEffectSource
    target: ContinuousObjectIdentity
    program_key: str
    turn_sequence: int

    def __post_init__(self):
        if (not isinstance(self.source, ResolutionEffectSource) or self.source.object_id is None
                or not isinstance(self.target, ContinuousObjectIdentity)
                or type(self.program_key) is not str or not self.program_key
                or type(self.turn_sequence) is not int or self.turn_sequence < 0):
            raise ValueError("A temporary Escape grant must retain its source, target and turn")

    @property
    def option_key(self):
        return f"{self.program_key}|{self.source.stack_ref}"

    def to_dict(self):
        return {"schema_version": 1, "source": self.source.to_dict(), "target": self.target.to_dict(),
                "program_key": self.program_key, "turn_sequence": self.turn_sequence}

    @classmethod
    def from_dict(cls, value):
        if (not isinstance(value, Mapping)
                or set(value) != {"schema_version", "source", "target", "program_key", "turn_sequence"}
                or type(value["schema_version"]) is not int or value["schema_version"] != 1):
            raise ValueError("Temporary Escape has a closed persisted identity")
        return cls(ResolutionEffectSource.from_effect({"_runtime_source": value["source"]}),
                   ContinuousObjectIdentity.from_dict(value["target"]), value["program_key"], value["turn_sequence"])


def current_temporary_graveyard_grants(state, seat, card, *, field):
    player = state.players.get(seat)
    if player is None or not player.in_game:
        return ()
    raw_grants = player.stats.get(field, ())
    if not isinstance(raw_grants, (list, tuple)):
        return ()
    active = []
    for raw in raw_grants:
        try:
            grant = ResolvedGraveyardEscape.from_dict(raw)
        except (TypeError, ValueError, KeyError):
            continue
        if (grant.turn_sequence == state.turn_sequence
                and grant.target.object_id == card.object_id and grant.target.logical_object_id == card.logical_object_id):
            active.append(grant)
    return tuple(active)


def temporary_escape_sources(state, seat, card):
    return tuple(
        (grant.source, grant.option_key, PRINTED_PRICE)
        for grant in current_temporary_graveyard_grants(state, seat, card, field=TEMPORARY_ESCAPE_STATS_FIELD)
    )
