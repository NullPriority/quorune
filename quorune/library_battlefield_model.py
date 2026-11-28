from __future__ import annotations

"""Closed battlefield selection shape over existing fixed inspection policy."""

from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass

from .mana_value_limit import ManaValueLimitSpec
from .replacement.immutable import thaw_value

LIBRARY_BATTLEFIELD_CAPABILITY='library.select.battlefield_controller'
LIBRARY_REVEALED_ENTRY_FOLLOWUP_CAPABILITY = "library.select.revealed_entry_followup"
LIBRARY_REVEALED_ENTRY_FOLLOWUP_VERSION = 4
RANDOM_REVEALED_CREATURE_ENTRY_VERSION = 28
PRIVATE_TOP_LAND_ENTRY_VERSION = 7
PRIVATE_TOP_LAND_OPTIONAL_BOTTOM_VERSION = 8
PRIVATE_TOP_LAND_OR_HAND_VERSION = 9
PRIVATE_TOP_LAND_OR_GOBLIN_VERSION = 10
PRIVATE_TOP_LAND_VERSIONS = (
    PRIVATE_TOP_LAND_ENTRY_VERSION, PRIVATE_TOP_LAND_OPTIONAL_BOTTOM_VERSION,
    PRIVATE_TOP_LAND_OR_HAND_VERSION, PRIVATE_TOP_LAND_OR_GOBLIN_VERSION,
)
LIBRARY_SHUFFLE_REMAINDER = "library_shuffle"
_ENTRY_FIELDS={'schema_version','selected_destination','entry_tapped','mana_value_limit','entry_followup'}


@dataclass(frozen=True, slots=True)
class RevealedCreatureEntryFollowup:
    counters: int | None
    shuffle_only: bool = False

    def __post_init__(self) -> None:
        if type(self.shuffle_only) is not bool:
            raise ValueError("A revealed entry followup needs a Boolean shuffle-only mode")
        if self.shuffle_only:
            if self.counters is not None:
                raise ValueError("A shuffle-only followup cannot add counters")
        elif type(self.counters) is not int or not 1 <= self.counters <= 10:
            raise ValueError("A revealed creature entry requires a bounded positive counter count")

    @classmethod
    def from_dict(cls, value: Mapping) -> RevealedCreatureEntryFollowup:
        if (isinstance(value, Mapping) and set(value) == {"schema_version", "kind"}
                and type(value["schema_version"]) is int and value["schema_version"] == 2
                and value["kind"] == "shuffle_only"):
            return cls(None, shuffle_only=True)
        if not isinstance(value, Mapping) or set(value) != {"counters"}:
            raise ValueError("A revealed creature entry followup has a closed shape")
        return cls(value["counters"])

    def to_dict(self) -> dict[str, int | str]:
        return {"counters": self.counters} if self.counters is not None else {"schema_version": 2, "kind": "shuffle_only"}


def fixed_selection_without_entry_fields(effect):
    proxy = {key:deepcopy(value) for key,value in effect.items() if key not in _ENTRY_FIELDS}
    if effect.get("schema_version") == LIBRARY_REVEALED_ENTRY_FOLLOWUP_VERSION:
        proxy["remainder_destination"] = "library_bottom"
    return proxy


def _validate_revealed_entry_followup(effect):
    from .object_predicate import ObjectQuerySpec

    followup = RevealedCreatureEntryFollowup.from_dict(effect.get("entry_followup"))
    groups = effect["predicate_groups"]
    creature = ObjectQuerySpec(zones=("library",), types_any=("creature",))
    if (followup.counters is None or effect.get("look_count") != 10 or effect.get("public_reveal") is not True
            or effect.get("selected_reveal") is not False or effect.get("selection_policy") != "up_to_matching"
            or effect.get("minimum") != 1 or effect.get("maximum") != 1 or effect.get("remainder_order") != "random"
            or effect["entry_tapped"] or effect["mana_value_limit"] is not None or len(groups) != 1
            or len(groups[0]) != 1 or ObjectQuerySpec.from_dict(groups[0][0]) != creature):
        raise ValueError("The revealed entry followup requires one creature from ten public cards and a shuffle")


