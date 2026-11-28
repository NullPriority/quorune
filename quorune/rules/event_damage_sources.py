from __future__ import annotations

"""Read-only dealer preparation; canonical trigger/zone/damage owners keep mutation."""

from collections.abc import Mapping
from dataclasses import replace
from ..event_damage_source_model import (EventDamageSourceReference, EventDamageSourceInformation,
    EVENT_DAMAGE_SOURCE_KIND, EVENT_DAMAGE_SOURCE_CONTEXT, EVENT_DAMAGE_SOURCE_CAPABILITY)
from ..event_counter_reference import event_object_identity
from ..received_damage_model import RECEIVED_DAMAGE_CAPABILITY, RECEIVED_DAMAGE_EVENT
from ..replacement.immutable import thaw_value
from ..scalar_effect_amount_model import SCALAR_AMOUNT_CAPABILITY, ScalarAmountOrigin, ScalarEffectAmountSpec
from ..creature_subtypes import canonical_creature_subtype
from ..soulbond_model import SOULBOND_CAPABILITY, SOULBOND_PARTNER_LOGICAL_ID
from ..life_gain_permission import (
    DEALT_DAMAGE_LIFE_GAIN_PROHIBITION_CAPABILITY, DEALT_DAMAGE_LIFE_GAIN_PROHIBITION_FIELD,
    has_dealt_damage_life_gain_prohibition,
)

TARGETED_RECEIVING_LIFE_PROHIBITION_BINDING = "fixed-counter-targeted-receiving-life-prohibition-v1"
_TARGETED_RECEIVING_LIFE_PROHIBITION_TEMPLATE = TARGETED_RECEIVING_LIFE_PROHIBITION_BINDING.replace(
    "fixed-counter-", "fixed-typed-effect-", 1,
)
TARGETED_RECEIVING_DAMAGE_BINDING = "fixed-counter-targeted-receiving-damage-v1"
TARGETED_RECEIVING_DAMAGE_RESULT = "targeted-receiving-damage-v1"
_TARGETED_RECEIVING_TEMPLATE = TARGETED_RECEIVING_DAMAGE_BINDING.replace("fixed-counter-", "fixed-typed-effect-", 1)
PAIRED_RECEIVING_DAMAGE_BINDING = "fixed-counter-paired-receiving-damage-v1"
PAIRED_RECEIVING_DAMAGE_RESULT = "paired-receiving-damage-v1"
_PAIRED_RECEIVING_TEMPLATE = PAIRED_RECEIVING_DAMAGE_BINDING.replace("fixed-counter-", "fixed-typed-effect-", 1)
_OTHER_DAMAGEABLE = "damageable_other_than_source"
_OPERATIONS = frozenset({'damage', 'damage_each_opponent', 'damage_fixed_set'})


def paired_receiving_damage_condition():
    identity = "target_logical_object_id"
    return {"all": [
        {"field": "target_kind", "op": "eq", "value": "permanent"},
        {"any": [
            {"field": identity, "op": "eq", "value": "$source.logical_object_id"},
            {"field": identity, "op": "eq", "value": SOULBOND_PARTNER_LOGICAL_ID},
        ]},
    ]}


def paired_receiving_damage_effect():
    return {**targeted_receiving_damage_effect(), "source": "$source"}


def paired_receiving_damage_target():
    return {"zones": ["player"], "categories": ["player"], "player_relation": "opponent", "count": 1}


def paired_receiving_damage_capabilities(effects, target_schema, mechanic_ids):
    if (
        thaw_value(tuple(effects)) != [paired_receiving_damage_effect()]
        or thaw_value(target_schema) != paired_receiving_damage_target()
    ):
        return ()
    from .damage_capability_shapes import fixed_damage_node_capabilities

    projected = {**paired_receiving_damage_effect(), "amount": 1}
    base = fixed_damage_node_capabilities(effects=(projected,), target_schema=target_schema, mechanic_ids=mechanic_ids)
    if not base:
        return ()
    return tuple(sorted({
        *base, SOULBOND_CAPABILITY, RECEIVED_DAMAGE_CAPABILITY, SCALAR_AMOUNT_CAPABILITY, "target.revalidate_resolution",
    }))


def targeted_receiving_damage_condition(excluded_subtype):
    if excluded_subtype is None:
        qualifier = (
            {"field": "target", "op": "eq", "value": "$source.ref"},
            {"field": "target_logical_object_id", "op": "eq", "value": "$source.logical_object_id"},
        )
    else:
        qualifier = (
            {"field": "target_controller", "op": "eq", "value": "$source.controller"},
            {"field": "target_subtypes", "op": "contains_any", "value": [excluded_subtype]},
        )
    return {"all": [{"field": "target_kind", "op": "eq", "value": "permanent"}, *qualifier]}


