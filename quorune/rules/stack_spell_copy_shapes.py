from __future__ import annotations

"""Exact typed source shape for the existing stack-copy choice owner."""

from ..stack_spell_copy_model import (
    FixedStackSpellCopySpec, STACK_SPELL_COPY_CAPABILITY, STACK_SPELL_COPY_MECHANIC,
)


def fixed_stack_spell_copy_node_capabilities(*, effects, target_schema, mechanic_ids):
    from ..stack_spell_copy_model import (QUALIFIED_STACK_COPY_MECHANIC, QUALIFIED_STACK_COPY_CAPABILITY,
        qualified_stack_copy_specs)
    mechanics = set(mechanic_ids)
    for spec in qualified_stack_copy_specs():
        if tuple(effects) == spec.effects and target_schema == spec.target_schema:
            from ..ability_source_information import ability_source_filter_dependencies
            return (QUALIFIED_STACK_COPY_CAPABILITY, 'target.revalidate_resolution',
                *ability_source_filter_dependencies(target_schema))
    if QUALIFIED_STACK_COPY_MECHANIC in mechanics:
        return ()
    from ..event_spell_copy_model import EVENT_SPELL_COPY_CAPABILITY,EVENT_SPELL_COPY_MECHANIC,event_spell_copy_effect
    if EVENT_SPELL_COPY_MECHANIC in mechanics:
        return (EVENT_SPELL_COPY_CAPABILITY,) if any(tuple(effects)==(event_spell_copy_effect(retain_targets_only=retain),) for retain in (False,True)) and target_schema is None else ()
    if STACK_SPELL_COPY_MECHANIC not in mechanics or len(effects) != 1:
        return ()
    for controlled in (False, True):
        spec = FixedStackSpellCopySpec(controlled)
        if tuple(effects) == spec.effects and target_schema == spec.target_schema:
            return (STACK_SPELL_COPY_CAPABILITY, 'target.revalidate_resolution')
    return ()


def fixed_stack_spell_copy_program_is_closed(program):
    from ..card_programs.admission import program_has_declared_complete_source_admission
    if not program_has_declared_complete_source_admission(program):
        return False
    required = fixed_stack_spell_copy_node_capabilities(effects=program.effects,
        target_schema=program.target_schema, mechanic_ids=program.coverage)
    return bool(required) and set(required).issubset(program.capability_dependencies)


def qualified_stack_copy_runtime_dependencies(program):
    """Require the new authority from represented result and target data alone."""
    from collections.abc import Mapping
    from ..stack_spell_copy_model import qualified_stack_copy_specs, QUALIFIED_STACK_COPY_MECHANIC

    effects = tuple(getattr(program, 'effects', ()))
    schema = getattr(program, 'target_schema', None)
    if (not isinstance(schema, Mapping) or not any(effect.get('op') == 'copy_stack_item'
            and effect.get('stack') == '$target.0' for effect in effects)):
        return ()
    for spec in qualified_stack_copy_specs():
        if effects == spec.effects and schema == spec.target_schema:
            return fixed_stack_spell_copy_node_capabilities(effects=effects, target_schema=schema,
                mechanic_ids=(QUALIFIED_STACK_COPY_MECHANIC,))
    permanent = bool(set(schema.get('types_any') or ()) &
        {'artifact', 'battle', 'creature', 'enchantment', 'land', 'planeswalker'})
    if schema.get('categories') == ['ability'] or permanent:
        raise ValueError('Qualified stack-copy target or retained-target policy is malformed')
    return ()


def reviewed_stack_copy_with_current_policy(current, old):
    """Compare only complete, closed compiler copies with exact legacy shapes.

    The old permanent-spell row omitted the printed retained-target policy.
    Only an admitted current source may replace that row, using normal strict
    binding and the shared source-hash/catalog-price checks in its caller.
    """
    from dataclasses import replace
    from copy import deepcopy
    from ..stack_spell_copy_model import qualified_stack_copy_specs
    if not fixed_stack_spell_copy_program_is_closed(current):
        return None
    if tuple(old.effects) != ({'op': 'copy_stack_item', 'stack': '$target.0'},):
        return None
    specs = (*qualified_stack_copy_specs(), FixedStackSpellCopySpec(True))
    for spec in specs:
        if tuple(current.effects) != spec.effects or current.target_schema != spec.target_schema:
            continue
        legacy = deepcopy(spec.target_schema)
        legacy.pop('source_exclusion')
        legacy['controller'] = legacy.pop('controller_relation')
        kind = getattr(spec, 'target_kind', None)
        if kind == 'activated_or_triggered_ability' and spec.source_filter is None:
            legacy.pop('predicate')
        elif kind == 'permanent_spell':
            legacy.pop('types_any')
            legacy['predicate'] = 'permanent_card'
        elif isinstance(spec, FixedStackSpellCopySpec):
            legacy['types_any'] = ['Instant', 'Sorcery']
        if old.target_schema != legacy:
            continue
        handlers = deepcopy(old.handlers)
        if len(handlers) != 1 or handlers[0].get('handler_id') != 'activation.catalog.pinned.v1':
            return None
        if handlers[0].get('ability', {}).get('target_schema') != old.target_schema:
            return None
        handlers[0]['ability']['target_schema'] = deepcopy(current.target_schema)
        return replace(old, target_schema=deepcopy(current.target_schema),
            effects=deepcopy(current.effects), handlers=handlers)
    return None
