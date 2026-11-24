from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum, IntEnum
from typing import Any, Mapping

from .activated_ability_descriptor import validate_activated_ability_descriptor
from .attachment_duration import AttachmentDuration
from .object_predicate import ObjectQuerySpec
from .source_continuity import SourceContinuitySnapshot
from .ability_fragments import ability_fragment_from_dict
from .replacement.immutable import (
    freeze_value,
    immutable_fingerprint,
    thaw_value,
)


class ContinuousEffectError(ValueError):
    pass


CONTINUOUS_DEPENDENCY_ORDER_VERSION = 1
CONTINUOUS_DEPENDENCY_ORDER_FIELD = "continuous_dependency_order_version"


def read_continuous_dependency_order_version(data: Mapping[str, Any]) -> int | None:
    if CONTINUOUS_DEPENDENCY_ORDER_FIELD not in data:
        return None
    value = data[CONTINUOUS_DEPENDENCY_ORDER_FIELD]
    if type(value) is not int or value != CONTINUOUS_DEPENDENCY_ORDER_VERSION:
        raise ContinuousEffectError("Invalid continuous dependency order version")
    return value


def continuous_dependency_order_fields(version: int | None) -> dict[str, int]:
    if version is None:
        return {}
    fields = {CONTINUOUS_DEPENDENCY_ORDER_FIELD: version}
    read_continuous_dependency_order_version(fields)
    return fields


class Layer(IntEnum):
    COPY = 1
    CONTROL = 2
    TEXT = 3
    TYPE = 4
    COLOR = 5
    ABILITY = 6
    POWER_TOUGHNESS = 7


class ContinuousEffectOrigin(str, Enum):
    """The CR 611 rule that determines an effect's affected-object set."""

    OBJECT = "object"
    REPLACEMENT = "replacement"
    RESOLUTION = "resolution"
    STATIC_ABILITY = "static_ability"


class ContinuousEffectDuration(str, Enum):
    """Closed duration vocabulary for represented continuous effects."""

    WHILE_SOURCE_PRESENT = "while_source_present"
    UNTIL_END_OF_TURN = "until_end_of_turn"
    UNTIL_NEXT_TURN = "until_next_turn"
    UNTIL_RECIPIENT_CONTROLLER_UNTAP = "until_recipient_controller_untap"
    UNTIL_CONTROL_CHANGE = "until_control_change"
    ZONE_OBJECT = "zone_object"
    UNTIL_UNATTACHED = "until_unattached"
    UNTIL_SOURCE_LEAVES = "until_source_leaves"
    UNTIL_SOURCE_BATTLEFIELD_EXIT = "until_source_battlefield_exit"
    UNTIL_SOURCE_CONTROL_CHANGES = "until_source_control_changes"
    UNTIL_SOURCE_LEAVES_OR_UNTAPS = "until_source_leaves_or_untaps"
    UNTIL_SOURCE_CONTROL_CHANGES_OR_UNTAPS = "until_source_control_changes_or_untaps"

    @property
    def source_bound(self) -> bool:
        """Whether the duration requires a source continuity snapshot."""
        return self in {
            self.UNTIL_SOURCE_LEAVES, self.UNTIL_SOURCE_CONTROL_CHANGES,
            self.UNTIL_SOURCE_LEAVES_OR_UNTAPS,
            self.UNTIL_SOURCE_CONTROL_CHANGES_OR_UNTAPS,
        }

    @property
    def requires_source_controller(self) -> bool:
        return self in {self.UNTIL_SOURCE_CONTROL_CHANGES,
                        self.UNTIL_SOURCE_CONTROL_CHANGES_OR_UNTAPS}

    @property
    def requires_tapped_source(self) -> bool:
        return self in {self.UNTIL_SOURCE_LEAVES_OR_UNTAPS,
                        self.UNTIL_SOURCE_CONTROL_CHANGES_OR_UNTAPS}


class ContinuousEffectRelation(str, Enum):
    """Closed live relationships that may scope a static effect.

    A relationship is authoritative identity data, not a characteristic
    predicate.  Keeping it separate from ``ObjectQuerySpec`` prevents an
    attachment effect from being approximated by controller, name, or another
    mutable characteristic of the affected object.
    """

    NONE = "none"
    SOURCE_OBJECT = "source_object"
    SOURCE_ATTACHED_TO_OBJECT = "source_attached_to_object"
    SOURCE_PAIRED_WITH_OBJECT = "source_paired_with_object"
    SOURCE_RING_BEARER_OBJECT = "source_ring_bearer_object"
    SOURCE_CONTROLLED_AURA_RECIPIENT = "source_controlled_aura_recipient"
    SOURCE_BLOCK_COUNTERPART_OBJECT = "source_block_counterpart_object"
    SOURCE_COUNTER_PLACEMENT_RECIPIENT = "source_counter_placement_recipient"


