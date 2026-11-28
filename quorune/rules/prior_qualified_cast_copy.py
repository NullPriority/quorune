"""Copy the exact cast spell once for each earlier qualified cast by its controller."""

from collections.abc import Mapping

from ..event_spell_copy_model import EVENT_SPELL_COPY_CAPABILITY, EVENT_SPELL_COPY_CONTEXT, EventSpellCopySource
from ..public_history_query_model import PublicHistoryQuerySpec
from ..replacement.immutable import FrozenMap
from .effect_shape_walkers import nested_effects


PRIOR_CAST_COPY_FIELD = "prior_qualified_cast_copy"
PRIOR_CAST_COPY_COUNT = "_prior_qualified_cast_copy_count"
PRIOR_CAST_COPY_CAPABILITY = "stack.copy.prior_qualified_controller_casts"
PRIOR_CAST_COPY_MECHANIC = "prior-qualified-controller-cast-copy"
_CASTS = PublicHistoryQuerySpec("spell_cast", types_any=("instant", "sorcery"))
PRIOR_CAST_COPY_DEPENDENCIES = (
    PRIOR_CAST_COPY_CAPABILITY, EVENT_SPELL_COPY_CAPABILITY, "trigger.event.normalized_spell_cast",
    "trigger.effect.fixed_event", "trigger.placement.apnap", *_CASTS.capability_dependencies,
)


def prior_cast_copy_condition():
    return {"all": [
        {"field": "controller", "op": "eq", "value": "$source.controller"},
        {"field": "types", "op": "contains_any", "value": ["instant", "sorcery"]},
    ]}


def prior_cast_copy_instruction():
    return {"op": "choose_objects", "player": "$controller", "stack": "$context.stack",
            "copy_source": "$context." + EVENT_SPELL_COPY_CONTEXT,
            PRIOR_CAST_COPY_FIELD: {"schema_version": 1}}


def prior_cast_copy_node_capabilities(*, effects, target_schema, mechanic_ids):
    if (target_schema is not None or len(effects) != 1 or PRIOR_CAST_COPY_MECHANIC not in mechanic_ids
            or FrozenMap(effects[0]) != FrozenMap(prior_cast_copy_instruction())):
        return ()
    marker = effects[0].get(PRIOR_CAST_COPY_FIELD)
    return PRIOR_CAST_COPY_DEPENDENCIES if isinstance(marker, Mapping) and type(marker.get("schema_version")) is int else ()


def prior_cast_copy_program_dependencies(program):
    marked = tuple(effect for effect in nested_effects((program.effects, program.handlers))
                   if PRIOR_CAST_COPY_FIELD in effect or PRIOR_CAST_COPY_COUNT in effect)
    if not marked and PRIOR_CAST_COPY_CAPABILITY not in program.capability_dependencies:
        return ()
    required = prior_cast_copy_node_capabilities(
        effects=program.effects, target_schema=program.target_schema, mechanic_ids=program.coverage,
    )
    if (not required or len(marked) != 1 or program.handlers or program.cost_schema is not None
            or getattr(program, "trigger_limit", None) is not None
            or (program.event, program.active_zone) != ("spell.cast", "battlefield")
            or FrozenMap(program.event_condition or {}) != FrozenMap(prior_cast_copy_condition())):
        raise ValueError("Prior-cast copy requires the complete original controller's instant-or-sorcery trigger")
    return required


def _eligible_prior_count(state, context, source):
    from .spell_history_ordinals import qualified_spell_cast_ordinal

    item = source.item()
    if (item.kind != "spell" or item.ref != context.get("stack") or item.controller != context.get("controller")
            or source.logical_object_id != context.get("logical_object_id")):
        raise ValueError("Prior-cast copies require the original committed spell and caster")
    ordinal = qualified_spell_cast_ordinal(state, _CASTS, context)
    if ordinal < 1:
        raise ValueError("The triggering spell must be a committed instant or sorcery")
    return ordinal - 1


def prior_cast_copy_count_context(host, context, source):
    return {PRIOR_CAST_COPY_COUNT: _eligible_prior_count(host.state, context, source)}


def bind_prior_cast_copy(host, item, effect):
    if PRIOR_CAST_COPY_FIELD not in effect:
        return effect
    program = host.semantics.get(item.semantic_key) if item.semantic_key else None
    dependencies = prior_cast_copy_program_dependencies(program) if program is not None else ()
    if (not dependencies or not set(dependencies) <= set(program.capability_dependencies)
            or not host.semantic_program_is_current_trusted(program)
            or item.kind != "triggered_ability" or item.targets or item.context.get("event") != "spell.cast"
            or item.context.get("controller") != item.controller
            or item.context.get("currently_resolving") is not True
            or FrozenMap(effect) != FrozenMap(program.effects[0])):
        raise ValueError("Prior-cast copy must retain the original complete resolving trigger")
    count = item.context.get(PRIOR_CAST_COPY_COUNT)
    source = EventSpellCopySource.from_dict(item.context[EVENT_SPELL_COPY_CONTEXT])
    if type(count) is not int or count != _eligible_prior_count(host.state, item.context, source):
        raise ValueError("Wrong count for the original cast occurrence")
    return {**effect, PRIOR_CAST_COPY_COUNT: count}


def decode_materialized_prior_cast_copy(effect, *, actor, trigger_ref):
    marker = effect.get(PRIOR_CAST_COPY_FIELD)
    count = effect.get(PRIOR_CAST_COPY_COUNT)
    if (set(effect) != set(prior_cast_copy_instruction()) | {PRIOR_CAST_COPY_COUNT}
            or effect.get("op") != "choose_objects" or effect.get("player") != actor
            or not isinstance(marker, Mapping) or set(marker) != {"schema_version"}
            or type(marker["schema_version"]) is not int or marker["schema_version"] != 1
            or type(count) is not int or count < 0):
        raise ValueError("Prior-cast copy requires the sealed nonnegative result")
    source = EventSpellCopySource.from_dict(effect["copy_source"])
    item = source.item()
    if item.kind != "spell" or item.ref != effect["stack"] or item.ref == trigger_ref or item.controller != actor:
        raise ValueError("Prior-cast copy must name the original spell rather than the trigger")
    return source, count
