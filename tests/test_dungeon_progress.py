"""CR 309 and 701.49: source-pinned dungeon paths and completion timing."""

from dataclasses import replace
from hashlib import sha256
import json
import re
import unittest

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.compiler.token_templates import fixed_token_creation_effect_template
from quorune.dungeon_catalog import DUNGEON_CATALOG, DungeonId, DungeonRoom, VentureMode
from quorune.dungeon_progress import (
    DungeonJourney,
    DungeonRoomRef,
    DungeonVisit,
    VentureOffer,
    accept_venture,
    complete_finished_dungeon,
    prepare_venture,
)


class DungeonProgressTests(unittest.TestCase):
    def test_initiative_compiler_and_damage_subject_are_closed(self):
        from types import SimpleNamespace
        from unittest.mock import patch
        from test_bound_effect_programs import record
        from quorune.initiative_model import (
            INHERENT_INITIATIVE_TAKE, INITIATIVE_TRIGGER_PLAYER, TAKE_INITIATIVE_CAPABILITY,
            TAKE_INITIATIVE_FIELD, initiative_effect_player, initiative_instruction,
        )
        from quorune.initiative_runtime import initiative_combat_triggers
        from quorune.oracle_ir import compile_oracle_card
        from quorune.rules.capabilities import load_default_capability_registry

        options = {"capability_registry": load_default_capability_registry(), "capability_profile": "commander_review"}
        source = replace(record("You take the initiative."), oracle_id="fixture:closed-initiative")
        ir = compile_oracle_card(source, **options)
        self.assertEqual("exact", ir.status, ir.material_residuals)
        node, = ir.faces[0].nodes
        effect, = node.effects
        initiative_instruction(effect)
        self.assertIn(TAKE_INITIATIVE_CAPABILITY, node.capability_dependencies)
        with self.assertRaises(ValueError):
            initiative_instruction({**effect, TAKE_INITIATIVE_FIELD: {"schema_version": True}})
        probe = replace(source, oracle_id="fixture:missing-initiative-producer")
        with patch("quorune.oracle_ir.fixed_initiative_effect_template", return_value=None):
            self.assertNotEqual("exact", compile_oracle_card(probe, **options).status)
        copy = SimpleNamespace(semantic_key=INHERENT_INITIATIVE_TAKE, controller="D",
                               context={INITIATIVE_TRIGGER_PLAYER: "A"})
        self.assertEqual("A", initiative_effect_player(copy, INHERENT_INITIATIVE_TAKE))
        event = {"source_controller": "A", "target_kind": "player", "target": "B", "source_types": ("creature",)}
        host = SimpleNamespace(state=SimpleNamespace(initiative="B"), active_seats=("A", "B", "C", "D"))
        self.assertEqual((), initiative_combat_triggers(host, (
            SimpleNamespace(**event, was_dealt=False, combat=True),
            SimpleNamespace(**event, was_dealt=True, combat=False),
        )))

    def test_venture_compiler_binds_only_a_closed_entry_mode(self):
        from unittest.mock import patch
        from test_bound_effect_programs import record
        from quorune.dungeon_progress import DUNGEON_VENTURE_CAPABILITY, DUNGEON_VENTURE_FIELD, venture_instruction
        from quorune.oracle_ir import compile_oracle_card
        from quorune.rules.capabilities import load_default_capability_registry

        options = {"capability_registry": load_default_capability_registry(), "capability_profile": "commander_review"}
        texts = (("Venture into the dungeon.", VentureMode.ORDINARY), ("Venture into Undercity.", VentureMode.UNDERCITY))
        for index, (text, mode) in enumerate(texts):
            with self.subTest(mode=mode):
                source = replace(record(text), oracle_id=f"fixture:venture-mode-{index}")
                ir = compile_oracle_card(source, **options)
                self.assertEqual("exact", ir.status, ir.material_residuals)
                node, = ir.faces[0].nodes
                effect, = node.effects
                self.assertIn(DUNGEON_VENTURE_CAPABILITY, node.capability_dependencies)
                self.assertEqual(mode, venture_instruction(effect))
                with self.assertRaises(ValueError):
                    venture_instruction({**effect, DUNGEON_VENTURE_FIELD: {"schema_version": 1, "mode": "other"}})
        source = replace(record("Venture into any dungeon."), oracle_id="fixture:unsupported-venture-mode")
        self.assertNotEqual("exact", compile_oracle_card(source, **options).status)
        probe = replace(record(texts[0][0]), oracle_id="fixture:missing-venture-producer")
        with patch("quorune.oracle_ir.fixed_venture_effect_template", return_value=None):
            self.assertNotEqual("exact", compile_oracle_card(probe, **options).status)

    def test_dungeon_room_pack_preserves_the_original_source_spans(self):
        from quorune.dungeon_progress import DUNGEON_ROOM_EVENT
        from quorune.semantics import SemanticRegistry

        records = json.loads((ROOT / "tests/fixtures/dungeon-cards.json").read_bytes())["cards"]
        source_by_id = {row["oracle_id"]: row["oracle_text"] for row in records}
        registry = SemanticRegistry()
        expected = set()
        for dungeon, definition in DUNGEON_CATALOG.items():
            for index, room in enumerate(definition.rooms):
                ref = DungeonRoomRef(dungeon, index)
                expected.add(ref.program_key)
                program = registry.get(ref.program_key)
                self.assertEqual("trusted", program.trust_level)
                self.assertEqual(("command", DUNGEON_ROOM_EVENT), (program.active_zone, program.event))
                self.assertEqual(ref.to_dict(), program.provenance["dungeon_room"])
                source = source_by_id[program.oracle_id]
                span = program.provenance["source_span"]
                self.assertEqual(definition.source_oracle_hash, sha256(source.encode("utf-8")).hexdigest())
                self.assertEqual(room.label, program.label)
                self.assertTrue(source[:span["start"]].endswith(room.label + " — "))
                self.assertNotIn("Leads to", source[span["start"]:span["end"]])
        self.assertEqual(expected, {program.key for program in registry.programs_for_event(DUNGEON_ROOM_EVENT)})

    def test_throne_room_body_closes_the_public_entry_and_shuffle_contract(self):
        from unittest.mock import patch
        from test_bound_effect_programs import record
        from quorune.library_battlefield_model import (
            LIBRARY_REVEALED_ENTRY_FOLLOWUP_CAPABILITY, LIBRARY_SHUFFLE_REMAINDER, validate_library_battlefield_effect,
        )
        from quorune.oracle_ir import compile_oracle_card
        from quorune.rules.capabilities import load_default_capability_registry

        payload = json.loads((ROOT / "tests/fixtures/dungeon-cards.json").read_bytes())
        dungeon = next(card for card in payload["cards"] if card["name"] == "Undercity // The Initiative")
        body, = (line.split(" — ", 1)[1] for line in dungeon["oracle_text"].splitlines()
                 if line.startswith("Throne of the Dead Three — "))
        options = {"capability_registry": load_default_capability_registry(), "capability_profile": "commander_review"}
        source = record(body)
        ir = compile_oracle_card(source, **options)
        self.assertEqual("exact", ir.status, ir.material_residuals)
        node, = ir.faces[0].nodes
        effect, = node.effects
        self.assertIn(LIBRARY_REVEALED_ENTRY_FOLLOWUP_CAPABILITY, node.capability_dependencies)
        self.assertEqual((10, True, 1, 1, LIBRARY_SHUFFLE_REMAINDER, {"counters": 3}),
                         tuple(effect[key] for key in ("look_count", "public_reveal", "minimum", "maximum",
                                                      "remainder_destination", "entry_followup")))
        for mutation in ({"public_reveal": False}, {"minimum": 0}, {"look_count": 11},
                         {"entry_followup": {"counters": True}}, {"remainder_destination": "library_bottom"}):
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                validate_library_battlefield_effect({**effect, **mutation})
        later = replace(source, oracle_id="fixture:longer-library-entry-grant",
                        oracle_text=body.replace("until your next turn", "until the end of your next turn"))
        self.assertNotEqual("exact", compile_oracle_card(later, **options).status)
        probe = replace(source, oracle_id="fixture:missing-entry-followup-producer")
        with patch("quorune.compiler.library_battlefield_templates._revealed_creature_entry_followup", return_value=None):
            self.assertNotEqual("exact", compile_oracle_card(probe, **options).status)

    def test_mad_wizard_room_body_uses_closed_draw_cast_contract(self):
        from unittest.mock import patch
        from test_bound_effect_programs import record
        from quorune.drawing.captured_hand import (
            CAPTURED_DRAW_CAST_CAPABILITY, CAPTURED_DRAW_CAST_FIELD, captured_draw_cast_instruction,
        )
        from quorune.oracle_ir import compile_oracle_card
        from quorune.rules.capabilities import load_default_capability_registry

        payload = json.loads((ROOT / "tests/fixtures/dungeon-cards.json").read_bytes())
        dungeon = next(card for card in payload["cards"] if card["name"] == "Dungeon of the Mad Mage")
        body, = (line.split(" — ", 1)[1] for line in dungeon["oracle_text"].splitlines()
                 if line.startswith("Mad Wizard's Lair — "))
        options = {"capability_registry": load_default_capability_registry(), "capability_profile": "commander_review"}
        source = record(body)
        ir = compile_oracle_card(source, **options)
        self.assertEqual("exact", ir.status, ir.material_residuals)
        node, = ir.faces[0].nodes
        effect, = node.effects
        self.assertEqual(3, captured_draw_cast_instruction(effect))
        self.assertIn(CAPTURED_DRAW_CAST_CAPABILITY, node.capability_dependencies)
        for payload_change in ({"count": True}, {"count": 6}, {"schema_version": 2}, {"from": "library"}):
            mutated = {**effect, CAPTURED_DRAW_CAST_FIELD: {**effect[CAPTURED_DRAW_CAST_FIELD], **payload_change}}
            with self.subTest(payload_change=payload_change), self.assertRaises(ValueError):
                captured_draw_cast_instruction(mutated)
        later = replace(source, oracle_id="fixture:later-cast-draw", oracle_text=body.rstrip(".") + " this turn.")
        self.assertNotEqual("exact", compile_oracle_card(later, **options).status)
        probe = replace(source, oracle_id="fixture:missing-draw-cast-producer")
        with patch("quorune.oracle_ir.fixed_captured_draw_cast_effect_template", return_value=None):
            self.assertNotEqual("exact", compile_oracle_card(probe, **options).status)

    def test_pinned_room_names_and_edges_match_the_printed_sources(self):
        payload = json.loads((ROOT / "tests/fixtures/dungeon-cards.json").read_bytes())
        cards = {row["name"]: row for row in payload["cards"]}
        self.assertEqual({definition.source_name for definition in DUNGEON_CATALOG.values()}, set(cards))
        for definition in DUNGEON_CATALOG.values():
            with self.subTest(dungeon=definition.dungeon):
                text = cards[definition.source_name]["oracle_text"]
                self.assertEqual(definition.source_oracle_hash, sha256(text.encode("utf-8")).hexdigest())
                printed = []
                for line in text.split("\n//\n", 1)[0].splitlines():
                    if " — " in line:
                        label, body = line.split(" — ", 1)
                        edges = re.search(r" \(Leads to: ([^)]+)\)$", body)
                        printed.append((label, tuple(edges[1].split(", ")) if edges else ()))
                graph = [(room.label, tuple(definition.rooms[index].label for index in room.exits))
                         for room in definition.rooms]
                self.assertEqual(printed, graph)

    def test_cradle_lowers_the_named_legendary_token_with_its_printed_ability(self):
        payload = json.loads((ROOT / "tests/fixtures/dungeon-cards.json").read_bytes())
        tomb = next(card for card in payload["cards"] if card["name"] == "Tomb of Annihilation")
        body, = (line.split(" — ", 1)[1] for line in tomb["oracle_text"].splitlines()
                 if line.startswith("Cradle of the Death God — "))
        compiled = fixed_token_creation_effect_template(body)
        self.assertIsNotNone(compiled)
        self.assertIsNone(compiled.target_schema)
        self.assertEqual({
            "op": "create_token", "controller": "$controller", "name": "The Atropal", "quantity": 1,
            "characteristics": {"type_line": "Token Legendary Creature — God Horror",
                                "literal_token_name_version": 1, "colors": ["B"],
                                "power": "4", "toughness": "4", "keywords": ["Deathtouch"]},
        }, compiled.effect)
        unsupported = body.replace("deathtouch", "deathtouch and all triggered abilities of the exiled cards")
        self.assertIsNone(fixed_token_creation_effect_template(unsupported))

    def test_catalog_rejects_backward_edges_and_unreachable_rooms(self):
        definition = DUNGEON_CATALOG[DungeonId.LOST_MINE]
        with self.assertRaisesRegex(ValueError, "below the current room"):
            replace(definition, rooms=(DungeonRoom("Start", (0,)), DungeonRoom("Finish")))
        with self.assertRaisesRegex(ValueError, "reachable"):
            replace(definition, rooms=(DungeonRoom("Start", (2,)), DungeonRoom("Isolated", (2,)), DungeonRoom("Finish")))

    def test_entry_modes_choose_distinct_pools_and_advance_every_printed_edge(self):
        fresh = DungeonJourney()
        ordinary, completed = prepare_venture("A", fresh, VentureMode.ORDINARY)
        undercity, _ = prepare_venture("A", fresh, VentureMode.UNDERCITY)
        self.assertIsNone(completed)
        self.assertEqual(tuple(DungeonRoomRef(dungeon, 0) for dungeon in (
            DungeonId.LOST_MINE, DungeonId.MAD_MAGE, DungeonId.TOMB,
        )), ordinary.destinations)
        self.assertEqual((DungeonRoomRef(DungeonId.UNDERCITY, 0),), undercity.destinations)
        with self.assertRaisesRegex(ValueError, "not offered"):
            accept_venture("A", fresh, ordinary, undercity.destinations[0])
        for dungeon, definition in DUNGEON_CATALOG.items():
            for index, room in enumerate(definition.rooms[:-1]):
                journey = DungeonJourney(DungeonVisit(DungeonRoomRef(dungeon, index), 1))
                expected = tuple(DungeonRoomRef(dungeon, successor) for successor in room.exits)
                for mode in VentureMode:
                    with self.subTest(dungeon=dungeon, room=index, mode=mode):
                        offer, completed = prepare_venture("B", journey, mode)
                        self.assertIsNone(completed)
                        self.assertEqual(expected, offer.destinations)
                        for destination in expected:
                            updated = accept_venture("B", journey, offer, destination)
                            self.assertEqual(DungeonJourney(DungeonVisit(destination, 1)), updated)

    def test_completion_waits_for_room_abilities_from_the_current_incarnation(self):
        bottom = DungeonRoomRef(DungeonId.LOST_MINE, 6)
        original = DungeonVisit(bottom, 1)
        journey = DungeonJourney(original)
        self.assertEqual((journey, None), complete_finished_dungeon(journey, (1, 1)))
        once = DungeonJourney(completed=1, completed_by_dungeon=((DungeonId.LOST_MINE, 1),))
        self.assertEqual((once, original), complete_finished_dungeon(journey, ()))

        offer, completed = prepare_venture("C", journey, VentureMode.ORDINARY)
        self.assertEqual(original, completed)
        self.assertEqual(once, offer.journey)
        reentry = accept_venture("C", offer.journey, offer, DungeonRoomRef(DungeonId.LOST_MINE, 0))
        self.assertEqual(replace(once, active=DungeonVisit(DungeonRoomRef(DungeonId.LOST_MINE, 0), 2)), reentry)
        current_last = DungeonVisit(bottom, 2)
        last_journey = replace(once, active=current_last)
        self.assertEqual((last_journey, None), complete_finished_dungeon(last_journey, (1, 2)))
        twice = DungeonJourney(completed=2, completed_by_dungeon=((DungeonId.LOST_MINE, 2),))
        self.assertEqual((twice, current_last), complete_finished_dungeon(last_journey, (1,)))

    def test_named_completion_is_recorded_only_when_its_dungeon_finishes(self):
        mine = DungeonVisit(DungeonRoomRef(DungeonId.LOST_MINE, 6), 1)
        journey, _ = complete_finished_dungeon(DungeonJourney(mine), ())
        self.assertEqual(1, journey.completed)
        self.assertNotIn(DungeonId.TOMB, dict(journey.completed_by_dungeon))
        tomb = DungeonVisit(DungeonRoomRef(DungeonId.TOMB, 4), 2)
        pending = replace(journey, active=tomb)
        self.assertEqual((pending, None), complete_finished_dungeon(pending, (2,)))
        offer, retired = prepare_venture("A", pending, VentureMode.ORDINARY)
        self.assertEqual(tomb, retired)
        self.assertEqual({DungeonId.LOST_MINE: 1, DungeonId.TOMB: 1}, dict(offer.journey.completed_by_dungeon))
        self.assertEqual(2, offer.journey.completed)
        self.assertEqual(offer, VentureOffer.from_dict(json.loads(json.dumps(offer.to_dict()))))

    def test_aggregate_only_journeys_keep_legacy_bytes_and_carry_new_named_evidence(self):
        legacy = {"active": None, "completed": 2}
        journey = DungeonJourney.from_dict(legacy)
        self.assertEqual(legacy, journey.to_dict())
        self.assertEqual((), journey.completed_by_dungeon)
        tomb = DungeonVisit(DungeonRoomRef(DungeonId.TOMB, 4), 3)
        finished, _ = complete_finished_dungeon(replace(journey, active=tomb), ())
        self.assertEqual(3, finished.completed)
        self.assertEqual(((DungeonId.TOMB, 1),), finished.completed_by_dungeon)
        for invalid in ({"tomb": 4}, {"tomb": True}, {"unknown": 1}):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                DungeonJourney.from_dict({**legacy, "completed_by_dungeon": invalid})

    def test_choice_round_trip_rejects_stale_owner_position_and_forged_edges(self):
        first = DungeonRoomRef(DungeonId.MAD_MAGE, 1)
        journey = DungeonJourney(DungeonVisit(first, 1))
        offer, _ = prepare_venture("D", journey, VentureMode.ORDINARY)
        wire = offer.to_dict()
        restored = VentureOffer.from_dict(json.loads(json.dumps(wire)))
        self.assertEqual(offer, restored)
        destination = restored.destinations[0]
        self.assertEqual(journey, DungeonJourney.from_dict(restored.journey.to_dict()))
        with self.assertRaisesRegex(ValueError, "owner and dungeon position"):
            accept_venture("A", journey, restored, destination)
        advanced = accept_venture("D", journey, restored, destination)
        with self.assertRaisesRegex(ValueError, "owner and dungeon position"):
            accept_venture("D", advanced, restored, destination)
        with self.assertRaisesRegex(ValueError, "not offered"):
            accept_venture("D", journey, restored, DungeonRoomRef(DungeonId.MAD_MAGE, 0))
        with self.assertRaisesRegex(ValueError, "pinned graph"):
            VentureOffer.from_dict({**wire, "destinations": [destination.to_dict()]})
        with self.assertRaisesRegex(ValueError, "existing room"):
            VentureOffer.from_dict({**wire, "destinations": [{**destination.to_dict(), "index": True}]})
        with self.assertRaisesRegex(ValueError, "version 1"):
            VentureOffer.from_dict({**wire, "schema_version": True})
        self.assertEqual(journey, restored.journey)


class DungeonVentureNativeTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from pathlib import Path
        from tempfile import TemporaryDirectory
        from quorune.carddb import CardDatabase
        from quorune.deck import DeckDefinition, DeckEntry
        from quorune.rules.capabilities import load_default_capability_registry
        from scripts.build_test_database import build_fixture_database

        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "venture.sqlite3"
        names = ("bound-effect-program-cards.json", "dungeon-cards.json", "venture-dungeon-cards.json",
                 "paid-sacrifice-creature-stat-cards.json", "initiative-dungeon-cards.json",
                 "fixed-extra-turn-cards.json")
        build_fixture_database([ROOT / "tests/fixtures" / name for name in names], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Venture witness", [DeckEntry("Generic Bound Commander", 1, "commander"),
                                                        DeckEntry("Generic Bound Plains", 30)], ["Generic Bound Commander"])

    def _choose(self, session, **response):
        result = session.act("pilot:A", {"action_id": "choose", **response})
        self.assertTrue(result.ok, result.summary)

    def _room_options(self, session):
        self.assertEqual("semantic.choice", session.state.pending_decision.kind)
        return tuple(option["id"] for option in session.packet("pilot:A", full=True)["decision"]["ctx"]["options"])

    def _activate_map(self, session, card):
        self.priority(session, "A")
        actions = session.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        action, = [action for action in actions if action["id"].startswith(f"activate:{card.ref}:")
                   and not action.get("mana_ability")]
        result = session.act("pilot:A", {"action_id": action["id"], "pay": "auto"})
        self.assertTrue(result.ok, result.summary)

    def _reach_last_room(self, seed, *, reenter=False):
        from quorune.card_programs import bind_semantic_program_runtime
        from quorune.dungeon_progress import DUNGEON_VENTURE_CAPABILITY
        from quorune.record import authoritative_state_hash

        session = self.session(seed)
        veteran = self.add(session.engine, "Veteran Dungeoneer", zone="hand")
        maps = tuple(self.add(session.engine, "Dungeon Map", ref=f"venture-map-{index}") for index in range(3))
        if reenter:
            self.add(session.engine, "Clattering Skeletons", ref="venture-skeletons")
            self.add(session.engine, "Fling", zone="hand", ref="venture-fling")
        for card in (veteran, maps[0]):
            self.assert_closed(session, card)
        program, = [program for program in session.engine.semantics.programs_for_oracle(veteran.oracle_id)
                    if DUNGEON_VENTURE_CAPABILITY in program.capability_dependencies]
        deps = tuple(cap for cap in program.capability_dependencies if cap != DUNGEON_VENTURE_CAPABILITY)
        forged = replace(program, capability_dependencies=deps,
                         capability_closure=self.registry.closure(deps, profile="commander_review").to_dict())
        binding = bind_semantic_program_runtime(forged, capability_registry=self.registry, profile="commander_review")
        self.assertFalse(binding["strict"])
        self.assertIn("capability:undeclared_runtime_dependency:" + DUNGEON_VENTURE_CAPABILITY, binding["blockers"])
        self.prepare(session, mana={"C": 13, "W": 1, "R": 1})
        self.checkpoint(session)
        self.cast_for(session, "A", veteran)
        self.assertEqual("semantic.choice", self.resolve(session).kind)
        self.assertEqual(("lost_mine:0", "mad_mage:0", "tomb:0"), self._room_options(session))
        before = authoritative_state_hash(session.state)
        for seat, choice in (("B", "lost_mine:0"), ("A", "undercity:0"), ("A", "lost_mine:6")):
            self.assertFalse(session.act(f"pilot:{seat}", {"action_id": "choose", "choice": choice}).ok)
            self.assertEqual(before, authoritative_state_hash(session.state))
        session = self.replay(session, load=True)
        self._choose(session, choice="lost_mine:0")
        self.assertEqual("semantic.choice", self.resolve(session).kind)
        private = session.packet("pilot:A", full=True)["decision"]["legal_actions"][0]["choice_schema"]["legal_refs"]
        self.assertIsNone(session.packet("pilot:C", full=True)["decision"])
        self._choose(session, cards={"top": private, "bottom": []})
        source, = [card for card in session.state.cards.values() if card.object_kind == "dungeon"]
        self.assertEqual(("A", "command"), (source.owner, source.zone))

        self._activate_map(session, maps[0])
        self.assertEqual("semantic.choice", self.resolve(session).kind)
        self.assertEqual(("lost_mine:1", "lost_mine:2"), self._room_options(session))
        self._choose(session, choice="lost_mine:1")
        self.assertIsNone(self.resolve(session))
        self.assertTrue(any(card.printed_name == "Goblin Token" and card.controller == "A"
                            for card in session.state.cards.values()))

        self._activate_map(session, maps[1])
        self.assertEqual("semantic.choice", self.resolve(session).kind)
        self.assertEqual(("lost_mine:3", "lost_mine:4"), self._room_options(session))
        self._choose(session, choice="lost_mine:3")
        self.assertEqual("semantic.target", session.state.pending_decision.kind)
        room_item = session.state.stack[-1]
        self.assertEqual(("dungeon-room:lost_mine:3", source.object_id), (room_item.semantic_key, room_item.source_object_id))
        self.assertFalse(room_item.targets)
        before = authoritative_state_hash(session.state)
        self.assertFalse(session.act("pilot:B", {"action_id": "choose", "targets": [veteran.ref]}).ok)
        self.assertEqual(before, authoritative_state_hash(session.state))
        self._choose(session, targets=[veteran.ref])
        self.assertIsNone(self.resolve(session))
        self.assertEqual({"+1/+1": 1}, session.state.cards[veteran.object_id].counters)

        self._activate_map(session, maps[2])
        self.resolve_top(session)
        final = session.state.stack[-1]
        journey = session.state.players["A"].dungeon_journey
        self.assertEqual(DungeonRoomRef(DungeonId.LOST_MINE, 6), journey.active.room)
        self.assertEqual(0, journey.completed)
        self.assertEqual("dungeon-room:lost_mine:6", final.semantic_key)
        return session, source, final

    def test_original_venture_announces_targets_and_completes_after_the_last_trigger(self):
        session, source, final = self._reach_last_room(309001)
        session = self.replay(session, load=True)
        self.assertIsNone(self.resolve(session))
        journey = session.state.players["A"].dungeon_journey
        self.assertEqual((None, 1, ((DungeonId.LOST_MINE, 1),)), (journey.active, journey.completed, journey.completed_by_dungeon))
        self.assertNotIn(source.object_id, session.state.cards)
        events = session.state.events
        resolution = next(event.event_id for event in events if event.code == "stack.resolve" and event.details["stack"] == final.ref)
        completed = next(event.event_id for event in events if event.code == "dungeon.completed")
        self.assertLess(resolution, completed)
        self.replay(session)

    def test_venture_retires_the_last_visit_with_its_room_trigger_still_pending(self):
        session, source, final = self._reach_last_room(309002, reenter=True)
        fling = next(card for card in session.state.cards.values() if card.ref == "venture-fling")
        self.priority(session, "A")
        action = self.action(session, "A", fling)
        result = session.act("pilot:A", {"action_id": action["id"], "targets": ["D"], "pay": "auto",
                                         "sacrifice_cards": ["venture-skeletons"]})
        self.assertTrue(result.ok, result.summary)
        self.assertEqual("semantic.choice", self.resolve(session).kind)
        self.assertEqual(("lost_mine:0", "mad_mage:0", "tomb:0"), self._room_options(session))
        self.assertNotIn(source.object_id, session.state.cards)
        self.assertTrue(any(item.ref == final.ref for item in session.state.stack))
        self.assertEqual(1, session.state.players["A"].dungeon_journey.completed)
        session = self.replay(session, load=True)
        self._choose(session, choice="mad_mage:0")
        self.assertIsNone(self.resolve(session))
        journey = session.state.players["A"].dungeon_journey
        self.assertEqual(DungeonVisit(DungeonRoomRef(DungeonId.MAD_MAGE, 0), 2), journey.active)
        self.assertEqual(((DungeonId.LOST_MINE, 1),), journey.completed_by_dungeon)
        self.assertTrue(any(event.code == "stack.resolve" and event.details["stack"] == final.ref
                            for event in session.state.events))
        self.replay(session)

    def test_original_initiative_retake_and_upkeep_advance_the_same_undercity(self):
        from quorune.card_programs import bind_semantic_program_runtime
        from quorune.initiative_model import INHERENT_INITIATIVE_VENTURE, TAKE_INITIATIVE_CAPABILITY

        session = self.session(726001)
        sneak = self.add(session.engine, "Aarakocra Sneak", zone="hand")
        explorer = self.add(session.engine, "Underdark Explorer", zone="hand")
        warp = self.add(session.engine, "Time Warp", zone="hand")
        for card in (sneak, explorer, warp):
            self.assert_closed(session, card)
        program, = [program for program in session.engine.semantics.programs_for_oracle(sneak.oracle_id)
                    if TAKE_INITIATIVE_CAPABILITY in program.capability_dependencies]
        deps = tuple(cap for cap in program.capability_dependencies if cap != TAKE_INITIATIVE_CAPABILITY)
        forged = replace(program, capability_dependencies=deps,
                         capability_closure=self.registry.closure(deps, profile="commander_review").to_dict())
        binding = bind_semantic_program_runtime(forged, capability_registry=self.registry, profile="commander_review")
        self.assertIn("capability:undeclared_runtime_dependency:" + TAKE_INITIATIVE_CAPABILITY, binding["blockers"])
        self.prepare(session, mana={"C": 10, "U": 3, "B": 1})
        self.checkpoint(session)
        self.cast_for(session, "A", sneak)
        self.assertEqual("semantic.search", self.resolve(session).kind)
        self.assertEqual("A", session.state.initiative)
        self.assertEqual("A", session.packet("spectator", full=True)["state"]["game"]["initiative"])
        first = session.state.players["A"].dungeon_journey.active
        self.assertEqual(DungeonVisit(DungeonRoomRef(DungeonId.UNDERCITY, 0), 1), first)
        self.assertIsNone(session.packet("pilot:B", full=True)["decision"])
        self._choose(session, cards=[])
        self.assertIsNone(self.resolve(session))

        self.cast_for(session, "A", explorer)
        self.assertEqual("semantic.choice", self.resolve(session).kind)
        self.assertEqual(("undercity:1", "undercity:2"), self._room_options(session))
        self.assertEqual(["A", "A"], [event.actor for event in session.state.events if event.code == "initiative.taken"])
        self._choose(session, choice="undercity:2")
        self.assertEqual("semantic.choice", self.resolve(session).kind)
        private = session.packet("pilot:A", full=True)["decision"]["legal_actions"][0]["choice_schema"]["legal_refs"]
        self._choose(session, cards={"top": private, "bottom": []})
        self.assertIsNone(self.resolve(session))
        self.assertEqual(DungeonVisit(DungeonRoomRef(DungeonId.UNDERCITY, 2), 1),
                         session.state.players["A"].dungeon_journey.active)

        self.cast_for(session, "A", warp, ["A"])
        self.assertIsNone(self.resolve(session))
        self.until(session, lambda game: any(item.semantic_key == INHERENT_INITIATIVE_VENTURE
                                             for item in game.state.stack))
        self.assertEqual("upkeep", session.state.step)
        self.assertTrue(session.state.current_turn.extra)
        self.assertEqual("semantic.choice", self.resolve(session).kind)
        self.assertEqual(("undercity:4", "undercity:5"), self._room_options(session))
        self.replay(session, load=True)

    def test_simultaneous_combat_damage_transfers_the_initiative_once_on_resolution(self):
        from quorune.initiative_model import INHERENT_INITIATIVE_TAKE, INHERENT_INITIATIVE_VENTURE, initiative_effect_player

        session = self.session(726002)
        first = self.add(session.engine, "Grizzly Bears", ref="initiative-bear-1")
        second = self.add(session.engine, "Grizzly Bears", ref="initiative-bear-2")
        for bear in (first, second):
            bear.acquired_control_turn_count = -1
        self.prepare(session)
        session.state.initiative = "B"
        self.checkpoint(session)
        self.attack(session, [(first, "B"), (second, "B")])
        self.until(session, lambda game: any(item.semantic_key == INHERENT_INITIATIVE_TAKE for item in game.state.stack))
        transfers = [item for item in session.state.stack if item.semantic_key == INHERENT_INITIATIVE_TAKE]
        transfer, = transfers
        self.assertEqual(("B", "A"), (transfer.controller, initiative_effect_player(transfer, INHERENT_INITIATIVE_TAKE)))
        self.assertIsNone(transfer.source_object_id)
        self.assertEqual("B", session.state.initiative)
        self.assertEqual(36, session.state.players["B"].life)
        session = self.replay(session, load=True)
        self.resolve_top(session)
        self.assertEqual("A", session.state.initiative)
        venture, = [item for item in session.state.stack if item.semantic_key == INHERENT_INITIATIVE_VENTURE]
        self.assertEqual(("A", None), (venture.controller, venture.source_object_id))
        self.assertEqual("semantic.search", self.resolve(session).kind)
        self._choose(session, cards=[])
        self.assertIsNone(self.resolve(session))
        self.assertEqual(DungeonVisit(DungeonRoomRef(DungeonId.UNDERCITY, 0), 1),
                         session.state.players["A"].dungeon_journey.active)
        session = self.replay(session, load=True)
        self.priority(session, "A")
        result = session.act("pilot:A", {"action_id": "concede", "choices": {"confirm_concede": True}})
        self.assertTrue(result.ok, result.summary)
        self.assertEqual("B", session.state.initiative)
        self.assertIsNone(session.state.players["A"].dungeon_journey.active)
        self.assertEqual([INHERENT_INITIATIVE_VENTURE], [item.semantic_key for item in session.state.stack])
        decision = self.resolve(session)
        self.assertEqual(("semantic.search", ("B",)), (decision.kind, tuple(decision.actors)))
        result = session.act("pilot:B", {"action_id": "choose", "cards": []})
        self.assertTrue(result.ok, result.summary)
        self.assertIsNone(self.resolve(session))
        self.replay(session)
