from __future__ import annotations

"""Fixed self-replacements based on an original spell's actual cast."""

from collections.abc import Mapping
from dataclasses import dataclass

from .graveyard_cast_reanimation_copy import (
    GRAVEYARD_REANIMATION_COPY_VERSION, validate_graveyard_reanimation_copy_results,
)
from .object_predicate import ObjectQuerySpec
from .library_search_model import FixedCountedLibrarySearchTemplate
from .replacement.immutable import FrozenMap, thaw_value


GRAVEYARD_CAST_REPLACEMENT_VERSION = 23
EXILE_CAST_REPLACEMENT_VERSION = 24
GRAVEYARD_CAST_COPY_REPLACEMENT_VERSION = 25
CAST_ORIGIN_BY_VERSION = {
    GRAVEYARD_CAST_REPLACEMENT_VERSION: "graveyard",
    EXILE_CAST_REPLACEMENT_VERSION: "exile",
    GRAVEYARD_CAST_COPY_REPLACEMENT_VERSION: "graveyard",
    GRAVEYARD_REANIMATION_COPY_VERSION: "graveyard",
}
CAST_ORIGIN_REPLACEMENT_VERSIONS = frozenset(CAST_ORIGIN_BY_VERSION)
CAST_ORIGIN_REFERENCE = "$context.cast_origin"
MAIN_PHASE_CAST_REPLACEMENT_VERSION = 26
MAIN_PHASE_CAST_ADDITIVE_VERSION = 27
MAIN_PHASE_CAST_PREPARATION_VERSION = 28
MAIN_PHASE_CAST_UNTAP_VERSION = 29
MAIN_PHASE_CAST_DISCARD_VERSION = 30
MAIN_PHASE_OPTIONAL_EXILE_VERSION = 44
MAIN_PHASE_OPTIONAL_EXILE_CAPABILITY = "resolution.effect.main_phase_optional_permanent_exile"
MAIN_PHASE_OPTIONAL_EXILE_TEMPLATE = "cast-main-phase-optional-permanent-exile-v1"
EXILE_MECHANICS = ("exile", "cr-115-targets")
OPTIONAL_EXILE_MECHANICS = (*EXILE_MECHANICS, "fixed-optional-effect-choice")
PRIMARY_EXILE_REF = "$target_group.target_0.0"
BONUS_EXILE_REF = "$target_group.target_1.0"


MAIN_PHASE_CAST_VERSIONS = {
    MAIN_PHASE_CAST_REPLACEMENT_VERSION, MAIN_PHASE_CAST_ADDITIVE_VERSION, MAIN_PHASE_CAST_PREPARATION_VERSION,
    MAIN_PHASE_CAST_UNTAP_VERSION, MAIN_PHASE_CAST_DISCARD_VERSION, MAIN_PHASE_OPTIONAL_EXILE_VERSION,
}
CAST_REPLACEMENT_VERSIONS = CAST_ORIGIN_REPLACEMENT_VERSIONS | MAIN_PHASE_CAST_VERSIONS
MAIN_PHASE_CAST_CONTEXT = "main_phase_cast"
MAIN_PHASE_CAST_REFERENCE = "$context.main_phase_cast"
CREATURE_TARGET = {
    "zones": ["battlefield"], "categories": ["permanent"], "count": 1, "types_any": ["creature"],
}


@dataclass(frozen=True, slots=True)
class CastOriginCondition:
    origin: str
    schema_version: int = 1

    def to_dict(self):
        return {"kind": f"cast_from_{self.origin}", "schema_version": self.schema_version}


@dataclass(frozen=True, slots=True)
class MainPhaseCastCondition:
    def to_dict(self):
        return {"kind": "cast_during_own_main_phase", "schema_version": 1}


