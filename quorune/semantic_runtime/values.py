from __future__ import annotations

import re
from typing import Any, Mapping, Protocol

from ..attachment_references import (
    AttachmentReferenceError,
    AttachmentReferenceSpec,
    resolve_source_attachment,
)
from ..cast_origin_replacement import (
    CAST_ORIGIN_REPLACEMENT_VERSIONS, CAST_REPLACEMENT_VERSIONS, is_main_phase_cast_replacement,
    was_cast_during_own_main_phase, was_cast_from_origin,
)
from ..linked_exile_return_model import SELECTED_EXILE_QUANTITY_KIND, conditional_exile_soldier_token
from ..station import StationAbilityError, station_resolution_power
from ..spell_mana_conditions import ACTUAL_CAST_MANA_TOTAL_KIND, CAST_MANA_UNITS_KIND
from ..affected_permanents import KICKED_ENTRY_DESTRUCTION_FIELD
from ..graveyard_exile_token_model import GRAVEYARD_EXILE_TOKEN_OPERATION
from ..rules.graveyard_exile_type_outcomes import GRAVEYARD_EXILE_TYPE_OUTCOME_OPERATION
from ..rules.combat_point_counters import bind_point_game_loss, uses_point_game_loss
from ..paid_sacrifice_conditions import PAID_SACRIFICE_CONDITION_VALUE
from ..query_effect_amount_model import (
    PUBLIC_QUERY_AMOUNT_KIND,
    CAST_X_AMOUNT_KIND,
    PublicQueryAmountError,
)

from ..granted_activation_model import GRANTING_ATTACHMENT_SACRIFICE_REF
from .context import SemanticNodeError
from .explore import explore_source_controller
from .query_effect_amounts import resolve_public_query_amount, resolve_cast_x_amount
from ..scalar_effect_amount_model import SCALAR_AMOUNT_KIND
from ..rules.revealed_library_top import REVEAL_TOP_OPERATION
from ..scalar_effect_amounts import resolve_paid_sacrifice_condition, resolve_scalar_effect_amount
from .direct_target_fields import bound_mutation_target_overrides
from ..library_bottom_result_model import LIBRARY_BOTTOM_RESULT_OPERATION, LibraryBottomResultSpec


_INDEX_GROUP = "index"
_TARGET_GROUP_REFERENCE = re.compile(
    r"\$target_group\.(?P<group>clause_[0-3]|target_[0-9]+|mode_[1-5]:[A-Za-z_][A-Za-z0-9_]*)"
    r"(?P<scalar>\.0|(?P<current_controller>\.current_controller\.0))?"
)


class SemanticValueHost(Protocol):
    state: Any
    semantics: Any

    def _stack_target_schema(self, item: Any, program: Any) -> Mapping[str, Any] | None: ...

    def _stack_source_ref(self, item: Any) -> str | None: ...

    def _target_snapshot(self, target_ref: str) -> dict[str, Any]: ...

    def _type_parts(
        self, type_line: str
    ) -> tuple[set[str], set[str], set[str]]: ...


def _empty_scalar_target_is_optional(host: SemanticValueHost, item: Any, name: str) -> bool:
    from ..targets import target_plan
    try:
        program = host.semantics.get(item.semantic_key)
        schema = host._stack_target_schema(item, program)
        if schema is None:
            return False
        group = next((group for group in target_plan(schema, item.modes).groups
            if group.group_id == name), None)
    except (AttributeError, TypeError, ValueError, KeyError):
        return False
    return group is not None and group.min_targets == 0 and group.max_targets == 1


def _resolve_target_group_reference(host: SemanticValueHost, match: re.Match[str], item: Any) -> Any:
    name = match['group']
    original_groups = item.context.get('target_groups')
    groups = item.context.get('target_groups_current')
    if groups is None:
        groups = original_groups
    if not isinstance(groups, Mapping) or name not in groups:
        raise SemanticNodeError('Scoped target group is unavailable')
    refs = groups[name]
    if not isinstance(refs, (list, tuple)) or any(
        ref is not None and (type(ref) is not str or not ref) for ref in refs
    ):
        raise SemanticNodeError('Scoped target group is malformed')
    if not match['scalar']:
        return [ref for ref in refs if ref is not None]
    original = original_groups.get(name) if isinstance(original_groups, Mapping) else None
    if isinstance(original, (list, tuple)) and not original and not refs:
        if _empty_scalar_target_is_optional(host, item, name):
            return None
    if not isinstance(original, (list, tuple)) or len(original) != 1 or len(refs) > 1:
        raise SemanticNodeError('Scoped scalar target requires its one fixed role')
    ref = refs[0] if refs else None
    if ref is not None and match['current_controller']:
        return host._target_snapshot(ref).get('controller')
    return ref


