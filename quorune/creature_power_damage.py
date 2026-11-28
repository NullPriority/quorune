from __future__ import annotations

"""Typed Fight and one-way creature-power damage resolution."""

from collections.abc import Mapping, Sequence
from dataclasses import replace
from typing import Any

from .affected_permanents import AffectedPermanentSetSpec, select_affected_permanents
from .damage import (
    DamageError,
    damage_proposal,
    resolve_damage_batch,
    source_snapshot,
)
from .damage_source import DamageSourceSnapshot, represented_toxic_value
from .object_predicate import ObjectQuerySpec
from .object_query import exact_numeric_characteristic
from .creature_power_damage_model import (
    CREATURE_POWER_DAMAGE_CAPABILITY,
    CREATURE_POWER_DAMAGE_LKI_CONTEXT,
    CREATURE_POWER_DAMAGE_MECHANIC,
    CREATURE_POWER_DAMAGE_OPERATION,
    OCCURRENCE_POWER_DAMAGE_CONTEXT,
    OCCURRENCE_POWER_DAMAGE_VERSION,
    is_creature_self_damage_set, is_creature_controller_damage_set,
    validate_creature_controller_damage_set,
    occurrence_power_damage_in,
    validate_creature_self_damage_set,
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
    return _capture_power_lki(host, source, characteristics=characteristics)


def _capture_power_lki(host, source, *, characteristics=None):
    effective = (
        dict(characteristics)
        if characteristics is not None
        else host._effective_card_data(source)
    )
    power = _creature_power(host, source, characteristics=effective)
    if power is None:
        types = host._type_parts(str(effective.get('type_line') or ''))[0]
        if source.zone != 'battlefield' or 'creature' in types:
            return None
        # CR 208.3/107.2: a battlefield noncreature has no power to read.
        power = 0
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


def occurrence_creature_power_snapshot(host, card, *, characteristics=None):
    snapshot = _capture_power_lki(host, card, characteristics=characteristics)
    if snapshot is None:
        raise CreaturePowerDamageError("Occurrence creature power is unavailable")
    if card.face_down:
        snapshot["source"]["oracle_id"] = None
    return snapshot


def _observes_departing_creature_power(program, event):
    if any(occurrence_power_damage_in(program.effects)):
        return True
    from .relative_power_target import MANA_VALUE_POWER_CAPABILITY, MANA_VALUE_POWER_PREDICATES

    schema = getattr(program, "target_schema", None)
    return (
        event == "creature.dies.self"
        and isinstance(schema, Mapping)
        and schema.get("predicate") in MANA_VALUE_POWER_PREDICATES
        and MANA_VALUE_POWER_CAPABILITY in program.capability_dependencies
    )


def with_occurrence_power_departure(host, card, characteristics):
    if (card.zone != "battlefield" or not any(_observes_departing_creature_power(program, event)
                                            for event in ("creature.dies", "creature.dies.self")
                                            for program in host.semantics.programs_for_event(event))
            or "creature" not in host._type_parts(str(characteristics.get("type_line") or ""))[0]):
        return characteristics
    return {**characteristics, OCCURRENCE_POWER_DAMAGE_CONTEXT: occurrence_creature_power_snapshot(
        host, card, characteristics=characteristics,
    )}


def validate_occurrence_power_snapshot(context, snapshot):
    from .replacement.immutable import thaw_value

    ref = context.get("card")
    identity = context.get("card_object_identity")
    raw = thaw_value(snapshot)
    value = _source_lki_value(ref, raw)
    if (value is None or not isinstance(identity, str)
            or value[1].logical_object_id != identity or value[1].object_id != identity.rpartition("@")[0]
            or value[1].zone != "battlefield"):
        raise CreaturePowerDamageError("Occurrence power damage lost its original public incarnation")
    return raw, value[1]


def occurrence_power_trigger_context(host, program, event, context, departure_characteristics):
    if not any(occurrence_power_damage_in(program.effects)):
        return {}
    identity = context.get("card_object_identity")
    object_id, _, _ = identity.rpartition("@") if isinstance(identity, str) else ("", "", "")
    if event == "creature.dies":
        data = (departure_characteristics or {}).get(object_id, {})
        snapshot = data.get(OCCURRENCE_POWER_DAMAGE_CONTEXT)
    else:
        card = host.state.cards.get(object_id)
        if card is None or card.zone != "battlefield" or card.phased_out or card.logical_object_id != identity:
            raise CreaturePowerDamageError("Occurrence power damage requires the entering permanent")
        snapshot = occurrence_creature_power_snapshot(host, card)
    raw, _source = validate_occurrence_power_snapshot(context, snapshot)
    return {OCCURRENCE_POWER_DAMAGE_CONTEXT: raw}


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
    public_only: bool = False,
) -> tuple[int, DamageSourceSnapshot] | None:
    current = _card_by_ref(host, ref)
    lki_value = _source_lki_value(ref, lki)
    if current is not None and current.zone == 'battlefield' and not current.phased_out and (
        lki_value is None
        or current.logical_object_id == lki_value[1].logical_object_id
    ):
        card = current
        effective = host._effective_card_data(card)
        power = _creature_power(host, card, characteristics=effective)
        if power is None:
            if 'creature' in host._type_parts(str(effective.get('type_line') or ''))[0]:
                return None
            power = 0
        snapshot = source_snapshot(
            host,
            card.ref,
            controller=card.controller,
        )
        return power, replace(snapshot, oracle_id=None) if public_only and card.face_down else snapshot
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
        raise CreaturePowerDamageError('Creature-power source LKI is malformed')
    if type(lki.get('schema_version')) is not int or lki.get("schema_version") != 1 or type(lki.get("power")) is not int:
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
) -> tuple[str, str | None, str | None, bool, int]:
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
        'schema_version',
        'power_multiplier',
        'recipient_is_original_source',
        "fight_excess_red_version",
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
    if ('recipient_is_original_source' in effect) != (effect.get('schema_version') == 4):
        raise CreaturePowerDamageError('Original-source recipient requires its explicit version')
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
    if type(must_be_creature) is not bool or (
        kind == "fight" and must_be_creature is not True
    ):
        raise CreaturePowerDamageError("Creature-power target domain is malformed")
    from .creature_power_damage_model import creature_power_damage_multiplier
    try:
        multiplier = creature_power_damage_multiplier(effect)
    except (TypeError, ValueError) as exc:
        raise CreaturePowerDamageError(str(exc)) from exc
    if effect.get("schema_version") == OCCURRENCE_POWER_DAMAGE_VERSION and effect.get("source_lki") is None:
        raise CreaturePowerDamageError("Occurrence power damage requires its sealed source")
    return kind, source_ref, target_ref, must_be_creature, multiplier