_OPERATION_LAYERS = {
    "copy_values": Layer.COPY,
    "face_down": Layer.COPY,
    "set_controller": Layer.CONTROL,
    "replace_text": Layer.TEXT,
    "set_types": Layer.TYPE,
    "set_basic_land_types": Layer.TYPE,
    "set_creature_subtype": Layer.TYPE,
    "set_creature_subtypes": Layer.TYPE,
    "set_creature_subtype_set": Layer.TYPE,
    "add_types": Layer.TYPE,
    "remove_types": Layer.TYPE,
    "remove_creature_card_type": Layer.TYPE,
    "set_colors": Layer.COLOR,
    "add_colors": Layer.COLOR,
    "remove_colors": Layer.COLOR,
    "remove_all_colors": Layer.COLOR,
    "add_ability": Layer.ABILITY,
    "remove_ability": Layer.ABILITY,
    "remove_color_protection": Layer.ABILITY,
    "remove_landwalk_abilities": Layer.ABILITY,
    "prohibit_ability": Layer.ABILITY,
    "remove_all_abilities": Layer.ABILITY,
    "add_rules_text": Layer.ABILITY,
    "add_ability_fragment": Layer.ABILITY,
    "remove_ability_fragment": Layer.ABILITY,
    "set_power": Layer.POWER_TOUGHNESS,
    "set_toughness": Layer.POWER_TOUGHNESS,
    "set_power_toughness": Layer.POWER_TOUGHNESS,
    "modify_power_toughness": Layer.POWER_TOUGHNESS,
    "modify_power_toughness_by_recipient_quantity": Layer.POWER_TOUGHNESS,
    "switch_power_toughness": Layer.POWER_TOUGHNESS,
}
_COPY_FIELDS = {
    "name",
    "controller",
    "mana_cost",
    "mana_value",
    "text",
    "executable_text",
    "supertypes",
    "card_types",
    "subtypes",
    "colors",
    "abilities",
    "ability_fragments",
    "activated_abilities",
    "power",
    "toughness",
    "loyalty",
    "defense",
    "copiable_values",
}
_WORD_FIELDS = {"supertypes", "card_types", "subtypes", "colors", "abilities"}
_LAYER_SUBLAYERS = {
    Layer.COPY: {"1a", "1b"},
    Layer.CONTROL: {"2"},
    Layer.TEXT: {"3"},
    Layer.TYPE: {"4"},
    Layer.COLOR: {"5"},
    Layer.ABILITY: {"6"},
    Layer.POWER_TOUGHNESS: {"7a", "7b", "7c", "7d"},
}


def _nonempty_words(value: Any, *, field_name: str) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)) or not value:
        raise ContinuousEffectError(f"{field_name} must be a nonempty string array")
    words = tuple(value)
    if not all(type(item) is str and item for item in words):
        raise ContinuousEffectError(f"{field_name} must be a nonempty string array")
    if len({item.casefold() for item in words}) != len(words):
        raise ContinuousEffectError(f"{field_name} values must be unique")
    return words


def _string_array(
    value: Any,
    *,
    field_name: str,
    unique: bool = True,
) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        raise ContinuousEffectError(f"{field_name} must be a string array")
    words = tuple(value)
    if not all(type(item) is str and item for item in words):
        raise ContinuousEffectError(f"{field_name} must be a string array")
    if unique and len({item.casefold() for item in words}) != len(words):
        raise ContinuousEffectError(f"{field_name} values must be unique")
    return words


def _validate_copy_values(value: Any) -> None:
    if not isinstance(value, Mapping) or not value:
        raise ContinuousEffectError("copy_values requires a nonempty object")
    unknown = set(value).difference(_COPY_FIELDS)
    if unknown:
        raise ContinuousEffectError(f"Unknown copiable fields: {sorted(unknown)}")
    for field_name, field_value in value.items():
        if field_name in _WORD_FIELDS:
            _string_array(
                field_value,
                field_name=f"copy_values.{field_name}",
                unique=field_name != "abilities",
            )
        elif field_name == "ability_fragments":
            if not isinstance(field_value, (list, tuple)):
                raise ContinuousEffectError(
                    "copy_values.ability_fragments must be an array"
                )
            try:
                for fragment in field_value:
                    ability_fragment_from_dict(fragment)
            except (TypeError, ValueError) as exc:
                raise ContinuousEffectError(str(exc)) from exc
        elif field_name == "activated_abilities":
            if not isinstance(field_value, (list, tuple)):
                raise ContinuousEffectError(
                    "copy_values.activated_abilities must be an array"
                )
            try:
                for ability in field_value:
                    validate_activated_ability_descriptor(thaw_value(ability))
            except (TypeError, ValueError) as exc:
                raise ContinuousEffectError(str(exc)) from exc
        elif field_name in {
            "name",
            "controller",
            "mana_cost",
            "text",
            "executable_text",
        }:
            if type(field_value) is not str:
                raise ContinuousEffectError(
                    f"copy_values.{field_name} must be a string"
                )
        elif field_name == "mana_value":
            if type(field_value) not in {int, float} or field_value < 0:
                raise ContinuousEffectError(
                    "copy_values.mana_value must be a nonnegative number"
                )
        elif field_name in {"power", "toughness", "loyalty", "defense"}:
            if field_value is not None and type(field_value) is not int:
                raise ContinuousEffectError(
                    f"copy_values.{field_name} must be an integer or null"
                )
        elif field_name == "copiable_values" and not isinstance(
            field_value, Mapping
        ):
            raise ContinuousEffectError(
                "copy_values.copiable_values must be an object"
            )


