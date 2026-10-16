from __future__ import annotations

"""Current and immediate last-known source facts for represented stack damage."""

from copy import deepcopy
from dataclasses import replace
from collections.abc import Mapping
from types import SimpleNamespace

from ..damage_source import DamageError, DamageSourceSnapshot
from ..entry_copy import copiable_oracle_id
from ..trigger_batches import PendingTriggerBatch
from .damage_snapshots import source_snapshot


SOURCE_DAMAGE_CONTEXT = 'source_damage_snapshot'
_OPERATIONS = {'damage', 'damage_each_opponent', 'damage_fixed_set'}


def _public_source_snapshot(host, source, *, characteristics=None):
    controller = source.controller if getattr(source, 'zone', 'battlefield') in {'battlefield', 'stack'} else source.owner
    snapshot = source_snapshot(host, source.ref, controller=controller,
        source_card=source, characteristics=characteristics)
    if snapshot.zone not in {'battlefield', 'stack'}:
        snapshot = replace(snapshot, controller=snapshot.owner)
    return replace(snapshot, oracle_id=None) if getattr(source, 'face_down', False) else snapshot


def needs_stack_damage_source(value):
    if isinstance(value, Mapping):
        return (value.get('op') in _OPERATIONS and value.get('source') == '$source') or any(
            needs_stack_damage_source(child) for child in value.values())
    return isinstance(value, (list, tuple)) and any(needs_stack_damage_source(child) for child in value)


def fixed_damage_source_context(host, source, effects, *, characteristics=None):
    if not needs_stack_damage_source(effects):
        return {}
    retained = characteristics.get(SOURCE_DAMAGE_CONTEXT) if isinstance(characteristics, Mapping) else None
    if retained is not None:
        snapshot = DamageSourceSnapshot.from_dict(dict(retained))
        if snapshot.object_id != source.object_id or snapshot.logical_object_id != source.logical_object_id:
            raise DamageError('Damage source departure snapshot refers to another incarnation')
    else:
        snapshot = _public_source_snapshot(host, source, characteristics=characteristics)
    return {SOURCE_DAMAGE_CONTEXT: snapshot.to_dict()}


def capture_damage_source_characteristics(host, source):
    data = deepcopy(host._effective_card_data(source))
    programs = host.semantics.programs_for_oracle(copiable_oracle_id(source))
    if (any(needs_stack_damage_source((program.effects, program.target_schema)) for program in programs)
            or needs_stack_damage_source(data.get('ability_fragments', ()))):
        data.update(fixed_damage_source_context(host, source, ({'op':'damage', 'source':'$source'},), characteristics=data))
    return data


def _frame_effects(host, item):
    program = host.semantics.get(item.get('semantic_key')) if item.get('semantic_key') else None
    context = item.get('context', {})
    return ((program.effects, program.target_schema) if program is not None else (),
        context.get('dynamic_effects', ()), context.get('cast_option_effects', ()))


def pin_stack_damage_departures(host, cards, *, error_type=DamageError):
    """Prepare all source facts before updating stack and waiting occurrences."""
    try:
        departing = {(card.object_id, card.logical_object_id):card for card in cards}
        updates = {}; snapshots = {}
        frames = [item.to_dict() for item in host.state.stack]
        for batch in host.state.pending_trigger_batches:
            frames.extend(item for group in batch.to_dict()['groups'] for item in group['items'])
        for item in frames:
            context = item.get('context', {})
            prior = context.get(SOURCE_DAMAGE_CONTEXT)
            if not needs_stack_damage_source(_frame_effects(host, item)) and SOURCE_DAMAGE_CONTEXT not in context:
                continue
            source_id = item.get('source_object_id') or item.get('card_object_id')
            if SOURCE_DAMAGE_CONTEXT in context:
                if not isinstance(prior, Mapping):
                    raise DamageError('Damage source frame snapshot is malformed')
                known = DamageSourceSnapshot.from_dict(dict(prior))
                if known.object_id != source_id or context.get('source_logical_object_id', known.logical_object_id) != known.logical_object_id:
                    raise DamageError('Damage source frame refers to another incarnation')
            identity = context.get('source_logical_object_id') or (prior.get('logical_object_id') if isinstance(prior, Mapping) else None)
            key = (source_id, identity)
            source = departing.get(key)
            if source is None:
                continue
            if key not in snapshots:
                snapshots[key] = _public_source_snapshot(host, source).to_dict()
            updates[item['ref']] = snapshots[key]
        pending = []
        for index, batch in enumerate(host.state.pending_trigger_batches):
            data = batch.to_dict(); changed = False
            for group in data['groups']:
                for item in group['items']:
                    if item['ref'] in updates:
                        item.setdefault('context', {})[SOURCE_DAMAGE_CONTEXT] = deepcopy(updates[item['ref']]); changed = True
            if changed:
                pending.append((index, PendingTriggerBatch.from_dict(data)))
        for item in host.state.stack:
            if item.ref in updates:
                item.context[SOURCE_DAMAGE_CONTEXT] = deepcopy(updates[item.ref])
        from ..trigger_processing import replace_pending_trigger_batch
        for index, batch in pending:
            replace_pending_trigger_batch(host, index, batch)
    except (KeyError, TypeError, ValueError) as exc:
        raise error_type(str(exc)) from exc


