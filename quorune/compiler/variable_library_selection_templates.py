from __future__ import annotations


"""Resolve only the inspection count; selection semantics keep their owner."""

from . import regex_working_set as re

from ..variable_library_selection_model import (ALL_LOOKED,VARIABLE_LIBRARY_SELECTION_OPERATION,
    VARIABLE_LIBRARY_SELECTION_MECHANIC, domain_top_selection_effect, validate_variable_library_selection,
    actual_mana_spent_library_selection_effect, variable_up_to_two_hand_selection_effect,
    DEVOTION_LIBRARY_WIN_TEMPLATE, devotion_library_win_effect,
)
from .declared_effect_amounts import declared_effect_amount_template
from .scalar_effect_amounts import scalar_effect_amount_template
from .fixed_library_selection_templates import fixed_library_selection_effect_template
from .fixed_numbers import FIXED_COUNT_PATTERN,fixed_number
from .oracle_source_text import without_ability_word_label
from ..spell_mana_conditions import actual_cast_mana_total_reference


_DEVOTION_LIBRARY_WIN = (
    "look at the top x cards of your library, where x is your devotion to blue. "
    "put up to one of them on top of your library and the rest on the bottom of your library in a random order. "
    "if x is greater than or equal to the number of cards in your library, you win the game."
)


def devotion_library_win_template(body):
    """Recognize the complete effect for the unqualified source-entry owner."""
    if " ".join(body.split()).casefold() != _DEVOTION_LIBRARY_WIN:
        return None
    return DEVOTION_LIBRARY_WIN_TEMPLATE, (devotion_library_win_effect(),), None, (VARIABLE_LIBRARY_SELECTION_MECHANIC,)


_HEADER=re.compile(r'(?P<header>(?:Look at|Reveal) the top X cards of your library)'
    r'(?P<definition>, where X is (?:equal to )?[^.]+)?\.\s*(?P<tail>.+)',re.I)


_PRIVATE_INSPECTION_COUNT = re.compile(
    r"Look at the top (?P<count>[0-9]+) cards of your library\.?", re.I,
)
_DOMAIN_TOP_TAILS = frozenset({
    "you may put one of those cards on top of your library. "
    "put the rest on the bottom of your library in a random order.",
    "put up to one of them on top of your library and the rest on the bottom of your library in a random order.",
    "put up to one of them on top of your library and the rest on the bottom in a random order.",
})


def _domain_top_header(body):
    numeric = _PRIVATE_INSPECTION_COUNT.fullmatch(body)
    if numeric is None:
        return None, (), None, ()
    effect = domain_top_selection_effect(int(numeric["count"]))
    validate_variable_library_selection(effect)
    return "domain-private-top-selection-v2", (effect,), None, (VARIABLE_LIBRARY_SELECTION_MECHANIC,)


_DOMAIN_COUNT_CLAUSE = ", where x is the number of basic land types among lands you control"
_UP_TO_TWO_IN_HAND = "put up to two of them into your hand and the rest "
_PRIVATE_GRAVEYARD_REMAINDER = _UP_TO_TWO_IN_HAND + "into your graveyard."
_PRIVATE_RANDOM_BOTTOM_REMAINDER = _UP_TO_TWO_IN_HAND + "on the bottom of your library in a random order."


