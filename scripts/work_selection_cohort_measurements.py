from __future__ import annotations

from functools import partial
from typing import Any, Mapping, Sequence

from quorune.compiler.exile_templates import targeted_exile_effect_template
from quorune.compiler.destruction_templates import destruction_effect_template
from quorune.compiler.optional_effect_templates import (
    fixed_optional_effect_template,
)
from quorune.compiler.modal_templates import FIXED_NONREPEATING_MODAL_MECHANIC
from quorune.compiler.public_zone_move_templates import (
    public_zone_move_effect_template,
)
from quorune.compiler.regeneration_templates import (
    fixed_regeneration_effect_template,
)
from quorune.compiler.fixed_counter_trigger_nodes import (
    FixedSpellCastCharacteristicQuery,
    fixed_counter_trigger_binding,
)
from quorune.compiler.token_templates import fixed_token_creation_effect_template
from quorune.oracle_ir import (
    _face_type_context,
    _reviewed_atomic_effect_template,
    _reviewed_effect_template,
    _without_parenthetical_reminder,
)
from quorune.work_selection_evidence import (
    COHORT_MEASUREMENT_ALGORITHM_VERSION,
    COHORT_MEASUREMENT_SCHEMA_VERSION,
    WorkSelectionCohortMeasurementError,
)
from quorune.work_selection_common import stable_hash


_PROBE_TOKEN = "fixed-token-creation-existing-owner-v1"
_PROBE_EXILE = "fixed-exile-existing-owner-v1"
_PROBE_OPTIONAL_EFFECT = "fixed-optional-effect-choice-existing-owner-v1"
_PROBE_REGENERATION = "fixed-regeneration-existing-owner-v1"
_PROBE_SPELL_CAST_CHARACTERISTIC = (
    "fixed-spell-cast-characteristic-trigger-existing-owner-v1"
)
_PROBE_IDS = {
    _PROBE_EXILE,
    _PROBE_OPTIONAL_EFFECT,
    _PROBE_REGENERATION,
    _PROBE_SPELL_CAST_CHARACTERISTIC,
    _PROBE_TOKEN,
}


def _source_line(card_record: Any, ability: Mapping[str, Any]) -> str:
    faces = {
        "front": str(card_record.oracle_text),
        **{
            str(face.get("name") or ""): str(face.get("oracle_text") or "")
            for face in card_record.faces
            if str(face.get("name") or "")
        },
    }
    face_id = str(ability.get("face_id") or "front")
    text = faces.get(face_id, faces["front"])
    lines = text.splitlines()
    line_index = int(ability.get("source_line") or 0) - 1
    return lines[line_index] if 0 <= line_index < len(lines) else text


def _token_instruction_candidates(line: str) -> tuple[str, ...]:
    normalized = " ".join(line.strip().split())
    bodies: list[str] = []
    if normalized.casefold().startswith("create "):
        bodies.append(normalized)
    elif normalized.startswith("• "):
        body = normalized[2:].strip()
        if body.casefold().startswith("create "):
            bodies.append(body)
    elif normalized.startswith("+ ") and " — " in normalized:
        body = normalized.split(" — ", 1)[1].strip()
        if body.casefold().startswith("create "):
            bodies.append(body)
    elif ": " in normalized and not normalized.startswith("{TK}"):
        body = normalized.split(": ", 1)[1].strip()
        if body.casefold().startswith("create "):
            bodies.append(body)
    elif normalized.casefold().startswith(("when ", "whenever ", "at ")):
        marker = normalized.casefold().find(", create ")
        if marker >= 0:
            bodies.append(normalized[marker + 2 :].strip())
    return tuple(dict.fromkeys(bodies))


def _exile_instruction_candidates(line: str) -> tuple[str, ...]:
    normalized = " ".join(line.strip().split())
    bodies = [normalized]
    if normalized.startswith("• "):
        bodies.append(normalized[2:].strip())
    if ": " in normalized and not normalized.startswith("{TK}"):
        bodies.append(normalized.split(": ", 1)[1].strip())
    if normalized.casefold().startswith(("when ", "whenever ", "at ")):
        marker = normalized.casefold().find(", exile ")
        if marker >= 0:
            bodies.append(normalized[marker + 2 :].strip())
    if " — " in normalized:
        bodies.append(normalized.split(" — ", 1)[1].strip())
    return tuple(dict.fromkeys(bodies))


def _optional_effect_instruction_candidates(line: str) -> tuple[str, ...]:
    normalized = " ".join(line.strip().split())
    candidates = [normalized]
    for candidate in tuple(candidates):
        if candidate.startswith("• "):
            candidates.append(candidate[2:].strip())
        if ": " in candidate and not candidate.startswith("{TK}"):
            candidates.append(candidate.split(": ", 1)[1].strip())
        if " — " in candidate:
            candidates.append(candidate.split(" — ", 1)[1].strip())
    for candidate in tuple(candidates):
        if candidate.casefold().startswith(
            ("when ", "whenever ", "at the beginning of ")
        ) and ", " in candidate:
            candidates.append(candidate.split(", ", 1)[1].strip())
    return tuple(
        dict.fromkeys(
            candidate
            for candidate in candidates
            if candidate.casefold().startswith("you may ")
        )
    )


