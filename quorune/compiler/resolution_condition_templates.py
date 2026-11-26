from __future__ import annotations

"""One public-state conditional result, optionally after a mandatory prefix."""

from copy import deepcopy
from . import regex_working_set as re
from typing import Callable

from ..ability_resolution_ordinal import (
    FIRST_ABILITY_COUNTER_CONDITION, FIRST_ABILITY_COUNTER_VERSION,
    SECOND_ABILITY_DRAW_CONDITION, SECOND_ABILITY_DRAW_VERSION, SECOND_ABILITY_REVEAL_VERSION,
    THIRD_ABILITY_RESULT_CONDITION, THIRD_ABILITY_RESULT_VERSION,
)
from ..continuous_conditions import FixedPublicStateFact
from ..counter_placement_targets import controlled_creature_counter_choice_effect
from ..resolution_conditions import (
    GREATEST_POWER_TARGET_DISCARD_VERSION,
    RESOLUTION_ALTERNATIVE_SCHEMA_VERSION,
    RESOLUTION_CONDITION_MECHANIC,
    RESOLUTION_CONDITION_OPERATION,
    resolution_condition_is_closed,
    resolution_result_is_fixed,
    validate_resolution_condition_instruction,
)
from .closed_effect_programs import CompiledEffectTemplate, _top_level_positions
from .fixed_numbers import FIXED_COUNT_PATTERN
from .public_state_queries import fixed_public_state_condition
from .public_state_fact_queries import greatest_creature_power_control_condition


_LINKED_BRANCH_WORDS = re.compile(r"\b(?:instead|this way|that much|otherwise|if)\b", re.I)
_COMPOUND_SUFFIX_RESULT = re.compile(r"\bthen\b|;", re.I)
_OTHERWISE = ". Otherwise, "
_DRAW_TAPPED_LAND_REPEAT = re.compile(
    rf"Draw (?:a|one) card\. You may put a land card from your hand onto the battlefield tapped\. "
    rf"If you control (?:{FIXED_COUNT_PATTERN}) or more lands, repeat this process once\.",
    re.IGNORECASE,
)
_ATTACK_HISTORY_DRAW_DISCARD = re.compile(
    r"(?P<draw>Draw (?:a card|two cards)\.) Then discard a card unless you attacked this turn\.", re.I,
)


def _leading_condition_parts(text: str) -> tuple[tuple[str, str, str], ...]:
    if "If " not in text and "Then if " not in text:
        return ()
    positions = _top_level_positions(text, "If ")
    separator = "If "
    if positions == () and "Then if " in text:
        separator = "Then if "
        positions = _top_level_positions(text, separator)
    if positions is None or len(positions) != 1 or not text.endswith("."):
        return ()
    position = positions[0]
    prefix = text[:position].strip()
    if (prefix and not prefix.endswith(".")) or (separator == "Then if " and not prefix):
        return ()
    body = text[position + len(separator):]
    if _LINKED_BRANCH_WORDS.search(body):
        return ()
    commas = _top_level_positions(body, ", ")
    return tuple((prefix, body[:comma], body[comma + 2:]) for comma in commas or ())


def _suffix_condition_parts(text: str) -> tuple[tuple[str, str, str], ...]:
    """Read a condition following the instruction it guards."""
    positions = _top_level_positions(text, " if ")
    if positions is None or len(positions) != 1 or not text.endswith("."):
        return ()
    split = positions[0]
    before, condition = text[:split], text[split + len(" if "):-1]
    stops = _top_level_positions(before, ". ")
    if stops is None:
        return ()
    prefix = before[:stops[-1] + 1] if stops else ""
    result = before[stops[-1] + 2:] if stops else before
    if prefix:
        result = result.removeprefix("Then ")
    return ((prefix, condition, result + "."),) if result and condition else ()