def targeted_receiving_damage_effect():
    return {
        "op": "damage",
        "source": EventDamageSourceReference(RECEIVED_DAMAGE_EVENT).to_dict(),
        "target": "$target.0",
        "amount": ScalarEffectAmountSpec(ScalarAmountOrigin.EVENT_AMOUNT).to_dict(),
    }


def controlled_permanent_receiving_damage_condition():
    return {"all": [
        {"field": "target_kind", "op": "eq", "value": "permanent"},
        {"field": "target_controller", "op": "eq", "value": "$source.controller"},
    ]}


def targeted_receiving_life_prohibition_effect():
    return {**targeted_receiving_damage_effect(), DEALT_DAMAGE_LIFE_GAIN_PROHIBITION_FIELD: 1}


def _targeted_receiving_result(scope, prohibit_life):
    if prohibit_life:
        if scope != ("source", None):
            raise ValueError("A dealt-damage life prohibition requires the original source and any other target")
        return targeted_receiving_life_prohibition_effect()
    return targeted_receiving_damage_effect()


def _damageable_target_schema():
    return {
        "zones": ["player", "battlefield"],
        "categories": ["player", "permanent"],
        "predicate": "damageable",
        "count": 1,
    }


def controlled_permanent_receiving_damage_result():
    effect = {**targeted_receiving_damage_effect(), "source": "$source"}
    return (effect,), _damageable_target_schema()


def targeted_receiving_damage_target(excluded_subtype):
    schema = _damageable_target_schema()
    if excluded_subtype is None:
        schema["predicate"] = _OTHER_DAMAGEABLE
    else:
        schema["subtypes_none"] = [excluded_subtype]
    return schema


def _targeted_receiving_target_scope(target_schema):
    """Distinguish an original-incarnation exclusion from one subtype exclusion."""
    schema = thaw_value(target_schema)
    if not isinstance(schema, Mapping) or type(schema.get("count")) is not int:
        return None
    if schema == targeted_receiving_damage_target(None):
        return ("source", None)
    subtypes = schema.get("subtypes_none")
    if isinstance(subtypes, list) and len(subtypes) == 1 and type(subtypes[0]) is str:
        subtype = canonical_creature_subtype(subtypes[0])
        if subtype is not None and schema == targeted_receiving_damage_target(subtype):
            return ("subtype", subtype)
    return None


def _permanent_received_condition(condition):
    if not isinstance(condition, Mapping):
        return False
    if condition.get('field') == 'target_kind' and condition.get('op') == 'eq' and condition.get('value') == 'permanent':
        return True
    return any(_permanent_received_condition(child) for child in condition.get('all', ()))


def event_damage_leaves(value):
    if isinstance(value, Mapping):
        source = value.get('source')
        if isinstance(source, Mapping) and (value.get('op') in _OPERATIONS or source.get('kind') == EVENT_DAMAGE_SOURCE_KIND):
            if value.get('op') not in _OPERATIONS:
                raise ValueError('Event damage source belongs to a damage instruction')
            EventDamageSourceReference.from_dict(source)
            yield value
        for child in value.values():
            yield from event_damage_leaves(child)
    elif isinstance(value, (list, tuple)):
        for child in value:
            yield from event_damage_leaves(child)


