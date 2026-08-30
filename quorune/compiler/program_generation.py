from __future__ import annotations

from collections import Counter
from dataclasses import asdict
import hashlib
from typing import Any, Iterable, Mapping

from ..carddb import CardDatabase, CardRecord
from ..death_return import PERSIST_KEYWORD, UNDYING_KEYWORD
from ..bloodthirst import BLOODTHIRST_MECHANIC
from ..fixed_keyword_entry_counters import FIXED_KEYWORD_ENTRY_MECHANICS
from ..object_predicate import ObjectQuerySpec
from ..modular import MODULAR_MECHANIC_ID
from ..riot import RIOT_MECHANIC
from ..semantic_runtime.sunburst import SUNBURST_MECHANIC_ID
from ..renown import RENOWN_MECHANIC_ID
from ..unleash import UNLEASH_MECHANIC
from ..rules.capabilities import (
    CapabilityRegistry,
    capability_covered_mechanics,
    capability_dependencies_for_node,
)
from .modal_program_closure import is_closed_fixed_modal_program
from .prevention_templates import is_closed_fixed_prevention_program
from ..rules.counter_capability_shapes import (
    fixed_counter_placement_group_node_capabilities,
)
from ..rules.delayed_draw_capability_shapes import (
    fixed_next_turn_draw_node_capabilities,
)
from ..rules.graveyard_card_targets import (
    targeted_own_graveyard_return_node_capabilities,
)
from ..rules.public_zone_move_capability_shapes import (
    fixed_public_zone_move_set_node_capabilities,
    public_graveyard_card_exile_node_capabilities,
)
from ..rules.counter_removal_capabilities import (
    all_counter_removal_node_capabilities,
    fixed_counter_removal_node_capabilities,
)
from ..rules.cumulative_upkeep_capability_shapes import (
    fixed_life_cumulative_upkeep_node_capabilities,
    fixed_mana_cumulative_upkeep_node_capabilities,
)
from ..rules.node_capability_shapes import (
    fixed_counter_placement_batch_node_capabilities,
    fixed_counter_placement_node_capabilities,
    fixed_counter_placement_set_node_capabilities,
    fixed_counter_placement_target_set_node_capabilities,
    fixed_self_counter_keyword_action_node_capabilities,
    fixed_bolster_node_capabilities,
    fixed_amass_node_capabilities,
    fixed_target_effect_sequence_node_capabilities,
    fixed_source_effect_sequence_node_capabilities,
    fixed_target_characteristics_node_capabilities,
    temporary_declaration_restriction_node_capabilities,
    fixed_player_counter_placement_node_capabilities,
    fixed_damage_node_capabilities,
    mass_destruction_node_capabilities,
    fixed_draw_node_capabilities,
    fixed_scry_node_capabilities,
    single_explore_node_capabilities,
    single_proliferate_node_capabilities,
    self_regeneration_node_capabilities,
    targeted_counter_node_capabilities,
    targeted_destruction_node_capabilities,
    targeted_exile_node_capabilities,
    targeted_return_to_hand_node_capabilities,
    targeted_tap_state_node_capabilities,
)
from ..rules.fixed_homogeneous_target_set_capability_shapes import (
    is_closed_fixed_homogeneous_target_set_program,
)
from ..rules.token_creation_capability_shapes import (
    fixed_token_creation_node_capabilities,
)
from ..rules.mill_capability_shapes import fixed_mill_node_capabilities
from ..rules.impulse_access_capability_shapes import (
    is_closed_fixed_impulse_access_program,
)
from ..rules.library_search_capability_shapes import (
    fixed_library_search_node_capabilities,
    fixed_type_to_hand_search_node_capabilities,
)
from ..rules.library_selection_capability_shapes import (
    fixed_library_selection_node_capabilities,
)
from ..rules.surveil_capability_shapes import fixed_surveil_node_capabilities
from ..rules.fixed_controller_effect_shapes import (
    fixed_counter_controller_effect_sequence_node_capabilities,
    fixed_controller_effect_sequence_node_capabilities,
    fixed_life_node_capabilities,
)
from ..rules.fixed_effect_clause_shapes import (
    fixed_effect_clause_sequence_node_capabilities,
)
from ..rules.closed_effect_program_shapes import (
    closed_effect_program_node_capabilities,
)
from ..rules.echo_capability_shapes import fixed_mana_echo_node_capabilities
from ..semantics import SemanticProgram, SemanticRegistry
from ..util import stable_json
from ..semantic_runtime.activated_abilities import (
    ACTIVATED_ABILITY_CATALOG_HANDLER_ID,
)
from .activated_ability_catalog import (
    catalog_carrier_is_shadowed,
    with_activated_ability_catalog,
)
from .program_composition import (
    generated_node_groups,
    is_closed_composed_spell_effect_program,
    program_from_generated_node_group,
)


_EVOLVE_MECHANIC = "evolve"
_CASCADE_MECHANIC = "cascade"
_PROWESS_MECHANIC = "prowess"
_EXILE_MECHANIC = "exile"
_TOXIC_MECHANIC = "toxic"


def runtime_handler_footprint(
    program: SemanticProgram,
) -> tuple[str, str, tuple[str, ...]] | None:
    handler_descriptors = tuple(
        sorted(
            _runtime_handler_semantic_descriptor(handler)
            for handler in program.handlers
            if handler.get("handler_id")
            != ACTIVATED_ABILITY_CATALOG_HANDLER_ID
        )
    )
    if not handler_descriptors or any(
        not value for value in handler_descriptors
    ):
        return None
    return program.active_zone, program.event, handler_descriptors


def _distinct_generated_siblings(
    existing: SemanticProgram,
    candidate: SemanticProgram,
) -> bool:
    authored_by = str(candidate.provenance.get("authored_by") or "")
    return bool(
        existing.key != candidate.key
        and authored_by.startswith("oracle-ir-v")
        and existing.provenance.get("authored_by") == authored_by
    )


