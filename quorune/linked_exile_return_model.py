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
EXILED_TARGET_SEARCH_BINDING = "blink:before-basic-land-search"
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
LINKED_PUBLIC_SET_END_STEP_VERSION = 9
LINKED_PUBLIC_SET_NEXT_COMBAT_VERSION = 10
SELECTED_CONDITIONAL_EXILE_VERSION = 11
LINKED_TYPE_ENTRY_COUNTERS_VERSION = 12
LINKED_CREATURE_ENTRY_COUNTER_VERSION = 13
LINKED_PUBLIC_ALL_CREATURES_END_STEP_VERSION = 14
CONTROLLER_LIBRARY_TOP_HAND_VERSION = 15
LINKED_TRANSFORMED_OWNER_RETURN_VERSION = 16
CONTROLLER_LIBRARY_TOP_HAND_CAPABILITY = "zone.linked_exile_return.private_library_top_to_hand"
CONTROLLER_LIBRARY_TOP_HAND_BINDING = "blink:controller-library-top-to-hand"
LINKED_TYPE_ENTRY_COUNTER_VERSIONS = (LINKED_TYPE_ENTRY_COUNTERS_VERSION, LINKED_CREATURE_ENTRY_COUNTER_VERSION)
LINKED_TYPE_ENTRY_COUNTERS_CAPABILITY = "zone.linked_exile_return.prospective_entry_counters"
SELECTED_CONDITIONAL_EXILE_FIELD = "selected_conditional_exile"
SELECTED_EXILE_QUANTITY_KIND = "committed_conditional_exile_quantity"
LINKED_PUBLIC_SET_VERSIONS = (
    LINKED_PUBLIC_SET_END_STEP_VERSION, LINKED_PUBLIC_SET_NEXT_COMBAT_VERSION,
    LINKED_PUBLIC_ALL_CREATURES_END_STEP_VERSION,
)
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


def is_exiled_target_search_return(effect: Mapping[str, Any]) -> bool:
    return (
        effect.get("op") == LINKED_EXILE_RETURN_OPERATION
        and effect.get("phase") == "return"
        and effect.get("binding_id") == EXILED_TARGET_SEARCH_BINDING
    )


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
    if phase == "return" and (
        (linked_ability and spec.fading_scope is not LinkedFadingScope.OPPONENT_HAND)
        or is_exiled_target_search_return(effect)
    ):
        allowed.add("_aura_target_refs")
    if effect.get("op") != LINKED_EXILE_RETURN_OPERATION or phase not in {"exile", "return"} or set(effect) - allowed or not {"op", "phase", "binding_id", "spec"}.issubset(effect):
        raise ValueError("Linked exile/return instruction has a closed schema")
    binding = effect["binding_id"]
    if linked_ability:
        validate_linked_ability_binding(binding)
    elif type(binding) is not str or not binding.startswith("blink:"):
        raise ValueError("Linked exile/return binding identity is malformed")
    if spec.schema_version == CONTROLLER_LIBRARY_TOP_HAND_VERSION:
        if binding != CONTROLLER_LIBRARY_TOP_HAND_BINDING or (
            phase == "exile" and (type(effect.get("cards")) is not str or not effect["cards"])
        ):
            raise ValueError("Private library exile requires its own controller and link")
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


def is_public_linked_return(effect):
    spec = effect.get("spec")
    return (
        effect.get("op") == LINKED_RETURN_OPERATION and isinstance(spec, Mapping)
        and spec.get("schema_version") in LINKED_PUBLIC_SET_VERSIONS
    )


class LinkedPublicSetScope(str, Enum):
    CONTROLLED_CREATURES = "controlled_creatures"
    TARGET_PLAYER_NONLANDS = "target_player_nonlands"
    ALL_CREATURES = "all_creatures"

    @property
    def player_reference(self) -> str:
        return "$target.0" if self is self.TARGET_PLAYER_NONLANDS else "$controller"

    def target_schema(self) -> dict[str, Any] | None:
        if self is not self.TARGET_PLAYER_NONLANDS:
            return None
        return {"zones": ["player"], "categories": ["player"], "count": 1, "player_relation": "any"}


