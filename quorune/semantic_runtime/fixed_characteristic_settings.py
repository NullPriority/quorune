from __future__ import annotations

"""Fixed public characteristic settings through existing query and layer owners."""

from dataclasses import dataclass, replace
from typing import Any, Mapping

from ..continuous_effect_model import ContinuousEffect, ContinuousEffectOrigin, ContinuousEffectRelation, ContinuousObjectIdentity, ContinuousOperation, Layer
from ..continuous_conditions import FixedPublicStateConditionSpec, FixedPublicStateConditionKind
from ..characteristic_fragments import CharacteristicQuantityReduction, CharacteristicQuantitySpec, CharacteristicQuantityScope
from ..object_predicate import NONOWNER_CONTROL_QUERY_CAPABILITY, ObjectQuerySpec, PermanentStatePredicateSpec
from ..object_query import object_matches_query
from .attached_continuous import _attached_characteristics_node, _has_characteristic_modifier
from .fixed_characteristic_effects import fixed_characteristic_effects
from .component_registry import exact_fields
from .context import SemanticNodeError
from .continuous_components import ContinuousEffectSourceContext, _fixed_query_effect_predicate


FIXED_SETTING_HANDLER_ID = "continuous.characteristics.fixed-public-setting.v1"
CONDITIONAL_SETTING_HANDLER_ID = "continuous.characteristics.fixed-public-setting.v2"
AURA_COUNT_SETTING_HANDLER_ID = "continuous.characteristics.fixed-aura-count-setting.v3"
CONTROLLED_AURA_RECIPIENT_SETTING_HANDLER_ID = "continuous.characteristics.fixed-controlled-aura-recipient-setting.v1"
FIXED_SETTING_CAPABILITY = "continuous.characteristics.fixed_public_setting"
CONTROLLED_ENCHANTMENT_ANIMATION_HANDLER = "continuous.characteristics.controlled-enchantment-animation.v1"
CONTROLLED_ENCHANTMENT_ANIMATION_CAPABILITY = "continuous.characteristics.controlled_enchantment_animation"
NONOWNER_CREATURE_MODIFIER_HANDLER = "continuous.characteristics.nonowner-creature-modifier.v1"
NONOWNER_CREATURE_MODIFIER_CAPABILITY = "continuous.characteristics.fixed_nonowner_creature_modifier"
CREATURE_TYPE_REMOVAL_HANDLER = 'continuous.characteristics.conditional-creature-type-removal.v1'
CREATURE_TYPE_REMOVAL_CAPABILITY = 'continuous.characteristics.conditional_creature_type_removal'


def validate_setting_condition(condition: Mapping[str, Any], *, allow_devotion=False) -> FixedPublicStateConditionSpec:
    parsed = FixedPublicStateConditionSpec.from_dict(condition)
    for leaf in parsed.leaf_conditions():
        if leaf.kind in {
            FixedPublicStateConditionKind.SOURCE_MATCHES_QUERY,
            FixedPublicStateConditionKind.ATTACHED_MATCHES_QUERY,
            FixedPublicStateConditionKind.CONTROLLER_CREATURE_NUMERIC_EXISTS,
        }:
            raise SemanticNodeError('Conditional type settings require non-characteristic public facts')
        if leaf.quantity is not None:
            if allow_devotion and leaf.quantity.reduction is CharacteristicQuantityReduction.DEVOTION:
                if leaf.quantity.multiplier != 1 or leaf.quantity.offset != 0:
                    raise SemanticNodeError('Devotion settings require the unscaled current devotion')
                continue
            if leaf.quantity.query is None or leaf.quantity.query.zones != ('graveyard',) or leaf.quantity.query.keywords_all:
                raise SemanticNodeError('Conditional setting quantities require public graveyard objects')
            if leaf.quantity.reduction is not CharacteristicQuantityReduction.OBJECT_COUNT:
                raise SemanticNodeError('Conditional settings require a fixed object-count condition')
    return parsed


