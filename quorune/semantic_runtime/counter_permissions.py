from __future__ import annotations

"""Counter permission facts through the existing static ability-fragment owner."""

from dataclasses import dataclass

from ..counter_permission import (
    COUNTER_PROHIBITION_CAPABILITY, COUNTER_PROHIBITION_EVENT, COUNTER_PROHIBITION_HANDLER,
    CounterPermissionSnapshot, CounterPlacementFact, CounterProhibitionRule, FixedCounterProhibitionSpec,
)
from ..entry_copy import copiable_oracle_id
from ..ability_fragments import CURRENT_ABILITY_FRAGMENT_COVERAGE
from .ability_fragments import _fragment
from .context import SemanticNodeError
from .self_entry_counters import program_static_component_is_applicable


@dataclass(frozen=True, slots=True)
class CounterProhibitionAbilityFragmentHandler:
    handler_id: str = COUNTER_PROHIBITION_HANDLER
    schema_version: int = 1
    family: str = 'ability.static.counter_prohibition'
    event: str = COUNTER_PROHIBITION_EVENT
    rule_references: tuple[str, ...] = ('101.2', '118.3', '118.11', '122.5', '122.6', '613.1f')
    capability_dependencies: tuple[str, ...] = (COUNTER_PROHIBITION_CAPABILITY,)

    def validate(self, descriptor):
        return _fragment(descriptor, handler_id=self.handler_id, event=self.event,
            expected_type=FixedCounterProhibitionSpec)

    def lower(self, descriptor, context):
        del context
        return (self.validate(descriptor),)


def collect_counter_permissions(host, *, sources=None, source_zones=None,
        prospective_controller=None, self_only=False):
    """Read current source predicates without caching mutable game state."""
    candidates = list(sources) if sources is not None else host._semantic_event_sources(zones={'battlefield'})
    rules = []
    handler = CounterProhibitionAbilityFragmentHandler()
    for source in candidates:
        zone = source_zones.get(source.object_id, source.zone) if source_zones is not None else source.zone
        controller = prospective_controller or source.controller
        if zone != 'battlefield' or source.phased_out or controller not in host.active_seats:
            continue
        programs = host.semantics.runtime_handler_programs_for_oracle(copiable_oracle_id(source),
            active_zone='battlefield', event='continuous')
        for program in programs:
            descriptors = tuple(d for d in program.handlers if d.get('handler_id') == COUNTER_PROHIBITION_HANDLER)
            if not descriptors:
                continue
            if not host.semantic_program_is_current_trusted(program):
                continue
            if CURRENT_ABILITY_FRAGMENT_COVERAGE not in program.coverage:
                raise SemanticNodeError('Counter prohibition lacks current source-ability scope')
            prospective = prospective_controller is not None or zone != source.zone
            if not program_static_component_is_applicable(host, program, source,
                    prospective_zone=zone if prospective else None,
                    prospective_controller=controller if prospective else None):
                continue
            for descriptor in descriptors:
                spec = handler.validate(descriptor)
                if self_only and spec.relation != 'self':
                    continue
                rules.append(CounterProhibitionRule(spec, source.object_id, controller))
    return CounterPermissionSnapshot(tuple(rules))


def allows_counter_placement_on_card(host, card, counter_name, amount, *, permissions=None, effective=None):
    """One permission query shared by advertised counter costs and payment."""
    permissions = collect_counter_permissions(host) if permissions is None else permissions
    if not permissions.rules:
        return True
    data = host._effective_card_data(card) if effective is None else effective
    types = tuple(sorted(host._type_parts(str(data.get('type_line') or ''))[0]))
    return permissions.allows(CounterPlacementFact('permanent', card.object_id, counter_name, amount,
        controller=card.controller if card.zone == 'battlefield' else None, zone=card.zone, types=types))


def entry_counter_permissions(host, subject, card, global_permissions):
    """Own self-only prohibitions use the existing prospective layer-6 view."""
    if subject.destination != 'battlefield':
        return global_permissions
    from ..card_program_faces import prospective_face_view
    record = host.card_record(card)
    if record is None:
        return global_permissions
    view = prospective_face_view(record, card, subject.entry_face_id)
    own = collect_counter_permissions(host, sources=(view,),
        source_zones={view.object_id: 'battlefield'},
        prospective_controller=subject.destination_controller, self_only=True)
    return CounterPermissionSnapshot((*global_permissions.rules, *own.rules))


def copied_entry_counter_permissions(host, subject, preview):
    """Copy forms replace the entering object's self rule, not existing sources."""
    global_rules = tuple(rule for rule in subject.counter_permissions.rules
        if not (rule.spec.relation == 'self' and rule.source_object_id == subject.object_id))
    own = collect_counter_permissions(host, sources=(preview,),
        source_zones={preview.object_id: 'battlefield'},
        prospective_controller=subject.destination_controller, self_only=True)
    return CounterPermissionSnapshot((*global_rules, *own.rules))
