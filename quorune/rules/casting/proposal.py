from __future__ import annotations

import copy
from collections.abc import Mapping
from dataclasses import replace
from typing import Any, Protocol

from ...aura import EnchantSpec, enchant_spec_to_dict, is_aura_type_line
from ...cast_timing import cast_timing_is_legal, type_line_has_card_type
from ...compiled_cast_timing import compiled_cast_timing_permissions
from ...compiled_morph import compiled_fixed_mana_face_down_method_spec
from ...convoke import ConvokeError
from ...morph import FACE_DOWN_CAST_METHODS, MORPH_CAST_METHOD
from ...targets import available_modes
from ..action_proposals import (
    ActionOffer,
    CastCostOption,
    CastProposal,
    freeze_json,
    thaw_json,
)
from ..modal_selection import canonical_modes
from .costs import CastCostHost, revalidate_convoke_payment
from .model import CastProposalError, CastProposalRequest, CastProposalResult


class CastProposalHost(CastCostHost, Protocol):
    semantics: Any
    state: Any

    def _check_priority(self, seat: str) -> None: ...

    def _resolve_object(
        self,
        actor: str,
        ref: str,
        *,
        zones: set[str],
        owned_only: bool = False,
    ) -> Any: ...

    def _compiled_zone_cast_permission(self, seat: str, card: Any) -> bool: ...

    def _select_cast_face(
        self, record: Any, requested_face: str | None
    ) -> Mapping[str, Any] | None: ...

    def _sorcery_timing(self, seat: str) -> None: ...

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

    def _public_target_schema(
        self,
        seat: str,
        schema: Mapping[str, Any],
        *,
        source_ref: str,
    ) -> Mapping[str, Any] | None: ...

    def semantic_program_is_current_trusted(self, program: Any) -> bool: ...

    def _compiled_enchant_spec(
        self,
        card: Any,
        *,
        face_name: str | None = None,
    ) -> EnchantSpec | None: ...

    def _trusted_generic_spell(self, record: Any) -> bool: ...

    def _cast_cost_options(
        self,
        seat: str,
        card: Any,
        program: Any,
        *,
        response: Mapping[str, Any] | None = None,
        hint: bool,
        force_without_mana_cost: bool = False,
        alternative_base: Mapping[str, Any] | None = None,
        cast_type_line: str | None = None,
        suppress_source_costs: bool = False,
    ) -> list[dict[str, Any]]: ...


def _resolve_cast_source(host: CastProposalHost, request: CastProposalRequest) -> Any:
    card = host._resolve_object(
        request.actor,
        request.card_ref,
        zones=set(request.zones),
        owned_only=False,
    )
    if card.zone in {"hand", "command"} and card.owner != request.actor:
        raise CastProposalError(
            "A player may cast only their own cards from this zone",
            reason="wrong_owner",
        )
    if card.zone == "command" and not card.is_commander:
        raise CastProposalError(
            "Only this seat's commander cards may be cast from the command zone",
            reason="not_commander",
        )
    internally_authorized = (
        request.authorized_from_zone is not None
        and card.zone == request.authorized_from_zone
    )
    if (
        card.zone not in {"hand", "command"}
        and not internally_authorized
        and not host._compiled_zone_cast_permission(request.actor, card)
    ):
        raise CastProposalError(
            f"Casting {card.printed_name} from {card.zone} is not authorized by a compiled zone permission.",
            reason="zone_permission",
        )
    return card


def _cast_face(
    host: CastProposalHost,
    request: CastProposalRequest,
    record: Any,
) -> Mapping[str, Any] | None:
    requested_face = request.required_face or request.face
    face = host._select_cast_face(record, requested_face)
    if (
        request.required_face is None
        and record.layout == "transform"
        and face is not None
        and record.faces
        and str(face.get("name") or "").casefold()
        != str(record.faces[0].get("name") or "").casefold()
    ):
        raise CastProposalError(
            "A transforming double-faced card's back face cannot be cast without a rules effect that specifically allows it",
            reason="transform_back_face",
        )
    if request.required_face is not None and (
        face is None
        or str(face.get("name") or "").casefold()
        != request.required_face.casefold()
    ):
        raise CastProposalError(
            "The rules effect no longer identifies that cast face",
            reason="stale_face_permission",
        )
    return face


