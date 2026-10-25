from __future__ import annotations

"""Object-local state reset for CR 400.7 zone transitions."""

import copy
from typing import Sequence
from .paid_entry_copy_model import PAID_ENTRY_COPY_FIELD, validate_paid_entry_copy_fact

from .model import CardInstance
from .kicker import KICKER_ANNOTATION
from .multikicker_model import MULTIKICKER_FIELD, validate_multikicker_fact
from .flashback import FLASHBACK_CAST_ANNOTATION
from .cast_lifecycles import (
    FixedCastLifecycleKind,
    FixedCastLifecycleSpec,
    FixedZoneCastDesignation,
    FIXED_CAST_LIFECYCLE_STACK_ANNOTATION,
    FIXED_ZONE_CAST_DESIGNATION_FIELD,
)
from .morph import (
    face_down_characteristics,
    FixedManaMorphSpec,
    MORPH_FACE_DOWN_ANNOTATION,
    MORPH_METHOD_ANNOTATION,
    morph_face_down_annotation,
)
from .nonmana_morph_model import FixedNonmanaMorphSpec


class ZoneObjectStateError(ValueError):
    """A zone-object reset request is malformed."""


def mark_cast_grant_rider(card: CardInstance, binding) -> None:
    from .rules.cast_grant_riders import CAST_GRANT_RIDER_FIELD, CastGrantRiderBinding
    if (not isinstance(binding, CastGrantRiderBinding) or card.zone != 'stack' or not card.is_card_object
            or binding.logical_object_id != card.logical_object_id or binding.caster != card.owner):
        raise ZoneObjectStateError('Casting follow-up requires its original owned spell incarnation')
    card.annotations[CAST_GRANT_RIDER_FIELD] = binding.to_dict()


def mark_resolving_paid_entry_copy(card: CardInstance, context) -> None:
    value = context.get(PAID_ENTRY_COPY_FIELD)
    if value is None:
        return
    if card.zone != 'stack':
        raise ZoneObjectStateError('Paid entry copy facts require the resolving spell incarnation')
    card.annotations[PAID_ENTRY_COPY_FIELD] = validate_paid_entry_copy_fact(value)


def mark_multikicker_paid(card: CardInstance, context) -> None:
    """Commit a cast decision to this spell incarnation, including a spell copy."""
    value = context.get(MULTIKICKER_FIELD)
    if value is None:
        return
    if not isinstance(card, CardInstance) or card.zone != 'stack':
        raise ZoneObjectStateError('Multikicker facts require a stack incarnation')
    fact = validate_multikicker_fact(value)
    previous = card.annotations.get(MULTIKICKER_FIELD)
    if previous is not None and validate_multikicker_fact(previous) != fact:
        raise ZoneObjectStateError('Multikicker payment history changed')
    card.annotations[MULTIKICKER_FIELD] = fact
    card.annotations[KICKER_ANNOTATION] = True


def stack_entry_cast_facts(card: CardInstance, *, include_multikicker_result=False):
    value = card.annotations.get(PAID_ENTRY_COPY_FIELD)
    return {'cast_option': 'kicked' if card.annotations.get(KICKER_ANNOTATION) is True else None,
        **({MULTIKICKER_FIELD: validate_multikicker_fact(card.annotations[MULTIKICKER_FIELD])}
           if include_multikicker_result and card.zone == 'stack' and MULTIKICKER_FIELD in card.annotations else {}),
        'paid_entry_copy': validate_paid_entry_copy_fact(value)
        if card.zone == 'stack' and value is not None else None}


def mark_card_kicked(card: CardInstance) -> None:
    """Record one paid Kicker cost on the current spell object."""

    if (
        not isinstance(card, CardInstance)
        or card.zone != "stack"
        or card.object_kind != "card"
        or card.annotations.get(KICKER_ANNOTATION) is not None
    ):
        raise ZoneObjectStateError("Paid Kicker spell state is malformed")
    card.annotations[KICKER_ANNOTATION] = True


