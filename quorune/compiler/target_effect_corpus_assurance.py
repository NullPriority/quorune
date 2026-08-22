from __future__ import annotations

"""Corpus assurance for the closed fixed-target effect grammars."""

from collections import Counter, defaultdict
from dataclasses import dataclass
import hashlib
import inspect
import re
from typing import Any, Iterable, Mapping, Sequence, TYPE_CHECKING

from ..abilities import parse_activated_abilities
from ..carddb import CardRecord
from ..rules.node_capability_shapes import (
    fixed_target_characteristics_node_capabilities,
    fixed_target_effect_sequence_node_capabilities,
)
from ..util import stable_json
from .fixed_counter_trigger_nodes import (
    FIXED_COUNTER_EVENT_TRIGGER_TEMPLATE_IDS,
    FIXED_TYPED_EVENT_EFFECT_TRIGGER_TEMPLATE_IDS,
)
from .direct_target import (
    DIRECT_NONCREATURE_SUBTYPES,
    DirectPermanentTargetSpec,
    direct_permanent_target_spec,
)
from .fixed_target_effect_sequences import (
    FIXED_TARGET_CHARACTERISTIC_KEYWORDS,
    FixedTargetCharacteristicsTemplate,
    FixedTargetEffectSequenceTemplate,
    fixed_target_characteristics_effect_template,
    fixed_target_effect_sequence_template,
)

if TYPE_CHECKING:
    from .ir_model import OracleCardIR, OracleNode
    from ..rules.capabilities import CapabilityRegistry


ASSURANCE_SCHEMA_VERSION = 1
ASSURANCE_ALGORITHM_VERSION = "fixed-target-corpus-assurance-v7"
STANDALONE_TEMPLATE_ID = (
    "fixed-target-characteristics-until-end-of-turn-v1"
)
SEQUENCE_TEMPLATE_ID = (
    "fixed-target-counter-characteristics-sequence-v1"
)
TARGET_TEMPLATE_IDS = frozenset(
    {STANDALONE_TEMPLATE_ID, SEQUENCE_TEMPLATE_ID}
)
COMPOSING_TEMPLATE_IDS = frozenset(
    FIXED_COUNTER_EVENT_TRIGGER_TEMPLATE_IDS
    | FIXED_TYPED_EVENT_EFFECT_TRIGGER_TEMPLATE_IDS
)
SUPPORTED_CONTEXTS = (
    "activated_ability",
    "spell_ability",
    "triggered_ability",
)
REJECTION_CATEGORIES = (
    "compound",
    "modal",
    "multi_target",
    "optional",
    "repeated",
    "variable",
)
_ABILITY_WORD = re.compile(
    r"^(?P<word>[A-Za-z][A-Za-z ']+)\s+[—-]\s+(?P<body>.+)$"
)
_SORCERY_ONLY = re.compile(
    r"\.?\s*activate only as a sorcery\.?$",
    re.IGNORECASE,
)


class TargetEffectAssuranceError(ValueError):
    """The promoted fixed-target compiler corpus is not closed."""


@dataclass(frozen=True, slots=True)
class AcceptedTargetEffectContract:
    category: str
    body: str
    template_id: str
    controller_relation: str
    target_predicate: str
    source_exclusion: bool
    clause_count: int
    operation_order: str


@dataclass(frozen=True, slots=True)
class RejectedTargetEffectContract:
    category: str
    body: str


@dataclass(frozen=True, slots=True)
class TargetEffectObservation:
    identity: str
    oracle_id: str
    face_id: str
    node_id: str
    template_id: str
    context: str
    clause_count: int
    effect_count: int
    operation_order: tuple[str, ...]
    controller_relation: str
    target_predicate: str
    source_exclusion: bool
    keywords: tuple[str, ...]
    node_exact: bool
    card_exact: bool
    source_sha256: str

    @property
    def shape_payload(self) -> dict[str, Any]:
        return {
            "template_id": self.template_id,
            "context": self.context,
            "clause_count": self.clause_count,
            "effect_count": self.effect_count,
            "operation_order": list(self.operation_order),
            "controller_relation": self.controller_relation,
            "target_predicate": self.target_predicate,
            "source_exclusion": self.source_exclusion,
            "keywords": list(self.keywords),
            "node_exact": self.node_exact,
        }


