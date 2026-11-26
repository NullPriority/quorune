from __future__ import annotations

"""Typed current-ability queries for finite and unbounded cleanup limits."""

from dataclasses import dataclass
from typing import Any, Mapping, Protocol

from .ability_fragments import StaticComponentSpec
from .composition_characteristics import copied_static_component_instances
from .game_hand_size_model import ResolvedNoMaximumHandSize
from .rules.linked_entry_opponent import current_linked_entry_opponent


NO_MAXIMUM_HAND_SIZE_CAPABILITY_ID = "hand.cleanup.no_maximum"
NO_MAXIMUM_HAND_SIZE_HANDLER_ID = "rule.cleanup.no-maximum-hand-size.v1"
NO_MAXIMUM_HAND_SIZE_TEMPLATE_ID = "no-maximum-hand-size-v1"
GLOBAL_NO_MAXIMUM_HAND_SIZE_TEMPLATE_ID = "global-no-maximum-hand-size-v2"
HAND_SIZE_ADJUSTMENT_CAPABILITY_ID = "hand.cleanup.fixed_adjustment"
HAND_SIZE_ADJUSTMENT_HANDLER_ID = "rule.cleanup.fixed-hand-size-adjustment.v1"
HAND_SIZE_ADJUSTMENT_TEMPLATE_ID = "fixed-hand-size-adjustment-v1"
HAND_SIZE_SETTING_HANDLER_ID = "rule.cleanup.fixed-hand-size-setting.v1"
HAND_SIZE_SETTING_TEMPLATE_ID = "fixed-maximum-hand-size-setting-v1"


class MaximumHandSizeError(ValueError):
    """A maximum-hand-size descriptor or query is malformed."""


