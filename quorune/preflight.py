from __future__ import annotations

from collections import Counter
from dataclasses import asdict
import hashlib
from pathlib import Path
import re
from typing import Any

from .carddb import CardDatabase, CardRecord
from .compiler.chosen_basic_land_entry import basic_land_entry_choice_handler
from .compiler.entry_state_templates import static_entry_state_handler
from .compiler.preflight_keyword_coverage import static_lines_without_keyword_owners
from .compiler.program_generation import rulings_source_hash
from .card_programs.adapters import card_program_source_diagnostics
from .card_programs.binding import bind_card_program_runtime
from .card_programs.trust import compute_match_trust_closure
from .cycling_abilities import PRINTED_CYCLING_KEYWORD_PATTERN
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
from .util import mana_cost_to_vector

from .rules.capabilities import (
    CapabilityRegistry,
    load_default_capability_registry,
)
from .rules.evidence import _capability_validation_snapshot


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


def _without_parenthetical_reminder(text: str, *, preserve_offsets: bool = False) -> str:
    """Remove balanced parenthetical reminder text from Oracle text.

    Reminder text frequently contains colons, trigger words, and token mana
    abilities that are not separate abilities of the printed card. Treating
    those fragments as source abilities makes preflight over-count coverage
    and makes the activated-ability parser expose impossible actions.
    """

    result: list[str] = []
    depth = 0
    for character in text:
        reminder = character == "(" or depth > 0
        if character == "(":
            depth += 1
        elif character == ")" and depth:
            depth -= 1
        if not reminder:
            result.append(character)
        elif preserve_offsets:
            result.append("\n" if character == "\n" else " ")
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
    from .compiler.activated_ability_catalog import specialize_compiled_activation_costs
    from .compiler.activated_costs import parse_compiled_activated_abilities
    return specialize_compiled_activation_costs(parse_compiled_activated_abilities(
        card_name=record.name,
        oracle_text=_without_quoted_granted_text(
            _material_oracle_text(record)
        ),
        keywords=record.keywords,
    ),source_name=record.name)


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
    if any(symbol.endswith("/P") for symbol in complex_symbols):
        from .rules.casting.face_queries import printed_phyrexian_cast_options

        return bool(printed_phyrexian_cast_options(record.mana_cost, x_value=0, commander_tax=0))
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


def _generic_land_status(record: CardRecord, trusted_programs=(), source_lines=()) -> tuple[str, list[str]]:
    from .color_set_mana_abilities import COMMANDER_TYPE_SCRY_MANA_HANDLER_ID

    spends_mana_with_delayed_trigger = any(
        handler.get("handler_id") == COMMANDER_TYPE_SCRY_MANA_HANDLER_ID
        for program in trusted_programs for handler in program.handlers
    )
    covered_trigger_lines = source_lines if spends_mana_with_delayed_trigger else ()
    oracle = record.oracle_text.casefold()
    compiled_entry = any(
        static_entry_state_handler(line, source_name=record.name) is not None
        for line in _printed_static_lines(record)
    ) or any(basic_land_entry_choice_handler(line) is not None for line in source_lines)
    unresolved: list[str] = []
    supported = (
        not oracle
        or "add {" in oracle
        or compiled_entry
        or "search your library for a" in oracle
    )
    uncovered_material = "\n".join(
        line.casefold() for line in _material_oracle_text(record).splitlines()
        if line.strip() not in covered_trigger_lines
    )
    if any(marker in uncovered_material for marker in ("when ", "whenever ", "return a land you control")):
        unresolved.append("triggered_ability")
    if "as " in oracle and "enters" in oracle and not supported:
        unresolved.append("replacement_effect")
    return ("trusted_builtin" if supported and not unresolved else "partial"), unresolved


def _card_source_hashes(
    db: CardDatabase,
    record: CardRecord,
) -> tuple[str, str]:
    oracle_hash = hashlib.sha256(record.oracle_text.encode("utf-8")).hexdigest()
    return oracle_hash, rulings_source_hash(db, record)


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
    bound_activation_lines: set[str],
) -> bool:
    if f"ability:{ability.ability_id}" in trusted_ids or ability.oracle_line in bound_activation_lines:
        return True
    from .rules.equip_preflight_coverage import trusted_equip_keyword_covers
    if trusted_equip_keyword_covers(ability,trusted_programs):return True
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


def _unique_bound_activation_lines(abilities, activation_lines):
    # Legacy activation IDs use flattened line numbers across faces. A unique
    # printed activation can instead be matched to its strictly bound native span.
    printed_lines = Counter(ability.oracle_line for ability in abilities)
    # The legacy parser excludes separately compiled quoted granted abilities.
    activation_lines = {_without_quoted_granted_text(line).strip() for line in activation_lines}
    return {
        ability.oracle_line for ability in abilities
        if printed_lines[ability.oracle_line] == 1
        and ability.oracle_line in activation_lines
    }