def _sha256(value: Mapping[str, Any] | Sequence[Any] | str) -> str:
    text = value if isinstance(value, str) else stable_json(value)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _canonical_relation_text(relation: str) -> str:
    return {
        "any": "",
        "you": " you control",
        "opponent": " an opponent controls",
    }[relation]


def accepted_target_effect_contracts() -> tuple[
    AcceptedTargetEffectContract, ...
]:
    """Return algorithmic contracts for every closed grammar dimension."""

    contracts: list[AcceptedTargetEffectContract] = []
    for keyword in sorted(FIXED_TARGET_CHARACTERISTIC_KEYWORDS):
        contracts.append(
            AcceptedTargetEffectContract(
                category=f"keyword:{keyword}",
                body=(
                    f"Target creature gains {keyword} until end of turn."
                ),
                template_id=STANDALONE_TEMPLATE_ID,
                controller_relation="any",
                target_predicate="creature",
                source_exclusion=False,
                clause_count=1,
                operation_order="target_only",
            )
        )
    for relation in ("any", "you", "opponent"):
        relation_text = _canonical_relation_text(relation)
        contracts.append(
            AcceptedTargetEffectContract(
                category=f"standalone_relation:{relation}",
                body=(
                    f"Target creature{relation_text} gets +2/-1 until end "
                    "of turn."
                ),
                template_id=STANDALONE_TEMPLATE_ID,
                controller_relation=relation,
                target_predicate="creature",
                source_exclusion=False,
                clause_count=1,
                operation_order="target_only",
            )
        )
        for clause_count in (2, 3):
            target_first = (
                f"Target creature{relation_text} gets +1/+1 until end of "
                "turn. Put a +1/+1 counter on it."
            )
            counter_first = (
                f"Put a +1/+1 counter on target creature{relation_text}. "
                "It gets +1/+1 until end of turn."
            )
            if clause_count == 3:
                target_first += " It gains flying until end of turn."
                counter_first += " It gains flying until end of turn."
            for order, body in (
                ("target_first", target_first),
                ("counter_first", counter_first),
            ):
                contracts.append(
                    AcceptedTargetEffectContract(
                        category=(
                            f"sequence:{relation}:{clause_count}:{order}"
                        ),
                        body=body,
                        template_id=SEQUENCE_TEMPLATE_ID,
                        controller_relation=relation,
                        target_predicate="creature",
                        source_exclusion=False,
                        clause_count=clause_count,
                        operation_order=order,
                    )
                )
    predicate_contracts = (
        (
            "artifact-or-creature",
            "target artifact or creature",
            "artifact-or-creature",
        ),
        (
            "enchantment-creature",
            "target enchantment creature",
            "creature-enchantment",
        ),
        (
            "creature-with-flying",
            "target creature with flying",
            "creature-with-flying",
        ),
        (
            "creature-subtype-disjunction",
            "target Bird or Cat",
            "bird-or-cat",
        ),
        (
            "source-exclusion",
            "another target creature",
            "creature",
        ),
    )
    for category, subject, target_predicate in predicate_contracts:
        source_exclusion = subject.startswith("another target ")
        contracts.append(
            AcceptedTargetEffectContract(
                category=f"sequence_predicate:{category}",
                body=(
                    f"Put a +1/+1 counter on {subject}. "
                    "It gains trample until end of turn."
                ),
                template_id=SEQUENCE_TEMPLATE_ID,
                controller_relation="any",
                target_predicate=target_predicate,
                source_exclusion=source_exclusion,
                clause_count=2,
                operation_order="counter_first",
            )
        )
    for subtype in sorted(DIRECT_NONCREATURE_SUBTYPES):
        contracts.append(
            AcceptedTargetEffectContract(
                category=(
                    "sequence_predicate:reviewed-noncreature-subtype:"
                    f"{subtype}"
                ),
                body=(
                    f"Put a +1/+1 counter on target {subtype.title()}. "
                    "It gains trample until end of turn."
                ),
                template_id=SEQUENCE_TEMPLATE_ID,
                controller_relation="any",
                target_predicate=DirectPermanentTargetSpec(
                    subtypes_any=(subtype,)
                ).characteristic_slug,
                source_exclusion=False,
                clause_count=2,
                operation_order="counter_first",
            )
        )
    return tuple(contracts)


