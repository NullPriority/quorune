from __future__ import annotations

"""Live source permissions in the existing life runtime component registry."""

from dataclasses import dataclass
from typing import Protocol, Any
from ..ability_fragments import CURRENT_ABILITY_FRAGMENT_COVERAGE
from ..entry_copy import copiable_oracle_id
from ..life_gain_permission import (FixedLifeGainProhibitionSpec,LifeGainPermissionSnapshot,
    LIFE_GAIN_PROHIBITION_CAPABILITY,LIFE_GAIN_PROHIBITION_HANDLER,LIFE_GAIN_PROHIBITION_EVENT,
    decode_life_gain_prohibition)
from .context import SemanticNodeError
from .current_ability_components import program_has_current_ability_fragments


class LifeGainPermissionHost(Protocol):
    semantics: Any
    active_seats: list[str]
    def _semantic_event_sources(self, *, zones: set[str] | None = None) -> list[Any]: ...
    def semantic_program_is_current_trusted(self, program: Any) -> bool: ...
    def _effective_card_data(self, card: Any): ...


@dataclass(frozen=True, slots=True)
class LifeGainProhibitionHandler:
    handler_id: str = LIFE_GAIN_PROHIBITION_HANDLER
    schema_version: int = 1
    family: str = 'life.gain.prohibition'
    event: str = LIFE_GAIN_PROHIBITION_EVENT
    rule_references: tuple[str, ...] = ('119.7','119.9','119.10')
    capability_dependencies: tuple[str, ...] = (LIFE_GAIN_PROHIBITION_CAPABILITY,)

    def validate(self, descriptor):
        try:
            return decode_life_gain_prohibition(descriptor)
        except (TypeError,ValueError) as exc:
            raise SemanticNodeError(str(exc)) from exc

    def lower(self, descriptor, context):
        del context
        return (self.validate(descriptor),)


def collect_life_gain_permissions(host: LifeGainPermissionHost, *, sources=None, source_zones=None):
    from .life_replacements import default_life_replacement_registry,LifeReplacementSourceContext
    registry = default_life_replacement_registry()
    denied = set()
    candidates = list(sources) if sources is not None else host._semantic_event_sources(zones={'battlefield'})
    for source in candidates:
        zone = source_zones.get(source.object_id,source.zone) if source_zones is not None else source.zone
        if zone != 'battlefield' or source.phased_out or source.controller not in host.active_seats:
            continue
        programs = host.semantics.runtime_handler_programs_for_oracle(copiable_oracle_id(source),
            active_zone='battlefield',event=LIFE_GAIN_PROHIBITION_EVENT)
        for program in programs:
            if not host.semantic_program_is_current_trusted(program):
                continue
            if CURRENT_ABILITY_FRAGMENT_COVERAGE not in program.coverage:
                raise SemanticNodeError('Life-gain prohibition lacks current source-ability scope')
            if not program_has_current_ability_fragments(program,host._effective_card_data(source)):
                continue
            for index,descriptor in enumerate(program.handlers):
                if descriptor.get('event') != LIFE_GAIN_PROHIBITION_EVENT:
                    continue
                context = LifeReplacementSourceContext(source.ref,source.controller,f'{program.key}:{index}')
                for spec in registry.lower(descriptor,context):
                    if not isinstance(spec,FixedLifeGainProhibitionSpec):
                        raise SemanticNodeError('Life permission handler produced another rule type')
                    denied.update(spec.permissions(source.controller,host.active_seats).denied_players)
    return LifeGainPermissionSnapshot(tuple(sorted(denied)))
