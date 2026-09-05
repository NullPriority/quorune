from __future__ import annotations

from functools import lru_cache, partial
import re
from typing import Any, Mapping, Sequence

from quorune.ability_fragments import CURRENT_ABILITY_FRAGMENT_COVERAGE
from quorune.abilities import parse_activated_abilities
from quorune.characteristic_evaluation import type_parts
from quorune.commander_pairing import (
    COMMANDER_PAIRING_TEMPLATE_ID,
    PARTNER_WITH_SEARCH_TEMPLATE_ID,
    partner_with_spec_for_material_line,
)
from quorune.compiler.activated_zone_change_costs import (
    fixed_activated_zone_change_cost,
)
from quorune.compiler.cast_cost_modifier_templates import (
    self_spell_cost_reduction_handler,
)
from quorune.compiler.exile_templates import targeted_exile_effect_template
from quorune.compiler.damage_templates import source_pronoun_damage_effect_template
from quorune.compiler.fixed_all_damage_prevention import (
    fixed_all_damage_prevention_specs,
)
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
from quorune.compiler.public_cast_cost_modifiers import (
    public_cast_cost_modifier_template,
)
from quorune.compiler.regeneration_templates import (
    fixed_regeneration_effect_template,
)
from quorune.compiler.fixed_counter_trigger_nodes import (
    FixedSpellCastCharacteristicQuery,
    fixed_counter_trigger_binding,
    fixed_typed_event_effect_trigger_node,
)
from quorune.compiler.fixed_cast_lifecycles import fixed_cast_lifecycle_spec
from quorune.compiler.fixed_entry_return_requirements import (
    fixed_entry_return_requirement_spec,
)
from quorune.compiler.fixed_homogeneous_target_sets import (
    FIXED_HOMOGENEOUS_TARGET_SET_MECHANIC,
)
from quorune.compiler.fixed_source_combat_growth import (
    FIXED_SOURCE_COMBAT_GROWTH_TEMPLATE_IDS,
    SOURCE_ZONE_OBJECT,
    fixed_source_combat_growth_effect_template,
)
from quorune.compiler.fixed_library_selection_templates import (
    fixed_library_selection_effect_template,
)
from quorune.morph import (
    compile_fixed_mana_face_down_method,
    DISGUISE_CAST_METHOD,
    MEGAMORPH_CAST_METHOD,
)
from quorune.read_ahead import saga_chapter_line
from quorune.compiler.continuous_templates import (
    attached_fixed_characteristics_handler,
    attached_quoted_ability_text,
    fixed_query_quoted_ability_text,
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
from quorune.targets import TargetGroup
from quorune.semantic_runtime.cast_costs import (
    SELF_SPELL_COST_REDUCTION_HANDLER_ID,
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
    "fixed-activation-zone-change-predicates-existing-owner-v2"
)
_PROBE_ORDINARY_SAGA_CHAPTER_PROGRAMS = (
    "ordinary-saga-chapter-programs-existing-owner-v1"
)
_PROBE_FIXED_SOURCE_PRONOUN_DAMAGE_TRIGGER = (
    "fixed-source-pronoun-damage-trigger-existing-owner-v1"
)
_PROBE_TRIGGER_ABILITY_WORD_CARRIER = (
    "trigger-ability-word-carrier-existing-owner-v1"
)
_PROBE_SELF_SPELL_COST_REDUCTION = (
    "fixed-self-spell-cost-reduction-existing-owner-v1"
)
_PROBE_FIXED_ALL_DAMAGE_PREVENTION = (
    "fixed-all-damage-prevention-scope-existing-owner-v1"
)
_PROBE_ATTACHED_QUOTED_ABILITY_GRANT = (
    "attached-quoted-ability-grant-existing-owner-v1"
)
_PROBE_SOURCE_COMBAT_GROWTH_TRIGGER = (
    "fixed-source-combat-growth-trigger-existing-owner-v1"
)
_PROBE_PUBLIC_STATIC_CAST_COST_MODIFIER = (
    "public-static-cast-cost-modifier-existing-owner-v1"
)
_PROBE_FIXED_CAST_LIFECYCLES = "fixed-cast-lifecycle-existing-owner-v1"
_PROBE_FIXED_CASTING_SURFACE = "fixed-casting-surface-existing-owner-v2"
_PROBE_FIXED_ENTRY_RETURN_REQUIREMENTS = (
    "fixed-entry-return-requirement-existing-owner-v1"
)
_PROBE_FIXED_PUBLIC_NUMERIC_DAMAGE_TARGET = (
    "fixed-public-numeric-damage-target-existing-owner-v1"
)
_PROBE_TYPED_LEVELER_BANDS = "typed-leveler-bands-existing-owner-v1"
_PROBE_SPELL_HISTORY_TRANSFORMATIONS = (
    "spell-history-transformations-existing-owner-v1"
)
_PROBE_FIXED_TOKEN_PRODUCTION = "fixed-token-production-existing-owner-v1"
_PROBE_TYPED_QUOTED_ABILITY_GRANT = (
    "typed-quoted-ability-grant-existing-owner-v1"
)
_PROBE_PARTNER_WITH = "partner-with-existing-owner-v1"
_FIXED_TOKEN_PRODUCTION_FAMILIES = frozenset(
    {
        "activated_effect:create-token",
        "activated_effect:unparsed-investigate",
        "effect_clause:create-token",
        "effect_clause:unparsed-investigate-three-times",
        "keyword_dependency:investigate",
    }
)
_CONTINUOUS_LAYER_FAMILY = (
    "continuous_layer:continuous-effect-layers-and-dependencies"
)
_ATTACHED_GRANT_HIGH_RISK_CAPABILITY_PAIRS = frozenset(
    {
        tuple(
            sorted(
                (
                    "attachment.equip.fixed_mana",
                    "zone.change.destination_replacement",
                )
            )
        ),
        tuple(sorted(("attachment.equip.fixed_mana", "zone.mill.fixed"))),
        tuple(
            sorted(
                (
                    "target.public.player_or_damageable_permanent",
                    "zone.self_move.activated",
                )
            )
        ),
    }
)
_PROBE_IDS = {
    _PROBE_ATTACHED_QUOTED_ABILITY_GRANT,
    _PROBE_EXILE,
    _PROBE_FIXED_CONTROLLED_CHARACTERISTIC,
    _PROBE_FIXED_CAST_LIFECYCLES,
    _PROBE_FIXED_CASTING_SURFACE,
    _PROBE_FIXED_ENTRY_RETURN_REQUIREMENTS,
    _PROBE_FIXED_PUBLIC_NUMERIC_DAMAGE_TARGET,
    _PROBE_TYPED_LEVELER_BANDS,
    _PROBE_SPELL_HISTORY_TRANSFORMATIONS,
    _PROBE_FIXED_TOKEN_PRODUCTION,
    _PROBE_TYPED_QUOTED_ABILITY_GRANT,
    _PROBE_PARTNER_WITH,
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
    _PROBE_FIXED_ALL_DAMAGE_PREVENTION,
    _PROBE_FIXED_FACE_DOWN_LIFECYCLE,
    _PROBE_FIXED_HOMOGENEOUS_TARGET_SET,
    _PROBE_FIXED_LIBRARY_SELECTION,
    _PROBE_OPTIONAL_EFFECT,
    _PROBE_OPTIONAL_MANA_PAYMENT,
    _PROBE_PUBLIC_STATIC_CAST_COST_MODIFIER,
    _PROBE_ORDINARY_SAGA_CHAPTER_PROGRAMS,
    _PROBE_REGENERATION,
    _PROBE_SPELL_CAST_CHARACTERISTIC,
    _PROBE_SELF_SPELL_COST_REDUCTION,
    _PROBE_SOURCE_COMBAT_GROWTH_TRIGGER,
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


def _source_face_type_line(
    card_record: Any, ability: Mapping[str, Any]
) -> str:
    face_id = str(ability.get("face_id") or "front")
    if face_id == "front" or not card_record.faces:
        return str(card_record.type_line)
    for face in card_record.faces:
        if str(face.get("name") or "") == face_id:
            return str(face.get("type_line") or card_record.type_line)
    return str(card_record.type_line)


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


_FIXED_NUMERIC_TARGET_SOURCE = re.compile(
    r"\btarget [^.]+? with (?:power|toughness|power or toughness|"
    r"total power and toughness) \d+ or (?:less|greater)\b",
    re.IGNORECASE,
)
_FIXED_DAMAGE_HISTORY_TARGET_SOURCE = re.compile(
    r"\btarget creature that (?:was dealt damage|dealt damage(?: to you)?) "
    r"this turn\b",
    re.IGNORECASE,
)


def _matches_fixed_public_numeric_damage_target(source: str) -> bool:
    normalized = " ".join(source.split())
    return bool(
        _FIXED_NUMERIC_TARGET_SOURCE.search(normalized)
        or _FIXED_DAMAGE_HISTORY_TARGET_SOURCE.search(normalized)
    )


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
    if probe_id == _PROBE_FIXED_ALL_DAMAGE_PREVENTION:
        if card_record is None or ability is None:
            raise WorkSelectionCohortMeasurementError(
                "All-damage prevention measurement requires card context"
            )
        return bool(
            _fixed_all_damage_prevention_specs_for_ability(
                source,
                card_record=card_record,
                ability=ability,
            )
        )
    if probe_id == _PROBE_SOURCE_COMBAT_GROWTH_TRIGGER:
        if card_record is None or ability is None:
            raise WorkSelectionCohortMeasurementError(
                "Source combat-growth measurement requires card context"
            )
        card_name, _source_is_permanent, _attachment_relation = (
            _source_face_context(card_record, ability)
        )
        binding = fixed_counter_trigger_binding(
            _without_parenthetical_reminder(source),
            card_name=card_name,
        )
        return bool(
            binding is not None
            and fixed_source_combat_growth_effect_template(
                binding.body,
                event=binding.event.value,
                variant=binding.variant,
            )[0]
            in FIXED_SOURCE_COMBAT_GROWTH_TEMPLATE_IDS
        )
    if probe_id == _PROBE_SELF_SPELL_COST_REDUCTION:
        return self_spell_cost_reduction_handler(source) is not None
    if probe_id == _PROBE_PUBLIC_STATIC_CAST_COST_MODIFIER:
        return public_cast_cost_modifier_template(source) is not None
    if probe_id == _PROBE_FIXED_CAST_LIFECYCLES:
        return fixed_cast_lifecycle_spec(source) is not None
    if probe_id == _PROBE_FIXED_CASTING_SURFACE:
        return bool(
            fixed_cast_lifecycle_spec(source) is not None
            or public_cast_cost_modifier_template(source) is not None
        )
    if probe_id == _PROBE_FIXED_ENTRY_RETURN_REQUIREMENTS:
        return fixed_entry_return_requirement_spec(source) is not None
    if probe_id == _PROBE_FIXED_PUBLIC_NUMERIC_DAMAGE_TARGET:
        return _matches_fixed_public_numeric_damage_target(source)
    if probe_id == _PROBE_SPELL_HISTORY_TRANSFORMATIONS:
        material = _without_parenthetical_reminder(source).strip()
        return bool(
            re.fullmatch(r"(?:Daybound|Nightbound)\.?", material, re.IGNORECASE)
            or re.fullmatch(
                r"At the beginning of each upkeep, if (?:no spells were cast "
                r"last turn|a player cast two or more spells last turn), "
                r"transform .+?\.?",
                material,
                re.IGNORECASE,
            )
        )
    if probe_id == _PROBE_FIXED_TOKEN_PRODUCTION:
        material = _without_parenthetical_reminder(source).strip()
        if fixed_token_creation_effect_template(material) is not None:
            return True
        if card_record is None:
            return False
        afterlife_parts = tuple(
            part.strip().rstrip(".") for part in material.split(",")
        )
        afterlife_matches = tuple(
            re.fullmatch(
                r"Afterlife (?P<count>[1-9]\d*)",
                part,
                re.IGNORECASE,
            )
            for part in afterlife_parts
        )
        return bool(
            "Afterlife" in getattr(card_record, "keywords", ())
            and afterlife_matches
            and all(
                match is not None
                and int(match.group("count")) <= 20
                for match in afterlife_matches
            )
        )
    if probe_id == _PROBE_TYPED_QUOTED_ABILITY_GRANT:
        if card_record is None:
            return False
        source_name, _source_is_permanent, _attachment_relation = (
            _source_face_context(card_record, ability or {})
        )
        return bool(
            fixed_query_quoted_ability_text(source) is not None
            or attached_quoted_ability_text(
                source,
                source_name=source_name,
            )
            is not None
        )
    if probe_id == _PROBE_PARTNER_WITH:
        return (
            partner_with_spec_for_material_line(
                _without_parenthetical_reminder(source)
            )
            is not None
        )
    if probe_id == _PROBE_ORDINARY_SAGA_CHAPTER_PROGRAMS:
        if card_record is None or ability is None:
            raise WorkSelectionCohortMeasurementError(
                "Ordinary Saga chapter measurement requires card context"
            )
        _types, subtypes, _supertypes = type_parts(
            _source_face_type_line(card_record, ability)
        )
        return "saga" in subtypes and saga_chapter_line(source) is not None
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


_FIXED_ALL_DAMAGE_PREVENTION_FAMILIES = frozenset(
    {
        "continuous_layer:affected-player-ordering",
        "continuous_layer:continuous-effect-layers-and-dependencies",
        "event_binding:intervening-if-and-reflexive-trigger-grammar",
        "event_binding:normalized-event-binding",
        "replacement:damage-prevention",
        "replacement:replacement-applicability",
        "replacement:self-replacement-and-prevention-ordering",
        "target_or_choice:target-predicate",
    }
)


def _ability_family_ids(ability: Mapping[str, Any]) -> set[str]:
    return {
        str(family_id)
        for residual in ability.get("residuals", ())
        for family_id in residual.get("family_ids", ())
    }


def _fixed_all_damage_prevention_specs_for_ability(
    source: str,
    *,
    card_record: Any,
    ability: Mapping[str, Any],
) -> tuple[Any, ...] | None:
    if not _ability_family_ids(ability).issubset(
        _FIXED_ALL_DAMAGE_PREVENTION_FAMILIES
    ):
        return None
    source_name, _source_is_permanent, _attachment_relation = (
        _source_face_context(card_record, ability)
    )
    kind = str(ability.get("kind") or "")
    material = _without_parenthetical_reminder(source)
    bodies: list[str] = []
    if kind in {"spell_ability", "static_ability"}:
        bodies.append(material)
    elif kind == "activated_ability":
        parsed = parse_activated_abilities(
            card_name=source_name,
            oracle_text=material,
            keywords=getattr(card_record, "keywords", ()),
        )
        if len(parsed) == 1:
            bodies.append(parsed[0].effect_text)
    elif kind == "triggered_ability":
        binding = fixed_counter_trigger_binding(
            material,
            card_name=source_name,
        )
        if binding is not None:
            bodies.append(binding.body)
    expected_duration = (
        "static" if kind == "static_ability" else "until_end_of_turn"
    )
    for body in bodies:
        specs = fixed_all_damage_prevention_specs(
            body,
            card_name=source_name,
        )
        if specs is not None and all(
            spec.duration == expected_duration for spec in specs
        ):
            return specs
    return None


def _fixed_all_damage_prevention_measurement(
    *,
    frontier: Mapping[str, Any],
    bundle_id: str,
    probe_id: str,
    cards_by_oracle_id: Mapping[str, Any],
    coverage: Mapping[str, Any],
    cohort_fingerprint: str,
) -> dict[str, Any]:
    """Measure the closed static and turn-bound all-prevention grammar."""

    parsed_by_card: dict[str, list[Mapping[str, Any]]] = {}
    prevention_carriers_by_card: dict[str, list[Mapping[str, Any]]] = {}
    cards_by_id: dict[str, Mapping[str, Any]] = {}
    for card in frontier.get("cards", []):
        oracle_id = str(card.get("oracle_id") or "")
        record = cards_by_oracle_id.get(oracle_id)
        if record is None:
            raise WorkSelectionCohortMeasurementError(
                f"Cohort measurement lacks pinned card {oracle_id}"
            )
        cards_by_id[oracle_id] = card
        for ability in card.get("abilities", []):
            if ability.get("status") == "exact":
                continue
            source = _source_line(record, ability)
            if "prevent all" in source.casefold() and "damage" in source.casefold():
                prevention_carriers_by_card.setdefault(oracle_id, []).append(ability)
            if _fixed_all_damage_prevention_specs_for_ability(
                source,
                card_record=record,
                ability=ability,
            ) is not None:
                parsed_by_card.setdefault(oracle_id, []).append(ability)

    registry = load_default_capability_registry()
    matched_by_card: dict[str, list[Mapping[str, Any]]] = {}
    compiled_by_card: dict[str, Any] = {}
    for oracle_id, parsed in parsed_by_card.items():
        record = cards_by_oracle_id[oracle_id]
        compiled = compile_oracle_card(
            record,
            capability_registry=registry,
            capability_profile="commander_review",
        )
        represented_lines = {
            node.span.line
            for face in compiled.faces
            for node in face.nodes
            if node.exact
            and (
                any(
                    handler.get("handler_id") == "prevention.damage.all.v1"
                    for handler in node.handlers
                )
                or any(
                    effect.get("op") == "create_damage_prevention_shield"
                    and effect.get("scope") is not None
                    for effect in node.effects
                )
            )
        }
        represented = [
            ability
            for ability in parsed
            if int(ability["source_line"]) in represented_lines
        ]
        if represented:
            matched_by_card[oracle_id] = represented
            compiled_by_card[oracle_id] = compiled

    affected_carriers = sum(len(values) for values in matched_by_card.values())
    complete_cards = 0
    exact_siblings = 0
    remaining_sibling_nodes = 0
    expected_residual_reduction = 0
    one_additional = 0
    two_additional = 0
    unsupported_sibling_cards = 0
    unsupported_grammar_cards: set[str] = set()
    for oracle_id, matched in matched_by_card.items():
        card = cards_by_id[oracle_id]
        compiled = compiled_by_card[oracle_id]
        exact_siblings += sum(
            ability.get("status") == "exact"
            for ability in card.get("abilities", ())
        )
        remaining = [
            node
            for face in compiled.faces
            for node in face.nodes
            if not node.exact
        ]
        base_residuals = sum(
            max(1, len(ability.get("residuals", ())))
            for ability in card.get("abilities", ())
            if ability.get("status") != "exact"
        )
        expected_residual_reduction += max(
            0,
            base_residuals - len(compiled.material_residuals),
        )
        remaining_sibling_nodes += len(remaining)
        one_additional += len(remaining) == 1
        two_additional += len(remaining) == 2
        if compiled.status == "exact":
            complete_cards += 1
        if remaining:
            unsupported_sibling_cards += 1
        if any(
            ability not in matched
            for ability in prevention_carriers_by_card.get(oracle_id, ())
        ):
            unsupported_grammar_cards.add(oracle_id)
    unsupported_grammar_cards.update(
        set(prevention_carriers_by_card) - set(matched_by_card)
    )
    reaches_floor = (
        complete_cards >= int(coverage["minimum_complete_card_gain"])
        or affected_carriers >= int(coverage["minimum_exact_ability_gain"])
        or expected_residual_reduction
        >= int(coverage["minimum_material_residual_reduction"])
    )
    return {
        "measurement_id": "measurement:" + bundle_id.split(":", 1)[-1],
        "bundle_id": bundle_id,
        "probe_id": probe_id,
        "cohort_fingerprint": cohort_fingerprint,
        "affected_commander_cards": len(matched_by_card),
        "complete_card_gain": complete_cards,
        "one_additional_blocker_cards": one_additional,
        "two_additional_blocker_cards": two_additional,
        "exact_ability_gain": affected_carriers,
        "material_residual_reduction": expected_residual_reduction,
        "decision": (
            "bounded_executable"
            if reaches_floor
            else "retired_below_harvest_floor"
        ),
        "grants_gameplay_trust": False,
        "candidate_accounting": {
            "affected_oracle_carriers": affected_carriers,
            "existing_exact_sibling_nodes": exact_siblings,
            "remaining_residual_sibling_nodes": remaining_sibling_nodes,
            "trusted_program_transitions": complete_cards,
            "unresolved_program_transitions": len(matched_by_card) - complete_cards,
            "expected_oracle_residual_reduction": expected_residual_reduction,
            "expected_card_program_residual_reduction": expected_residual_reduction,
            "newly_applicable_high_risk_pairs": 0,
            "cards_excluded_by_unsupported_sibling": unsupported_sibling_cards,
            "cards_excluded_by_unsupported_grammar": len(
                unsupported_grammar_cards
            ),
        },
    }


def _attached_quoted_ability_grant_measurement(
    *,
    frontier: Mapping[str, Any],
    bundle_id: str,
    probe_id: str,
    cards_by_oracle_id: Mapping[str, Any],
    coverage: Mapping[str, Any],
    cohort_fingerprint: str,
) -> dict[str, Any]:
    """Measure only exact inner programs inside accepted attachment shells."""

    cards_by_id: dict[str, Mapping[str, Any]] = {}
    quote_carriers_by_card: dict[str, list[Mapping[str, Any]]] = {}
    parsed_by_card: dict[str, list[Mapping[str, Any]]] = {}
    for card in frontier.get("cards", []):
        oracle_id = str(card.get("oracle_id") or "")
        record = cards_by_oracle_id.get(oracle_id)
        if record is None:
            raise WorkSelectionCohortMeasurementError(
                f"Cohort measurement lacks pinned card {oracle_id}"
            )
        cards_by_id[oracle_id] = card
        for ability in card.get("abilities", []):
            if ability.get("status") == "exact":
                continue
            source = _source_line(record, ability)
            lowered = source.casefold()
            if (
                lowered.startswith(
                    ("enchanted creature ", "equipped creature ")
                )
                and source.count('"') >= 2
            ):
                quote_carriers_by_card.setdefault(oracle_id, []).append(ability)
            if attached_quoted_ability_text(
                source,
                source_name=str(record.name),
            ) is not None:
                parsed_by_card.setdefault(oracle_id, []).append(ability)

    registry = load_default_capability_registry()
    matched_by_card: dict[str, list[Mapping[str, Any]]] = {}
    compiled_by_card: dict[str, Any] = {}
    exact_nodes_by_card: dict[str, int] = {}
    for oracle_id, parsed in parsed_by_card.items():
        record = cards_by_oracle_id[oracle_id]
        compiled = compile_oracle_card(
            record,
            capability_registry=registry,
            capability_profile="commander_review",
        )
        represented: list[Mapping[str, Any]] = []
        exact_node_count = 0
        for ability in parsed:
            face_id = str(ability.get("face_id") or "front")
            source_line = int(ability["source_line"])
            face = next(
                (
                    value
                    for value in compiled.faces
                    if value.face_id == face_id
                ),
                None,
            )
            if face is None:
                continue
            line_nodes = [
                node for node in face.nodes if node.span.line == source_line
            ]
            outer = any(
                node.exact
                and node.template_id
                == "continuous-attached-fixed-characteristics-"
                "granted-ability-v1"
                for node in line_nodes
            )
            inner = [
                node
                for node in line_nodes
                if node.exact and node.kind.startswith("granted_")
            ]
            if outer and len(inner) == 1:
                represented.append(ability)
                exact_node_count += 2
        if represented:
            matched_by_card[oracle_id] = represented
            compiled_by_card[oracle_id] = compiled
            exact_nodes_by_card[oracle_id] = exact_node_count

    affected_carriers = sum(len(values) for values in matched_by_card.values())
    exact_node_gain = sum(exact_nodes_by_card.values())
    complete_cards = 0
    exact_siblings = 0
    remaining_sibling_nodes = 0
    expected_residual_reduction = 0
    one_additional = 0
    two_additional = 0
    unsupported_sibling_cards = 0
    newly_applicable_high_risk_pairs: set[tuple[str, str]] = set()
    for oracle_id in matched_by_card:
        card = cards_by_id[oracle_id]
        compiled = compiled_by_card[oracle_id]
        exact_siblings += sum(
            ability.get("status") == "exact"
            for ability in card.get("abilities", ())
        )
        remaining = [
            node
            for face in compiled.faces
            for node in face.nodes
            if not node.exact
        ]
        remaining_sibling_nodes += len(remaining)
        one_additional += len(remaining) == 1
        two_additional += len(remaining) == 2
        unsupported_sibling_cards += bool(remaining)
        base_residuals = sum(
            max(1, len(ability.get("residuals", ())))
            for ability in card.get("abilities", ())
            if ability.get("status") != "exact"
        )
        expected_residual_reduction += max(
            0,
            base_residuals - len(compiled.material_residuals),
        )
        complete_cards += compiled.status == "exact"
        capabilities = {
            capability
            for face in compiled.faces
            for node in face.nodes
            if node.exact
            for capability in node.capability_dependencies
        }
        newly_applicable_high_risk_pairs.update(
            pair
            for pair in _ATTACHED_GRANT_HIGH_RISK_CAPABILITY_PAIRS
            if set(pair).issubset(capabilities)
        )

    unsupported_grammar_cards = set(quote_carriers_by_card) - set(
        matched_by_card
    )
    unsupported_grammar_cards.update(
        oracle_id
        for oracle_id, carriers in quote_carriers_by_card.items()
        if any(
            ability not in matched_by_card.get(oracle_id, ())
            for ability in carriers
        )
    )
    reaches_floor = (
        complete_cards >= int(coverage["minimum_complete_card_gain"])
        or exact_node_gain >= int(coverage["minimum_exact_ability_gain"])
        or expected_residual_reduction
        >= int(coverage["minimum_material_residual_reduction"])
    )
    return {
        "measurement_id": "measurement:" + bundle_id.split(":", 1)[-1],
        "bundle_id": bundle_id,
        "probe_id": probe_id,
        "cohort_fingerprint": cohort_fingerprint,
        "affected_commander_cards": len(matched_by_card),
        "complete_card_gain": complete_cards,
        "one_additional_blocker_cards": one_additional,
        "two_additional_blocker_cards": two_additional,
        "exact_ability_gain": exact_node_gain,
        "material_residual_reduction": expected_residual_reduction,
        "decision": (
            "bounded_executable"
            if reaches_floor
            else "retired_below_harvest_floor"
        ),
        "grants_gameplay_trust": False,
        "candidate_accounting": {
            "affected_oracle_carriers": affected_carriers,
            "existing_exact_sibling_nodes": exact_siblings,
            "remaining_residual_sibling_nodes": remaining_sibling_nodes,
            "trusted_program_transitions": complete_cards,
            "unresolved_program_transitions": (
                len(matched_by_card) - complete_cards
            ),
            "expected_oracle_residual_reduction": (
                expected_residual_reduction
            ),
            "expected_card_program_residual_reduction": (
                expected_residual_reduction
            ),
            "newly_applicable_high_risk_pairs": len(
                newly_applicable_high_risk_pairs
            ),
            "cards_excluded_by_unsupported_sibling": (
                unsupported_sibling_cards
            ),
            "cards_excluded_by_unsupported_grammar": len(
                unsupported_grammar_cards
            ),
        },
    }


def _source_combat_growth_trigger_measurement(
    *,
    frontier: Mapping[str, Any],
    bundle_id: str,
    probe_id: str,
    cards_by_oracle_id: Mapping[str, Any],
    coverage: Mapping[str, Any],
    cohort_fingerprint: str,
) -> dict[str, Any]:
    """Measure exact source-self combat growth through integrated owners."""

    cards_by_id: dict[str, Mapping[str, Any]] = {}
    broad_carriers: dict[str, list[Mapping[str, Any]]] = {}
    matched_carriers: dict[str, list[Mapping[str, Any]]] = {}
    broad = re.compile(
        r"^Whenever this creature "
        r"(?:attacks|blocks|becomes blocked|deals combat damage)\b.*"
        r"(?:gets [+-]|counter on (?:it|this creature))",
        re.IGNORECASE,
    )
    for card in frontier.get("cards", []):
        oracle_id = str(card.get("oracle_id") or "")
        record = cards_by_oracle_id.get(oracle_id)
        if record is None:
            raise WorkSelectionCohortMeasurementError(
                f"Cohort measurement lacks pinned card {oracle_id}"
            )
        cards_by_id[oracle_id] = card
        for ability in card.get("abilities", []):
            if ability.get("status") == "exact":
                continue
            source = _source_line(record, ability)
            if broad.search(source):
                broad_carriers.setdefault(oracle_id, []).append(ability)
            if _matches_probe(
                probe_id,
                source,
                card_record=record,
                ability=ability,
            ):
                matched_carriers.setdefault(oracle_id, []).append(ability)

    registry = load_default_capability_registry()
    represented_by_card: dict[str, list[Mapping[str, Any]]] = {}
    compiled_by_card: dict[str, Any] = {}
    for oracle_id, carriers in matched_carriers.items():
        compiled = compile_oracle_card(
            cards_by_oracle_id[oracle_id],
            capability_registry=registry,
            capability_profile="commander_review",
        )
        represented: list[Mapping[str, Any]] = []
        for ability in carriers:
            face_id = str(ability.get("face_id") or "front")
            source_line = int(ability["source_line"])
            face = next(
                (value for value in compiled.faces if value.face_id == face_id),
                None,
            )
            if face is None:
                continue
            nodes = [
                node
                for node in face.nodes
                if node.span.line == source_line
                and node.exact
                and len(node.effects) == 1
                and node.effects[0].get("card") == SOURCE_ZONE_OBJECT
                and node.effects[0].get("op")
                in {"modify_stats_until_end_of_turn", "place_counters"}
                and "current_ability_fragment_required" in node.runtime_coverage
            ]
            if len(nodes) == 1:
                represented.append(ability)
        if represented:
            represented_by_card[oracle_id] = represented
            compiled_by_card[oracle_id] = compiled

    affected_carriers = sum(
        len(values) for values in represented_by_card.values()
    )
    complete_cards = 0
    exact_siblings = 0
    remaining_sibling_nodes = 0
    expected_residual_reduction = 0
    one_additional = 0
    two_additional = 0
    unsupported_sibling_cards = 0
    for oracle_id, represented in represented_by_card.items():
        card = cards_by_id[oracle_id]
        compiled = compiled_by_card[oracle_id]
        exact_siblings += sum(
            ability.get("status") == "exact"
            for ability in card.get("abilities", ())
        )
        remaining = [
            node
            for face in compiled.faces
            for node in face.nodes
            if not node.exact
        ]
        remaining_sibling_nodes += len(remaining)
        one_additional += len(remaining) == 1
        two_additional += len(remaining) == 2
        unsupported_sibling_cards += bool(remaining)
        base_residuals = sum(
            max(1, len(ability.get("residuals", ())))
            for ability in card.get("abilities", ())
            if ability.get("status") != "exact"
        )
        expected_residual_reduction += max(
            0,
            base_residuals - len(compiled.material_residuals),
        )
        complete_cards += compiled.status == "exact"
        if len(represented) != len(matched_carriers[oracle_id]):
            raise WorkSelectionCohortMeasurementError(
                "Source combat-growth probe matched a carrier without one "
                "exact integrated node"
            )

    unsupported_grammar_cards = set(broad_carriers) - set(
        represented_by_card
    )
    unsupported_grammar_cards.update(
        oracle_id
        for oracle_id, carriers in broad_carriers.items()
        if any(
            ability not in represented_by_card.get(oracle_id, ())
            for ability in carriers
        )
    )
    reaches_floor = (
        complete_cards >= int(coverage["minimum_complete_card_gain"])
        or affected_carriers >= int(coverage["minimum_exact_ability_gain"])
        or expected_residual_reduction
        >= int(coverage["minimum_material_residual_reduction"])
    )
    return {
        "measurement_id": "measurement:" + bundle_id.split(":", 1)[-1],
        "bundle_id": bundle_id,
        "probe_id": probe_id,
        "cohort_fingerprint": cohort_fingerprint,
        "affected_commander_cards": len(represented_by_card),
        "complete_card_gain": complete_cards,
        "one_additional_blocker_cards": one_additional,
        "two_additional_blocker_cards": two_additional,
        "exact_ability_gain": affected_carriers,
        "material_residual_reduction": expected_residual_reduction,
        "decision": (
            "bounded_executable"
            if reaches_floor
            else "retired_below_harvest_floor"
        ),
        "grants_gameplay_trust": False,
        "candidate_accounting": {
            "affected_oracle_carriers": affected_carriers,
            "existing_exact_sibling_nodes": exact_siblings,
            "remaining_residual_sibling_nodes": remaining_sibling_nodes,
            "trusted_program_transitions": complete_cards,
            "unresolved_program_transitions": (
                len(represented_by_card) - complete_cards
            ),
            "expected_oracle_residual_reduction": expected_residual_reduction,
            "expected_card_program_residual_reduction": (
                expected_residual_reduction
            ),
            "newly_applicable_high_risk_pairs": 0,
            "cards_excluded_by_unsupported_sibling": (
                unsupported_sibling_cards
            ),
            "cards_excluded_by_unsupported_grammar": len(
                unsupported_grammar_cards
            ),
        },
    }


def _fixed_entry_return_requirement_measurement(
    *,
    frontier: Mapping[str, Any],
    bundle_id: str,
    probe_id: str,
    cards_by_oracle_id: Mapping[str, Any],
    coverage: Mapping[str, Any],
    cohort_fingerprint: str,
) -> dict[str, Any]:
    """Measure exact entry-return triggers through event and choice owners."""

    registry = load_default_capability_registry()
    cards_by_id: dict[str, Mapping[str, Any]] = {}
    broad_cards: set[str] = set()
    matched_by_card: dict[str, list[Mapping[str, Any]]] = {}
    represented_by_card: dict[str, list[Mapping[str, Any]]] = {}
    compiled_by_card: dict[str, Any] = {}
    broad = re.compile(
        r"^When .+ enters, (?:sacrifice it unless you )?return .+ owner's hand",
        re.IGNORECASE,
    )
    for card in frontier.get("cards", []):
        oracle_id = str(card.get("oracle_id") or "")
        record = cards_by_oracle_id.get(oracle_id)
        if record is None:
            raise WorkSelectionCohortMeasurementError(
                f"Cohort measurement lacks pinned card {oracle_id}"
            )
        cards_by_id[oracle_id] = card
        for ability in card.get("abilities", []):
            if ability.get("status") == "exact":
                continue
            source = _source_line(record, ability)
            if broad.search(source):
                broad_cards.add(oracle_id)
            if _matches_probe(
                probe_id,
                source,
                card_record=record,
                ability=ability,
            ):
                matched_by_card.setdefault(oracle_id, []).append(ability)

    for oracle_id, carriers in matched_by_card.items():
        compiled = compile_oracle_card(
            cards_by_oracle_id[oracle_id],
            capability_registry=registry,
            capability_profile="commander_review",
        )
        represented: list[Mapping[str, Any]] = []
        for ability in carriers:
            face_id = str(ability.get("face_id") or "front")
            source_line = int(ability["source_line"])
            face = next(
                (value for value in compiled.faces if value.face_id == face_id),
                None,
            )
            if face is None:
                continue
            nodes = [
                node
                for node in face.nodes
                if node.span.line == source_line
                and node.exact
                and node.template_id
                == "fixed-typed-effect-entry-return-public-zone-trigger-v1"
                and len(node.effects) == 1
                and node.effects[0].get("op")
                in {"bounce", "choose_cards_apnap", "choose_option"}
                and CURRENT_ABILITY_FRAGMENT_COVERAGE in node.runtime_coverage
                and "choice.controller.fixed_return_owner_hand"
                in node.capability_dependencies
            ]
            if len(nodes) == 1:
                represented.append(ability)
        if represented:
            represented_by_card[oracle_id] = represented
            compiled_by_card[oracle_id] = compiled

    affected_carriers = sum(len(value) for value in represented_by_card.values())
    complete_cards = 0
    exact_siblings = 0
    remaining_siblings = 0
    expected_residual_reduction = 0
    one_additional = 0
    two_additional = 0
    unsupported_sibling_cards = 0
    for oracle_id, represented in represented_by_card.items():
        card = cards_by_id[oracle_id]
        compiled = compiled_by_card[oracle_id]
        exact_siblings += sum(
            ability.get("status") == "exact"
            for ability in card.get("abilities", ())
        )
        remaining = [
            node
            for face in compiled.faces
            for node in face.nodes
            if not node.exact
        ]
        remaining_siblings += len(remaining)
        one_additional += len(remaining) == 1
        two_additional += len(remaining) == 2
        unsupported_sibling_cards += bool(remaining)
        base_residuals = sum(
            max(1, len(ability.get("residuals", ())))
            for ability in card.get("abilities", ())
            if ability.get("status") != "exact"
        )
        expected_residual_reduction += max(
            0, base_residuals - len(compiled.material_residuals)
        )
        complete_cards += compiled.status == "exact"
        if len(represented) != len(matched_by_card[oracle_id]):
            raise WorkSelectionCohortMeasurementError(
                "Entry-return probe matched a carrier without one exact "
                "integrated node"
            )

    reaches_floor = (
        complete_cards >= int(coverage["minimum_complete_card_gain"])
        or affected_carriers >= int(coverage["minimum_exact_ability_gain"])
        or expected_residual_reduction
        >= int(coverage["minimum_material_residual_reduction"])
    )
    return {
        "measurement_id": "measurement:" + bundle_id.split(":", 1)[-1],
        "bundle_id": bundle_id,
        "probe_id": probe_id,
        "cohort_fingerprint": cohort_fingerprint,
        "affected_commander_cards": len(represented_by_card),
        "complete_card_gain": complete_cards,
        "one_additional_blocker_cards": one_additional,
        "two_additional_blocker_cards": two_additional,
        "exact_ability_gain": affected_carriers,
        "material_residual_reduction": expected_residual_reduction,
        "decision": (
            "bounded_executable" if reaches_floor else "retired_below_harvest_floor"
        ),
        "grants_gameplay_trust": False,
        "candidate_accounting": {
            "affected_oracle_carriers": affected_carriers,
            "existing_exact_sibling_nodes": exact_siblings,
            "remaining_residual_sibling_nodes": remaining_siblings,
            "trusted_program_transitions": complete_cards,
            "unresolved_program_transitions": len(represented_by_card) - complete_cards,
            "expected_oracle_residual_reduction": expected_residual_reduction,
            "expected_card_program_residual_reduction": expected_residual_reduction,
            "newly_applicable_high_risk_pairs": 0,
            "cards_excluded_by_unsupported_sibling": unsupported_sibling_cards,
            "cards_excluded_by_unsupported_grammar": len(
                broad_cards - set(represented_by_card)
            ),
        },
    }


def _self_spell_cost_reduction_measurement(
    *,
    frontier: Mapping[str, Any],
    bundle_id: str,
    probe_id: str,
    cards_by_oracle_id: Mapping[str, Any],
    coverage: Mapping[str, Any],
    cohort_fingerprint: str,
) -> dict[str, Any]:
    """Measure integrated selected-face self-reduction programs."""

    registry = load_default_capability_registry()
    matched_cards: dict[str, int] = {}
    matched_abilities = 0
    exact_nodes = 0
    complete_cards = 0
    for card in frontier.get("cards", []):
        oracle_id = str(card.get("oracle_id") or "")
        record = cards_by_oracle_id.get(oracle_id)
        if record is None:
            raise WorkSelectionCohortMeasurementError(
                f"Cohort measurement lacks pinned card {oracle_id}"
            )
        unresolved = [
            ability
            for ability in card.get("abilities", [])
            if ability.get("status") != "exact"
        ]
        matched = [
            ability
            for ability in unresolved
            if _matches_probe(
                probe_id,
                _source_line(record, ability),
                card_record=record,
                ability=ability,
            )
        ]
        if not matched:
            continue
        compiled = compile_oracle_card(
            record,
            capability_registry=registry,
            capability_profile="commander_review",
        )
        represented_lines = {
            node.span.line
            for face in compiled.faces
            for node in face.nodes
            if node.exact
            and any(
                handler.get("handler_id")
                == SELF_SPELL_COST_REDUCTION_HANDLER_ID
                for handler in node.handlers
            )
        }
        matched_lines = {int(ability["source_line"]) for ability in matched}
        if not matched_lines <= represented_lines:
            raise WorkSelectionCohortMeasurementError(
                "Self spell-cost probe matched a carrier without an exact "
                f"integrated node on {record.name}"
            )
        matched_cards[oracle_id] = len(unresolved) - len(matched)
        matched_abilities += len(matched)
        exact_nodes += len(matched_lines)
        if compiled.status == "exact":
            complete_cards += 1
    reaches_floor = (
        complete_cards >= int(coverage["minimum_complete_card_gain"])
        or exact_nodes >= int(coverage["minimum_exact_ability_gain"])
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
        "exact_ability_gain": exact_nodes,
        "material_residual_reduction": matched_abilities,
        "decision": (
            "bounded_executable"
            if reaches_floor
            else "retired_below_harvest_floor"
        ),
        "grants_gameplay_trust": False,
    }


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


def _fixed_activation_zone_change_predicate_measurement(
    *,
    frontier: Mapping[str, Any],
    bundle_id: str,
    probe_id: str,
    member_ids: set[str],
    cards_by_oracle_id: Mapping[str, Any],
    coverage: Mapping[str, Any],
    cohort_fingerprint: str,
) -> dict[str, Any]:
    """Measure only matched activation costs whose compiled node is exact."""

    registry = load_default_capability_registry()
    matched_abilities = 0
    matched_cards: dict[str, int] = {}
    cards_with_unmatched_member_ability: set[str] = set()
    compiled_exact_cards: set[str] = set()
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
        matched_node_ids: set[str] = set()
        for ability in candidates:
            node = nodes.get(str(ability.get("ability_id") or ""))
            if (
                node is not None
                and node.exact
                and node.kind == "activated_ability"
            ):
                matched_node_ids.add(node.node_id)
            else:
                cards_with_unmatched_member_ability.add(oracle_id)
        card_matches = len(matched_node_ids)
        if not card_matches:
            continue
        matched_abilities += card_matches
        matched_cards[oracle_id] = len(
            set(card.get("minimum_known_blocker_set", [])) - member_ids
        )
        if compiled.status == "exact":
            compiled_exact_cards.add(oracle_id)
    complete_cards = sum(
        count == 0
        and oracle_id not in cards_with_unmatched_member_ability
        and oracle_id in compiled_exact_cards
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


def _ordinary_saga_chapter_program_measurement(
    *,
    frontier: Mapping[str, Any],
    bundle_id: str,
    probe_id: str,
    member_ids: set[str],
    cards_by_oracle_id: Mapping[str, Any],
    coverage: Mapping[str, Any],
    cohort_fingerprint: str,
) -> dict[str, Any]:
    """Measure chapter carriers whose integrated typed node is exact."""

    registry = load_default_capability_registry()
    matched_abilities = 0
    matched_cards: dict[str, int] = {}
    cards_with_unmatched_member_ability: set[str] = set()
    compiled_exact_cards: set[str] = set()
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
        card_matches = 0
        for ability in candidates:
            node = nodes.get(str(ability.get("ability_id") or ""))
            if (
                node is not None
                and node.exact
                and node.kind == "triggered_ability"
                and str(node.event).startswith("saga.chapter.")
            ):
                card_matches += 1
            else:
                cards_with_unmatched_member_ability.add(oracle_id)
        if not card_matches:
            continue
        matched_abilities += card_matches
        matched_cards[oracle_id] = len(
            set(card.get("minimum_known_blocker_set", [])) - member_ids
        )
        if compiled.status == "exact":
            compiled_exact_cards.add(oracle_id)
    complete_cards = sum(
        count == 0
        and oracle_id not in cards_with_unmatched_member_ability
        and oracle_id in compiled_exact_cards
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
    if probe_id == _PROBE_FIXED_ALL_DAMAGE_PREVENTION:
        return _fixed_all_damage_prevention_measurement(
            frontier=frontier,
            bundle_id=bundle_id,
            probe_id=probe_id,
            cards_by_oracle_id=cards_by_oracle_id,
            coverage=coverage,
            cohort_fingerprint=cohort_fingerprint,
        )
    if probe_id == _PROBE_ATTACHED_QUOTED_ABILITY_GRANT:
        return _attached_quoted_ability_grant_measurement(
            frontier=frontier,
            bundle_id=bundle_id,
            probe_id=probe_id,
            cards_by_oracle_id=cards_by_oracle_id,
            coverage=coverage,
            cohort_fingerprint=cohort_fingerprint,
        )
    if probe_id == _PROBE_SOURCE_COMBAT_GROWTH_TRIGGER:
        return _source_combat_growth_trigger_measurement(
            frontier=frontier,
            bundle_id=bundle_id,
            probe_id=probe_id,
            cards_by_oracle_id=cards_by_oracle_id,
            coverage=coverage,
            cohort_fingerprint=cohort_fingerprint,
        )
    if probe_id == _PROBE_FIXED_ENTRY_RETURN_REQUIREMENTS:
        return _fixed_entry_return_requirement_measurement(
            frontier=frontier,
            bundle_id=bundle_id,
            probe_id=probe_id,
            cards_by_oracle_id=cards_by_oracle_id,
            coverage=coverage,
            cohort_fingerprint=cohort_fingerprint,
        )
    if probe_id == _PROBE_FIXED_CASTING_SURFACE:
        return _fixed_casting_surface_measurement(
            frontier=frontier,
            bundle_id=bundle_id,
            probe_id=probe_id,
            cards_by_oracle_id=cards_by_oracle_id,
            coverage=coverage,
            cohort_fingerprint=cohort_fingerprint,
        )
    if probe_id == _PROBE_FIXED_PUBLIC_NUMERIC_DAMAGE_TARGET:
        return _fixed_public_numeric_damage_target_measurement(
            frontier=frontier,
            bundle_id=bundle_id,
            probe_id=probe_id,
            cards_by_oracle_id=cards_by_oracle_id,
            coverage=coverage,
            cohort_fingerprint=cohort_fingerprint,
        )
    if probe_id == _PROBE_TYPED_LEVELER_BANDS:
        return _typed_leveler_band_measurement(
            frontier=frontier,
            bundle_id=bundle_id,
            probe_id=probe_id,
            cards_by_oracle_id=cards_by_oracle_id,
            coverage=coverage,
            cohort_fingerprint=cohort_fingerprint,
        )
    if probe_id == _PROBE_SPELL_HISTORY_TRANSFORMATIONS:
        return _spell_history_transformation_measurement(
            frontier=frontier,
            bundle_id=bundle_id,
            probe_id=probe_id,
            cards_by_oracle_id=cards_by_oracle_id,
            coverage=coverage,
            cohort_fingerprint=cohort_fingerprint,
        )
    if probe_id == _PROBE_FIXED_TOKEN_PRODUCTION:
        return _fixed_token_production_measurement(
            frontier=frontier,
            bundle_id=bundle_id,
            probe_id=probe_id,
            cards_by_oracle_id=cards_by_oracle_id,
            coverage=coverage,
            cohort_fingerprint=cohort_fingerprint,
        )
    if probe_id == _PROBE_TYPED_QUOTED_ABILITY_GRANT:
        return _typed_quoted_ability_grant_measurement(
            frontier=frontier,
            bundle_id=bundle_id,
            probe_id=probe_id,
            cards_by_oracle_id=cards_by_oracle_id,
            coverage=coverage,
            cohort_fingerprint=cohort_fingerprint,
        )
    if probe_id == _PROBE_PARTNER_WITH:
        return _partner_with_measurement(
            frontier=frontier,
            bundle_id=bundle_id,
            probe_id=probe_id,
            cards_by_oracle_id=cards_by_oracle_id,
            coverage=coverage,
            cohort_fingerprint=cohort_fingerprint,
        )
    if probe_id == _PROBE_SELF_SPELL_COST_REDUCTION:
        return _self_spell_cost_reduction_measurement(
            frontier=frontier,
            bundle_id=bundle_id,
            probe_id=probe_id,
            cards_by_oracle_id=cards_by_oracle_id,
            coverage=coverage,
            cohort_fingerprint=cohort_fingerprint,
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
    if probe_id == _PROBE_FIXED_ACTIVATION_ZONE_CHANGE_PREDICATES:
        return _fixed_activation_zone_change_predicate_measurement(
            frontier=frontier,
            bundle_id=bundle_id,
            probe_id=probe_id,
            member_ids=member_ids,
            cards_by_oracle_id=cards_by_oracle_id,
            coverage=coverage,
            cohort_fingerprint=cohort_fingerprint,
        )
    if probe_id == _PROBE_ORDINARY_SAGA_CHAPTER_PROGRAMS:
        return _ordinary_saga_chapter_program_measurement(
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


def _fixed_casting_surface_measurement(
    *,
    frontier: Mapping[str, Any],
    bundle_id: str,
    probe_id: str,
    cards_by_oracle_id: Mapping[str, Any],
    coverage: Mapping[str, Any],
    cohort_fingerprint: str,
) -> dict[str, Any]:
    """Measure the integrated fixed public modifier and lifecycle surface."""

    registry = load_default_capability_registry()
    matched_cards: dict[str, int] = {}
    matched_abilities = 0
    complete_cards = 0
    one_additional = 0
    two_additional = 0
    expected_residual_reduction = 0
    existing_exact_sibling_nodes = 0
    remaining_residual_sibling_nodes = 0
    unsupported_sibling_cards = 0
    unsupported_grammar_cards: set[str] = set()
    for card in frontier.get("cards", []):
        oracle_id = str(card.get("oracle_id") or "")
        record = cards_by_oracle_id.get(oracle_id)
        if record is None:
            raise WorkSelectionCohortMeasurementError(
                f"Cohort measurement lacks pinned card {oracle_id}"
            )
        potential = []
        for ability in card.get("abilities", []):
            if ability.get("status") == "exact":
                continue
            source = _source_line(record, ability)
            if (
                fixed_cast_lifecycle_spec(source) is not None
                or public_cast_cost_modifier_template(source) is not None
            ):
                potential.append(ability)
        if not potential:
            continue
        compiled = compile_oracle_card(
            record,
            capability_registry=registry,
            capability_profile="commander_review",
        )
        represented_lines: set[int] = set()
        for face in compiled.faces:
            for node in face.nodes:
                if not node.exact:
                    continue
                if node.template_id == "public-fixed-spell-cost-modifier-v1":
                    represented_lines.add(node.span.line)
                elif any(
                    descriptor.get("handler_id")
                    == "casting.lifecycle.fixed-public.v1"
                    for descriptor in node.handlers
                ):
                    represented_lines.add(node.span.line)
        represented = [
            ability
            for ability in potential
            if int(ability.get("source_line") or 0) in represented_lines
        ]
        if not represented:
            unsupported_grammar_cards.add(oracle_id)
            continue
        if len(represented) != len(potential):
            unsupported_grammar_cards.add(oracle_id)
        matched = len(represented)
        matched_abilities += matched
        matched_cards[oracle_id] = matched
        remaining_nodes = [
            node
            for face in compiled.faces
            for node in face.nodes
            if not node.exact
        ]
        existing_exact_sibling_nodes += sum(
            ability.get("status") == "exact"
            for ability in card.get("abilities", ())
        )
        remaining_residual_sibling_nodes += len(remaining_nodes)
        if compiled.status == "exact":
            complete_cards += 1
        else:
            unsupported_sibling_cards += 1
        one_additional += len(remaining_nodes) == 1
        two_additional += len(remaining_nodes) == 2
        base_residuals = sum(
            max(1, len(ability.get("residuals", ())))
            for ability in card.get("abilities", ())
            if ability.get("status") != "exact"
        )
        expected_residual_reduction += max(
            0,
            base_residuals - len(compiled.material_residuals),
        )
    reaches_floor = (
        complete_cards >= int(coverage["minimum_complete_card_gain"])
        or matched_abilities >= int(coverage["minimum_exact_ability_gain"])
        or expected_residual_reduction
        >= int(coverage["minimum_material_residual_reduction"])
    )
    return {
        "measurement_id": "measurement:" + bundle_id.split(":", 1)[-1],
        "bundle_id": bundle_id,
        "probe_id": probe_id,
        "cohort_fingerprint": cohort_fingerprint,
        "affected_commander_cards": len(matched_cards),
        "complete_card_gain": complete_cards,
        "one_additional_blocker_cards": one_additional,
        "two_additional_blocker_cards": two_additional,
        "exact_ability_gain": matched_abilities,
        "material_residual_reduction": expected_residual_reduction,
        "decision": (
            "bounded_executable"
            if reaches_floor
            else "retired_below_harvest_floor"
        ),
        "grants_gameplay_trust": False,
        "candidate_accounting": {
            "affected_oracle_carriers": matched_abilities,
            "existing_exact_sibling_nodes": existing_exact_sibling_nodes,
            "remaining_residual_sibling_nodes": (
                remaining_residual_sibling_nodes
            ),
            "trusted_program_transitions": complete_cards,
            "unresolved_program_transitions": (
                len(matched_cards) - complete_cards
            ),
            "expected_oracle_residual_reduction": (
                expected_residual_reduction
            ),
            "expected_card_program_residual_reduction": (
                expected_residual_reduction
            ),
            "newly_applicable_high_risk_pairs": 0,
            "cards_excluded_by_unsupported_sibling": (
                unsupported_sibling_cards
            ),
            "cards_excluded_by_unsupported_grammar": len(
                unsupported_grammar_cards
            ),
        },
    }


def _target_group_mappings(
    target_schema: Mapping[str, Any] | None,
) -> tuple[Mapping[str, Any], ...]:
    if not isinstance(target_schema, Mapping):
        return ()
    modes = target_schema.get("modes")
    if isinstance(modes, Mapping):
        groups: list[Mapping[str, Any]] = []
        for definition in modes.values():
            if not isinstance(definition, Mapping):
                continue
            nested = definition.get("groups")
            if isinstance(nested, list):
                groups.extend(
                    value for value in nested if isinstance(value, Mapping)
                )
                continue
            groups.append(
                {
                    key: value
                    for key, value in definition.items()
                    if key not in {"effects", "mechanics"}
                }
            )
        return tuple(groups)
    groups = target_schema.get("groups")
    if isinstance(groups, list):
        return tuple(value for value in groups if isinstance(value, Mapping))
    return (target_schema,)


def _node_has_fixed_public_numeric_damage_target(node: Any) -> bool:
    for mapping in _target_group_mappings(node.target_schema):
        try:
            group = TargetGroup.from_mapping(mapping)
        except (TypeError, ValueError):
            continue
        if (
            group.numeric_characteristic is not None
            or group.damage_history is not None
        ):
            return True
    return False


def _fixed_public_numeric_damage_target_measurement(
    *,
    frontier: Mapping[str, Any],
    bundle_id: str,
    probe_id: str,
    cards_by_oracle_id: Mapping[str, Any],
    coverage: Mapping[str, Any],
    cohort_fingerprint: str,
) -> dict[str, Any]:
    """Measure fixed numeric and positive-damage direct-target closure."""

    registry = load_default_capability_registry()
    matched_cards: dict[str, int] = {}
    complete_cards = 0
    exact_ability_gain = 0
    expected_residual_reduction = 0
    existing_exact_sibling_nodes = 0
    remaining_residual_sibling_nodes = 0
    unsupported_sibling_cards = 0
    unsupported_grammar_cards: set[str] = set()
    for card in frontier.get("cards", []):
        oracle_id = str(card.get("oracle_id") or "")
        record = cards_by_oracle_id.get(oracle_id)
        if record is None:
            raise WorkSelectionCohortMeasurementError(
                f"Cohort measurement lacks pinned card {oracle_id}"
            )
        potential = [
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
        if not potential:
            continue
        compiled = compile_oracle_card(
            record,
            capability_registry=registry,
            capability_profile="commander_review",
        )
        if not any(
            node.exact and _node_has_fixed_public_numeric_damage_target(node)
            for face in compiled.faces
            for node in face.nodes
        ):
            unsupported_grammar_cards.add(oracle_id)
            continue
        current_exact = sum(
            node.exact for face in compiled.faces for node in face.nodes
        )
        base_exact = int(card.get("exact_ability_count", 0))
        exact_delta = max(0, current_exact - base_exact)
        base_residuals = sum(
            max(1, len(ability.get("residuals", ())))
            for ability in card.get("abilities", ())
            if ability.get("status") != "exact"
        )
        current_residuals = len(compiled.material_residuals)
        residual_delta = max(0, base_residuals - current_residuals)
        if not (exact_delta or residual_delta):
            continue
        remaining = [
            node
            for face in compiled.faces
            for node in face.nodes
            if not node.exact
        ]
        matched_cards[oracle_id] = len(remaining)
        existing_exact_sibling_nodes += base_exact
        exact_ability_gain += exact_delta
        expected_residual_reduction += residual_delta
        remaining_residual_sibling_nodes += len(remaining)
        if compiled.status == "exact":
            complete_cards += 1
        else:
            unsupported_sibling_cards += 1
    reaches_floor = (
        complete_cards >= int(coverage["minimum_complete_card_gain"])
        or exact_ability_gain >= int(coverage["minimum_exact_ability_gain"])
        or expected_residual_reduction
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
        "exact_ability_gain": exact_ability_gain,
        "material_residual_reduction": expected_residual_reduction,
        "decision": (
            "bounded_executable"
            if reaches_floor
            else "retired_below_harvest_floor"
        ),
        "grants_gameplay_trust": False,
        "candidate_accounting": {
            "affected_oracle_carriers": exact_ability_gain,
            "existing_exact_sibling_nodes": existing_exact_sibling_nodes,
            "remaining_residual_sibling_nodes": (
                remaining_residual_sibling_nodes
            ),
            "trusted_program_transitions": complete_cards,
            "unresolved_program_transitions": (
                len(matched_cards) - complete_cards
            ),
            "expected_oracle_residual_reduction": (
                expected_residual_reduction
            ),
            "expected_card_program_residual_reduction": (
                expected_residual_reduction
            ),
            "newly_applicable_high_risk_pairs": 0,
            "cards_excluded_by_unsupported_sibling": (
                unsupported_sibling_cards
            ),
            "cards_excluded_by_unsupported_grammar": len(
                unsupported_grammar_cards
            ),
        },
    }


def _typed_leveler_band_measurement(
    *,
    frontier: Mapping[str, Any],
    bundle_id: str,
    probe_id: str,
    cards_by_oracle_id: Mapping[str, Any],
    coverage: Mapping[str, Any],
    cohort_fingerprint: str,
) -> dict[str, Any]:
    """Measure two-striation creature Levelers through the typed layer owner."""

    registry = load_default_capability_registry()
    matched_cards: dict[str, int] = {}
    complete_cards = 0
    exact_ability_gain = 0
    expected_residual_reduction = 0
    existing_exact_sibling_nodes = 0
    remaining_residual_sibling_nodes = 0
    unsupported_sibling_cards = 0
    unsupported_grammar_cards: set[str] = set()
    band_row = re.compile(
        r"^(?:LEVEL\s+[1-9]\d*(?:[-\u2013\u2014][1-9]\d*|\+)|\d+/\d+)$",
        re.IGNORECASE,
    )
    for card in frontier.get("cards", []):
        oracle_id = str(card.get("oracle_id") or "")
        record = cards_by_oracle_id.get(oracle_id)
        if record is None:
            raise WorkSelectionCohortMeasurementError(
                f"Cohort measurement lacks pinned card {oracle_id}"
            )
        if record.layout != "leveler" or not record.is_creature:
            continue
        potential = [
            ability
            for ability in card.get("abilities", [])
            if ability.get("status") != "exact"
            and band_row.fullmatch(_source_line(record, ability)) is not None
        ]
        if not potential:
            continue
        compiled = compile_oracle_card(
            record,
            capability_registry=registry,
            capability_profile="commander_review",
        )
        band_nodes = [
            node
            for face in compiled.faces
            for node in face.nodes
            if node.exact
            and node.template_id
            in {"leveler-bands-v1", "leveler-band-range-v1"}
        ]
        if len(band_nodes) != 2:
            unsupported_grammar_cards.add(oracle_id)
            continue
        current_exact = sum(
            node.exact for face in compiled.faces for node in face.nodes
        )
        base_exact = int(card.get("exact_ability_count", 0))
        exact_delta = max(0, current_exact - base_exact)
        base_residuals = sum(
            max(1, len(ability.get("residuals", ())))
            for ability in card.get("abilities", ())
            if ability.get("status") != "exact"
        )
        residual_delta = max(
            0, base_residuals - len(compiled.material_residuals)
        )
        if not (exact_delta or residual_delta):
            continue
        remaining = [
            node
            for face in compiled.faces
            for node in face.nodes
            if not node.exact
        ]
        matched_cards[oracle_id] = len(remaining)
        existing_exact_sibling_nodes += base_exact
        exact_ability_gain += exact_delta
        expected_residual_reduction += residual_delta
        remaining_residual_sibling_nodes += len(remaining)
        if compiled.status == "exact":
            complete_cards += 1
        else:
            unsupported_sibling_cards += 1
    reaches_floor = (
        complete_cards >= int(coverage["minimum_complete_card_gain"])
        or exact_ability_gain >= int(coverage["minimum_exact_ability_gain"])
        or expected_residual_reduction
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
        "exact_ability_gain": exact_ability_gain,
        "material_residual_reduction": expected_residual_reduction,
        "decision": (
            "bounded_executable"
            if reaches_floor
            else "retired_below_harvest_floor"
        ),
        "grants_gameplay_trust": False,
        "candidate_accounting": {
            "affected_oracle_carriers": exact_ability_gain,
            "existing_exact_sibling_nodes": existing_exact_sibling_nodes,
            "remaining_residual_sibling_nodes": (
                remaining_residual_sibling_nodes
            ),
            "trusted_program_transitions": complete_cards,
            "unresolved_program_transitions": (
                len(matched_cards) - complete_cards
            ),
            "expected_oracle_residual_reduction": (
                expected_residual_reduction
            ),
            "expected_card_program_residual_reduction": (
                expected_residual_reduction
            ),
            "newly_applicable_high_risk_pairs": 0,
            "cards_excluded_by_unsupported_sibling": (
                unsupported_sibling_cards
            ),
            "cards_excluded_by_unsupported_grammar": len(
                unsupported_grammar_cards
            ),
        },
    }


def _spell_history_transformation_measurement(
    *,
    frontier: Mapping[str, Any],
    bundle_id: str,
    probe_id: str,
    cards_by_oracle_id: Mapping[str, Any],
    coverage: Mapping[str, Any],
    cohort_fingerprint: str,
) -> dict[str, Any]:
    """Measure paired bound keywords and exact legacy upkeep transforms."""

    registry = load_default_capability_registry()
    templates = {
        "daybound-static-v1",
        "nightbound-static-v1",
        "previous-turn-no-spells-self-transform-v1",
        "previous-turn-player-two-spells-self-transform-v1",
    }
    matched_cards: dict[str, int] = {}
    complete_cards = 0
    exact_ability_gain = 0
    expected_residual_reduction = 0
    existing_exact_sibling_nodes = 0
    remaining_residual_sibling_nodes = 0
    unsupported_sibling_cards = 0
    unsupported_grammar_cards: set[str] = set()
    for card in frontier.get("cards", []):
        oracle_id = str(card.get("oracle_id") or "")
        record = cards_by_oracle_id.get(oracle_id)
        if record is None:
            raise WorkSelectionCohortMeasurementError(
                f"Cohort measurement lacks pinned card {oracle_id}"
            )
        potential = [
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
        if not potential:
            continue
        compiled = compile_oracle_card(
            record,
            capability_registry=registry,
            capability_profile="commander_review",
        )
        candidate_nodes = [
            node
            for face in compiled.faces
            for node in face.nodes
            if node.exact and node.template_id in templates
        ]
        if len(candidate_nodes) != len(potential):
            unsupported_grammar_cards.add(oracle_id)
            continue
        remaining = [
            node
            for face in compiled.faces
            for node in face.nodes
            if not node.exact
        ]
        matched_cards[oracle_id] = len(remaining)
        gain = len(candidate_nodes)
        exact_ability_gain += gain
        expected_residual_reduction += gain
        existing_exact_sibling_nodes += int(card.get("exact_ability_count", 0))
        remaining_residual_sibling_nodes += len(remaining)
        if compiled.status == "exact":
            complete_cards += 1
        else:
            unsupported_sibling_cards += 1
    reaches_floor = (
        complete_cards >= int(coverage["minimum_complete_card_gain"])
        or exact_ability_gain >= int(coverage["minimum_exact_ability_gain"])
        or expected_residual_reduction
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
        "exact_ability_gain": exact_ability_gain,
        "material_residual_reduction": expected_residual_reduction,
        "decision": (
            "bounded_executable"
            if reaches_floor
            else "retired_below_harvest_floor"
        ),
        "grants_gameplay_trust": False,
        "candidate_accounting": {
            "affected_oracle_carriers": exact_ability_gain,
            "existing_exact_sibling_nodes": existing_exact_sibling_nodes,
            "remaining_residual_sibling_nodes": (
                remaining_residual_sibling_nodes
            ),
            "trusted_program_transitions": complete_cards,
            "unresolved_program_transitions": (
                len(matched_cards) - complete_cards
            ),
            "expected_oracle_residual_reduction": (
                expected_residual_reduction
            ),
            "expected_card_program_residual_reduction": (
                expected_residual_reduction
            ),
            "newly_applicable_high_risk_pairs": 0,
            "cards_excluded_by_unsupported_sibling": (
                unsupported_sibling_cards
            ),
            "cards_excluded_by_unsupported_grammar": len(
                unsupported_grammar_cards
            ),
        },
    }


def _fixed_token_production_measurement(
    *,
    frontier: Mapping[str, Any],
    bundle_id: str,
    probe_id: str,
    cards_by_oracle_id: Mapping[str, Any],
    coverage: Mapping[str, Any],
    cohort_fingerprint: str,
) -> dict[str, Any]:
    """Measure exact gains from the bounded fixed token-production grammar."""

    registry = load_default_capability_registry()
    matched_cards: dict[str, int] = {}
    complete_cards: set[str] = set()
    exact_ability_gain = 0
    expected_residual_reduction = 0
    existing_exact_sibling_nodes = 0
    remaining_residual_sibling_nodes = 0
    unsupported_sibling_cards = 0
    unsupported_grammar_cards: set[str] = set()
    for card in frontier.get("cards", []):
        oracle_id = str(card.get("oracle_id") or "")
        record = cards_by_oracle_id.get(oracle_id)
        if record is None:
            raise WorkSelectionCohortMeasurementError(
                f"Cohort measurement lacks pinned card {oracle_id}"
            )
        blockers = {
            str(value) for value in card.get("minimum_known_blocker_set", ())
        }
        if not (
            blockers.intersection(_FIXED_TOKEN_PRODUCTION_FAMILIES)
            or (
                _CONTINUOUS_LAYER_FAMILY in blockers
                and "Afterlife" in getattr(record, "keywords", ())
            )
        ):
            continue
        compiled = compile_oracle_card(
            record,
            capability_registry=registry,
            capability_profile="commander_review",
        )
        previous_exact = int(card.get("exact_ability_count") or 0)
        current_exact = sum(
            int(node.exact)
            for face in compiled.faces
            for node in face.nodes
        )
        ability_gain = max(0, current_exact - previous_exact)
        if not ability_gain:
            unsupported_grammar_cards.add(oracle_id)
            continue
        previous_residuals = sum(
            len(ability.get("residuals", ()))
            for ability in card.get("abilities", ())
        )
        residual_reduction = max(
            0,
            previous_residuals - len(compiled.material_residuals),
        )
        remaining = len(compiled.material_residuals)
        matched_cards[oracle_id] = remaining
        exact_ability_gain += ability_gain
        expected_residual_reduction += residual_reduction
        existing_exact_sibling_nodes += previous_exact
        remaining_residual_sibling_nodes += remaining
        if (
            card.get("oracle_ir_status") != "exact"
            and compiled.status == "exact"
        ):
            complete_cards.add(oracle_id)
        else:
            unsupported_sibling_cards += 1
    reaches_floor = (
        len(complete_cards) >= int(coverage["minimum_complete_card_gain"])
        or exact_ability_gain >= int(coverage["minimum_exact_ability_gain"])
        or expected_residual_reduction
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
        "exact_ability_gain": exact_ability_gain,
        "material_residual_reduction": expected_residual_reduction,
        "decision": (
            "bounded_executable"
            if reaches_floor
            else "retired_below_harvest_floor"
        ),
        "grants_gameplay_trust": False,
        "candidate_accounting": {
            "affected_oracle_carriers": exact_ability_gain,
            "existing_exact_sibling_nodes": existing_exact_sibling_nodes,
            "remaining_residual_sibling_nodes": (
                remaining_residual_sibling_nodes
            ),
            "trusted_program_transitions": len(complete_cards),
            "unresolved_program_transitions": (
                len(matched_cards) - len(complete_cards)
            ),
            "expected_oracle_residual_reduction": (
                expected_residual_reduction
            ),
            "expected_card_program_residual_reduction": (
                expected_residual_reduction
            ),
            "newly_applicable_high_risk_pairs": 0,
            "cards_excluded_by_unsupported_sibling": (
                unsupported_sibling_cards
            ),
            "cards_excluded_by_unsupported_grammar": len(
                unsupported_grammar_cards
            ),
        },
    }


def _typed_quoted_ability_grant_measurement(
    *,
    frontier: Mapping[str, Any],
    bundle_id: str,
    probe_id: str,
    cards_by_oracle_id: Mapping[str, Any],
    coverage: Mapping[str, Any],
    cohort_fingerprint: str,
) -> dict[str, Any]:
    """Measure exact typed inner programs behind attached or queried grants."""

    registry = load_default_capability_registry()
    matched_cards: dict[str, int] = {}
    complete_cards: set[str] = set()
    affected_carriers = 0
    exact_ability_gain = 0
    expected_residual_reduction = 0
    existing_exact_sibling_nodes = 0
    remaining_residual_sibling_nodes = 0
    unsupported_sibling_cards = 0
    unsupported_grammar_cards: set[str] = set()
    template_ids = {
        "continuous-attached-fixed-characteristics-granted-ability-v1",
        "continuous-fixed-query-granted-ability-v1",
    }
    for card in frontier.get("cards", []):
        oracle_id = str(card.get("oracle_id") or "")
        record = cards_by_oracle_id.get(oracle_id)
        if record is None:
            raise WorkSelectionCohortMeasurementError(
                f"Cohort measurement lacks pinned card {oracle_id}"
            )
        candidates = [
            ability
            for ability in card.get("abilities", ())
            if ability.get("status") != "exact"
            and _CONTINUOUS_LAYER_FAMILY
            in {
                str(value)
                for value in ability.get("blockers", {}).get(
                    "canonical_family_ids", ()
                )
            }
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
        represented = 0
        for ability in candidates:
            face_id = str(ability.get("face_id") or "front")
            source_line = int(ability.get("source_line") or 0)
            face = next(
                (value for value in compiled.faces if value.face_id == face_id),
                None,
            )
            if face is None:
                continue
            outer = [
                node
                for node in face.nodes
                if node.span.line == source_line
                and node.exact
                and node.template_id in template_ids
            ]
            inner = [
                node
                for node in face.nodes
                if node.span.line == source_line
                and node.exact
                and node.kind.startswith("granted_")
            ]
            if len(outer) == 1 and len(inner) == 1:
                represented += 1
        if not represented:
            unsupported_grammar_cards.add(oracle_id)
            continue
        if represented != len(candidates):
            unsupported_grammar_cards.add(oracle_id)
        previous_residuals = sum(
            len(ability.get("residuals", ()))
            for ability in card.get("abilities", ())
        )
        residual_reduction = max(
            0,
            previous_residuals - len(compiled.material_residuals),
        )
        remaining = len(compiled.material_residuals)
        matched_cards[oracle_id] = remaining
        affected_carriers += represented
        exact_ability_gain += represented * 2
        expected_residual_reduction += residual_reduction
        existing_exact_sibling_nodes += int(card.get("exact_ability_count", 0))
        remaining_residual_sibling_nodes += remaining
        if card.get("oracle_ir_status") != "exact" and compiled.status == "exact":
            complete_cards.add(oracle_id)
        else:
            unsupported_sibling_cards += 1
    reaches_floor = (
        len(complete_cards) >= int(coverage["minimum_complete_card_gain"])
        or exact_ability_gain >= int(coverage["minimum_exact_ability_gain"])
        or expected_residual_reduction
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
        "exact_ability_gain": exact_ability_gain,
        "material_residual_reduction": expected_residual_reduction,
        "decision": (
            "bounded_executable"
            if reaches_floor
            else "retired_below_harvest_floor"
        ),
        "grants_gameplay_trust": False,
        "candidate_accounting": {
            "affected_oracle_carriers": affected_carriers,
            "existing_exact_sibling_nodes": existing_exact_sibling_nodes,
            "remaining_residual_sibling_nodes": (
                remaining_residual_sibling_nodes
            ),
            "trusted_program_transitions": len(complete_cards),
            "unresolved_program_transitions": (
                len(matched_cards) - len(complete_cards)
            ),
            "expected_oracle_residual_reduction": (
                expected_residual_reduction
            ),
            "expected_card_program_residual_reduction": (
                expected_residual_reduction
            ),
            "newly_applicable_high_risk_pairs": 0,
            "cards_excluded_by_unsupported_sibling": (
                unsupported_sibling_cards
            ),
            "cards_excluded_by_unsupported_grammar": len(
                unsupported_grammar_cards
            ),
        },
    }


def _partner_with_measurement(
    *,
    frontier: Mapping[str, Any],
    bundle_id: str,
    probe_id: str,
    cards_by_oracle_id: Mapping[str, Any],
    coverage: Mapping[str, Any],
    cohort_fingerprint: str,
) -> dict[str, Any]:
    """Measure the indivisible setup-and-entry Partner with production."""

    registry = load_default_capability_registry()
    matched_cards: dict[str, int] = {}
    complete_cards: set[str] = set()
    affected_carriers = 0
    exact_ability_gain = 0
    expected_residual_reduction = 0
    existing_exact_sibling_nodes = 0
    remaining_residual_sibling_nodes = 0
    unsupported_sibling_cards = 0
    unsupported_grammar_cards: set[str] = set()
    template_ids = {
        COMMANDER_PAIRING_TEMPLATE_ID,
        PARTNER_WITH_SEARCH_TEMPLATE_ID,
    }
    for card in frontier.get("cards", []):
        oracle_id = str(card.get("oracle_id") or "")
        record = cards_by_oracle_id.get(oracle_id)
        if record is None:
            raise WorkSelectionCohortMeasurementError(
                f"Cohort measurement lacks pinned card {oracle_id}"
            )
        candidates = [
            ability
            for ability in card.get("abilities", ())
            if ability.get("status") != "exact"
            and _CONTINUOUS_LAYER_FAMILY
            in {
                str(value)
                for value in ability.get("blockers", {}).get(
                    "canonical_family_ids", ()
                )
            }
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
        represented = 0
        for ability in candidates:
            face_id = str(ability.get("face_id") or "front")
            source_line = int(ability.get("source_line") or 0)
            face = next(
                (
                    value
                    for value in compiled.faces
                    if value.face_id == face_id
                ),
                None,
            )
            nodes = (
                [
                    node
                    for node in face.nodes
                    if node.span.line == source_line
                    and node.exact
                    and node.template_id in template_ids
                ]
                if face is not None
                else []
            )
            if (
                len(nodes) == 2
                and {node.template_id for node in nodes} == template_ids
            ):
                represented += 1
        if not represented:
            unsupported_grammar_cards.add(oracle_id)
            continue
        if represented != len(candidates):
            unsupported_grammar_cards.add(oracle_id)
        previous_residuals = sum(
            max(1, len(ability.get("residuals", ())))
            for ability in card.get("abilities", ())
            if ability.get("status") != "exact"
        )
        residual_reduction = max(
            0,
            previous_residuals - len(compiled.material_residuals),
        )
        remaining = len(compiled.material_residuals)
        matched_cards[oracle_id] = remaining
        affected_carriers += represented
        exact_ability_gain += represented * 2
        expected_residual_reduction += residual_reduction
        existing_exact_sibling_nodes += int(card.get("exact_ability_count", 0))
        remaining_residual_sibling_nodes += remaining
        if card.get("oracle_ir_status") != "exact" and compiled.status == "exact":
            complete_cards.add(oracle_id)
        else:
            unsupported_sibling_cards += 1
    reaches_floor = (
        len(complete_cards) >= int(coverage["minimum_complete_card_gain"])
        or exact_ability_gain >= int(coverage["minimum_exact_ability_gain"])
        or expected_residual_reduction
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
        "exact_ability_gain": exact_ability_gain,
        "material_residual_reduction": expected_residual_reduction,
        "decision": (
            "bounded_executable"
            if reaches_floor
            else "retired_below_harvest_floor"
        ),
        "grants_gameplay_trust": False,
        "candidate_accounting": {
            "affected_oracle_carriers": affected_carriers,
            "existing_exact_sibling_nodes": existing_exact_sibling_nodes,
            "remaining_residual_sibling_nodes": (
                remaining_residual_sibling_nodes
            ),
            "trusted_program_transitions": len(complete_cards),
            "unresolved_program_transitions": (
                len(matched_cards) - len(complete_cards)
            ),
            "expected_oracle_residual_reduction": (
                expected_residual_reduction
            ),
            "expected_card_program_residual_reduction": (
                expected_residual_reduction
            ),
            "newly_applicable_high_risk_pairs": 0,
            "cards_excluded_by_unsupported_sibling": (
                unsupported_sibling_cards
            ),
            "cards_excluded_by_unsupported_grammar": len(
                unsupported_grammar_cards
            ),
        },
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
