from __future__ import annotations

"""Closed operational equivalence for current and reviewed trigger views."""

from copy import deepcopy
from dataclasses import replace
import hashlib,json
from ..fixed_token_production import historical_unnamed_token_view
from ..targets import target_plan
from .trigger_result_equivalence import (
    fixed_trigger_results_equal, fixed_graveyard_exile_views_equal, fixed_graveyard_self_entry_views_equal,
    ordered_sacrifice_or_entry_subtype_supersedes_reviewed,
)


def _condition(value,event):
    if value is None:return None
    value=deepcopy(value)
    if (event=='creature.dies' and isinstance(value,dict)
            and value=={'field':'previous_controller','op':'eq','value':'$source.controller'}):
        return {**value,'field':'controller'}
    if not isinstance(value,dict) or set(value)!={'all'} or not isinstance(value['all'],list):return value
    leaves=[]
    for leaf in value['all']:
        if not isinstance(leaf,dict) or set(leaf)!={'field','op','value'}:return value
        field,op,arg=leaf['field'],leaf['op'],leaf['value']
        if field in {'controller','previous_controller','card'}:
            if op not in {'eq','ne'} or not isinstance(arg,str):return value
        elif field=='types':
            if op not in {'contains_any','contains_all'} or not isinstance(arg,list) or not all(isinstance(x,str) for x in arg):return value
        else:return value
        leaves.append(_condition(leaf,event))
    return {'all':sorted(leaves,key=lambda x:json.dumps(x,sort_keys=True))}


def _effects(program):
    from .reviewed_overlay import _canonical_effects
    effects=deepcopy(_canonical_effects(program))
    for effect in effects:
        if effect.get('op')=='destroy' and isinstance(effect.get('reason'),str):effect.pop('reason')
        if effect.get('op')=='create_token' and isinstance(effect.get('characteristics'),dict):
            chars = effect['characteristics']
            if 'copy_of' not in effect and 'copy_spec' not in effect:
                former = historical_unnamed_token_view(effect.get('name'), chars)
                if former is not None:
                    effect['name'], chars = former
                    effect['characteristics'] = chars
            line = chars.get('type_line')
            if isinstance(line,str) and line.startswith('Token '):
                from ..characteristic_evaluation import type_parts
                stripped=line.removeprefix('Token ')
                if type_parts(line)==type_parts(stripped):chars['type_line']=stripped
    return effects


def _optional_token_copy_supersedes_reviewed(current, old):
    """Retire the archived mandatory result only for its corrected printed trigger."""
    from ..token_copy_recipes import optional_controlled_token_copy_runtime_dependencies

    try:
        if not optional_controlled_token_copy_runtime_dependencies(current):
            return False
    except ValueError:
        return False
    if len(old.effects) != 2:
        return False
    old_create, old_copy = old.effects
    if old_copy != {"op": "copy_all_tokens", "player": "$controller"}:
        return False
    current_create = current.effects[0]
    if set(current_create) != {"op", "controller", "name", "quantity", "characteristics"}:
        return False
    definition = current_create["characteristics"]
    if not isinstance(definition, dict):
        return False
    previous = historical_unnamed_token_view(current_create["name"], definition)
    if previous is None or not previous[1]["type_line"].startswith("Token "):
        return False
    name, characteristics = previous
    characteristics["type_line"] = characteristics["type_line"].removeprefix("Token ")
    return old_create == {**current_create, "name": name, "characteristics": characteristics}


def _creature_subset_search_supersedes_reviewed(current, old):
    """Retire the exact limited, optional legacy view of this printed search."""
    if len(current.effects) != 1 or len(old.effects) != 1:
        return False
    from ..library_search_model import (
        CREATURE_SUBSET_MANA_VALUE_SEARCH_VERSION, CreatureSubsetManaValueSearch,
    )
    from ..replacement.immutable import FrozenMap

    if current.effects[0].get("schema_version") != CREATURE_SUBSET_MANA_VALUE_SEARCH_VERSION:
        return False
    try:
        search = CreatureSubsetManaValueSearch.from_effect(current.effects[0])
    except ValueError:
        return False
    previous = old.effects[0]
    expected = {
        "op": "search",
        "searching_player": "$controller",
        "zone": "library",
        "selector": {"types": ["Creature"], "mana_value_total": {"maximum": search.maximum}},
        "count": {"minimum": 0, "maximum": 100},
        "destination": "battlefield",
        "reveal": False,
        "shuffle_after": True,
        "optional": True,
    }
    return (
        FrozenMap(previous) == FrozenMap(expected)
        and type(previous["selector"]["mana_value_total"]["maximum"]) is int
        and all(type(value) is int for value in previous["count"].values())
        and previous["reveal"] is False and previous["shuffle_after"] is True
        and previous["optional"] is True
    )