class LinkedFadingScope(str, Enum):
    CREATURE = "creature"
    LAND = "land"
    OPPONENT_HAND = "opponent_hand"


def is_announced_opponent_exile(effect):
    spec = effect.get("spec")
    return (effect.get("op") == LINKED_EXILE_RETURN_OPERATION and isinstance(spec, Mapping)
            and ("announced_opponents" in spec or spec.get("schema_version") == ANNOUNCED_OPPONENT_EXILE_VERSION))


class SelectedConditionalExileScope(str, Enum):
    NON_ANGEL_CREATURES = "controlled_non_angel_creatures"
    OTHER_NONTOKEN_CREATURES = "other_controlled_nontoken_creatures"
    OTHER_NONLAND_PERMANENTS_WITH_SOLDIERS = "other_controlled_nonland_permanents_with_soldiers"

    @property
    def binding_id(self):
        return f"blink:selected:{self.value}"

    def matches(self, *, card_object_id, source_object_id, types, subtypes, token):
        if self is SelectedConditionalExileScope.NON_ANGEL_CREATURES:
            return "creature" in types and "angel" not in subtypes
        if self is SelectedConditionalExileScope.OTHER_NONTOKEN_CREATURES:
            return card_object_id != source_object_id and "creature" in types and not token
        return card_object_id != source_object_id and "land" not in types


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
    public_end_step_scope: LinkedPublicSetScope | None = None
    selected_departure_scope: SelectedConditionalExileScope | None = None

    def __post_init__(self) -> None:
        if type(self.schema_version) is not int or self.schema_version not in {
            1, 2, 3, 4, SAME_NAME_CONDITIONAL_EXILE_VERSION, ANNOUNCED_OPPONENT_EXILE_VERSION,
            LINKED_BATCH_DEPARTURE_RETURN_VERSION, LINKED_FADING_EXILE_VERSION,
            *LINKED_PUBLIC_SET_VERSIONS, SELECTED_CONDITIONAL_EXILE_VERSION, *LINKED_TYPE_ENTRY_COUNTER_VERSIONS,
            CONTROLLER_LIBRARY_TOP_HAND_VERSION, LINKED_TRANSFORMED_OWNER_RETURN_VERSION,
        }:
            raise ValueError("Unsupported linked exile/return schema")
        if type(self.transformed) is not bool:
            raise ValueError("Linked transformed entry must be a boolean")
        if self.timing not in {"immediate", "next_end_step", "controller_next_end_step", "next_combat",
                               "source_departure", LINKED_ABILITY_TIMING}:
            raise ValueError("Unsupported linked return timing")
        if type(self.announced_opponents) is not bool:
            raise ValueError("Announced opponent exile requires its typed marker")
        if self.schema_version == CONTROLLER_LIBRARY_TOP_HAND_VERSION:
            if (
                self.timing != "controller_next_end_step" or self.controller != "owner"
                or self.tapped or self.entry_counters or self.keywords or self.transformed
                or self.announced_opponents or any(scope is not None for scope in (
                    self.public_end_step_scope, self.batch_departure_scope, self.fading_scope,
                    self.same_name_scope, self.selected_departure_scope,
                ))
            ):
                raise ValueError("Private library exile requires the controller's next end step and own hand")
            version = CONTROLLER_LIBRARY_TOP_HAND_VERSION
        elif self.schema_version in LINKED_TYPE_ENTRY_COUNTER_VERSIONS:
            if (
                self.timing != "next_end_step" or self.controller != "owner" or self.tapped
                or self.entry_counters or self.keywords or self.transformed or self.announced_opponents
                or any(scope is not None for scope in (
                    self.public_end_step_scope, self.batch_departure_scope, self.fading_scope,
                    self.same_name_scope, self.selected_departure_scope,
                ))
            ):
                raise ValueError("Prospective entry counters require the exact delayed owner return")
            version = self.schema_version
        elif self.selected_departure_scope is not None:
            if (
                not isinstance(self.selected_departure_scope, SelectedConditionalExileScope)
                or self.timing != "source_departure" or self.controller != "owner" or self.tapped
                or self.entry_counters or self.keywords or self.transformed or self.announced_opponents
                or any(scope is not None for scope in (
                    self.public_end_step_scope, self.batch_departure_scope, self.fading_scope, self.same_name_scope,
                ))
            ):
                raise ValueError("Selected conditional exile requires its exact source-departure owner return")
            version = SELECTED_CONDITIONAL_EXILE_VERSION
        elif self.timing == "next_combat":
            self._validate_public_next_combat()
            version = LINKED_PUBLIC_SET_NEXT_COMBAT_VERSION
        elif self.public_end_step_scope is not None:
            if (
                not isinstance(self.public_end_step_scope, LinkedPublicSetScope)
                or self.timing != "next_end_step" or self.controller != "owner" or self.tapped
                or self.entry_counters or self.keywords or self.transformed or self.announced_opponents
                or any(value is not None for value in (self.fading_scope, self.batch_departure_scope, self.same_name_scope))
            ):
                raise ValueError("A public linked set requires its exact next-end-step owner return")
            version = (LINKED_PUBLIC_ALL_CREATURES_END_STEP_VERSION
                       if self.public_end_step_scope is LinkedPublicSetScope.ALL_CREATURES
                       else LINKED_PUBLIC_SET_END_STEP_VERSION)
        elif self.fading_scope is not None:
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
            if self.transformed:
                version = LINKED_TRANSFORMED_OWNER_RETURN_VERSION if self.controller == "owner" else 4
            else:
                version = {"source_departure": 2, LINKED_ABILITY_TIMING: 3}.get(self.timing, 1)
        if self.schema_version != version:
            raise ValueError("Linked return timing requires its exact spec version")
        if version in {2, 3, SAME_NAME_CONDITIONAL_EXILE_VERSION, ANNOUNCED_OPPONENT_EXILE_VERSION} and (
            self.controller != "owner" or self.tapped or self.entry_counters or self.keywords
        ):
            raise ValueError("Departure and ability-pair returns have a fixed owner policy")
        if version in {4, LINKED_TRANSFORMED_OWNER_RETURN_VERSION} and (
                self.timing != "immediate" or self.tapped or self.entry_counters or self.keywords):
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
        if self.keywords and self.timing not in ("immediate", "next_combat"):
            raise ValueError("Other delayed keyword-result riders are not represented")

    def _validate_public_next_combat(self):
        if (
            self.controller != "owner" or self.tapped or self.entry_counters or self.keywords != ("Haste",)
            or self.transformed or self.announced_opponents
            or any(value is not None for value in (
                self.public_end_step_scope, self.fading_scope, self.batch_departure_scope, self.same_name_scope,
            ))
        ):
            raise ValueError("Next-combat return requires its exact owned creature return and temporary haste")

    @property
    def public_set_scope(self):
        if self.schema_version == LINKED_PUBLIC_SET_NEXT_COMBAT_VERSION:
            return LinkedPublicSetScope.CONTROLLED_CREATURES
        return self.public_end_step_scope

    def to_dict(self) -> dict[str, Any]:
        value = {"schema_version": self.schema_version, "timing": self.timing,
                "controller": self.controller, "tapped": self.tapped,
                "entry_counters": [[name, amount] for name, amount in self.entry_counters],
                "keywords": list(self.keywords)}
        if self.schema_version in {4, LINKED_TRANSFORMED_OWNER_RETURN_VERSION}:
            value["transformed"] = self.transformed
        if self.same_name_scope is not None:
            value["same_name_scope"] = self.same_name_scope.value
        if self.announced_opponents:
            value["announced_opponents"] = True
        if self.batch_departure_scope is not None:
            value["batch_departure_scope"] = self.batch_departure_scope.value
        if self.fading_scope is not None:
            value["fading_scope"] = self.fading_scope.value
        if self.public_end_step_scope is not None:
            value["public_end_step_scope"] = self.public_end_step_scope.value
        if self.selected_departure_scope is not None:
            value["selected_departure_scope"] = self.selected_departure_scope.value
        return value

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "LinkedExileReturnSpec":
        fields = {
            "schema_version", "timing", "controller", "tapped", "entry_counters", "keywords",
        }
        if isinstance(value, Mapping) and value.get("schema_version") in {4, LINKED_TRANSFORMED_OWNER_RETURN_VERSION}:
            fields.add("transformed")
        if isinstance(value, Mapping) and value.get("schema_version") == SAME_NAME_CONDITIONAL_EXILE_VERSION:
            fields.add("same_name_scope")
        if isinstance(value, Mapping) and value.get("schema_version") == ANNOUNCED_OPPONENT_EXILE_VERSION:
            fields.add("announced_opponents")
        if isinstance(value, Mapping) and value.get("schema_version") == LINKED_BATCH_DEPARTURE_RETURN_VERSION:
            fields.add("batch_departure_scope")
        if isinstance(value, Mapping) and value.get("schema_version") == LINKED_FADING_EXILE_VERSION:
            fields.add("fading_scope")
        if isinstance(value, Mapping) and value.get("schema_version") in (
            LINKED_PUBLIC_SET_END_STEP_VERSION, LINKED_PUBLIC_ALL_CREATURES_END_STEP_VERSION,
        ):
            fields.add("public_end_step_scope")
        if isinstance(value, Mapping) and value.get("schema_version") == SELECTED_CONDITIONAL_EXILE_VERSION:
            fields.add("selected_departure_scope")
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
                   fading_scope=LinkedFadingScope(value["fading_scope"]) if "fading_scope" in value else None,
                   public_end_step_scope=LinkedPublicSetScope(value["public_end_step_scope"])
                   if "public_end_step_scope" in value else None,
                   selected_departure_scope=SelectedConditionalExileScope(value["selected_departure_scope"])
                   if "selected_departure_scope" in value else None)



