"""Capture and preserve referenced spell information for the canonical copy owner."""

from dataclasses import replace
from collections.abc import Mapping

from ..event_spell_copy_model import (EventSpellCopySource,EVENT_SPELL_COPY_CONTEXT,EVENT_SPELL_COPY_MECHANIC,EVENT_SPELL_COPY_CAPABILITY,event_spell_copy_effect)
from .nonmana_activation_copy_model import (
    ACTIVATION_COPY_CONTEXT, ActivationCopySource, COPY_SOURCE_VERSION, TRIGGER_COPY_SOURCE_VERSION,
)
from ..replacement.immutable import thaw_value


def decode_event_spell_copy_source(value):
    """Validate nested physical data at the rules-owned source boundary."""
    from ..defined_token_faces import DEFINED_TOKEN_FACES_FIELD,DEFINED_TOKEN_ACTIVE_FACE_FIELD,defined_token_creation_identity
    source=EventSpellCopySource.from_dict(value)
    if DEFINED_TOKEN_FACES_FIELD in source.characteristics:
        defined_token_creation_identity(source.characteristics)
    elif DEFINED_TOKEN_ACTIVE_FACE_FIELD in source.characteristics:
        raise ValueError('Copied spell orientation requires its complete physical faces')
    return source


def capture_event_spell_copy_source(host,item,*,departed=False):
    card=host.state.cards.get(item.card_object_id or '')
    if item.kind not in {'spell','spell_copy'} or card is None or card.zone!='stack':raise ValueError('Referenced copy source is not a current spell')
    program=host.semantics.get(item.semantic_key)
    from ..stack_resolution import trusted_generic_empty_resolution
    if (program is not None and not host.semantic_program_is_current_trusted(program)
            or program is None and trusted_generic_empty_resolution(host,item,None) is None):
        raise ValueError('Referenced spell has no current trusted resolution program')
    raw=item.to_dict();context=dict(raw['context'])
    for key in ('semantic_frame','currently_resolving','declared_scalar_amounts','targets_revalidated','target_groups_current'):
        context.pop(key,None)
    raw['context']=context
    from ..token_copy_faces import spell_copy_characteristics
    return EventSpellCopySource(raw,spell_copy_characteristics(host,card),host._stack_target_schema(item,program),card.logical_object_id,departed)


def event_spell_copy_trigger_context(host,program,event,context):
    dependencies = event_spell_copy_runtime_dependencies(program)
    if not dependencies:return {}
    if event!='spell.cast':raise ValueError('Event spell copy requires a cast occurrence')
    target=next((i for i in host.state.stack if i.ref==context.get('stack')),None)
    if target is None:raise ValueError('Event spell-copy occurrence lost its exact stack spell')
    source=capture_event_spell_copy_source(host,target)
    if source.logical_object_id!=context.get('logical_object_id'):raise ValueError('Event spell-copy cast incarnation disagrees')
    receipt = {EVENT_SPELL_COPY_CONTEXT:source.to_dict()}
    from .prior_qualified_cast_copy import PRIOR_CAST_COPY_CAPABILITY, prior_cast_copy_count_context

    if PRIOR_CAST_COPY_CAPABILITY in dependencies:
        receipt.update(prior_cast_copy_count_context(host, context, source))
    from .token_creation_capability_shapes import CAST_PERMANENT_TOKEN_COPY_CAPABILITY
    if CAST_PERMANENT_TOKEN_COPY_CAPABILITY in dependencies:
        from ..entry_copy import COPIABLE_ORACLE_FIELD
        from ..event_spell_copy_model import CAST_SPELL_TOKEN_ORACLE_CONTEXT
        if target.kind != "spell":
            raise ValueError("Direct cast token requires the committed original spell")
        card = host.state.cards[target.card_object_id]
        # Retain the public copy identity with the cast so departure needs no card lookup.
        # A represented alternate copiable identity can replace the physical identity.
        oracle_id = source.characteristics.get(COPIABLE_ORACLE_FIELD, card.oracle_id)
        if type(oracle_id) is not str or not oracle_id:
            raise ValueError("Direct cast token copy requires a copiable Oracle identity")
        receipt[CAST_SPELL_TOKEN_ORACLE_CONTEXT] = oracle_id
    return receipt


