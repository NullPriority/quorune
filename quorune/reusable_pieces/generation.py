from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field
import gzip
import hashlib
import json
from pathlib import Path
import re
from typing import Any, Iterable, Mapping, Sequence

from ..util import stable_json
from .architecture_metrics import project_architecture_metrics as _architecture_metrics
from .interactions import (
    _PIECE_ID,
    build_interactions as _build_interactions,
)


REUSABLE_PIECE_SCHEMA_VERSION = 1
REUSABLE_PIECE_ALGORITHM_VERSION = "reusable-piece-matrix-v2"
PROGRAM_BASELINE_SCHEMA_VERSION = 1
DEFAULT_POLICY_PATH = Path("platform/reusable-piece-policy.json")
DEFAULT_MATRIX_PATH = Path("coverage/reusable-piece-matrix.json.gz")
DEFAULT_CARD_INDEX_PATH = Path(
    "coverage/reusable-piece-card-index.json.gz"
)
DEFAULT_INTERACTIONS_PATH = Path(
    "coverage/reusable-piece-interactions.json.gz"
)
DEFAULT_DELTA_PATH = Path("coverage/reusable-piece-delta.json")
DEFAULT_COMPLEX_CARDS_PATH = Path("coverage/complex-card-composition.json")
DEFAULT_BASELINE_PATH = Path("coverage/program-baseline.json")

_SYSTEM_STATES = (
    "inventoried",
    "represented",
    "compositional",
    "foundation_trusted",
    "harvest_ready",
    "snapshot_complete",
)
_RUNTIME_RANK = {
    "absent": 0,
    "represented": 1,
    "compositional": 2,
    "foundation_trusted": 3,
    "snapshot_complete": 4,
}
_ASSURANCE_RANK = {
    "untested": 0,
    "tested": 1,
    "interaction_tested": 2,
    "trusted": 3,
}
_COMPILER_RANK = {
    "unrecognized": 0,
    "segmented": 1,
    "parsed": 2,
    "bound": 3,
    "typed": 4,
    "lowered": 5,
    "validated": 6,
}


def _hash(value: Any) -> str:
    return hashlib.sha256(stable_json(value).encode("utf-8")).hexdigest()


def _with_fingerprint(value: Mapping[str, Any]) -> dict[str, Any]:
    result = dict(value)
    result["fingerprint"] = _hash(result)
    return result


def _validate_fingerprint(value: Mapping[str, Any], *, label: str) -> None:
    supplied = value.get("fingerprint")
    payload = dict(value)
    payload.pop("fingerprint", None)
    if supplied != _hash(payload):
        raise ValueError(f"{label} fingerprint does not match")


def _slug(value: str) -> str:
    result = re.sub(r"[^a-z0-9._-]+", "-", value.casefold()).strip("-")
    return result[:160] or "unknown"


def _sorted_strings(values: Iterable[str]) -> list[str]:
    return sorted({str(value) for value in values if str(value)})


def load_json(path: str | Path) -> dict[str, Any]:
    from quorune.frontier_storage import unpack_frontier_document

    candidate = Path(path)
    try:
        if candidate.suffix == ".gz":
            raw = gzip.decompress(candidate.read_bytes()).decode("utf-8")
        else:
            raw = candidate.read_text(encoding="utf-8")
        value = unpack_frontier_document(json.loads(raw))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"Invalid JSON artifact: {candidate}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"JSON artifact must be an object: {candidate}")
    return value


def validate_reusable_piece_policy(policy: Mapping[str, Any]) -> None:
    required = {
        "schema_version",
        "ontology_version",
        "profile",
        "classes",
        "relation_types",
        "status_axes",
        "residual_family_classes",
        "capability_prefix_classes",
        "runtime_family_classes",
        "universal_system_classes",
        "high_risk_class_pairs",
        "ambient_high_risk_piece_pairs",
        "complex_card_weights",
        "complex_card_sentinels",
    }
    if set(policy) != required or policy.get("schema_version") != 1:
        raise ValueError("Reusable-piece policy fields or schema are invalid")
    classes = policy.get("classes")
    if not isinstance(classes, list) or not classes:
        raise ValueError("Reusable-piece policy classes are missing")
    class_ids = [row.get("id") for row in classes if isinstance(row, Mapping)]
    if (
        len(class_ids) != len(classes)
        or len(class_ids) != len(set(class_ids))
        or not all(isinstance(value, str) and value for value in class_ids)
    ):
        raise ValueError("Reusable-piece policy classes are invalid")
    class_set = set(class_ids)
    relation_types = policy.get("relation_types")
    if (
        not isinstance(relation_types, list)
        or relation_types != list(dict.fromkeys(relation_types))
        or not relation_types
    ):
        raise ValueError("Reusable-piece relation types are invalid")
    expected_axes = {
        "inventory",
        "compiler",
        "runtime",
        "assurance",
        "corpus",
        "interaction",
    }
    axes = policy.get("status_axes")
    if not isinstance(axes, Mapping) or set(axes) != expected_axes:
        raise ValueError("Reusable-piece status axes are invalid")
    for values in axes.values():
        if (
            not isinstance(values, list)
            or not values
            or values != list(dict.fromkeys(values))
        ):
            raise ValueError("Reusable-piece status values are invalid")
    for key in ("residual_family_classes", "universal_system_classes"):
        mapping = policy.get(key)
        if not isinstance(mapping, Mapping) or not mapping:
            raise ValueError(f"Reusable-piece {key} is invalid")
        referenced = (
            mapping.values()
            if key == "residual_family_classes"
            else (item for values in mapping.values() for item in values)
        )
        if not set(referenced) <= class_set:
            raise ValueError(f"Reusable-piece {key} references unknown classes")
    for key in ("capability_prefix_classes", "runtime_family_classes"):
        rows = policy.get(key)
        if not isinstance(rows, list):
            raise ValueError(f"Reusable-piece {key} is invalid")
        if any(
            not isinstance(row, Mapping)
            or set(row) != {"prefix", "class_id"}
            or not isinstance(row["prefix"], str)
            or row["class_id"] not in class_set
            for row in rows
        ):
            raise ValueError(f"Reusable-piece {key} entries are invalid")
    pairs = policy.get("high_risk_class_pairs")
    if not isinstance(pairs, list) or any(
        not isinstance(pair, list)
        or len(pair) != 2
        or not set(pair) <= class_set
        for pair in pairs
    ):
        raise ValueError("Reusable-piece high-risk pairs are invalid")
    ambient_pairs = policy.get("ambient_high_risk_piece_pairs")
    if (
        not isinstance(ambient_pairs, list)
        or ambient_pairs != sorted(ambient_pairs)
        or len(ambient_pairs) != len({tuple(pair) for pair in ambient_pairs})
        or any(
            not isinstance(pair, list)
            or len(pair) != 2
            or pair != sorted(set(pair))
            or not all(
                isinstance(piece_id, str)
                and _PIECE_ID.fullmatch(piece_id)
                for piece_id in pair
            )
            for pair in ambient_pairs
        )
    ):
        raise ValueError(
            "Reusable-piece ambient high-risk pairs are invalid"
        )
    sentinels = policy.get("complex_card_sentinels")
    if (
        not isinstance(sentinels, list)
        or not sentinels
        or len(sentinels) != len(set(sentinels))
        or any(not isinstance(value, str) or not value for value in sentinels)
    ):
        raise ValueError("Reusable-piece complex-card sentinels are invalid")


