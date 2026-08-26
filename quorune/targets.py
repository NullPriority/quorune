from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Any, Iterable, Mapping, Sequence

from .object_predicate import (
    ObjectQueryError,
    PermanentStatePredicateSpec,
)
from .relative_power_target import (
    RelativePowerTargetCondition,
    RelativePowerTargetError,
)
from .target_forms import TargetCharacteristicForm


PUBLIC_TARGET_ZONES = {
    "battlefield",
    "stack",
    "graveyard",
    "exile",
    "command",
    "player",
}


LEGACY_SELECTORS: dict[str, dict[str, Any]] = {
    "opponent": {
        "zones": ["player"],
        "categories": ["player"],
        "player_relation": "opponent",
    },
    "blue_spell_or_permanent": {
        "zones": ["stack", "battlefield"],
        "categories": ["spell", "permanent"],
        "colors_any": ["U"],
    },
}


def _strings(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        values = (value,)
    elif isinstance(value, (list, tuple)):
        values = tuple(value)
    else:
        raise ValueError("Target string predicates must be strings or arrays")
    if any(type(item) is not str or not item for item in values):
        raise ValueError("Target string predicates require nonempty strings")
    if len(set(values)) != len(values):
        raise ValueError("Target string predicates require unique values")
    return values


def _optional_bool(value: Any) -> bool | None:
    if value is None:
        return None
    if type(value) is not bool:
        raise ValueError("Target boolean predicates must be boolean or null")
    return value


def _optional_exact_int(
    value: Any,
    *,
    field: str,
) -> int | None:
    if value is None:
        return None
    if type(value) is not int or not 0 <= value <= 5:
        raise ValueError(f"{field} must be an exact integer from 0 through 5")
    return value


def _characteristic_forms(value: Any) -> tuple[TargetCharacteristicForm, ...]:
    if not isinstance(value, (list, tuple)):
        raise ValueError("Target characteristic_forms_any must be an array")
    forms = tuple(
        TargetCharacteristicForm.from_mapping(form) for form in value
    )
    if len(forms) != len(set(forms)):
        raise ValueError("Target characteristic forms must be unique")
    return forms


def _target_count_bounds(raw: Mapping[str, Any]) -> tuple[int, int]:
    count = raw.get("count")
    minimum = raw.get("min", raw.get("minimum", count))
    maximum = raw.get("max", raw.get("maximum", count))
    if raw.get("up_to") is not None:
        minimum = 0
        maximum = raw["up_to"]
    if minimum is not None and type(minimum) is not int:
        raise ValueError("Target minimum must be an exact integer")
    if maximum is not None and type(maximum) is not int:
        raise ValueError("Target maximum must be an exact integer")
    minimum = 1 if minimum is None else minimum
    maximum = minimum if maximum is None else maximum
    if minimum < 0 or maximum < minimum:
        raise ValueError("Target count bounds are invalid")
    return minimum, maximum


def _target_zones(raw: Mapping[str, Any]) -> tuple[str, ...]:
    zones = _strings(raw.get("zones", raw.get("zone", ("battlefield",))))
    unknown_zones = sorted(set(zones) - PUBLIC_TARGET_ZONES)
    if unknown_zones:
        raise ValueError(
            "Target schemas may not enumerate hidden/nonpublic zones: "
            + ", ".join(unknown_zones)
        )
    return zones


def _target_relations(raw: Mapping[str, Any]) -> tuple[str, str, str]:
    controller = str(
        raw.get("controller", raw.get("controller_relation", "any"))
    )
    owner = str(raw.get("owner", raw.get("owner_relation", "any")))
    player = str(raw.get("player_relation", "any"))
    for name, relation in (
        ("controller", controller),
        ("owner", owner),
        ("player", player),
    ):
        if relation not in {"any", "you", "opponent"}:
            raise ValueError(f"Unknown {name} relation {relation!r}")
    return controller, owner, player


def _target_resolution_condition(
    raw: Mapping[str, Any],
    *,
    predicate: str,
) -> dict[str, Any]:
    raw_condition = raw.get("resolution_condition") or {}
    if not isinstance(raw_condition, Mapping):
        raise ValueError("Target resolution_condition must be an object")
    resolution_condition = dict(raw_condition)
    if predicate != "power_less_than_source":
        return resolution_condition
    try:
        return RelativePowerTargetCondition.from_dict(
            resolution_condition
        ).to_dict()
    except RelativePowerTargetError as exc:
        raise ValueError(str(exc)) from exc


def _target_state_predicate(
    raw: Mapping[str, Any],
) -> PermanentStatePredicateSpec | None:
    value = raw.get("state_predicate")
    if value is None:
        return None
    try:
        return PermanentStatePredicateSpec.from_dict(value)
    except ObjectQueryError as exc:
        raise ValueError(str(exc)) from exc


def _target_characteristic_fields(
    raw: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "categories": _strings(raw.get("categories", raw.get("category"))),
        "types_any": _strings(
            raw.get("types_any", raw.get("card_types", raw.get("type")))
        ),
        "types_all": _strings(raw.get("types_all")),
        "types_none": _strings(raw.get("types_none")),
        "subtypes_any": _strings(
            raw.get("subtypes_any", raw.get("subtype"))
        ),
        "subtypes_none": _strings(raw.get("subtypes_none")),
        "supertypes_any": _strings(
            raw.get("supertypes_any", raw.get("supertype"))
        ),
        "supertypes_none": _strings(raw.get("supertypes_none")),
        "keywords_all": _strings(raw.get("keywords_all")),
        "keywords_none": _strings(raw.get("keywords_none")),
        "colors_any": tuple(
            color.upper()
            for color in _strings(raw.get("colors_any", raw.get("colors")))
        ),
        "colors_all": tuple(
            color.upper() for color in _strings(raw.get("colors_all"))
        ),
        "colors_none": tuple(
            color.upper() for color in _strings(raw.get("colors_none"))
        ),
        "colorless": _optional_bool(raw.get("colorless")),
        "color_count_min": _optional_exact_int(
            raw.get("color_count_min"),
            field="Target color_count_min",
        ),
        "color_count_equal": _optional_exact_int(
            raw.get("color_count_equal"),
            field="Target color_count_equal",
        ),
    }


def _target_mana_value_fields(raw: Mapping[str, Any]) -> dict[str, float | None]:
    return {
        "mana_value_min": (
            float(raw["mana_value_min"])
            if raw.get("mana_value_min") is not None
            else None
        ),
        "mana_value_max": (
            float(raw["mana_value_max"])
            if raw.get("mana_value_max") is not None
            else None
        ),
        "mana_value_equal": (
            float(raw["mana_value"])
            if raw.get("mana_value") is not None
            else None
        ),
    }


def _target_boolean_fields(raw: Mapping[str, Any]) -> dict[str, Any]:
    return {
        field_name: _optional_bool(raw.get(field_name))
        for field_name in (
            "attacking",
            "blocking",
            "tapped",
            "commander",
            "token",
            "land",
            "creature",
            "artifact",
            "enchantment",
            "permanent",
        )
    }


def _target_combat_state(raw: Mapping[str, Any]) -> str | None:
    value = raw.get("combat_state")
    if value is None:
        return None
    if type(value) is not str or value not in {
        "attacking",
        "blocking",
        "attacking_or_blocking",
    }:
        raise ValueError("Target combat_state is unsupported")
    return value


_TARGET_GROUP_FIELDS = frozenset(
    {
        "selector",
        "count",
        "min",
        "minimum",
        "max",
        "maximum",
        "up_to",
        "zones",
        "zone",
        "categories",
        "category",
        "types_any",
        "card_types",
        "type",
        "types_all",
        "types_none",
        "subtypes_any",
        "subtypes_none",
        "subtype",
        "supertypes_any",
        "supertypes_none",
        "supertype",
        "keywords_all",
        "keywords_none",
        "colors_any",
        "colors",
        "colors_all",
        "colors_none",
        "colorless",
        "color_count_min",
        "color_count_equal",
        "mana_value_min",
        "mana_value_max",
        "mana_value",
        "controller",
        "controller_relation",
        "controller_seat",
        "owner",
        "owner_relation",
        "player_relation",
        "attacking",
        "blocking",
        "combat_state",
        "tapped",
        "commander",
        "token",
        "land",
        "creature",
        "artifact",
        "enchantment",
        "permanent",
        "source_exclusion",
        "another",
        "distinct",
        "allow_reuse",
        "different_from_groups",
        "predicate",
        "resolution_condition",
        "state_predicate",
        "characteristic_forms_any",
        "id",
        "group",
        # Validated by the owning compiler/capability shape rather than by
        # target selection itself.
        "support_source_context",
        # A modal definition may colocate its resolution effects with its
        # target group.  Effects remain owned by mode_effects().
        "effects",
        # Plan-level containers are consumed by target_plan(); retaining them
        # here keeps the single-group compatibility representation strict but
        # composable.
        "modes",
        "min_modes",
        "max_modes",
        "mode_count",
        "groups",
        "globally_distinct",
        "same_player_groups",
    }
)


@dataclass(frozen=True, slots=True)
class TargetGroup:
    """A declarative target domain and its structural selection constraints."""

    group_id: str = "target"
    zones: tuple[str, ...] = ("battlefield",)
    categories: tuple[str, ...] = ()
    types_any: tuple[str, ...] = ()
    types_all: tuple[str, ...] = ()
    types_none: tuple[str, ...] = ()
    subtypes_any: tuple[str, ...] = ()
    subtypes_none: tuple[str, ...] = ()
    supertypes_any: tuple[str, ...] = ()
    supertypes_none: tuple[str, ...] = ()
    keywords_all: tuple[str, ...] = ()
    keywords_none: tuple[str, ...] = ()
    colors_any: tuple[str, ...] = ()
    colors_all: tuple[str, ...] = ()
    colors_none: tuple[str, ...] = ()
    colorless: bool | None = None
    color_count_min: int | None = None
    color_count_equal: int | None = None
    mana_value_min: float | None = None
    mana_value_max: float | None = None
    mana_value_equal: float | None = None
    controller_relation: str = "any"
    controller_seat: str | None = None
    owner_relation: str = "any"
    player_relation: str = "any"
    attacking: bool | None = None
    blocking: bool | None = None
    combat_state: str | None = None
    tapped: bool | None = None
    commander: bool | None = None
    token: bool | None = None
    land: bool | None = None
    creature: bool | None = None
    artifact: bool | None = None
    enchantment: bool | None = None
    permanent: bool | None = None
    source_exclusion: bool = False
    another: bool = False
    min_targets: int = 1
    max_targets: int = 1
    distinct: bool = True
    allow_reuse: bool = False
    different_from_groups: tuple[str, ...] = ()
    predicate: str = ""
    resolution_condition: dict[str, Any] = field(default_factory=dict)
    state_predicate: PermanentStatePredicateSpec | None = None
    characteristic_forms_any: tuple[TargetCharacteristicForm, ...] = ()

    def __post_init__(self) -> None:
        if self.combat_state is not None and (
            self.combat_state
            not in {"attacking", "blocking", "attacking_or_blocking"}
            or self.attacking is not None
            or self.blocking is not None
        ):
            raise ValueError(
                "Target combat_state cannot mix with legacy combat flags"
            )
        if sum(
            value is not None
            for value in (self.color_count_min, self.color_count_equal)
        ) > 1:
            raise ValueError(
                "Target color-count predicates are mutually exclusive"
            )
        for field_name in ("color_count_min", "color_count_equal"):
            value = getattr(self, field_name)
            if value is not None and (
                type(value) is not int or not 0 <= value <= 5
            ):
                raise ValueError(
                    f"Target {field_name} must be an exact integer from 0 through 5"
                )

    def matches_type_characteristics(
        self,
        *,
        types: Iterable[str],
        subtypes: Iterable[str],
        supertypes: Iterable[str],
    ) -> bool:
        """Evaluate only the canonical parsed type predicates."""

        actual_types = {str(value).casefold() for value in types}
        actual_subtypes = {str(value).casefold() for value in subtypes}
        actual_supertypes = {str(value).casefold() for value in supertypes}
        types_any = {value.casefold() for value in self.types_any}
        types_all = {value.casefold() for value in self.types_all}
        types_none = {value.casefold() for value in self.types_none}
        if self.characteristic_forms_any and not any(
            form.matches(
                types=actual_types,
                subtypes=actual_subtypes,
                supertypes=actual_supertypes,
            )
            for form in self.characteristic_forms_any
        ):
            return False
        return not (
            (types_any and not actual_types.intersection(types_any))
            or (types_all and not types_all.issubset(actual_types))
            or (types_none and actual_types.intersection(types_none))
            or (
                self.subtypes_any
                and not actual_subtypes.intersection(
                    value.casefold() for value in self.subtypes_any
                )
            )
            or (
                self.subtypes_none
                and actual_subtypes.intersection(
                    value.casefold() for value in self.subtypes_none
                )
            )
            or (
                self.supertypes_any
                and not actual_supertypes.intersection(
                    value.casefold() for value in self.supertypes_any
                )
            )
            or (
                self.supertypes_none
                and actual_supertypes.intersection(
                    value.casefold() for value in self.supertypes_none
                )
            )
        )

    def matches_keyword_characteristics(
        self,
        *,
        keywords: Iterable[str],
    ) -> bool:
        """Evaluate the canonical current keyword predicate."""

        actual = {str(value).casefold() for value in keywords}
        required = {value.casefold() for value in self.keywords_all}
        excluded = {value.casefold() for value in self.keywords_none}
        return required.issubset(actual) and not actual.intersection(excluded)

    @classmethod
    def from_mapping(
        cls,
        value: Mapping[str, Any],
        *,
        default_id: str = "target",
    ) -> "TargetGroup":
        raw = dict(value)
        unknown_fields = sorted(set(raw) - _TARGET_GROUP_FIELDS)
        if unknown_fields:
            raise ValueError(
                "Target schema has unknown fields: " + ", ".join(unknown_fields)
            )
        selector = str(raw.pop("selector", "") or "")
        if selector:
            legacy = LEGACY_SELECTORS.get(selector)
            if legacy is None:
                raise ValueError(f"Unknown legacy target selector {selector!r}")
            raw = {**legacy, **raw}
        minimum, maximum = _target_count_bounds(raw)
        zones = _target_zones(raw)
        relation, owner, player_relation = _target_relations(raw)
        predicate = str(raw.get("predicate") or "")
        fields = {
            "group_id": str(raw.get("id") or raw.get("group") or default_id),
            "zones": zones,
            **_target_characteristic_fields(raw),
            **_target_mana_value_fields(raw),
            "controller_relation": relation,
            "controller_seat": (
                str(raw["controller_seat"])
                if raw.get("controller_seat") is not None
                else None
            ),
            "owner_relation": owner,
            "player_relation": player_relation,
            **_target_boolean_fields(raw),
            "combat_state": _target_combat_state(raw),
            "source_exclusion": bool(raw.get("source_exclusion", False)),
            "another": bool(raw.get("another", False)),
            "min_targets": minimum,
            "max_targets": maximum,
            "distinct": bool(raw.get("distinct", True)),
            "allow_reuse": bool(raw.get("allow_reuse", False)),
            "different_from_groups": _strings(raw.get("different_from_groups")),
            "predicate": predicate,
            "resolution_condition": _target_resolution_condition(
                raw,
                predicate=predicate,
            ),
            "state_predicate": _target_state_predicate(raw),
            "characteristic_forms_any": _characteristic_forms(
                raw.get("characteristic_forms_any", [])
            ),
        }
        return cls(**fields)

    def public_dict(self, legal_refs: Sequence[str]) -> dict[str, Any]:
        return {
            "id": self.group_id,
            "zones": list(self.zones),
            "categories": list(self.categories),
            "min": self.min_targets,
            "max": self.max_targets,
            "distinct": self.distinct,
            "allow_reuse": self.allow_reuse,
            "different_from_groups": list(self.different_from_groups),
            "legal_refs": list(legal_refs),
        }


@dataclass(frozen=True, slots=True)
class TargetPlan:
    groups: tuple[TargetGroup, ...]
    modes: tuple[str, ...] = ()
    globally_distinct: bool = False
    same_player_groups: tuple[tuple[str, str], ...] = ()


def available_modes(schema: Mapping[str, Any]) -> tuple[str, ...]:
    modes = schema.get("modes")
    if not isinstance(modes, Mapping):
        return ()
    return tuple(str(name) for name in modes)


def target_plan(
    schema: Mapping[str, Any],
    modes: Sequence[str] = (),
    *,
    require_modes: bool = True,
) -> TargetPlan:
    selected_modes = tuple(str(mode) for mode in modes)
    mode_definitions = schema.get("modes")
    raw_groups: list[Mapping[str, Any]] = []
    if isinstance(mode_definitions, Mapping):
        minimum_modes = int(schema.get("min_modes", schema.get("mode_count", 1)))
        maximum_modes = int(schema.get("max_modes", schema.get("mode_count", 1)))
        if require_modes and not (
            minimum_modes <= len(selected_modes) <= maximum_modes
        ):
            raise ValueError(
                f"Action requires between {minimum_modes} and {maximum_modes} mode(s)"
            )
        if len(set(selected_modes)) != len(selected_modes):
            raise ValueError("The same mode cannot be selected twice")
        mode_order = {str(mode): index for index, mode in enumerate(mode_definitions)}
        if any(mode not in mode_order for mode in selected_modes):
            unknown = next(mode for mode in selected_modes if mode not in mode_order)
            raise ValueError(f"Unknown target mode {unknown!r}")
        if tuple(sorted(selected_modes, key=mode_order.__getitem__)) != selected_modes:
            raise ValueError("Selected modes must remain in printed order")
        for mode in selected_modes:
            definition = mode_definitions.get(mode)
            if not isinstance(definition, Mapping):
                raise ValueError(f"Unknown target mode {mode!r}")
            groups = definition.get("groups")
            if groups is None:
                groups = [
                    {
                        key: value
                        for key, value in definition.items()
                        if key not in {"effects", "mechanics"}
                    }
                ]
            raw_groups.extend(dict(group) for group in groups)
    else:
        groups = schema.get("groups")
        if groups is None:
            groups = [schema]
        raw_groups.extend(dict(group) for group in groups)
    parsed = tuple(
        TargetGroup.from_mapping(group, default_id=f"target_{index}")
        for index, group in enumerate(raw_groups)
    )
    group_ids = [group.group_id for group in parsed]
    if len(set(group_ids)) != len(group_ids):
        raise ValueError("Target group ids must be unique")
    return TargetPlan(
        groups=parsed,
        modes=selected_modes,
        globally_distinct=bool(schema.get("globally_distinct", False)),
        same_player_groups=tuple(
            (str(value[0]), str(value[1]))
            for value in schema.get("same_player_groups", [])
            if isinstance(value, (list, tuple)) and len(value) == 2
        ),
    )


def mode_effects(
    schema: Mapping[str, Any],
    modes: Sequence[str],
    *,
    target_groups: Mapping[str, Sequence[Any]] | None = None,
) -> list[dict[str, Any]]:
    definitions = schema.get("modes")
    if not isinstance(definitions, Mapping):
        return []

    def rebase(value: Any, offset: int, count: int) -> Any:
        if isinstance(value, Mapping):
            return {
                key: rebase(child, offset, count)
                for key, child in value.items()
            }
        if isinstance(value, list):
            return [rebase(child, offset, count) for child in value]
        if isinstance(value, tuple):
            return tuple(rebase(child, offset, count) for child in value)
        if value == "$targets":
            return [f"$target.{offset + index}" for index in range(count)]
        if not isinstance(value, str):
            return value
        match = re.fullmatch(
            r"\$target(?P<attribute>\.(?:controller|owner|mana_value|colors|type_line))?"
            r"(?P<separator>[.\[])(?P<index>\d+)(?P<close>\]?)",
            value,
        )
        if match is None:
            return value
        local_index = int(match.group("index"))
        if local_index >= count:
            return value
        return (
            "$target"
            f"{match.group('attribute') or ''}"
            f"{match.group('separator')}"
            f"{offset + local_index}"
            f"{match.group('close')}"
        )

    effects: list[dict[str, Any]] = []
    target_offset = 0
    for mode in modes:
        definition = definitions.get(str(mode))
        if isinstance(definition, Mapping):
            groups = definition.get("groups")
            group_ids = (
                [str(group.get("id")) for group in groups]
                if isinstance(groups, (list, tuple))
                and all(isinstance(group, Mapping) for group in groups)
                else []
            )
            if target_groups is not None:
                target_count = sum(
                    len(target_groups.get(group_id, ()))
                    for group_id in group_ids
                )
            else:
                try:
                    target_count = sum(
                        group.max_targets
                        for group in target_plan(
                            {"groups": list(groups or [])}
                        ).groups
                    )
                except (TypeError, ValueError):
                    target_count = 0
            effects.extend(
                rebase(dict(effect), target_offset, target_count)
                for effect in definition.get("effects", [])
            )
            target_offset += target_count
    return effects
