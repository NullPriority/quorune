from __future__ import annotations

"""Pure Multikicker cost and prospective named-counter entry components."""

from dataclasses import dataclass

from ..card_programs.admission import REQUIRES_COMPLETE_CARD_PROGRAM_FIELD
from ..multikicker_model import (
    FixedManaMultikickerSpec, MultikickerEntrySpec, MULTIKICKER_CAPABILITY,
    MULTIKICKER_ENTRY_CAPABILITY, MULTIKICKER_COST_HANDLER, MULTIKICKER_ENTRY_HANDLER,
    MULTIKICKER_FIELD, validate_multikicker_fact,
)
from ..replacement.immutable import FrozenMap
from ..replacement.model import ReplacementClass, ReplacementEffect
from ..replacement.operations import CreateAffectedObjectCounter
from .component_registry import exact_fields
from .context import SemanticNodeError


def stack_multikicker_fact(host, card):
    """Read the exact stack incarnation, including copied casting decisions."""
    if card.zone != 'stack':
        return None
    facts = [item.context.get(MULTIKICKER_FIELD) for item in host.state.stack
        if item.card_object_id == card.object_id]
    if len(facts) > 1:
        raise ValueError('Multikicker stack incarnation is ambiguous')
    annotation = card.annotations.get(MULTIKICKER_FIELD)
    fact = facts[0] if facts else annotation
    if fact is None:
        if annotation is not None:
            raise ValueError('Multikicker stack and object facts disagree')
        return None
    result = validate_multikicker_fact(fact)
    if annotation is not None and validate_multikicker_fact(annotation) != result:
        raise ValueError('Multikicker stack and object facts disagree')
    return FrozenMap(result)


def prospective_multikicker_fields(host, card, destination):
    from ..kicker import KICKER_ANNOTATION
    fact = stack_multikicker_fact(host, card) if destination == 'battlefield' else None
    return {'multikicker_paid': fact,
        'cast_option': 'kicked' if fact is not None or card.annotations.get(KICKER_ANNOTATION) is True else None}


@dataclass(frozen=True, slots=True)
class FixedManaMultikickerHandler:
    handler_id: str = MULTIKICKER_COST_HANDLER
    schema_version: int = 1
    family: str = MULTIKICKER_CAPABILITY
    event: str = 'cast.cost'
    rule_references: tuple[str, ...] = ('601.2b', '601.2f', '601.2h', '702.33c', '702.33d', '702.33e')
    capability_dependencies: tuple[str, ...] = (MULTIKICKER_CAPABILITY,)

    def validate(self, descriptor):
        exact_fields(descriptor, {'handler_id', 'schema_version', 'event',
            REQUIRES_COMPLETE_CARD_PROGRAM_FIELD, 'multikicker'}, field='Multikicker cost')
        if (descriptor['handler_id'] != self.handler_id or type(descriptor['schema_version']) is not int
                or descriptor['schema_version'] != 1 or descriptor['event'] != self.event
                or descriptor[REQUIRES_COMPLETE_CARD_PROGRAM_FIELD] is not True):
            raise SemanticNodeError('Multikicker cost identity changed')
        try:
            return FixedManaMultikickerSpec.from_dict(descriptor['multikicker'])
        except (KeyError, TypeError, ValueError) as exc:
            raise SemanticNodeError(str(exc)) from exc

    def lower(self, descriptor, context):
        return (self.validate(descriptor),)


@dataclass(frozen=True, slots=True)
class MultikickerEntryHandler:
    handler_id: str = MULTIKICKER_ENTRY_HANDLER
    schema_version: int = 1
    family: str = MULTIKICKER_ENTRY_CAPABILITY
    event: str = 'zone.change'
    rule_references: tuple[str, ...] = ('400.7d', '614.1c', '614.12', '614.16', '702.33c', '702.33e')
    capability_dependencies: tuple[str, ...] = (MULTIKICKER_ENTRY_CAPABILITY,)

    def validate(self, descriptor):
        exact_fields(descriptor, {'handler_id', 'schema_version', 'event', 'entry'}, field='Multikicker entry')
        if (descriptor['handler_id'] != self.handler_id or type(descriptor['schema_version']) is not int
                or descriptor['schema_version'] != 1 or descriptor['event'] != self.event):
            raise SemanticNodeError('Multikicker entry identity changed')
        try:
            return MultikickerEntrySpec.from_dict(descriptor['entry'])
        except (KeyError, TypeError, ValueError) as exc:
            raise SemanticNodeError(str(exc)) from exc

    def lower(self, descriptor, context):
        self.validate(descriptor)
        return ()

    def subject_replacement_effect(self, descriptor, *, subject, component_id):
        spec = self.validate(descriptor)
        try:
            count = spec.paid_count(subject.multikicker_paid)
        except ValueError as exc:
            raise SemanticNodeError(str(exc)) from exc
        if not count:
            return None
        if (subject.origin != 'stack' or subject.destination != 'battlefield'
                or subject.cast_option != 'kicked' or not subject.destination_controller or not component_id):
            raise SemanticNodeError('Multikicker entry requires its resolving spell incarnation')
        return ReplacementEffect(
            effect_id=f'{self.handler_id}:{subject.object_ref}:{component_id}',
            source_id=subject.object_ref, event_kind=self.event,
            replacement_class=ReplacementClass.SELF_REPLACEMENT,
            conditions={'destination': {'eq': 'battlefield'}, 'object_ref': {'eq': subject.object_ref}},
            operations=(CreateAffectedObjectCounter(counter_name=spec.counter_name, amount=count,
                placing_player=subject.destination_controller, source_ref=subject.object_ref, sequence=0),),
            label=f'{subject.object_ref}: {count} Multikicker {spec.counter_name} counter(s)',
        )
