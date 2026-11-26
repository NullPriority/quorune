"""Retain the accepted Kinship reveal and the issued play option."""

from collections.abc import Mapping
from dataclasses import dataclass
import re

from .library_selection import LibrarySelectionObjectIdentity
from .shared_type_library_model import (
    SHARED_TYPE_LIBRARY_OPERATION, SHARED_TYPE_PLAY_MECHANIC,
    SharedCreatureTypeComparison, SharedTypeLibraryResult,
)
from ..replacement.immutable import FrozenMap, thaw_value


PLAY_STAGE = "play"
_EFFECT_FIELDS = {"op", "player", "result", "_shared_type_stage", "_top", "_comparison"}
_REVEAL_CAST_EFFECT_FIELDS = {
    "op", "player", "result", "_shared_type_stage", "_revealed_identity", "_stack_label",
}
_OFFER_FIELDS = {"_stack_ref", "_options_fingerprint"}


def revealed_shared_type_card(effect, *, actor, issued_stack=None):
    issued = issued_stack is not None
    if not isinstance(effect, Mapping):
        raise ValueError("Revealed library play requires its exact continuation")
    result = SharedTypeLibraryResult.from_dict(effect.get("result"))
    fields = _REVEAL_CAST_EFFECT_FIELDS if result.mandatory_reveal else _EFFECT_FIELDS
    if (set(effect) != fields | (_OFFER_FIELDS if issued else set())
            or effect["op"] != SHARED_TYPE_LIBRARY_OPERATION or effect["_shared_type_stage"] != PLAY_STAGE
            or effect["player"] != actor or type(actor) is not str or not actor
            or issued and effect["_stack_ref"] != issued_stack):
        raise ValueError("Kinship play requires its exact revealed-card continuation")
    if result.mandatory_reveal:
        label = effect["_stack_label"]
        if type(label) is not str or not label:
            raise ValueError("The public reveal requires its original stack label")
        return LibrarySelectionObjectIdentity.from_dict(effect["_revealed_identity"]), None
    comparison = SharedCreatureTypeComparison.from_dict(effect["_comparison"])
    top = effect["_top"]
    if (result.mechanics != (SHARED_TYPE_PLAY_MECHANIC,) or not comparison.matches
            or not isinstance(top, Mapping) or set(top) != {"ref", "logical_object_id"}
            or top["logical_object_id"] != comparison.candidate.identity.logical_object_id):
        raise ValueError("Only the original matching card may be played")
    return LibrarySelectionObjectIdentity(
        comparison.candidate.identity.object_id, top["logical_object_id"], top["ref"],
    ), comparison


def unissued_shared_type_play(effect):
    return FrozenMap({key: value for key, value in effect.items() if key not in _OFFER_FIELDS})


@dataclass(frozen=True, slots=True)
class ResolvingSharedTypeLibraryPlayIntent:
    actor: str
    stack_ref: str
    effect: FrozenMap
    options_fingerprint: str
    response: FrozenMap
    replacement_selections: tuple = ()

    def __post_init__(self):
        if (not isinstance(self.effect, FrozenMap) or not isinstance(self.response, FrozenMap)
                or type(self.stack_ref) is not str or not self.stack_ref
                or type(self.options_fingerprint) is not str
                or re.fullmatch(r"[0-9a-f]{64}", self.options_fingerprint) is None
                or not isinstance(self.replacement_selections, tuple)
                or type(self.response.get("cost_option")) is not str
                or not self.response["cost_option"]
                or any(type(key) is not str or key.startswith("_") for key in self.response)):
            raise ValueError("Kinship play intent changed its original choice or price")
        revealed_shared_type_card(self.effect, actor=self.actor)
        from ..semantic_runtime.zone_move_intent import _freeze_replacement_selections
        object.__setattr__(self, "replacement_selections",
            _freeze_replacement_selections(self.replacement_selections, family="Kinship play"))

    def identity(self):
        return {"actor": self.actor, "stack_ref": self.stack_ref, "effect": thaw_value(self.effect),
            "options_fingerprint": self.options_fingerprint, "response": thaw_value(self.response)}


def validate_shared_type_play_identity(data):
    fields = {"actor", "stack_ref", "effect", "options_fingerprint", "response"}
    if not isinstance(data, Mapping) or set(data) != fields:
        raise ValueError("Kinship play identity fields are malformed")
    return ResolvingSharedTypeLibraryPlayIntent(
        data["actor"], data["stack_ref"], FrozenMap(data["effect"]),
        data["options_fingerprint"], FrozenMap(data["response"]),
    ).identity()