def rejected_target_effect_contracts() -> tuple[
    RejectedTargetEffectContract, ...
]:
    """Return text-adjacent forms that the closed grammar must reject."""

    return (
        RejectedTargetEffectContract(
            "optional",
            "Up to one target creature gets +1/+1 until end of turn. "
            "Put a +1/+1 counter on it.",
        ),
        RejectedTargetEffectContract(
            "modal",
            "Choose one — Target creature gets +1/+1 until end of turn; "
            "or put a +1/+1 counter on target creature.",
        ),
        RejectedTargetEffectContract(
            "variable",
            "Target creature gets +X/+X until end of turn. "
            "Put a +1/+1 counter on it.",
        ),
        RejectedTargetEffectContract(
            "compound",
            "Target creature gains flying or reach until end of turn. "
            "Put a +1/+1 counter on it.",
        ),
        RejectedTargetEffectContract(
            "repeated",
            "Target creature gains flying and flying until end of turn. "
            "Put a +1/+1 counter on it.",
        ),
        RejectedTargetEffectContract(
            "multi_target",
            "Target creature gets +1/+1 until end of turn. "
            "Put a +1/+1 counter on target creature.",
        ),
    )


def _contract_record(text: str, context: str) -> CardRecord:
    if context == "spell_ability":
        oracle_text, type_line = text, "Instant"
    elif context == "triggered_ability":
        oracle_text = f"When this creature enters, {text}"
        type_line = "Creature — Fixture"
    elif context == "activated_ability":
        oracle_text = f"{{1}}: {text}"
        type_line = "Creature — Fixture"
    else:
        raise TargetEffectAssuranceError(
            f"unsupported target-effect context: {context}"
        )
    return CardRecord(
        oracle_id=_sha256(f"{context}:{text}"),
        name="Target Effect Contract Fixture",
        mana_cost="{1}",
        mana_value=1.0,
        type_line=type_line,
        oracle_text=oracle_text,
        power="1" if "Creature" in type_line else None,
        toughness="1" if "Creature" in type_line else None,
        loyalty=None,
        defense=None,
        colors=(),
        color_identity=(),
        keywords=(),
        produced_mana=(),
        layout="normal",
        released_at="2000-01-01",
        legalities={"commander": "legal"},
        faces=(),
        raw={},
    )


