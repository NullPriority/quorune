from __future__ import annotations

"""Closed public values chosen by intrinsic battlefield-entry replacements."""

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
import re
from typing import Literal

from .basic_land_types import BASIC_LAND_MANA
from .basic_type_setting_model import BASIC_TYPE_SETTING_CAPABILITY
from .creature_subtypes import CREATURE_SUBTYPES, canonical_creature_subtype
from .util import normalize_card_name, stable_json



ENTRY_DESIGNATION_HANDLER_ID = "replacement.zone.entry-designation.v1"
ENTRY_DESIGNATION_CAPABILITY = "zone.entry.public_designation"
ENTRY_FACTION_CAPABILITY = "zone.entry.faction_designation"
ENTRY_FACTIONS = ("Brotherhood", "Enclave")
ENTRY_MARDU_JESKAI_CAPABILITY = "zone.entry.mardu_jeskai_designation"
MARDU_JESKAI_FACTIONS = ("Mardu", "Jeskai")
ENTRY_JESKAI_TEMUR_CAPABILITY = "zone.entry.jeskai_temur_designation"
ENTRY_OPPONENT_HANDLER_ID = "replacement.zone.linked-entry-opponent.v1"
ENTRY_OPPONENT_FIELD = "entry_chosen_opponent"
LINKED_ENTRY_OPPONENT_FIELD = "linked_entry_opponent"
LINKED_ENTRY_OPPONENT_TEMPLATE = "linked-entry-opponent-v1"
ENTRY_PLAYER_HANDLER_ID = "replacement.zone.linked-entry-player.v2"
ENTRY_CONTROL_OPPONENT_HANDLER_ID = "replacement.zone.entry-control-opponent.v3"
ENTRY_CONTROL_OPPONENT_TEMPLATE = "entry-control-opponent-v1"
ENTRY_CONTROL_OPPONENT_CAPABILITY = "zone.entry.opponent_controller_choice"
LINKED_ENTRY_PLAYER_TEMPLATE = "linked-entry-player-v2"
LINKED_PLAYER_PROTECTION_HANDLER_ID = "continuous.characteristics.linked-player-protection.v1"
LINKED_PLAYER_PROTECTION_TEMPLATE = "linked-entry-player-protection-v1"
LINKED_PLAYER_PROTECTION_CAPABILITIES = (ENTRY_DESIGNATION_CAPABILITY, "protection.typed.debt")
LINKED_COLOR_PROTECTION_HANDLER_ID = "continuous.characteristics.linked-color-protection.v1"
LINKED_COLOR_PROTECTION_TEMPLATE = "linked-entry-color-protection-v1"
LINKED_COLOR_PROTECTION_CAPABILITY = "continuous.characteristics.linked_entry_color_protection"
LINKED_COLOR_PROTECTION_CAPABILITIES = (
    ENTRY_DESIGNATION_CAPABILITY, "protection.typed.debt", LINKED_COLOR_PROTECTION_CAPABILITY,
)
LINKED_PLAYER_DAMAGE_HANDLER_ID = "replacement.damage.quantity.chosen-player.v3"
LINKED_PLAYER_DAMAGE_TEMPLATE = "linked-entry-player-damage-quantity-v1"
LINKED_PLAYER_RECEIVED_DAMAGE_TEMPLATE = "fixed-counter-linked-player-received-damage-v1"
LINKED_PLAYER_RECEIVED_DAMAGE_PROGRAM_TEMPLATE = LINKED_PLAYER_RECEIVED_DAMAGE_TEMPLATE.replace("fixed-counter-", "fixed-typed-effect-", 1)
LINKED_RECEIVED_DAMAGE_PLAYER_FIELD = "linked_received_damage_player"
LINKED_PLAYER_GRAVEYARD_DEFINITION_TEMPLATE = "linked-entry-player-graveyard-definition-v1"
LINKED_PLAYER_CAST_MILL_TEMPLATE = "fixed-counter-linked-player-cast-mill-v1"
LINKED_PLAYER_CAST_MILL_PROGRAM_TEMPLATE = LINKED_PLAYER_CAST_MILL_TEMPLATE.replace("fixed-counter-", "fixed-typed-effect-", 1)
LINKED_PLAYER_CAST_FIELD = "linked_entry_player_is_event_caster"
LINKED_PLAYER_DAMAGE_CAPABILITIES = (ENTRY_DESIGNATION_CAPABILITY, "damage.replacement.static_quantity")
COUNTED_ENTRY_OPPONENT_HANDLER_ID = "replacement.zone.chosen-opponent-entry-counter.v1"
COUNTED_ENTRY_OPPONENT_TEMPLATE = "chosen-opponent-entry-creature-count-v1"
COUNTED_ENTRY_OPPONENT_DEPENDENCIES = (
    ENTRY_DESIGNATION_CAPABILITY, "counter.producer.dynamic_self_entry", "counter.placement.quantity_replacement",
)
LINKED_OPPONENT_UPKEEP_FIELD = "linked_entry_opponent_is_event_player"
LINKED_OPPONENT_UPKEEP_VARIANT = "linked_entry_opponent_upkeep"
LINKED_OPPONENT_UPKEEP_TEMPLATE = "linked-entry-opponent-upkeep-damage-v1"
LINKED_OPPONENT_MAXIMUM_TEMPLATE = "linked-entry-opponent-maximum-hand-size-v1"
LINKED_OPPONENT_HAND_MODIFIER_TEMPLATE = "continuous-linked-opponent-hand-modifier-v1"
LINKED_OPPONENT_DEFINITION_TEMPLATE = "continuous-linked-opponent-characteristic-definition-v1"
CHOSEN_CREATURE_TYPE_TARGET_PREDICATE = "source_chosen_creature_type"
CHOSEN_COLOR_TARGET_PREDICATE = "source_chosen_color"
EXCLUDED_COLOR_ENTRY_HANDLER_ID = "replacement.zone.entry-color-exclusion.v2"
ENTRY_COLOR_COMPOSITION_CAPABILITY = "zone.entry.color_designation_composition"
LIMITED_CREATURE_TYPE_ENTRY_HANDLER_ID = "replacement.zone.entry-creature-type-options.v3"
LIMITED_CREATURE_TYPE_ENTRY_CAPABILITY = "zone.entry.limited_creature_type_designation"
COUNTED_CREATURE_TYPE_ENTRY_HANDLER_ID = "replacement.zone.entry-creature-type-counter.v4"
COUNTED_CREATURE_TYPE_ENTRY_CAPABILITY = "zone.entry.chosen_creature_type_counter"
SELF_CHOSEN_SUBTYPE_ENTRY_HANDLER_ID = "replacement.zone.entry-self-chosen-subtype.v5"
SELF_CHOSEN_SUBTYPE_CONTINUOUS_HANDLER_ID = "continuous.characteristics.self-chosen-subtype.v1"
SELF_CHOSEN_SUBTYPE_CAPABILITY = "continuous.characteristics.self_chosen_creature_type_addition"
BASIC_LAND_ENTRY_HANDLER_ID = "replacement.zone.entry-chosen-basic-land-type.v1"
CHOSEN_BASIC_LAND_HANDLER_ID = "continuous.characteristics.chosen-basic-land-type.v1"
BASIC_LAND_ENTRY_CAPABILITY = "zone.entry.linked_basic_land_type"
BASIC_LAND_ENTRY_CAPABILITIES = (
    BASIC_LAND_ENTRY_CAPABILITY, ENTRY_DESIGNATION_CAPABILITY,
    BASIC_TYPE_SETTING_CAPABILITY, "zone.entry.tapped_state",
)
BASIC_LAND_ENTRY_LIFE_FIELD = "entry_basic_land_type_life_available"
BASIC_LAND_ENTRY_PAID_FIELD = "entry_basic_land_type_life_decided"
ENTRY_DESIGNATION_HANDLER_IDS = (
    BASIC_LAND_ENTRY_HANDLER_ID,
    ENTRY_DESIGNATION_HANDLER_ID, EXCLUDED_COLOR_ENTRY_HANDLER_ID,
    LIMITED_CREATURE_TYPE_ENTRY_HANDLER_ID, COUNTED_CREATURE_TYPE_ENTRY_HANDLER_ID,
    SELF_CHOSEN_SUBTYPE_ENTRY_HANDLER_ID,
)
COUNTED_ENTRY_COUNTER_NAME = r"[a-z]+"
CHOSEN_CHARACTERISTICS_HANDLER_ID = "continuous.characteristics.chosen-designation.v1"
CHOSEN_CHARACTERISTICS_CAPABILITY = "continuous.characteristics.chosen_designation"
CONDITIONAL_CHOSEN_CHARACTERISTICS_HANDLER_ID = "continuous.characteristics.conditional-chosen-designation.v1"
CONDITIONAL_CHOSEN_CHARACTERISTICS_CAPABILITY = "continuous.characteristics.conditional_chosen_designation"


