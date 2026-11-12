from __future__ import annotations

"""Closed battlefield selection shape over existing fixed inspection policy."""

from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass

from .mana_value_limit import ManaValueLimitSpec

LIBRARY_BATTLEFIELD_CAPABILITY='library.select.battlefield_controller'
LIBRARY_REVEALED_ENTRY_FOLLOWUP_CAPABILITY = "library.select.revealed_entry_followup"
LIBRARY_REVEALED_ENTRY_FOLLOWUP_VERSION = 4
LIBRARY_SHUFFLE_REMAINDER = "library_shuffle"
_ENTRY_FIELDS={'schema_version','selected_destination','entry_tapped','mana_value_limit','entry_followup'}


@dataclass(frozen=True, slots=True)
class RevealedCreatureEntryFollowup:
    counters: int

    def __post_init__(self) -> None:
        if type(self.counters) is not int or not 1 <= self.counters <= 10:
            raise ValueError("A revealed creature entry requires a bounded positive counter count")

    @classmethod
    def from_dict(cls, value: Mapping) -> RevealedCreatureEntryFollowup:
        if not isinstance(value, Mapping) or set(value) != {"counters"}:
            raise ValueError("A revealed creature entry followup has a closed shape")
        return cls(value["counters"])

    def to_dict(self) -> dict[str, int]:
        return {"counters": self.counters}


def fixed_selection_without_entry_fields(effect):
    proxy = {key:deepcopy(value) for key,value in effect.items() if key not in _ENTRY_FIELDS}
    if effect.get("schema_version") == LIBRARY_REVEALED_ENTRY_FOLLOWUP_VERSION:
        proxy["remainder_destination"] = "library_bottom"
    return proxy


def _validate_revealed_entry_followup(effect):
    from .object_predicate import ObjectQuerySpec

    RevealedCreatureEntryFollowup.from_dict(effect.get("entry_followup"))
    groups = effect["predicate_groups"]
    creature = ObjectQuerySpec(zones=("library",), types_any=("creature",))
    if (effect.get("look_count") != 10 or effect.get("public_reveal") is not True
            or effect.get("selected_reveal") is not False or effect.get("selection_policy") != "up_to_matching"
            or effect.get("minimum") != 1 or effect.get("maximum") != 1 or effect.get("remainder_order") != "random"
            or effect["entry_tapped"] or effect["mana_value_limit"] is not None or len(groups) != 1
            or len(groups[0]) != 1 or ObjectQuerySpec.from_dict(groups[0][0]) != creature):
        raise ValueError("The revealed entry followup requires one creature from ten public cards and a shuffle")


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
    # The initial entry family owns explicit creature, land and planeswalker
    # selections. Aura-capable/ambiguous sets need an attachment-choice owner.
    groups=effect['predicate_groups']
    if not groups or any(not _entry_query_is_owned(predicate) for group in groups for predicate in group):
        raise ValueError('Battlefield selection predicate requires an unrepresented entry quality')
    if version == LIBRARY_REVEALED_ENTRY_FOLLOWUP_VERSION:
        _validate_revealed_entry_followup(effect)


def _entry_query_is_owned(value):
    from .object_predicate import ObjectQuerySpec
    query=ObjectQuerySpec.from_dict(value)
    kinds={'creature','land','planeswalker'}
    return bool(set(query.types_all)&kinds or query.types_any and set(query.types_any)<=kinds)


def library_mana_value_permits(effect,row):
    limit=effect.get('mana_value_limit')
    return limit is None or ManaValueLimitSpec.from_dict(limit).permits(row.mana_value)


def library_battlefield_payload_dependencies(value):
    if not isinstance(value,Mapping) or value.get('op')!='fixed_library_selection' or value.get('schema_version') not in (3,LIBRARY_REVEALED_ENTRY_FOLLOWUP_VERSION):return ()
    validate_library_battlefield_effect(value)
    if value["schema_version"] == LIBRARY_REVEALED_ENTRY_FOLLOWUP_VERSION:
        return (LIBRARY_BATTLEFIELD_CAPABILITY, "library.select.fixed_controller", LIBRARY_REVEALED_ENTRY_FOLLOWUP_CAPABILITY,
                "counter.producer.effect_entry", "continuous.resolution.until_controller_next_turn",
                "target.protection.hexproof_permanent", "library.shuffle.fixed_controller")
    return (LIBRARY_BATTLEFIELD_CAPABILITY,'library.select.fixed_controller',
        *(('zone.entry.tapped_state',) if value['entry_tapped'] else ()))