def synthetic_target_effect_contract(
    capability_registry: CapabilityRegistry,
    *,
    capability_profile: str,
) -> dict[str, Any]:
    """Compile every grammar dimension without pinning corpus card names."""

    from ..oracle_ir import compile_oracle_card

    accepted = accepted_target_effect_contracts()
    rejected = rejected_target_effect_contracts()
    accepted_context_counts: Counter[str] = Counter()
    template_counts: Counter[str] = Counter()
    for contract in accepted:
        for context in SUPPORTED_CONTEXTS:
            ir = compile_oracle_card(
                _contract_record(contract.body, context),
                capability_registry=capability_registry,
                capability_profile=capability_profile,
            )
            nodes = tuple(
                node
                for face in ir.faces
                for node in face.nodes
                if node.template_id in TARGET_TEMPLATE_IDS
            )
            if len(nodes) != 1 or nodes[0].template_id != contract.template_id:
                raise TargetEffectAssuranceError(
                    "accepted target-effect contract did not lower through "
                    f"{context}: {contract.category}"
                )
            if _target_predicate(nodes[0].target_schema) != contract.target_predicate:
                raise TargetEffectAssuranceError(
                    "accepted target-effect contract lowered the wrong target "
                    f"predicate through {context}: {contract.category}"
                )
            if (
                _target_source_exclusion(nodes[0].target_schema)
                is not contract.source_exclusion
            ):
                raise TargetEffectAssuranceError(
                    "accepted target-effect contract lowered the wrong source "
                    f"exclusion through {context}: {contract.category}"
                )
            accepted_context_counts[context] += 1
            template_counts[contract.template_id] += 1
    for contract in rejected:
        for context in SUPPORTED_CONTEXTS:
            ir = compile_oracle_card(
                _contract_record(contract.body, context),
                capability_registry=capability_registry,
                capability_profile=capability_profile,
            )
            if any(
                node.template_id in TARGET_TEMPLATE_IDS
                for face in ir.faces
                for node in face.nodes
            ):
                raise TargetEffectAssuranceError(
                    "adjacent target-effect form was promoted: "
                    f"{contract.category} in {context}"
                )
    payload = {
        "accepted_case_count": len(accepted) * len(SUPPORTED_CONTEXTS),
        "accepted_context_counts": dict(sorted(accepted_context_counts.items())),
        "accepted_template_counts": dict(sorted(template_counts.items())),
        "clause_counts": sorted({value.clause_count for value in accepted}),
        "contexts": list(SUPPORTED_CONTEXTS),
        "controller_relations": sorted(
            {value.controller_relation for value in accepted}
        ),
        "operation_orders": sorted(
            {value.operation_order for value in accepted}
        ),
        "target_predicates": sorted(
            {value.target_predicate for value in accepted}
        ),
        "source_exclusion_values": sorted(
            {value.source_exclusion for value in accepted}
        ),
        "rejected_case_count": len(rejected) * len(SUPPORTED_CONTEXTS),
        "rejection_categories": sorted(
            {value.category for value in rejected}
        ),
        "supported_keywords": sorted(
            FIXED_TARGET_CHARACTERISTIC_KEYWORDS
        ),
        "contract_source_fingerprint": _sha256(
            {
                "accepted": [
                    {
                        "category": value.category,
                        "body_sha256": _sha256(value.body),
                        "template_id": value.template_id,
                        "relation": value.controller_relation,
                        "target_predicate": value.target_predicate,
                        "source_exclusion": value.source_exclusion,
                        "clause_count": value.clause_count,
                        "operation_order": value.operation_order,
                    }
                    for value in accepted
                ],
                "rejected": [
                    {
                        "category": value.category,
                        "body_sha256": _sha256(value.body),
                    }
                    for value in rejected
                ],
            }
        ),
    }
    return {**payload, "fingerprint": _sha256(payload)}


def grammar_source_fingerprint() -> str:
    """Fingerprint the exact parser and capability-shape implementation."""

    values: Iterable[Any] = (
        FixedTargetCharacteristicsTemplate.__post_init__,
        FixedTargetCharacteristicsTemplate.compiled,
        FixedTargetEffectSequenceTemplate.compiled,
        DirectPermanentTargetSpec.__post_init__,
        DirectPermanentTargetSpec.from_target_schema,
        DirectPermanentTargetSpec.to_target_schema,
        direct_permanent_target_spec,
        fixed_target_characteristics_effect_template,
        fixed_target_effect_sequence_template,
        fixed_target_characteristics_node_capabilities,
        fixed_target_effect_sequence_node_capabilities,
        accepted_target_effect_contracts,
        rejected_target_effect_contracts,
        _without_parenthetical_reminder,
        _resolution_body,
    )
    payload = {
        "algorithm_version": ASSURANCE_ALGORITHM_VERSION,
        "keywords": sorted(FIXED_TARGET_CHARACTERISTIC_KEYWORDS),
        "sources": [inspect.getsource(value) for value in values],
    }
    return _sha256(payload)


