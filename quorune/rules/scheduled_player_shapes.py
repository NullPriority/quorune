from __future__ import annotations

"""Closed fixed effects addressed to the sealed scheduled active player."""

from typing import Any,Iterable,Mapping,Sequence
from ..event_player_result_values import event_result_value_capabilities
from ..replacement.immutable import thaw_value
from ..scalar_effect_amount_model import SCALAR_AMOUNT_CAPABILITY, ScalarAmountOrigin, ScalarEffectAmountSpec
from ..untargeted_untap_model import is_scheduled_land_untap
from .affected_player_discard_capability_shapes import (
    fixed_context_player_discard_node_capabilities,
    fixed_controller_discard_node_capabilities,
)

SCHEDULED_PLAYER_CAPABILITY = 'trigger.event.scheduled_player_result'
SCHEDULED_PLAYER_MECHANIC = 'scheduled-player-event-result'
SCHEDULED_PLAYER_DEPARTURE_COVERAGE = 'scheduled_player_departure_v1'
UPKEEP_SOURCE_COUNTER_DRAW_CAPABILITY = 'resolution.scheduled_player.upkeep_source_counter_draw'
UPKEEP_SOURCE_COUNTER_DRAW_FIELD = 'upkeep_source_counter_draw'
_UPKEEP_DRAW_SCHEDULE = {'all': [{'field': 'step', 'op': 'eq', 'value': 'upkeep'}]}
_UPKEEP_DRAW_AMOUNT = ScalarEffectAmountSpec(ScalarAmountOrigin.SOURCE, counter_name='+1/+1', schema_version=2)


def upkeep_source_counter_draw_effect():
    return {'op': 'draw', 'player': '$context.player', 'count': _UPKEEP_DRAW_AMOUNT.to_dict(),
            'private': True, UPKEEP_SOURCE_COUNTER_DRAW_FIELD: 1}


def _is_upkeep_source_counter_draw(effect):
    return isinstance(effect, Mapping) and UPKEEP_SOURCE_COUNTER_DRAW_FIELD in effect


def _upkeep_source_counter_draw_capabilities(effects):
    if len(effects) != 1 or not _is_upkeep_source_counter_draw(effects[0]):
        return ()
    effect = effects[0]
    try:
        if (type(effect[UPKEEP_SOURCE_COUNTER_DRAW_FIELD]) is not int or effect.get('private') is not True
                or thaw_value(effect) != upkeep_source_counter_draw_effect()
                or ScalarEffectAmountSpec.from_dict(effect['count']) != _UPKEEP_DRAW_AMOUNT):
            return ()
    except (TypeError, ValueError, KeyError):
        return ()
    return (UPKEEP_SOURCE_COUNTER_DRAW_CAPABILITY, SCHEDULED_PLAYER_CAPABILITY,
            SCALAR_AMOUNT_CAPABILITY, 'trigger.placement.apnap', 'zone.draw.library_to_hand')


def bind_upkeep_source_counter_draw(host, item, effect):
    if not _is_upkeep_source_counter_draw(effect):
        return effect
    seat = item.context.get('player')
    if (not _upkeep_source_counter_draw_capabilities((effect,)) or item.kind != 'triggered_ability'
            or item.context.get('event') != 'step.begin' or item.context.get('step') != 'upkeep'
            or type(seat) is not str or seat not in host.state.players):
        raise ValueError('Scheduled source-counter draw requires its original upkeep participant and source count')
    if not host.state.players[seat].in_game:
        return None
    from ..scalar_effect_amounts import resolve_scalar_effect_amount

    count = resolve_scalar_effect_amount(host, effect['count'], item, use_phased_source_lki=True)
    return {'op': 'draw', 'player': seat, 'count': count, 'private': True} if count else None


def scheduled_player_subject_instruction(effect: Mapping[str, Any]) -> bool:
    from .scheduled_life_return import is_scheduled_life_return

    if is_scheduled_life_return(effect) or is_scheduled_land_untap(effect):
        return effect.get("player") == "$context.player"
    operation = effect.get('op')
    if operation == 'hand_library_redraw':
        from ..hand_library_redraw_model import decode_hand_library_redraw, HandLibraryScope
        try:return decode_hand_library_redraw(effect).scope is HandLibraryScope.EVENT_PLAYER
        except (TypeError,ValueError):return False
    if operation == 'damage':
        return effect.get('target') == '$context.player'
    if operation == 'choose_cards_apnap':
        return effect.get('players') == ['$context.player']
    return (operation in ('life', 'lose_life', 'draw', 'mill')
            and effect.get('player') == '$context.player')


