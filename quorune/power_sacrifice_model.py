"""Closed creature sacrifice predicates and committed life results."""
from dataclasses import dataclass
from typing import Any, Mapping

from .replacement.immutable import thaw_value
from .object_predicate import ObjectQuerySpec
from .target_numeric import TargetNumericCharacteristic, TargetNumericComparison, TargetNumericCharacteristicSpec

POWER_SACRIFICE_CAPABILITY = 'casting.additional_cost.creature_power_minimum'


def power_sacrifice_predicate(minimum):
    if type(minimum) is not int or minimum < 0:
        raise ValueError('Creature sacrifice power requires a fixed nonnegative minimum')
    return ObjectQuerySpec(zones=('battlefield',), controller='$actor', types_all=('creature',), known_to_actor=True,
        numeric_characteristic=TargetNumericCharacteristicSpec(TargetNumericCharacteristic.POWER, TargetNumericComparison.AT_LEAST, minimum))


def validate_power_sacrifice_variant(version, operation, count, predicate, unblocked_attacker, count_parameter):
    numeric = predicate.numeric_characteristic
    if version != 4:
        return
    if (numeric is None or operation != 'sacrifice_one' or count != 1 or unblocked_attacker or count_parameter is not None
            or predicate != power_sacrifice_predicate(numeric.value)):
        raise ValueError('Zone-cost schema four requires one creature with power at least its fixed minimum')

GREATEST_POWER_SACRIFICE_FIELD = "greatest_power_sacrifice"
GREATEST_POWER_SACRIFICE_INCARNATIONS = "greatest_power_sacrifice_incarnations"
GREATEST_POWER_SACRIFICE_LIFE_RESULT = "greatest_power_sacrifice_life_result"


@dataclass(frozen=True, slots=True)
class GreatestPowerSacrificeSpec:
    lost_life_only: bool = False
    target_player: str | None = None

    def __post_init__(self) -> None:
        if type(self.lost_life_only) is not bool:
            raise ValueError("Greatest-power sacrifice requires one exact opponent scope")
        if self.target_player is not None and (
            type(self.target_player) is not str or not self.target_player or self.lost_life_only
        ):
            raise ValueError("Targeted greatest-power sacrifice requires one affected player")

    @property
    def target_schema(self) -> dict[str, Any] | None:
        if self.target_player is None:
            return None
        return {"zones": ["player"], "categories": ["player"], "player_relation": "any", "count": 1}

    def effect(self, *, actor: str = "$controller") -> dict[str, Any]:
        value = {
            "op": "choose_cards_apnap",
            "actor": actor,
            "players": "opponents",
            "zone": "battlefield",
            "predicate": ObjectQuerySpec(zones=("battlefield",), types_all=("creature",)).to_dict(),
            "count": 1,
            "then": "sacrifice",
            "prompt": "Choose one of your creatures with the greatest power to sacrifice.",
            GREATEST_POWER_SACRIFICE_FIELD: {"schema_version": 1, "lost_life_only": self.lost_life_only},
        }
        if self.target_player is not None:
            value.update(players=[self.target_player], target=self.target_player)
            value[GREATEST_POWER_SACRIFICE_FIELD] = {"schema_version": 2, "gain_life": True}
        return value

    @classmethod
    def from_effect(
        cls, effect: Mapping[str, Any], *, actor: str = "$controller",
    ) -> "GreatestPowerSacrificeSpec":
        raw = effect.get(GREATEST_POWER_SACRIFICE_FIELD)
        if not isinstance(raw, Mapping) or type(raw.get("schema_version")) is not int:
            raise ValueError("Greatest-power sacrifice requires its exact descriptor version")
        if raw["schema_version"] == 1 and set(raw) == {"schema_version", "lost_life_only"}:
            spec = cls(raw["lost_life_only"])
        elif raw["schema_version"] == 2 and set(raw) == {"schema_version", "gain_life"} and raw["gain_life"] is True:
            target = effect.get("target")
            if (type(target) is not str or not target
                    or actor == "$controller" and target != "$target.0"
                    or actor != "$controller" and (actor.startswith("$") or target.startswith("$"))):
                raise ValueError("Greatest-power sacrifice requires its announced affected player")
            spec = cls(target_player=target)
        else:
            raise ValueError("Greatest-power sacrifice requires its exact descriptor version")
        if type(effect.get("count")) is not int or thaw_value(effect) != spec.effect(actor=actor):
            raise ValueError("Greatest-power sacrifice requires one controlled creature per affected opponent")
        return spec


CONTROLLER_SACRIFICE_LIFE_FIELD = "controller_sacrifice_toughness_life"
CONTROLLER_SACRIFICE_LIFE_TEMPLATE = "fixed-counter-upkeep-sacrifice-toughness-life-v1"
ENTRY_SACRIFICE_POWER_DRAW_FIELD = "entry_sacrifice_power_life_draw"
ENTRY_SACRIFICE_POWER_DRAW_TEMPLATE = "fixed-counter-entry-sacrifice-power-life-draw-v1"
ATTACK_SACRIFICE_STAT_FIELD = "attack_sacrifice_power_draw_toughness_loss"
ATTACK_SACRIFICE_STAT_TEMPLATE = "fixed-counter-attack-sacrifice-stat-results-v1"


def _controller_sacrifice_effect(actor: str) -> dict[str, Any]:
    return {
        "op": "choose_cards_apnap",
        "actor": actor,
        "players": [actor],
        "zone": "battlefield",
        "predicate": ObjectQuerySpec(zones=("battlefield",), types_all=("creature",)).to_dict(),
        "count": 1,
        "then": "sacrifice",
        "prompt": "Choose one of your creatures to sacrifice.",
    }