def _face_keywords(record: CardRecord, face_index: int) -> tuple[str, ...]:
    if not record.faces:
        return record.keywords
    raw = record.faces[face_index]
    return tuple(raw.get("keywords") or record.keywords)


def _without_parenthetical_reminder(text: str) -> str:
    result: list[str] = []
    depth = 0
    for character in text:
        if character == "(":
            depth += 1
            continue
        if character == ")" and depth:
            depth -= 1
            continue
        if depth == 0:
            result.append(character)
    return "".join(result).strip()


def _resolution_body(
    record: CardRecord,
    node: OracleNode,
    *,
    face_index: int,
    face_name: str,
) -> str | None:
    text = " ".join(
        _without_parenthetical_reminder(node.text).strip().split()
    )
    if node.kind == "spell_ability":
        ability_word = _ABILITY_WORD.fullmatch(text)
        return ability_word.group("body") if ability_word else text
    if node.kind == "triggered_ability":
        if node.event == "unresolved" or ", " not in text:
            return None
        return text.split(", ", 1)[1]
    if node.kind == "activated_ability":
        abilities = parse_activated_abilities(
            card_name=face_name or record.name,
            oracle_text=text,
            keywords=_face_keywords(record, face_index),
        )
        if len(abilities) != 1:
            return None
        return _SORCERY_ONLY.sub("", abilities[0].effect_text).strip()
    return None


def _compiled_template(
    body: str,
    *,
    card_name: str,
) -> tuple[
    str,
    tuple[Mapping[str, Any], ...],
    Mapping[str, Any] | None,
    tuple[str, ...],
] | None:
    standalone = fixed_target_characteristics_effect_template(body)
    if standalone is not None:
        return standalone.compiled()
    sequence = fixed_target_effect_sequence_template(
        body,
        card_name=card_name,
    )
    return sequence.compiled() if sequence is not None else None


def _clause_count(body: str, template_id: str) -> int:
    if template_id == STANDALONE_TEMPLATE_ID:
        return 1
    normalized = " ".join(body.strip().split()).rstrip(".")
    return len(tuple(value for value in re.split(r"\.\s+", normalized) if value))


def _relation(target_schema: Mapping[str, Any] | None) -> str:
    relation = str((target_schema or {}).get("controller_relation") or "any")
    if relation not in {"any", "you", "opponent"}:
        raise TargetEffectAssuranceError(
            f"unsupported target controller relation: {relation}"
        )
    return relation


def _target_predicate(target_schema: Mapping[str, Any] | None) -> str:
    try:
        return DirectPermanentTargetSpec.from_target_schema(
            target_schema  # type: ignore[arg-type]
        ).characteristic_slug
    except (TypeError, ValueError) as exc:
        raise TargetEffectAssuranceError(
            "target-effect assurance requires one closed direct target"
        ) from exc


def _target_source_exclusion(
    target_schema: Mapping[str, Any] | None,
) -> bool:
    try:
        return DirectPermanentTargetSpec.from_target_schema(
            target_schema  # type: ignore[arg-type]
        ).source_exclusion
    except (TypeError, ValueError) as exc:
        raise TargetEffectAssuranceError(
            "target-effect assurance requires one closed direct target"
        ) from exc


def _required_capabilities(
    node: OracleNode,
    *,
    target_template_id: str,
) -> tuple[str, ...]:
    resolver = (
        fixed_target_characteristics_node_capabilities
        if target_template_id == STANDALONE_TEMPLATE_ID
        else fixed_target_effect_sequence_node_capabilities
    )
    return resolver(
        effects=node.effects,
        target_schema=node.target_schema,
        mechanic_ids=node.mechanics,
    )


