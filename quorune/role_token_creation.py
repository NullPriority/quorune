from __future__ import annotations

"""Replacement-aware attached Role commit through the canonical token owner."""

from dataclasses import replace
from typing import Any, Mapping

from .replacement.immutable import thaw_value


def ordered_role_plans(plans, submitted_order, first_timestamp):
    from .token_creation import TokenCreationError
    order = tuple(submitted_order)
    if (len(order) != len(plans) or any(type(index) is not int for index in order)
            or sorted(order) != list(range(len(plans)))):
        raise TokenCreationError('Role timestamp order must be a complete permutation')
    for plan in plans:
        characteristics = plan.annotations.get('token_characteristics', {})
        if plan.aura_target_ref is None or characteristics.get('type_line') != 'Token Enchantment — Aura Role':
            raise TokenCreationError('Ordered attached token timestamps require only Role tokens')
    rank = {index: position for position, index in enumerate(order)}
    return tuple(replace(plan, zone_timestamp=first_timestamp + rank[index])
        for index, plan in enumerate(plans))


def create_ordered_role_token(host: Any, effect: Mapping[str, Any], *, actor: str, reason: str):
    from .token_creation import TokenCreationError, _token_specification_inputs, _token_replacement_effects, _resolved_token_specs, _commit_resolved_token_specs
    target_ref = effect.get('aura_target_ref')
    if target_ref is None:
        return []
    from .ability_fragments import StaticComponentSpec, GrantedTriggeredAbilitySpec, canonical_ability_fragments
    for fragment in canonical_ability_fragments(effect['characteristics']['ability_fragments']):
        if isinstance(fragment, (StaticComponentSpec, GrantedTriggeredAbilitySpec)):
            program = host.semantics.get(fragment.semantic_key)
            if (program is None or not program.provenance.get('granted_only')
                    or not host.semantic_program_is_current_trusted(program)):
                raise TokenCreationError('Role child program is missing or no longer trusted')
    row = host._resolve_object(actor, str(target_ref), zones={'battlefield'})
    if row.logical_object_id != effect.get('_attachment_incarnation'):
        raise TokenCreationError('Role attachment subject changed before creation')
    specs, types, subtypes, sources = _token_specification_inputs(host, actor,
        name=str(effect['name']), quantity=1, tapped=False, attacking=None, attacking_groups=(),
        battle_protector=None, copy_of=None, copy_source_zone='battlefield', copy_snapshot=None,
        characteristics=dict(effect['characteristics']), temporary_keywords=(), aura_target_ref=str(target_ref))
    replacements = _token_replacement_effects(host, actor, types, subtypes, sources)
    resolved = _resolved_token_specs(host, actor, quantity=1, token_specs=specs,
        created_types=types, created_subtypes=subtypes, replacement_effects=replacements,
        replacement_selections=tuple(effect.get('_replacement_selections', ())))
    expected = tuple(thaw_value(value) for value in effect.get('_role_preview', ()))
    actual = tuple({**thaw_value(spec), 'aura_target_ref': None} for spec in resolved.specs)
    if actual != expected:
        raise TokenCreationError('Role replacement preview changed before creation')
    return _commit_resolved_token_specs(host, actor, resolved=resolved,
        base_name=str(effect['name']), base_quantity=1, reason=reason,
        role_timestamp_order=tuple(effect['_role_order']))


__all__ = ['ordered_role_plans', 'create_ordered_role_token']
