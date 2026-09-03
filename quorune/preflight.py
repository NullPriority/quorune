from __future__ import annotations

from collections import Counter
from dataclasses import asdict
import hashlib
from pathlib import Path
import re
from typing import Any

from .abilities import parse_activated_abilities
from .carddb import CardDatabase, CardRecord
from .compiler.entry_state_templates import static_entry_state_handler
from .card_programs.binding import bind_card_program_runtime
from .card_programs.trust import compute_match_trust_closure
from .deck import DeckDefinition, DeckLoader
from .mana import ManaPlanError, extract_mana_modes, parsed_cost
from .oracle_ir import (
    ORACLE_COMPILER_VERSION,
    compile_oracle_card,
    register_generated_programs,
)
from .profiles import (
    deck_list_fingerprint,
    deck_source_fingerprint,
)
from .semantics import SemanticRegistry
from .util import mana_cost_to_vector, stable_json

from .rules.capabilities import (
    CapabilityRegistry,
    load_default_capability_registry,
)


PREFLIGHT_SCHEMA_VERSION = 3

_BUILTIN_STATIC_KEYWORDS = {
    # These keywords are consumed authoritatively by timing, attack, target,
    # destruction, or state-based-action code rather than semantic programs.
    "deathtouch",
    "flash",
    "flying",
    "haste",
    "hexproof",
    "indestructible",
    "infect",
    "lifelink",
    "protection",
    "reach",
    "shadow",
    "shroud",
    "toxic",
    "vigilance",
    "wither",
}

_BUILTIN_MATERIAL_KEYWORDS = {
    "affinity",
    "channel",
    "convoke",
    "flash",
    "kicker",
    "metalcraft",
    "overload",
    "storm",
}

_MATERIAL_KEYWORDS = {
    "bestow",
    "craft",
    "crew",
    "cumulative upkeep",
    "cycling",
    "deathtouch",
    "delirium",
    "double strike",
    "dredge",
    "enchant",
    "equip",
    "evoke",
    "explore",
    "fabricate",
    "first strike",
    "flying",
    "haste",
    "hexproof",
    "indestructible",
    "infect",
    "lifelink",
    "menace",
    "populate",
    "protection",
    "reach",
    "shadow",
    "shroud",
    "station",
    "trample",
    "transform",
    "toxic",
    "vigilance",
    "ward",
    "wither",
}


def _without_parenthetical_reminder(text: str) -> str:
    """Remove balanced parenthetical reminder text from Oracle text.

    Reminder text frequently contains colons, trigger words, and token mana
    abilities that are not separate abilities of the printed card. Treating
    those fragments as source abilities makes preflight over-count coverage
    and makes the activated-ability parser expose impossible actions.
    """

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
    return "".join(result)


def _without_quoted_granted_text(text: str) -> str:
    """Remove quoted abilities granted to another object.

    The enclosing static ability remains material. The quoted activated or
    triggered ability is not itself an ability of the source card.
    """

    result: list[str] = []
    quoted = False
    for character in text:
        if character in {'"', "“", "”"}:
            quoted = not quoted
            continue
        if not quoted:
            result.append(character)
    return "".join(result)


def _material_oracle_text(record: CardRecord) -> str:
    # The compact database prefixes each face in its combined display text
    # (``Face Name: Oracle text``).  Those labels are not activated-ability
    # costs.  Inspect the actual face texts so transform/modal cards do not
    # acquire phantom colon abilities during preflight.
    if record.faces:
        return "\n//\n".join(
            _without_parenthetical_reminder(
                str(face.get("oracle_text") or "")
            )
            for face in record.faces
            if str(face.get("oracle_text") or "").strip()
        )
    return _without_parenthetical_reminder(record.oracle_text)


def _printed_activated_abilities(record: CardRecord) -> tuple[Any, ...]:
    return parse_activated_abilities(
        card_name=record.name,
        oracle_text=_without_quoted_granted_text(
            _material_oracle_text(record)
        ),
        keywords=record.keywords,
    )


def _printed_trigger_lines(record: CardRecord) -> list[str]:
    if not record.is_permanent_spell:
        return []
    return [
        line
        for line in _material_oracle_text(record).splitlines()
        if any(
            marker in line.casefold()
            for marker in ("when ", "whenever ", "at the beginning")
        )
    ]


