from __future__ import annotations

"""Closed domain values for combat declaration static abilities."""

from dataclasses import dataclass
from typing import Any, Literal, Mapping

from .continuous_conditions import FixedPublicStateConditionSpec


DeclarationKind = Literal["attack", "block"]
DeclarationCostScope = Literal[
    "attached",
    "global",
    "self",
    "source_controller",
    "source_planeswalkers",
]
DeclarationSourceCondition = Literal["source_attacking", "source_untapped"]
DeclarationRestrictionScope = Literal[
    "attached",
    "attached_option",
    "global",
    "self",
    "source_opponents",
    "source_option",
]
DeclarationRestrictionMode = Literal[
    "prohibit",
    "minimum_matching_selections",
    "minimum_total_selections",
    "maximum_total_selections",
    "minimum_option_uses",
    "maximum_option_uses",
]
PowerOperand = Literal["fixed", "source"]
PowerOperator = Literal["eq", "lt", "le", "gt", "ge"]
ComparedStat = Literal["power", "toughness"]
DeclarationConditionPlayer = Literal[
    "attacking_player",
    "defending_player",
    "source_controller",
]
DeclarationTurnHistoryFact = Literal[
    "cast_spell",
    "cast_creature_spell",
    "cast_noncreature_spell",
    "creature_died_under_control",
    "opponent_dealt_damage",
    "attacked_player",
]
DeclarationRequirementKind = Literal[
    "attack_each_combat",
    "block_each_combat",
    "must_be_blocked",
    "all_able_blockers",
]

DECLARATION_COMPONENT_CAPABILITY_ID = "combat.declaration.typed_components"
DECLARATION_MANA_KEYS = ("GENERIC", "W", "U", "B", "R", "G", "C")


def _validate_declarations(values: tuple[DeclarationKind, ...]) -> None:
    if not values or any(value not in {"attack", "block"} for value in values):
        raise ValueError("Unknown declaration domain")
    if len(values) != len(set(values)):
        raise ValueError("Declaration domains must be unique")


def _mechanics(values: tuple[DeclarationKind, ...]) -> tuple[str, ...]:
    result: list[str] = []
    if "attack" in values:
        result.append("cr-508-declare-attackers-step")
    if "block" in values:
        result.append("cr-509-declare-blockers-step")
    return tuple(result)


