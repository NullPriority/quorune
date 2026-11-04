from __future__ import annotations

"""Read-only validation and source binding for compiled command-zone emblems."""

from .emblem_program_model import (
    CompiledEmblemSpec, EMBLEM_CHILD_COVERAGE, EMBLEM_PROGRAM_ANNOTATION,
)
from .errors import GameRuleError


def resolve_emblem_programs(semantics, spec, *, program_is_trusted):
    if not isinstance(spec, CompiledEmblemSpec):
        raise GameRuleError('Emblem abilities require a closed compiled specification')
    result = []
    for reference in spec.abilities:
        program = semantics.get(reference.semantic_key)
        if (program is None or program.key != reference.semantic_key or program.oracle_id != spec.oracle_id
                or program.active_zone != 'command' or program.event != reference.event
                or program.provenance.get('granted_only') is not True
                or EMBLEM_CHILD_COVERAGE not in program.coverage
                or program.cost_schema is not None
                or tuple(sorted(program.capability_dependencies)) != reference.capabilities
                or (reference.kind == 'static' and (program.effects or not program.handlers))
                or (reference.kind == 'triggered' and not program.effects)
                or not program_is_trusted(program)):
            raise GameRuleError('An emblem ability references an unavailable or different trusted command program')
        result.append(program)
    return tuple(result)


def command_emblem_programs(semantics, source, *, program_is_trusted):
    raw = source.annotations.get(EMBLEM_PROGRAM_ANNOTATION)
    if raw is None:
        return ()
    if (source.object_kind != 'emblem' or source.zone != 'command'
            or source.owner != source.controller or source.phased_out):
        raise GameRuleError('Compiled emblem programs require their owned command-zone source')
    try:
        spec = CompiledEmblemSpec.from_dict(raw)
    except (TypeError, ValueError) as exc:
        raise GameRuleError(str(exc)) from exc
    return resolve_emblem_programs(semantics, spec, program_is_trusted=program_is_trusted)