def _printed_static_lines(record: CardRecord) -> list[str]:
    """Return conservative material permanent text not otherwise categorized."""

    if not record.is_permanent_spell:
        return []
    activated_effects = {
        ability.effect_text.casefold().strip().rstrip(".")
        for ability in _printed_activated_abilities(record)
    }
    rows: list[str] = []
    for raw_line in _material_oracle_text(record).splitlines():
        line = raw_line.strip()
        lower = line.casefold()
        if not line or line in {"//"} or line.startswith("•"):
            continue
        if any(
            marker in lower
            for marker in ("when ", "whenever ", "at the beginning")
        ):
            continue
        if ":" in line and any(
            effect and effect in lower for effect in activated_effects
        ):
            continue
        # Keyword action/cost lines are accounted for separately.
        if any(
            lower.startswith(prefix)
            for prefix in (
                "affinity ",
                "cycling ",
                "equip ",
                "crew ",
                "station",
                "evoke",
                "bestow ",
                "craft with ",
            )
        ):
            continue
        rows.append(line)
    return rows


def _static_lines_needing_program(record: CardRecord) -> list[str]:
    rows: list[str] = []
    for line in _printed_static_lines(record):
        lower = line.casefold().strip().rstrip(".")
        if lower in _BUILTIN_STATIC_KEYWORDS:
            continue
        keyword_parts = [
            part.strip() for part in lower.split(",") if part.strip()
        ]
        if keyword_parts and all(
            part in _BUILTIN_STATIC_KEYWORDS
            or (
                part.startswith("protection from ")
                and "protection" in _BUILTIN_STATIC_KEYWORDS
            )
            for part in keyword_parts
        ):
            continue
        if record.is_land and static_entry_state_handler(
            line,
            source_name=record.name,
        ) is not None:
            continue
        rows.append(line)
    return rows


def _mana_ability_requires_semantics(
    record: CardRecord,
    *,
    commander_identity: tuple[str, ...] = (),
    effect_text: str | None = None,
) -> bool:
    """Whether generic mana production would lose a restriction or effect."""

    if effect_text is not None and re.search(
        r"activate only if you control "
        r"(?:\d+|one|two|three|four|five|six|seven|eight|nine|ten) "
        r"or more (?:artifacts?|creatures?|lands?)",
        effect_text,
        re.IGNORECASE,
    ):
        # The authoritative activation-condition grammar validates this
        # public minimum-permanent predicate before exposing or using the
        # mana ability.
        return False
    if effect_text is not None and re.fullmatch(
        r"add (?:\{[WUBRGC0-9]+\})+(?: or (?:\{[WUBRGC0-9]+\})+)*\.",
        effect_text.strip(),
        re.IGNORECASE,
    ):
        return False
    if (
        "one mana of any color in your commander's color identity"
        in record.oracle_text.casefold()
    ):
        return False
    modes = extract_mana_modes(record, commander_identity)
    return any(
        mode.conditional
        or mode.requires_choice
        or any(
            str(effect.get("op") or "")
            not in {"damage_self", "pay_life"}
            for effect in mode.side_effects
        )
        for mode in modes
    )


def _kernel_compiles_cast_cost(record: CardRecord) -> bool:
    _, complex_symbols = mana_cost_to_vector(record.mana_cost)
    for symbol in complex_symbols:
        if symbol == "X":
            continue
        parts = symbol.split("/")
        if len(parts) == 2 and (
            all(part in "WUBRGC" and len(part) == 1 for part in parts)
            or (
                "2" in parts
                and any(
                    part in "WUBRGC" and len(part) == 1
                    for part in parts
                )
            )
        ):
            continue
        return False
    return True


