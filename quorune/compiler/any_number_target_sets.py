from __future__ import annotations

"""Unrestricted cardinality over complete existing homogeneous result owners."""

import re
from copy import deepcopy

from ..target_cardinality import (
    AnnouncedTargetCardinality, SELECTED_PLAYER_RESULT_LAYOUTS,
    SELECTED_PLAYER_RESULT_MECHANIC, selected_target_count_descriptor,
)
from .fixed_homogeneous_target_sets import fixed_homogeneous_target_set_effect_template
from .target_characteristic_sets import target_characteristic_set_template

ANY_NUMBER_TARGET_MECHANIC = "any-number-target-set"


def any_number_target_set_template(text):
    if len(re.findall(r"\bany number of (?:other )?target\b", text, re.I)) != 1:
        return None
    prototype = re.sub(r"\bany number of (?P<other>other )?target\b",
        lambda match: "up to one " + (match["other"] or "") + "target", text, flags=re.I)
    template = fixed_homogeneous_target_set_effect_template(prototype)
    compiled = template.compiled() if template is not None else target_characteristic_set_template(prototype)
    if compiled is None or len(compiled[1]) != 1:
        return None
    effect = deepcopy(dict(compiled[1][0]))
    schema = deepcopy(dict(compiled[2]))
    if schema.pop("up_to", None) != 1 or effect.get("maximum_targets") != 1:
        return None
    schema["count_parameter"] = AnnouncedTargetCardinality(
        selection="up_to", parameter="any_number", schema_version=2).to_dict()
    effect.update(maximum_targets=selected_target_count_descriptor(), announced_target_count=True)
    return (compiled[0] + "-any-number-v1", (effect,), schema,
        tuple(dict.fromkeys((*compiled[3], ANY_NUMBER_TARGET_MECHANIC))))


_PLAYER_RESULT = re.compile(
    r"(?:(?P<prefix>Destroy all creatures), then )?Any number of target "
    r"(?P<subject>players|opponents) each (?P<body>.+?)\.?",
    re.IGNORECASE,
)


def _selected_player_leaf(clause, compile_independent):
    verbs = {"mill": "mills", "gain": "gains", "lose": "loses", "discard": "discards"}
    verb, _, rest = clause.partition(" ")
    plural = verbs.get(verb.casefold())
    if plural is None:
        return None
    compiled = compile_independent(f"Target player {plural} {rest.rstrip('.')}.")
    if compiled[0] is None or len(compiled[1]) != 1 or (compiled[2] or {}).get("count") != 1:
        return None
    effect = dict(compiled[1][0])
    if effect.get("op") in {"mill", "life", "lose_life"}:
        if effect.pop("player", None) != "$target.0":
            return None
    elif effect.get("op") == "choose_cards_apnap":
        if effect.pop("target", None) != "$target.0" or effect.get("players") != ["$target.0"]:
            return None
    elif effect.get("op") != "discard_whole_hands" or effect.get("players") != ["$target.0"]:
        return None
    return {**effect, "players": "$targets"}, compiled[3]


def any_number_player_result_template(text, *, compile_independent):
    match = _PLAYER_RESULT.fullmatch(" ".join(text.strip().split()))
    if match is None:
        return None
    body = match["body"]
    effects = []
    mechanics = [ANY_NUMBER_TARGET_MECHANIC, SELECTED_PLAYER_RESULT_MECHANIC, "cr-115-targets"]
    if match["prefix"]:
        prefix = compile_independent(match["prefix"] + ".")
        if prefix[0] is None or prefix[2] is not None or len(prefix[1]) != 1:
            return None
        effects.extend(prefix[1])
        mechanics.extend(prefix[3])
    mill_loss = re.fullmatch(r"(mill .+?) and (lose .+)", body, re.IGNORECASE)
    discard_draw = re.fullmatch(r"(discard their hands), then (draw .+)", body, re.IGNORECASE)
    clauses = mill_loss.groups() if mill_loss else (discard_draw[1],) if discard_draw else (body,)
    for clause in clauses:
        selected = _selected_player_leaf(clause, compile_independent)
        if selected is None:
            return None
        effect, covered = selected
        effects.append(effect)
        mechanics.extend(covered)
    if discard_draw:
        from .draw_templates import fixed_draw_effect_template
        draw = fixed_draw_effect_template("Each player " + re.sub(r"^draw ", "draws ", discard_draw[2], flags=re.I) + ".")
        if draw is None or draw[2] is not None or len(draw[1]) != 1 or draw[1][0].get("op") != "draw_each_player":
            return None
        effects.append({**draw[1][0], "players": "$targets"})
        mechanics.extend(draw[3])
    operations = tuple(effect["op"] for effect in effects)
    if operations not in SELECTED_PLAYER_RESULT_LAYOUTS:
        return None
    schema = {"zones": ["player"], "categories": ["player"],
              "player_relation": "opponent" if match["subject"].casefold() == "opponents" else "any",
              "count_parameter": AnnouncedTargetCardinality(
                  selection="up_to", parameter="any_number", schema_version=2).to_dict()}
    return "selected-player-result-set-v1", tuple(effects), schema, tuple(dict.fromkeys(mechanics))