def _resolve_attachment_reference(
    host: SemanticValueHost,
    value: Mapping[str, Any],
    item: Any,
) -> str | None:
    spec = AttachmentReferenceSpec.from_dict(value)
    if spec.schema_version in {2, 3}:
        from ..attachment_references import ATTACHMENT_INFORMATION_VERSION
        version=item.context.get(ATTACHMENT_INFORMATION_VERSION)
        if type(version) is not int or version!=1:
            raise AttachmentReferenceError('Attachment reference requires captured departure information')
    snapshot = item.context.get("source_attachment_snapshot")
    if not isinstance(snapshot, Mapping):
        raise AttachmentReferenceError(
            "Attachment-reference stack context is missing or malformed"
        )
    source_object_id = item.source_object_id or item.card_object_id
    source_logical_object_id = item.context.get("source_logical_object_id")
    if type(source_object_id) is not str or not source_object_id:
        raise AttachmentReferenceError(
            "Attachment-reference stack source identity is missing"
        )
    if (
        type(source_logical_object_id) is not str
        or not source_logical_object_id
    ):
        raise AttachmentReferenceError(
            "Attachment-reference stack source incarnation is missing"
        )
    target = resolve_source_attachment(
        host.state.cards,
        snapshot,
        spec,
        source_object_id=source_object_id,
        source_logical_object_id=source_logical_object_id,
    )
    if target is None:
        return None
    facts = host._target_snapshot(target.ref)
    card_types = host._type_parts(str(facts.get("type_line") or ""))[0]
    if spec.required_color is not None and spec.required_color not in facts.get("colors", ()):
        return None
    if (
        spec.required_card_type != "permanent"
        and spec.required_card_type not in card_types
    ):
        return None
    return target.ref


def _resolve_typed_semantic_value(host, value, item):
    """Delegate typed amounts and public predicates to their narrow owners."""
    if value["kind"] == "target_player_greatest_creature_power":
        from ..rules.greatest_power_life import resolve_greatest_power_life_value

        try:
            return resolve_greatest_power_life_value(
                host, value, resolve=lambda ref: resolve_semantic_value(host, ref, item),
            )
        except (TypeError, ValueError) as exc:
            raise SemanticNodeError(str(exc)) from exc
    if value['kind']=='current_controller_life_effect_amount':
        from ..rules.current_life_effect_amounts import resolve_current_life_effect_amount
        try:return resolve_current_life_effect_amount(host,value,item)
        except (ValueError,TypeError) as exc:raise SemanticNodeError(str(exc)) from exc
    if value['kind']=='all_player_turn_life_loss_amount':
        from ..rules.turn_life_loss_recovery import resolve_all_player_turn_life_loss
        try:return resolve_all_player_turn_life_loss(host,value,item)
        except (ValueError,TypeError) as exc:raise SemanticNodeError(str(exc)) from exc
    if value['kind'] in {'per_player_public_query_life_loss_amount','bound_per_player_public_query_life_loss_amount'}:
        from .per_player_query_life import resolve_per_player_query_life
        try:return resolve_per_player_query_life(host,value,item)
        except (TypeError,ValueError) as exc:raise SemanticNodeError(str(exc)) from exc
    if value['kind'] in {'per_player_public_query_damage_amount','bound_per_player_public_query_damage_amount'}:
        from .per_player_query_damage import resolve_per_player_query_damage
        try:return resolve_per_player_query_damage(host,value,item)
        except (TypeError,ValueError) as exc:raise SemanticNodeError(str(exc)) from exc
    if value['kind'] in {'per_player_current_life_damage_amount','bound_per_player_current_life_damage_amount'}:
        from .per_player_life_damage import resolve_per_player_life_damage
        try:return resolve_per_player_life_damage(host,value,item)
        except (TypeError,ValueError) as exc:raise SemanticNodeError(str(exc)) from exc
    if value['kind']=='target_player_public_query_amount':
        from .target_player_query_amounts import resolve_target_player_query_amount
        try:return resolve_target_player_query_amount(host,value,item)
        except (TypeError,ValueError) as exc:raise SemanticNodeError(str(exc)) from exc
    if value['kind']=='unique_player_maximum':
        from ..player_maximum import resolve_player_maximum
        try:return resolve_player_maximum(host,value,item)
        except (TypeError,ValueError) as exc:raise SemanticNodeError(str(exc)) from exc
    if value['kind'] == 'devoured_entry_creatures_amount':
        from ..devour import resolve_devour_entry_amount
        try:
            return resolve_devour_entry_amount(value, item)
        except (ValueError, TypeError) as exc:
            raise SemanticNodeError(str(exc)) from exc
    if value['kind']=='source_entry_x_amount':
        from ..entry_x_amount_model import resolve_entry_x_amount
        try:return resolve_entry_x_amount(value,item)
        except (ValueError,TypeError) as exc:raise SemanticNodeError(str(exc)) from exc
    if value['kind'] in {'source_entry_designation_predicate','source_entry_designation_scope',
                         'source_entry_designation_library_filter'}:
        from ..designation_prevention import resolve_designation_predicate
        try:return resolve_designation_predicate(value,item)
        except (ValueError,TypeError) as exc:raise SemanticNodeError(str(exc)) from exc
    if value['kind'] == 'selected_target_count':
        from ..target_cardinality import resolve_selected_target_count
        try:return resolve_selected_target_count(value, item)
        except (TypeError, ValueError) as exc:raise SemanticNodeError(str(exc)) from exc
    try:
        if value['kind'] == PUBLIC_QUERY_AMOUNT_KIND:
            return resolve_public_query_amount(host, value, item)
        if value['kind'] == CAST_X_AMOUNT_KIND:
            return resolve_cast_x_amount(value, item)
        if value['kind'] == SCALAR_AMOUNT_KIND:
            from ..counter_placement_sets import is_source_power_combat_counter_amount

            return resolve_scalar_effect_amount(
                host, value, item, use_phased_source_lki=is_source_power_combat_counter_amount(value),
            )
    except PublicQueryAmountError as exc:
        raise SemanticNodeError(str(exc)) from exc
    raise SemanticNodeError('Typed semantic value kind is unsupported')


