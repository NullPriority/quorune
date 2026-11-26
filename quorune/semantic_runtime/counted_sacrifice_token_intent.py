"""Versioned public movement identity for a count-based sacrifice token result."""

from dataclasses import dataclass, field
from collections.abc import Mapping

from ..replacement.immutable import FrozenMap, thaw_value
from ..rules.counted_sacrifice_tokens import counted_sacrifice_token_commit_parts
from ..rules.any_number_sacrifice import any_number_sacrifice_commit_parts
from ..zone_trigger_events import ZoneTransitionKind
from .intents import MoveObjectsSimultaneouslyIntent


COUNTED_SACRIFICE_TOKEN_INTENT_KIND = "counted_sacrifice_token"
COUNTED_SACRIFICE_QUANTITY_INTENT_KIND = "counted_sacrifice_quantity"


@dataclass(frozen=True, slots=True)
class CountedSacrificeIntent(MoveObjectsSimultaneouslyIntent):
    payload: FrozenMap = field(default_factory=FrozenMap)

    def __post_init__(self):
        MoveObjectsSimultaneouslyIntent.__post_init__(self)
        if (self.expected_zones != ("battlefield",) or self.destination != "graveyard"
                or self.transition_kind is not ZoneTransitionKind.SACRIFICE or self.owned_only
                or self.controlled_only or self.battlefield_controller is not None or self.library_order is not None):
            raise ValueError("Counted sacrifice tokens require their ordinary sacrifice transaction")

    def identity(self):
        return {"actor": self.actor, "refs": list(self.object_refs), "reason": self.reason,
                "payload": thaw_value(self.payload)}


@dataclass(frozen=True, slots=True)
class CountedSacrificeTokenIntent(CountedSacrificeIntent):
    def __post_init__(self):
        CountedSacrificeIntent.__post_init__(self)
        token, selected = counted_sacrifice_token_commit_parts(self.payload, self.object_refs, actor=self.actor)
        object.__setattr__(self, "payload", FrozenMap({
            "schema_version": 1, "token": token, "selected_incarnations": selected,
        }))


@dataclass(frozen=True, slots=True)
class CountedSacrificeQuantityIntent(CountedSacrificeIntent):
    def __post_init__(self):
        CountedSacrificeIntent.__post_init__(self)
        followup, selected, target = any_number_sacrifice_commit_parts(self.payload, self.object_refs, actor=self.actor)
        object.__setattr__(self, "payload", FrozenMap({
            "schema_version": self.payload["schema_version"], "followup": followup, "selected_incarnations": selected,
            **({"target": target} if target is not None else {}),
        }))


def _validate_counted_sacrifice_identity(value, intent_type):
    if not isinstance(value, Mapping) or set(value) != {"actor", "refs", "reason", "payload"}:
        raise ValueError("Counted sacrifice movement lost its exact identity")
    return intent_type(
        actor=value["actor"], object_refs=value["refs"], expected_zones=("battlefield",),
        destination="graveyard", reason=value["reason"], transition_kind=ZoneTransitionKind.SACRIFICE,
        payload=value["payload"],
    ).identity()


def validate_counted_sacrifice_token_identity(value):
    return _validate_counted_sacrifice_identity(value, CountedSacrificeTokenIntent)


def validate_counted_sacrifice_quantity_identity(value):
    return _validate_counted_sacrifice_identity(value, CountedSacrificeQuantityIntent)
