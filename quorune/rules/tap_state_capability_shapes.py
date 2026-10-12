from __future__ import annotations

"""Closed CardProgram capability shapes for single-object tap state."""

from typing import Iterable, Mapping, Sequence

from ..attachment_references import (
    AttachmentReferenceError,
    AttachmentReferenceSpec,
)
from ..compiler.fixed_source_effect_sequences import SOURCE_ZONE_OBJECT
from .permanent_predicate_capability_shapes import (
    direct_permanent_target_schema_is_closed,
    direct_target_predicate_capabilities,
)


def _operation_capability(value: object) -> str | None:
    return {
        "tap": "permanent.tap.effect",
        "untap": "permanent.untap.effect",
        "prohibit_next_untap": "untap.step.prohibit_next_controller",
    }.get(value)


def targeted_tap_state_node_capabilities(
    *,
    effects: Sequence[Mapping[str, object]],
    target_schema: Mapping[str, object] | None,
    mechanic_ids: Iterable[str],
) -> tuple[str, ...]:
    """Return ownership only for closed single-object tap-state shapes."""

    mechanics = {str(value).casefold() for value in mechanic_ids}
    if 'tap-and-untap' in mechanics and len(effects) == 2 and target_schema is not None:
        if (list(effects) != [{'op': 'tap', 'card': '$target.0'}, {'op': 'prohibit_next_untap', 'card': '$target.0'}]
                or 'cr-115-targets' not in mechanics or not direct_permanent_target_schema_is_closed(target_schema)):
            return ()
        return ('permanent.tap.effect', 'untap.step.prohibit_next_controller',
                *direct_target_predicate_capabilities(target_schema), 'target.revalidate_resolution')
    if "tap-and-untap" not in mechanics or len(effects) != 1:
        return ()
    effect = effects[0]
    operation = effect.get("op")

    if target_schema is None:
        capability = _operation_capability(operation)
        if capability is None or set(effect) != {"op", "card"}:
            return ()
        reference = effect.get("card")
        if reference == SOURCE_ZONE_OBJECT:
            return (capability,)
        if not isinstance(reference, Mapping):
            return ()
        try:
            AttachmentReferenceSpec.from_dict(reference)
        except (AttachmentReferenceError, TypeError):
            return ()
        return (capability, "attachment.reference.current_or_lki")

    if (
        "cr-115-targets" not in mechanics
        or not direct_permanent_target_schema_is_closed(target_schema)
    ):
        return ()
    target_capabilities = direct_target_predicate_capabilities(target_schema)
    capability = _operation_capability(operation)
    if (
        capability is not None
        and set(effect) == {"op", "card"}
        and effect.get("card") == "$target.0"
    ):
        return (
            capability,
            *target_capabilities,
            "target.revalidate_resolution",
        )

    if set(effect) != {
        "op",
        "player",
        "prompt",
        "options",
        "then_by_choice",
    } or operation != "choose_option":
        return ()
    options = effect.get("options")
    then_by_choice = effect.get("then_by_choice")
    choice_rows = (
        {key: list(value) for key, value in then_by_choice.items()}
        if isinstance(then_by_choice, Mapping)
        and all(
            isinstance(value, (list, tuple))
            for value in then_by_choice.values()
        )
        else None
    )
    if (
        effect.get("player") != "$controller"
        or effect.get("prompt")
        != "Tap, untap, or leave the target unchanged."
        or not isinstance(options, (list, tuple))
        or list(options)
        != [
            {"id": "tap", "label": "Tap"},
            {"id": "untap", "label": "Untap"},
            {"id": "decline", "label": "Leave unchanged"},
        ]
        or choice_rows is None
        or set(choice_rows) != {"tap", "untap", "decline"}
        or choice_rows["tap"]
        != [{"op": "tap", "card": "$target.0"}]
        or choice_rows["untap"]
        != [{"op": "untap", "card": "$target.0"}]
        or choice_rows["decline"] != []
    ):
        return ()
    return (
        "permanent.tap.effect",
        "permanent.tap_state.optional_choice",
        "permanent.untap.effect",
        *target_capabilities,
        "target.revalidate_resolution",
    )


__all__ = ["targeted_tap_state_node_capabilities"]
