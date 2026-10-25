from __future__ import annotations

"""Compose one fused program from the two ordinarily lowered half programs."""

from dataclasses import replace
from ..rules.fuse_model import FuseSpec, FUSE_HANDLER, FUSE_CAPABILITY, FUSE_TEMPLATE, FUSE_BOUNDARY
from ..rules.fuse_shapes import fused_body, closed_fused_program


def with_fused_programs(programs, *, capability_registry, capability_profile):
    result = list(programs)
    for declaration in programs:
        for handler in declaration.handlers:
            if handler.get('handler_id') != FUSE_HANDLER:
                continue
            spec = FuseSpec.from_dict(handler['fuse'])
            halves = [next((p for p in programs if p.ability_id == 'spell:' + face.face_id), None)
                for face in (spec.left, spec.right)]
            if any(half is None for half in halves):
                continue
            try:
                effects, targets = fused_body(halves)
            except (KeyError, TypeError, ValueError):
                continue
            dependencies = sorted({FUSE_CAPABILITY, *(c for half in halves for c in half.capability_dependencies)})
            closure = capability_registry.closure(dependencies, profile=capability_profile) if capability_registry else None
            if closure is None or not closure.trusted:
                continue
            provenance = {**halves[0].provenance, 'template_id': FUSE_TEMPLATE, 'fuse': spec.to_dict(),
                'fuse_components': [half.to_dict() for half in halves],
                'capability_closure_fingerprint': closure.fingerprint}
            fused = replace(halves[0], key=f'{declaration.oracle_id}:{spec.ability_id}',
                ability_id=spec.ability_id, effects=effects, target_schema=targets,
                provenance=provenance, coverage=['generated_oracle_ir', 'spell_resolution', FUSE_BOUNDARY, 'fused_spell_resolution'],
                capability_dependencies=dependencies, capability_closure=closure.to_dict())
            if closed_fused_program(fused):
                result.append(fused)
    return result