def load_reusable_piece_policy(
    root: str | Path = ".",
) -> dict[str, Any]:
    value = load_json(Path(root) / DEFAULT_POLICY_PATH)
    validate_reusable_piece_policy(value)
    return value


def _class_for_prefix(
    value: str,
    rows: Sequence[Mapping[str, Any]],
    *,
    fallback: str,
) -> str:
    for row in rows:
        if value.startswith(str(row["prefix"])):
            return str(row["class_id"])
    return fallback


def _relation_for_capability(capability_id: str) -> str:
    if ".prevention." in capability_id or capability_id.startswith(
        "protection."
    ):
        return "prevents"
    if ".replacement." in capability_id:
        return "replaces"
    if capability_id.startswith("continuous."):
        return "modifies"
    if capability_id.startswith("attachment."):
        return "links"
    if capability_id.startswith("target."):
        return "observes"
    if capability_id.startswith(("format.", "variant.")):
        return "requires_profile_support"
    if capability_id.startswith(
        ("damage.", "draw.", "life.", "counter.", "token.", "zone.")
    ):
        return "produces"
    return "intrinsically_consumes"


def _relation_for_family(family_id: str) -> str:
    base = family_id.split(":", 1)[0]
    return {
        "replacement": "replaces",
        "continuous_layer": "modifies",
        "static_clause": "modifies",
        "copy_or_face": "copies",
        "reference_binding": "links",
        "unsupported_profile": "requires_profile_support",
        "multiplayer": "requires_profile_support",
        "event_binding": "observes",
        "effect_clause": "produces",
        "activated_effect": "produces",
        "zone_transition": "produces",
    }.get(base, "intrinsically_consumes")


def _piece_id_for_family(family_id: str) -> str:
    base, detail = family_id.split(":", 1)
    if detail.startswith("unparsed-"):
        # The frontier retains precise Oracle-clause clusters. Those clusters
        # are useful harvest evidence, but each prose prefix is not a reusable
        # rule primitive. Inventory the shared missing grammar boundary while
        # preserving every raw family ID on that piece for drill-down.
        detail = "unparsed-clause-grammar"
    return f"residual.{_slug(base)}.{_slug(detail)}"


def _corpus_status(exact: int, residual: int) -> str:
    if exact and residual:
        return "mixed"
    if exact:
        return "exact_ability_use"
    if residual:
        return "residual_use"
    return "unused"


@dataclass
class _PieceAccumulator:
    piece_id: str
    class_id: str
    label: str
    source_kinds: set[str] = field(default_factory=set)
    source_ids: dict[str, set[str]] = field(
        default_factory=lambda: defaultdict(set)
    )
    rule_ids: set[str] = field(default_factory=set)
    implementation_components: set[str] = field(default_factory=set)
    test_ids: set[str] = field(default_factory=set)
    interaction_test_ids: set[str] = field(default_factory=set)
    blockers: set[str] = field(default_factory=set)
    relation_counts: Counter[str] = field(default_factory=Counter)
    cards: set[str] = field(default_factory=set)
    exact_cards: set[str] = field(default_factory=set)
    residual_cards: set[str] = field(default_factory=set)
    abilities: set[str] = field(default_factory=set)
    exact_abilities: set[str] = field(default_factory=set)
    residual_occurrences: set[str] = field(default_factory=set)
    sole_blocker_cards: set[str] = field(default_factory=set)
    one_additional_blocker_cards: set[str] = field(default_factory=set)
    two_additional_blocker_cards: set[str] = field(default_factory=set)
    override_cards: set[str] = field(default_factory=set)
    ruling_cards: set[str] = field(default_factory=set)
    compiler_status: str = "unrecognized"
    runtime_status: str = "absent"
    assurance_status: str = "untested"
    frontier_family_ids: set[str] = field(default_factory=set)

    def promote_compiler(self, status: str) -> None:
        if _COMPILER_RANK[status] > _COMPILER_RANK[self.compiler_status]:
            self.compiler_status = status

    def promote_runtime(self, status: str) -> None:
        if _RUNTIME_RANK[status] > _RUNTIME_RANK[self.runtime_status]:
            self.runtime_status = status

    def promote_assurance(self, status: str) -> None:
        if _ASSURANCE_RANK[status] > _ASSURANCE_RANK[self.assurance_status]:
            self.assurance_status = status


def _ensure_piece(
    pieces: dict[str, _PieceAccumulator],
    piece_id: str,
    *,
    class_id: str,
    label: str,
    source_kind: str,
    source_id: str,
) -> _PieceAccumulator:
    if not _PIECE_ID.fullmatch(piece_id):
        raise ValueError(f"Invalid reusable piece ID: {piece_id}")
    if source_kind == "frontier_family" and ":unparsed-" in source_id:
        label = (
            source_id.split(":", 1)[0]
            + ": unparsed Oracle-clause grammar"
        )
    existing = pieces.get(piece_id)
    if existing is None:
        existing = _PieceAccumulator(piece_id, class_id, label)
        pieces[piece_id] = existing
    elif existing.class_id != class_id:
        raise ValueError(
            f"Reusable piece {piece_id} has conflicting classes "
            f"{existing.class_id!r} and {class_id!r}"
        )
    existing.source_kinds.add(source_kind)
    existing.source_ids[source_kind].add(source_id)
    return existing


def _capability_statuses(row: Mapping[str, Any]) -> tuple[str, str]:
    status = str(row.get("status") or "unclassified")
    components = list(row.get("implementation_components") or ())
    runtime = "represented" if components else "absent"
    assurance = "untested"
    if status in {"tested", "interaction_tested", "trusted"}:
        assurance = "tested"
    if row.get("interaction_tests"):
        assurance = "interaction_tested"
    if status == "trusted":
        runtime = "compositional"
        assurance = "trusted"
    return runtime, assurance


