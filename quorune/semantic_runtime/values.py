from __future__ import annotations

import re
from typing import Any, Mapping, Protocol

from ..attachment_references import (
    AttachmentReferenceError,
    AttachmentReferenceSpec,
    resolve_source_attachment,
)
from ..station import StationAbilityError, station_resolution_power
from ..graveyard_exile_token_model import GRAVEYARD_EXILE_TOKEN_OPERATION
from ..rules.graveyard_exile_type_outcomes import GRAVEYARD_EXILE_TYPE_OUTCOME_OPERATION
from ..paid_sacrifice_conditions import PAID_SACRIFICE_CONDITION_VALUE
from ..query_effect_amount_model import (
    PUBLIC_QUERY_AMOUNT_KIND,
    CAST_X_AMOUNT_KIND,
    PublicQueryAmountError,
)

from .context import SemanticNodeError
from .explore import explore_source_controller
from .query_effect_amounts import resolve_public_query_amount, resolve_cast_x_amount
from ..scalar_effect_amount_model import SCALAR_AMOUNT_KIND
from ..rules.revealed_library_top import REVEAL_TOP_OPERATION
from ..scalar_effect_amounts import resolve_paid_sacrifice_condition, resolve_scalar_effect_amount
from .direct_target_fields import bound_mutation_target_overrides


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
    if spec.schema_version==2:
        from ..attachment_references import ATTACHMENT_INFORMATION_VERSION
        version=item.context.get(ATTACHMENT_INFORMATION_VERSION)
        if type(version) is not int or version!=1:
            raise AttachmentReferenceError('Version-two attachment references require captured departure information')
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
    card_types = host._type_parts(
        str(host._target_snapshot(target.ref).get("type_line") or "")
    )[0]
    if (
        spec.required_card_type != "permanent"
        and spec.required_card_type not in card_types
    ):
        return None
    return target.ref


def _resolve_typed_semantic_value(host, value, item):
    """Delegate typed amounts and public predicates to their narrow owners."""
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
    if value['kind']=='source_entry_x_amount':
        from ..entry_x_amount_model import resolve_entry_x_amount
        try:return resolve_entry_x_amount(value,item)
        except (ValueError,TypeError) as exc:raise SemanticNodeError(str(exc)) from exc
    if value['kind'] in {'source_entry_designation_predicate','source_entry_designation_scope'}:
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
            return resolve_scalar_effect_amount(host, value, item)
    except PublicQueryAmountError as exc:
        raise SemanticNodeError(str(exc)) from exc
    raise SemanticNodeError('Typed semantic value kind is unsupported')


def _resolve_effect_mapping(host, value, item):
    if value.get("op") == "look_top_shared_type_reveal":
        from ..rules.shared_type_library_model import SharedTypeLibraryResult
        try:
            SharedTypeLibraryResult.from_dict(value["result"])
        except (TypeError, ValueError, KeyError) as exc:
            raise SemanticNodeError(str(exc)) from exc
        return {**value, "player": resolve_semantic_value(host, value["player"], item)}
    if (value.get("op") == "create_token" and isinstance(value.get("copy_spec"), Mapping)
            and value["copy_spec"].get("schema_version") in {3, 4}):
        from ..token_copy_recipes import TokenCopyRecipeSpec
        try:
            recipe = TokenCopyRecipeSpec.from_dict(value["copy_spec"])
        except (TypeError, ValueError) as exc:
            raise SemanticNodeError(str(exc)) from exc
        # The nested reference declares capture; the copy owner resolves it later.
        return {key: recipe.to_dict() if key == "copy_spec"
                else resolve_semantic_value(host, child, item)
                for key, child in value.items()}
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
    if value.get("op") == "resolve_stack_counter_followup":
        from ..rules.stack_counter_followup import materialize_stack_counter_followup
        try:
            return materialize_stack_counter_followup(value, resolve=lambda operand: resolve_semantic_value(host, operand, item))
        except (TypeError, ValueError, KeyError) as exc:
            raise SemanticNodeError(str(exc)) from exc
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
    if value.get('op') == 'resolve_fixed_object_action' and value.get('schema_version') == 3:
        from ..rules.reanimation_followup import current_linked_subject, validate_linked_departure

        try:
            subject = validate_linked_departure(value)
        except (ValueError, TypeError) as exc:
            raise SemanticNodeError(str(exc)) from exc
        return {**value, 'object': None} if current_linked_subject(host, subject, zone='battlefield') is None else dict(value)
    if value.get('op') == 'reanimate' and ('attachment_return' in value or value.get('schema_version') == 7):
        from ..attachment_source_return import bind_attachment_source_return
        try:return bind_attachment_source_return(value, item)
        except (TypeError, ValueError, KeyError) as exc:raise SemanticNodeError(str(exc)) from exc
    if value.get('op')=='schedule_fixed_object_action':
        from ..delayed_object_actions import bind_delayed_object_instruction
        try:return bind_delayed_object_instruction(host,value,item)
        except (TypeError,ValueError,KeyError) as exc:raise SemanticNodeError(str(exc)) from exc
    result_operations = (GRAVEYARD_EXILE_TOKEN_OPERATION, GRAVEYARD_EXILE_TYPE_OUTCOME_OPERATION)
    deferred_field = 'result' if value.get('op') in result_operations else 'followup'
    if deferred_field in value and value.get('op') in (
        'destroy_all', 'move_public_zone_set', REVEAL_TOP_OPERATION, *result_operations,
    ):
        preceding_action = {key: child for key, child in value.items() if key != deferred_field}
        return {**_resolve_effect_mapping(host, preceding_action, item), deferred_field: value[deferred_field]}
    if value.get('op')=='create_token_then_counters':
        from ..created_token_counter_model import validate_created_token_counter
        try:validate_created_token_counter(value)
        except (TypeError,ValueError,KeyError) as exc:raise SemanticNodeError(str(exc)) from exc
        return {**value,'controller':resolve_semantic_value(host,value['controller'],item),
            'source':resolve_semantic_value(host,value['source'],item),
            'token':resolve_semantic_value(host,value['token'],item)}
    if value.get('op')=='apply_if_public_condition' and value.get('schema_version') in {10,11}:
        from ..resolution_conditions import validate_resolution_condition_instruction
        validate_resolution_condition_instruction(value)
        return {**value,'player':resolve_semantic_value(host,value['player'],item),
            'cast_fact':resolve_semantic_value(host,value['cast_fact'],item)}
    from ..rules.stack_damage_sources import bound_stack_damage_source
    from ..announced_damage import ANNOUNCED_DAMAGE_EFFECT_FIELD, bind_announced_damage_recipients
    from .direct_target_fields import bound_graveyard_return_target_snapshot
    try:
        if 'fixed_target_role_damage' in value:
            from ..rules.fixed_damage_roles import bind_fixed_damage_roles
            value=bind_fixed_damage_roles(host,value,item)
        if 'fixed_target_controller_damage' in value:
            from ..rules.fixed_dual_damage import bind_fixed_dual_damage
            value = {**value,'fixed_target_controller_damage':bind_fixed_dual_damage(host,value,item)}
        if value.get(ANNOUNCED_DAMAGE_EFFECT_FIELD) is True:
            total, recipients = bind_announced_damage_recipients(host, value, item)
            value = {**value, "amount": total, ANNOUNCED_DAMAGE_EFFECT_FIELD: recipients}
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


