from __future__ import annotations

"""Live battlefield Cascade grants for completed spells on the stack."""

from dataclasses import dataclass, replace
from typing import Any, Mapping

from ..ability_fragments import (
    CURRENT_ABILITY_FRAGMENT_COVERAGE,
    SpellCastKeywordTriggerKind,
    SpellCastKeywordTriggerSpec,
    ability_fragment_to_dict,
)
from ..card_program_faces import program_matches_face
from ..card_programs.admission import program_has_complete_card_program_admission
from ..continuous_conditions import (
    FixedPublicStateConditionKind, FixedPublicStateConditionSpec, FixedPublicStateFact,
)
from ..continuous_effect_model import (
    ContinuousEffect, ContinuousEffectDuration, ContinuousEffectOrigin, ContinuousEffectRelation, ContinuousObjectIdentity,
    ContinuousOperation, Layer,
)
from ..object_predicate import ObjectQuerySpec
from ..public_history_query_model import PublicHistoryQuerySpec, SPELL_ORIGIN_HISTORY_CAPABILITY, matching_public_history_events
from ..rules.cast_cascade_grants import announced_cascade_instances
from .component_registry import exact_fields
from .context import SemanticNodeError


CASCADE_GRANT_CAPABILITY = "continuous.ability.battlefield_cascade_grant"
FIRST_EXILE_CASCADE_CAPABILITY = "continuous.ability.first_exile_cast_cascade_grant"
CASCADE_GRANT_EVENT = "spell.cast.keyword.grant"
CAST_ANNOUNCEMENT_MARKER = "cascade_grant_cast_announced"


@dataclass(frozen=True, slots=True)
class BattlefieldCascadeGrantSpec:
    predicate: ObjectQuerySpec
    origin: str | None = None
    minimum_mana_value: int | None = None
    instances: int = 1
    first_exile_cast: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.predicate, ObjectQuerySpec):
            raise ValueError("Cascade grants require a typed spell predicate")
        closed_predicate = ObjectQuerySpec(
            types_all=self.predicate.types_all,
            types_any=self.predicate.types_any,
            subtypes_all=self.predicate.subtypes_all,
            subtypes_any=self.predicate.subtypes_any,
            colorless=self.predicate.colorless,
            commander=self.predicate.commander,
        )
        if type(self.first_exile_cast) is not bool or (
            self.first_exile_cast and (self.origin != "exile" or self.predicate != ObjectQuerySpec()
                                       or self.minimum_mana_value is not None or self.instances != 1)
        ):
            raise ValueError("First-exile Cascade requires its exact unfiltered cast")
        if (self.predicate != closed_predicate or self.predicate.colorless is False
                or self.predicate.commander is False or self.origin not in (("exile",) if self.first_exile_cast else (None, "hand"))
                or (self.minimum_mana_value is not None and (
                    type(self.minimum_mana_value) is not int or self.minimum_mana_value < 0
                )) or type(self.instances) is not int or self.instances not in (1, 2)):
            raise ValueError("Cascade grants require closed spell, origin and instance facts")

    def to_dict(self) -> dict[str, Any]:
        return {"predicate": self.predicate.to_dict(), "origin": self.origin,
                "minimum_mana_value": self.minimum_mana_value, "instances": self.instances,
                **({"first_exile_cast": True} if self.first_exile_cast else {})}

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> BattlefieldCascadeGrantSpec:
        fields = {"predicate", "origin", "minimum_mana_value", "instances"}
        if (not isinstance(value, Mapping) or set(value) not in (fields, fields | {"first_exile_cast"})
                or "first_exile_cast" in value and value["first_exile_cast"] is not True):
            raise ValueError("Battlefield Cascade grant fields are closed")
        return cls(ObjectQuerySpec.from_dict(value["predicate"]), value["origin"],
                   value["minimum_mana_value"], value["instances"], value.get("first_exile_cast", False))