def random_revealed_creature_entry_effect(look_count, *, player="$controller"):
    from .object_predicate import ObjectQuerySpec

    if type(look_count) is not int or look_count <= 0:
        raise ValueError("Random creature entry requires a positive fixed reveal count")
    creature = ObjectQuerySpec(zones=("library",), types_any=("creature",))
    return {
        "op": "fixed_library_selection", "schema_version": RANDOM_REVEALED_CREATURE_ENTRY_VERSION,
        "player": player, "look_count": look_count, "public_reveal": True, "selected_reveal": False,
        "selection_policy": "random_matching", "minimum": 1, "maximum": 1,
        "predicate_groups": [[creature.to_dict()]], "selected_destination": "battlefield",
        "entry_tapped": False, "mana_value_limit": None, "remainder_destination": "library_bottom",
        "remainder_order": "random",
    }


def validate_random_revealed_creature_entry(effect, *, player="$controller"):
    value = thaw_value(effect)
    if (not isinstance(value, Mapping)
            or any(type(value.get(field)) is not int for field in ("schema_version", "minimum", "maximum"))
            or value.get("public_reveal") is not True or value.get("selected_reveal") is not False
            or value.get("entry_tapped") is not False
            or value != random_revealed_creature_entry_effect(value.get("look_count"), player=player)):
        raise ValueError("Random creature entry requires a public reveal and a random bottom remainder")
    return value["look_count"]


def validate_library_battlefield_effect(effect):
    version = effect.get("schema_version") if isinstance(effect, Mapping) else None
    if (not isinstance(effect,Mapping) or type(effect.get('schema_version')) is not int
            or version not in (3, LIBRARY_REVEALED_ENTRY_FOLLOWUP_VERSION) or effect.get('selected_destination')!='battlefield'
            or type(effect.get('entry_tapped')) is not bool or 'mana_value_limit' not in effect
            or effect.get('remainder_destination') != (LIBRARY_SHUFFLE_REMAINDER if version == LIBRARY_REVEALED_ENTRY_FOLLOWUP_VERSION else 'library_bottom')
            or (version == LIBRARY_REVEALED_ENTRY_FOLLOWUP_VERSION) != ("entry_followup" in effect)):
        raise ValueError('Battlefield selection requires its closed entry and remainder shape')
    if effect['mana_value_limit'] is not None:ManaValueLimitSpec.from_dict(effect['mana_value_limit'])
    proxy=fixed_selection_without_entry_fields(effect);proxy['player']='$controller'
    from .rules.library_selection_capability_shapes import fixed_library_selection_node_capabilities
    if not fixed_library_selection_node_capabilities(effects=(proxy,),target_schema=None,
            mechanic_ids={'fixed-library-selection'}):
        raise ValueError('Battlefield selection lacks an owned fixed partition')
    # Broad enchantment selection still requires an attachment-choice owner.
    # The exact Saga selector uses the represented non-Aura Saga entry path.
    groups=effect['predicate_groups']
    if not groups or any(not _entry_query_is_owned(predicate) for group in groups for predicate in group):
        raise ValueError('Battlefield selection predicate requires an unrepresented entry quality')
    if version == LIBRARY_REVEALED_ENTRY_FOLLOWUP_VERSION:
        _validate_revealed_entry_followup(effect)


def _entry_query_is_owned(value):
    from .object_predicate import ObjectQuerySpec
    query=ObjectQuerySpec.from_dict(value)
    kinds={'creature','land','planeswalker'}
    return (query == ObjectQuerySpec(zones=("library",), subtypes_any=("saga",))
            or bool(set(query.types_all)&kinds or query.types_any and set(query.types_any)<=kinds))


def library_mana_value_permits(effect,row):
    limit=effect.get('mana_value_limit')
    return limit is None or ManaValueLimitSpec.from_dict(limit).permits(row.mana_value)


def private_top_land_entry_effect(*, player="$controller", may_put_on_bottom=False):
    from .object_predicate import ObjectQuerySpec

    return {
        "op": "fixed_library_selection",
        "schema_version": PRIVATE_TOP_LAND_OPTIONAL_BOTTOM_VERSION if may_put_on_bottom else PRIVATE_TOP_LAND_ENTRY_VERSION,
        "player": player, "look_count": 1, "public_reveal": False, "selected_reveal": False,
        "selection_policy": "up_to_matching", "minimum": 0, "maximum": 1,
        "predicate_groups": [[ObjectQuerySpec(zones=("library",), types_all=("land",)).to_dict()]],
        "selected_destination": "battlefield", "entry_tapped": True,
        "remainder_destination": "optional_bottom" if may_put_on_bottom else "keep_top",
        "remainder_order": "chosen",
    }