def _second_ability_draw_template(text, compile_component):
    for prefix, condition, body in (*_leading_condition_parts(text), *_suffix_condition_parts(text)):
        if (not prefix or condition.casefold() != "this is the second time this ability has resolved this turn"
                or body.casefold() != "draw a card."):
            continue
        mandatory = compile_component(prefix)
        result = compile_component(body)
        if mandatory[0] is None or result[0] is None or len(mandatory[1]) != 1 or result[2] is not None:
            continue
        wrapper = {
            "op": RESOLUTION_CONDITION_OPERATION, "schema_version": SECOND_ABILITY_DRAW_VERSION,
            "player": "$controller", "condition": dict(SECOND_ABILITY_DRAW_CONDITION),
            "resolution_ordinal": None, "effects": deepcopy(list(result[1])),
            "mechanic_ids": list(result[3]), "prefix_mechanic_ids": list(mandatory[3]),
        }
        return ("second-ability-resolution-draw-v1", (*deepcopy(mandatory[1]), wrapper),
                deepcopy(mandatory[2]), tuple(dict.fromkeys((RESOLUTION_CONDITION_MECHANIC, *mandatory[3], *result[3]))))
    return None


def first_ability_counter_trigger_result(binding, body, *, compile_component):
    from ..rules.ability_resolution_ordinal_shapes import (
        CONTROLLED_ARTIFACT_ENTRY, first_ability_counter_node_capabilities,
    )

    if ((binding.event.value, binding.variant, binding.active_zone) != (
        "artifact.enter", "artifact:source_controller:including_source:any_object", "battlefield",
    ) or body != binding.body or binding.event_condition != CONTROLLED_ARTIFACT_ENTRY):
        return None
    for prefix, condition, result in _leading_condition_parts(body):
        if (prefix.casefold(), condition.casefold(), result.casefold()) != (
            "you gain 1 life.", "this is the first time this ability has resolved this turn",
            "put a +1/+1 counter on this creature.",
        ):
            continue
        mandatory = compile_component(prefix)
        counter = compile_component(result)
        if mandatory[0] is None or counter[0] is None or mandatory[2] is not None or counter[2] is not None:
            return None
        wrapper = {
            "op": RESOLUTION_CONDITION_OPERATION, "schema_version": FIRST_ABILITY_COUNTER_VERSION,
            "player": "$controller", "condition": dict(FIRST_ABILITY_COUNTER_CONDITION),
            "resolution_ordinal": None, "effects": list(counter[1]),
            "mechanic_ids": list(counter[3]), "prefix_mechanic_ids": list(mandatory[3]),
        }
        effects = (*mandatory[1], wrapper)
        mechanics = (RESOLUTION_CONDITION_MECHANIC, *mandatory[3], *counter[3])
        if first_ability_counter_node_capabilities(effects=effects, target_schema=None, mechanic_ids=mechanics):
            return "first-artifact-entry-source-counter-v1", effects, None, mechanics
    return None


def second_ability_reveal_trigger_result(binding, body):
    from ..rules.ability_resolution_ordinal_shapes import CONTROLLED_LAND_ENTRY, second_ability_reveal_node_capabilities
    from .fixed_resolution_mana import fixed_resolution_mana_effect_template
    from .reveal_until_library_selection_templates import reveal_until_library_selection_effect_template

    if ((binding.event.value, binding.variant, binding.active_zone) != ("land.enter", "controlled_land", "battlefield")
            or body != binding.body or binding.event_condition != CONTROLLED_LAND_ENTRY):
        return None
    for prefix, condition, result in _leading_condition_parts(" ".join(body.split())):
        if (prefix.casefold() != "add one mana of any color."
                or condition.casefold() != "this is the second time this ability has resolved this turn"):
            continue
        mandatory = fixed_resolution_mana_effect_template(prefix)
        selection = reveal_until_library_selection_effect_template(result)
        if mandatory is None or selection is None:
            continue
        wrapper = {
            "op": RESOLUTION_CONDITION_OPERATION, "schema_version": SECOND_ABILITY_REVEAL_VERSION,
            "player": "$controller", "condition": dict(SECOND_ABILITY_DRAW_CONDITION),
            "resolution_ordinal": None, "effects": list(selection[1]),
            "mechanic_ids": list(selection[3]), "prefix_mechanic_ids": list(mandatory[3]),
        }
        effects = (*mandatory[1], wrapper)
        mechanics = (RESOLUTION_CONDITION_MECHANIC, *mandatory[3], *selection[3])
        if second_ability_reveal_node_capabilities(effects=effects, target_schema=None, mechanic_ids=mechanics):
            return "second-land-entry-reveal-until-v1", effects, None, mechanics
    return None