def _generated_runtime_program_is_shadowed(
    candidate: SemanticProgram,
    existing_programs: Iterable[SemanticProgram],
) -> bool:
    existing = tuple(existing_programs)
    footprint = runtime_handler_footprint(candidate)
    if footprint is not None and any(
        program.trust_level == "trusted"
        and runtime_handler_footprint(program) == footprint
        and not _distinct_generated_siblings(program, candidate)
        for program in existing
    ):
        return True
    return bool(
        candidate.ability_id.startswith("trigger:")
        and any(
            program.trust_level == "trusted"
            and program.active_zone == candidate.active_zone
            and program.event == candidate.event
            and not _distinct_generated_siblings(program, candidate)
            for program in existing
        )
    )


def _runtime_handler_semantic_descriptor(
    handler: dict[str, Any],
) -> str:
    handler_id = str(handler.get("handler_id") or "")
    if handler_id not in {
        "continuous.anthem.power_toughness.v1",
        "continuous.anthem.fixed-query.v2",
    }:
        return (
            stable_json(_canonical_semantic_value(handler))
            if handler_id
            else ""
        )

    condition = dict(handler.get("condition") or {})
    modifier = dict(handler.get("modifier") or {})
    if handler_id == "continuous.anthem.power_toughness.v1":
        predicate = ObjectQuerySpec(
            zones=("battlefield",),
            types_all=("creature",),
            subtypes_all=tuple(
                condition.get("target_subtypes_all") or ()
            ),
        )
        exclude_source = False
    else:
        predicate = ObjectQuerySpec.from_dict(condition.get("predicate"))
        exclude_source = bool(condition.get("exclude_source", False))
    return stable_json(
        {
            "family": "continuous.anthem.fixed-query",
            "event": str(handler.get("event") or ""),
            "target_controller": str(
                condition.get("target_controller") or ""
            ),
            "predicate": predicate.canonical_dict(),
            "exclude_source": exclude_source,
            "modifier": modifier,
        }
    )


_CANONICAL_QUERY_FIELDS = frozenset(
    ObjectQuerySpec().canonical_dict()
)
_LEGACY_QUERY_FIELDS = _CANONICAL_QUERY_FIELDS - {"types_any"}


def _canonical_semantic_value(value: Any) -> Any:
    """Normalize typed query values inside a complete handler descriptor."""

    if isinstance(value, Mapping):
        fields = frozenset(value)
        if fields in {_CANONICAL_QUERY_FIELDS, _LEGACY_QUERY_FIELDS}:
            return ObjectQuerySpec.from_dict(value).canonical_dict()
        return {
            str(key): _canonical_semantic_value(item)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_canonical_semantic_value(item) for item in value]
    return value


def rulings_source_hash(db: CardDatabase, record: CardRecord) -> str:
    rows = sorted(
        (asdict(ruling) for ruling in db.rulings(record)),
        key=lambda row: (
            str(row["published_at"]),
            str(row["source"]),
            str(row["comment"]),
            str(row["oracle_id"]),
        ),
    )
    return hashlib.sha256(stable_json(rows).encode("utf-8")).hexdigest()


def _generated_ability_id(
    *,
    kind: str,
    face_id: str,
    line: int,
    static_declaration: bool,
    node_id: str | None = None,
) -> str | None:
    if kind == "spell_ability":
        return f"spell:{face_id}"
    if kind in {"activated_ability", "mana_ability"}:
        return f"ability:ab{line}"
    if kind == "triggered_ability":
        base = f"trigger:{face_id}:n{line}"
        # Most Oracle lines contain one triggered ability, so preserve their
        # long-lived IDs.  A repeated keyword can nevertheless create more
        # than one independent trigger on the same source line (for example,
        # multiple instances of Evolve).  The IR node carries a canonical
        # ``:<family>:<occurrence>`` suffix for that case; retain it so one
        # generated program cannot overwrite its sibling in the registry.
        parts = str(node_id or "").split(":")
        if (
            len(parts) >= 2
            and parts[-1].isdigit()
            and parts[-2]
            in {
                _EVOLVE_MECHANIC,
                _CASCADE_MECHANIC,
                _PROWESS_MECHANIC,
                PERSIST_KEYWORD,
                RENOWN_MECHANIC_ID,
                UNDYING_KEYWORD,
            }
        ):
            return f"{base}:{parts[-2]}:{parts[-1]}"
        if (
            len(parts) >= 3
            and parts[-1] == "departure"
            and parts[-2].isdigit()
            and parts[-3] == MODULAR_MECHANIC_ID
        ):
            return (
                f"{base}:{MODULAR_MECHANIC_ID}:"
                f"{parts[-2]}:departure"
            )
        return base
    if static_declaration:
        parts = str(node_id or "").split(":")
        suffix = parts[-1]
        if suffix in {"unleash-entry", "unleash-block"}:
            if (
                len(parts) >= 3
                and parts[-2].isdigit()
                and parts[-3] == UNLEASH_MECHANIC
            ):
                return (
                    f"static:{face_id}:n{line}:{UNLEASH_MECHANIC}:"
                    f"{parts[-2]}:{suffix}"
                )
            return f"static:{face_id}:n{line}:{suffix}"
        if (
            suffix == "entry"
            and len(parts) >= 3
            and parts[-2].isdigit()
            and parts[-3]
            in {MODULAR_MECHANIC_ID, *FIXED_KEYWORD_ENTRY_MECHANICS}
        ):
            return (
                f"static:{face_id}:n{line}:{parts[-3]}:"
                f"{parts[-2]}:entry"
            )
        if (
            len(parts) >= 2
            and parts[-1].isdigit()
            and parts[-2]
            in {
                RIOT_MECHANIC,
                BLOODTHIRST_MECHANIC,
                SUNBURST_MECHANIC_ID,
                _TOXIC_MECHANIC,
                "affinity",
            }
        ):
            return (
                f"static:{face_id}:n{line}:{parts[-2]}:"
                f"{parts[-1]}"
            )
        if str(node_id or "").endswith(":flash"):
            return f"static:{face_id}:n{line}:flash"
        node_parts = str(node_id or "").split(":")
        if str(node_id or "").endswith(":convoke") or (
            len(node_parts) >= 2
            and node_parts[-1].isdigit()
            and node_parts[-2] == "convoke"
        ):
            return f"static:{face_id}:n{line}:convoke"
        if str(node_id or "").endswith(":affinity"):
            return f"static:{face_id}:n{line}:affinity"
        return f"static:{face_id}:n{line}"
    return None


