from __future__ import annotations

"""Lower one counter followed by closed results from the same stack subject."""

from . import regex_working_set as re

from ..rules.stack_counter_followup import (
    STACK_COUNTER_FOLLOWUP_OPERATION, STACK_TARGET_CONTROLLER, STACK_FOLLOWUP_PLAYER_SCHEMA,
    STACK_CHARACTERISTIC_FOLLOWUP_VERSION, StackCounterSubject, StackManaValueResult, stack_mana_value_followup,
    shared_type_hand_cast_followup, library_counter_position_followup, next_turn_draw_pair_followup,
    stack_counter_followup_node_capabilities, with_stack_counter_recipient,
    countered_ability_source_loss_effect, countered_ability_source_loss_schema,
    countered_ability_activation_lock_effect, countered_ability_activation_lock_schema,
)
from ..rules.stack_counter_name_scan import COUNTER_NAME_SCAN_EFFECT, COUNTER_NAME_SCAN_MECHANICS, COUNTER_NAME_SCAN_TARGET
from ..stack_counter import CounteredAbilityActivationLock
from .affected_player_discard_templates import fixed_controller_discard_effect_template
from .counter_templates import targeted_counter_effect_template


_FOLLOWUP = re.compile(r"Its controller (?P<verb>mills|loses|draws|discards|creates) (?P<body>.+)", re.I)


_COUNTER_NAME_SCAN = (
    "counter target spell. choose 1, 2, or 3 at random. its controller mills that many cards, "
    "then exiles cards from the top of their library until they exile a nonland card with a different name "
    "than that spell. they may cast that card without paying its mana cost. "
    "then they put the exiled cards on the bottom of their library in a random order."
)

_CHARACTERISTIC_FOLLOWUPS = {
    "if it was a legendary spell, the ring tempts you.": (
        StackCounterSubject.LEGENDARY_CREATURE_SPELL, "The Ring tempts you.",
    ),
    "if it was a jace planeswalker spell, scry 2.": (
        StackCounterSubject.JACE_PLANESWALKER_SPELL, "Scry 2.",
    ),
}


def _characteristic_counter_template(counter, tail, compile_component):
    subject, text = _CHARACTERISTIC_FOLLOWUPS[tail.casefold()]
    if counter.target_schema != subject.target_schema():
        return None
    template, results, schema, mechanics = compile_component(text)
    if template is None or schema is not None or len(results) != 1:
        return None
    effects = ({"op": STACK_COUNTER_FOLLOWUP_OPERATION, "schema_version": STACK_CHARACTERISTIC_FOLLOWUP_VERSION,
                "stack": "$target.0", "subject": subject.value, "followups": list(results)},)
    mechanics = (*counter.mechanics, *mechanics)
    if not stack_counter_followup_node_capabilities(
        effects=effects, target_schema=counter.target_schema, mechanic_ids=mechanics,
    ):
        return None
    return "fixed-stack-counter-characteristic-followup-v1", effects, counter.target_schema, mechanics


_COUNTERED_ABILITY_SOURCE_LOSS = (
    "counter up to one target activated or triggered ability. "
    "if an ability of an artifact, creature, or planeswalker is countered this way, "
    "that permanent loses all abilities for as long as this creature remains on the battlefield."
)


_ACTIVATION_LOCK_FOLLOWUPS = {
    "counter target activated or triggered ability. "
    "if a permanent's ability is countered this way, activated abilities of that permanent can't be activated this turn.":
        CounteredAbilityActivationLock.AFTER_SUCCESS,
    "counter target activated ability from an artifact, creature, enchantment, or land. "
    "that permanent's activated abilities can't be activated this turn.":
        CounteredAbilityActivationLock.AFTER_ATTEMPT,
}


_MANA_VALUE_RESULTS = {
    "counter target spell. you may cast a spell with equal or lesser mana value from your hand "
    "without paying its mana cost.":
        StackManaValueResult.HAND_CAST,
    "counter target creature spell. draw cards equal to that spell's mana value.":
        StackManaValueResult.DRAW,
    "counter target spell. create x treasure tokens, where x is that spell's mana value.":
        StackManaValueResult.TREASURE,
    "counter target spell. at the beginning of your next main phase, add an amount of {c} equal to that spell's mana value.":
        StackManaValueResult.NEXT_MAIN_COLORLESS,
    "counter target spell. at the beginning of your next first main phase, add x mana in any combination of colors, "
    "where x is that spell's mana value.":
        StackManaValueResult.NEXT_PRECOMBAT_COLORS,
}


_SHARED_TYPE_HAND_CAST = (
    "counter target spell. you may cast a spell that shares a card type with it "
    "from your hand without paying its mana cost."
)


_LIBRARY_COUNTER_POSITION = (
    "counter target spell. if that spell is countered this way, put that card on your choice of the top "
    "or bottom of its owner's library instead of into that player's graveyard."
)


