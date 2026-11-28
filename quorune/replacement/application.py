from __future__ import annotations

from ..rules.linked_graveyard_play import EXILE_LINK_FIELD, PRIOR_CONTROL_FIELD, checked_exile_marker

from typing import Any, Iterable, Mapping, Sequence

from .face_status_copy import ENTRY_COPY_HISTORY_FIELD, FACE_UP_COPY_EVENT, FACE_UP_COPY_RESULT
from ..turn_skip_model import TURN_BOUNDARY_SKIP_EVENT
from ..day_night_model import DAY_ENTRY_FIELD, DAY_ENTRY_HANDLER
from ..self_graveyard_exile_model import SELF_GRAVEYARD_SHUFFLE_RESULT, RevealForOwnerLibraryShuffle

from .applicability import canonical_effects, replacement_choice
from .immutable import thaw_value
from .model import (
    COUNTER_PLACEMENT_ROUNDED_TO_ZERO_FIELD,
    COUNTER_REPLACEMENT_GENERATED_FIELD,
    AffectedObject,
    EntryReplacementScope,
    ReplaceableEvent,
    ReplacementChoice,
    ReplacementEffect,
    ReplacementEffectError,
    mapping_sequence,
    string_sequence,
)
from .counted_entry_counters import (
    ENTRY_COUNTER_TRANSFER_OPERATIONS, ChosenTransferEntryCounters, apply_counted_entry_counters,
    apply_transferred_entry_counters,
)
from .operations import (
    AmplifyEntryOperation,
    OpenTributeEntryChoice,
    CompleteTributeEntryChoice,
    DevourEntryOperation,
    GraveyardCopyEntryOperation,
    LinkedGraveyardEntryOperation,
    ENTRY_OBJECT_SELECTION_OPERATIONS,
    AddAmount,
    AppendValues,
    CapResultLifeLoss,
    CreateAffectedObjectCounter,
    CreateCountedEntryCounters,
    AddAffectedObjectCounter,
    ApplyEntryCounterReplacement,
    GrantAffectedObjectSubtype,
    CreateAdditionalToken,
    MATCH_PENDING_TOKEN_QUANTITY,
    CreateResultDraws,
    CreateNestedEvent,
    DiscardInsteadOfDraw,
    EXILE_TURN_PLAY_DRAW_RESULT,
    WIN_INSTEAD_OF_EMPTY_DRAW,
    WinInsteadOfEmptyDraw,
    ExileTopForTurnPlayInsteadOfDraw,
    DredgeDraw,
    GrantAffectedObjectKeyword,
    MultiplyAmount,
    HalveCounterPlacementAmount,
    MultiplyTokenCreation,
    ReplaceCreatureTokenDefinition,
    ReplacePredefinedArtifactTokenSet,
    PreventAmount,
    PreventDraw,
    ReplaceDrawWithScheduledResult,
    ReplaceDrawWithLibraryChoice,
    PreventUsingShield,
    RedirectDamage,
    ReserveZoneChange,
    SetField,
    UnionValues,
)


_LIFE_LOSS_DIRECTION = "loss"


_SET_FIELDS = {
    "damage": {"amount", "prevented", "prevented_by", "target"},
    "zone.change": {"destination", "read_ahead_chapter", "tapped", "entry_chosen_card_name", "entry_chosen_color", "entry_chosen_creature_type", "entry_chosen_basic_land_type", "entry_basic_land_type_life_available", "entry_basic_land_type_life_decided", "entry_chosen_faction", "entry_chosen_jeskai_temur_faction", "entry_chosen_opponent", "entry_linked_card_type", "entry_copy", "entry_copy_source", "entry_copy_form", "object_types", 'entry_revealed_hand_card','entry_land_discard','entry_prepared','_counter_permissions',ENTRY_COPY_HISTORY_FIELD,DAY_ENTRY_FIELD,EXILE_LINK_FIELD},
    "token.create": {"quantity", "created_types", "created_subtypes"},
    "counter.place": {"amount"},
    "counter.add": {"amount", "quantity"},
    "permanent.destroy": {"disposition", "clear_damage", "umbra_aura_object_id", "umbra_aura_logical_object_id"},
    "life.change": {"amount", "direction"},
    "draw.instruction": {"count"},
    "damage.results": {"life_loss_amount", "life_after_without_replacement"},
    "effect": {"resolved"},
    TURN_BOUNDARY_SKIP_EVENT: {"skipped"},
    FACE_UP_COPY_EVENT: {FACE_UP_COPY_RESULT},
}
_NUMERIC_FIELDS = {
    "damage": {"amount", "prevented"},
    "token.create": {"quantity"},
    "counter.place": {"amount"},
    "counter.add": {"amount", "quantity"},
    "life.change": {"amount"},
    "draw.instruction": {"count"},
    "zone.change": {"entry_life_payment"},
}
_SEQUENCE_FIELDS = {
    'zone.change':{'commander_component_returns', 'object_types'},
    "token.create": {"tokens", "created_types", "created_subtypes"},
}


def _require_field(
    event: ReplaceableEvent,
    field_name: str,
    allowed: Mapping[str, set[str]],
    *,
    operation: str,
) -> None:
    if field_name not in allowed.get(event.kind, set()):
        raise ReplacementEffectError(
            f"Replacement {operation} does not support {event.kind}.{field_name}"
        )