def _trusted_generated_source_coverage(record, oracle_ir, programs, capability_registry, profile):
    """Recognize only exact current source spans with bound native execution."""
    from .card_programs.binding import bind_semantic_program_runtime
    from .card_programs.model import _ability_face_id

    ranges={face.face_id:[] for face in oracle_ir.faces}
    covered_occurrences=set()
    all_occurrences=set()
    activation_lines=set()
    for face in oracle_ir.faces:
        all_occurrences.update((face.face_id,line.strip()) for line in
            _without_parenthetical_reminder(face.oracle_text).splitlines() if line.strip())
        for program in programs:
            if (program.provenance.get('authored_by')!=oracle_ir.compiler_version
                    or _ability_face_id(program)!=face.face_id):
                continue
            node=next((node for node in face.nodes if node.exact and node.lowerable
                and not node.residual_ids and asdict(node.span)==program.provenance.get('source_span')
                and node.template_id==program.provenance.get('template_id')),None)
            if node is None or not bind_semantic_program_runtime(program,
                    capability_registry=capability_registry,profile=profile)['strict']:
                continue
            ranges[face.face_id].append((node.span.start,node.span.end))
            text=face.oracle_text[node.span.start:node.span.end]
            source_lines={line.strip() for line in
                _without_parenthetical_reminder(text).splitlines() if line.strip()}
            covered_occurrences.update((face.face_id,line) for line in source_lines)
            if node.kind == "activated_ability":
                activation_lines.update(source_lines)
    lines={line for _,line in all_occurrences if
        all((face,line) in covered_occurrences for face,value in all_occurrences if value==line)}
    keywords=set()
    material_text = {face.face_id: _without_parenthetical_reminder(face.oracle_text, preserve_offsets=True)
                     for face in oracle_ir.faces}
    for keyword in record.keywords:
        if keyword.casefold() == "cycling":
            pattern = PRINTED_CYCLING_KEYWORD_PATTERN
        elif keyword.casefold() == "transform":
            pattern = re.compile(r"\btransform(?:s|ed)?\b", re.I)
        else:
            pattern = re.compile(r'\b'+re.escape(keyword)+r's?\b', re.I)
        matches=[(face.face_id,match) for face in oracle_ir.faces
            for match in pattern.finditer(face.oracle_text)]
        if keyword.casefold() == "transform":
            material_matches = [(face.face_id, match) for face in oracle_ir.faces
                                for match in pattern.finditer(material_text[face.face_id])]
            # A bound Disturb instruction can describe its back-face cast in reminder text.
            matches = material_matches or matches
        if matches and all(any(start<=match.start() and match.end()<=end
                for start,end in ranges[face_id]) for face_id,match in matches):
            keywords.add(keyword.casefold())
    return lines, keywords, lines & activation_lines


def _unresolved_source_materials(record, oracle_ir, trusted_programs, capability_registry, capability_profile):
    """Keep source-bound native and reviewed material coverage together."""
    unresolved=[]
    legacy_coverage={value for program in trusted_programs
        if not str(program.provenance.get('authored_by','')).startswith('oracle-ir-v')
        for value in program.coverage}
    source_lines,source_keywords,activation_lines=_trusted_generated_source_coverage(record,oracle_ir,trusted_programs,
        capability_registry or load_default_capability_registry(),capability_profile)
    replacement_lines={line.strip() for line in _material_oracle_text(record).splitlines()
        if 'instead' in line.casefold()}
    if replacement_lines-source_lines and 'replacement_effect' not in legacy_coverage:
        unresolved.append('replacement_effect')
    if (
        static_lines_without_keyword_owners([line for line in _static_lines_needing_program(record)
            if line.strip() not in source_lines],record=record,trusted_programs=trusted_programs)
        and not legacy_coverage.intersection({
            "static_ability", "continuous_effect", "runtime_static_handler", "cost_reduction",
        })
    ):
        unresolved.append("static_ability")
    unsupported_keywords = sorted(
        {
            keyword.casefold()
            for keyword in record.keywords
            if keyword.casefold() in _MATERIAL_KEYWORDS
            and keyword.casefold() not in _BUILTIN_STATIC_KEYWORDS
            and keyword.casefold() not in _BUILTIN_MATERIAL_KEYWORDS
            and keyword.casefold() not in legacy_coverage
            and keyword.casefold() not in source_keywords
        }
    )
    if (
        unsupported_keywords
        and "keyword_ability" not in legacy_coverage
        and "combat_keyword" not in legacy_coverage
        and "protection_keyword" not in legacy_coverage
    ):
        unresolved.extend(
            f"keyword:{keyword}" for keyword in unsupported_keywords
        )
    if (
        "zone_permission" in _material_effect_categories(record)
        and not legacy_coverage.intersection(
            {
                "zone_permission",
                "graveyard_cast_permission",
                "graveyard_land_permission",
                "play_without_mana_cost",
            }
        )
        and any(line.strip() not in source_lines for line in _material_oracle_text(record).splitlines()
            if any(marker in line.casefold() for marker in ('you may cast this card from',
                'you may cast that card','you may play lands from','you may play it this turn')))
    ):
        unresolved.append("zone_permission")
    return tuple(unresolved), frozenset(legacy_coverage), source_lines, activation_lines


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
    oracle = _material_oracle_text(record).casefold()
    trigger_lines = _printed_trigger_lines(record)
    trusted_event_programs = [
        program
        for program in trusted_programs
        if program.event not in {"resolve", "cast"}
    ]
    if trigger_lines and len(trusted_event_programs) < len(trigger_lines):
        unresolved.append("triggered_ability")
    trusted_coverage = {
        value
        for program in trusted_programs
        for value in program.coverage
    }
    material_unresolved,legacy_coverage,source_lines,activation_lines=_unresolved_source_materials(
        record,oracle_ir,trusted_programs,capability_registry,capability_profile)
    unresolved.extend(material_unresolved)
    generic_status = "none"
    if record.is_land:
        generic_status, generic_unresolved = _generic_land_status(record, trusted_programs, source_lines)
        unresolved.extend(generic_unresolved)
    trusted_ids = {
        program.ability_id
        for program in trusted_programs
        if program.ability_id.startswith("ability:")
    }
    bound_activation_lines = _unique_bound_activation_lines(abilities, activation_lines)
    unresolved.extend(
        "activated_ability"
        for ability in abilities
        if not ability.compiled_cost and not ability.mana_ability
        and ability.oracle_line not in bound_activation_lines
    )

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
            and not _trusted_activated_ability_is_covered(ability, trusted_programs, trusted_ids, bound_activation_lines)
        ):
            unresolved.append(f"activated_ability:{ability.ability_id}")
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
        "replacement_effect" in legacy_coverage
        or "replacement_destination" in legacy_coverage
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
    source_diagnostics: dict[str, dict[str, Any]] | None,
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
        if source_diagnostics is not None:
            diagnostics = card_program_source_diagnostics(
                record, registry.programs_for_oracle(record.oracle_id),
                oracle_hash=row["oracle_hash"], rulings_hash=row["rulings_hash"],
            )
            for diagnostic in diagnostics:
                source_diagnostics[diagnostic["diagnostic_id"]] = diagnostic
            if diagnostics:
                row["source_hash_match"] = False
        program = (
            registry.card_program_for_oracle(record.oracle_id)
            if source_diagnostics is None or row["source_hash_match"]
            else None
        )
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


