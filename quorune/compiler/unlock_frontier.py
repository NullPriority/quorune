from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import asdict
import hashlib
from itertools import combinations
import re
from typing import Any, Iterable, Mapping, Sequence

from ..card_programs.adapters import compile_best_available_card_program
from ..carddb import CardDatabase
from ..oracle_ir import compile_oracle_card
from ..rules.capabilities import CapabilityRegistry
from ..semantics import SemanticRegistry
from ..util import stable_json
from .ir_model import OracleCardIR, OracleNode, OracleResidual


CARD_UNLOCK_FRONTIER_SCHEMA_VERSION = 1
CARD_UNLOCK_FRONTIER_ALGORITHM_VERSION = "card-unlock-frontier-v3"
MAX_BUNDLE_FAMILIES = 48
CARD_DATA_SNAPSHOT_FIELDS = (
    "schema_version",
    "card_count",
    "ruling_count",
    "oracle_source_sha256",
    "rulings_source_sha256",
    "scryfall_oracle_updated_at",
    "scryfall_rulings_updated_at",
)
BASE_RESIDUAL_FAMILIES = frozenset(
    {
        "capability_dependency",
        "mechanic_dependency",
        "keyword_dependency",
        "event_binding",
        "effect_clause",
        "static_clause",
        "activated_cost",
        "activated_effect",
        "target_or_choice",
        "reference_binding",
        "quantity_expression",
        "duration",
        "zone_permission",
        "search",
        "zone_transition",
        "replacement",
        "continuous_layer",
        "copy_or_face",
        "card_form",
        "multiplayer",
        "unsupported_profile",
        "non_rules_governed",
    }
)

_STATUS_FIELD = "status"
_REASON_FIELD = "reason"
_ERROR_FIELD = "error"
_COPY_MARKER = "copy"
_EXILE_MARKER = "exile"
_RETURN_MARKER = "return"
_SACRIFICE_MARKER = "sacrifice"
_REGENERATION_MARKER = "regeneration"
_FAMILY_PATTERNS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("continuous_layer", ("continuous", "layer")),
    ("event_binding", ("event binding", "trigger grammar", "intervening-if", "reflexive-trigger")),
    ("target_or_choice", ("target", "choice", "choose", "modal")),
    ("reference_binding", ("reference", "that card", "that object", "it binding")),
    ("quantity_expression", ("quantity", "variable amount", "dynamic amount", "counted value")),
    ("duration", ("duration", "until end", "for as long")),
    ("zone_permission", ("permission", "cast from", "play from", "zone casting")),
    ("search", ("search", "shuffle")),
    ("zone_transition", ("zone transition", "zone change", "return to", "move between zones")),
    ("replacement", ("replacement", "instead", "prevent")),
    ("copy_or_face", (_COPY_MARKER, "face-down", "transform", "meld", "merge")),
    ("card_form", ("saga", "class", "battle subtype", "split card", "adventure", "prototype")),
    ("multiplayer", ("multiplayer", "each opponent", "team", "apnap")),
    ("unsupported_profile", ("profile", "format unsupported")),
    ("non_rules_governed", ("non-rules", "non rules", "concession policy", "tournament")),
)


def canonical_card_data_snapshot(metadata: Mapping[str, Any]) -> dict[str, Any]:
    """Return the content identity shared by local and cloud card databases."""

    return {
        key: metadata[key]
        for key in CARD_DATA_SNAPSHOT_FIELDS
        if metadata.get(key) is not None
    }


_RISK_BY_BASE = {
    "capability_dependency": "medium",
    "mechanic_dependency": "high",
    "keyword_dependency": "medium",
    "event_binding": "high",
    "effect_clause": "high",
    "static_clause": "high",
    "activated_cost": "high",
    "activated_effect": "high",
    "target_or_choice": "high",
    "reference_binding": "high",
    "quantity_expression": "medium",
    "duration": "medium",
    "zone_permission": "high",
    "search": "medium",
    "zone_transition": "high",
    "replacement": "very_high",
    "continuous_layer": "very_high",
    "copy_or_face": "very_high",
    "card_form": "high",
    "multiplayer": "high",
    "unsupported_profile": "high",
    "non_rules_governed": "low",
}
_EFFORT_BY_BASE = {
    "capability_dependency": "small",
    "mechanic_dependency": "medium",
    "keyword_dependency": "medium",
    "event_binding": "large",
    "effect_clause": "large",
    "static_clause": "large",
    "activated_cost": "large",
    "activated_effect": "large",
    "target_or_choice": "large",
    "reference_binding": "large",
    "quantity_expression": "medium",
    "duration": "medium",
    "zone_permission": "large",
    "search": "medium",
    "zone_transition": "large",
    "replacement": "very_large",
    "continuous_layer": "very_large",
    "copy_or_face": "very_large",
    "card_form": "large",
    "multiplayer": "large",
    "unsupported_profile": "large",
    "non_rules_governed": "not_applicable",
}
_PRINTED_KEYWORD_MECHANICS = frozenset(
    "deathtouch defender double-strike first-strike flash flying haste "
    "hexproof indestructible infect lifelink menace reach shadow shroud "
    "trample vigilance wither ward equip enchant cycling crew dredge "
    "kicker toxic cumulative-upkeep echo morph bestow evoke flashback unearth "
    "protection".split()
)
_CLAUSE_SIGNATURES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("add-mana", ("add ",)),
    ("counter", ("counter target", "counter all")),
    ("create-token", ("create ", "creates ", "amass ", "incubate ")),
    ("destroy", ("destroy ",)),
    ("discard", ("discard ", "each player discards")),
    ("draw", ("draw ", "you draw", "each player draws")),
    (_EXILE_MARKER, (_EXILE_MARKER + " ",)),
    ("gain-control", ("gain control",)),
    ("life-change", ("gain life", "lose life", "life total")),
    ("look-reveal", ("look at", "reveal ")),
    ("mill", ("mill ",)),
    ("put-onto-battlefield", ("put onto the battlefield",)),
    (_RETURN_MARKER, (_RETURN_MARKER + " ",)),
    (_SACRIFICE_MARKER, (_SACRIFICE_MARKER + " ",)),
    ("search", ("search ",)),
    ("scry", ("scry ",)),
    ("tap-state", ("tap ", "untap ")),
)