def _fixed_regeneration_instruction_candidates(line: str) -> tuple[str, ...]:
    normalized = " ".join(
        _without_parenthetical_reminder(line).strip().split()
    )
    candidates = [normalized]
    for candidate in tuple(candidates):
        if candidate.startswith("• "):
            candidates.append(candidate[2:].strip())
        if ": " in candidate and not candidate.startswith("{TK}"):
            candidates.append(candidate.split(": ", 1)[1].strip())
        if " — " in candidate:
            candidates.append(candidate.split(" — ", 1)[1].strip())
    for candidate in tuple(candidates):
        if candidate.casefold().startswith(
            ("when ", "whenever ", "at the beginning of ")
        ) and ", " in candidate:
            candidates.append(candidate.split(", ", 1)[1].strip())
    return tuple(dict.fromkeys(candidates))


def _matches_spell_cast_characteristic_probe(
    source: str,
    *,
    card_record: Any,
    ability: Mapping[str, Any],
) -> bool:
    card_name, source_is_permanent, attachment_relation = (
        _source_face_context(card_record, ability)
    )
    binding = fixed_counter_trigger_binding(source, card_name=card_name)
    if (
        binding is None
        or binding.spell_subject is None
        or not isinstance(
            binding.spell_subject.characteristic_query,
            FixedSpellCastCharacteristicQuery,
        )
    ):
        return False
    face_id = str(ability.get("face_id") or "front")
    face = next(
        (
            value
            for value in card_record.faces
            if str(value.get("name") or "") == face_id
        ),
        None,
    )
    type_line = str(
        (face or {}).get("type_line") or card_record.type_line
    )
    card_types, _permanent, _spell, _support, _attachment = (
        _face_type_context(type_line)
    )
    template, effects, _target_schema, mechanics = _reviewed_effect_template(
        binding.body,
        card_name=card_name,
        source_is_permanent=source_is_permanent,
        source_card_types=tuple(sorted(card_types)),
        source_attachment_relation=attachment_relation,
    )
    return bool(
        template is not None
        and (
            effects
            or FIXED_NONREPEATING_MODAL_MECHANIC in mechanics
        )
    )


def _source_face_context(
    card_record: Any,
    ability: Mapping[str, Any],
) -> tuple[str, bool | None, Any]:
    face_id = str(ability.get("face_id") or "front")
    if face_id == "front":
        face_name = str(card_record.name)
        type_line = str(card_record.type_line)
    else:
        face = next(
            (
                value
                for value in card_record.faces
                if str(value.get("name") or "") == face_id
            ),
            None,
        )
        if face is None:
            face_name = str(card_record.name)
            type_line = str(card_record.type_line)
        else:
            face_name = str(face.get("name") or card_record.name)
            type_line = str(face.get("type_line") or card_record.type_line)
    _types, _permanent, _spell, support_source, attachment = (
        _face_type_context(type_line)
    )
    return face_name, support_source, attachment


def _matches_probe(
    probe_id: str,
    source: str,
    *,
    card_record: Any | None = None,
    ability: Mapping[str, Any] | None = None,
) -> bool:
    if probe_id == _PROBE_TOKEN:
        return any(
            fixed_token_creation_effect_template(body) is not None
            for body in _token_instruction_candidates(source)
        )
    if probe_id == _PROBE_EXILE:
        return any(
            targeted_exile_effect_template(body) is not None
            or public_zone_move_effect_template(body) is not None
            for body in _exile_instruction_candidates(source)
        )
    if probe_id == _PROBE_OPTIONAL_EFFECT:
        compile_effect = partial(
            _reviewed_atomic_effect_template,
            card_name="Cohort source",
            source_is_permanent=True,
        )
        return any(
            fixed_optional_effect_template(
                body,
                compile_effect=compile_effect,
            )
            is not None
            for body in _optional_effect_instruction_candidates(source)
        )
    if probe_id == _PROBE_REGENERATION:
        if card_record is None or ability is None:
            raise WorkSelectionCohortMeasurementError(
                "Fixed-regeneration measurement requires card context"
            )
        card_name, source_is_permanent, attachment_relation = (
            _source_face_context(card_record, ability)
        )
        return any(
            fixed_regeneration_effect_template(
                body,
                card_name=card_name,
                source_is_permanent=source_is_permanent,
                source_attachment_relation=attachment_relation,
            )
            is not None
            or (
                (template := destruction_effect_template(body)) is not None
                and template.regeneration_prohibited
            )
            for body in _fixed_regeneration_instruction_candidates(source)
        )
    if probe_id == _PROBE_SPELL_CAST_CHARACTERISTIC:
        if card_record is None or ability is None:
            raise WorkSelectionCohortMeasurementError(
                "Spell-cast characteristic measurement requires card context"
            )
        return _matches_spell_cast_characteristic_probe(
            source,
            card_record=card_record,
            ability=ability,
        )
    raise WorkSelectionCohortMeasurementError(
        f"Unknown cohort measurement probe: {probe_id}"
    )


