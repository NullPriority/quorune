"""Closed triggered source-return instruction authority and validation."""
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


def graveyard_self_return_runtime_dependencies(program):
    from collections.abc import Mapping
    found=set()
    def visit(value):
        if isinstance(value,Mapping):
            found.update(graveyard_self_return_dependencies(value))
            for child in value.values():visit(child)
        elif isinstance(value,(tuple,list)):
            for child in value:visit(child)
    visit(program.effects)
    events = GRAVEYARD_SELF_ENTRY_EVENTS if GRAVEYARD_SELF_ENTRY_CAPABILITY in found else GRAVEYARD_SELF_RETURN_EVENTS
    if found and (program.active_zone != "graveyard" or program.event not in events):
        raise ValueError('Triggered source return requires its represented graveyard event envelope')
    if GRAVEYARD_SELF_ENTRY_CAPABILITY in found:
        from ..card_programs.admission import program_has_declared_complete_source_admission
        if not program_has_declared_complete_source_admission(program):
            raise ValueError('Triggered battlefield self-return requires complete source admission')
    return tuple(sorted(found))