def _sha(value: Any) -> str:
    return hashlib.sha256(stable_json(value).encode("utf-8")).hexdigest()


def _slug(value: str) -> str:
    result = re.sub(r"[^a-z0-9._-]+", "-", value.casefold()).strip("-")
    return result[:96] or "unknown"


def _family(base: str, detail: str) -> str:
    if base not in BASE_RESIDUAL_FAMILIES:
        raise ValueError(f"Unknown residual-family base: {base}")
    return f"{base}:{_slug(detail)}"


def _without_parenthetical_text(text: str) -> str:
    """Remove nested reminder text before executable-clause classification."""

    result: list[str] = []
    depth = 0
    for character in text:
        if character == "(":
            depth += 1
        elif character == ")" and depth:
            depth -= 1
        elif not depth:
            result.append(character)
    return "".join(result)


def _material_clause_text(text: str) -> str:
    without_reminders = _without_parenthetical_text(text)
    without_quoted_abilities = re.sub(
        r'"[^"\r\n]*"|“[^”\r\n]*”',
        " ",
        without_reminders,
    )
    return " ".join(without_quoted_abilities.casefold().split())


def _grants_quoted_ability(text: str) -> bool:
    material = " ".join(_without_parenthetical_text(text).casefold().split())
    return bool(
        re.search(
            r'\bgains?\b[^.!?]{0,120}(?:"[^"\r\n]*"|“[^”\r\n]*”)',
            material,
        )
    )


def _damage_instruction_count(material: str) -> int:
    amount = (
        r"(?:\d+|x|\{x\}|that much|twice that much|an amount of)"
    )
    fixed_or_variable = re.compile(
        rf"\bdeals?\s+{amount}\s+damage\b|"
        rf"\band\s+{amount}\s+damage\b"
    )
    equal_to = re.compile(
        r"\bdeals?\s+damage\b[^.!?]{0,120}\b(?:equal to|for each)\b"
    )
    return len(fixed_or_variable.findall(material)) + len(
        equal_to.findall(material)
    )


def _has_life_change(material: str) -> bool:
    return bool(
        re.search(
            r"\b(?:gain|gains|lose|loses)\b[^.!?]{0,48}\blife\b",
            material,
        )
    )


def _kind_base(kind: str) -> str:
    return {
        "dependency_contract": "mechanic_dependency",
        "trigger": "event_binding",
        "spell_effect": "effect_clause",
        "static_ability": "static_clause",
        "effect": "activated_effect",
        "cost": "activated_cost",
        "declaration_cost": "activated_cost",
        "replacement_effect": "replacement",
        "declaration_restriction": "static_clause",
        "unsupported_enchant_restriction": "target_or_choice",
        "unsupported_protection_quality": "target_or_choice",
    }.get(kind, "effect_clause")


def _capability_id(blocker: str) -> str:
    value = blocker.removeprefix("capability:")
    for prefix in ("status:", "missing:", "profile:", "blocker:"):
        if value.startswith(prefix):
            value = value.removeprefix(prefix)
            break
    if ":" in value:
        value = value.split(":", 1)[0]
    return value or "unknown"


def _clause_signature(text: str, *, kind: str, reason: str) -> str:
    material = _material_clause_text(text)
    if re.fullmatch(
        r"flashback(?:\s+(?:\{(?:0|[1-9]\d*|[wubrgc])\})+|"
        r"[—–]\s*(?:\{(?:0|[1-9]\d*|[wubrgc])\})+,\s*"
        r"pay [1-9]\d* life)\.?",
        material,
    ):
        # Cost literals are parameters of one reusable Flashback grammar, not
        # independent implementation families.
        return "unparsed-flashback-fixed-cost"
    counter_signatures = _counter_clause_signatures(material)
    if "remove-counter" in counter_signatures:
        return "remove-counter"
    put_signatures = _put_clause_signatures(material)
    if "put-counter" in put_signatures:
        return "put-counter"
    if "put-onto-battlefield" in put_signatures:
        return "put-onto-battlefield"
    if _damage_instruction_count(material):
        return "deal-damage"
    if _has_life_change(material):
        return "life-change"
    for signature, markers in _CLAUSE_SIGNATURES:
        if any(material.startswith(marker) for marker in markers):
            return signature
    if _COPY_MARKER in material:
        return _COPY_MARKER
    words = re.findall(r"[a-z0-9]+", material)
    if words:
        return "unparsed-" + "-".join(words[:3])
    return f"unparsed-{_slug(reason or kind)[:48]}"