@dataclass(frozen=True, slots=True)
class MainPhaseCastFact:
    caster: str
    own_main_phase: bool

    def __post_init__(self):
        if type(self.caster) is not str or not self.caster or type(self.own_main_phase) is not bool:
            raise ValueError("Main-phase cast requires its original caster and recorded timing")

    @classmethod
    def from_event(cls, event):
        own_main_phase = event.controller == event.active_player and event.phase in ("precombat_main", "postcombat_main")
        return cls(event.controller, own_main_phase)

    @classmethod
    def from_dict(cls, raw):
        if (not isinstance(raw, Mapping) or set(raw) != {"schema_version", "caster", "own_main_phase"}
                or type(raw["schema_version"]) is not int or raw["schema_version"] != 1):
            raise ValueError("Main-phase condition requires the actual committed cast fact")
        return cls(raw["caster"], raw["own_main_phase"])

    def to_dict(self):
        return {"schema_version": 1, "caster": self.caster, "own_main_phase": self.own_main_phase}


def is_main_phase_cast_replacement(effect):
    return (effect.get("op") == "apply_if_public_condition"
            and effect.get("schema_version") in MAIN_PHASE_CAST_VERSIONS)


COHORT_ADDENDUM_FIELD = "creature_cohort_addendum"


def needs_own_main_phase_cast_fact(effect):
    return is_main_phase_cast_replacement(effect) or COHORT_ADDENDUM_FIELD in effect


def was_cast_during_own_main_phase(item):
    if item.kind == "spell_copy":
        return False
    if item.kind != "spell":
        raise ValueError("Main-phase condition requires a resolving spell")
    fact = MainPhaseCastFact.from_dict(item.context.get(MAIN_PHASE_CAST_CONTEXT))
    return fact.own_main_phase and fact.caster == item.controller


_EXILE_DAMAGE_GROUPS = [
    {"kind": "players", "relation": "opponents"},
    {"kind": "permanents", "controller_relation": "opponents", "target_controller": None,
     "query": ObjectQuerySpec(zones=("battlefield",), types_all=("creature",)).canonical_dict()},
]


def was_cast_from_origin(item, version):
    # A stack copy retains many decisions of the original but was never itself cast.
    expected = CAST_ORIGIN_BY_VERSION[version]
    if item.kind == "spell_copy":
        return False
    origin = item.context.get("cast_origin")
    if item.kind != "spell" or type(origin) is not str or not origin:
        raise ValueError("Cast origin condition requires the actual spell's committed origin")
    return origin == expected


def _quantity_only(normal, alternative, field):
    first, second = normal.get(field), alternative.get(field)
    return (
        type(first) is int and type(second) is int and 1 <= first < second <= 20
        and {**normal, field: second} == alternative
    )


def _validate_results(normal, alternative, *, bound):
    if normal.get("op") == "create_token":
        if _quantity_only(normal, alternative, "quantity"):
            return
    elif normal.get("op") == "place_counters":
        if (
            normal.get("counter") == "+1/+1" and _quantity_only(normal, alternative, "amount")
            and (bound or normal.get("card") == "$target.0" and normal.get("source") == "$source")
        ):
            return
    elif normal.get("op") == "search":
        expected = tuple(
            FixedCountedLibrarySearchTemplate(count, False, FrozenMap(), "hand", False).effect()
            for count in (1, 2)
        )
        if (normal, alternative) == expected:
            return
    raise ValueError("Graveyard cast replacement requires the same closed result with its alternate quantity")


def _validate_exile_damage(normal, alternative, *, bound):
    source = normal.get("source")
    fields = {"op", "source", "amount", "groups"}
    if bound:
        fields.add("source_snapshot")
        valid_source = type(source) is str and bool(source) and not source.startswith("$")
        valid_source = valid_source and isinstance(normal.get("source_snapshot"), Mapping)
    else:
        valid_source = source == "$source"
    if (
        set(normal) != fields or normal["op"] != "damage_fixed_set" or not valid_source
        or not _quantity_only(normal, alternative, "amount") or normal["groups"] != _EXILE_DAMAGE_GROUPS
    ):
        raise ValueError("Exile cast replacement preserves its single source and exact opponent damage set")


def _validate_cast_stack_copies(normal, copied, *, bound):
    target = normal.get("stack")
    valid_target = type(target) is str and bool(target) and not target.startswith("$") if bound else target == "$target.0"
    if (
        not valid_target or normal != {"op": "copy_stack_item", "stack": target}
        or any(value != normal for value in copied)
    ):
        raise ValueError("The two copies require exactly the same original stack target and retarget policy")