def _validate_declared_cost(
    host: CastProposalHost,
    request: CastProposalRequest,
    card: Any,
    commander_tax: int,
) -> None:
    response = request.response()
    declared = response.get("declared_cost")
    if not declared or not host.state.config.strict_mana:
        return
    printed, _ = host._compiled_printed_cost_options(
        request.actor,
        card,
        x_value=request.x_value,
        hint=False,
    )
    supplied = {
        "GENERIC": int(declared.get("GENERIC", 0)) + commander_tax,
        **{color: int(declared.get(color, 0)) for color in "WUBRGC"},
    }
    authoritative = [
        host._mana_vector(option["requirements"]) for option in printed
    ]
    if supplied not in authoritative:
        raise CastProposalError(
            f"Pilot-declared casting cost {supplied} does not match authoritative cost {authoritative}.",
            reason="declared_cost_mismatch",
        )


def _selected_cost_option(
    request: CastProposalRequest,
    options: tuple[Any, ...],
    *,
    card_name: str,
    mana_cost: str,
    strict_mana: bool,
) -> Any:
    if not options:
        if strict_mana:
            raise CastProposalError(
                f"{card_name} has no currently payable compiled casting cost ({mana_cost}).",
                status="unpayable",
                reason="mandatory_cost_unpayable",
            )
        raise CastProposalError(
            f"Supply declared_cost for {card_name} in non-strict mode.",
            status="unresolved",
            reason="unresolved_cost_semantics",
        )
    option_id = request.cost_option_id or (
        "normal"
        if any(option.option_id == "normal" for option in options)
        else options[0].option_id if len(options) == 1 else ""
    )
    selected = next(
        (option for option in options if option.option_id == option_id), None
    )
    if selected is None:
        raise CastProposalError(
            "The selected casting-cost option is not currently legal and payable",
            status="unpayable",
            reason="cost_option_unpayable",
        )
    return selected


def _validate_offer_fingerprint(
    host: CastProposalHost,
    request: CastProposalRequest,
    card: Any,
) -> None:
    response = request.response()
    supplied = response.get("proposal_fingerprint")
    if not supplied:
        return
    advertised = build_cast_offer(
        host,
        request.actor,
        card,
        cast_method=request.cast_method,
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
        or str(supplied)
        != replace(advertised.offer, expiry_revision=expiry_revision).fingerprint
    ):
        raise CastProposalError(
            "The advertised casting proposal is stale",
            reason="stale_proposal",
        )


def _spell_semantic_key(
    record: Any,
    face: Mapping[str, Any] | None,
) -> str:
    face_key = str(face.get("name") or "") if face else "front"
    return f"{record.oracle_id}:spell:{face_key}"


def _required_face_down_method_spec(
    host: CastProposalHost,
    card: Any,
    cast_method: str | None,
) -> Any | None:
    if cast_method is None:
        return None
    method_spec = compiled_fixed_mana_face_down_method_spec(
        host,
        card,
        method=cast_method,
    )
    if method_spec is None:
        raise CastProposalError(
            "This card has no current trusted fixed-mana face-down contract",
            reason=(
                "morph_contract_unavailable"
                if cast_method == MORPH_CAST_METHOD
                else "face_down_method_contract_unavailable"
            ),
        )
    return method_spec


def _rules_authorized_cost_base(
    request: CastProposalRequest,
    method_spec: Any | None,
) -> tuple[dict[str, Any] | None, bool]:
    if method_spec is not None and request.authorized_cost_option is not None:
        raise CastProposalError(
            "A scoped cast cannot combine face-down and rules-authored costs",
            reason="conflicting_authorized_cost",
        )
    if request.authorized_cost_option is not None:
        return dict(thaw_json(request.authorized_cost_option)), True
    if method_spec is None:
        return None, False
    return (
        {
            "id": str(request.cast_method),
            "kind": "alternate",
            "label": f"Cast face down using {str(request.cast_method).title()}",
            "requirements": {
                "GENERIC": 3,
                **{color: 0 for color in "WUBRGC"},
            },
        },
        True,
    )


