from __future__ import annotations

"""Typed Fight and one-way creature-power damage resolution."""

from collections.abc import Mapping, Sequence
from dataclasses import replace
from typing import Any

from .damage import (
    DamageError,
    damage_proposal,
    resolve_damage_batch,
    source_snapshot,
)
from .damage_source import DamageSourceSnapshot, represented_toxic_value
from .object_query import exact_numeric_characteristic
from .creature_power_damage_model import (
    CREATURE_POWER_DAMAGE_CAPABILITY,
    CREATURE_POWER_DAMAGE_LKI_CONTEXT,
    CREATURE_POWER_DAMAGE_MECHANIC,
    CREATURE_POWER_DAMAGE_OPERATION,
)


class CreaturePowerDamageError(ValueError):
    """A typed creature-power damage instruction is malformed."""


def _contains_source_reference(value: object) -> bool:
    if isinstance(value, Mapping):
        return (
            value.get("op") == CREATURE_POWER_DAMAGE_OPERATION
            and value.get("source") == "$source"
        ) or any(_contains_source_reference(child) for child in value.values())
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return any(_contains_source_reference(child) for child in value)
    return False


def creature_power_damage_needs_source_lki(
    effects: Sequence[Mapping[str, Any]],
) -> bool:
    return _contains_source_reference(effects)


def _creature_power(
    host: Any,
    card: Any,
    *,
    characteristics: Mapping[str, Any] | None = None,
) -> int | None:
    effective = (
        dict(characteristics)
        if characteristics is not None
        else host._effective_card_data(card)
    )
    card_types = host._type_parts(str(effective.get("type_line") or ""))[0]
    if "creature" not in card_types:
        return None
    power = exact_numeric_characteristic(card, effective, "power")
    return power if type(power) is int else None