def _generic_land_status(record: CardRecord) -> tuple[str, list[str]]:
    oracle = record.oracle_text.casefold()
    compiled_entry = any(
        static_entry_state_handler(line, source_name=record.name) is not None
        for line in _printed_static_lines(record)
    )
    unresolved: list[str] = []
    supported = (
        not oracle
        or "add {" in oracle
        or compiled_entry
        or "search your library for a" in oracle
    )
    if "when " in oracle or "whenever " in oracle:
        unresolved.append("triggered_ability")
    if "as " in oracle and "enters" in oracle and not supported:
        unresolved.append("replacement_effect")
    if "return a land you control" in oracle:
        unresolved.append("triggered_ability")
    return ("trusted_builtin" if supported and not unresolved else "partial"), unresolved


def _card_source_hashes(
    db: CardDatabase,
    record: CardRecord,
) -> tuple[str, str]:
    oracle_hash = hashlib.sha256(
        record.oracle_text.encode("utf-8")
    ).hexdigest()
    # Scryfall does not define an ordering among rulings that share a
    # publication date. SQLite's insertion-order tie break therefore differs
    # between a full bulk-data database and the compact CI fixture even when
    # both contain the exact same ruling set. Provenance must describe content,
    # not import order, so canonicalize every field before hashing.
    ruling_rows = sorted(
        (asdict(ruling) for ruling in db.rulings(record)),
        key=lambda row: (
            str(row["published_at"]),
            str(row["source"]),
            str(row["comment"]),
            str(row["oracle_id"]),
        ),
    )
    rulings_hash = hashlib.sha256(
        stable_json(ruling_rows).encode("utf-8")
    ).hexdigest()
    return oracle_hash, rulings_hash


def _material_effect_categories(record: CardRecord) -> list[str]:
    oracle = _material_oracle_text(record).casefold()
    categories: set[str] = set()
    abilities = _printed_activated_abilities(record)
    if record.is_instant or record.is_sorcery:
        categories.add("spell_effect")
    if any(ability.mana_ability for ability in abilities):
        categories.add("mana_ability")
    if any(not ability.mana_ability for ability in abilities):
        categories.add("activated_ability")
    if _printed_trigger_lines(record) or (
        record.is_instant or record.is_sorcery
    ) and "storm" in {
        keyword.casefold() for keyword in record.keywords
    }:
        categories.add("triggered_ability")
    if "instead" in oracle:
        categories.add("replacement_effect")
    if any(
        marker in oracle
        for marker in (
            "additional cost",
            "rather than pay",
            "without paying",
            "kicker",
            "overload",
            "convoke",
            "improvise",
            "affinity",
            "{x}",
        )
    ):
        categories.add("cost_option")
    if _printed_static_lines(record):
        categories.add("static_ability")
    keywords = {keyword.casefold() for keyword in record.keywords}
    if keywords.intersection(_MATERIAL_KEYWORDS):
        categories.add("combat_or_protection_keyword")
        categories.add("keyword_ability")
    if any(
        marker in oracle
        for marker in (
            "you may cast this card from",
            "you may cast that card",
            "you may play lands from",
            "you may play it this turn",
        )
    ):
        categories.add("zone_permission")
    return sorted(categories)


def _status_oracle_ir(
    record: CardRecord,
    capability_registry: CapabilityRegistry | None,
    capability_profile: str,
):
    return compile_oracle_card(
        record,
        capability_registry=(
            capability_registry or load_default_capability_registry()
        ),
        capability_profile=capability_profile,
    )


def _status_source_hashes(
    db: CardDatabase | None,
    record: CardRecord,
) -> tuple[str | None, str | None]:
    return _card_source_hashes(db, record) if db is not None else (None, None)


def _trusted_activated_ability_is_covered(
    ability: Any,
    trusted_programs: list[Any],
    trusted_ids: set[str],
) -> bool:
    if f"ability:{ability.ability_id}" in trusted_ids:
        return True
    if ability.crew_threshold is None:
        return False
    source_line = ability.line_index + 1
    return any(
        "activation.crew.fixed_power" in program.capability_dependencies
        and int(
            dict(program.provenance.get("source_span") or {}).get("line", -1)
        )
        == source_line
        for program in trusted_programs
    )