def _generated_coverage(*, kind: str, runtime_handler: bool) -> str:
    if kind == "spell_ability":
        return "spell_resolution"
    if kind == "triggered_ability":
        return "triggered_ability"
    if kind == "mana_ability":
        return "activated_mana_ability"
    if runtime_handler:
        return "runtime_static_handler"
    return "activated_ability"


def _has_executable_runtime_handler(program: SemanticProgram) -> bool:
    return any(
        handler.get("handler_id")
        != ACTIVATED_ABILITY_CATALOG_HANDLER_ID
        for handler in program.handlers
    )


def _copy_mapping(value: Any) -> dict[str, Any] | None:
    return dict(value) if value is not None else None


def _validate_generated_program_trust(
    ir: Any,
    *,
    trust_level: str,
    has_rules_derived_trust_carrier: bool,
) -> None:
    if trust_level != "trusted":
        return
    generated_nodes = tuple(
        node
        for face in ir.faces
        for node in face.nodes
        if node.lowerable
        and (node.effects or node.handlers or node.capability_dependencies)
        and _generated_ability_id(
            kind=node.kind,
            face_id=face.face_id,
            line=node.span.line,
            static_declaration=_generated_static_declaration(node),
            node_id=node.node_id,
        )
        is not None
    )
    if not generated_nodes and has_rules_derived_trust_carrier:
        # A rules-derived card-form declaration can be the only executable
        # trust carrier (for example, a basic land whose Oracle line is solely
        # nonexecuting reminder text).  CardProgram residual and card-form
        # validation owns trust only when the caller found such a declaration.
        return
    if generated_nodes and any(
        _generated_node_is_independently_exact(node)
        for node in generated_nodes
    ):
        return
    raise ValueError(
        f"{ir.card_name} cannot be promoted to trusted generated "
        "semantics while material Oracle residuals remain on "
        "generated nodes"
    )


def _independently_exact_protection_handler(node: Any) -> bool:
    """Allow closed protection fragments on a partially known keyword line.

    Printed comma-separated keyword lists are independent abilities.  A typed
    protection fragment therefore remains exact even when a sibling keyword
    on the same Oracle line lacks a capability contract.  No effect program or
    arbitrary runtime-handler family receives this exception.
    """

    return bool(
        node.kind == "keyword_ability"
        and node.template_id == "printed-keyword-list-v1"
        and not node.effects
        and node.handlers
        and all(
            handler.get("handler_id") == "ability.static.protection.v1"
            for handler in node.handlers
        )
        and tuple(node.capability_dependencies)
        == ("protection.typed.debt",)
    )


def _generated_node_is_independently_exact(node: Any) -> bool:
    return bool(
        node.exact or _independently_exact_protection_handler(node)
    )


def _generated_static_declaration(node: Any) -> bool:
    return bool(
        node.handlers
        or (
            node.kind == "keyword_ability"
            and node.capability_dependencies
        )
        or (
            node.kind == "static_ability"
            and node.template_id
            == "intrinsic-spell-counter-prohibition-v1"
            and tuple(node.capability_dependencies)
            == ("stack.counter.prohibition.intrinsic",)
            and not node.effects
        )
    )


def _is_closed_fixed_damage_program(program: SemanticProgram) -> bool:
    """Recognize only the reviewed fixed-damage effect-program family."""

    template_id = str(program.provenance.get("template_id") or "")
    if not template_id.startswith("damage-"):
        return False
    required = set(
        fixed_damage_node_capabilities(
            effects=program.effects,
            target_schema=program.target_schema,
            mechanic_ids=(
                value
                for value in program.coverage
                if value.startswith("cr-")
            ),
        )
    )
    return bool(required) and required.issubset(
        program.capability_dependencies
    )


def _is_closed_fixed_draw_program(program: SemanticProgram) -> bool:
    """Recognize only fixed-count draw effect programs with strict shapes."""

    required = set(
        fixed_draw_node_capabilities(
            effects=program.effects,
            target_schema=program.target_schema,
            mechanic_ids=(
                value
                for value in program.coverage
                if value.startswith("cr-")
            ),
        )
    )
    return bool(required) and required.issubset(
        program.capability_dependencies
    )


def _is_closed_fixed_next_turn_draw_program(
    program: SemanticProgram,
) -> bool:
    """Recognize only the canonical single-use delayed controller draw."""

    required = set(
        fixed_next_turn_draw_node_capabilities(
            effects=program.effects,
            target_schema=program.target_schema,
            mechanic_ids=program.coverage,
        )
    )
    return bool(required) and required.issubset(
        program.capability_dependencies
    )


def _is_closed_fixed_mill_program(program: SemanticProgram) -> bool:
    """Recognize only mandatory fixed-count single-player Mill effects."""

    required = set(
        fixed_mill_node_capabilities(
            effects=program.effects,
            target_schema=program.target_schema,
            mechanic_ids=program.coverage,
        )
    )
    return bool(required) and required.issubset(
        program.capability_dependencies
    )


def _is_closed_fixed_library_search_program(
    program: SemanticProgram,
) -> bool:
    """Recognize one restrictive fixed library search to the battlefield."""

    required = set(
        fixed_library_search_node_capabilities(
            effects=program.effects,
            target_schema=program.target_schema,
            mechanic_ids=program.coverage,
        )
    )
    return bool(required) and required.issubset(
        program.capability_dependencies
    )


def _is_closed_fixed_type_to_hand_search_program(
    program: SemanticProgram,
) -> bool:
    """Recognize only the closed fixed Typecycling search instruction."""

    required = set(
        fixed_type_to_hand_search_node_capabilities(
            effects=program.effects,
            target_schema=program.target_schema,
            mechanic_ids=program.coverage,
        )
    )
    return bool(required) and required.issubset(
        program.capability_dependencies
    )


def _node_capabilities_close_program(
    program: SemanticProgram,
    resolver: Any,
) -> bool:
    required = set(
        resolver(
            effects=program.effects,
            target_schema=program.target_schema,
            mechanic_ids=program.coverage,
        )
    )
    return bool(required) and required.issubset(
        program.capability_dependencies
    )


