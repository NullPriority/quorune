"""Shape and resolution gate for magecraft rummage and its two exact ordinal results."""
from ..object_predicate import ObjectQuerySpec
from ..replacement.immutable import FrozenMap, thaw_value

FIELD = "magecraft_rummage_ordinal"
CAPABILITY = "resolution.effect.magecraft_rummage_ordinal"
MECHANIC = "magecraft-rummage-ordinal"
CAST_COPY_CONDITION = {"all":[{"field":"controller","op":"eq","value":"$source.controller"},
                            {"field":"types","op":"contains_any","value":["instant","sorcery"]}]}


def magecraft_instructions():
    return (
        {"op":"choose_cards_apnap","actor":"$controller","players":["$controller"],"zone":"hand",
         "predicate":ObjectQuerySpec(zones=("hand",)).to_dict(),"count":1,"then":"discard","hidden":True,
         "prompt":"Choose the required card(s) to discard."},
        {"op":"draw","player":"$controller","count":1,"private":True},
        {"op":"choose_objects",FIELD:1,
         "second":{"op":"damage_fixed_set","source":"$source","amount":2,"groups":[
             {"kind":"players","relation":"opponents"},
             {"kind":"permanents","controller_relation":"opponents","target_controller":None,
              "query":ObjectQuerySpec(zones=("battlefield",),types_all=("creature",)).to_dict()},
         ]},
         "third":{"op":"mana","player":"$controller","color":"R","amount":4,"source":"$source"}},
    )


def magecraft_ordinal_node_capabilities(*, effects, target_schema, mechanic_ids):
    if (target_schema is not None or MECHANIC not in mechanic_ids or len(effects)!=3
            or type(effects[2].get(FIELD)) is not int
            or FrozenMap({"effects":effects})!=FrozenMap({"effects":magecraft_instructions()})):
        return ()
    return (CAPABILITY,"choice.affected_player.fixed_controller_discard","zone.draw.library_to_hand",
            "damage.amount.positive","damage.result.player_life","damage.source.stack_current_or_lki",
            "damage.result.multitype_permanent","mana.production.fixed_resolution")


def magecraft_ordinal_program_dependencies(program):
    if not any(FIELD in effect for effect in program.effects) and CAPABILITY not in program.capability_dependencies:
        return ()
    required=magecraft_ordinal_node_capabilities(effects=program.effects,target_schema=program.target_schema,mechanic_ids=program.coverage)
    if (not required or (program.event,program.active_zone)!=("spell.cast_or_copy","battlefield")
            or program.cost_schema is not None or program.handlers or program.trigger_limit is not None
            or FrozenMap(program.event_condition or {})!=FrozenMap(CAST_COPY_CONDITION)):
        raise ValueError("Magecraft rummage requires its exact original cast-or-copy trigger")
    return required


def bind_magecraft_result(host,item,effects,record_ordinal):
    if not any(FIELD in effect for effect in effects):
        return None
    from ..errors import GameRuleError

    program=host.semantics.get(item.semantic_key) if item.semantic_key else None
    try:
        required=magecraft_ordinal_program_dependencies(program) if program is not None else ()
        if (not required or item.kind!="triggered_ability" or item.targets
                or not host.semantic_program_is_current_trusted(program)
                or not set(required)<=set(program.capability_dependencies)
                or FrozenMap({"effects":effects})!=FrozenMap({"effects":program.effects})):
            raise ValueError("Missing exact magecraft resolution authority")
    except (TypeError,ValueError,KeyError) as exc:
        raise GameRuleError(str(exc)) from exc
    ordinal=record_ordinal(host,item)
    result=effects[2].get({2:"second",3:"third"}.get(ordinal))
    return [*effects[:2],thaw_value(result)] if result is not None else list(effects[:2])