def _resolve_kicked_entry_destruction(host, effect, item):
    from ..affected_permanents import KickedEntryDestructionSpec

    try:
        spec = KickedEntryDestructionSpec.from_effect(effect)
    except (TypeError, ValueError) as exc:
        raise SemanticNodeError(str(exc)) from exc
    facts = item.context
    cast_option = facts.get("cast_option")
    counter = facts.get("card_zone_change_counter")
    origin = facts.get("from")
    source_id = item.source_object_id
    first_token_entry = facts.get("token") is True and origin in ("stack", "outside")
    if (item.kind != "triggered_ability" or facts.get("event") != "permanent.enter"
            or facts.get("to") != "battlefield" or type(counter) is not int or counter < 0
            or counter == 0 and not first_token_entry
            or type(source_id) is not str or not source_id
            or facts.get("source_logical_object_id") != f"{source_id}@{counter}"
            or cast_option not in (None, "kicked")
            or cast_option == "kicked" and origin != "stack"):
        raise SemanticNodeError("Kicked entry destruction requires the committed self-entry occurrence")
    source_ref = resolve_semantic_value(host, effect["source"], item)
    affected = spec.affected_set(kicked=cast_option == "kicked", source_ref=source_ref)
    return {"op": "destroy_all", "source": source_ref, "set": affected.to_dict()}


def _resolve_fixed_object_action(host, value, item):
    from ..delayed_object_model import is_forced_block_status_effect, validate_forced_block_status_effect

    if is_forced_block_status_effect(value):
        try:
            validate_forced_block_status_effect(value, compiled=True)
        except (ValueError, TypeError) as exc:
            raise SemanticNodeError(str(exc)) from exc
        ref = resolve_semantic_value(host, value["object"], item)
        if ref is None:
            return {**value, "object": None}
        snapshot = item.context.get("target_snapshots", {}).get(ref)
        if (not isinstance(snapshot, Mapping) or snapshot.get("zone") != "battlefield"
                or type(snapshot.get("object_id")) is not str
                or type(snapshot.get("zone_change_counter")) is not int):
            raise SemanticNodeError("The blocked-status effect lost its announced attacking incarnation")
        identity = f"{snapshot['object_id']}@{snapshot['zone_change_counter']}"
        return {**value, "object": {"reference": ref, "logical_object_id": identity}}
    from ..rules.reanimation_followup import current_linked_subject, validate_linked_departure

    try:
        subject = validate_linked_departure(value)
    except (ValueError, TypeError) as exc:
        raise SemanticNodeError(str(exc)) from exc
    return {**value, "object": None} if current_linked_subject(host, subject, zone="battlefield") is None else dict(value)


def _resolve_library_bottom_result(host, value, item):
    try:
        if "_replacement_selections" in value:
            retained = {key: child for key, child in value.items() if key != "_replacement_selections"}
            spec = LibraryBottomResultSpec.from_effect(retained, actor=item.controller)
            role = "$target.0" if spec.targets_player else "$controller"
            if retained["player"] != resolve_semantic_value(host, role, item):
                raise ValueError("Library-bottom replacement changed its printed library role")
            return dict(value)
        LibraryBottomResultSpec.from_effect(value)
    except (TypeError, ValueError, KeyError) as exc:
        raise SemanticNodeError(str(exc)) from exc
    return {**value, "player": resolve_semantic_value(host, value["player"], item)}


def _bind_condition_cast_fact(host, value, item):
    from ..resolution_conditions import validate_resolution_condition_instruction

    try:
        validate_resolution_condition_instruction(value)
        if is_main_phase_cast_replacement(value):
            fact = was_cast_during_own_main_phase(item)
        elif value["schema_version"] in CAST_ORIGIN_REPLACEMENT_VERSIONS:
            fact = was_cast_from_origin(item, value["schema_version"])
        else:
            fact = resolve_semantic_value(host, value["cast_fact"], item)
    except (TypeError, ValueError) as exc:
        raise SemanticNodeError(str(exc)) from exc
    return {**value, "player": resolve_semantic_value(host, value["player"], item), "cast_fact": fact}


