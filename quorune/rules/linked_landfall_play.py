"""Linked public top-card exile with an incarnation-bound normal play permission."""
from collections.abc import Mapping

from ..continuous_effect_model import ContinuousObjectIdentity
from ..replacement.immutable import FrozenMap


EXPLORE_EXILE_FIELD = "linked_landfall_play"
EXPLORE_SETTLE_FIELD = "linked_landfall_settlement"
EXPLORE_CONDITION_FIELD = "linked_landfall_exile_nonempty"
EXPLORE_BINDING = "landfall-play-and-end-step-settlement"
EXPLORE_EXILE_MECHANIC = "linked-landfall-play"
EXPLORE_SETTLE_MECHANIC = "linked-landfall-settlement"
EXPLORE_EXILE_CAPABILITY = "zone.exile.linked_landfall_play"
EXPLORE_SETTLE_CAPABILITY = "zone.exile.linked_landfall_settlement"


def exploration_exile_effect():
    return {"op":"exile_top_library_card","player":"$controller",EXPLORE_EXILE_FIELD:1}


def exploration_settlement_effect():
    return {"op":"choose_objects","source":"$source",EXPLORE_SETTLE_FIELD:1}


def exploration_end_step_condition():
    return {"all":[{"field":"player","op":"eq","value":"$source.controller"},
                   {"field":"step","op":"eq","value":"end_step"},
                   {"field":EXPLORE_CONDITION_FIELD,"op":"truthy","value":True}]}


def exploration_links(host, source_object_id, source_logical_object_id):
    from ..linked_ability_exile_state import current_linked_ability_exile

    if not source_object_id or not source_logical_object_id:
        return ()
    return current_linked_ability_exile(host,
        source=ContinuousObjectIdentity(source_object_id,source_logical_object_id),
        ability_pair=EXPLORE_BINDING)


def exploration_program_dependencies(program):
    if (not any(marker in effect for effect in program.effects for marker in (EXPLORE_EXILE_FIELD,EXPLORE_SETTLE_FIELD))
            and not {EXPLORE_EXILE_CAPABILITY,EXPLORE_SETTLE_CAPABILITY}.intersection(program.capability_dependencies)):
        return ()
    if (program.active_zone != "battlefield" or program.target_schema is not None
            or program.handlers or program.cost_schema is not None or len(program.effects) != 1):
        raise ValueError("Linked exploration requires its exact unqualified triggered ability")
    if FrozenMap(program.effects[0]) == FrozenMap(exploration_exile_effect()):
        if (program.event != "land.enter" or EXPLORE_EXILE_MECHANIC not in program.coverage
                or FrozenMap(program.event_condition or {}) != FrozenMap(
                    {"field":"controller","op":"eq","value":"$source.controller"})):
            raise ValueError("Linked exploration requires its original controlled land entry")
        return (EXPLORE_EXILE_CAPABILITY,"zone.exile.linked_ability_pair",
                "zone.change.destination_replacement","trigger.placement.apnap")
    if (FrozenMap(program.effects[0]) != FrozenMap(exploration_settlement_effect())
            or program.event != "step.begin" or EXPLORE_SETTLE_MECHANIC not in program.coverage
            or FrozenMap(program.event_condition or {}) != FrozenMap(exploration_end_step_condition())):
        raise ValueError("Linked settlement requires its original end step and intervening condition")
    return (EXPLORE_SETTLE_CAPABILITY, "zone.change.destination_replacement",
            "trigger.placement.apnap","damage.amount.positive","damage.result.player_life","damage.source.stack_current_or_lki")

def require_exploration_item(host,item,capability):
    program=host.semantics.get(item.semantic_key) if item is not None and item.semantic_key else None
    if (program is None or item.kind != "triggered_ability" or item.targets
            or item.context.get("currently_resolving") is not True
            or not host.semantic_program_is_current_trusted(program)
            or capability not in exploration_program_dependencies(program)
            or not set(exploration_program_dependencies(program)) <= set(program.capability_dependencies)):
        from ..errors import GameRuleError
        raise GameRuleError("Linked landfall instructions require their exact resolving source")
    return program


def apply_exploration_exile(host,effect,*,actor,reason):
    from ..errors import GameRuleError
    from ..impulse_access import grant_linked_normal_play
    from ..linked_ability_exile_state import exile_for_linked_ability

    runtime=effect.get("_runtime_source")
    ref=runtime.get("stack_ref") if isinstance(runtime,Mapping) else None
    item=next((candidate for candidate in host.state.stack if candidate.ref==ref),None)
    require_exploration_item(host,item,EXPLORE_EXILE_CAPABILITY)
    if (actor!=item.controller or effect.get("player")!=actor
            or set(effect)-{"op","player",EXPLORE_EXILE_FIELD,"_runtime_source","_replacement_selections","reason"}
            or type(effect[EXPLORE_EXILE_FIELD]) is not int or effect[EXPLORE_EXILE_FIELD]!=1):
        raise GameRuleError("Linked landfall exile changed its original controller or instruction")
    library=host.state.players[actor].zones["library"]
    if not library:
        return None
    card=host.state.cards[library[-1]]
    source=ContinuousObjectIdentity(item.source_object_id,item.context.get("source_logical_object_id"))
    exile_for_linked_ability(host,card=card,source=source,ability_pair=EXPLORE_BINDING,reason=reason,
                             replacement_selections=effect.get("_replacement_selections",()))
    if card.zone=="exile" and not card.face_down:
        grant_linked_normal_play(host,card=card,actor=actor)
    return card.ref


def bind_exploration_settlement(host,item,effect):
    if EXPLORE_SETTLE_FIELD not in effect:
        return effect
    from .stack_damage_sources import bound_stack_damage_source

    program=require_exploration_item(host,item,EXPLORE_SETTLE_CAPABILITY)
    links=exploration_links(host,item.source_object_id,item.context.get("source_logical_object_id"))
    source=bound_stack_damage_source(host,{"op":"damage_each_opponent","source":"$source"},item)
    bound={"op":"choose_objects","player":item.controller,EXPLORE_SETTLE_FIELD:1,
            "_linked_settlement_refs":[row.ref for row in links],"_linked_damage_source":source}
    if FrozenMap(effect)==FrozenMap(program.effects[0]):
        return bound
    if "_linked_landfall_orders" in effect and FrozenMap({
        key:value for key,value in effect.items() if key not in {"player","_linked_landfall_orders"}
    })==FrozenMap({key:value for key,value in bound.items() if key!="player"}):
        return effect
    raise ValueError("Linked settlement changed its exact printed instruction")
