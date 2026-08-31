from __future__ import annotations

from functools import lru_cache, partial
import re
from typing import Any, Mapping, Sequence

from quorune.abilities import parse_activated_abilities
from quorune.compiler.activated_zone_change_costs import (
    fixed_activated_zone_change_cost,
)
from quorune.compiler.exile_templates import targeted_exile_effect_template
from quorune.compiler.damage_templates import source_pronoun_damage_effect_template
from quorune.compiler.destruction_templates import destruction_effect_template
from quorune.compiler.optional_effect_templates import (
    fixed_optional_effect_template,
)
from quorune.compiler.optional_payment_templates import (
    FIXED_OPTIONAL_MANA_PAYMENT_MECHANIC,
    fixed_optional_mana_payment_template,
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
    fixed_typed_event_effect_trigger_node,
)
from quorune.compiler.fixed_homogeneous_target_sets import (
    FIXED_HOMOGENEOUS_TARGET_SET_MECHANIC,
)
from quorune.compiler.fixed_library_selection_templates import (
    fixed_library_selection_effect_template,
)
from quorune.morph import (
    compile_fixed_mana_face_down_method,
    DISGUISE_CAST_METHOD,
    MEGAMORPH_CAST_METHOD,
)
from quorune.compiler.continuous_templates import (
    attached_fixed_characteristics_handler,
    fixed_power_toughness_anthem_handler,
    fixed_query_characteristic_grant_handler,
    fixed_query_keyword_grant_handler,
    fixed_public_state_characteristics_handler,
)
from quorune.compiler.query_characteristic_templates import (
    query_power_toughness_definition_handler,
    query_self_characteristics_handler,
)
from quorune.compiler.ir_model import SourceSpan
from quorune.compiler.token_templates import fixed_token_creation_effect_template
from quorune.oracle_ir import (
    _face_type_context,
    _effect_template,
    _reviewed_atomic_effect_template,
    _reviewed_effect_template,
    _source_self_zone_trigger_match,
    _without_parenthetical_reminder,
    compile_oracle_card,
)
from quorune.rules.capabilities import load_default_capability_registry
from quorune.work_selection_evidence import (
    COHORT_MEASUREMENT_ALGORITHM_VERSION,
    COHORT_MEASUREMENT_SCHEMA_VERSION,
    WorkSelectionCohortMeasurementError,
)
from quorune.work_selection_common import stable_hash


_PROBE_TOKEN = "fixed-token-creation-existing-owner-v1"
_PROBE_EXILE = "fixed-exile-existing-owner-v1"
_PROBE_OPTIONAL_EFFECT = "fixed-optional-effect-choice-existing-owner-v1"
_PROBE_TYPED_PUBLIC_EVENT_EFFECT_TRIGGER = (
    "typed-public-event-effect-trigger-existing-owner-v1"
)
_PROBE_OPTIONAL_MANA_PAYMENT = (
    "fixed-optional-mana-payment-trigger-existing-owner-v1"
)
_PROBE_REGENERATION = "fixed-regeneration-existing-owner-v1"
_PROBE_SPELL_CAST_CHARACTERISTIC = (
    "fixed-spell-cast-characteristic-trigger-existing-owner-v2"
)
_PROBE_TYPED_SPELL_CAST_FACT_PREDICATE = (
    "typed-spell-cast-fact-predicate-existing-owner-v1"
)
_PROBE_FIXED_HOMOGENEOUS_TARGET_SET = (
    "fixed-homogeneous-target-set-existing-owner-v1"
)
_PROBE_FIXED_LIBRARY_SELECTION = (
    "fixed-library-selection-existing-owner-v1"
)
_PROBE_FIXED_CONTROLLED_CHARACTERISTIC = (
    "fixed-controlled-characteristic-effect-existing-owner-v1"
)
_PROBE_FIXED_PUBLIC_STATE_CHARACTERISTIC = (
    "fixed-public-state-characteristic-existing-owner-v1"
)
_PROBE_TYPED_PUBLIC_STATE_CHARACTERISTIC_QUERY = (
    "typed-public-state-characteristic-query-existing-owner-v1"
)
_PROBE_FIXED_BATTLEFIELD_QUERY_CHARACTERISTIC = (
    "fixed-battlefield-query-characteristics-existing-owner-v1"
)
_PROBE_TYPED_QUERY_SELF_CHARACTERISTIC = (
    "typed-query-self-characteristic-existing-owner-v1"
)
_PROBE_QUERY_GATED_SELF_CHARACTERISTIC = (
    "query-gated-self-characteristic-existing-owner-v1"
)
_PROBE_QUERY_POWER_TOUGHNESS_DEFINITION = (
    "query-power-toughness-definition-existing-owner-v1"
)
_PROBE_ATTACHED_CHARACTERISTIC_CLOSURE = (
    "attached-characteristic-closure-existing-owner-v1"
)
_PROBE_FIXED_FACE_DOWN_LIFECYCLE = (
    "fixed-face-down-lifecycle-existing-owner-v1"
)
_PROBE_FIXED_ACTIVATION_ZONE_CHANGE_PREDICATES = (
    "fixed-activation-zone-change-predicates-existing-owner-v1"
)
_PROBE_FIXED_SOURCE_PRONOUN_DAMAGE_TRIGGER = (
    "fixed-source-pronoun-damage-trigger-existing-owner-v1"
)
_PROBE_TRIGGER_ABILITY_WORD_CARRIER = (
    "trigger-ability-word-carrier-existing-owner-v1"
)
_PROBE_IDS = {
    _PROBE_EXILE,
    _PROBE_FIXED_CONTROLLED_CHARACTERISTIC,
    _PROBE_FIXED_BATTLEFIELD_QUERY_CHARACTERISTIC,
    _PROBE_FIXED_PUBLIC_STATE_CHARACTERISTIC,
    _PROBE_TYPED_PUBLIC_STATE_CHARACTERISTIC_QUERY,
    _PROBE_FIXED_SOURCE_PRONOUN_DAMAGE_TRIGGER,
    _PROBE_TYPED_QUERY_SELF_CHARACTERISTIC,
    _PROBE_TYPED_PUBLIC_EVENT_EFFECT_TRIGGER,
    _PROBE_QUERY_GATED_SELF_CHARACTERISTIC,
    _PROBE_QUERY_POWER_TOUGHNESS_DEFINITION,
    _PROBE_ATTACHED_CHARACTERISTIC_CLOSURE,
    _PROBE_FIXED_ACTIVATION_ZONE_CHANGE_PREDICATES,
    _PROBE_FIXED_FACE_DOWN_LIFECYCLE,
    _PROBE_FIXED_HOMOGENEOUS_TARGET_SET,
    _PROBE_FIXED_LIBRARY_SELECTION,
    _PROBE_OPTIONAL_EFFECT,
    _PROBE_OPTIONAL_MANA_PAYMENT,
    _PROBE_REGENERATION,
    _PROBE_SPELL_CAST_CHARACTERISTIC,
    _PROBE_TOKEN,
    _PROBE_TYPED_SPELL_CAST_FACT_PREDICATE,
    _PROBE_TRIGGER_ABILITY_WORD_CARRIER,
}