_BATTLEFIELD_CREATURES = AffectedPermanentSetSpec(
    query=ObjectQuerySpec(zones=("battlefield",), types_all=("creature",)),
)


def _creature_set_damage_proposals(host, effect, *, actor, reason):
    to_controller = is_creature_controller_damage_set(effect)
    validate = validate_creature_controller_damage_set if to_controller else validate_creature_self_damage_set
    try:
        validate(effect, resolved=True)
        creatures = select_affected_permanents(
            host.affected_permanent_object_rows(actor), _BATTLEFIELD_CREATURES, actor=actor,
            active_seats=host.affected_permanent_active_seats(), apnap_order=host.affected_permanent_apnap_order(),
        )
        return _damage_for_creature_set(host, creatures, actor=actor, reason=reason, to_controller=to_controller)
    except (DamageError, TypeError, ValueError) as exc:
        raise CreaturePowerDamageError(str(exc)) from exc


def _damage_for_creature_set(host, creatures, *, actor, reason, to_controller=False):
    proposals = []
    batch_kind = "controller-set" if to_controller else "self-set"
    batch_id = f"damage.creature-power:{host.state.revision}:{host.state.event_sequence + 1}:{batch_kind}"
    for row in creatures:
        power = 1 if to_controller else row.effective_power
        if type(power) is not int:
            raise CreaturePowerDamageError("A selected creature has no exact current power")
        if power <= 0:
            continue
        card = host.state.cards.get(row.object_id)
        if card is None or card.logical_object_id != row.logical_object_id or card.ref != row.ref:
            raise CreaturePowerDamageError("A selected creature changed before damage preparation")
        source = source_snapshot(host, row.ref, controller=row.controller, source_card=card)
        if card.face_down:
            source = replace(source, oracle_id=None)
        proposals.append(damage_proposal(
            host, proposal_id=f"{batch_id}:{len(proposals)}", actor=actor,
            source_ref=row.ref, target=row.controller if to_controller else row.ref, amount=power, combat=False,
            reason=reason, source_override=source,
        ))
    return proposals