def fixed_setting_modifier_node(modifier):
    """Validate the shared closed modifier independently of its receiver scope."""
    from ..creature_subtypes import canonical_creature_subtype,CREATURE_SUBTYPES
    from ..keyword_abilities import FIXED_CHARACTERISTIC_KEYWORDS
    allowed_types={'artifact','battle','creature','enchantment','land','planeswalker'}
    node = _attached_characteristics_node((), modifier)
    if not _has_characteristic_modifier(node) or node.quantity is not None or node.add_rules_text or node.add_ability_fragments:
        raise SemanticNodeError("Fixed settings require represented constant operations")
    if any(value not in FIXED_CHARACTERISTIC_KEYWORDS for value in node.add_abilities):
        raise SemanticNodeError("Fixed setting keyword additions require represented rules")
    if any(value not in FIXED_CHARACTERISTIC_KEYWORDS for value in node.remove_abilities):
        raise SemanticNodeError('Fixed setting keyword removals require represented rules')
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
    return node


@dataclass(frozen=True, slots=True)
class FixedCharacteristicSettingsHandler:
    handler_id: str = FIXED_SETTING_HANDLER_ID
    schema_version: int = 1
    family: str = "continuous.characteristics.fixed_public_setting"
    event: str = "characteristics.evaluate"
    rule_references: tuple[str, ...] = ("611.3a", "611.3b", "611.3c", "613.1d", "613.1e", "613.1f", "613.4b", "613.4c", "613.6", "613.7", "613.8")
    capability_dependencies: tuple[str, ...] = (FIXED_SETTING_CAPABILITY,)

    def descriptor_source_component_layer(self,descriptor):
        _target,_predicate,node=self.validate(descriptor)
        if node.type_operations:return Layer.TYPE
        if node.color_operations:return Layer.COLOR
        return Layer.ABILITY

    def _validate_source_condition(self, condition):
        validate_setting_condition(condition)

    def validate(self, descriptor: Mapping[str, Any]):
        version = descriptor.get('schema_version')
        fields = {"handler_id", "schema_version", "event", "target", "modifier"}
        if version in (2, 3):
            fields.add('source_condition')
        exact_fields(descriptor, fields, field="fixed characteristic setting")
        if descriptor["handler_id"] != self.handler_id or type(version) is not int or version != self.schema_version or descriptor["event"] != self.event:
            raise SemanticNodeError("Fixed characteristic setting identity changed")
        condition = descriptor.get('source_condition')
        if condition is not None:
            self._validate_source_condition(condition)
        target = descriptor["target"]
        exact_fields(target, {"kind", "controller", "predicate", "exclude_source"}, field="fixed setting target")
        if target["kind"] not in ({"attached", "fixed_query", "source"} if version in (2, 3) else {"attached", "fixed_query"}) or type(target["exclude_source"]) is not bool:
            raise SemanticNodeError("Fixed settings require an attachment or public battlefield query")
        predicate = ObjectQuerySpec.from_dict(target["predicate"])
        if predicate.zones not in {(), ("battlefield",)} or predicate.owner is not None or predicate.controller is not None or predicate.excluded_controllers or predicate.known_to_actor is not None or predicate.exclude_ref is not None:
            raise SemanticNodeError("Fixed settings reserve authority and visibility to the query owner")
        projected = ObjectQuerySpec(zones=predicate.zones, types_all=predicate.types_all,
            excluded_types=predicate.excluded_types, subtypes_all=predicate.subtypes_all,
            excluded_subtypes=predicate.excluded_subtypes, supertypes_all=predicate.supertypes_all,
            colors_all=predicate.colors_all, token=predicate.token, state_predicate=predicate.state_predicate,
            commander=predicate.commander)
        if projected != predicate:
            raise SemanticNodeError("Fixed setting predicate is outside the reviewed public grammar")
        if (target['controller']=='source_owner' or predicate.commander is not None) and (
                target['kind']!='fixed_query' or predicate.commander is not True
                or predicate.types_all!=('creature',) or target['controller'] not in {'source_owner','source_controller'}):
            raise SemanticNodeError('Owner-relative settings require designated commander creatures')
        allowed_types={'artifact','battle','creature','enchantment','land','planeswalker'}
        if not set((*predicate.types_all,*predicate.excluded_types))<=allowed_types:
            raise SemanticNodeError("Fixed setting subjects require actual permanent card types")
        from ..creature_subtypes import canonical_creature_subtype
        from ..basic_land_types import BASIC_LAND_MANA
        basic_land_query=(predicate.types_all==('land',) and not predicate.excluded_subtypes
            and bool(predicate.subtypes_all) and set(predicate.subtypes_all)<=set(BASIC_LAND_MANA))
        if not basic_land_query and any(canonical_creature_subtype(value)!=value for value in (*predicate.subtypes_all,*predicate.excluded_subtypes)):
            raise SemanticNodeError("Fixed setting subtypes require the canonical creature vocabulary")
        if not set(predicate.supertypes_all)<={'legendary','snow','basic'}:
            raise SemanticNodeError("Fixed setting supertypes are outside the reviewed grammar")
        if predicate.state_predicate is not None and predicate.state_predicate != PermanentStatePredicateSpec(counter_name='+1/+1',minimum_counter_count=1):
            raise SemanticNodeError("Fixed settings support only the named positive counter qualification")
        if target['kind'] == 'source':
            if target['controller'] is not None or target['exclude_source'] or predicate != ObjectQuerySpec(zones=('battlefield',)):
                raise SemanticNodeError('Source settings cannot widen the logical source predicate')
        elif target["kind"] == "attached":
            if target["controller"] is not None or target["exclude_source"]:
                raise SemanticNodeError("Attached settings cannot carry source-controller predicates")
            if predicate != ObjectQuerySpec(zones=('battlefield',),types_all=predicate.types_all) or len(predicate.types_all)>1 or not set(predicate.types_all)<={'artifact','battle','creature','enchantment','land','planeswalker'}:
                raise SemanticNodeError("Attached settings require one ordinary permanent subject type")
        elif target["controller"] not in {"any", "source_controller", "source_opponents", "source_owner"}:
            raise SemanticNodeError("Fixed setting controller relation is unsupported")
        node = fixed_setting_modifier_node(descriptor['modifier'])
        return target, predicate, node

    def lower(self, descriptor: Mapping[str, Any], context: ContinuousEffectSourceContext):
        target, predicate, node = self.validate(descriptor)
        condition = descriptor.get('source_condition')
        if condition is not None:
            if context.public_state is None:
                raise SemanticNodeError('Conditional settings require current public facts')
            if not FixedPublicStateConditionSpec.from_dict(condition).matches(context.public_state):
                return ()
        common = {"source_id": context.source_object_id, "timestamp": context.source_timestamp, "origin": ContinuousEffectOrigin.STATIC_ABILITY}
        if target['kind'] == 'source':
            if not context.source_logical_object_id:
                raise SemanticNodeError('Source settings require logical incarnation')
            common.update(relation=ContinuousEffectRelation.SOURCE_OBJECT,
                related_object=ContinuousObjectIdentity(context.source_object_id, context.source_logical_object_id), applies=predicate)
        elif target["kind"] == "attached":
            if context.attached_object is None:
                return ()
            common.update(relation=ContinuousEffectRelation.SOURCE_ATTACHED_TO_OBJECT, related_object=context.attached_object, applies=predicate)
        else:
            common["applies"] = _fixed_query_effect_predicate(predicate, target_controller=target["controller"], exclude_source=target["exclude_source"], context=context)
        return fixed_characteristic_effects(node, context, common=common)


