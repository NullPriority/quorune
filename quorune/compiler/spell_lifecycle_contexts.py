"""Orchestrate independently owned spell keyword contexts without changing source rows."""
from dataclasses import dataclass
from .cipher_nodes import cipher_source_rows, apply_cipher_context
from .paradigm_nodes import paradigm_source_rows, apply_paradigm_context


@dataclass(frozen=True, slots=True)
class SpellLifecycleCompilationContext:
    cipher: object
    paradigm: object


def contextual_source_rows(record, rows):
    from .station_context_nodes import station_source_context
    rows, station = station_source_context(record, rows)
    rows, cipher = cipher_source_rows(record, rows)
    rows, paradigm = paradigm_source_rows(rows)
    return rows, station, SpellLifecycleCompilationContext(cipher, paradigm)


def apply_context(face, context, *, capability_registry, capability_profile):
    face = apply_cipher_context(face, context.cipher,
        capability_registry=capability_registry, capability_profile=capability_profile)
    return apply_paradigm_context(face, context.paradigm,
        capability_registry=capability_registry, capability_profile=capability_profile)


def finish_contextual_face(context, *args):
    from .leveler_context_nodes import leveler_face_ir
    return apply_context(leveler_face_ir(*args), context,
        capability_registry=args[-2], capability_profile=args[-1])