def _cast_program_and_cost(
    host: CastProposalHost,
    request: CastProposalRequest,
    card: Any,
    record: Any,
    face: Mapping[str, Any] | None,
) -> tuple[str, str, int, str, Any, CastCostOption]:
    response = request.response()
    method_spec = _required_face_down_method_spec(
        host,
        card,
        request.cast_method,
    )
    type_line = (
        "Creature"
        if method_spec is not None
        else str(face.get("type_line") or "") if face else record.type_line
    )
    mana_cost = (
        "{3}"
        if method_spec is not None
        else str(face.get("mana_cost") or "") if face else record.mana_cost
    )
    face_name = str(face.get("name") or "") if face else None
    if response.get("protector") is not None:
        raise CastProposalError(
            "A Battle protector is chosen as the Battle enters, not while its spell is cast",
            reason="battle_protector_timing",
        )
    if type_line_has_card_type(type_line, "land"):
        raise CastProposalError(
            "A land card cannot be cast as a spell",
            reason="land_not_spell",
        )
    if not request.ignore_timing and not cast_timing_is_legal(
        host.state,
        request.actor,
        type_line,
        ()
        if method_spec is not None
        else compiled_cast_timing_permissions(
            host,
            card,
            face_name=face_name,
        ),
    ):
        host._sorcery_timing(request.actor)
        raise CastProposalError("Illegal cast timing", reason="timing")
    commander_tax = (
        2
        * host.state.players[request.actor].commander_casts.get(card.oracle_id, 0)
        if card.zone == "command" and card.is_commander
        else 0
    )
    if method_spec is None:
        _validate_declared_cost(host, request, card, commander_tax)
    elif response.get("declared_cost") is not None:
        raise CastProposalError(
            "Face-down casting uses only its server-authored alternative cost",
            reason=(
                "morph_declared_cost"
                if request.cast_method == MORPH_CAST_METHOD
                else "face_down_method_declared_cost"
            ),
        )
    semantic_key = (
        f"builtin:{request.cast_method}-face-down"
        if method_spec is not None
        else _spell_semantic_key(record, face)
    )
    program = None if method_spec is not None else host.semantics.get(semantic_key)
    alternative_base, suppress_source_costs = _rules_authorized_cost_base(
        request,
        method_spec,
    )
    if request.cost_option_id is None:
        advertised = tuple(
            CastCostOption.from_dict(value)
            for value in host._cast_cost_options(
                request.actor,
                card,
                program,
                response=response,
                hint=True,
                force_without_mana_cost=request.force_without_mana_cost,
                alternative_base=alternative_base,
                cast_type_line=type_line if method_spec is not None else None,
                suppress_source_costs=suppress_source_costs,
            )
        )
        if sum(
            option.kind == "additional_alternative"
            for option in advertised
        ) > 1:
            raise CastProposalError(
                "Select one of the advertised additional-cost branches",
                reason="cost_option_required",
            )
    options = tuple(
        CastCostOption.from_dict(value)
        for value in host._cast_cost_options(
            request.actor,
            card,
            program,
            response=response,
            hint=False,
            force_without_mana_cost=request.force_without_mana_cost,
            alternative_base=alternative_base,
            cast_type_line=type_line if method_spec is not None else None,
            suppress_source_costs=suppress_source_costs,
        )
    )
    selected = _selected_cost_option(
        request,
        options,
        card_name=card.printed_name,
        mana_cost=mana_cost,
        strict_mana=host.state.config.strict_mana,
    )
    requirements = host._mana_vector(selected.to_dict()["requirements"])
    declared = response.get("declared_cost")
    if declared and host.state.config.strict_mana:
        supplied = {
            "GENERIC": int(declared.get("GENERIC", 0)) + commander_tax,
            **{color: int(declared.get(color, 0)) for color in "WUBRGC"},
        }
        if supplied != requirements:
            raise CastProposalError(
                f"Pilot-declared casting cost {supplied} does not match authoritative cost {requirements}.",
                reason="declared_cost_mismatch",
            )
    return type_line, mana_cost, commander_tax, semantic_key, program, selected


