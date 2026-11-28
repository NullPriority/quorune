from __future__ import annotations

"""Retire the complete reviewed chapter and activation view together."""

from collections.abc import Mapping
from dataclasses import replace

from ..abilities import ActivatedAbility
from ..ability_fragments import ability_fragment_to_dict
from ..characteristic_fragments import (
    CharacteristicQuantityScope, CharacteristicQuantitySpec,
    PowerToughnessCalculation, QueryCharacteristicModifierSpec,
)
from ..compiler.activated_costs import activated_ability_cost
from ..fixed_mana_abilities import (
    FIXED_MANA_HANDLER_ID, MANA_COST_KEYS, FixedActivatedManaAbilitySpec, FixedManaMode,
)
from ..fixed_token_production import historical_unnamed_token_view
from ..granted_activation_model import GrantedActivatedAbilitySpec
from ..library_search_model import (
    PRINTED_MANA_COST_ARTIFACT_SEARCH_CAPABILITY_ID,
    PRINTED_MANA_COST_ARTIFACT_SEARCH_PREDICATE,
)
from ..object_predicate import ObjectQuerySpec
from ..replacement.immutable import FrozenMap, thaw_value
from ..rules.saga_chapter_grants import SAGA_CHAPTER_GRANT_CAPABILITY, validate_saga_chapter_grant
from ..semantic_runtime.activated_abilities import ACTIVATED_ABILITY_CATALOG_HANDLER_ID
from .current_view_equivalence import reviewed_rulings_match


_CHAPTER_EVENTS = ("saga.chapter.1", "saga.chapter.2", "saga.chapter.3")
_TOKEN_TEXT = "This token gets +1/+1 for each artifact you control."


def _source_pair(programs):
    sources = [
        (program.provenance.get("source_oracle_hash"),
         program.provenance.get("source_rulings_hash"))
        for program in programs
    ]
    if any(type(value) is not str or not value for source in sources for value in source):
        return None
    return sources[0] if len(set(sources)) == 1 else None


def _chapter_programs(programs):
    chapters = [program for program in programs if program.event in _CHAPTER_EVENTS]
    by_event = {program.event: program for program in chapters}
    if len(chapters) != len(_CHAPTER_EVENTS) or tuple(sorted(by_event)) != _CHAPTER_EVENTS:
        return None
    if any(program.active_zone != "battlefield" or program.event_condition is not None
           or program.target_schema is not None or program.cost_schema is not None
           or program.destination is not None or program.trigger_limit is not None
           or program.handlers or program.requires_arbiter
           for program in chapters):
        return None
    return tuple(by_event[event] for event in _CHAPTER_EVENTS)


def _single_effect(program):
    if len(program.effects) == 1:
        return thaw_value(program.effects[0])
    return None


def _granted_activation(program, *, current):
    effect = _single_effect(program)
    if not isinstance(effect, Mapping):
        return None
    try:
        if current:
            if SAGA_CHAPTER_GRANT_CAPABILITY not in program.capability_dependencies:
                return None
            return validate_saga_chapter_grant(effect)
        if effect != {"op": "grant_ability_fragment", "source": "$source",
                      "fragment": effect.get("fragment")}:
            return None
        fragment = effect["fragment"]
        if not isinstance(fragment, Mapping) or set(fragment) != {"kind", "value"} or fragment["kind"] != "granted_activated":
            return None
        return GrantedActivatedAbilitySpec.from_dict(fragment["value"])
    except (TypeError, ValueError, KeyError):
        return None


def _same_activation(current, old, *, effect_text):
    return (
        old.schema_version == 1 and current.effect_text == effect_text
        and replace(current, ability_id=old.ability_id, semantic_key=old.semantic_key,
                    effect_text=old.effect_text) == old
    )