def card_semantic_status(
    record: CardRecord,
    registry: SemanticRegistry,
    *,
    db: CardDatabase | None = None,
    capability_registry: CapabilityRegistry | None = None,
    capability_profile: str = "commander_review",
) -> dict[str, Any]:
    oracle_ir = _status_oracle_ir(record, capability_registry, capability_profile)
    programs = registry.programs_for_oracle(record.oracle_id)
    oracle_hash, rulings_hash = _status_source_hashes(db, record)
    program_rows: list[dict[str, Any]] = []
    trusted_programs = []
    drifted_programs = []
    for program in programs:
        source_oracle_hash = program.provenance.get(
            "source_oracle_hash"
        )
        source_rulings_hash = program.provenance.get(
            "source_rulings_hash"
        )
        hash_match = (
            True
            if db is None
            else source_oracle_hash == oracle_hash
            and source_rulings_hash == rulings_hash
        )
        if program.trust_level == "trusted" and hash_match:
            trusted_programs.append(program)
        if program.trust_level == "trusted" and not hash_match:
            drifted_programs.append(program)
        program_rows.append(
            {
                "key": program.key,
                "version": program.version,
                "ability_id": program.ability_id,
                "active_zone": program.active_zone,
                "semantic_family": sorted(set(program.coverage)),
                "trust_level": program.trust_level,
                "source_hash_match": hash_match,
                "scenario_tests": list(program.tests),
            }
        )
    trust = registry.trust_for_oracle(record.oracle_id)
    if drifted_programs:
        trust = "unresolved"
    unresolved: list[str] = []
    try:
        parsed_cost(record.mana_cost)
    except ManaPlanError:
        if _kernel_compiles_cast_cost(record):
            pass
        else:
            unresolved.append("cast_cost")
    abilities = _printed_activated_abilities(record)
    unresolved.extend(
        "activated_ability"
        for ability in abilities
        if not ability.compiled_cost and not ability.mana_ability
    )
    oracle = _material_oracle_text(record).casefold()
    if record.is_land:
        generic_status, generic_unresolved = _generic_land_status(record)
        unresolved.extend(generic_unresolved)
    else:
        generic_status = "none"
    trigger_lines = _printed_trigger_lines(record)
    trusted_event_programs = [
        program
        for program in trusted_programs
        if program.event not in {"resolve", "cast"}
    ]
    if trigger_lines and len(trusted_event_programs) < len(trigger_lines):
        unresolved.append("triggered_ability")
    if "instead" in oracle and not any(
        "replacement_effect" in program.coverage
        for program in trusted_programs
    ):
        unresolved.append("replacement_effect")
    trusted_coverage = {
        value
        for program in trusted_programs
        for value in program.coverage
    }
    trusted_ids = {
        program.ability_id
        for program in trusted_programs
        if program.ability_id.startswith("ability:")
    }

    for ability in abilities:
        if ability.mana_ability:
            if (
                _mana_ability_requires_semantics(
                    record,
                    effect_text=ability.effect_text,
                )
                and f"ability:{ability.ability_id}"
                not in trusted_ids
                and "restricted_mana" not in trusted_coverage
                and "mana_side_effect" not in trusted_coverage
            ):
                unresolved.append("mana_ability")
            continue
        builtin_fetch = bool(
            re.search(
                r"search your library for (?:an?|up to one) "
                r"(?:basic land|plains|island|swamp|mountain|forest)"
                r"(?: or (?:plains|island|swamp|mountain|forest))* card, "
                r"put (?:it|that card) onto the battlefield",
                ability.effect_text,
                re.IGNORECASE,
            )
        )
        if (
            not builtin_fetch
            and not _trusted_activated_ability_is_covered(ability, trusted_programs, trusted_ids)
        ):
            unresolved.append(f"activated_ability:{ability.ability_id}")
    if (
        _static_lines_needing_program(record)
        and "static_ability" not in trusted_coverage
        and "continuous_effect" not in trusted_coverage
        and "cost_reduction" not in trusted_coverage
    ):
        unresolved.append("static_ability")
    unsupported_keywords = sorted(
        {
            keyword.casefold()
            for keyword in record.keywords
            if keyword.casefold() in _MATERIAL_KEYWORDS
            and keyword.casefold() not in _BUILTIN_STATIC_KEYWORDS
            and keyword.casefold() not in _BUILTIN_MATERIAL_KEYWORDS
            and keyword.casefold() not in trusted_coverage
        }
    )
    if (
        unsupported_keywords
        and "keyword_ability" not in trusted_coverage
        and "combat_keyword" not in trusted_coverage
        and "protection_keyword" not in trusted_coverage
    ):
        unresolved.extend(
            f"keyword:{keyword}" for keyword in unsupported_keywords
        )
    if (
        "zone_permission" in _material_effect_categories(record)
        and not trusted_coverage.intersection(
            {
                "zone_permission",
                "graveyard_cast_permission",
                "graveyard_land_permission",
                "play_without_mana_cost",
            }
        )
    ):
        unresolved.append("zone_permission")
    trusted_spell_program = any(
        program.ability_id.startswith("spell:")
        and "spell_resolution" in program.coverage
        for program in trusted_programs
    )
    if (
        "triggered_ability" in trusted_coverage
        or "delayed_trigger" in trusted_coverage
        or "storm" in trusted_coverage
        or (
            trusted_spell_program
            and (record.is_instant or record.is_sorcery)
        )
    ):
        unresolved = [
            value for value in unresolved if value != "triggered_ability"
        ]
    if (
        "replacement_effect" in trusted_coverage
        or "replacement_destination" in trusted_coverage
        or (
            trusted_spell_program
            and (record.is_instant or record.is_sorcery)
        )
    ):
        unresolved = [
            value for value in unresolved if value != "replacement_effect"
        ]
    if any(program.cost_schema for program in trusted_programs):
        unresolved = [
            value for value in unresolved if value != "cast_cost"
        ]
    if drifted_programs:
        unresolved.append("semantic_source_hash_drift")
    unresolved = sorted(set(unresolved))
    if trust == "trusted" and not unresolved:
        status = "fully_playable"
    elif record.is_land and generic_status == "trusted_builtin" and not unresolved:
        status = "fully_playable"
        trust = "trusted"
    elif not record.oracle_text.strip() and not unresolved:
        status = "fully_playable"
        trust = "trusted"
    elif (
        record.produced_mana
        and not unresolved
        and not any(
            marker in oracle
            for marker in ("when ", "whenever ", "instead", "sacrifice another")
        )
    ):
        status = "fully_playable"
        trust = "trusted"
    elif trust == "trusted":
        status = "partial" if unresolved else "fully_playable"
    elif trust == "provisional":
        status = "partial"
    else:
        status = "unresolved"
    scenario_tests = sorted(
        {
            test
            for program in programs
            for test in program.tests
        }
    )
    ignored_reasons = sorted(
        {
            str(
                program.provenance.get("intentionally_ignored_reason")
                or program.notes
            )
            for program in programs
            if program.trust_level == "intentionally_ignored"
            and (
                program.provenance.get("intentionally_ignored_reason")
                or program.notes
            )
        }
    )
    if status == "fully_playable":
        support_kind = (
            "trusted_card"
            if trusted_programs
            else "trusted_generic"
        )
    else:
        support_kind = trust
    return {
        "name": record.name,
        "oracle_id": record.oracle_id,
        "active_face": "front",
        "zones": sorted(
            {program.active_zone for program in programs}
            or (
                {"stack"}
                if record.is_instant or record.is_sorcery
                else {"battlefield", "stack"}
            )
        ),
        "semantic_family": sorted(
            {
                coverage
                for program in programs
                for coverage in program.coverage
            }
        ),
        "material_effect_categories": _material_effect_categories(
            record
        ),
        "status": status,
        "trust_level": trust,
        "support_kind": support_kind,
        "oracle_hash": oracle_hash,
        "rulings_hash": rulings_hash,
        "source_hash_match": not drifted_programs,
        "scenario_tests": scenario_tests,
        "intentionally_ignored_reasons": ignored_reasons,
        "unresolved": unresolved,
        "programs": program_rows,
        "oracle_ir": {
            "schema_version": oracle_ir.schema_version,
            "compiler_version": oracle_ir.compiler_version,
            "semantic_hash": oracle_ir.semantic_hash,
            "status": oracle_ir.status,
            "material_residual_count": len(
                oracle_ir.material_residuals
            ),
            "material_residuals": [
                {
                    "kind": residual.kind,
                    "reason": residual.reason,
                    "source_line": residual.span.line,
                    "blockers": list(residual.blockers),
                }
                for residual in oracle_ir.material_residuals
            ],
            "covered_by_trusted_card_semantics": (
                status == "fully_playable"
                and bool(trusted_programs)
            ),
            "covered_by_existing_trusted_runtime": (
                status == "fully_playable"
            ),
        },
    }


