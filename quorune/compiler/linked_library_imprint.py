"""Pair one private top-library imprint with its exact printed death result."""

import hashlib

from . import regex_working_set as re
from .draw_templates import trigger_ability_word_material_line

from ..linked_library_imprint_model import (
    LIBRARY_IMPRINT_MECHANIC, library_imprint_death_effects, library_imprint_selection_effect,
)


_ENTRY_BODY = re.compile(
    r"look at the top four cards of your library, exile one face down, "
    r"then put the rest on the bottom of your library in any order\.", re.IGNORECASE,
)
_DEATH_BODY = re.compile(
    r"turn the exiled card face up\. If it['’]s a creature card, "
    r"put it onto the battlefield under your control\.", re.IGNORECASE,
)


def linked_library_imprint_trigger_compiler(fallback, *, trigger_node, material_rows, card_name, match_source, permanent):
    if not permanent:
        return fallback
    pairs = {"enters": [], "dies": []}
    for _, text, span in material_rows:
        match = match_source(trigger_ability_word_material_line(text), card_name=card_name)
        if match is None:
            continue
        event = match["event"].casefold()
        pattern = {"enters": _ENTRY_BODY, "dies": _DEATH_BODY}.get(event)
        if pattern is not None and pattern.fullmatch(match["body"]):
            pairs[event].append(span.line)
    if len(pairs["enters"]) != 1 or len(pairs["dies"]) != 1:
        return fallback
    templates = {
        pairs["enters"][0]: (
            "linked-library-imprint-selection-v1", (library_imprint_selection_effect(),), None,
            (LIBRARY_IMPRINT_MECHANIC, "fixed-library-selection"),
        ),
        pairs["dies"][0]: (
            "linked-library-imprint-death-v1", library_imprint_death_effects(), None,
            (LIBRARY_IMPRINT_MECHANIC,),
        ),
    }

    def compile_trigger(**values):
        compiled = templates.get(values["span"].line)
        if compiled is None:
            return fallback(**values)
        return trigger_node(**values, effect_template=lambda _body, **_source: compiled)

    return compile_trigger


_ARTIFACT_SEARCH_BODY = re.compile(r"you may search your library for an artifact card, exile it, then shuffle\.", re.I)
_ARTIFACT_RETURN_BODY = re.compile(r"you may put the exiled card into its owner['’]s hand\.", re.I)


def linked_artifact_library_trigger_compiler(fallback, *, trigger_node, material_rows, card_name, match_source, permanent):
    if not permanent:
        return fallback
    found = {"enters": [], "dies": []}
    patterns = {"enters": _ARTIFACT_SEARCH_BODY, "dies": _ARTIFACT_RETURN_BODY}
    for _, text, span in material_rows:
        match = match_source(trigger_ability_word_material_line(text), card_name=card_name)
        if match is None:
            continue
        event = match["event"].casefold()
        if event in patterns and patterns[event].fullmatch(match["body"]):
            found[event].append(span.line)
    if len(found["enters"]) != 1 or len(found["dies"]) != 1:
        return fallback
    from ..linked_library_imprint_model import (
        LINKED_ARTIFACT_PAIR_PREFIX, linked_artifact_library_instruction,
    )

    seed = f"{card_name}:{found['enters'][0]}:{found['dies'][0]}"
    pair = LINKED_ARTIFACT_PAIR_PREFIX + hashlib.sha256(seed.encode("utf-8")).hexdigest()[:24]
    lines = {found["enters"][0]: False, found["dies"][0]: True}

    def compile_trigger(**values):
        line = values["span"].line
        if line not in lines:
            return fallback(**values)
        returning = lines[line]
        wrapper = {"op": "offer_optional_effect", "player": "$controller",
                   "effects": [linked_artifact_library_instruction(pair, returning=returning)]}
        template = "linked-artifact-owner-hand-v1" if returning else "linked-artifact-library-exile-v1"
        mechanics = ("fixed-optional-effect-choice",)
        return trigger_node(**values, effect_template=lambda _body, **_source: (template, (wrapper,), None, mechanics))

    return compile_trigger