@dataclass(frozen=True, slots=True)
class NonownerCreatureModifierHandler:
    """Apply one live creature modifier through its first layer's recipient selection."""

    handler_id: str = NONOWNER_CREATURE_MODIFIER_HANDLER
    schema_version: int = 1
    family: str = NONOWNER_CREATURE_MODIFIER_CAPABILITY
    event: str = "characteristics.evaluate"
    source_component_layer: Layer = Layer.TYPE
    rule_references: tuple[str, ...] = (
        "108.3", "110.2", "205.3d", "611.3a", "611.3b", "613.1b", "613.1d",
        "613.1f", "613.4c", "613.6", "613.7a", "613.8",
    )
    capability_dependencies: tuple[str, ...] = (
        NONOWNER_CREATURE_MODIFIER_CAPABILITY, NONOWNER_CONTROL_QUERY_CAPABILITY, FIXED_SETTING_CAPABILITY,
    )

    def validate(self, descriptor):
        exact_fields(descriptor, {"handler_id","schema_version","event","predicate","modifier"},
                     field="nonowner creature modifier")
        if (descriptor["handler_id"] != self.handler_id or descriptor["event"] != self.event
                or type(descriptor["schema_version"]) is not int or descriptor["schema_version"] != self.schema_version):
            raise SemanticNodeError("Nonowner creature modifier identity changed")
        predicate = ObjectQuerySpec.from_dict(descriptor["predicate"])
        if predicate != ObjectQuerySpec(zones=("battlefield",),types_all=("creature",),controlled_by_nonowner=True):
            raise SemanticNodeError("Nonowner creature modifiers must bind the source controller at evaluation")
        node = fixed_setting_modifier_node(descriptor["modifier"])
        if (node.color_operations or node.remove_abilities or node.remove_all_abilities
                or node.base_power is not None or not node.add_abilities
                or not (node.power or node.toughness) or len(node.type_operations) != 1):
            raise SemanticNodeError("Nonowner creature modifiers require subtype, keyword and stat additions")
        operation, = node.type_operations
        if operation.op != "add_types" or operation.field != "subtypes" or len(operation.value) != 1:
            raise SemanticNodeError("Nonowner creature modifiers add one represented creature subtype")
        return predicate, node

    def required_capabilities(self, descriptor):
        from ..keyword_abilities import FIXED_CHARACTERISTIC_KEYWORD_CAPABILITIES

        _predicate, node = self.validate(descriptor)
        return tuple(sorted({
            *self.capability_dependencies,
            *(capability for keyword in node.add_abilities
              for capability in FIXED_CHARACTERISTIC_KEYWORD_CAPABILITIES[keyword]),
        }))

    def lower(self, descriptor, context: ContinuousEffectSourceContext):
        predicate, node = self.validate(descriptor)
        if not context.source_logical_object_id:
            raise SemanticNodeError("Nonowner creature modifiers require the original source incarnation")
        common = dict(source_id=context.source_object_id,timestamp=context.source_timestamp,
                      origin=ContinuousEffectOrigin.STATIC_ABILITY,
                      applies=replace(predicate,controller=context.source_controller))
        effects = fixed_characteristic_effects(node, context, common=common)
        anchor = effects[0].effect_id
        return tuple(replace(effect,recipient_scope_anchor=anchor) for effect in effects)