def scheduled_player_node_capabilities(*,effects:Sequence[Mapping[str,Any]],target_schema:Mapping[str,Any]|None,mechanic_ids:Iterable[str]):
    if SCHEDULED_PLAYER_MECHANIC not in set(mechanic_ids) or target_schema is not None or not 1<=len(effects)<=2:return ()
    from .upkeep_power_counter_balance import has_upkeep_power_counter_effect, upkeep_power_counter_node_capabilities

    if any(has_upkeep_power_counter_effect(effect) for effect in effects):
        return upkeep_power_counter_node_capabilities(effects, target_schema)
    if any(_is_upkeep_source_counter_draw(effect) for effect in effects):
        return _upkeep_source_counter_draw_capabilities(effects)
    from .upkeep_source_counter_sacrifice import (
        upkeep_source_counter_sacrifice_node_capabilities, uses_upkeep_source_counter_sacrifice,
    )

    if any(uses_upkeep_source_counter_sacrifice(effect) for effect in effects):
        return upkeep_source_counter_sacrifice_node_capabilities(effects, target_schema)
    if any(is_scheduled_land_untap(effect) for effect in effects):
        from .untargeted_untap_shapes import fixed_land_untap_choice_capabilities

        required = fixed_land_untap_choice_capabilities(
            effects=effects, target_schema=target_schema, mechanic_ids=mechanic_ids,
        )
        return (SCHEDULED_PLAYER_CAPABILITY, *required) if required else ()
    from .scheduled_life_return import is_scheduled_life_return, scheduled_life_return_node_dependencies
    if any(is_scheduled_life_return(effect) for effect in effects):
        return scheduled_life_return_node_dependencies(effects[0], target_schema, mechanic_ids) if len(effects) == 1 else ()
    from .event_player_choice_shapes import UPKEEP_CREATURE_RETURN_MECHANIC, upkeep_creature_return_node_capabilities
    if UPKEEP_CREATURE_RETURN_MECHANIC in mechanic_ids:
        return upkeep_creature_return_node_capabilities(effects, target_schema, mechanic_ids)
    if not any(scheduled_player_subject_instruction(effect) for effect in effects):return ()
    capabilities={SCHEDULED_PLAYER_CAPABILITY}
    for effect in effects:
        op=effect.get('op')
        if op == 'hand_library_redraw':
            from .hand_library_redraw import hand_library_redraw_node_capabilities
            required = hand_library_redraw_node_capabilities(effects=(effect,),target_schema=None,mechanic_ids=mechanic_ids)
            if not required or not scheduled_player_subject_instruction(effect):return ()
            capabilities.update(required)
            continue
        if op == 'choose_cards_apnap':
            if effect.get('players') not in (['$context.player'], ['$controller']):
                return ()
            if effect.get('then') == 'sacrifice' and effect.get('players') == ['$context.player']:
                from .affected_player_sacrifice_capability_shapes import fixed_affected_player_sacrifice_node_capabilities
                required = fixed_affected_player_sacrifice_node_capabilities(
                    effects=({**effect, 'players': ['$controller']},), target_schema=None, mechanic_ids=mechanic_ids)
            else:
                checker = fixed_context_player_discard_node_capabilities if effect.get('players') == ['$context.player'] else fixed_controller_discard_node_capabilities
                required = checker(effects=(effect,), target_schema=None, mechanic_ids=mechanic_ids)
            if not required:
                return ()
            capabilities.update(required)
            continue
        layouts={'life':('delta',{'op','player','delta'},'life.change.effect'),
            'lose_life':('amount',{'op','player','amount'},'life.change.effect'),
            'draw':('count',{'op','player','count','private'},'zone.draw.library_to_hand'),
            'mill':('count',{'op','player','count'},'zone.mill.fixed'),
            'damage':('amount',{'op','source','target','amount'},'damage.amount.positive')}
        if op not in layouts:return ()
        field,fields,capability=layouts[op]
        reference='target' if op=='damage' else 'player'
        players = {'$context.player'} if op == 'damage' else {'$context.player', '$controller'}
        if set(effect)!=fields or type(effect.get(reference)) is not str or effect.get(reference) not in players:
            return ()
        if op == 'lose_life':
            value_capabilities=event_result_value_capabilities(op,field,effect.get(field))
            if value_capabilities is None:return ()
            if value_capabilities and 'half-current-player-life-loss' not in set(mechanic_ids):return ()
            capabilities.update(value_capabilities)
        elif type(effect.get(field)) is not int or (effect[field]==0 if op=='life' else effect[field]<=0):return ()
        if op=='draw' and effect['private'] is not True:return ()
        if op=='damage':
            if effect['source']!='$source':return ()
            capabilities.add('damage.result.player_life')
        capabilities.add(capability)
    return tuple(sorted(capabilities))


def is_closed_scheduled_player_program(program):
    from .upkeep_hand_differences import _difference_specs,is_closed_upkeep_hand_difference_program
    if _difference_specs(program):return is_closed_upkeep_hand_difference_program(program)
    required=scheduled_player_node_capabilities(effects=program.effects,target_schema=program.target_schema,mechanic_ids=program.coverage)
    return bool(required) and set(required).issubset(program.capability_dependencies)


