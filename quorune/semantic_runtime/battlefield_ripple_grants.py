from __future__ import annotations

"""A current battlefield source grants a trusted Ripple body to cast spells."""

from dataclasses import dataclass
from typing import Any, Mapping

from ..ability_fragments import GrantedTriggeredAbilitySpec, ability_fragment_to_dict
from ..continuous_effect_model import (
    ContinuousEffect,
    ContinuousEffectOrigin,
    ContinuousOperation,
    Layer,
)
from ..object_predicate import ObjectQuerySpec
from ..ripple_model import RippleSpec
from ..rules.ripple import RIPPLE_TRIGGER_CAPABILITIES, ripple_instruction, ripple_program_dependencies
from .battlefield_cascade_grants import current_battlefield_keyword_grants
from .component_registry import exact_fields
from .context import SemanticNodeError


RIPPLE_GRANT_CAPABILITY = "continuous.ability.battlefield_ripple_grant"
RIPPLE_GRANT_EVENT = "spell.cast.ripple.grant"


@dataclass(frozen=True, slots=True)
class BattlefieldRippleGrantSpec:
    ripple: RippleSpec
    fragment: GrantedTriggeredAbilitySpec

    def __post_init__(self):
        if (
            not isinstance(self.ripple, RippleSpec)
            or not isinstance(self.fragment, GrantedTriggeredAbilitySpec)
            or self.fragment.schema_version != 1
            or self.fragment.event != "spell.cast"
            or self.fragment.label != f"Ripple {self.ripple.count}"
        ):
            raise ValueError("A battlefield Ripple grant requires one exact Ripple body")

    def to_dict(self):
        return {"keyword_count": self.ripple.count, "fragment": self.fragment.to_dict()}

    @classmethod
    def from_dict(cls, value):
        if not isinstance(value, Mapping) or set(value) != {"keyword_count", "fragment"}:
            raise ValueError("Battlefield Ripple grant fields are closed")
        return cls(RippleSpec(value["keyword_count"]),
                   GrantedTriggeredAbilitySpec.from_dict(value["fragment"]))


@dataclass(frozen=True, slots=True)
class BattlefieldRippleGrantHandler:
    handler_id: str = "rule.spell.ripple.battlefield-grant.v1"
    schema_version: int = 1
    family: str = RIPPLE_GRANT_CAPABILITY
    event: str = RIPPLE_GRANT_EVENT
    rule_references: tuple[str, ...] = ("601.2i", "611.3a", "611.3b", "613.1f", "702.60a", "702.60b")
    capability_dependencies: tuple[str, ...] = (RIPPLE_GRANT_CAPABILITY, *RIPPLE_TRIGGER_CAPABILITIES)

    def validate(self, descriptor: Mapping[str, Any]) -> BattlefieldRippleGrantSpec:
        exact_fields(descriptor, {"handler_id", "schema_version", "event", "requires_complete_card_program", "grant"},
                     field="battlefield Ripple grant")
        if (descriptor["handler_id"] != self.handler_id or type(descriptor["schema_version"]) is not int
                or descriptor["schema_version"] != self.schema_version or descriptor["event"] != self.event
                or descriptor["requires_complete_card_program"] is not True):
            raise SemanticNodeError("Ripple grants require the exact handler and complete source")
        try:
            return BattlefieldRippleGrantSpec.from_dict(descriptor["grant"])
        except (TypeError, ValueError) as exc:
            raise SemanticNodeError(str(exc)) from exc

    def lower(self, descriptor: Mapping[str, Any], context: Any) -> tuple[BattlefieldRippleGrantSpec, ...]:
        return (self.validate(descriptor),)


def _linked_ripple_body(host, owner_key, spec):
    fragment = spec.fragment
    oracle_id = owner_key.split(":", 1)[0]
    if fragment.semantic_key != f"{oracle_id}:{fragment.ability_id}":
        return False
    program = host.semantics.get(fragment.semantic_key)
    return bool(
        program is not None
        and program.provenance.get("granted_only") is True
        and host.semantic_program_is_current_trusted(program)
        and ripple_program_dependencies(program)
        and tuple(program.effects) == (ripple_instruction(spec.ripple),)
    )


def _caster(host, spell, card):
    """Use the sealed caster after control changes; announcement precedes the journal write."""
    return next(
        (event.actor for event in reversed(host._current_turn_history("spell_cast"))
         if event.object_incarnation == card.logical_object_id and event.actor is not None),
        spell.controller,
    )


def stack_ripple_grant_effects(host, card):
    """Materialize one layer-six granted ability per live casting-controller source."""
    if not host.semantics.programs_for_event(RIPPLE_GRANT_EVENT):
        return ()
    spell = next((item for item in host.state.stack if item.kind == "spell"
                  and item.card_object_id == card.object_id), None)
    if spell is None or not spell.context.get("cast_origin"):
        return ()
    effects = []
    handler = BattlefieldRippleGrantHandler()
    for source, component, spec in current_battlefield_keyword_grants(
        host, _caster(host, spell, card), handler, include_copies=True,
    ):
        if not _linked_ripple_body(host, component, spec):
            continue
        effects.append(ContinuousEffect(
            effect_id=f"{source.object_id}:{component}:6",
            source_id=source.object_id,
            layer=Layer.ABILITY,
            sublayer="6",
            timestamp=source.zone_timestamp,
            operations=(
                ContinuousOperation("add_ability", "Ripple"),
                ContinuousOperation("add_ability_fragment", ability_fragment_to_dict(spec.fragment)),
            ),
            origin=ContinuousEffectOrigin.STATIC_ABILITY,
            applies=ObjectQuerySpec(zones=("stack",)),
        ))
    return tuple(effects)