def _is_closed_fixed_life_program(program: SemanticProgram) -> bool:
    """Recognize only reviewed fixed controller or opponent life effects."""

    return _node_capabilities_close_program(
        program,
        fixed_life_node_capabilities,
    )


def _is_closed_fixed_scry_program(program: SemanticProgram) -> bool:
    """Recognize only one reviewed fixed controller Scry instruction."""

    return _node_capabilities_close_program(
        program,
        fixed_scry_node_capabilities,
    )


def _is_closed_fixed_library_selection_program(
    program: SemanticProgram,
) -> bool:
    """Recognize one fixed controller library selection partition."""

    return _node_capabilities_close_program(
        program,
        fixed_library_selection_node_capabilities,
    )


def _is_closed_fixed_surveil_program(program: SemanticProgram) -> bool:
    """Recognize one fixed positive controller Surveil instruction."""

    return _node_capabilities_close_program(
        program,
        fixed_surveil_node_capabilities,
    )


def _is_closed_fixed_token_creation_program(
    program: SemanticProgram,
) -> bool:
    """Recognize one compiler-owned fixed token-definition instruction."""

    return _node_capabilities_close_program(
        program,
        fixed_token_creation_node_capabilities,
    )


def _is_closed_fixed_effect_clause_sequence_program(
    program: SemanticProgram,
) -> bool:
    """Recognize two independently closed effects in printed order."""

    if program.provenance.get("template_id") != (
        "fixed-effect-clause-sequence-v1"
    ):
        return False
    required = set(
        fixed_effect_clause_sequence_node_capabilities(
            effects=program.effects,
            target_schema=program.target_schema,
            mechanic_ids=program.coverage,
        )
    )
    return bool(required) and required.issubset(
        program.capability_dependencies
    )


def _is_closed_composed_effect_program(
    program: SemanticProgram,
) -> bool:
    """Recognize a bounded program of independently closed components."""

    if (
        program.provenance.get("template_id") != "closed-effect-program-v1"
        and "closed-effect-program" not in program.coverage
    ):
        return False
    required = set(
        closed_effect_program_node_capabilities(
            effects=program.effects,
            target_schema=program.target_schema,
            mechanic_ids=program.coverage,
        )
    )
    return bool(required) and required.issubset(
        program.capability_dependencies
    )


def _is_closed_fixed_controller_effect_sequence_program(
    program: SemanticProgram,
) -> bool:
    """Recognize the ordered fixed controller draw sequence family."""

    required = set(
        fixed_controller_effect_sequence_node_capabilities(
            effects=program.effects,
            target_schema=program.target_schema,
            mechanic_ids=program.coverage,
        )
    )
    return bool(required) and required.issubset(
        program.capability_dependencies
    )


def _is_closed_fixed_counter_controller_effect_sequence_program(
    program: SemanticProgram,
) -> bool:
    """Recognize one fixed counter and controller-effect sequence."""

    required = set(
        fixed_counter_controller_effect_sequence_node_capabilities(
            effects=program.effects,
            target_schema=program.target_schema,
            mechanic_ids=program.coverage,
        )
    )
    return bool(required) and required.issubset(
        program.capability_dependencies
    )


def _is_closed_single_explore_program(program: SemanticProgram) -> bool:
    """Recognize only one source or controlled target exploring once."""

    required = set(
        single_explore_node_capabilities(
            effects=program.effects,
            target_schema=program.target_schema,
            mechanic_ids=(
                value
                for value in program.coverage
                if value in {"explore", "cr-115-targets"}
            ),
        )
    )
    return bool(required) and required.issubset(
        program.capability_dependencies
    )


def _is_closed_single_proliferate_program(
    program: SemanticProgram,
) -> bool:
    """Recognize one ordinary Proliferate instruction."""

    required = set(
        single_proliferate_node_capabilities(
            effects=program.effects,
            target_schema=program.target_schema,
            mechanic_ids=(
                value
                for value in program.coverage
                if value == "proliferate"
            ),
        )
    )
    return bool(required) and required.issubset(
        program.capability_dependencies
    )


def _is_closed_targeted_tap_state_program(
    program: SemanticProgram,
) -> bool:
    """Recognize only the reviewed direct-target tap/untap effect family."""

    required = set(
        targeted_tap_state_node_capabilities(
            effects=program.effects,
            target_schema=program.target_schema,
            mechanic_ids=(
                value
                for value in program.coverage
                if value in {"tap-and-untap", "cr-115-targets"}
            ),
        )
    )
    return bool(required) and required.issubset(
        program.capability_dependencies
    )


def _is_closed_targeted_counter_program(
    program: SemanticProgram,
) -> bool:
    """Recognize only the reviewed direct stack-counter effect family."""

    required = set(
        targeted_counter_node_capabilities(
            effects=program.effects,
            target_schema=program.target_schema,
            mechanic_ids=(
                value
                for value in program.coverage
                if value in {"counter", "cr-115-targets"}
            ),
        )
    )
    return bool(required) and required.issubset(
        program.capability_dependencies
    )


def _is_closed_fixed_counter_placement_program(
    program: SemanticProgram,
) -> bool:
    """Recognize only the reviewed fixed permanent-counter effect family."""

    required = set(
        fixed_counter_placement_node_capabilities(
            effects=program.effects,
            target_schema=program.target_schema,
            mechanic_ids=(
                value
                for value in program.coverage
                if value in {"cr-122-counters", "cr-115-targets"}
            ),
        )
    )
    return bool(required) and required.issubset(
        program.capability_dependencies
    )


def _is_closed_fixed_counter_removal_program(
    program: SemanticProgram,
) -> bool:
    """Recognize only the reviewed fixed permanent-counter removal family."""

    required = set(
        fixed_counter_removal_node_capabilities(
            effects=program.effects,
            target_schema=program.target_schema,
            mechanic_ids=(
                value
                for value in program.coverage
                if value in {"cr-122-counters", "cr-115-targets"}
            ),
        )
    )
    return bool(required) and required.issubset(
        program.capability_dependencies
    )