def third_ability_result_template(text, *, compile_component, cost_schema):
    """Lower one exact activated prefix and its third-resolution result."""
    from ..rules.ability_resolution_ordinal_shapes import (
        third_ability_result_cost_is_closed, third_ability_result_node_capabilities,
    )

    for prefix, condition, body in _leading_condition_parts(text):
        if not prefix or condition.casefold() != "this is the third time this ability has resolved this turn":
            continue
        mandatory = compile_component(prefix)
        result = compile_component(body)
        if (mandatory[0] is None or result[0] is None or len(mandatory[1]) != 1
                or len(result[1]) != 1 or result[2] is not None):
            continue
        wrapper = {
            "op": RESOLUTION_CONDITION_OPERATION, "schema_version": THIRD_ABILITY_RESULT_VERSION,
            "player": "$controller", "condition": dict(THIRD_ABILITY_RESULT_CONDITION),
            "resolution_ordinal": None, "effects": deepcopy(list(result[1])),
            "mechanic_ids": list(result[3]), "prefix_mechanic_ids": list(mandatory[3]),
        }
        effects = (*deepcopy(mandatory[1]), wrapper)
        schema = mandatory[2]
        mechanics = tuple(dict.fromkeys((RESOLUTION_CONDITION_MECHANIC, *mandatory[3], *result[3])))
        if (third_ability_result_cost_is_closed(wrapper, cost_schema)
                and third_ability_result_node_capabilities(effects=effects, target_schema=schema, mechanic_ids=mechanics)):
            return "third-ability-resolution-v1", effects, deepcopy(schema), mechanics
    return None


def other_spell_draw_template(text, *, compile_component):
    """Lower the exact other-spell draw following its announced damage instruction."""
    from ..other_spell_history_model import OTHER_SPELL_DRAW_CONDITION, OTHER_SPELL_DRAW_VERSION
    from ..rules.other_spell_history_shapes import other_spell_draw_node_capabilities

    if "cast another spell this turn" not in text.casefold():
        return None
    for prefix, condition, body in _leading_condition_parts(text.strip()):
        if (not prefix or condition.casefold().replace("’", "'") != "you've cast another spell this turn"
                or body.casefold() != "draw a card."):
            continue
        mandatory = compile_component(prefix)
        result = compile_component(body)
        if mandatory[0] is None or result[0] is None or len(mandatory[1]) != 1 or result[2] is not None:
            continue
        wrapper = {
            "op": RESOLUTION_CONDITION_OPERATION, "schema_version": OTHER_SPELL_DRAW_VERSION,
            "player": "$controller", "condition": dict(OTHER_SPELL_DRAW_CONDITION), "cast_fact": None,
            "effects": deepcopy(list(result[1])), "mechanic_ids": list(result[3]),
            "prefix_mechanic_ids": list(mandatory[3]),
        }
        effects = (*deepcopy(mandatory[1]), wrapper)
        mechanics = tuple(dict.fromkeys((RESOLUTION_CONDITION_MECHANIC, *mandatory[3], *result[3])))
        if other_spell_draw_node_capabilities(effects=effects, target_schema=mandatory[2], mechanic_ids=mechanics):
            return "other-current-turn-spell-draw-v1", effects, deepcopy(mandatory[2]), mechanics
    return None


