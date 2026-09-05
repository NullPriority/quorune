from __future__ import annotations

import copy
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from ...additional_cost_vocabulary import ZONE_CHANGE_COST_KIND
from ...cascade import cascade_trigger_items
from ...casting_payment_keywords import CastingPaymentKeywordError
from ...compiled_cast_costs import compiled_evoke_specs
from ...storm import prior_storm_spell_count, storm_trigger_items
from ...convoke import ConvokeError
from ...counter_placement import (
    CounterPlacementError,
    CounterPlacementRequest,
    commit_prepared_counter_placements,
    prepare_counter_placements,
)
from ...compiled_morph import compiled_fixed_mana_face_down_method_spec
from ...compiled_flashback import compiled_fixed_mana_flashback_spec
from ...compiled_madness import current_fixed_cast_lifecycle_spec
from ...cast_lifecycles import (
    FixedCastLifecycleError,
    FixedCastLifecycleKind,
    FixedCastLifecycleSpec,
    FIXED_CAST_LIFECYCLE_CONTEXT_FIELD,
    fixed_cast_lifecycle_stack_fields,
)
from ...compiled_kicker import compiled_fixed_mana_kicker_spec
from ...flashback import FLASHBACK_CAST_OPTION_ID
from ...evoke import EVOKE_PAYMENT_FIELD, validate_evoke_payment_marker
from ...kicker import KICKER_ANNOTATION, KICKER_MECHANIC_ID
from ...life_state import LifeStateError, pay_life_cost
from ...model import StackItem, YieldPolicy
from ...morph import (
    FACE_DOWN_CAST_METHODS,
    FixedManaMorphSpec,
    MORPH_FACE_DOWN_LABEL,
    MORPH_CAST_METHOD,
    MorphError,
)
from ..spell_cast_events import SpellCastEvent
from ...selection.exile_cast import mana_value_of_cost
from ...zone_object_state import (
    mark_card_face_down_for_morph,
    mark_card_flashed_back,
    mark_card_kicked,
)
from ...stack_counter import oracle_has_intrinsic_counter_prohibition
from ...tap_state import set_permanent_tapped
from ...trigger_processing import collect_ward_occurrences, enqueue_trigger_batch
from ...zone_trigger_events import ZoneTransitionKind
from ..action_proposals import CastProposal, thaw_json
from ..casting_additional_costs import (
    AdditionalCostError,
    fixed_counter_additional_cost,
    fixed_counter_cost_candidates,
    FixedZoneChangeAdditionalCost,
    fixed_zone_change_additional_cost,
    fixed_zone_change_cost_candidates,
)
from ..casting_additional_cost_groups import (
    fixed_life_payment_additional_cost,
)
from .model import CastProposalError
from .costs import revalidate_convoke_payment, revalidate_delve_payment


class CastCommitHost(Protocol):
    state: Any
    semantics: Any
    seats: list[str]

    def _resolve_object(
        self,
        actor: str,
        ref: str,
        *,
        zones: set[str],
        controlled_only: bool = False,
        owned_only: bool = False,
    ) -> Any: ...

    def card_record(self, card: Any) -> Any: ...

    def _pay_for_cost(
        self,
        seat: str,
        requirements: Mapping[str, int],
        response: Mapping[str, Any],
        *,
        exclude_sources: set[str] | None = None,
        spend_context: Any = None,
    ) -> tuple[dict[str, int], list[dict[str, Any]]]: ...

    def _spell_mana_spend_context(self, type_line: str) -> Any: ...

    def _log(self, actor: str | None, code: str, summary: str, details: Any = None, **kwargs: Any) -> None: ...

    def move_card(self, object_id: str, zone: str, **kwargs: Any) -> Any: ...

    def _semantic_event_sources(self) -> list[Any]: ...

    def _effective_card_data(self, card: Any) -> Mapping[str, Any]: ...

    def _remove_from_zone(self, card: Any) -> None: ...

    def _reset_zone_change(self, card: Any, zone: str) -> None: ...

    def _next_ref(self, prefix: str) -> str: ...

    def _stable_runtime_id(self, kind: str, ref: str) -> str: ...

    def _current_turn_history(self, kind: str) -> Sequence[Any]: ...

    def _stack_target_schema(self, item: Any, program: Any) -> Any: ...

    def _dispatch_zone_change_events(self, *args: Any, **kwargs: Any) -> None: ...

    def _type_parts(self, type_line: str) -> tuple[set[str], set[str], set[str]]: ...

    def _record_turn_history(self, kind: str, **kwargs: Any) -> None: ...

    def _dispatch_semantic_event(self, event: str, context: Mapping[str, Any], **kwargs: Any) -> None: ...

    def _stabilize(self) -> bool: ...

    def semantic_program_is_current_trusted(self, program: Any) -> bool: ...