def _is_closed_all_counter_removal_program(
    program: SemanticProgram,
) -> bool:
    """Recognize only the reviewed direct all-counter removal family."""

    required = set(
        all_counter_removal_node_capabilities(
            effects=program.effects,
            target_schema=program.target_schema,
            mechanic_ids=(
                value
                for value in program.coverage
                if value in {"cr-122-counters", "cr-115-targets"}
            ),
        )
    )
    return bool(required) and required.issubset(
        program.capability_dependencies
    )


def _is_closed_fixed_counter_placement_batch_program(
    program: SemanticProgram,
) -> bool:
    """Recognize one reviewed fixed multi-kind counter batch."""

    required = set(
        fixed_counter_placement_batch_node_capabilities(
            effects=program.effects,
            target_schema=program.target_schema,
            mechanic_ids=program.coverage,
        )
    )
    return bool(required) and required.issubset(
        program.capability_dependencies
    )


def _is_closed_fixed_counter_placement_group_program(
    program: SemanticProgram,
) -> bool:
    """Recognize one reviewed fixed same-kind multi-subject placement."""

    required = set(
        fixed_counter_placement_group_node_capabilities(
            effects=program.effects,
            target_schema=program.target_schema,
            mechanic_ids=program.coverage,
        )
    )
    return bool(required) and required.issubset(
        program.capability_dependencies
    )


def _is_closed_fixed_self_counter_keyword_action_program(
    program: SemanticProgram,
) -> bool:
    """Recognize one capability-closed fixed self-counter keyword action."""

    required = set(
        fixed_self_counter_keyword_action_node_capabilities(
            effects=program.effects,
            target_schema=program.target_schema,
            mechanic_ids=program.coverage,
        )
    )
    return bool(required) and required.issubset(
        program.capability_dependencies
    )


def _is_closed_fixed_bolster_program(program: SemanticProgram) -> bool:
    """Recognize one capability-closed fixed positive Bolster action."""

    required = set(
        fixed_bolster_node_capabilities(
            effects=program.effects,
            target_schema=program.target_schema,
            mechanic_ids=program.coverage,
        )
    )
    return bool(required) and required.issubset(
        program.capability_dependencies
    )


def _is_closed_fixed_amass_program(program: SemanticProgram) -> bool:
    """Recognize one capability-closed fixed positive Amass action."""

    required = set(
        fixed_amass_node_capabilities(
            effects=program.effects,
            target_schema=program.target_schema,
            mechanic_ids=program.coverage,
        )
    )
    return bool(required) and required.issubset(
        program.capability_dependencies
    )


def _is_closed_fixed_target_effect_sequence_program(
    program: SemanticProgram,
) -> bool:
    """Recognize one closed target-threaded counter/characteristic sequence."""

    required = set(
        fixed_target_effect_sequence_node_capabilities(
            effects=program.effects,
            target_schema=program.target_schema,
            mechanic_ids=program.coverage,
        )
    )
    return bool(required) and required.issubset(
        program.capability_dependencies
    )


def _is_closed_fixed_source_effect_sequence_program(
    program: SemanticProgram,
) -> bool:
    """Recognize one closed source-threaded counter/characteristic sequence."""

    required = set(
        fixed_source_effect_sequence_node_capabilities(
            effects=program.effects,
            target_schema=program.target_schema,
            mechanic_ids=program.coverage,
        )
    )
    return bool(required) and required.issubset(
        program.capability_dependencies
    )


def _is_closed_fixed_target_characteristics_program(
    program: SemanticProgram,
) -> bool:
    """Recognize one closed targeted fixed characteristic effect."""

    required = set(
        fixed_target_characteristics_node_capabilities(
            effects=program.effects,
            target_schema=program.target_schema,
            mechanic_ids=program.coverage,
        )
    )
    return bool(required) and required.issubset(
        program.capability_dependencies
    )


def _is_closed_temporary_declaration_restriction_program(
    program: SemanticProgram,
) -> bool:
    """Recognize one capability-closed temporary declaration restriction."""

    required = set(
        temporary_declaration_restriction_node_capabilities(
            effects=program.effects,
            target_schema=program.target_schema,
            mechanic_ids=program.coverage,
        )
    )
    return bool(required) and required.issubset(
        program.capability_dependencies
    )


def _is_closed_fixed_player_counter_placement_program(
    program: SemanticProgram,
) -> bool:
    """Recognize only the reviewed fixed player-counter effect family."""

    required = set(
        fixed_player_counter_placement_node_capabilities(
            effects=program.effects,
            target_schema=program.target_schema,
            mechanic_ids=(
                value
                for value in program.coverage
                if value in {"cr-122-counters", "cr-115-targets"}
            ),
        )
    )
    return bool(required) and required.issubset(
        program.capability_dependencies
    )


def _is_closed_fixed_mana_cumulative_upkeep_program(
    program: SemanticProgram,
) -> bool:
    """Recognize exactly one ordinary fixed-mana upkeep trigger."""

    required = set(
        fixed_mana_cumulative_upkeep_node_capabilities(
            effects=program.effects,
            event_condition=program.event_condition,
            target_schema=program.target_schema,
            mechanic_ids=(
                value
                for value in program.coverage
                if value == "cumulative upkeep"
            ),
        )
    )
    return bool(required) and required.issubset(
        program.capability_dependencies
    )


def _is_closed_fixed_life_cumulative_upkeep_program(
    program: SemanticProgram,
) -> bool:
    """Recognize exactly one fixed-life cumulative-upkeep trigger."""

    required = set(
        fixed_life_cumulative_upkeep_node_capabilities(
            effects=program.effects,
            event_condition=program.event_condition,
            target_schema=program.target_schema,
            mechanic_ids=(
                value
                for value in program.coverage
                if value == "cumulative upkeep"
            ),
        )
    )
    return bool(required) and required.issubset(
        program.capability_dependencies
    )


