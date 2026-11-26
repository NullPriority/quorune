"""Native owner-hand movement for one opposing spell or nonland permanent."""
from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.opponent_spell_or_permanent_return import CAPABILITY, FIELD, target_schema
from scripts.build_test_database import build_fixture_database


SOURCE = "Sink into Stupor // Soporific Springs"
PROFILE = "commander_review"


class OpponentSpellOrPermanentReturnTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "opponent-spell-or-permanent-return.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures/opponent-spell-or-permanent-return-cards.json"], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        commander = "Isamaru, Hound of Konda"
        cls.deck = DeckDefinition("Owner hand target witness", [
            DeckEntry(commander, 1, "commander"), DeckEntry("Plains", 30),
        ], [commander])

    def accepted(self, game, seat, command):
        result = game.act("pilot:" + seat, command)
        self.assertTrue(result.ok, result.summary)

    def rejected(self, game, seat, command):
        before = authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:" + seat, command).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))

    def stack_item(self, game, card):
        item, = (item for item in game.state.stack if item.card_object_id == card.object_id)
        return item

    def test_sink_exact_single_opponent_target_and_shape_mutations_fail_closed(self):
        game = self.session(11224010)
        source = self.add(game.engine, SOURCE, zone="hand")
        self.assert_closed(game, source)
        record = self.db.by_oracle_id(source.oracle_id)
        exact = compile_oracle_card(record, capability_registry=self.registry, capability_profile=PROFILE)
        self.assertEqual("exact", exact.status, exact.material_residuals)
        front, back = exact.faces
        self.assertEqual(target_schema(), front.nodes[0].target_schema)
        self.assertNotIn(CAPABILITY, {c for node in back.nodes for c in node.capability_dependencies})
        program, = (p for p in game.engine.semantics.programs_for_oracle(source.oracle_id)
                    if any(FIELD in effect for effect in p.effects))
        for bad in (replace(program, target_schema={**target_schema(), "controller": "any"}),
                    replace(program, effects=[{**program.effects[0], FIELD: 2}])):
            with self.subTest(program=bad.effects):
                proof = bind_semantic_program_runtime(bad, capability_registry=self.registry, profile=PROFILE)
                self.assertFalse(proof["strict"], proof)
        remaining = tuple(cap for cap in program.capability_dependencies if cap != CAPABILITY)
        closure = self.registry.closure(remaining, profile=PROFILE).to_dict()
        bad = replace(program, capability_dependencies=remaining, capability_closure=closure,
                      provenance={**program.provenance, "capability_closure_fingerprint": closure["fingerprint"]})
        proof = bind_semantic_program_runtime(bad, capability_registry=self.registry, profile=PROFILE)
        self.assertIn("capability:undeclared_runtime_dependency:" + CAPABILITY, proof["blockers"])

    def test_actual_sink_returns_uncounterable_spell_and_spell_copy_with_exact_replay(self):
        for copy in (False, True):
            with self.subTest(copy=copy):
                game = self.session(11224011 + copy)
                source = self.add(game.engine, SOURCE, zone="hand")
                victim = self.add(game.engine, "Volcanic Fallout", seat="B", zone="hand")
                copier = self.add(game.engine, "Twincast", seat="C", zone="hand")
                self.prepare(game, mana={"C": 1, "U": 2})
                game.state.players["B"].mana_pool.update({"C": 1, "R": 2})
                game.state.players["C"].mana_pool.update({"U": 2})
                game.state.config.semantic_policy = "trusted_only"
                self.checkpoint(game)
                self.cast(game, "B", victim)
                original = self.stack_item(game, victim)
                self.assertFalse(game.engine._stack_item_can_be_countered(original))
                target = original
                if copy:
                    self.cast(game, "C", copier, [original.ref])
                    self.resolve_top(game)
                    target, = (item for item in game.state.stack if item.kind == "spell_copy")
                self.cast(game, "A", source, [target.ref])
                game = self.replay(game, load=True)
                self.resolve_top(game)
                self.assertNotIn(target.ref, {item.ref for item in game.state.stack})
                self.assertNotIn(target.ref, {entry.details.get("stack") for entry in game.state.events
                                             if entry.code == "stack.counter"})
                self.assertEqual(40, game.state.players["A"].life)
                if copy:
                    self.assertEqual("stack", game.state.cards[victim.object_id].zone)
                    copy_card = game.state.cards.get(target.card_object_id)
                    self.assertTrue(copy_card is None or copy_card.zone == "outside")
                else:
                    self.assertEqual("hand", game.state.cards[victim.object_id].zone)
                    self.assertIn(victim.object_id, game.state.players["B"].zones["hand"])
                self.replay(game, load=True)

    def test_actual_sink_targets_opponent_controller_and_returns_to_distinct_owner(self):
        for departed in (False, True):
            with self.subTest(departed=departed):
                game = self.session(11224020 + departed)
                source = self.add(game.engine, SOURCE, zone="hand")
                victim = self.add(game.engine, "Grizzly Bears", seat="C", zone="hand", ref="borrowed-bear")
                wrong = self.add(game.engine, "Grizzly Bears", ref="own-bear")
                land = self.add(game.engine, "Plains", seat="B", ref="opponent-land")
                respondent = self.add(game.engine, "Unsummon", seat="B", zone="hand")
                game.engine.move_card(victim.object_id, "battlefield", controller="B", log=False)
                self.prepare(game, mana={"C": 1, "U": 2})
                game.state.players["B"].mana_pool["U"] = 1
                self.checkpoint(game)
                offer = self.action(game, "A", source)
                projection = json.dumps(offer["target_schema"])
                self.assertIn(victim.ref, projection)
                self.assertNotIn(wrong.ref, projection)
                self.assertNotIn(land.ref, projection)
                command = {"action_id": offer["id"], "pay": "auto"}
                self.rejected(game, "A", {**command, "targets": [wrong.ref]})
                self.rejected(game, "A", {**command, "targets": [land.ref]})
                self.accepted(game, "A", {**command, "targets": [victim.ref]})
                if departed:
                    self.cast(game, "B", respondent, [victim.ref])
                    self.resolve_top(game)
                game = self.replay(game, load=True)
                self.resolve_top(game)
                self.assertIn(victim.object_id, game.state.players["C"].zones["hand"])
                self.assertNotIn(victim.object_id, game.state.players["B"].zones["hand"])
                self.assertEqual("battlefield", game.state.cards[wrong.object_id].zone)
                self.replay(game, load=True)

    def test_actual_soporific_land_face_pays_or_enters_tapped_and_replays(self):
        for pay in (False, True):
            with self.subTest(pay=pay):
                game = self.session(11224031 + pay)
                source = self.add(game.engine, SOURCE, zone="hand")
                self.prepare(game)
                self.checkpoint(game)
                before = game.state.players["A"].life
                actions = game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
                land, = (action for action in actions if action["action"] == "play_land" and action.get("card") == source.ref)
                self.accepted(game, "A", {"action_id": land["id"], "pay_life": pay})
                actual = game.state.cards[source.object_id]
                self.assertEqual(("battlefield", "Soporific Springs", not pay), (actual.zone, actual.active_face, actual.tapped))
                self.assertEqual(before - (3 if pay else 0), game.state.players["A"].life)
                self.assertFalse(game.state.stack)
                if pay:
                    actions = game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
                    mana, = (action for action in actions if action["id"].startswith("activate:" + source.ref + ":"))
                    self.accepted(game, "A", {"action_id": mana["id"], "pay": "auto"})
                    self.assertEqual(1, game.state.players["A"].mana_pool["U"])
                self.replay(game, load=True)