OPPONENT_BASE_TOUGHNESS_HANDLER = "continuous.characteristics.fixed-opponent-base-toughness.v1"
OPPONENT_BASE_TOUGHNESS_CAPABILITY = "continuous.characteristics.fixed_opponent_base_toughness"


@dataclass(frozen=True, slots=True)
class FixedOpponentBaseToughnessHandler:
    handler_id: str = OPPONENT_BASE_TOUGHNESS_HANDLER
    schema_version: int = 1
    family: str = OPPONENT_BASE_TOUGHNESS_CAPABILITY
    event: str = "characteristics.evaluate"
    source_component_layer: Layer = Layer.ABILITY
    rule_references: tuple[str, ...] = ("109.5", "611.3a", "611.3b", "613.4b", "613.7a")
    capability_dependencies: tuple[str, ...] = (
        OPPONENT_BASE_TOUGHNESS_CAPABILITY, FIXED_SETTING_CAPABILITY,
    )

    def validate(self, descriptor: Mapping[str, Any]) -> tuple[ObjectQuerySpec, int]:
        exact_fields(descriptor, {"handler_id", "schema_version", "event", "predicate", "toughness"},
                     field="opponent base toughness")
        if (descriptor["handler_id"] != self.handler_id or descriptor["event"] != self.event
                or type(descriptor["schema_version"]) is not int
                or descriptor["schema_version"] != self.schema_version):
            raise SemanticNodeError("Opponent base toughness setting identity changed")
        predicate = ObjectQuerySpec.from_dict(descriptor["predicate"])
        if predicate != ObjectQuerySpec(zones=("battlefield",), types_all=("creature",)):
            raise SemanticNodeError("Opponent base toughness settings require current battlefield creatures")
        amount = descriptor["toughness"]
        if type(amount) is not int:
            raise SemanticNodeError("A fixed base toughness setting requires one integer")
        return predicate, amount

    def lower(self, descriptor: Mapping[str, Any], context: ContinuousEffectSourceContext):
        predicate, amount = self.validate(descriptor)
        if not context.source_logical_object_id:
            raise SemanticNodeError("Opponent base toughness requires the current source incarnation")
        return (
            ContinuousEffect(
                effect_id=f"{context.source_object_id}:{context.component_id}:7b",
                source_id=context.source_object_id, layer=Layer.POWER_TOUGHNESS, sublayer="7b",
                timestamp=context.source_timestamp, origin=ContinuousEffectOrigin.STATIC_ABILITY,
                applies=_fixed_query_effect_predicate(
                    predicate, target_controller="source_opponents", exclude_source=False, context=context,
                ),
                operations=(ContinuousOperation("set_toughness", amount),),
            ),
        )