@dataclass(slots=True)
class _AdditionalCostCommit:
    deferred_events: list[
        tuple[
            Any,
            str,
            str,
            str,
            dict[str, Any],
            list[str],
            str,
            ZoneTransitionKind,
        ]
    ]
    source_snapshots: list[Any]
    source_zones: dict[str, str]
    source_characteristics: dict[str, dict[str, Any]]
    paid_refs: list[str]


def _revalidate_source(host: CastCommitHost, proposal: CastProposal) -> tuple[Any, Any, Any]:
    card = host._resolve_object(
        proposal.seat,
        proposal.card_ref,
        zones={proposal.origin},
        owned_only=False,
    )
    if card.object_id != proposal.object_id or card.zone != proposal.origin:
        raise CastProposalError(
            "The casting source changed after proposal construction",
            reason="stale_source",
        )
    record = host.card_record(card)
    if record is None:
        raise CastProposalError("The casting card record is unavailable")
    program = host.semantics.get(proposal.semantic_key)
    return card, record, program


def _commit_tap_costs(
    host: CastCommitHost, proposal: CastProposal, card: Any
) -> list[Any]:
    result = []
    for ref in proposal.tap_cost_refs:
        tapped = host._resolve_object(
            proposal.seat,
            ref,
            zones={"battlefield"},
            controlled_only=True,
        )
        if tapped.tapped:
            raise CastProposalError(
                f"{tapped.ref} is no longer available for a tap cost",
                status="unpayable",
                reason="tap_cost_unpayable",
            )
        set_permanent_tapped(
            host,
            tapped.ref,
            actor=proposal.seat,
            tapped=True,
            reason=f"{card.printed_name} casting cost",
            log=False,
        )
        host._log(
            proposal.seat,
            "cost.tap",
            f"{proposal.seat} tapped {tapped.ref} to help cast {card.printed_name}.",
            {
                "spell": card.ref,
                "object": tapped.ref,
                "cost_option": proposal.cost_option_id,
            },
            importance=1,
            changed_objects=[tapped.object_id],
            changed_players=[proposal.seat],
        )
        result.append(tapped)
    return result


def _pay_life_additional_cost(
    host: CastCommitHost,
    proposal: CastProposal,
    response: Mapping[str, Any],
    card: Any,
    selected_option: Mapping[str, Any],
) -> None:
    amount = int(response.get("x", 0))
    try:
        pay_life_cost(host, proposal.seat, amount)
    except LifeStateError as exc:
        raise CastProposalError(
            "The selected life payment is no longer payable",
            status="unpayable",
            reason="life_cost_unpayable",
        ) from exc
    host._log(
        proposal.seat,
        "cost.life",
        f"{proposal.seat} paid {amount} life to cast {card.printed_name}.",
        {
            "object": card.ref,
            "amount": amount,
            "cost_option": selected_option["id"],
        },
        importance=1,
        changed_players=[proposal.seat],
    )


def _pay_fixed_life_additional_cost(
    host: CastCommitHost,
    proposal: CastProposal,
    card: Any,
    selected_option: Mapping[str, Any],
    amount: int,
) -> None:
    try:
        pay_life_cost(host, proposal.seat, amount)
    except LifeStateError as exc:
        raise CastProposalError(
            "The fixed life payment is no longer payable",
            status="unpayable",
            reason="life_cost_unpayable",
        ) from exc
    host._log(
        proposal.seat,
        "cost.life",
        f"{proposal.seat} paid {amount} life to cast {card.printed_name}.",
        {
            "object": card.ref,
            "amount": amount,
            "cost_option": selected_option["id"],
        },
        importance=1,
        changed_players=[proposal.seat],
    )


