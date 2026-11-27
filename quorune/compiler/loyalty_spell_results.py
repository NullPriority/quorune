"""Full-consumption compilation for the bounded fixed loyalty results."""

from ..impulse_access_model import top_free_instant_sorcery_effect
from ..next_spell_cost_model import MECHANIC as NEXT_COST_MECHANIC, next_turn_spell_cost_effect
from ..resolution_conditions import RESOLUTION_CONDITION_MECHANIC, other_blue_permanent_draw_effect
from ..rules.loyalty_spell_results import CAPABILITY, DRAW_MECHANIC, MECHANIC, loyalty_spell_result_capabilities
from ..rules.source_references import SourceReferenceSpec
from .activated_costs import activated_ability_cost
from .announced_damage_templates import fixed_announced_damage_template
from .dependency_gate import DependencyGate, explicit_capabilities_gate
from .impulse_access_templates import IMPULSE_ACCESS_MECHANIC_ID
from . import regex_working_set as re


def fixed_loyalty_spell_result(ability, *, source_name, text, public_timing):
    if (ability.loyalty_delta not in (1, -2, -8) or ability.zones != ("battlefield",)
            or ability.mana_ability or ability.sorcery_speed or ability.activation_conditions
            or public_timing is not None):
        return None
    delta = ability.loyalty_delta
    normalized = " ".join(text.strip().split())
    if delta == 1 and normalized.casefold() == "until your next turn, instant and sorcery spells you cast cost {1} less to cast.":
        result = ("loyalty-next-turn-spell-cost-v1", (next_turn_spell_cost_effect(),), None, (MECHANIC, NEXT_COST_MECHANIC))
    elif (delta == -8 and normalized.casefold() == "exile the top eight cards of your library. "
            "you may cast instant and sorcery spells from among them this turn without paying their mana costs."):
        result = ("loyalty-top-eight-instant-sorcery-v1", (top_free_instant_sorcery_effect(),), None,
                  (MECHANIC, IMPULSE_ACCESS_MECHANIC_ID))
    elif delta == -2:
        match = re.fullmatch(
            r"(?P<damage>.+ deals 2 damage divided as you choose among one or two targets\.) "
            r"Draw a card if you control a blue permanent other than (?P<subject>[^.]+)\.",
            normalized, re.I,
        )
        if match is None or not SourceReferenceSpec(source_name).matches(match["subject"]):
            return None
        damage = fixed_announced_damage_template(match["damage"], card_name=source_name, allow_source_pronoun=True)
        if damage is None:
            return None
        _template, effects, targets, mechanics = damage
        result = ("loyalty-divided-damage-then-conditional-draw-v1", (*effects, other_blue_permanent_draw_effect()),
                  targets, (MECHANIC, *mechanics, RESOLUTION_CONDITION_MECHANIC, DRAW_MECHANIC))
    else:
        return None
    _, effects, target_schema, mechanics = result
    return result if loyalty_spell_result_capabilities(
        effects=effects, target_schema=target_schema, active_zone="battlefield",
        cost_schema=activated_ability_cost(ability), mechanics=mechanics,
    ) else None


def fixed_loyalty_spell_result_gate(*, effects, target_schema, active_zone, cost_schema, mechanics,
                                    capability_registry, capability_profile):
    if MECHANIC not in mechanics:
        return None
    dependencies = loyalty_spell_result_capabilities(
        effects=effects, target_schema=target_schema, active_zone=active_zone,
        cost_schema=cost_schema, mechanics=mechanics,
    )
    if not dependencies:
        return DependencyGate(blockers=(f"capability:{CAPABILITY}",))
    return explicit_capabilities_gate(
        dependencies, capability_registry=capability_registry, capability_profile=capability_profile,
    )