def capture_creature_power_damage_source_lki(
    host: Any,
    source: Any,
    effects: Sequence[Mapping[str, Any]],
    *,
    characteristics: Mapping[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Capture source power and damage characteristics before departure."""

    if not creature_power_damage_needs_source_lki(effects):
        return None
    effective = (
        dict(characteristics)
        if characteristics is not None
        else host._effective_card_data(source)
    )
    power = _creature_power(host, source, characteristics=effective)
    if power is None:
        return None
    card_types, subtypes, supertypes = host._type_parts(
        str(effective.get("type_line") or "")
    )
    keywords = tuple(
        sorted(
            {
                str(value).strip().casefold()
                for value in effective.get("keywords", ())
                if str(value).strip()
            }
        )
    )
    toxic_value = represented_toxic_value(
        effective,
        temporary_keywords=getattr(source, "temporary_keywords", ()),
    )
    if toxic_value and "toxic" not in keywords:
        keywords = tuple(sorted({*keywords, "toxic"}))
    snapshot = replace(
        source_snapshot(host, source.ref, controller=source.controller),
        ref=source.ref,
        object_id=source.object_id,
        logical_object_id=source.logical_object_id,
        controller=source.controller,
        owner=source.owner,
        zone=source.zone,
        oracle_id=source.oracle_id,
        commander_designation_id=source.commander_designation_id,
        types=tuple(sorted(card_types)),
        subtypes=tuple(sorted(subtypes)),
        supertypes=tuple(sorted(supertypes)),
        colors=tuple(
            sorted(str(value).upper() for value in effective.get("colors", ()))
        ),
        keywords=keywords,
        mana_value=effective.get("mana_value"),
        is_commander=bool(source.is_commander),
        toxic_value=toxic_value,
    )
    return {
        "schema_version": 1,
        "power": power,
        "source": snapshot.to_dict(),
    }


def creature_power_damage_source_available(
    host: Any,
    source: Any,
    effects: Sequence[Mapping[str, Any]],
) -> bool:
    return not creature_power_damage_needs_source_lki(effects) or (
        capture_creature_power_damage_source_lki(host, source, effects)
        is not None
    )


def _card_by_ref(host: Any, ref: str) -> Any | None:
    return next(
        (card for card in host.state.cards.values() if card.ref == ref),
        None,
    )


def _current_creature(host: Any, ref: str) -> tuple[Any, int] | None:
    card = _card_by_ref(host, ref)
    if card is None or card.zone != "battlefield" or card.phased_out:
        return None
    power = _creature_power(host, card)
    return (card, power) if power is not None else None


def _source_value(
    host: Any,
    *,
    ref: str,
    lki: Mapping[str, Any] | None,
) -> tuple[int, DamageSourceSnapshot] | None:
    current = _current_creature(host, ref)
    lki_value = _source_lki_value(ref, lki)
    if current is not None and (
        lki_value is None
        or current[0].logical_object_id == lki_value[1].logical_object_id
    ):
        card, power = current
        return power, source_snapshot(
            host,
            card.ref,
            controller=card.controller,
        )
    return lki_value


def _source_lki_value(
    ref: str,
    lki: Mapping[str, Any] | None,
) -> tuple[int, DamageSourceSnapshot] | None:
    if lki is None:
        return None
    if not isinstance(lki, Mapping) or set(lki) != {
        "power",
        "schema_version",
        "source",
    }:
        return None
    if lki.get("schema_version") != 1 or type(lki.get("power")) is not int:
        raise CreaturePowerDamageError("Creature-power source LKI is malformed")
    raw_source = lki.get("source")
    if not isinstance(raw_source, Mapping):
        raise CreaturePowerDamageError("Creature-power source LKI is malformed")
    try:
        snapshot = DamageSourceSnapshot.from_dict(dict(raw_source))
    except (DamageError, TypeError, ValueError) as exc:
        raise CreaturePowerDamageError(
            "Creature-power source LKI is malformed"
        ) from exc
    if snapshot.ref != ref:
        raise CreaturePowerDamageError("Creature-power source LKI is stale")
    return int(lki["power"]), snapshot


def _instruction(
    effect: Mapping[str, Any],
) -> tuple[str, str | None, str | None, bool]:
    allowed = {
        "op",
        "kind",
        "source",
        "target",
        "target_must_be_creature",
        "source_lki",
        "reason",
        "_replacement_event_ids",
        "_replacement_selections",
        "_runtime_source",
    }
    required = {
        "op",
        "kind",
        "source",
        "target",
        "target_must_be_creature",
    }
    if set(effect) - allowed or not required.issubset(effect):
        raise CreaturePowerDamageError("Creature-power damage shape is invalid")
    if effect.get("op") != CREATURE_POWER_DAMAGE_OPERATION:
        raise CreaturePowerDamageError("Creature-power damage operation is invalid")
    kind = effect.get("kind")
    if kind not in {"bite", "fight"}:
        raise CreaturePowerDamageError("Creature-power damage kind is invalid")
    source_ref = effect.get("source")
    target_ref = effect.get("target")
    if source_ref is not None and (type(source_ref) is not str or not source_ref):
        raise CreaturePowerDamageError("Creature-power source is malformed")
    if target_ref is not None and (type(target_ref) is not str or not target_ref):
        raise CreaturePowerDamageError("Creature-power target is malformed")
    must_be_creature = effect.get("target_must_be_creature")
    if type(must_be_creature) is not bool or (kind == "fight") is not must_be_creature:
        raise CreaturePowerDamageError("Creature-power target domain is malformed")
    return kind, source_ref, target_ref, must_be_creature


def resolve_creature_power_damage(
    host: Any,
    effect: Mapping[str, Any],
    *,
    actor: str,
    reason: str,
) -> tuple[int, ...]:
    """Resolve one Fight or bite through the canonical damage batch."""

    kind, source_ref, target_ref, must_be_creature = _instruction(effect)
    if source_ref is None or target_ref is None:
        return ()
    if kind == "fight":
        current_source = _current_creature(host, source_ref)
        if current_source is None:
            return ()
        source_card, source_power = current_source
        source_lki = _source_lki_value(source_ref, effect.get("source_lki"))
        if source_lki is not None and (
            source_card.logical_object_id
            != source_lki[1].logical_object_id
        ):
            return ()
        source_damage = source_snapshot(
            host,
            source_card.ref,
            controller=source_card.controller,
        )
    else:
        source_value = _source_value(
            host,
            ref=source_ref,
            lki=effect.get("source_lki"),
        )
        if source_value is None:
            return ()
        source_power, source_damage = source_value
    proposals = []
    proposal_base = (
        f"damage.creature-power:{host.state.revision}:"
        f"{host.state.event_sequence + 1}"
    )
    if kind == "fight":
        target_value = _current_creature(host, target_ref)
        if target_value is None:
            return ()
        target_card, target_power = target_value
        if source_ref == target_ref:
            if source_power > 0:
                proposals.append(
                    damage_proposal(
                        host,
                        proposal_id=f"{proposal_base}:fight:self",
                        actor=actor,
                        source_ref=source_ref,
                        target=target_ref,
                        amount=max(0, source_power) * 2,
                        combat=False,
                        reason=reason,
                        source_override=source_damage,
                    )
                )
        else:
            target_damage = source_snapshot(
                host,
                target_card.ref,
                controller=target_card.controller,
            )
            for index, (damage_source, recipient, amount) in enumerate(
                (
                    (source_damage, target_ref, source_power),
                    (target_damage, source_ref, target_power),
                )
            ):
                if amount > 0:
                    proposals.append(
                        damage_proposal(
                            host,
                            proposal_id=f"{proposal_base}:fight:{index}",
                            actor=actor,
                            source_ref=damage_source.ref,
                            target=recipient,
                            amount=amount,
                            combat=False,
                            reason=reason,
                            source_override=damage_source,
                        )
                    )
    else:
        target_card = _card_by_ref(host, target_ref)
        if target_card is not None and (
            target_card.zone != "battlefield" or target_card.phased_out
        ):
            return ()
        if must_be_creature and _current_creature(host, target_ref) is None:
            return ()
        if source_power > 0:
            proposals.append(
                damage_proposal(
                    host,
                    proposal_id=f"{proposal_base}:bite:0",
                    actor=actor,
                    source_ref=source_damage.ref,
                    target=target_ref,
                    amount=source_power,
                    combat=False,
                    reason=reason,
                    source_override=source_damage,
                )
            )
    if not proposals:
        return ()
    replacement_ids = effect.get("_replacement_event_ids", ())
    if not isinstance(replacement_ids, (list, tuple)) or (
        replacement_ids and len(replacement_ids) != len(proposals)
    ):
        raise CreaturePowerDamageError("Creature-power damage event IDs are stale")
    if replacement_ids:
        proposals = [
            replace(proposal, proposal_id=str(replacement_ids[index]))
            for index, proposal in enumerate(proposals)
        ]
    selections = effect.get("_replacement_selections", ())
    if not isinstance(selections, (list, tuple)):
        raise CreaturePowerDamageError("Creature-power replacement choices are malformed")
    try:
        result = resolve_damage_batch(
            host,
            tuple(proposals),
            replacement_selections=tuple(selections),
        )
    except DamageError as exc:
        raise CreaturePowerDamageError(str(exc)) from exc
    return tuple(event.dealt_amount for event in result.events)


__all__ = [
    "capture_creature_power_damage_source_lki",
    "creature_power_damage_needs_source_lki",
    "creature_power_damage_source_available",
    "resolve_creature_power_damage",
    "CREATURE_POWER_DAMAGE_CAPABILITY",
    "CREATURE_POWER_DAMAGE_LKI_CONTEXT",
    "CREATURE_POWER_DAMAGE_MECHANIC",
    "CREATURE_POWER_DAMAGE_OPERATION",
    "CreaturePowerDamageError",
]