def _is_closed_fixed_mana_echo_program(
    program: SemanticProgram,
) -> bool:
    """Recognize exactly one ordinary fixed-mana Echo trigger."""

    required = set(
        fixed_mana_echo_node_capabilities(
            effects=program.effects,
            event_condition=program.event_condition,
            target_schema=program.target_schema,
            mechanic_ids=(
                value for value in program.coverage if value == "echo"
            ),
        )
    )
    return bool(required) and required.issubset(
        program.capability_dependencies
    )


def _is_closed_fixed_counter_placement_set_program(
    program: SemanticProgram,
) -> bool:
    """Recognize only the reviewed fixed affected-set counter family."""

    required = set(
        fixed_counter_placement_set_node_capabilities(
            effects=program.effects,
            target_schema=program.target_schema,
            mechanic_ids=(
                value
                for value in program.coverage
                if value in {"cr-122-counters", "cr-115-targets"}
            ),
        )
    )
    return bool(required) and required.issubset(
        program.capability_dependencies
    )


def _is_closed_fixed_counter_placement_target_set_program(
    program: SemanticProgram,
) -> bool:
    """Recognize only the reviewed fixed permanent target-set family."""

    required = set(
        fixed_counter_placement_target_set_node_capabilities(
            effects=program.effects,
            target_schema=program.target_schema,
            mechanic_ids=(
                value
                for value in program.coverage
                if value in {"cr-122-counters", "cr-115-targets", "support"}
            ),
        )
    )
    return bool(required) and required.issubset(
        program.capability_dependencies
    )


def _is_closed_targeted_destruction_program(
    program: SemanticProgram,
) -> bool:
    """Recognize only the reviewed direct-target destruction family."""

    required = set(
        targeted_destruction_node_capabilities(
            effects=program.effects,
            target_schema=program.target_schema,
            mechanic_ids=(
                value
                for value in program.coverage
                if value in {"destroy", "cr-115-targets", "regeneration-prohibition"}
            ),
        )
    )
    return bool(required) and required.issubset(
        program.capability_dependencies
    )


def _is_closed_self_regeneration_program(
    program: SemanticProgram,
) -> bool:
    """Recognize only the reviewed fixed regeneration effect family."""

    required = set(
        self_regeneration_node_capabilities(
            effects=program.effects,
            target_schema=program.target_schema,
            mechanic_ids=(
                value for value in program.coverage if value in {"regenerate", "cr-115-targets"}
            ),
        )
    )
    return bool(required) and required.issubset(
        program.capability_dependencies
    )


def _is_closed_mass_destruction_program(
    program: SemanticProgram,
) -> bool:
    """Recognize only the reviewed fixed affected-set destruction family."""

    required = set(
        mass_destruction_node_capabilities(
            effects=program.effects,
            target_schema=program.target_schema,
            mechanic_ids=(
                value
                for value in program.coverage
                if value in {"destroy", "destroy-fixed-set", "cr-115-targets", "regeneration-prohibition"}
            ),
        )
    )
    return bool(required) and required.issubset(
        program.capability_dependencies
    )


def _is_closed_targeted_exile_program(
    program: SemanticProgram,
) -> bool:
    """Recognize only the reviewed direct battlefield exile family."""

    required = set(
        targeted_exile_node_capabilities(
            effects=program.effects,
            target_schema=program.target_schema,
            mechanic_ids=(
                value
                for value in program.coverage
                if value in {_EXILE_MECHANIC, "cr-115-targets"}
            ),
        )
    )
    return bool(required) and required.issubset(
        program.capability_dependencies
    )


def _is_closed_targeted_return_to_hand_program(
    program: SemanticProgram,
) -> bool:
    """Recognize only the reviewed direct battlefield return family."""

    required = set(
        targeted_return_to_hand_node_capabilities(
            effects=program.effects,
            target_schema=program.target_schema,
            mechanic_ids=(
                value
                for value in program.coverage
                if value in {"return-to-owner-hand", "cr-115-targets"}
            ),
        )
    )
    return bool(required) and required.issubset(
        program.capability_dependencies
    )


def _is_closed_targeted_own_graveyard_return_program(
    program: SemanticProgram,
) -> bool:
    """Recognize only the reviewed own-graveyard card return family."""

    required = set(
        targeted_own_graveyard_return_node_capabilities(
            effects=program.effects,
            target_schema=program.target_schema,
            mechanic_ids=(
                value
                for value in program.coverage
                if value in {"return-to-owner-hand", "cr-115-targets"}
            ),
        )
    )
    return bool(required) and required.issubset(
        program.capability_dependencies
    )


def _is_closed_public_graveyard_card_exile_program(
    program: SemanticProgram,
) -> bool:
    required = set(
        public_graveyard_card_exile_node_capabilities(
            effects=program.effects,
            target_schema=program.target_schema,
            mechanic_ids=(
                value
                for value in program.coverage
                if value
                in {"exile", "fixed-public-zone-move", "cr-115-targets"}
            ),
        )
    )
    return bool(required) and required.issubset(
        program.capability_dependencies
    )


def _is_closed_fixed_public_zone_move_set_program(
    program: SemanticProgram,
) -> bool:
    required = set(
        fixed_public_zone_move_set_node_capabilities(
            effects=program.effects,
            target_schema=program.target_schema,
            mechanic_ids=(
                value
                for value in program.coverage
                if value
                in {
                    "exile",
                    "return-to-owner-hand",
                    "fixed-public-zone-move",
                    "fixed-public-zone-move-set",
                    "cr-115-targets",
                }
            ),
        )
    )
    return bool(required) and required.issubset(
        program.capability_dependencies
    )