def _observation(
    record: CardRecord,
    ir: OracleCardIR,
    node: OracleNode,
    *,
    face_index: int,
    face_id: str,
    face_name: str,
    body: str,
) -> TargetEffectObservation:
    compiled = _compiled_template(body, card_name=face_name or record.name)
    if compiled is None or (
        compiled[0] != node.template_id
        and node.template_id not in COMPOSING_TEMPLATE_IDS
    ):
        raise TargetEffectAssuranceError(
            "promoted target-effect node is outside the exact source grammar: "
            f"{record.oracle_id}:{face_id}:{node.node_id}"
        )
    template_id, effects, target_schema, mechanics = compiled
    if stable_json(effects) != stable_json(node.effects) or stable_json(
        target_schema
    ) != stable_json(node.target_schema):
        raise TargetEffectAssuranceError(
            "promoted target-effect node differs from source lowering: "
            f"{record.oracle_id}:{face_id}:{node.node_id}"
        )
    if not set(mechanics).issubset(node.mechanics):
        raise TargetEffectAssuranceError(
            "promoted target-effect node lost grammar mechanics: "
            f"{record.oracle_id}:{face_id}:{node.node_id}"
        )
    required = _required_capabilities(
        node,
        target_template_id=template_id,
    )
    if not required:
        raise TargetEffectAssuranceError(
            "promoted target-effect node is outside the closed capability shape: "
            f"{record.oracle_id}:{face_id}:{node.node_id}"
        )
    missing = sorted(set(required) - set(node.capability_dependencies))
    if missing:
        raise TargetEffectAssuranceError(
            "promoted target-effect node lost required capabilities "
            f"{missing}: {record.oracle_id}:{face_id}:{node.node_id}"
        )
    if node.exact and not set(node.capability_dependencies).issubset(
        node.capability_closure
    ):
        raise TargetEffectAssuranceError(
            "exact target-effect node has an incomplete capability closure: "
            f"{record.oracle_id}:{face_id}:{node.node_id}"
        )
    operations = tuple(str(value.get("op") or "") for value in node.effects)
    keywords = tuple(
        sorted(
            str(value["keyword"]).casefold()
            for value in node.effects
            if value.get("op") == "grant_keyword_until_end_of_turn"
        )
    )
    identity_payload = {
        "oracle_id": record.oracle_id,
        "face_id": face_id,
        "node_id": node.node_id,
        "template_id": template_id,
        "source_sha256": _sha256(node.text),
        "effects": [dict(value) for value in node.effects],
        "target_schema": node.target_schema,
        "capability_dependencies": list(node.capability_dependencies),
    }
    return TargetEffectObservation(
        identity=_sha256(identity_payload),
        oracle_id=record.oracle_id,
        face_id=face_id,
        node_id=node.node_id,
        template_id=template_id,
        context=node.kind,
        clause_count=_clause_count(body, template_id),
        effect_count=len(node.effects),
        operation_order=operations,
        controller_relation=_relation(node.target_schema),
        target_predicate=_target_predicate(node.target_schema),
        source_exclusion=_target_source_exclusion(node.target_schema),
        keywords=keywords,
        node_exact=node.exact,
        card_exact=ir.status == "exact",
        source_sha256=_sha256(node.text),
    )