@dataclass(frozen=True, slots=True)
class ConditionalCharacteristicSettingsHandler(FixedCharacteristicSettingsHandler):
    handler_id: str = CONDITIONAL_SETTING_HANDLER_ID
    schema_version: int = 2
    capability_dependencies: tuple[str, ...] = (
        FIXED_SETTING_CAPABILITY, 'continuous.characteristics.fixed_public_state',
    )


def controlled_enchantment_animation_condition():
    return FixedPublicStateConditionSpec(
        FixedPublicStateConditionKind.QUERY_COUNT_AT_LEAST, amount=5, schema_version=2,
        quantity=CharacteristicQuantitySpec(
            CharacteristicQuantityScope.CONTROLLER_ZONE,
            query=ObjectQuerySpec(zones=("battlefield",), types_all=("enchantment",)),
        ),
    )


@dataclass(frozen=True, slots=True)
class ControlledEnchantmentAnimationHandler:
    handler_id: str = CONTROLLED_ENCHANTMENT_ANIMATION_HANDLER
    schema_version: int = 1
    family: str = CONTROLLED_ENCHANTMENT_ANIMATION_CAPABILITY
    event: str = "characteristics.evaluate"
    source_component_layer: Layer = Layer.TYPE
    rule_references: tuple[str, ...] = ("202.3", "205.1b", "611.3a", "611.3b", "613.1d", "613.4b", "613.6")
    capability_dependencies: tuple[str, ...] = (
        CONTROLLED_ENCHANTMENT_ANIMATION_CAPABILITY, FIXED_SETTING_CAPABILITY,
        "continuous.characteristics.fixed_public_state",
    )

    def descriptor(self):
        return {"handler_id":self.handler_id, "schema_version":self.schema_version, "event":self.event,
                "source_condition":controlled_enchantment_animation_condition().to_dict()}

    def validate(self, descriptor):
        from ..replacement.immutable import FrozenMap

        exact_fields(descriptor, {"handler_id","schema_version","event","source_condition"},
                     field="controlled enchantment animation")
        if type(descriptor["schema_version"]) is not int or FrozenMap(descriptor) != FrozenMap(self.descriptor()):
            raise SemanticNodeError("Controlled enchantment animation requires its original public threshold")
        return controlled_enchantment_animation_condition()

    def lower(self, descriptor, context: ContinuousEffectSourceContext):
        condition = self.validate(descriptor)
        receivers = ObjectQuerySpec(zones=("battlefield",), types_all=("enchantment",), excluded_subtypes=("aura",))
        applies = _fixed_query_effect_predicate(receivers, target_controller="source_controller",
                                                exclude_source=True, context=context)
        group = f"{context.source_object_id}:{context.component_id}"
        common = {"source_id":context.source_object_id, "timestamp":context.source_timestamp,
                  "origin":ContinuousEffectOrigin.STATIC_ABILITY, "applies":applies,
                  "recipient_scope_anchor":f"{group}:4"}
        return (
            ContinuousEffect(effect_id=f"{group}:4", layer=Layer.TYPE, sublayer="4",
                             operations=(ContinuousOperation("add_creature_for_enchantment_threshold", condition.amount),),
                             **common),
            ContinuousEffect(effect_id=f"{group}:7b", layer=Layer.POWER_TOUGHNESS, sublayer="7b",
                             operations=(ContinuousOperation("set_power_toughness_to_mana_value"),), **common),
        )


def attached_aura_count_setting_condition(amount: int, *, exact: bool) -> FixedPublicStateConditionSpec:
    quantity = CharacteristicQuantitySpec(
        CharacteristicQuantityScope.ATTACHED_TO_SOURCE,
        query=ObjectQuerySpec(zones=("battlefield",), types_all=("enchantment",), subtypes_all=("aura",)),
    )
    minimum = FixedPublicStateConditionSpec(
        FixedPublicStateConditionKind.QUERY_COUNT_AT_LEAST, amount=amount, quantity=quantity, schema_version=2,
    )
    if not exact:
        return minimum
    maximum = replace(minimum, kind=FixedPublicStateConditionKind.QUERY_COUNT_AT_MOST)
    return FixedPublicStateConditionSpec(
        FixedPublicStateConditionKind.ALL, terms=(minimum, maximum), schema_version=6,
    )


