from __future__ import annotations

"""Closed exile/return instruction data; all movement stays zone-owned."""

from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping

from .zone_object_keyword_model import ZONE_OBJECT_KEYWORDS


LINKED_EXILE_RETURN_CAPABILITY = "zone.linked_exile_return.fixed"
LINKED_TRANSFORMED_RETURN_CAPABILITY = "zone.linked_exile_return.transformed_source"
LINKED_EXILE_RETURN_MECHANIC = "linked-exile-return"
LINKED_EXILE_RETURN_OPERATION = "linked_exile_return"
LINKED_RETURN_OPERATION = "return_linked_exiled_objects"
CONDITIONAL_EXILE_CAPABILITY = 'zone.exile_until_source_departure.fixed'
SAME_NAME_CONDITIONAL_EXILE_CAPABILITY = "zone.exile_until_source_departure.same_name_public_set"
SAME_NAME_CONDITIONAL_EXILE_VERSION = 5
ANNOUNCED_OPPONENT_EXILE_CAPABILITY = "zone.exile_until_source_departure.announced_opponent_set"
ANNOUNCED_OPPONENT_EXILE_VERSION = 6
LINKED_BATCH_DEPARTURE_RETURN_CAPABILITY = "zone.linked_exile_return.batch_ability_pair"
LINKED_BATCH_DEPARTURE_RETURN_VERSION = 7
LINKED_FADING_EXILE_CAPABILITY = "zone.linked_exile_return.fade_paid_activation"
LINKED_FADING_EXILE_VERSION = 8
LINKED_FADING_HAND_BINDING = "linked_fading_hand_binding"
LINKED_ABILITY_TIMING = "linked_ability"
LINKED_ABILITY_BINDING_PREFIX = "ability-exile:"


@dataclass(frozen=True, slots=True)
class ExiledObjectIdentity:
    """The actual exile incarnation retained by a returning instruction."""

    object_id: str
    ref: str
    logical_object_id: str

    def __post_init__(self) -> None:
        if any(type(value) is not str or not value for value in
               (self.object_id, self.ref, self.logical_object_id)):
            raise ValueError('Linked exile identity is malformed')

    def to_dict(self) -> dict[str, str]:
        return {'object_id': self.object_id, 'ref': self.ref,
                'logical_object_id': self.logical_object_id}

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> 'ExiledObjectIdentity':
        if not isinstance(value, Mapping) or set(value) != {'object_id', 'ref', 'logical_object_id'}:
            raise ValueError('Linked exile identity is malformed')
        return cls(**value)

    def matches_exiled_card(self, card: Any) -> bool:
        return bool(card is not None and card.object_id == self.object_id
            and card.ref == self.ref and card.logical_object_id == self.logical_object_id
            and card.zone == 'exile' and not card.is_token and not card.is_spell_copy)


def validate_linked_ability_binding(binding: Any) -> None:
    prefix = LINKED_ABILITY_BINDING_PREFIX
    if (type(binding) is not str or not binding.startswith(prefix)
            or len(binding) != len(prefix) + 24
            or any(c not in "0123456789abcdef" for c in binding[len(prefix):])):
        raise ValueError("Linked exile/return binding identity is malformed")


def validate_linked_instruction(effect: Mapping[str, Any]) -> None:
    """Validate one phase before mutable state is made available."""
    if not isinstance(effect, Mapping):
        raise ValueError("Linked exile/return instruction must be an object")
    phase = effect.get("phase")
    allowed = {"op", "phase", "binding_id", "spec", "_replacement_selections"}
    if phase == "exile":
        allowed.add("cards")
    spec = LinkedExileReturnSpec.from_dict(effect.get("spec"))
    linked_ability = spec.timing == LINKED_ABILITY_TIMING
    if linked_ability and phase == "return" and spec.fading_scope is not LinkedFadingScope.OPPONENT_HAND:
        allowed.add("_aura_target_refs")
    if effect.get("op") != LINKED_EXILE_RETURN_OPERATION or phase not in {"exile", "return"} or set(effect) - allowed or not {"op", "phase", "binding_id", "spec"}.issubset(effect):
        raise ValueError("Linked exile/return instruction has a closed schema")
    binding = effect["binding_id"]
    if linked_ability:
        validate_linked_ability_binding(binding)
    elif type(binding) is not str or not binding.startswith("blink:"):
        raise ValueError("Linked exile/return binding identity is malformed")
    if phase == "exile":
        if "cards" not in effect:
            raise ValueError("Linked exile requires a selected object set")
        cards = effect["cards"]
        if cards is not None and not isinstance(cards, (str, list, tuple)):
            raise ValueError("Linked exile object set is malformed")
    if not isinstance(effect.get("_replacement_selections", ()), (list, tuple)):
        raise ValueError("Linked exile/return replacement selections are malformed")
    aura_targets = effect.get("_aura_target_refs", {})
    if not isinstance(aura_targets, Mapping) or any(type(key) is not str or not key or type(ref) is not str or not ref
                                                    for key, ref in aura_targets.items()):
        raise ValueError("Linked return Aura selections are malformed")