def _clause_families(text: str, *, kind: str, reason: str) -> set[str]:
    """Return every obvious blocker in an unparsed executable clause.

    Residuals can contain several sentences or linked instructions. Treating
    only their first verb as the blocker inflates the predicted card gain.
    This intentionally recognizes only conservative syntax landmarks; the
    compiler remains the authority for exact lowering.
    """

    material = _material_clause_text(text)
    prevents_damage = bool(
        re.search(r"\bprevent\b[^.!?]{0,160}\bdamage\b", material)
    )
    grants_quoted_ability = _grants_quoted_ability(text)
    damage_instruction_count = (
        0 if prevents_damage else _damage_instruction_count(material)
    )
    signatures = {
        signature
        for signature, markers in _CLAUSE_SIGNATURES
        if any(marker in material for marker in markers)
    }
    if damage_instruction_count:
        signatures.add("deal-damage")
    if _has_life_change(material):
        signatures.add("life-change")
    signatures.update(_counter_clause_signatures(material))
    signatures.update(_put_clause_signatures(material))
    if "destroy " in material:
        signatures.discard("destroy")
        if re.search(r"\bdestroy (?:all|each)\b", material):
            signatures.add("destroy-mass")
        elif "destroy target" in material or "destroy two target" in material:
            signatures.add("destroy-target")
        else:
            signatures.add("destroy")
    has_standalone_family = prevents_damage or grants_quoted_ability
    if not signatures and not has_standalone_family:
        signatures.add(_clause_signature(material, kind=kind, reason=reason))
    result = {_family(_kind_base(kind), signature) for signature in signatures}
    if prevents_damage:
        result.add(_family("replacement", "damage-prevention"))
    if grants_quoted_ability:
        result.add(_family("continuous_layer", "granted-ability"))
    if "until end of turn" in material:
        result.add(_family("duration", "until-end-of-turn"))
    material_sentences = len(
        [
            sentence
            for sentence in re.split(r"[.!?]+", material)
            if sentence.strip()
        ]
    )
    if (
        len(signatures) > 1
        or material_sentences > 1
        or damage_instruction_count > 1
        or re.search(r"\bthen\b", material)
    ):
        result.add(_family(_kind_base(kind), "ordered-effect-composition"))
    if re.search(r"\b(if|unless)\b", material):
        result.add(_family("target_or_choice", "conditional-effect"))
    if re.search(r"\b(for each|number of|equal to)\b|\bx\b", material):
        result.add(_family("quantity_expression", "dynamic-quantity"))
    if re.search(
        r"\b(with (?:power|toughness)|without [a-z-]+|attacking|blocking|nonbasic|"
        r"nonblack|nonblue|nongreen|nonred|nonwhite|mana value|"
        r"dealt damage this turn|entered this turn)\b|"
        r"\b(?:target|each|all)\s+(?:[a-z-]+\s+|"
        r"[a-z-]+\s+or\s+[a-z-]+\s+)"
        r"(?:cards?|creatures?|permanents?|artifacts?|enchantments?|lands?|"
        r"planeswalkers?|players?|opponents?|spells?)\b|"
        r"\b(?:target|each|all)\s+(?:[a-z-]+\s+){0,3}"
        r"(?:cards?|creatures?|permanents?|artifacts?|enchantments?|lands?|"
        r"planeswalkers?|players?|opponents?|spells?)\b[^.!?]{0,64}"
        r"\b(?:with|without|that|which|you|an opponent)\b|"
        r"\btarget creature token\b",
        material,
    ):
        result.add(_family("target_or_choice", "target-predicate"))
    if re.search(r"\bdivided\b[^.!?]{0,96}\b(?:choose|among)\b", material):
        result.add(_family("target_or_choice", "divided-damage-allocation"))
    if re.search(
        r"\b(?:two|three|four|five|up to (?:two|three|four|five)) targets?\b|"
        r"\bone, two, or three targets?\b|\bone or two targets?\b|"
        r"\btarget\b[^.!?]{0,96}\band target\b|\b(?:an)?other target\b",
        material,
    ):
        result.add(_family("target_or_choice", "multiple-targets"))
    if damage_instruction_count > 1 or re.search(
        r"\bdamage to\b[^.!?]{0,96}\band (?:each|all)\b",
        material,
    ):
        result.add(_family("target_or_choice", "multiple-damage-recipients"))
    if re.search(r"\broll (?:a|one or more) d(?:6|20)\b", material):
        result.add(_family("target_or_choice", "random-outcome"))
    if "can't be regenerated" in material or "cannot be regenerated" in material:
        result.add(_family("replacement", _REGENERATION_MARKER))
    if re.search(
        r"\b(this way|that (?:card|creature|permanent|player)|"
        r"its (?:owner|controller))\b",
        material,
    ):
        result.add(_family("reference_binding", "linked-result-reference"))
    return result