def mark_card_unearthed(card: CardInstance) -> None:
    """Commit CR 702.84a's noncopiable designation to one incarnation."""

    if (
        not isinstance(card, CardInstance)
        or card.zone != "battlefield"
        or card.object_kind != "card"
        or card.phased_out
        or card.unearthed
    ):
        raise ZoneObjectStateError("Unearthed designation is malformed")
    card.unearthed = True


def mark_card_flashed_back(card: CardInstance) -> None:
    """Record Flashback's noncopiable designation on one stack incarnation."""

    if (
        not isinstance(card, CardInstance)
        or card.zone != "stack"
        or card.object_kind != "card"
        or card.annotations.get(FLASHBACK_CAST_ANNOTATION) is not None
    ):
        raise ZoneObjectStateError("Flashed-back spell state is malformed")
    card.annotations[FLASHBACK_CAST_ANNOTATION] = True


def mark_card_fixed_cast_lifecycle(
    card: CardInstance,
    spec: FixedCastLifecycleSpec,
) -> None:
    """Record an intrinsic cast lifecycle on one stack incarnation."""

    if (
        not isinstance(card, CardInstance)
        or card.zone != "stack"
        or card.object_kind != "card"
        or not isinstance(spec, FixedCastLifecycleSpec)
        or spec.kind
        not in {
            FixedCastLifecycleKind.ESCAPE,
            FixedCastLifecycleKind.JUMP_START,
            FixedCastLifecycleKind.AFTERMATH,
            FixedCastLifecycleKind.REBOUND,
        }
        or card.annotations.get(FIXED_CAST_LIFECYCLE_STACK_ANNOTATION)
        is not None
    ):
        raise ZoneObjectStateError("Fixed cast-lifecycle stack state is malformed")
    card.annotations[FIXED_CAST_LIFECYCLE_STACK_ANNOTATION] = spec.to_dict()


def mark_fixed_zone_cast_designation(
    card: CardInstance,
    *,
    spec: FixedCastLifecycleSpec,
    turn_sequence: int,
    viewers: Sequence[str],
) -> None:
    """Commit one rules-created Foretell or Plot exile designation."""

    if (
        card.zone != "exile"
        or card.object_kind != "card"
        or card.owner not in viewers
        or spec.kind
        not in {FixedCastLifecycleKind.FORETELL, FixedCastLifecycleKind.PLOT}
    ):
        raise ZoneObjectStateError("Fixed zone-cast designation is malformed")
    designation = FixedZoneCastDesignation(
        lifecycle=spec,
        logical_object_id=card.logical_object_id,
        created_turn_sequence=turn_sequence,
    )
    card.annotations[FIXED_ZONE_CAST_DESIGNATION_FIELD] = designation.to_dict()
    if spec.kind is FixedCastLifecycleKind.FORETELL:
        card.face_down = True
        card.known_to = [card.owner]
        card.revealed_to = []
    else:
        card.face_down = False
        card.known_to = list(viewers)
        card.revealed_to = list(viewers)


def mark_card_face_down_for_morph(
    card: CardInstance,
    *,
    controller: str,
    spec: FixedManaMorphSpec,
) -> None:
    """Commit one represented layer-1b face-down method to a stack object."""

    if (
        not isinstance(card, CardInstance)
        or card.zone != "stack"
        or card.object_kind != "card"
        or type(controller) is not str
        or not controller
        or not isinstance(spec, (FixedManaMorphSpec, FixedNonmanaMorphSpec))
    ):
        raise ZoneObjectStateError("Face-down casting state is malformed")
    card.face_down = True
    card.annotations[MORPH_FACE_DOWN_ANNOTATION] = copy.deepcopy(
        face_down_characteristics(spec.method)
    )
    card.annotations[MORPH_METHOD_ANNOTATION] = morph_face_down_annotation(spec)
    card.known_to = sorted({card.owner, controller, *card.known_to})
    card.revealed_to = []


