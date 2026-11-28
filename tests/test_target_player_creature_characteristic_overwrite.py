"""Resolution locks one player's original creatures into ordinary layered changes."""

from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
import test_next_player_untap_restrictions as native_turns
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.resolution_characteristic_model import TARGET_PLAYER_CREATURE_OVERWRITE_VERSION
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


class TargetPlayerCreatureCharacteristicOverwriteTests(InheritedNativeFixture, unittest.TestCase):
    playing = native_turns.NextPlayerUntapRestrictionTests.playing

    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "target-player-creature-characteristic-overwrite.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures/target-player-creature-characteristic-overwrite-cards.json"], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Fixed current creatures and layers", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"), DeckEntry("Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def test_complete_originals_keep_exact_result_and_split_second(self):
        for name, form, sibling_count, changed_stat in (
            ("Polymorphist's Jest", "blue_frog", 1, "1/1"),
            ("Sudden Spoiling", "zero_two", 2, "0/2"),
        ):
            with self.subTest(name=name):
                admitted, semantics = register_card(self.db, self.registry, name)
                self.assertTrue(admitted["strict_capability_ready"], admitted["blockers"])
                record = self.db.lookup(name, fuzzy=False)
                programs = semantics.programs_for_oracle(record.oracle_id)
                self.assertEqual(sibling_count, len(programs))
                spell, = (program for program in programs if program.event == "resolve")
                effect, = spell.effects
                self.assertEqual((TARGET_PLAYER_CREATURE_OVERWRITE_VERSION, form),
                                 (effect["schema_version"], effect["characteristics"]["form"]))
                self.assertEqual("any", spell.target_schema["player_relation"])
                changed = {**effect, "predicate": {**effect["predicate"], "controller": "$controller"}}
                self.assertFalse(bind_semantic_program_runtime(
                    replace(spell, effects=[changed]), capability_registry=self.registry,
                    profile="commander_review",
                )["strict"])
                oracle = record.oracle_text.replace(f"base power and toughness {changed_stat}", "base power and toughness 2/4")
                self.assertNotEqual("exact", compile_oracle_card(
                    replace(record, oracle_text=oracle), capability_registry=self.registry,
                    capability_profile="commander_review",
                ).status)

    def current(self, game, card):
        return game.state.cards[card.object_id]

    def stats(self, game, card):
        return tuple(game.engine._numeric_stat(card.object_id, key) for key in ("power", "toughness"))

    def effective(self, game, card):
        return game.engine._effective_card_data(self.current(game, card))

    def types(self, game, card):
        return game.engine._type_parts(self.effective(game, card)["type_line"])

    def keywords(self, game, card):
        return game.engine._combat_keywords(self.current(game, card))

    def test_actual_jest_preserves_other_types_and_counters_and_expires_on_original_incarnations(self):
        game = self.playing(61123101)
        spell = self.add(game.engine, "Polymorphist's Jest", zone="hand")
        wing = self.add(game.engine, "Ornithopter", seat="B")
        base = self.add(game.engine, "Wind Drake", seat="B")
        other = self.add(game.engine, "Grizzly Bears", seat="C")
        noncreature = self.add(game.engine, "Sol Ring", seat="B")
        blink = self.add(game.engine, "Cloudshift", seat="B", zone="hand")
        wing.counters["+1/+1"] = 1
        for card in (spell, wing, base, blink):
            self.assert_closed(game, card)
        self.prepare(game, mana={"C": 1, "U": 2})
        game.state.players["B"].mana_pool["W"] = 1
        self.checkpoint(game)
        self.cast_for(game, "A", spell, ["B"])
        self.assertIsNone(self.resolve(game))
        self.assertEqual(({"artifact", "creature"}, {"frog"}, set()), self.types(game, wing))
        self.assertEqual(["U"], self.effective(game, wing)["colors"])
        self.assertEqual((2, 2), self.stats(game, wing))
        self.assertEqual((1, 1), self.stats(game, base))
        self.assertNotIn("flying", self.keywords(game, wing))
        self.assertEqual((2, 2), self.stats(game, other))
        self.assertEqual({"artifact"}, self.types(game, noncreature)[0])
        original = self.current(game, wing).logical_object_id
        game = self.replay(game, load=True)
        self.cast_for(game, "B", blink, [wing.ref])
        self.assertIsNone(self.resolve(game))
        self.assertNotEqual(original, self.current(game, wing).logical_object_id)
        self.assertEqual((0, 2), self.stats(game, wing))
        self.assertIn("flying", self.keywords(game, wing))
        self.assertEqual((1, 1), self.stats(game, base))
        self.until(game, lambda current: current.state.active_player == "B" and current.state.step == "upkeep")
        self.assertEqual((2, 2), self.stats(game, base))
        self.assertIn("flying", self.keywords(game, base))
        self.replay(game, load=True)

    def test_actual_jest_does_not_restore_creature_type_after_devotion_drops(self):
        game = self.session(61123102)
        spell = self.add(game.engine, "Polymorphist's Jest", zone="hand")
        god = self.add(game.engine, "Thassa, God of the Sea", seat="B")
        devotion = self.add(game.engine, "Arcanis the Omnipotent", seat="B")
        self.add(game.engine, "Wind Drake", seat="B")
        bounce = self.add(game.engine, "Unsummon", seat="B", zone="hand")
        for card in (spell, god, devotion, bounce):
            self.assert_closed(game, card)
        self.prepare(game, mana={"C": 1, "U": 2})
        game.state.players["B"].mana_pool["U"] = 1
        self.checkpoint(game)
        self.assertIn("creature", self.types(game, god)[0])
        self.cast_for(game, "A", spell, ["B"])
        self.assertIsNone(self.resolve(game))
        self.assertEqual(({"enchantment", "creature"}, {"frog"}, {"legendary"}), self.types(game, god))
        self.assertEqual((1, 1), self.stats(game, god))
        self.cast_for(game, "B", bounce, [devotion.ref])
        self.assertIsNone(self.resolve(game))
        types, subtypes, supertypes = self.types(game, god)
        self.assertEqual({"enchantment"}, types)
        self.assertNotIn("frog", subtypes)
        self.assertEqual({"legendary"}, supertypes)
        self.assertEqual(["U"], self.effective(game, god)["colors"])
        self.assertNotIn("indestructible", self.keywords(game, god))
        self.replay(game, load=True)

    def test_actual_sudden_spoiling_keeps_split_second_and_the_original_types(self):
        game = self.session(61123103)
        spell = self.add(game.engine, "Sudden Spoiling", zone="hand")
        target = self.add(game.engine, "Wind Drake", seat="B", ref="spoiling-target")
        other = self.add(game.engine, "Wind Drake", seat="C", ref="spoiling-unrelated")
        bounce = self.add(game.engine, "Unsummon", seat="B", zone="hand")
        target.counters["+1/+1"] = 1
        for card in (spell, target, bounce):
            self.assert_closed(game, card)
        self.prepare(game, mana={"C": 1, "B": 2})
        game.state.players["B"].mana_pool["U"] = 1
        self.checkpoint(game)
        self.cast_for(game, "A", spell, ["B"])
        self.priority(game, "B")
        actions = game.packet("pilot:B", full=True)["decision"]["ctx"]["legal"]["actions"]
        self.assertNotIn("cast:" + bounce.ref, {action["id"] for action in actions})
        before = authoritative_state_hash(game.state)
        denied = game.act("pilot:B", {"action_id": "cast:" + bounce.ref, "targets": [target.ref], "pay": "auto"})
        self.assertFalse(denied.ok, denied.summary)
        self.assertEqual(before, authoritative_state_hash(game.state))
        self.assertIsNone(self.resolve(game))
        self.assertEqual((1, 3), self.stats(game, target))
        self.assertEqual(({"creature"}, {"drake"}, set()), self.types(game, target))
        self.assertEqual(["U"], self.effective(game, target)["colors"])
        self.assertNotIn("flying", self.keywords(game, target))
        self.assertIn("flying", self.keywords(game, other))
        game = self.replay(game, load=True)
        self.cast_for(game, "B", bounce, [target.ref])
        self.assertIsNone(self.resolve(game))
        self.assertEqual("hand", self.current(game, target).zone)
        self.replay(game, load=True)