def _put_clause_signatures(material: str) -> set[str]:
    """Classify counter placement separately from zone placement."""

    signatures: set[str] = set()
    if re.search(r"\bput\b[^.!?]{0,160}\bcounters?\s+on\b", material):
        signatures.add("put-counter")
    if re.search(
        r"\bput\b[^.!?]{0,160}\bonto the battlefield\b",
        material,
    ):
        signatures.add("put-onto-battlefield")
    return signatures


def _counter_clause_signatures(material: str) -> set[str]:
    """Classify counter removal separately from unrelated removal wording."""

    if re.search(
        r"\bremove\b[^.!?]{0,160}\bcounters?\s+from\b",
        material,
    ):
        return {"remove-counter"}
    return set()


def canonical_residual_families(
    residual: OracleResidual | Mapping[str, Any],
) -> tuple[str, ...]:
    """Classify one material residual into stable, dependency-sized leaves."""

    if isinstance(residual, OracleResidual):
        kind = residual.kind
        reason = residual.reason
        blockers = residual.blockers
        text = residual.text
    else:
        kind = str(residual.get("kind") or "")
        reason = str(residual.get(_REASON_FIELD) or "")
        blockers = tuple(str(value) for value in residual.get("blockers", ()))
        text = str(residual.get("text") or "")
    result: set[str] = set()
    for blocker in blockers:
        lowered = blocker.casefold().strip()
        if lowered.startswith("mechanic:"):
            mechanic = lowered.split(":", 1)[1]
            keyword = (
                mechanic in _PRINTED_KEYWORD_MECHANICS
                or "recognized keyword" in reason.casefold()
            )
            result.add(
                _family(
                    "keyword_dependency" if keyword else "mechanic_dependency",
                    mechanic,
                )
            )
            continue
        if lowered.startswith("capability:"):
            result.add(_family("capability_dependency", _capability_id(lowered)))
            continue
        matched = False
        for base, markers in _FAMILY_PATTERNS:
            if any(marker in lowered for marker in markers):
                result.add(_family(base, lowered))
                matched = True
        if not matched:
            result.add(_family(_kind_base(kind), lowered))
    if not result:
        base = _kind_base(kind)
        if base in {"effect_clause", "activated_effect", "static_clause"}:
            result.update(_clause_families(text, kind=kind, reason=reason))
        else:
            result.add(_family(base, reason or kind or "unclassified"))
    return tuple(sorted(result))


def _capability_blockers(
    node: OracleNode,
    capabilities: CapabilityRegistry,
    *,
    profile: str,
) -> tuple[str, ...]:
    if not node.capability_dependencies:
        return ()
    closure = capabilities.closure(
        node.capability_dependencies,
        profile=profile,
    )
    return tuple(
        sorted(
            _family("capability_dependency", _capability_id(blocker))
            for blocker in closure.blockers
        )
    )


def _ability_row(
    node: OracleNode,
    residuals: Sequence[OracleResidual],
    capabilities: CapabilityRegistry,
    *,
    profile: str,
) -> dict[str, Any]:
    residual_by_id = {residual.residual_id: residual for residual in residuals}
    attached = [
        residual_by_id[residual_id]
        for residual_id in node.residual_ids
        if residual_id in residual_by_id and residual_by_id[residual_id].material
    ]
    family_ids = {
        family_id
        for residual in attached
        for family_id in canonical_residual_families(residual)
    }
    family_ids.update(
        _capability_blockers(node, capabilities, profile=profile)
    )
    if node.exact and not family_ids:
        ability_status = "exact"
    elif node.lowerable:
        ability_status = "lowerable_untrusted"
    else:
        ability_status = "unresolved"
    mechanic_ids = sorted(set(node.mechanics))
    capability_ids = sorted(set(node.capability_dependencies))
    runtime_components = sorted(
        {
            component
            for capability_id in capability_ids
            for component in (
                (capabilities.capability(capability_id) or {}).get(
                    "implementation_components", ()
                )
            )
        }
    )
    blockers = {
        key: value
        for key, value in {
            "canonical_family_ids": sorted(family_ids),
            "capability_ids": capability_ids,
            "mechanic_ids": mechanic_ids,
            "compiler_stage_ids": sorted(
                {value.split(":", 1)[0] for value in family_ids}
            ),
            "runtime_component_ids": runtime_components,
            "interaction_ids": sorted(
                {
                    value
                    for residual in attached
                    for value in residual.blockers
                    if not value.startswith(("mechanic:", "capability:"))
                }
            ),
        }.items()
        if value
    }
    row: dict[str, Any] = {
        "ability_id": node.node_id,
        "kind": node.kind,
        "source_line": node.span.line,
        _STATUS_FIELD: ability_status,
    }
    if node.lowerable:
        row["lowerable"] = True
    if node.template_id is not None:
        row["template_id"] = node.template_id
    if blockers:
        row["blockers"] = blockers
    attached_rows = [
        {
            "residual_id": residual.residual_id,
            "family_ids": list(canonical_residual_families(residual)),
        }
        for residual in attached
    ]
    if attached_rows:
        row["residuals"] = attached_rows
    return row