def resolve_event_spell_copy_source(host,effect):
    if 'copy_source' not in effect:return None
    raw=effect.get('copy_source')
    version = raw.get("schema_version") if isinstance(raw, Mapping) else None
    if version in (COPY_SOURCE_VERSION, TRIGGER_COPY_SOURCE_VERSION):
        decode, capture, field = _ability_copy_reader(version)
    else:
        decode, capture, field = decode_event_spell_copy_source, capture_event_spell_copy_source, EVENT_SPELL_COPY_CONTEXT
    source=decode(raw);item=source.item()
    if effect.get('stack')!=item.ref:raise ValueError('Event spell-copy source reference changed')
    live=next((i for i in host.state.stack if i.ref==item.ref),None)
    if live is not None:
        current=capture(host,live)
        if current.logical_object_id!=source.logical_object_id:raise ValueError('Another spell replaced the referenced incarnation')
        return current
    if source.departed:return source
    for pending in host.state.stack:
        raw=pending.context.get(field)
        if raw is None:continue
        known=decode(raw)
        if known.item().ref==item.ref and known.logical_object_id==source.logical_object_id and known.departed:return known
    raise ValueError('Referenced spell departure information is unavailable')


def _ability_copy_reader(version):
    from functools import partial
    from .nonmana_activation_copy import capture_activation_copy_source
    from .attack_trigger_copy import COPY_CONTEXT

    field = COPY_CONTEXT if version == TRIGGER_COPY_SOURCE_VERSION else ACTIVATION_COPY_CONTEXT
    return ActivationCopySource.from_dict, partial(capture_activation_copy_source, schema_version=version), field


def pin_event_spell_copy_departure(host,target):
    if target.kind == "triggered_ability":
        decode, capture, field = _ability_copy_reader(TRIGGER_COPY_SOURCE_VERSION)
    elif target.kind == "activated_ability":
        decode, capture, field = _ability_copy_reader(COPY_SOURCE_VERSION)
    else:
        decode, capture, field = decode_event_spell_copy_source, capture_event_spell_copy_source, EVENT_SPELL_COPY_CONTEXT
    selected=[]
    for pending in host.state.stack:
        raw=pending.context.get(field)
        if raw is None:continue
        source=decode(raw)
        if source.item().ref==target.ref:selected.append((pending,source))
    batches=[]
    for index,batch in enumerate(getattr(host.state,'pending_trigger_batches',())):
        matching=set()
        for group in batch.groups:
            for pending in group.items:
                raw=pending.payload.get('context',{}).get(field)
                if raw is None:continue
                source=decode(raw)
                if source.item().ref==target.ref:
                    matching.add(pending.ref);selected.append((None,source))
        if matching:batches.append((index,batch,matching))
    if not selected:return
    latest=capture(host,target,departed=True)
    for pending,source in selected:
        if source.logical_object_id!=latest.logical_object_id:raise ValueError('Referenced spell-copy departure incarnation changed')
    def pin_occurrence(pending):
        from ..trigger_batches import PendingTriggerItem
        payload=pending.to_dict();payload['context'][field]=latest.to_dict()
        return PendingTriggerItem.from_dict(payload)
    replacements=[(index,replace(batch,groups=tuple(replace(group,items=tuple(
        pin_occurrence(pending) if pending.ref in matching else pending for pending in group.items
    )) for group in batch.groups))) for index,batch,matching in batches]
    for pending,_source in selected:
        if pending is not None:pending.context[field]=latest.to_dict()
    from ..trigger_processing import replace_pending_trigger_batch
    for index,batch in replacements:replace_pending_trigger_batch(host,index,batch)


def cast_copy_runtime_dependencies(program):
    from .cast_target_copy import cast_target_copy_runtime_dependencies

    from .targeted_discard_spell_copy import targeted_discard_copy_runtime_dependencies
    from .targeted_return_spell_copy import targeted_return_copy_runtime_dependencies

    return (*event_spell_copy_runtime_dependencies(program), *cast_target_copy_runtime_dependencies(program),
            *targeted_discard_copy_runtime_dependencies(program), *targeted_return_copy_runtime_dependencies(program))


