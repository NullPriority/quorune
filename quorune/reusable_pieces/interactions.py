from __future__ import annotations

from collections import defaultdict
from itertools import combinations
import re
from typing import Any, Iterable, Mapping, Protocol, Sequence


_PIECE_ID = re.compile(r"^[a-z][a-z0-9._:-]*$")
_ASSURANCE_KINDS = frozenset(
    {
        "fail_closed_runtime_admission",
        "pair_derived",
        "runtime_composition",
    }
)


class InteractionPiece(Protocol):
    class_id: str
    test_ids: set[str]


def validate_interaction_evidence(value: Mapping[str, Any]) -> None:
    if not isinstance(value, Mapping) or set(value) != {
        "schema_version",
        "declarations",
    }:
        raise ValueError("Interaction evidence has a closed top-level schema")
    if value.get("schema_version") != 2:
        raise ValueError("Unsupported interaction evidence schema")
    declarations = value.get("declarations")
    if not isinstance(declarations, list):
        raise ValueError("Interaction evidence declarations must be an array")
    identities: set[tuple[str, tuple[str, ...]]] = set()
    for row in declarations:
        if not isinstance(row, Mapping) or set(row) != {
            "evidence_class",
            "assurance_kind",
            "test_id",
            "piece_ids",
            "capability_ids",
            "interaction_order",
            "assertion",
        }:
            raise ValueError(
                "Interaction evidence declarations have a closed schema"
            )
        if row["evidence_class"] != "interaction":
            raise ValueError("Interaction evidence class must be interaction")
        assurance_kind = row["assurance_kind"]
        if assurance_kind not in _ASSURANCE_KINDS:
            raise ValueError("Interaction assurance kind is unsupported")
        test_id = row["test_id"]
        assertion = row["assertion"]
        if not isinstance(test_id, str) or not test_id:
            raise ValueError(
                "Interaction evidence test IDs must be nonempty"
            )
        if not isinstance(assertion, str) or not assertion:
            raise ValueError(
                "Interaction evidence assertions must be nonempty"
            )
        piece_ids = row["piece_ids"]
        capability_ids = row["capability_ids"]
        if (
            not isinstance(piece_ids, list)
            or len(piece_ids) < 2
            or piece_ids != sorted(set(piece_ids))
            or not all(_PIECE_ID.fullmatch(value) for value in piece_ids)
        ):
            raise ValueError(
                "Interaction evidence piece IDs must be sorted unique pairs "
                "or tuples"
            )
        contains_residual = any(
            piece_id.startswith("residual.") for piece_id in piece_ids
        )
        if len(piece_ids) == 2 and contains_residual != (
            assurance_kind == "fail_closed_runtime_admission"
        ):
            raise ValueError(
                "Residual interaction evidence must prove fail-closed "
                "runtime admission; executable pairs must prove runtime "
                "composition"
            )
        if len(piece_ids) == 2 and assurance_kind == "pair_derived":
            raise ValueError(
                "Exact interaction pairs must declare their assurance kind"
            )
        if len(piece_ids) > 2 and assurance_kind != "pair_derived":
            raise ValueError(
                "Higher-order legacy evidence must derive assurance per pair"
            )
        if (
            not isinstance(capability_ids, list)
            or capability_ids != sorted(set(capability_ids))
            or not all(
                isinstance(value, str) and value for value in capability_ids
            )
        ):
            raise ValueError(
                "Interaction evidence capability IDs must be sorted and unique"
            )
        if any(
            f"capability.{value}" not in piece_ids for value in capability_ids
        ):
            raise ValueError(
                "Every declared capability ID must name an exact declared piece"
            )
        if (
            not isinstance(row["interaction_order"], int)
            or isinstance(row["interaction_order"], bool)
            or row["interaction_order"] != len(piece_ids)
        ):
            raise ValueError(
                "Interaction evidence order must equal its exact piece tuple"
            )
        identity = (test_id, tuple(piece_ids))
        if identity in identities:
            raise ValueError("Duplicate interaction evidence declaration")
        identities.add(identity)


def validate_interaction_test_references(
    value: Mapping[str, Any], *, known_test_ids: Iterable[str],
) -> None:
    """Validate exact test identities before expensive corpus generation."""
    validate_interaction_evidence(value)
    known = set(known_test_ids)
    missing = sorted({
        str(row['test_id']) for row in value['declarations']
        if row['test_id'] not in known
    })
    if missing:
        raise ValueError('Interaction evidence references unknown tests: ' + ', '.join(missing))