def event_damage_source_node_capabilities(*, effects, target_schema, mechanic_ids):
    if len(effects) != 1 or not isinstance(effects[0], Mapping) or not isinstance(effects[0].get('source'), Mapping):
        return ()
    effect = effects[0]
    try:
        reference = EventDamageSourceReference.from_dict(effect['source'])
        if effect.get('op') not in _OPERATIONS:
            return ()
        prohibit_life = has_dealt_damage_life_gain_prohibition(effect)
        amount = effect.get('amount')
        if isinstance(amount, Mapping):
            from .qualified_entry_effects import entry_subtype_damage_capabilities
            if entry := entry_subtype_damage_capabilities(effect, target_schema):
                return entry
            scalar = ScalarEffectAmountSpec.from_dict(amount)
            if scalar.origin is not ScalarAmountOrigin.EVENT_AMOUNT or reference.event not in {'damage.received', 'combat.damage.received'}:
                return ()
            amount = 1
        if type(amount) is not int or amount <= 0:
            return ()
    except (TypeError, ValueError):
        return ()
    from .damage_capability_shapes import fixed_damage_node_capabilities

    scope = _targeted_receiving_target_scope(target_schema)
    if scope is not None or prohibit_life:
        try:
            if thaw_value(effect) != _targeted_receiving_result(scope, prohibit_life):
                return ()
        except ValueError:
            return ()
        target_schema = _damageable_target_schema()
    projected = {key: value for key, value in effect.items() if key != DEALT_DAMAGE_LIFE_GAIN_PROHIBITION_FIELD}
    projected.update(source='$source', amount=amount)
    base = fixed_damage_node_capabilities(effects=(projected,), target_schema=target_schema, mechanic_ids=mechanic_ids)
    if not base:
        return ()
    receiving = (
        RECEIVED_DAMAGE_CAPABILITY, SCALAR_AMOUNT_CAPABILITY, "target.revalidate_resolution",
    ) if scope is not None else ()
    permission = (DEALT_DAMAGE_LIFE_GAIN_PROHIBITION_CAPABILITY, "life.change.effect") if prohibit_life else ()
    return tuple(sorted({*base, EVENT_DAMAGE_SOURCE_CAPABILITY, *receiving, *permission}))


def _validate_targeted_receiving_damage_program(program, effects, condition, target_schema):
    from ..ability_fragments import CURRENT_ABILITY_FRAGMENT_COVERAGE

    if (
        thaw_value(program.effects) != thaw_value(effects)
        or program.event != RECEIVED_DAMAGE_EVENT or program.active_zone != "battlefield"
        or program.cost_schema is not None or program.handlers
        or CURRENT_ABILITY_FRAGMENT_COVERAGE not in program.coverage
        or thaw_value(program.event_condition) != condition
        or thaw_value(program.target_schema) != target_schema
    ):
        raise ValueError("Targeted receiving damage requires its original recipient, dealer, amount, and target")


def _controlled_permanent_receiving_damage_dependencies(program):
    from .damage_capability_shapes import fixed_damage_node_capabilities

    effects, target_schema = controlled_permanent_receiving_damage_result()
    _validate_targeted_receiving_damage_program(
        program, effects, controlled_permanent_receiving_damage_condition(), target_schema,
    )
    ordinary_damage = fixed_damage_node_capabilities(
        effects=({**effects[0], "amount": 1},), target_schema=target_schema,
        mechanic_ids=("cr-115-targets", "cr-120-damage"),
    )
    return (*ordinary_damage, RECEIVED_DAMAGE_CAPABILITY, SCALAR_AMOUNT_CAPABILITY)


def event_damage_source_runtime_dependencies(program):
    from .effect_shape_walkers import contains_nested_key

    leaves = tuple(event_damage_leaves(program.effects))
    raw_template = str(getattr(program, "provenance", {}).get("template_id", ""))
    templates = raw_template.split("+")
    prohibit_life = _TARGETED_RECEIVING_LIFE_PROHIBITION_TEMPLATE in templates
    if not prohibit_life and contains_nested_key(program.effects, DEALT_DAMAGE_LIFE_GAIN_PROHIBITION_FIELD):
        raise ValueError("A dealt-damage life prohibition requires its closed receiving trigger")
    if _PAIRED_RECEIVING_TEMPLATE in templates or (
        thaw_value(program.event_condition) == paired_receiving_damage_condition()
    ):
        from ..ability_fragments import CURRENT_ABILITY_FRAGMENT_COVERAGE

        required = paired_receiving_damage_capabilities(program.effects, program.target_schema, program.coverage)
        if (
            not required or program.event != RECEIVED_DAMAGE_EVENT or program.active_zone != "battlefield"
            or thaw_value(program.event_condition) != paired_receiving_damage_condition()
            or program.cost_schema is not None or program.handlers
            or CURRENT_ABILITY_FRAGMENT_COVERAGE not in program.coverage
        ):
            raise ValueError("Paired receiving damage requires its original source, pair and opponent target")
        return required
    targeted_template = prohibit_life or _TARGETED_RECEIVING_TEMPLATE in templates
    if not leaves:
        if prohibit_life:
            raise ValueError("A dealt-damage life prohibition requires its original event dealer")
        return _controlled_permanent_receiving_damage_dependencies(program) if targeted_template else ()
    scope = _targeted_receiving_target_scope(program.target_schema)
    if targeted_template or scope is not None:
        if scope is None:
            raise ValueError("A receiving event dealer requires its closed target exclusion")
        _validate_targeted_receiving_damage_program(
            program, (_targeted_receiving_result(scope, prohibit_life),),
            targeted_receiving_damage_condition(scope[1]), targeted_receiving_damage_target(scope[1]),
        )
    if len(leaves) != 1 or program.active_zone != 'battlefield':
        raise ValueError('Event dealer requires one battlefield-triggered damage instruction')
    effect = leaves[0];reference = EventDamageSourceReference.from_dict(effect['source'])
    if program.event != reference.event or 'event_subscriptions' in (program.event_condition or {}):
        raise ValueError('Event dealer and its unique normalized subscription must agree')
    if reference.event in {'damage.received', 'combat.damage.received'} and not _permanent_received_condition(program.event_condition):
        raise ValueError('A receiving dealer requires the permanent-recipient subscription')
    required = event_damage_source_node_capabilities(effects=(effect,), target_schema=program.target_schema,
        mechanic_ids=('cr-120-damage', *(('cr-115-targets',) if program.target_schema is not None else ())))
    if not required:
        raise ValueError('Event dealer damage result is outside its closed owner')
    from .qualified_entry_effects import entry_subtype_damage_subtype, validate_entry_subtype_damage_program

    if subtype := entry_subtype_damage_subtype(effect):
        validate_entry_subtype_damage_program(program, leaves, subtype)
    return required