def _mechanic_statuses(row: Mapping[str, Any]) -> tuple[str, str]:
    status = str(row.get("coverage_status") or "unclassified")
    if status == "trusted":
        return "compositional", "trusted"
    if status == "tested":
        return "represented", "tested"
    if status in {"partial", "implemented"}:
        return "represented", "untested"
    return "absent", "untested"


def _ability_piece_relations(
    ability: Mapping[str, Any],
    policy: Mapping[str, Any],
) -> list[tuple[str, str, str, str, str]]:
    """Return piece, relation, class, source kind, and source ID."""

    result: list[tuple[str, str, str, str, str]] = []
    kind = str(ability.get("kind") or "unknown")
    result.append(
        (
            f"compiler.node.{_slug(kind)}",
            "intrinsically_consumes",
            "compiler_cardprogram",
            "compiler_node_kind",
            kind,
        )
    )
    template_id = ability.get("template_id")
    if isinstance(template_id, str) and template_id:
        result.append(
            (
                f"compiler.template.{_slug(template_id)}",
                "derives",
                "compiler_cardprogram",
                "compiler_template",
                template_id,
            )
        )
    blockers = ability.get("blockers")
    blocker_map = blockers if isinstance(blockers, Mapping) else {}
    for capability_id in blocker_map.get("capability_ids", ()):
        capability_id = str(capability_id)
        class_id = _class_for_prefix(
            capability_id,
            policy["capability_prefix_classes"],
            fallback="actions_permissions",
        )
        result.append(
            (
                f"capability.{capability_id}",
                _relation_for_capability(capability_id),
                class_id,
                "capability",
                capability_id,
            )
        )
    for mechanic_id in blocker_map.get("mechanic_ids", ()):
        mechanic_id = str(mechanic_id)
        result.append(
            (
                f"mechanic.{_slug(mechanic_id)}",
                "intrinsically_consumes",
                "keyword_mechanics",
                "mechanic",
                mechanic_id,
            )
        )
    for family_id in blocker_map.get("canonical_family_ids", ()):
        family_id = str(family_id)
        base = family_id.split(":", 1)[0]
        class_id = str(
            policy["residual_family_classes"].get(
                base, "compiler_cardprogram"
            )
        )
        result.append(
            (
                _piece_id_for_family(family_id),
                _relation_for_family(family_id),
                class_id,
                "frontier_family",
                family_id,
            )
        )
    return result


def _build_card_relations(
    frontier: Mapping[str, Any],
    policy: Mapping[str, Any],
    pieces: dict[str, _PieceAccumulator],
) -> list[dict[str, Any]]:
    cards: list[dict[str, Any]] = []
    relation_types = set(policy["relation_types"])
    unclassified = 0
    for card in frontier.get("cards", ()):
        oracle_id = str(card["oracle_id"])
        card_relations: dict[str, dict[str, set[str]]] = defaultdict(
            lambda: {"relations": set(), "ability_ids": set()}
        )
        blocker_piece_ids: set[str] = set()
        minimum_families = set(card.get("minimum_known_blocker_set") or ())
        for ability in card.get("abilities", ()):
            ability_id = str(ability["ability_id"])
            status = str(ability.get("status") or "unresolved")
            relations = _ability_piece_relations(ability, policy)
            if not relations:
                unclassified += 1
                continue
            for piece_id, relation, class_id, source_kind, source_id in relations:
                if relation not in relation_types:
                    raise ValueError(f"Unknown reusable-piece relation: {relation}")
                piece = _ensure_piece(
                    pieces,
                    piece_id,
                    class_id=class_id,
                    label=source_id,
                    source_kind=source_kind,
                    source_id=source_id,
                )
                piece.cards.add(oracle_id)
                piece.abilities.add(ability_id)
                piece.relation_counts[relation] += 1
                if status == "exact":
                    piece.exact_cards.add(oracle_id)
                    piece.exact_abilities.add(ability_id)
                    piece.promote_compiler("validated")
                    if source_kind in {"compiler_node_kind", "compiler_template"}:
                        piece.promote_runtime("represented")
                        piece.promote_assurance("tested")
                else:
                    piece.residual_cards.add(oracle_id)
                    piece.promote_compiler(
                        "lowered"
                        if status == "lowerable_untrusted"
                        else "segmented"
                    )
                if source_kind == "frontier_family":
                    piece.frontier_family_ids.add(source_id)
                    blocker_piece_ids.add(piece_id)
                card_relations[piece_id]["relations"].add(relation)
                card_relations[piece_id]["ability_ids"].add(ability_id)
            for residual in ability.get("residuals", ()):
                residual_id = str(residual["residual_id"])
                for family_id in residual.get("family_ids", ()):
                    piece_id = _piece_id_for_family(str(family_id))
                    if piece_id in pieces:
                        pieces[piece_id].residual_occurrences.add(
                            f"{oracle_id}:{residual_id}"
                        )
        for family_id in minimum_families:
            piece_id = _piece_id_for_family(str(family_id))
            piece = pieces.get(piece_id)
            if piece is None:
                continue
            additional = len(minimum_families) - 1
            if additional == 0:
                piece.sole_blocker_cards.add(oracle_id)
            elif additional == 1:
                piece.one_additional_blocker_cards.add(oracle_id)
            elif additional == 2:
                piece.two_additional_blocker_cards.add(oracle_id)
        if card.get("card_program_trust_basis") in {
            "legacy_reviewed",
            "mixed",
        }:
            for piece_id in card_relations:
                pieces[piece_id].override_cards.add(oracle_id)
        cards.append(
            {
                "oracle_id": oracle_id,
                "card_name": str(card["card_name"]),
                "oracle_ir_status": str(card["oracle_ir_status"]),
                "card_program_status": str(card["card_program_status"]),
                "card_program_trust_basis": card.get(
                    "card_program_trust_basis"
                ),
                "material_ability_count": int(card["material_ability_count"]),
                "exact_ability_count": int(card["exact_ability_count"]),
                "minimum_blocker_piece_ids": sorted(blocker_piece_ids),
                "pieces": [
                    {
                        "piece_id": piece_id,
                        "relation_types": sorted(values["relations"]),
                        "ability_ids": sorted(values["ability_ids"]),
                    }
                    for piece_id, values in sorted(card_relations.items())
                ],
            }
        )
    if unclassified:
        raise ValueError(
            f"Reusable-piece inventory left {unclassified} abilities unclassified"
        )
    return cards