@dataclass(frozen=True, slots=True)
class AuraCountCharacteristicSettingsHandler(ConditionalCharacteristicSettingsHandler):
    """Use the earlier-layer source Aura count for fixed layer-6 and layer-7b results."""

    handler_id: str = AURA_COUNT_SETTING_HANDLER_ID
    schema_version: int = 3

    def _validate_source_condition(self, condition):
        parsed = FixedPublicStateConditionSpec.from_dict(condition)
        minimum = parsed.leaf_conditions()[0]
        if (minimum.kind is not FixedPublicStateConditionKind.QUERY_COUNT_AT_LEAST
                or parsed != attached_aura_count_setting_condition(minimum.amount, exact=bool(parsed.terms))):
            raise SemanticNodeError("Source Aura settings require a closed exact or minimum attached-Aura count")

    def validate(self, descriptor: Mapping[str, Any]):
        target, predicate, node = FixedCharacteristicSettingsHandler.validate(self, descriptor)
        if (target["kind"] != "source" or descriptor["source_condition"] is None
                or node.base_power is None or node.type_operations or node.color_operations
                or node.remove_all_abilities or node.remove_abilities or node.power or node.toughness):
            raise SemanticNodeError("Source Aura counts support only fixed self base stats and keyword grants")
        return target, predicate, node


@dataclass(frozen=True, slots=True)
class ControlledAuraRecipientSettingsHandler(FixedCharacteristicSettingsHandler):
    """Set characteristics on other creatures enchanted by the source controller's Auras."""

    handler_id: str = CONTROLLED_AURA_RECIPIENT_SETTING_HANDLER_ID

    def validate(self, descriptor: Mapping[str, Any]):
        target, predicate, node = FixedCharacteristicSettingsHandler.validate(self, descriptor)
        if (target["kind"] != "fixed_query" or target["controller"] != "source_controller"
                or not target["exclude_source"]
                or predicate != ObjectQuerySpec(zones=("battlefield",), types_all=("creature",))
                or node.base_power is None or not node.add_abilities or node.type_operations or node.color_operations
                or node.remove_all_abilities or node.remove_abilities or node.power or node.toughness):
            raise SemanticNodeError("Controlled Aura recipients require other controlled creatures and fixed base stats and keywords")
        return target, predicate, node

    def lower(self, descriptor: Mapping[str, Any], context: ContinuousEffectSourceContext):
        _target, predicate, node = self.validate(descriptor)
        if context.public_type_objects is None:
            raise SemanticNodeError("Controlled Aura recipient facts are unavailable")
        aura_query = ObjectQuerySpec(
            zones=("battlefield",), controller=context.source_controller, types_all=("enchantment",),
            subtypes_all=("aura",),
        )
        attached_refs = {
            row.attached_to_ref for row in context.public_type_objects
            if row.attached_to_ref is not None and object_matches_query(row, aura_query)
        }
        recipient_query = _fixed_query_effect_predicate(
            predicate, target_controller="source_controller", exclude_source=True, context=context,
        )
        effects = []
        for row in context.public_type_objects:
            if row.ref not in attached_refs or not object_matches_query(row, recipient_query):
                continue
            common = {
                "source_id": context.source_object_id, "timestamp": context.source_timestamp,
                "origin": ContinuousEffectOrigin.STATIC_ABILITY, "applies": recipient_query,
                "relation": ContinuousEffectRelation.SOURCE_CONTROLLED_AURA_RECIPIENT,
                "related_object": ContinuousObjectIdentity(row.object_id, row.logical_object_id),
            }
            recipient_context = replace(context, component_id=f"{context.component_id}:recipient:{row.object_id}")
            effects.extend(fixed_characteristic_effects(node, recipient_context, common=common))
        return tuple(effects)


