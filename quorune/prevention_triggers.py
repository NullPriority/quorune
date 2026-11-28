from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
from typing import Any, Mapping, Sequence, TYPE_CHECKING, TypeAlias

from .damage_source import DamageError, DamageSourceSnapshot
from .replacement.immutable import FrozenMap, thaw_value
from .util import stable_json

if TYPE_CHECKING:
    from .model import StackItem


class PreventionTriggerError(ValueError):
    """A CR 615.13 prevention-trigger value is malformed or stale."""


PREVENTION_OTHER_TARGET_FIELD = "exclude_source_incarnation"


def _exact_fields(
    value: Mapping[str, Any], expected: set[str], *, label: str
) -> None:
    if set(value) != expected:
        raise PreventionTriggerError(f"{label} fields are malformed")


def _strict_text(value: object, *, label: str) -> str:
    if type(value) is not str or not value.strip():
        raise PreventionTriggerError(f"{label} must be a nonempty string")
    return value


def _scaled_amount(
    *, per_prevented: int, fixed_amount: int, label: str
) -> None:
    if (
        type(per_prevented) is not int
        or per_prevented < 0
        or type(fixed_amount) is not int
        or fixed_amount < 0
        or not (per_prevented or fixed_amount)
    ):
        raise PreventionTriggerError(
            f"{label} requires a positive fixed or scaled amount"
        )


@dataclass(frozen=True, slots=True)
class DrawCardsPreventionTrigger:
    player: str
    per_prevented: int = 0
    fixed_amount: int = 0
    private: bool = True
    schema_version: int = 1

    def __post_init__(self) -> None:
        _strict_text(self.player, label="Prevention-trigger draw player")
        if type(self.private) is not bool:
            raise PreventionTriggerError(
                "A prevention-trigger draw requires a player and privacy flag"
            )
        if self.schema_version != 1:
            raise PreventionTriggerError(
                "Unsupported prevention-trigger draw schema version"
            )
        _scaled_amount(
            per_prevented=self.per_prevented,
            fixed_amount=self.fixed_amount,
            label="A prevention-trigger draw",
        )

    def amount(self, prevented: int) -> int:
        return self.fixed_amount + self.per_prevented * prevented

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": "draw_cards",
            "schema_version": self.schema_version,
            "player": self.player,
            "per_prevented": self.per_prevented,
            "fixed_amount": self.fixed_amount,
            "private": self.private,
        }


@dataclass(frozen=True, slots=True)
class DealDamagePreventionTrigger:
    source: DamageSourceSnapshot
    recipient_kind: str
    per_prevented: int = 0
    fixed_amount: int = 0
    schema_version: int = 1

    def __post_init__(self) -> None:
        if not isinstance(self.source, DamageSourceSnapshot):
            raise PreventionTriggerError(
                "A prevention-trigger damage result requires source LKI"
            )
        _strict_text(
            self.recipient_kind,
            label="Prevention-trigger damage recipient kind",
        )
        if self.recipient_kind not in {
            "prevented_source_controller",
            "selected_target",
        }:
            raise PreventionTriggerError(
                "A prevention-trigger damage recipient is unsupported"
            )
        if self.schema_version != 1:
            raise PreventionTriggerError(
                "Unsupported prevention-trigger damage schema version"
            )
        _scaled_amount(
            per_prevented=self.per_prevented,
            fixed_amount=self.fixed_amount,
            label="A prevention-trigger damage result",
        )

    def amount(self, prevented: int) -> int:
        return self.fixed_amount + self.per_prevented * prevented

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": "deal_damage",
            "schema_version": self.schema_version,
            "source": self.source.to_dict(),
            "recipient_kind": self.recipient_kind,
            "per_prevented": self.per_prevented,
            "fixed_amount": self.fixed_amount,
        }


