from __future__ import annotations

"""Current ambient rules and sealed intrinsic source facts for damage."""

from dataclasses import dataclass
from ..ability_fragments import canonical_ability_fragments,static_component_keys
from ..damage_prevention_permission import (DamagePreventionPermissionSnapshot,
    UNPREVENTABLE_HANDLER,UNPREVENTABLE_EVENT,UNPREVENTABLE_CAPABILITY,decode_static_unpreventability)
from .context import SemanticNodeError


@dataclass(frozen=True,slots=True)
class StaticUnpreventableDamageHandler:
    handler_id:str = UNPREVENTABLE_HANDLER
    schema_version:int = 1
    family:str = 'damage.prevention.prohibition'
    event:str = UNPREVENTABLE_EVENT
    rule_references:tuple[str,...] = ('615.12','615.12a')
    capability_dependencies:tuple[str,...] = (UNPREVENTABLE_CAPABILITY,)

    def validate(self,descriptor):
        try:return decode_static_unpreventability(descriptor)
        except (TypeError,ValueError) as exc:raise SemanticNodeError(str(exc)) from exc

    def lower(self,descriptor,context):
        del context
        return (self.validate(descriptor),)


def _current_specs(host,characteristics):
    keys = static_component_keys(canonical_ability_fragments(characteristics.get('ability_fragments',())))
    for key in keys:
        program = host.semantics.get(key)
        if program is None or program.event != UNPREVENTABLE_EVENT or not host.semantic_program_is_current_trusted(program):continue
        for descriptor in program.handlers:
            if descriptor.get('handler_id') == UNPREVENTABLE_HANDLER:
                yield decode_static_unpreventability(descriptor)


def source_has_unpreventable_damage(host,characteristics):
    """Read the supplied current or LKI characteristics, never another copy."""
    return any(spec.scope == 'self' for spec in _current_specs(host,characteristics))


def collect_damage_prevention_permissions(host,*,sources=None,source_zones=None):
    candidates = list(sources) if sources is not None else host._semantic_event_sources(zones={'battlefield'})
    global_rule = False;controllers = set()
    for source in candidates:
        zone = source_zones.get(source.object_id,source.zone) if source_zones is not None else source.zone
        if zone != 'battlefield' or source.phased_out or source.controller not in host.active_seats:continue
        # Only matching source programs require a layer query. The same typed
        # component keys govern the resulting current characteristics.
        from ..entry_copy import copiable_oracle_id
        programs = host.semantics.runtime_handler_programs_for_oracle(copiable_oracle_id(source),
            active_zone='battlefield',event=UNPREVENTABLE_EVENT)
        if not programs:continue
        for spec in _current_specs(host,host._effective_card_data(source)):
            if spec.scope == 'global':global_rule = True
            elif spec.scope == 'controller_creatures_combat':controllers.add(source.controller)
    return DamagePreventionPermissionSnapshot(global_rule,tuple(sorted(controllers)))
