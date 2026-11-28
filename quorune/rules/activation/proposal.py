from __future__ import annotations

from ...target_source import ability_targeting_source

from collections.abc import Mapping
from dataclasses import replace
from typing import Any, Protocol, Sequence

from ...abilities import ActivatedAbility, choose_ability
from ...activation_condition_model import public_activation_program_timing
from ...announced_damage import announced_allocation_details
from .pricing import (
    payable_activation_mana_options,
    select_activation_mana_option,
)
from ...creature_power_damage import (
    capture_creature_power_damage_source_lki,
    creature_power_damage_needs_source_lki,
    creature_power_damage_source_available,
    CREATURE_POWER_DAMAGE_LKI_CONTEXT,
)
from ...errors import GameRuleError
from ...haste import summoning_sickness_prohibits_tap_or_untap_cost
from ...replacement.immutable import thaw_value
from ...station import station_cost_choice
from ...targets import available_modes
from ..action_proposals import ActionOffer, ActivationProposal, freeze_json, thaw_json
from ..modal_selection import canonical_modes
from ..random_activation_target import choose_random_activated_target, has_random_activation_target
from .model import (
    ActivationProposalError,
    ActivationProposalRequest,
    ActivationProposalResult,
)
from .offer_payload import activation_offer_cost_hint
from .query import activation_actor_allowed
from .equip_permissions import (
    EQUIP_PAYMENT_FIELD, EQUIP_PAYMENT_OPTIONS, PRINTED_EQUIP_PAYMENT, ZERO_EQUIP_PAYMENT,
    equip_instant_timing, selected_equip_price, zero_equip_price,
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
        acting_source: Any = None,
        target_schema: Mapping[str, Any] | None = None,
        announcing_targets: bool = False,
        record_optimization: bool = True,
    ) -> tuple[list[str], Any]: ...

    def _target_snapshot(self, ref: str) -> Mapping[str, Any]: ...

    def _fetch_context(
        self, seat: str, ability: ActivatedAbility, response: Mapping[str, Any], *, program: Any = None,
    ) -> dict[str, Any]: ...

    def _effective_card_data(self, card: Any) -> Mapping[str, Any]: ...

    def _type_parts(
        self, type_line: str
    ) -> tuple[set[str], set[str], set[str]]: ...

    def _may_activate_creature_as_haste(self, seat: str, source: Any) -> bool: ...

    def _legendary_creatures_controlled(self, seat: str) -> int: ...

    def semantic_program_is_current_trusted(self, program: Any) -> bool: ...

    def _public_target_schema(
        self,
        seat: str,
        schema: Mapping[str, Any],
        *,
        source_ref: str,
        acting_source: Any = None,
        announcing_targets: bool = False,
    ) -> Mapping[str, Any] | None: ...

    def _crew_threshold(self, ability: ActivatedAbility) -> int | None: ...

    def _crew_candidates(self, seat: str, source: Any) -> list[Any]: ...

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
    if source.zone != "battlefield" and source.owner != request.actor:
        raise ActivationProposalError(
            "You do not own that nonbattlefield ability source",
            reason="wrong_owner",
        )
    return source


def _select_ability(
    host: ActivationProposalHost,
    source: Any,
    selector: str | int | None,
    *,
    actor: str,
) -> ActivatedAbility:
    abilities = tuple(
        ability
        for ability in host._activated_abilities(source)
        if activation_actor_allowed(host, actor, source, ability)
    )
    if not abilities and source.zone == "battlefield" and source.controller != actor:
        raise ActivationProposalError(
            "You do not control that ability source",
            reason="wrong_controller",
        )
    try:
        return choose_ability(abilities, selector)
    except ValueError as exc:
        raise ActivationProposalError(str(exc), reason="ability_selection") from exc


def activation_result_is_represented(host, source, ability):
    """A structural cost catalogue never stands in for an executable result."""
    from .resolution import is_builtin_activation_semantic
    key = host._semantic_key_for_ability(source, ability)
    return (ability.mana_ability or host.semantics.get(key) is not None
        or is_builtin_activation_semantic(key) or host._crew_threshold(ability) is not None
        or station_cost_choice(ability) is not None or bool(ability.library_search_types))


def _payable_activation_price(host, seat, source, ability, priced_ability=None):
    if priced_ability is None or priced_ability is ability:
        return host._ability_availability(seat, source, ability)
    from .availability import activation_availability

    return activation_availability(host, seat, source, ability, priced_ability=priced_ability)