def validate_cast_origin_replacement(effect, *, bound=False):
    fields = {
        "op", "schema_version", "player", "condition", "cast_fact", "effects",
        "mechanic_ids", "otherwise_effects", "otherwise_mechanic_ids", "prefix_mechanic_ids",
    }
    if (
        not isinstance(effect, Mapping) or set(effect) != fields
        or effect["op"] != "apply_if_public_condition"
        or type(effect["schema_version"]) is not int
        or effect["schema_version"] not in CAST_ORIGIN_REPLACEMENT_VERSIONS
        or effect["prefix_mechanic_ids"] not in ([], ())
    ):
        raise ValueError("Cast origin replacement requires its exact version and complete branches")
    condition = CastOriginCondition(CAST_ORIGIN_BY_VERSION[effect["schema_version"]])
    if effect["condition"] != condition.to_dict() or type(effect["condition"]["schema_version"]) is not int:
        raise ValueError("Cast origin replacement requires its exact typed condition")
    actor, fact = effect["player"], effect["cast_fact"]
    if bound:
        if type(actor) is not str or not actor or actor.startswith("$") or type(fact) is not bool:
            raise ValueError("Graveyard cast replacement requires a bound controller and casting fact")
    elif actor != "$controller" or fact != CAST_ORIGIN_REFERENCE:
        raise ValueError("Graveyard cast replacement must read the resolving spell's origin")
    version = effect["schema_version"]
    two_cast_results = version in (GRAVEYARD_CAST_COPY_REPLACEMENT_VERSION, GRAVEYARD_REANIMATION_COPY_VERSION)
    for key, count in (("effects", 2 if two_cast_results else 1), ("otherwise_effects", 1)):
        children = effect[key]
        if (
            not isinstance(children, (list, tuple)) or len(children) != count
            or any(not isinstance(child, Mapping) for child in children)
        ):
            raise ValueError("Cast origin replacement requires the exact complete instructions in both branches")
    mechanics, otherwise_mechanics = effect["mechanic_ids"], effect["otherwise_mechanic_ids"]
    if (
        not isinstance(mechanics, (list, tuple)) or not mechanics
        or any(type(m) is not str or not m for m in mechanics)
        or not isinstance(otherwise_mechanics, (list, tuple))
        or tuple(mechanics) != tuple(otherwise_mechanics)
    ):
        raise ValueError("Both cast-origin results must use the same concrete owner")
    normal, alternative = (thaw_value(FrozenMap(effect[key][0])) for key in ("otherwise_effects", "effects"))
    if version == GRAVEYARD_CAST_COPY_REPLACEMENT_VERSION:
        _validate_cast_stack_copies(normal, effect["effects"], bound=bound)
    elif version == GRAVEYARD_REANIMATION_COPY_VERSION:
        validate_graveyard_reanimation_copy_results(normal, effect["effects"], actor, bound=bound)
    elif condition.origin == "exile":
        _validate_exile_damage(normal, alternative, bound=bound)
    else:
        _validate_results(normal, alternative, bound=bound)
    return condition, tuple(effect["effects"])



_TAPPED_CREATURE_TARGET = {
    **CREATURE_TARGET, "state_predicate": {
        "entered_this_turn": False, "tapped": True, "counter_name": None, "minimum_counter_count": None,
    },
}
_PLAYER_TARGET = {"zones": ["player"], "categories": ["player"], "player_relation": "any", "count": 1}
_ALL_CREATURE_PLAYER_DAMAGE_GROUPS = [
    {"kind": "permanents", "controller_relation": "any", "target_controller": None,
     "query": ObjectQuerySpec(zones=("battlefield",), types_all=("creature",)).canonical_dict()},
    {"kind": "players", "relation": "all"},
]


def _resolution_reference(value, expected, *, bound):
    return (type(value) is str and bool(value) and not value.startswith("$")) if bound else value == expected


