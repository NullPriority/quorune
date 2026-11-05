from __future__ import annotations

"""Closed immutable references to independently compiled emblem abilities."""

from dataclasses import dataclass
from typing import Any, Mapping

EMBLEM_PROGRAM_CAPABILITY = 'object.emblem.compiled_abilities'
EMBLEM_CHILD_COVERAGE = 'compiled-emblem-child'
EMBLEM_PROGRAM_ANNOTATION = 'compiled_emblem_v1'


@dataclass(frozen=True, slots=True)
class EmblemAbilityProgram:
    semantic_key: str
    kind: str
    event: str
    text: str
    capabilities: tuple[str, ...]

    def __post_init__(self):
        if any(type(value) is not str or not value.strip() or value != value.strip()
               for value in (self.semantic_key, self.event, self.text)):
            raise ValueError('An emblem ability requires canonical program, event and display text')
        if (self.kind not in {'static', 'triggered'}
                or (self.kind == 'static') != (self.event == 'characteristics.evaluate')):
            raise ValueError('The emblem ability source owner is unsupported')
        if (not isinstance(self.capabilities, tuple) or not self.capabilities
                or any(type(value) is not str or not value for value in self.capabilities)
                or tuple(sorted(set(self.capabilities))) != self.capabilities):
            raise ValueError('Emblem ability capabilities must be canonical and nonempty')

    def to_dict(self):
        return {'semantic_key': self.semantic_key, 'kind': self.kind, 'event': self.event,
                'text': self.text, 'capabilities': list(self.capabilities)}

    @classmethod
    def from_dict(cls, value):
        if (not isinstance(value, Mapping) or set(value) != {'semantic_key', 'kind', 'event', 'text', 'capabilities'}
                or not isinstance(value['capabilities'], (list, tuple))):
            raise ValueError('An emblem ability has a closed program-reference schema')
        return cls(**{**value, 'capabilities': tuple(value['capabilities'])})


@dataclass(frozen=True, slots=True)
class CompiledEmblemSpec:
    oracle_id: str
    abilities: tuple[EmblemAbilityProgram, ...]
    schema_version: int = 1

    def __post_init__(self):
        if type(self.schema_version) is not int or self.schema_version != 1:
            raise ValueError('Unsupported compiled emblem version')
        if type(self.oracle_id) is not str or not self.oracle_id or self.oracle_id != self.oracle_id.strip():
            raise ValueError('An emblem requires its creating Oracle source identity')
        if (not isinstance(self.abilities, tuple) or not 1 <= len(self.abilities) <= 4
                or any(not isinstance(value, EmblemAbilityProgram) for value in self.abilities)
                or len({value.semantic_key for value in self.abilities}) != len(self.abilities)):
            raise ValueError('An emblem requires distinct closed ability programs')

    @property
    def capabilities(self):
        return tuple(sorted({'object.emblem.creation', EMBLEM_PROGRAM_CAPABILITY,
                             *(cap for ability in self.abilities for cap in ability.capabilities)}))

    def to_dict(self):
        return {'schema_version': self.schema_version, 'oracle_id': self.oracle_id,
                'abilities': [ability.to_dict() for ability in self.abilities]}

    @classmethod
    def from_dict(cls, value):
        if (not isinstance(value, Mapping) or set(value) != {'schema_version', 'oracle_id', 'abilities'}
                or not isinstance(value['abilities'], (list, tuple))):
            raise ValueError('Compiled emblems have a closed schema')
        return cls(oracle_id=value['oracle_id'], schema_version=value['schema_version'],
                   abilities=tuple(EmblemAbilityProgram.from_dict(row) for row in value['abilities']))


def compiled_emblem_effect_dependencies(effect):
    if not isinstance(effect, Mapping) or effect.get('op') != 'create_compiled_emblem':
        return ()
    if set(effect) != {'op', 'controller', 'emblem'} or effect['controller'] != '$controller':
        raise ValueError('Compiled emblem creation requires the resolving controller')
    return CompiledEmblemSpec.from_dict(effect['emblem']).capabilities


def compiled_emblem_runtime_dependencies(program):
    required = set()
    def visit(value):
        if isinstance(value, Mapping):
            required.update(compiled_emblem_effect_dependencies(value))
            for child in value.values():
                visit(child)
        elif isinstance(value, (list, tuple)):
            for child in value:
                visit(child)
    visit(program.effects)
    command_static = (program.active_zone == 'command' and program.provenance.get('granted_only') is True
                      and program.event == 'characteristics.evaluate')
    if EMBLEM_CHILD_COVERAGE in program.coverage or command_static:
        if (program.active_zone != 'command' or program.provenance.get('granted_only') is not True
                or program.cost_schema is not None or EMBLEM_CHILD_COVERAGE not in program.coverage):
            raise ValueError('An emblem child must be a command-zone-only ability')
        required.add(EMBLEM_PROGRAM_CAPABILITY)
    return tuple(sorted(required))


def compiled_emblem_node_capabilities(*, effects, target_schema, mechanic_ids):
    if len(effects) != 1 or target_schema is not None:
        return ()
    try:
        return compiled_emblem_effect_dependencies(effects[0])
    except (TypeError, ValueError):
        return ()


def is_closed_compiled_emblem_program(program):
    required = compiled_emblem_node_capabilities(effects=program.effects,
        target_schema=program.target_schema, mechanic_ids=program.coverage)
    if not required or not set(required) <= set(program.capability_dependencies):
        return False
    return CompiledEmblemSpec.from_dict(program.effects[0]['emblem']).oracle_id == program.oracle_id
