from __future__ import annotations

"""First named spell resolution schedules a source-independent recurring copy."""

from ..errors import GameRuleError
from ..paradigm_model import (PARADIGM_CAPABILITY, PARADIGM_MECHANIC, PARADIGM_HANDLER,
    PARADIGM_TRIGGER_KEY, PARADIGM_CAST_PRODUCER, PARADIGM_SNAPSHOT_FIELD,
    RESOLUTION_NAME_HISTORY_CAPABILITY, RETAINED_CARD_COPY_CAPABILITY, RetainedSpellCopySnapshot)
from ..replacement.immutable import thaw_value
from ..spell_resolution_name_model import SpellResolutionNameFact

PARADIGM_TEMPLATE = 'first-resolution-recurring-copy-v1'
PARADIGM_CAPABILITIES = (PARADIGM_CAPABILITY, RESOLUTION_NAME_HISTORY_CAPABILITY,
    RETAINED_CARD_COPY_CAPABILITY, 'zone.exile.resolving_spell')


def paradigm_runtime_dependencies(program):
    declarations = [d for d in program.handlers if d.get('handler_id') == PARADIGM_HANDLER]
    marked = PARADIGM_MECHANIC in program.coverage
    wrapped = str(program.provenance.get('template_id') or '').startswith(PARADIGM_TEMPLATE + '+')
    if not declarations and not marked and not wrapped:
        return ()
    if (len(declarations) != 1 or not marked or not wrapped or program.event != 'resolve'
            or program.active_zone != 'stack' or not program.ability_id.startswith('spell:')
            or program.destination != 'exile' or not program.effects):
        raise ValueError('Paradigm requires a complete resolving spell and exile destination')
    from ..semantic_runtime.paradigm import ParadigmSpellDeclarationHandler
    ParadigmSpellDeclarationHandler().validate(declarations[0])
    return PARADIGM_CAPABILITIES


def require_paradigm_program(host, program):
    if (program is None or not paradigm_runtime_dependencies(program)
            or not set(PARADIGM_CAPABILITIES) <= set(program.capability_dependencies)
            or not host.semantic_program_is_current_trusted(program)):
        raise GameRuleError('The complete Paradigm spell program is no longer trusted')
    return program


def schedule_paradigm_resolution(host, item):
    if item.kind not in {'spell', 'spell_copy'}:
        return
    program = host.semantics.get(item.semantic_key)
    if program is None or not paradigm_runtime_dependencies(program):
        return
    require_paradigm_program(host, program)
    ledger = host.state.spell_resolution_name_ledger
    if ledger is None or item.ref not in ledger.pending:
        raise GameRuleError('Paradigm requires known complete lifetime resolution history')
    fact = SpellResolutionNameFact.from_dict(ledger.pending[item.ref])
    if not fact.require_known_first():
        return
    card = host.state.cards.get(item.card_object_id)
    if card is None or card.zone != 'stack' or fact.controller != item.controller:
        raise GameRuleError('Paradigm lost its resolving spell incarnation or controller')
    from ..token_copy_faces import spell_copy_characteristics
    from ..entry_copy import copiable_oracle_id, COPIABLE_ORACLE_FIELD, COPIABLE_FACE_FIELD
    values = spell_copy_characteristics(host, card)
    oracle_id = copiable_oracle_id(card, values)
    face_id = values.get(COPIABLE_FACE_FIELD) or item.chosen_face or 'front'
    values = {**values, COPIABLE_ORACLE_FIELD: oracle_id, COPIABLE_FACE_FIELD: face_id}
    snapshot = RetainedSpellCopySnapshot(oracle_id, face_id, values)
    from ..trigger_processing import schedule_delayed_trigger
    schedule_delayed_trigger(host, controller=item.controller, label='Paradigm: ' + values['name'],
        event_kind='step.begin', condition={'phase': 'precombat_main', 'step': 'main', 'player': item.controller},
        stack_template={'semantic_key': PARADIGM_TRIGGER_KEY, 'label': 'Paradigm: ' + values['name'],
            'context': {PARADIGM_SNAPSHOT_FIELD: snapshot.to_dict()}}, once=False)


def begin_paradigm_copy_cast(host, item):
    if item.kind != 'triggered_ability' or item.semantic_key != PARADIGM_TRIGGER_KEY:
        raise GameRuleError('A recurring copy requires its original delayed trigger')
    snapshot = RetainedSpellCopySnapshot.from_dict(item.context.get(PARADIGM_SNAPSHOT_FIELD))
    program = host.semantics.get(f'{snapshot.oracle_id}:spell:{snapshot.face_id}')
    require_paradigm_program(host, program)
    copied = host._create_copy_object(controller=item.controller, source=None,
        characteristics=thaw_value(snapshot.characteristics), object_kind='card_copy', zone='exile')
    options = host._one_shot_exile_cast_options(actor=item.controller, card=copied, maximum_mana_value=None)
    if options:
        host._begin_one_shot_exile_cast_choice(item=item, card=copied, cleanup_cards=(),
            maximum_mana_value=None, producer=PARADIGM_CAST_PRODUCER)
    else:
        from ..stack_resolution import complete_stack_resolution
        host.move_card(copied.object_id, 'outside', reason='Recurring copy has no legal cast', log=False)
        complete_stack_resolution(host, item=item, destination=None, prepared_replacement=None)
        host._grant_priority(host.state.active_player)


def is_closed_paradigm_program(program, recursive_is_closed):
    from dataclasses import replace
    if (not paradigm_runtime_dependencies(program)
            or not set(PARADIGM_CAPABILITIES) <= set(program.capability_dependencies)):
        return False
    prefix = PARADIGM_TEMPLATE + '+'
    body = replace(program,
        handlers=[d for d in program.handlers if d.get('handler_id') != PARADIGM_HANDLER],
        coverage=[m for m in program.coverage if m != PARADIGM_MECHANIC],
        provenance={**program.provenance, 'template_id': program.provenance['template_id'][len(prefix):]})
    return recursive_is_closed(body)