def _controlled_destruction_reward_supersedes_reviewed(current, old):
    from ..replacement.immutable import FrozenMap
    from ..rules.controlled_destruction_source_counters import closed_controlled_destruction_source_counter_program

    if not closed_controlled_destruction_source_counter_program(current) or len(old.effects) != 1:
        return False
    legacy_effect = {
        "op": "destroy_selected_and_reward_source", "source": "$source", "cards": "$targets",
        "counter": "+1/+1", "counter_amount": 2,
    }
    legacy_schema = {"globally_distinct": True, "groups": [
        {"id": f"seat_{seat.lower()}", "zones": ["battlefield"], "categories": ["permanent"],
         "creature": True, "controller_seat": seat, "min": 0, "max": 1}
        for seat in "ABCD"
    ]}
    return (old.provenance.get("review_status") == "reviewed"
            and FrozenMap(old.target_schema or {}) == FrozenMap(legacy_schema)
            and type(old.effects[0].get("counter_amount")) is int
            and FrozenMap(old.effects[0]) == FrozenMap(legacy_effect))


def _named_additive_copy_supersedes_reviewed(current, old):
    """Recognize the archived name, target and cleanup convention for this copy shape."""
    from ..replacement.immutable import thaw_value
    from ..rules.token_creation_capability_shapes import _copy_recipe_capabilities
    from ..token_copy_recipes import NAMED_ADDITIVE_COPY_VERSION, TokenCopyRecipeSpec

    condition = {"all": [
        {"field": "player", "op": "eq", "value": "$source.controller"},
        {"field": "step", "op": "eq", "value": "beginning_combat"},
    ]}
    if ((current.event, current.active_zone) != ("step.begin", "battlefield")
            or thaw_value(current.event_condition) != condition or old.event_condition is not None
            or len(current.effects) != 1 or len(old.effects) != 1):
        return False
    effect = current.effects[0]
    copy_spec = effect.get("copy_spec")
    if not isinstance(copy_spec, dict) or copy_spec.get("schema_version") != NAMED_ADDITIVE_COPY_VERSION:
        return False
    dependencies = _copy_recipe_capabilities(effect, current.target_schema, current.coverage)
    if not dependencies or not set(dependencies) <= set(current.capability_dependencies):
        return False
    exception = TokenCopyRecipeSpec.from_dict(copy_spec).exception
    prior_target = {"zones": ["battlefield"], "categories": ["permanent"], "controller": "you",
                    "artifact": True, "creature": False, "count": 1}
    prior_effect = {
        "op": "create_modified_token_copy", "controller": "$controller", "card": "$target.0",
        "name": exception.name, "characteristics": {
            "name": exception.name, "type_line": "Artifact Creature \u2014 " + exception.add_subtypes[0].title(),
            "power": str(exception.power), "toughness": str(exception.toughness), "mana_value": 0,
        },
        "temporary_keywords": ["Haste"], "sacrifice_on_controller_end_step": True,
    }
    old_effect = thaw_value(old.effects[0])
    old_target = thaw_value(old.target_schema)
    return (old_target == prior_target and type(old_target["count"]) is int
            and old_target["artifact"] is True and old_target["creature"] is False
            and old_effect == prior_effect and type(old_effect["characteristics"]["mana_value"]) is int
            and old_effect["sacrifice_on_controller_end_step"] is True)


def _hand_entry_subtype_life_supersedes_reviewed(record, current, old):
    """Prefer the committed Cave result for the matching source-spanned trigger."""
    from ..object_predicate import ObjectQuerySpec
    from ..replacement.immutable import FrozenMap
    from ..rules.hand_entry_followup import HAND_ENTRY_SUBTYPE_LIFE_VERSION

    first_line = record.oracle_text.partition("\n")[0]
    if (current.event != "permanent.enter.self"
            or current.provenance.get("source_span") != {"start": 0, "end": len(first_line), "line": 1}
            or len(current.effects) != 2 or len(old.effects) != 2):
        return False
    draw = {"op": "draw", "player": "$controller", "count": 1, "private": True}
    legacy_entry = {
        "op": "put_land_from_hand", "player": "$controller", "cave_life": 4,
        "prompt": "You may put a land card from your hand onto the battlefield.",
    }
    current_entry = {
        "op": "put_card_from_hand", "player": "$controller", "tapped": False,
        "query": ObjectQuerySpec(zones=("hand",), types_all=("land",)).canonical_dict(),
        "schema_version": HAND_ENTRY_SUBTYPE_LIFE_VERSION,
        "followup": {"land_subtype": "cave", "amount": 4},
    }
    new_draw, new_entry = _effects(current)
    old_draw, old_entry = _effects(old)
    return (
        new_draw == old_draw == draw and old_entry == legacy_entry
        and FrozenMap(new_entry) == FrozenMap(current_entry)
    )


