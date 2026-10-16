from __future__ import annotations

"""Put a paid CR 603.12 occurrence through ordinary pending trigger placement."""

from copy import deepcopy

from ..errors import GameRuleError
from ..model import StackItem
from ..reflexive_trigger_model import ReflexiveTriggerProgram
from ..replacement.immutable import FrozenMap
from ..trigger_processing import enqueue_trigger_batch
from .reflexive_payment_shapes import reflexive_program_capabilities


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
    source = host.state.cards.get(source_id)
    if source is not None:
        if not context.get('source_logical_object_id'):
            context['source_logical_object_id'] = source.logical_object_id
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
