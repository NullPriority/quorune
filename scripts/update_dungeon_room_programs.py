from __future__ import annotations

"""Compile pinned room bodies into the ordinary semantic pack format."""

import argparse
from dataclasses import replace
from hashlib import sha256
import json
from pathlib import Path
import re
import sys
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from quorune.card_programs import bind_card_program_runtime
from quorune.carddb import CardDatabase, CardRecord
from quorune.dungeon_catalog import DUNGEON_CATALOG, DungeonDefinition
from quorune.dungeon_progress import DUNGEON_ROOM_EVENT, DUNGEON_VENTURE_CAPABILITY, DungeonRoomRef
from quorune.oracle_ir import generated_programs
from quorune.rules.capabilities import load_default_capability_registry
from quorune.semantics import SEMANTIC_SCHEMA_VERSION, SemanticRegistry
from scripts.build_test_database import build_fixture_database


FIXTURE = ROOT / "tests/fixtures/dungeon-cards.json"
OUTPUT = ROOT / "quorune/semantic_packs/compiled-dungeon-rooms.json"
PROFILE = "commander_review"
_EDGES = re.compile(r" \(Leads to: ([^)]+)\)$")


def printed_rooms(record: CardRecord, definition: DungeonDefinition):
    """Return checked room bodies with spans in the unmodified pinned source."""
    if sha256(record.oracle_text.encode("utf-8")).hexdigest() != definition.source_oracle_hash:
        raise ValueError(f"Dungeon source hash changed for {definition.source_name}")
    offset = 0
    rows = []
    for number, line in enumerate(record.oracle_text.splitlines(keepends=True), 1):
        printed = line.rstrip("\r\n")
        if printed == "//":
            break
        if " — " in printed:
            label, body = printed.split(" — ", 1)
            suffix = _EDGES.search(body)
            edges = tuple(suffix[1].split(", ")) if suffix is not None else ()
            material = body[:suffix.start()] if suffix is not None else body
            start = offset + len(label) + len(" — ")
            rows.append((label, material, edges, {"start": start, "end": start + len(material), "line": number}))
        offset += len(line)
    expected = [(room.label, tuple(definition.rooms[index].label for index in room.exits))
                for room in definition.rooms]
    if [(label, edges) for label, _, edges, _ in rows] != expected:
        raise ValueError(f"Printed room graph changed for {definition.source_name}")
    return rows


def _compile_room(db, record, room, label, body, source_span, capabilities):
    # Compile the isolated effect using the normal spell-body owner, then bind
    # that exact result to its real dungeon source and original Oracle span.
    body_record = replace(record, oracle_text=body, faces=(), type_line="Instant", layout="normal",
                          mana_cost="{0}", mana_value=0)
    programs = generated_programs(db, body_record, trust_level="trusted",
                                  capability_registry=capabilities, capability_profile=PROFILE)
    if len(programs) != 1 or programs[0].event != "resolve" or not programs[0].effects:
        raise ValueError(f"Room body does not lower to one executable instruction list for {label}")
    program = programs[0]
    if program.handlers or program.cost_schema or program.requires_arbiter:
        raise ValueError(f"Room body requires unsupported admission for {label}")
    provenance = {**program.provenance, "source_oracle_hash": sha256(record.oracle_text.encode("utf-8")).hexdigest(),
                  "source_span": source_span, "dungeon_room": room.to_dict()}
    if "source_spans" in provenance or "complete_card_program_admission" in provenance:
        raise ValueError(f"Room body requires an independent composition envelope for {label}")
    return replace(program, key=room.program_key, ability_id=f"room:front:{room.index}",
                   label=label, active_zone="command", event=DUNGEON_ROOM_EVENT,
                   destination=None, provenance=provenance)


def build_room_pack():
    capabilities = load_default_capability_registry()
    registry = SemanticRegistry(include_builtin_packs=False)
    with TemporaryDirectory(prefix="quorune-dungeon-rooms-") as work:
        db_path = Path(work) / "rooms.sqlite3"
        build_fixture_database(FIXTURE, db_path)
        with CardDatabase(db_path) as db:
            for definition in DUNGEON_CATALOG.values():
                source = db.lookup(definition.source_name, fuzzy=False)
                for index, (label, body, _, span) in enumerate(printed_rooms(source, definition)):
                    room = DungeonRoomRef(definition.dungeon, index)
                    registry.put(_compile_room(db, source, room, label, body, span, capabilities))
            for card in registry.card_programs():
                binding = bind_card_program_runtime(card, capability_registry=capabilities, profile=PROFILE)
                if not binding["strict_capability_ready"]:
                    raise ValueError(f"Dungeon program binding failed for {card.name}: {binding['blockers']}")
    closure = capabilities.closure((DUNGEON_VENTURE_CAPABILITY,), profile=PROFILE)
    required = {capability for program in registry.programs() for capability in program.capability_dependencies}
    if not closure.trusted or not required.issubset(closure.reachable):
        raise ValueError("The Venture capability must close over every generated room body")
    return {"schema_version": SEMANTIC_SCHEMA_VERSION, "name": "compiled-dungeon-rooms",
            "programs": [program.to_dict() for program in registry.programs()]}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true")
    mode.add_argument("--write", action="store_true")
    options = parser.parse_args()
    expected = json.dumps(build_room_pack(), ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if options.write:
        OUTPUT.write_text(expected, encoding="utf-8")
    elif not OUTPUT.is_file() or OUTPUT.read_text(encoding="utf-8") != expected:
        print("The compiled dungeon room pack is stale.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
