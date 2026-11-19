"""Compose one fixed token instruction and its ordinary reflexive target result."""

from . import regex_working_set as re
from ..reflexive_trigger_model import (
    BOUND_REFLEXIVE_PROGRAM_VERSION, TOKEN_CREATION_REFLEXIVE_FIELD,
    REFLEXIVE_PAYMENT_MECHANIC, ReflexiveTriggerProgram,
)
from ..rules.token_creation_reflexive import token_creation_reflexive_node_capabilities


_REFLEXIVE_CREATION = re.compile(r"(Create [^.]+\.) When you do, ([^.]+\.)", re.I)


def token_creation_reflexive_template(text, *, compile_effect):
    match = _REFLEXIVE_CREATION.fullmatch(text.strip())
    if match is None:
        return None
    parent, effects, targets, mechanics = compile_effect(match[1])
    if parent is None or len(effects) != 1 or effects[0].get("op") != "create_token" or targets is not None:
        return None
    child, child_effects, child_targets, child_mechanics = compile_effect(match[2])
    if child is None or child_targets is None:
        return None
    try:
        consequence = ReflexiveTriggerProgram(child_effects, child_targets, child_mechanics,
            schema_version=BOUND_REFLEXIVE_PROGRAM_VERSION)
        effect = {**effects[0], TOKEN_CREATION_REFLEXIVE_FIELD: consequence.to_dict()}
        coverage = (*mechanics, REFLEXIVE_PAYMENT_MECHANIC)
        owned = token_creation_reflexive_node_capabilities(
            effects=(effect,), target_schema=None, mechanic_ids=coverage,
        )
    except (KeyError, TypeError, ValueError):
        return None
    return ("fixed-token-creation-reflexive-v1", (effect,), None, coverage) if owned else None