def variable_up_to_two_hand_selection_template(text, *, source_name, cast_x_available=False, forbid_public_x=False):
    material = without_ability_word_label(" ".join(text.strip().split()))
    match = _HEADER.fullmatch(material) if material is not None else None
    if match is None or match["header"].casefold() != "look at the top x cards of your library":
        return None
    definition = match["definition"] or ""
    tail = match["tail"].casefold()
    if definition.casefold() == _DOMAIN_COUNT_CLAUSE and tail == _PRIVATE_GRAVEYARD_REMAINDER:
        destination = "graveyard"
    elif cast_x_available and not definition and tail == _PRIVATE_RANDOM_BOTTOM_REMAINDER:
        destination = "library_bottom"
    else:
        return None

    def header(body):
        numeric = _PRIVATE_INSPECTION_COUNT.fullmatch(body)
        if numeric is None:
            return None, (), None, ()
        effect = variable_up_to_two_hand_selection_effect(int(numeric["count"]), remainder_destination=destination)
        validate_variable_library_selection(effect)
        return "variable-private-up-to-two-hand-v3", (effect,), None, (VARIABLE_LIBRARY_SELECTION_MECHANIC,)

    return declared_effect_amount_template(
        match["header"] + definition + ".", source_name=source_name, compile_fixed=header,
        cast_x_available=cast_x_available, forbid_public_x=forbid_public_x,
    )


def actual_mana_spent_library_spell_compiler(compile_effect):
    oracle = ("look at the top x cards of your library, where x is the amount of mana spent to cast this spell. "
              "put two of them into your hand and the rest on the bottom of your library in a random order.")

    def compile_spell_effect(text, **context):
        if " ".join(text.casefold().split()) == oracle:
            effect = actual_mana_spent_library_selection_effect(actual_cast_mana_total_reference())
            return "actual-mana-spent-library-selection-v4", (effect,), None, (VARIABLE_LIBRARY_SELECTION_MECHANIC,)
        return compile_effect(text, **context)

    return compile_spell_effect


def variable_library_selection_effect_template(text,*,source_name,cast_x_available=False,
        forbid_public_x=False,**context):
    material=without_ability_word_label(' '.join(text.strip().split()))
    match=_HEADER.fullmatch(material) if material is not None else None
    if match is None:return None
    if hand := variable_up_to_two_hand_selection_template(
        text, source_name=source_name, cast_x_available=cast_x_available, forbid_public_x=forbid_public_x,
    ):
        return hand
    if (
        match["header"].casefold() == "look at the top x cards of your library"
        and (match["definition"] or "").casefold() == _DOMAIN_COUNT_CLAUSE
        and match["tail"].casefold() in _DOMAIN_TOP_TAILS
    ):
        return declared_effect_amount_template(
            match["header"] + match["definition"] + ".", source_name=source_name,
            compile_fixed=_domain_top_header, forbid_public_x=forbid_public_x,
        )
    # A probe larger than every printed numeric literal cannot collide with a
    # printed selection maximum. It is never emitted or used for inspection.
    probe=max([2,*(fixed_number(number[0]) for number in re.finditer(
        rf'\b(?:{FIXED_COUNT_PATTERN})\b',match['tail'],re.I))])+1
    fixed=fixed_library_selection_effect_template(re.sub(r'\bX\b',str(probe),match['header'])+'. '+match['tail'])
    if fixed is None or fixed.selected_destination!='hand':return None
    selection=fixed.compiled()[1][0]
    maximum=ALL_LOOKED if fixed.maximum==probe else fixed.maximum
    def header(body):
        numeric=re.fullmatch(r'(?:Look at|Reveal) the top (?P<count>[0-9]+) cards of your library\.?',body,re.I)
        if numeric is None:return None,(),None,()
        effect={**selection,'op':VARIABLE_LIBRARY_SELECTION_OPERATION,'schema_version':1,
            'look_count':int(numeric['count']),'maximum':maximum}
        try:validate_variable_library_selection(effect)
        except (TypeError,ValueError,KeyError):return None,(),None,()
        return 'variable-library-selection-v1',(effect,),None,(VARIABLE_LIBRARY_SELECTION_MECHANIC,)
    amount_source=match['header']+(match['definition'] or '')+'.'
    return declared_effect_amount_template(amount_source,source_name=source_name,compile_fixed=header,
        cast_x_available=cast_x_available,forbid_public_x=forbid_public_x) or scalar_effect_amount_template(
            amount_source,source_name=source_name,compile_fixed=header,
            event=context.get('event'),source_event=context.get('source_event',False))
