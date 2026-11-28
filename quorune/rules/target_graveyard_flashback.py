from __future__ import annotations

"""Closed resolved grant of printed-price Flashback to one graveyard card."""

from collections.abc import Mapping

from ..continuous_effect_model import ContinuousObjectIdentity
from ..errors import GameRuleError
from ..flashback import FLASHBACK_CAPABILITY_ID
from ..replacement.immutable import FrozenMap, thaw_value
from ..temporary_graveyard_escape import ResolvedGraveyardEscape
from .effect_shape_walkers import nested_effect_operations
from .graveyard_card_targets import targeted_graveyard_cast_schema
from .target_graveyard_escape import commit_temporary_graveyard_grant, resolving_graveyard_grant_target


GRAVEYARD_FLASHBACK_OPERATION = "grant_target_graveyard_flashback"
GRAVEYARD_FLASHBACK_CAPABILITY = "casting.flashback.target_graveyard_until_cleanup"
GRAVEYARD_FLASHBACK_MECHANIC = "target-graveyard-flashback-until-cleanup"
GRAVEYARD_FLASHBACK_STATS_FIELD = "target_graveyard_flashback_grants"
TARGET_SCHEMA = FrozenMap(targeted_graveyard_cast_schema("you"))
INSTRUCTION = FrozenMap({"op": GRAVEYARD_FLASHBACK_OPERATION, "schema_version": 1, "card": "$target.0"})
DEPENDENCIES = (GRAVEYARD_FLASHBACK_CAPABILITY, FLASHBACK_CAPABILITY_ID, "target.revalidate_resolution")
_SOURCE_COMBAT_DAMAGE_TO_PLAYER = FrozenMap({"all": [
    {"field": "target_kind", "op": "eq", "value": "player"},
    {"field": "combat", "op": "truthy", "value": True},
    {"field": "source_logical_object_id", "op": "eq", "value": "$source.logical_object_id"},
]})
_SPELL_KINDS = {"spell", "spell_copy"}
_TRIGGER_KINDS = {"triggered_ability", "triggered_ability_copy"}


def validate_target_graveyard_flashback_effect(effect, *, resolved=False):
    values = dict(effect)
    expected = INSTRUCTION
    if resolved:
        values = {key: value for key, value in values.items() if key not in {"reason", "_runtime_source"}}
        target = values.get("card")
        if type(target) is not str or not target or target.startswith("$"):
            raise ValueError("Temporary Flashback requires its revalidated graveyard card")
        expected = FrozenMap({**INSTRUCTION, "card": target})
    if FrozenMap(values) != expected or type(values.get("schema_version")) is not int:
        raise ValueError("Temporary Flashback requires its exact printed-price grant")


def target_graveyard_flashback_node_capabilities(*, effects, target_schema, mechanic_ids):
    if (len(effects) != 1 or GRAVEYARD_FLASHBACK_MECHANIC not in mechanic_ids
            or not isinstance(target_schema, Mapping) or type(target_schema.get("count")) is not int
            or FrozenMap(target_schema) != TARGET_SCHEMA):
        return ()
    try:
        validate_target_graveyard_flashback_effect(effects[0])
    except (TypeError, ValueError, KeyError):
        return ()
    return DEPENDENCIES


def _attached_flashback_trigger(program):
    from ..ability_fragments import GrantedTriggeredAbilitySpec, ability_fragment_from_dict
    from ..semantic_runtime.attached_continuous import AttachedFixedCharacteristicsHandler

    if (program.effects or (program.event, program.active_zone) != ("characteristics.evaluate", "battlefield")
            or not program.ability_id.startswith("static:") or len(program.handlers) != 1
            or program.cost_schema is not None or program.target_schema is not None
            or program.event_condition is not None or program.trigger_limit is not None
            or program.provenance.get("template_id") != "continuous-attached-fixed-characteristics-granted-ability-v1"
            or "attached_typed_ability_grant" not in program.coverage):
        return None
    descriptor, = program.handlers
    handler = AttachedFixedCharacteristicsHandler()
    if descriptor.get("handler_id") != handler.handler_id:
        return None
    node = handler.validate(thaw_value(FrozenMap(descriptor)))
    if node.subject_types_all != ("creature",) or len(node.add_ability_fragments) != 1:
        return None
    fragment = ability_fragment_from_dict(node.add_ability_fragments[0])
    expected_id = "trigger:" + program.ability_id.removeprefix("static:") + ":granted"
    if (isinstance(fragment, GrantedTriggeredAbilitySpec) and fragment.schema_version == 1
            and fragment.event == "damage.dealt.self" and fragment.ability_id == expected_id
            and fragment.semantic_key == f"{program.oracle_id}:{expected_id}"):
        return fragment
    return None