def _bind_conditional_exile_token(value, item):
    quantity = value.get("quantity")
    if value.get("op") == "create_token" and isinstance(quantity, Mapping) and quantity.get("kind") == SELECTED_EXILE_QUANTITY_KIND:
        from ..linked_exile_return import committed_conditional_exile_count
        from ..replacement.immutable import FrozenMap, thaw_value

        token = conditional_exile_soldier_token()
        if (set(quantity) != {"kind", "schema_version", "binding_id"} or type(quantity["schema_version"]) is not int
                or quantity["schema_version"] != 1 or type(quantity["binding_id"]) is not str
                or thaw_value(FrozenMap({**value, "quantity": 1})) != token):
            raise SemanticNodeError("Conditional exile token result has an invalid shape")
        try:
            count = committed_conditional_exile_count(item, quantity["binding_id"])
        except (TypeError, ValueError) as exc:
            raise SemanticNodeError(str(exc)) from exc
        return {**token, "controller": item.controller, "quantity": count}
    return None


def _resolve_stack_counter_followup(host, value, item):
    from ..rules.stack_counter_followup import (
        captured_stack_target_mana_value, counter_library_position_is_applicable, materialize_stack_counter_followup,
    )

    source = host.state.cards.get(item.card_object_id or "")
    try:
        if value["op"] == "resolve_stack_counter_name_scan":
            from ..rules.stack_counter_name_scan import bind_counter_name_scan

            target = resolve_semantic_value(host, value["stack"], item)
            return bind_counter_name_scan(host, item, value, target_ref=target)
        return materialize_stack_counter_followup(
            value, resolve=lambda operand: resolve_semantic_value(host, operand, item), snapshot=host._target_snapshot,
            mana_value=lambda ref: captured_stack_target_mana_value(host, ref),
            library_position=lambda ref: counter_library_position_is_applicable(host, ref),
            spell_source_ref=source.ref if source is not None and source.zone == "stack" else None,
        )
    except (TypeError, ValueError, KeyError) as exc:
        raise SemanticNodeError(str(exc)) from exc


def _resolve_token_copy_creation(host, value, item):
    from ..token_copy_recipes import TokenCopyRecipeSpec

    try:
        recipe = TokenCopyRecipeSpec.from_dict(value["copy_spec"])
    except (TypeError, ValueError) as exc:
        raise SemanticNodeError(str(exc)) from exc
    # The nested reference declares capture; the copy owner resolves it later.
    return {key: recipe.to_dict() if key == "copy_spec" else resolve_semantic_value(host, child, item)
            for key, child in value.items()}


def _resolve_shared_entry_return(host, value, item):
    from ..rules.entry_type_return import bind_shared_entry_return

    try:
        return bind_shared_entry_return(host, value, item)
    except (TypeError, ValueError, KeyError) as exc:
        raise SemanticNodeError(str(exc)) from exc


def _resolve_cast_fact_mapping(host, value, item):
    from ..rules.cast_trigger_life_payment import CAST_TRIGGER_LIFE_PAYMENT_FIELD, materialize_cast_trigger_life_payment
    from ..rules.opponent_event_mana_payment import (
        materialize_opponent_source_power_payment, uses_opponent_source_power_payment,
    )
    from ..rules.named_cast_win import bind_named_cast_win, uses_named_cast_win

    try:
        if CAST_TRIGGER_LIFE_PAYMENT_FIELD in value:
            return materialize_cast_trigger_life_payment(value, item)
        if uses_opponent_source_power_payment(value) and "source_power" in value:
            return materialize_opponent_source_power_payment(host, value, item)
        if uses_named_cast_win(value):
            return bind_named_cast_win(host, value, item)
    except (TypeError, ValueError) as exc:
        raise SemanticNodeError(str(exc)) from exc
    return None


def _resolve_deferred_effect_mapping(host, value, item, deferred_field):
    from ..rules.controlled_destruction_source_counters import (
        ControlledDestructionSourceCounters, uses_controlled_destruction_source_counters,
    )

    followup = value[deferred_field]
    if uses_controlled_destruction_source_counters(value):
        try:
            if followup.get("stack_ref") == "$stack":
                ControlledDestructionSourceCounters.from_dict(followup)
                followup = ControlledDestructionSourceCounters(item.ref).to_dict()
            elif ControlledDestructionSourceCounters.from_dict(followup, resolved=True).stack_ref != item.ref:
                raise ValueError("Destruction reward lost its resolving stack reference")
        except ValueError as exc:
            raise SemanticNodeError(str(exc)) from exc
    preceding_action = {key: child for key, child in value.items() if key != deferred_field}
    return {**_resolve_effect_mapping(host, preceding_action, item), deferred_field: followup}


