from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from typing import Any, Protocol

from ...abilities import ActivatedAbility, choose_ability, reduced_requirements
from ...haste import summoning_sickness_prohibits_tap_or_untap_cost
from ...replacement.immutable import thaw_value
from ...station import station_candidates, station_cost_choice
from ..action_proposals import ActionOffer, ActivationProposal, freeze_json
from .model import (
    ActivationProposalError,
    ActivationProposalRequest,
    ActivationProposalResult,
)


class ActivationProposalHost(Protocol):
    state: Any
    semantics: Any

    def _check_priority(self, seat: str) -> None: ...

    def _resolve_object(
        self,
        actor: str,
        ref: str,
        *,
        zones: set[str],
        controlled_only: bool = False,
        owned_only: bool = False,
    ) -> Any: ...

    def _activated_abilities(self, card: Any) -> tuple[ActivatedAbility, ...]: ...

    def _ability_availability(
        self, seat: str, source: Any, ability: ActivatedAbility
    ) -> tuple[str, str | None]: ...

    def _sorcery_timing(self, seat: str) -> None: ...

    def _semantic_key_for_ability(
        self, source: Any, ability: ActivatedAbility
    ) -> str: ...

    def _normalize_target_submission(self, value: Any) -> Any: ...

    def _validate_semantic_targets(
        self,
        seat: str,
        program: Any,
        submission: Any,
        *,
        modes: list[str],
        source_ref: str,
        target_schema: Mapping[str, Any] | None = None,
    ) -> tuple[list[str], Any]: ...

    def _target_snapshot(self, ref: str) -> Mapping[str, Any]: ...

    def _fetch_context(
        self, seat: str, ability: ActivatedAbility, response: Mapping[str, Any]
    ) -> dict[str, Any]: ...

    def _effective_card_data(self, card: Any) -> Mapping[str, Any]: ...

    def _may_activate_creature_as_haste(self, seat: str, source: Any) -> bool: ...

    def _legendary_creatures_controlled(self, seat: str) -> int: ...

    def semantic_program_is_current_trusted(self, program: Any) -> bool: ...

    def _public_target_schema(
        self,
        seat: str,
        schema: Mapping[str, Any],
        *,
        source_ref: str,
    ) -> Mapping[str, Any] | None: ...

    def _crew_threshold(self, ability: ActivatedAbility) -> int | None: ...

    def _crew_candidates(self, seat: str, source: Any) -> list[Any]: ...

    def _ability_choice_candidates(
        self, seat: str, source: Any, choice: Any
    ) -> tuple[str, ...]: ...

    def _mana_modes_for_ability(
        self, seat: str, source: Any, ability: ActivatedAbility
    ) -> list[Any]: ...

    def display_name(self, object_id: str) -> str: ...


def _resolve_activation_source(
    host: ActivationProposalHost, request: ActivationProposalRequest
) -> Any:
    source = host._resolve_object(
        request.actor,
        request.source_ref,
        zones=set(request.zones),
        controlled_only=False,
        owned_only=False,
    )
    if source.zone == "battlefield":
        if source.controller != request.actor:
            raise ActivationProposalError(
                "You do not control that ability source",
                reason="wrong_controller",
            )
    elif source.owner != request.actor:
        raise ActivationProposalError(
            "You do not own that nonbattlefield ability source",
            reason="wrong_owner",
        )
    return source


def _select_ability(
    host: ActivationProposalHost,
    source: Any,
    selector: str | int | None,
) -> ActivatedAbility:
    abilities = tuple(
        ability
        for ability in host._activated_abilities(source)
        if source.zone in ability.zones
    )
    try:
        return choose_ability(abilities, selector)
    except ValueError as exc:
        raise ActivationProposalError(str(exc), reason="ability_selection") from exc


def _validate_activation_cost_contract(
    host: ActivationProposalHost,
    request: ActivationProposalRequest,
    source: Any,
    ability: ActivatedAbility,
) -> None:
    if source.zone not in ability.zones:
        raise ActivationProposalError(
            f"{ability.ability_id} cannot be activated from {source.zone}",
            reason="source_zone",
        )
    if not ability.compiled_cost:
        detail = list(ability.complex_symbols) + list(ability.uncompiled_costs)
        raise ActivationProposalError(
            f"Cost for {source.printed_name} {ability.ability_id} is not compiled: {detail}. Request a rules/cost semantic rather than declaring the cost as a pilot.",
            status="unresolved",
            reason="uncompiled_cost",
        )
    status, reason = host._ability_availability(request.actor, source, ability)
    if status != "payable":
        raise ActivationProposalError(
            f"{source.printed_name} {ability.ability_id} is not currently payable"
            + (f": {reason}" if reason else ""),
            status=("unresolved" if status == "unresolved" else "unpayable"),
            reason=reason or "unpayable",
        )
    response = request.response()
    if host.state.config.strict_mana and any(
        key in response
        for key in ("mana_cost", "declared_cost", "costs", "cost_effects", "tap")
    ):
        raise ActivationProposalError(
            "Pilot-supplied activation costs are disabled in strict mode; select the Oracle ability and cost objects only.",
            reason="pilot_cost",
        )
    if ability.sorcery_speed:
        host._sorcery_timing(request.actor)