def _cast_targets_and_tap_costs(
    host: CastProposalHost,
    request: CastProposalRequest,
    card: Any,
    program: Any,
    selected: CastCostOption,
    aura_target_schema: Mapping[str, Any] | None,
) -> tuple[
    dict[str, Any],
    list[str],
    Any,
    dict[str, Any],
    list[str],
    Mapping[str, Any] | None,
    list[str],
]:
    selected_dict = selected.to_dict()
    try:
        revalidate_convoke_payment(host, request.actor, selected_dict)
    except ConvokeError as exc:
        raise CastProposalError(
            str(exc),
            status="unpayable",
            reason="stale_convoke_payment",
        ) from exc
    target_schema = (
        copy.deepcopy(dict(selected_dict["target_schema"]))
        if isinstance(selected_dict.get("target_schema"), Mapping)
        else copy.deepcopy(dict(aura_target_schema))
        if aura_target_schema is not None
        else None
    )
    targets, target_groups = host._validate_semantic_targets(
        request.actor,
        program,
        host._normalize_target_submission(request.response().get("targets")),
        modes=list(request.modes),
        source_ref=card.ref,
        target_schema=target_schema,
    )
    effective_target_schema = (
        target_schema
        if target_schema is not None
        else getattr(program, "target_schema", None)
    )
    selected_modes = (
        list(
            canonical_modes(
                effective_target_schema,
                request.modes,
                require_modes=bool(available_modes(effective_target_schema)),
            )
        )
        if effective_target_schema is not None
        else []
    )
    snapshots = {ref: host._target_snapshot(ref) for ref in targets}
    tap_refs: list[str] = []
    for tap_ref in selected_dict.get("selected_tap_cost_cards", []):
        tap_card = host._resolve_object(
            request.actor,
            str(tap_ref),
            zones={"battlefield"},
            owned_only=False,
        )
        if tap_card.controller != request.actor or tap_card.tapped:
            raise CastProposalError(
                f"{tap_card.ref} is no longer available for a tap cost",
                status="unpayable",
                reason="tap_cost_unpayable",
            )
        tap_refs.append(tap_card.ref)
    return (
        selected_dict,
        targets,
        target_groups,
        snapshots,
        tap_refs,
        target_schema,
        selected_modes,
    )


def _aura_spell_target_schema(
    *,
    type_line: str,
    enchant_spec: EnchantSpec | None,
    reviewed_target_schema: Mapping[str, Any] | None = None,
) -> Mapping[str, Any] | None:
    del reviewed_target_schema
    if not is_aura_type_line(type_line):
        return None
    if enchant_spec is None:
        raise CastProposalError(
            "This Aura lacks one trusted compiled Enchant descriptor",
            status="unresolved",
            reason="unresolved_aura_enchant_descriptor",
        )
    return enchant_spec.target_schema()


