from __future__ import annotations

from dataclasses import replace
from hashlib import sha256
from typing import Any, Protocol

from ..activation_counter_cost import SourceCounterRemovalCost
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
from ...card_programs.admission import program_has_complete_card_program_admission
from ...continuous_effect_model import Layer
from ...granted_activation_model import (
    GRANTED_NINJUTSU_VERSION, GRANTED_UNEARTH_VERSION, GRANTOR_REDIRECTION_CAPABILITY, GRANTOR_REDIRECTION_VERSION,
)
from ...intrinsic_land_abilities import intrinsic_land_mana_abilities
from ...util import normalize_mana_bundle


class ActivatedAbilityQueryHost(Protocol):
    semantics: Any

    def _effective_card_data(self, card: Any) -> dict[str, Any]: ...


def activated_abilities(
    host: ActivatedAbilityQueryHost,
    card: Any,
) -> tuple[ActivatedAbility, ...]:
    """Return compiler-pinned, intrinsic, and typed granted abilities."""

    from .battlefield_grants import battlefield_creature_activated_abilities

    data = host._effective_card_data(card)
    base = current_activation_catalog(host, card, data, allow_historical=True)
    return (*base, *battlefield_creature_activated_abilities(host, card, data))


def current_activation_catalog(host, card, data, *, allow_historical=False):
    """Read the local ability catalog without recursing through battlefield donor effects."""
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
        and allow_historical
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
    from ...linked_ability_exile_model import LINKED_EXILE_COPY_TARGET_PREDICATE
    from ..linked_graveyard_entry import has_linked_graveyard_entry_source

    if not has_linked_graveyard_entry_source(card):
        abilities = [
            ability for ability in abilities
            if (ability.target_schema or {}).get("predicate") != LINKED_EXILE_COPY_TARGET_PREDICATE
        ]
    abilities.sort(key=lambda ability: ability.line_index)
    from ..linked_exile_activations import linked_exile_activated_abilities

    fragments = canonical_ability_fragments(data.get("ability_fragments", ()))
    abilities.extend(_typed_granted_abilities(host, fragments))
    abilities.extend(linked_exile_activated_abilities(host, card, fragments))
    return tuple(abilities)


def bind_creature_donor_activations(
    host, donor, *, binding_identity: str, ability_prefix: str, first_line_index: int,
) -> tuple[ActivatedAbility, ...]:
    """Rebind represented donor abilities without recursively asking for activation offers."""
    values = host._effective_card_data(donor, maximum_layer=Layer.ABILITY)
    types, subtypes, _supertypes = host._type_parts(str(values.get('type_line') or ''))
    if 'creature' not in types:
        return ()

    activations = (ActivatedAbility.from_dict(raw) for raw in values.get('activated_abilities', ()))
    return bind_donor_activation_catalog(
        donor, represented_donor_activation_catalog(host, donor, activations, types=types, subtypes=subtypes),
        binding_identity=binding_identity, ability_prefix=ability_prefix, first_line_index=first_line_index,
    )


def represented_donor_activation_catalog(host, donor, abilities, *, types, subtypes):
    """Keep only current battlefield costs and independently executable donor programs."""
    intrinsic = intrinsic_land_mana_abilities(types, subtypes)
    for ability in abilities:
        if not ability.compiled_cost or 'battlefield' not in ability.zones:
            continue
        key = host._semantic_key_for_ability(donor, ability)
        program = host.semantics.get(key)
        if program is None:
            if not any(item.ability_id == ability.ability_id and item.fixed_mana_outputs == ability.fixed_mana_outputs
                       for item in intrinsic):
                continue
        elif program.event != 'activate' or not host.semantic_program_is_current_trusted(program):
            continue
        yield ability, key