def _activation_child(programs, grant):
    return next((program for program in programs if program.key == grant.semantic_key
                 and program.ability_id in {grant.ability_id, f"ability:{grant.ability_id}"}
                 and program.active_zone == "battlefield" and program.event == "activate"
                 and program.event_condition is None and program.target_schema is None
                 and program.trigger_limit is None and not program.requires_arbiter
                 and program.destination is None), None)


def _generated_activation(child, chapter, grant):
    child_span = child.provenance.get("source_span")
    chapter_span = chapter.provenance.get("source_span")
    if not isinstance(child_span, Mapping) or not isinstance(chapter_span, Mapping):
        return None
    line = child_span.get("line")
    if (child.provenance.get("granted_only") is not True or type(line) is not int or line < 1
            or line != chapter_span.get("line") or child.ability_id != grant.ability_id
            or not child.ability_id.startswith("ability:granted:")
            or child.key != f"{child.oracle_id}:{child.ability_id}"):
        return None
    ability = ActivatedAbility(
        ability_id=f"ab{line}", line_index=line - 1,
        oracle_line=f"{grant.cost_text}: {grant.effect_text}",
        cost_text=grant.cost_text, effect_text=grant.effect_text, zones=("battlefield",),
        mana=FrozenMap(dict.fromkeys(MANA_COST_KEYS, 0) | grant.mana_bundle),
        tap_source=grant.tap_source, mana_ability=grant.mana_ability,
        builtin_semantic_key=grant.semantic_key,
        fixed_mana_outputs=tuple(FixedManaMode.from_bundle(dict(output)) for output in grant.fixed_mana_outputs),
    )
    return ability if thaw_value(child.cost_schema) == activated_ability_cost(ability) else None


def _same_mana_child(child, ability):
    if child.effects or not ability.fixed_mana_outputs:
        return False
    mana = FixedActivatedManaAbilitySpec(
        ability_id=ability.ability_id, line_index=ability.line_index,
        oracle_line=ability.oracle_line, cost_text=ability.cost_text, effect_text=ability.effect_text,
        mana_cost=ability.mana, tap_source=True, sacrifice_source=False, life_payment=0,
        modes=ability.fixed_mana_outputs,
    )
    return thaw_value(child.handlers) == [
        {"handler_id": FIXED_MANA_HANDLER_ID, "schema_version": 1, "event": "activate",
         "ability": mana.to_dict()},
        {"handler_id": ACTIVATED_ABILITY_CATALOG_HANDLER_ID, "schema_version": 1, "event": "activate",
         "ability": ability.to_dict()},
    ]


def _same_token_child(child, old):
    effect, previous = _single_effect(child), _single_effect(old)
    if child.handlers or old.handlers or old.cost_schema is not None:
        return False
    if not isinstance(effect, Mapping) or not isinstance(previous, Mapping):
        return False
    characteristics = effect.get("characteristics")
    if not isinstance(characteristics, Mapping) or type(effect.get("quantity")) is not int:
        return False
    historical = historical_unnamed_token_view(effect.get("name"), characteristics)
    if historical is None:
        return False
    name, definition = historical
    modifier = QueryCharacteristicModifierSpec(
        quantity=CharacteristicQuantitySpec(
            scope=CharacteristicQuantityScope.CONTROLLER_ZONE,
            query=ObjectQuerySpec(zones=("battlefield",), types_all=("artifact",)),
        ),
        calculation=PowerToughnessCalculation.PER_MATCHING_OBJECT, power=1, toughness=1,
    )
    token = {"type_line": f"Token Artifact Creature — {name}", "power": "0", "toughness": "0", "colors": []}
    # The current default name and typed characteristic modifier replace the old
    # subtype-only name and static scaling payload for new records.
    if definition != {**token, "ability_fragments": [ability_fragment_to_dict(modifier)]}:
        return False
    legacy_modifier = {"kind": "dynamic_power_toughness", "value": {
        "schema_version": 1, "count_kind": "controller_battlefield_artifacts",
        "calculation": "per_matching_object", "power": 1, "toughness": 1, "minimum_count": 0,
    }}
    fixed_effect = {"op": "create_token", "controller": "$controller"}
    return effect == {**fixed_effect, "name": f"{name} Token", "quantity": 1,
                      "characteristics": characteristics} and previous == {
        **fixed_effect, "name": name, "characteristics": {
            **token, "oracle_text": _TOKEN_TEXT, "ability_fragments": [legacy_modifier],
        },
    }


