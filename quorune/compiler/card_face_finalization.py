from __future__ import annotations

"""Apply source-wide ability and designation scopes after exact face lowering."""

from .backup_nodes import compile_backup_faces
from .declared_effect_amounts import scope_declared_card_faces
from .entry_designation_templates import scope_entry_designation_faces


def finalize_card_faces(record,faces,*,material_line_for,capability_registry,capability_profile):
    faces=compile_backup_faces(record,faces,material_line_for=material_line_for,capability_registry=capability_registry,capability_profile=capability_profile)
    from .fuse_faces import compile_fuse_faces
    faces=compile_fuse_faces(record,faces,material_line_for=material_line_for,capability_registry=capability_registry,capability_profile=capability_profile)
    return scope_declared_card_faces(scope_entry_designation_faces(faces))
