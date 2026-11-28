"""Keep one spell-copy ability effect on the same resolving copy through entry."""

from contextlib import contextmanager
from contextvars import ContextVar

from ..errors import GameRuleError
from ..replacement.immutable import thaw_value


EXILE_CAST_COPY_CONDITION = {"all": [
    {"field": "controller", "op": "eq", "value": "$source.controller"},
    {"field": "from", "op": "eq", "value": "exile"},
]}
EXILE_COPY_GRANT_ZONES = ("battlefield", "stack")
END_STEP = {"all": [{"field": "step", "op": "eq", "value": "end_step"}]}
_COPY_GRANT = ContextVar("exile_cast_spell_copy_grant", default=None)


def copy_cleanup_effect():
    return {"op": "move_if_in_zone", "card": "$source.zone_object", "from": "battlefield",
            "destination": "graveyard", "transition_kind": "sacrifice", "required_controller": "$controller"}


def validate_exile_cast_copy_program(program, spec):
    if (tuple(program.effects) != spec.effects or thaw_value(program.event_condition) != EXILE_CAST_COPY_CONDITION
            or (program.event, program.active_zone) != ("spell.cast", "battlefield")
            or program.target_schema is not None or program.cost_schema is not None or program.handlers
            or program.trigger_limit is not None):
        raise ValueError("Exile spell copies require their original untargeted controller cast.")
    from .stack_spell_copy_shapes import permanent_spell_copy_node_dependencies

    return permanent_spell_copy_node_dependencies(program.effects[0], None)


def _cleanup_child(host, grant):
    child = host.semantics.get(grant.semantic_key)
    if (child is None or not host.semantic_program_is_current_trusted(child)
            or not child.provenance.get("granted_only") or child.ability_id != grant.ability_id
            or (child.event, child.active_zone) != ("step.begin", "battlefield")
            or thaw_value(child.event_condition) != END_STEP or child.target_schema or child.cost_schema or child.handlers
            or tuple(child.effects) != (copy_cleanup_effect(),)):
        raise GameRuleError("The copied spell lost its trusted recurring sacrifice ability.")
    return child


@contextmanager
def resolving_exile_spell_copy_grant(host, intent, bound):
    spec = intent.permanent_copy
    if spec is None or spec.kind != "exile_cast_grant":
        yield
        return
    from ..continuous_effect_state import ResolutionEffectSource
    from ..event_spell_copy_model import EVENT_SPELL_COPY_CONTEXT

    trigger = host.state.stack[-1] if host.state.stack else None
    if (trigger is None or trigger.kind != "triggered_ability" or trigger.controller != intent.actor
            or intent.actor != intent.controller or trigger.context.get("currently_resolving") is not True
            or trigger.context.get("from") != "exile" or trigger.context.get("controller") != intent.controller
            or trigger.context.get("event") != "spell.cast"
            or trigger.context.get("stack") != intent.target_stack_ref or bound is None
            or trigger.context.get(EVENT_SPELL_COPY_CONTEXT, {}).get("logical_object_id") != bound.logical_object_id):
        raise GameRuleError("Copy grant requires the original resolving exile-cast trigger.")
    parent = host.semantics.get(trigger.semantic_key)
    child = _cleanup_child(host, spec.grant)
    if (parent is None or not host.semantic_program_is_current_trusted(parent)
            or parent.oracle_id != child.oracle_id or not validate_exile_cast_copy_program(parent, spec)):
        raise GameRuleError("Copy grant requires its admitted enclosing ability and quoted child.")
    source = ResolutionEffectSource(
        trigger.ref, trigger.source_object_id,
        trigger.context.get("source_logical_object_id"), trigger.context.get("source_ref"),
    )
    token = _COPY_GRANT.set((host, source, spec.grant, intent.target_stack_ref))
    try:
        yield
    finally:
        _COPY_GRANT.reset(token)


def apply_exile_spell_copy_grant(host, item, card, copied_from_ref):
    scope = _COPY_GRANT.get()
    if scope is None or scope[0] is not host:
        return
    _, source, grant, target = scope
    if copied_from_ref != target:
        raise GameRuleError("The spell copy changed its original grant recipient.")
    if not item.context.get("copy_permanent_spell"):
        return
    from ..ability_fragments import ability_fragment_to_dict
    from ..continuous_effects import ContinuousEffectDuration, ContinuousOperation, Layer
    from ..continuous_effect_state import create_resolution_continuous_effect

    operations = (ContinuousOperation("add_ability", "Haste"),
                  ContinuousOperation("add_ability_fragment", ability_fragment_to_dict(grant)))
    if create_resolution_continuous_effect(
        host, source=source, targets=(card,), layer=Layer.ABILITY, sublayer="6",
        operations=operations, duration=ContinuousEffectDuration.ZONE_OBJECT, zones=EXILE_COPY_GRANT_ZONES,
    ) is None:
        raise GameRuleError("Exile-cast spell-copy grants require the continuous effect journal.")


def exile_spell_copy_grant_effect(effect):
    from ..ability_fragments import GrantedTriggeredAbilitySpec, ability_fragment_from_dict
    from ..continuous_effects import ContinuousEffect, ContinuousEffectDuration, ContinuousEffectOrigin, ContinuousOperation, Layer
    from ..object_predicate import ObjectQuerySpec

    if (not isinstance(effect, ContinuousEffect) or effect.origin is not ContinuousEffectOrigin.RESOLUTION
            or effect.layer is not Layer.ABILITY or effect.sublayer != "6"
            or effect.duration is not ContinuousEffectDuration.ZONE_OBJECT
            or len(effect.locked_objects) != 1 or effect.duration_source is not None
            or effect.applies != ObjectQuerySpec(zones=EXILE_COPY_GRANT_ZONES) or len(effect.operations) != 2
            or effect.operations[0] != ContinuousOperation("add_ability", "Haste")
            or effect.operations[1].op != "add_ability_fragment"):
        return False
    grant = ability_fragment_from_dict(thaw_value(effect.operations[1].value))
    return isinstance(grant, GrantedTriggeredAbilitySpec) and grant.schema_version == 1 and grant.event == "step.begin"