def resolve_semantic_value(
    host: SemanticValueHost,
    value: Any,
    item: Any,
) -> Any:
    """Resolve transport-safe runtime placeholders against one stack item."""

    if isinstance(value, list):
        return [resolve_semantic_value(host, child, item) for child in value]
    if isinstance(value, Mapping) and value.get("kind") == PAID_SACRIFICE_CONDITION_VALUE:
        try:
            return resolve_paid_sacrifice_condition(value, item)
        except (TypeError, ValueError) as exc:
            raise SemanticNodeError(str(exc)) from exc
    if isinstance(value, Mapping) and value.get('kind')=='cast_mana_spent_units':
        from ..rules.paid_mana_resolution import resolve_cast_mana_units
        try:return resolve_cast_mana_units(host,value,item)
        except (TypeError,ValueError) as exc:raise SemanticNodeError(str(exc)) from exc
    if isinstance(value, Mapping) and value.get('kind') in {
            'target_controller_information','target_owner_information','occurrence_controller_information'}:
        return _resolve_target_information_value(host,value,item)
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
    if isinstance(value, Mapping) and value.get('kind') in (PUBLIC_QUERY_AMOUNT_KIND, CAST_X_AMOUNT_KIND, SCALAR_AMOUNT_KIND, 'source_entry_x_amount','selected_target_count', 'source_entry_designation_predicate','source_entry_designation_scope','unique_player_maximum','target_player_public_query_amount','per_player_public_query_damage_amount','bound_per_player_public_query_damage_amount','per_player_current_life_damage_amount','bound_per_player_current_life_damage_amount','per_player_public_query_life_loss_amount','bound_per_player_public_query_life_loss_amount','all_player_turn_life_loss_amount','current_controller_life_effect_amount'):
        return _resolve_typed_semantic_value(host, value, item)
    if isinstance(value, dict):
        return _resolve_effect_mapping(host, value, item)
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
    if target_match:
        index = int(target_match.group(_INDEX_GROUP))
        if index >= len(item.targets):
            return None
        return item.targets[index]
    return value


__all__ = ["SemanticValueHost", "resolve_semantic_value"]


def _resolve_target_information_value(host,value,item):
    """Keep the related current, departure and physical-owner value codecs together."""
    from ..target_controller_information import (
        resolve_target_controller_information,resolve_target_owner_information,resolve_occurrence_controller_information,
    )
    reader={'target_controller_information':resolve_target_controller_information,
        'target_owner_information':resolve_target_owner_information,
        'occurrence_controller_information':resolve_occurrence_controller_information}[value['kind']]
    try:return reader(host,value,item)
    except (TypeError,ValueError) as exc:raise SemanticNodeError(str(exc)) from exc