def mark_card_manifested(card: CardInstance, *, controller: str) -> None:
    from .manifest import manifest_characteristics, MANIFEST_MARKER
    if card.zone != 'battlefield' or card.object_kind != 'card' or not controller:
        raise ZoneObjectStateError('Manifest state requires one entering physical card')
    card.face_down = True
    card.annotations[MORPH_FACE_DOWN_ANNOTATION] = manifest_characteristics()
    card.annotations[MANIFEST_MARKER] = {'schema_version': 1}
    card.known_to = [controller]
    card.revealed_to = []


def turn_card_face_up(
    card: CardInstance,
    *,
    viewers: tuple[str, ...],
) -> None:
    """End one represented face-down effect without changing incarnation."""

    if (
        not isinstance(card, CardInstance)
        or card.zone != "battlefield"
        or not card.face_down
        or any(type(viewer) is not str or not viewer for viewer in viewers)
        or len(set(viewers)) != len(viewers)
    ):
        raise ZoneObjectStateError("Turn-face-up state is malformed")
    card.face_down = False
    card.annotations.pop('manifested_card', None)
    card.annotations.pop(MORPH_FACE_DOWN_ANNOTATION, None)
    card.annotations.pop(MORPH_METHOD_ANNOTATION, None)
    card.known_to = list(viewers)
    card.revealed_to = list(viewers)


def reset_card_after_zone_change(
    card: CardInstance,
    *,
    destination: str,
    stack_to_battlefield: bool,
) -> None:
    """Reset state that the new zone object cannot retain.

    Zone-list mutation, timestamps, attachment graph cleanup, and event
    dispatch remain with their existing owners.  This boundary owns only the
    state stored on one card object after the host has established that CR
    400.7 creates a new object or applies the spell-to-permanent exception.
    """

    if not isinstance(card, CardInstance):
        raise ZoneObjectStateError("Zone-object reset requires one card")
    if type(destination) is not str or not destination:
        raise ZoneObjectStateError(
            "Zone-object reset requires a destination"
        )
    if type(stack_to_battlefield) is not bool:
        raise ZoneObjectStateError(
            "Zone-object reset requires an exact transition kind"
        )

    card.tapped = False
    card.marked_damage = 0
    card.deathtouch_damage = False
    card.regeneration_shields = 0
    card.temporary_keywords.clear()
    card.goaded_by.clear()
    card.monstrous_value = None
    card.renowned = False
    card.class_level = None
    card.unearthed = False
    if not stack_to_battlefield:
        card.transform_count = 0
    card.attacking = None
    card.blocking = None
    card.attached_to = None
    card.attachments.clear()
    card.phased_out = False
    card.source_continuity = None
    if not stack_to_battlefield:
        card.battle_protector = None

    # CR 400.7 gives the destination a new logical object. Retain only state
    # covered by an implemented exception or by initial copiable token data.
    retained_annotation_keys = {
        "object_characteristics",
        "token_characteristics",
    }
    if card.is_token or card.object_kind in {
        "spell_copy",
        "card_copy",
    }:
        retained_annotation_keys.update(
            {"copied_from", "copy_overrides"}
        )
        if destination=='stack':retained_annotation_keys.add('prepared_spell_copy')
    if stack_to_battlefield:
        retained_annotation_keys.update(
            {
                "bestowed",
                "chosen_creature_type",
                "chosen_creature_type_adds_subtype",
                "chosen_name",
                "copy_overrides",
                "evoked",
                "pending_aura_target",
                "pending_aura_zone",
                "face_down_characteristics",
                "face_down_method",
                "prototype_characteristics",
                FIXED_CAST_LIFECYCLE_STACK_ANNOTATION,
            }
        )
    card.annotations = {
        key: value
        for key, value in card.annotations.items()
        if key in retained_annotation_keys
    }
    card.counters.clear()
    if not stack_to_battlefield:
        card.active_face = None
        card.face_down = False
    if destination != "battlefield":
        card.controller = card.owner


__all__ = [
    "mark_card_face_down_for_morph",
    "mark_card_flashed_back",
    "mark_card_fixed_cast_lifecycle",
    "mark_fixed_zone_cast_designation",
    "mark_card_kicked",
    "mark_card_unearthed",
    "ZoneObjectStateError",
    "reset_card_after_zone_change",
    "turn_card_face_up",
]