def _source_power_opponent_proposals(host, effect, *, actor, reason):
    from .creature_power_damage_model import validate_source_power_opponent_damage

    try:
        validate_source_power_opponent_damage(effect, resolved=True)
    except (TypeError, ValueError) as exc:
        raise CreaturePowerDamageError(str(exc)) from exc
    source = _source_value(host, ref=effect["source"], lki=effect["source_lki"], public_only=True)
    if source is None or source[0] <= 0:
        return ()
    amount, damage_source = source
    proposal_id = f"damage.creature-power:{host.state.revision}:{host.state.event_sequence + 1}:opponent"
    return tuple(
        damage_proposal(
            host, proposal_id=f"{proposal_id}:{seat}", actor=actor, source_ref=damage_source.ref,
            target=seat, amount=amount, combat=False, reason=reason, source_override=damage_source,
        )
        for seat in host.active_seats if seat != actor
    )


def _target_power_damage_cohort_proposals(host, effect, *, actor, reason):
    from .creature_power_damage_model import validate_target_power_damage_cohort

    try:
        validate_target_power_damage_cohort(effect, resolved=True)
        current = _current_creature(host, effect["source"])
        if current is None or current[0].controller != actor or current[1] <= 0:
            return ()
        card, power = current
        source = source_snapshot(host, card.ref, controller=card.controller, source_card=card)
        if card.face_down:
            source = replace(source, oracle_id=None)
        creatures = select_affected_permanents(
            host.affected_permanent_object_rows(actor), _BATTLEFIELD_CREATURES, actor=actor,
            active_seats=host.affected_permanent_active_seats(), apnap_order=host.affected_permanent_apnap_order(),
        )
        recipients = (
            *(row.ref for row in creatures if row.logical_object_id != card.logical_object_id),
            *(seat for seat in host.active_seats if seat != actor),
        )
        batch_id = f"damage.creature-power:{host.state.revision}:{host.state.event_sequence + 1}:target-cohort"
        return tuple(
            damage_proposal(
                host, proposal_id=f"{batch_id}:{index}", actor=actor, source_ref=source.ref,
                target=recipient, amount=power, combat=False, reason=reason, source_override=source,
            )
            for index, recipient in enumerate(recipients)
        )
    except (DamageError, TypeError, ValueError) as exc:
        raise CreaturePowerDamageError(str(exc)) from exc


def _trample_bite_proposals(host, effect, *, actor, reason):
    from .creature_power_damage_model import split_trample_bite_damage, validate_trample_bite
    from .deathtouch import source_has_deathtouch

    try:
        validate_trample_bite(effect, resolved=True)
        source_ref, target_ref = effect["source"], effect["target"]
        if source_ref is None or target_ref is None or source_ref == target_ref:
            return ()
        source_value = _source_value(host, ref=source_ref, lki=None, public_only=True)
        target = _card_by_ref(host, target_ref)
        if (source_value is None or source_value[0] <= 0 or target is None
                or target.zone != "battlefield" or target.phased_out):
            return ()
        power, source = source_value
        if source.controller != actor or target.controller == actor:
            return ()
        effective = host._effective_card_data(target)
        types = host._type_parts(str(effective.get("type_line") or ""))[0]
        if "creature" not in types:
            return ()
        kept, excess = power, 0
        if {"trample", "trample over planeswalkers"}.intersection(source.keywords):
            kept, excess = split_trample_bite_damage(
                power, toughness=exact_numeric_characteristic(target, effective, "toughness"),
                marked=target.marked_damage, deathtouch=source_has_deathtouch(source),
                loyalty=target.counters.get("loyalty", 0) if "planeswalker" in types else None,
                defense=target.counters.get("defense", 0) if "battle" in types else None,
            )
        base_id = f"damage.creature-power:{host.state.revision}:{host.state.event_sequence + 1}:trample"
        return tuple(damage_proposal(
            host, proposal_id=f"{base_id}:{index}", actor=actor, source_ref=source.ref,
            target=recipient, amount=amount, combat=False, reason=reason, source_override=source,
        ) for index, (recipient, amount) in enumerate(((target.ref, kept), (target.controller, excess))) if amount)
    except (DamageError, TypeError, ValueError) as exc:
        raise CreaturePowerDamageError(str(exc)) from exc


