from __future__ import annotations

"""Apply source-wide ability and designation scopes after exact face lowering."""

from .backup_nodes import compile_backup_faces
from .declared_effect_amounts import scope_declared_card_faces
from .entry_designation_templates import scope_entry_designation_faces


def finalize_card_faces(record,faces,*,material_line_for,capability_registry,capability_profile):
    faces=compile_backup_faces(record,faces,material_line_for=material_line_for,capability_registry=capability_registry,capability_profile=capability_profile)
    from .fuse_faces import compile_fuse_faces
    faces=compile_fuse_faces(record,faces,material_line_for=material_line_for,capability_registry=capability_registry,capability_profile=capability_profile)
    from .adventure_faces import compile_adventure_faces
    faces=compile_adventure_faces(record,faces,capability_registry=capability_registry,capability_profile=capability_profile)
    from .linked_clue_copy_nodes import compile_linked_clue_copy_faces
    faces = compile_linked_clue_copy_faces(
        faces, material_line_for=material_line_for, capability_registry=capability_registry,
        capability_profile=capability_profile,
    )
    from .linked_exile_activation_nodes import compile_linked_exile_activation_faces

    faces = compile_linked_exile_activation_faces(
        faces, material_line_for=material_line_for, capability_registry=capability_registry,
        capability_profile=capability_profile,
    )
    from .linked_graveyard_aura_nodes import compile_linked_graveyard_aura_faces

    faces = compile_linked_graveyard_aura_faces(
        record, faces, capability_registry=capability_registry, capability_profile=capability_profile,
    )
    from .linked_enchantment_entry_nodes import compile_linked_enchantment_entry_faces

    faces = compile_linked_enchantment_entry_faces(
        record, faces, capability_registry=capability_registry, capability_profile=capability_profile,
    )
    return scope_declared_card_faces(scope_entry_designation_faces(faces))
