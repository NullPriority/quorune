"""Closed static targeting requirements in supported Oracle text."""

from ..semantic_runtime.target_requirements import (
    FLAGBEARER_TARGET_TEMPLATE,
    FlagbearerTargetRequirementHandler,
)


def flagbearer_target_requirement_template(text: str):
    material = text.strip().removesuffix(".").casefold()
    if material != (
        "while an opponent is choosing targets as part of casting a spell they control or activating "
        "an ability they control, that player must choose at least one flagbearer on the battlefield if able"
    ):
        return None
    handler = FlagbearerTargetRequirementHandler()
    return FLAGBEARER_TARGET_TEMPLATE, handler.descriptor(), handler.capability_dependencies
