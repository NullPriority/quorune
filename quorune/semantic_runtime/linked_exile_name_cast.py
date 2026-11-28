"""One static cast rule using the public names of a source's linked exiles."""

from dataclasses import dataclass

from ..continuous_effect_model import ContinuousObjectIdentity
from ..object_predicate import ObjectQuerySpec
from ..rules.linked_color_cast_exile import (
    EXILE_NAME_CAST_CAPABILITY,
    EXILE_NAME_CAST_EVENT,
    EXILE_NAME_CAST_HANDLER,
    exile_name_cast_binding,
)


@dataclass(frozen=True, slots=True)
class LinkedExileNameCast:
    source: ContinuousObjectIdentity
    binding_id: str

    def matches(self, host, spell_names):
        from ..carddb_characteristics import public_exile_rules_names
        from ..linked_ability_exile_state import current_linked_ability_exile

        if not spell_names:
            return False
        for identity in current_linked_ability_exile(host, source=self.source, ability_pair=self.binding_id):
            card = host.state.cards[identity.object_id]
            if not card.face_down and not spell_names.isdisjoint(public_exile_rules_names(card, host.card_record(card))):
                return True
        return False


@dataclass(frozen=True, slots=True)
class LinkedExileNameCastHandler:
    handler_id: str = EXILE_NAME_CAST_HANDLER
    schema_version: int = 1
    family: str = "rule.cast.static"
    event: str = EXILE_NAME_CAST_EVENT
    rule_references: tuple[str, ...] = ("101.2", "201.2", "400.7", "406.3", "601.2e", "607.2a", "709.4a")
    capability_dependencies: tuple[str, ...] = (EXILE_NAME_CAST_CAPABILITY,)

    def validate(self, descriptor):
        return exile_name_cast_binding(descriptor)

    def lower(self, descriptor, context):
        from .static_cast_rules import StaticCastCondition, StaticCastPlayerScope, StaticCastRule, StaticCastRuleKind

        binding_id = self.validate(descriptor)
        source = ContinuousObjectIdentity(context.source_object_id, context.source_logical_object_id)
        return (StaticCastRule(
            kind=StaticCastRuleKind.CAST_PROHIBITION,
            player_scope=StaticCastPlayerScope.SOURCE_OPPONENTS,
            spell_queries=(ObjectQuerySpec(excluded_types=("land",)),),
            condition=StaticCastCondition.ALWAYS,
            origin_zones=(),
            maximum_per_turn=None,
            chosen_name=False,
            affects_abilities=False,
            required_enchant_spec=None,
            source_ref=context.source_ref,
            source_logical_object_id=context.source_logical_object_id,
            source_controller=context.source_controller,
            source_tapped=context.source_tapped,
            source_chosen_name="",
            source_attached_player=None,
            linked_exile_names=LinkedExileNameCast(source, binding_id),
        ),)