def _main_phase_damage_target(normal, alternate, *, bound):
    fields = {"op", "source", "amount", "target" if normal["op"] == "damage" else "groups"}
    if bound:
        fields.add("source_snapshot")
    if (set(normal) != fields or not _resolution_reference(normal["source"], "$source", bound=bound)
            or not _quantity_only(normal, alternate, "amount")
            or (bound and not isinstance(normal["source_snapshot"], Mapping))):
        raise ValueError("Main-phase damage requires the same original source and fixed damage result")
    if normal["op"] == "damage" and _resolution_reference(normal["target"], "$target.0", bound=bound):
        return _TAPPED_CREATURE_TARGET
    if normal["op"] == "damage_fixed_set" and normal["groups"] == _ALL_CREATURE_PLAYER_DAMAGE_GROUPS:
        return None
    raise ValueError("Main-phase damage requires the original tapped creature or the fixed all-creature and player set")


def _main_phase_draw_discard_target(normal, alternate, *, bound):
    draw, discard = normal
    alt_draw, alt_discard = alternate
    player = draw.get("player")
    actor, default_count = discard.get("actor"), discard.get("count")
    if (draw != alt_draw or draw != {"op": "draw", "player": player, "count": 4, "private": True}
            or not _resolution_reference(player, "$target.0", bound=bound)
            or discard.get("op") != "choose_cards_apnap" or discard.get("then") != "discard"
            or discard.get("target") != player or discard.get("players") != [player]
            or discard.get("zone") != "hand" or discard.get("hidden") is not True
            or not _resolution_reference(actor, "$controller", bound=bound)
            or type(default_count) is not int or default_count > 3
            or not _quantity_only(alt_discard, discard, "count")):
        raise ValueError("Main-phase draw and discard must preserve the original affected player and private order")
    return _PLAYER_TARGET


def _main_phase_addendum_target(normal, alternate, *, player, bound):
    if len(normal) != 1 or len(alternate) != 2 or normal[0] != alternate[0]:
        raise ValueError("Addendum must keep its one original instruction before the fixed bonus")
    base, bonus = alternate
    if (
        base == {"op": "draw", "player": player, "count": 2, "private": True}
        and bonus == {"op": "life", "player": player, "delta": 2}
        and type(base["count"]) is int and type(bonus["delta"]) is int
    ):
        return None
    card = base.get("card")
    if not _resolution_reference(card, "$target.0", bound=bound):
        raise ValueError("Targeted Addendum must preserve its originally chosen creature")
    if base == {"op": "bounce", "card": card} and (
        bonus == {"op": "draw", "player": player, "count": 1, "private": True}
        and type(bonus["count"]) is int
    ):
        return CREATURE_TARGET
    if (
        base == {"op": "modify_stats_until_end_of_turn", "card": card, "power": 2, "toughness": 2}
        and type(base["power"]) is int and type(base["toughness"]) is int
        and bonus == {"op": "grant_keyword_until_end_of_turn", "card": card, "keyword": "Flying"}
    ):
        return CREATURE_TARGET
    raise ValueError("Addendum requires a closed ordered controller or same-creature result")


_MAIN_PHASE_FIGHT_TARGET = {
    "groups": [
        {**CREATURE_TARGET, "id": "fighter", "controller_relation": "you"},
        {**CREATURE_TARGET, "id": "opponent", "controller_relation": "opponent"},
    ],
    "globally_distinct": True,
}


def _main_phase_preparation_target(normal, alternate, *, player, bound):
    if len(normal) != 1 or len(alternate) != 2 or normal[0] != alternate[1]:
        raise ValueError("Preparation must keep the ordinary instruction after the exact prefix")
    preparation, base = alternate
    if (
        preparation == {"op": "scry", "player": player, "count": 3}
        and base == {"op": "draw", "player": player, "count": 3, "private": True}
        and type(preparation["count"]) is int and type(base["count"]) is int
    ):
        return None
    fighter, rival = base.get("source"), base.get("target")
    if (
        base != {"op": "creature_power_damage", "kind": "fight", "source": fighter, "target": rival,
                 "target_must_be_creature": True, "source_lki": None}
        or preparation != {"op": "modify_stats_until_end_of_turn", "card": fighter, "power": 1, "toughness": 1}
        or type(preparation["power"]) is not int or type(preparation["toughness"]) is not int
        or fighter == rival
        or not all((bound and ref is None) or _resolution_reference(ref, original, bound=bound)
                   for ref, original in ((fighter, "$target.0"), (rival, "$target.1")))
    ):
        raise ValueError("Fight preparation must preserve its two original roles and fixed first-creature bonus")
    return _MAIN_PHASE_FIGHT_TARGET


