from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from enum import Enum
import re
from typing import TYPE_CHECKING, Protocol

from .ability_fragments import (
    AbilityFragmentError,
    PARTNER_WITH_FRAGMENT_HANDLER_ID,
    PartnerWithSpec,
    ability_fragment_from_dict,
)
from .characteristic_evaluation import type_parts

if TYPE_CHECKING:
    from .carddb import CardDatabase, CardRecord
    from .deck import DeckDefinition
    from .semantics import SemanticProgram


COMMANDER_PAIRING_TEMPLATE_ID = "commander-pairing-eligibility-v1"
COMMANDER_PAIRING_EVENT = "game.setup"
COMMANDER_PAIRING_COVERAGE = "commander_pairing_eligibility"
PARTNER_WITH_SEARCH_CAPABILITY_ID = "library.search.partner_with_named_to_hand"
PARTNER_WITH_SEARCH_MECHANIC_ID = "partner-with-entry-search"
PARTNER_WITH_SEARCH_TEMPLATE_ID = "partner-with-entry-search-v1"


class CommanderPairingKind(str, Enum):
    PARTNER = "partner"
    PARTNER_WITH = "partner with"
    CHOOSE_A_BACKGROUND = "choose a background"
    DOCTORS_COMPANION = "doctor's companion"


PAIRING_CAPABILITY_BY_KIND = {
    CommanderPairingKind.PARTNER: "format.commander.pairing.partner",
    CommanderPairingKind.PARTNER_WITH: (
        "format.commander.pairing.partner_with"
    ),
    CommanderPairingKind.CHOOSE_A_BACKGROUND: (
        "format.commander.pairing.choose_background"
    ),
    CommanderPairingKind.DOCTORS_COMPANION: (
        "format.commander.pairing.doctors_companion"
    ),
}


class CommanderPairingError(ValueError):
    """A proposed two-commander designation is not compiler-certified."""


class PairingProgramRegistry(Protocol):
    def programs_for_oracle(
        self,
        oracle_id: str,
        *,
        active_zone: str | None = None,
        event: str | None = None,
    ) -> list["SemanticProgram"]: ...

    def card_program_for_oracle(self, oracle_id: str) -> object | None: ...


@dataclass(frozen=True, slots=True)
class CommanderPairingDeclaration:
    kind: CommanderPairingKind
    capability_id: str
    program_key: str
    partner_name: str | None = None


def partner_with_spec_for_material_line(
    material_line: str,
) -> PartnerWithSpec | None:
    """Parse one complete Partner with declaration without reminder prose."""

    match = re.fullmatch(
        (
            r"Partner with (?P<partner>[A-Za-z0-9]"
            r"[A-Za-z0-9 ,&'’-]*[A-Za-z0-9])\.?"
        ),
        material_line.strip(),
        re.IGNORECASE,
    )
    if match is None:
        return None
    partner_name = match.group("partner").strip()
    if partner_name.casefold() in {"itself", "knight"}:
        return None
    try:
        return PartnerWithSpec(partner_name)
    except AbilityFragmentError:
        return None


def pairing_kind_for_material_line(
    material_line: str,
) -> CommanderPairingKind | None:
    """Recognize one exact supported Commander pairing declaration."""

    normalized = material_line.strip().rstrip(".").casefold()
    if partner_with_spec_for_material_line(material_line) is not None:
        return CommanderPairingKind.PARTNER_WITH
    try:
        return CommanderPairingKind(normalized)
    except ValueError:
        return None