def _closed_effect_recognizers():
    return (
        is_closed_fixed_modal_program,
        _is_closed_fixed_damage_program,
        _is_closed_fixed_next_turn_draw_program,
        _is_closed_fixed_draw_program,
        _is_closed_fixed_mill_program,
        is_closed_fixed_impulse_access_program,
        _is_closed_fixed_library_search_program,
        _is_closed_fixed_type_to_hand_search_program,
        _is_closed_fixed_life_program,
        _is_closed_fixed_library_selection_program,
        _is_closed_fixed_scry_program,
        _is_closed_fixed_surveil_program,
        _is_closed_fixed_token_creation_program,
        is_closed_fixed_prevention_program,
        _is_closed_fixed_effect_clause_sequence_program,
        _is_closed_composed_effect_program,
        _is_closed_fixed_controller_effect_sequence_program,
        _is_closed_fixed_counter_controller_effect_sequence_program,
        _is_closed_single_explore_program,
        _is_closed_single_proliferate_program,
        _is_closed_fixed_counter_placement_program,
        _is_closed_fixed_counter_removal_program,
        _is_closed_all_counter_removal_program,
        _is_closed_fixed_counter_placement_batch_program,
        _is_closed_fixed_counter_placement_group_program,
        _is_closed_fixed_self_counter_keyword_action_program,
        _is_closed_fixed_bolster_program,
        _is_closed_fixed_amass_program,
        _is_closed_fixed_target_characteristics_program,
        _is_closed_temporary_declaration_restriction_program,
        _is_closed_fixed_target_effect_sequence_program,
        _is_closed_fixed_source_effect_sequence_program,
        _is_closed_fixed_counter_placement_set_program,
        _is_closed_fixed_counter_placement_target_set_program,
        is_closed_fixed_homogeneous_target_set_program,
        _is_closed_fixed_player_counter_placement_program,
        _is_closed_fixed_life_cumulative_upkeep_program,
        _is_closed_fixed_mana_cumulative_upkeep_program,
        _is_closed_fixed_mana_echo_program,
        _is_closed_targeted_counter_program,
        _is_closed_targeted_destruction_program,
        _is_closed_self_regeneration_program,
        _is_closed_mass_destruction_program,
        _is_closed_targeted_exile_program,
        _is_closed_targeted_return_to_hand_program,
        _is_closed_targeted_own_graveyard_return_program,
        _is_closed_public_graveyard_card_exile_program,
        _is_closed_fixed_public_zone_move_set_program,
        _is_closed_targeted_tap_state_program,
    )


def _is_closed_effect_program(program: SemanticProgram) -> bool:
    """Return whether a reviewed capability-shaped effect owns execution."""

    if is_closed_composed_spell_effect_program(
        program,
        component_recognizers=_closed_effect_recognizers(),
    ):
        return True
    recognizers = _closed_effect_recognizers()
    return any(recognizer(program) for recognizer in recognizers)


def generated_programs(
    db: CardDatabase,
    record: CardRecord,
    *,
    trust_level: str = "provisional",
    trusted_mechanics: Iterable[str] = (),
    capability_registry: CapabilityRegistry | None = None,
    capability_profile: str = "traditional",
    has_rules_derived_trust_carrier: bool = False,
) -> list[SemanticProgram]:
    """Lower exact Oracle IR nodes into the generic effect DSL."""

    # Imported lazily so oracle_ir can retain its stable public compatibility
    # functions without creating a module-initialization cycle.
    from ..oracle_ir import ORACLE_COMPILER_VERSION, compile_oracle_card

    ir = compile_oracle_card(
        record,
        trusted_mechanics=trusted_mechanics,
        capability_registry=capability_registry,
        capability_profile=capability_profile,
    )
    _validate_generated_program_trust(
        ir,
        trust_level=trust_level,
        has_rules_derived_trust_carrier=has_rules_derived_trust_carrier,
    )
    programs: list[SemanticProgram] = []
    rulings_hash = rulings_source_hash(db, record)
    for face in ir.faces:
        ability_id_for = lambda node: _generated_ability_id(
            kind=node.kind,
            face_id=face.face_id,
            line=node.span.line,
            static_declaration=_generated_static_declaration(node),
            node_id=node.node_id,
        )
        for nodes in generated_node_groups(
            face,
            ability_id_for=ability_id_for,
        ):
            program = program_from_generated_node_group(
                record=record,
                face=face,
                nodes=nodes,
                ir=ir,
                rulings_hash=rulings_hash,
                authored_by=ORACLE_COMPILER_VERSION,
                trust_level=trust_level,
                capability_registry=capability_registry,
                capability_profile=capability_profile,
                ability_id_for=ability_id_for,
                is_static_declaration=_generated_static_declaration,
                is_independently_exact=(
                    _generated_node_is_independently_exact
                ),
                represented_mechanics_for=lambda node: (
                    capability_covered_mechanics(
                        node.capability_dependencies
                    )
                    if trust_level == "trusted"
                    and not node.exact
                    and _independently_exact_protection_handler(node)
                    else node.mechanics
                ),
                generated_coverage=_generated_coverage,
            )
            if program is not None:
                programs.append(program)
    return list(
        with_activated_ability_catalog(
            record,
            programs,
            carrier_provenance={
                "source_oracle_hash": ir.oracle_hash,
                "source_rulings_hash": rulings_hash,
                "authored_by": ORACLE_COMPILER_VERSION,
                "review_status": "generated_review_required",
            },
        )
    )


def _trusted_program_is_requested(
    program: SemanticProgram,
    *,
    promotable_effect_keys: set[str],
    promote_exact_trigger_programs: bool,
    promote_exact_effect_programs: bool,
    promote_exact_capability_declarations: bool,
) -> bool:
    return bool(
        _has_executable_runtime_handler(program)
        or (
            promote_exact_effect_programs
            and program.key in promotable_effect_keys
            and _is_closed_effect_program(program)
            and program.ability_id.startswith(("spell:", "ability:"))
        )
        or (
            promote_exact_trigger_programs
            and program.ability_id.startswith("trigger:")
        )
        or (
            promote_exact_capability_declarations
            and program.ability_id.startswith("static:")
            and program.capability_dependencies
        )
    )


