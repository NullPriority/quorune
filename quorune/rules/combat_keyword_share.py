"""The exact beginning-of-combat share of represented creature keyword variants."""

from collections.abc import Mapping

from ..keyword_abilities import FIXED_CHARACTERISTIC_KEYWORD_CAPABILITIES, normalized_effective_keywords
from ..object_predicate import ObjectQuerySpec
from ..qualified_hexproof import QUALIFIED_HEXPROOF_KEYWORDS
from ..replacement.immutable import FrozenMap


CAPABILITY = "continuous.resolution.combat_creature_keyword_share"
MECHANIC = "cr-611-continuous-effects"
TEMPLATE = "combat-creature-keyword-share-v1"
OPERATION = "modify_all_matching_permanents_until_end_of_turn"
VERSION = "combat_keyword_share_version"
CONDITION = FrozenMap({"all": [{"field": "step", "op": "eq", "value": "beginning_combat"}]})
_KEYWORDS = (
    "First Strike", "Flying", "Deathtouch", "Double Strike", "Haste", "Hexproof",
    "Indestructible", "Lifelink", "Menace", "Reach", "Skulk", "Trample", "Vigilance",
    *(keyword.title() for keyword in sorted(QUALIFIED_HEXPROOF_KEYWORDS)),
)


def combat_keyword_share_query(controller):
    return ObjectQuerySpec(zones=("battlefield",), controller=controller, types_all=("creature",))


def combat_keyword_share_effect(controller="$controller"):
    return {"op": OPERATION, VERSION: 1, "predicate": combat_keyword_share_query(controller).to_dict()}


def combat_keyword_share_effect_is_exact(effect, *, controller="$controller"):
    return (isinstance(effect, Mapping) and type(effect.get(VERSION)) is int
            and FrozenMap(effect) == FrozenMap(combat_keyword_share_effect(controller)))


def combat_keyword_share_result_dependencies(*, effects, target_schema, mechanic_ids):
    if (target_schema is not None or len(effects) != 1 or MECHANIC not in mechanic_ids
            or not combat_keyword_share_effect_is_exact(effects[0])):
        return ()
    return tuple(sorted({
        CAPABILITY, "continuous.resolution.fixed_characteristics_until_end_of_turn",
        *(capability for keyword in _KEYWORDS for capability in FIXED_CHARACTERISTIC_KEYWORD_CAPABILITIES[keyword]),
    }))


def combat_keyword_share_program_dependencies(program):
    from .effect_shape_walkers import nested_effects

    if not any(VERSION in effect for effect in nested_effects((program.effects, program.handlers))):
        return ()
    required = combat_keyword_share_result_dependencies(
        effects=program.effects, target_schema=program.target_schema, mechanic_ids=program.coverage,
    )
    if (not required or program.event != "step.begin" or program.active_zone != "battlefield"
            or FrozenMap(program.event_condition or {}) != CONDITION or program.handlers or program.cost_schema is not None):
        raise ValueError("The combat keyword share requires its exact unconditional battlefield schedule")
    return required


def shared_creature_keywords(host, creatures):
    """Capture actual keyword labels once, keeping each Hexproof quality distinct."""
    current = set().union(*(normalized_effective_keywords(host, creature) for creature in creatures))
    if "trample over planeswalkers" in current:
        raise ValueError("Trample over planeswalkers needs its own admitted combat assignment owner")
    return tuple(keyword for keyword in _KEYWORDS if keyword.casefold() in current)