def private_top_land_or_hand_effect(*, player="$controller"):
    return {
        **private_top_land_entry_effect(player=player),
        "schema_version": PRIVATE_TOP_LAND_OR_HAND_VERSION, "remainder_destination": "hand",
    }


def private_top_land_or_goblin_effect(*, player="$controller"):
    return {
        **private_top_land_entry_effect(player=player),
        "schema_version": PRIVATE_TOP_LAND_OR_GOBLIN_VERSION,
        "otherwise_token": {
            "op": "create_token", "controller": player, "name": "Goblin Token", "quantity": 1,
            "characteristics": {
                "type_line": "Token Creature — Goblin", "literal_token_name_version": 1,
                "colors": ["R"], "power": "1", "toughness": "1",
            },
        },
    }


def validate_private_top_land_entry(effect, *, player="$controller"):
    from .object_predicate import ObjectQuerySpec
    from .replacement.immutable import thaw_value

    actual = thaw_value(effect)
    if not isinstance(actual, Mapping):
        raise ValueError("Private top land selection requires a typed effect")
    version = actual.get("schema_version")
    if version == PRIVATE_TOP_LAND_OR_HAND_VERSION:
        expected = private_top_land_or_hand_effect(player=player)
    elif version == PRIVATE_TOP_LAND_OR_GOBLIN_VERSION:
        expected = private_top_land_or_goblin_effect(player=player)
    else:
        expected = private_top_land_entry_effect(
            player=player, may_put_on_bottom=version == PRIVATE_TOP_LAND_OPTIONAL_BOTTOM_VERSION,
        )
    if (
        actual != expected
        or any(type(actual.get(key)) is not int for key in ("schema_version", "look_count", "minimum", "maximum"))
        or actual.get("public_reveal") is not False or actual.get("selected_reveal") is not False
        or actual.get("entry_tapped") is not True
        or version == PRIVATE_TOP_LAND_OR_GOBLIN_VERSION
        and type(actual["otherwise_token"]["quantity"]) is not int
    ):
        raise ValueError("Private top land selection requires its original look, land predicate and tapped entry")
    return ObjectQuerySpec.from_dict(actual["predicate_groups"][0][0])


def library_battlefield_payload_dependencies(value):
    if not isinstance(value, Mapping) or value.get("op") != "fixed_library_selection":
        return ()
    if value.get("schema_version") in PRIVATE_TOP_LAND_VERSIONS:
        validate_private_top_land_entry(value)
        return (LIBRARY_BATTLEFIELD_CAPABILITY, "library.select.fixed_controller", "zone.entry.tapped_state",
                *(("token.creation.fixed_definition",)
                  if value["schema_version"] == PRIVATE_TOP_LAND_OR_GOBLIN_VERSION else ()))
    if value.get("schema_version") == RANDOM_REVEALED_CREATURE_ENTRY_VERSION:
        validate_random_revealed_creature_entry(value)
        return (LIBRARY_BATTLEFIELD_CAPABILITY, "library.select.fixed_controller")
    if value.get('schema_version') not in (3, LIBRARY_REVEALED_ENTRY_FOLLOWUP_VERSION):
        return ()
    validate_library_battlefield_effect(value)
    if value["schema_version"] == LIBRARY_REVEALED_ENTRY_FOLLOWUP_VERSION:
        return (LIBRARY_BATTLEFIELD_CAPABILITY, "library.select.fixed_controller", LIBRARY_REVEALED_ENTRY_FOLLOWUP_CAPABILITY,
                "counter.producer.effect_entry", "continuous.resolution.until_controller_next_turn",
                "target.protection.hexproof_permanent", "library.shuffle.fixed_controller")
    return (LIBRARY_BATTLEFIELD_CAPABILITY,'library.select.fixed_controller',
        *(('zone.entry.tapped_state',) if value['entry_tapped'] else ()))