def _orphan_ability_row(
    residual: OracleResidual,
    *,
    index: int,
) -> dict[str, Any]:
    families = canonical_residual_families(residual)
    blockers = {
        "canonical_family_ids": list(families),
        "compiler_stage_ids": sorted(
            {value.split(":", 1)[0] for value in families}
        ),
    }
    if residual.blockers:
        blockers["interaction_ids"] = sorted(residual.blockers)
    return {
        "ability_id": f"orphan-residual:{index}:{residual.residual_id}",
        "kind": residual.kind,
        "source_line": residual.span.line,
        _STATUS_FIELD: "unresolved",
        "blockers": blockers,
        "residuals": [
            {
                "residual_id": residual.residual_id,
                "family_ids": list(families),
            }
        ],
    }


def analyze_card_unlocks(
    ir: OracleCardIR,
    *,
    program: Any | None,
    program_error: str | None,
    capabilities: CapabilityRegistry,
    profile: str,
) -> dict[str, Any]:
    abilities: list[dict[str, Any]] = []
    for face in ir.faces:
        attached_ids: set[str] = set()
        for node in face.nodes:
            row = _ability_row(
                node,
                face.residuals,
                capabilities,
                profile=profile,
            )
            attached_ids.update(
                residual["residual_id"]
                for residual in row.get("residuals", ())
            )
            row["face_id"] = face.face_id
            abilities.append(row)
        for index, residual in enumerate(face.residuals, start=1):
            if residual.material and residual.residual_id not in attached_ids:
                row = _orphan_ability_row(residual, index=index)
                row["face_id"] = face.face_id
                abilities.append(row)
    family_ids = sorted(
        {
            family_id
            for ability in abilities
            for family_id in ability.get("blockers", {}).get(
                "canonical_family_ids", ()
            )
        }
    )
    exact_abilities = sum(
        ability[_STATUS_FIELD] == "exact" for ability in abilities
    )
    lowerable_untrusted = sum(
        ability[_STATUS_FIELD] == "lowerable_untrusted"
        for ability in abilities
    )
    if program_error is not None:
        program_status = "failed"
        trust_basis = None
    elif program is not None and program.trust_closure["trusted"]:
        program_status = "trusted"
        trust_basis = program.trust_closure["trust_basis"]
    elif program is not None and program.residuals:
        program_status = "residual"
        trust_basis = program.trust_closure["trust_basis"]
    else:
        program_status = "untrusted"
        trust_basis = (
            program.trust_closure["trust_basis"] if program is not None else None
        )
    return {
        "oracle_id": ir.oracle_id,
        "card_name": ir.card_name,
        "oracle_ir_status": ir.status,
        "card_program_status": program_status,
        "card_program_trust_basis": trust_basis,
        "hard_construction_failure": program_error,
        "material_ability_count": len(abilities),
        "exact_ability_count": exact_abilities,
        "lowerable_untrusted_ability_count": lowerable_untrusted,
        "minimum_known_blocker_set": family_ids,
        "abilities": abilities,
    }


def _family_readiness(
    family_id: str,
    *,
    capabilities: CapabilityRegistry,
    mechanic_contracts: Mapping[str, Mapping[str, Any]],
    lowerable_occurrences: int,
    occurrences: int,
) -> tuple[str, list[str]]:
    base, detail = family_id.split(":", 1)
    prerequisites: list[str] = []
    if base == "capability_dependency":
        row = capabilities.capability(detail)
        if row is None:
            return "missing", [detail]
        prerequisites.extend(str(value) for value in row["dependencies"])
        prerequisites.extend(str(value) for value in row["blockers"])
        if row[_STATUS_FIELD] == "trusted":
            return "trusted", sorted(set(prerequisites))
        if row["implementation_components"]:
            return "implemented_untrusted", sorted(set(prerequisites))
        return str(row[_STATUS_FIELD]), sorted(set(prerequisites))
    if base in {"keyword_dependency", "mechanic_dependency"}:
        contract = mechanic_contracts.get(detail)
        if contract is None:
            return "missing_contract", []
        prerequisites.extend(str(value) for value in contract["dependencies"])
        prerequisites.extend(str(value) for value in contract["known_blockers"])
        return str(contract["coverage_status"]), sorted(set(prerequisites))
    if occurrences and lowerable_occurrences == occurrences:
        return "lowered_untrusted", []
    if lowerable_occurrences:
        return "partial_lowering", []
    return "missing_lowering", []


