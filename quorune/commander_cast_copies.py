from __future__ import annotations

"""Commander-cast copy occurrences delegated to the canonical spell-copy owner."""

import copy
from collections.abc import Mapping

from .ability_fragments import SpellCastKeywordTriggerKind, SpellCastKeywordTriggerSpec
from .commander_cast_counts import controller_commander_cast_count
from .errors import StateInvariantError
from .model import StackItem
from .semantic_runtime.commander_cast_copies import COMMANDER_CAST_COPY_HANDLER, COMMANDER_CAST_COPY_KEY


def commander_cast_copy_trigger_items(host, *, spell, card, program):
    from .storm import _selected_face_id, spell_copy_template
    from .semantic_runtime.commander_cast_copies import CommanderCastCopyHandler

    record = host.card_record(card)
    if record is None:
        return ()
    specs = []
    for candidate in host.semantics.programs_for_oracle(record.oracle_id, active_zone='stack'):
        if (not host.semantic_program_is_current_trusted(candidate)
                or str(candidate.provenance.get('face_id') or '') != _selected_face_id(record, card)):
            continue
        specs.extend(CommanderCastCopyHandler().validate(descriptor) for descriptor in candidate.handlers
            if descriptor.get('handler_id') == COMMANDER_CAST_COPY_HANDLER)
    if not specs:
        return ()
    template = spell_copy_template(host, spell, program)
    from .rules.casting.spell_text import CAST_TEXT_COPY_CHARACTERISTICS
    characteristics = host._spell_copy_characteristics(card)
    template[CAST_TEXT_COPY_CHARACTERISTICS] = copy.deepcopy(characteristics)
    types, _, _ = host._type_parts(str(characteristics.get('type_line') or ''))
    permanent = bool(types & {'artifact', 'battle', 'creature', 'enchantment', 'land', 'planeswalker'})
    if permanent and not host._trusted_generic_spell(record):
        raise StateInvariantError('Commander-copy permanent spell lacks complete trusted semantics')
    template['copy_permanent_spell'] = permanent
    result = []
    for index, spec in enumerate(specs, start=1):
        ref = host._next_ref('S')
        result.append(StackItem(stack_id=host._stable_runtime_id('stack', ref), ref=ref,
            kind='triggered_ability', controller=spell.controller, label=f'{spell.label} — Commander cast copies',
            source_object_id=card.object_id, semantic_key=COMMANDER_CAST_COPY_KEY, visibility=list(host.seats),
            context={'commander_cast_spec': spec.to_dict(), 'commander_cast_instance': index,
                'copy_template': copy.deepcopy(template)}))
    return tuple(result)


def commander_cast_copy_template(item):
    from .semantic_runtime.commander_cast_copies import COMMANDER_CAST_COPY_KINDS

    if item.semantic_key != COMMANDER_CAST_COPY_KEY:
        raise StateInvariantError('Commander-cast copy owner received another stack item')
    try:
        spec = SpellCastKeywordTriggerSpec.from_dict(item.context.get('commander_cast_spec'))
    except (TypeError, ValueError) as exc:
        raise StateInvariantError('Commander-cast copy trigger specification is malformed') from exc
    instance = item.context.get('commander_cast_instance')
    template = item.context.get('copy_template')
    if (spec.kind not in COMMANDER_CAST_COPY_KINDS or type(instance) is not int or instance < 1
            or not isinstance(template, Mapping) or 'copy_count' in item.context):
        raise StateInvariantError('Commander-cast copy trigger context is malformed')
    from .rules.casting.spell_text import CAST_TEXT_COPY_CHARACTERISTICS
    if (not isinstance(template.get(CAST_TEXT_COPY_CHARACTERISTICS), Mapping)
            or type(template.get('copy_permanent_spell')) is not bool):
        raise StateInvariantError('Commander-cast copy template lost its immutable spell information')
    template = copy.deepcopy(dict(template))
    template['commander_copy_new_targets'] = spec.kind is SpellCastKeywordTriggerKind.COMMANDER_CAST_COPY_NEW_TARGETS
    return template


def validated_commander_cast_copy_trigger(host, item):
    template = commander_cast_copy_template(item)
    try:
        count = controller_commander_cast_count(host.state, item.controller)
    except ValueError as exc:
        raise StateInvariantError(str(exc)) from exc
    return count, template


def completed_commander_cast_copy_spec(item, payload):
    """Validate the issued selection's frozen count without reading game history again."""
    from .errors import GameRuleError

    template = commander_cast_copy_template(item)
    if (not isinstance(payload, Mapping) or set(payload) != {'copy_count', 'copy_template'}
            or type(payload.get('copy_count')) is not int or payload['copy_count'] < 0
            or payload['copy_template'] != template):
        raise GameRuleError('Commander-cast copy selection lost its frozen specification')
    return payload['copy_count'], template