def _commit_counter_placement_additional_cost(
    host: CastCommitHost,
    proposal: CastProposal,
    response: Mapping[str, Any],
    card: Any,
    selected_option: Mapping[str, Any],
    selected: Mapping[str, Any],
) -> str:
    raw_costs = selected_option.get("additional_costs")
    cost_position = selected.get("cost_position")
    if (
        not isinstance(raw_costs, list)
        or type(cost_position) is not int
        or cost_position < 0
        or cost_position >= len(raw_costs)
        or set(selected) != {"kind", "card", "cost_position"}
    ):
        raise CastProposalError(
            "The counter additional-cost selection is malformed",
            reason="additional_cost_malformed",
        )
    try:
        cost = fixed_counter_additional_cost(raw_costs[cost_position])
    except AdditionalCostError as exc:
        raise CastProposalError(
            str(exc), reason="additional_cost_malformed"
        ) from exc
    if cost is None or len(raw_costs) != 1:
        raise CastProposalError(
            "The counter additional cost is outside the represented family",
            reason="additional_cost_unsupported",
        )
    selected_ref = selected.get("card")
    if (
        type(selected_ref) is not str
        or selected_ref
        not in fixed_counter_cost_candidates(
            host,
            actor=proposal.seat,
            cost=cost,
        )
    ):
        raise CastProposalError(
            "The selected counter-cost creature is no longer legal",
            status="unpayable",
            reason="counter_cost_unpayable",
        )
    paid = host._resolve_object(
        proposal.seat,
        selected_ref,
        zones={"battlefield"},
        controlled_only=True,
    )
    payment_id = str(
        response.get("_mana_payment_id") or proposal.fingerprint
    )
    event_id = (
        f"counter.cost:{payment_id}:{proposal.card_ref}:additional:{cost_position}"
    )
    raw_journal = response.get("_mana_replacement_selections") or {}
    if (
        not isinstance(raw_journal, Mapping)
        or set(raw_journal) - {event_id}
    ):
        raise CastProposalError(
            "The casting replacement journal is malformed",
            reason="replacement_journal_malformed",
        )
    selections = raw_journal.get(event_id) or ()
    if not isinstance(selections, (list, tuple)):
        raise CastProposalError(
            "The casting replacement selections are malformed",
            reason="replacement_journal_malformed",
        )
    try:
        prepared = prepare_counter_placements(
            host,
            (
                CounterPlacementRequest(
                    subject_kind="permanent",
                    subject_id=paid.object_id,
                    counter_name=cost.counter_name,
                    amount=cost.amount,
                    placing_player=proposal.seat,
                    source_ref=card.ref,
                    effect_generated=False,
                ),
            ),
            selections=tuple(selections),
            event_ids=(event_id,),
        )
        results = commit_prepared_counter_placements(
            host,
            prepared,
            reason=f"{card.printed_name} additional casting cost",
            log=False,
        )
    except CounterPlacementError as exc:
        raise CastProposalError(
            str(exc),
            status="unpayable",
            reason="counter_cost_placement",
        ) from exc
    if len(results) != 1:
        raise CastProposalError(
            "The counter additional cost did not produce one result",
            reason="counter_cost_placement",
        )
    result = results[0]
    host._log(
        proposal.seat,
        "cost.counter_placement",
        f"{proposal.seat} put {result.placed} {result.counter_name} "
        f"counter(s) on {paid.ref} to cast {card.printed_name}.",
        {
            "spell": card.ref,
            "object": paid.ref,
            "counter": result.counter_name,
            "requested": result.requested,
            "placed": result.placed,
            "replacement_event": event_id,
        },
        importance=1,
        changed_objects=[paid.object_id],
        changed_players=[proposal.seat],
    )
    return paid.ref


def _resolve_fixed_zone_change_additional_cost(
    host: CastCommitHost,
    proposal: CastProposal,
    response: Mapping[str, Any],
    selected_option: Mapping[str, Any],
    selected: Mapping[str, Any],
) -> tuple[
    Any,
    FixedZoneChangeAdditionalCost,
    tuple[str | None | Mapping[str, Any], ...],
]:
    raw_costs = selected_option.get("additional_costs")
    cost_position = selected.get("cost_position")
    if (
        not isinstance(raw_costs, list)
        or type(cost_position) is not int
        or cost_position < 0
        or cost_position >= len(raw_costs)
        or set(selected) != {
            "kind",
            "operation",
            "card",
            "cost_position",
        }
        or selected.get("kind") != ZONE_CHANGE_COST_KIND
    ):
        raise CastProposalError(
            "The zone-change additional-cost selection is malformed",
            reason="additional_cost_malformed",
        )
    try:
        cost = fixed_zone_change_additional_cost(raw_costs[cost_position])
    except AdditionalCostError as exc:
        raise CastProposalError(
            str(exc), reason="additional_cost_malformed"
        ) from exc
    if cost is None or len(raw_costs) != 1:
        raise CastProposalError(
            "The zone-change additional cost is outside the represented family",
            reason="additional_cost_unsupported",
        )
    if selected.get("operation") != cost.operation:
        raise CastProposalError(
            "The zone-change additional-cost operation is malformed",
            reason="additional_cost_malformed",
        )
    selected_ref = selected.get("card")
    if (
        type(selected_ref) is not str
        or selected_ref
        not in fixed_zone_change_cost_candidates(
            host,
            actor=proposal.seat,
            cost=cost,
            exclude_object_id=proposal.object_id,
        )
    ):
        raise CastProposalError(
            "The selected zone-change cost object is no longer legal",
            status="unpayable",
            reason="zone_change_cost_unpayable",
        )
    paid = host._resolve_object(
        proposal.seat,
        selected_ref,
        zones={cost.origin_zone},
        controlled_only=cost.origin_zone == "battlefield",
        owned_only=cost.origin_zone != "battlefield",
    )
    raw_journal = response.get("_mana_replacement_selections") or {}
    if not isinstance(raw_journal, Mapping) or len(raw_journal) > 1:
        raise CastProposalError(
            "The casting replacement journal is malformed",
            reason="replacement_journal_malformed",
        )
    if raw_journal:
        event_id, selections = next(iter(raw_journal.items()))
        if (
            type(event_id) is not str
            or not event_id.startswith("zone.change:")
            or not event_id.endswith(f":{paid.ref}")
        ):
            raise CastProposalError(
                "The casting replacement journal is malformed",
                reason="replacement_journal_malformed",
            )
    else:
        selections = ()
    if not isinstance(selections, (list, tuple)):
        raise CastProposalError(
            "The casting replacement selections are malformed",
            reason="replacement_journal_malformed",
        )
    return paid, cost, tuple(selections)