def _main_phase_untap_target(normal, alternate, *, player, bound):
    if len(normal) != 2 or len(alternate) != 4 or normal != alternate[:2]:
        raise ValueError("Untap Addendum must preserve both mandatory original instructions")
    debuff, draw = normal
    tap, restriction = alternate[2:]
    creature = debuff.get("card")
    if (
        not _resolution_reference(creature, "$target.0", bound=bound)
        or debuff != {"op": "modify_stats_until_end_of_turn", "card": creature, "power": -4, "toughness": 0}
        or type(debuff["power"]) is not int or type(debuff["toughness"]) is not int
        or draw != {"op": "draw", "player": player, "count": 1, "private": True} or type(draw["count"]) is not int
        or tap != {"op": "tap", "card": creature}
        or restriction != {"op": "prohibit_next_untap", "card": creature}
    ):
        raise ValueError("Untap Addendum needs its fixed draw, ordered tap, and one next untap for the original creature")
    return CREATURE_TARGET


def _main_phase_discard_target(normal, alternate, *, player, bound):
    if len(normal) != 1 or len(alternate) != 1:
        raise ValueError("Targeted discard scaling requires its one original result")
    base, = normal
    qualified, = alternate
    target = base.get("target")
    if (
        base.get("op") != "choose_cards_apnap" or base.get("actor") != player
        or base.get("then") != "discard" or base.get("zone") != "hand" or base.get("hidden") is not True
        or base.get("players") != [target] or not _resolution_reference(target, "$target.0", bound=bound)
        or base.get("count") != 2 or qualified.get("count") != 4
        or not _quantity_only(base, qualified, "count")
    ):
        raise ValueError("Main-phase discard must keep its original private affected player and exact quantities")
    return _PLAYER_TARGET


def validate_main_phase_cast_replacement(effect, *, bound=False):
    fields = {
        "op", "schema_version", "player", "condition", "cast_fact", "effects",
        "mechanic_ids", "otherwise_effects", "otherwise_mechanic_ids", "prefix_mechanic_ids",
    }
    if (not isinstance(effect, Mapping) or set(effect) != fields or not is_main_phase_cast_replacement(effect)
            or type(effect["schema_version"]) is not int or effect["prefix_mechanic_ids"] not in ([], ())):
        raise ValueError("Main-phase replacement requires its exact instruction and complete branches")
    condition = MainPhaseCastCondition()
    if (effect["condition"] != condition.to_dict() or type(effect["condition"]["schema_version"]) is not int
            or not _resolution_reference(effect["player"], "$controller", bound=bound)
            or (type(effect["cast_fact"]) is not bool if bound else effect["cast_fact"] != MAIN_PHASE_CAST_REFERENCE)):
        raise ValueError("Main-phase replacement requires its original controller and actual cast fact")
    version = effect["schema_version"]
    bonus_count = 2 if version == MAIN_PHASE_CAST_UNTAP_VERSION else int(
        version in (MAIN_PHASE_CAST_ADDITIVE_VERSION, MAIN_PHASE_CAST_PREPARATION_VERSION, MAIN_PHASE_OPTIONAL_EXILE_VERSION)
    )
    mechanics, base_mechanics = effect["mechanic_ids"], effect["otherwise_mechanic_ids"]
    if any(not isinstance(values, (tuple, list)) or not values
           or any(type(mechanic) is not str or not mechanic for mechanic in values)
           for values in (mechanics, base_mechanics)):
        raise ValueError("Main-phase results require their fixed result owners")
    if (bonus_count and not set(base_mechanics) <= set(mechanics)) or (not bonus_count and base_mechanics != mechanics):
        raise ValueError("Main-phase branches must preserve their original result owners")
    normal, alternate = (effect[key] for key in ("otherwise_effects", "effects"))
    if (not isinstance(normal, (tuple, list)) or not isinstance(alternate, (tuple, list))
            or len(normal) not in (1, 2) or len(alternate) != len(normal) + bonus_count
            or any(not isinstance(child, Mapping) for child in (*normal, *alternate))):
        raise ValueError("Main-phase replacement requires both complete results")
    normal, alternate = (tuple(thaw_value(FrozenMap(child)) for child in branch) for branch in (normal, alternate))
    if version == MAIN_PHASE_CAST_ADDITIVE_VERSION:
        target = _main_phase_addendum_target(normal, alternate, player=effect["player"], bound=bound)
    elif version == MAIN_PHASE_CAST_PREPARATION_VERSION:
        target = _main_phase_preparation_target(normal, alternate, player=effect["player"], bound=bound)
    elif version == MAIN_PHASE_CAST_UNTAP_VERSION:
        target = _main_phase_untap_target(normal, alternate, player=effect["player"], bound=bound)
    elif version == MAIN_PHASE_CAST_DISCARD_VERSION:
        target = _main_phase_discard_target(normal, alternate, player=effect["player"], bound=bound)
    elif version == MAIN_PHASE_OPTIONAL_EXILE_VERSION:
        target = validate_main_phase_optional_exile(normal, alternate, player=effect["player"], bound=bound)
        if bound:
            return condition, available_main_phase_optional_exile_effects(alternate), target
    elif len(normal) == 2:
        target = _main_phase_draw_discard_target(normal, alternate, bound=bound)
    else:
        base, = normal
        result, = alternate
        if base.get("op") in ("damage", "damage_fixed_set"):
            target = _main_phase_damage_target(base, result, bound=bound)
        else:
            card, power, new_power = base.get("card"), base.get("power"), result.get("power")
            if (not _resolution_reference(card, "$target.0", bound=bound)
                    or base != {"op": "modify_stats_until_end_of_turn", "card": card, "power": power, "toughness": power}
                    or result != {**base, "power": new_power, "toughness": new_power}
                    or type(power) is not int or type(new_power) is not int or not 1 <= power < new_power <= 20):
                raise ValueError("Main-phase pump requires two literal equal power/toughness bonuses to the same creature")
            target = CREATURE_TARGET
    return condition, tuple(effect["effects"]), target