_FIXED_TARGET_SET_COMPOSITION_MECHANICS = {
    "fixed-effect-clause-sequence",
    "fixed-nonrepeating-modal",
    "fixed-optional-effect-choice",
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


def _matches_query_self_characteristic_probe(
    probe_id: str,
    source: str,
    *,
    source_name: str,
) -> bool:
    """Keep the completed count grammar distinct from its gated extension."""

    if probe_id not in {
        _PROBE_TYPED_QUERY_SELF_CHARACTERISTIC,
        _PROBE_QUERY_GATED_SELF_CHARACTERISTIC,
        _PROBE_QUERY_POWER_TOUGHNESS_DEFINITION,
    }:
        raise WorkSelectionCohortMeasurementError(
            f"Unsupported query-characteristic probe: {probe_id}"
        )
    if probe_id == _PROBE_QUERY_POWER_TOUGHNESS_DEFINITION:
        return (
            query_power_toughness_definition_handler(
                source,
                source_name=source_name,
            )
            is not None
        )
    if query_self_characteristics_handler(source, source_name=source_name) is None:
        return False
    normalized = re.sub(
        r"\s+\([^()]*\)\.?$", "", source.strip()
    ).strip()
    ability_word = re.fullmatch(
        r"[A-Z][A-Za-z0-9' ]{0,80} — (?P<body>.+)",
        normalized,
    )
    if ability_word is not None:
        normalized = ability_word.group("body").strip()
    marker = normalized.casefold().rfind(" as long as ")
    query_gated = normalized.casefold().startswith("as long as ") or (
        marker > 0 and " gets " not in normalized[:marker].casefold()
    )
    return query_gated == (
        probe_id == _PROBE_QUERY_GATED_SELF_CHARACTERISTIC
    )


def _is_typed_public_state_characteristic_compilation(
    compiled: tuple[str, Mapping[str, Any], Any] | None,
) -> bool:
    if compiled is None:
        return False
    template_id, descriptor, _capabilities = compiled
    if template_id == "continuous-fixed-public-state-characteristics-v1":
        condition = descriptor.get("source_condition")
        return bool(
            isinstance(condition, Mapping)
            and condition.get("schema_version") == 2
            and condition.get("kind")
            in {
                "attached_matches_query",
                "query_count_at_least",
                "source_matches_query",
            }
        )
    if template_id not in {
        "continuous-fixed-query-anthem-v2",
        "continuous-fixed-query-characteristic-grant-v1",
        "continuous-fixed-query-keyword-grant-v2",
    }:
        return False
    condition = descriptor.get("condition")
    predicate = (
        condition.get("predicate")
        if isinstance(condition, Mapping)
        else None
    )
    state = (
        predicate.get("state_predicate")
        if isinstance(predicate, Mapping)
        else None
    )
    return bool(
        isinstance(state, Mapping)
        and any(
            state.get(field) is not None
            for field in (
                "attacking",
                "blocking",
                "enchanted",
                "equipped",
                "modified",
                "tapped",
            )
        )
    )


def _matches_typed_public_state_characteristic_query(
    source: str,
    *,
    source_name: str,
) -> bool:
    return any(
        _is_typed_public_state_characteristic_compilation(compiled)
        for compiled in (
            fixed_public_state_characteristics_handler(
                source,
                source_name=source_name,
            ),
            fixed_query_characteristic_grant_handler(source),
            fixed_query_keyword_grant_handler(source),
            fixed_power_toughness_anthem_handler(source),
        )
    )


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


def _fixed_library_selection_instruction_candidates(
    line: str,
) -> tuple[str, ...]:
    normalized = " ".join(line.strip().split())
    candidates = [normalized]
    for marker in ("look at the top ", "reveal the top "):
        index = normalized.casefold().find(marker)
        if index >= 0:
            candidates.append(normalized[index:])
    return tuple(dict.fromkeys(candidates))


def _matches_integrated_spell_cast_probe(
    source: str,
    *,
    card_record: Any,
    ability: Mapping[str, Any],
    extended_only: bool,
) -> bool:
    card_name, source_is_permanent, attachment_relation = (
        _source_face_context(card_record, ability)
    )
    binding = fixed_counter_trigger_binding(source, card_name=card_name)
    subject = binding.spell_subject if binding is not None else None
    if (
        subject is None
        or (
            extended_only
            and not subject.extended
        )
        or (
            not extended_only
            and (
                subject.extended
                or not isinstance(
                    subject.characteristic_query,
                    FixedSpellCastCharacteristicQuery,
                )
            )
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
    residuals = []
    node = fixed_typed_event_effect_trigger_node(
        node_id="probe:n1",
        line=source,
        material_line=source,
        span=SourceSpan(start=0, end=len(source), line=1),
        card_name=card_name,
        trusted_mechanics=frozenset(),
        capability_registry=_spell_cast_probe_capability_registry(),
        capability_profile="commander_review",
        residuals=residuals,
        effect_template=partial(
            _reviewed_effect_template,
            source_is_permanent=source_is_permanent,
            source_card_types=tuple(sorted(card_types)),
            source_attachment_relation=attachment_relation,
        ),
    )
    return bool(node is not None and node.exact and not residuals)


def _matches_spell_cast_characteristic_probe(
    source: str,
    *,
    card_record: Any,
    ability: Mapping[str, Any],
) -> bool:
    return _matches_integrated_spell_cast_probe(
        source,
        card_record=card_record,
        ability=ability,
        extended_only=False,
    )


def _matches_typed_spell_cast_fact_probe(
    source: str,
    *,
    card_record: Any,
    ability: Mapping[str, Any],
) -> bool:
    return _matches_integrated_spell_cast_probe(
        _without_parenthetical_reminder(source),
        card_record=card_record,
        ability=ability,
        extended_only=True,
    )


def _matches_typed_public_event_effect_trigger_probe(
    source: str,
    *,
    card_record: Any,
    ability: Mapping[str, Any],
) -> bool:
    """Require both the selected event carrier and its integrated typed body."""

    material = _without_parenthetical_reminder(source)
    card_name, source_is_permanent, attachment_relation = (
        _source_face_context(card_record, ability)
    )
    binding = fixed_counter_trigger_binding(material, card_name=card_name)
    if binding is None or not (
        binding.public_template_id is not None
        or re.fullmatch(
            r"Whenever (?:an opponent casts a (?:white|blue|black|red|green) "
            r"spell during your turn|you cast an instant spell during your "
            r"main phase), .+",
            material,
            re.IGNORECASE,
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
    residuals = []
    node = fixed_typed_event_effect_trigger_node(
        node_id="probe:n1",
        line=source,
        material_line=material,
        span=SourceSpan(start=0, end=len(source), line=1),
        card_name=card_name,
        trusted_mechanics=frozenset(),
        capability_registry=_spell_cast_probe_capability_registry(),
        capability_profile="commander_review",
        residuals=residuals,
        effect_template=partial(
            _reviewed_effect_template,
            source_is_permanent=source_is_permanent,
            source_card_types=tuple(sorted(card_types)),
            source_attachment_relation=attachment_relation,
        ),
    )
    return bool(node is not None and node.exact and not residuals)


@lru_cache(maxsize=1)
def _spell_cast_probe_capability_registry():
    return load_default_capability_registry()


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
    if probe_id == _PROBE_FIXED_ACTIVATION_ZONE_CHANGE_PREDICATES:
        if card_record is None or ability is None:
            raise WorkSelectionCohortMeasurementError(
                "Fixed activation zone-change measurement requires card context"
            )
        source_name, _source_is_permanent, _attachment_relation = (
            _source_face_context(card_record, ability)
        )
        parsed = parse_activated_abilities(
            card_name=source_name,
            oracle_text=source,
            keywords=getattr(card_record, "keywords", ()),
        )
        if len(parsed) != 1:
            return False
        lowered = fixed_activated_zone_change_cost(parsed[0])
        return bool(
            lowered.compiled_cost
            and len(lowered.choices) == 1
            and lowered.choices[0].fixed_zone_change_cost() is not None
        )
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
        if card_record is None or ability is None:
            raise WorkSelectionCohortMeasurementError(
                "Fixed optional-effect measurement requires card context"
            )
        card_name, source_is_permanent, _attachment_relation = (
            _source_face_context(card_record, ability)
        )
        binding = fixed_counter_trigger_binding(
            _without_parenthetical_reminder(source),
            card_name=card_name,
        )
        if binding is None:
            return False
        compile_effect = partial(
            _reviewed_atomic_effect_template,
            card_name=card_name,
            source_is_permanent=source_is_permanent,
        )
        return any(
            fixed_optional_effect_template(
                body,
                compile_effect=compile_effect,
            )
            is not None
            for body in _optional_effect_instruction_candidates(binding.body)
        )
    if probe_id == _PROBE_TYPED_PUBLIC_EVENT_EFFECT_TRIGGER:
        if card_record is None or ability is None:
            raise WorkSelectionCohortMeasurementError(
                "Typed public-event measurement requires card context"
            )
        return _matches_typed_public_event_effect_trigger_probe(
            source,
            card_record=card_record,
            ability=ability,
        )
    if probe_id == _PROBE_OPTIONAL_MANA_PAYMENT:
        if card_record is None or ability is None:
            raise WorkSelectionCohortMeasurementError(
                "Fixed optional mana-payment measurement requires card context"
            )
        card_name, source_is_permanent, attachment_relation = (
            _source_face_context(card_record, ability)
        )
        material = _without_parenthetical_reminder(source)
        binding = fixed_counter_trigger_binding(
            material,
            card_name=card_name,
        )
        source_self = _source_self_zone_trigger_match(
            material,
            card_name=card_name,
        )
        body = (
            binding.body
            if binding is not None
            else source_self.group("body")
            if source_self is not None
            else ""
        )
        return bool(
            body
            and fixed_optional_mana_payment_template(
                body,
                compile_effect=partial(
                    _effect_template,
                    card_name=card_name,
                    source_is_permanent=source_is_permanent,
                    source_attachment_relation=attachment_relation,
                ),
            )
            is not None
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
    if probe_id == _PROBE_TYPED_SPELL_CAST_FACT_PREDICATE:
        if card_record is None or ability is None:
            raise WorkSelectionCohortMeasurementError(
                "Typed spell-cast fact measurement requires card context"
            )
        return _matches_typed_spell_cast_fact_probe(
            source,
            card_record=card_record,
            ability=ability,
        )
    if probe_id == _PROBE_FIXED_SOURCE_PRONOUN_DAMAGE_TRIGGER:
        if card_record is None or ability is None:
            raise WorkSelectionCohortMeasurementError(
                "Fixed source-pronoun damage-trigger measurement requires "
                "card context"
            )
        card_name, _source_is_permanent, _attachment_relation = (
            _source_face_context(card_record, ability)
        )
        binding = _source_self_zone_trigger_match(
            _without_parenthetical_reminder(source),
            card_name=card_name,
        )
        return bool(
            binding is not None
            and binding.group("event").casefold() in {"enters", "dies"}
            and source_pronoun_damage_effect_template(binding.group("body"))
            is not None
        )
    if probe_id == _PROBE_FIXED_BATTLEFIELD_QUERY_CHARACTERISTIC:
        return any(
            handler(source) is not None
            for handler in (
                fixed_query_characteristic_grant_handler,
                fixed_query_keyword_grant_handler,
                fixed_power_toughness_anthem_handler,
            )
        )
    if probe_id == _PROBE_ATTACHED_CHARACTERISTIC_CLOSURE:
        if card_record is None or ability is None:
            raise WorkSelectionCohortMeasurementError(
                "Attached-characteristic measurement requires card context"
            )
        source_name, _source_is_permanent, _attachment_relation = (
            _source_face_context(card_record, ability)
        )
        return (
            attached_fixed_characteristics_handler(
                source,
                source_name=source_name,
            )
            is not None
        )
    if probe_id == _PROBE_FIXED_FACE_DOWN_LIFECYCLE:
        material = _without_parenthetical_reminder(source)
        method = compile_fixed_mana_face_down_method(material)
        if method is not None:
            return method.method in {
                DISGUISE_CAST_METHOD,
                MEGAMORPH_CAST_METHOD,
            }
        binding = fixed_counter_trigger_binding(material)
        return bool(
            binding is not None
            and binding.event.value == "permanent.turned_face_up"
            and binding.variant == "source_turned_face_up"
        )
    if probe_id == _PROBE_TYPED_PUBLIC_STATE_CHARACTERISTIC_QUERY:
        if card_record is None or ability is None:
            raise WorkSelectionCohortMeasurementError(
                "Typed public-state characteristic measurement requires card context"
            )
        source_name, _source_is_permanent, _attachment_relation = (
            _source_face_context(card_record, ability)
        )
        return _matches_typed_public_state_characteristic_query(
            source,
            source_name=source_name,
        )
    if probe_id == _PROBE_FIXED_LIBRARY_SELECTION:
        return any(
            fixed_library_selection_effect_template(body) is not None
            for body in _fixed_library_selection_instruction_candidates(
                source
            )
        )
    if probe_id == _PROBE_TRIGGER_ABILITY_WORD_CARRIER:
        material = _without_parenthetical_reminder(source)
        match = re.fullmatch(
            r"[A-Za-z][A-Za-z ']+\s+[—-]\s+(?P<body>.+)",
            material,
        )
        return bool(
            match is not None
            and re.match(
                r"^(?:when|whenever|at the beginning of)\b",
                match.group("body"),
                re.IGNORECASE,
            )
        )
    raise WorkSelectionCohortMeasurementError(
        f"Unknown cohort measurement probe: {probe_id}"
    )


def _trigger_ability_word_carrier_measurement(
    *,
    frontier: Mapping[str, Any],
    bundle_id: str,
    probe_id: str,
    member_ids: set[str],
    cards_by_oracle_id: Mapping[str, Any],
    coverage: Mapping[str, Any],
    cohort_fingerprint: str,
) -> dict[str, Any]:
    """Measure trigger carriers that become exact through existing owners."""

    registry = load_default_capability_registry()
    matched_abilities = 0
    matched_cards: dict[str, int] = {}
    complete_cards: set[str] = set()
    for card in frontier.get("cards", []):
        oracle_id = str(card.get("oracle_id") or "")
        record = cards_by_oracle_id.get(oracle_id)
        if record is None:
            raise WorkSelectionCohortMeasurementError(
                f"Cohort measurement lacks pinned card {oracle_id}"
            )
        candidates = [
            ability
            for ability in card.get("abilities", [])
            if ability.get("status") != "exact"
            and _matches_probe(
                probe_id,
                _source_line(record, ability),
                card_record=record,
                ability=ability,
            )
        ]
        if not candidates:
            continue
        compiled = compile_oracle_card(
            record,
            capability_registry=registry,
            capability_profile="commander_review",
        )
        nodes = {
            node.node_id: node
            for face in compiled.faces
            for node in face.nodes
        }
        card_matches = sum(
            node is not None
            and node.exact
            and node.kind == "triggered_ability"
            for ability in candidates
            for node in (nodes.get(str(ability.get("ability_id") or "")),)
        )
        if not card_matches:
            continue
        matched_abilities += card_matches
        matched_cards[oracle_id] = len(
            set(card.get("minimum_known_blocker_set", [])) - member_ids
        )
        if compiled.status == "exact":
            complete_cards.add(oracle_id)
    reaches_floor = (
        len(complete_cards) >= int(coverage["minimum_complete_card_gain"])
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
        "complete_card_gain": len(complete_cards),
        "one_additional_blocker_cards": sum(
            count == 1 for count in matched_cards.values()
        ),
        "two_additional_blocker_cards": sum(
            count == 2 for count in matched_cards.values()
        ),
        "exact_ability_gain": matched_abilities,
        "material_residual_reduction": matched_abilities,
        "decision": (
            "bounded_executable"
            if reaches_floor
            else "retired_below_harvest_floor"
        ),
        "grants_gameplay_trust": False,
    }


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
    if probe_id == _PROBE_FIXED_HOMOGENEOUS_TARGET_SET:
        return _fixed_homogeneous_target_set_measurement(
            frontier=frontier,
            bundle_id=bundle_id,
            probe_id=probe_id,
            member_ids=member_ids,
            cards_by_oracle_id=cards_by_oracle_id,
            coverage=coverage,
            cohort_fingerprint=cohort_fingerprint,
        )
    if probe_id == _PROBE_FIXED_LIBRARY_SELECTION:
        return _fixed_library_selection_measurement(
            frontier=frontier,
            bundle_id=bundle_id,
            probe_id=probe_id,
            member_ids=member_ids,
            cards_by_oracle_id=cards_by_oracle_id,
            coverage=coverage,
            cohort_fingerprint=cohort_fingerprint,
        )
    if probe_id == _PROBE_TRIGGER_ABILITY_WORD_CARRIER:
        return _trigger_ability_word_carrier_measurement(
            frontier=frontier,
            bundle_id=bundle_id,
            probe_id=probe_id,
            member_ids=member_ids,
            cards_by_oracle_id=cards_by_oracle_id,
            coverage=coverage,
            cohort_fingerprint=cohort_fingerprint,
        )
    if probe_id == _PROBE_FIXED_BATTLEFIELD_QUERY_CHARACTERISTIC:
        return _fixed_battlefield_query_characteristic_measurement(
            frontier=frontier,
            bundle_id=bundle_id,
            probe_id=probe_id,
            member_ids=member_ids,
            cards_by_oracle_id=cards_by_oracle_id,
            coverage=coverage,
            cohort_fingerprint=cohort_fingerprint,
        )
    if probe_id == _PROBE_ATTACHED_CHARACTERISTIC_CLOSURE:
        return _attached_characteristic_closure_measurement(
            frontier=frontier,
            bundle_id=bundle_id,
            probe_id=probe_id,
            member_ids=member_ids,
            cards_by_oracle_id=cards_by_oracle_id,
            coverage=coverage,
            cohort_fingerprint=cohort_fingerprint,
        )
    if probe_id == _PROBE_FIXED_FACE_DOWN_LIFECYCLE:
        return _fixed_face_down_lifecycle_measurement(
            frontier=frontier,
            bundle_id=bundle_id,
            probe_id=probe_id,
            member_ids=member_ids,
            cards_by_oracle_id=cards_by_oracle_id,
            coverage=coverage,
            cohort_fingerprint=cohort_fingerprint,
        )
    if probe_id == _PROBE_FIXED_CONTROLLED_CHARACTERISTIC:
        return _fixed_controlled_characteristic_measurement(
            frontier=frontier,
            bundle_id=bundle_id,
            probe_id=probe_id,
            member_ids=member_ids,
            cards_by_oracle_id=cards_by_oracle_id,
            coverage=coverage,
            cohort_fingerprint=cohort_fingerprint,
        )
    if probe_id == _PROBE_FIXED_PUBLIC_STATE_CHARACTERISTIC:
        return _fixed_public_state_characteristic_measurement(
            frontier=frontier,
            bundle_id=bundle_id,
            probe_id=probe_id,
            member_ids=member_ids,
            cards_by_oracle_id=cards_by_oracle_id,
            coverage=coverage,
            cohort_fingerprint=cohort_fingerprint,
        )
    if probe_id == _PROBE_TYPED_PUBLIC_STATE_CHARACTERISTIC_QUERY:
        return _typed_public_state_characteristic_query_measurement(
            frontier=frontier,
            bundle_id=bundle_id,
            probe_id=probe_id,
            member_ids=member_ids,
            cards_by_oracle_id=cards_by_oracle_id,
            coverage=coverage,
            cohort_fingerprint=cohort_fingerprint,
        )
    if probe_id in {
        _PROBE_TYPED_QUERY_SELF_CHARACTERISTIC,
        _PROBE_QUERY_GATED_SELF_CHARACTERISTIC,
        _PROBE_QUERY_POWER_TOUGHNESS_DEFINITION,
    }:
        return _typed_query_self_characteristic_measurement(
            frontier=frontier,
            bundle_id=bundle_id,
            probe_id=probe_id,
            member_ids=member_ids,
            cards_by_oracle_id=cards_by_oracle_id,
            coverage=coverage,
            cohort_fingerprint=cohort_fingerprint,
        )
    if probe_id == _PROBE_FIXED_SOURCE_PRONOUN_DAMAGE_TRIGGER:
        return _fixed_source_pronoun_damage_trigger_measurement(
            frontier=frontier,
            bundle_id=bundle_id,
            probe_id=probe_id,
            member_ids=member_ids,
            cards_by_oracle_id=cards_by_oracle_id,
            coverage=coverage,
            cohort_fingerprint=cohort_fingerprint,
        )
    if probe_id == _PROBE_OPTIONAL_MANA_PAYMENT:
        return _fixed_optional_mana_payment_measurement(
            frontier=frontier,
            bundle_id=bundle_id,
            probe_id=probe_id,
            member_ids=member_ids,
            cards_by_oracle_id=cards_by_oracle_id,
            coverage=coverage,
            cohort_fingerprint=cohort_fingerprint,
        )
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
            if (
                not family_ids.intersection(member_ids)
                or not family_ids <= member_ids
            ):
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


def _fixed_battlefield_query_characteristic_measurement(
    *,
    frontier: Mapping[str, Any],
    bundle_id: str,
    probe_id: str,
    member_ids: set[str],
    cards_by_oracle_id: Mapping[str, Any],
    coverage: Mapping[str, Any],
    cohort_fingerprint: str,
) -> dict[str, Any]:
    """Measure exact static query characteristics through integrated owners."""

    registry = load_default_capability_registry()
    handler_ids = {
        "continuous.anthem.fixed-query.v2",
        "continuous.ability.fixed-query-keyword-grant.v1",
        "continuous.characteristics.fixed-query-grant.v1",
    }
    matched_abilities = 0
    matched_cards: dict[str, int] = {}
    complete_cards: set[str] = set()
    for card in frontier.get("cards", []):
        oracle_id = str(card.get("oracle_id") or "")
        record = cards_by_oracle_id.get(oracle_id)
        if record is None:
            raise WorkSelectionCohortMeasurementError(
                f"Cohort measurement lacks pinned card {oracle_id}"
            )
        candidates = []
        for ability in card.get("abilities", []):
            family_ids = {
                str(value)
                for value in ability.get("blockers", {}).get(
                    "canonical_family_ids", []
                )
            }
            if (
                ability.get("status") == "exact"
                or not family_ids
                or not family_ids.intersection(member_ids)
                or not family_ids <= member_ids
                or not _matches_probe(
                    probe_id,
                    _source_line(record, ability),
                    card_record=record,
                    ability=ability,
                )
            ):
                continue
            candidates.append(ability)
        if not candidates:
            continue
        compiled = compile_oracle_card(
            record,
            capability_registry=registry,
            capability_profile="commander_review",
        )
        nodes = {
            node.node_id: node
            for face in compiled.faces
            for node in face.nodes
        }
        card_matches = sum(
            node is not None
            and node.exact
            and any(
                descriptor.get("handler_id") in handler_ids
                for descriptor in node.handlers
            )
            for ability in candidates
            for node in (nodes.get(str(ability.get("ability_id") or "")),)
        )
        if not card_matches:
            continue
        matched_abilities += card_matches
        matched_cards[oracle_id] = len(
            set(card.get("minimum_known_blocker_set", [])) - member_ids
        )
        if compiled.status == "exact":
            complete_cards.add(oracle_id)
    reaches_floor = (
        len(complete_cards) >= int(coverage["minimum_complete_card_gain"])
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
        "complete_card_gain": len(complete_cards),
        "one_additional_blocker_cards": sum(
            count == 1 for count in matched_cards.values()
        ),
        "two_additional_blocker_cards": sum(
            count == 2 for count in matched_cards.values()
        ),
        "exact_ability_gain": matched_abilities,
        "material_residual_reduction": matched_abilities,
        "decision": (
            "bounded_executable"
            if reaches_floor
            else "retired_below_harvest_floor"
        ),
        "grants_gameplay_trust": False,
    }


def _attached_characteristic_closure_measurement(
    *,
    frontier: Mapping[str, Any],
    bundle_id: str,
    probe_id: str,
    member_ids: set[str],
    cards_by_oracle_id: Mapping[str, Any],
    coverage: Mapping[str, Any],
    cohort_fingerprint: str,
) -> dict[str, Any]:
    """Measure attached characteristics through the integrated compiler."""

    registry = load_default_capability_registry()
    matched_abilities = 0
    matched_cards: dict[str, int] = {}
    complete_cards: set[str] = set()
    for card in frontier.get("cards", []):
        oracle_id = str(card.get("oracle_id") or "")
        record = cards_by_oracle_id.get(oracle_id)
        if record is None:
            raise WorkSelectionCohortMeasurementError(
                f"Cohort measurement lacks pinned card {oracle_id}"
            )
        candidates = []
        for ability in card.get("abilities", []):
            family_ids = {
                str(value)
                for value in ability.get("blockers", {}).get(
                    "canonical_family_ids", []
                )
            }
            if (
                ability.get("status") == "exact"
                or not family_ids
                or not family_ids.intersection(member_ids)
                or not family_ids <= member_ids
                or not _matches_probe(
                    probe_id,
                    _source_line(record, ability),
                    card_record=record,
                    ability=ability,
                )
            ):
                continue
            candidates.append(ability)
        if not candidates:
            continue
        compiled = compile_oracle_card(
            record,
            capability_registry=registry,
            capability_profile="commander_review",
        )
        nodes = {
            node.node_id: node
            for face in compiled.faces
            for node in face.nodes
        }
        card_matches = sum(
            node is not None
            and node.exact
            and any(
                descriptor.get("handler_id")
                == "continuous.attached.fixed-characteristics.v1"
                for descriptor in node.handlers
            )
            for ability in candidates
            for node in (nodes.get(str(ability.get("ability_id") or "")),)
        )
        if not card_matches:
            continue
        matched_abilities += card_matches
        matched_cards[oracle_id] = len(
            set(card.get("minimum_known_blocker_set", [])) - member_ids
        )
        if compiled.status == "exact":
            complete_cards.add(oracle_id)
    reaches_floor = (
        len(complete_cards) >= int(coverage["minimum_complete_card_gain"])
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
        "complete_card_gain": len(complete_cards),
        "one_additional_blocker_cards": sum(
            count == 1 for count in matched_cards.values()
        ),
        "two_additional_blocker_cards": sum(
            count == 2 for count in matched_cards.values()
        ),
        "exact_ability_gain": matched_abilities,
        "material_residual_reduction": matched_abilities,
        "decision": (
            "bounded_executable"
            if reaches_floor
            else "retired_below_harvest_floor"
        ),
        "grants_gameplay_trust": False,
    }


def _fixed_face_down_lifecycle_measurement(
    *,
    frontier: Mapping[str, Any],
    bundle_id: str,
    probe_id: str,
    member_ids: set[str],
    cards_by_oracle_id: Mapping[str, Any],
    coverage: Mapping[str, Any],
    cohort_fingerprint: str,
) -> dict[str, Any]:
    """Measure fixed methods and source face-up triggers as one lifecycle."""

    registry = load_default_capability_registry()
    matched_abilities = 0
    matched_cards: dict[str, int] = {}
    complete_cards: set[str] = set()
    for card in frontier.get("cards", []):
        oracle_id = str(card.get("oracle_id") or "")
        record = cards_by_oracle_id.get(oracle_id)
        if record is None:
            raise WorkSelectionCohortMeasurementError(
                f"Cohort measurement lacks pinned card {oracle_id}"
            )
        candidates = []
        for ability in card.get("abilities", []):
            family_ids = {
                str(value)
                for value in ability.get("blockers", {}).get(
                    "canonical_family_ids", []
                )
            }
            if (
                ability.get("status") == "exact"
                or not family_ids
                or not family_ids.intersection(member_ids)
                or not family_ids <= member_ids
                or not _matches_probe(
                    probe_id,
                    _source_line(record, ability),
                    card_record=record,
                    ability=ability,
                )
            ):
                continue
            candidates.append(ability)
        if not candidates:
            continue
        compiled = compile_oracle_card(
            record,
            capability_registry=registry,
            capability_profile="commander_review",
        )
        nodes = {
            node.node_id: node
            for face in compiled.faces
            for node in face.nodes
        }
        card_matches = sum(
            node is not None
            and node.exact
            and (
                str(node.template_id or "").startswith(
                    ("disguise-fixed-mana-", "megamorph-fixed-mana-")
                )
                or (
                    node.event == "permanent.turned_face_up"
                    and node.event_condition
                    == {
                        "field": "card",
                        "op": "eq",
                        "value": "$source.ref",
                    }
                )
            )
            for ability in candidates
            for node in (nodes.get(str(ability.get("ability_id") or "")),)
        )
        if not card_matches:
            continue
        matched_abilities += card_matches
        matched_cards[oracle_id] = len(
            set(card.get("minimum_known_blocker_set", [])) - member_ids
        )
        if compiled.status == "exact":
            complete_cards.add(oracle_id)
    reaches_floor = (
        len(complete_cards) >= int(coverage["minimum_complete_card_gain"])
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
        "complete_card_gain": len(complete_cards),
        "one_additional_blocker_cards": sum(
            count == 1 for count in matched_cards.values()
        ),
        "two_additional_blocker_cards": sum(
            count == 2 for count in matched_cards.values()
        ),
        "exact_ability_gain": matched_abilities,
        "material_residual_reduction": matched_abilities,
        "decision": (
            "bounded_executable"
            if reaches_floor
            else "retired_below_harvest_floor"
        ),
        "grants_gameplay_trust": False,
    }


def _fixed_source_pronoun_damage_trigger_measurement(
    *,
    frontier: Mapping[str, Any],
    bundle_id: str,
    probe_id: str,
    member_ids: set[str],
    cards_by_oracle_id: Mapping[str, Any],
    coverage: Mapping[str, Any],
    cohort_fingerprint: str,
) -> dict[str, Any]:
    """Measure exact source-self damage triggers through the integrated owner."""

    registry = load_default_capability_registry()
    matched_abilities = 0
    matched_cards: dict[str, int] = {}
    complete_cards: set[str] = set()
    for card in frontier.get("cards", []):
        oracle_id = str(card.get("oracle_id") or "")
        candidates = []
        for ability in card.get("abilities", []):
            family_ids = {
                str(value)
                for value in ability.get("blockers", {}).get(
                    "canonical_family_ids", []
                )
            }
            if (
                ability.get("status") == "exact"
                or not family_ids
                or not family_ids.intersection(member_ids)
                or not family_ids <= member_ids
            ):
                continue
            candidates.append(ability)
        if not candidates:
            continue
        record = cards_by_oracle_id.get(oracle_id)
        if record is None:
            raise WorkSelectionCohortMeasurementError(
                f"Cohort measurement lacks pinned card {oracle_id}"
            )
        compiled = compile_oracle_card(
            record,
            capability_registry=registry,
            capability_profile="commander_review",
        )
        nodes = {
            node.node_id: node
            for face in compiled.faces
            for node in face.nodes
        }
        card_matches = 0
        for ability in candidates:
            source = _source_line(record, ability)
            node = nodes.get(str(ability.get("ability_id") or ""))
            if (
                node is None
                or not node.exact
                or node.event
                not in {"permanent.enter.self", "creature.dies.self"}
                or not _matches_probe(
                    probe_id,
                    source,
                    card_record=record,
                    ability=ability,
                )
            ):
                continue
            card_matches += 1
        if not card_matches:
            continue
        matched_abilities += card_matches
        matched_cards[oracle_id] = len(
            set(card.get("minimum_known_blocker_set", [])) - member_ids
        )
        if compiled.status == "exact":
            complete_cards.add(oracle_id)
    reaches_floor = (
        len(complete_cards) >= int(coverage["minimum_complete_card_gain"])
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
        "complete_card_gain": len(complete_cards),
        "one_additional_blocker_cards": sum(
            count == 1 for count in matched_cards.values()
        ),
        "two_additional_blocker_cards": sum(
            count == 2 for count in matched_cards.values()
        ),
        "exact_ability_gain": matched_abilities,
        "material_residual_reduction": matched_abilities,
        "decision": (
            "bounded_executable"
            if reaches_floor
            else "retired_below_harvest_floor"
        ),
        "grants_gameplay_trust": False,
    }


def _fixed_optional_mana_payment_measurement(
    *,
    frontier: Mapping[str, Any],
    bundle_id: str,
    probe_id: str,
    member_ids: set[str],
    cards_by_oracle_id: Mapping[str, Any],
    coverage: Mapping[str, Any],
    cohort_fingerprint: str,
) -> dict[str, Any]:
    """Measure fixed payment triggers through the integrated typed owners."""

    registry = load_default_capability_registry()
    matched_abilities = 0
    matched_cards: dict[str, int] = {}
    complete_cards: set[str] = set()
    for card in frontier.get("cards", []):
        oracle_id = str(card.get("oracle_id") or "")
        record = cards_by_oracle_id.get(oracle_id)
        if record is None:
            raise WorkSelectionCohortMeasurementError(
                f"Cohort measurement lacks pinned card {oracle_id}"
            )
        candidates = []
        for ability in card.get("abilities", []):
            family_ids = {
                str(value)
                for value in ability.get("blockers", {}).get(
                    "canonical_family_ids", []
                )
            }
            if (
                ability.get("status") == "exact"
                or not family_ids
                or not family_ids.intersection(member_ids)
                or not family_ids <= member_ids
                or not _matches_probe(
                    probe_id,
                    _source_line(record, ability),
                    card_record=record,
                    ability=ability,
                )
            ):
                continue
            candidates.append(ability)
        if not candidates:
            continue
        compiled = compile_oracle_card(
            record,
            capability_registry=registry,
            capability_profile="commander_review",
        )
        nodes = {
            node.node_id: node
            for face in compiled.faces
            for node in face.nodes
        }
        card_matches = sum(
            node is not None
            and node.exact
            and FIXED_OPTIONAL_MANA_PAYMENT_MECHANIC in node.mechanics
            for ability in candidates
            for node in (nodes.get(str(ability.get("ability_id") or "")),)
        )
        if not card_matches:
            continue
        matched_abilities += card_matches
        matched_cards[oracle_id] = len(
            set(card.get("minimum_known_blocker_set", [])) - member_ids
        )
        if compiled.status == "exact":
            complete_cards.add(oracle_id)
    reaches_floor = (
        len(complete_cards) >= int(coverage["minimum_complete_card_gain"])
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
        "complete_card_gain": len(complete_cards),
        "one_additional_blocker_cards": sum(
            count == 1 for count in matched_cards.values()
        ),
        "two_additional_blocker_cards": sum(
            count == 2 for count in matched_cards.values()
        ),
        "exact_ability_gain": matched_abilities,
        "material_residual_reduction": matched_abilities,
        "decision": (
            "bounded_executable"
            if reaches_floor
            else "retired_below_harvest_floor"
        ),
        "grants_gameplay_trust": False,
    }


def _target_group_maximum(node: Any) -> int:
    schema = node.target_schema
    if not isinstance(schema, Mapping):
        return 0
    for field in ("count", "max", "up_to"):
        value = schema.get(field)
        if type(value) is int:
            return value
    return 0


def _contains_fixed_library_selection(value: Any) -> bool:
    if isinstance(value, Mapping):
        if value.get("op") == "fixed_library_selection":
            return True
        return any(
            _contains_fixed_library_selection(child)
            for child in value.values()
        )
    if isinstance(value, (list, tuple)):
        return any(
            _contains_fixed_library_selection(child) for child in value
        )
    return False


def _fixed_library_selection_measurement(
    *,
    frontier: Mapping[str, Any],
    bundle_id: str,
    probe_id: str,
    member_ids: set[str],
    cards_by_oracle_id: Mapping[str, Any],
    coverage: Mapping[str, Any],
    cohort_fingerprint: str,
) -> dict[str, Any]:
    """Measure complete promotions through the integrated partition owner."""

    registry = load_default_capability_registry()
    matched_abilities = 0
    matched_cards: dict[str, int] = {}
    complete_cards: set[str] = set()
    for card in frontier.get("cards", []):
        oracle_id = str(card.get("oracle_id") or "")
        record = cards_by_oracle_id.get(oracle_id)
        if record is None:
            raise WorkSelectionCohortMeasurementError(
                f"Cohort measurement lacks pinned card {oracle_id}"
            )
        candidates = [
            ability
            for ability in card.get("abilities", [])
            if ability.get("status") != "exact"
            and _matches_probe(
                probe_id,
                _source_line(record, ability),
                card_record=record,
                ability=ability,
            )
        ]
        if not candidates:
            continue
        compiled = compile_oracle_card(
            record,
            capability_registry=registry,
            capability_profile="commander_review",
        )
        nodes = {
            node.node_id: node
            for face in compiled.faces
            for node in face.nodes
        }
        card_matches = sum(
            node is not None
            and node.exact
            and _contains_fixed_library_selection(node.to_dict())
            for ability in candidates
            for node in (nodes.get(str(ability.get("ability_id") or "")),)
        )
        if not card_matches:
            continue
        matched_abilities += card_matches
        matched_cards[oracle_id] = len(
            set(card.get("minimum_known_blocker_set", [])) - member_ids
        )
        if compiled.status == "exact":
            complete_cards.add(oracle_id)
    reaches_floor = (
        len(complete_cards) >= int(coverage["minimum_complete_card_gain"])
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
        "complete_card_gain": len(complete_cards),
        "one_additional_blocker_cards": sum(
            count == 1 for count in matched_cards.values()
        ),
        "two_additional_blocker_cards": sum(
            count == 2 for count in matched_cards.values()
        ),
        "exact_ability_gain": matched_abilities,
        "material_residual_reduction": matched_abilities,
        "decision": (
            "bounded_executable"
            if reaches_floor
            else "retired_below_harvest_floor"
        ),
        "grants_gameplay_trust": False,
    }


def _fixed_homogeneous_target_set_measurement(
    *,
    frontier: Mapping[str, Any],
    bundle_id: str,
    probe_id: str,
    member_ids: set[str],
    cards_by_oracle_id: Mapping[str, Any],
    coverage: Mapping[str, Any],
    cohort_fingerprint: str,
) -> dict[str, Any]:
    """Measure only direct plural target-set promotions through the full owner."""

    registry = load_default_capability_registry()
    matched_abilities = 0
    matched_cards: dict[str, int] = {}
    complete_cards: set[str] = set()
    for card in frontier.get("cards", []):
        oracle_id = str(card.get("oracle_id") or "")
        candidates = []
        for ability in card.get("abilities", []):
            family_ids = {
                str(value)
                for value in ability.get("blockers", {}).get(
                    "canonical_family_ids", []
                )
            }
            if (
                ability.get("status") == "exact"
                or not family_ids
                or not family_ids.intersection(member_ids)
                or not family_ids <= member_ids
            ):
                continue
            candidates.append(ability)
        if not candidates:
            continue
        record = cards_by_oracle_id.get(oracle_id)
        if record is None:
            raise WorkSelectionCohortMeasurementError(
                f"Cohort measurement lacks pinned card {oracle_id}"
            )
        compiled = compile_oracle_card(
            record,
            capability_registry=registry,
            capability_profile="commander_review",
        )
        nodes = {
            node.node_id: node
            for face in compiled.faces
            for node in face.nodes
        }
        card_matches = 0
        for ability in candidates:
            node = nodes.get(str(ability.get("ability_id") or ""))
            mechanics = set(node.mechanics) if node is not None else set()
            if (
                node is None
                or not node.exact
                or FIXED_HOMOGENEOUS_TARGET_SET_MECHANIC not in mechanics
                or mechanics.intersection(_FIXED_TARGET_SET_COMPOSITION_MECHANICS)
                or _target_group_maximum(node) < 2
            ):
                continue
            card_matches += 1
        if not card_matches:
            continue
        matched_abilities += card_matches
        matched_cards[oracle_id] = len(
            set(card.get("minimum_known_blocker_set", [])) - member_ids
        )
        if compiled.status == "exact":
            complete_cards.add(oracle_id)
    reaches_floor = (
        len(complete_cards) >= int(coverage["minimum_complete_card_gain"])
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
        "complete_card_gain": len(complete_cards),
        "one_additional_blocker_cards": sum(
            count == 1 for count in matched_cards.values()
        ),
        "two_additional_blocker_cards": sum(
            count == 2 for count in matched_cards.values()
        ),
        "exact_ability_gain": matched_abilities,
        "material_residual_reduction": matched_abilities,
        "decision": (
            "bounded_executable"
            if reaches_floor
            else "retired_below_harvest_floor"
        ),
        "grants_gameplay_trust": False,
    }


def _could_be_fixed_controlled_characteristic(source: str) -> bool:
    normalized = " ".join(source.casefold().split())
    return (
        "until end of turn" in normalized
        and "you control" in normalized
        and any(
            marker in normalized
            for marker in (" gain ", " get ", " have ")
        )
    )


def _contains_fixed_controlled_characteristic_effect(value: Any) -> bool:
    if isinstance(value, Mapping):
        if (
            value.get("op")
            == "modify_all_matching_permanents_until_end_of_turn"
            and isinstance(value.get("keywords"), list)
            and bool(value["keywords"])
            and type(value.get("power")) is int
            and type(value.get("toughness")) is int
        ):
            return True
        return any(
            _contains_fixed_controlled_characteristic_effect(child)
            for child in value.values()
        )
    if isinstance(value, (list, tuple)):
        return any(
            _contains_fixed_controlled_characteristic_effect(child)
            for child in value
        )
    return False


def _fixed_controlled_characteristic_measurement(
    *,
    frontier: Mapping[str, Any],
    bundle_id: str,
    probe_id: str,
    member_ids: set[str],
    cards_by_oracle_id: Mapping[str, Any],
    coverage: Mapping[str, Any],
    cohort_fingerprint: str,
) -> dict[str, Any]:
    """Measure integrated fixed controlled characteristic promotions."""

    registry = load_default_capability_registry()
    matched_abilities = 0
    matched_cards: dict[str, int] = {}
    complete_cards: set[str] = set()
    for card in frontier.get("cards", []):
        oracle_id = str(card.get("oracle_id") or "")
        record = cards_by_oracle_id.get(oracle_id)
        if record is None:
            raise WorkSelectionCohortMeasurementError(
                f"Cohort measurement lacks pinned card {oracle_id}"
            )
        candidates = [
            ability
            for ability in card.get("abilities", [])
            if ability.get("status") != "exact"
            and _could_be_fixed_controlled_characteristic(
                _source_line(record, ability)
            )
        ]
        if not candidates:
            continue
        compiled = compile_oracle_card(
            record,
            capability_registry=registry,
            capability_profile="commander_review",
        )
        nodes = {
            node.node_id: node
            for face in compiled.faces
            for node in face.nodes
        }
        card_matches = sum(
            node is not None
            and node.exact
            and _contains_fixed_controlled_characteristic_effect(
                node.to_dict()
            )
            for ability in candidates
            for node in (nodes.get(str(ability.get("ability_id") or "")),)
        )
        if not card_matches:
            continue
        matched_abilities += card_matches
        matched_cards[oracle_id] = len(
            set(card.get("minimum_known_blocker_set", [])) - member_ids
        )
        if compiled.status == "exact":
            complete_cards.add(oracle_id)
    reaches_floor = (
        len(complete_cards) >= int(coverage["minimum_complete_card_gain"])
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
        "complete_card_gain": len(complete_cards),
        "one_additional_blocker_cards": sum(
            count == 1 for count in matched_cards.values()
        ),
        "two_additional_blocker_cards": sum(
            count == 2 for count in matched_cards.values()
        ),
        "exact_ability_gain": matched_abilities,
        "material_residual_reduction": matched_abilities,
        "decision": (
            "bounded_executable"
            if reaches_floor
            else "retired_below_harvest_floor"
        ),
        "grants_gameplay_trust": False,
    }


def _fixed_public_state_characteristic_measurement(
    *,
    frontier: Mapping[str, Any],
    bundle_id: str,
    probe_id: str,
    member_ids: set[str],
    cards_by_oracle_id: Mapping[str, Any],
    coverage: Mapping[str, Any],
    cohort_fingerprint: str,
) -> dict[str, Any]:
    """Measure exact promotions through the integrated conditional owner."""

    registry = load_default_capability_registry()
    matched_abilities = 0
    matched_cards: dict[str, int] = {}
    complete_cards: set[str] = set()
    for card in frontier.get("cards", []):
        oracle_id = str(card.get("oracle_id") or "")
        record = cards_by_oracle_id.get(oracle_id)
        if record is None:
            raise WorkSelectionCohortMeasurementError(
                f"Cohort measurement lacks pinned card {oracle_id}"
            )
        candidates = []
        for ability in card.get("abilities", []):
            if ability.get("status") == "exact":
                continue
            source_name, _source_is_permanent, _attachment = (
                _source_face_context(record, ability)
            )
            if fixed_public_state_characteristics_handler(
                _source_line(record, ability),
                source_name=source_name,
            ) is not None:
                candidates.append(ability)
        if not candidates:
            continue
        compiled = compile_oracle_card(
            record,
            capability_registry=registry,
            capability_profile="commander_review",
        )
        nodes = {
            node.node_id: node
            for face in compiled.faces
            for node in face.nodes
        }
        card_matches = sum(
            node is not None
            and node.exact
            and node.template_id
            == "continuous-fixed-public-state-characteristics-v1"
            for ability in candidates
            for node in (nodes.get(str(ability.get("ability_id") or "")),)
        )
        if not card_matches:
            continue
        matched_abilities += card_matches
        matched_cards[oracle_id] = len(
            set(card.get("minimum_known_blocker_set", [])) - member_ids
        )
        if compiled.status == "exact":
            complete_cards.add(oracle_id)
    reaches_floor = (
        len(complete_cards) >= int(coverage["minimum_complete_card_gain"])
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
        "complete_card_gain": len(complete_cards),
        "one_additional_blocker_cards": sum(
            count == 1 for count in matched_cards.values()
        ),
        "two_additional_blocker_cards": sum(
            count == 2 for count in matched_cards.values()
        ),
        "exact_ability_gain": matched_abilities,
        "material_residual_reduction": matched_abilities,
        "decision": (
            "bounded_executable"
            if reaches_floor
            else "retired_below_harvest_floor"
        ),
        "grants_gameplay_trust": False,
    }


def _typed_public_state_characteristic_query_measurement(
    *,
    frontier: Mapping[str, Any],
    bundle_id: str,
    probe_id: str,
    member_ids: set[str],
    cards_by_oracle_id: Mapping[str, Any],
    coverage: Mapping[str, Any],
    cohort_fingerprint: str,
) -> dict[str, Any]:
    """Measure one shared public-state/query characteristic boundary."""

    registry = load_default_capability_registry()
    matched_abilities = 0
    matched_cards: dict[str, int] = {}
    complete_cards: set[str] = set()
    for card in frontier.get("cards", []):
        oracle_id = str(card.get("oracle_id") or "")
        record = cards_by_oracle_id.get(oracle_id)
        if record is None:
            raise WorkSelectionCohortMeasurementError(
                f"Cohort measurement lacks pinned card {oracle_id}"
            )
        candidates = [
            ability
            for ability in card.get("abilities", [])
            if ability.get("status") != "exact"
            and _matches_probe(
                probe_id,
                _source_line(record, ability),
                card_record=record,
                ability=ability,
            )
        ]
        if not candidates:
            continue
        compiled = compile_oracle_card(
            record,
            capability_registry=registry,
            capability_profile="commander_review",
        )
        nodes = {
            node.node_id: node
            for face in compiled.faces
            for node in face.nodes
        }
        card_matches = sum(
            node is not None
            and node.exact
            and node.template_id
            in {
                "continuous-fixed-public-state-characteristics-v1",
                "continuous-fixed-query-anthem-v2",
                "continuous-fixed-query-characteristic-grant-v1",
                "continuous-fixed-query-keyword-grant-v2",
            }
            for ability in candidates
            for node in (nodes.get(str(ability.get("ability_id") or "")),)
        )
        if not card_matches:
            continue
        matched_abilities += card_matches
        matched_cards[oracle_id] = len(
            set(card.get("minimum_known_blocker_set", [])) - member_ids
        )
        if compiled.status == "exact":
            complete_cards.add(oracle_id)
    reaches_floor = (
        len(complete_cards) >= int(coverage["minimum_complete_card_gain"])
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
        "complete_card_gain": len(complete_cards),
        "one_additional_blocker_cards": sum(
            count == 1 for count in matched_cards.values()
        ),
        "two_additional_blocker_cards": sum(
            count == 2 for count in matched_cards.values()
        ),
        "exact_ability_gain": matched_abilities,
        "material_residual_reduction": matched_abilities,
        "decision": (
            "bounded_executable"
            if reaches_floor
            else "retired_below_harvest_floor"
        ),
        "grants_gameplay_trust": False,
    }


def _typed_query_self_characteristic_measurement(
    *,
    frontier: Mapping[str, Any],
    bundle_id: str,
    probe_id: str,
    member_ids: set[str],
    cards_by_oracle_id: Mapping[str, Any],
    coverage: Mapping[str, Any],
    cohort_fingerprint: str,
) -> dict[str, Any]:
    """Measure exact promotions through the typed layer-5 query owner."""

    registry = load_default_capability_registry()
    matched_abilities = 0
    matched_cards: dict[str, int] = {}
    complete_cards: set[str] = set()
    for card in frontier.get("cards", []):
        oracle_id = str(card.get("oracle_id") or "")
        record = cards_by_oracle_id.get(oracle_id)
        if record is None:
            raise WorkSelectionCohortMeasurementError(
                f"Cohort measurement lacks pinned card {oracle_id}"
            )
        candidates = []
        for ability in card.get("abilities", []):
            if ability.get("status") == "exact":
                continue
            source_name, _source_is_permanent, _attachment = (
                _source_face_context(record, ability)
            )
            if _matches_query_self_characteristic_probe(
                probe_id,
                _source_line(record, ability),
                source_name=source_name,
            ):
                candidates.append(ability)
        if not candidates:
            continue
        compiled = compile_oracle_card(
            record,
            capability_registry=registry,
            capability_profile="commander_review",
        )
        nodes = {
            node.node_id: node
            for face in compiled.faces
            for node in face.nodes
        }
        expected_template_id = (
            "continuous-query-power-toughness-definition-v1"
            if probe_id == _PROBE_QUERY_POWER_TOUGHNESS_DEFINITION
            else "continuous-self-query-characteristics-v1"
        )
        card_matches = sum(
            node is not None
            and node.exact
            and node.template_id == expected_template_id
            for ability in candidates
            for node in (nodes.get(str(ability.get("ability_id") or "")),)
        )
        if not card_matches:
            continue
        matched_abilities += card_matches
        matched_cards[oracle_id] = len(
            set(card.get("minimum_known_blocker_set", [])) - member_ids
        )
        if compiled.status == "exact":
            complete_cards.add(oracle_id)
    reaches_floor = (
        len(complete_cards) >= int(coverage["minimum_complete_card_gain"])
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
        "complete_card_gain": len(complete_cards),
        "one_additional_blocker_cards": sum(
            count == 1 for count in matched_cards.values()
        ),
        "two_additional_blocker_cards": sum(
            count == 2 for count in matched_cards.values()
        ),
        "exact_ability_gain": matched_abilities,
        "material_residual_reduction": matched_abilities,
        "decision": (
            "bounded_executable"
            if reaches_floor
            else "retired_below_harvest_floor"
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
    transition_measurements: Sequence[Mapping[str, Any]] = (),
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
        "transition_measurements": [
            dict(value) for value in transition_measurements
        ],
    }
    payload["fingerprint"] = stable_hash(payload)
    return payload


__all__ = ["build_work_selection_cohort_measurements"]