def _attach_capability_inventory(
    capabilities: Mapping[str, Any],
    policy: Mapping[str, Any],
    pieces: dict[str, _PieceAccumulator],
) -> None:
    for row in capabilities.get("capabilities", ()):
        capability_id = str(row["id"])
        class_id = _class_for_prefix(
            capability_id,
            policy["capability_prefix_classes"],
            fallback="actions_permissions",
        )
        piece = _ensure_piece(
            pieces,
            f"capability.{capability_id}",
            class_id=class_id,
            label=capability_id,
            source_kind="capability",
            source_id=capability_id,
        )
        piece.rule_ids.update(str(value) for value in row.get("official_rules", ()))
        piece.implementation_components.update(
            str(value) for value in row.get("implementation_components", ())
        )
        for evidence_key in (
            "positive_tests",
            "negative_tests",
            "multiplayer_tests",
            "privacy_tests",
            "replay_tests",
        ):
            piece.test_ids.update(str(value) for value in row.get(evidence_key, ()))
        piece.interaction_test_ids.update(
            str(value) for value in row.get("interaction_tests", ())
        )
        piece.test_ids.update(piece.interaction_test_ids)
        piece.blockers.update(str(value) for value in row.get("blockers", ()))
        runtime, assurance = _capability_statuses(row)
        piece.promote_runtime(runtime)
        piece.promote_assurance(assurance)
        if piece.cards:
            piece.promote_compiler(
                "validated" if piece.exact_abilities else "typed"
            )


def _attach_mechanic_inventory(
    mechanics: Mapping[str, Any],
    pieces: dict[str, _PieceAccumulator],
) -> None:
    for row in mechanics.get("mechanics", ()):
        mechanic_id = str(row["mechanic_id"])
        piece = _ensure_piece(
            pieces,
            f"mechanic.{_slug(mechanic_id)}",
            class_id="keyword_mechanics",
            label=str(row.get("official_name") or mechanic_id),
            source_kind="mechanic",
            source_id=mechanic_id,
        )
        piece.label = str(row.get("official_name") or mechanic_id)
        piece.rule_ids.update(str(value) for value in row.get("rule_references", ()))
        component = row.get("implementation_component")
        if isinstance(component, str) and component:
            piece.implementation_components.add(component)
        mechanic_tests = {
            str(value) for value in row.get("test_ids", ())
        }
        piece.test_ids.update(mechanic_tests)
        # A test intentionally cited by both a mechanic contract and another
        # reusable piece is pairwise interaction evidence, not merely two
        # unrelated unit-test citations. Coverage still requires the exact
        # same stable test ID on both sides of an applicable card-level pair.
        piece.interaction_test_ids.update(mechanic_tests)
        runtime, assurance = _mechanic_statuses(row)
        piece.promote_runtime(runtime)
        piece.promote_assurance(assurance)
        if piece.cards:
            piece.promote_compiler(
                "validated" if piece.exact_abilities else "parsed"
            )


def _attach_runtime_inventory(
    runtime_status: Mapping[str, Any],
    policy: Mapping[str, Any],
    pieces: dict[str, _PieceAccumulator],
) -> None:
    for collection, source_kind in (
        ("semantic_handlers", "runtime_handler"),
        ("runtime_components", "runtime_component"),
    ):
        for row in runtime_status.get(collection, ()):
            handler_id = str(row.get("handler_id") or "")
            operation = str(row.get("operation") or "")
            family = str(row.get("family") or "")
            source_id = handler_id or operation or family
            if not source_id:
                raise ValueError("Runtime inventory row has no stable identity")
            class_id = _class_for_prefix(
                family,
                policy["runtime_family_classes"],
                fallback="one_shot_effects",
            )
            semantic_id = operation or family or handler_id
            piece = _ensure_piece(
                pieces,
                f"runtime.operation.{_slug(semantic_id)}",
                class_id=class_id,
                label=semantic_id,
                source_kind=source_kind,
                source_id=source_id,
            )
            piece.rule_ids.update(
                str(value) for value in row.get("rule_references", ())
            )
            piece.implementation_components.add(handler_id or source_id)
            closure = row.get("capability_closure")
            trusted = bool(
                isinstance(closure, Mapping) and closure.get("trusted")
            )
            piece.promote_runtime(
                "compositional" if trusted else "represented"
            )
            piece.promote_assurance("tested" if trusted else "untested")
            piece.promote_compiler("lowered")
            for capability_id in row.get("capability_dependencies", ()):
                capability_id = str(capability_id)
                capability_piece = pieces.get(f"capability.{capability_id}")
                if capability_piece is None:
                    class_for_capability = _class_for_prefix(
                        capability_id,
                        policy["capability_prefix_classes"],
                        fallback="actions_permissions",
                    )
                    capability_piece = _ensure_piece(
                        pieces,
                        f"capability.{capability_id}",
                        class_id=class_for_capability,
                        label=capability_id,
                        source_kind="capability",
                        source_id=capability_id,
                    )
                capability_piece.source_ids[source_kind].add(source_id)
                capability_piece.implementation_components.add(
                    handler_id or source_id
                )


def _attach_frontier_candidates(
    frontier: Mapping[str, Any],
    policy: Mapping[str, Any],
    pieces: dict[str, _PieceAccumulator],
) -> dict[str, Mapping[str, Any]]:
    candidates: dict[str, Mapping[str, Any]] = {}
    for row in frontier.get("family_candidates", ()):
        family_id = str(row["family_id"])
        candidates[family_id] = row
        piece_id = _piece_id_for_family(family_id)
        base = family_id.split(":", 1)[0]
        piece = _ensure_piece(
            pieces,
            piece_id,
            class_id=str(
                policy["residual_family_classes"].get(
                    base, "compiler_cardprogram"
                )
            ),
            label=family_id,
            source_kind="frontier_family",
            source_id=family_id,
        )
        piece.frontier_family_ids.add(family_id)
        piece.blockers.update(
            str(value) for value in row.get("prerequisite_capabilities", ())
        )
    return candidates


def _interaction_status(counts: Mapping[str, int]) -> str:
    if not counts.get("applicable"):
        return "unknown"
    if (
        counts.get("high_risk_applicable")
        and counts.get("high_risk_applicable")
        == counts.get("high_risk_covered")
    ):
        return "high_risk_covered"
    if counts.get("covered"):
        return "pairwise_covered"
    return "unknown"


