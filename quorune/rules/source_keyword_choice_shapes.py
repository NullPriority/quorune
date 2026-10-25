from __future__ import annotations

"""Closed choice composition over the existing source-characteristic owner."""

from collections.abc import Mapping

from ..compiler.source_keyword_choices import CAPABILITY, MECHANIC
from ..keyword_abilities import FIXED_CHARACTERISTIC_KEYWORD_CAPABILITIES
from ..keyword_counters import keyword_counter_mechanic
from .node_capability_shapes import fixed_source_characteristics_node_capabilities


def source_keyword_choice_capabilities(*, effects, target_schema, mechanic_ids):
    mechanics = set(mechanic_ids)
    if target_schema is not None or MECHANIC not in mechanics or not 1 <= len(effects) <= 2:
        return ()
    choice = effects[-1]
    if not isinstance(choice, Mapping) or set(choice) != {"op", "player", "prompt", "options", "then_by_choice"}:
        return ()
    if choice["op"] != "choose_option" or choice["player"] != "$controller" or choice["prompt"] != "Choose a temporary keyword.":
        return ()
    options, branches = choice["options"], choice["then_by_choice"]
    if not isinstance(options, (list, tuple)) or len(options) < 2 or not isinstance(branches, Mapping):
        return ()
    words = []
    for option in options:
        if not isinstance(option, Mapping) or set(option) != {"id", "label"} or option["id"] != option["label"]:
            return ()
        word = option["id"]
        if type(word) is not str or word not in FIXED_CHARACTERISTIC_KEYWORD_CAPABILITIES:
            return ()
        words.append(word)
    if len(set(words)) != len(words) or set(words) != set(branches):
        return ()
    dependencies = {CAPABILITY}
    for word, branch in branches.items():
        if (not isinstance(branch, (list, tuple)) or len(branch) != 1
                or not isinstance(branch[0], Mapping) or branch[0].get("keywords") not in ([word], (word,))
                or branch[0].get("power") != 0 or branch[0].get("toughness") != 0
                or any(branch[0].get(key) is not None for key in ("set_card_types", "set_subtypes", "set_colors", "base_power", "base_toughness"))
                or (keyword_counter_mechanic(word) or word.casefold()) not in mechanics):
            return ()
        owned = fixed_source_characteristics_node_capabilities(effects=branch, target_schema=None, mechanic_ids=mechanics)
        if not owned:
            return ()
        dependencies.update(owned)
        dependencies.update(FIXED_CHARACTERISTIC_KEYWORD_CAPABILITIES[word])
    if len(effects) == 2:
        prefix = effects[0]
        if (not isinstance(prefix, Mapping) or prefix.get("keywords") not in ([], ())
                or any(prefix.get(key) is not None for key in ("set_card_types", "set_subtypes", "set_colors", "base_power", "base_toughness"))):
            return ()
        owned = fixed_source_characteristics_node_capabilities(effects=(prefix,), target_schema=None, mechanic_ids=mechanics)
        if not owned:
            return ()
        dependencies.update(owned)
    return tuple(sorted(dependencies))


def source_keyword_choice_runtime_dependencies(program):
    marked = MECHANIC in program.coverage
    for effect in program.effects:
        branches = effect.get("then_by_choice")
        if effect.get("op") != "choose_option" or not isinstance(branches, Mapping):
            continue
        marked = marked or any(isinstance(branch, (list, tuple)) and any(
            isinstance(leaf, Mapping) and leaf.get("card") == "$source.zone_object"
            and leaf.get("op") == "apply_source_characteristics_until_end_of_turn"
            and "schema_version" not in leaf
            for leaf in branch) for branch in branches.values())
    if not marked:
        return ()
    dependencies = source_keyword_choice_capabilities(effects=program.effects,
        target_schema=program.target_schema, mechanic_ids=program.coverage)
    if not dependencies:
        raise ValueError("Source keyword choice lacks complete source and branch authority")
    return dependencies


def is_closed_source_keyword_choice_program(program):
    required = set(source_keyword_choice_capabilities(effects=program.effects,
        target_schema=program.target_schema, mechanic_ids=program.coverage))
    return bool(required) and required <= set(program.capability_dependencies)
