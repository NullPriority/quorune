from __future__ import annotations

"""Complete source-backed result views for fixed entry search and exile."""

from collections.abc import Mapping
from ..library_search_model import FixedCountedLibrarySearchTemplate
from ..object_predicate import ObjectQuerySpec
from ..replacement.immutable import FrozenMap
from ..rules.capabilities import capability_dependencies_for_node
from ..rules.public_zone_move_capability_shapes import public_graveyard_card_exile_node_capabilities
from ..targets import target_plan


def fixed_graveyard_self_entry_views_equal(current, old):
    """Retire one closed legacy optional return after complete typed admission."""
    from ..rules.graveyard_self_return import (
        GRAVEYARD_SELF_ENTRY_CAPABILITY, GRAVEYARD_SELF_ENTRY_VERSION,
        graveyard_self_return_runtime_dependencies,
    )
    if (current.active_zone != 'graveyard' or old.active_zone != 'graveyard'
            or current.event != old.event or current.target_schema is not None or old.target_schema is not None
            or len(current.effects) != 1 or len(old.effects) != 1):return False
    outer, previous = current.effects[0], old.effects[0]
    if (set(outer) != {'op', 'player', 'effects'} or outer['op'] != 'offer_optional_effect'
            or outer['player'] != '$controller' or not isinstance(outer['effects'], (list, tuple))
            or list(outer['effects']) != [{'op': 'self_zone_move', 'origin': 'graveyard',
                'destination': 'battlefield', 'tapped': False, 'source_form': 'card', GRAVEYARD_SELF_ENTRY_VERSION: 1}]):return False
    if (set(previous) != {'op', 'player', 'prompt', 'options', 'then_by_choice'}
            or previous['op'] != 'choose_option' or previous['player'] != '$controller'
            or type(previous['prompt']) is not str or not previous['prompt']
            or not isinstance(previous['options'], (list, tuple)) or len(previous['options']) != 2
            or not isinstance(previous['then_by_choice'], Mapping)):return False
    options = previous['options']
    if any(not isinstance(row, Mapping) or set(row) != {'id', 'label'}
           or any(type(row[k]) is not str or not row[k] for k in row) for row in options):return False
    ids = [row['id'] for row in options]
    branches = previous['then_by_choice']
    if len(set(ids)) != 2 or set(branches) != set(ids):return False
    populated = [branch for branch in branches.values() if branch]
    if (len(populated) != 1 or not isinstance(populated[0], (list, tuple)) or len(populated[0]) != 1
            or not isinstance(populated[0][0], Mapping)
            or any(not isinstance(branch, (list, tuple)) for branch in branches.values())):return False
    move = dict(populated[0][0])
    reason = move.pop('reason', '')
    if (type(reason) is not str or move != {'op': 'move_if_in_zone', 'card': '$source',
            'from': 'graveyard', 'destination': 'battlefield', 'controller': '$controller'}):return False
    # A card in its graveyard has no battlefield controller; "you" is its owner.
    condition = old.event_condition
    if condition is not None and condition != {'field': 'controller', 'op': 'eq', 'value': '$source.controller'}:
        return False
    expected = None if condition is None else {**condition, 'value': '$source.owner'}
    if current.event_condition != expected:return False
    try:
        required = graveyard_self_return_runtime_dependencies(current)
    except (ValueError, TypeError, KeyError):return False
    return (GRAVEYARD_SELF_ENTRY_CAPABILITY in required
        and set(required) <= set(current.capability_dependencies))


def _graveyard_card_target(schema):
    required = {'zones', 'categories', 'count'}
    if (not isinstance(schema, Mapping) or not required <= set(schema)
            or set(schema)-required-{'owner_relation'} or schema['zones'] != ['graveyard']
            or schema['categories'] != ['card'] or type(schema['count']) is not int
            or schema['count'] != 1 or schema.get('owner_relation', 'any') != 'any'):return None
    try:return target_plan({**schema, 'owner_relation': 'any'})
    except (TypeError, ValueError):return None


def fixed_graveyard_exile_views_equal(current, old):
    a, b = (_graveyard_card_target(p.target_schema) for p in (current, old))
    if a is None or b is None or a != b:return False
    if (list(old.effects) != [{'op': 'exile', 'card': '$target.0'}]
            or list(current.effects) != [{'op': 'exile_public_graveyard_card', 'card': '$target.0'}]):return False
    required = public_graveyard_card_exile_node_capabilities(effects=current.effects,
        target_schema=current.target_schema, mechanic_ids=current.coverage)
    return bool(required) and set(required) <= set(current.capability_dependencies)