def _fixed_zone_change_commit_entry(
    host: CastCommitHost,
    proposal: CastProposal,
    response: Mapping[str, Any],
    selected_option: Mapping[str, Any],
    selected: Mapping[str, Any],
) -> tuple[Any, str, str, str, dict[str, Any], list[str], str, str, tuple]:
    paid, cost, replacement_selections = (
        _resolve_fixed_zone_change_additional_cost(
            host,
            proposal,
            response,
            selected_option,
            selected,
        )
    )
    return (
        paid,
        paid.zone,
        paid.controller,
        paid.logical_object_id,
        copy.deepcopy(host._effective_card_data(paid)),
        [
            host.state.cards[attachment_id].ref
            for attachment_id in paid.attachments
            if attachment_id in host.state.cards
        ],
        cost.log_kind,
        cost.destination_zone,
        replacement_selections,
    )


def _ordinary_card_cost_commit_entries(
    host: CastCommitHost,
    proposal: CastProposal,
    selected: Mapping[str, Any],
) -> list[
    tuple[Any, str, str, str, dict[str, Any], list[str], str, str, tuple]
]:
    """Snapshot ordinary discard, sacrifice, or Delve card movements."""

    kind = str(selected["kind"])
    zone = (
        "hand"
        if kind == "discard"
        else "graveyard"
        if kind == "delve"
        else "battlefield"
    )
    destination = "exile" if kind == "delve" else "graveyard"
    entries = []
    for ref in selected.get("cards", []):
        paid = host._resolve_object(
            proposal.seat,
            str(ref),
            zones={zone},
            controlled_only=zone == "battlefield",
            owned_only=zone != "battlefield",
        )
        entries.append(
            (
                paid,
                paid.zone,
                paid.controller,
                paid.logical_object_id,
                copy.deepcopy(host._effective_card_data(paid)),
                [
                    host.state.cards[attachment_id].ref
                    for attachment_id in paid.attachments
                    if attachment_id in host.state.cards
                ],
                kind,
                destination,
                (),
            )
        )
    return entries


def _commit_additional_costs(
    host: CastCommitHost,
    proposal: CastProposal,
    response: Mapping[str, Any],
    card: Any,
    selected_option: Mapping[str, Any],
) -> _AdditionalCostCommit:
    result = _AdditionalCostCommit([], [], {}, {}, [])
    selected_exile = selected_option.get("selected_exile_card")
    if selected_exile:
        exiled = host._resolve_object(
            proposal.seat,
            str(selected_exile),
            zones={"hand"},
            owned_only=True,
        )
        host.move_card(
            exiled.object_id,
            "exile",
            reason=f"{card.printed_name} alternate cost",
            semantic_events=True,
        )
    for additional in selected_option.get("additional_costs", []):
        try:
            fixed_life = fixed_life_payment_additional_cost(additional)
        except AdditionalCostError as exc:
            raise CastProposalError(
                str(exc), reason="additional_cost_malformed"
            ) from exc
        if fixed_life is not None:
            _pay_fixed_life_additional_cost(
                host,
                proposal,
                card,
                selected_option,
                fixed_life.amount,
            )
            continue
        if additional.get("kind") == "life_x":
            _pay_life_additional_cost(
                host, proposal, response, card, selected_option
            )
    selections = list(selected_option.get("selected_additional_costs", []))
    if not selections:
        return result
    result.source_snapshots = [
        copy.deepcopy(source)
        for source in host._semantic_event_sources()
    ]
    result.source_zones = {
        source.object_id: source.zone for source in result.source_snapshots
    }
    result.source_characteristics = {
        source.object_id: copy.deepcopy(
            host._effective_card_data(source)
        )
        for source in result.source_snapshots
    }
    changes = []
    for selected in selections:
        kind = str(selected["kind"])
        if kind == "counter_placement":
            result.paid_refs.append(
                _commit_counter_placement_additional_cost(
                    host,
                    proposal,
                    response,
                    card,
                    selected_option,
                    selected,
                )
            )
            continue
        if kind == ZONE_CHANGE_COST_KIND:
            changes.append(
                _fixed_zone_change_commit_entry(
                    host, proposal, response, selected_option, selected
                )
            )
            continue
        changes.extend(
            _ordinary_card_cost_commit_entries(host, proposal, selected)
        )
    for (
        paid,
        origin,
        controller,
        logical_id,
        data,
        attachments,
        kind,
        destination,
        replacement_selections,
    ) in changes:
        transition_kind = (
            ZoneTransitionKind.DISCARD
            if kind == "discard"
            else ZoneTransitionKind.SACRIFICE
            if kind == "sacrifice"
            else ZoneTransitionKind.ORDINARY
        )
        host.move_card(
            paid.object_id,
            destination,
            reason=f"{card.printed_name} {kind} cost",
            semantic_events=False,
            replacement_selections=replacement_selections,
            transition_kind=transition_kind,
        )
        result.paid_refs.append(paid.ref)
        result.deferred_events.append(
            (
                paid,
                origin,
                controller,
                logical_id,
                data,
                attachments,
                paid.zone,
                transition_kind,
            )
        )
        host._log(
            proposal.seat,
            f"cost.{kind}",
            f"{proposal.seat} paid {paid.ref} as a {kind} cost.",
            {"spell": card.ref, "object": paid.ref},
            importance=1,
            changed_objects=[paid.object_id],
            changed_players=[proposal.seat],
        )
    return result


