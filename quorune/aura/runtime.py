from __future__ import annotations

from typing import Any, Iterable, Mapping, Protocol

from ..attachments import (
    attach_objects,
    attach_to_player,
    attachment_target_ref,
)
from ..model import CardInstance
from ..protection import (
    ProtectionSource,
    ProtectionVerdict,
    protection_verdict,
)
from ..targets import TargetGroup
from ..target_protection_engine_adapter import (
    player_protection_allows_attachment,
)
from ..util import unique_preserving_order
from .grammar import is_aura_type_line
from .model import (
    AuraEntryChoiceRequired,
    AuraEntryOutcome,
    AuraEntryPlan,
    AuraRuleError,
    EnchantSpec,
    LinkedGraveyardCreatureEnchantSpec,
    SimpleEnchantSpec,
    TypedEnchantSpec,
    AuraZoneMovePreflight,
    enchant_spec_from_dict,
)


class AuraRuntimeHost(Protocol):
    state: Any

    def _effective_card_data(self, card: Any) -> Mapping[str, Any]: ...

    def _target_candidate_rows(
        self, controller: str, group: TargetGroup
    ) -> list[dict[str, Any]]: ...

    def _target_row_matches(
        self,
        controller: str,
        group: TargetGroup,
        row: Mapping[str, Any],
        *,
        source_ref: str | None,
        as_target: bool = True,
    ) -> bool: ...

    def _compiled_enchant_spec(
        self,
        card: CardInstance,
        *,
        face_name: str | None = None,
    ) -> EnchantSpec | None: ...

    def _next_zone_timestamp(self) -> int: ...

    def _require_seat(self, seat: str, *, in_game: bool = False) -> Any: ...

    def card_record(self, card: Any) -> Any: ...

    def _log(
        self,
        actor: str | None,
        code: str,
        message: str,
        details: Mapping[str, Any] | None = None,
        *,
        visibility: Iterable[str] | None = None,
        importance: int = 1,
        changed_objects: Iterable[str] | None = None,
        changed_players: Iterable[str] | None = None,
    ) -> Any: ...


def _protection_allows_attachment(
    host: AuraRuntimeHost,
    aura: CardInstance,
    target: CardInstance,
) -> bool:
    aura_data = host._effective_card_data(aura)
    verdict = protection_verdict(
        host._effective_card_data(target),
        ProtectionSource.from_characteristics(aura_data),
    )
    return verdict is ProtectionVerdict.ALLOWED


def legal_aura_target_refs(
    host: AuraRuntimeHost,
    aura: CardInstance,
    spec: EnchantSpec,
    *,
    controller: str,
    as_target: bool,
) -> tuple[str, ...]:
    group = TargetGroup.from_mapping(spec.target_schema(aura))
    linked_target_id = spec.linked_target_object_id(aura)
    refs: list[str] = []
    for row in host._target_candidate_rows(controller, group):
        target = row.get("card")
        if isinstance(target, CardInstance):
            if target.object_id == aura.object_id or target.phased_out:
                continue
            if (
                linked_target_id is not None
                and target.object_id != linked_target_id
            ):
                continue
        elif row.get("category") != "player":
            continue
        elif not player_protection_allows_attachment(
            host,
            str(row["ref"]),
        ):
            continue
        if not host._target_row_matches(
            controller,
            group,
            row,
            source_ref=aura.ref,
            as_target=as_target,
        ):
            continue
        if isinstance(target, CardInstance) and not _protection_allows_attachment(
            host, aura, target
        ):
            continue
        refs.append(str(row["ref"]))
    return tuple(unique_preserving_order(refs))


def simple_aura_attachment_is_legal(
    host: AuraRuntimeHost,
    aura: CardInstance,
) -> bool | None:
    if aura.attached_to is None:
        return False
    spec = host._compiled_enchant_spec(aura)
    if spec is None:
        return None
    target_ref = attachment_target_ref(
        host.state.cards,
        host.state.players,
        aura,
    )
    if target_ref is None:
        return False
    return target_ref in legal_aura_target_refs(
        host,
        aura,
        spec,
        controller=aura.controller,
        as_target=False,
    )


def prepare_aura_entry(
    host: AuraRuntimeHost,
    aura: CardInstance,
    *,
    spec: EnchantSpec,
    controller: str,
    target_ref: str | None,
    resolving_as_spell: bool,
) -> AuraEntryPlan:
    if not isinstance(
        spec,
        (
            SimpleEnchantSpec,
            TypedEnchantSpec,
            LinkedGraveyardCreatureEnchantSpec,
        ),
    ):
        raise AuraRuleError(
            "Aura entry requires one trusted compiled Enchant descriptor"
        )
    legal = legal_aura_target_refs(
        host,
        aura,
        spec,
        controller=controller,
        as_target=resolving_as_spell,
    )
    identity = {
        "source_object_id": aura.object_id,
        "source_logical_object_id": aura.logical_object_id,
        "source_zone": aura.zone,
        "controller": controller,
        "spec": spec,
        "legal_target_refs": legal,
    }
    if target_ref is not None and target_ref in legal:
        return AuraEntryPlan(
            **identity,
            outcome=AuraEntryOutcome.ENTER_ATTACHED,
            target_ref=target_ref,
        )
    if aura.zone == "stack" and resolving_as_spell:
        return AuraEntryPlan(
            **identity,
            outcome=AuraEntryOutcome.MOVE_TO_GRAVEYARD,
        )
    pending = AuraEntryPlan(
        **identity,
        outcome=AuraEntryOutcome.REMAIN_IN_ZONE,
    )
    if target_ref is not None or not legal:
        return pending
    raise AuraEntryChoiceRequired(pending)


