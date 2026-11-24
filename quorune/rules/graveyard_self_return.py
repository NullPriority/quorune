"""Closed triggered source-return instruction authority and validation."""
from collections.abc import Mapping

from ..dungeon_progress import DUNGEON_COMPLETED_EVENT
from ..life_change import FIRST_LIFE_GAIN_EVENT


GRAVEYARD_SELF_RETURN_CAPABILITY='zone.return.triggered_self_graveyard_hand'
GRAVEYARD_SELF_RETURN_VERSION='triggered_graveyard_hand_version'
GRAVEYARD_SELF_ENTRY_CAPABILITY = 'zone.return.triggered_self_graveyard_battlefield'
GRAVEYARD_SELF_ENTRY_VERSION = 'triggered_graveyard_battlefield_version'
GRAVEYARD_SELF_ENTRY_MECHANIC = 'source-graveyard-battlefield-return'
GRAVEYARD_SELF_RETURN_EVENTS = frozenset({
    "step.begin", "spell.cast", "land.enter", "permanent.enter", "creature.enter",
    "artifact.enter", "enchantment.enter", "permanent.sacrificed", FIRST_LIFE_GAIN_EVENT, "card.second_draw",
    DUNGEON_COMPLETED_EVENT,
})
GRAVEYARD_SELF_ENTRY_EVENTS = GRAVEYARD_SELF_RETURN_EVENTS | {'player.committed_crime'}


def graveyard_self_return_dependencies(effect):
    if GRAVEYARD_SELF_ENTRY_VERSION in effect:
        fields = {'op','origin','destination','tapped','source_form',GRAVEYARD_SELF_ENTRY_VERSION}
        if (set(effect)-{'_replacement_selections','_aura_target_ref'} != fields
                or effect['op'] != 'self_zone_move'
                or type(effect[GRAVEYARD_SELF_ENTRY_VERSION]) is not int or effect[GRAVEYARD_SELF_ENTRY_VERSION] != 1
                or (effect['origin'],effect['destination'],effect['source_form']) != ('graveyard','battlefield','card')
                or type(effect['tapped']) is not bool
                or '_aura_target_ref' in effect and (type(effect['_aura_target_ref']) is not str or not effect['_aura_target_ref'])):
            raise ValueError('Triggered graveyard battlefield entry requires a closed version-one source instruction')
        return (GRAVEYARD_SELF_ENTRY_CAPABILITY,)
    if GRAVEYARD_SELF_RETURN_VERSION not in effect:return ()
    fields={'op','origin','destination','tapped','source_form',GRAVEYARD_SELF_RETURN_VERSION}
    if (set(effect)-{'_replacement_selections'}!=fields or effect['op']!='self_zone_move'
            or type(effect[GRAVEYARD_SELF_RETURN_VERSION]) is not int or effect[GRAVEYARD_SELF_RETURN_VERSION]!=1
            or (effect['origin'],effect['destination'],effect['tapped'],effect['source_form'])!=('graveyard','hand',False,'card')
            or type(effect['tapped']) is not bool):
        raise ValueError('Triggered graveyard-to-hand return requires a closed version-one source instruction')
    return (GRAVEYARD_SELF_RETURN_CAPABILITY,)


def graveyard_self_return_node_capabilities(*,effects,target_schema,mechanic_ids):
    if target_schema is not None or not {'source-graveyard-hand-return',GRAVEYARD_SELF_ENTRY_MECHANIC}.intersection(mechanic_ids) or len(effects)!=1:return ()
    try:result=graveyard_self_return_dependencies(effects[0])
    except (ValueError,TypeError,KeyError):return ()
    return (*result,'zone.self_move.activated') if result else ()


def graveyard_opponent_life_gain_condition():
    return {"field": "player", "op": "ne", "value": "$source.owner"}


def _opponent_life_gain_paid_return_dependencies(program, found):
    from ..mana import mana_cost_to_vector

    payment = program.effects[0] if len(program.effects) == 1 else {}
    cost = payment.get("cost") if isinstance(payment, Mapping) else None
    effects = payment.get("effects") if isinstance(payment, Mapping) else None
    expected_cost, _ = mana_cost_to_vector("{R}")
    if (
        found != {GRAVEYARD_SELF_RETURN_CAPABILITY}
        or program.active_zone != "graveyard"
        or program.event_condition != graveyard_opponent_life_gain_condition()
        or program.target_schema is not None or program.cost_schema is not None or program.handlers
        or "one_or_more_event_batch" in program.coverage
        or not isinstance(payment, Mapping)
        or set(payment) != {"op", "player", "cost", "effects"}
        or payment["op"] != "offer_optional_mana_payment" or payment["player"] != "$controller"
        or not isinstance(cost, Mapping) or dict(cost) != expected_cost
        or any(type(amount) is not int for amount in cost.values())
        or not isinstance(effects, (list, tuple)) or len(effects) != 1
        or not isinstance(effects[0], Mapping)
        or graveyard_self_return_dependencies(effects[0]) != (GRAVEYARD_SELF_RETURN_CAPABILITY,)
    ):
        raise ValueError("Opponent life-gain return requires the original graveyard occurrence and one optional red mana payment")
    return (GRAVEYARD_SELF_RETURN_CAPABILITY, "trigger.event.normalized_life_gain")


def graveyard_self_return_runtime_dependencies(program):
    found=set()
    has_graveyard_hand_move = False
    def visit(value):
        nonlocal has_graveyard_hand_move
        if isinstance(value,Mapping):
            found.update(graveyard_self_return_dependencies(value))
            if (value.get("op"), value.get("origin"), value.get("destination")) == ("self_zone_move", "graveyard", "hand"):
                has_graveyard_hand_move = True
            for child in value.values():visit(child)
        elif isinstance(value,(tuple,list)):
            for child in value:visit(child)
    visit(program.effects)
    if program.event == "life.gained" and (found or has_graveyard_hand_move):
        return _opponent_life_gain_paid_return_dependencies(program, found)
    events = GRAVEYARD_SELF_ENTRY_EVENTS if GRAVEYARD_SELF_ENTRY_CAPABILITY in found else GRAVEYARD_SELF_RETURN_EVENTS
    if found and (program.active_zone != "graveyard" or program.event not in events):
        raise ValueError('Triggered source return requires its represented graveyard event envelope')
    if GRAVEYARD_SELF_ENTRY_CAPABILITY in found:
        from ..card_programs.admission import program_has_declared_complete_source_admission
        if not program_has_declared_complete_source_admission(program):
            raise ValueError('Triggered battlefield self-return requires complete source admission')
    return tuple(sorted(found))