def _face_down_method_spec_from_details(
    details: Mapping[str, Any],
) -> FixedManaMorphSpec | None:
    cast_method = details.get("cast_method")
    if cast_method not in FACE_DOWN_CAST_METHODS:
        return None
    descriptor_field = (
        "morph" if cast_method == MORPH_CAST_METHOD else "face_down_method"
    )
    reason = (
        "morph_contract_malformed"
        if cast_method == MORPH_CAST_METHOD
        else "face_down_method_contract_malformed"
    )
    try:
        method_spec = FixedManaMorphSpec.from_dict(
            details.get(descriptor_field) or {}
        )
    except MorphError as exc:
        raise CastProposalError(str(exc), reason=reason) from exc
    if method_spec.method != cast_method:
        raise CastProposalError(
            "Face-down method descriptor changed before commit",
            reason=reason,
        )
    return method_spec


def _create_spell_item(
    host: CastCommitHost,
    proposal: CastProposal,
    card: Any,
    record: Any,
    program: Any,
    selected_option: Mapping[str, Any],
    details: Mapping[str, Any],
    spent: Mapping[str, int],
) -> StackItem:
    card.annotations.pop("temporary_play_permission", None)
    host._remove_from_zone(card)
    host._reset_zone_change(card, "stack")
    card.zone = "stack"
    card.controller = proposal.seat
    card.active_face = proposal.face
    if str(selected_option.get("kind") or "").casefold() == KICKER_MECHANIC_ID:
        mark_card_kicked(card)
    if selected_option.get("id") == FLASHBACK_CAST_OPTION_ID:
        mark_card_flashed_back(card)
    method_spec = _face_down_method_spec_from_details(details)
    if method_spec is not None:
        mark_card_face_down_for_morph(
            card,
            controller=proposal.seat,
            spec=method_spec,
        )
    destination = (
        "battlefield"
        if any(
            word in proposal.type_line.casefold()
            for word in (
                "artifact",
                "battle",
                "creature",
                "enchantment",
                "planeswalker",
            )
        )
        else "graveyard"
    )
    destination, lifecycle_context = fixed_cast_lifecycle_stack_fields(
        selected_option,
        destination,
    )
    ref = host._next_ref("S")
    used_improvise = bool(
        host.state.players[proposal.seat].stats.pop("next_spell_improvise", False)
    )
    used_uncounterable = bool(
        host.state.players[proposal.seat].stats.pop(
            "next_spell_uncounterable", False
        )
    )
    intrinsic_uncounterable = oracle_has_intrinsic_counter_prohibition(
        host.semantics,
        record.oracle_id,
        current_trusted=host.semantic_program_is_current_trusted,
    )
    return StackItem(
        stack_id=host._stable_runtime_id("stack", ref),
        ref=ref,
        kind="spell",
        controller=proposal.seat,
        label=(
            MORPH_FACE_DOWN_LABEL
            if method_spec is not None
            else card.active_face or record.name
        ),
        card_object_id=card.object_id,
        semantic_key=proposal.semantic_key,
        targets=list(proposal.targets),
        modes=list(details.get("selected_modes") or []),
        x_value=details.get("x_value"),
        chosen_face=card.active_face,
        notes=str(details.get("note") or ""),
        default_destination=destination,
        visibility=list(host.seats),
        mana_colors_spent=tuple(
            color
            for color in "WUBRG"
            if type(spent.get(color, 0)) is int and spent.get(color, 0) > 0
        ),
        context={
            "target_groups": thaw_json(proposal.target_groups),
            "target_snapshots": thaw_json(proposal.target_snapshots),
            "targets_revalidated": False,
            "targets_chosen_at_creation": True,
            "aura_spell": bool(details.get("aura_spell", False)),
            **(
                {
                    "aura_enchant_spec": copy.deepcopy(
                        details["aura_enchant_spec"]
                    )
                }
                if details.get("aura_enchant_spec") is not None
                else {}
            ),
            "cant_be_countered": (
                used_uncounterable or intrinsic_uncounterable
            ),
            "granted_improvise": used_improvise,
            "cost_option": proposal.cost_option_id,
            **lifecycle_context,
            **(
                {
                    EVOKE_PAYMENT_FIELD: copy.deepcopy(
                        selected_option[EVOKE_PAYMENT_FIELD]
                    )
                }
                if selected_option.get(EVOKE_PAYMENT_FIELD) is not None
                else {}
            ),
            **(
                {
                    "cast_method": method_spec.method,
                    (
                        "morph_spec_fingerprint"
                        if method_spec.method == MORPH_CAST_METHOD
                        else "face_down_method_spec_fingerprint"
                    ): method_spec.fingerprint,
                }
                if method_spec is not None
                else {}
            ),
            **(
                {"target_schema_override": details["target_schema_override"]}
                if details.get("target_schema_override") is not None
                else {}
            ),
            **(
                {
                    "cast_option_effects": copy.deepcopy(
                        list(selected_option.get("effects", []))
                    ),
                    "dynamic_effects": copy.deepcopy(
                        list(selected_option.get("effects", []))
                    ),
                }
                if "effects" in selected_option
                else {}
            ),
        },
    )