class EntryDesignationKind(StrEnum):
    COLOR = "color"
    CREATURE_TYPE = "creature_type"
    BASIC_LAND_TYPE = "basic_land_type"
    FACTION = "faction"
    MARDU_JESKAI_FACTION = "mardu_jeskai_faction"
    JESKAI_TEMUR_FACTION = "jeskai_temur_faction"

    @property
    def annotation(self) -> Literal[
        "chosen_color", "chosen_creature_type", "chosen_faction", "chosen_jeskai_temur_faction",
        "chosen_basic_land_type",
    ]:
        return {
            self.COLOR: "chosen_color",
            self.CREATURE_TYPE: "chosen_creature_type",
            self.BASIC_LAND_TYPE: "chosen_basic_land_type",
            self.FACTION: "chosen_faction",
            self.MARDU_JESKAI_FACTION: "chosen_faction",
            self.JESKAI_TEMUR_FACTION: "chosen_jeskai_temur_faction",
        }[self]

    @property
    def event_field(self) -> str:
        return "entry_" + self.annotation

    @property
    def values(self) -> tuple[str, ...]:
        if self is self.BASIC_LAND_TYPE:
            return tuple(BASIC_LAND_MANA)
        if self is self.FACTION:
            return ENTRY_FACTIONS
        if self is self.MARDU_JESKAI_FACTION:
            return MARDU_JESKAI_FACTIONS
        if self is self.JESKAI_TEMUR_FACTION:
            return ("Jeskai", "Temur")
        return tuple("WUBRG") if self is self.COLOR else tuple(sorted(CREATURE_SUBTYPES))