def bind_donor_activation_catalog(donor, catalog, *, binding_identity, ability_prefix, first_line_index):
    result = []
    for ability, key in catalog:
        identity = f'{binding_identity}:{donor.logical_object_id}:{ability.ability_id}'
        occurrence = sha256(identity.encode('utf-8')).hexdigest()[:24]
        result.append(replace(ability, ability_id=f'{ability_prefix}:{occurrence}:{ability.ability_id}',
                              line_index=first_line_index + len(result), builtin_semantic_key=key))
    return tuple(result)


def activation_actor_allowed(host, seat: str, source: Any, ability: ActivatedAbility) -> bool:
    """Keep printed public permission on the current trusted result program."""
    if source.zone not in ability.zones:
        return False
    if source.zone != "battlefield":
        return source.owner == seat
    if source.phased_out:
        return False
    if ability.activation_grantor is not None:
        program = host.semantics.get(host._semantic_key_for_ability(source, ability))
        return (seat == ability.activation_grantor and program is not None
                and GRANTOR_REDIRECTION_CAPABILITY in program.capability_dependencies
                and host.semantic_program_is_current_trusted(program))
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

    if not any({PUBLIC_ACTIVATION_CAPABILITY, GRANTOR_REDIRECTION_CAPABILITY}.intersection(program.capability_dependencies)
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


def _is_current_granted_unearth_program(host, spec) -> bool:
    from ...semantic_runtime.unearth import ordinary_unearth_specs_from_descriptors

    program = host.semantics.get(spec.semantic_key)
    if (
        program is None or program.ability_id != spec.ability_id
        or program.active_zone != "graveyard" or program.event != "activate"
        or program.provenance.get("granted_only") is not True
        or program.target_schema is not None or program.event_condition is not None
        or tuple(program.effects) != ({"op": "unearth", "action": "return"},)
        or not program_has_complete_card_program_admission(program)
        or not host.semantic_program_is_current_trusted(program)
    ):
        return False
    try:
        abilities = ordinary_unearth_specs_from_descriptors(program.handlers)
    except ValueError:
        return False
    if len(abilities) != 1:
        return False
    printed = abilities[0].to_activated_ability()
    return (printed.cost_text == spec.cost_text
            and {key: value for key, value in printed.mana.items() if value} == spec.mana_bundle)


def _typed_granted_abilities(
    host: ActivatedAbilityQueryHost, fragments,
) -> tuple[ActivatedAbility, ...]:
    specs = granted_activated_specs(fragments)
    counts: dict[str, int] = {}
    result: list[ActivatedAbility] = []
    for index, spec in enumerate(specs):
        if spec.schema_version == GRANTED_NINJUTSU_VERSION:
            from ...semantic_runtime.hand_ninjutsu_grants import granted_ninjutsu_activation

            activation = granted_ninjutsu_activation(host, spec)
            if activation is not None:
                result.append(activation)
            continue
        ability_id = spec.ability_id
        if spec.schema_version == GRANTOR_REDIRECTION_VERSION:
            if spec.activation_grantor is None or spec.activation_grantor.startswith("$"):
                continue
            suffix = sha256(spec.activation_grantor.encode("utf-8")).hexdigest()[:20]
            ability_id = f"{ability_id}#grantor-{suffix}"
        if spec.schema_version == GRANTED_UNEARTH_VERSION:
            if spec.grant_incarnation is None or not _is_current_granted_unearth_program(host, spec):
                continue
            suffix = sha256(spec.grant_incarnation.encode("utf-8")).hexdigest()[:20]
            ability_id = f"{ability_id}#unearth-{suffix}"
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
                zones=spec.activation_zones,
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
                activation_grantor=spec.activation_grantor,
                mana_spend_restriction=spec.mana_spend_restriction,
                granting_attachment_payment=payment,
                announced_x_symbols=spec.announced_x_symbols,
                source_counter_removal_cost=(SourceCounterRemovalCost.from_dict(spec.source_counter_removal_cost)
                    if spec.source_counter_removal_cost is not None else None),
            )
        )
    return tuple(result)


__all__ = ["ActivatedAbilityQueryHost", "activated_abilities", "bind_creature_donor_activations"]