def resolution_condition_template(
    text: str,
    *,
    source_name: str,
    compile_component: Callable[[str], CompiledEffectTemplate],
) -> CompiledEffectTemplate | None:
    normalized = re.sub(r"^[A-Z][A-Za-z' ]{0,80} — ", "", text.strip())
    if attack_discard := _ATTACK_HISTORY_DRAW_DISCARD.fullmatch(normalized):
        normalized = f"{attack_discard['draw']} If you haven't attacked this turn, discard a card."
    if ordinal := _second_ability_draw_template(normalized, compile_component):
        return ordinal
    from .public_self_replacement_templates import fixed_public_self_replacement_template
    replacement = fixed_public_self_replacement_template(
        normalized, source_name=source_name, compile_component=compile_component,
    )
    if replacement is not None:
        return replacement
    from .target_replacements import current_target_replacement_template
    replacement = current_target_replacement_template(normalized, compile_component=compile_component)
    if replacement is not None:
        return replacement
    alternative = _top_level_positions(normalized, _OTHERWISE) if _OTHERWISE in normalized else ()
    if alternative is None or len(alternative) > 1:
        return None
    otherwise = None
    if alternative:
        split = alternative[0]
        otherwise = normalized[split + len(_OTHERWISE):]
        normalized = normalized[:split + 1]
        if not otherwise.endswith(".") or _LINKED_BRANCH_WORDS.search(otherwise):
            return None
    parts = _leading_condition_parts(normalized)
    suffix_form = not parts
    standalone_suffix = suffix_form and otherwise is None
    if suffix_form:
        parts = _suffix_condition_parts(normalized)
    candidates = []
    for prefix, condition_text, body in parts:
        if _LINKED_BRANCH_WORDS.search(body):
            continue
        if standalone_suffix and _COMPOUND_SUFFIX_RESULT.search(body):
            continue
        greatest_power = greatest_creature_power_control_condition(condition_text)
        greatest_power_discard = False
        if greatest_power is not None:
            result = body.casefold()
            greatest_power_discard = result == "target opponent discards a card."
            if (
                greatest_power.fact is not FixedPublicStateFact.CONTROLS_A_GREATEST_POWER_CREATURE
                or not suffix_form or prefix
                or result not in {"draw a card.", "draw two cards.", "target opponent discards a card."}
                or otherwise is not None and (
                    result != "draw a card."
                    or otherwise.casefold() != "put a +1/+1 counter on a creature you control."
                )
            ):
                continue
        condition = greatest_power or fixed_public_state_condition(condition_text, source_name=source_name)
        if condition is None or not resolution_condition_is_closed(condition):
            continue
        mandatory = compile_component(prefix) if prefix else (None, (), None, ())
        # This relative instruction repeats only the two resolution effects. Keeping
        # the condition after them observes the land after its entry has settled.
        repeat_draw_and_entry = (
            body.casefold() == "repeat this process once."
            and _DRAW_TAPPED_LAND_REPEAT.fullmatch(normalized) is not None
            and mandatory[0] == "fixed-draw-then-private-hand-entry-v1"
        )
        conditional = mandatory if repeat_draw_and_entry else compile_component(body)
        if conditional[0] is None or not conditional[1] or (prefix and mandatory[0] is None):
            continue
        if (conditional[2] is not None) != greatest_power_discard:
            continue
        if standalone_suffix and len(conditional[1]) != 1:
            continue
        if not resolution_result_is_fixed(conditional[1], conditional[3]):
            continue
        if "fixed-next-turn-upkeep-draw" in (*mandatory[3], *conditional[3]):
            continue
        schemas = [schema for _, _, schema, _ in (mandatory, conditional) if schema is not None]
        # Independent targets in both branches need a different binding owner.
        if len(schemas) > 1 or len(mandatory[1]) + len(conditional[1]) > 8:
            continue
        wrapper = {
            "op": RESOLUTION_CONDITION_OPERATION,
            "player": "$controller",
            "condition": condition.to_dict(),
            "effects": deepcopy(list(conditional[1])),
            "mechanic_ids": list(conditional[3]),
            "prefix_mechanic_ids": list(mandatory[3]),
        }
        template_id = "fixed-resolution-public-condition-v1"
        mechanics = (*mandatory[3], *conditional[3])
        if greatest_power_discard:
            wrapper["schema_version"] = GREATEST_POWER_TARGET_DISCARD_VERSION
            try:
                validate_resolution_condition_instruction(wrapper)
            except ValueError:
                continue
        if otherwise is not None:
            alternate = ("one-controlled-creature-counter-choice-v1", (controlled_creature_counter_choice_effect(),),
                         None, ("cr-122-counters",)) if greatest_power is not None else compile_component(otherwise)
            if (alternate[0] is None or alternate[2] is not None or len(mandatory[1]) > 1):
                continue
            wrapper.update(
                schema_version=RESOLUTION_ALTERNATIVE_SCHEMA_VERSION,
                otherwise_effects=deepcopy(list(alternate[1])),
                otherwise_mechanic_ids=list(alternate[3]),
            )
            try:
                validate_resolution_condition_instruction(wrapper)
            except ValueError:
                continue
            mechanics += alternate[3]
            template_id = "fixed-resolution-public-alternative-v1"
        candidates.append((
            template_id,
            (*deepcopy(mandatory[1]), wrapper),
            deepcopy(schemas[0]) if schemas else None,
            tuple(dict.fromkeys((RESOLUTION_CONDITION_MECHANIC, *mechanics))),
        ))
    return candidates[0] if len(candidates) == 1 else None