STACK_NEXT_TURN_DRAW_PAIR_TEMPLATE = "counter-next-turn-draw-pair-v1"
_NEXT_TURN_DRAW_PAIR = (
    "counter target spell. its controller may draw up to two cards at the beginning of the next turn's upkeep. "
    "you draw a card at the beginning of the next turn's upkeep."
)


def stack_counter_followup_template(text, *, compile_component):
    normalized = " ".join(text.casefold().replace("’", "'").split())
    if normalized == _COUNTER_NAME_SCAN:
        from ..replacement.immutable import thaw_value

        return ("counter-different-name-exile-cast-v1", (thaw_value(COUNTER_NAME_SCAN_EFFECT),),
                thaw_value(COUNTER_NAME_SCAN_TARGET), COUNTER_NAME_SCAN_MECHANICS)
    if normalized == _NEXT_TURN_DRAW_PAIR:
        effect, schema = next_turn_draw_pair_followup()
        return STACK_NEXT_TURN_DRAW_PAIR_TEMPLATE, (effect,), schema, (
            "counter", "cr-115-targets", "cr-121-drawing-a-card", "cr-603-handling-triggered-abilities",
        )
    if normalized == _LIBRARY_COUNTER_POSITION:
        effect, schema = library_counter_position_followup()
        return "fixed-counter-library-position-v1", (effect,), schema, ("counter", "cr-115-targets")
    if normalized == _SHARED_TYPE_HAND_CAST:
        effect, schema = shared_type_hand_cast_followup()
        return "fixed-stack-counter-shared-type-hand-cast-v1", (effect,), schema, ("counter", "cr-115-targets")
    mana_result = _MANA_VALUE_RESULTS.get(normalized)
    if mana_result is not None:
        effect, schema = stack_mana_value_followup(mana_result)
        mechanics = ("counter", "cr-115-targets")
        if mana_result is StackManaValueResult.DRAW:
            mechanics += ("cr-121-drawing-a-card",)
        return "fixed-stack-counter-mana-value-followup-v1", (effect,), schema, mechanics
    mode = _ACTIVATION_LOCK_FOLLOWUPS.get(normalized)
    if mode is not None:
        return (
            "fixed-countered-ability-activation-prohibition-v1",
            (countered_ability_activation_lock_effect(mode),),
            countered_ability_activation_lock_schema(mode), ("counter", "cr-115-targets"),
        )
    if normalized == _COUNTERED_ABILITY_SOURCE_LOSS:
        return ("fixed-countered-ability-source-loss-v1", (countered_ability_source_loss_effect(),),
                countered_ability_source_loss_schema(), ("counter", "cr-115-targets"))
    if text.lstrip()[:7].casefold() != "counter":
        return None
    prefix, separator, tail = " ".join(text.split()).partition(". ")
    if not separator or not prefix.casefold().startswith("counter target "):
        return None
    counter = targeted_counter_effect_template(prefix + ".")
    if counter is None or counter.counter_destination is not None:
        return None
    if tail.casefold() in _CHARACTERISTIC_FOLLOWUPS:
        return _characteristic_counter_template(counter, tail, compile_component)
    tail, second = _split_followups(tail)
    first = _first_followup(tail, compile_component=compile_component)
    if first is None or first[0] is None or len(first[1]) != 1:
        return None
    followups = [with_stack_counter_recipient(first[1][0], STACK_TARGET_CONTROLLER)]
    mechanics = (*counter.mechanics, *first[3])
    if second is not None:
        compiled = compile_component(second)
        if compiled[0] is None or compiled[2] is not None or len(compiled[1]) != 1:
            return None
        followups.append(compiled[1][0])
        mechanics = (*mechanics, *compiled[3])
    effects = ({"op": STACK_COUNTER_FOLLOWUP_OPERATION, "schema_version": 1,
                "stack": "$target.0", "followups": followups},)
    if not stack_counter_followup_node_capabilities(
        effects=effects, target_schema=counter.target_schema, mechanic_ids=mechanics,
    ):
        return None
    return "fixed-stack-counter-controller-followup-v1", effects, counter.target_schema, tuple(dict.fromkeys(mechanics))


def _first_followup(tail, *, compile_component):
    if tail.casefold().replace("’", "'") == "its controller can't cast spells this turn.":
        restriction = compile_component("Target player can't cast spells this turn.")
        return restriction if restriction[2] == STACK_FOLLOWUP_PLAYER_SCHEMA else None
    match = _FOLLOWUP.fullmatch(tail)
    if match is None:
        return None
    verb = match["verb"].casefold().removesuffix("s")
    body = f"{verb.capitalize()} {match['body']}"
    if verb == "discard":
        discard = fixed_controller_discard_effect_template(body)
        return discard.compiled() if discard is not None else None
    compiled = compile_component(body)
    return compiled if compiled[2] is None else None


def _split_followups(tail):
    loss = re.fullmatch(r"(?P<first>Its controller loses .+?) and (?P<second>you gain .+?\.)", tail, re.I)
    if loss is not None:
        return loss["first"] + ".", loss["second"]
    first, separator, second = tail.partition(". ")
    return (first + ".", second) if separator else (tail, None)