class SameNameConditionalExileScope(str, Enum):
    OPPONENTS = "opponents"
    ANCHOR_CONTROLLER = "anchor_controller"


class LinkedBatchDepartureScope(str, Enum):
    ALL_LANDS = "all_lands"
    OTHER_CONTROLLED_PERMANENTS = "other_controlled_permanents"
    TWO_TARGET_LANDS = "two_target_lands"

    @property
    def tapped_return(self) -> bool:
        return self is self.ALL_LANDS


class LinkedFadingScope(str, Enum):
    CREATURE = "creature"
    LAND = "land"
    OPPONENT_HAND = "opponent_hand"


def is_announced_opponent_exile(effect):
    spec = effect.get("spec")
    return (effect.get("op") == LINKED_EXILE_RETURN_OPERATION and isinstance(spec, Mapping)
            and ("announced_opponents" in spec or spec.get("schema_version") == ANNOUNCED_OPPONENT_EXILE_VERSION))


@dataclass(frozen=True, slots=True)
class LinkedExileReturnSpec:
    timing: str = "immediate"
    controller: str = "owner"
    tapped: bool = False
    entry_counters: tuple[tuple[str, int], ...] = ()
    keywords: tuple[str, ...] = ()
    schema_version: int = 1
    transformed: bool = False
    same_name_scope: SameNameConditionalExileScope | None = None
    announced_opponents: bool = False
    batch_departure_scope: LinkedBatchDepartureScope | None = None
    fading_scope: LinkedFadingScope | None = None

    def __post_init__(self) -> None:
        if type(self.schema_version) is not int or self.schema_version not in {
            1, 2, 3, 4, SAME_NAME_CONDITIONAL_EXILE_VERSION, ANNOUNCED_OPPONENT_EXILE_VERSION,
            LINKED_BATCH_DEPARTURE_RETURN_VERSION, LINKED_FADING_EXILE_VERSION,
        }:
            raise ValueError("Unsupported linked exile/return schema")
        if type(self.transformed) is not bool:
            raise ValueError("Linked transformed entry must be a boolean")
        if self.timing not in {"immediate", "next_end_step", "controller_next_end_step", "source_departure", LINKED_ABILITY_TIMING}:
            raise ValueError("Unsupported linked return timing")
        if type(self.announced_opponents) is not bool:
            raise ValueError("Announced opponent exile requires its typed marker")
        if self.fading_scope is not None:
            if (not isinstance(self.fading_scope, LinkedFadingScope)
                    or self.timing != LINKED_ABILITY_TIMING or self.batch_departure_scope is not None
                    or self.same_name_scope is not None or self.announced_opponents or self.transformed
                    or self.controller != "owner" or self.tapped or self.entry_counters or self.keywords):
                raise ValueError("Fading exile requires its exact activation scope and owner return")
            version = LINKED_FADING_EXILE_VERSION
        elif self.batch_departure_scope is not None:
            if (not isinstance(self.batch_departure_scope, LinkedBatchDepartureScope)
                    or self.timing != LINKED_ABILITY_TIMING or self.same_name_scope is not None
                    or self.announced_opponents or self.transformed or self.controller != "owner"
                    or self.tapped is not self.batch_departure_scope.tapped_return
                    or self.entry_counters or self.keywords):
                raise ValueError("A linked batch requires its exact exile scope and owner return")
            version = LINKED_BATCH_DEPARTURE_RETURN_VERSION
        elif self.announced_opponents:
            if self.same_name_scope is not None or self.timing != "source_departure" or self.transformed:
                raise ValueError("Announced opponent exile requires its source-departure timing")
            version = ANNOUNCED_OPPONENT_EXILE_VERSION
        elif self.same_name_scope is not None:
            if (not isinstance(self.same_name_scope, SameNameConditionalExileScope)
                    or self.timing != "source_departure" or self.transformed):
                raise ValueError("Same-name exile requires the source departure timing and a closed member scope")
            version = SAME_NAME_CONDITIONAL_EXILE_VERSION
        else:
            version = 4 if self.transformed else {"source_departure": 2, LINKED_ABILITY_TIMING: 3}.get(self.timing, 1)
        if self.schema_version != version:
            raise ValueError("Linked return timing requires its exact spec version")
        if version in {2, 3, SAME_NAME_CONDITIONAL_EXILE_VERSION, ANNOUNCED_OPPONENT_EXILE_VERSION} and (
            self.controller != "owner" or self.tapped or self.entry_counters or self.keywords
        ):
            raise ValueError("Departure and ability-pair returns have a fixed owner policy")
        if version == 4 and (self.timing != "immediate" or self.controller != "actor"
                             or self.tapped or self.entry_counters or self.keywords):
            raise ValueError("Transformed source return requires an immediate controller entry")
        if self.controller not in {"owner", "actor"} or type(self.tapped) is not bool:
            raise ValueError("Linked return controller or tapped state is malformed")
        if len(set(name for name, _ in self.entry_counters)) != len(self.entry_counters):
            raise ValueError("Linked return entry counters must be unique")
        for name, amount in self.entry_counters:
            if name not in {"+1/+1", "flying", "vigilance", "lifelink"} or type(amount) is not int or not 1 <= amount <= 3:
                raise ValueError("Linked return entry counter is outside its fixed scope")
        if len(set(self.keywords)) != len(self.keywords) or any(value.casefold() not in ZONE_OBJECT_KEYWORDS for value in self.keywords):
            raise ValueError("Linked return keywords are unsupported or duplicated")
        if self.keywords and self.timing != "immediate":
            raise ValueError("Delayed keyword-result riders are not represented")

    def to_dict(self) -> dict[str, Any]:
        value = {"schema_version": self.schema_version, "timing": self.timing,
                "controller": self.controller, "tapped": self.tapped,
                "entry_counters": [[name, amount] for name, amount in self.entry_counters],
                "keywords": list(self.keywords)}
        if self.schema_version == 4:
            value["transformed"] = self.transformed
        if self.same_name_scope is not None:
            value["same_name_scope"] = self.same_name_scope.value
        if self.announced_opponents:
            value["announced_opponents"] = True
        if self.batch_departure_scope is not None:
            value["batch_departure_scope"] = self.batch_departure_scope.value
        if self.fading_scope is not None:
            value["fading_scope"] = self.fading_scope.value
        return value

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "LinkedExileReturnSpec":
        fields = {
            "schema_version", "timing", "controller", "tapped", "entry_counters", "keywords",
        }
        if isinstance(value, Mapping) and value.get("schema_version") == 4:
            fields.add("transformed")
        if isinstance(value, Mapping) and value.get("schema_version") == SAME_NAME_CONDITIONAL_EXILE_VERSION:
            fields.add("same_name_scope")
        if isinstance(value, Mapping) and value.get("schema_version") == ANNOUNCED_OPPONENT_EXILE_VERSION:
            fields.add("announced_opponents")
        if isinstance(value, Mapping) and value.get("schema_version") == LINKED_BATCH_DEPARTURE_RETURN_VERSION:
            fields.add("batch_departure_scope")
        if isinstance(value, Mapping) and value.get("schema_version") == LINKED_FADING_EXILE_VERSION:
            fields.add("fading_scope")
        if not isinstance(value, Mapping) or set(value) != fields:
            raise ValueError("Linked exile/return spec has a closed schema")
        counters, keywords = value["entry_counters"], value["keywords"]
        if not isinstance(counters, (list, tuple)) or any(not isinstance(entry, (list, tuple)) or len(entry) != 2 for entry in counters):
            raise ValueError("Linked return entry counters must be pairs")
        if not isinstance(keywords, (list, tuple)) or any(type(keyword) is not str for keyword in keywords):
            raise ValueError("Linked return keywords must be strings")
        return cls(schema_version=value["schema_version"], timing=value["timing"],
                   controller=value["controller"], tapped=value["tapped"],
                   entry_counters=tuple(tuple(entry) for entry in counters), keywords=tuple(keywords),
                   transformed=value.get("transformed", False),
                   same_name_scope=SameNameConditionalExileScope(value["same_name_scope"]) if "same_name_scope" in value else None,
                   announced_opponents=value.get("announced_opponents", False),
                   batch_departure_scope=LinkedBatchDepartureScope(value["batch_departure_scope"])
                   if "batch_departure_scope" in value else None,
                   fading_scope=LinkedFadingScope(value["fading_scope"]) if "fading_scope" in value else None)