def _validate_operation_value(op: str, value: Any, field: str | None) -> None:
    if op not in {"set_types", "add_types", "remove_types"} and field is not None:
        raise ContinuousEffectError(f"{op} does not accept a field")
    if op == "copy_values":
        _validate_copy_values(value)
    elif op == "face_down":
        if not isinstance(value, Mapping):
            raise ContinuousEffectError("face_down requires an object")
        allowed = {
            "name",
            "mana_cost",
            "mana_value",
            "text",
            "supertypes",
            "card_types",
            "subtypes",
            "colors",
            "abilities",
            "ability_fragments",
            "power",
            "toughness",
        }
        if set(value).difference(allowed):
            raise ContinuousEffectError("face_down contains unknown fields")
        for field_name in _WORD_FIELDS.intersection(value):
            _string_array(
                value[field_name],
                field_name=f"face_down.{field_name}",
                unique=field_name != "abilities",
            )
        for field_name in {"name", "mana_cost", "text"}.intersection(value):
            if type(value[field_name]) is not str:
                raise ContinuousEffectError(
                    f"face_down.{field_name} must be a string"
                )
        if "ability_fragments" in value:
            fragments = value["ability_fragments"]
            if not isinstance(fragments, (list, tuple)):
                raise ContinuousEffectError(
                    "face_down.ability_fragments must be an array"
                )
            try:
                for fragment in fragments:
                    ability_fragment_from_dict(fragment)
            except (TypeError, ValueError) as exc:
                raise ContinuousEffectError(
                    "face_down.ability_fragments are malformed"
                ) from exc
        if "mana_value" in value and (
            type(value["mana_value"]) not in {int, float}
            or value["mana_value"] < 0
        ):
            raise ContinuousEffectError(
                "face_down.mana_value must be a nonnegative number"
            )
        for field_name in {"power", "toughness"}.intersection(value):
            if type(value[field_name]) is not int:
                raise ContinuousEffectError(
                    f"face_down.{field_name} must be an integer"
                )
    elif op == "set_controller":
        if type(value) is not str or not value:
            raise ContinuousEffectError("set_controller requires a nonempty string")
    elif op == "replace_text":
        if not isinstance(value, Mapping) or set(value) != {"from", "to"}:
            raise ContinuousEffectError("replace_text requires exact from/to fields")
        if type(value["from"]) is not str or not value["from"] or type(value["to"]) is not str:
            raise ContinuousEffectError("replace_text values must be strings and from must be nonempty")
    elif op == 'set_basic_land_types':
        from .basic_land_types import BASIC_LAND_MANA
        words=_nonempty_words(value,field_name=op)
        if field is not None or any(word not in BASIC_LAND_MANA for word in words):
            raise ContinuousEffectError('Basic land type setting requires canonical basic subtypes and no field')
    elif op == "set_creature_subtype_set":
        if field is not None or type(value) is not str or value not in {"all", "none"}:
            raise ContinuousEffectError("Creature subtype sets require all or none without a field")
    elif op == "set_creature_subtypes":
        from .creature_subtypes import canonical_creature_subtype

        subtypes = _nonempty_words(value, field_name=op)
        if (field is not None or any(canonical_creature_subtype(word) != word for word in subtypes)
                or tuple(subtypes) != tuple(sorted(set(subtypes)))):
            raise ContinuousEffectError("A creature subtype replacement needs distinct canonical types")
    elif op == "set_creature_subtype":
        from .creature_subtypes import canonical_creature_subtype

        if canonical_creature_subtype(value) != value or value is None:
            raise ContinuousEffectError("Creature type setting requires one canonical creature subtype")
    elif op in {"set_types", "add_types", "remove_types"}:
        if field not in {None, "supertypes", "card_types", "subtypes"}:
            raise ContinuousEffectError("Type operations require a represented type field")
        if not (
            op == "set_types"
            and field == "subtypes"
            and isinstance(value, (list, tuple))
            and not value
        ):
            _nonempty_words(value, field_name=op)
    elif op in {"set_colors", "add_colors", "remove_colors"}:
        if field is not None:
            raise ContinuousEffectError("Color operations do not accept a field")
        colors = _nonempty_words(value, field_name=op)
        if any(color.upper() not in set("WUBRGC") for color in colors):
            raise ContinuousEffectError("Color operations require Magic color symbols")
    elif op in {"add_ability", "remove_ability", "prohibit_ability", "add_rules_text"}:
        if field is not None or type(value) is not str or not value:
            raise ContinuousEffectError(f"{op} requires one nonempty ability string")
    elif op == "remove_color_protection":
        if field is not None or type(value) is not str or value not in {"W", "U", "B", "R", "G"}:
            raise ContinuousEffectError("remove_color_protection requires one canonical color symbol")
    elif op in {"add_ability_fragment", "remove_ability_fragment"}:
        if field is not None or not isinstance(value, Mapping):
            raise ContinuousEffectError(
                f"{op} requires one typed ability-fragment object"
            )
        try:
            ability_fragment_from_dict(value)
        except (TypeError, ValueError) as exc:
            raise ContinuousEffectError(str(exc)) from exc
    elif op in {
        "remove_all_abilities",
        "remove_landwalk_abilities",
        "remove_all_colors",
        "remove_creature_card_type",
        "switch_power_toughness",
    }:
        if field is not None or value is not None:
            raise ContinuousEffectError(f"{op} accepts no value or field")
    elif op in {"set_power", "set_toughness"}:
        if field is not None or type(value) is not int:
            raise ContinuousEffectError(f"{op} requires one integer")
    elif op == 'modify_power_toughness_by_recipient_quantity':
        from .recipient_modifier_model import validate_recipient_modifier
        if field is not None:raise ContinuousEffectError('Recipient modifiers accept no field')
        try:validate_recipient_modifier(value)
        except (TypeError,ValueError) as exc:raise ContinuousEffectError(str(exc)) from exc
    elif op in {"set_power_toughness", "modify_power_toughness"}:
        if field is not None or not isinstance(value, (list, tuple)) or len(value) != 2:
            raise ContinuousEffectError(f"{op} requires an integer pair")
        if any(type(item) is not int for item in value):
            raise ContinuousEffectError(f"{op} requires an integer pair")