def controller_sacrifice_toughness_life_effect(*, actor: str = "$controller") -> dict[str, Any]:
    return {**_controller_sacrifice_effect(actor), CONTROLLER_SACRIFICE_LIFE_FIELD: {"schema_version": 1}}


def _other_creature_sacrifice_effect(*, optional: bool, actor: str, excluded: str | None) -> dict[str, Any]:
    return {
        **_controller_sacrifice_effect(actor),
        "prompt": (
            "Choose another creature you control to sacrifice, or decline."
            if optional else "Choose another creature you control to sacrifice."
        ),
        **({"exclude_ref": excluded} if excluded is not None else {}),
    }


def entry_sacrifice_power_life_draw_effect(
    optional: bool, *, actor: str = "$controller", excluded: str | None = "$source.zone_object",
) -> dict[str, Any]:
    return {
        **_other_creature_sacrifice_effect(optional=optional, actor=actor, excluded=excluded),
        ENTRY_SACRIFICE_POWER_DRAW_FIELD: {"schema_version": 1, "optional": optional},
    }


def attack_sacrifice_stat_effect(*, actor: str = "$controller", excluded: str | None = "$source.zone_object") -> dict[str, Any]:
    return {
        **_other_creature_sacrifice_effect(optional=True, actor=actor, excluded=excluded),
        ATTACK_SACRIFICE_STAT_FIELD: {"schema_version": 1},
    }


def validate_attack_sacrifice_stat_effect(
    effect: Mapping[str, Any], *, actor: str = "$controller", excluded: str | None = "$source.zone_object",
) -> None:
    spec = effect.get(ATTACK_SACRIFICE_STAT_FIELD)
    if (not isinstance(spec, Mapping) or type(spec.get("schema_version")) is not int
            or type(effect.get("count")) is not int
            or thaw_value(effect) != attack_sacrifice_stat_effect(actor=actor, excluded=excluded)):
        raise ValueError("Attack sacrifice requires one other controlled creature and its complete draw and life loss")


def validate_entry_sacrifice_power_life_draw(
    effect: Mapping[str, Any], *, actor: str = "$controller", excluded: str | None = "$source.zone_object",
) -> bool:
    spec = effect.get(ENTRY_SACRIFICE_POWER_DRAW_FIELD)
    if (
        not isinstance(spec, Mapping) or type(spec.get("schema_version")) is not int
        or type(spec.get("optional")) is not bool or type(effect.get("count")) is not int
        or thaw_value(effect) != entry_sacrifice_power_life_draw_effect(spec["optional"], actor=actor, excluded=excluded)
    ):
        raise ValueError("Entry sacrifice requires one other controlled creature and its complete power result")
    return spec["optional"]


def validate_controller_sacrifice_toughness_life(effect: Mapping[str, Any], *, actor: str = "$controller") -> None:
    spec = effect.get(CONTROLLER_SACRIFICE_LIFE_FIELD)
    if (
        not isinstance(spec, Mapping) or type(spec.get("schema_version")) is not int
        or type(effect.get("count")) is not int
        or thaw_value(effect) != controller_sacrifice_toughness_life_effect(actor=actor)
    ):
        raise ValueError("Controller sacrifice life requires one exact controlled creature choice")


@dataclass(frozen=True, slots=True)
class GreatestPowerSacrificeLifeResult:
    """Version one is the historical affected-player power result."""
    player: str
    incarnation: str
    characteristic: str = "power"
    draw_after_life: bool = False
    draw_then_lose_life: bool = False

    def __post_init__(self) -> None:
        if any(type(value) is not str or not value or value.startswith("$")
               for value in (self.player, self.incarnation)):
            raise ValueError("Sacrifice life requires its bound player and selected incarnation")
        if (
            self.characteristic not in ("power", "toughness")
            or type(self.draw_after_life) is not bool or type(self.draw_then_lose_life) is not bool
            or self.draw_after_life and self.draw_then_lose_life
            or ((self.draw_after_life or self.draw_then_lose_life) and self.characteristic != "power")
        ):
            raise ValueError("Sacrifice life requires a represented creature characteristic and result")

    def to_dict(self) -> dict[str, Any]:
        fields = {"player": self.player, "incarnation": self.incarnation}
        if self.draw_then_lose_life:
            return {"schema_version": 4, **fields, "draw_then_lose_life": True}
        if self.draw_after_life:
            return {"schema_version": 3, **fields, "draw_after_life": True}
        if self.characteristic == "power":
            return {"schema_version": 1, **fields}
        return {"schema_version": 2, **fields, "characteristic": "toughness"}

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "GreatestPowerSacrificeLifeResult":
        fields = {"schema_version", "player", "incarnation"}
        if isinstance(raw, Mapping) and type(raw.get("schema_version")) is int:
            if raw["schema_version"] == 1 and set(raw) == fields:
                return cls(raw["player"], raw["incarnation"])
            if (raw["schema_version"] == 2 and set(raw) == fields | {"characteristic"}
                    and raw["characteristic"] == "toughness"):
                return cls(raw["player"], raw["incarnation"], "toughness")
            if raw["schema_version"] == 3 and set(raw) == fields | {"draw_after_life"} and raw["draw_after_life"] is True:
                return cls(raw["player"], raw["incarnation"], draw_after_life=True)
            if raw["schema_version"] == 4 and set(raw) == fields | {"draw_then_lose_life"} and raw["draw_then_lose_life"] is True:
                return cls(raw["player"], raw["incarnation"], draw_then_lose_life=True)
        raise ValueError("Sacrifice life requires its exact committed-result descriptor")
