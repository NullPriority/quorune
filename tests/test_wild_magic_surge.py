import json
from dataclasses import replace
from pathlib import Path
from random import Random
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.target_controller_library_reveal import TARGET_CONTROLLER_REVEAL_CAPABILITY
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card

SOURCE = "Wild Magic Surge"
PROFILE = "commander_review"
REVEALS = {"library.reveal", "library.top.reveal"}


class WildMagicSurgeTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "wild-magic-surge.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures/wild-magic-surge-cards.json"], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        commander = "Isamaru, Hound of Konda"
        cls.deck = DeckDefinition("Matching permanent library prefix", [
            DeckEntry(commander, 1, "commander"), DeckEntry("Plains", 30),
        ], [commander])

    def scene(self, seed, target_name, library, *, response=False, aura_recipients=False, replacement=False,
              positions_known=False):
        game = self.session(seed)
        host = game.engine
        owner = game.state.players["B"]
        for object_id in tuple(owner.zones["library"]):
            host.move_card(object_id, "outside", log=False, semantic_events=False)
        rows = []
        for index, name in enumerate(reversed(library)):
            card = self.add(host, name, seat="B", zone="library", ref=f"surge-library-{index}")
            card.known_to = ["B", "C"] if positions_known else []
            card.revealed_to = []
            rows.append(card)
        target = self.add(host, target_name, seat="D", ref="surge-borrowed-target")
        host.change_control(target.object_id, "B", reason="fixture different controller")
        spell = self.add(host, SOURCE, zone="hand", ref="surge-original-spell")
        self.assert_closed(game, spell)
        own = self.add(host, "Sol Ring", ref="surge-caster-permanent")
        response_card = self.add(host, "Disenchant", seat="D", zone="hand", ref="surge-response") if response else None
        if replacement:
            self.add(host, "Rest in Peace", seat="D", ref="surge-destination-replacement")
        if aura_recipients:
            self.add(host, "Grizzly Bears", seat="A", ref="surge-aura-recipient-A")
            self.add(host, "Grizzly Bears", seat="C", ref="surge-aura-recipient-C")
        self.prepare(game, mana={"R": 2})
        game.state.players["D"].mana_pool.update({"C": 1, "W": 1})
        self.checkpoint(game)
        matrix = game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        cast, = [action for action in matrix if action.get("card") == spell.ref]
        self.assertIn(target.ref, cast["target_schema"]["legal_refs"])
        self.assertNotIn(own.ref, cast["target_schema"]["legal_refs"])
        before = authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:A", {"action_id": cast["id"], "targets": [own.ref], "pay": "auto"}).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        self.cast_for(game, "A", spell, [target.ref])
        return game, target, tuple(reversed(rows)), response_card

    def assert_public_prefix(self, game, start, revealed, *, hidden=()):
        events = [event for event in game.state.events if event.event_id > start and event.code in REVEALS]
        event, = events
        self.assertEqual("B", event.actor)
        details = json.dumps(event.details)
        for row in revealed:
            self.assertIn(row.ref, details)
        for principal in ("pilot:A", "pilot:C", "spectator"):
            packet = game.packet(principal, full=True, cursor_key=f"surge-public-{principal}")
            public, = [row for row in packet.get("events", []) if row["c"] in REVEALS]
            for card in revealed:
                self.assertIn(card.printed_name, public["s"])
            for card in hidden:
                self.assertNotIn(card.ref, json.dumps(packet))
        return event.event_id

    def test_committed_destruction_uses_affected_controller_and_stops_at_first_permanent(self):
        for index, (target_name, library, target_zone, replacement) in enumerate((
            ("Sol Ring", ("Plains", "Disenchant", "Ichor Wellspring", "Darksteel Citadel", "Forest"), "graveyard", False),
            ("Darksteel Citadel", ("Disenchant", "Forest", "Sol Ring", "Plains"), "battlefield", False),
            ("Sol Ring", ("Plains", "Disenchant", "Sol Ring", "Darksteel Citadel"), "exile", True),
        )):
            with self.subTest(target=target_name, target_zone=target_zone):
                game, target, rows, _ = self.scene(715001 + index, target_name, library, replacement=replacement)
                count = {"battlefield": 1, "graveyard": 2, "exile": 2}[target_zone]
                missed, selected, rest = rows[:count], rows[count], rows[count + 1:]
                before_a = tuple(game.state.players["A"].zones["library"])
                before_d = tuple(game.state.players["D"].zones["library"])
                start = game.state.event_sequence
                game = self.replay(game, load=True)
                self.resolve_top(game)
                current = game.state.cards[selected.object_id]
                self.assertEqual(("B", "B", "battlefield"), (current.owner, current.controller, current.zone))
                self.assertEqual(target_zone, game.state.cards[target.object_id].zone)
                b_library = game.state.players["B"].zones["library"]
                self.assertEqual({row.object_id for row in missed}, set(b_library[:count]))
                self.assertEqual([row.object_id for row in reversed(rest)], b_library[count:])
                self.assertEqual(before_a, tuple(game.state.players["A"].zones["library"]))
                self.assertEqual(before_d, tuple(game.state.players["D"].zones["library"]))
                reveal = self.assert_public_prefix(game, start, (*missed, selected), hidden=rest)
                destruct, = [event for event in game.state.events if event.event_id > start
                             and event.code in {"permanent.destroyed", "permanent.destroy.prohibited"}]
                self.assertLess(destruct.event_id, reveal)
                game = self.replay(game, load=True)
                if selected.printed_name == "Ichor Wellspring":
                    self.assertEqual("B", game.state.stack[-1].controller)
                    self.resolve_top(game)
                    self.assertEqual("hand", game.state.cards[rest[0].object_id].zone)
                    self.replay(game, load=True)

    def test_stale_target_has_no_reveal_and_no_match_bottoms_entire_library(self):
        for index, stale in enumerate((False, True)):
            with self.subTest(stale=stale):
                game, target, rows, response = self.scene(
                    715111 + index, "Sol Ring", ("Grizzly Bears", "Disenchant", "Forest"),
                    response=stale, positions_known=True,
                )
                if stale:
                    self.cast_for(game, "D", response, [target.ref])
                    self.resolve_top(game)
                start = game.state.event_sequence
                stack_ref = game.state.stack[-1].ref
                count = game.state.players["B"].stats.get("shuffle_count", 0)
                before = list(game.state.players["B"].zones["library"])
                self.assertIsNone(self.resolve(game))
                after = game.state.players["B"].zones["library"]
                if stale:
                    self.assertEqual(before, after)
                    self.assertFalse(any(event.event_id > start and event.code in REVEALS for event in game.state.events))
                else:
                    selected, = [event for event in game.state.events if event.event_id > start
                                 and event.code == "library.selection"]
                    expected = list(reversed(before))
                    Random(f"{game.state.config.seed}|B|library-battlefield-selection|"
                           f"{selected.event_id - 1}|{stack_ref}").shuffle(expected)
                    self.assertEqual(expected, after)
                    self.assert_public_prefix(game, start, rows)
                    self.assertTrue(all(not game.state.cards[object_id].known_to for object_id in after))
                self.assertEqual(count, game.state.players["B"].stats.get("shuffle_count", 0))
                self.replay(game, load=True)

    def test_first_matching_aura_uses_affected_player_entry_choice_and_replays(self):
        for index, legal_attachment in enumerate((True, False)):
            with self.subTest(legal_attachment=legal_attachment):
                game, _, rows, _ = self.scene(715221 + index, "Seal of Cleansing",
                    ("Grizzly Bears", "Disenchant", "Pacifism", "Forest"), aura_recipients=legal_attachment)
                misses, aura, hidden = rows[:2], rows[2], rows[3]
                start = game.state.event_sequence
                pending = self.resolve(game)
                self.assert_public_prefix(game, start, (*misses, aura), hidden=(hidden,))
                if legal_attachment:
                    self.assertEqual(("aura.entry", ("B",)), (pending.kind, tuple(pending.actors)))
                    game = self.replay(game, load=True)
                    recipient, = [card for card in game.state.cards.values() if card.ref == "surge-aura-recipient-C"]
                    action = {"action_id": "choose", "aura_target": recipient.ref}
                    before = authoritative_state_hash(game.state)
                    self.assertFalse(game.act("pilot:A", action).ok)
                    self.assertEqual(before, authoritative_state_hash(game.state))
                    result = game.act("pilot:B", action)
                    self.assertTrue(result.ok, result.summary)
                    current = game.state.cards[aura.object_id]
                    self.assertEqual(("battlefield", "B", recipient.object_id),
                                     (current.zone, current.controller, current.attached_to))
                else:
                    self.assertIsNone(pending)
                    self.assertEqual("library", game.state.cards[aura.object_id].zone)
                library = game.state.players["B"].zones["library"]
                self.assertCountEqual([row.object_id for row in misses], library[:2])
                self.assertEqual([hidden.object_id] + ([] if legal_attachment else [aura.object_id]), library[2:])
                self.replay(game, load=True)

    def test_exact_destruction_and_library_scope_fail_closed(self):
        record = self.db.lookup(SOURCE, fuzzy=False)
        options = {"capability_registry": self.registry, "capability_profile": PROFILE}
        self.assertEqual("exact", compile_oracle_card(record, **options).status)
        report, semantics = register_card(self.db, self.registry, SOURCE)
        self.assertTrue(report["strict_capability_ready"], report)
        program, = semantics.programs_for_oracle(record.oracle_id)
        self.assertIn(TARGET_CONTROLLER_REVEAL_CAPABILITY, program.capability_dependencies)
        target = dict(program.target_schema)
        del target["controller_relation"]
        self.assertFalse(bind_semantic_program_runtime(replace(program, target_schema=target),
            capability_registry=self.registry, profile=PROFILE)["strict"])
        deps = tuple(value for value in program.capability_dependencies if value != TARGET_CONTROLLER_REVEAL_CAPABILITY)
        omitted = replace(program, capability_dependencies=deps,
                          capability_closure=self.registry.closure(deps, profile=PROFILE).to_dict())
        result = bind_semantic_program_runtime(omitted, capability_registry=self.registry, profile=PROFILE)
        self.assertIn("capability:undeclared_runtime_dependency:" + TARGET_CONTROLLER_REVEAL_CAPABILITY, result["blockers"])
        with patch("quorune.compiler.destruction_followup_templates.target_controller_library_reveal_template",
                   return_value=None):
            self.assertNotEqual("exact", compile_oracle_card(record, **options).status)
