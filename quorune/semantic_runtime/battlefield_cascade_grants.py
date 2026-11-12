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
from ..continuous_effect_model import (
    ContinuousEffect, ContinuousEffectDuration, ContinuousEffectOrigin, ContinuousObjectIdentity,
    ContinuousOperation, Layer,
)
from ..object_predicate import ObjectQuerySpec
from ..rules.cast_cascade_grants import announced_cascade_instances
from .component_registry import exact_fields
from .context import SemanticNodeError


CASCADE_GRANT_CAPABILITY = "continuous.ability.battlefield_cascade_grant"
CASCADE_GRANT_EVENT = "spell.cast.keyword.grant"
CAST_ANNOUNCEMENT_MARKER = "cascade_grant_cast_announced"


@dataclass(frozen=True, slots=True)
class BattlefieldCascadeGrantSpec:
    predicate: ObjectQuerySpec
    origin: str | None = None
    minimum_mana_value: int | None = None
    instances: int = 1

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
        if (self.predicate != closed_predicate or self.predicate.colorless is False
                or self.predicate.commander is False or self.origin not in (None, "hand")
                or (self.minimum_mana_value is not None and (
                    type(self.minimum_mana_value) is not int or self.minimum_mana_value < 0
                )) or type(self.instances) is not int or self.instances not in (1, 2)):
            raise ValueError("Cascade grants require closed spell, origin and instance facts")

    def to_dict(self) -> dict[str, Any]:
        return {"predicate": self.predicate.to_dict(), "origin": self.origin,
                "minimum_mana_value": self.minimum_mana_value, "instances": self.instances}

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> BattlefieldCascadeGrantSpec:
        if not isinstance(value, Mapping) or set(value) != {
            "predicate", "origin", "minimum_mana_value", "instances",
        }:
            raise ValueError("Battlefield Cascade grant fields are closed")
        return cls(ObjectQuerySpec.from_dict(value["predicate"]), value["origin"],
                   value["minimum_mana_value"], value["instances"])


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


def _current_grants(host, seat):
    keys = host._effective_static_component_key_map()
    handler = BattlefieldCascadeGrantHandler()
    for source in host._semantic_event_sources(zones={"battlefield"}):
        if source.zone != "battlefield" or source.phased_out or source.controller != seat:
            continue
        current = keys.get(source.object_id, ())
        if not current:
            continue
        record = host.card_record(source)
        if record is None:
            continue
        for program in host.semantics.runtime_handler_programs_for_oracle(
            record.oracle_id, active_zone="battlefield", event=CASCADE_GRANT_EVENT,
        ):
            if (program.key not in current or CURRENT_ABILITY_FRAGMENT_COVERAGE not in program.coverage
                    or not program_matches_face(record, program, source)
                    or not program_has_complete_card_program_admission(program)
                    or not host.semantic_program_is_current_trusted(program)):
                continue
            for index, descriptor in enumerate(program.handlers):
                if descriptor.get("handler_id") == handler.handler_id:
                    yield source, f"{program.key}:{index}", handler.validate(descriptor)


def _cascade_operations(instances):
    fragment = ability_fragment_to_dict(SpellCastKeywordTriggerSpec(SpellCastKeywordTriggerKind.CASCADE))
    return (ContinuousOperation("add_ability", "Cascade"),
            *(ContinuousOperation("add_ability_fragment", fragment) for _ in range(instances)))


def stack_cascade_grant_effects(host, card):
    spell = next((item for item in host.state.stack if item.kind == "spell"
                  and item.card_object_id == card.object_id), None)
    if spell is None:
        return ()
    instances = announced_cascade_instances(spell.context)
    temporary = (ContinuousEffect(
        effect_id=f"{spell.ref}:announced-cascade:6", source_id=card.object_id,
        layer=Layer.ABILITY, sublayer="6", timestamp=card.zone_timestamp,
        operations=_cascade_operations(instances), origin=ContinuousEffectOrigin.RESOLUTION,
        duration=ContinuousEffectDuration.UNTIL_END_OF_TURN,
        locked_objects=(ContinuousObjectIdentity(card.object_id, card.logical_object_id),),
    ),) if instances else ()
    return (*temporary, *battlefield_cascade_stack_effects(host, card, spell))


def battlefield_cascade_stack_effects(host, card, spell):
    """Use committed cast provenance and current sources for the live stack ability."""
    if (not host.semantics.programs_for_event(CASCADE_GRANT_EVENT)
            or spell.context.get(CAST_ANNOUNCEMENT_MARKER) is True):
        return ()
    origin = spell.context.get("cast_origin")
    if not isinstance(origin, str) or not origin:
        return ()
    grants = tuple(_current_grants(host, spell.controller))
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
            applies=replace(spec.predicate, zones=("stack",), controller=source.controller),
        )
        for source, component, spec in grants
        if spec.origin in (None, origin)
        and (spec.minimum_mana_value is None or mana_value >= spec.minimum_mana_value)
    )
