"""Native chosen-color spell spending and draw payment."""
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
from quorune.mana_provenance import mana_provenance_lots
from quorune.mana_spend_rules import CHOSEN_MONOCOLORED_MANA_CAPABILITY, decode_mana_spend_rule
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.chosen_color_draw_price import CAPABILITY as DRAW_CAPABILITY, FIELD
from scripts.build_test_database import build_fixture_database
import test_chosen_color_mana as chosen_native


class ChosenColorActivationsTests(InheritedNativeFixture, unittest.TestCase):
    choose_color = chosen_native.ChosenColorManaRuntimeTests.choose_color

    @classmethod
    def setUpClass(cls):
        cls.temp = TemporaryDirectory()
        path = Path(cls.temp.name) / "chosen-color-activations.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures/chosen-color-activations-cards.json"], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        commander = "Isamaru, Hound of Konda"
        cls.deck = DeckDefinition("Chosen color test", [DeckEntry(commander, 1, "commander"),
                                                      DeckEntry("Plains", 30)], [commander])

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temp.cleanup()

    def accepted(self, game, command):
        result = game.act("pilot:A", command)
        self.assertTrue(result.ok, result.summary)

    def rejected(self, game, command):
        before = authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:A", command).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))

    def offers(self, game):
        self.priority(game, "A")
        return game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]

    def enter(self, game, source):
        self.accepted(game, {"action_id": f"cast:{source.ref}", "pay": "manual", "payment": {"C": 5}})
        self.choose_color(game, "R")
        source = game.state.cards[source.object_id]
        self.assertEqual(("battlefield", "R"), (source.zone, source.annotations["chosen_color"]))
        return source

    def test_exact_chosen_color_activation_shapes_reject_unsealed_payment_and_output(self):
        game = self.session(106401)
        source = self.add(game.engine, "Throne of Eldraine", zone="hand")
        self.assert_closed(game, source)
        record = self.db.by_oracle_id(source.oracle_id)
        def compile_card(value):
            return compile_oracle_card(value, capability_registry=self.registry, capability_profile="commander_review")
        self.assertEqual("exact", compile_card(record).status)
        for text in (record.oracle_text.replace("monocolored", "multicolored"),
                     record.oracle_text.replace("Draw two cards", "Draw three cards")):
            with self.subTest(text=text):
                self.assertNotEqual("exact", compile_card(replace(record, oracle_text=text)).status)
        programs = game.engine.semantics.programs_for_oracle(source.oracle_id)
        draw, = (value for value in programs if DRAW_CAPABILITY in value.capability_dependencies)
        invalid = replace(draw, cost_schema={key: value for key, value in draw.cost_schema.items() if key != FIELD})
        proof = bind_semantic_program_runtime(invalid, capability_registry=self.registry, profile="commander_review")
        self.assertFalse(proof["strict"], proof)
        self.assertTrue(any(CHOSEN_MONOCOLORED_MANA_CAPABILITY in value.capability_dependencies for value in programs))

    def test_actual_throne_mana_binds_chosen_mono_spell_color_in_offers_and_payment(self):
        for manual in (False, True):
            with self.subTest(manual=manual):
                game = self.session(106402 + manual)
                source = self.add(game.engine, "Throne of Eldraine", zone="hand")
                red = self.add(game.engine, "Lightning Bolt", zone="hand")
                hybrid = self.add(game.engine, "Rakdos Shred-Freak", zone="hand")
                colorless = self.add(game.engine, "Sol Ring", zone="hand")
                removal = self.add(game.engine, "Disenchant", seat="B", zone="hand") if manual else None
                self.prepare(game, mana={"C": 5})
                if manual:
                    game.state.players["B"].mana_pool.update({"C": 1, "W": 1})
                self.checkpoint(game)
                source = self.enter(game, source)
                if manual:
                    activation, = (r for r in self.offers(game) if r.get("source") == source.ref)
                    command = {"action_id": activation["id"], "pay": "auto"}
                    self.rejected(game, {**command, "mana_output": {"U": 4}})
                    self.accepted(game, {**command, "mana_output": {"R": 4}})
                    self.assertEqual(4, game.state.players["A"].mana_pool["R"])
                    self.cast(game, "B", removal, [source.ref])
                    self.resolve_top(game)
                    self.assertEqual("graveyard", game.state.cards[source.object_id].zone)
                for wrong in (hybrid, colorless):
                    self.rejected(game, {"a": "cast", "card": wrong.ref, "pay": "auto"})
                self.cast(game, "A", red, ["B"])
                if not manual:
                    self.assertTrue(game.state.cards[source.object_id].tapped)
                self.assertEqual(3, game.state.players["A"].mana_pool["R"])
                restricted, = mana_provenance_lots(game.state.players["A"])
                self.assertEqual("R", decode_mana_spend_rule(restricted.restriction).chosen_type)
                self.assertEqual((("R", 3),), restricted.bundle)
                game = self.replay(game, load=True)
                self.resolve_top(game)
                self.assertEqual(37, game.state.players["B"].life)
                self.replay(game, load=True)

    def test_actual_throne_draw_requires_current_chosen_color_and_keeps_draw_private(self):
        for payable in (False, True):
            with self.subTest(payable=payable):
                game = self.session(106404 + payable)
                source = self.add(game.engine, "Throne of Eldraine", zone="hand")
                draws = [self.add(game.engine, "Mountain", zone="library", ref=f"private-rise-{index}")
                         for index in range(2)]
                self.add(game.engine, "Chromatic Orrery").tapped = True
                self.prepare(game, mana={"C": 5, "R" if payable else "U": 3})
                self.checkpoint(game)
                source = self.enter(game, source)
                actions = [r for r in self.offers(game) if r.get("source") == source.ref
                           and r["cost_summary"].get("chosen_payment_color") == "R"]
                if not payable:
                    self.assertFalse(actions)
                    self.rejected(game, {"a": "activate", "source": source.ref, "ability": "ab3", "pay": "auto"})
                    self.replay(game, load=True)
                    continue
                action, = actions
                command = {"action_id": action["id"], "pay": "manual", "payment": {"R": 3}}
                self.rejected(game, {**command, "payment": {"U": 3}})
                self.accepted(game, command)
                self.assertEqual(0, game.state.players["A"].mana_pool["R"])
                self.assertTrue(game.state.cards[source.object_id].tapped)
                game = self.replay(game, load=True)
                self.resolve_top(game)
                for card in draws:
                    self.assertIn(card.object_id, game.state.players["A"].zones["hand"])
                    self.assertNotIn(card.ref, json.dumps(game.packet("spectator", full=True)))
                self.replay(game, load=True)
