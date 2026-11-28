"""Versioned public permanent state, separate from derived characteristics."""
from dataclasses import dataclass,fields
from collections.abc import Mapping
from typing import Any

BATTLEFIELD_ENTRY_PLAYER_VERSION_FIELD = "battlefield_entry_player_version"


def read_battlefield_entry_player_version(data: Mapping[str, object]) -> int | None:
    if BATTLEFIELD_ENTRY_PLAYER_VERSION_FIELD not in data:
        return None
    version = data[BATTLEFIELD_ENTRY_PLAYER_VERSION_FIELD]
    if type(version) is not int or version != 1:
        raise ValueError("Unsupported battlefield entry player version")
    return version

PERMANENT_EVENT_STATE_KEY='public_permanent_event_state'
PERMANENT_EVENT_STATE_PREFIX='permanent_state_'
ZONE_EVENT_NUMERICS_KEY = 'public_zone_event_numerics'
ZONE_EVENT_NUMERICS_VERSION = 2
STATE_QUALIFIED_ZONE_CAPABILITY='trigger.event.state_qualified_zone_change'
STATE_QUALIFIED_DAMAGE_CAPABILITY='trigger.event.state_qualified_damage'
COUNTERED_DAMAGE_SOURCE_CAPABILITY = "trigger.event.countered_creature_combat_damage"
COUNTERED_DAMAGE_SOURCE_VARIANT = "countered_controlled_creature_combat_damage_player"
SOURCE_PLUS_ONE_COUNTER_FIELD = "source_had_plus_one_counter"
COUNTERED_COMBAT_DRAW_PROLIFERATE_CAPABILITY = "trigger.event.countered_combat_damage_draw_proliferate"
SOURCE_ANY_COUNTER_FIELD = "source_had_any_counter"
ABOVE_BASE_POWER_FIELD = "source_power_above_base"
ABOVE_BASE_POWER_CAPABILITY = "trigger.event.combat_source_power_above_base"


def permanent_event_state_version(value):
    if value is not None and (type(value) is not int or value not in {1, ZONE_EVENT_NUMERICS_VERSION}):
        raise ValueError('Unsupported permanent event-state execution version')
    return value


@dataclass(frozen=True,slots=True)
class PermanentEventState:
    tapped: bool
    attacking: bool
    blocking: bool
    enchanted: bool
    equipped: bool
    modified: bool
    monstrous: bool
    schema_version: int=1

    def __post_init__(self):
        if type(self.schema_version) is not int or self.schema_version!=1:
            raise ValueError('Unsupported permanent event-state facts version')
        if any(type(getattr(self,f.name)) is not bool for f in fields(self) if f.name!='schema_version'):
            raise ValueError('Permanent event-state facts require exact booleans')

    def to_dict(self):return {f.name:getattr(self,f.name) for f in fields(self)}

    @classmethod
    def from_dict(cls,value):
        if not isinstance(value,Mapping) or set(value)!={f.name for f in fields(cls)}:
            raise ValueError('Permanent event-state facts have a closed schema')
        return cls(**dict(value))

    def context(self):
        return {PERMANENT_EVENT_STATE_PREFIX+('version' if key=='schema_version' else key):value
            for key,value in self.to_dict().items()}


def sealed_permanent_state_context(characteristics):
    if PERMANENT_EVENT_STATE_KEY not in characteristics:return {}
    return PermanentEventState.from_dict(characteristics[PERMANENT_EVENT_STATE_KEY]).context()


def damage_permanent_state_context(facts, *, had_plus_one_counter=None, had_any_counter=None, power_above_base=None):
    context = {} if facts is None else {"source_" + key: value for key, value in facts.context().items()}
    if had_plus_one_counter is not None:
        context[SOURCE_PLUS_ONE_COUNTER_FIELD] = had_plus_one_counter
    if had_any_counter is not None:
        context[SOURCE_ANY_COUNTER_FIELD] = had_any_counter
    if power_above_base is not None:
        context[ABOVE_BASE_POWER_FIELD] = power_above_base
    return context


def permanent_state_from_damage_context(payload):
    prefix='source_'+PERMANENT_EVENT_STATE_PREFIX
    values={key.removeprefix(prefix):value for key,value in payload.items() if key.startswith(prefix)}
    if not values:return None
    return PermanentEventState.from_dict({('schema_version' if key=='version' else key):value
        for key,value in values.items()})


def _state_event_dependencies(program,*,prefix,events,capability):
    found=False;version=False
    names={f.name for f in fields(PermanentEventState)}-{'schema_version'}
    def visit(value):
        nonlocal found,version
        if isinstance(value,Mapping):
            field=value.get('field')
            if isinstance(field,str) and field.startswith(prefix):
                found=True
                if set(value)!={'field','op','value'} or value['op']!='eq':
                    raise ValueError('Permanent state conditions have closed equality fields')
                name=field.removeprefix(prefix)
                if name=='version':
                    if type(value['value']) is not int or value['value']!=1:raise ValueError('Permanent state condition version must be1')
                    version=True
                elif name not in names or type(value['value']) is not bool:
                    raise ValueError('Permanent state conditions require a represented boolean')
            for child in value.values():visit(child)
        elif isinstance(value,(list,tuple)):
            for child in value:visit(child)
    visit(program.event_condition)
    if found and (not version or program.event.removesuffix('.self') not in events):
        raise ValueError('Permanent state conditions require a versioned supported occurrence')
    return (capability,) if found else ()


def state_qualified_zone_runtime_dependencies(program):
    return _state_event_dependencies(program,prefix=PERMANENT_EVENT_STATE_PREFIX,
        events={'permanent.enter','creature.dies','permanent.leave','permanent.graveyard'},
        capability=STATE_QUALIFIED_ZONE_CAPABILITY)