def build_interactions(
    cards: Sequence[Mapping[str, Any]],
    pieces: Mapping[str, InteractionPiece],
    policy: Mapping[str, Any],
    interaction_evidence: Mapping[str, Any],
    *,
    known_test_ids: Iterable[str],
) -> tuple[list[dict[str, Any]], dict[str, dict[str, int]]]:
    """Build corpus and declared ambient pair coverage.

    Corpus co-occurrence alone cannot identify cross-card composition surfaces.
    The existing policy therefore declares bounded ambient high-risk pairs, and
    explicit evidence declarations make every exercised pair visible even when
    no one printed card contains both pieces.
    """

    validate_interaction_test_references(interaction_evidence, known_test_ids=known_test_ids)
    evidence_by_pair: dict[tuple[str, str], set[str]] = defaultdict(set)
    assurance_by_pair: dict[tuple[str, str], set[str]] = defaultdict(set)
    for declaration in interaction_evidence["declarations"]:
        piece_ids = tuple(declaration["piece_ids"])
        unknown = set(piece_ids) - set(pieces)
        if unknown:
            raise ValueError(
                "Interaction evidence references unknown pieces: "
                + ", ".join(sorted(unknown))
            )
        test_id = str(declaration["test_id"])
        for pair in combinations(piece_ids, 2):
            canonical_pair = tuple(sorted(pair))
            evidence_by_pair[canonical_pair].add(test_id)
            assurance_kind = str(declaration["assurance_kind"])
            if assurance_kind == "pair_derived":
                assurance_kind = (
                    "fail_closed_runtime_admission"
                    if any(
                        piece_id.startswith("residual.")
                        for piece_id in canonical_pair
                    )
                    else "runtime_composition"
                )
            assurance_by_pair[canonical_pair].add(assurance_kind)

    ambient_high_risk_pairs = {
        tuple(str(piece_id) for piece_id in pair)
        for pair in policy["ambient_high_risk_piece_pairs"]
    }
    unknown_ambient = {
        piece_id
        for pair in ambient_high_risk_pairs
        for piece_id in pair
        if piece_id not in pieces
    }
    if unknown_ambient:
        raise ValueError(
            "Ambient high-risk policy references unknown pieces: "
            + ", ".join(sorted(unknown_ambient))
        )

    pair_cards: dict[tuple[str, str], set[str]] = defaultdict(set)
    pair_abilities: dict[tuple[str, str], set[str]] = defaultdict(set)
    for card in cards:
        oracle_id = str(card["oracle_id"])
        piece_ids = sorted(
            str(row["piece_id"]) for row in card.get("pieces", ())
        )
        for index, left in enumerate(piece_ids):
            for right in piece_ids[index + 1 :]:
                pair_cards[(left, right)].add(oracle_id)
        ability_pieces: dict[str, set[str]] = defaultdict(set)
        for relation in card.get("pieces", ()):
            piece_id = str(relation["piece_id"])
            for ability_id in relation.get("ability_ids", ()):
                ability_pieces[str(ability_id)].add(piece_id)
        for ability_id, related in ability_pieces.items():
            ordered = sorted(related)
            for index, left in enumerate(ordered):
                for right in ordered[index + 1 :]:
                    pair_abilities[(left, right)].add(
                        f"{oracle_id}:{ability_id}"
                    )

    high_risk_pairs = {
        tuple(sorted(str(value) for value in pair))
        for pair in policy["high_risk_class_pairs"]
    }
    rows: list[dict[str, Any]] = []
    per_piece: dict[str, dict[str, int]] = defaultdict(
        lambda: {
            "applicable": 0,
            "covered": 0,
            "high_risk_applicable": 0,
            "high_risk_covered": 0,
        }
    )
    applicable_pairs = (
        set(pair_cards) | set(evidence_by_pair) | ambient_high_risk_pairs
    )
    for pair in sorted(applicable_pairs):
        left, right = pair
        left_piece = pieces[left]
        right_piece = pieces[right]
        evidence_tests = sorted(evidence_by_pair.get(pair, ()))
        assurance_kinds = sorted(assurance_by_pair.get(pair, ()))
        covered = bool(evidence_tests)
        high_risk = pair in ambient_high_risk_pairs or (
            tuple(sorted((left_piece.class_id, right_piece.class_id)))
            in high_risk_pairs
        )
        applicability_bases = []
        if pair in pair_cards:
            applicability_bases.append("corpus_cooccurrence")
        if pair in ambient_high_risk_pairs:
            applicability_bases.append("declared_ambient_high_risk")
        if pair in evidence_by_pair:
            applicability_bases.append("explicit_interaction_evidence")
        rows.append(
            {
                "piece_ids": [left, right],
                "class_ids": [left_piece.class_id, right_piece.class_id],
                "card_count": len(pair_cards[pair]),
                "ability_count": len(pair_abilities.get(pair, set())),
                "covered": covered,
                "high_risk": high_risk,
                "applicability_bases": sorted(applicability_bases),
                "evidence_test_ids": evidence_tests,
                "evidence_assurance_kinds": assurance_kinds,
                "evidence_basis": "explicit_interaction_declaration_v2",
            }
        )
        for piece_id in pair:
            per_piece[piece_id]["applicable"] += 1
            per_piece[piece_id]["covered"] += int(covered)
            per_piece[piece_id]["high_risk_applicable"] += int(high_risk)
            per_piece[piece_id]["high_risk_covered"] += int(
                high_risk and covered
            )
    return rows, dict(per_piece)


__all__ = [
    "build_interactions",
    "validate_interaction_evidence",
]