@dataclass(frozen=True, slots=True)
class BattlefieldCascadeGrantHandler:
    handler_id: str = "rule.spell.cascade.battlefield-grant.v1"
    schema_version: int = 1
    family: str = CASCADE_GRANT_CAPABILITY
    event: str = CASCADE_GRANT_EVENT
    rule_references: tuple[str, ...] = ("601.2i", "611.3a", "611.3b", "613.1f", "702.85a", "702.85c")
    capability_dependencies: tuple[str, ...] = (CASCADE_GRANT_CAPABILITY, "trigger.keyword.cascade")

    def validate(self, descriptor: Mapping[str, Any]) -> BattlefieldCascadeGrantSpec:
        exact_fields(descriptor, {"handler_id", "schema_version", "event", "requires_complete_card_program", "grant"},
                     field="battlefield Cascade grant")
        if (descriptor["handler_id"] != self.handler_id or type(descriptor["schema_version"]) is not int
                or descriptor["schema_version"] != self.schema_version or descriptor["event"] != self.event
                or descriptor["requires_complete_card_program"] is not True):
            raise SemanticNodeError("Cascade grants require their exact handler and complete source")
        try:
            return BattlefieldCascadeGrantSpec.from_dict(descriptor["grant"])
        except (TypeError, ValueError) as exc:
            raise SemanticNodeError(str(exc)) from exc

    def lower(self, descriptor: Mapping[str, Any], context: Any) -> tuple[BattlefieldCascadeGrantSpec, ...]:
        return (self.validate(descriptor),)


def first_exile_cascade_runtime_dependencies(program):
    handler = BattlefieldCascadeGrantHandler()
    marked = tuple(descriptor for descriptor in program.handlers if descriptor.get("handler_id") == handler.handler_id
                   and isinstance(descriptor.get("grant"), Mapping) and "first_exile_cast" in descriptor["grant"])
    if not marked and FIRST_EXILE_CASCADE_CAPABILITY not in program.capability_dependencies:
        return ()
    if (len(marked) != 1 or tuple(program.handlers) != marked or program.effects
            or (program.event, program.active_zone) != (CASCADE_GRANT_EVENT, "battlefield")
            or not handler.validate(marked[0]).first_exile_cast):
        raise ValueError("First-exile Cascade needs its complete battlefield grant")
    return (*handler.capability_dependencies, FIRST_EXILE_CASCADE_CAPABILITY, SPELL_ORIGIN_HISTORY_CAPABILITY)


def current_battlefield_keyword_grants(host, seat, handler, *, include_copies=False):
    """Read live complete grant components from the proper face and controller."""
    from ..entry_copy import copiable_oracle_id

    keys = host._effective_static_component_key_map()
    for source in host._semantic_event_sources(zones={"battlefield"}):
        if source.zone != "battlefield" or source.phased_out or source.controller != seat:
            continue
        current = keys.get(source.object_id, ())
        if not current:
            continue
        record = host.card_record(source)
        if record is None:
            continue
        oracle_id = copiable_oracle_id(source) if include_copies else record.oracle_id
        for program in host.semantics.runtime_handler_programs_for_oracle(
            oracle_id, active_zone="battlefield", event=handler.event,
        ):
            if (program.key not in current or CURRENT_ABILITY_FRAGMENT_COVERAGE not in program.coverage
                    or not program_matches_face(record, program, source)
                    or not program_has_complete_card_program_admission(program)
                    or not host.semantic_program_is_current_trusted(program)):
                continue
            for index, descriptor in enumerate(program.handlers):
                if descriptor.get("handler_id") == handler.handler_id:
                    yield source, f"{program.key}:{index}", handler.validate(descriptor)


def _current_grants(host, seat):
    return current_battlefield_keyword_grants(host, seat, BattlefieldCascadeGrantHandler())


def _cascade_operations(instances):
    fragment = ability_fragment_to_dict(SpellCastKeywordTriggerSpec(SpellCastKeywordTriggerKind.CASCADE))
    return (ContinuousOperation("add_ability", "Cascade"),
            *(ContinuousOperation("add_ability_fragment", fragment) for _ in range(instances)))


CONDITIONAL_STACK_CASCADE_CAPABILITY = "continuous.ability.conditional_stack_cascade"
CONDITIONAL_STACK_CASCADE_EVENT = "spell.stack.conditional-cascade"