def build_cast_proposal(
    host: CastProposalHost,
    request: CastProposalRequest,
) -> CastProposal:
    """Build a complete immutable executable cast plan without mutation."""

    if not request.ignore_priority:
        host._check_priority(request.actor)
    response = request.response()
    if response.get("semantic_key") is not None:
        raise CastProposalError(
            "Pilots cannot select semantic program identifiers",
            reason="pilot_semantic_key",
        )
    card = _resolve_cast_source(host, request)
    record = host.card_record(card)
    if not record:
        raise CastProposalError("Cannot cast a custom token", reason="custom_token")
    face = (
        None
        if request.cast_method in FACE_DOWN_CAST_METHODS
        else _cast_face(host, request, record)
    )
    _validate_offer_fingerprint(host, request, card)
    (
        type_line,
        mana_cost,
        commander_tax,
        semantic_key,
        program,
        selected,
    ) = _cast_program_and_cost(host, request, card, record, face)
    enchant_spec = host._compiled_enchant_spec(
        card,
        face_name=(str(face.get("name") or "") if face else None),
    )
    aura_target_schema = _aura_spell_target_schema(
        type_line=type_line,
        enchant_spec=enchant_spec,
        reviewed_target_schema=(
            selected.to_dict().get("target_schema")
            if isinstance(
                selected.to_dict().get("target_schema"), Mapping
            )
            else getattr(program, "target_schema", None)
        ),
    )
    (
        selected_dict,
        targets,
        target_groups,
        snapshots,
        tap_refs,
        target_schema,
        selected_modes,
    ) = _cast_targets_and_tap_costs(
        host,
        request,
        card,
        program,
        selected,
        aura_target_schema,
    )
    requirements = host._mana_vector(selected_dict["requirements"])
    if selected_dict.get("cast_type_line"):
        type_line = str(selected_dict["cast_type_line"])
    proposal = CastProposal(
        seat=request.actor,
        card_ref=card.ref,
        object_id=card.object_id,
        origin=card.zone,
        face=str(face.get("name")) if face else None,
        type_line=type_line,
        semantic_key=semantic_key,
        cost_option_id=selected.option_id,
        requirements=freeze_json(requirements),
        targets=tuple(targets),
        target_groups=freeze_json(target_groups),
        target_snapshots=freeze_json(snapshots),
        tap_cost_refs=tuple(tap_refs),
        details=freeze_json(
            {
                "selected_cost_option": selected_dict,
                "selected_modes": selected_modes,
                "target_schema_override": target_schema,
                "aura_spell": aura_target_schema is not None,
                "aura_enchant_spec": (
                    enchant_spec_to_dict(enchant_spec)
                    if enchant_spec is not None
                    else None
                ),
                "commander_tax": commander_tax,
                "mana_cost": mana_cost,
                "during_resolution": request.during_resolution,
                "cast_method": request.cast_method,
                **(
                    {
                        (
                            "morph"
                            if request.cast_method == MORPH_CAST_METHOD
                            else "face_down_method"
                        ): compiled_fixed_mana_face_down_method_spec(
                            host,
                            card,
                            method=request.cast_method,
                        ).to_dict()
                    }
                    if request.cast_method in FACE_DOWN_CAST_METHODS
                    else {}
                ),
            }
        ),
    )
    return proposal


def _cast_offer_payload(
    host: CastProposalHost,
    seat: str,
    card: Any,
    record: Any,
    face: Mapping[str, Any] | None,
    options: tuple[Any, ...],
    program: Any,
    aura_target_schema: Mapping[str, Any] | None,
) -> tuple[dict[str, Any], tuple[Any, ...]]:
    public_options = []
    legal_options = []
    for option in options:
        raw = option.to_dict()
        target_spec = (
            dict(raw["target_schema"])
            if isinstance(raw.get("target_schema"), Mapping)
            else dict(aura_target_schema)
            if aura_target_schema is not None
            else program.target_schema if program is not None else None
        )
        public_schema = None
        if target_spec is not None:
            public_schema = host._public_target_schema(
                seat, target_spec, source_ref=card.ref
            )
            if public_schema is None:
                continue
        projected = {
            key: copy.deepcopy(value)
            for key, value in raw.items()
            if key in {"id", "kind", "requirements", "choice_schema", "label"}
        }
        if public_schema is not None:
            projected["target_schema"] = public_schema
        public_options.append(projected)
        legal_options.append(option)
    cast_name = str(face.get("name") or "") if face else record.name
    cast_cost = str(face.get("mana_cost") or "") if face else record.mana_cost
    payload: dict[str, Any] = {
        "kind": "cast",
        "card": card.ref,
        "from": card.zone,
        "cost": cast_cost,
        "auto_pay": True,
        "cost_options": public_options,
    }
    if face is not None:
        payload["face"] = cast_name
    if len(public_options) == 1 and public_options[0].get("target_schema"):
        payload["target_schema"] = copy.deepcopy(
            public_options[0]["target_schema"]
        )
    return payload, tuple(legal_options)