@dataclass(frozen=True, slots=True)
class PlaceCountersPreventionTrigger:
    subject_ref: str
    counter_name: str
    placing_player: str
    per_prevented: int = 0
    fixed_amount: int = 0
    schema_version: int = 1

    def __post_init__(self) -> None:
        _strict_text(
            self.subject_ref,
            label="Prevention-trigger counter subject",
        )
        counter = " ".join(
            _strict_text(
                self.counter_name,
                label="Prevention-trigger counter name",
            ).casefold().split()
        )
        _strict_text(
            self.placing_player,
            label="Prevention-trigger counter placing player",
        )
        object.__setattr__(self, "counter_name", counter)
        if self.schema_version != 1:
            raise PreventionTriggerError(
                "Unsupported prevention-trigger counter schema version"
            )
        _scaled_amount(
            per_prevented=self.per_prevented,
            fixed_amount=self.fixed_amount,
            label="A prevention-trigger counter result",
        )

    def amount(self, prevented: int) -> int:
        return self.fixed_amount + self.per_prevented * prevented

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": "place_counters",
            "schema_version": self.schema_version,
            "subject_ref": self.subject_ref,
            "counter_name": self.counter_name,
            "placing_player": self.placing_player,
            "per_prevented": self.per_prevented,
            "fixed_amount": self.fixed_amount,
        }


PreventionTriggerResult: TypeAlias = (
    DrawCardsPreventionTrigger
    | DealDamagePreventionTrigger
    | PlaceCountersPreventionTrigger
)


def prevention_trigger_result_from_dict(
    value: Mapping[str, Any],
) -> PreventionTriggerResult:
    if not isinstance(value, Mapping):
        raise PreventionTriggerError(
            "A prevention-trigger result must be an object"
        )
    kind = value.get("kind")
    if kind == "draw_cards":
        _exact_fields(
            value,
            {
                "kind",
                "schema_version",
                "player",
                "per_prevented",
                "fixed_amount",
                "private",
            },
            label="Prevention-trigger draw",
        )
        return DrawCardsPreventionTrigger(
            player=_strict_text(
                value["player"], label="Prevention-trigger draw player"
            ),
            per_prevented=value["per_prevented"],
            fixed_amount=value["fixed_amount"],
            private=value["private"],
            schema_version=value["schema_version"],
        )
    if kind == "deal_damage":
        _exact_fields(
            value,
            {
                "kind",
                "schema_version",
                "source",
                "recipient_kind",
                "per_prevented",
                "fixed_amount",
            },
            label="Prevention-trigger damage",
        )
        try:
            if not isinstance(value["source"], Mapping):
                raise PreventionTriggerError(
                    "Prevention-trigger damage source is malformed"
                )
            source = DamageSourceSnapshot.from_dict(dict(value["source"]))
        except DamageError as exc:
            raise PreventionTriggerError(str(exc)) from exc
        return DealDamagePreventionTrigger(
            source=source,
            recipient_kind=_strict_text(
                value["recipient_kind"],
                label="Prevention-trigger damage recipient kind",
            ),
            per_prevented=value["per_prevented"],
            fixed_amount=value["fixed_amount"],
            schema_version=value["schema_version"],
        )
    if kind == "place_counters":
        _exact_fields(
            value,
            {
                "kind",
                "schema_version",
                "subject_ref",
                "counter_name",
                "placing_player",
                "per_prevented",
                "fixed_amount",
            },
            label="Prevention-trigger counter",
        )
        return PlaceCountersPreventionTrigger(
            subject_ref=_strict_text(
                value["subject_ref"],
                label="Prevention-trigger counter subject",
            ),
            counter_name=_strict_text(
                value["counter_name"],
                label="Prevention-trigger counter name",
            ),
            placing_player=_strict_text(
                value["placing_player"],
                label="Prevention-trigger counter placing player",
            ),
            per_prevented=value["per_prevented"],
            fixed_amount=value["fixed_amount"],
            schema_version=value["schema_version"],
        )
    raise PreventionTriggerError("Unknown prevention-trigger result kind")