@dataclass(frozen=True, slots=True)
class ConditionalStackCascadeHandler:
    handler_id: str = "rule.spell.cascade.conditional-self.v1"
    schema_version: int = 1
    family: str = CONDITIONAL_STACK_CASCADE_CAPABILITY
    event: str = CONDITIONAL_STACK_CASCADE_EVENT
    rule_references: tuple[str, ...] = ("601.2i", "611.3a", "613.1f", "702.85a", "707.10")
    capability_dependencies: tuple[str, ...] = (
        CONDITIONAL_STACK_CASCADE_CAPABILITY, "trigger.keyword.cascade",
        "continuous.characteristics.fixed_public_state",
    )

    def validate(self, descriptor):
        exact_fields(descriptor, {
            "handler_id", "schema_version", "event", "requires_complete_card_program", "source_condition",
        }, field="conditional stack Cascade")
        if (descriptor["handler_id"] != self.handler_id or type(descriptor["schema_version"]) is not int
                or descriptor["schema_version"] != self.schema_version or descriptor["event"] != self.event
                or descriptor["requires_complete_card_program"] is not True):
            raise SemanticNodeError("Conditional Cascade requires its exact stack handler and complete source")
        condition = FixedPublicStateConditionSpec.from_dict(descriptor["source_condition"])
        if (condition.kind is not FixedPublicStateConditionKind.PUBLIC_FACT_AT_LEAST
                or condition.fact is not FixedPublicStateFact.CONTROLLER_GRAVEYARD_DISTINCT_CARD_TYPES
                or condition.amount is None or condition.amount < 1):
            raise SemanticNodeError("Conditional Cascade requires a closed caster graveyard type count")
        return condition

    def lower(self, descriptor, context):
        condition = self.validate(descriptor)
        public = context.public_state
        if (public is None or public.source_controller != context.source_controller
                or not context.source_logical_object_id):
            raise SemanticNodeError("Conditional Cascade requires its current spell and controller snapshot")
        if not condition.matches(public):
            return ()
        return (ContinuousEffect(
            effect_id=f"{context.source_object_id}:{context.component_id}:6",
            source_id=context.source_object_id, layer=Layer.ABILITY, sublayer="6",
            timestamp=context.source_timestamp, operations=_cascade_operations(1),
            origin=ContinuousEffectOrigin.STATIC_ABILITY, applies=ObjectQuerySpec(zones=("stack",)),
            relation=ContinuousEffectRelation.SOURCE_OBJECT,
            related_object=ContinuousObjectIdentity(context.source_object_id, context.source_logical_object_id),
        ),)


def conditional_stack_cascade_programs(host, card):
    if card.zone != "stack" or card.face_down:
        return ()
    if not host.semantics.programs_for_event(CONDITIONAL_STACK_CASCADE_EVENT):
        return ()
    record = host.card_record(card)
    if record is None:
        return ()
    return tuple(
        program for program in host.semantics.runtime_handler_programs_for_oracle(
            record.oracle_id, active_zone="stack", event=CONDITIONAL_STACK_CASCADE_EVENT,
        )
        if program_has_complete_card_program_admission(program)
        and host.semantic_program_is_current_trusted(program)
        and program_matches_face(record, program, card)
    )


def _conditional_source_spell_effects(host, card):
    programs = conditional_stack_cascade_programs(host, card)
    if not programs:
        return ()
    from ..card_programs.runtime import _FixedPublicStateSnapshotResolver
    from .continuous_components import ContinuousEffectSourceContext

    handler = ConditionalStackCascadeHandler()
    resolver = _FixedPublicStateSnapshotResolver(
        state=host.state, public_object_resolver=host._public_object_query_result, quantity_resolver=None,
    )
    effects = []
    for program in programs:
        for index, descriptor in enumerate(program.handlers):
            if descriptor.get("handler_id") != handler.handler_id:
                continue
            condition = handler.validate(descriptor)
            context = ContinuousEffectSourceContext(
                source_object_id=card.object_id, source_ref=card.ref, source_controller=card.controller,
                source_timestamp=card.zone_timestamp, component_id=f"{program.key}:{index}",
                source_logical_object_id=card.logical_object_id, public_state=resolver.snapshot(card, condition),
            )
            effects.extend(handler.lower(descriptor, context))
    return tuple(effects)