def _deck_strict_binding_blockers(
    cards: list[dict[str, Any]], match_blockers: list[str]
) -> list[str]:
    blockers = set(match_blockers)
    for row in cards:
        name = row["name"]
        if row["card_program_fingerprint"] is None:
            blockers.add(f"card_program:missing:{name}")
        binding = row["card_program_runtime_binding"]
        if binding is not None:
            blockers.update(f"card:{name}:{blocker}" for blocker in binding["blockers"])
    return sorted(blockers)


@_capability_validation_snapshot()
def semantic_preflight(
    db: CardDatabase,
    deck_or_source: DeckDefinition | str | Path,
    *,
    registry: SemanticRegistry | None = None,
    cache_dir: str | Path | None = None,
    force_refresh: bool = False,
    capability_profile: str = "commander_review",
    collect_source_diagnostics: bool = False,
    checked_card_programs: dict[str, Any] | None = None,
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
    source_diagnostics: dict[str, dict[str, Any]] | None = {} if collect_source_diagnostics else None
    cards, card_programs = _deck_preflight_rows(
        db, deck, registry, capability_registry, capability_profile, source_diagnostics,
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
    strict_binding_blockers = _deck_strict_binding_blockers(
        cards, match_trust_closure["blockers"]
    )
    deck_review_eligible = not (
        unresolved_cards or partial_cards or drifted_cards or ignored_without_reason
    )
    total_cards = deck.total_cards()
    card_rules_ready = (
        0 < total_cards == quantities["fully_playable"]
        and deck_review_eligible
        and not uncovered_oracle_residual_cards
        and all(row["card_program_strict_capability_ready"] for row in cards)
    )
    compatibility_ready = (
        deck_review_eligible
        and match_trust_closure["compatible_ready"]
        and all(
            row["card_program_runtime_binding"]["compatible_ready"]
            for row in cards
            if row["card_program_runtime_binding"] is not None
        )
    )
    if checked_card_programs is not None:
        checked_card_programs.update(card_programs)
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
        "total_cards": total_cards,
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
        "deck_review_eligible_possible": deck_review_eligible,
        "card_rules_ready": card_rules_ready,
        "trusted_only_ready": (
            deck_review_eligible
            and not strict_binding_blockers
            and match_trust_closure["strict_capability_ready"]
        ),
        "compatibility_ready": compatibility_ready,
        "strict_binding_blockers": strict_binding_blockers,
        "match_trust_closure": match_trust_closure,
        "source_hash_drift_cards": sorted(set(drifted_cards)),
        **({"data_integrity_blockers": sorted(
            source_diagnostics.values(), key=lambda issue: (issue["card"].casefold(), issue["kind"]),
        )} if source_diagnostics is not None else {}),
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