def _record_cast(
    host: CastCommitHost,
    proposal: CastProposal,
    card: Any,
    item: StackItem,
    spent: Mapping[str, int],
    activations: Sequence[Mapping[str, Any]],
    selected_option: Mapping[str, Any],
    costs: _AdditionalCostCommit,
) -> None:
    host._log(
        proposal.seat,
        "stack.cast",
        f"{proposal.seat} cast {item.ref} {item.label}.",
        {
            "stack": item.ref,
            "object": card.ref,
            "colors": [
                str(value).upper()
                for value in host._effective_card_data(card).get("colors", [])
            ],
            "from": proposal.origin,
            "requirements": thaw_json(proposal.requirements),
            "payment": {key: value for key, value in spent.items() if value},
            "mana_sources": [
                {
                    "source": activation.get("source_ref")
                    or activation.get("source"),
                    "bundle": activation.get("bundle"),
                }
                for activation in activations
            ],
            "targets": item.targets,
            "modes": item.modes,
            "x": item.x_value,
            "commander_tax": thaw_json(proposal.details).get("commander_tax", 0),
            "cost_option": proposal.cost_option_id,
            "exiled_for_cost": selected_option.get("selected_exile_card"),
            "additional_cost_objects": costs.paid_refs,
            "tap_cost_objects": list(proposal.tap_cost_refs),
        },
        importance=2,
        changed_objects=[card.object_id],
        changed_players=[proposal.seat],
    )


def _prior_controller_spell_count(
    host: CastCommitHost,
    controller: str,
) -> int:
    """Snapshot the current caster's public spell count before this cast."""

    if host.state.turn_history is not None:
        return sum(
            event.actor == controller
            for event in host._current_turn_history("spell_cast")
        )
    return sum(
        event.code == "stack.cast"
        and event.actor == controller
        and event.turn_sequence == host.state.turn_sequence
        for event in host.state.events
    )