def pin_creature_power_damage_departures(host, cards, *, error_type=DamageError):
    """Refresh source power before any member of a departure batch changes."""
    from ..creature_power_damage import (
        capture_creature_power_damage_source_lki, _source_lki_value,
        CREATURE_POWER_DAMAGE_LKI_CONTEXT, CREATURE_POWER_DAMAGE_OPERATION,
    )
    try:
        departing = {(card.object_id, card.logical_object_id):card for card in cards}
        source_ids = {key[0] for key in departing}
        frames = [item.to_dict() for item in host.state.stack]
        for batch in host.state.pending_trigger_batches:
            frames.extend(item for group in batch.to_dict()['groups'] for item in group['items'])
        snapshots = {}; updates = {}
        for item in frames:
            context = item.get('context', {})
            source_id = item.get('source_object_id') or item.get('card_object_id')
            if source_id not in source_ids or CREATURE_POWER_DAMAGE_LKI_CONTEXT not in context:
                continue
            prior = context[CREATURE_POWER_DAMAGE_LKI_CONTEXT]
            if not isinstance(prior, Mapping) or not isinstance(prior.get('source'), Mapping):
                raise DamageError('Creature-power frame snapshot is malformed')
            parsed = _source_lki_value(prior['source'].get('ref'), prior)
            if parsed is None:
                raise DamageError('Creature-power frame snapshot is malformed')
            known = parsed[1]
            if known.object_id != source_id or context.get('source_logical_object_id', known.logical_object_id) != known.logical_object_id:
                raise DamageError('Creature-power frame refers to another incarnation')
            key = (source_id, known.logical_object_id)
            source = departing.get(key)
            if source is None:
                continue
            if known.ref != source.ref:
                raise DamageError('Creature-power frame public source reference changed')
            if key not in snapshots:
                value = capture_creature_power_damage_source_lki(host, source,
                    ({'op': CREATURE_POWER_DAMAGE_OPERATION, 'source': '$source'},))
                if value is None:
                    raise DamageError('Creature-power departure value is unrepresented')
                snapshots[key] = value
            updates[item['ref']] = snapshots[key]
        pending = []
        for index, batch in enumerate(host.state.pending_trigger_batches):
            data = batch.to_dict(); changed = False
            for group in data['groups']:
                for item in group['items']:
                    if item['ref'] in updates:
                        item.setdefault('context', {})[CREATURE_POWER_DAMAGE_LKI_CONTEXT] = deepcopy(updates[item['ref']]); changed = True
            if changed:
                pending.append((index, PendingTriggerBatch.from_dict(data)))
        for item in host.state.stack:
            if item.ref in updates:
                item.context[CREATURE_POWER_DAMAGE_LKI_CONTEXT] = deepcopy(updates[item.ref])
        from ..trigger_processing import replace_pending_trigger_batch
        for index, batch in pending:
            replace_pending_trigger_batch(host, index, batch)
    except (KeyError, TypeError, ValueError) as exc:
        raise error_type(str(exc)) from exc


def bound_stack_damage_source(host, effect, item):
    """Bind only the source; targets and amounts keep their ordinary owners."""
    if effect.get('op') not in _OPERATIONS or effect.get('source') != '$source':
        return {}
    source_id = getattr(item, 'source_object_id', None) or getattr(item, 'card_object_id', None)
    if not source_id:
        return {}
    source = host.state.cards.get(source_id)
    raw = item.context.get(SOURCE_DAMAGE_CONTEXT)
    if raw is not None and not isinstance(raw, Mapping):
        raise DamageError('Damage source frame snapshot is malformed')
    snapshot = DamageSourceSnapshot.from_dict(dict(raw)) if isinstance(raw, Mapping) else None
    expected = item.context.get('source_logical_object_id') or (snapshot.logical_object_id if snapshot else None)
    if snapshot is not None and (snapshot.object_id != source_id or snapshot.logical_object_id != expected):
        raise DamageError('Damage source frame refers to another incarnation')
    current = source is not None and (expected is None or source.logical_object_id == expected)
    if current and (snapshot is None or source.zone == snapshot.zone):
        snapshot = _public_source_snapshot(host, source)
    elif snapshot is None:
        raise DamageError('Damage source last-known information is unavailable')
    return {'source': snapshot.ref, 'source_snapshot': snapshot.to_dict()}


def refresh_stack_damage_source(host, snapshot, *, sealed=False):
    """Read source facts at the damage instruction, after any paid prefix."""
    if sealed:
        return snapshot
    item = next((value for value in reversed(getattr(host.state, 'stack', ()))
                 if value.context.get('currently_resolving')), None)
    if item is None or (item.source_object_id or item.card_object_id) != snapshot.object_id:
        return snapshot
    context = dict(item.context)
    expected = context.get('source_logical_object_id') or snapshot.logical_object_id
    if expected != snapshot.logical_object_id:
        return snapshot
    context.setdefault(SOURCE_DAMAGE_CONTEXT, snapshot.to_dict())
    context['source_logical_object_id'] = expected
    frame = SimpleNamespace(source_object_id=item.source_object_id, card_object_id=item.card_object_id,
        controller=item.controller, context=context)
    bound = bound_stack_damage_source(host, {'op':'damage','source':'$source'}, frame)
    return DamageSourceSnapshot.from_dict(bound['source_snapshot']) if bound else snapshot