def _optional_graveyard_search_views_equal(current, old):
    if current.target_schema is not None or old.target_schema is not None:return False
    if len(current.effects) != 1 or len(old.effects) != 1:return False
    outer, previous = current.effects[0], old.effects[0]
    if (set(outer) != {'op', 'player', 'effects'} or outer['op'] != 'offer_optional_effect'
            or outer['player'] != '$controller' or not isinstance(outer['effects'], (list, tuple))
            or len(outer['effects']) != 1):return False
    fields = {'op', 'searching_player', 'zone', 'selector', 'count', 'destination',
        'reveal', 'shuffle_after', 'optional'}
    if (set(previous) != fields or previous['op'] != 'search' or previous['zone'] != 'library'
            or previous['searching_player'] != '$controller' or previous['destination'] != 'graveyard'
            or previous['reveal'] is not False or previous['shuffle_after'] is not True
            or previous['optional'] is not True):return False
    count = previous['count'];selector = previous['selector']
    if (not isinstance(count, Mapping) or set(count) != {'minimum', 'maximum'}
            or any(type(count[k]) is not int for k in count)
            or dict(count) != {'minimum': 0, 'maximum': 1}
            or not isinstance(selector, Mapping) or set(selector) != {'types'}
            or not isinstance(selector['types'], (list, tuple))):return False
    try:
        query = ObjectQuerySpec(zones=('library',), types_all=tuple(selector['types']))
        if query != ObjectQuerySpec(zones=('library',), types_all=('artifact',)):return False
        actual = FixedCountedLibrarySearchTemplate.from_effect(outer['effects'][0])
        expected = FixedCountedLibrarySearchTemplate(count=1, optional_count=False,
            selector=FrozenMap({'types': ['artifact']}), destination='graveyard', reveal=False)
    except (TypeError, ValueError, KeyError):return False
    required = capability_dependencies_for_node(effects=current.effects,
        target_schema=None, mechanic_ids=current.coverage)
    return actual == expected and bool(required) and set(required) <= set(current.capability_dependencies)


def _fixed_mana_cumulative_upkeep_views_equal(current, old):
    from ..rules.cumulative_upkeep_capability_shapes import fixed_mana_cumulative_upkeep_node_capabilities

    if (current.event != "step.begin" or current.active_zone != "battlefield" or len(old.effects) != 1):
        return False
    required = fixed_mana_cumulative_upkeep_node_capabilities(
        effects=current.effects, event_condition=current.event_condition,
        target_schema=current.target_schema, mechanic_ids=current.coverage,
    )
    if not required or not set(required) <= set(current.capability_dependencies):
        return False
    effect, = current.effects
    previous, = old.effects
    if set(previous) != set(effect):
        return False
    current_cost, old_cost = effect["cost_per_counter"], previous["cost_per_counter"]
    if (not isinstance(old_cost, Mapping) or not set(old_cost) <= set(current_cost)
            or any(type(value) is not int for value in old_cost.values())):
        return False
    return previous == {**effect, "cost_per_counter": {key: current_cost[key] for key in old_cost}} and all(
        not value or key in old_cost for key, value in current_cost.items()
    )


def _opponent_cast_payment_views_equal(current, old):
    from ..rules.opponent_event_mana_payment import (
        opponent_event_mana_payment_program_dependencies, uses_opponent_event_mana_payment,
    )

    if len(current.effects) != 1 or len(old.effects) != 1:
        return False
    effect, = current.effects
    previous, = old.effects
    if (not uses_opponent_event_mana_payment(effect) or current.event != "spell.cast"
            or previous.get("op") != "remora_tax"
            or tuple(child.get("op") for child in effect.get("declined_effects", ())) != ("offer_draw",)):
        return False
    try:
        required = opponent_event_mana_payment_program_dependencies(current, effect)
    except (ValueError, TypeError, KeyError):
        return False
    amount = effect["cost"]["GENERIC"]
    old_cost = previous.get("cost")
    return (set(required) <= set(current.capability_dependencies)
            and isinstance(old_cost, Mapping) and type(old_cost.get("GENERIC")) is int
            and previous == {"op": "remora_tax", "player": "$context.controller",
                             "beneficiary": "$controller", "cost": {"GENERIC": amount}})


def _source_bound_populate_views_equal(current, old):
    from ..fixed_token_production import uses_source_bound_populate
    from ..rules.token_creation_capability_shapes import source_bound_populate_runtime_dependencies

    if (len(current.effects) != 1 or not uses_source_bound_populate(current.effects[0])
            or tuple(old.effects) != ({"op": "populate_with_haste", "player": "$controller"},)):
        return False
    try:
        required = source_bound_populate_runtime_dependencies(current)
    except (TypeError, ValueError, KeyError):
        return False
    return bool(required) and set(required) <= set(current.capability_dependencies)


def fixed_trigger_results_equal(current, old):
    from ..rules.draw_step_drawing_settlement import draw_step_settlement_supersedes_reviewed

    return (draw_step_settlement_supersedes_reviewed(current, old)
        or fixed_graveyard_exile_views_equal(current, old)
        or _optional_graveyard_search_views_equal(current, old)
        or fixed_graveyard_self_entry_views_equal(current, old)
        or fixed_token_choice_results_equal(current,old)
        or _source_bound_populate_views_equal(current, old)
        or _fixed_mana_cumulative_upkeep_views_equal(current, old)
        or _opponent_cast_payment_views_equal(current, old))


