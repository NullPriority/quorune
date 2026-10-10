from __future__ import annotations

"""Fixed public characteristic settings through existing query and layer owners."""

from dataclasses import dataclass
from typing import Any, Mapping

from ..continuous_effect_model import ContinuousEffectOrigin, ContinuousEffectRelation
from ..object_predicate import ObjectQuerySpec, PermanentStatePredicateSpec
from .attached_continuous import _attached_characteristics_node, _has_characteristic_modifier
from .fixed_characteristic_effects import fixed_characteristic_effects
from .component_registry import exact_fields
from .context import SemanticNodeError
from .continuous_components import ContinuousEffectSourceContext, _fixed_query_effect_predicate


FIXED_SETTING_HANDLER_ID = "continuous.characteristics.fixed-public-setting.v1"
FIXED_SETTING_CAPABILITY = "continuous.characteristics.fixed_public_setting"


@dataclass(frozen=True, slots=True)
class FixedCharacteristicSettingsHandler:
    handler_id: str = FIXED_SETTING_HANDLER_ID
    schema_version: int = 1
    family: str = "continuous.characteristics.fixed_public_setting"
    event: str = "characteristics.evaluate"
    rule_references: tuple[str, ...] = ("611.3a", "611.3b", "611.3c", "613.1d", "613.1e", "613.1f", "613.4b", "613.4c", "613.6", "613.7", "613.8")
    capability_dependencies: tuple[str, ...] = (FIXED_SETTING_CAPABILITY,)

    def validate(self, descriptor: Mapping[str, Any]):
        exact_fields(descriptor, {"handler_id", "schema_version", "event", "target", "modifier"}, field="fixed characteristic setting")
        if descriptor["handler_id"] != self.handler_id or type(descriptor["schema_version"]) is not int or descriptor["schema_version"] != self.schema_version or descriptor["event"] != self.event:
            raise SemanticNodeError("Fixed characteristic setting identity changed")
        target = descriptor["target"]
        exact_fields(target, {"kind", "controller", "predicate", "exclude_source"}, field="fixed setting target")
        if target["kind"] not in {"attached", "fixed_query"} or type(target["exclude_source"]) is not bool:
            raise SemanticNodeError("Fixed settings require an attachment or public battlefield query")
        predicate = ObjectQuerySpec.from_dict(target["predicate"])
        if predicate.zones not in {(), ("battlefield",)} or predicate.owner is not None or predicate.controller is not None or predicate.excluded_controllers or predicate.known_to_actor is not None or predicate.exclude_ref is not None:
            raise SemanticNodeError("Fixed settings reserve authority and visibility to the query owner")
        projected = ObjectQuerySpec(zones=predicate.zones, types_all=predicate.types_all,
            excluded_types=predicate.excluded_types, subtypes_all=predicate.subtypes_all,
            excluded_subtypes=predicate.excluded_subtypes, supertypes_all=predicate.supertypes_all,
            colors_all=predicate.colors_all, token=predicate.token, state_predicate=predicate.state_predicate)
        if projected != predicate:
            raise SemanticNodeError("Fixed setting predicate is outside the reviewed public grammar")
        allowed_types={'artifact','battle','creature','enchantment','land','planeswalker'}
        if not set((*predicate.types_all,*predicate.excluded_types))<=allowed_types:
            raise SemanticNodeError("Fixed setting subjects require actual permanent card types")
        from ..creature_subtypes import canonical_creature_subtype
        if any(canonical_creature_subtype(value)!=value for value in (*predicate.subtypes_all,*predicate.excluded_subtypes)):
            raise SemanticNodeError("Fixed setting subtypes require the canonical creature vocabulary")
        if not set(predicate.supertypes_all)<={'legendary','snow','basic'}:
            raise SemanticNodeError("Fixed setting supertypes are outside the reviewed grammar")
        if predicate.state_predicate is not None and predicate.state_predicate != PermanentStatePredicateSpec(counter_name='+1/+1',minimum_counter_count=1):
            raise SemanticNodeError("Fixed settings support only the named positive counter qualification")
        if target["kind"] == "attached":
            if target["controller"] is not None or target["exclude_source"]:
                raise SemanticNodeError("Attached settings cannot carry source-controller predicates")
            if predicate != ObjectQuerySpec(zones=('battlefield',),types_all=predicate.types_all) or len(predicate.types_all)>1 or not set(predicate.types_all)<={'artifact','battle','creature','enchantment','land','planeswalker'}:
                raise SemanticNodeError("Attached settings require one ordinary permanent subject type")
        elif target["controller"] not in {"any", "source_controller", "source_opponents"}:
            raise SemanticNodeError("Fixed setting controller relation is unsupported")
        node = _attached_characteristics_node((), descriptor["modifier"])
        if not _has_characteristic_modifier(node) or node.quantity is not None or node.add_rules_text or node.add_ability_fragments or node.remove_abilities:
            raise SemanticNodeError("Fixed settings require represented constant operations")
        from ..keyword_abilities import FIXED_CHARACTERISTIC_KEYWORDS
        from ..creature_subtypes import CREATURE_SUBTYPES
        if any(value not in FIXED_CHARACTERISTIC_KEYWORDS for value in node.add_abilities):
            raise SemanticNodeError("Fixed setting keyword additions require represented rules")
        for operation in node.type_operations:
            values=tuple(operation.value)
            if operation.field=='card_types' and (operation.op!='add_types' or not set(v.lower() for v in values)<=allowed_types):
                raise SemanticNodeError("Fixed settings preserve card types when adding represented permanent types")
            if operation.field=='supertypes' and (operation.op!='add_types' or set(v.lower() for v in values)!={'legendary'}):
                raise SemanticNodeError("Fixed setting supertype operation is unsupported")
            if operation.field=='subtypes':
                if operation.op=='set_types' or any(canonical_creature_subtype(value) is None for value in values):
                    raise SemanticNodeError("Fixed settings modify canonical creature subtype sets")
                if operation.op=='remove_types' and {v.lower() for v in values}!=set(CREATURE_SUBTYPES):
                    raise SemanticNodeError("Subtype replacement removes exactly the creature vocabulary")
        return target, predicate, node

    def lower(self, descriptor: Mapping[str, Any], context: ContinuousEffectSourceContext):
        target, predicate, node = self.validate(descriptor)
        common = {"source_id": context.source_object_id, "timestamp": context.source_timestamp, "origin": ContinuousEffectOrigin.STATIC_ABILITY}
        if target["kind"] == "attached":
            if context.attached_object is None:
                return ()
            common.update(relation=ContinuousEffectRelation.SOURCE_ATTACHED_TO_OBJECT, related_object=context.attached_object, applies=predicate)
        else:
            common["applies"] = _fixed_query_effect_predicate(predicate, target_controller=target["controller"], exclude_source=target["exclude_source"], context=context)
        return fixed_characteristic_effects(node, context, common=common)
