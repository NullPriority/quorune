from __future__ import annotations

from typing import Any, Protocol
from dataclasses import replace

from .cast_timing import (
    CAST_PERMISSION_ACTIVE_ZONE,
    CAST_PERMISSION_EVENT,
    CastTimingPermission,
    canonical_cast_timing_permissions,
)
from .semantic_runtime.cast_permissions import (
    default_cast_permission_registry,
)


class CompiledCastTimingHost(Protocol):
    semantics: Any
    state: Any

    def card_record(self, card: Any) -> Any: ...

    def semantic_program_is_current_trusted(self, program: Any) -> bool: ...

    def _fixed_public_state_condition_holds(self, source: Any, condition: Any,
        *, require_available: bool = False) -> bool: ...


def _face_id(record: Any, face_name: str | None) -> str:
    if face_name:
        return str(face_name)
    if getattr(record, "faces", ()):
        return str(record.faces[0].get("name") or "front")
    return "front"


def compiled_cast_timing_permissions(
    host: CompiledCastTimingHost,
    card: Any,
    *,
    face_name: str | None = None,
    actor: str | None = None,
    exclude_handler_ids: tuple[str,...] = (),
) -> tuple[CastTimingPermission, ...]:
    """Return trusted precompiled permissions for exactly one selected face."""

    record = host.card_record(card)
    if record is None:
        return ()
    expected_face = _face_id(record, face_name)
    registry = default_cast_permission_registry()
    result: list[CastTimingPermission] = []
    for program in host.semantics.runtime_handler_programs_for_oracle(
        record.oracle_id,
        active_zone=CAST_PERMISSION_ACTIVE_ZONE,
        event=CAST_PERMISSION_EVENT,
    ):
        if not host.semantic_program_is_current_trusted(program):
            continue
        if str(program.provenance.get("face_id") or "") != expected_face:
            continue
        for descriptor in program.handlers:
            if descriptor.get('handler_id') in exclude_handler_ids:continue
            if registry.describe(str(descriptor.get("handler_id") or "")):
                from .semantic_runtime.conditional_flash import (
                    CONDITIONAL_FLASH_PERMISSION_HANDLER, ConditionalFlashPermissionContext,
                )
                context = None
                if descriptor.get('handler_id') == CONDITIONAL_FLASH_PERMISSION_HANDLER:
                    if actor is None or actor not in host.state.players:
                        from .semantic_runtime.context import SemanticNodeError
                        raise SemanticNodeError('Conditional timing requires the authenticated caster')
                    from .continuous_conditions import FixedPublicStateConditionSpec
                    source = replace(card,controller=actor)
                    condition = FixedPublicStateConditionSpec.from_dict(descriptor['source_condition'])
                    context = ConditionalFlashPermissionContext(host._fixed_public_state_condition_holds(
                        source,condition,require_available=True))
                result.extend(registry.lower(descriptor, context))
    return canonical_cast_timing_permissions(result)


def compiled_self_cast_restriction_reason(host, actor, card, *, face_name=None):
    """Check one proposed face against the actual caster before payment."""
    from .semantic_runtime.self_cast_restrictions import (
        SELF_CAST_RESTRICTION_EVENT, SELF_CAST_RESTRICTION_HANDLER,
    )
    from .semantic_runtime.context import SemanticNodeError

    record = host.card_record(card)
    if record is None:
        return None
    expected_face = _face_id(record, face_name)
    for program in host.semantics.runtime_handler_programs_for_oracle(record.oracle_id,
            active_zone=CAST_PERMISSION_ACTIVE_ZONE, event=SELF_CAST_RESTRICTION_EVENT):
        if (not host.semantic_program_is_current_trusted(program)
                or str(program.provenance.get('face_id') or '') != expected_face):
            continue
        for descriptor in program.handlers:
            if descriptor.get('handler_id') != SELF_CAST_RESTRICTION_HANDLER:
                continue
            if actor not in host.state.players:
                raise SemanticNodeError('Intrinsic cast restriction requires the authenticated caster')
            condition, = default_cast_permission_registry().lower(descriptor, None)
            if not host._fixed_public_state_condition_holds(
                    replace(card, controller=actor), condition, require_available=True):
                return 'self_cast_condition'
    return None


def conditional_flash_authorship(host,card, *, face_name=None):
    """Return conditional and unconditional declarations without reading prose."""
    from .card_program_faces import program_matches_face
    from .semantic_runtime.conditional_flash import CONDITIONAL_FLASH_PERMISSION_HANDLER
    from .semantic_runtime.cast_permissions import FLASH_CAST_PERMISSION_HANDLER_ID
    record=host.card_record(card)
    if record is None:return False,False
    conditional=printed=False
    for program in host.semantics.runtime_handler_programs_for_oracle(record.oracle_id,
            active_zone=CAST_PERMISSION_ACTIVE_ZONE,event=CAST_PERMISSION_EVENT):
        if not host.semantic_program_is_current_trusted(program) or not program_matches_face(
                record,program,card,prospective_name=face_name):continue
        ids={value.get('handler_id') for value in program.handlers}
        conditional |= CONDITIONAL_FLASH_PERMISSION_HANDLER in ids
        printed |= FLASH_CAST_PERMISSION_HANDLER_ID in ids
    return conditional,printed


def compiled_conditional_flash_stack_effects(host,card):
    """CR 113.6e: the real self-granted keyword functions on the stack."""
    if card.zone!='stack' or card.face_down:return ()
    from .card_program_faces import program_matches_face
    from .card_programs.runtime import _FixedPublicStateSnapshotResolver
    from .dynamic_characteristics import query_characteristic_count
    from .semantic_runtime.conditional_flash import ConditionalFlashKeywordHandler
    from .semantic_runtime.continuous_components import ContinuousEffectSourceContext
    handler=ConditionalFlashKeywordHandler();record=host.card_record(card)
    if record is None:return ()
    resolver=_FixedPublicStateSnapshotResolver(state=host.state,
        public_object_resolver=host._public_object_query_result,
        quantity_resolver=lambda source,quantity:query_characteristic_count(host,source,quantity))
    effects=[]
    for program in host.semantics.runtime_handler_programs_for_oracle(record.oracle_id,
            active_zone='stack',event='characteristics.evaluate'):
        if not host.semantic_program_is_current_trusted(program) or not program_matches_face(record,program,card):continue
        for index,descriptor in enumerate(program.handlers):
            if descriptor.get('handler_id')!=handler.handler_id:continue
            condition=handler.validate(descriptor)
            effects.extend(handler.lower(descriptor,ContinuousEffectSourceContext(
                source_object_id=card.object_id,source_ref=card.ref,source_controller=card.controller,
                source_timestamp=card.zone_timestamp,component_id=f'{program.key}:{index}',
                source_logical_object_id=card.logical_object_id,public_state=resolver.snapshot(card,condition))))
    return tuple(effects)


def prospective_conditional_flash_spell_data(host,card,caster, *, face_down_method=None):
    """Read only a detached proposed stack view for conditional-Flash costs."""
    conditional,_=conditional_flash_authorship(host,card)
    if not conditional:return host._effective_card_data(card)
    view=replace(card,zone='stack',controller=caster)
    if face_down_method is not None:
        from .morph import MORPH_FACE_DOWN_ANNOTATION, face_down_characteristics
        view=replace(view,face_down=True,annotations={**view.annotations,
            MORPH_FACE_DOWN_ANNOTATION:face_down_characteristics(face_down_method)})
    return host._effective_card_data(view)


__all__ = [
    "CompiledCastTimingHost",
    "compiled_cast_timing_permissions",
]