def _aggregate_candidates(
    cards: Sequence[Mapping[str, Any]],
    *,
    capabilities: CapabilityRegistry,
    mechanic_contracts: Mapping[str, Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], int]:
    occurrences: Counter[str] = Counter()
    affected_cards: defaultdict[str, set[str]] = defaultdict(set)
    lowerable_occurrences: Counter[str] = Counter()
    card_sets: Counter[tuple[str, ...]] = Counter()
    ability_sets: Counter[tuple[str, ...]] = Counter()
    residual_sets: Counter[tuple[str, ...]] = Counter()
    additional: defaultdict[str, Counter[int]] = defaultdict(Counter)
    for card in cards:
        card_blockers = tuple(card["minimum_known_blocker_set"])
        if card_blockers:
            card_sets[card_blockers] += 1
        for family_id in card_blockers:
            affected_cards[family_id].add(str(card["oracle_id"]))
            additional[family_id][min(len(card_blockers) - 1, 3)] += 1
        for ability in card["abilities"]:
            blockers = tuple(
                ability.get("blockers", {}).get("canonical_family_ids", ())
            )
            if blockers:
                ability_sets[blockers] += 1
            for family_id in blockers:
                occurrences[family_id] += 1
                if ability.get("lowerable", False):
                    lowerable_occurrences[family_id] += 1
            for residual in ability.get("residuals", ()):
                residual_blockers = tuple(residual["family_ids"])
                if residual_blockers:
                    residual_sets[residual_blockers] += 1

    def gain(counts: Mapping[tuple[str, ...], int], bundle: set[str]) -> int:
        return sum(
            count for blockers, count in counts.items() if set(blockers) <= bundle
        )

    candidates = []
    for family_id in sorted(occurrences):
        base = family_id.split(":", 1)[0]
        readiness, prerequisites = _family_readiness(
            family_id,
            capabilities=capabilities,
            mechanic_contracts=mechanic_contracts,
            lowerable_occurrences=lowerable_occurrences[family_id],
            occurrences=occurrences[family_id],
        )
        singleton = {family_id}
        candidates.append(
            {
                "family_id": family_id,
                "base_family": base,
                "occurrences": occurrences[family_id],
                "affected_cards": len(affected_cards[family_id]),
                "sole_blocker_cards": gain(card_sets, singleton),
                "one_additional_blocker_cards": additional[family_id][1],
                "two_additional_blocker_cards": additional[family_id][2],
                "lowerable_untrusted_abilities": lowerable_occurrences[family_id],
                "runtime_compiler_readiness": readiness,
                "interaction_risk": _RISK_BY_BASE[base],
                "prerequisites": prerequisites,
                "estimated_effort": _EFFORT_BY_BASE[base],
                "expected_exact_card_gain": gain(card_sets, singleton),
                "expected_exact_ability_gain": gain(ability_sets, singleton),
                "expected_material_residual_gain": gain(residual_sets, singleton),
            }
        )
    candidates.sort(
        key=lambda row: (
            -row["expected_exact_card_gain"],
            -row["expected_exact_ability_gain"],
            -row["affected_cards"],
            row["family_id"],
        )
    )
    bundle_universe = [row["family_id"] for row in candidates[:MAX_BUNDLE_FAMILIES]]
    bundles: list[dict[str, Any]] = []
    evaluated = 0
    for size in (1, 2, 3):
        for family_ids in combinations(bundle_universe, size):
            evaluated += 1
            bundle = set(family_ids)
            exact_cards = gain(card_sets, bundle)
            exact_abilities = gain(ability_sets, bundle)
            residual_gain = gain(residual_sets, bundle)
            if not (exact_cards or exact_abilities or residual_gain):
                continue
            bundles.append(
                {
                    "family_ids": list(family_ids),
                    "size": size,
                    "expected_exact_card_gain": exact_cards,
                    "expected_exact_ability_gain": exact_abilities,
                    "expected_material_residual_gain": residual_gain,
                }
            )
    bundles.sort(
        key=lambda row: (
            -row["expected_exact_card_gain"],
            -row["expected_exact_ability_gain"],
            row["size"],
            row["family_ids"],
        )
    )
    return candidates, bundles[:100], evaluated


def build_card_unlock_frontier(
    db: CardDatabase,
    *,
    registry: SemanticRegistry,
    capabilities: CapabilityRegistry,
    mechanic_contracts: Iterable[Mapping[str, Any]] = (),
    profile: str = "commander_review",
    limit: int | None = None,
) -> dict[str, Any]:
    contract_map = {
        str(contract["mechanic_id"]): dict(contract)
        for contract in mechanic_contracts
    }
    cards: list[dict[str, Any]] = []
    oracle_statuses: Counter[str] = Counter()
    program_statuses: Counter[str] = Counter()
    hard_failures = []
    for record in db.iter_cards(commander_legal_only=True, limit=limit):
        ir = compile_oracle_card(
            record,
            capability_registry=capabilities,
            capability_profile=profile,
        )
        program = None
        program_error = None
        try:
            program = compile_best_available_card_program(
                db,
                record,
                semantic_registry=registry,
                capability_profile=profile,
                capability_registry=capabilities,
            )
        except (KeyError, ValueError) as exc:
            program_error = str(exc)
        row = analyze_card_unlocks(
            ir,
            program=program,
            program_error=program_error,
            capabilities=capabilities,
            profile=profile,
        )
        cards.append(row)
        oracle_statuses[row["oracle_ir_status"]] += 1
        program_statuses[row["card_program_status"]] += 1
        if program_error is not None:
            hard_failures.append(
                {
                    "oracle_id": record.oracle_id,
                    "card_name": record.name,
                    _ERROR_FIELD: program_error,
                }
            )
    candidates, bundles, evaluated = _aggregate_candidates(
        cards,
        capabilities=capabilities,
        mechanic_contracts=contract_map,
    )
    report: dict[str, Any] = {
        "schema_version": CARD_UNLOCK_FRONTIER_SCHEMA_VERSION,
        "algorithm_version": CARD_UNLOCK_FRONTIER_ALGORITHM_VERSION,
        "profile": profile,
        "commander_legal_only": True,
        "limited": limit is not None,
        "card_data_snapshot": canonical_card_data_snapshot(db.metadata()),
        "capability_registry_fingerprint": capabilities.fingerprint,
        "capability_evidence_fingerprint": capabilities.evidence_fingerprint,
        "semantic_registry_fingerprint": _sha(
            {
                "schema_version": 1,
                "programs": [
                    program.to_dict() for program in registry.programs()
                ],
            }
        ),
        "base_residual_families": sorted(BASE_RESIDUAL_FAMILIES),
        "ability_field_defaults": {
            "blockers": {},
            "lowerable": False,
            "residuals": [],
            "template_id": None,
        },
        "cards_considered": len(cards),
        "oracle_status_counts": dict(sorted(oracle_statuses.items())),
        "card_program_status_counts": dict(sorted(program_statuses.items())),
        "hard_construction_failures": hard_failures,
        "family_candidates": candidates,
        "bundle_evaluation": {
            "maximum_size": 3,
            "family_universe_limit": MAX_BUNDLE_FAMILIES,
            "evaluated_bundle_count": evaluated,
            "top_bundles": bundles,
        },
        "cards": cards,
        "complete_snapshot_claimed": False,
        "boundary": (
            "This is a minimum-known-blocker frontier for the pinned Commander-legal "
            "snapshot. It does not prove complete Comprehensive Rules behavior."
        ),
    }
    report["fingerprint"] = _sha(report)
    return report