def validate_attached_flashback_trigger(program, lookup):
    if GRAVEYARD_FLASHBACK_CAPABILITY not in program.capability_dependencies:
        return
    fragment = _attached_flashback_trigger(program)
    if fragment is None:
        return
    child = lookup(fragment.semantic_key)
    if (child is None or child.ability_id != fragment.ability_id or child.oracle_id != program.oracle_id
            or child.event != fragment.event or child.provenance.get("granted_only") is not True
            or "granted_triggered_ability" not in child.coverage
            or child.provenance.get("face_id") != program.provenance.get("face_id")
            or child.provenance.get("source_span") != program.provenance.get("source_span")
            or GRAVEYARD_FLASHBACK_OPERATION not in nested_effect_operations(child.effects)
            or not is_closed_target_graveyard_flashback_program(child)):
        raise ValueError("Attached Flashback requires its matching granted combat trigger")


def target_graveyard_flashback_runtime_dependencies(program):
    if GRAVEYARD_FLASHBACK_OPERATION not in nested_effect_operations(program.effects):
        if (GRAVEYARD_FLASHBACK_CAPABILITY not in program.capability_dependencies
                or _attached_flashback_trigger(program) is not None):
            return ()
    required = target_graveyard_flashback_node_capabilities(
        effects=program.effects, target_schema=program.target_schema, mechanic_ids=program.coverage,
    )
    spell = ((program.event, program.active_zone) == ("resolve", "stack")
             and program.event_condition is None and program.ability_id.startswith("spell:"))
    entry = program.event == "permanent.enter.self" and program.event_condition is None
    combat = (program.event == "damage.dealt.self"
              and FrozenMap(program.event_condition or {}) == _SOURCE_COMBAT_DAMAGE_TO_PLAYER)
    trigger = program.active_zone == "battlefield" and (entry or combat)
    if not required or program.handlers or program.cost_schema is not None or not (spell or trigger):
        raise ValueError("Temporary Flashback requires its unpriced spell, original self-entry, or sealed combat damage effect")
    return required


def is_closed_target_graveyard_flashback_program(program):
    try:
        required = target_graveyard_flashback_runtime_dependencies(program)
    except (TypeError, ValueError, KeyError):
        return False
    return bool(required) and set(required) <= set(program.capability_dependencies)


def apply_target_graveyard_flashback(host, effect, *, actor, operation, reason):
    try:
        validate_target_graveyard_flashback_effect(effect, resolved=True)
        source, item, target = resolving_graveyard_grant_target(
            host, effect, actor=actor, kinds=_SPELL_KINDS | _TRIGGER_KINDS,
        )
        program = host.semantics.get(item.semantic_key)
        if (program is None or not host.semantic_program_is_current_trusted(program)
                or not is_closed_target_graveyard_flashback_program(program)):
            raise ValueError("Temporary Flashback requires its admitted resolving spell or trigger")
        if (item.kind in _SPELL_KINDS) != (program.event == "resolve"):
            raise ValueError("Temporary Flashback lost its original spell or trigger kind")
        types, _, _ = host._type_parts(str(host._effective_card_data(target).get("type_line") or ""))
        if not {"instant", "sorcery"}.intersection(types):
            raise ValueError("The resolved Flashback target must still be an instant or sorcery card")
        grant = ResolvedGraveyardEscape(
            source, ContinuousObjectIdentity(target.object_id, target.logical_object_id), program.key,
            host.state.turn_sequence,
        )
    except (ValueError, TypeError, KeyError) as exc:
        raise GameRuleError(str(exc)) from exc
    commit_temporary_graveyard_grant(host, actor, grant, field=GRAVEYARD_FLASHBACK_STATS_FIELD)
    host._log(
        actor, "permission.temporary_graveyard_flashback", f"Granted Flashback to {target.ref} until end of turn.",
        {"card": target.ref, "source": source.stack_ref}, changed_objects=[target.object_id], changed_players=[actor],
    )
    return target.ref