def _register_deck_programs(
    db: CardDatabase,
    deck: DeckDefinition,
    registry: SemanticRegistry,
    capability_registry: CapabilityRegistry,
    capability_profile: str,
) -> dict[str, Any]:
    return register_generated_programs(
        db,
        registry,
        (
            db.lookup(entry.name)
            for entry in deck.entries
            if entry.board in {"mainboard", "commander"}
        ),
        trust_level="provisional",
        capability_registry=capability_registry,
        capability_profile=capability_profile,
        promote_exact_runtime_handlers=True,
        promote_exact_trigger_programs=True,
        promote_exact_effect_programs=True,
        promote_exact_capability_declarations=True,
    )


def _deck_preflight_rows(
    db: CardDatabase,
    deck: DeckDefinition,
    registry: SemanticRegistry,
    capability_registry: CapabilityRegistry,
    capability_profile: str,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    cards: list[dict[str, Any]] = []
    card_programs: dict[str, Any] = {}
    for entry in deck.entries:
        if entry.board not in {"mainboard", "commander"}:
            continue
        record = db.lookup(entry.name)
        row = card_semantic_status(
            record,
            registry,
            db=db,
            capability_registry=capability_registry,
            capability_profile=capability_profile,
        )
        program = registry.card_program_for_oracle(record.oracle_id)
        if program is not None:
            card_programs[record.oracle_id] = program
            binding = bind_card_program_runtime(
                program,
                capability_registry=capability_registry,
                profile=capability_profile,
            )
            row["card_program_fingerprint"] = program.fingerprint
            row["card_program_trust_basis"] = program.trust_closure[
                "trust_basis"
            ]
            row["card_program_strict_capability_ready"] = binding[
                "strict_capability_ready"
            ]
            row["card_program_runtime_binding"] = binding
        else:
            row["card_program_fingerprint"] = None
            row["card_program_trust_basis"] = "unresolved"
            row["card_program_strict_capability_ready"] = False
            row["card_program_runtime_binding"] = None
        row["quantity"] = entry.quantity
        cards.append(row)
    return cards, card_programs


def semantic_preflight(
    db: CardDatabase,
    deck_or_source: DeckDefinition | str | Path,
    *,
    registry: SemanticRegistry | None = None,
    cache_dir: str | Path | None = None,
    force_refresh: bool = False,
    capability_profile: str = "commander_review",
) -> dict[str, Any]:
    registry = registry or SemanticRegistry()
    capability_registry = load_default_capability_registry()
    deck = (
        deck_or_source
        if isinstance(deck_or_source, DeckDefinition)
        else DeckLoader(db, cache_dir=cache_dir).load(
            deck_or_source, force_refresh=force_refresh
        )
    )
    generation = _register_deck_programs(
        db, deck, registry, capability_registry, capability_profile
    )
    cards, card_programs = _deck_preflight_rows(
        db, deck, registry, capability_registry, capability_profile
    )
    quantities = Counter()
    for row in cards:
        quantities[row["status"]] += int(row["quantity"])
    unresolved_costs = [
        row["name"] for row in cards if "cast_cost" in row["unresolved"]
    ]
    unresolved_abilities = [
        row["name"] for row in cards if "activated_ability" in row["unresolved"]
    ]
    unresolved_triggers = [
        row["name"] for row in cards if "triggered_ability" in row["unresolved"]
    ]
    unresolved_replacements = [
        row["name"] for row in cards if "replacement_effect" in row["unresolved"]
    ]
    unresolved_cards = [
        row for row in cards if row["status"] == "unresolved"
    ]
    partial_cards = [row for row in cards if row["status"] == "partial"]
    drifted_cards = [
        row["name"]
        for row in cards
        if not row["source_hash_match"]
    ]
    ignored_without_reason = [
        row["name"]
        for row in cards
        if row["trust_level"] == "intentionally_ignored"
        and not row["intentionally_ignored_reasons"]
    ]
    oracle_ir_statuses = Counter(
        row["oracle_ir"]["status"] for row in cards
    )
    uncovered_oracle_residual_cards = [
        row["name"]
        for row in cards
        if row["oracle_ir"]["material_residual_count"]
        and not row["oracle_ir"][
            "covered_by_existing_trusted_runtime"
        ]
    ]
    match_trust_closure = compute_match_trust_closure(
        card_programs.values(),
        registry=capability_registry,
        profile=capability_profile,
    )
    missing_card_programs = sorted(
        row["name"]
        for row in cards
        if row["card_program_fingerprint"] is None
    )
    strict_binding_blockers = list(match_trust_closure["blockers"])
    strict_binding_blockers.extend(
        f"card_program:missing:{name}" for name in missing_card_programs
    )
    for row in cards:
        binding = row["card_program_runtime_binding"]
        if binding is None:
            continue
        strict_binding_blockers.extend(
            f"card:{row['name']}:{blocker}"
            for blocker in binding["blockers"]
        )
    strict_binding_blockers = sorted(set(strict_binding_blockers))
    compatibility_ready = (
        not unresolved_cards
        and not partial_cards
        and not drifted_cards
        and not ignored_without_reason and match_trust_closure["compatible_ready"]
        and all(
            row["card_program_runtime_binding"]["compatible_ready"]
            for row in cards
            if row["card_program_runtime_binding"] is not None
        )
    )
    return {
        "schema_version": PREFLIGHT_SCHEMA_VERSION,
        "deck": deck.name,
        "source": deck.source,
        "commander": list(deck.commanders),
        "deck_fingerprint": deck_list_fingerprint(deck),
        "deck_list_fingerprint": deck_list_fingerprint(deck),
        "deck_source_fingerprint": deck_source_fingerprint(deck),
        "capability_profile": capability_profile,
        "capability_registry_fingerprint": capability_registry.fingerprint,
        "capability_evidence_fingerprint": (
            capability_registry.evidence_fingerprint
        ),
        "card_data_metadata": db.metadata(),
        "total_cards": deck.total_cards(),
        "fully_playable_cards": quantities["fully_playable"],
        "partial_cards": quantities["partial"],
        "unresolved_cards": quantities["unresolved"],
        "unresolved_cast_costs": sorted(set(unresolved_costs)),
        "unresolved_activated_abilities": sorted(set(unresolved_abilities)),
        "unresolved_triggered_abilities": sorted(set(unresolved_triggers)),
        "unresolved_replacement_effects": sorted(set(unresolved_replacements)),
        "expected_arbiter_calls": sum(
            int(row["quantity"]) for row in unresolved_cards + partial_cards
        ),
        "deck_review_eligible_possible": not unresolved_cards
        and not partial_cards
        and not drifted_cards
        and not ignored_without_reason,
        "trusted_only_ready": (
            not unresolved_cards
            and not partial_cards
            and not drifted_cards
            and not ignored_without_reason
            and not strict_binding_blockers
            and match_trust_closure["strict_capability_ready"]
        ),
        "compatibility_ready": compatibility_ready,
        "strict_binding_blockers": strict_binding_blockers,
        "match_trust_closure": match_trust_closure,
        "source_hash_drift_cards": sorted(set(drifted_cards)),
        "intentionally_ignored_without_reason": sorted(
            set(ignored_without_reason)
        ),
        "cards": cards,
        "semantic_packs": list(registry.loaded_packs),
        "oracle_compiler_version": ORACLE_COMPILER_VERSION,
        "oracle_ir_status_counts": dict(
            sorted(oracle_ir_statuses.items())
        ),
        "oracle_ir_uncovered_residual_cards": sorted(
            set(uncovered_oracle_residual_cards)
        ),
        "generated_semantics": generation,
        "generic_oracle_compiler_ready": all(
            row["oracle_ir"]["status"] == "exact"
            for row in cards
        ),
        "oracle_residual_gate_pass": not (
            uncovered_oracle_residual_cards
        ),
    }
