from __future__ import annotations

"""Typed prevention scope and common damage-modifier codecs."""

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from .replacement.immutable import thaw_value


class DamageModifierError(ValueError):
    """A durable prevention or redirection value is malformed or stale."""


def _exact_fields(
    value: Mapping[str, Any], expected: set[str], *, label: str
) -> None:
    actual = set(value)
    if actual == expected:
        return
    missing = sorted(expected - actual)
    unknown = sorted(actual - expected)
    details: list[str] = []
    if missing:
        details.append("missing " + ", ".join(missing))
    if unknown:
        details.append("unknown " + ", ".join(unknown))
    raise DamageModifierError(f"{label} fields: {'; '.join(details)}")


def _strings(value: Any, *, label: str) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        raise DamageModifierError(f"{label} must be an array")
    result = tuple(sorted({str(item) for item in value if str(item)}))
    if len(result) != len(value):
        raise DamageModifierError(
            f"{label} must contain unique nonempty strings"
        )
    return result


@dataclass(frozen=True, slots=True)
class DamagePreventionScope:
    """Public applicability shared by static and durable prevention."""

    source_controller_relation: str = "any"
    target_controller_relation: str = "any"
    target_kinds: tuple[str, ...] = ()
    source_characteristics_all: tuple[str, ...] = ()
    source_characteristics_any: tuple[str, ...] = ()
    source_characteristics_none: tuple[str, ...] = ()
    source_colors_any: tuple[str, ...] = ()
    source_colors_none: tuple[str, ...] = ()
    target_characteristics_all: tuple[str, ...] = ()
    target_characteristics_any: tuple[str, ...] = ()
    target_characteristics_none: tuple[str, ...] = ()
    source_ref: str | None = None
    target_ref: str | None = None
    excluded_source_ref: str | None = None
    excluded_target_ref: str | None = None
    combat_pair: bool = False
    target_colors_any: tuple[str,...] = ()
    shared_color: bool = False
    source_controller: str | None = None
    source_power_max: int | None = None
    excluded_source_logical_object_id: str | None = None

    def __post_init__(self) -> None:
        if type(self.shared_color) is not bool:raise DamageModifierError('Prevention shared color must be a boolean')
        if (not isinstance(self.target_colors_any,(list,tuple)) or any(type(c) is not str or c not in 'WUBRG' for c in self.target_colors_any)):
            raise DamageModifierError('Prevention target colors must be represented colors')
        object.__setattr__(self,'target_colors_any',tuple(sorted(set(self.target_colors_any))))
        if type(self.combat_pair) is not bool:
            raise DamageModifierError('Prevention combat pair must be a boolean')
        relations = {"any", "source_controller", "opponent"}
        if self.source_controller is not None and (
            type(self.source_controller)is not str or not self.source_controller or self.source_controller_relation!='any'):
            raise DamageModifierError('A bound prevention source controller must be one unambiguous player')
        if (
            self.source_controller_relation not in relations
            or self.target_controller_relation not in relations
        ):
            raise DamageModifierError(
                "Prevention scope controller relation is unsupported"
            )
        for field in (
            "target_kinds",
            "source_characteristics_all",
            "source_characteristics_any",
            "source_characteristics_none",
            "source_colors_any",
            "source_colors_none",
            "target_characteristics_all",
            "target_characteristics_any",
            "target_characteristics_none",
        ):
            values = tuple(getattr(self, field))
            if any(type(value) is not str or not value for value in values):
                raise DamageModifierError(
                    f"Prevention scope {field} must contain strings"
                )
            normalized = tuple(sorted(set(values)))
            if values != normalized:
                raise DamageModifierError(
                    f"Prevention scope {field} must be canonical"
                )
        if set(self.target_kinds) - {"player", "permanent"}:
            raise DamageModifierError(
                "Prevention scope target kind is unsupported"
            )
        if set(self.source_colors_any).union(self.source_colors_none) - set(
            "WUBRG"
        ):
            raise DamageModifierError(
                "Prevention scope source color is unsupported"
            )
        for field in (
            "source_ref",
            "target_ref",
            "excluded_source_ref",
            "excluded_target_ref",
        ):
            value = getattr(self, field)
            if value is not None and (type(value) is not str or not value):
                raise DamageModifierError(
                    f"Prevention scope {field} must be a nonempty reference"
                )
        if self.source_ref is not None and self.excluded_source_ref is not None:
            raise DamageModifierError(
                "Prevention scope source identity predicates conflict"
            )
        if self.target_ref is not None and self.excluded_target_ref is not None:
            raise DamageModifierError(
                "Prevention scope target identity predicates conflict"
            )

        if self.source_power_max is not None:
            if type(self.source_power_max) is not int or self.source_power_max < 0:
                raise DamageModifierError("Combat source power maximum must be a nonnegative integer")
            scope = self.to_dict()
            del scope["source_power_max"]
            if scope != DamagePreventionScope(source_characteristics_all=("creature",)).to_dict():
                raise DamageModifierError("Combat source power maximum requires the exact creature source scope")

        if self.excluded_source_logical_object_id is not None:
            identity = self.excluded_source_logical_object_id
            if type(identity) is not str or not identity or identity.startswith("$"):
                raise DamageModifierError("Exempt combat source requires its original logical object identity")
            scope = self.to_dict()
            del scope["excluded_source_logical_object_id"]
            if scope != DamagePreventionScope(source_characteristics_all=("creature",)).to_dict():
                raise DamageModifierError("Original source exemption requires the exact creature source scope")

    @staticmethod
    def _relation(
        relation: str,
        controller: str,
    ) -> Mapping[str, Any] | None:
        if relation == "any":
            return None
        if relation == "source_controller":
            return {"eq": controller}
        return {"not_in": [controller, None]}

    @staticmethod
    def _collection_conditions(
        *,
        all_values: tuple[str, ...],
        any_values: tuple[str, ...],
        none_values: tuple[str, ...],
    ) -> dict[str, Any]:
        result: dict[str, Any] = {}
        if all_values:
            result["contains_all"] = list(all_values)
        if any_values:
            result["contains_any"] = list(any_values)
        if none_values:
            result["contains_none"] = list(none_values)
        return result

    def event_conditions(
        self, *, controller: str, source_powers: Sequence[tuple[str, int]] | None = None,
    ) -> dict[str, Any]:
        if type(controller) is not str or not controller:
            raise DamageModifierError(
                "Prevention scope requires an active controller"
            )
        result: dict[str, Any] = {}
        if self.source_power_max is not None:
            if source_powers is None:
                raise DamageModifierError("Combat source power prevention requires the damage-time source snapshot")
            result["source_logical_object_id"] = {
                "in": [identity for identity, power in source_powers if power <= self.source_power_max],
            }
        if self.excluded_source_logical_object_id is not None:
            result["source_logical_object_id"] = {"not_in": [None, self.excluded_source_logical_object_id]}
        if self.combat_pair:result['source_and_target_block_pair']={'eq':True}
        if self.target_colors_any:result['target_colors']={'contains_any':list(self.target_colors_any)}
        if self.shared_color:
            result['source_and_target_share_color']={'eq':True}
            result['source_and_target_distinct']={'eq':True}
        source_controller = self._relation(
            self.source_controller_relation, controller
        )
        if source_controller is not None:
            result["source_controller"] = source_controller
        if self.source_controller is not None:result['source_controller']={'eq':self.source_controller}
        target_controller = self._relation(
            self.target_controller_relation, controller
        )
        if target_controller is not None:
            result["target_controller"] = target_controller
        if self.target_kinds:
            result["target_kind"] = {"in": list(self.target_kinds)}
        source_characteristics = self._collection_conditions(
            all_values=self.source_characteristics_all,
            any_values=self.source_characteristics_any,
            none_values=self.source_characteristics_none,
        )
        if source_characteristics:
            result["source_characteristics"] = source_characteristics
        source_colors = self._collection_conditions(
            all_values=(),
            any_values=self.source_colors_any,
            none_values=self.source_colors_none,
        )
        if source_colors:
            result["source_colors"] = source_colors
        target_characteristics = self._collection_conditions(
            all_values=self.target_characteristics_all,
            any_values=self.target_characteristics_any,
            none_values=self.target_characteristics_none,
        )
        if target_characteristics:
            result["target_characteristics"] = target_characteristics
        if self.source_ref is not None:
            result["source"] = {"eq": self.source_ref}
        elif self.excluded_source_ref is not None:
            result["source"] = {"not_in": [self.excluded_source_ref]}
        if self.target_ref is not None:
            result["target"] = {"eq": self.target_ref}
        elif self.excluded_target_ref is not None:
            result["target"] = {"not_in": [self.excluded_target_ref]}
        return result

    def to_dict(self) -> dict[str, Any]:
        return {
            **({'combat_pair':True} if self.combat_pair else {}),
            **({'target_colors_any':list(self.target_colors_any)} if self.target_colors_any else {}),
            **({'shared_color':True} if self.shared_color else {}),
            **({"excluded_source_logical_object_id": self.excluded_source_logical_object_id}
               if self.excluded_source_logical_object_id is not None else {}),
            **({"source_power_max": self.source_power_max} if self.source_power_max is not None else {}),
            **({'source_controller':self.source_controller} if self.source_controller is not None else {}),
            "source_controller_relation": self.source_controller_relation,
            "target_controller_relation": self.target_controller_relation,
            "target_kinds": list(self.target_kinds),
            "source_characteristics_all": list(self.source_characteristics_all),
            "source_characteristics_any": list(self.source_characteristics_any),
            "source_characteristics_none": list(self.source_characteristics_none),
            "source_colors_any": list(self.source_colors_any),
            "source_colors_none": list(self.source_colors_none),
            "target_characteristics_all": list(self.target_characteristics_all),
            "target_characteristics_any": list(self.target_characteristics_any),
            "target_characteristics_none": list(self.target_characteristics_none),
            "source_ref": self.source_ref,
            "target_ref": self.target_ref,
            "excluded_source_ref": self.excluded_source_ref,
            "excluded_target_ref": self.excluded_target_ref,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "DamagePreventionScope":
        combat_pair=value.get('combat_pair',False)
        target_colors=value.get('target_colors_any',())
        shared_color=value.get('shared_color',False)
        source_controller=value.get('source_controller')
        if 'source_controller' in value and (type(source_controller)is not str or not source_controller):
            raise DamageModifierError('A stored prevention source controller cannot be null or empty')
        source_power_max = value.get("source_power_max")
        if "source_power_max" in value and type(source_power_max) is not int:
            raise DamageModifierError("A stored combat source power maximum must be an integer")
        excluded_identity = value.get("excluded_source_logical_object_id")
        if "excluded_source_logical_object_id" in value and type(excluded_identity) is not str:
            raise DamageModifierError("A stored exempt combat source identity must be a string")
        optional = {"combat_pair", "target_colors_any", "shared_color", "source_controller",
                    "source_power_max", "excluded_source_logical_object_id"}
        value = {key: val for key, val in value.items() if key not in optional}
        expected = set(cls().to_dict())
        _exact_fields(value, expected, label="Damage prevention scope")
        return cls(
            combat_pair=combat_pair,
            target_colors_any=target_colors,
            shared_color=shared_color,
            excluded_source_logical_object_id=excluded_identity,
            source_power_max=source_power_max,
            source_controller=source_controller,
            source_controller_relation=str(value["source_controller_relation"]),
            target_controller_relation=str(value["target_controller_relation"]),
            target_kinds=_strings(value["target_kinds"], label="scope target kinds"),
            source_characteristics_all=_strings(
                value["source_characteristics_all"],
                label="scope source all characteristics",
            ),
            source_characteristics_any=_strings(
                value["source_characteristics_any"],
                label="scope source any characteristics",
            ),
            source_characteristics_none=_strings(
                value["source_characteristics_none"],
                label="scope source excluded characteristics",
            ),
            source_colors_any=_strings(
                value["source_colors_any"], label="scope source any colors"
            ),
            source_colors_none=_strings(
                value["source_colors_none"], label="scope source excluded colors"
            ),
            target_characteristics_all=_strings(
                value["target_characteristics_all"],
                label="scope target all characteristics",
            ),
            target_characteristics_any=_strings(
                value["target_characteristics_any"],
                label="scope target any characteristics",
            ),
            target_characteristics_none=_strings(
                value["target_characteristics_none"],
                label="scope target excluded characteristics",
            ),
            source_ref=(
                str(value["source_ref"])
                if value["source_ref"] is not None
                else None
            ),
            target_ref=(
                str(value["target_ref"])
                if value["target_ref"] is not None
                else None
            ),
            excluded_source_ref=(
                str(value["excluded_source_ref"])
                if value["excluded_source_ref"] is not None
                else None
            ),
            excluded_target_ref=(
                str(value["excluded_target_ref"])
                if value["excluded_target_ref"] is not None
                else None
            ),
        )


def public_combat_prevention_replacement_side(effect, *, source_binding):
    """Identify the two exact ordinary shield instructions used in this replacement."""
    base = {
        "op": "create_damage_prevention_shield", "source": source_binding, "subject": "*",
        "mode": "all", "duration": "until_end_of_turn", "damage_kind": "combat",
    }
    value = thaw_value(effect)
    if value == base:
        return "all"
    opposed = DamagePreventionScope(
        source_controller_relation="opponent", source_characteristics_all=("creature",),
    ).to_dict()
    return "opponent_creatures" if value == {**base, "scope": opposed} else None