def _same_search(current, old):
    effect = {
        "op": "search", "zone": "library", "destination": "battlefield", "shuffle_after": True,
        "count": {"minimum": 1, "maximum": 1},
    }
    # The shared search owner permits a zero result for this qualified selector.
    # The current selector uses the library's front-face characteristics, and
    # the printed instruction specifies no separate reveal.
    return ({PRINTED_MANA_COST_ARTIFACT_SEARCH_CAPABILITY_ID, "library.search.fixed_to_battlefield"}
            .issubset(current.capability_dependencies)
            and _single_effect(current) == {
                **effect, "selector": {"types": ["artifact"], "predicate": PRINTED_MANA_COST_ARTIFACT_SEARCH_PREDICATE},
            }
            and _single_effect(old) == {
                **effect, "selector": {"types": ["artifact"], "predicate": "mana_cost_0_or_1"},
                "searching_player": "$controller", "reveal": True,
            })


def shadowed_reviewed_saga_chapter_keys(record, generated_programs, reviewed_programs):
    """Return all four historical keys only after the complete current view matches."""
    current, reviewed = tuple(generated_programs), tuple(reviewed_programs)
    if len(current) != 5 or len(reviewed) != 4 or any(
        program.oracle_id != record.oracle_id or program.trust_level != "trusted"
        for program in (*current, *reviewed)
    ):
        return set()
    current_source, previous_source = _source_pair(current), _source_pair(reviewed)
    if (current_source is None or previous_source is None or current_source[0] != previous_source[0]
            or any(not reviewed_rulings_match(program, current_source[1]) for program in reviewed)):
        return set()
    if any(not str(program.provenance.get("authored_by", "")).startswith("oracle-ir-v")
           or not program.capability_closure or program.capability_closure.get("trusted") is not True
           for program in current) or any(
               program.provenance.get("review_status") != "reviewed"
               or str(program.provenance.get("authored_by", "")).startswith("oracle-ir-v") for program in reviewed
           ):
        return set()
    chapters, old_chapters = _chapter_programs(current), _chapter_programs(reviewed)
    if chapters is None or old_chapters is None or not _same_search(chapters[2], old_chapters[2]):
        return set()
    mana, token = (_granted_activation(program, current=True) for program in chapters[:2])
    old_mana, old_token = (_granted_activation(program, current=False) for program in old_chapters[:2])
    if any(grant is None for grant in (mana, token, old_mana, old_token)):
        return set()
    if (not mana.mana_ability or token.mana_ability or old_mana.semantic_key != "builtin:mana-fixed"
            or not _same_activation(mana, old_mana, effect_text=old_mana.effect_text)
            or not _same_activation(token, old_token,
                                    effect_text=old_token.effect_text.removesuffix(".") + f" with '{_TOKEN_TEXT}'")):
        return set()
    mana_child = _activation_child(current, mana)
    token_child = _activation_child(current, token)
    old_child = _activation_child(reviewed, old_token)
    if mana_child is None or token_child is None or old_child is None or mana_child.key == token_child.key:
        return set()
    mana_ability = _generated_activation(mana_child, chapters[0], mana)
    token_ability = _generated_activation(token_child, chapters[1], token)
    if mana_ability is None or token_ability is None or not _same_mana_child(mana_child, mana_ability):
        return set()
    if not _same_token_child(token_child, old_child):
        return set()
    return {program.key for program in reviewed}