def _program_pairing_kind(
    program: "SemanticProgram",
) -> CommanderPairingKind | None:
    matches = [
        kind
        for kind, capability_id in PAIRING_CAPABILITY_BY_KIND.items()
        if program.capability_dependencies == [capability_id]
    ]
    if len(matches) != 1:
        return None
    kind = matches[0]
    closure = program.capability_closure
    if (
        program.trust_level != "trusted"
        or program.requires_arbiter
        or not program.ability_id.startswith("static:")
        or program.active_zone != "all"
        or program.event != COMMANDER_PAIRING_EVENT
        or program.provenance.get("template_id")
        != COMMANDER_PAIRING_TEMPLATE_ID
        or program.effects
        or program.target_schema is not None
        or program.cost_schema is not None
        or program.event_condition is not None
        or COMMANDER_PAIRING_COVERAGE not in program.coverage
        or kind.value not in program.coverage
        or not isinstance(closure, dict)
        or closure.get("requested") != [PAIRING_CAPABILITY_BY_KIND[kind]]
        or closure.get("trusted") is not True
        or closure.get("blockers") != []
    ):
        return None
    if kind is CommanderPairingKind.PARTNER_WITH:
        if len(program.handlers) != 1:
            return None
        descriptor = program.handlers[0]
        if (
            not isinstance(descriptor, dict)
            or set(descriptor)
            != {"handler_id", "schema_version", "event", "fragment"}
            or descriptor.get("handler_id")
            != PARTNER_WITH_FRAGMENT_HANDLER_ID
            or descriptor.get("schema_version") != 1
            or descriptor.get("event") != COMMANDER_PAIRING_EVENT
        ):
            return None
        try:
            fragment = ability_fragment_from_dict(descriptor["fragment"])
        except (AbilityFragmentError, TypeError):
            return None
        if not isinstance(fragment, PartnerWithSpec):
            return None
    elif program.handlers:
        return None
    return kind


def _program_partner_name(
    program: "SemanticProgram",
    kind: CommanderPairingKind,
) -> str | None:
    if kind is not CommanderPairingKind.PARTNER_WITH:
        return None
    fragment = ability_fragment_from_dict(program.handlers[0]["fragment"])
    assert isinstance(fragment, PartnerWithSpec)
    return fragment.partner_name


def commander_pairing_declaration(
    card_db: "CardDatabase",
    registry: PairingProgramRegistry | None,
    card: "CardRecord",
) -> CommanderPairingDeclaration | None:
    """Read one current typed setup declaration without reparsing Oracle text."""

    if registry is None:
        return None
    # Imported lazily because the compiler also imports the declaration
    # constants from this module while CardProgram adapters are initializing.
    from .card_programs.validation import (
        canonical_program_fingerprint,
        program_source_is_current,
    )

    candidates: list[tuple[SemanticProgram, CommanderPairingKind]] = []
    for program in registry.programs_for_oracle(
        card.oracle_id,
        active_zone="all",
        event=COMMANDER_PAIRING_EVENT,
    ):
        kind = _program_pairing_kind(program)
        if (
            kind is None
            or program.oracle_id != card.oracle_id
            or canonical_program_fingerprint(registry, program) is None
            or not program_source_is_current(card_db, program)
        ):
            continue
        candidates.append((program, kind))
    if len(candidates) != 1:
        return None
    program, kind = candidates[0]
    return CommanderPairingDeclaration(
        kind=kind,
        capability_id=PAIRING_CAPABILITY_BY_KIND[kind],
        program_key=program.key,
        partner_name=_program_partner_name(program, kind),
    )


def _is_legendary(card: "CardRecord") -> bool:
    _, _, supertypes = type_parts(card.type_line)
    return "legendary" in supertypes


def _is_background(card: "CardRecord") -> bool:
    card_types, subtypes, supertypes = type_parts(card.type_line)
    return bool(
        "legendary" in supertypes
        and "enchantment" in card_types
        and "background" in subtypes
    )


def _is_legendary_creature(card: "CardRecord") -> bool:
    card_types, _, supertypes = type_parts(card.type_line)
    return "legendary" in supertypes and "creature" in card_types


def _is_doctor(card: "CardRecord") -> bool:
    card_types, subtypes, supertypes = type_parts(card.type_line)
    return bool(
        "legendary" in supertypes
        and "creature" in card_types
        and subtypes == {"time lord", "doctor"}
    )