def _dispatch_cast_events(
    host: CastCommitHost,
    proposal: CastProposal,
    card: Any,
    item: StackItem,
    program: Any,
    costs: _AdditionalCostCommit,
    *,
    prior_spell_count: int,
    prior_controller_spell_count: int,
) -> None:
    trigger_batch = list(
        cascade_trigger_items(host, spell=item, card=card)
    )
    trigger_batch.extend(
        storm_trigger_items(
            host,
            spell=item,
            card=card,
            program=program,
            prior_spell_count=prior_spell_count,
        )
    )
    for (
        paid,
        origin,
        controller,
        logical_id,
        data,
        attachments,
        destination,
        transition_kind,
    ) in costs.deferred_events:
        host._dispatch_zone_change_events(
            paid,
            origin=origin,
            destination=destination,
            origin_controller=controller,
            origin_logical_object_id=logical_id,
            origin_data=data,
            origin_attachments=attachments,
            departure_sources=costs.source_snapshots,
            departure_source_zones=costs.source_zones,
            departure_source_characteristics=(
                costs.source_characteristics
            ),
            reason=f"{card.printed_name} additional cost",
            transition_kind=transition_kind,
            trigger_batch=trigger_batch,
        )
    cast_types, cast_subtypes, cast_supertypes = host._type_parts(
        proposal.type_line
    )
    effective_spell = host._effective_card_data(card)
    cast_colors = tuple(effective_spell.get("colors") or ())
    mana_cost = str(effective_spell.get("mana_cost") or "")
    host._record_turn_history(
        "spell_cast",
        actor=proposal.seat,
        object_incarnation=card.logical_object_id,
        types=cast_types,
    )
    context = SpellCastEvent(
        schema_version=4,
        card_ref=card.ref,
        object_id=card.object_id,
        logical_object_id=card.logical_object_id,
        controller=proposal.seat,
        origin=proposal.origin,
        stack_ref=item.ref,
        types=tuple(cast_types),
        subtypes=tuple(cast_subtypes),
        supertypes=tuple(cast_supertypes),
        colors=cast_colors,
        mana_value=mana_value_of_cost(
            mana_cost,
            x_value=int(item.x_value or 0),
        ),
        owner=card.owner,
        active_player=host.state.active_player,
        caster_spell_number=prior_controller_spell_count + 1,
        kicked=card.annotations.get(KICKER_ANNOTATION) is True,
        has_x_cost="{X}" in mana_cost.upper(),
        has_adventure=getattr(host.card_record(card), "layout", None)
        == "adventure",
        keywords=tuple(effective_spell.get("keywords") or ()),
        phase=host.state.phase,
    ).to_context()
    event_sources = list(host._semantic_event_sources())
    if all(source.object_id != card.object_id for source in event_sources):
        event_sources.append(card)
    host._dispatch_semantic_event(
        "spell.cast",
        context,
        sources=event_sources,
        trigger_batch=trigger_batch,
    )
    if "artifact" in cast_types:
        host._dispatch_semantic_event(
            "artifact.cast",
            context,
            sources=event_sources,
            trigger_batch=trigger_batch,
        )
    enqueue_trigger_batch(host, trigger_batch)


def _revalidate_cast_contracts(
    host: CastCommitHost,
    proposal: CastProposal,
    card: Any,
    record: Any,
    program: Any,
    details: Mapping[str, Any],
) -> dict[str, Any]:
    """Revalidate compiled casting contracts without mutating state."""

    cast_method = details.get("cast_method")
    proposed_method = _face_down_method_spec_from_details(details)
    if proposed_method is not None:
        if (
            compiled_fixed_mana_face_down_method_spec(
                host,
                card,
                method=cast_method,
            )
            != proposed_method
        ):
            raise CastProposalError(
                "The fixed-mana face-down contract changed before commit",
                reason=(
                    "stale_morph_contract"
                    if cast_method == MORPH_CAST_METHOD
                    else "stale_face_down_method_contract"
                ),
            )
    selected_option = dict(details["selected_cost_option"])
    if str(selected_option.get("kind") or "").casefold() == KICKER_MECHANIC_ID:
        current_kicker = compiled_fixed_mana_kicker_spec(host, card)
        if (
            current_kicker is None
            or selected_option.get("kicker_fingerprint")
            != current_kicker.fingerprint
        ):
            raise CastProposalError(
                "The fixed-mana Kicker contract changed before commit",
                reason="stale_kicker_contract",
            )
    if selected_option.get("id") == FLASHBACK_CAST_OPTION_ID:
        current_flashback = compiled_fixed_mana_flashback_spec(host, card)
        if (
            proposal.origin != "graveyard"
            or current_flashback is None
            or selected_option.get("flashback_fingerprint")
            != current_flashback.fingerprint
            or selected_option.get("source_zone") != "graveyard"
        ):
            raise CastProposalError(
                "The fixed-mana Flashback contract changed before commit",
                reason="stale_flashback_contract",
            )
    lifecycle_raw = selected_option.get(FIXED_CAST_LIFECYCLE_CONTEXT_FIELD)
    if lifecycle_raw is not None:
        try:
            proposed_lifecycle = FixedCastLifecycleSpec.from_dict(
                lifecycle_raw
            )
        except (FixedCastLifecycleError, TypeError) as exc:
            raise CastProposalError(
                str(exc),
                reason="stale_fixed_cast_lifecycle_contract",
            ) from exc
        current_lifecycle = current_fixed_cast_lifecycle_spec(
            host,
            card,
            proposed_lifecycle.kind,
        )
        expected_origin = {
            FixedCastLifecycleKind.MADNESS: "exile",
            FixedCastLifecycleKind.WARP: "hand",
            FixedCastLifecycleKind.RETRACE: "graveyard",
        }.get(proposed_lifecycle.kind)
        option_id = str(selected_option.get("id") or "")
        if (
            current_lifecycle != proposed_lifecycle
            or selected_option.get("fixed_cast_lifecycle_fingerprint")
            != proposed_lifecycle.fingerprint
            or (
                expected_origin is not None
                and proposal.origin != expected_origin
            )
            or (
                proposed_lifecycle.kind
                is FixedCastLifecycleKind.RETRACE
                and option_id not in {"retrace"}
            )
            or (
                proposed_lifecycle.kind
                is not FixedCastLifecycleKind.RETRACE
                and option_id != proposed_lifecycle.kind.value
            )
        ):
            raise CastProposalError(
                "The fixed cast-lifecycle contract changed before commit",
                reason="stale_fixed_cast_lifecycle_contract",
            )
    evoke_payment = selected_option.get(EVOKE_PAYMENT_FIELD)
    if evoke_payment is not None:
        if not validate_evoke_payment_marker(evoke_payment):
            raise CastProposalError(
                "The Evoke payment marker is malformed",
                reason="stale_evoke_contract",
            )
        evoke_fingerprint = selected_option.get("evoke_fingerprint")
        if evoke_fingerprint is not None and evoke_fingerprint not in {
            spec.fingerprint
            for spec in compiled_evoke_specs(
                host,
                record.oracle_id,
                spell_program=program,
            )
        }:
            raise CastProposalError(
                "The fixed-mana Evoke contract changed before commit",
                reason="stale_evoke_contract",
            )
    try:
        convoke_plan = revalidate_convoke_payment(
            host,
            proposal.seat,
            selected_option,
        )
    except ConvokeError as exc:
        raise CastProposalError(
            str(exc),
            status="unpayable",
            reason="stale_convoke_payment",
        ) from exc
    try:
        revalidate_delve_payment(host, proposal.seat, selected_option)
    except CastingPaymentKeywordError as exc:
        raise CastProposalError(
            str(exc),
            status="unpayable",
            reason="stale_delve_payment",
        ) from exc
    if convoke_plan is not None and tuple(convoke_plan.selected_refs) != tuple(
        ref for ref in proposal.tap_cost_refs if ref in convoke_plan.selected_refs
    ):
        raise CastProposalError(
            "The Convoke tap plan no longer matches the cast proposal",
            status="unpayable",
            reason="stale_convoke_payment",
        )
    return selected_option


