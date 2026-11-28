"""Native distinct-controller selection, resolution, and original incarnation LKI."""

import json
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture

from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.paired_target_controllers import PAIRED_CONTROLLER_CAPABILITY
from scripts.build_test_database import build_fixture_database


SOURCE = "Run Away Together"


class PairedTargetControllersTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        work = Path(cls.temporary.name)
        fixture = ROOT / "tests/fixtures"
        inputs = [fixture / "paired-target-controller-cards.json", fixture / "next-personal-turn-protection-cards.json"]
        for file, names in (
            ("combat-damage-dealer-recipient-control-cards.json", {"Word of Seizing"}),
            ("activated-self-transform-cards.json", {"Ephemerate"}),
        ):
            payload = json.loads((fixture / file).read_bytes())
            payload["cards"] = [row for row in payload["cards"] if row["name"] in names]
            assert {row["name"] for row in payload["cards"]} == names
            ids = {row["oracle_id"] for row in payload["cards"]}
            payload["rulings"] = [row for row in payload.get("rulings", []) if row["oracle_id"] in ids]
            copy = work / file
            copy.write_text(json.dumps(payload), encoding="utf-8")
            inputs.append(copy)
        database = work / "paired-controllers.sqlite3"
        build_fixture_database(inputs, database)
        cls.db = CardDatabase(database)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Paired target controllers", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"), DeckEntry("Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def start(self, game, *cards):
        game.state.config.semantic_policy = "trusted_only"
        for card in cards:
            self.assert_closed(game, card)
        self.prepare(game, mana={"C": 1, "U": 1})
        self.checkpoint(game)

    def offers(self, game):
        self.priority(game, "A")
        return {row["id"]: row for row in game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]}

    def test_retains_complete_source_and_requires_the_exact_pair_shape(self):
        card = self.db.lookup(SOURCE, fuzzy=False)
        ir = compile_oracle_card(card, capability_registry=self.registry, capability_profile="commander_review")
        self.assertEqual("exact", ir.status, ir.material_residuals)
        node, = ir.faces[0].nodes
        self.assertEqual(card.oracle_text, card.oracle_text[node.span.start:node.span.end])
        self.assertIs(node.target_schema["distinct_controllers"], True)
        self.assertEqual(2, node.target_schema["count"])
        self.assertNotIn("retain_controller", node.target_schema)
        self.assertIn(PAIRED_CONTROLLER_CAPABILITY, node.capability_dependencies)
        for old, new in (
            ("two target creatures", "three target creatures"),
            ("different players", "the same player"),
            ("their owners' hands", "their controllers' hands"),
        ):
            with self.subTest(new=new):
                changed = replace(card, oracle_text=card.oracle_text.replace(old, new))
                self.assertNotEqual("exact", compile_oracle_card(
                    changed, capability_registry=self.registry, capability_profile="commander_review",
                ).status)
        game = self.session(60820701)
        source = self.add(game.engine, SOURCE, zone="hand")
        self.assert_closed(game, source)
        program, = game.engine.semantics.programs_for_oracle(card.oracle_id)
        remaining = [value for value in program.capability_dependencies if value != PAIRED_CONTROLLER_CAPABILITY]
        closure = self.registry.closure(remaining, profile="commander_review")
        changed = replace(program, capability_dependencies=remaining, capability_closure=closure.to_dict())
        self.assertFalse(bind_semantic_program_runtime(
            changed, capability_registry=self.registry, profile="commander_review",
        )["strict"])

    def test_offer_absent_without_distinct_controllers(self):
        game = self.session(60820702)
        source = self.add(game.engine, SOURCE, zone="hand")
        self.add(game.engine, "Grizzly Bears")
        self.add(game.engine, "Grizzly Bears", ref="same-controller")
        self.start(game, source)
        self.assertNotIn("cast:" + source.ref, self.offers(game))
        self.replay(game, load=True)

    def test_legal_pair_keeps_changed_controller_and_returns_to_physical_owners(self):
        game = self.session(60820703)
        source = self.add(game.engine, SOURCE, zone="hand")
        first = self.add(game.engine, "Grizzly Bears")
        second = self.add(game.engine, "Grizzly Bears", seat="C", ref="partner")
        other = self.add(game.engine, "Grizzly Bears", ref="same-controller")
        theft = self.add(game.engine, "Word of Seizing", seat="D", zone="hand")
        game.state.players["D"].mana_pool.update({"C": 3, "R": 2})
        self.start(game, source, theft)
        for seat in "BCD":
            self.assertNotIn(source.ref, json.dumps(game.packet("pilot:" + seat, full=True)))
        action = "cast:" + source.ref
        self.assertIn(action, self.offers(game))
        before = authoritative_state_hash(game.state)
        for invalid in ([first.ref, other.ref], [first.ref, first.ref]):
            result = game.act("pilot:A", {"action_id": action, "targets": invalid, "pay": "auto"})
            self.assertFalse(result.ok)
            self.assertEqual(before, authoritative_state_hash(game.state))
        self.cast(game, "A", source, [first.ref, second.ref])
        game = self.replay(game, load=True)
        self.cast_for(game, "D", theft, [second.ref])
        self.resolve_top(game)
        self.assertEqual(("C", "D"), (game.state.cards[second.object_id].owner, game.state.cards[second.object_id].controller))
        self.resolve_top(game)
        self.assertIn(first.object_id, game.state.players["A"].zones["hand"])
        self.assertIn(second.object_id, game.state.players["C"].zones["hand"])
        self.assertEqual("battlefield", game.state.cards[other.object_id].zone)
        self.replay(game, load=True)

    def test_same_current_controller_makes_both_targets_illegal(self):
        game = self.session(60820704)
        source = self.add(game.engine, SOURCE, zone="hand")
        first = self.add(game.engine, "Grizzly Bears", seat="B")
        second = self.add(game.engine, "Grizzly Bears", seat="C", ref="partner")
        theft = self.add(game.engine, "Word of Seizing", seat="B", zone="hand")
        game.state.players["B"].mana_pool.update({"C": 3, "R": 2})
        self.start(game, source, theft)
        self.cast(game, "A", source, [first.ref, second.ref])
        self.cast_for(game, "B", theft, [second.ref])
        self.resolve_top(game)
        self.assertEqual("B", game.state.cards[second.object_id].controller)
        self.resolve_top(game)
        self.assertEqual(["battlefield", "battlefield"], [game.state.cards[card.object_id].zone for card in (first, second)])
        self.assertEqual("graveyard", game.state.cards[source.object_id].zone)
        self.replay(game, load=True)

    def test_stale_original_uses_last_battlefield_controller_not_new_or_announced_controller(self):
        for thief, destination in (("B", "battlefield"), ("D", "hand")):
            with self.subTest(thief=thief):
                game = self.session(60820705)
                source = self.add(game.engine, SOURCE, zone="hand")
                first = self.add(game.engine, "Grizzly Bears", seat="B")
                second = self.add(game.engine, "Grizzly Bears", seat="C", ref="returning-new-object")
                theft = self.add(game.engine, "Word of Seizing", seat=thief, zone="hand")
                blink = self.add(game.engine, "Ephemerate", seat=thief, zone="hand")
                original = second.logical_object_id
                game.state.players[thief].mana_pool.update({"C": 3, "R": 2, "W": 1})
                self.start(game, source, theft, blink)
                self.cast(game, "A", source, [first.ref, second.ref])
                self.cast_for(game, thief, theft, [second.ref], reserve={"W": 1})
                self.resolve_top(game)
                self.assertEqual(thief, game.state.cards[second.object_id].controller)
                self.assertEqual(1, game.state.players[thief].mana_pool.get("W"))
                self.cast_for(game, thief, blink, [second.ref])
                self.resolve_top(game)
                returned = game.state.cards[second.object_id]
                self.assertEqual(("C", "battlefield"), (returned.controller, returned.zone))
                self.assertNotEqual(original, returned.logical_object_id)
                game = self.replay(game, load=True)
                self.resolve_top(game)
                self.assertEqual(destination, game.state.cards[first.object_id].zone)
                self.assertEqual("battlefield", game.state.cards[second.object_id].zone)
                self.replay(game, load=True)
