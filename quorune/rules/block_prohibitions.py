from __future__ import annotations

"""Read-only combat prohibition owner shared by offers and block validation."""

from ..errors import GameRuleError
from .temporary_declaration_restrictions import current_declaration_restrictions
from .source_block_pairs import source_block_pair_prohibits


def block_prohibition_verdict(host, attacker, blocker):
    by_ref = {card.ref: card for card in host.state.cards.values()}
    for participant in current_declaration_restrictions(host, error_type=GameRuleError):
        source = participant.source
        for template in (participant.template,):
            if 'block' not in template.declarations or template.mode != 'prohibit':
                continue
            applies = {
                'self': source.object_id == blocker.object_id,
                'attached': source.attached_to == blocker.object_id,
                'attached_option': source.attached_to == attacker.object_id,
                'source_opponents': blocker.controller != source.controller,
                'source_option': source.object_id == attacker.object_id,
                'global': True,
            }[template.scope]
            if not applies:
                continue
            if not host._declaration_option_matches_relation(
                template, kind='block', source=source, option=attacker.ref, by_ref=by_ref,
            ):
                continue
            if template.condition is not None and host._declaration_condition_holds(
                template.condition, kind='block', source=source,
                variable=blocker.ref, option=attacker.ref, by_ref=by_ref,
            ) != template.applies_when_condition:
                continue
            if (host._matches_declaration_predicate(blocker, template.subject, source=source)
                    and host._matches_declaration_predicate(attacker, template.opposing, source=source)):
                return (False, f'declaration_restriction:{template.template_id}')
    if source_block_pair_prohibits(host.state, attacker, blocker):
        return False, 'declaration_restriction:source_block_pair'
    return None
