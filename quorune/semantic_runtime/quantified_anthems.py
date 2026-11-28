from __future__ import annotations

"""Live layer-7c quantities over the existing validated anthem subject owner."""

from dataclasses import dataclass
from collections import Counter
from collections.abc import Mapping
from ..characteristic_fragments import (
    CharacteristicQuantityReduction, CharacteristicQuantityScope as Scope, CharacteristicQuantitySpec,
)
from ..object_predicate import ObjectQuerySpec
from ..continuous_effect_model import (
    ContinuousEffect, ContinuousEffectOrigin, ContinuousEffectRelation,
    ContinuousObjectIdentity, ContinuousOperation, Layer,
)
from .context import SemanticNodeError
from .component_registry import exact_fields

QUANTIFIED_ANTHEM_HANDLER='continuous.anthem.live-quantity.v1'
QUANTIFIED_ANTHEM_CAPABILITY='continuous.power_toughness.live_quantity'
RECIPIENT_ATTACK_HISTORY_FIELD = "recipient_attack_history"
RECIPIENT_ATTACK_ANTHEM_CAPABILITY = "continuous.power_toughness.recipient_attack_history"
_RECIPIENT_ATTACK_HISTORY = {"schema_version": 1, "kind": "creature_attacked"}


def recipient_attack_anthem_descriptor():
    return {
        "handler_id": QUANTIFIED_ANTHEM_HANDLER, "schema_version": 1, "event": "characteristics.evaluate",
        "condition": {
            "target_controller": "source_controller",
            "predicate": ObjectQuerySpec(zones=("battlefield",), types_all=("creature",)).to_dict(),
            "exclude_source": False,
        },
        "modifier": {"power": 1, "toughness": 0, RECIPIENT_ATTACK_HISTORY_FIELD: dict(_RECIPIENT_ATTACK_HISTORY)},
    }


def _uses_recipient_attack_history(descriptor):
    modifier = descriptor.get("modifier")
    return (
        descriptor.get("handler_id") == QUANTIFIED_ANTHEM_HANDLER
        and isinstance(modifier, Mapping) and RECIPIENT_ATTACK_HISTORY_FIELD in modifier
    )


def recipient_attack_anthem_dependencies(descriptor):
    if not _uses_recipient_attack_history(descriptor):
        return ()
    QuantifiedAnthemHandler().validate(descriptor)
    return (RECIPIENT_ATTACK_ANTHEM_CAPABILITY,)


def recipient_attack_history_facts(state, descriptor):
    if not _uses_recipient_attack_history(descriptor):
        return None
    from ..turn_history import current_turn_history_events

    history = getattr(state, "turn_history", None)
    if history is None or history.schema_version != 1 or history.turn_sequence != state.turn_sequence:
        raise SemanticNodeError("Attack-count anthems need complete current declaration history")
    counts = Counter(event.object_incarnation for event in current_turn_history_events(
        history, turn_sequence=state.turn_sequence, kind="creature_attacked",
    ))
    return tuple(
        (ContinuousObjectIdentity(card.object_id, card.logical_object_id), counts[card.logical_object_id])
        for card in sorted(state.cards.values(), key=lambda card: card.object_id)
        if card.zone == "battlefield" and counts[card.logical_object_id]
    )


def _recipient_attack_effects(context, predicate):
    counts = context.recipient_attack_counts
    if counts is None:
        raise SemanticNodeError("Attack-count anthems need resolved recipient history")
    return tuple(
        ContinuousEffect(
            effect_id=f"{context.source_object_id}:{context.component_id}:attacks:{identity.object_id}",
            source_id=context.source_object_id,
            layer=Layer.POWER_TOUGHNESS, sublayer="7c", timestamp=context.source_timestamp,
            origin=ContinuousEffectOrigin.STATIC_ABILITY, applies=predicate,
            relation=ContinuousEffectRelation.SOURCE_ATTACK_HISTORY_RECIPIENT, related_object=identity,
            operations=(ContinuousOperation("modify_power_toughness", [amount, 0]),),
        )
        for identity, amount in counts
    )


def recipient_is_in_quantity_domain(predicate,quantity):
    q=quantity.query
    if (quantity.scope is not Scope.ALL_ZONES or not isinstance(q,ObjectQuerySpec)
            or q.zones!=('battlefield',) or quantity.exclude_source or quantity.exclude_attached_object
            or quantity.schema_version not in {1,2} or quantity.reduction.value!='object_count'
            or quantity.multiplier!=1 or quantity.offset!=0):
        return False
    plain=ObjectQuerySpec(zones=('battlefield',),types_all=q.types_all,
        subtypes_all=q.subtypes_all,supertypes_all=q.supertypes_all)
    return (q==plain and set(q.types_all)<=set(predicate.types_all)
        and set(q.subtypes_all)<=set(predicate.subtypes_all)
        and set(q.supertypes_all)<=set(predicate.supertypes_all))