def _validate_activation_timing_costs(
    host: ActivationProposalHost,
    request: ActivationProposalRequest,
    source: Any,
    ability: ActivatedAbility,
) -> None:
    if (
        (ability.tap_source or ability.untap_source)
        and source.zone == "battlefield"
        and summoning_sickness_prohibits_tap_or_untap_cost(
            host,
            source,
            as_though_haste=host._may_activate_creature_as_haste(
                request.actor, source
            ),
        )
    ):
        raise ActivationProposalError(
            f"{source.ref} is summoning sick",
            status="unpayable",
            reason="summoning_sickness",
        )
    if ability.tap_source and (
        source.zone != "battlefield" or source.tapped
    ):
        message = (
            "Tap costs require a battlefield permanent"
            if source.zone != "battlefield"
            else f"{source.ref} is tapped"
        )
        raise ActivationProposalError(
            message, status="unpayable", reason="tap_cost_unpayable"
        )
    if ability.untap_source and (
        source.zone != "battlefield" or not source.tapped
    ):
        raise ActivationProposalError(
            "Untap-symbol cost requires a tapped battlefield permanent",
            status="unpayable",
            reason="untap_cost_unpayable",
        )


def build_activation_proposal(
    host: ActivationProposalHost,
    request: ActivationProposalRequest,
) -> ActivationProposal:
    """Build a complete immutable activation plan without mutation."""

    host._check_priority(request.actor)
    response = request.response()
    if response.get("semantic_key") is not None:
        raise ActivationProposalError(
            "Pilots cannot select semantic program identifiers",
            reason="pilot_semantic_key",
        )
    source = _resolve_activation_source(host, request)
    ability = _select_ability(host, source, request.ability_selector)
    supplied_fingerprint = response.get("proposal_fingerprint")
    if supplied_fingerprint:
        advertised = build_activation_offer(
            host, request.actor, source, ability
        )
        expiry_revision = int(
            response.get(
                "expiry_revision",
                advertised.offer.expiry_revision if advertised.offer else -1,
            )
        )
        if (
            advertised.offer is None
            or host.state.revision not in {expiry_revision, expiry_revision + 1}
            or str(supplied_fingerprint)
            != replace(
                advertised.offer, expiry_revision=expiry_revision
            ).fingerprint
        ):
            raise ActivationProposalError(
                "The advertised activation proposal is stale",
                reason="stale_proposal",
            )
    _validate_activation_cost_contract(host, request, source, ability)
    _validate_activation_timing_costs(host, request, source, ability)
    semantic_key = host._semantic_key_for_ability(source, ability)
    program = host.semantics.get(semantic_key)
    target_schema = (
        thaw_value(ability.target_schema)
        if ability.target_schema is not None
        else None
    )
    targets, target_groups = host._validate_semantic_targets(
        request.actor,
        program,
        host._normalize_target_submission(response.get("targets")),
        modes=list(request.modes),
        source_ref=source.ref,
        target_schema=target_schema,
    )
    snapshots = {ref: host._target_snapshot(ref) for ref in targets}
    context = host._fetch_context(request.actor, ability, response)
    requirements = reduced_requirements(
        ability,
        legendary_creatures=host._legendary_creatures_controlled(request.actor),
    )
    proposal = ActivationProposal(
        seat=request.actor,
        source_ref=source.ref,
        source_object_id=source.object_id,
        source_zone=source.zone,
        ability_id=ability.ability_id,
        semantic_key=("builtin:fetch_land" if context else semantic_key),
        mana_ability=ability.mana_ability,
        requirements=freeze_json(requirements),
        targets=tuple(targets),
        target_groups=freeze_json(target_groups),
        target_snapshots=freeze_json(snapshots),
        details=freeze_json(
            {
                "selected_modes": list(request.modes),
                "target_schema_override": target_schema,
                "builtin_context": context,
            }
        ),
    )
    return proposal