def validate_card_unlock_frontier(value: Mapping[str, Any]) -> None:
    if value.get("schema_version") != CARD_UNLOCK_FRONTIER_SCHEMA_VERSION:
        raise ValueError("Unsupported card-unlock frontier schema_version")
    if value.get("algorithm_version") != CARD_UNLOCK_FRONTIER_ALGORITHM_VERSION:
        raise ValueError("Unsupported card-unlock frontier algorithm_version")
    if value.get("commander_legal_only") is not True:
        raise ValueError("Card-unlock frontier must be Commander-legal scoped")
    if value.get("complete_snapshot_claimed") is not False:
        raise ValueError("Card-unlock frontier cannot claim complete coverage")
    families = value.get("base_residual_families")
    if families != sorted(BASE_RESIDUAL_FAMILIES):
        raise ValueError("Card-unlock frontier base family registry is stale")
    if value.get("ability_field_defaults") != {
        "blockers": {},
        "lowerable": False,
        "residuals": [],
        "template_id": None,
    }:
        raise ValueError("Card-unlock frontier ability defaults are invalid")
    cards = value.get("cards")
    if not isinstance(cards, list) or len(cards) != value.get("cards_considered"):
        raise ValueError("Card-unlock frontier card accounting is invalid")
    oracle_ids: list[Any] = []
    ability_required = {
        "ability_id",
        "face_id",
        "kind",
        "source_line",
        _STATUS_FIELD,
    }
    ability_allowed = ability_required | {
        "blockers",
        "lowerable",
        "residuals",
        "template_id",
    }
    blocker_allowed = {
        "canonical_family_ids",
        "capability_ids",
        "mechanic_ids",
        "compiler_stage_ids",
        "runtime_component_ids",
        "interaction_ids",
    }
    valid_ability_statuses = {"exact", "lowerable_untrusted", "unresolved"}
    for card in cards:
        if not isinstance(card, Mapping):
            raise ValueError("Card-unlock frontier card rows must be mappings")
        oracle_ids.append(card.get("oracle_id"))
        abilities = card.get("abilities")
        if not isinstance(abilities, list):
            raise ValueError("Card-unlock frontier abilities must be a list")
        observed_families: set[str] = set()
        observed_exact = 0
        observed_lowerable = 0
        ability_ids: set[str] = set()
        for ability in abilities:
            if not isinstance(ability, Mapping):
                raise ValueError("Card-unlock frontier ability rows must be mappings")
            fields = set(ability)
            if not ability_required <= fields or not fields <= ability_allowed:
                raise ValueError("Card-unlock frontier ability fields are invalid")
            ability_id = ability.get("ability_id")
            if not isinstance(ability_id, str) or not ability_id:
                raise ValueError("Card-unlock frontier ability_id is invalid")
            if ability_id in ability_ids:
                raise ValueError("Card-unlock frontier contains duplicate ability IDs")
            ability_ids.add(ability_id)
            status = ability.get(_STATUS_FIELD)
            if status not in valid_ability_statuses:
                raise ValueError("Card-unlock frontier ability status is invalid")
            observed_exact += status == "exact"
            observed_lowerable += status == "lowerable_untrusted"
            if "lowerable" in ability and ability.get("lowerable") is not True:
                raise ValueError("Card-unlock frontier lowerable override is invalid")
            if "template_id" in ability and ability.get("template_id") is None:
                raise ValueError("Card-unlock frontier template override is invalid")
            blockers = ability.get("blockers", {})
            if not isinstance(blockers, Mapping) or not blockers:
                if blockers != {}:
                    raise ValueError("Card-unlock frontier blockers are invalid")
            else:
                if not set(blockers) <= blocker_allowed:
                    raise ValueError("Card-unlock frontier blocker fields are invalid")
                for key, entries in blockers.items():
                    if (
                        not isinstance(entries, list)
                        or not entries
                        or not all(isinstance(entry, str) and entry for entry in entries)
                        or entries != sorted(set(entries))
                    ):
                        raise ValueError(
                            f"Card-unlock frontier blocker list {key} is invalid"
                        )
                canonical = blockers.get("canonical_family_ids", [])
                for family_id in canonical:
                    if ":" not in family_id:
                        raise ValueError("Card-unlock frontier family ID is invalid")
                    base, detail = family_id.split(":", 1)
                    if base not in BASE_RESIDUAL_FAMILIES or not detail:
                        raise ValueError("Card-unlock frontier family ID is invalid")
                observed_families.update(canonical)
            residuals = ability.get("residuals", [])
            if not isinstance(residuals, list):
                raise ValueError("Card-unlock frontier residuals are invalid")
            for residual in residuals:
                if not isinstance(residual, Mapping) or set(residual) != {
                    "family_ids",
                    "residual_id",
                }:
                    raise ValueError("Card-unlock frontier residual row is invalid")
                residual_families = residual.get("family_ids")
                if (
                    not isinstance(residual_families, list)
                    or not residual_families
                    or not all(
                        isinstance(entry, str) and entry
                        for entry in residual_families
                    )
                    or residual_families != sorted(set(residual_families))
                ):
                    raise ValueError("Card-unlock frontier residual families are invalid")
        minimum = card.get("minimum_known_blocker_set")
        if minimum != sorted(observed_families):
            raise ValueError("Card-unlock frontier minimum blocker set is invalid")
        if card.get("material_ability_count") != len(abilities):
            raise ValueError("Card-unlock frontier ability accounting is invalid")
        if card.get("exact_ability_count") != observed_exact:
            raise ValueError("Card-unlock frontier exact ability accounting is invalid")
        if card.get("lowerable_untrusted_ability_count") != observed_lowerable:
            raise ValueError("Card-unlock frontier lowerable accounting is invalid")
    if len(oracle_ids) != len(set(oracle_ids)):
        raise ValueError("Card-unlock frontier contains duplicate Oracle IDs")
    supplied = value.get("fingerprint")
    payload = dict(value)
    payload.pop("fingerprint", None)
    if supplied != _sha(payload):
        raise ValueError("Card-unlock frontier fingerprint does not match")


