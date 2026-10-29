from __future__ import annotations

"""Each current layer-6 granted static cost fragment is one independent source."""

from ..ability_fragments import StaticComponentSpec, canonical_ability_fragments
from ..errors import GameRuleError
from ..static_component_scope import GRANTED_STATIC_COST_EVENT
from ..targeted_spell_costs import TARGETED_SPELL_COST_HANDLER, TargetedSpellCostSource, is_granted_source_tax


def granted_static_cost_sources(host, caster, recipients):
    from ..semantic_runtime.targeted_spell_costs import TargetedSpellCostHandler
    handler = TargetedSpellCostHandler()
    sources = []
    for recipient in recipients:
        if recipient.zone != 'battlefield' or recipient.phased_out or recipient.controller not in host.active_seats:
            continue
        characteristics = host._effective_card_data(recipient)
        for ordinal, fragment in enumerate(canonical_ability_fragments(characteristics.get('ability_fragments', ()))):
            if not isinstance(fragment, StaticComponentSpec) or fragment.schema_version != 2:
                continue
            program = host.semantics.get(fragment.semantic_key)
            if (program is None or not host.semantic_program_is_current_trusted(program)
                    or program.provenance.get('granted_only') is not True or program.active_zone != 'battlefield'
                    or program.event != fragment.event or fragment.event != GRANTED_STATIC_COST_EVENT
                    or program.effects or len(program.handlers) != 1
                    or program.handlers[0].get('handler_id') != TARGETED_SPELL_COST_HANDLER):
                raise GameRuleError('Current granted static cost references an unknown, untrusted or different child')
            try:
                spec = handler.validate(program.handlers[0])
            except (TypeError, ValueError, KeyError) as exc:
                raise GameRuleError('Current granted static cost has an invalid descriptor') from exc
            if not is_granted_source_tax(spec):
                raise GameRuleError('Granted static cost child has an unrepresented subject')
            if caster == recipient.controller:
                continue
            sources.append(TargetedSpellCostSource(recipient.ref, recipient.logical_object_id, recipient.controller,
                f'{recipient.logical_object_id}:grant:{program.key}:{ordinal}', spec, (recipient.ref,)))
    return tuple(sources)