def _resolve_effect_mapping(host, value, item):
    if (bound := _resolve_cast_fact_mapping(host, value, item)) is not None:
        return bound
    from ..rules.controlled_return import uses_shared_entry_return
    if uses_shared_entry_return(value):
        return _resolve_shared_entry_return(host, value, item)
    if uses_point_game_loss(value):
        return bind_point_game_loss(host, value, item)
    if KICKED_ENTRY_DESTRUCTION_FIELD in value:
        return _resolve_kicked_entry_destruction(host, value, item)
    if value.get("op") == LIBRARY_BOTTOM_RESULT_OPERATION:
        return _resolve_library_bottom_result(host, value, item)
    if value.get("op") == "look_top_shared_type_reveal":
        from ..rules.shared_type_library_model import SharedTypeLibraryResult
        try:
            SharedTypeLibraryResult.from_dict(value["result"])
        except (TypeError, ValueError, KeyError) as exc:
            raise SemanticNodeError(str(exc)) from exc
        return {**value, "player": resolve_semantic_value(host, value["player"], item)}
    if (value.get("op") == "create_token" and isinstance(value.get("copy_spec"), Mapping)
            and value["copy_spec"].get("schema_version") in {3, 4, 5, 7}):
        return _resolve_token_copy_creation(host, value, item)
    from ..colored_hand_reveal_model import REVEAL_RESULT_KIND, ColoredRevealResult
    if value.get('kind')==REVEAL_RESULT_KIND:
        try:ColoredRevealResult.from_dict(value)
        except (TypeError,ValueError,KeyError) as exc:raise SemanticNodeError(str(exc)) from exc
        return dict(value)
    if value.get('op') == 'search' and value.get('schema_version') == 8:
        from ..library_search_model import QueryBoundLibrarySearchSpec
        try:
            retained = {key: child for key, child in value.items()
                        if key not in {"_search_completion", "_replacement_selections"}}
            QueryBoundLibrarySearchSpec.from_effect(retained, allow_resolved=True)
        except (TypeError, ValueError, KeyError) as exc:
            raise SemanticNodeError(str(exc)) from exc
        return dict(value)
    if value.get("op") in {"resolve_stack_counter_followup", "resolve_stack_counter_name_scan"}:
        return _resolve_stack_counter_followup(host, value, item)
    if value.get('op') == 'counter_unless_pay' and value.get('schema_version') == 6:
        from ..rules.stack_controller_payment_cost import bind_target_announcement_payment

        try:
            return bind_target_announcement_payment(
                host, value, resolve=lambda operand: resolve_semantic_value(host, operand, item),
            )
        except (TypeError, ValueError, KeyError) as exc:
            raise SemanticNodeError(str(exc)) from exc
    if value.get('op') == 'counter_unless_pay' and value.get('schema_version') == 3:
        from ..rules.stack_controller_payment_cost import bind_stack_controller_quantity_payment

        try:
            return bind_stack_controller_quantity_payment(
                host, value, resolve=lambda operand: resolve_semantic_value(host, operand, item),
            )
        except (TypeError, ValueError) as exc:
            raise SemanticNodeError(str(exc)) from exc
    if value.get('op') == 'counter_unless_pay' and value.get('schema_version') == 5:
        if resolve_semantic_value(host, value['stack'], item) is None:
            return {**value, 'stack': None}
    if value.get('op') == 'resolve_fixed_object_action' and value.get('schema_version') in {3, 4}:
        return _resolve_fixed_object_action(host, value, item)
    if value.get('op') == 'reanimate' and ('attachment_return' in value or value.get('schema_version') == 7):
        from ..attachment_source_return import bind_attachment_source_return
        try:return bind_attachment_source_return(value, item)
        except (TypeError, ValueError, KeyError) as exc:raise SemanticNodeError(str(exc)) from exc
    if value.get('op')=='schedule_fixed_object_action':
        from ..delayed_object_actions import bind_delayed_object_instruction
        try:return bind_delayed_object_instruction(host,value,item)
        except (TypeError,ValueError,KeyError) as exc:raise SemanticNodeError(str(exc)) from exc
    from ..rules.destroyed_creature_copy import COMMITTED_DEATH_COPY_FIELD

    if value.get("op") == "create_token" and COMMITTED_DEATH_COPY_FIELD in value:
        return dict(value)
    result_operations = (GRAVEYARD_EXILE_TOKEN_OPERATION, GRAVEYARD_EXILE_TYPE_OUTCOME_OPERATION)
    deferred_field = 'result' if value.get('op') in result_operations else 'followup'
    if deferred_field in value and value.get('op') in (
        'destroy', 'destroy_targets', 'destroy_all', 'mill', 'move_public_zone_set', REVEAL_TOP_OPERATION, *result_operations,
    ):
        return _resolve_deferred_effect_mapping(host, value, item, deferred_field)
    if value.get('op')=='create_token_then_counters':
        from ..created_token_counter_model import validate_created_token_counter
        try:validate_created_token_counter(value)
        except (TypeError,ValueError,KeyError) as exc:raise SemanticNodeError(str(exc)) from exc
        return {**value,'controller':resolve_semantic_value(host,value['controller'],item),
            'source':resolve_semantic_value(host,value['source'],item),
            'token':resolve_semantic_value(host,value['token'],item)}
    if value.get("op") == "apply_if_public_condition" and value.get("schema_version") in {10, 11, *CAST_REPLACEMENT_VERSIONS}:
        value = _bind_condition_cast_fact(host, value, item)
        if value["schema_version"] in {10, 11}:
            return value
    from ..rules.stack_damage_sources import bind_source_relative_damage, bound_stack_damage_source
    from ..rules.damage_capability_shapes import SOURCE_RELATIVE_DAMAGE_VERSION_FIELD
    from ..rules.random_opponent_damage import RANDOM_OPPONENT_DAMAGE_FIELD, bind_random_opponent_damage
    from ..announced_damage import ANNOUNCED_DAMAGE_EFFECT_FIELD, bind_announced_damage_recipients
    from .direct_target_fields import bound_graveyard_return_target_snapshot
    try:
        if SOURCE_RELATIVE_DAMAGE_VERSION_FIELD in value:
            value = bind_source_relative_damage(host, value, item, resolve=lambda operand: resolve_semantic_value(host, operand, item))
        if 'fixed_target_role_damage' in value:
            from ..rules.fixed_damage_roles import bind_fixed_damage_roles
            value=bind_fixed_damage_roles(host,value,item)
        if 'fixed_target_controller_damage' in value:
            from ..rules.fixed_dual_damage import bind_fixed_dual_damage
            value = {**value,'fixed_target_controller_damage':bind_fixed_dual_damage(host,value,item)}
        if value.get(ANNOUNCED_DAMAGE_EFFECT_FIELD) is True:
            total, recipients = bind_announced_damage_recipients(host, value, item)
            value = {**value, "amount": total, ANNOUNCED_DAMAGE_EFFECT_FIELD: recipients}
        if RANDOM_OPPONENT_DAMAGE_FIELD in value:
            value = bind_random_opponent_damage(host, value, item)
        missing = bound_mutation_target_overrides(host, value, item,
            lambda reference: resolve_semantic_value(host, reference, item))
        if missing:
            return {**value, **missing}
        value = {**value, **bound_stack_damage_source(host, value, item),
            **bound_graveyard_return_target_snapshot(value, item,
                lambda reference: resolve_semantic_value(host, reference, item))}
    except (TypeError, ValueError) as exc:
        raise SemanticNodeError(str(exc)) from exc
    source_exclusion = (
        value.get("op") == "choose_cards_apnap"
        and value.get("then") in {"return_owner_hand", "sacrifice"}
        and value.get("exclude_ref") == "$source.zone_object"
    )
    if source_exclusion:
        source_id = item.source_object_id
        source_incarnation = item.context.get("source_logical_object_id")
        if (
            type(source_id) is not str
            or not source_id
            or type(source_incarnation) is not str
            or not source_incarnation
        ):
            raise SemanticNodeError("Source exclusion requires the triggering source incarnation")
    resolved = {key: resolve_semantic_value(host, child, item) for key, child in value.items()}
    if source_exclusion and resolved["exclude_ref"] is None:
        del resolved["exclude_ref"]
    if value.get('op')=='damage':
        from ..rules.referenced_damage_shapes import referenced_damage_kind
        try:
            kind=referenced_damage_kind(value.get('target'))
            if kind is not None:
                target=resolved.get('target')
                if target is None:resolved['amount']=0
                elif kind!='controller':
                    card=next((c for c in host.state.cards.values() if c.ref==target),None)
                    if card is None:resolved['amount']=0
                    else:
                        types=host._type_parts(str(host._effective_card_data(card).get('type_line') or ''))[0]
                        if not types.intersection({'creature','planeswalker','battle'}):resolved['amount']=0
        except (TypeError,ValueError) as exc:raise SemanticNodeError(str(exc)) from exc
    return resolved


