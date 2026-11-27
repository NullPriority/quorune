from __future__ import annotations

"""Exact typed source shape for the existing stack-copy choice owner."""

from ..stack_spell_copy_model import (
    FixedStackSpellCopySpec, STACK_SPELL_COPY_CAPABILITY, STACK_SPELL_COPY_MECHANIC,
    PERMANENT_SPELL_COPY_CAPABILITY, PERMANENT_SPELL_COPY_FIELD, PermanentSpellCopySpec,
    FIXED_PERMANENT_SPELL_COPY_COUNT_FIELD, FixedQualifiedStackCopySpec,
)


def permanent_spell_copy_node_dependencies(effect, target_schema):
    from ..event_spell_copy_model import EVENT_SPELL_COPY_CAPABILITY
    from ..replacement.immutable import FrozenMap
    from ..stack_spell_copy_model import QUALIFIED_STACK_COPY_CAPABILITY

    try:
        spec = PermanentSpellCopySpec.from_dict(effect.get(PERMANENT_SPELL_COPY_FIELD))
    except (TypeError, ValueError):
        return ()
    if effect != spec.effects[0] or FrozenMap(target_schema or {}) != FrozenMap(spec.target_schema or {}):
        return ()
    if spec.kind == "artifact":
        return (PERMANENT_SPELL_COPY_CAPABILITY, EVENT_SPELL_COPY_CAPABILITY)
    if spec.kind == "exile_cast_grant":
        from ..stack_spell_copy_model import EXILE_CAST_COPY_CAPABILITY

        return (EXILE_CAST_COPY_CAPABILITY, EVENT_SPELL_COPY_CAPABILITY,
                "continuous.resolution.fixed_keyword_zone_object", "token.creation.fixed_copy",
                "trigger.event.normalized_spell_cast", "trigger.placement.apnap")
    return (PERMANENT_SPELL_COPY_CAPABILITY, QUALIFIED_STACK_COPY_CAPABILITY, "target.revalidate_resolution")


def fixed_stack_spell_copy_node_capabilities(*, effects, target_schema, mechanic_ids):
    from ..stack_spell_copy_model import (QUALIFIED_STACK_COPY_MECHANIC, QUALIFIED_STACK_COPY_CAPABILITY,
        qualified_stack_copy_specs)
    if len(effects) == 1 and PERMANENT_SPELL_COPY_FIELD in effects[0]:
        return permanent_spell_copy_node_dependencies(effects[0], target_schema)
    from .announced_ability_copies import COPY_FIELD, node_dependencies

    if any(COPY_FIELD in leaf for leaf in effects):
        return node_dependencies(effects=effects, schema=target_schema)
    mechanics = set(mechanic_ids)
    if any(FIXED_PERMANENT_SPELL_COPY_COUNT_FIELD in effect for effect in effects):
        expected = FixedQualifiedStackCopySpec('permanent_spell', schema_version=2, copy_count=3)
        if (len(effects) == 1 and type(effects[0].get(FIXED_PERMANENT_SPELL_COPY_COUNT_FIELD)) is int
                and tuple(effects) == expected.effects and target_schema == expected.target_schema):
            return (QUALIFIED_STACK_COPY_CAPABILITY, 'target.revalidate_resolution')
        return ()
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

    from .announced_ability_copies import program_dependencies

    if required := program_dependencies(program):
        return required
    effects = tuple(getattr(program, 'effects', ()))
    schema = getattr(program, 'target_schema', None)
    from .effect_shape_walkers import nested_effects

    handlers = getattr(program, 'handlers', ())
    has_fixed_count = any(FIXED_PERMANENT_SPELL_COPY_COUNT_FIELD in effect
                          for effect in nested_effects((effects, handlers)))
    if has_fixed_count:
        expected = FixedQualifiedStackCopySpec('permanent_spell', schema_version=2, copy_count=3)
        if ((program.event, program.active_zone) != ('activate', 'battlefield')
                or program.event_condition is not None or program.cost_schema is None
                or any(handler.get('handler_id') != 'activation.catalog.pinned.v1' for handler in handlers)
                or len(effects) != 1 or type(effects[0].get(FIXED_PERMANENT_SPELL_COPY_COUNT_FIELD)) is not int
                or effects != expected.effects or schema != expected.target_schema):
            raise ValueError('Three permanent spell copies require the complete controlled-target activation')
        return fixed_stack_spell_copy_node_capabilities(
            effects=effects, target_schema=schema, mechanic_ids=(QUALIFIED_STACK_COPY_MECHANIC,),
        )
    if any(PERMANENT_SPELL_COPY_FIELD in effect for effect in effects):
        if len(effects) == 1:
            spec = PermanentSpellCopySpec.from_dict(effects[0][PERMANENT_SPELL_COPY_FIELD])
            if spec.kind in {'artifact', 'exile_cast_grant'}:
                return ()
            if required := permanent_spell_copy_node_dependencies(effects[0], schema):
                return required
        raise ValueError('Permanent spell copy needs its exact single controlled creature target')
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

    The reviewed permanent-spell shape omits the printed retained-target
    policy. The reviewed artifact-source predicate predates current/LKI source
    qualification. Existing records retain both reviewed shapes. The caller
    checks source hashes, catalog, price and strict binding for the current view.
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
            if (kind != 'activated_or_triggered_ability' or spec.source_filter != 'artifact'
                    or old.target_schema != {**legacy, 'predicate': 'artifact_source'}
                    or type(old.target_schema['count']) is not int):
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


def apply_permanent_spell_copy_exception(spec, characteristics):
    from ..characteristic_evaluation import type_parts
    from ..token_copy_recipes import TokenCopyExceptionSpec, apply_copy_exception

    types, _, _ = type_parts(str(characteristics.get("type_line") or ""))
    if "creature" not in types:
        raise ValueError("Permanent spell exception requires the represented copied creature spell")
    exception = (
        TokenCopyExceptionSpec(add_card_types=("artifact",))
        if spec.kind == "artifact" else TokenCopyExceptionSpec(remove_legendary=True)
    )
    return apply_copy_exception(characteristics, exception)