def _measurement(
    *,
    frontier: Mapping[str, Any],
    bundle: Mapping[str, Any],
    cards_by_oracle_id: Mapping[str, Any],
    coverage: Mapping[str, Any],
    cohort_fingerprint: str,
) -> dict[str, Any]:
    bundle_id = str(bundle["bundle_id"])
    probe_id = str(bundle["measurement_probe_id"])
    if probe_id not in _PROBE_IDS:
        raise WorkSelectionCohortMeasurementError(
            f"Unknown cohort measurement probe: {probe_id}"
        )
    member_ids = {str(value) for value in bundle["member_family_ids"]}
    matched_abilities = 0
    matched_cards: dict[str, int] = {}
    cards_with_unmatched_member_ability: set[str] = set()
    for card in frontier.get("cards", []):
        oracle_id = str(card.get("oracle_id") or "")
        record = cards_by_oracle_id.get(oracle_id)
        if record is None:
            raise WorkSelectionCohortMeasurementError(
                f"Cohort measurement lacks pinned card {oracle_id}"
            )
        for ability in card.get("abilities", []):
            family_ids = {
                str(value)
                for value in ability.get("blockers", {}).get(
                    "canonical_family_ids", []
                )
            }
            if not family_ids.intersection(member_ids) or not family_ids <= member_ids:
                continue
            if not _matches_probe(
                probe_id,
                _source_line(record, ability),
                card_record=record,
                ability=ability,
            ):
                cards_with_unmatched_member_ability.add(oracle_id)
                continue
            matched_abilities += 1
            matched_cards[oracle_id] = len(
                set(card.get("minimum_known_blocker_set", [])) - member_ids
            )
    complete_cards = sum(
        count == 0 and oracle_id not in cards_with_unmatched_member_ability
        for oracle_id, count in matched_cards.items()
    )
    reaches_floor = (
        complete_cards >= int(coverage["minimum_complete_card_gain"])
        or matched_abilities >= int(coverage["minimum_exact_ability_gain"])
        or matched_abilities
        >= int(coverage["minimum_material_residual_reduction"])
    )
    return {
        "measurement_id": "measurement:" + bundle_id.split(":", 1)[-1],
        "bundle_id": bundle_id,
        "probe_id": probe_id,
        "cohort_fingerprint": cohort_fingerprint,
        "affected_commander_cards": len(matched_cards),
        "complete_card_gain": complete_cards,
        "one_additional_blocker_cards": sum(
            count == 1 for count in matched_cards.values()
        ),
        "two_additional_blocker_cards": sum(
            count == 2 for count in matched_cards.values()
        ),
        "exact_ability_gain": matched_abilities,
        "material_residual_reduction": matched_abilities,
        "decision": (
            "bounded_executable" if reaches_floor else "retired_below_harvest_floor"
        ),
        "grants_gameplay_trust": False,
    }


def build_work_selection_cohort_measurements(
    *,
    frontier: Mapping[str, Any],
    bundle_policies: Sequence[Mapping[str, Any]],
    cards_by_oracle_id: Mapping[str, Any],
    coverage: Mapping[str, Any],
    cohort_fingerprints: Mapping[str, str],
) -> dict[str, Any]:
    measurements = [
        _measurement(
            frontier=frontier,
            bundle=bundle,
            cards_by_oracle_id=cards_by_oracle_id,
            coverage=coverage,
            cohort_fingerprint=cohort_fingerprints[str(bundle["bundle_id"])],
        )
        for bundle in bundle_policies
        if bundle.get("measurement_probe_id") is not None
    ]
    snapshot = frontier.get("card_data_snapshot")
    oracle_source = (
        str(snapshot.get("oracle_source_sha256") or "")
        if isinstance(snapshot, Mapping)
        else ""
    )
    payload = {
        "schema_version": COHORT_MEASUREMENT_SCHEMA_VERSION,
        "algorithm_version": COHORT_MEASUREMENT_ALGORITHM_VERSION,
        "frontier_fingerprint": str(frontier.get("fingerprint") or ""),
        "oracle_source_sha256": oracle_source,
        "measurements": measurements,
    }
    payload["fingerprint"] = stable_hash(payload)
    return payload


__all__ = ["build_work_selection_cohort_measurements"]