@dataclass(frozen=True, slots=True)
class NoMaximumHandSizeSpec:
    schema_version: int = 1
    player_scope: str = "controller"

    def __post_init__(self) -> None:
        if type(self.schema_version) is not int or self.schema_version not in (1, 2):
            raise MaximumHandSizeError("Unsupported no-maximum-hand-size schema version")
        expected = "controller" if self.schema_version == 1 else "all_players"
        if type(self.player_scope) is not str or self.player_scope != expected:
            raise MaximumHandSizeError("The no-maximum-hand-size player scope must match its version")

    def to_dict(self) -> dict[str, int | str]:
        return {
            "schema_version": self.schema_version,
            **({"player_scope": self.player_scope} if self.schema_version == 2 else {}),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "NoMaximumHandSizeSpec":
        expected = {"schema_version"}
        if isinstance(value, Mapping) and value.get("schema_version") == 2:
            expected.add("player_scope")
        if not isinstance(value, Mapping) or set(value) != expected:
            raise MaximumHandSizeError("No-maximum-hand-size specs use a closed schema")
        return cls(schema_version=value["schema_version"], player_scope=value.get("player_scope", "controller"))


@dataclass(frozen=True, slots=True)
class FixedHandSizeAdjustmentSpec:
    player_scope: str
    amount: int
    schema_version: int = 1

    def __post_init__(self):
        if (type(self.schema_version) is not int or self.schema_version != 1
                or type(self.player_scope) is not str or self.player_scope not in {'controller','opponents'}
                or type(self.amount) is not int or self.amount == 0):
            raise MaximumHandSizeError('Hand-size adjustments require a fixed signed amount and owned player relation')

    def to_dict(self):
        return {'schema_version':self.schema_version,'player_scope':self.player_scope,'amount':self.amount}

    @classmethod
    def from_dict(cls,value):
        if not isinstance(value,Mapping) or set(value)!={'schema_version','player_scope','amount'}:
            raise MaximumHandSizeError('Hand-size adjustment fields are incomplete or unknown')
        return cls(value['player_scope'],value['amount'],value['schema_version'])


@dataclass(frozen=True, slots=True)
class FixedHandSizeSettingSpec:
    amount: int
    schema_version: int = 1
    player_scope: str = "controller"

    def __post_init__(self) -> None:
        expected = {1: "controller", 2: "linked_opponent"}
        if (type(self.schema_version) is not int or self.schema_version not in expected
                or type(self.player_scope) is not str or self.player_scope != expected[self.schema_version]
                or type(self.amount) is not int or self.amount < 0):
            raise MaximumHandSizeError("Hand-size settings require a fixed nonnegative maximum and owned player scope")

    def to_dict(self) -> dict[str, int | str]:
        result = {"schema_version": self.schema_version, "amount": self.amount}
        if self.schema_version == 2:
            result["player_scope"] = self.player_scope
        return result

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> FixedHandSizeSettingSpec:
        expected = {"schema_version", "amount"}
        if isinstance(value, Mapping) and value.get("schema_version") == 2:
            expected.add("player_scope")
        if not isinstance(value, Mapping) or set(value) != expected:
            raise MaximumHandSizeError("Hand-size setting fields are incomplete or unknown")
        return cls(amount=value["amount"], schema_version=value["schema_version"],
                   player_scope=value.get("player_scope", "controller"))


def fixed_hand_size_setting_descriptor(spec: FixedHandSizeSettingSpec) -> dict[str, Any]:
    return {"handler_id": HAND_SIZE_SETTING_HANDLER_ID, "schema_version": 1,
            "event": "characteristics.evaluate", "spec": spec.to_dict()}


def fixed_hand_size_adjustment_descriptor(spec):
    if not isinstance(spec,FixedHandSizeAdjustmentSpec):
        raise MaximumHandSizeError('Hand-size component requires its typed adjustment')
    return {'handler_id':HAND_SIZE_ADJUSTMENT_HANDLER_ID,'schema_version':1,
        'event':'characteristics.evaluate','spec':spec.to_dict()}


def no_maximum_hand_size_handler_descriptor(
    spec: NoMaximumHandSizeSpec = NoMaximumHandSizeSpec(),
) -> dict[str, Any]:
    if not isinstance(spec, NoMaximumHandSizeSpec):
        raise MaximumHandSizeError("No-maximum-hand-size components require their typed player scope")
    return {
        "handler_id": NO_MAXIMUM_HAND_SIZE_HANDLER_ID,
        "schema_version": 1,
        "event": "characteristics.evaluate",
        "spec": spec.to_dict(),
    }


class MaximumHandSizeHost(Protocol):
    state: Any
    semantics: Any

    def _effective_ability_fragments(self, card: Any) -> tuple[Any, ...]: ...

    def semantic_program_is_current_trusted(self, program: Any) -> bool: ...


_HAND_SIZE_SPECS = {
    NO_MAXIMUM_HAND_SIZE_HANDLER_ID: NoMaximumHandSizeSpec,
    HAND_SIZE_SETTING_HANDLER_ID: FixedHandSizeSettingSpec,
    HAND_SIZE_ADJUSTMENT_HANDLER_ID: FixedHandSizeAdjustmentSpec,
}


def _hand_size_rule_applies(host, spec, source, seat: str) -> bool:
    source_controller = source.controller
    if isinstance(spec, FixedHandSizeSettingSpec) and spec.player_scope == "linked_opponent":
        return current_linked_entry_opponent(host, source) == seat
    if isinstance(spec, NoMaximumHandSizeSpec):
        return spec.player_scope == "all_players" or source_controller == seat
    if isinstance(spec, FixedHandSizeAdjustmentSpec):
        return (source_controller == seat) == (spec.player_scope == "controller")
    return source_controller == seat


def _current_hand_size_rules(host: MaximumHandSizeHost, seat: str):
    rows = [
        (effect.timestamp, NoMaximumHandSizeSpec())
        for effect in host.state.continuous_effects or ()
        if isinstance(effect, ResolvedNoMaximumHandSize) and effect.controller == seat
    ]
    for source in host.state.cards.values():
        if source.zone != "battlefield" or source.phased_out:
            continue
        fragments = host._effective_ability_fragments(source)
        components = [fragment for fragment in fragments if isinstance(fragment, StaticComponentSpec)]
        copies = {}
        for key, timestamp in copied_static_component_instances(fragments, (fragment.semantic_key for fragment in components)):
            copies.setdefault(key, []).append(timestamp)
        for fragment in components:
            program = host.semantics.get(fragment.semantic_key)
            if program is None:
                continue
            handlers = [(spec_type, item["spec"]) for item in program.handlers
                        if (spec_type := _HAND_SIZE_SPECS.get(item.get("handler_id"))) is not None]
            if not handlers or not host.semantic_program_is_current_trusted(program):
                continue
            timestamps = copies.get(fragment.semantic_key)
            copied_at = timestamps.pop(0) if timestamps and fragment.schema_version == 1 else None
            timestamp = max(source.zone_timestamp, fragment.grant_timestamp or 0, copied_at or 0)
            for spec_type, value in handlers:
                spec = spec_type.from_dict(value)
                if _hand_size_rule_applies(host, spec, source, seat):
                    rows.append((timestamp, spec))
    return tuple(rows)


def controller_has_no_maximum_hand_size(host: MaximumHandSizeHost, seat: str, rules=None) -> bool:
    """Query current unbounded permissions that apply to the requested player."""

    current = _current_hand_size_rules(host, seat) if rules is None else rules
    return any(isinstance(spec, NoMaximumHandSizeSpec) for _, spec in current)


def current_hand_size_adjustments(host: MaximumHandSizeHost, seat: str, rules=None) -> tuple[int, ...]:
    current = _current_hand_size_rules(host, seat) if rules is None else rules
    return tuple(spec.amount for _, spec in current if isinstance(spec, FixedHandSizeAdjustmentSpec))


def effective_maximum_hand_size(host: MaximumHandSizeHost, seat: str) -> int | None:
    rules = _current_hand_size_rules(host, seat)
    has_setting = any(isinstance(spec, FixedHandSizeSettingSpec) for _, spec in rules)
    if not has_setting and controller_has_no_maximum_hand_size(host, seat, rules):
        return None
    value = host.state.players[seat].max_hand_size
    if type(value) is not int or value < 0:
        raise MaximumHandSizeError("Maximum hand size must be a nonnegative integer")
    if not has_setting:
        return max(0, value + sum(current_hand_size_adjustments(host, seat, rules)))

    for _, spec in sorted(rules, key=lambda row: row[0]):
        if isinstance(spec, NoMaximumHandSizeSpec):
            value = None
        elif isinstance(spec, FixedHandSizeSettingSpec):
            value = spec.amount
        elif value is not None:
            value += spec.amount
    return None if value is None else max(0, value)


def linked_maximum_runtime_dependencies(program) -> tuple[str, ...]:
    from .entry_designations import ENTRY_DESIGNATION_CAPABILITY, LINKED_OPPONENT_MAXIMUM_TEMPLATE

    linked = [descriptor for descriptor in program.handlers
              if descriptor.get("handler_id") == HAND_SIZE_SETTING_HANDLER_ID
              and FixedHandSizeSettingSpec.from_dict(descriptor.get("spec")).player_scope == "linked_opponent"]
    if not linked and program.provenance.get("template_id") != LINKED_OPPONENT_MAXIMUM_TEMPLATE:
        return ()
    if (program.provenance.get("template_id") != LINKED_OPPONENT_MAXIMUM_TEMPLATE
            or (program.event, program.active_zone) != ("characteristics.evaluate", "battlefield")
            or len(linked) != 1 or len(program.handlers) != 1 or linked[0]["spec"]["amount"] != 4
            or program.target_schema is not None or program.cost_schema is not None or program.effects):
        raise MaximumHandSizeError("A chosen-player maximum requires its exact linked static program")
    return (ENTRY_DESIGNATION_CAPABILITY, HAND_SIZE_ADJUSTMENT_CAPABILITY_ID)


__all__ = [
    "HAND_SIZE_ADJUSTMENT_CAPABILITY_ID",
    "HAND_SIZE_SETTING_HANDLER_ID",
    "HAND_SIZE_SETTING_TEMPLATE_ID",
    "FixedHandSizeSettingSpec",
    "fixed_hand_size_setting_descriptor",
    "HAND_SIZE_ADJUSTMENT_HANDLER_ID",
    "HAND_SIZE_ADJUSTMENT_TEMPLATE_ID",
    "FixedHandSizeAdjustmentSpec",
    "current_hand_size_adjustments",
    "fixed_hand_size_adjustment_descriptor",
    "NO_MAXIMUM_HAND_SIZE_CAPABILITY_ID",
    "NO_MAXIMUM_HAND_SIZE_HANDLER_ID",
    "NO_MAXIMUM_HAND_SIZE_TEMPLATE_ID",
    "GLOBAL_NO_MAXIMUM_HAND_SIZE_TEMPLATE_ID",
    "MaximumHandSizeError",
    "MaximumHandSizeHost",
    "NoMaximumHandSizeSpec",
    "controller_has_no_maximum_hand_size",
    "effective_maximum_hand_size",
    "no_maximum_hand_size_handler_descriptor",
]