def validate_counted_entry_counter_name(value: object) -> str:
    if type(value) is not str or re.fullmatch(COUNTED_ENTRY_COUNTER_NAME, value) is None:
        raise ValueError("Counted creature-type entry requires an ordinary single-word counter name")
    return value


ENTRY_CARD_NAME_HANDLER_ID = "replacement.zone.entry-card-name.v1"
ENTRY_CARD_NAME_TEMPLATE = "intrinsic-entry-card-name-v1"
ENTRY_CARD_NAME_FIELD = "entry_chosen_card_name"
ENTRY_CARD_NAME_OPERATION = "choose_entry_card_name"


@dataclass(frozen=True, slots=True)
class EntryCardNameChoice:
    nonland: bool

    def __post_init__(self) -> None:
        if type(self.nonland) is not bool:
            raise ValueError("Entry card naming requires an explicit nonland scope")

    def to_dict(self) -> dict:
        return {"op": ENTRY_CARD_NAME_OPERATION, "schema_version": 1, "nonland": self.nonland}

    @classmethod
    def from_dict(cls, value: Mapping) -> "EntryCardNameChoice":
        if (
            not isinstance(value, Mapping) or set(value) != {"op", "schema_version", "nonland"}
            or value["op"] != ENTRY_CARD_NAME_OPERATION
            or type(value["schema_version"]) is not int or value["schema_version"] != 1
        ):
            raise ValueError("Entry card naming requires its version-one shape")
        return cls(value["nonland"])


