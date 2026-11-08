from __future__ import annotations

"""Read the Defender-only exception after the characteristic query finishes."""

from dataclasses import replace

from ..attachments import attached_object_identity
from ..card_program_faces import program_matches_face
from ..continuous_effect_state import active_resolution_declaration_rule_effects
from ..declaration_rule_effects import ResolutionDeclarationRuleEffect
from ..defender_permission import (
    DEFENDER_DECLARATION_EVENT, DEFENDER_DECLARATION_HANDLER, DEFENDER_ATTACHMENT_HANDLER,
    DefenderAttackPermission, DefenderAttachmentScope,
)
from ..semantic_runtime.current_ability_components import program_has_current_ability_fragments


def current_defender_declaration_permission(host, card, characteristics):
    if card.zone != 'battlefield' or card.phased_out or card.controller not in host.active_seats:
        return False
    if _resolved_permission_applies(host, card):
        return True
    for rule in _current_defender_declarations(host, card, characteristics):
        if rule is DefenderAttachmentScope.ENCHANTED_OR_EQUIPPED_SOURCE:
            row = host._public_object_query_result(card)
            if row.enchanted or row.equipped:
                return True
        elif not isinstance(rule, DefenderAttachmentScope) and host._fixed_public_state_condition_holds(
            card, rule.source_condition, require_available=True,
        ):
            return True
    return _equipped_permission_applies(host, card)


def _current_defender_declarations(host, source, characteristics):
    record = host.card_record(source)
    if record is None:
        return
    from ..semantic_runtime.continuous_components import default_continuous_effect_component_registry
    registry = default_continuous_effect_component_registry()
    programs = host.semantics.runtime_handler_programs_for_oracle(record.oracle_id,
        active_zone='battlefield', event=DEFENDER_DECLARATION_EVENT)
    for program in programs:
        if (not host.semantic_program_is_current_trusted(program)
                or not program_matches_face(record, program, source)
                or not program_has_current_ability_fragments(program, characteristics)):
            continue
        for descriptor in program.handlers:
            if descriptor.get('handler_id') in {DEFENDER_DECLARATION_HANDLER, DEFENDER_ATTACHMENT_HANDLER}:
                yield from registry.lower(descriptor, None)


def _equipped_permission_applies(host, card):
    for source_id in card.attachments:
        source = host.state.cards.get(source_id)
        if source is None or source.controller not in host.active_seats:
            continue
        target = attached_object_identity(host.state.cards, source)
        if (target is None or target.object_id != card.object_id
                or target.logical_object_id != card.logical_object_id):
            continue
        data = host._effective_card_data(source)
        if "equipment" not in host._type_parts(str(data.get("type_line") or ""))[1]:
            continue
        if DefenderAttachmentScope.EQUIPPED_CREATURE in _current_defender_declarations(host, source, data):
            return True
    return False


def _resolved_permission_applies(host, card):
    permissions = [
        effect for effect in host.state.continuous_effects or ()
        if isinstance(effect, ResolutionDeclarationRuleEffect)
        and isinstance(effect.restriction, DefenderAttackPermission)
    ]
    if not permissions:
        return False
    row = None
    if any(effect.live_query is not None for effect in permissions):
        row = replace(host._public_object_query_result(card), keywords=tuple(sorted(host._combat_keywords(card))))
    return any(
        isinstance(effect.restriction, DefenderAttackPermission)
        for effect in active_resolution_declaration_rule_effects(host.state, card, query_row=row)
    )