@dataclass(frozen=True, slots=True)
class ContinuousOperation:
    op: str
    value: Any = None
    field: str | None = None

    def __post_init__(self) -> None:
        if type(self.op) is not str or not self.op:
            raise ContinuousEffectError(
                "Continuous operation names must be nonempty strings"
            )
        if self.op not in _OPERATION_LAYERS:
            raise ContinuousEffectError(
                f"Unsupported continuous operation {self.op!r}"
            )
        if self.field is not None and (
            type(self.field) is not str or not self.field
        ):
            raise ContinuousEffectError(
                "Continuous operation fields must be nonempty strings or null"
            )
        _validate_operation_value(self.op, self.value, self.field)
        try:
            object.__setattr__(
                self,
                "value",
                freeze_value(
                    self.value, field="continuous operation value"
                ),
            )
        except ValueError as exc:
            raise ContinuousEffectError(str(exc)) from exc

    def to_dict(self) -> dict[str, Any]:
        return {
            "op": self.op,
            "value": thaw_value(self.value),
            "field": self.field,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ContinuousOperation":
        if not isinstance(value, Mapping):
            raise ContinuousEffectError(
                "Continuous operation must be an object"
            )
        if set(value) != {"op", "value", "field"}:
            raise ContinuousEffectError(
                "Continuous operation fields must be op, value, and field"
            )
        if type(value["op"]) is not str:
            raise ContinuousEffectError(
                "Continuous operation names must be strings"
            )
        return cls(
            op=value["op"], value=value["value"], field=value["field"]
        )


@dataclass(frozen=True, slots=True, order=True)
class ContinuousObjectIdentity:
    """One battlefield object's physical and current logical identity."""

    object_id: str
    logical_object_id: str

    def __post_init__(self) -> None:
        if (
            type(self.object_id) is not str
            or type(self.logical_object_id) is not str
            or not self.object_id
            or not self.logical_object_id
        ):
            raise ContinuousEffectError(
                "Locked continuous-effect objects require physical and logical IDs"
            )

    def to_dict(self) -> dict[str, str]:
        return {
            "object_id": self.object_id,
            "logical_object_id": self.logical_object_id,
        }

    @classmethod
    def from_dict(
        cls, value: Mapping[str, Any]
    ) -> "ContinuousObjectIdentity":
        if not isinstance(value, Mapping) or set(value) != {
            "object_id",
            "logical_object_id",
        }:
            raise ContinuousEffectError(
                "Locked continuous-effect identity fields are invalid"
            )
        if type(value["object_id"]) is not str or type(
            value["logical_object_id"]
        ) is not str:
            raise ContinuousEffectError(
                "Locked continuous-effect identity values must be strings"
            )
        return cls(
            object_id=value["object_id"],
            logical_object_id=value["logical_object_id"],
        )


def _validate_continuous_effect_identity_scope(
    effect: "ContinuousEffect",
    locked: tuple[ContinuousObjectIdentity, ...],
) -> None:
    from .attachment_duration import validate_attachment_duration
    try:
        validate_attachment_duration(effect, locked)
    except ValueError as exc:
        raise ContinuousEffectError(str(exc)) from exc
    validate_turn_expiry(effect.duration, effect.expires_on_next_turn_of)
    if (effect.duration is ContinuousEffectDuration.UNTIL_NEXT_TURN
            and effect.origin is not ContinuousEffectOrigin.RESOLUTION):
        raise ContinuousEffectError("Next-turn duration requires a resolution-created effect")
    if effect.duration in {
        ContinuousEffectDuration.UNTIL_RECIPIENT_CONTROLLER_UNTAP,
        ContinuousEffectDuration.UNTIL_SOURCE_BATTLEFIELD_EXIT,
    }:
        if (
            effect.origin is not ContinuousEffectOrigin.RESOLUTION
            or effect.layer is not Layer.TYPE
            or len(locked) != 1
            or len(effect.operations) != 1
            or effect.operations[0].op != "set_basic_land_types"
            or effect.applies != ObjectQuerySpec(zones=("battlefield",))
        ):
            raise ContinuousEffectError("Land type duration requires one locked battlefield land-type setting")
    if effect.duration is ContinuousEffectDuration.UNTIL_SOURCE_BATTLEFIELD_EXIT:
        if (
            not isinstance(effect.duration_source, ContinuousObjectIdentity)
            or effect.source_id != effect.duration_source.object_id
            or effect.duration_controller is not None
            or effect.duration_history is not None
        ):
            raise ContinuousEffectError("Battlefield-exit duration requires only the original source identity")
    elif effect.duration.source_bound:
        if (effect.origin is not ContinuousEffectOrigin.RESOLUTION
                or (effect.layer is not Layer.CONTROL and not (
                    effect.duration is ContinuousEffectDuration.UNTIL_SOURCE_LEAVES_OR_UNTAPS
                    and effect.layer in {Layer.TYPE, Layer.COLOR, Layer.ABILITY, Layer.POWER_TOUGHNESS}
                    or effect.duration is ContinuousEffectDuration.UNTIL_SOURCE_LEAVES
                    and effect.layer is Layer.TYPE and len(locked) == len(effect.operations) == 1
                    and effect.operations[0].op == "set_basic_land_types"
                ))
                or not isinstance(effect.duration_source, ContinuousObjectIdentity)
                or not isinstance(effect.duration_history, SourceContinuitySnapshot)
                or effect.source_id != effect.duration_source.object_id):
            raise ContinuousEffectError("Source-bound effects require an original source incarnation and a supported layer")
        if effect.duration.requires_source_controller:
            if type(effect.duration_controller) is not str or not effect.duration_controller:
                raise ContinuousEffectError("Source-control duration requires its resolving controller")
        elif effect.duration_controller is not None:
            raise ContinuousEffectError("Source-presence duration cannot add a controller condition")
    elif any(value is not None for value in (effect.duration_source, effect.duration_controller, effect.duration_history)):
        raise ContinuousEffectError("Only source-bound effects may carry duration source facts")
    locked_origins = {
        ContinuousEffectOrigin.REPLACEMENT,
        ContinuousEffectOrigin.RESOLUTION,
    }
    if effect.origin in locked_origins and not locked:
        raise ContinuousEffectError(
            "Generated continuous effects require a locked object set"
        )
    if (
        effect.origin in locked_origins
        and effect.duration is ContinuousEffectDuration.WHILE_SOURCE_PRESENT
    ):
        raise ContinuousEffectError(
            "Generated continuous effects require an explicit duration"
        )
    if effect.origin not in locked_origins and locked:
        raise ContinuousEffectError(
            "Only generated continuous effects may lock objects"
        )
    if (
        effect.origin is ContinuousEffectOrigin.STATIC_ABILITY
        and effect.duration
        is not ContinuousEffectDuration.WHILE_SOURCE_PRESENT
    ):
        raise ContinuousEffectError(
            "Static-ability continuous effects require source presence"
        )
    if effect.relation is ContinuousEffectRelation.NONE:
        if effect.related_object is not None:
            raise ContinuousEffectError(
                "Unrelated continuous effects cannot name a related object"
            )
        return
    if not isinstance(effect.related_object, ContinuousObjectIdentity):
        raise ContinuousEffectError(
            "Related continuous effects require a typed related object"
        )
    if effect.origin is not ContinuousEffectOrigin.STATIC_ABILITY:
        raise ContinuousEffectError(
            "Live source relations require a static-ability effect"
        )
    if effect.duration is not ContinuousEffectDuration.WHILE_SOURCE_PRESENT:
        raise ContinuousEffectError(
            "Live source relations require source presence"
        )
    if effect.relation is ContinuousEffectRelation.SOURCE_CONTROLLED_AURA_RECIPIENT:
        valid_layer = (
            effect.layer is Layer.ABILITY and all(op.op == "add_ability" for op in effect.operations)
            or (effect.layer, effect.sublayer) == (Layer.POWER_TOUGHNESS, "7b")
            and tuple(op.op for op in effect.operations) == ("set_power_toughness",)
        )
        predicate = effect.applies
        if (not valid_layer or effect.related_object.object_id == effect.source_id
                or not predicate.controller or not predicate.exclude_ref
                or predicate != ObjectQuerySpec(
                    zones=("battlefield",), types_all=("creature",),
                    controller=predicate.controller, exclude_ref=predicate.exclude_ref,
                )):
            raise ContinuousEffectError("Controlled Aura recipients require one other controlled creature and fixed late layers")
    elif effect.relation is ContinuousEffectRelation.SOURCE_BLOCK_COUNTERPART_OBJECT and (
            (effect.layer, effect.sublayer) != (Layer.ABILITY, "6")
            or effect.operations != (ContinuousOperation("add_ability", "Lifelink"),)
            or effect.related_object.object_id == effect.source_id
            or effect.applies != ObjectQuerySpec(zones=("battlefield",), types_all=("creature",))):
        raise ContinuousEffectError("A live block counterpart requires one other creature and ordinary lifelink")
    elif effect.relation is ContinuousEffectRelation.SOURCE_COUNTER_PLACEMENT_RECIPIENT:
        predicate = effect.applies
        if (effect.layer is not Layer.ABILITY or any(op.op != "add_ability" for op in effect.operations)
                or not predicate.controller or predicate != ObjectQuerySpec(
                    zones=("battlefield",), types_all=("creature",),
                    controller=predicate.controller, exclude_ref=predicate.exclude_ref,
                )):
            raise ContinuousEffectError("Counter placement recipients require a current controlled creature and fixed keywords")


def _validate_recipient_scope_anchor(effect: "ContinuousEffect") -> None:
    anchor = effect.recipient_scope_anchor
    if anchor is None:
        return
    if type(anchor) is not str or not anchor:
        raise ContinuousEffectError("A retained recipient scope requires an effect identity")
    if (effect.origin is not ContinuousEffectOrigin.STATIC_ABILITY
            or effect.duration is not ContinuousEffectDuration.WHILE_SOURCE_PRESENT
            or effect.relation is not ContinuousEffectRelation.NONE
            or effect.locked_objects or effect.characteristic_defining
            or not effect.applies.controlled_by_nonowner or not effect.applies.controller
            or effect.applies.types_all != ("creature",)):
        raise ContinuousEffectError("Retained recipients require a live nonowner creature query")
    if (anchor == effect.effect_id) != (effect.layer is Layer.TYPE) or effect.sublayer not in {"4","6","7c"}:
        raise ContinuousEffectError("Retained recipients start in layer four and continue in six or seven c")


@dataclass(frozen=True, slots=True)
class ContinuousEffect:
    effect_id: str
    source_id: str
    layer: Layer
    sublayer: str
    timestamp: int
    operations: tuple[ContinuousOperation, ...]
    depends_on: tuple[str, ...] = ()
    characteristic_defining: bool = False
    origin: ContinuousEffectOrigin = ContinuousEffectOrigin.OBJECT
    duration: ContinuousEffectDuration = (
        ContinuousEffectDuration.WHILE_SOURCE_PRESENT
    )
    source_present: bool = True
    applies: ObjectQuerySpec = field(default_factory=ObjectQuerySpec)
    locked_objects: tuple[ContinuousObjectIdentity, ...] = ()
    relation: ContinuousEffectRelation = ContinuousEffectRelation.NONE
    related_object: ContinuousObjectIdentity | None = None
    duration_source: ContinuousObjectIdentity | None = None
    duration_controller: str | None = None
    duration_history: SourceContinuitySnapshot | None = None
    denied_keyword_grants: tuple[tuple[str, str, str], ...] = ()
    attachment_duration: AttachmentDuration | None = None
    expires_on_next_turn_of: str | None = None
    recipient_scope_anchor: str | None = None

    def __post_init__(self) -> None:
        for name, value in (
            ("effect_id", self.effect_id),
            ("source_id", self.source_id),
            ("sublayer", self.sublayer),
        ):
            if type(value) is not str or not value:
                raise ContinuousEffectError(
                    f"Continuous effect {name} must be a nonempty string"
                )
        if type(self.timestamp) is not int or self.timestamp < 0:
            raise ContinuousEffectError(
                "Continuous effect timestamps must be nonnegative integers"
            )
        if type(self.characteristic_defining) is not bool:
            raise ContinuousEffectError(
                "Continuous effect characteristic_defining must be boolean"
            )
        if not isinstance(self.layer, Layer) and type(self.layer) is not int:
            raise ContinuousEffectError(
                "Continuous effect layer must be an integer layer"
            )
        try:
            object.__setattr__(self, "layer", Layer(self.layer))
            object.__setattr__(
                self, "origin", ContinuousEffectOrigin(self.origin)
            )
            object.__setattr__(
                self, "duration", ContinuousEffectDuration(self.duration)
            )
            object.__setattr__(
                self, "relation", ContinuousEffectRelation(self.relation)
            )
        except (TypeError, ValueError) as exc:
            raise ContinuousEffectError(
                "Continuous effect layer, origin, duration, or relation is invalid"
            ) from exc
        if self.sublayer not in _LAYER_SUBLAYERS[self.layer]:
            raise ContinuousEffectError(
                f"Sublayer {self.sublayer!r} is not in layer {self.layer}"
            )
        try:
            operations = tuple(self.operations)
        except TypeError as exc:
            raise ContinuousEffectError(
                "Continuous effects require typed operations"
            ) from exc
        object.__setattr__(self, "operations", operations)
        if not operations:
            raise ContinuousEffectError(
                "Continuous effects require at least one operation"
            )
        if not all(
            isinstance(operation, ContinuousOperation)
            for operation in operations
        ):
            raise ContinuousEffectError(
                "Continuous effects require typed operations"
            )
        if any(
            _OPERATION_LAYERS[operation.op] is not self.layer
            for operation in operations
        ):
            raise ContinuousEffectError(
                "Continuous operations must be placed in their represented layer"
            )
        if not isinstance(self.applies, ObjectQuerySpec):
            raise ContinuousEffectError(
                "Continuous-effect applicability requires ObjectQuerySpec"
            )
        if type(self.source_present) is not bool:
            raise ContinuousEffectError(
                "Continuous-effect source presence must be boolean"
            )
        try:
            depends_on = tuple(self.depends_on)
        except TypeError as exc:
            raise ContinuousEffectError(
                "Continuous effect dependencies must be nonempty strings"
            ) from exc
        if not all(type(value) is str and value for value in depends_on):
            raise ContinuousEffectError(
                "Continuous effect dependencies must be nonempty strings"
            )
        if len(set(depends_on)) != len(depends_on):
            raise ContinuousEffectError(
                "Continuous effect dependencies must be unique"
            )
        object.__setattr__(self, "depends_on", depends_on)
        try:
            locked = tuple(sorted(tuple(self.locked_objects)))
        except (TypeError, AttributeError) as exc:
            raise ContinuousEffectError(
                "Continuous-effect locked identities must be unique typed values"
            ) from exc
        if len(set(locked)) != len(locked) or not all(
            isinstance(identity, ContinuousObjectIdentity)
            for identity in locked
        ):
            raise ContinuousEffectError(
                "Continuous-effect locked identities must be unique typed values"
            )
        object.__setattr__(self, "locked_objects", locked)
        _validate_continuous_effect_identity_scope(self, locked)
        _validate_recipient_scope_anchor(self)
        denied = self.denied_keyword_grants
        if not isinstance(denied, (tuple, list)) or any(
            not isinstance(row, (tuple, list)) or len(row) != 3
            or any(type(value) is not str or not value for value in row) for row in denied
        ):
            raise ContinuousEffectError('Denied keyword grants require closed object, incarnation and keyword triples')
        denied = tuple(tuple(row) for row in denied)
        allowed = {str(op.value).casefold() for op in operations if op.op == 'add_ability'}
        if (len(set(denied)) != len(denied) or (denied and self.origin is not ContinuousEffectOrigin.RESOLUTION)
                or any(ContinuousObjectIdentity(row[0], row[1]) not in locked
                    or row[2] != row[2].casefold() or row[2] not in allowed for row in denied)):
            raise ContinuousEffectError('Denied keyword grants must name a locked resolution target and added keyword')
        object.__setattr__(self, 'denied_keyword_grants', denied)

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "effect_id": self.effect_id,
            "source_id": self.source_id,
            "layer": int(self.layer),
            "sublayer": self.sublayer,
            "timestamp": self.timestamp,
            "operations": [value.to_dict() for value in self.operations],
            "depends_on": list(self.depends_on),
            "characteristic_defining": self.characteristic_defining,
            "origin": self.origin.value,
            "duration": self.duration.value,
            "source_present": self.source_present,
            "applies": self.applies.to_dict(),
            "locked_objects": [
                value.to_dict() for value in self.locked_objects
            ],
        }
        if self.relation is not ContinuousEffectRelation.NONE:
            payload.update(
                {
                    "relation": self.relation.value,
                    "related_object": self.related_object.to_dict(),
                }
            )
        if self.duration.source_bound:
            payload.update({"duration_source": self.duration_source.to_dict(),
                            "duration_controller": self.duration_controller,
                            "duration_history": self.duration_history.to_dict()})
        elif self.duration is ContinuousEffectDuration.UNTIL_SOURCE_BATTLEFIELD_EXIT:
            payload["duration_source"] = self.duration_source.to_dict()
        if self.denied_keyword_grants:
            payload['denied_keyword_grants'] = [list(row) for row in self.denied_keyword_grants]
        if self.attachment_duration is not None:
            payload['attachment_duration'] = self.attachment_duration.to_dict()
        if self.expires_on_next_turn_of is not None:
            payload["expires_on_next_turn_of"] = self.expires_on_next_turn_of
        if self.recipient_scope_anchor is not None:
            payload["recipient_scope_anchor"] = self.recipient_scope_anchor
        return payload

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ContinuousEffect":
        legacy_fields = {
            "effect_id",
            "source_id",
            "layer",
            "sublayer",
            "timestamp",
            "operations",
            "depends_on",
            "characteristic_defining",
            "origin",
            "duration",
            "source_present",
            "applies",
            "locked_objects",
        }
        relation_fields = legacy_fields | {"relation", "related_object"}
        duration_fields = legacy_fields | {"duration_source", "duration_controller", "duration_history"}
        if not isinstance(value, Mapping):
            raise ContinuousEffectError(
                "Continuous effect fields are missing or unknown"
            )
        turn_expiry = decode_turn_expiry(value)
        if "recipient_scope_anchor" in value and value["recipient_scope_anchor"] is None:
            raise ContinuousEffectError("An explicit recipient scope anchor cannot be null")
        actual_fields = frozenset(set(value) - {'denied_keyword_grants', 'attachment_duration', 'expires_on_next_turn_of', 'recipient_scope_anchor'})
        if actual_fields not in {
            frozenset(legacy_fields),
            frozenset(relation_fields),
            frozenset(duration_fields),
            frozenset(legacy_fields | {"duration_source"}),
        }:
            raise ContinuousEffectError(
                "Continuous effect fields are missing or unknown"
            )
        if not isinstance(value["operations"], list) or not isinstance(
            value["locked_objects"], list
        ):
            raise ContinuousEffectError(
                "Continuous effect operations and locked objects must be arrays"
            )
        if not isinstance(value["depends_on"], list) or not all(
            type(item) is str and item for item in value["depends_on"]
        ):
            raise ContinuousEffectError(
                "Continuous effect dependencies must be nonempty strings"
            )
        if type(value["characteristic_defining"]) is not bool:
            raise ContinuousEffectError(
                "Continuous effect characteristic_defining must be boolean"
            )
        if (
            type(value["effect_id"]) is not str
            or type(value["source_id"]) is not str
            or type(value["sublayer"]) is not str
            or type(value["layer"]) is not int
            or type(value["timestamp"]) is not int
            or type(value["origin"]) is not str
            or type(value["duration"]) is not str
            or type(value["source_present"]) is not bool
        ):
            raise ContinuousEffectError(
                "Continuous effect scalar fields have invalid types"
            )
        try:
            return cls(
                effect_id=value["effect_id"],
                source_id=value["source_id"],
                layer=Layer(value["layer"]),
                sublayer=value["sublayer"],
                timestamp=value["timestamp"],
                operations=tuple(
                    ContinuousOperation.from_dict(operation)
                    for operation in value["operations"]
                ),
                depends_on=tuple(value["depends_on"]),
                characteristic_defining=value["characteristic_defining"],
                origin=ContinuousEffectOrigin(value["origin"]),
                duration=ContinuousEffectDuration(value["duration"]),
                source_present=value["source_present"],
                applies=ObjectQuerySpec.from_dict(value["applies"]),
                locked_objects=tuple(
                    ContinuousObjectIdentity.from_dict(identity)
                    for identity in value["locked_objects"]
                ),
                relation=(
                    ContinuousEffectRelation(value["relation"])
                    if "relation" in value
                    else ContinuousEffectRelation.NONE
                ),
                related_object=(
                    ContinuousObjectIdentity.from_dict(
                        value["related_object"]
                    )
                    if "related_object" in value
                    else None
                ),
                duration_source=(
                    ContinuousObjectIdentity.from_dict(value["duration_source"])
                    if "duration_source" in value else None
                ),
                duration_controller=value.get("duration_controller"),
                duration_history=(
                    SourceContinuitySnapshot.from_dict(value["duration_history"])
                    if "duration_history" in value else None
                ),
                denied_keyword_grants=value.get('denied_keyword_grants', ()),
                attachment_duration=(AttachmentDuration.from_dict(value['attachment_duration'])
                    if 'attachment_duration' in value else None),
                expires_on_next_turn_of=turn_expiry,
                recipient_scope_anchor=value.get("recipient_scope_anchor"),
            )
        except (TypeError, ValueError) as exc:
            if isinstance(exc, ContinuousEffectError):
                raise
            raise ContinuousEffectError(str(exc)) from exc

    @property
    def fingerprint(self) -> str:
        return immutable_fingerprint(self.to_dict())


def validate_turn_expiry(duration: ContinuousEffectDuration, player: str | None) -> None:
    if duration is ContinuousEffectDuration.UNTIL_NEXT_TURN:
        if type(player) is not str or not player:
            raise ContinuousEffectError("Next-turn duration requires an expiry player")
    elif player is not None:
        raise ContinuousEffectError("Only next-turn duration may name an expiry player")


def decode_turn_expiry(value: Mapping[str, Any]) -> str | None:
    has_anchor = "expires_on_next_turn_of" in value
    if has_anchor != (value.get("duration") == ContinuousEffectDuration.UNTIL_NEXT_TURN.value):
        raise ContinuousEffectError("Next-turn duration and its expiry player must be recorded together")
    return value.get("expires_on_next_turn_of")

__all__ = [
    "ContinuousEffect",
    "ContinuousEffectDuration",
    "ContinuousEffectError",
    "ContinuousEffectOrigin",
    "ContinuousEffectRelation",
    "ContinuousObjectIdentity",
    "ContinuousOperation",
    "Layer",
]
