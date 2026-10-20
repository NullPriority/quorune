from __future__ import annotations

"""Closed typed protection branches through the existing ability bundle owner."""

from collections.abc import Mapping

from ..ability_fragments import ProtectionSpec, ability_fragment_from_dict
from ..compiler.source_protection_choices import CAPABILITY, COLORS, MECHANIC
from .temporary_ability_grant import decode_temporary_ability_grant


def source_protection_choice_capabilities(*, effects, target_schema, mechanic_ids):
    if target_schema is not None or not {MECHANIC, "protection", "cr-611-continuous-effects"} <= set(mechanic_ids) or len(effects) != 1:
        return ()
    choice = effects[0]
    if (not isinstance(choice, Mapping) or set(choice) != {"op", "player", "prompt", "options", "then_by_choice"}
            or choice["op"] != "choose_option" or choice["player"] != "$controller"
            or choice["prompt"] != "Choose a protection color."):
        return ()
    options = choice["options"]
    if not isinstance(options, (list, tuple)) or list(options) != [{"id": symbol, "label": word} for symbol, word in COLORS]:
        return ()
    branches = choice["then_by_choice"]
    if not isinstance(branches, Mapping) or set(branches) != {symbol for symbol, _ in COLORS}:
        return ()
    for symbol, branch in branches.items():
        if not isinstance(branch, (list, tuple)) or len(branch) != 1 or not isinstance(branch[0], Mapping):
            return ()
        grant = branch[0]
        try:
            keywords, fragments = decode_temporary_ability_grant(grant)
            if (keywords or len(fragments) != 1 or grant["card"] != "$source.zone_object"
                    or grant["exclude_source"] or grant["printed_only"]):
                return ()
            fragment = ability_fragment_from_dict(fragments[0])
        except (TypeError, ValueError, KeyError):
            return ()
        if not isinstance(fragment, ProtectionSpec) or fragment.quality != symbol:
            return ()
    return tuple(sorted((CAPABILITY, "protection.typed.debt",
        "continuous.resolution.fixed_characteristics_until_end_of_turn")))


def source_protection_choice_runtime_dependencies(program):
    marked = MECHANIC in program.coverage
    for effect in program.effects:
        branches = effect.get("then_by_choice")
        if effect.get("op") != "choose_option" or not isinstance(branches, Mapping):
            continue
        marked = marked or any(isinstance(branch, (list, tuple)) and any(
            isinstance(leaf, Mapping) and leaf.get("op") == "apply_source_characteristics_until_end_of_turn"
            and leaf.get("schema_version") == 7 and leaf.get("card") == "$source.zone_object"
            and isinstance(leaf.get("ability_fragments"), (list, tuple))
            and any(isinstance(fragment, Mapping) and fragment.get("kind") == "protection"
                for fragment in leaf["ability_fragments"])
            for leaf in branch) for branch in branches.values())
    if not marked:
        return ()
    required = source_protection_choice_capabilities(effects=program.effects,
        target_schema=program.target_schema, mechanic_ids=program.coverage)
    if not required:
        raise ValueError("Source color protection requires its complete choice and source authority")
    return required


def is_closed_source_protection_choice_program(program):
    required = set(source_protection_choice_capabilities(effects=program.effects,
        target_schema=program.target_schema, mechanic_ids=program.coverage))
    return bool(required) and required <= set(program.capability_dependencies)