def artifact_or_enchantment_exile_schema():
    return {"zones": ["battlefield"], "categories": ["permanent"], "types_any": ["artifact", "enchantment"], "count": 1}


def main_phase_optional_exile_target_schema():
    shape = artifact_or_enchantment_exile_schema()
    shape.pop("count")
    return {"groups": [
        {**shape, "id": "target_0", "min": 1, "max": 1},
        {**shape, "id": "target_1", "min": 0, "max": 1, "different_from_groups": ["target_0"]},
    ]}


def main_phase_exile_instruction(card):
    return {"op": "exile_permanent", "card": card}


def optional_exile_instruction(card, *, player="$controller"):
    return {"op": "offer_optional_effect", "player": player, "effects": [main_phase_exile_instruction(card)]}


def validate_main_phase_optional_exile(normal, alternate, *, player, bound):
    if len(normal) != 1 or len(alternate) != 2:
        raise ValueError("The main-phase exile requires exactly its original two target roles")
    primary, = normal
    first, optional = alternate
    optional_children = optional.get("effects")
    if (primary != first or not isinstance(optional_children, list) or len(optional_children) != 1
            or not isinstance(optional_children[0], Mapping) or "card" not in primary or "card" not in optional_children[0]):
        raise ValueError("The optional second exile must retain its original independent first exile")
    first_ref, second_ref = primary["card"], optional_children[0]["card"]
    if (primary != main_phase_exile_instruction(first_ref)
            or optional != optional_exile_instruction(second_ref, player=player)):
        raise ValueError("The conditional second target requires its exact optional exile")
    if not bound and (first_ref, second_ref) != (PRIMARY_EXILE_REF, BONUS_EXILE_REF):
        raise ValueError("The conditional exile must retain both originally announced target roles")
    if bound and (
        any(value is not None and (type(value) is not str or not value or value.startswith("$"))
            for value in (first_ref, second_ref))
        or first_ref is not None and first_ref == second_ref
    ):
        raise ValueError("Both resolved exile targets must retain distinct public object references")
    return main_phase_optional_exile_target_schema()


def available_main_phase_optional_exile_effects(alternate):
    if alternate[1]["effects"][0]["card"] is None:
        return tuple(alternate[:1])
    return tuple(alternate)