def validate_commander_pair(
    card_db: "CardDatabase",
    registry: PairingProgramRegistry | None,
    commanders: tuple["CardRecord", "CardRecord"],
) -> tuple[CommanderPairingDeclaration | None, ...]:
    """Validate CR 702.124h/j/k/m through one shared typed setup owner."""

    first, second = commanders
    if first.oracle_id == second.oracle_id:
        raise CommanderPairingError(
            "Two commanders must designate distinct Commander-legal cards"
        )
    if not _is_legendary(first) or not _is_legendary(second):
        raise CommanderPairingError(
            "Both cards in a two-commander designation must be legendary"
        )

    declarations = (
        commander_pairing_declaration(card_db, registry, first),
        commander_pairing_declaration(card_db, registry, second),
    )
    kinds = tuple(
        declaration.kind if declaration is not None else None
        for declaration in declarations
    )
    if kinds == (
        CommanderPairingKind.PARTNER,
        CommanderPairingKind.PARTNER,
    ) and all(_is_legendary_creature(card) for card in commanders):
        return declarations
    if kinds == (
        CommanderPairingKind.PARTNER_WITH,
        CommanderPairingKind.PARTNER_WITH,
    ) and all(_is_legendary_creature(card) for card in commanders):
        try:
            named = tuple(
                card_db.lookup(declaration.partner_name, fuzzy=False)
                for declaration in declarations
                if declaration is not None
                and declaration.partner_name is not None
            )
        except KeyError:
            named = ()
        if (
            len(named) == 2
            and named[0].oracle_id == second.oracle_id
            and named[1].oracle_id == first.oracle_id
        ):
            return declarations
    if (
        kinds[0] == CommanderPairingKind.CHOOSE_A_BACKGROUND
        and _is_background(second)
    ) or (
        kinds[1] == CommanderPairingKind.CHOOSE_A_BACKGROUND
        and _is_background(first)
    ):
        return declarations
    if (
        kinds[0] == CommanderPairingKind.DOCTORS_COMPANION
        and _is_legendary_creature(first)
        and _is_doctor(second)
    ) or (
        kinds[1] == CommanderPairingKind.DOCTORS_COMPANION
        and _is_legendary_creature(second)
        and _is_doctor(first)
    ):
        return declarations
    raise CommanderPairingError(
        "Two commanders require matching typed Partner, Partner with, Choose "
        "a Background, or Doctor's companion setup declarations"
    )


def validated_commander_counts(
    card_db: "CardDatabase",
    registry: PairingProgramRegistry | None,
    deck: "DeckDefinition",
) -> dict[str, int]:
    """Validate one deck's physical and typed Commander designations."""

    board_names = tuple(
        entry.name
        for entry in deck.entries
        if entry.board == "commander"
        for _ in range(entry.quantity)
    )
    commander_names = tuple(deck.commanders) or board_names
    if len(commander_names) > 2:
        raise ValueError(
            "Commander setup permits at most two designated commanders"
        )
    commander_records = tuple(
        card_db.lookup(name) for name in commander_names
    )
    commander_counts = Counter(record.name for record in commander_records)
    available_counts = Counter(
        card_db.lookup(entry.name).name
        for entry in deck.entries
        if entry.board in {"mainboard", "commander"}
        for _ in range(entry.quantity)
    )
    if commander_counts - available_counts:
        raise ValueError(
            "Every designated commander must exist in the submitted deck"
        )
    if deck.commanders:
        board_counts = Counter(card_db.lookup(name).name for name in board_names)
        if board_counts - commander_counts:
            raise ValueError(
                "Commander-board entries must match the designated commander list"
            )
    if len(commander_records) == 2:
        validate_commander_pair(card_db, registry, commander_records)
    return dict(commander_counts)


__all__ = [
    "COMMANDER_PAIRING_COVERAGE",
    "COMMANDER_PAIRING_EVENT",
    "COMMANDER_PAIRING_TEMPLATE_ID",
    "CommanderPairingDeclaration",
    "CommanderPairingError",
    "CommanderPairingKind",
    "PAIRING_CAPABILITY_BY_KIND",
    "PARTNER_WITH_SEARCH_CAPABILITY_ID",
    "PARTNER_WITH_SEARCH_MECHANIC_ID",
    "PARTNER_WITH_SEARCH_TEMPLATE_ID",
    "commander_pairing_declaration",
    "pairing_kind_for_material_line",
    "partner_with_spec_for_material_line",
    "validate_commander_pair",
    "validated_commander_counts",
]