@dataclass(frozen=True, slots=True)
class PreventionTriggeredAbility:
    controller: str
    source: DamageSourceSnapshot
    label: str
    results: tuple[PreventionTriggerResult, ...]
    target_schema: FrozenMap = field(default_factory=FrozenMap)
    schema_version: int = 1

    def __post_init__(self) -> None:
        _strict_text(
            self.controller,
            label="Prevention-trigger ability controller",
        )
        _strict_text(self.label, label="Prevention-trigger ability label")
        if (
            not isinstance(self.source, DamageSourceSnapshot)
        ):
            raise PreventionTriggerError(
                "A prevention-trigger ability requires controller, source LKI, and label"
            )
        values = tuple(self.results)
        if not values or any(
            not isinstance(
                value,
                (
                    DrawCardsPreventionTrigger,
                    DealDamagePreventionTrigger,
                    PlaceCountersPreventionTrigger,
                ),
            )
            for value in values
        ):
            raise PreventionTriggerError(
                "A prevention-trigger ability requires typed results"
            )
        object.__setattr__(self, "results", values)
        if not isinstance(self.target_schema, FrozenMap):
            object.__setattr__(self, "target_schema", FrozenMap(self.target_schema))
        needs_target = any(
            isinstance(value, DealDamagePreventionTrigger)
            and value.recipient_kind == "selected_target"
            for value in values
        )
        if needs_target != bool(self.target_schema):
            raise PreventionTriggerError(
                "A targeted prevention trigger requires exactly one target schema"
            )
        if self.schema_version != 1:
            raise PreventionTriggerError(
                "Unsupported prevention-trigger ability schema version"
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "controller": self.controller,
            "source": self.source.to_dict(),
            "label": self.label,
            "results": [value.to_dict() for value in self.results],
            "target_schema": thaw_value(self.target_schema),
        }

    @classmethod
    def from_dict(
        cls, value: Mapping[str, Any]
    ) -> "PreventionTriggeredAbility":
        if not isinstance(value, Mapping):
            raise PreventionTriggerError(
                "A prevention-trigger ability must be an object"
            )
        _exact_fields(
            value,
            {
                "schema_version",
                "controller",
                "source",
                "label",
                "results",
                "target_schema",
            },
            label="Prevention-trigger ability",
        )
        raw_results = value["results"]
        target_schema = value["target_schema"]
        if (
            not isinstance(raw_results, list)
            or any(not isinstance(item, Mapping) for item in raw_results)
            or not isinstance(target_schema, Mapping)
        ):
            raise PreventionTriggerError(
                "Prevention-trigger nested values are malformed"
            )
        try:
            if not isinstance(value["source"], Mapping):
                raise PreventionTriggerError(
                    "Prevention-trigger ability source is malformed"
                )
            source = DamageSourceSnapshot.from_dict(dict(value["source"]))
        except DamageError as exc:
            raise PreventionTriggerError(str(exc)) from exc
        return cls(
            controller=_strict_text(
                value["controller"],
                label="Prevention-trigger ability controller",
            ),
            source=source,
            label=_strict_text(
                value["label"], label="Prevention-trigger ability label"
            ),
            results=tuple(
                prevention_trigger_result_from_dict(item)
                for item in raw_results
            ),
            target_schema=FrozenMap(target_schema),
            schema_version=value["schema_version"],
        )