def controller_library_top_hand_effects() -> tuple[dict[str, Any], ...]:
    spec = LinkedExileReturnSpec(
        timing="controller_next_end_step", schema_version=CONTROLLER_LIBRARY_TOP_HAND_VERSION,
    )
    common = {"op": LINKED_EXILE_RETURN_OPERATION, "binding_id": CONTROLLER_LIBRARY_TOP_HAND_BINDING}
    return (
        {**common, "phase": "exile", "cards": "$controller", "spec": spec.to_dict()},
        {**common, "phase": "return", "spec": spec.to_dict()},
    )


def linked_type_entry_counter_target_schema(version=LINKED_TYPE_ENTRY_COUNTERS_VERSION) -> dict[str, Any]:
    base = {"zones": ["battlefield"], "categories": ["permanent"], "controller_relation": "you"}
    if version == LINKED_CREATURE_ENTRY_COUNTER_VERSION:
        return {**base, "count": 1}
    if version == LINKED_TYPE_ENTRY_COUNTERS_VERSION:
        return {**base, "up_to": 999, "types_any": ["creature", "planeswalker"]}
    raise ValueError("Prospective entry counters require a supported linked target mode")


def is_selected_conditional_exile(effect):
    return (isinstance(effect, Mapping) and effect.get("op") == "choose_objects"
            and SELECTED_CONDITIONAL_EXILE_FIELD in effect)


def selected_conditional_exile_spec(effect):
    if not is_selected_conditional_exile(effect) or set(effect) != {"op", SELECTED_CONDITIONAL_EXILE_FIELD}:
        raise ValueError("Selected conditional exile requires one exact resolution choice")
    spec = LinkedExileReturnSpec.from_dict(effect[SELECTED_CONDITIONAL_EXILE_FIELD])
    if spec.schema_version != SELECTED_CONDITIONAL_EXILE_VERSION:
        raise ValueError("Selected conditional exile needs its versioned owner-return instruction")
    return spec


def conditional_exile_soldier_token():
    return {
        "op": "create_token", "controller": "$controller", "name": "Soldier Token", "quantity": 1,
        "characteristics": {"type_line": "Token Creature — Soldier", "literal_token_name_version": 1,
                            "colors": ["W"], "power": "1", "toughness": "1"},
    }
