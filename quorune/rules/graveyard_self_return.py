"""Closed triggered source-return instruction authority and validation."""
GRAVEYARD_SELF_RETURN_CAPABILITY='zone.return.triggered_self_graveyard_hand'
GRAVEYARD_SELF_RETURN_VERSION='triggered_graveyard_hand_version'


def graveyard_self_return_dependencies(effect):
    if GRAVEYARD_SELF_RETURN_VERSION not in effect:return ()
    fields={'op','origin','destination','tapped','source_form',GRAVEYARD_SELF_RETURN_VERSION}
    if (set(effect)-{'_replacement_selections'}!=fields or effect['op']!='self_zone_move'
            or type(effect[GRAVEYARD_SELF_RETURN_VERSION]) is not int or effect[GRAVEYARD_SELF_RETURN_VERSION]!=1
            or (effect['origin'],effect['destination'],effect['tapped'],effect['source_form'])!=('graveyard','hand',False,'card')
            or type(effect['tapped']) is not bool):
        raise ValueError('Triggered graveyard-to-hand return requires a closed version-one source instruction')
    return (GRAVEYARD_SELF_RETURN_CAPABILITY,)


def graveyard_self_return_node_capabilities(*,effects,target_schema,mechanic_ids):
    if target_schema is not None or 'source-graveyard-hand-return' not in set(mechanic_ids) or len(effects)!=1:return ()
    try:result=graveyard_self_return_dependencies(effects[0])
    except (ValueError,TypeError,KeyError):return ()
    return (*result,'zone.self_move.activated') if result else ()


def graveyard_self_return_runtime_dependencies(program):
    from collections.abc import Mapping
    found=False
    def visit(value):
        nonlocal found
        if isinstance(value,Mapping):
            found=bool(graveyard_self_return_dependencies(value)) or found
            for child in value.values():visit(child)
        elif isinstance(value,(tuple,list)):
            for child in value:visit(child)
    visit(program.effects)
    if found and (program.active_zone!='graveyard' or program.event not in {
            'step.begin','spell.cast','land.enter','permanent.enter','creature.enter','artifact.enter','enchantment.enter',
            'permanent.sacrificed'}):
        raise ValueError('Triggered source return requires its represented graveyard event envelope')
    return (GRAVEYARD_SELF_RETURN_CAPABILITY,) if found else ()