def _scheduled_steps(condition: Mapping[str, Any] | None) -> set[str]:
    if not isinstance(condition, Mapping):
        return set()
    if set(condition) == {'all'} and isinstance(condition['all'], (list, tuple)):
        return set().union(*(_scheduled_steps(child) for child in condition['all']))
    value = condition.get('value')
    if type(value) is str and condition == {'field': 'step', 'op': 'eq', 'value': value}:
        return {value}
    return set()


def scheduled_player_result_step(program) -> str | None:
    steps = _scheduled_steps(program.event_condition)
    if program.event == 'step.begin' and steps in ({'upkeep'}, {'draw'}):
        return next(iter(steps))
    return None


def is_upkeep_participant_schedule(condition: Any) -> bool:
    if not isinstance(condition, Mapping) or set(condition) != {'all'}:
        return False
    clauses = condition['all']
    if not isinstance(clauses, (tuple, list)):
        return False
    upkeep = {'field': 'step', 'op': 'eq', 'value': 'upkeep'}
    opponent = {'field': 'player', 'op': 'ne', 'value': '$source.controller'}
    return tuple(clauses) in ((upkeep,), (upkeep, opponent))


def _scheduled_land_untap_runtime_dependencies(program):
    from ..replacement.immutable import thaw_value

    expected = {"all": [{"field": "step", "op": "eq", "value": "upkeep"}]}
    required = scheduled_player_node_capabilities(
        effects=program.effects, target_schema=program.target_schema, mechanic_ids=program.coverage,
    )
    if (not required or (program.event, program.active_zone) != ("step.begin", "battlefield")
            or thaw_value(program.event_condition) != expected or program.handlers or program.cost_schema is not None
            or SCHEDULED_PLAYER_DEPARTURE_COVERAGE not in program.coverage):
        raise ValueError("Mandatory land untap requires the exact sealed upkeep participant")
    return required


def scheduled_player_departure_runtime_dependencies(program) -> tuple[str, ...]:
    from .effect_shape_walkers import nested_effects
    from .upkeep_source_counter_sacrifice import upkeep_source_counter_sacrifice_program_dependencies
    from .upkeep_power_counter_balance import upkeep_power_counter_runtime_dependencies

    if power_counter_balance := upkeep_power_counter_runtime_dependencies(program):
        return power_counter_balance
    marked_draws = tuple(effect for effect in nested_effects((program.effects, program.handlers))
                         if _is_upkeep_source_counter_draw(effect))
    if marked_draws or UPKEEP_SOURCE_COUNTER_DRAW_CAPABILITY in program.capability_dependencies:
        required = _upkeep_source_counter_draw_capabilities(program.effects)
        if (not required or tuple(program.effects) != marked_draws or program.event != 'step.begin'
                or program.active_zone != 'battlefield' or thaw_value(program.event_condition) != _UPKEEP_DRAW_SCHEDULE
                or program.target_schema is not None or program.cost_schema is not None or program.handlers
                or SCHEDULED_PLAYER_DEPARTURE_COVERAGE not in program.coverage):
            raise ValueError('Scheduled source-counter draw requires the exact original battlefield upkeep trigger')
        return required

    if required := upkeep_source_counter_sacrifice_program_dependencies(program):
        return required

    if any(is_scheduled_land_untap(effect) for effect in nested_effects((program.effects, program.handlers))):
        return _scheduled_land_untap_runtime_dependencies(program)
    if program.event == 'step.begin' and any(effect.get('op') == 'choose_cards_apnap' and effect.get('then') == 'sacrifice'
           and effect.get('players') == ['$context.player'] for effect in program.effects):
        from ..compiler.affected_player_sacrifice_templates import FIXED_AFFECTED_PLAYER_SACRIFICE_MECHANIC
        required = scheduled_player_node_capabilities(effects=program.effects,
            target_schema=program.target_schema,
            mechanic_ids=(SCHEDULED_PLAYER_MECHANIC, FIXED_AFFECTED_PLAYER_SACRIFICE_MECHANIC, 'sacrifice'))
        if (not required or program.event != 'step.begin' or program.active_zone not in {'battlefield', 'command'}
                or not is_upkeep_participant_schedule(program.event_condition)):
            raise ValueError('Scheduled sacrifice requires a closed sealed upkeep-player choice')
        return required
    from .upkeep_hand_differences import upkeep_hand_difference_program_dependencies
    if required:=upkeep_hand_difference_program_dependencies(program):return required
    if SCHEDULED_PLAYER_DEPARTURE_COVERAGE not in program.coverage:
        return ()
    if scheduled_player_result_step(program) is None or not is_closed_scheduled_player_program(program):
        raise ValueError('Scheduled player departure requires a closed upkeep or draw result')
    return (SCHEDULED_PLAYER_CAPABILITY,)