@dataclass(frozen=True, slots=True)
class PreventionTriggerOccurrence:
    ability: PreventionTriggeredAbility
    effect_id: str
    prevented_amount: int
    damage_event_ids: tuple[str, ...]
    prevented_source_controllers: tuple[str, ...]
    schema_version: int = 1

    def __post_init__(self) -> None:
        if not isinstance(self.ability, PreventionTriggeredAbility):
            raise PreventionTriggerError(
                "A prevention-trigger occurrence requires a typed ability"
            )
        _strict_text(self.effect_id, label="Prevention-trigger effect id")
        if (
            type(self.prevented_amount) is not int
            or self.prevented_amount < 1
        ):
            raise PreventionTriggerError(
                "A prevention-trigger occurrence requires positive prevented damage"
            )
        for field_name in (
            "damage_event_ids",
            "prevented_source_controllers",
        ):
            values = getattr(self, field_name)
            if not isinstance(values, (list, tuple)) or any(
                type(value) is not str or not value for value in values
            ):
                raise PreventionTriggerError(
                    f"Prevention-trigger {field_name} are malformed"
                )
            object.__setattr__(self, field_name, tuple(sorted(set(values))))
        if not self.damage_event_ids or not self.prevented_source_controllers:
            raise PreventionTriggerError(
                "A prevention-trigger occurrence lost event or source-controller identity"
            )
        if self.schema_version != 1:
            raise PreventionTriggerError(
                "Unsupported prevention-trigger occurrence schema version"
            )

    @property
    def occurrence_id(self) -> str:
        material = stable_json(self.to_dict()).encode("utf-8")
        return "PT-" + hashlib.sha256(material).hexdigest()[:20]

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "ability": self.ability.to_dict(),
            "effect_id": self.effect_id,
            "prevented_amount": self.prevented_amount,
            "damage_event_ids": list(self.damage_event_ids),
            "prevented_source_controllers": list(
                self.prevented_source_controllers
            ),
        }

    def runtime_effects(self) -> tuple[dict[str, Any], ...]:
        if len(self.prevented_source_controllers) != 1 and any(
            isinstance(result, DealDamagePreventionTrigger)
            and result.recipient_kind == "prevented_source_controller"
            for result in self.ability.results
        ):
            raise PreventionTriggerError(
                "A source-controller prevention trigger requires one prevented source controller"
            )
        prevented_controller = self.prevented_source_controllers[0]
        effects: list[dict[str, Any]] = []
        for index, result in enumerate(self.ability.results):
            amount = result.amount(self.prevented_amount)
            if not amount:
                continue
            if isinstance(result, DrawCardsPreventionTrigger):
                effects.append(
                    {
                        "op": "draw",
                        "player": result.player,
                        "count": amount,
                        "private": result.private,
                    }
                )
            elif isinstance(result, PlaceCountersPreventionTrigger):
                effects.append(
                    {
                        "op": "counter",
                        "card": result.subject_ref,
                        "counter": result.counter_name,
                        "delta": amount,
                        "source": self.ability.source.ref,
                    }
                )
            else:
                target = (
                    prevented_controller
                    if result.recipient_kind
                    == "prevented_source_controller"
                    else "$target.0"
                )
                effects.append(
                    {
                        "op": "damage",
                        "source": result.source.ref,
                        "source_snapshot": result.source.to_dict(),
                        "target": target,
                        "amount": amount,
                        "damage_event_id": (
                            f"damage.prevention-trigger:{self.occurrence_id}:{index}"
                        ),
                    }
                )
        return tuple(effects)


def prevention_trigger_stack_item(
    occurrence: PreventionTriggerOccurrence,
    *,
    ref: str,
    stack_id: str,
    visibility: Sequence[str],
    target_source_context: Mapping[str, Any] | None = None,
) -> "StackItem":
    """Materialize one CR 615.13 occurrence as an ordinary stack object."""

    from .model import StackItem

    if not ref or not stack_id:
        raise PreventionTriggerError(
            "A prevention-trigger stack object requires stable identity"
        )
    target_schema = thaw_value(occurrence.ability.target_schema)
    if isinstance(target_schema, Mapping) and PREVENTION_OTHER_TARGET_FIELD in target_schema and not target_source_context:
        raise PreventionTriggerError("A prevention trigger with an excluded source requires that source binding")
    context: dict[str, Any] = {
        "event": "damage.prevented",
        "effect_id": occurrence.effect_id,
        "prevented_amount": occurrence.prevented_amount,
        "damage_event_ids": list(occurrence.damage_event_ids),
        "prevention_trigger_occurrence": occurrence.to_dict(),
        "dynamic_effects": [
            dict(effect) for effect in occurrence.runtime_effects()
        ],
        **dict(target_source_context or {}),
    }
    if target_schema:
        context.update(
            {
                "target_schema_override": target_schema,
                "trigger_target_selection_pending": True,
            }
        )
    return StackItem(
        stack_id=stack_id,
        ref=ref,
        kind="triggered_ability",
        controller=occurrence.ability.controller,
        label=occurrence.ability.label,
        source_object_id=occurrence.ability.source.object_id,
        visibility=list(visibility),
        context=context,
        referred_object_ids=[occurrence.ability.source.object_id],
    )


__all__ = [
    "DealDamagePreventionTrigger",
    "DrawCardsPreventionTrigger",
    "PlaceCountersPreventionTrigger",
    "PreventionTriggeredAbility",
    "PreventionTriggerError",
    "PreventionTriggerOccurrence",
    "PreventionTriggerResult",
    "prevention_trigger_result_from_dict",
    "prevention_trigger_stack_item",
]
