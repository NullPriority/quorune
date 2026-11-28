"""Orchestrate independently owned spell keyword contexts without changing source rows."""
from dataclasses import dataclass
from ..characteristic_evaluation import type_parts
from .cipher_nodes import cipher_source_rows, apply_cipher_context
from .paradigm_nodes import paradigm_source_rows, apply_paradigm_context


@dataclass(frozen=True, slots=True)
class SpellLifecycleCompilationContext:
    cipher: object
    paradigm: object
    source_name: str
    single_face_types: frozenset[str]


def contextual_source_rows(record, rows):
    from .station_context_nodes import station_source_context
    rows, station = station_source_context(record, rows)
    rows, cipher = cipher_source_rows(record, rows)
    rows, paradigm = paradigm_source_rows(rows)
    single_face_types = frozenset(type_parts(record.type_line)[0]) if not record.faces else frozenset()
    return rows, station, SpellLifecycleCompilationContext(cipher, paradigm, record.name, single_face_types)


def apply_context(face, context, *, capability_registry, capability_profile):
    face = apply_cipher_context(face, context.cipher,
        capability_registry=capability_registry, capability_profile=capability_profile)
    return apply_paradigm_context(face, context.paradigm,
        capability_registry=capability_registry, capability_profile=capability_profile)


def lower_complete_face(
    record, *, face_id, face_name, type_line, keywords, oracle_text, material_rows, card_types, spell,
    effect_template, trusted_mechanics, capability_registry, capability_profile, residuals, context,
    compile_spell_face,
):
    """Resolve closed whole-face rule groups before lowering independent paragraphs."""
    if "creature" in card_types:
        from .targeted_attack_die_copy import targeted_attack_die_copy_face

        table = targeted_attack_die_copy_face(
            record=record, face_id=face_id, face_name=face_name, oracle_text=oracle_text,
            material_rows=material_rows, registry=capability_registry, profile=capability_profile,
        )
        if table is not None:
            return material_rows, table
    if not spell:
        return material_rows, None

    from .declared_effect_amounts import declared_spell_effect_compiler
    from .public_replacement_rows import public_replacement_material_rows

    compile_effect = declared_spell_effect_compiler(
        record, is_spell=True, oracle_text=oracle_text, compile_effect=effect_template, face_name=face_name,
    )
    rows = public_replacement_material_rows(
        material_rows, oracle_text=oracle_text, source_name=face_name or record.name, compile_effect=compile_effect,
    )
    face = compile_spell_face(
        record, face_id=face_id, face_name=face_name, type_line=type_line, keywords=keywords,
        oracle_text=oracle_text, material_rows=rows, effect_template=compile_effect,
        trusted_mechanics=trusted_mechanics, capability_registry=capability_registry,
        capability_profile=capability_profile, residuals=residuals,
    )
    if face is not None:
        face = apply_context(face, context, capability_registry=capability_registry, capability_profile=capability_profile)
    return rows, face


def finish_contextual_face(context, leveler, oracle_id, face_id, face_name, oracle_text, material_rows,
                           keywords, nodes, residuals, capability_registry, capability_profile):
    from .discarded_card_exile_templates import restrict_linked_discard_exile
    from .ir_model import append_residual
    from .enchanted_departure_return_templates import require_enchanted_departure_restriction
    from .leveler_context_nodes import leveler_face_ir
    from .linked_exile_characteristics import paired_linked_exile_characteristics
    from .linked_exile_owner_tokens import paired_linked_exile_owner_tokens
    from .linked_hand_mana import paired_linked_hand_mana
    from .linked_graveyard_mana import paired_linked_graveyard_mana
    from .linked_color_cast_exile import paired_linked_color_cast_exile
    from .linked_graveyard_play_templates import paired_linked_graveyard_play

    nodes = restrict_linked_discard_exile(
        nodes, oracle_text=oracle_text, source_name=face_name or oracle_id,
        residuals=residuals, append_residual=append_residual,
    )
    nodes = require_enchanted_departure_restriction(nodes, residuals)
    nodes = paired_linked_exile_owner_tokens(nodes, residuals, oracle_id=oracle_id, face_id=face_id,
        registry=capability_registry, profile=capability_profile)
    nodes = paired_linked_exile_characteristics(nodes, residuals, source_name=face_name or oracle_id, face=face_id,
        capability_registry=capability_registry, capability_profile=capability_profile)
    nodes = paired_linked_color_cast_exile(nodes, residuals, source_name=context.source_name, oracle_id=oracle_id,
        face_id=face_id, source_types=context.single_face_types,
        capability_registry=capability_registry, capability_profile=capability_profile)
    nodes = paired_linked_hand_mana(nodes, residuals, oracle_id=oracle_id, face_id=face_id,
        source_types=context.single_face_types, capability_registry=capability_registry,
        capability_profile=capability_profile)
    nodes = paired_linked_graveyard_play(nodes, residuals, source_name=face_name or oracle_id, oracle_id=oracle_id,
        face_id=face_id, capability_registry=capability_registry, capability_profile=capability_profile)
    nodes = paired_linked_graveyard_mana(nodes, residuals, oracle_id=oracle_id, face_id=face_id,
        source_types=context.single_face_types, capability_registry=capability_registry, capability_profile=capability_profile)
    face = leveler_face_ir(leveler, oracle_id, face_id, face_name, oracle_text, material_rows,
        keywords, nodes, residuals, capability_registry, capability_profile)
    return apply_context(face, context, capability_registry=capability_registry, capability_profile=capability_profile)
