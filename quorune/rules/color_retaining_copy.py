"""Retain an object's original colors while copying another model."""

from __future__ import annotations

from dataclasses import replace

from ..token_copy_recipes import TokenCopyExceptionSpec


def retain_copiable_colors(source_values, exception: TokenCopyExceptionSpec) -> TokenCopyExceptionSpec:
    """Read the receiving object's layer-one palette before applying the copy."""
    if not isinstance(exception, TokenCopyExceptionSpec) or exception.colors is not None:
        raise ValueError("Retained colors require a copy without a separate color exception")
    palette = source_values.get("colors")
    if (not isinstance(palette, (tuple, list)) or any(type(color) is not str or color not in "WUBRG"
                                                      for color in palette)
            or len(set(palette)) != len(palette)):
        raise ValueError("A copy that retains color requires a canonical source palette")
    return replace(exception, colors=tuple(color for color in "WUBRG" if color in palette))


def apply_color_retaining_copy(source_values, model_values, exception: TokenCopyExceptionSpec) -> dict:
    """Apply the retained palette and omit the model's color-defining ability."""
    from ..token_copy_recipes import apply_copy_exception

    result = apply_copy_exception(model_values, retain_copiable_colors(source_values, exception))
    result["ability_fragments"] = [
        fragment for fragment in result["ability_fragments"]
        if fragment.get("kind") != "all_colors_characteristic_definition"
    ]
    return result


RETAINED_COLOR_ENTRY_HANDLER = "replacement.zone.entry-copy.v14"
RETAINED_COLOR_COPY_CAPABILITY = "zone.entry.copy_retained_color_and_upkeep"
RETAINED_COLOR_UPKEEP_TEMPLATE = "retained-color-upkeep-copy-v1"
RETAINED_COLOR_UPKEEP_LABEL = "Retain this creature's color and copy the target"
RETAINED_COLOR_COPY_VERSION = "retained_color_upkeep_copy_version"


def retained_color_upkeep_instruction():
    return {
        "op": "copy_until_end_of_turn", RETAINED_COLOR_COPY_VERSION: 1,
        "source": "$source.zone_object", "target": "$target.0",
    }


def retained_color_upkeep_effect():
    return {"op": "offer_optional_effect", "player": "$controller",
            "effects": [retained_color_upkeep_instruction()]}


def retained_color_upkeep_target():
    return {"zones": ["battlefield"], "categories": ["permanent"], "types_any": ["creature"], "count": 1}


def retained_color_upkeep_condition():
    from ..replacement.face_status_copy import upkeep_refacing_condition

    return upkeep_refacing_condition()


def is_retained_color_upkeep_copy(effect):
    from collections.abc import Mapping

    return isinstance(effect, Mapping) and RETAINED_COLOR_COPY_VERSION in effect


def retained_color_copy_component_capabilities(*, effects, target_schema, mechanic_ids):
    from ..replacement.immutable import thaw_value

    if ("cr-115-targets" in mechanic_ids
            and thaw_value(target_schema) == retained_color_upkeep_target()
            and thaw_value(effects) == [retained_color_upkeep_instruction()]):
        return (RETAINED_COLOR_COPY_CAPABILITY, "target.revalidate_resolution")
    return ()


def is_closed_retained_color_upkeep_program(program):
    from ..replacement.immutable import thaw_value

    return (program.provenance.get("granted_only") is True
            and program.provenance.get("template_id") == RETAINED_COLOR_UPKEEP_TEMPLATE
            and program.event == "step.begin" and program.active_zone == "battlefield"
            and program.cost_schema is None and thaw_value(program.target_schema) == retained_color_upkeep_target()
            and thaw_value(program.event_condition) == retained_color_upkeep_condition()
            and thaw_value(program.effects) == [retained_color_upkeep_effect()]
            and {RETAINED_COLOR_COPY_CAPABILITY, "effect.choice.optional_fixed", "target.revalidate_resolution"}
                <= set(program.capability_dependencies))


def retained_color_flip_values(host, target, *, entering, exception):
    """Capture both printed or already retained flip faces for this copy only."""
    from .copied_flip_faces import CopiedFlipFaces, capture_copied_flip_faces, current_flipped_status
    from ..replacement.immutable import thaw_value

    faces = capture_copied_flip_faces(host, target)
    if faces is None:
        return None
    palette = host._copyable_characteristics(entering)
    copied = CopiedFlipFaces(
        apply_color_retaining_copy(palette, thaw_value(faces.normal), exception),
        apply_color_retaining_copy(palette, thaw_value(faces.alternative), exception),
    )
    return copied.current_values(flipped=current_flipped_status(host, entering))