@dataclass(frozen=True, slots=True)
class DeclarationCostTemplate:
    """One fixed-mana declaration cost compiled from a whole line."""

    template_id: str
    declarations: tuple[DeclarationKind, ...]
    scope: DeclarationCostScope
    mana: tuple[tuple[str, int], ...]
    printed_cost: str
    source_condition: DeclarationSourceCondition | None = None
    includes_planeswalkers: bool = False

    def __post_init__(self) -> None:
        if type(self.template_id) is not str or not self.template_id:
            raise ValueError("Declaration cost requires a template ID")
        _validate_declarations(self.declarations)
        if self.scope not in {
            "attached",
            "global",
            "self",
            "source_controller",
            "source_planeswalkers",
        }:
            raise ValueError("Unknown declaration cost scope")
        if not self.mana:
            raise ValueError("Declaration cost requires fixed positive mana")
        seen: set[str] = set()
        for key, amount in self.mana:
            if (
                key not in DECLARATION_MANA_KEYS
                or key in seen
                or type(amount) is not int
                or amount <= 0
            ):
                raise ValueError("Declaration cost mana is malformed")
            seen.add(key)
        if type(self.printed_cost) is not str or not self.printed_cost:
            raise ValueError("Declaration cost requires printed provenance")
        if self.source_condition not in {
            None,
            "source_attacking",
            "source_untapped",
        }:
            raise ValueError("Unknown declaration cost source condition")
        if type(self.includes_planeswalkers) is not bool:
            raise ValueError(
                "Declaration cost planeswalker inclusion must be boolean"
            )

    @property
    def mechanics(self) -> tuple[str, ...]:
        return _mechanics(self.declarations)

    def to_dict(self) -> dict[str, object]:
        return {
            "template_id": self.template_id,
            "declarations": list(self.declarations),
            "scope": self.scope,
            "mana": dict(self.mana),
            "printed_cost": self.printed_cost,
            "source_condition": self.source_condition,
            "includes_planeswalkers": self.includes_planeswalkers,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "DeclarationCostTemplate":
        expected = {
            "template_id",
            "declarations",
            "scope",
            "mana",
            "printed_cost",
            "source_condition",
            "includes_planeswalkers",
        }
        if not isinstance(value, Mapping) or set(value) != expected:
            raise ValueError("Declaration cost fragments have a closed schema")
        declarations = value["declarations"]
        mana = value["mana"]
        if not isinstance(declarations, list) or not isinstance(mana, Mapping):
            raise ValueError(
                "Declaration cost declarations and mana have typed shapes"
            )
        normalized_mana: list[tuple[str, int]] = []
        for raw_key, raw_amount in mana.items():
            key = str(raw_key).upper()
            if (
                key not in DECLARATION_MANA_KEYS
                or type(raw_amount) is not int
                or raw_amount <= 0
            ):
                raise ValueError("Declaration cost mana is malformed")
            normalized_mana.append((key, raw_amount))
        return cls(
            template_id=value["template_id"],
            declarations=tuple(declarations),
            scope=value["scope"],
            mana=tuple(
                (key, amount)
                for key in DECLARATION_MANA_KEYS
                for candidate, amount in normalized_mana
                if candidate == key
            ),
            printed_cost=value["printed_cost"],
            source_condition=value["source_condition"],
            includes_planeswalkers=value["includes_planeswalkers"],
        )


@dataclass(frozen=True, slots=True)
class DeclarationRequirementTemplate:
    """One closed source-local requirement consumed by the solver."""

    template_id: str
    declaration: DeclarationKind
    kind: DeclarationRequirementKind

    def __post_init__(self) -> None:
        expected_domain = {
            "attack_each_combat": "attack",
            "block_each_combat": "block",
            "must_be_blocked": "block",
            "all_able_blockers": "block",
        }
        if self.kind not in expected_domain:
            raise ValueError("Unknown declaration requirement kind")
        if self.declaration != expected_domain[self.kind]:
            raise ValueError("Declaration requirement kind and domain disagree")
        expected_template = {
            "attack_each_combat": "intrinsic-attack-each-combat-if-able-v1",
            "block_each_combat": "intrinsic-block-each-combat-if-able-v1",
            "must_be_blocked": "intrinsic-must-be-blocked-if-able-v1",
            "all_able_blockers": "intrinsic-all-able-blockers-v1",
        }[self.kind]
        if self.template_id != expected_template:
            raise ValueError("Declaration requirement template identity changed")

    @property
    def mechanics(self) -> tuple[str, ...]:
        return _mechanics((self.declaration,))

    def to_dict(self) -> dict[str, object]:
        return {
            "template_id": self.template_id,
            "declaration": self.declaration,
            "kind": self.kind,
        }

    @classmethod
    def from_dict(
        cls, value: Mapping[str, Any]
    ) -> "DeclarationRequirementTemplate":
        if not isinstance(value, Mapping) or set(value) != {
            "template_id",
            "declaration",
            "kind",
        }:
            raise ValueError(
                "Declaration requirement fragments have a closed schema"
            )
        return cls(
            template_id=value["template_id"],
            declaration=value["declaration"],
            kind=value["kind"],
        )


@dataclass(frozen=True, slots=True)
class StatComparison:
    stat: ComparedStat
    operator: PowerOperator
    operand: PowerOperand
    value: int | None = None

    def __post_init__(self) -> None:
        if self.stat not in {"power", "toughness"}:
            raise ValueError("Unknown declaration comparison stat")
        if self.operator not in {"eq", "lt", "le", "gt", "ge"}:
            raise ValueError("Unknown declaration comparison operator")
        if self.operand not in {"fixed", "source"}:
            raise ValueError("Unknown declaration comparison operand")
        if self.operand == "fixed" and self.value is None:
            raise ValueError("A fixed stat comparison requires a value")
        if self.operand == "source" and self.value is not None:
            raise ValueError("A source stat comparison cannot set a value")
        if self.value is not None and type(self.value) is not int:
            raise ValueError("A declaration comparison value must be an integer")

    def to_dict(self) -> dict[str, object]:
        return {
            "stat": self.stat,
            "operator": self.operator,
            "operand": self.operand,
            "value": self.value,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "StatComparison":
        if not isinstance(value, Mapping) or set(value) != {
            "stat",
            "operator",
            "operand",
            "value",
        }:
            raise ValueError("Stat comparisons have a closed schema")
        return cls(
            stat=value["stat"],
            operator=value["operator"],
            operand=value["operand"],
            value=value["value"],
        )


@dataclass(frozen=True, slots=True)
class DeclarationObjectPredicate:
    """Declarative battlefield-object filter for declaration components."""

    types_any: tuple[str, ...] = ()
    types_none: tuple[str, ...] = ()
    supertypes_any: tuple[str, ...] = ()
    supertypes_none: tuple[str, ...] = ()
    subtypes_any: tuple[str, ...] = ()
    subtypes_none: tuple[str, ...] = ()
    colors_any: tuple[str, ...] = ()
    colors_none: tuple[str, ...] = ()
    keywords_any: tuple[str, ...] = ()
    keywords_none: tuple[str, ...] = ()
    token: bool | None = None
    goaded: bool | None = None
    stat: StatComparison | None = None
    additional_stats: tuple[StatComparison, ...] = ()
    tapped: bool | None = None
    enchanted: bool | None = None
    has_any_counter: bool | None = None

    def matches_counter_state(self, counters: Mapping[str, Any]) -> bool:
        if self.has_any_counter is None:
            return True
        return (any(int(amount) > 0 for amount in counters.values())) == (
            self.has_any_counter
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "types_any": list(self.types_any),
            "types_none": list(self.types_none),
            "supertypes_any": list(self.supertypes_any),
            "supertypes_none": list(self.supertypes_none),
            "subtypes_any": list(self.subtypes_any),
            "subtypes_none": list(self.subtypes_none),
            "colors_any": list(self.colors_any),
            "colors_none": list(self.colors_none),
            "keywords_any": list(self.keywords_any),
            "keywords_none": list(self.keywords_none),
            "token": self.token,
            "goaded": self.goaded,
            "stat": self.stat.to_dict() if self.stat else None,
            "additional_stats": [item.to_dict() for item in self.additional_stats],
            "tapped": self.tapped,
            "enchanted": self.enchanted,
            "has_any_counter": self.has_any_counter,
        }

    @classmethod
    def from_dict(
        cls, value: Mapping[str, Any]
    ) -> "DeclarationObjectPredicate":
        sequence_fields = {
            "types_any",
            "types_none",
            "supertypes_any",
            "supertypes_none",
            "subtypes_any",
            "subtypes_none",
            "colors_any",
            "colors_none",
            "keywords_any",
            "keywords_none",
        }
        expected = {
            *sequence_fields,
            "token",
            "goaded",
            "stat",
            "additional_stats",
            "tapped",
            "enchanted",
            "has_any_counter",
        }
        if not isinstance(value, Mapping) or frozenset(value) not in {
            frozenset(expected),
            frozenset(expected - {"has_any_counter"}),
        }:
            raise ValueError(
                "Declaration object predicates have a closed schema"
            )
        for field in sequence_fields | {"additional_stats"}:
            if not isinstance(value[field], list):
                raise ValueError("Declaration predicate sequences must be arrays")
        for field in sequence_fields:
            if any(type(item) is not str for item in value[field]):
                raise ValueError("Declaration predicate values must be strings")
        for field in (
            "token",
            "goaded",
            "tapped",
            "enchanted",
            "has_any_counter",
        ):
            if value.get(field) is not None and type(value.get(field)) is not bool:
                raise ValueError(
                    "Declaration predicate flags must be booleans or null"
                )
        stat = value["stat"]
        if stat is not None and not isinstance(stat, Mapping):
            raise ValueError("Declaration predicate stat must be an object")
        if any(
            not isinstance(item, Mapping) for item in value["additional_stats"]
        ):
            raise ValueError("Additional declaration stats must be objects")
        return cls(
            **{field: tuple(value[field]) for field in sequence_fields},
            token=value["token"],
            goaded=value["goaded"],
            stat=StatComparison.from_dict(stat) if stat is not None else None,
            additional_stats=tuple(
                StatComparison.from_dict(item)
                for item in value["additional_stats"]
            ),
            tapped=value["tapped"],
            enchanted=value["enchanted"],
            has_any_counter=value.get("has_any_counter"),
        )


def _validate_condition_player(value: DeclarationConditionPlayer | None) -> None:
    if value not in {
        None,
        "attacking_player",
        "defending_player",
        "source_controller",
    }:
        raise ValueError("Unknown declaration condition player")


@dataclass(frozen=True, slots=True)
class DeclarationBattlefieldCondition:
    player: DeclarationConditionPlayer
    predicates_any: tuple[DeclarationObjectPredicate, ...]
    minimum: int = 1
    maximum: int | None = None
    exclude_source: bool = False
    compare_player: DeclarationConditionPlayer | None = None

    def __post_init__(self) -> None:
        _validate_condition_player(self.player)
        _validate_condition_player(self.compare_player)
        if not self.predicates_any:
            raise ValueError("A battlefield condition requires a predicate")
        if self.minimum < 0:
            raise ValueError("A battlefield condition minimum cannot be negative")
        if self.maximum is not None and self.maximum < self.minimum:
            raise ValueError("A battlefield condition maximum is below its minimum")
        if self.compare_player is not None and (
            self.minimum != 1 or self.maximum is not None
        ):
            raise ValueError("A comparative condition cannot set a count range")

    def to_dict(self) -> dict[str, object]:
        return {
            "kind": "battlefield_count",
            "player": self.player,
            "predicates_any": [item.to_dict() for item in self.predicates_any],
            "minimum": self.minimum,
            "maximum": self.maximum,
            "exclude_source": self.exclude_source,
            "compare_player": self.compare_player,
        }


@dataclass(frozen=True, slots=True)
class DeclarationCombatCondition:
    kind: Literal["attacking_alone"]

    def __post_init__(self) -> None:
        if self.kind != "attacking_alone":
            raise ValueError(f"Unknown declaration combat condition {self.kind!r}")

    def to_dict(self) -> dict[str, object]:
        return {"kind": self.kind}


@dataclass(frozen=True, slots=True)
class DeclarationSharedSubtypeCondition:
    player: DeclarationConditionPlayer
    minimum: int

    def __post_init__(self) -> None:
        _validate_condition_player(self.player)
        if self.minimum < 1:
            raise ValueError(
                "A shared-subtype condition requires a positive minimum"
            )

    def to_dict(self) -> dict[str, object]:
        return {
            "kind": "shared_creature_subtype_count",
            "player": self.player,
            "minimum": self.minimum,
        }


@dataclass(frozen=True, slots=True)
class DeclarationPlayerStateCondition:
    player: DeclarationConditionPlayer
    state: Literal["monarch", "poisoned"]

    def __post_init__(self) -> None:
        _validate_condition_player(self.player)
        if self.state not in {"monarch", "poisoned"}:
            raise ValueError("Unknown declaration player state")

    def to_dict(self) -> dict[str, object]:
        return {"kind": "player_state", "player": self.player, "state": self.state}


@dataclass(frozen=True, slots=True)
class DeclarationTurnHistoryCondition:
    fact: DeclarationTurnHistoryFact
    player: DeclarationConditionPlayer | None = None

    def __post_init__(self) -> None:
        if self.fact not in {
            "cast_spell",
            "cast_creature_spell",
            "cast_noncreature_spell",
            "creature_died_under_control",
            "opponent_dealt_damage",
            "attacked_player",
        }:
            raise ValueError("Unknown declaration turn-history fact")
        _validate_condition_player(self.player)
        if self.fact == "attacked_player" and self.player is not None:
            raise ValueError("An attacked-player condition is object-scoped")
        if self.fact != "attacked_player" and self.player is None:
            raise ValueError(
                "A player-scoped turn-history condition needs a player"
            )

    def to_dict(self) -> dict[str, object]:
        return {"kind": "turn_history", "fact": self.fact, "player": self.player}


@dataclass(frozen=True, slots=True)
class DeclarationSourceStatCondition:
    stat: ComparedStat
    operator: PowerOperator
    value: int

    def __post_init__(self) -> None:
        if self.stat not in {"power", "toughness"}:
            raise ValueError("Unknown declaration source stat")
        if self.operator not in {"eq", "lt", "le", "gt", "ge"}:
            raise ValueError("Unknown declaration source-stat operator")
        if type(self.value) is not int:
            raise ValueError("Declaration source-stat values must be integers")

    def to_dict(self) -> dict[str, object]:
        return {
            "kind": "source_stat",
            "stat": self.stat,
            "operator": self.operator,
            "value": self.value,
        }


DeclarationCondition = (
    DeclarationBattlefieldCondition
    | DeclarationCombatCondition
    | DeclarationPlayerStateCondition
    | DeclarationSharedSubtypeCondition
    | DeclarationTurnHistoryCondition
    | DeclarationSourceStatCondition
    | FixedPublicStateConditionSpec
)


def declaration_condition_from_dict(
    value: Mapping[str, Any],
) -> DeclarationCondition:
    if isinstance(value, Mapping) and "schema_version" in value:
        return FixedPublicStateConditionSpec.from_dict(value)
    if not isinstance(value, Mapping) or type(value.get("kind")) is not str:
        raise ValueError("Declaration conditions require a typed kind")
    kind = value["kind"]
    if kind == "battlefield_count":
        expected = {
            "kind",
            "player",
            "predicates_any",
            "minimum",
            "maximum",
            "exclude_source",
            "compare_player",
        }
        if set(value) != expected or not isinstance(value["predicates_any"], list):
            raise ValueError(
                "Battlefield declaration conditions have a closed schema"
            )
        if type(value["minimum"]) is not int or (
            value["maximum"] is not None and type(value["maximum"]) is not int
        ):
            raise ValueError("Battlefield declaration counts must be integers")
        if type(value["exclude_source"]) is not bool:
            raise ValueError("Battlefield source exclusion must be boolean")
        return DeclarationBattlefieldCondition(
            player=value["player"],
            predicates_any=tuple(
                DeclarationObjectPredicate.from_dict(item)
                for item in value["predicates_any"]
            ),
            minimum=value["minimum"],
            maximum=value["maximum"],
            exclude_source=value["exclude_source"],
            compare_player=value["compare_player"],
        )
    if kind == "attacking_alone" and set(value) == {"kind"}:
        return DeclarationCombatCondition(kind="attacking_alone")
    if kind == "source_stat" and set(value) == {
        "kind",
        "stat",
        "operator",
        "value",
    }:
        return DeclarationSourceStatCondition(
            stat=value["stat"],
            operator=value["operator"],
            value=value["value"],
        )
    if kind == "shared_creature_subtype_count" and set(value) == {
        "kind",
        "player",
        "minimum",
    }:
        if type(value["minimum"]) is not int:
            raise ValueError(
                "Shared-subtype declaration counts must be integers"
            )
        return DeclarationSharedSubtypeCondition(
            player=value["player"], minimum=value["minimum"]
        )
    if kind == "player_state" and set(value) == {"kind", "player", "state"}:
        return DeclarationPlayerStateCondition(
            player=value["player"], state=value["state"]
        )
    if kind == "turn_history" and set(value) == {"kind", "fact", "player"}:
        return DeclarationTurnHistoryCondition(
            fact=value["fact"], player=value["player"]
        )
    raise ValueError(f"Unknown or malformed declaration condition {kind!r}")


@dataclass(frozen=True, slots=True)
class DeclarationRestrictionTemplate:
    """One reviewed whole-line declaration restriction."""

    template_id: str
    declarations: tuple[DeclarationKind, ...]
    scope: DeclarationRestrictionScope
    mode: DeclarationRestrictionMode = "prohibit"
    count: int = 0
    subject: DeclarationObjectPredicate = DeclarationObjectPredicate()
    opposing: DeclarationObjectPredicate = DeclarationObjectPredicate()
    matching: DeclarationObjectPredicate = DeclarationObjectPredicate()
    condition: DeclarationCondition | None = None
    applies_when_condition: bool = True
    option_relation: Literal["source_controller"] | None = None
    includes_planeswalkers: bool = False

    def __post_init__(self) -> None:
        if type(self.template_id) is not str or not self.template_id:
            raise ValueError("Declaration restriction needs a template ID")
        _validate_declarations(self.declarations)
        if self.scope not in {
            "attached",
            "attached_option",
            "global",
            "self",
            "source_opponents",
            "source_option",
        }:
            raise ValueError("Unknown declaration restriction scope")
        if self.mode not in {
            "prohibit",
            "minimum_matching_selections",
            "minimum_total_selections",
            "maximum_total_selections",
            "minimum_option_uses",
            "maximum_option_uses",
        }:
            raise ValueError("Unknown declaration restriction mode")
        if type(self.count) is not int or self.count < 0:
            raise ValueError("Declaration restriction count must be nonnegative")
        if self.mode != "prohibit" and self.count < 1:
            raise ValueError(
                "Quantified declaration restrictions need a positive count"
            )
        if type(self.applies_when_condition) is not bool or type(
            self.includes_planeswalkers
        ) is not bool:
            raise ValueError("Declaration restriction flags must be booleans")
        if self.option_relation not in {None, "source_controller"}:
            raise ValueError("Unknown declaration option relation")
        if not all(
            isinstance(predicate, DeclarationObjectPredicate)
            for predicate in (self.subject, self.opposing, self.matching)
        ):
            raise ValueError("Declaration restriction predicates must be typed")
        if self.condition is not None and not isinstance(
            self.condition,
            (
                DeclarationBattlefieldCondition,
                DeclarationCombatCondition,
                DeclarationPlayerStateCondition,
                DeclarationSharedSubtypeCondition,
                DeclarationTurnHistoryCondition,
                DeclarationSourceStatCondition,
                FixedPublicStateConditionSpec,
            ),
        ):
            raise ValueError("Declaration restriction condition must be typed")

    @property
    def mechanics(self) -> tuple[str, ...]:
        return _mechanics(self.declarations)

    def to_dict(self) -> dict[str, object]:
        return {
            "template_id": self.template_id,
            "declarations": list(self.declarations),
            "scope": self.scope,
            "mode": self.mode,
            "count": self.count,
            "subject": self.subject.to_dict(),
            "opposing": self.opposing.to_dict(),
            "matching": self.matching.to_dict(),
            "condition": self.condition.to_dict() if self.condition else None,
            "applies_when_condition": self.applies_when_condition,
            "option_relation": self.option_relation,
            "includes_planeswalkers": self.includes_planeswalkers,
        }

    @classmethod
    def from_dict(
        cls, value: Mapping[str, Any]
    ) -> "DeclarationRestrictionTemplate":
        expected = {
            "template_id",
            "declarations",
            "scope",
            "mode",
            "count",
            "subject",
            "opposing",
            "matching",
            "condition",
            "applies_when_condition",
            "option_relation",
            "includes_planeswalkers",
        }
        if not isinstance(value, Mapping) or set(value) != expected:
            raise ValueError(
                "Declaration restriction fragments have a closed schema"
            )
        declarations = value["declarations"]
        if not isinstance(declarations, list):
            raise ValueError("Declaration restriction domains must be an array")
        if type(value["count"]) is not int:
            raise ValueError("Declaration restriction count must be an integer")
        if type(value["applies_when_condition"]) is not bool or type(
            value["includes_planeswalkers"]
        ) is not bool:
            raise ValueError("Declaration restriction flags must be booleans")
        for field in ("subject", "opposing", "matching"):
            if not isinstance(value[field], Mapping):
                raise ValueError(
                    "Declaration restriction predicates must be objects"
                )
        condition = value["condition"]
        if condition is not None and not isinstance(condition, Mapping):
            raise ValueError(
                "Declaration restriction condition must be an object"
            )
        return cls(
            template_id=value["template_id"],
            declarations=tuple(declarations),
            scope=value["scope"],
            mode=value["mode"],
            count=value["count"],
            subject=DeclarationObjectPredicate.from_dict(value["subject"]),
            opposing=DeclarationObjectPredicate.from_dict(value["opposing"]),
            matching=DeclarationObjectPredicate.from_dict(value["matching"]),
            condition=(
                declaration_condition_from_dict(condition)
                if condition is not None
                else None
            ),
            applies_when_condition=value["applies_when_condition"],
            option_relation=value["option_relation"],
            includes_planeswalkers=value["includes_planeswalkers"],
        )


__all__ = [
    "ComparedStat",
    "DECLARATION_COMPONENT_CAPABILITY_ID",
    "DECLARATION_MANA_KEYS",
    "DeclarationBattlefieldCondition",
    "DeclarationCombatCondition",
    "DeclarationCondition",
    "DeclarationConditionPlayer",
    "DeclarationCostScope",
    "DeclarationCostTemplate",
    "DeclarationKind",
    "DeclarationObjectPredicate",
    "DeclarationPlayerStateCondition",
    "DeclarationRequirementKind",
    "DeclarationRequirementTemplate",
    "DeclarationRestrictionMode",
    "DeclarationRestrictionScope",
    "DeclarationRestrictionTemplate",
    "DeclarationSharedSubtypeCondition",
    "DeclarationSourceStatCondition",
    "DeclarationSourceCondition",
    "DeclarationTurnHistoryCondition",
    "DeclarationTurnHistoryFact",
    "PowerOperand",
    "PowerOperator",
    "StatComparison",
    "declaration_condition_from_dict",
]