def render_card_unlock_frontier_markdown(value: Mapping[str, Any]) -> str:
    validate_card_unlock_frontier(value)
    lines = [
        "---",
        'title: "Commander card-unlock frontier"',
        'status: "generated"',
        'authoritative_source: "coverage/card-unlock-frontier.json.gz"',
        f'verified: "{value["fingerprint"]}"',
        'audience: "compiler and rules contributors"',
        'maintenance: "generated"',
        "---",
        "",
        "# Commander card-unlock frontier",
        "",
        "This generated report ranks minimum known compiler and rules blockers for the pinned Commander-legal card snapshot. It is not a claim of complete Comprehensive Rules coverage.",
        "",
        "## Snapshot",
        "",
        f"- Cards considered: {value['cards_considered']:,}",
        f"- Oracle states: `{stable_json(value['oracle_status_counts'])}`",
        f"- CardProgram states: `{stable_json(value['card_program_status_counts'])}`",
        f"- Hard construction failures: {len(value['hard_construction_failures']):,}",
        f"- Frontier fingerprint: `{value['fingerprint']}`",
        "",
        "## Highest-leverage single families",
        "",
        "| Family | Occurrences | Cards | Sole-blocker cards | Exact abilities | Readiness | Risk |",
        "|---|---:|---:|---:|---:|---|---|",
    ]
    for row in value["family_candidates"][:25]:
        lines.append(
            "| `{family_id}` | {occurrences:,} | {affected_cards:,} | "
            "{expected_exact_card_gain:,} | {expected_exact_ability_gain:,} | "
            "{runtime_compiler_readiness} | {interaction_risk} |".format(**row)
        )
    lines.extend(
        [
            "",
            "## Highest-leverage bounded bundles",
            "",
            "| Families | Exact cards | Exact abilities | Residuals |",
            "|---|---:|---:|---:|",
        ]
    )
    for row in value["bundle_evaluation"]["top_bundles"][:20]:
        lines.append(
            f"| `{', '.join(row['family_ids'])}` | "
            f"{row['expected_exact_card_gain']:,} | "
            f"{row['expected_exact_ability_gain']:,} | "
            f"{row['expected_material_residual_gain']:,} |"
        )
    lines.extend(
        [
            "",
            "## Hard construction failures",
            "",
        ]
    )
    if value["hard_construction_failures"]:
        for failure in value["hard_construction_failures"]:
            lines.append(
                f"- `{failure['oracle_id']}` — {failure['card_name']}: {failure[_ERROR_FIELD]}"
            )
    else:
        lines.append("- None in the pinned Commander-legal snapshot.")
    lines.extend(
        [
            "",
            "## Boundary",
            "",
            str(value["boundary"]),
            "The JSON artifact contains every card, every represented material ability, canonical blocker sets, dependency categories, and the bounded one/two/three-family evaluation.",
            "",
        ]
    )
    return "\n".join(lines)


__all__ = [
    "BASE_RESIDUAL_FAMILIES",
    "CARD_UNLOCK_FRONTIER_ALGORITHM_VERSION",
    "CARD_UNLOCK_FRONTIER_SCHEMA_VERSION",
    "analyze_card_unlocks",
    "build_card_unlock_frontier",
    "canonical_residual_families",
    "render_card_unlock_frontier_markdown",
    "validate_card_unlock_frontier",
]