@dataclass(frozen=True,slots=True)
class QuantifiedAnthemHandler:
    handler_id: str=QUANTIFIED_ANTHEM_HANDLER
    schema_version: int=1
    family: str='continuous.quantified_query_power_toughness_anthem'
    event: str='characteristics.evaluate'
    source_component_layer: Layer=Layer.POWER_TOUGHNESS
    rule_references: tuple[str,...]=('107.1b','402.3','611.3a','611.3b','613.4c','613.8')
    capability_dependencies: tuple[str,...]=('continuous.power_toughness.fixed_anthem',QUANTIFIED_ANTHEM_CAPABILITY)

    def validate(self,descriptor):
        from .continuous_components import FixedQueryPowerToughnessAnthemHandler
        exact_fields(descriptor,{'handler_id','schema_version','event','condition','modifier'},field='quantified anthem')
        if (descriptor['handler_id']!=self.handler_id or type(descriptor['schema_version']) is not int
                or descriptor['schema_version']!=1 or descriptor['event']!=self.event):
            raise SemanticNodeError('Quantified anthem identity is malformed')
        modifier=descriptor['modifier']
        if _uses_recipient_attack_history(descriptor):
            from ..replacement.immutable import FrozenMap

            raw = modifier[RECIPIENT_ATTACK_HISTORY_FIELD]
            if (
                FrozenMap(descriptor) != FrozenMap(recipient_attack_anthem_descriptor())
                or type(modifier["power"]) is not int or type(modifier["toughness"]) is not int
                or type(raw["schema_version"]) is not int
                or descriptor["condition"]["exclude_source"] is not False
            ):
                raise SemanticNodeError("Attack-count anthems require their exact live controlled creature modifier")
            base = FixedQueryPowerToughnessAnthemHandler().validate({
                **descriptor, "handler_id": "continuous.anthem.fixed-query.v2", "schema_version": 2,
                "modifier": {"power": 1, "toughness": 0},
            })
            return base, None, False
        exact_fields(modifier,{'power','toughness','quantity','exclude_recipient'},field='quantified anthem modifier')
        if type(modifier['exclude_recipient']) is not bool:
            raise SemanticNodeError('Quantified recipient exclusion must be explicit')
        base=FixedQueryPowerToughnessAnthemHandler().validate({**descriptor,
            'handler_id':'continuous.anthem.fixed-query.v2','schema_version':2,
            'modifier':{key:modifier[key] for key in ('power','toughness')}})
        try:quantity=CharacteristicQuantitySpec.from_dict(modifier['quantity'])
        except (TypeError,ValueError) as exc:raise SemanticNodeError(str(exc)) from exc
        query=quantity.query
        if quantity.schema_version == 8:
            if modifier['exclude_recipient'] or base.target_controller == 'source_owner':
                raise SemanticNodeError('Commander cast anthems require an ordinary current subject')
            return base, quantity, False
        source_attachments = (quantity.scope is Scope.ATTACHED_TO_SOURCE and quantity.schema_version == 2
            and quantity.reduction is CharacteristicQuantityReduction.OBJECT_COUNT)
        if ((quantity.scope not in {Scope.CONTROLLER_ZONE,Scope.OPPONENT_ZONES,Scope.ALL_ZONES}
                and not source_attachments)
                or query is None or quantity.exclude_source or quantity.exclude_attached_object
                or query.keywords_all or query.numeric_characteristic is not None):
            raise SemanticNodeError('Global modifiers require unambiguous layer-safe collection quantities')
        if query.zones==('hand',) and query!=ObjectQuerySpec(zones=('hand',)):
            raise SemanticNodeError('Global hand quantities expose only raw cardinality')
        if 'creature' not in base.predicate.types_all or base.target_controller=='source_owner':
            raise SemanticNodeError('Live anthems require an ordinary current creature subject')
        if modifier['exclude_recipient'] and not recipient_is_in_quantity_domain(base.predicate,quantity):
            raise SemanticNodeError('Other-object quantities must prove every affected recipient belongs to their domain')
        return base,quantity,modifier['exclude_recipient']

    def lower(self,descriptor,context):
        from .continuous_components import _fixed_query_effect_predicate
        base,quantity,exclude=self.validate(descriptor)
        predicate = _fixed_query_effect_predicate(base.predicate, target_controller=base.target_controller,
                                                  exclude_source=base.exclude_source, context=context)
        if quantity is None:
            return _recipient_attack_effects(context, predicate)
        count=context.resolved_quantity
        if type(count) is not int or count<0:
            raise SemanticNodeError('Live anthem quantity is unavailable')
        # The validated subject is a subset of the count domain. Each recipient
        # therefore has exactly one member of that domain to exclude, including
        # when the ability source itself is not a member.
        if exclude:count=max(0,count-1)
        if not count:return ()
        return (ContinuousEffect(effect_id=f'{context.source_object_id}:{context.component_id}',
            source_id=context.source_object_id,layer=Layer.POWER_TOUGHNESS,sublayer='7c',
            timestamp=context.source_timestamp,origin=ContinuousEffectOrigin.STATIC_ABILITY,applies=predicate,
            operations=(ContinuousOperation('modify_power_toughness',[base.power*count,base.toughness*count]),)),)