def _resolve_granting_attachment_sacrifice(host: SemanticValueHost, item: Any) -> str | None:
    from ..granted_activation_model import (
        GRANTING_ATTACHMENT_PAYMENT_FIELD, GrantingAttachmentPayment, GrantingAttachmentPaymentKind,
    )

    try:
        payment = GrantingAttachmentPayment.from_dict(item.context.get(GRANTING_ATTACHMENT_PAYMENT_FIELD))
    except (TypeError, ValueError) as exc:
        raise SemanticNodeError(str(exc)) from exc
    identity = payment.granting_object
    if payment.kind is not GrantingAttachmentPaymentKind.SACRIFICE or identity is None:
        raise SemanticNodeError('The resolved cost requires the original granting Equipment sacrifice')
    card = host.state.cards.get(identity.object_id)
    if (card is None or card.zone != 'battlefield' or card.phased_out
            or card.logical_object_id != identity.logical_object_id):
        return None
    return card.ref


def _resolve_wrapped_effect(host, value, item):
    if value.get("op") == "place_counters_on_targets":
        from ..rules.counter_target_set_shapes import bind_paid_white_entry_target_amount, bind_split_two_counter_allocation

        try:
            value = bind_paid_white_entry_target_amount(value, item)
            value = bind_split_two_counter_allocation(value, item)
        except (TypeError, ValueError, KeyError) as exc:
            raise SemanticNodeError(str(exc)) from exc
    from ..rules.entrant_power_branch import bind_entrant_power_branch, uses_entrant_power_branch

    if uses_entrant_power_branch(value):
        try:
            return bind_entrant_power_branch(
                host, value, item, resolve=lambda operand: resolve_semantic_value(host, operand, item),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise SemanticNodeError(str(exc)) from exc
    from ..rules.loyalty_damage import LOYALTY_X_DAMAGE_FIELD, bind_loyalty_x_target_damage

    if LOYALTY_X_DAMAGE_FIELD in value:
        try:
            value = bind_loyalty_x_target_damage(host, value, item)
        except (TypeError, ValueError) as exc:
            raise SemanticNodeError(str(exc)) from exc
    token = _bind_conditional_exile_token(value, item)
    return token if token is not None else _resolve_effect_mapping(host, value, item)


def resolve_semantic_value(
    host: SemanticValueHost,
    value: Any,
    item: Any,
) -> Any:
    """Resolve transport-safe runtime placeholders against one stack item."""

    if isinstance(value, list):
        return [resolve_semantic_value(host, child, item) for child in value]
    from ..paid_activation_card_types import REFERENCE_KIND as PAID_CARD_TYPES_REFERENCE, resolve_paid_type_condition
    if isinstance(value, Mapping) and value.get("kind") == PAID_CARD_TYPES_REFERENCE:
        try:
            return resolve_paid_type_condition(value, item)
        except (TypeError, ValueError) as exc:
            raise SemanticNodeError(str(exc)) from exc
    if isinstance(value, Mapping) and value.get("kind") == PAID_SACRIFICE_CONDITION_VALUE:
        try:
            return resolve_paid_sacrifice_condition(value, item)
        except (TypeError, ValueError) as exc:
            raise SemanticNodeError(str(exc)) from exc
    if isinstance(value, Mapping) and value.get('kind') in {CAST_MANA_UNITS_KIND, ACTUAL_CAST_MANA_TOTAL_KIND}:
        from ..rules.paid_mana_resolution import resolve_cast_mana_units
        try:return resolve_cast_mana_units(host,value,item)
        except (TypeError,ValueError) as exc:raise SemanticNodeError(str(exc)) from exc
    if isinstance(value, Mapping) and value.get('kind') in {
            'target_controller_information','target_owner_information','occurrence_controller_information',
            'damage_dealer_controller_information','opponent_damage_source_sacrifice_player_set'}:
        return _resolve_target_information_value(host,value,item)
    if isinstance(value, Mapping) and value.get("kind") == "source_charge_life_total":
        from ..rules.source_charge_life_totals import resolve_source_charge_life_value

        try:
            return resolve_source_charge_life_value(host, value, item)
        except (TypeError, ValueError) as exc:
            raise SemanticNodeError(str(exc)) from exc
    if isinstance(value, Mapping) and value.get('kind') == 'reflexive_trigger_program':
        from ..reflexive_trigger_model import ReflexiveTriggerProgram
        try:
            return ReflexiveTriggerProgram.from_dict(value).to_dict()
        except (TypeError, ValueError) as exc:
            raise SemanticNodeError(str(exc)) from exc
    if isinstance(value, Mapping) and value.get("kind") == "source_attachment":
        return _resolve_attachment_reference(host, value, item)
    if isinstance(value, Mapping) and value.get('kind')=='source_attachment_controller_information':
        from ..rules.attachment_controller_information import resolve_attachment_controller
        try:return resolve_attachment_controller(host,value,item)
        except (TypeError,ValueError,KeyError) as exc:raise SemanticNodeError(str(exc)) from exc
    if isinstance(value, Mapping) and value.get('kind') in (PUBLIC_QUERY_AMOUNT_KIND, CAST_X_AMOUNT_KIND, SCALAR_AMOUNT_KIND, 'source_entry_x_amount','devoured_entry_creatures_amount','selected_target_count', 'source_entry_designation_predicate','source_entry_designation_scope','source_entry_designation_library_filter','unique_player_maximum','target_player_public_query_amount','per_player_public_query_damage_amount','bound_per_player_public_query_damage_amount','per_player_current_life_damage_amount','bound_per_player_current_life_damage_amount','per_player_public_query_life_loss_amount','bound_per_player_public_query_life_loss_amount','all_player_turn_life_loss_amount','current_controller_life_effect_amount','target_player_greatest_creature_power'):
        return _resolve_typed_semantic_value(host, value, item)
    if isinstance(value, dict):
        return _resolve_wrapped_effect(host, value, item)
    if not isinstance(value, str) or not value.startswith("$"):
        return value
    if value == "$controller":
        return item.controller
    if value == "$active":
        return host.state.active_player
    if value == "$source":
        return host._stack_source_ref(item)
    if value == '$source.discarded_mana_value':
        from ..transmute import retained_transmute_mana_value
        try:
            return retained_transmute_mana_value(item)
        except ValueError as exc:
            raise SemanticNodeError(str(exc)) from exc
    if value == GRANTING_ATTACHMENT_SACRIFICE_REF:
        return _resolve_granting_attachment_sacrifice(host, item)
    if value in ("$source.zone_object", "$source.graveyard_object"):
        zone = "battlefield" if value == "$source.zone_object" else "graveyard"
        object_id = str(getattr(item, "source_object_id", None) or "")
        logical_object_id = str(
            getattr(item, "context", {}).get("source_logical_object_id")
            or ""
        )
        source = host.state.cards.get(object_id)
        if (
            source is None
            or source.zone != zone
            or source.phased_out
            or not logical_object_id
            or source.logical_object_id != logical_object_id
        ):
            return None
        return source.ref
    from ..event_counter_reference import resolve_sealed_event_object_value
    try:matched, reference = resolve_sealed_event_object_value(host, item, value)
    except ValueError as exc:raise SemanticNodeError(str(exc)) from exc
    if matched:return reference
    if value == "$source.transform_count":
        count = getattr(item, "context", {}).get("source_transform_count")
        if type(count) is not int or count < 0:
            return None
        return count
    if value == "$source.controller":
        return explore_source_controller(item, host.state.cards)
    if value == "$source.connive_controller":
        from ..connive import connive_source_controller
        return connive_source_controller(item,host.state.cards)
    if value == "$card":
        card = host.state.cards.get(item.card_object_id or "")
        return card.ref if card else None
    if value == "$stack":
        return item.ref
    if value == "$x":
        return item.x_value or 0
    if value == "$neg_x":
        return -(item.x_value or 0)
    if value == "$turn_sequence":
        return host.state.turn_sequence
    if value == "$station.power":
        try:
            return station_resolution_power(host, item)
        except StationAbilityError as exc:
            raise SemanticNodeError(str(exc)) from exc
    if value.startswith("$context."):
        return item.context.get(value.removeprefix("$context."))
    target_group_match = _TARGET_GROUP_REFERENCE.fullmatch(value)
    if target_group_match:
        return _resolve_target_group_reference(host, target_group_match, item)
    if value == "$targets":
        return [target for target in item.targets if target is not None]
    current_controller_match = re.fullmatch(
        r"\$target\.current_controller[.\[](?P<index>\d+)\]?",
        value,
    )
    if current_controller_match:
        index = int(current_controller_match.group(_INDEX_GROUP))
        if index >= len(item.targets) or item.targets[index] is None:
            return None
        snapshot = host._target_snapshot(str(item.targets[index]))
        return snapshot.get("controller")
    attribute_match = re.fullmatch(
        r"\$target\.(?P<attribute>controller|owner|mana_value|colors|type_line)"
        r"[.\[](?P<index>\d+)\]?",
        value,
    )
    if attribute_match:
        index = int(attribute_match.group(_INDEX_GROUP))
        if index >= len(item.targets):
            return None
        target_ref = item.targets[index]
        if target_ref is None:
            return None
        snapshot = dict(
            item.context.get("target_snapshots", {}).get(
                str(target_ref),
                host._target_snapshot(str(target_ref)),
            )
        )
        return snapshot.get(attribute_match.group("attribute"))
    target_match = re.fullmatch(r"\$target[.\[](?P<index>\d+)\]?", value)
    return _current_target_value(host, item, int(target_match.group(_INDEX_GROUP))) if target_match else value


def _current_target_value(host, item, index):
    if index >= len(item.targets):
        return None
    ref = item.targets[index]
    from ..pawprint_modes import PAWPRINT_SELECTION_FIELD
    schema = getattr(item, "context", {}).get("target_schema_override") or {}
    if ref is not None and schema.get(PAWPRINT_SELECTION_FIELD):
        from ..selection.target_query import target_identity_matches_snapshot

        snapshot = item.context.get("target_snapshots", {}).get(ref, {})
        if not target_identity_matches_snapshot(host, ref, snapshot):
            return None
    return ref


__all__ = ["SemanticValueHost", "resolve_semantic_value"]


def _resolve_target_information_value(host,value,item):
    """Keep the related current, departure and physical-owner value codecs together."""
    from ..target_controller_information import (
        resolve_target_controller_information,resolve_target_owner_information,resolve_occurrence_controller_information,
    )
    from ..rules.opponent_damage_source_sacrifice import resolve_opponent_damage_source_players

    reader={'target_controller_information':resolve_target_controller_information,
        'target_owner_information':resolve_target_owner_information,
        'occurrence_controller_information':resolve_occurrence_controller_information,
        'damage_dealer_controller_information':resolve_occurrence_controller_information,
        'opponent_damage_source_sacrifice_player_set':resolve_opponent_damage_source_players}[value['kind']]
    try:return reader(host,value,item)
    except (TypeError,ValueError) as exc:raise SemanticNodeError(str(exc)) from exc