def _same_trigger_view(record, current, old):
    return (
        (_condition(current.event_condition, current.event) == _condition(old.event_condition, old.event)
         or fixed_graveyard_self_entry_views_equal(current, old))
        and (_effects(current) == _effects(old) or fixed_trigger_results_equal(current, old)
             or _optional_token_copy_supersedes_reviewed(current, old)
             or _hand_entry_subtype_life_supersedes_reviewed(record, current, old)
             or _creature_subset_search_supersedes_reviewed(current, old)
             or _random_graveyard_bottom_supersedes_reviewed(current, old)
             or _controlled_destruction_reward_supersedes_reviewed(current, old))
        and _targets_equal(current, old)
    ) or _named_additive_copy_supersedes_reviewed(current, old) or ordered_sacrifice_or_entry_subtype_supersedes_reviewed(current, old)


def _targets_equal(current,old):
    if _controlled_destruction_reward_supersedes_reviewed(current, old): return True
    if current.target_schema==old.target_schema:return True
    if fixed_graveyard_exile_views_equal(current,old):return True
    # Only one positional destroy result uses this schema normalization.
    effects=_effects(current)
    if (effects!=_effects(old) or len(effects)!=1 or effects[0].get('op')!='destroy'
            or effects[0].get('card')!='$target.0'):return False
    if current.target_schema is None or old.target_schema is None:return False
    try:a,b=(target_plan(p.target_schema) for p in (current,old))
    except (TypeError,ValueError,KeyError):return False
    if (a.modes or b.modes or a.globally_distinct or b.globally_distinct
            or a.same_player_groups or b.same_player_groups or len(a.groups)!=1 or len(b.groups)!=1):return False
    if any(g.min_targets!=1 or g.max_targets!=1 for g in (*a.groups,*b.groups)):return False
    return replace(a.groups[0],group_id='target')==replace(b.groups[0],group_id='target')


def _random_graveyard_bottom_supersedes_reviewed(current, old):
    """Prefer the replacement-aware version while keeping the archived operation executable."""
    from ..graveyard_recycling_model import (
        GRAVEYARD_RECYCLING_OPERATION, RANDOM_GRAVEYARD_BOTTOM_VERSION,
        GraveyardRecyclingSpec, random_graveyard_bottom_target_schema,
    )

    return (
        current.event == 'permanent.enter.self'
        and current.target_schema == old.target_schema == random_graveyard_bottom_target_schema()
        and tuple(current.effects) == ({
            'op': GRAVEYARD_RECYCLING_OPERATION, 'player': '$target.0',
            'recycling': GraveyardRecyclingSpec('target_player', RANDOM_GRAVEYARD_BOTTOM_VERSION).to_dict(),
        },)
        and tuple(old.effects) == ({'op': 'shuffle_graveyard_bottom_random', 'players': '$targets'},)
    )


def shadowed_reviewed_trigger_view_keys(record,generated_programs,reviewed_programs,*,capability_registry=None,capability_profile='traditional'):
    if capability_registry is None or record.layout!='normal' or record.faces:return set()
    from .binding import bind_semantic_program_runtime
    generated=tuple(generated_programs);reviewed=tuple(reviewed_programs);shadowed=set()
    oracle_hash=hashlib.sha256(record.oracle_text.encode('utf-8')).hexdigest()
    fields=('semantic_schema_version','active_zone','event','destination','cost_schema','handlers','trigger_limit','requires_arbiter')
    for old in reviewed:
        if (old.oracle_id!=record.oracle_id or old.trust_level!='trusted' or old.requires_arbiter
                or not old.ability_id.startswith('trigger:')
                or str(old.provenance.get('authored_by','')).startswith('oracle-ir-v')
                or old.provenance.get('source_oracle_hash')!=oracle_hash
                or not old.provenance.get('source_rulings_hash')):continue
        matches=[]
        for current in generated:
            if (current.oracle_id!=record.oracle_id or current.trust_level!='trusted'
                    or not current.ability_id.startswith('trigger:')
                    or not str(current.provenance.get('authored_by','')).startswith('oracle-ir-v')
                    or current.provenance.get('source_oracle_hash')!=oracle_hash
                    or current.provenance.get('source_rulings_hash')!=old.provenance['source_rulings_hash']
                    or any(getattr(current,key)!=getattr(old,key) for key in fields)
                    or not _same_trigger_view(record, current, old)):continue
            if bind_semantic_program_runtime(current,capability_registry=capability_registry,profile=capability_profile)['strict']:
                matches.append(current)
        if len(matches)==1:shadowed.add(old.key)
    return shadowed
