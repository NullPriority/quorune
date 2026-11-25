"""Target roles for a persistent copy between two public permanents."""

from __future__ import annotations

from collections.abc import Mapping

from ..compiler.direct_target import DirectPermanentTargetSpec
from ..replacement.immutable import thaw_value


PERSISTENT_PUBLIC_COPY_VERSION = "persistent_public_copy_version"
PERSISTENT_PUBLIC_COPY_TEMPLATE = "persistent-public-copy-v1"
PERSISTENT_PUBLIC_COPY_CAPABILITY = "continuous.resolution.persistent_public_copy"
PERSISTENT_PUBLIC_COPY_DEPENDENCIES = (PERSISTENT_PUBLIC_COPY_CAPABILITY, "target.revalidate_resolution")


def persistent_public_copy_target_schema() -> dict:
    permanent = DirectPermanentTargetSpec(types_any=("artifact", "creature")).to_target_schema()
    return {
        "groups": [{"id": "recipient", **permanent}, {"id": "model", **permanent}],
        "globally_distinct": True,
    }


def persistent_public_copy_effect() -> dict:
    return {
        "op": "copy_until_end_of_turn", PERSISTENT_PUBLIC_COPY_VERSION: 1,
        "recipient": "$target.0", "model": "$target.1",
    }


def is_persistent_public_copy_effect(effect) -> bool:
    return isinstance(effect, Mapping) and PERSISTENT_PUBLIC_COPY_VERSION in effect


def validate_persistent_public_copy_effect(effect, *, resolved=False) -> None:
    expected = persistent_public_copy_effect()
    if (not is_persistent_public_copy_effect(effect) or type(effect[PERSISTENT_PUBLIC_COPY_VERSION]) is not int
            or effect[PERSISTENT_PUBLIC_COPY_VERSION] != 1):
        raise ValueError("Persistent public copy requires version one")
    if not resolved:
        if dict(effect) != expected:
            raise ValueError("Persistent public copy requires its ordered target bindings")
        return
    if (set(effect) - {"reason", "_runtime_source"} != set(expected) or effect["op"] != expected["op"]
            or any(effect[key] is not None and (type(effect[key]) is not str or not effect[key])
                   for key in ("recipient", "model"))):
        raise ValueError("Persistent public copy requires its two revalidated target roles")
    if effect["recipient"] is not None and effect["recipient"] == effect["model"]:
        raise ValueError("Persistent public copy requires different permanents")


def persistent_public_copy_node_capabilities(*, effects, target_schema, mechanic_ids):
    if (len(effects) != 1 or "cr-115-targets" not in mechanic_ids
            or thaw_value(target_schema) != persistent_public_copy_target_schema()):
        return ()
    try:
        validate_persistent_public_copy_effect(effects[0])
    except ValueError:
        return ()
    return PERSISTENT_PUBLIC_COPY_DEPENDENCIES


def persistent_public_copy_runtime_dependencies(program):
    if not any(is_persistent_public_copy_effect(effect) for effect in program.effects):
        return ()
    required = persistent_public_copy_node_capabilities(
        effects=program.effects, target_schema=program.target_schema, mechanic_ids=("cr-115-targets",),
    )
    if (not required or program.event != "resolve" or not program.ability_id.startswith("spell:")):
        raise ValueError("Persistent public copy requires its complete resolving spell")
    return required


def is_closed_persistent_public_copy_program(program) -> bool:
    try:
        required = persistent_public_copy_runtime_dependencies(program)
    except (KeyError, TypeError, ValueError):
        return False
    return bool(required) and set(required).issubset(program.capability_dependencies)


def persistent_public_copy_values(host, model, *, recipient, exception=None) -> dict:
    from ..card_program_faces import selected_face_id
    from ..defined_token_faces import single_face_copy_values
    from ..entry_copy import COPIABLE_FACE_FIELD, COPIABLE_ORACLE_FIELD, copiable_oracle_id
    from ..errors import GameRuleError
    from ..face_down_copy import current_face_down_copy_values
    from ..token_copy_recipes import apply_copy_exception
    from .copied_flip_faces import CopiedFlipFaces, capture_copied_flip_faces, current_flipped_status

    if model.face_down:
        values = current_face_down_copy_values(host, model)
        if values is None:
            raise GameRuleError("Persistent copy of a face-down model requires represented public status")
    else:
        faces = capture_copied_flip_faces(host, model)
        if faces is not None:
            if exception is not None:
                faces = CopiedFlipFaces(
                    apply_copy_exception(thaw_value(faces.normal), exception),
                    apply_copy_exception(thaw_value(faces.alternative), exception),
                )
            return faces.current_values(flipped=current_flipped_status(host, recipient))
        values = single_face_copy_values(host._copyable_characteristics(model))
        record = host.card_record(model)
        values.setdefault(COPIABLE_ORACLE_FIELD, copiable_oracle_id(model))
        values.setdefault(COPIABLE_FACE_FIELD, selected_face_id(record, model))
    return apply_copy_exception(values, exception) if exception is not None else values