@dataclass(frozen=True, slots=True)
class ConditionalCreatureTypeRemovalHandler:
    handler_id: str = CREATURE_TYPE_REMOVAL_HANDLER
    schema_version: int = 1
    family: str = CREATURE_TYPE_REMOVAL_CAPABILITY
    event: str = 'characteristics.evaluate'
    source_component_layer: Layer = Layer.TEXT
    rule_references: tuple[str, ...] = ('205.1a', '611.3a', '611.3b', '613.1d', '613.6', '614.12', '700.5', '700.5a')
    capability_dependencies: tuple[str, ...] = (CREATURE_TYPE_REMOVAL_CAPABILITY,)

    def validate(self, descriptor):
        exact_fields(descriptor, {'handler_id', 'schema_version', 'event', 'source_condition'},
            field='Conditional creature-card-type removal')
        if (descriptor['handler_id'] != self.handler_id or type(descriptor['schema_version']) is not int
                or descriptor['schema_version'] != self.schema_version or descriptor['event'] != self.event):
            raise SemanticNodeError('Conditional creature-card-type removal identity changed')
        return validate_setting_condition(descriptor['source_condition'], allow_devotion=True)

    def lower(self, descriptor, context):
        condition = self.validate(descriptor)
        if context.public_state is None:
            raise SemanticNodeError('Creature-card-type removal requires current public facts')
        leaves = condition.terms or (condition,)
        snapshots = context.public_state.term_snapshots if condition.terms else (context.public_state,)
        if len(leaves) != len(snapshots) or any(
                (leaf.quantity is not None or leaf.kind is FixedPublicStateConditionKind.PUBLIC_FACT_AT_LEAST)
                and snapshot.condition_quantity is None for leaf, snapshot in zip(leaves, snapshots)):
            raise SemanticNodeError('Creature-card-type removal condition information is unavailable')
        if not condition.matches(context.public_state):
            return ()
        if not context.source_logical_object_id:
            raise SemanticNodeError('Creature-card-type removal requires the source incarnation')
        return (ContinuousEffect(effect_id=f'{context.source_object_id}:{context.component_id}:4',
            source_id=context.source_object_id, layer=Layer.TYPE, sublayer='4', timestamp=context.source_timestamp,
            operations=(ContinuousOperation('remove_creature_card_type'),), origin=ContinuousEffectOrigin.STATIC_ABILITY,
            applies=ObjectQuerySpec(zones=('battlefield',)), relation=ContinuousEffectRelation.SOURCE_OBJECT,
            related_object=ContinuousObjectIdentity(context.source_object_id, context.source_logical_object_id)),)


def prospective_creature_type_characteristics(
    host, card, characteristics, *, destination_controller, include_controlled_subtypes=True,
):
    """CR 614.12/700.5a: evaluate the entering source against pre-entry facts."""
    import copy
    from ..ability_fragments import static_component_keys
    from ..card_program_faces import program_matches_face
    from ..characteristic_evaluation import evaluate_card_characteristics
    from ..dynamic_characteristics import query_characteristic_count
    from ..entry_copy import copiable_oracle_id
    from ..card_programs.runtime import _FixedPublicStateSnapshotResolver

    keys = set(static_component_keys(characteristics.get('ability_fragments', ())))
    programs = tuple(program for program in host.semantics.runtime_handler_programs_for_oracle(
        copiable_oracle_id(card, characteristics), active_zone='battlefield', event='characteristics.evaluate')
        if program.key in keys and host.semantic_program_is_current_trusted(program))
    from ..rules.cross_zone_characteristics import battlefield_entry_type_addition_effects

    # Copy-form previews can already include the external layer-four effects.
    effects = list(battlefield_entry_type_addition_effects(host, destination_controller)) if include_controlled_subtypes else []
    if not programs and not effects:
        return characteristics
    prospective = copy.copy(card)
    prospective.zone = 'battlefield'
    prospective.controller = destination_controller
    record = host.card_record(card)
    handler = ConditionalCreatureTypeRemovalHandler()
    resolver = _FixedPublicStateSnapshotResolver(state=host.state, public_object_resolver=None,
        quantity_resolver=lambda source, quantity: query_characteristic_count(host, source, quantity))
    for program in programs:
        if not program_matches_face(record, program, card, prospective_name=characteristics.get('name')):
            continue
        for index, descriptor in enumerate(program.handlers):
            if descriptor.get('handler_id') != CREATURE_TYPE_REMOVAL_HANDLER:
                continue
            condition = handler.validate(descriptor)
            context = ContinuousEffectSourceContext(source_object_id=card.object_id, source_ref=card.ref,
                source_controller=destination_controller, source_timestamp=max(0, card.zone_timestamp),
                source_logical_object_id=card.logical_object_id, component_id=f'{program.key}:{index}',
                public_state=resolver.snapshot(prospective, condition))
            effects.extend(handler.lower(descriptor, context))
    return (evaluate_card_characteristics(prospective, characteristics, runtime_effects=effects,
        maximum_layer=Layer.TYPE,
        dependency_order_version=getattr(host.state, 'continuous_dependency_order_version', None),
    ) if effects else characteristics)