def resolve_creature_power_damage(
    host: Any,
    effect: Mapping[str, Any],
    *,
    actor: str,
    reason: str,
) -> tuple[int, ...]:
    """Resolve one creature-power instruction through the canonical damage batch."""
    from .creature_power_damage_model import (
        uses_source_power_opponent_damage,
        uses_target_power_damage_cohort,
        uses_trample_bite,
    )

    if uses_target_power_damage_cohort(effect):
        proposals = _target_power_damage_cohort_proposals(host, effect, actor=actor, reason=reason)
        return _resolve_power_damage_batch(host, effect, proposals)
    if uses_trample_bite(effect):
        return _resolve_power_damage_batch(host, effect, _trample_bite_proposals(host, effect, actor=actor, reason=reason))
    if uses_source_power_opponent_damage(effect):
        proposals = _source_power_opponent_proposals(host, effect, actor=actor, reason=reason)
        return _resolve_power_damage_batch(host, effect, proposals)
    if is_creature_self_damage_set(effect) or is_creature_controller_damage_set(effect):
        proposals = _creature_set_damage_proposals(host, effect, actor=actor, reason=reason)
        return _resolve_power_damage_batch(host, effect, proposals)
    kind, source_ref, target_ref, must_be_creature, multiplier = _instruction(effect)
    if source_ref is None or target_ref is None:
        return ()
    if effect.get('schema_version') == 4:
        from .reciprocal_power_damage import original_source_recipient_matches
        try:
            if not original_source_recipient_matches(host, effect):
                return ()
        except ValueError as exc:
            raise CreaturePowerDamageError(str(exc)) from exc
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
            public_only=effect.get("schema_version") == OCCURRENCE_POWER_DAMAGE_VERSION,
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
        proposals = _fight_proposals(
            host, source_damage=source_damage, source_power=source_power,
            target_card=target_card, target_power=target_power, actor=actor, reason=reason,
            proposal_base=proposal_base,
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
                    amount=source_power * multiplier,
                    combat=False,
                    reason=reason,
                    source_override=source_damage,
                )
            )
    excess_target = None
    if "fight_excess_red_version" in effect:
        from .rules.fight_excess_damage import prepare_fight_excess_target

        excess_target = prepare_fight_excess_target(host, effect, actor, target_card if kind == "fight" else None)
    return _resolve_power_damage_batch(host, effect, proposals, excess_target=excess_target, actor=actor)

def _fight_proposals(host, *, source_damage, source_power, target_card, target_power, actor, reason, proposal_base):
    if source_damage.ref == target_card.ref:
        packets = (("self", source_damage, target_card.ref, max(0, source_power) * 2),)
    else:
        target_damage = source_snapshot(host, target_card.ref, controller=target_card.controller)
        packets = (
            (0, source_damage, target_card.ref, source_power),
            (1, target_damage, source_damage.ref, target_power),
        )
    return tuple(
        damage_proposal(
            host, proposal_id=f"{proposal_base}:fight:{index}", actor=actor, source_ref=source.ref,
            target=recipient, amount=amount, combat=False, reason=reason, source_override=source,
        )
        for index, source, recipient, amount in packets if amount > 0
    )


def resolve_simultaneous_fights(host, pairs, *, actor, reason, batch_ref, effect):
    proposals = []
    for index, (first, second) in enumerate(pairs):
        source = _current_creature(host, first)
        target = _current_creature(host, second)
        if source is None or target is None:
            continue
        source_card, source_power = source
        target_card, target_power = target
        source_damage = source_snapshot(host, source_card.ref, controller=source_card.controller)
        proposals.extend(_fight_proposals(
            host, source_damage=source_damage, source_power=source_power, target_card=target_card,
            target_power=target_power, actor=actor, reason=reason,
            proposal_base=f"damage.created-fight:{batch_ref}:{index}",
        ))
    return _resolve_power_damage_batch(host, effect, proposals)


def _resolve_power_damage_batch(host, effect, proposals, *, excess_target=None, actor=None):
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
    if excess_target is not None:
        from .rules.fight_excess_damage import settle_fight_excess_red

        settle_fight_excess_red(host, result.events, excess_target, actor)
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