def _nested_event_from_mapping(
    parent: ReplaceableEvent,
    value: Mapping[str, Any],
    *,
    suffix: str,
) -> ReplaceableEvent:
    allowed = {
        "event_id",
        "kind",
        "affected_player",
        "affected_object",
        "payload",
        "applied_effects",
        "children",
        "entry_scope",
    }
    unknown = sorted(str(field) for field in set(value) - allowed)
    if unknown:
        raise ReplacementEffectError(
            "Nested replacement event has unknown field(s): "
            + ", ".join(unknown)
        )
    affected_object_value = value.get("affected_object")
    if affected_object_value is not None and not isinstance(
        affected_object_value, Mapping
    ):
        raise ReplacementEffectError(
            "Nested affected_object must be an object or null"
        )
    has_player = "affected_player" in value
    has_object = isinstance(affected_object_value, Mapping)
    if has_player and has_object:
        raise ReplacementEffectError(
            "A nested event cannot specify both affected subjects"
        )
    if has_player:
        affected_player = (
            str(value["affected_player"])
            if value.get("affected_player") is not None
            else None
        )
        affected_object = None
    elif has_object:
        affected_player = None
        affected_object = AffectedObject.from_dict(affected_object_value)
    else:
        affected_player = parent.affected_player
        affected_object = parent.affected_object
    payload = value["payload"] if "payload" in value else {}
    if not isinstance(payload, Mapping):
        raise ReplacementEffectError(
            "Nested replacement event payload must be an object"
        )
    entry_scope_value = value.get("entry_scope")
    if entry_scope_value is not None and not isinstance(
        entry_scope_value, Mapping
    ):
        raise ReplacementEffectError(
            "Nested entry_scope must be an object or null"
        )
    event = ReplaceableEvent(
        event_id=str(value.get("event_id") or f"{parent.event_id}/{suffix}"),
        kind=str(value.get("kind") or ""),
        affected_player=affected_player,
        affected_object=affected_object,
        payload=payload,
        applied_effects=string_sequence(
            value.get("applied_effects", ()),
            field_name="nested applied_effects",
        ),
        entry_scope=(
            EntryReplacementScope.from_dict(entry_scope_value)
            if isinstance(entry_scope_value, Mapping)
            else None
        ),
    )
    children = tuple(
        _nested_event_from_mapping(event, child, suffix=str(index))
        for index, child in enumerate(
            mapping_sequence(
                value.get("children", ()),
                field_name="nested event children",
            )
        )
    )
    if not children:
        return event
    return ReplaceableEvent(
        event_id=event.event_id,
        kind=event.kind,
        affected_player=event.affected_player,
        affected_object=event.affected_object,
        payload=event.payload,
        applied_effects=event.applied_effects,
        children=children,
        entry_scope=event.entry_scope,
    )


