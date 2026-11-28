"""Closed fight excess and independent current-turn red mana retention."""

from ..creature_power_damage_model import CREATURE_POWER_DAMAGE_CAPABILITY
from ..replacement.immutable import FrozenMap, thaw_value


FIGHT_EXCESS_MANA_FIELD = "fight_opposing_creature_excess_red_version"
RETAIN_ALL_RED_FIELD = "retain_unspent_red_mana_version"
FIGHT_EXCESS_CAPABILITY = "damage.result.fight_opposing_creature_excess_red"
FIGHT_EXCESS_MECHANIC = "fight-opposing-creature-excess-red"
RETAIN_ALL_RED_MECHANIC = "retain-all-red-until-cleanup"
FIGHT_EXCESS_TEMPLATE = "fight-opposing-creature-committed-excess-red-v1"


def red_retention_effect(*, actor="$controller"):
    return {"op": "mana", "player": actor, "color": "R", "amount": 0, RETAIN_ALL_RED_FIELD: 1}


def fight_excess_schema():
    return {
        "groups": [
            {"id": group, "zones": ["battlefield"], "categories": ["permanent"],
             "controller_relation": relation, "types_any": ["creature"], "count": 1}
            for group, relation in (("fighter", "you"), ("opponent", "opponent"))
        ],
        "globally_distinct": True,
    }


def fight_excess_effect():
    return {"op": "creature_power_damage", "kind": "fight", "source": "$target.0", "target": "$target.1",
            "target_must_be_creature": True, "source_lki": None, FIGHT_EXCESS_MANA_FIELD: 1}


def fight_excess_red_capabilities(effects, target_schema):
    if len(effects) != 1:
        return ()
    effect = effects[0]
    flag = effect.get(FIGHT_EXCESS_MANA_FIELD)
    if type(flag) is not int or flag != 1:
        return ()
    if FrozenMap(effect) != FrozenMap(fight_excess_effect()) or FrozenMap(target_schema or {}) != FrozenMap(fight_excess_schema()):
        return ()
    return (
        FIGHT_EXCESS_CAPABILITY, CREATURE_POWER_DAMAGE_CAPABILITY, "damage.amount.positive",
        "damage.result.multitype_permanent",
        "target.revalidate_resolution", "mana.production.fixed_resolution",
    )


def is_retained_red(effect, *, actor="$controller"):
    metadata = {"reason", "_runtime_source"} if actor != "$controller" else set()
    body = {key: value for key, value in effect.items() if key not in metadata}
    return type(body.get(RETAIN_ALL_RED_FIELD)) is int and thaw_value(body) == red_retention_effect(actor=actor)


def fight_excess_mana_dependencies(program):
    from .effect_shape_walkers import nested_effects

    if not any(FIGHT_EXCESS_MANA_FIELD in effect or RETAIN_ALL_RED_FIELD in effect
               for effect in nested_effects((program.effects, program.handlers))):
        return ()
    if ((program.event, program.active_zone) != ("resolve", "stack") or len(program.effects) != 2
            or program.event_condition is not None or program.cost_schema is not None or program.handlers
            or not is_retained_red(program.effects[1])):
        raise ValueError("Fight excess requires its original resolving spell and independent red retention")
    required = fight_excess_red_capabilities(program.effects[:1], program.target_schema)
    if not required:
        raise ValueError("Fight excess lost the lawful original fighter roles")
    return required


def is_closed_fight_excess_mana(program):
    try:
        required = fight_excess_mana_dependencies(program)
    except (TypeError, ValueError, KeyError):
        return False
    return bool(required) and set(required) <= set(program.capability_dependencies)
