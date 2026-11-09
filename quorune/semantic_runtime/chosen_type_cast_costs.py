from __future__ import annotations

"""Resolve a retained type designation at the shared public cast-cost boundary."""

from dataclasses import dataclass, replace

from ..cast_cost_modifiers import (
    CHOSEN_TYPE_CAST_COST_CAPABILITY, CHOSEN_TYPE_CAST_COST_HANDLER_ID,
    CastCostAffectedController, CastCostModifierError, PublicCastCostModifierV2Spec,
)
from ..morph import MORPH_FACE_DOWN_ANNOTATION, face_down_characteristics
from ..object_predicate import ObjectQuerySpec
from ..object_query import object_query_result
from ..rules.chosen_type_events import chosen_type_event_value
from .component_registry import exact_fields
from .context import SemanticNodeError


@dataclass(frozen=True, slots=True)
class ChosenTypeSpellCostModifierHandler:
    handler_id: str = CHOSEN_TYPE_CAST_COST_HANDLER_ID
    schema_version: int = 1
    family: str = "casting.cost.modifier.fixed_query"
    event: str = "cast.cost.modify"
    rule_references: tuple[str, ...] = ("109.5", "118.7a", "601.2f", "707.6")
    capability_dependencies: tuple[str, ...] = (CHOSEN_TYPE_CAST_COST_CAPABILITY,)

    def validate(self, descriptor):
        exact_fields(descriptor, {"handler_id", "schema_version", "event", "modifier"},
                     field="chosen creature-type cast-cost modifier")
        if (descriptor["handler_id"] != self.handler_id or descriptor["event"] != self.event
                or type(descriptor["schema_version"]) is not int or descriptor["schema_version"] != 1):
            raise SemanticNodeError("Chosen type cast-cost handler identity changed")
        try:
            modifier = PublicCastCostModifierV2Spec.from_dict(descriptor["modifier"])
        except (CastCostModifierError, TypeError) as exc:
            raise SemanticNodeError(str(exc)) from exc
        mana = dict(modifier.mana_adjustment)
        if (
            modifier.affected_controller not in (CastCostAffectedController.SOURCE_CONTROLLER,
                                                 CastCostAffectedController.ALL_PLAYERS)
            or modifier.predicates_any not in ((ObjectQuerySpec(),), (ObjectQuerySpec(types_all=("creature",)),))
            or modifier != PublicCastCostModifierV2Spec(
                modifier.affected_controller, modifier.predicates_any, modifier.mana_adjustment,
            )
            or any(amount > 0 for amount in mana.values())
            or "C" in mana or ("GENERIC" in mana and len(mana) != 1)
            or "GENERIC" not in mana and modifier.affected_controller is not CastCostAffectedController.SOURCE_CONTROLLER
        ):
            raise SemanticNodeError("Chosen type modifiers require a fixed generic or colored-only reduction")
        return modifier

    def lower(self, descriptor, source):
        modifier = self.validate(descriptor)
        if source is None:
            raise SemanticNodeError("Chosen type reduction requires a current source")
        try:
            chosen_type = chosen_type_event_value(source)
        except ValueError as exc:
            raise SemanticNodeError(str(exc)) from exc
        if chosen_type is None:
            return ()
        predicate = replace(modifier.predicates_any[0], subtypes_all=(chosen_type,))
        return (replace(modifier, predicates_any=(predicate,)),)


def chosen_type_cost_spell_row(host, caster, card, *, cast_type_line, face_down_method):
    """Use the declared spell on its proposed stack, including face-down values."""
    from ..rules.casting.source_placement import prospective_characteristic_read_host

    candidate = card
    if face_down_method is not None:
        candidate = replace(card, face_down=True, annotations={
            **card.annotations, MORPH_FACE_DOWN_ANNOTATION: face_down_characteristics(face_down_method),
        })
    read_host = prospective_characteristic_read_host(host, caster, candidate)
    spell = read_host.state.cards[card.object_id]
    effective = read_host._effective_card_data(spell)
    type_line = (
        cast_type_line if cast_type_line is not None and face_down_method is None
        else str(effective.get("type_line") or "")
    )
    return object_query_result(
        spell, effective, type_parts=read_host._type_parts(type_line),
        known_to_actor=True, attached_to_ref=None,
    )