def _apply_result_life_floor(
    event: ReplaceableEvent,
    payload: dict[str, Any],
    children: list[ReplaceableEvent],
    operation: CapResultLifeLoss,
) -> None:
    if event.kind != "damage.results" or event.affected_player is None:
        raise ReplacementEffectError(
            "A damage-result life floor requires a player result event"
        )
    try:
        life_before = int(payload["life_before"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ReplacementEffectError(
            "A damage-result life floor requires the prior life total"
        ) from exc
    losses = [
        (index, child)
        for index, child in enumerate(children)
        if child.kind == "life.change"
        and child.affected_player == event.affected_player
        and child.payload.get("direction") == _LIFE_LOSS_DIRECTION
    ]
    gains = [
        child
        for child in children
        if child.kind == "life.change"
        and child.affected_player == event.affected_player
        and child.payload.get("direction") == "gain"
    ]
    if not losses:
        raise ReplacementEffectError(
            "A damage-result life floor requires a life-loss result"
        )
    loss_total = sum(int(child.payload.get("amount", 0)) for _, child in losses)
    gain_total = sum(int(child.payload.get("amount", 0)) for child in gains)
    if life_before - loss_total + gain_total >= operation.minimum:
        raise ReplacementEffectError(
            "A damage-result life floor is not applicable"
        )
    remaining = max(0, life_before + gain_total - operation.minimum)
    for index, child in losses:
        kept = min(int(child.payload.get("amount", 0)), remaining)
        remaining -= kept
        child_payload = thaw_value(child.payload)
        child_payload["amount"] = kept
        children[index] = ReplaceableEvent(
            event_id=child.event_id,
            kind=child.kind,
            affected_player=child.affected_player,
            affected_object=child.affected_object,
            payload=child_payload,
            applied_effects=child.applied_effects,
            children=child.children,
            entry_scope=child.entry_scope,
        )
    resolved_loss = sum(
        int(child.payload.get("amount", 0))
        for child in children
        if child.kind == "life.change"
        and child.affected_player == event.affected_player
        and child.payload.get("direction") == _LIFE_LOSS_DIRECTION
    )
    payload["life_loss_amount"] = resolved_loss
    payload["life_after_without_replacement"] = life_before - resolved_loss + gain_total


def _result_draw_exclusions(
    event: ReplaceableEvent, payload: dict[str, Any], effect_id: str
) -> tuple[str, ...]:
    if (
        event.kind != "draw"
        or event.affected_player is None
        or payload.get("is_draw") is not True
    ):
        raise ReplacementEffectError(
            "Result draws require one unresolved affected-player draw"
        )
    inherited = payload.get("excluded_effect_ids", ())
    if not isinstance(inherited, (list, tuple)) or any(
        type(value) is not str or not value for value in inherited
    ):
        raise ReplacementEffectError(
            "Result-draw exclusions must be stable effect IDs"
        )
    return tuple(sorted({*inherited, effect_id}))


def _create_result_draw_instruction(
    event: ReplaceableEvent,
    payload: dict[str, Any],
    children: list[ReplaceableEvent],
    operation: CreateResultDraws,
    *,
    effect_id: str,
) -> None:
    """Replace one draw with a typed, independently replaceable result queue."""

    exclusions = _result_draw_exclusions(event, payload, effect_id)
    recipient = operation.recipient or event.affected_player
    payload["is_draw"] = False
    payload["result_kind"] = "result_draws"
    children.append(
        ReplaceableEvent(
            event_id=f"{event.event_id}/result-draw:{len(children)}",
            kind="draw.result_instruction",
            affected_player=recipient,
            payload={
                "player": recipient,
                "count": operation.count,
                "reason": payload.get("reason"),
                "private": payload.get("private"),
                "excluded_effect_ids": list(exclusions),
            },
        )
    )


def _create_affected_object_counter_event(
    event: ReplaceableEvent,
    payload: Mapping[str, Any],
    children: list[ReplaceableEvent],
    operation: CreateAffectedObjectCounter,
    *,
    effect_id: str,
) -> None:
    if event.kind != "zone.change" or event.affected_object is None:
        raise ReplacementEffectError(
            "Affected-object counters require a zone-change object event"
        )
    object_ref = payload.get("object_ref")
    object_types = payload.get("object_types")
    destination = payload.get("destination")
    if type(object_ref) is not str or not object_ref:
        raise ReplacementEffectError(
            "Affected-object counters require the object's public ref"
        )
    if not isinstance(object_types, (list, tuple)) or any(
        type(value) is not str or not value for value in object_types
    ):
        raise ReplacementEffectError(
            "Affected-object counters require canonical object types"
        )
    if type(destination) is not str or not destination:
        raise ReplacementEffectError(
            "Affected-object counters require a resolved destination"
        )
    target_controller = (
        payload.get("destination_controller")
        if destination == "battlefield"
        else None
    )
    event_id = (
        f"zone.counter:{object_ref}:{operation.source_ref}:"
        f"{operation.sequence}"
    )
    if any(child.event_id == event_id for child in children):
        event_id = f"{event_id}:{effect_id}"
    from .entry_counter_types import BATTLEFIELD_ENTRY_COUNTER_CHILD, BATTLEFIELD_ENTRY_COUNTER_SCOPE

    children.append(
        ReplaceableEvent(
            event_id=event_id,
            kind="counter.place",
            affected_player=None,
            affected_object=AffectedObject(
                object_id=event.affected_object.object_id,
                owner=event.affected_object.owner,
                controller=(
                    str(target_controller)
                    if target_controller is not None
                    else None
                ),
            ),
            payload={
                "placing_player": operation.placing_player,
                "target_controller": target_controller,
                "target_zone": destination,
                "target_kind": (
                    "permanent" if destination == "battlefield" else "card"
                ),
                "target_types": sorted(set(object_types)),
                "counter_name": operation.counter_name,
                "amount": operation.amount,
                "requested_amount": operation.amount,
                "source": operation.source_ref,
                "effect_generated": True,
                "follows_zone_destination": True,
                **({BATTLEFIELD_ENTRY_COUNTER_CHILD: True}
                   if payload.get(BATTLEFIELD_ENTRY_COUNTER_SCOPE) is True and destination == "battlefield" else {}),
                **({"prospective_subject": True,
                    "target_logical_object_id": payload.get("logical_object_id")}
                   if payload.get("prospective_subject") is True else {}),
            },
        )
    )


def _retarget_zone_counter_children(
    event: ReplaceableEvent,
    payload: Mapping[str, Any],
    children: list[ReplaceableEvent],
) -> None:
    """Keep typed "with counters" results attached to the final zone event."""

    if event.kind != "zone.change" or event.affected_object is None:
        return
    destination = payload.get("destination")
    if type(destination) is not str or not destination:
        raise ReplacementEffectError(
            "Zone counter results require a resolved destination"
        )
    target_controller = (
        payload.get("destination_controller")
        if destination == "battlefield"
        else None
    )
    for index, child in enumerate(children):
        if child.children:
            from dataclasses import replace
            nested=list(child.children)
            _retarget_zone_counter_children(event,payload,nested)
            child=replace(child,children=tuple(nested))
            children[index]=child
        if (
            child.kind != "counter.place"
            or child.affected_object is None
            or child.affected_object.object_id
            != event.affected_object.object_id
            or child.payload.get("follows_zone_destination") is not True
        ):
            continue
        child_payload = thaw_value(child.payload)
        child_payload["target_zone"] = destination
        child_payload["target_kind"] = (
            "permanent" if destination == "battlefield" else "card"
        )
        child_payload["target_controller"] = target_controller
        children[index] = ReplaceableEvent(
            event_id=child.event_id,
            kind=child.kind,
            affected_player=child.affected_player,
            affected_object=AffectedObject(
                object_id=child.affected_object.object_id,
                owner=child.affected_object.owner,
                controller=(
                    str(target_controller)
                    if target_controller is not None
                    else None
                ),
            ),
            payload=child_payload,
            applied_effects=child.applied_effects,
            children=child.children,
            entry_scope=child.entry_scope,
        )


def _shared_token_creation_status(tokens):
    """Keep instruction status distinct from the replaced token definition."""
    status={}
    for field,default in (('tapped',False),('temporary_keywords',()),('effect_entry_counters',())):
        values=[]
        for token in tokens:
            if not isinstance(token,Mapping):raise ReplacementEffectError('Token creation specification must be an object')
            value=token.get(field,default)
            if field=='tapped':
                if type(value) is not bool:raise ReplacementEffectError('Token tapped status must be boolean')
            elif field=='effect_entry_counters':
                from ..entry_counter_model import EffectEntryCounter
                if not isinstance(value,(list,tuple)):
                    raise ReplacementEffectError('Token entry counters must be an array')
                try:
                    value=tuple(EffectEntryCounter.from_dict(raw).to_dict() for raw in value)
                except ValueError as exc:
                    raise ReplacementEffectError(str(exc)) from exc
            else:
                if not isinstance(value,(list,tuple)) or any(type(keyword) is not str for keyword in value):
                    raise ReplacementEffectError('Token temporary keywords must be an array of strings')
                value=tuple(value)
            values.append(value)
        if values and any(value!=values[0] for value in values[1:]):
            raise ReplacementEffectError('Mixed token creation status requires an explicit per-definition scope')
        if values and values[0]!=default:status[field]=values[0] if field=='tapped' else list(values[0])
    return status


def _additional_token_recipe(operation, quantity, status):
    return {
        **status,
        "name": operation.name,
        "quantity": quantity,
        "characteristics": thaw_value(operation.characteristics),
        "replacement_component": {
            "handler_id": operation.handler_id,
            "source": operation.source_ref,
            "quantity": quantity,
        },
    }


def _matching_additional_token_recipes(operation, tokens):
    recipes = []
    for token in tokens:
        status = _shared_token_creation_status((token,))
        quantity = token.get("quantity")
        if type(quantity) is not int or quantity < 1:
            raise ReplacementEffectError("Matching additional token count needs positive pending quantities")
        attack = {field: token[field] for field in ("attacking", "_attacking_group") if token.get(field) is not None}
        if len(attack) > 1 or any(type(value) is not str or not value for value in attack.values()):
            raise ReplacementEffectError("Pending token attack assignments must be well formed")
        recipes.append(_additional_token_recipe(operation, quantity, {**status, **attack}))
    return recipes


def _apply_additional_token(
    event: ReplaceableEvent,
    payload: dict[str, Any],
    operation: CreateAdditionalToken,
) -> None:
    if event.kind != "token.create":
        raise ReplacementEffectError(
            "Additional-token creation requires a token.create event"
        )
    for field_name in ("tokens", "created_types", "created_subtypes"):
        current = payload.get(field_name, ())
        if not isinstance(current, Sequence) or isinstance(
            current, (str, bytes)
        ):
            raise ReplacementEffectError(
                f"Token creation {field_name} must be an array"
            )
    tokens = list(payload.get("tokens", ()))
    if operation.quantity == MATCH_PENDING_TOKEN_QUANTITY:
        additions = _matching_additional_token_recipes(operation, tokens)
    else:
        additions = [_additional_token_recipe(operation, operation.quantity, _shared_token_creation_status(tokens))]
    payload["tokens"] = [*tokens, *additions]
    payload["created_types"] = sorted(
        {
            *payload.get("created_types", ()),
            *operation.card_types,
        }
    )
    payload["created_subtypes"] = sorted(
        {
            *payload.get("created_subtypes", ()),
            *operation.subtypes,
        }
    )


def _apply_token_multiplier(
    event: ReplaceableEvent,
    payload: dict[str, Any],
    operation: MultiplyTokenCreation,
) -> None:
    if event.kind != "token.create":
        raise ReplacementEffectError(
            "Token multiplication requires a token.create event"
        )
    tokens = payload.get("tokens", ())
    if not isinstance(tokens, Sequence) or isinstance(tokens, (str, bytes)):
        raise ReplacementEffectError("Token creation tokens must be an array")
    originals: list[dict[str, Any]] = []
    for token in tokens:
        if not isinstance(token, Mapping):
            raise ReplacementEffectError(
                "Token creation specifications must be objects"
            )
        originals.append(thaw_value(token))
    component = {
        "handler_id": operation.handler_id,
        "source": operation.source_ref,
        "multiplier": operation.factor,
    }
    eligible = originals
    if operation.creature_only:
        from ..creature_token_definition_replacement import creation_member_is_creature

        try:
            eligible = [token for token in originals if creation_member_is_creature(token)]
        except ValueError as exc:
            raise ReplacementEffectError(str(exc)) from exc
        if not eligible:
            raise ReplacementEffectError("Creature multiplication requires a current creature member")
    copies = []
    for _ in range(operation.factor - 1):
        for token in eligible:
            clone = thaw_value(token)
            clone["replacement_component"] = component
            copies.append(clone)
    payload["tokens"] = [*originals, *copies]


def _apply_creature_token_definition(event, payload, operation):
    from ..creature_token_definition_replacement import replace_creature_token_definitions

    if event.kind != "token.create":
        raise ReplacementEffectError("Token definition replacement requires a creation event")
    try:
        tokens, types, subtypes = replace_creature_token_definitions(
            payload.get("tokens"), name=operation.name, characteristics=operation.characteristics,
            handler_id=operation.handler_id, source_ref=operation.source_ref,
        )
    except (TypeError, ValueError) as exc:
        raise ReplacementEffectError(str(exc)) from exc
    payload.update(tokens=tokens, created_types=types, created_subtypes=subtypes)


def _apply_predefined_artifact_token_set(event, payload, operation):
    from ..creature_token_definition_replacement import replace_predefined_artifact_token_set

    if event.kind != "token.create":
        raise ReplacementEffectError("Predefined token substitution requires a creation event")
    try:
        tokens, types, subtypes = replace_predefined_artifact_token_set(
            payload.get("tokens"), handler_id=operation.handler_id, source_ref=operation.source_ref,
        )
    except (TypeError, ValueError) as exc:
        raise ReplacementEffectError(str(exc)) from exc
    payload.update(tokens=tokens, created_types=types, created_subtypes=subtypes)


def _apply_affected_object_keyword(
    event: ReplaceableEvent,
    payload: dict[str, Any],
    operation: GrantAffectedObjectKeyword,
    *,
    effect_id: str,
) -> None:
    if (
        event.kind != "zone.change"
        or payload.get("destination") != "battlefield"
    ):
        raise ReplacementEffectError(
            "Affected-object keyword grants require battlefield entry"
        )
    grants = payload.get("entry_keyword_grants", ())
    if not isinstance(grants, Sequence) or isinstance(grants, (str, bytes)):
        raise ReplacementEffectError("Entry keyword grants must be an array")
    payload["entry_keyword_grants"] = [
        *list(grants),
        {
            "effect_id": effect_id,
            "keyword": operation.keyword,
            "sequence": operation.sequence,
        },
    ]


def _apply_amount_operation(
    event: ReplaceableEvent,
    payload: dict[str, Any],
    operation: AddAmount | MultiplyAmount | HalveCounterPlacementAmount,
) -> None:
    if isinstance(operation, AddAmount):
        _require_field(event, operation.field, _NUMERIC_FIELDS, operation="add")
        payload[operation.field] = int(payload.get(operation.field, 0)) + operation.amount
        return
    if isinstance(operation, MultiplyAmount):
        _require_field(event, operation.field, _NUMERIC_FIELDS, operation="multiply")
        payload[operation.field] = int(payload.get(operation.field, 0)) * operation.factor
        return
    amount = payload.get("amount")
    if event.kind != "counter.place" or type(amount) is not int or amount < 1:
        raise ReplacementEffectError("Counter placement halving requires a positive current placement")
    payload["amount"] = amount // 2
    if payload["amount"] == 0:
        payload[COUNTER_PLACEMENT_ROUNDED_TO_ZERO_FIELD] = True


def _apply_sequence_operation(event, payload, operation):
    if operation.field=='commander_component_returns':
        from ..composition_commander_replacement_model import validate_commander_component_returns
        if not isinstance(operation,UnionValues) or payload.get('destination') not in {'hand','library'}:
            raise ReplacementEffectError('Commander component return requires a hand/library union')
        try:validate_commander_component_returns(payload,operation.values)
        except ValueError as exc:raise ReplacementEffectError(str(exc)) from exc
    _require_field(event, operation.field, _SEQUENCE_FIELDS, operation="sequence")
    current = payload.get(operation.field, [])
    if not isinstance(current, Sequence) or isinstance(current, (str, bytes)):
        raise ReplacementEffectError("Replacement sequence destination must be an array")
    if operation.field == "object_types":
        from ..creature_subtypes import canonical_creature_subtype

        chosen = payload.get("entry_chosen_creature_type")
        if (not isinstance(operation, UnionValues) or payload.get("destination") != "battlefield"
                or not isinstance(chosen, str) or canonical_creature_subtype(chosen) != chosen
                or operation.values != (chosen,)):
            raise ReplacementEffectError("Prospective subtype addition requires its exact chosen creature type")
        if not {"creature", "kindred"}.intersection(current):
            return
    values = [*current, *(thaw_value(value) for value in operation.values)]
    payload[operation.field] = values if isinstance(operation, AppendValues) else sorted(set(values), key=str)


def _apply_draw_operation(event, payload, children, operation, *, effect_id) -> None:
    """Apply the typed operations that replace one draw event."""

    if isinstance(operation, WinInsteadOfEmptyDraw):
        size = payload.get("library_size")
        if event.kind != "draw" or payload.get("is_draw") is not True or type(size) is not int or size != 0:
            raise ReplacementEffectError("Winning instead of drawing requires one unresolved empty-library draw")
        payload.update(is_draw=False, result_kind=WIN_INSTEAD_OF_EMPTY_DRAW)
        return
    if isinstance(operation, ExileTopForTurnPlayInsteadOfDraw):
        if event.kind != "draw" or payload.get("is_draw") is not True:
            raise ReplacementEffectError("Exile play requires one unresolved draw")
        payload.update(is_draw=False, result_kind=EXILE_TURN_PLAY_DRAW_RESULT)
        return
    if isinstance(operation, ReplaceDrawWithScheduledResult):
        if event.kind != "draw" or payload.get("is_draw") is not True:
            raise ReplacementEffectError("A scheduled result requires an unresolved draw")
        payload.update(is_draw=False, result_kind="scheduled_result", scheduled_grant_id=operation.grant_id)
        return
    if isinstance(operation, ReplaceDrawWithLibraryChoice):
        if event.kind != "draw" or payload.get("is_draw") is not True:
            raise ReplacementEffectError("A library choice requires an unresolved draw")
        payload.update(is_draw=False, result_kind="library_choice", library_choice_effect_id=effect_id)
        return
    if isinstance(operation, PreventDraw):
        if event.kind != "draw" or payload.get("is_draw") is not True:
            raise ReplacementEffectError(
                "Draw prevention requires an unresolved draw event"
            )
        payload["is_draw"] = False
        payload["result_kind"] = "prevented"
        return
    if isinstance(operation, DiscardInsteadOfDraw):
        exclusions = _result_draw_exclusions(event, payload, effect_id)
        payload.update(is_draw=False, result_kind="discard_or_mill", excluded_effect_ids=list(exclusions))
        return
    if isinstance(operation, CreateResultDraws):
        _create_result_draw_instruction(
            event,
            payload,
            children,
            operation,
            effect_id=effect_id,
        )
        return
    if isinstance(operation, DredgeDraw):
        if event.kind != "draw" or payload.get("is_draw") is not True:
            raise ReplacementEffectError(
                "Dredge requires an unresolved draw event"
            )
        library_size = payload.get("library_size")
        if type(library_size) is not int or library_size < operation.mill_count:
            raise ReplacementEffectError(
                "Dredge requires enough cards in the affected library"
            )
        payload.update(
            {
                "is_draw": False,
                "result_kind": _DREDGE_RESULT_KIND,
                "dredge_source_ref": operation.source_ref,
                "dredge_source_object_id": operation.source_object_id,
                "dredge_source_zone_change_counter": (
                    operation.source_zone_change_counter
                ),
                "dredge_mill_count": operation.mill_count,
            }
        )
        return
    raise ReplacementEffectError("Unknown typed draw replacement operation")


def _validate_set_field(event, payload, operation, effect_id):
    _require_field(event, operation.field, _SET_FIELDS, operation="set")
    if operation.field == EXILE_LINK_FIELD:
        if (event.payload.get("destination") != "graveyard" or payload.get("destination") != "exile"
                or PRIOR_CONTROL_FIELD not in event.payload or EXILE_LINK_FIELD in payload):
            raise ReplacementEffectError("An exile link requires its original graveyard-to-exile replacement")
        try:
            checked_exile_marker(operation.value, effect_id)
        except ValueError as exc:
            raise ReplacementEffectError(str(exc)) from exc
    if operation.field == "direction" and (
        operation.value != _LIFE_LOSS_DIRECTION or payload.get("direction") != "gain"
        or type(payload.get("amount")) is not int or payload["amount"] <= 0
    ):
        raise ReplacementEffectError("Life direction replacement can convert only positive gain to equal loss")
    if event.kind == TURN_BOUNDARY_SKIP_EVENT and operation.value is not True:
        raise ReplacementEffectError("A skipped boundary cannot be restored by a skip replacement")
    if operation.field == DAY_ENTRY_FIELD and (
        operation.value is not True or payload.get("destination") != "battlefield"
        or payload.get("origin") == "battlefield" or payload.get(DAY_ENTRY_FIELD) is not None
        or f"{DAY_ENTRY_HANDLER}:{payload.get('object_ref')}:" not in effect_id
    ):
        raise ReplacementEffectError("Initial day requires the original battlefield-entry replacement")


def _apply_operation(
    event: ReplaceableEvent,
    payload: dict[str, Any],
    children: list[ReplaceableEvent],
    entry_scope: EntryReplacementScope | None,
    operation: Any,
    *,
    effect_id: str,
) -> EntryReplacementScope | None:
    if isinstance(operation, SetField):
        _validate_set_field(event, payload, operation, effect_id)
        from .chosen_basic_land_entry import validate_basic_land_entry_field

        validate_basic_land_entry_field(event, payload, operation, effect_id)
        if operation.field=='destination' and 'commander_components' in payload and payload.get('destination')!=operation.value:
            from ..composition_commander_replacement_model import payload_commander_components,EPOCH_FIELD
            try:payload_commander_components(payload)
            except ValueError as exc:raise ReplacementEffectError(str(exc)) from exc
            payload[EPOCH_FIELD]=payload.get(EPOCH_FIELD,0)+1
        if operation.field in {"entry_chosen_faction", "entry_chosen_jeskai_temur_faction"}:
            from ..entry_designations import ENTRY_DESIGNATION_HANDLER_ID, EntryDesignationKind, validate_designation

            try:
                kind = (EntryDesignationKind.FACTION if operation.field == "entry_chosen_faction"
                        else EntryDesignationKind.JESKAI_TEMUR_FACTION)
                faction = validate_designation(kind, operation.value)
                component = f"{ENTRY_DESIGNATION_HANDLER_ID}:{payload['object_ref']}:"
                if (payload.get("destination") != "battlefield" or payload.get("origin") == "battlefield"
                        or payload.get(operation.field) is not None
                        or not effect_id.startswith(component) or not effect_id.endswith(f":{faction}")):
                    raise ValueError("The faction choice requires its original entering object")
            except (KeyError, ValueError) as exc:
                raise ReplacementEffectError(str(exc)) from exc
        if operation.field == "entry_linked_card_type":
            from ..linked_card_type import ENTRY_FIELD, LinkedCardTypeChoice

            try:
                card_type_choice = LinkedCardTypeChoice.from_dict(operation.value)
                if (payload.get("destination") != "battlefield" or payload.get("origin") == "battlefield"
                        or payload.get(ENTRY_FIELD) is not None
                        or card_type_choice.origin_logical_id != payload.get("logical_object_id")
                        or effect_id != card_type_choice.effect_id(payload["object_ref"])):
                    raise ValueError("The card type requires its original entering subject and component")
            except (KeyError, ValueError) as exc:
                raise ReplacementEffectError(str(exc)) from exc
        if operation.field == "entry_chosen_opponent":
            from ..entry_designations import ENTRY_OPPONENT_FIELD, EntryOpponentChoice, entry_opponent_effect_id
            try:
                choice = EntryOpponentChoice.from_dict(operation.value)
                if (payload.get("destination") != "battlefield" or payload.get("origin") == "battlefield"
                        or (not choice.any_player and choice.seat == payload.get("destination_controller"))
                        or (choice.any_player and event.kind != "zone.change")
                        or payload.get(ENTRY_OPPONENT_FIELD) is not None
                        or effect_id != entry_opponent_effect_id(payload["object_ref"], choice)):
                    raise ValueError("The selection requires its original entering subject and permitted player")
            except (KeyError, ValueError) as exc:
                raise ReplacementEffectError(str(exc)) from exc
        payload[operation.field] = thaw_value(operation.value)
        if operation.field == "destination":
            _retarget_zone_counter_children(event, payload, children)
        return entry_scope
    if isinstance(operation, (AddAmount, MultiplyAmount, HalveCounterPlacementAmount)):
        _apply_amount_operation(event, payload, operation)
        return entry_scope
    if isinstance(operation, PreventAmount):
        if event.kind != "damage":
            raise ReplacementEffectError(
                "Damage prevention can apply only to a damage event"
            )
        available = int(payload.get("amount", 0))
        if available < 0:
            raise ReplacementEffectError("Damage amounts cannot be negative")
        if operation.draw_for_combat_players:
            from ..rules.combat_prevention_draw import record_prevention_draw

            try:
                record_prevention_draw(event, payload, available, effect_id)
            except (TypeError, ValueError) as exc:
                raise ReplacementEffectError(str(exc)) from exc
        unpreventable = bool(payload.get("unpreventable"))
        if operation.tracks_unpreventable_amount and unpreventable and available:
            from .operations import UNPREVENTABLE_DAMAGE_ATTEMPTS

            attempts = dict(payload.get(UNPREVENTABLE_DAMAGE_ATTEMPTS) or {})
            attempts[effect_id] = available
            payload[UNPREVENTABLE_DAMAGE_ATTEMPTS] = attempts
        requested = operation.requested_prevention(available)
        prevented = 0 if unpreventable else min(available, requested)
        payload["amount"] = available - prevented
        payload["prevented"] = int(payload.get("prevented", 0)) + prevented
        return entry_scope
    if isinstance(operation, PreventUsingShield):
        raise ReplacementEffectError(
            "Durable shield prevention requires a replacement batch boundary"
        )
    if isinstance(operation, RedirectDamage):
        raise ReplacementEffectError(
            "Damage redirection requires subject-aware replacement application"
        )
    if isinstance(operation, (AppendValues, UnionValues)):
        _apply_sequence_operation(event, payload, operation)
        return entry_scope
    if isinstance(operation, CreateNestedEvent):
        children.append(
            _nested_event_from_mapping(
                event,
                thaw_value(operation.event),
                suffix=str(len(children)),
            )
        )
        return entry_scope
    if isinstance(operation, CreateCountedEntryCounters):
        apply_counted_entry_counters(event, payload, children, entry_scope, operation, effect_id=effect_id)
        return entry_scope
    if isinstance(operation,(ApplyEntryCounterReplacement,CreateAffectedObjectCounter,GrantAffectedObjectSubtype,GrantAffectedObjectKeyword,
            OpenTributeEntryChoice,CompleteTributeEntryChoice)):
        from .entry_counter_application import apply_entry_counter_operation
        apply_entry_counter_operation(event,payload,children,operation,effect_id=effect_id)
        return entry_scope
    if isinstance(operation, CreateAdditionalToken):
        _apply_additional_token(event, payload, operation)
        return entry_scope
    if isinstance(operation, MultiplyTokenCreation):
        _apply_token_multiplier(event, payload, operation)
        return entry_scope
    if isinstance(operation, ReplaceCreatureTokenDefinition):
        _apply_creature_token_definition(event, payload, operation)
        return
    if isinstance(operation, ReplacePredefinedArtifactTokenSet):
        _apply_predefined_artifact_token_set(event, payload, operation)
        return entry_scope
    if isinstance(operation, ReserveZoneChange):
        if event.kind != "zone.change" or entry_scope is None:
            raise ReplacementEffectError(
                "Zone-change reservation requires a scoped zone-change event"
            )
        values: Any = operation.objects
        if operation.from_field is not None:
            values = payload.get(operation.from_field, ())
        if not isinstance(values, Sequence) or isinstance(values, (str, bytes)):
            raise ReplacementEffectError(
                "Zone-change reservation requires object IDs"
            )
        return entry_scope.reserve_zone_changes(str(value) for value in values)
    if isinstance(operation, CapResultLifeLoss):
        _apply_result_life_floor(event, payload, children, operation)
        return entry_scope
    if isinstance(operation, (ReplaceDrawWithScheduledResult, PreventDraw, DiscardInsteadOfDraw,
                              ReplaceDrawWithLibraryChoice, CreateResultDraws,
                              ExileTopForTurnPlayInsteadOfDraw, WinInsteadOfEmptyDraw, DredgeDraw)):
        _apply_draw_operation(event, payload, children, operation, effect_id=effect_id)
        return entry_scope
    raise ReplacementEffectError("Unknown typed replacement operation")


def canonical_replacement_selection(
    choice: ReplacementChoice, selected_effect_id: str | None
) -> str:
    if selected_effect_id is None:
        if len(choice.options) != 1 or choice.options[0] not in choice.optional_options:
            raise ReplacementEffectError(
                "A decline must identify one currently optional replacement"
            )
        return f"decline:{choice.options[0]}"
    if not isinstance(selected_effect_id, str) or not selected_effect_id:
        raise ReplacementEffectError(
            "A replacement selection must be a nonempty string"
        )
    return selected_effect_id


def _apply_zone_outcome_operation(event, payload, children, operations, operation, *, effect_id, objects):
    if isinstance(operation, RevealForOwnerLibraryShuffle):
        if (operations != (SetField("destination", "library"), operation)
                or event.kind != "zone.change" or event.affected_object is None
                or event.affected_object.object_id != operation.object_id
                or event.affected_object.owner != operation.owner
                or event.payload.get("destination") != "graveyard"
                or event.payload.get("logical_object_id") != operation.logical_object_id
                or event.payload.get("object_ref") != operation.object_ref
                or SELF_GRAVEYARD_SHUFFLE_RESULT in payload):
            raise ReplacementEffectError("Self-shuffle must replace the original object's graveyard arrival")
        payload[SELF_GRAVEYARD_SHUFFLE_RESULT] = operation.to_dict()
        return True
    if isinstance(operation, ENTRY_COUNTER_TRANSFER_OPERATIONS):
        if len(operations) != 1:
            raise ReplacementEffectError("Counter transfer requires one closed entry instruction")
        if isinstance(operation, ChosenTransferEntryCounters):
            try:
                operation = operation.select(event, objects)
            except ValueError as exc:
                raise ReplacementEffectError(str(exc)) from exc
        apply_transferred_entry_counters(event, payload, children, operation, effect_id=effect_id)
        return True
    return False


def _apply_effect_operations(
    event: ReplaceableEvent,
    effect: ReplacementEffect,
    operations: Sequence[Any],
    *,objects: tuple[str,...] | None = None,
) -> ReplaceableEvent:
    from .composition_scope import COMPONENTS_FIELD,RESULT_FIELD,card_component_event,card_only_replacement
    if COMPONENTS_FIELD in event.payload or RESULT_FIELD in event.payload:
        from dataclasses import replace
        try:group=card_component_event(event)
        except (ValueError,TypeError,KeyError) as exc:raise ReplacementEffectError(str(exc)) from exc
        if group is None:raise ReplacementEffectError('Card-component replacement scope is missing')
        changed_group=_apply_effect_operations(group,effect,operations,objects=objects)
        if card_only_replacement(effect):
            payload=thaw_value(event.payload)
            payload[RESULT_FIELD]=changed_group.to_dict()
            chooser_history=dict(payload.get('replacement_choosers') or {})
            chooser_history[effect.effect_id]=event.chooser
            payload['replacement_choosers']=chooser_history
            return event.with_payload(payload,applied_effect=effect.effect_id)
        payload=thaw_value(event.payload)
        payload.pop(COMPONENTS_FIELD);payload.pop(RESULT_FIELD,None)
        changed=_apply_effect_operations(replace(event,payload=payload),effect,operations,objects=objects)
        payload=thaw_value(changed.payload)
        payload[COMPONENTS_FIELD]=thaw_value(event.payload[COMPONENTS_FIELD])
        payload[RESULT_FIELD]=changed_group.to_dict()
        return replace(changed,payload=payload)
    payload = thaw_value(event.payload)
    children = list(event.children)
    entry_scope = event.entry_scope
    affected_player = event.affected_player
    affected_object = event.affected_object
    for operation in operations:
        if _apply_zone_outcome_operation(event, payload, children, operations, operation,
                                         effect_id=effect.effect_id, objects=objects):
            if isinstance(operation, RevealForOwnerLibraryShuffle) and operation.is_commander:
                affected_object = AffectedObject(operation.object_id, operation.owner, operation.owner)
            continue
        if isinstance(operation, AmplifyEntryOperation):
            from .amplify import apply_amplify_entry_operation
            if len(operations) != 1:
                raise ReplacementEffectError("Amplify requires one closed entry instruction")
            apply_amplify_entry_operation(event, payload, children, operation,
                effect_id=effect.effect_id, objects=objects)
            continue
        if isinstance(operation,GraveyardCopyEntryOperation):
            from ..rules.graveyard_entry_copy import apply_graveyard_copy_pair_selection

            try:
                entry_scope=apply_graveyard_copy_pair_selection(event,payload,entry_scope,operation,objects)
            except ValueError as exc:
                raise ReplacementEffectError(str(exc)) from exc
            continue
        if isinstance(operation, LinkedGraveyardEntryOperation):
            from ..rules.linked_graveyard_entry import apply_linked_graveyard_entry_selection

            try:
                entry_scope = apply_linked_graveyard_entry_selection(event, payload, entry_scope, operation, objects)
            except ValueError as exc:
                raise ReplacementEffectError(str(exc)) from exc
            continue
        if isinstance(operation,DevourEntryOperation):
            from .devour import apply_devour_entry_operation
            entry_scope=apply_devour_entry_operation(event,payload,children,entry_scope,operation,effect_id=effect.effect_id,objects=objects)
            continue
        if isinstance(operation, RedirectDamage):
            if event.kind != "damage":
                raise ReplacementEffectError(
                    "Damage redirection can apply only to a damage event"
                )
            if operation.maximum_damage is not None:
                from .damage_redirection import same_damage_recipient

                if payload.get("target_kind") != "permanent" or (
                    payload["amount"] > operation.maximum_damage and not same_damage_recipient(event, operation)
                ):
                    raise ReplacementEffectError("Finite redirection requires its bounded damage branch")
            payload.update(
                {
                    "target": operation.target,
                    "target_kind": operation.target_kind,
                    "target_object_id": operation.target_object_id,
                    "target_logical_object_id": (
                        operation.target_logical_object_id
                    ),
                    "target_controller": operation.target_controller,
                    "target_owner": operation.target_owner,
                    "target_types": list(operation.target_types),
                    "target_subtypes": list(operation.target_subtypes),
                    "target_combat_partners":list(operation.target_combat_partners),
                    "target_colors":list(operation.target_colors),
                    "source_and_target_block_pair":operation.target_logical_object_id in payload.get('source_combat_partners',()),
                    "source_and_target_share_color":bool(set(payload.get('source_colors',()))&set(operation.target_colors)),
                    "source_and_target_distinct":payload.get('source_logical_object_id')!=operation.target_logical_object_id,
                    "target_characteristics": sorted(
                        {*operation.target_types, *operation.target_subtypes,*operation.target_supertypes,*operation.target_scope_facts}
                    ),
                }
            )
            if operation.target_kind == "player":
                affected_player = operation.target
                affected_object = None
            else:
                assert operation.target_object_id is not None
                assert operation.target_owner is not None
                affected_player = None
                affected_object = AffectedObject(
                    object_id=operation.target_object_id,
                    owner=operation.target_owner,
                    controller=operation.target_controller,
                )
            continue
        prevented_before = int(payload.get("prevented", 0))
        entry_scope = _apply_operation(
            event,
            payload,
            children,
            entry_scope,
            operation,
            effect_id=effect.effect_id,
        )
        if isinstance(operation, PreventAmount):
            prevented = int(payload.get("prevented", 0)) - prevented_before
            by_effect = dict(payload.get("prevention_applied") or {})
            by_effect[effect.effect_id] = (
                int(by_effect.get(effect.effect_id, 0)) + prevented
            )
            payload["prevention_applied"] = by_effect
    from .devour import combine_devour_entry_counters
    combine_devour_entry_counters(event,payload,children)
    if COUNTER_REPLACEMENT_GENERATED_FIELD in payload:
        _record_replacement_generated_counters(payload, operations)
    if event.kind == 'zone.change' and '_counter_permissions' in payload:
        from dataclasses import replace
        from ..counter_permission_events import refresh_entry_counter_permissions
        from ..counter_permission import CounterPermissionError
        try:
            refresh_entry_counter_permissions(replace(event,entry_scope=entry_scope),payload,children)
        except CounterPermissionError as exc:
            raise ReplacementEffectError(str(exc)) from exc
    chooser_history = dict(payload.get("replacement_choosers") or {})
    chooser_history[effect.effect_id] = event.chooser
    payload["replacement_choosers"] = chooser_history
    return ReplaceableEvent(
        event_id=event.event_id,
        kind=event.kind,
        affected_player=affected_player,
        affected_object=affected_object,
        payload=payload,
        applied_effects=(*event.applied_effects, effect.effect_id),
        children=tuple(children),
        entry_scope=entry_scope,
    )


def _record_replacement_generated_counters(payload: dict[str, Any], operations: Sequence[Any]) -> None:
    amount = payload.get("amount")
    if type(amount) is int and amount > 0 and any(
        isinstance(operation, HalveCounterPlacementAmount)
        or isinstance(operation, (AddAmount, MultiplyAmount, SetField)) and operation.field == "amount"
        for operation in operations
    ):
        payload[COUNTER_REPLACEMENT_GENERATED_FIELD] = True


def apply_replacement(
    choice: ReplacementChoice,
    effects: Iterable[ReplacementEffect],
    selected_effect_id: str | None,
    *, objects: tuple[str,...] | None = None, card_name: str | None = None,
) -> ReplaceableEvent:
    from ..entry_designations import ENTRY_CARD_NAME_FIELD, entry_card_name_operation, validate_bound_entry_card_name

    all_effects = canonical_effects(effects)
    by_id = {effect.effect_id: effect for effect in all_effects}
    selection = canonical_replacement_selection(choice, selected_effect_id)
    if selection.startswith("decline:"):
        if objects or card_name is not None:
            raise ReplacementEffectError("Declining a replacement cannot choose objects or a card name")
        declined = selection.removeprefix("decline:")
        if declined not in choice.options or declined not in choice.optional_options:
            raise ReplacementEffectError(
                "Selected replacement cannot currently be declined"
            )
        effect = by_id[declined]
        if effect.decline_operations:
            return _apply_effect_operations(
                choice.event,
                effect,
                effect.decline_operations,
            )
        return choice.event.with_payload(
            choice.event.payload, applied_effect=declined
        )
    if selection not in choice.options:
        raise ReplacementEffectError(
            "Selected replacement is not currently applicable"
        )
    effect = by_id[selection]
    if entry_card_name_operation(effect) is not None:
        if objects is not None or choice.event.payload.get("destination") != "battlefield":
            raise ReplacementEffectError("A card-name choice requires the original battlefield entry")
        try:
            name = validate_bound_entry_card_name(card_name)
        except ValueError as exc:
            raise ReplacementEffectError(str(exc)) from exc
        return _apply_effect_operations(choice.event, effect, (SetField(ENTRY_CARD_NAME_FIELD, name),))
    if card_name is not None:
        raise ReplacementEffectError("This replacement does not accept a card name")
    if objects is not None and not any(isinstance(op,ENTRY_OBJECT_SELECTION_OPERATIONS) for op in effect.operations):
        if objects:raise ReplacementEffectError('This replacement does not select objects')
        objects=None
    return _apply_effect_operations(choice.event, effect, effect.operations,objects=objects)


def resolve_replacements(
    event: ReplaceableEvent,
    effects: Iterable[ReplacementEffect],
    *,
    selections: Iterable[str | None],
) -> ReplaceableEvent:
    all_effects = canonical_effects(effects)
    supplied = iter(selections)
    current = event
    while choice := replacement_choice(current, all_effects):
        try:
            selected = next(supplied)
        except StopIteration as exc:
            raise ReplacementEffectError(
                "Replacement choice sequence ended early"
            ) from exc
        current = apply_replacement(choice, all_effects, selected)
    try:
        next(supplied)
    except StopIteration:
        return current
    raise ReplacementEffectError(
        "Replacement choice sequence contains unused choices"
    )

_DREDGE_RESULT_KIND = "dredge"