def event_spell_copy_runtime_dependencies(program):
    from .cast_spell_suspend import cast_spell_suspend_runtime_dependencies

    if (suspended_copy := cast_spell_suspend_runtime_dependencies(program)) is not None:
        return suspended_copy
    from .prior_qualified_cast_copy import prior_cast_copy_program_dependencies

    if prior_copy := prior_cast_copy_program_dependencies(program):
        return prior_copy
    from ..event_spell_copy_model import cast_spell_token_condition
    from ..replacement.immutable import FrozenMap
    from .other_spell_history_shapes import other_spell_self_cast_runtime_dependencies
    from ..stack_spell_copy_model import (
        PERMANENT_SPELL_COPY_CAPABILITY, PERMANENT_SPELL_COPY_FIELD, PermanentSpellCopySpec,
        beast_or_bird_creature_cast_condition,
    )
    from .token_creation_capability_shapes import cast_spell_token_copy_payload_dependencies

    from .cast_creature_set_spell_copy import CAST_CREATURE_SET_COPY_FIELD, cast_creature_set_copy_program_dependencies
    from .cast_source_spell_copy import SOURCE_CAST_COPY_FIELD, source_cast_copy_program_dependencies

    other_spell = other_spell_self_cast_runtime_dependencies(program)
    marked = EVENT_SPELL_COPY_MECHANIC in program.coverage
    stack_copy = False
    direct_token = False
    artifact_copy = False
    exile_cast_copy = None
    fixed_source_target = False
    creature_set = False

    def visit(value):
        nonlocal stack_copy, direct_token, artifact_copy, exile_cast_copy, fixed_source_target, creature_set
        if isinstance(value, Mapping):
            if CAST_CREATURE_SET_COPY_FIELD in value:
                creature_set = True
            elif SOURCE_CAST_COPY_FIELD in value:
                fixed_source_target = True
            elif value.get("op") == "copy_stack_item" and "copy_source" in value:
                if PERMANENT_SPELL_COPY_FIELD in value:
                    spec = PermanentSpellCopySpec.from_dict(value[PERMANENT_SPELL_COPY_FIELD])
                    if spec.kind not in {"artifact", "exile_cast_grant"} or dict(value) != spec.effects[0]:
                        raise ValueError("A cast-event copy must retain its exact original spell instruction")
                    if spec.kind == "artifact":
                        artifact_copy = True
                    else:
                        exile_cast_copy = spec
                elif not any(dict(value) == event_spell_copy_effect(retain_targets_only=retain) for retain in (False,True)):
                    raise ValueError("Event spell-copy instruction has a closed source reference")
                stack_copy = True
            elif value.get("op") == "create_token" and cast_spell_token_copy_payload_dependencies(value):
                direct_token = True
            else:
                for child in value.values():
                    visit(child)
        elif isinstance(value, (list, tuple)):
            for child in value:
                visit(child)

    visit(program.effects)
    if creature_set:
        return cast_creature_set_copy_program_dependencies(program)
    if fixed_source_target:
        return source_cast_copy_program_dependencies(program)
    if not stack_copy and not direct_token:
        if marked:
            raise ValueError("Event spell-copy marker lacks a represented instruction")
        return ()
    if program.event != "spell.cast":
        raise ValueError("Event spell-copy instruction requires its normalized cast event")
    if direct_token:
        if (stack_copy or len(program.effects) != 1 or program.active_zone != "stack"
                or program.target_schema is not None
                or FrozenMap(program.event_condition or {}) != FrozenMap(cast_spell_token_condition())):
            raise ValueError("Direct cast spell copy requires its exact intrinsic nontargeted cast")
        required = cast_spell_token_copy_payload_dependencies(program.effects[0])
        if not required:
            raise ValueError("Direct cast spell copy must be the complete trigger result")
        return (*required, "trigger.event.normalized_spell_cast", "trigger.effect.fixed_event",
                "trigger.placement.apnap")
    if exile_cast_copy is not None:
        from .exile_spell_copy_grant import validate_exile_cast_copy_program

        return validate_exile_cast_copy_program(program, exile_cast_copy)
    if artifact_copy:
        payment = program.effects[0]
        if (len(program.effects) != 1 or payment.get("op") != "offer_optional_effect"
                or tuple(payment.get("effects", ())) != PermanentSpellCopySpec("artifact").effects
                or program.active_zone != "battlefield" or program.target_schema is not None
                or FrozenMap(program.event_condition or {}) != FrozenMap(beast_or_bird_creature_cast_condition())):
            raise ValueError("Artifact copy requires the original optional Beast or Bird creature cast")
        return (EVENT_SPELL_COPY_CAPABILITY, PERMANENT_SPELL_COPY_CAPABILITY)
    return (EVENT_SPELL_COPY_CAPABILITY, *other_spell)