def _fixed_token_choice_results(program):
    from copy import deepcopy
    from ..fixed_token_production import historical_unnamed_token_view
    from ..rules.token_creation_capability_shapes import _fixed_token_effect_closure
    if program.target_schema is not None or len(program.effects)!=1:return None
    choice = program.effects[0]
    if (not isinstance(choice,Mapping) or set(choice)!={'op','player','prompt','options','then_by_choice'}
            or choice['op']!='choose_option' or choice['player']!='$controller'
            or type(choice['prompt']) is not str or not choice['prompt']):return None
    options,branches = choice['options'],choice['then_by_choice']
    if not isinstance(options,(list,tuple)) or not 2<=len(options)<=4 or not isinstance(branches,Mapping):return None
    result,ids = {},set()
    for option in options:
        if (not isinstance(option,Mapping) or set(option)!={'id','label'}
                or any(type(option[k]) is not str or not option[k] for k in option)
                or option['id'].casefold()!=option['label'].casefold()
                or option['id'] in ids or option['label'].casefold() in result):return None
        ids.add(option['id']);branch = branches.get(option['id'])
        if not isinstance(branch,(list,tuple)) or len(branch)!=1 or not isinstance(branch[0],Mapping):return None
        token = deepcopy(dict(branch[0]));reason = token.pop('reason','')
        if (type(reason) is not str or token.get('quantity')!=1 or type(token.get('quantity')) is not int
                or 'tapped' in token or _fixed_token_effect_closure(token) is None):return None
        former = historical_unnamed_token_view(token['name'],token['characteristics'])
        if former is not None:token['name'],token['characteristics'] = former
        if token['name'].casefold()!=option['label'].casefold():return None
        result[option['label'].casefold()] = token
    return result if ids==set(branches) else None


def fixed_token_choice_results_equal(current,old):
    """Normalize presentation IDs only after complete typed branch comparison."""
    from ..rules.fixed_token_choices import fixed_token_choice_node_capabilities
    required = fixed_token_choice_node_capabilities(effects=current.effects,
        target_schema=current.target_schema,mechanic_ids=current.coverage)
    if not required or not set(required)<=set(current.capability_dependencies):return False
    current_results,old_results = (_fixed_token_choice_results(program) for program in (current,old))
    return current_results is not None and current_results==old_results


def ordered_sacrifice_or_entry_subtype_supersedes_reviewed(current, old):
    """Match the complete earlier recipes while retaining their archived keys."""
    from ..creature_subtypes import canonical_creature_subtype
    from ..replacement.immutable import thaw_value
    from ..rules.ordered_opponent_creature_sacrifice import ordered_opponent_sacrifice_program_dependencies
    from ..rules.reanimation_entry_subtype import FIELD, entry_subtype_reanimation_program_dependencies

    if current.event_condition != old.event_condition:
        return False
    try:
        if ordered_opponent_sacrifice_program_dependencies(current):
            if len(old.effects) != 1 or old.target_schema is not None:
                return False
            prior = thaw_value(old.effects[0])
            prompt = prior.pop("prompt", None)
            return (
                type(prompt) is str and bool(prompt) and type(prior.get("count")) is int
                and prior == {"op": "choose_cards_apnap", "players": "opponents", "actor": "$controller",
                              "zone": "battlefield", "card_type": "creature", "count": 3, "then": "sacrifice"}
            )
        if not entry_subtype_reanimation_program_dependencies(current) or len(old.effects) != 2:
            return False
        schema = thaw_value(old.target_schema)
        if not isinstance(schema, Mapping) or set(schema) != {"groups"} or len(schema["groups"]) != 1:
            return False
        group = dict(schema["groups"][0])
        group_id = group.pop("id", None)
        if (type(group_id) is not str or not group_id or group.get("creature") is not True
                or type(group.get("min")) is not int or type(group.get("max")) is not int
                or group != {"zones": ["graveyard"], "categories": ["card"], "creature": True, "min": 1, "max": 1}):
            return False
        prior_move, prior_subtype = map(thaw_value, old.effects)
        reasons = (prior_move.pop("reason", ""), prior_subtype.pop("reason", ""))
        subtype = prior_subtype.get("type")
        return (
            all(type(reason) is str for reason in reasons)
            and prior_move == {"op": "reanimate", "card": "$target.0", "controller": "$controller"}
            and type(subtype) is str and canonical_creature_subtype(subtype) == current.effects[0][FIELD]
            and prior_subtype == {"op": "add_type", "card": "$target.0", "type": subtype}
        )
    except (TypeError, ValueError, KeyError):
        return False