def stack_cascade_grant_effects(host, card):
    conditional = _conditional_source_spell_effects(host, card)
    spell = next((item for item in host.state.stack if item.kind == "spell"
                  and item.card_object_id == card.object_id), None)
    if spell is None:
        return conditional
    instances = announced_cascade_instances(spell.context)
    temporary = (ContinuousEffect(
        effect_id=f"{spell.ref}:announced-cascade:6", source_id=card.object_id,
        layer=Layer.ABILITY, sublayer="6", timestamp=card.zone_timestamp,
        operations=_cascade_operations(instances), origin=ContinuousEffectOrigin.RESOLUTION,
        duration=ContinuousEffectDuration.UNTIL_END_OF_TURN,
        locked_objects=(ContinuousObjectIdentity(card.object_id, card.logical_object_id),),
    ),) if instances else ()
    return (*conditional, *temporary, *battlefield_cascade_stack_effects(host, card, spell))


_EXILE_CAST_HISTORY = PublicHistoryQuerySpec("spell_cast", schema_version=2, origin="exile")


def first_exile_cascade_specs_after_payment(host, spell):
    """Qualify the live paid cast before this occurrence is written to history."""
    if (spell.context.get("cast_origin") != "exile"
            or spell.context.get(CAST_ANNOUNCEMENT_MARKER) is True
            or not host.semantics.programs_for_event(CASCADE_GRANT_EVENT)):
        return ()
    if matching_public_history_events(host.state, spell.controller, _EXILE_CAST_HISTORY) != ():
        return ()
    return tuple(SpellCastKeywordTriggerSpec(SpellCastKeywordTriggerKind.CASCADE)
                 for _, _, spec in _current_grants(host, spell.controller) if spec.first_exile_cast)


def _recorded_first_exile_caster(host, card):
    history = host.state.turn_history
    if history is None or history.turn_sequence != host.state.turn_sequence or history.spell_origin_history_version != 1:
        return None
    occurrences = tuple(event for event in history.events
                        if event.kind == "spell_cast" and event.object_incarnation == card.logical_object_id)
    if len(occurrences) != 1 or occurrences[0].origin != "exile" or not occurrences[0].actor:
        return None
    event = occurrences[0]
    matches = matching_public_history_events(host.state, event.actor, _EXILE_CAST_HISTORY)
    return event.actor if matches and matches[0] == event else None


def battlefield_cascade_stack_effects(host, card, spell):
    """Use committed cast provenance and current sources for the live stack ability."""
    if (not host.semantics.programs_for_event(CASCADE_GRANT_EVENT)
            or spell.context.get(CAST_ANNOUNCEMENT_MARKER) is True):
        return ()
    origin = spell.context.get("cast_origin")
    if not isinstance(origin, str) or not origin:
        return ()
    current = tuple(_current_grants(host, spell.controller))
    grants = tuple(grant for grant in current if not grant[2].first_exile_cast)
    caster = _recorded_first_exile_caster(host, card) if origin == "exile" else None
    if caster is not None:
        candidates = current if caster == spell.controller else _current_grants(host, caster)
        grants += tuple(grant for grant in candidates if grant[2].first_exile_cast)
    if not grants:
        return ()
    from ..selection.exile_cast import mana_value_of_cost

    effective = host._effective_card_data(card, maximum_layer=Layer.COLOR)
    mana_value = mana_value_of_cost(str(effective.get("mana_cost") or ""), x_value=int(spell.x_value or 0))
    return tuple(
        ContinuousEffect(
            effect_id=f"{source.object_id}:{component}:6", source_id=source.object_id,
            layer=Layer.ABILITY, sublayer="6", timestamp=source.zone_timestamp,
            operations=_cascade_operations(spec.instances),
            origin=ContinuousEffectOrigin.STATIC_ABILITY,
            applies=replace(spec.predicate, zones=("stack",),
                            controller=None if spec.first_exile_cast else source.controller),
        )
        for source, component, spec in grants
        if spec.origin in (None, origin)
        and (spec.minimum_mana_value is None or mana_value >= spec.minimum_mana_value)
    )