def preflight_aura_zone_move(
    host: AuraRuntimeHost,
    aura: CardInstance,
    *,
    destination: str,
    requested_destination: str,
    destination_type_line: str,
    enter_face: str | None,
    enchant_spec: EnchantSpec | None,
    controller: str | None,
    target_ref: str | None,
    resolving_as_spell: bool,
    origin: str,
    log: bool,
    error_type: type[Exception],
) -> AuraZoneMovePreflight:
    if (
        destination != "battlefield"
        or not is_aura_type_line(destination_type_line)
        or bool(aura.annotations.get("pending_aura_target"))
    ):
        return AuraZoneMovePreflight(destination)
    spec = enchant_spec or host._compiled_enchant_spec(
        aura,
        face_name=enter_face,
    )
    if spec is None:
        raise error_type(
            "This Aura lacks one trusted compiled Enchant descriptor"
        )
    entry_controller = controller or aura.owner
    host._require_seat(entry_controller)
    try:
        plan = prepare_aura_entry(
            host,
            aura,
            spec=spec,
            controller=entry_controller,
            target_ref=target_ref,
            resolving_as_spell=resolving_as_spell,
        )
    except AuraEntryChoiceRequired:
        raise
    except AuraRuleError as exc:
        raise error_type(str(exc)) from exc
    if plan.outcome is AuraEntryOutcome.REMAIN_IN_ZONE:
        if log:
            host._log(
                None,
                "zone.move.prevented",
                (
                    f"{aura.ref} remained in {origin}; it had no "
                    "selected legal object to enchant."
                ),
                {
                    "object": aura.ref,
                    "from": origin,
                    "requested_destination": requested_destination,
                    "rule": "303.4f-g",
                },
                importance=2,
                changed_objects=[aura.object_id],
                changed_players=[aura.owner],
            )
        return AuraZoneMovePreflight(
            destination,
            entry_plan=plan,
            remain_in_origin=True,
        )
    return AuraZoneMovePreflight(
        (
            "graveyard"
            if plan.outcome is AuraEntryOutcome.MOVE_TO_GRAVEYARD
            else destination
        ),
        entry_plan=plan,
    )


def commit_aura_zone_move(
    host: AuraRuntimeHost,
    aura: CardInstance,
    plan: AuraEntryPlan | None,
    *,
    error_type: type[Exception],
) -> None:
    if plan is None or plan.outcome is not AuraEntryOutcome.ENTER_ATTACHED:
        return
    try:
        commit_aura_entry_attachment(host, aura, plan)
    except AuraRuleError as exc:
        raise error_type(str(exc)) from exc


def aura_resolution_move_kwargs(item: Any) -> dict[str, Any]:
    is_aura = bool(item.context.get("aura_spell"))
    raw_spec = item.context.get("aura_enchant_spec")
    spec = None
    if is_aura:
        if not isinstance(raw_spec, Mapping):
            raise AuraRuleError(
                "A resolving Aura lacks its compiled Enchant descriptor"
            )
        spec = enchant_spec_from_dict(raw_spec)
    return {
        "aura_target_ref": (
            str(item.targets[0])
            if is_aura and item.targets and item.targets[0] is not None
            else None
        ),
        "resolving_as_aura_spell": is_aura,
        "aura_enchant_spec": spec,
    }


def commit_aura_entry_attachment(
    host: AuraRuntimeHost,
    aura: CardInstance,
    plan: AuraEntryPlan,
) -> None:
    if plan.outcome is not AuraEntryOutcome.ENTER_ATTACHED:
        raise AuraRuleError("Only an entering Aura plan can be committed")
    if (
        aura.object_id != plan.source_object_id
        or aura.zone != "battlefield"
        or aura.controller != plan.controller
        or (
            plan.source_zone == "stack"
            and aura.logical_object_id
            != plan.source_logical_object_id
        )
    ):
        raise AuraRuleError("Aura entry identity changed before attachment")
    target = next(
        (
            card
            for card in host.state.cards.values()
            if card.ref == plan.target_ref
        ),
        None,
    )
    if target is not None and target.ref in plan.legal_target_refs:
        attach_objects(
            host.state.cards,
            aura,
            target,
            source_timestamp=host._next_zone_timestamp(),
            players=host.state.players,
        )
        return
    if (
        plan.target_ref in host.state.players
        and plan.target_ref in plan.legal_target_refs
    ):
        attach_to_player(
            host.state.cards,
            host.state.players,
            aura,
            plan.target_ref,
            source_timestamp=host._next_zone_timestamp(),
        )
        return
    raise AuraRuleError("Aura entry target left before attachment")


__all__ = [
    "AuraRuntimeHost",
    "commit_aura_entry_attachment",
    "commit_aura_zone_move",
    "aura_resolution_move_kwargs",
    "legal_aura_target_refs",
    "prepare_aura_entry",
    "preflight_aura_zone_move",
    "simple_aura_attachment_is_legal",
]