def _piece_record(
    piece: _PieceAccumulator,
    *,
    interaction_counts: Mapping[str, int],
    candidate_map: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    exact_ability_count = len(piece.exact_abilities)
    residual_occurrences = len(piece.residual_occurrences)
    status = {
        "inventory": "inventoried",
        "compiler": piece.compiler_status,
        "runtime": piece.runtime_status,
        "assurance": piece.assurance_status,
        "corpus": _corpus_status(exact_ability_count, residual_occurrences),
        "interaction": _interaction_status(interaction_counts),
    }
    frontier_rows = [
        candidate_map[family_id]
        for family_id in sorted(piece.frontier_family_ids)
        if family_id in candidate_map
    ]
    frontier_summary = {
        "family_ids": sorted(piece.frontier_family_ids),
        "material_occurrences": sum(
            int(row.get("occurrences") or 0) for row in frontier_rows
        ),
        "affected_cards": len(piece.residual_cards),
        "sole_blocker_cards": len(piece.sole_blocker_cards),
        "one_additional_blocker_cards": len(
            piece.one_additional_blocker_cards
        ),
        "two_additional_blocker_cards": len(
            piece.two_additional_blocker_cards
        ),
        "expected_exact_card_gain": sum(
            int(row.get("expected_exact_card_gain") or 0)
            for row in frontier_rows
        ),
        "expected_exact_ability_gain": sum(
            int(row.get("expected_exact_ability_gain") or 0)
            for row in frontier_rows
        ),
        "interaction_risks": sorted(
            {
                str(row.get("interaction_risk"))
                for row in frontier_rows
                if row.get("interaction_risk")
            }
        ),
    }
    record = {
        "piece_id": piece.piece_id,
        "class_id": piece.class_id,
        "label": piece.label,
        "source_kinds": sorted(piece.source_kinds),
        "source_ids": {
            key: sorted(values)
            for key, values in sorted(piece.source_ids.items())
        },
        "rule_ids": sorted(piece.rule_ids),
        "implementation_components": sorted(piece.implementation_components),
        "test_ids": sorted(piece.test_ids),
        "interaction_test_ids": sorted(piece.interaction_test_ids),
        "blockers": sorted(piece.blockers),
        "status": status,
        "relation_counts": dict(sorted(piece.relation_counts.items())),
        "counts": {
            "distinct_oracle_ids": len(piece.cards),
            "material_abilities": len(piece.abilities),
            "exact_cards": len(piece.exact_cards),
            "exact_abilities": exact_ability_count,
            "residual_cards": len(piece.residual_cards),
            "residual_occurrences": residual_occurrences,
            "override_cards": len(piece.override_cards),
            "cards_with_official_rulings": len(piece.ruling_cards),
            "applicable_interaction_pairs": int(
                interaction_counts.get("applicable") or 0
            ),
            "covered_interaction_pairs": int(
                interaction_counts.get("covered") or 0
            ),
            "applicable_high_risk_interactions": int(
                interaction_counts.get("high_risk_applicable") or 0
            ),
            "covered_high_risk_interactions": int(
                interaction_counts.get("high_risk_covered") or 0
            ),
        },
        "frontier": frontier_summary,
    }
    record["fingerprint"] = _hash(record)
    return record


def _build_rule_index(
    rules_index: Mapping[str, Any],
    piece_rows: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    by_rule: dict[str, set[str]] = defaultdict(set)
    for piece in piece_rows:
        for rule_id in piece.get("rule_ids", ()):
            by_rule[str(rule_id)].add(str(piece["piece_id"]))
    rows = []
    for rule in rules_index.get("rules", ()):
        rule_id = str(rule["rule_id"])
        piece_ids = sorted(by_rule.get(rule_id, set()))
        rows.append(
            {
                "rule_id": rule_id,
                "piece_ids": piece_ids,
                "status": "mapped" if piece_ids else "unmapped",
            }
        )
    mapped = sum(row["status"] == "mapped" for row in rows)
    return {
        "rules_total": len(rows),
        "mapped_rules": mapped,
        "unmapped_rules": len(rows) - mapped,
        "rules": rows,
    }


def _system_status(
    class_ids: Sequence[str],
    piece_rows: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    relevant = [
        row for row in piece_rows if row["class_id"] in set(class_ids)
    ]
    if not relevant:
        return {
            "status": "inventoried",
            "piece_count": 0,
            "blocking_piece_ids": [],
        }
    runtime_values = [
        _RUNTIME_RANK[str(row["status"]["runtime"])] for row in relevant
    ]
    assurance_values = [
        _ASSURANCE_RANK[str(row["status"]["assurance"])]
        for row in relevant
    ]
    if min(runtime_values) >= _RUNTIME_RANK["foundation_trusted"] and min(
        assurance_values
    ) >= _ASSURANCE_RANK["trusted"]:
        status = "foundation_trusted"
    elif min(runtime_values) >= _RUNTIME_RANK["compositional"]:
        status = "compositional"
    elif min(runtime_values) >= _RUNTIME_RANK["represented"]:
        status = "represented"
    else:
        status = "inventoried"
    blocking = [
        str(row["piece_id"])
        for row in relevant
        if row["status"]["runtime"] == "absent"
        or row["status"]["assurance"] == "untested"
    ]
    return {
        "status": status,
        "piece_count": len(relevant),
        "blocking_piece_count": len(blocking),
        "blocking_piece_ids": blocking[:100],
    }


def _status_counts(
    piece_rows: Sequence[Mapping[str, Any]],
) -> dict[str, dict[str, int]]:
    axes: dict[str, Counter[str]] = defaultdict(Counter)
    for row in piece_rows:
        for axis, status in row["status"].items():
            axes[str(axis)][str(status)] += 1
    return {
        axis: dict(sorted(counts.items()))
        for axis, counts in sorted(axes.items())
    }


def _matrix_summary(
    piece_rows: Sequence[Mapping[str, Any]],
    cards: Sequence[Mapping[str, Any]],
    interactions: Sequence[Mapping[str, Any]],
    rule_index: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "piece_count": len(piece_rows),
        "class_counts": dict(
            sorted(Counter(str(row["class_id"]) for row in piece_rows).items())
        ),
        "status_counts": _status_counts(piece_rows),
        "cards_indexed": len(cards),
        "material_abilities_classified": sum(
            int(card["material_ability_count"]) for card in cards
        ),
        "unclassified_material_spans": 0,
        "rule_count": int(rule_index["rules_total"]),
        "mapped_rule_count": int(rule_index["mapped_rules"]),
        "unmapped_rule_count": int(rule_index["unmapped_rules"]),
        "applicable_interaction_pairs": len(interactions),
        "covered_interaction_pairs": sum(
            bool(row["covered"]) for row in interactions
        ),
        "applicable_high_risk_interactions": sum(
            bool(row["high_risk"]) for row in interactions
        ),
        "covered_high_risk_interactions": sum(
            bool(row["high_risk"] and row["covered"])
            for row in interactions
        ),
    }


def _build_complex_card_benchmark(
    cards: Sequence[Mapping[str, Any]],
    piece_rows: Sequence[Mapping[str, Any]],
    policy: Mapping[str, Any],
    *,
    ruling_counts: Mapping[str, int],
    matrix_fingerprint: str,
) -> dict[str, Any]:
    piece_map = {str(row["piece_id"]): row for row in piece_rows}
    class_to_systems: dict[str, set[str]] = defaultdict(set)
    for system_id, class_ids in policy["universal_system_classes"].items():
        for class_id in class_ids:
            class_to_systems[str(class_id)].add(str(system_id))
    weights = {key: int(value) for key, value in policy["complex_card_weights"].items()}
    ranked = []
    sentinels = {
        str(value).casefold() for value in policy["complex_card_sentinels"]
    }
    for card in cards:
        piece_ids = [str(row["piece_id"]) for row in card.get("pieces", ())]
        classes = {
            str(piece_map[piece_id]["class_id"])
            for piece_id in piece_ids
            if piece_id in piece_map
        }
        systems = {
            system_id
            for class_id in classes
            for system_id in class_to_systems.get(class_id, ())
        }
        flags = {
            "linked_reference": "references" in classes,
            "delayed_or_trigger": "triggers" in classes,
            "replacement": "replacement_prevention" in classes,
            "continuous_layer": "continuous_effects" in classes,
            "copy_or_face": "card_forms" in classes,
            "multiplayer": bool(
                {"players_format", "multiplayer_commander"} & classes
            ),
        }
        ruling_count = int(ruling_counts.get(str(card["oracle_id"]), 0))
        score = (
            len(piece_ids) * weights["distinct_piece"]
            + len(systems) * weights["universal_system"]
            + int(card["material_ability_count"]) * weights["material_ability"]
            + len(card["minimum_blocker_piece_ids"]) * weights["blocker"]
            + sum(
                int(enabled) * weights[key]
                for key, enabled in flags.items()
            )
            + ruling_count * weights["official_ruling"]
        )
        ranked.append(
            {
                "oracle_id": str(card["oracle_id"]),
                "card_name": str(card["card_name"]),
                "score": score,
                "piece_count": len(piece_ids),
                "universal_system_count": len(systems),
                "material_ability_count": int(card["material_ability_count"]),
                "minimum_blocker_count": len(
                    card["minimum_blocker_piece_ids"]
                ),
                "official_ruling_count": ruling_count,
                "flags": flags,
                "piece_ids": sorted(piece_ids),
                "universal_system_ids": sorted(systems),
                "composition_status": (
                    "exact"
                    if card["card_program_status"] == "trusted"
                    and not card["minimum_blocker_piece_ids"]
                    else "blocked"
                ),
                "sentinel": str(card["card_name"]).casefold() in sentinels,
            }
        )
    ranked.sort(
        key=lambda row: (-row["score"], row["card_name"], row["oracle_id"])
    )
    selected = ranked[:50]
    selected_ids = {row["oracle_id"] for row in selected}
    selected.extend(
        row
        for row in ranked
        if row["sentinel"] and row["oracle_id"] not in selected_ids
    )
    return _with_fingerprint(
        {
            "schema_version": REUSABLE_PIECE_SCHEMA_VERSION,
            "algorithm_version": "complex-card-composition-v1",
            "profile": policy["profile"],
            "matrix_fingerprint": matrix_fingerprint,
            "ranking_weights": weights,
            "cards_considered": len(cards),
            "benchmark_size": len(selected),
            "cards": selected,
            "content_boundary": (
                "Contains public card names, Oracle IDs, generated piece IDs, "
                "and aggregate counts only; no Oracle prose or hidden game data."
            ),
        }
    )


def build_program_baseline(
    matrix: Mapping[str, Any],
    interactions: Mapping[str, Any],
    *,
    oracle_coverage: Mapping[str, Any],
    program_coverage: Mapping[str, Any],
    capability_registry: Mapping[str, Any],
    architecture_audit: Mapping[str, Any],
    platform_status: Mapping[str, Any],
) -> dict[str, Any]:
    snapshot = matrix["snapshot"]
    generated = platform_status.get("generated") or {}
    piece_status_by_id = {
        str(row["piece_id"]): dict(row["status"])
        for row in matrix["pieces"]
    }
    payload = {
        "schema_version": PROGRAM_BASELINE_SCHEMA_VERSION,
        "baseline_kind": "accelerator_adoption",
        "source_tree_fingerprint": generated.get(
            "evaluated_input_fingerprint",
            generated.get("evaluated_source_tree_hash"),
        ),
        "source_tree_fingerprint_algorithm": generated.get(
            "input_fingerprint_algorithm",
            generated.get("source_tree_fingerprint_algorithm"),
        ),
        "rules_fingerprint": snapshot["comprehensive_rules"]["sha256"],
        "oracle_fingerprint": snapshot["oracle"]["sha256"],
        "rulings_fingerprint": snapshot["rulings"]["sha256"],
        "compiler_version": oracle_coverage["compiler_version"],
        "card_program_version": program_coverage[
            "card_program_schema_version"
        ],
        "capability_registry_version": capability_registry[
            "registry_version"
        ],
        "capability_registry_fingerprint": matrix["input_fingerprints"][
            "capability_registry"
        ],
        "reusable_piece_schema_version": REUSABLE_PIECE_SCHEMA_VERSION,
        "reusable_piece_ontology_version": matrix["ontology_version"],
        "matrix_fingerprint": matrix["fingerprint"],
        "counts": {
            "generic_exact_commander_cards": int(
                oracle_coverage["status_counts"].get("exact", 0)
            ),
            "capability_closed_commander_card_programs": int(
                program_coverage["trust_basis_counts"].get(
                    "capability_closed", 0
                )
            ),
            "material_residuals": int(program_coverage["material_residuals"]),
            "hard_construction_failures": len(
                program_coverage.get("failures", ())
            ),
            "piece_status_counts": matrix["summary"]["status_counts"],
            "interaction_coverage": interactions["summary"],
            "architecture_debt": _architecture_metrics(architecture_audit),
        },
        "piece_status_by_id": piece_status_by_id,
    }
    payload["baseline_id"] = (
        "accelerator-"
        + str(payload["oracle_fingerprint"])[:12]
        + "-"
        + str(payload["matrix_fingerprint"])[:12]
    )
    return _with_fingerprint(payload)


def validate_program_baseline(value: Mapping[str, Any]) -> None:
    if value.get("schema_version") != PROGRAM_BASELINE_SCHEMA_VERSION:
        raise ValueError("Unsupported program baseline schema_version")
    required = {
        "baseline_kind",
        "source_tree_fingerprint",
        "source_tree_fingerprint_algorithm",
        "rules_fingerprint",
        "oracle_fingerprint",
        "rulings_fingerprint",
        "compiler_version",
        "card_program_version",
        "capability_registry_version",
        "capability_registry_fingerprint",
        "reusable_piece_schema_version",
        "reusable_piece_ontology_version",
        "matrix_fingerprint",
        "counts",
        "piece_status_by_id",
        "baseline_id",
        "fingerprint",
    }
    if set(value) != required | {"schema_version"}:
        raise ValueError("Program baseline fields are invalid")
    _validate_fingerprint(value, label="Program baseline")


def _axis_change(
    axis: str,
    before: str,
    after: str,
    policy: Mapping[str, Any],
) -> int:
    values = list(policy["status_axes"][axis])
    return values.index(after) - values.index(before)


def build_reusable_piece_delta(
    matrix: Mapping[str, Any],
    interactions: Mapping[str, Any],
    baseline: Mapping[str, Any],
    *,
    oracle_coverage: Mapping[str, Any],
    program_coverage: Mapping[str, Any],
    architecture_audit: Mapping[str, Any],
    policy: Mapping[str, Any],
) -> dict[str, Any]:
    validate_program_baseline(baseline)
    before_pieces = baseline["piece_status_by_id"]
    after_pieces = {
        str(row["piece_id"]): row["status"] for row in matrix["pieces"]
    }
    added = sorted(set(after_pieces) - set(before_pieces))
    removed = sorted(set(before_pieces) - set(after_pieces))
    promoted: list[dict[str, Any]] = []
    demoted: list[dict[str, Any]] = []
    for piece_id in sorted(set(before_pieces) & set(after_pieces)):
        for axis in policy["status_axes"]:
            before = str(before_pieces[piece_id][axis])
            after = str(after_pieces[piece_id][axis])
            change = _axis_change(axis, before, after, policy)
            if change:
                row = {
                    "piece_id": piece_id,
                    "axis": axis,
                    "before": before,
                    "after": after,
                }
                (promoted if change > 0 else demoted).append(row)
    before_counts = baseline["counts"]
    current_architecture = _architecture_metrics(architecture_audit)
    metrics = {
        "generic_exact_commander_cards": int(
            oracle_coverage["status_counts"].get("exact", 0)
        ),
        "capability_closed_commander_card_programs": int(
            program_coverage["trust_basis_counts"].get(
                "capability_closed", 0
            )
        ),
        "material_residuals": int(program_coverage["material_residuals"]),
        "hard_construction_failures": len(program_coverage.get("failures", ())),
    }
    deltas = {
        key: value - int(before_counts[key]) for key, value in metrics.items()
    }
    architecture_deltas = {
        key: value - int(before_counts["architecture_debt"].get(key, 0))
        for key, value in current_architecture.items()
    }
    return _with_fingerprint(
        {
            "schema_version": REUSABLE_PIECE_SCHEMA_VERSION,
            "algorithm_version": "reusable-piece-delta-v1",
            "baseline_id": baseline["baseline_id"],
            "baseline_fingerprint": baseline["fingerprint"],
            "current_matrix_fingerprint": matrix["fingerprint"],
            "metrics": metrics,
            "deltas": deltas,
            "architecture_deltas": architecture_deltas,
            "piece_changes": {
                "added": added,
                "removed": removed,
                "promoted": promoted,
                "demoted": demoted,
            },
            "interaction_coverage": interactions["summary"],
        }
    )


def _pinned_snapshot(platform_status: Mapping[str, Any]) -> dict[str, Any]:
    platform_snapshots = platform_status.get("snapshots")
    if not isinstance(platform_snapshots, Mapping) or not {
        "comprehensive_rules",
        "oracle",
        "rulings",
    } <= set(platform_snapshots):
        raise ValueError("Platform status is missing pinned source snapshots")
    snapshot = {
        key: dict(platform_snapshots[key])
        for key in ("comprehensive_rules", "oracle", "rulings")
    }
    for key, row in snapshot.items():
        if not re.fullmatch(r"[0-9a-f]{64}", str(row.get("sha256") or "")):
            raise ValueError(f"Pinned {key} snapshot has no valid SHA-256")
    return snapshot


def _build_matrix(
    *,
    cards: list[dict[str, Any]],
    piece_rows: list[dict[str, Any]],
    interaction_rows: list[dict[str, Any]],
    rule_index: Mapping[str, Any],
    snapshot: Mapping[str, Any],
    frontier: Mapping[str, Any],
    mechanics_registry: Mapping[str, Any],
    runtime_status: Mapping[str, Any],
    rules_index: Mapping[str, Any],
    oracle_coverage: Mapping[str, Any],
    program_coverage: Mapping[str, Any],
    policy: Mapping[str, Any],
    interaction_evidence: Mapping[str, Any],
) -> dict[str, Any]:
    return _with_fingerprint(
        {
            "schema_version": REUSABLE_PIECE_SCHEMA_VERSION,
            "algorithm_version": REUSABLE_PIECE_ALGORITHM_VERSION,
            "ontology_version": policy["ontology_version"],
            "profile": policy["profile"],
            "commander_legal_only": True,
            "classification_boundary": (
                "Current Oracle IR material ability and residual spans plus "
                "all registered capabilities, mechanics, handlers, components, "
                "and pinned rule references. This inventories current source "
                "relations without claiming universal runtime completion."
            ),
            "ruling_evidence_boundary": (
                "Counts official ruling presence by Oracle ID. Ruling prose "
                "is not yet behaviorally classified, so these counts are "
                "composition evidence rather than coverage claims."
            ),
            "snapshot": dict(snapshot),
            "input_fingerprints": {
                "frontier": frontier["fingerprint"],
                "capability_registry": runtime_status[
                    "capability_registry_fingerprint"
                ],
                "capability_evidence": runtime_status[
                    "capability_evidence_fingerprint"
                ],
                "mechanics_registry": _hash(mechanics_registry),
                "semantic_handler_registry": runtime_status[
                    "semantic_handler_registry_fingerprint"
                ],
                "runtime_component_registry": runtime_status[
                    "runtime_component_registry_fingerprint"
                ],
                "rules_index": _hash(rules_index),
                "oracle_coverage": _hash(oracle_coverage),
                "program_coverage": _hash(program_coverage),
                "policy": _hash(policy),
                "interaction_evidence": _hash(interaction_evidence),
            },
            "classes": list(policy["classes"]),
            "relation_types": list(policy["relation_types"]),
            "status_axes": dict(policy["status_axes"]),
            "summary": _matrix_summary(
                piece_rows, cards, interaction_rows, rule_index
            ),
            "universal_systems": {
                system_id: _system_status(class_ids, piece_rows)
                for system_id, class_ids in sorted(
                    policy["universal_system_classes"].items()
                )
            },
            "rule_index": dict(rule_index),
            "pieces": piece_rows,
            "complete_snapshot_claimed": False,
        }
    )


def _build_indexes(
    *,
    cards: list[dict[str, Any]],
    interaction_rows: list[dict[str, Any]],
    matrix: Mapping[str, Any],
    policy: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    card_index = _with_fingerprint(
        {
            "schema_version": REUSABLE_PIECE_SCHEMA_VERSION,
            "algorithm_version": "reusable-piece-card-index-v1",
            "profile": policy["profile"],
            "matrix_fingerprint": matrix["fingerprint"],
            "cards_considered": len(cards),
            "unclassified_material_spans": 0,
            "cards": cards,
        }
    )
    interactions = _with_fingerprint(
        {
            "schema_version": REUSABLE_PIECE_SCHEMA_VERSION,
            "algorithm_version": "reusable-piece-interactions-v4",
            "profile": policy["profile"],
            "matrix_fingerprint": matrix["fingerprint"],
            "summary": {
                "applicable_piece_pairs": len(interaction_rows),
                "covered_piece_pairs": sum(
                    bool(row["covered"]) for row in interaction_rows
                ),
                "covered_runtime_composition_pairs": sum(
                    "runtime_composition"
                    in row["evidence_assurance_kinds"]
                    for row in interaction_rows
                ),
                "covered_fail_closed_runtime_admission_pairs": sum(
                    "fail_closed_runtime_admission"
                    in row["evidence_assurance_kinds"]
                    for row in interaction_rows
                ),
                "applicable_high_risk_pairs": sum(
                    bool(row["high_risk"]) for row in interaction_rows
                ),
                "covered_high_risk_pairs": sum(
                    bool(row["high_risk"] and row["covered"])
                    for row in interaction_rows
                ),
                "covered_high_risk_runtime_composition_pairs": sum(
                    bool(
                        row["high_risk"]
                        and "runtime_composition"
                        in row["evidence_assurance_kinds"]
                    )
                    for row in interaction_rows
                ),
                "covered_high_risk_fail_closed_runtime_admission_pairs": sum(
                    bool(
                        row["high_risk"]
                        and "fail_closed_runtime_admission"
                        in row["evidence_assurance_kinds"]
                    )
                    for row in interaction_rows
                ),
            },
            "pairs": interaction_rows,
            "complete_snapshot_claimed": False,
        }
    )
    return card_index, interactions


def build_reusable_piece_artifacts(
    *,
    frontier: Mapping[str, Any],
    capability_registry: Mapping[str, Any],
    mechanics_registry: Mapping[str, Any],
    runtime_status: Mapping[str, Any],
    rules_index: Mapping[str, Any],
    oracle_coverage: Mapping[str, Any],
    program_coverage: Mapping[str, Any],
    architecture_audit: Mapping[str, Any],
    platform_status: Mapping[str, Any],
    policy: Mapping[str, Any],
    interaction_evidence: Mapping[str, Any],
    known_test_ids: Iterable[str],
    baseline: Mapping[str, Any] | None = None,
    ruling_counts: Mapping[str, int] | None = None,
) -> dict[str, dict[str, Any]]:
    validate_reusable_piece_policy(policy)
    if frontier.get("profile") != policy["profile"]:
        raise ValueError("Reusable-piece policy and frontier profiles differ")
    pieces: dict[str, _PieceAccumulator] = {}
    cards = _build_card_relations(frontier, policy, pieces)
    _attach_capability_inventory(capability_registry, policy, pieces)
    _attach_mechanic_inventory(mechanics_registry, pieces)
    _attach_runtime_inventory(runtime_status, policy, pieces)
    candidate_map = _attach_frontier_candidates(frontier, policy, pieces)
    ruling_counts = ruling_counts or {}
    for card in cards:
        oracle_id = str(card["oracle_id"])
        if int(ruling_counts.get(oracle_id, 0)) <= 0:
            continue
        for relation in card["pieces"]:
            pieces[str(relation["piece_id"])].ruling_cards.add(oracle_id)
    interaction_rows, per_piece_interactions = _build_interactions(
        cards,
        pieces,
        policy,
        interaction_evidence,
        known_test_ids=known_test_ids,
    )
    piece_rows = [
        _piece_record(
            pieces[piece_id],
            interaction_counts=per_piece_interactions.get(piece_id, {}),
            candidate_map=candidate_map,
        )
        for piece_id in sorted(pieces)
    ]
    rule_index = _build_rule_index(rules_index, piece_rows)
    snapshot = _pinned_snapshot(platform_status)
    matrix = _build_matrix(
        cards=cards,
        piece_rows=piece_rows,
        interaction_rows=interaction_rows,
        rule_index=rule_index,
        snapshot=snapshot,
        frontier=frontier,
        mechanics_registry=mechanics_registry,
        runtime_status=runtime_status,
        rules_index=rules_index,
        oracle_coverage=oracle_coverage,
        program_coverage=program_coverage,
        policy=policy,
        interaction_evidence=interaction_evidence,
    )
    card_index, interactions = _build_indexes(
        cards=cards,
        interaction_rows=interaction_rows,
        matrix=matrix,
        policy=policy,
    )
    complex_cards = _build_complex_card_benchmark(
        cards,
        piece_rows,
        policy,
        ruling_counts=ruling_counts,
        matrix_fingerprint=matrix["fingerprint"],
    )
    effective_baseline = (
        dict(baseline)
        if baseline is not None
        else build_program_baseline(
            matrix,
            interactions,
            oracle_coverage=oracle_coverage,
            program_coverage=program_coverage,
            capability_registry=capability_registry,
            architecture_audit=architecture_audit,
            platform_status=platform_status,
        )
    )
    validate_program_baseline(effective_baseline)
    for key, current in (
        ("rules_fingerprint", snapshot["comprehensive_rules"]["sha256"]),
        ("oracle_fingerprint", snapshot["oracle"]["sha256"]),
        ("rulings_fingerprint", snapshot["rulings"]["sha256"]),
    ):
        if effective_baseline[key] != current:
            raise ValueError(
                "Pinned snapshot changed; archive the durable program baseline "
                f"before replacing {key}"
            )
    delta = build_reusable_piece_delta(
        matrix,
        interactions,
        effective_baseline,
        oracle_coverage=oracle_coverage,
        program_coverage=program_coverage,
        architecture_audit=architecture_audit,
        policy=policy,
    )
    return {
        "matrix": matrix,
        "card_index": card_index,
        "interactions": interactions,
        "complex_cards": complex_cards,
        "baseline": effective_baseline,
        "delta": delta,
    }