def commit_cast(
    host: CastCommitHost,
    proposal: CastProposal,
    response: Mapping[str, Any],
) -> None:
    """Commit one revalidated cast proposal through authoritative owners."""

    card, record, program = _revalidate_source(host, proposal)
    details = dict(thaw_json(proposal.details))
    selected_option = _revalidate_cast_contracts(
        host,
        proposal,
        card,
        record,
        program,
        details,
    )
    requirements = dict(thaw_json(proposal.requirements))
    tap_cards = [
        host._resolve_object(
            proposal.seat,
            ref,
            zones={"battlefield"},
            controlled_only=True,
        )
        for ref in proposal.tap_cost_refs
    ]
    spent, activations = host._pay_for_cost(
        proposal.seat,
        requirements,
        response,
        exclude_sources={card.object_id for card in tap_cards},
        spend_context=host._spell_mana_spend_context(proposal.type_line),
    )
    _commit_tap_costs(host, proposal, card)
    costs = _commit_additional_costs(
        host, proposal, response, card, selected_option
    )
    item = _create_spell_item(
        host,
        proposal,
        card,
        record,
        program,
        selected_option,
        details,
        spent,
    )
    item.x_value = response.get("x")
    item.notes = str(response.get("note") or "")
    prior_spell_count = prior_storm_spell_count(host)
    prior_controller_spell_count = _prior_controller_spell_count(
        host,
        proposal.seat,
    )
    host.state.stack.append(item)
    if proposal.origin == "command" and card.is_commander:
        player = host.state.players[proposal.seat]
        player.commander_casts[card.oracle_id] = (
            player.commander_casts.get(card.oracle_id, 0) + 1
        )
    _record_cast(
        host, proposal, card, item, spent, activations, selected_option, costs
    )
    _dispatch_cast_events(
        host,
        proposal,
        card,
        item,
        program,
        costs,
        prior_spell_count=prior_spell_count,
        prior_controller_spell_count=prior_controller_spell_count,
    )
    collect_ward_occurrences(host, item)
    host.state.players[proposal.seat].yield_policy = YieldPolicy()
    if bool(details.get("during_resolution")):
        return
    if host._stabilize():
        return
    host.state.priority_player = proposal.seat
    host.state.priority_passes = []


__all__ = ["CastCommitHost", "commit_cast"]