def _validate_activation_cost_contract(
    host: ActivationProposalHost,
    request: ActivationProposalRequest,
    source: Any,
    ability: ActivatedAbility,
    *,
    priced_ability: ActivatedAbility | None = None,
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
    from ...granted_activation_model import GRANTING_ATTACHMENT_PAYMENT_FIELD
    payment = ability.granting_attachment_payment
    program = host.semantics.get(host._semantic_key_for_ability(source, ability))
    try:
        public_timing = public_activation_program_timing(program)
    except ValueError as exc:
        raise ActivationProposalError(
            str(exc), status="unresolved", reason="unresolved_public_activation_permission",
        ) from exc
    if public_timing is not None and public_timing.has_step_window and not public_timing.permits_step(
        phase=host.state.phase, step=host.state.step,
        actor_is_active=request.actor == host.state.active_player,
    ):
        raise ActivationProposalError(
            "The current step does not allow this activation", reason="public_activation_step_window",
        )
    from .paid_characteristic_results import granted_paid_activation_price_matches
    if not granted_paid_activation_price_matches(program, ability):
        raise ActivationProposalError(
            'Granted paid creature result has a different effective activation price',
            status='unresolved', reason='unresolved_granted_paid_stat_price',
        )
    cost = program.cost_schema if program is not None else None
    declared = cost.get(GRANTING_ATTACHMENT_PAYMENT_FIELD) if isinstance(cost, Mapping) else None
    if payment is not None or declared is not None:
        if (payment is None or declared != {'kind': payment.kind.value} or program is None
                or not host.semantic_program_is_current_trusted(program)):
            raise ActivationProposalError('Granting Equipment payment requires its trusted activation body',
                                          status='unresolved', reason='untrusted_granting_equipment_payment')
    if host.state.config.semantic_policy == 'trusted_only' and not activation_result_is_represented(host, source, ability):
        raise ActivationProposalError('This ability has no compiled result semantics',
            status='unresolved', reason='unresolved_result_semantics')
    status, reason = _payable_activation_price(host, request.actor, source, ability, priced_ability)
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
    if ability.sorcery_speed and not equip_instant_timing(host, request.actor, source, ability):
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
    if ability.untap_source:
        from ..untap_permissions import untap_is_permitted
        if not untap_is_permitted(host,source):
            raise ActivationProposalError('An absolute rule prohibits this untap cost',status='unpayable',reason='untap_cost_prohibited')


def _activation_effect_context(
    host: ActivationProposalHost,
    source: Any,
    program: Any,
    target_schema: Mapping[str, Any] | None,
    targets: Sequence[str],
    response: Mapping[str, Any],
) -> dict[str, Any]:
    try:
        allocation = announced_allocation_details(target_schema, targets, response)
    except ValueError as exc:
        raise ActivationProposalError(str(exc), reason="invalid_damage_allocation") from exc
    if program is None:
        return allocation
    source_lki = capture_creature_power_damage_source_lki(
        host,
        source,
        program.effects,
    )
    if creature_power_damage_needs_source_lki(program.effects) and source_lki is None:
        raise ActivationProposalError(
            "Creature-power damage source is unavailable",
            status="unavailable",
            reason="creature_power_source_unavailable",
        )
    from ...scalar_effect_amounts import scalar_source_context
    from ...token_copy_references import copy_source_context
    from ..stack_damage_sources import fixed_damage_source_context
    return (
        allocation
        | scalar_source_context(host, source, program.effects)
        | copy_source_context(host, source, program.effects)
        | fixed_damage_source_context(host, source, (program.effects, program.target_schema))
        | ({CREATURE_POWER_DAMAGE_LKI_CONTEXT: source_lki} if source_lki is not None else {})
    )


def _collect_activation_payment(host, actor, source, ability, response, targets):
    """Collect the same announced mana and object choices for proposal and commit."""
    mana_option = None
    announced_x = None
    if ability.mana_cost_options:
        try:
            mana_option = select_activation_mana_option(
                host,
                actor,
                source,
                ability,
                response.get("cost_option"),
                targets=targets,
            )
        except ValueError as exc:
            raise ActivationProposalError(
                str(exc),
                status="unpayable",
                reason="activation_mana_cost_option",
            ) from exc
        requirements = dict(mana_option.requirements)
    else:
        from .x_costs import activation_requirements, validate_activation_x_payment
        requirements, announced_x = activation_requirements(host, actor, source, ability, response, targets=targets)
        from ..announced_artifact_sacrifice_return import has_announced_artifact_sacrifice
        if has_announced_artifact_sacrifice(ability):
            from .announced_artifact_payment import validate_announced_artifact_payment
            validate_announced_artifact_payment(host, actor, source, ability, response, requirements, announced_x)
        elif announced_x is not None:
            try: validate_activation_x_payment(host,actor,source,ability,requirements)
            except ValueError as exc: raise ActivationProposalError(str(exc),status='unpayable',reason='activation_x_unpayable') from exc
    waterbend_details={}
    if not ability.mana_cost_options:
        from .pricing import activation_price_details
        waterbend_details.update(activation_price_details(host,actor,source,ability,response,targets=targets))
    if ability.waterbend is not None:
        from ..waterbend_payments import activation_waterbend_plan
        try:payment=activation_waterbend_plan(host,actor,source,ability,requirements,response,hint=False)
        except ValueError as exc:raise ActivationProposalError(str(exc),reason='stale_waterbend_payment') from exc
        if payment is None:raise ActivationProposalError('Waterbend payment is not payable',status='unpayable',reason='waterbend_unpayable')
        requirements,_,waterbend,_=payment;waterbend_details['waterbend_payment']=waterbend.to_dict()
    from .variable_colored_reveal import variable_colored_reveal_proposal_details
    from .announced_selected_zone_cost import announced_selected_zone_cost_details
    from .commander_identity_life import commander_identity_life_details

    details = {
        **waterbend_details,
        **variable_colored_reveal_proposal_details(host, actor, ability, announced_x, response),
        **announced_selected_zone_cost_details(host, actor, source, ability, announced_x, response),
        **commander_identity_life_details(host, actor, ability),
    }
    return requirements, announced_x, mana_option, details


def build_activation_proposal(
    host: ActivationProposalHost,
    request: ActivationProposalRequest,
    *,
    record_optimization: bool = True,
) -> ActivationProposal:
    """Build an immutable activation plan, optionally counting target evaluation metrics."""

    host._check_priority(request.actor)
    response = request.response()
    if response.get("semantic_key") is not None:
        raise ActivationProposalError(
            "Pilots cannot select semantic program identifiers",
            reason="pilot_semantic_key",
        )
    source = _resolve_activation_source(host, request)
    ability = _select_ability(host, source, request.ability_selector, actor=request.actor)
    from ...activation_x import chosen_activation_x
    try: announced_x = chosen_activation_x(ability, response)
    except ValueError as exc: raise ActivationProposalError(str(exc),reason='invalid_activation_x') from exc
    semantic_key = host._semantic_key_for_ability(source, ability)
    program = host.semantics.get(semantic_key)
    random_target = has_random_activation_target(program)
    if random_target and response.get("targets"):
        raise ActivationProposalError(
            "This activation chooses its target at random", reason="random_target_override",
        )
    supplied_fingerprint = response.get("proposal_fingerprint")
    advertised = None
    if supplied_fingerprint or random_target:
        advertised = build_activation_offer(host, request.actor, source, ability)
    if supplied_fingerprint:
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
    equip_payment = response.get(EQUIP_PAYMENT_FIELD)
    priced_ability = selected_equip_price(host, request.actor, source, ability, equip_payment)
    if priced_ability is not ability and any(field in response for field in ("cost_cards", "cost_option")):
        raise ActivationProposalError("This Equip alternative does not pay the printed Equip cost", reason="equip_payment_fields")
    _validate_activation_cost_contract(host, request, source, ability, priced_ability=priced_ability)
    _validate_activation_timing_costs(host, request, source, priced_ability)
    target_schema = (
        thaw_value(ability.target_schema)
        if ability.target_schema is not None
        else getattr(program, "target_schema", None)
    )
    from .graveyard_copy import bind_activation_targets
    try:
        target_schema = bind_activation_targets(program, ability, target_schema, response, announced_x=announced_x,
            target_limit=len(host.state.cards)+len(host.state.players)+len(host.state.stack))
    except ValueError as exc:
        raise ActivationProposalError(str(exc), reason="invalid_announced_target_count") from exc
    target_submission = response.get("targets")
    if random_target:
        if advertised.offer is None:
            raise ActivationProposalError(
                "Random activation has no available target or payment", status=advertised.status,
                reason=advertised.reason,
            )
        target_submission = [choose_random_activated_target(
            host, request.actor, source, ability, advertised.random_target_refs,
        )]
    targets, target_groups = host._validate_semantic_targets(
        request.actor,
        program,
        host._normalize_target_submission(target_submission),
        modes=list(request.modes),
        acting_source=ability_targeting_source(host,source), source_ref=source.ref,
        target_schema=target_schema,
        announcing_targets=True,
        record_optimization=record_optimization,
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
    context = host._fetch_context(request.actor, ability, response, program=program)
    context.update(_activation_effect_context(host, source, program, effective_target_schema, targets, response))
    requirements, announced_x, mana_option, price_details = _collect_activation_payment(
        host, request.actor, source, priced_ability, response, tuple(targets),
    )
    from ..hand_artifact_mana import artifact_hand_activation_details
    try:
        artifact_hand_details = artifact_hand_activation_details(host, request.actor, source, ability, response)
    except GameRuleError as exc:
        raise ActivationProposalError(str(exc), reason="invalid_artifact_hand_mana_selection") from exc
    proposal = ActivationProposal(
        seat=request.actor,
        source_ref=source.ref,
        source_object_id=source.object_id,
        source_zone=source.zone,
        ability_id=ability.ability_id,
        semantic_key=(
            "builtin:fetch_land"
            if context.get("builtin") == "fetch_land"
            else semantic_key
        ),
        mana_ability=ability.mana_ability,
        requirements=freeze_json(requirements),
        targets=tuple(targets),
        target_groups=freeze_json(target_groups),
        target_snapshots=freeze_json(snapshots),
        details=freeze_json(
            {
                "selected_modes": selected_modes,
                "target_schema_override": target_schema,
                "builtin_context": context,
                **({'announced_x':announced_x} if announced_x is not None else {}),
                **price_details,
                **({EQUIP_PAYMENT_FIELD: equip_payment} if equip_payment == ZERO_EQUIP_PAYMENT else {}),
                **artifact_hand_details,
                **(
                    {
                        "mana_cost_option": mana_option.to_dict(),
                    }
                    if mana_option is not None
                    else {}
                ),
            }
        ),
    )
    return proposal


def _activation_mana_option_label(option: Any) -> str:
    parts = []
    generic = int(option.requirements.get("GENERIC", 0))
    mana = (f"{{{generic}}}" if generic else "") + "".join(
        f"{{{color}}}" * int(option.requirements.get(color, 0))
        for color in "WUBRGC"
    )
    if mana:
        parts.append(mana)
    if option.life_payment:
        parts.append(f"{option.life_payment} life")
    if option.snow_payment:
        parts.append(f"{option.snow_payment} snow mana")
    return "Pay " + ", ".join(parts)


def _activation_choice_schema(host, seat, source, ability, hint):
    choices = _activation_intrinsic_choice_schema(host, seat, source, ability, hint)
    if not (hint.get("m") or hint.get("mana_options") or ability.chooses_activation_x):
        return choices
    from ...mana_restrictions import ability_mana_spend_context
    from ..hand_artifact_mana import artifact_hand_mana_payment_fields

    cost_type = str(host._effective_card_data(source).get("type_line") or "")
    fields = artifact_hand_mana_payment_fields(
        host, seat, spend_context=ability_mana_spend_context(cost_type, source_zone=source.zone),
        exclude_sources={source.object_id} if ability.tap_source or ability.untap_source else set(),
    )
    return {**(choices or {}), **fields} if fields else choices


def _activation_intrinsic_choice_schema(
    host: ActivationProposalHost,
    seat: str,
    source: Any,
    ability: ActivatedAbility,
    hint: Mapping[str, Any],
) -> dict[str, Any] | None:
    from ..selected_counter_costs import selected_counter_cost_choice_schema
    from ...artifact_hand_mana_model import ARTIFACT_HAND_MANA_REVEAL_FIELD, is_artifact_hand_mana
    if is_artifact_hand_mana(ability):
        from ..hand_artifact_mana import artifact_hand_mana_choice
        return {ARTIFACT_HAND_MANA_REVEAL_FIELD: artifact_hand_mana_choice(host, seat)}
    from ...paid_activation_card_types import has_bounded_sacrifice
    if has_bounded_sacrifice(ability):
        from .bounded_type_sacrifice import bounded_sacrifice_choice
        return bounded_sacrifice_choice(host, seat)
    from ..forage import forage_choice_schema, has_forage_cost

    forage = has_forage_cost(ability)
    if forage:
        cost_choice = forage_choice_schema(host, seat)
        if cost_choice is None or not ability.mana_ability:
            return cost_choice
    else:
        counter_choice = selected_counter_cost_choice_schema(ability, hint, host=host, actor=seat)
        from .source_counter_choices import source_counter_cost_choice_schema

        cost_choice = counter_choice if counter_choice is not None else source_counter_cost_choice_schema(source, ability)
    if ability.mana_ability:
        from ...triggered_mana import triggered_mana_offer_schema
        from ...triggered_mana_model import TRIGGERED_MANA_OPTION_FIELD

        triggered_choice = triggered_mana_offer_schema(host, seat, source, ability)
        if triggered_choice is not None:
            return {**(cost_choice or {}), TRIGGERED_MANA_OPTION_FIELD: triggered_choice}
    if cost_choice is not None and not forage:
        return cost_choice
    if ability.mana_ability:
        from ...public_quantity_mana_runtime import public_quantity_mana_choice_schema
        quantity_choice = public_quantity_mana_choice_schema(host, seat, source, ability)
        if quantity_choice is not None:
            return quantity_choice
        modes = host._mana_modes_for_ability(seat, source, ability)
        if len(modes) > 1:
            return {
                **(cost_choice or {}),
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


def _creature_power_damage_offer_result(
    host: ActivationProposalHost,
    source: Any,
    program: Any,
) -> ActivationProposalResult | None:
    if program is None or creature_power_damage_source_available(
        host,
        source,
        program.effects,
    ):
        return None
    return ActivationProposalResult(
        "unavailable",
        "creature_power_source_unavailable",
    )


def _build_priced_activation_offer(
    host: ActivationProposalHost,
    seat: str,
    source: Any,
    ability: ActivatedAbility,
    *,
    priced_ability: ActivatedAbility | None = None,
) -> ActivationProposalResult:
    """Advertise one activation at the selected quotation."""

    request = ActivationProposalRequest(
        actor=seat,
        source_ref=source.ref,
        zones=(source.zone,),
        ability_selector=ability.ability_id,
    )
    try:
        resolved = _resolve_activation_source(host, request)
        selected = _select_ability(host, resolved, request.ability_selector, actor=seat)
        offer_price = priced_ability or selected
        _validate_activation_cost_contract(host, request, resolved, selected, priced_ability=offer_price)
        _validate_activation_timing_costs(host, request, resolved, offer_price)
    except ActivationProposalError as exc:
        return ActivationProposalResult(exc.status, exc.reason)
    semantic_key = host._semantic_key_for_ability(source, selected)
    program = host.semantics.get(semantic_key)
    random_target = has_random_activation_target(program)
    if (
        program is not None
        and (random_target or host.state.config.semantic_policy == "trusted_only")
        and not host.semantic_program_is_current_trusted(program)
    ):
        return ActivationProposalResult("unresolved", "semantic_policy_requires_trusted")
    unavailable = _creature_power_damage_offer_result(host, source, program)
    if unavailable is not None:
        return unavailable
    target_schema = (
        thaw_value(selected.target_schema)
        if selected.target_schema is not None
        else program.target_schema if program is not None else None
    )
    unbound_target_schema = target_schema
    from ...activation_x import bind_activation_target_cardinality
    try:
        target_schema = bind_activation_target_cardinality(selected, target_schema, {}, hint=True,
            target_limit=len(host.state.cards)+len(host.state.players)+len(host.state.stack))
    except ValueError:
        return ActivationProposalResult("unavailable", "invalid_announced_target_count")
    public_schema = None
    if target_schema is not None:
        from .graveyard_copy import activation_public_targets

        public_schema = activation_public_targets(host, seat, source, selected, program, target_schema)
        if public_schema is None:
            return ActivationProposalResult("unavailable", "mandatory_target_unavailable")
    cost_hint = activation_offer_cost_hint(host, seat, source, offer_price, selected=selected)
    if cost_hint is None:
        return ActivationProposalResult('unpayable', 'granting_equipment_payment_unpayable')
    hint, attachment_label = cost_hint
    random_refs = tuple(public_schema["legal_refs"]) if random_target and public_schema else ()
    from .pricing import activation_priced_offer_payload
    try:
        payload, public_schema = activation_priced_offer_payload(host, seat, source, offer_price, hint, public_schema, program=program)
    except ValueError:
        if not random_target:
            raise
        return ActivationProposalResult("unpayable", "random_activation_payment_unavailable")
    if offer_price.mana_cost_options:
        payload["cost_options"] = [
            {
                **option.to_dict(),
                "label": _activation_mana_option_label(option),
            }
            for option in payable_activation_mana_options(
                host,
                seat,
                source,
                offer_price,
            )
        ]
    if selected.mana_ability:
        payload["mana_ability"] = True
    choice_schema = _activation_choice_schema(
        host, seat, source, offer_price, hint
    )
    if selected.waterbend is not None:
        from ..waterbend_payments import activation_waterbend_plan
        from .x_costs import activation_requirements
        requirements,_=activation_requirements(host,seat,source,selected)
        payment=activation_waterbend_plan(host,seat,source,selected,requirements,{},hint=True)
        if payment is None:return ActivationProposalResult('unpayable','waterbend_unpayable')
        choice_schema={**(choice_schema or {}),**payment[3]}
    if selected.chooses_activation_x:
        from .graveyard_copy import announced_activation_choice

        x_choice = announced_activation_choice(public_schema)
        if selected.announced_x_minimum:
            x_choice.update(minimum=selected.announced_x_minimum, default=selected.announced_x_minimum)
        if selected.loyalty_x:
            x_choice["maximum"] = source.counters.get("loyalty", 0)
        choice_schema = {**(choice_schema or {}), "x": x_choice}
        from .announced_selected_zone_cost import variable_object_activation_offer

        choice_schema.update(variable_object_activation_offer(host, seat, selected, hint, choice_schema["x"]))
    from ..announced_artifact_sacrifice_return import has_announced_artifact_sacrifice

    if has_announced_artifact_sacrifice(selected):
        choice, = hint["choose_cost"]
        maximum = choice_schema["x"]["maximum"]
        choice.update(minimum=1, maximum=maximum)
        choice_schema["cost_cards"] = {
            "type": "object_ref_array", "legal_refs": choice["legal_refs"], "zone": "battlefield",
            "minimum": 1, "maximum": maximum, "distinct": True,
            "count_parameter": "x", "label": "Sacrifice X artifacts you control",
        }
    if not offer_price.mana_cost_options:
        from .pricing import activation_price_choice_schema
        price_choices=activation_price_choice_schema(host,seat,source,offer_price)
        if price_choices:choice_schema={**(choice_schema or {}),**price_choices}
    if choice_schema is not None:
        payload["choice_schema"] = choice_schema
    if public_schema is not None:
        if isinstance(unbound_target_schema, Mapping) and 'count_parameter' in unbound_target_schema:
            from ...target_cardinality import link_announced_public_bounds
            public_schema = link_announced_public_bounds(public_schema, unbound_target_schema['count_parameter'])
        if not random_target:
            payload["target_schema"] = public_schema
    offer = ActionOffer(
        action_id=f"activate:{source.ref}:{selected.ability_id}",
        action="activate",
        seat=seat,
        label=f"{host.display_name(source.object_id)} — {selected.effect_text}{attachment_label}",
        expiry_revision=host.state.revision,
        payload=freeze_json(payload),
    )
    return ActivationProposalResult("payable", "payable", offer=offer, random_target_refs=random_refs)


def build_activation_offer(host, seat, source, ability):
    """Advertise the printed price and, when payable, the first Equip alternative."""
    printed = _build_priced_activation_offer(host, seat, source, ability)
    alternate_price = zero_equip_price(host, seat, source, ability)
    if alternate_price is None:
        return printed
    alternate = _build_priced_activation_offer(host, seat, source, ability, priced_ability=alternate_price)
    if alternate.offer is None:
        return printed

    variants = []
    for result, name, label in (
        (printed, PRINTED_EQUIP_PAYMENT, "Pay the Equip cost"),
        (alternate, ZERO_EQUIP_PAYMENT, "Use the first Equip alternative"),
    ):
        if result.offer is not None:
            payload = thaw_json(result.offer.payload)
            variants.append({"id": name, "label": label, **{
                field: payload[field] for field in ("cost_summary", "cost_options", "choice_schema", "target_schema", "target_costs")
                if field in payload
            }})
    chosen = printed if printed.offer is not None else alternate
    payload = thaw_json(chosen.offer.payload)
    payload.pop("cost_options", None)
    payload.pop("choice_schema", None)
    payload[EQUIP_PAYMENT_OPTIONS] = variants
    return replace(chosen, offer=replace(chosen.offer, payload=freeze_json(payload)))


__all__ = [
    "ActivationProposalHost",
    "build_activation_offer",
    "build_activation_proposal",
]
