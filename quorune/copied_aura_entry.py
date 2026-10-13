from __future__ import annotations

"""Copied Aura entry uses the existing private attachment choice owner."""

from .entry_copy import copied_entry_preview, serialize_prepared_copy_entry

STACK_COPY_AURA_ENTRY = '_stack_copy_aura_entry'


def prepare_copied_aura_entry_choice(host, *, item, card, prepared, destination,
        note, instruction_pointer, selections, error_type):
    from .aura import AuraEntryChoiceRequired, prepare_aura_entry
    from .aura.decisions import issue_aura_entry_choice
    from .replacement.immutable import thaw_value
    copied = copied_entry_preview(card, prepared)
    if copied is None or 'aura' not in host._type_parts(
            str(prepared.event.payload['entry_copy']['type_line']))[1]:
        return False
    spec = host._compiled_enchant_spec(copied)
    if spec is None:
        raise error_type('Copied Aura lacks a trusted Enchant descriptor')
    try:
        prepare_aura_entry(host, copied, spec=spec, controller=item.controller,
            target_ref=item.context.get('copied_entry_aura_target'), resolving_as_spell=False)
    except AuraEntryChoiceRequired as required:
        item.context['copied_aura_prepared_entry'] = serialize_prepared_copy_entry(prepared)
        issue_aura_entry_choice(host, item=item,
            effect={STACK_COPY_AURA_ENTRY: {'values':thaw_value(prepared.event.payload['entry_copy']),
                'selections':list(selections)}}, remaining=(), destination=destination,
            note=note, instruction_pointer=instruction_pointer, required=required)
        return True
    return False


def complete_copied_aura_entry_choice(host, *, item, restored, source, spec, selected, error_type):
    from dataclasses import replace
    from .aura import legal_aura_target_refs
    from .replacement.immutable import thaw_value
    effect = thaw_value(restored.effect)
    marker = effect.get(STACK_COPY_AURA_ENTRY)
    if marker is None:
        return False
    if (set(effect) != {STACK_COPY_AURA_ENTRY} or not isinstance(marker, dict)
            or set(marker) != {'values', 'selections'} or item.card_object_id != source.object_id
            or restored.remaining or not isinstance(marker['values'], dict)
            or not isinstance(marker['selections'], list)):
        raise error_type('Copied Aura stack-entry continuation is malformed')
    preview = replace(source, annotations={**source.annotations,'copy_overrides':marker['values']})
    current = legal_aura_target_refs(host, preview, spec, controller=restored.controller, as_target=False)
    if selected not in restored.advertised_targets or selected not in current:
        raise error_type('Copied Aura entry target is no longer legal')
    item.context['copied_entry_aura_target'] = selected
    host._continue_resolution(stack_ref=restored.stack_ref, effects=[],
        destination=restored.destination, note=restored.note,
        instruction_pointer=restored.instruction_pointer,
        entry_replacement_selections=marker['selections'])
    return True