class TargetEffectCorpusCollector:
    """Collect and independently validate promoted target-effect nodes."""

    def __init__(self) -> None:
        self._observations: list[TargetEffectObservation] = []

    def observe(self, record: CardRecord, ir: OracleCardIR) -> None:
        for face_index, face in enumerate(ir.faces):
            for node in face.nodes:
                body = _resolution_body(
                    record,
                    node,
                    face_index=face_index,
                    face_name=face.face_name,
                )
                compiled = (
                    _compiled_template(body, card_name=face.face_name)
                    if body is not None
                    else None
                )
                expected_template = compiled[0] if compiled is not None else None
                if expected_template in TARGET_TEMPLATE_IDS and (
                    node.template_id != expected_template
                    and node.template_id not in COMPOSING_TEMPLATE_IDS
                ):
                    raise TargetEffectAssuranceError(
                        "accepted target-effect source was routed to a different "
                        f"template: {record.oracle_id}:{face.face_id}:{node.node_id}"
                    )
                if (
                    node.template_id not in TARGET_TEMPLATE_IDS
                    and not (
                        expected_template in TARGET_TEMPLATE_IDS
                        and node.template_id in COMPOSING_TEMPLATE_IDS
                    )
                ):
                    continue
                if body is None:
                    raise TargetEffectAssuranceError(
                        "promoted target-effect node has no closed resolution body: "
                        f"{record.oracle_id}:{face.face_id}:{node.node_id}"
                    )
                self._observations.append(
                    _observation(
                        record,
                        ir,
                        node,
                        face_index=face_index,
                        face_id=face.face_id,
                        face_name=face.face_name,
                        body=body,
                    )
                )

    def report(
        self,
        *,
        compiler_version: str,
        capability_registry: CapabilityRegistry,
        capability_profile: str,
        card_data_snapshot: Mapping[str, Any],
        commander_legal_only: bool,
    ) -> dict[str, Any]:
        observations = tuple(
            sorted(
                self._observations,
                key=lambda value: (
                    value.oracle_id,
                    value.face_id,
                    value.node_id,
                    value.identity,
                ),
            )
        )
        grouped: defaultdict[str, list[TargetEffectObservation]] = defaultdict(list)
        shape_payloads: dict[str, Mapping[str, Any]] = {}
        for value in observations:
            shape_id = _sha256(value.shape_payload)
            grouped[shape_id].append(value)
            shape_payloads[shape_id] = value.shape_payload
        shapes = []
        for shape_id in sorted(grouped):
            values = grouped[shape_id]
            shapes.append(
                {
                    "shape_id": shape_id,
                    **shape_payloads[shape_id],
                    "count": len(values),
                    "representative_identities": [
                        {
                            "identity": value.identity,
                            "oracle_id": value.oracle_id,
                            "face_id": value.face_id,
                            "node_id": value.node_id,
                            "source_sha256": value.source_sha256,
                        }
                        for value in values[:3]
                    ],
                }
            )
        dimensions: dict[str, Counter[Any]] = {
            "clause_counts": Counter(value.clause_count for value in observations),
            "contexts": Counter(value.context for value in observations),
            "controller_relations": Counter(
                value.controller_relation for value in observations
            ),
            "target_predicates": Counter(
                value.target_predicate for value in observations
            ),
            "source_exclusion": Counter(
                value.source_exclusion for value in observations
            ),
            "effect_counts": Counter(value.effect_count for value in observations),
            "keywords": Counter(
                keyword for value in observations for keyword in value.keywords
            ),
            "operation_orders": Counter(
                ",".join(value.operation_order) for value in observations
            ),
            "templates": Counter(value.template_id for value in observations),
        }
        contract = synthetic_target_effect_contract(
            capability_registry,
            capability_profile=capability_profile,
        )
        payload = {
            "schema_version": ASSURANCE_SCHEMA_VERSION,
            "algorithm_version": ASSURANCE_ALGORITHM_VERSION,
            "compiler_version": compiler_version,
            "grammar_source_fingerprint": grammar_source_fingerprint(),
            "capability_profile": capability_profile,
            "capability_registry_fingerprint": capability_registry.fingerprint,
            "capability_evidence_fingerprint": (
                capability_registry.evidence_fingerprint
            ),
            "card_data_snapshot": dict(card_data_snapshot),
            "commander_legal_only": commander_legal_only,
            "content_boundary": (
                "Public Oracle IDs, generated node identities, source hashes, "
                "semantic categories, and aggregate counts only; no Oracle prose."
            ),
            "total_nodes": len(observations),
            "total_cards": len({value.oracle_id for value in observations}),
            "exact_nodes": sum(value.node_exact for value in observations),
            "exact_cards_with_template": len(
                {value.oracle_id for value in observations if value.card_exact}
            ),
            "identity_fingerprint": _sha256(
                [value.identity for value in observations]
            ),
            "shape_count": len(shapes),
            "shapes": shapes,
            "dimensions": {
                name: {
                    str(key): count
                    for key, count in sorted(values.items(), key=lambda item: str(item[0]))
                }
                for name, values in sorted(dimensions.items())
            },
            "synthetic_contract": contract,
        }
        return {**payload, "fingerprint": _sha256(payload)}