def entry_card_name_operation(effect) -> EntryCardNameChoice | None:
    if len(effect.operations) == 1 and isinstance(effect.operations[0], EntryCardNameChoice):
        return effect.operations[0]
    return None


def implicit_entry_card_name_selection(response, pending, effects):
    """Use the existing name-only command when exactly one name effect is offered."""
    if (
        "replacement" in response or "card_name" not in response or pending.event_order_options
        or len(pending.choice.legal_selections) != 1
    ):
        return response
    selected, = pending.choice.legal_selections
    if any(effect.effect_id == selected and entry_card_name_operation(effect) is not None for effect in effects):
        return {**response, "replacement": selected}
    return response


def validate_bound_entry_card_name(name: object) -> str:
    if type(name) is not str or not name or name != name.strip():
        raise ValueError("An entry card name must be a canonical name")
    return name


def resolve_entry_card_name(database, submitted: object, *, nonland: bool) -> str:
    """Validate one public Oracle card or face name, including its own types."""
    if type(submitted) is not str or not submitted.strip() or type(nonland) is not bool:
        raise ValueError("An entry choice requires one card name")
    normalized = normalize_card_name(submitted)
    try:
        record = database.lookup(submitted, fuzzy=False, nameable_only=True)
    except KeyError as exc:
        raise ValueError("Choose a name in the Oracle card catalogue") from exc
    entries = (
        tuple((face.get("name"), face.get("type_line")) for face in record.faces)
        if record.faces else ((record.name, record.type_line),)
    )
    matching = {
        (name, type_line) for name, type_line in entries
        if isinstance(name, str) and normalize_card_name(name) == normalized and isinstance(type_line, str)
    }
    if len(matching) != 1:
        raise ValueError("Choose one printed name from the Oracle card catalogue")
    (name, type_line), = matching
    types = type_line.split("—", 1)[0].casefold().split()
    if nonland and (not types or "land" in types):
        raise ValueError("This entry choice requires a nonland card name")
    return name


def validate_designation(kind: EntryDesignationKind, value: object) -> str:
    if type(value) is not str:
        raise ValueError("An entry designation must be a canonical public value")
    if kind is EntryDesignationKind.COLOR:
        if value not in tuple("WUBRG"):
            raise ValueError("A chosen color must be one WUBRG color")
    elif kind is EntryDesignationKind.FACTION:
        # The shared stored field distinguishes both exact entry option sets by value.
        if value not in (*ENTRY_FACTIONS, *MARDU_JESKAI_FACTIONS):
            raise ValueError("A stored faction must be in one of the supported entry option sets")
    elif kind is EntryDesignationKind.MARDU_JESKAI_FACTION:
        if value not in MARDU_JESKAI_FACTIONS:
            raise ValueError("This entry choice requires Mardu or Jeskai")
    elif kind is EntryDesignationKind.JESKAI_TEMUR_FACTION:
        if value not in kind.values:
            raise ValueError("This faction choice must be Jeskai or Temur")
    elif kind is EntryDesignationKind.BASIC_LAND_TYPE:
        if value not in BASIC_LAND_MANA:
            raise ValueError("A chosen basic land type must use the pinned vocabulary")
    elif canonical_creature_subtype(value) != value:
        raise ValueError("A chosen creature type must use the pinned vocabulary")
    return value


def linked_player_damage_descriptor() -> dict:
    return {
        "handler_id": LINKED_PLAYER_DAMAGE_HANDLER_ID, "schema_version": 3, "event": "damage",
        "condition": {"recipient_scope": "chosen_player_or_controlled_permanent"},
        "modification": {"multiplier": 2, "additional": 0},
    }