def _activation_choice_schema(
    host: ActivationProposalHost,
    seat: str,
    source: Any,
    ability: ActivatedAbility,
    hint: Mapping[str, Any],
) -> dict[str, Any] | None:
    if ability.mana_ability:
        modes = host._mana_modes_for_ability(seat, source, ability)
        if len(modes) > 1:
            return {
                "mana_output": {
                    "type": "mana_bundle",
                    "label": "Mana to add",
                    "options": [
                        {
                            "value": {
                                key: amount
                                for key, amount in mode.bundle.items()
                                if amount
                            },
                            "label": "Add "
                            + "".join(
                                f"{{{key}}}" * amount
                                for key, amount in mode.bundle.items()
                                if amount
                            ),
                        }
                        for mode in modes
                    ],
                }
            }
    if hint.get("search_types"):
        return {
            "resolution_time": True,
            "search_types": list(hint["search_types"]),
        }
    return None


def build_activation_offer(
    host: ActivationProposalHost,
    seat: str,
    source: Any,
    ability: ActivatedAbility,
) -> ActivationProposalResult:
    """Advertise one activation using the same source/cost/target queries."""

    request = ActivationProposalRequest(
        actor=seat,
        source_ref=source.ref,
        zones=(source.zone,),
        ability_selector=ability.ability_id,
    )
    try:
        resolved = _resolve_activation_source(host, request)
        selected = _select_ability(host, resolved, request.ability_selector)
        _validate_activation_cost_contract(host, request, resolved, selected)
        _validate_activation_timing_costs(host, request, resolved, selected)
    except ActivationProposalError as exc:
        return ActivationProposalResult(exc.status, exc.reason)
    semantic_key = host._semantic_key_for_ability(source, selected)
    program = host.semantics.get(semantic_key)
    if (
        program is not None
        and host.state.config.semantic_policy == "trusted_only"
        and not host.semantic_program_is_current_trusted(program)
    ):
        return ActivationProposalResult("unresolved", "semantic_policy_requires_trusted")
    target_schema = (
        thaw_value(selected.target_schema)
        if selected.target_schema is not None
        else program.target_schema if program is not None else None
    )
    public_schema = None
    if target_schema is not None:
        public_schema = host._public_target_schema(
            seat, target_schema, source_ref=source.ref
        )
        if public_schema is None:
            return ActivationProposalResult("unavailable", "mandatory_target_unavailable")
    hint = selected.compact(source_ref=source.ref, zone=source.zone)
    if selected.choices:
        hint["choose_cost"] = [
            {
                **choice.compact(),
                "legal_refs": list(
                    host._ability_choice_candidates(
                        seat, source, choice
                    )
                ),
            }
            for choice in selected.choices
        ]
    threshold = host._crew_threshold(selected)
    if threshold is not None:
        hint["choose_cost"] = [
            {
                "k": "crew",
                "z": "battlefield",
                "minimum": 0 if threshold == 0 else 1,
                "minimum_total_power": threshold,
                "legal_refs": [
                    candidate.ref
                    for candidate in host._crew_candidates(seat, source)
                ],
            }
        ]
    if station_cost_choice(selected) is not None:
        hint["choose_cost"] = [
            {
                "k": "station",
                "z": "battlefield",
                "minimum": 1,
                "maximum": 1,
                "legal_refs": [
                    candidate.ref
                    for candidate in station_candidates(host, seat, source)
                ],
            }
        ]
    fetch_types = selected.library_search_types
    if fetch_types:
        hint["search_types"] = list(fetch_types)
    payload: dict[str, Any] = {
        "kind": "activate",
        "source": source.ref,
        "ability": selected.ability_id,
        "from": source.zone,
        "cost_summary": {
            key: value
            for key, value in hint.items()
            if key
            in {
                "m",
                "tap",
                "life",
                "energy",
                "loyalty",
                "sac_self",
                "discard_self",
                "exile_self",
                "choose_cost",
            }
        },
    }
    if selected.mana_ability:
        payload["mana_ability"] = True
    choice_schema = _activation_choice_schema(
        host, seat, source, selected, hint
    )
    if choice_schema is not None:
        payload["choice_schema"] = choice_schema
    if public_schema is not None:
        payload["target_schema"] = public_schema
    offer = ActionOffer(
        action_id=f"activate:{source.ref}:{selected.ability_id}",
        action="activate",
        seat=seat,
        label=f"{host.display_name(source.object_id)} — {selected.effect_text}",
        expiry_revision=host.state.revision,
        payload=freeze_json(payload),
    )
    return ActivationProposalResult("payable", "payable", offer=offer)


__all__ = [
    "ActivationProposalHost",
    "build_activation_offer",
    "build_activation_proposal",
]