def validate_target_effect_assurance(
    value: Mapping[str, Any],
    *,
    compiler_version: str,
    capability_registry: CapabilityRegistry,
    capability_profile: str,
    card_data_snapshot: Mapping[str, Any],
    commander_legal_only: bool,
) -> None:
    """Validate one persisted assurance report without trusting its claims."""

    fingerprint = value.get("fingerprint")
    payload = {key: item for key, item in value.items() if key != "fingerprint"}
    if fingerprint != _sha256(payload):
        raise TargetEffectAssuranceError(
            "target-effect corpus assurance fingerprint is stale"
        )
    expected = {
        "schema_version": ASSURANCE_SCHEMA_VERSION,
        "algorithm_version": ASSURANCE_ALGORITHM_VERSION,
        "compiler_version": compiler_version,
        "grammar_source_fingerprint": grammar_source_fingerprint(),
        "capability_profile": capability_profile,
        "capability_registry_fingerprint": capability_registry.fingerprint,
        "capability_evidence_fingerprint": capability_registry.evidence_fingerprint,
        "card_data_snapshot": dict(card_data_snapshot),
        "commander_legal_only": commander_legal_only,
    }
    for field, expected_value in expected.items():
        if value.get(field) != expected_value:
            raise TargetEffectAssuranceError(
                f"target-effect corpus assurance {field} is stale"
            )
    contract = synthetic_target_effect_contract(
        capability_registry,
        capability_profile=capability_profile,
    )
    if value.get("synthetic_contract") != contract:
        raise TargetEffectAssuranceError(
            "target-effect corpus assurance contract is stale"
        )
    shapes = value.get("shapes")
    if not isinstance(shapes, list) or value.get("shape_count") != len(shapes):
        raise TargetEffectAssuranceError(
            "target-effect corpus assurance shapes are malformed"
        )
    if any(not isinstance(item, Mapping) for item in shapes):
        raise TargetEffectAssuranceError(
            "target-effect corpus assurance shape must be an object"
        )
    total = sum(int(item.get("count") or 0) for item in shapes)
    if total != value.get("total_nodes"):
        raise TargetEffectAssuranceError(
            "target-effect corpus assurance node totals disagree"
        )
    dimensions = value.get("dimensions")
    if not isinstance(dimensions, Mapping):
        raise TargetEffectAssuranceError(
            "target-effect corpus assurance dimensions are malformed"
        )
    template_counts = dimensions.get("templates")
    if not isinstance(template_counts, Mapping) or sum(
        int(item) for item in template_counts.values()
    ) != value.get("total_nodes"):
        raise TargetEffectAssuranceError(
            "target-effect corpus assurance template totals disagree"
        )


__all__ = [
    "ASSURANCE_ALGORITHM_VERSION",
    "ASSURANCE_SCHEMA_VERSION",
    "REJECTION_CATEGORIES",
    "SEQUENCE_TEMPLATE_ID",
    "STANDALONE_TEMPLATE_ID",
    "SUPPORTED_CONTEXTS",
    "TARGET_TEMPLATE_IDS",
    "TargetEffectAssuranceError",
    "TargetEffectCorpusCollector",
    "accepted_target_effect_contracts",
    "grammar_source_fingerprint",
    "rejected_target_effect_contracts",
    "synthetic_target_effect_contract",
    "validate_target_effect_assurance",
]