@dataclass(frozen=True, slots=True)
class EntryOpponentChoice:
    """One player designation or prospective opponent-controller choice."""
    seat: str
    component_id: str
    schema_version: int = 1

    def __post_init__(self) -> None:
        if (
            type(self.schema_version) is not int or self.schema_version not in (1, 2, 3)
            or type(self.seat) is not str or not self.seat or self.seat != self.seat.strip()
            or type(self.component_id) is not str or not self.component_id
            or not self.component_id.endswith(":0")
        ):
            raise ValueError("An entry player choice needs one seat and its original entry component")

    @property
    def any_player(self) -> bool:
        return self.schema_version == 2

    @property
    def changes_entry_controller(self) -> bool:
        return self.schema_version == 3

    @property
    def entry_program_key(self) -> str:
        return self.component_id.removesuffix(":0")

    def to_dict(self) -> dict[str, str | int]:
        return {"schema_version": self.schema_version, "seat": self.seat, "component_id": self.component_id}

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> EntryOpponentChoice:
        if not isinstance(value, Mapping) or set(value) != {"schema_version", "seat", "component_id"}:
            raise ValueError("Entry opponent choices have a closed schema")
        return cls(seat=value["seat"], component_id=value["component_id"], schema_version=value["schema_version"])


def entry_opponent_effect_id(object_ref: str, choice: EntryOpponentChoice) -> str:
    handler = {
        1: ENTRY_OPPONENT_HANDLER_ID, 2: ENTRY_PLAYER_HANDLER_ID, 3: ENTRY_CONTROL_OPPONENT_HANDLER_ID,
    }[choice.schema_version]
    return f"{handler}:{object_ref}:{choice.component_id}:{choice.seat}"



@dataclass(frozen=True, slots=True)
class BoundEntryOpponentChoice:
    choice: EntryOpponentChoice
    logical_object_id: str
    face: str | None
    copy_form: str
    schema_version: int = 1

    def __post_init__(self) -> None:
        if (
            type(self.schema_version) is not int or self.schema_version != 1
            or not isinstance(self.choice, EntryOpponentChoice)
            or type(self.logical_object_id) is not str or not self.logical_object_id
            or self.face is not None and (type(self.face) is not str or not self.face)
            or type(self.copy_form) is not str or re.fullmatch(r"[0-9a-f]{64}", self.copy_form) is None
        ):
            raise ValueError("A retained opponent choice requires its entry incarnation and effective form")

    def to_dict(self) -> dict[str, object]:
        return {"schema_version": self.schema_version, "choice": self.choice.to_dict(),
                "logical_object_id": self.logical_object_id, "face": self.face, "copy_form": self.copy_form}

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> BoundEntryOpponentChoice:
        prior_fields = {"schema_version", "choice", "logical_object_id", "face", "copy_form"}
        if not isinstance(value, Mapping) or set(value) != prior_fields:
            raise ValueError("Retained opponent choices have a closed schema")
        return cls(EntryOpponentChoice.from_dict(value["choice"]), value["logical_object_id"], value["face"],
                   value["copy_form"], schema_version=value["schema_version"])

    def matches(self, card, *, copy_form: str) -> bool:
        return (card.zone == "battlefield" and not card.phased_out and card.logical_object_id == self.logical_object_id
                and card.active_face == self.face and copy_form == self.copy_form)


def linked_player_cast_condition() -> dict:
    return {"field": LINKED_PLAYER_CAST_FIELD, "op": "truthy", "value": True}


def linked_player_cast_mill_effect() -> dict:
    return {"op": "mill", "player": "$context.player", "count": 1}


def linked_opponent_upkeep_condition() -> dict:
    return {"all": [
        {"field": "step", "op": "eq", "value": "upkeep"},
        {"field": LINKED_OPPONENT_UPKEEP_FIELD, "op": "truthy", "value": True},
    ]}


def is_linked_opponent_upkeep_condition(condition) -> bool:
    return isinstance(condition, Mapping) and stable_json(condition) == stable_json(linked_opponent_upkeep_condition())