def _information(context, reference):
    info = EventDamageSourceInformation.from_dict(context.get(EVENT_DAMAGE_SOURCE_CONTEXT))
    if context.get('event') != reference.event or info.event != reference.event:
        raise ValueError('Event damage source lost its proved occurrence')
    if (info.received_amount is not None and (context.get('event_id') != info.event_id
            or type(context.get('amount')) is not int or context['amount'] != info.received_amount)):
        raise ValueError('Event damage source lost its sealed received amount or transaction')
    identity = event_object_identity(reference.event, context)
    if (info.source_snapshot.ref != identity['reference']
            or info.source_snapshot.logical_object_id != identity['logical_object_id']):
        raise ValueError('Event damage source information names another incarnation')
    return info


def event_damage_source_context(host, program, event, context):
    leaves = tuple(event_damage_leaves(program.effects))
    if not leaves:
        return {}
    event_damage_source_runtime_dependencies(program)
    reference = EventDamageSourceReference.from_dict(leaves[0]['source'])
    info = event_damage_source_information(host, reference, event, context)
    return {EVENT_DAMAGE_SOURCE_CONTEXT: info.to_dict()}


def event_damage_source_information(host, reference, event, context):
    """Seal the already authorized occurrence's exact live battlefield source."""
    if event != reference.event:
        raise ValueError('Event damage source was queued by another event')
    identity = event_object_identity(event, context)
    object_id = identity['logical_object_id'].rpartition('@')[0]
    card = host.state.cards.get(object_id)
    if (card is None or card.ref != identity['reference'] or card.logical_object_id != identity['logical_object_id']
            or card.zone != 'battlefield' or card.phased_out):
        raise ValueError('Event damage dealer is unavailable at its occurrence')
    from .stack_damage_sources import _public_source_snapshot
    received = event in {'damage.received', 'combat.damage.received'}
    return EventDamageSourceInformation(event, _public_source_snapshot(host, card),
        event_id=context.get('event_id') if received else None,
        received_amount=context.get('amount') if received else None)


def bind_event_damage_source(host, effect, item):
    if not isinstance(effect.get('source'), Mapping):
        return {}
    reference = EventDamageSourceReference.from_dict(effect['source'])
    info = _information(item.context, reference);snapshot = info.source_snapshot
    card = host.state.cards.get(snapshot.object_id)
    if (card is not None and card.logical_object_id == snapshot.logical_object_id
            and card.zone == 'battlefield' and not card.phased_out):
        from .stack_damage_sources import _public_source_snapshot
        snapshot = _public_source_snapshot(host, card)
    return {'source': snapshot.ref, 'source_snapshot': snapshot.to_dict()}


def event_damage_departure_updates(host, frames, departing):
    from .stack_damage_sources import _public_source_snapshot
    updates = {}
    for item in frames:
        context = item.get('context', {})
        if EVENT_DAMAGE_SOURCE_CONTEXT not in context:
            continue
        raw = context[EVENT_DAMAGE_SOURCE_CONTEXT];info = EventDamageSourceInformation.from_dict(raw)
        _information(context, EventDamageSourceReference(info.event))
        snapshot = info.source_snapshot
        card = departing.get((snapshot.object_id, snapshot.logical_object_id))
        if card is not None:
            updates[item['ref']] = replace(info, source_snapshot=_public_source_snapshot(host, card)).to_dict()
    return updates