def build_cast_offer(
    host: CastProposalHost,
    seat: str,
    card: Any,
    *,
    cast_method: str | None = None,
) -> CastProposalResult:
    """Use the casting proposal queries to advertise one executable action."""

    record = host.card_record(card)
    if not record:
        return CastProposalResult("unavailable", "custom_token")
    method_spec = (
        compiled_fixed_mana_face_down_method_spec(
            host,
            card,
            method=cast_method,
        )
        if cast_method in FACE_DOWN_CAST_METHODS
        else None
    )
    if cast_method is not None and method_spec is None:
        return CastProposalResult(
            "unavailable",
            (
                "morph_contract_unavailable"
                if cast_method == MORPH_CAST_METHOD
                else "face_down_method_contract_unavailable"
            ),
        )
    front = record.faces[0] if record.faces else None
    type_line = (
        "Creature"
        if method_spec is not None
        else str(front.get("type_line") or "") if front else record.type_line
    )
    if type_line_has_card_type(type_line, "land"):
        return CastProposalResult("unavailable", "land_not_spell")
    face_name = str(front.get("name") or "") if front else None
    if not cast_timing_is_legal(
        host.state,
        seat,
        type_line,
        ()
        if method_spec is not None
        else compiled_cast_timing_permissions(
            host,
            card,
            face_name=face_name,
        ),
    ):
        return CastProposalResult("unavailable", "timing")
    semantic_key = (
        f"builtin:{cast_method}-face-down"
        if method_spec is not None
        else _spell_semantic_key(record, front)
    )
    program = None if method_spec is not None else host.semantics.get(semantic_key)
    enchant_spec = host._compiled_enchant_spec(
        card,
        face_name=(
            None
            if method_spec is not None
            else str(front.get("name") or "") if front else None
        ),
    )
    try:
        aura_target_schema = _aura_spell_target_schema(
            type_line=type_line,
            enchant_spec=enchant_spec,
            reviewed_target_schema=getattr(
                program, "target_schema", None
            ),
        )
    except CastProposalError as exc:
        return CastProposalResult(exc.status, exc.reason)
    if method_spec is None and host.state.config.semantic_policy == "trusted_only" and (
        (program is not None and not host.semantic_program_is_current_trusted(program))
        or (program is None and not host._trusted_generic_spell(record))
    ):
        return CastProposalResult("unresolved", "semantic_policy_requires_trusted")
    options = tuple(
        CastCostOption.from_dict(value)
        for value in host._cast_cost_options(
            seat,
            card,
            program,
            hint=True,
            alternative_base=(
                {
                    "id": str(cast_method),
                    "kind": "alternate",
                    "label": f"Cast face down using {str(cast_method).title()}",
                    "requirements": {
                        "GENERIC": 3,
                        **{color: 0 for color in "WUBRGC"},
                    },
                }
                if method_spec is not None
                else None
            ),
            cast_type_line=type_line if method_spec is not None else None,
            suppress_source_costs=method_spec is not None,
        )
    )
    if not options:
        return CastProposalResult("unpayable", "mandatory_cost_unpayable")
    payload, legal = _cast_offer_payload(
        host,
        seat,
        card,
        record,
        None if method_spec is not None else front,
        options,
        program,
        aura_target_schema,
    )
    if not legal:
        return CastProposalResult("unavailable", "mandatory_target_unavailable")
    if method_spec is not None:
        payload["cost"] = "{3}"
        payload["cast_method"] = cast_method
    cast_name = str(front.get("name") or "") if front else record.name
    cast_cost = str(front.get("mana_cost") or "") if front else record.mana_cost
    offer = ActionOffer(
        action_id=(
            f"cast-{cast_method}:{card.ref}"
            if method_spec is not None
            else f"cast:{card.ref}"
        ),
        action="cast",
        seat=seat,
        label=(
            f"Cast {cast_name} face down — {{3}}"
            if method_spec is not None
            else f"Cast {cast_name} — {cast_cost}"
            if cast_cost
            else f"Cast {cast_name}"
        ),
        expiry_revision=host.state.revision,
        payload=freeze_json(payload),
    )
    return CastProposalResult(
        status="payable",
        reason="payable",
        offer=offer,
        cost_options=legal,
    )


__all__ = [
    "CastProposalHost",
    "build_cast_offer",
    "build_cast_proposal",
]