def next_cast_copy_node_capabilities(*, effects, target_schema, mechanic_ids):
    from ..event_spell_copy_model import NEXT_CAST_COPY_CAPABILITY, NEXT_CAST_COPY_MECHANIC, next_cast_copy_payload

    if target_schema is not None or len(effects) != 1 or NEXT_CAST_COPY_MECHANIC not in mechanic_ids:
        return ()
    try:
        next_cast_copy_payload(effects[0])
    except (TypeError, ValueError):
        return ()
    return (NEXT_CAST_COPY_CAPABILITY,)


def is_closed_next_cast_copy_program(program):
    required = next_cast_copy_node_capabilities(
        effects=program.effects, target_schema=program.target_schema, mechanic_ids=program.coverage,
    )
    return bool(required) and set(required) <= set(program.capability_dependencies)


def schedule_next_cast_copy(host, effect, *, actor):
    from ..continuous_effect_state import ResolutionEffectSource
    from ..event_spell_copy_model import NEXT_CAST_COPY_FIELD, next_cast_copy_payload
    from ..trigger_processing import schedule_delayed_trigger

    spec = next_cast_copy_payload(effect, actor=actor)
    source = ResolutionEffectSource.from_effect(effect)
    if source.object_id is None or source.logical_object_id is None:
        raise ValueError("Next-cast copy requires its resolving source incarnation")
    return schedule_delayed_trigger(
        host, controller=actor, label="Copy the next qualifying spell this turn", event_kind="spell.cast",
        condition={NEXT_CAST_COPY_FIELD: spec.to_dict()}, stack_template={}, source_object_id=source.object_id,
        source_logical_object_id=source.logical_object_id, once=True, expires_turn_sequence=host.state.turn_sequence,
    ).ref


def next_cast_copy_matches(trigger, context):
    from ..event_spell_copy_model import next_cast_copy_trigger_spec
    from .spell_cast_events import SpellCastEvent

    spec = next_cast_copy_trigger_spec(trigger)
    cast = SpellCastEvent.from_context(context)
    if cast.controller != trigger.controller:
        return False
    if spec.scope == "instant_or_sorcery" and not {"instant", "sorcery"}.intersection(cast.types):
        return False
    if spec.scope == "lesson" and "lesson" not in cast.subtypes:
        return False
    return spec.maximum_mana_value is None or (
        cast.mana_value is not None and cast.mana_value <= spec.maximum_mana_value
    )


def bind_next_cast_copy_occurrence(host, trigger, item, context):
    from ..event_spell_copy_model import next_cast_copy_trigger_spec
    from .spell_cast_events import SpellCastEvent

    spec = next_cast_copy_trigger_spec(trigger)
    cast = SpellCastEvent.from_context(context)
    target = next((spell for spell in host.state.stack if spell.ref == cast.stack_ref), None)
    if target is None:
        raise ValueError("Next-cast copy lost the committed spell")
    source = capture_event_spell_copy_source(host, target)
    if source.logical_object_id != cast.logical_object_id:
        raise ValueError("Next-cast copy occurrence changed its spell incarnation")
    item.context.update({
        "stack": target.ref,
        EVENT_SPELL_COPY_CONTEXT: source.to_dict(),
        "dynamic_effects": [event_spell_copy_effect() for _ in range(spec.copies)],
    })
