from __future__ import annotations

from hashlib import sha256
from typing import Any, Protocol

from ...abilities import ActivatedAbility, CostChoice
from ...activation_condition_model import (
    PUBLIC_ACTIVATION_CAPABILITY, PublicActivationTiming, public_activation_program_timing,
)
from ...ability_fragments import (
    canonical_ability_fragments,
    granted_activated_specs,
)
from ...card_overrides.game_record_v3 import (
    historical_game_record_v3_activated_abilities,
)
from ...fixed_mana_abilities import FixedManaMode
from ...util import normalize_mana_bundle


class ActivatedAbilityQueryHost(Protocol):
    semantics: Any

    def _effective_card_data(self, card: Any) -> dict[str, Any]: ...


def activated_abilities(
    host: ActivatedAbilityQueryHost,
    card: Any,
) -> tuple[ActivatedAbility, ...]:
    """Return compiler-pinned, intrinsic, and typed granted abilities."""

    data = host._effective_card_data(card)
    raw_abilities = data.get("activated_abilities", ())
    if not isinstance(raw_abilities, (list, tuple)):
        raise ValueError("activated_abilities must be an array")
    abilities = [
        value
        if isinstance(value, ActivatedAbility)
        else ActivatedAbility.from_dict(value)
        for value in raw_abilities
    ]
    if (
        not abilities
        and bool(
            getattr(
                getattr(host, "semantics", None),
                "runtime_handler_compatibility_enabled",
                False,
            )
        )
    ):
        abilities.extend(
            historical_game_record_v3_activated_abilities(card, data)
        )
    abilities.sort(key=lambda ability: ability.line_index)
    abilities.extend(_typed_granted_abilities(data))
    return tuple(abilities)


def activation_actor_allowed(host, seat: str, source: Any, ability: ActivatedAbility) -> bool:
    """Keep printed public permission on the current trusted result program."""
    if source.zone not in ability.zones:
        return False
    if source.zone != "battlefield":
        return source.owner == seat
    if source.phased_out:
        return False
    if source.controller == seat:
        return True
    if ability.mana_ability:
        return False
    program = host.semantics.get(host._semantic_key_for_ability(source, ability))
    if program is None or PUBLIC_ACTIVATION_CAPABILITY not in program.capability_dependencies:
        return False
    try:
        timing = public_activation_program_timing(program)
    except ValueError:
        return False
    return (timing is not None
            and ability.sorcery_speed == (timing is PublicActivationTiming.SORCERY)
            and host.semantic_program_is_current_trusted(program))


def activation_offer_candidates(host, seat: str):
    player = host.state.players[seat]
    for zone in ("battlefield", "hand", "graveyard", "exile", "command"):
        for object_id in player.zones[zone]:
            source = host.state.cards[object_id]
            if (zone == "battlefield" and (source.phased_out or source.controller != seat)
                    or zone != "battlefield" and source.owner != seat):
                continue
            for ability in host._activated_abilities(source):
                if activation_actor_allowed(host, seat, source, ability):
                    yield source, ability

    if not any(PUBLIC_ACTIVATION_CAPABILITY in program.capability_dependencies
               for program in host.semantics.programs_for_event("activate")):
        return
    for controller in host.active_seats:
        if controller == seat:
            continue
        for object_id in host.state.players[controller].zones["battlefield"]:
            source = host.state.cards[object_id]
            if source.zone != "battlefield" or source.controller != controller or source.phased_out:
                continue
            for ability in host._activated_abilities(source):
                if activation_actor_allowed(host, seat, source, ability):
                    yield source, ability


def _typed_granted_abilities(
    data: dict[str, Any],
) -> tuple[ActivatedAbility, ...]:
    specs = granted_activated_specs(
        canonical_ability_fragments(data.get("ability_fragments", ()))
    )
    counts: dict[str, int] = {}
    result: list[ActivatedAbility] = []
    for index, spec in enumerate(specs):
        ability_id = spec.ability_id
        payment = spec.granting_attachment_payment
        if payment is not None:
            identity = payment.granting_object
            if identity is None:
                continue
            suffix = sha256(identity.logical_object_id.encode('utf-8')).hexdigest()[:20]
            ability_id = f'{ability_id}#equipment-{suffix}'
        ordinal = counts.get(ability_id, 0) + 1
        counts[ability_id] = ordinal
        result.append(
            ActivatedAbility(
                ability_id=(
                    ability_id
                    if ordinal == 1
                    else f"{ability_id}#{ordinal}"
                ),
                line_index=25_000 + index,
                oracle_line=f"{spec.cost_text}: {spec.effect_text}",
                cost_text=spec.cost_text,
                effect_text=spec.effect_text,
                zones=("battlefield",),
                mana={
                    key: spec.mana_bundle.get(key, 0)
                    for key in ("GENERIC", "W", "U", "B", "R", "G", "C")
                },
                tap_source=spec.tap_source,
                crew_threshold=spec.crew_threshold,
                untap_source=spec.untap_source,
                sacrifice_source=spec.sacrifice_source,
                life_payment=spec.life_payment,
                choices=tuple(
                    CostChoice.from_dict(choice) for choice in spec.choices
                ),
                sorcery_speed=spec.sorcery_speed,
                mana_ability=spec.mana_ability,
                fixed_mana_outputs=tuple(
                    FixedManaMode.from_bundle(dict(output))
                    for output in spec.fixed_mana_outputs
                ),
                builtin_semantic_key=spec.semantic_key,
                mana_spend_restriction=spec.mana_spend_restriction,
                granting_attachment_payment=payment,
            )
        )
    return tuple(result)


__all__ = ["ActivatedAbilityQueryHost", "activated_abilities"]