def _trusted_generated_programs(
    db: CardDatabase,
    record: CardRecord,
    *,
    provisional_programs: list[SemanticProgram],
    promotable_effect_keys: set[str],
    trust_level: str,
    trusted_mechanics: Iterable[str],
    capability_registry: CapabilityRegistry | None,
    capability_profile: str,
    promote_exact_runtime_handlers: bool,
    promote_exact_trigger_programs: bool,
    promote_exact_effect_programs: bool,
    promote_exact_capability_declarations: bool,
) -> dict[str, SemanticProgram]:
    promotion_requested = any(
        (
            promote_exact_runtime_handlers,
            promote_exact_trigger_programs,
            promote_exact_effect_programs,
            promote_exact_capability_declarations,
        )
    )
    candidate_exists = bool(
        promote_exact_trigger_programs
        and any(
            program.ability_id.startswith("trigger:")
            for program in provisional_programs
        )
        or any(
            _has_executable_runtime_handler(program)
            for program in provisional_programs
        )
        or (
            promote_exact_effect_programs
            and promotable_effect_keys
        )
        or (
            promote_exact_capability_declarations
            and any(
                program.ability_id.startswith("static:")
                and program.capability_dependencies
                for program in provisional_programs
            )
        )
    )
    if (
        not promotion_requested
        or trust_level != "provisional"
        or capability_registry is None
        or not candidate_exists
    ):
        return {}
    try:
        candidates = generated_programs(
            db,
            record,
            trust_level="trusted",
            trusted_mechanics=trusted_mechanics,
            capability_registry=capability_registry,
            capability_profile=capability_profile,
        )
    except ValueError as exc:
        if "cannot be promoted to trusted generated semantics" not in str(exc):
            raise
        return {}
    return {
        program.key: program
        for program in candidates
        if _trusted_program_is_requested(
            program,
            promotable_effect_keys=promotable_effect_keys,
            promote_exact_trigger_programs=promote_exact_trigger_programs,
            promote_exact_effect_programs=(
                promote_exact_effect_programs
            ),
            promote_exact_capability_declarations=(
                promote_exact_capability_declarations
            ),
        )
    }


def register_generated_programs(
    db: CardDatabase,
    registry: SemanticRegistry,
    records: Iterable[CardRecord],
    *,
    trust_level: str = "provisional",
    trusted_mechanics: Iterable[str] = (),
    capability_registry: CapabilityRegistry | None = None,
    capability_profile: str = "traditional",
    promote_exact_runtime_handlers: bool = False,
    promote_exact_trigger_programs: bool = False,
    promote_exact_effect_programs: bool = False,
    promote_exact_capability_declarations: bool = False,
) -> dict[str, Any]:
    from ..oracle_ir import ORACLE_COMPILER_VERSION

    generated = 0
    skipped_existing = 0
    promoted_runtime_handlers = 0
    promoted_exact_programs = 0
    promoted_exact_effect_programs = 0
    promoted_exact_fixed_damage_programs = 0
    promoted_exact_fixed_draw_programs = 0
    cards_seen: set[str] = set()
    for record in records:
        if record.oracle_id in cards_seen:
            continue
        cards_seen.add(record.oracle_id)
        provisional_programs = generated_programs(
            db,
            record,
            trust_level=trust_level,
            trusted_mechanics=trusted_mechanics,
            capability_registry=capability_registry,
            capability_profile=capability_profile,
        )
        existing_programs = registry.programs_for_oracle(record.oracle_id)
        for existing, augmented in zip(
            existing_programs,
            with_activated_ability_catalog(
                record,
                existing_programs,
                reference_programs=provisional_programs,
            ),
            strict=True,
        ):
            if existing.to_dict() != augmented.to_dict():
                registry.put(augmented)
        program_key_counts = Counter(
            program.key for program in provisional_programs
        )
        promotable_effect_keys = {
            program.key
            for program in provisional_programs
            if program_key_counts[program.key] == 1
            and _is_closed_effect_program(program)
            and program.ability_id.startswith(("spell:", "ability:"))
        }
        trusted_programs = _trusted_generated_programs(
            db,
            record,
            provisional_programs=provisional_programs,
            promotable_effect_keys=promotable_effect_keys,
            trust_level=trust_level,
            trusted_mechanics=trusted_mechanics,
            capability_registry=capability_registry,
            capability_profile=capability_profile,
            promote_exact_runtime_handlers=promote_exact_runtime_handlers,
            promote_exact_trigger_programs=promote_exact_trigger_programs,
            promote_exact_effect_programs=(
                promote_exact_effect_programs
            ),
            promote_exact_capability_declarations=(
                promote_exact_capability_declarations
            ),
        )
        for provisional in provisional_programs:
            program = trusted_programs.get(provisional.key, provisional)
            if registry.get(program.key) is not None:
                skipped_existing += 1
                continue
            if catalog_carrier_is_shadowed(
                record,
                program,
                registry.programs_for_oracle(record.oracle_id),
            ):
                skipped_existing += 1
                continue
            if _generated_runtime_program_is_shadowed(
                program,
                registry.programs_for_oracle(record.oracle_id),
            ):
                # Reviewed event handlers take precedence. Trigger program
                # keys are author-defined, so key equality alone cannot detect
                # that a reviewed pack already owns this event family.
                skipped_existing += 1
                continue
            if program is not provisional:
                promoted_exact_programs += 1
                if _has_executable_runtime_handler(program):
                    promoted_runtime_handlers += 1
                if (
                    program.key in promotable_effect_keys
                    and _is_closed_effect_program(program)
                    and program.ability_id.startswith(
                        ("spell:", "ability:")
                    )
                ):
                    promoted_exact_effect_programs += 1
                    if _is_closed_fixed_damage_program(program):
                        promoted_exact_fixed_damage_programs += 1
                    if _is_closed_fixed_draw_program(program):
                        promoted_exact_fixed_draw_programs += 1
            registry.put(program)
            generated += 1
    return {
        "cards_considered": len(cards_seen),
        "programs_generated": generated,
        "programs_skipped_existing": skipped_existing,
        "runtime_handlers_promoted": promoted_runtime_handlers,
        "exact_programs_promoted": promoted_exact_programs,
        "exact_effect_programs_promoted": promoted_exact_effect_programs,
        "exact_fixed_damage_programs_promoted": (
            promoted_exact_fixed_damage_programs
        ),
        "exact_fixed_draw_programs_promoted": (
            promoted_exact_fixed_draw_programs
        ),
        "trust_level": trust_level,
        "compiler_version": ORACLE_COMPILER_VERSION,
        "capability_registry_fingerprint": (
            capability_registry.fingerprint
            if capability_registry is not None
            else None
        ),
        "capability_profile": (
            capability_profile if capability_registry is not None else None
        ),
    }