def _controlled_creature_combat_damage_condition(field):
    return {"all": [
        {"field": "source_controller", "op": "eq", "value": "$source.controller"},
        {"field": "source_zone", "op": "eq", "value": "battlefield"},
        {"field": "source_types", "op": "contains_any", "value": ["creature"]},
        {"field": "target_kind", "op": "eq", "value": "player"},
        {"field": "combat", "op": "truthy", "value": True},
        {"field": field, "op": "eq", "value": True},
    ]}


def countered_creature_combat_damage_condition():
    return _controlled_creature_combat_damage_condition(SOURCE_PLUS_ONE_COUNTER_FIELD)


def any_counter_combat_damage_condition():
    return _controlled_creature_combat_damage_condition(SOURCE_ANY_COUNTER_FIELD)


def above_base_power_combat_damage_condition():
    return _controlled_creature_combat_damage_condition(ABOVE_BASE_POWER_FIELD)


def _uses_damage_source_fact(value, *, field=SOURCE_PLUS_ONE_COUNTER_FIELD):
    if isinstance(value, Mapping):
        return (value.get("field") == field
                or any(_uses_damage_source_fact(child, field=field) for child in value.values()))
    if isinstance(value, (list, tuple)):
        return any(_uses_damage_source_fact(child, field=field) for child in value)
    return False


def optional_draw_proliferate_is_closed(effects, *, player='$controller') -> bool:
    from .counter_placement_event_model import PROLIFERATE_ORDER_VERSION_FIELD

    if not isinstance(effects, (list, tuple)) or len(effects) != 2:
        return False
    draw, proliferate = effects
    return (
        draw == {"op": "draw", "player": player, "count": 1, "private": True}
        and type(draw["count"]) is int and draw["private"] is True
        and proliferate == {"op": "proliferate", PROLIFERATE_ORDER_VERSION_FIELD: 1}
        and type(proliferate[PROLIFERATE_ORDER_VERSION_FIELD]) is int
    )


def supported_countered_combat_optional_result(program: Any) -> bool:
    from .replacement.immutable import FrozenMap

    if ((program.event, program.active_zone) != ("damage.dealt", "battlefield")
            or program.target_schema is not None or len(program.effects) != 1
            or FrozenMap(program.event_condition or {}) != FrozenMap(any_counter_combat_damage_condition())):
        return False
    wrapper = program.effects[0]
    return (set(wrapper) == {"op", "player", "effects"} and wrapper["op"] == "offer_optional_effect"
            and wrapper["player"] == "$controller" and optional_draw_proliferate_is_closed(wrapper["effects"]))


def _above_base_power_runtime_dependencies(program):
    from .replacement.immutable import immutable_fingerprint
    from .ability_fragments import CURRENT_ABILITY_FRAGMENT_COVERAGE

    draw = {"op": "draw", "player": "$controller", "count": 1, "private": True}
    coverage = {"one_or_more_event_batch", CURRENT_ABILITY_FRAGMENT_COVERAGE}
    if (
        (program.event, program.active_zone) != ("damage.dealt", "battlefield")
        or immutable_fingerprint(program.event_condition) != immutable_fingerprint(above_base_power_combat_damage_condition())
        or program.target_schema is not None or program.cost_schema is not None or program.handlers
        or len(program.effects) != 1 or immutable_fingerprint(program.effects[0]) != immutable_fingerprint(draw)
        or not coverage.issubset(program.coverage) or "intervening_condition" in program.coverage
        or program.trigger_limit is not None
    ):
        raise ValueError("Power-qualified combat draw requires its exact controlled damage batch and result")
    return (ABOVE_BASE_POWER_CAPABILITY, "trigger.event.qualified_damage_batch", "zone.draw.library_to_hand")


def state_qualified_damage_runtime_dependencies(program):
    declared = getattr(program, "capability_dependencies", ())
    if (ABOVE_BASE_POWER_CAPABILITY in declared
            or _uses_damage_source_fact(program.event_condition, field=ABOVE_BASE_POWER_FIELD)):
        return _above_base_power_runtime_dependencies(program)
    if (COUNTERED_COMBAT_DRAW_PROLIFERATE_CAPABILITY in declared
            or _uses_damage_source_fact(program.event_condition, field=SOURCE_ANY_COUNTER_FIELD)):
        if (not supported_countered_combat_optional_result(program)
                or program.handlers or program.cost_schema is not None
                or "one_or_more_event_batch" not in program.coverage
                or program.trigger_limit != {"schema_version": 4, "kind": "optional_result_once_per_turn"}):
            raise ValueError("Countered combat group requires its exact optional draw and proliferate result")
        return (COUNTERED_COMBAT_DRAW_PROLIFERATE_CAPABILITY,)
    if (COUNTERED_DAMAGE_SOURCE_CAPABILITY in declared
            or _uses_damage_source_fact(program.event_condition)):
        from .replacement.immutable import FrozenMap

        expected = {"op": "offer_draw", "player": "$controller", "drawer": "$controller",
                    "count": 1, "private": True}
        if (program.event != "damage.dealt" or program.active_zone != "battlefield"
                or FrozenMap(program.event_condition) != FrozenMap(countered_creature_combat_damage_condition())
                or program.target_schema is not None or program.handlers or len(program.effects) != 1
                or FrozenMap(program.effects[0]) != FrozenMap(expected)):
            raise ValueError("Countered creature damage requires the exact controlled combat draw occurrence")
        return (COUNTERED_DAMAGE_SOURCE_CAPABILITY,)
    return _state_event_dependencies(program,prefix='source_'+PERMANENT_EVENT_STATE_PREFIX,
        events={'damage.dealt'},capability=STATE_QUALIFIED_DAMAGE_CAPABILITY)
