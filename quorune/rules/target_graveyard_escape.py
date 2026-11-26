"""Trust and commit one resolved activation's temporary Escape grant."""

from ..continuous_effect_model import ContinuousObjectIdentity
from ..continuous_effect_state import ResolutionEffectSource
from ..errors import GameRuleError
from ..replacement.immutable import FrozenMap
from ..temporary_graveyard_escape import (
    INSTRUCTION, TEMPORARY_ESCAPE_CAPABILITY, TEMPORARY_ESCAPE_OPERATION, TEMPORARY_ESCAPE_STATS_FIELD,
    ResolvedGraveyardEscape, targeted_escape_requirements,
)
from .effect_shape_walkers import nested_effect_operations


def target_graveyard_escape_runtime_dependencies(program):
    if (TEMPORARY_ESCAPE_OPERATION not in nested_effect_operations(program.effects)
            and TEMPORARY_ESCAPE_CAPABILITY not in program.capability_dependencies):
        return ()
    required = targeted_escape_requirements(program.effects, program.target_schema, program.coverage)
    if (not required or (program.event, program.active_zone) != ("activate", "battlefield")
            or program.cost_schema is None or program.event_condition is not None or len(program.handlers) != 1):
        raise ValueError("Temporary Escape requires its complete targeted battlefield activation")
    from ..semantic_runtime.activated_abilities import ActivatedAbilityCatalogHandler

    ability = ActivatedAbilityCatalogHandler().validate(program.handlers[0])
    if ability.mana_ability or ability.builtin_semantic_key != program.key:
        raise ValueError("Temporary Escape requires its exact nonmana activation")
    return required


def is_closed_target_graveyard_escape_program(program):
    try:
        required = target_graveyard_escape_runtime_dependencies(program)
    except (TypeError, ValueError, KeyError):
        return False
    return bool(required) and set(required) <= set(program.capability_dependencies)


def validate_target_graveyard_escape_effect(effect, *, resolved=False):
    values = dict(effect)
    expected = INSTRUCTION
    if resolved:
        extras = {"reason", "_runtime_source"}
        values = {key: value for key, value in values.items() if key not in extras}
        target = values.get("card")
        if type(target) is not str or not target or target.startswith("$"):
            raise ValueError("Temporary Escape needs its revalidated graveyard card")
        expected = FrozenMap({**INSTRUCTION, "card": target})
    if FrozenMap(values) != expected or type(values.get("schema_version")) is not int:
        raise ValueError("Temporary Escape requires its closed fixed-printed-price instruction")


def apply_target_graveyard_escape(host, effect, *, actor, operation, reason):
    try:
        validate_target_graveyard_escape_effect(effect, resolved=True)
        source = ResolutionEffectSource.from_effect(effect)
        item = next((entry for entry in host.state.stack if entry.ref == source.stack_ref), None)
        if (item is None or item.kind not in {"activated_ability", "activated_ability_copy"}
                or item.controller != actor or not item.context.get("currently_resolving")
                or tuple(item.targets) != (effect["card"],) or item.source_object_id != source.object_id):
            raise ValueError("Temporary Escape lost the resolving activation and its original target")
        program = host.semantics.get(item.semantic_key)
        if (program is None or not host.semantic_program_is_current_trusted(program)
                or not target_graveyard_escape_runtime_dependencies(program)):
            raise ValueError("Temporary Escape requires its admitted activated ability")
        target = host._resolve_object(actor, effect["card"], zones={"graveyard"}, owned_only=True)
        snapshot = (item.context.get("target_snapshots") or {}).get(target.ref)
        if (not snapshot or snapshot.get("object_id") != target.object_id
                or snapshot.get("zone_change_counter") != target.zone_change_counter):
            raise ValueError("Temporary Escape requires its actual graveyard incarnation")
        data = host._effective_card_data(target)
        types, _subtypes, supertypes = host._type_parts(str(data.get("type_line") or ""))
        if "creature" not in types or "legendary" not in supertypes or not target.is_card_object:
            raise ValueError("The resolved Escape target must still be a legendary creature card")
        grant = ResolvedGraveyardEscape(
            source, ContinuousObjectIdentity(target.object_id, target.logical_object_id), program.key,
            host.state.turn_sequence,
        )
    except (ValueError, TypeError, KeyError) as exc:
        raise GameRuleError(str(exc)) from exc
    stats = host.state.players[actor].stats
    grants = tuple(stats.get(TEMPORARY_ESCAPE_STATS_FIELD, ()))
    stats[TEMPORARY_ESCAPE_STATS_FIELD] = [*grants, grant.to_dict()]
    host._log(
        actor, "permission.temporary_graveyard_escape", f"Granted Escape to {target.ref} until end of turn.",
        {"card": target.ref, "source": source.stack_ref}, changed_objects=[target.object_id], changed_players=[actor],
    )
    return target.ref
