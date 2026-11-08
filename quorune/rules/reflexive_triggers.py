from __future__ import annotations

"""Put a paid CR 603.12 occurrence through ordinary pending trigger placement."""

from copy import deepcopy

from ..errors import GameRuleError
from ..model import StackItem
from ..reflexive_trigger_model import BOUND_REFLEXIVE_PROGRAM_VERSION, ReflexiveTriggerProgram
from ..replacement.immutable import FrozenMap
from ..trigger_processing import enqueue_trigger_batch
from .reflexive_payment_shapes import reflexive_program_capabilities


def _bound_reflexive_context(parent, consequence, source_id):
    from ..creature_power_damage import validate_occurrence_power_snapshot
    from ..creature_power_damage_model import OCCURRENCE_POWER_DAMAGE_CONTEXT, occurrence_power_damage_in
    from ..target_source import BOUND_TARGETING_SOURCE_VERSION, TARGETING_SOURCE_CONTEXT, TargetingSourceBinding

    result = {}
    try:
        if consequence.target_schema is not None:
            source = TargetingSourceBinding.from_dict(parent.context.get(TARGETING_SOURCE_CONTEXT))
            if source.schema_version != BOUND_TARGETING_SOURCE_VERSION or source.object_id != source_id:
                raise ValueError('Reflexive targets lost their original ability source')
            result[TARGETING_SOURCE_CONTEXT] = source.to_dict()
            result['source_logical_object_id'] = source.logical_object_id
        if any(occurrence_power_damage_in(consequence.effects)):
            snapshot, occurrence = validate_occurrence_power_snapshot(
                parent.context, parent.context.get(OCCURRENCE_POWER_DAMAGE_CONTEXT),
            )
            result.update(card=occurrence.ref, card_object_identity=occurrence.logical_object_id)
            result[OCCURRENCE_POWER_DAMAGE_CONTEXT] = snapshot
    except (TypeError, ValueError, KeyError) as exc:
        raise GameRuleError(str(exc)) from exc
    return result


def enqueue_reflexive_trigger(host, intent):
    parent = next((item for item in host.state.stack if item.ref == intent.parent_stack_ref), None)
    if parent is None or parent.controller != intent.controller or intent.controller not in host.active_seats:
        raise GameRuleError('Reflexive trigger creation requires its original resolving controller and frame')
    program = host.semantics.get(parent.semantic_key)
    if (program is None or not host.semantic_program_is_current_trusted(program)
            or not any(effect.get('op') == 'offer_optional_mana_payment' and effect.get('schema_version') == 4
                       and FrozenMap(effect.get('reflexive_program', {})) == intent.program for effect in program.effects)):
        raise GameRuleError('Reflexive consequence does not belong to the admitted creating program')
    consequence = ReflexiveTriggerProgram.from_dict(intent.program)
    if not reflexive_program_capabilities(consequence):
        raise GameRuleError('Reflexive consequence ownership is incomplete')
    retained = {'creature_power_damage_source_lki', 'scalar_reference_snapshots'}
    context = {key: deepcopy(value) for key, value in parent.context.items()
               if key.startswith('source_') or key in retained}
    source_id = parent.source_object_id or parent.card_object_id
    if consequence.schema_version == BOUND_REFLEXIVE_PROGRAM_VERSION:
        context.update(_bound_reflexive_context(parent, consequence, source_id))
    source = host.state.cards.get(source_id)
    if source is not None:
        if not context.get('source_logical_object_id'):
            context['source_logical_object_id'] = source.logical_object_id
        if source.logical_object_id == context['source_logical_object_id'] and 'source_damage_snapshot' not in context:
            from .stack_damage_sources import fixed_damage_source_context
            context.update(fixed_damage_source_context(host, source, consequence.effects))
    context.update(event='trigger.reflexive', reflexive_parent=parent.ref,
        dynamic_effects=consequence.to_dict()['effects'])
    if consequence.target_schema is not None:
        context.update(target_schema_override=consequence.to_dict()['target_schema'], trigger_target_selection_pending=True)
    ref = host._next_ref('S')
    item = StackItem(stack_id=host._stable_runtime_id('stack', ref), ref=ref, kind='triggered_ability',
        controller=intent.controller, label=parent.label + ' — reflexive', source_object_id=source_id,
        visibility=list(host.seats), context=context)
    enqueue_trigger_batch(host, (item,))
    return ref
