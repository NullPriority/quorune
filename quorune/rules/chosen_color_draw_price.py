"""One exact chosen-color payment rule for an ordinary private draw."""

from dataclasses import replace

from ..entry_designations import EntryDesignationKind, validate_designation
from ..replacement.immutable import FrozenMap, thaw_value


CAPABILITY = "activation.cost.chosen_color_draw"
FIELD = "chosen_color_draw_payment"
EFFECT_TEXT = "draw two cards. spend only mana of the chosen color to activate this ability."
COST_TEXT = "{3}, {T}"


def chosen_color_draw_text(cost, effect):
    return cost.strip() == COST_TEXT and " ".join(effect.split()).casefold() == EFFECT_TEXT


def draw_effect():
    return {"op": "draw", "player": "$controller", "count": 2, "private": True}


def chosen_color_draw_runtime_dependencies(program):
    schema = program.cost_schema or {}
    if FIELD not in schema and CAPABILITY not in program.capability_dependencies:
        return ()
    if (type(schema.get(FIELD)) is not int or schema[FIELD] != 1 or schema.get("text") != COST_TEXT
            or schema.get("tap_source") is not True or schema.get("mana", {}).get("GENERIC") != 3
            or any(schema.get("mana", {}).get(color) for color in "WUBRGC")
            or (program.event, program.active_zone) != ("activate", "battlefield")
            or program.target_schema is not None or program.event_condition is not None
            or thaw_value(program.effects) != [draw_effect()]):
        raise ValueError("Chosen-color draw requires its exact activation and original private draw")
    return (CAPABILITY, "zone.draw.library_to_hand")


def current_draw_price_color(host, source, ability):
    registry = getattr(host, "semantics", None)
    program = registry.get(ability.builtin_semantic_key) if registry is not None and ability.builtin_semantic_key else None
    if program is None or not chosen_color_draw_runtime_dependencies(program):
        return False, None
    try:
        return True, validate_designation(EntryDesignationKind.COLOR, source.annotations.get("chosen_color"))
    except ValueError:
        return True, None


def draw_spend_context(host, source, ability, context):
    _applies, color = current_draw_price_color(host, source, ability)
    if color is None:
        return context
    from ..mana_restrictions import actual_color_ability_spend_context

    return actual_color_ability_spend_context(context, color)


def priced_in_chosen_color(host, source, ability, prices):
    uses_color, chosen = current_draw_price_color(host, source, ability)
    if not uses_color:
        return prices
    from .activation.model import ActivationProposalError

    payable = []
    for price in prices:
        requirements = dict(price.requirements)
        count = requirements["GENERIC"]
        if (count and chosen is None) or any(requirements[color] for color in "WUBRGC" if color != chosen):
            continue
        if count:
            requirements[chosen] += count
            requirements["GENERIC"] = 0
        payable.append(replace(price, requirements=FrozenMap(requirements)))
    if not payable:
        raise ActivationProposalError("This activation requires the current chosen mana color",
                                      status="unpayable", reason="chosen_color_price")
    return tuple(payable)
