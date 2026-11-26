"""Native tests for the fixed creature-or-planeswalker death replacement."""

from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
import test_damage_death_exile as death_witnesses
from quorune.carddb import CardDatabase
from quorune.damage_death_exile_model import DAMAGE_DEATH_EXILE_FIELD, DamageDeathExileScope, ResolvedDamageDeathExile
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database


ORIGINALS = ("Fanged Flames", "Obliterating Bolt", "Flame-Blessed Bolt", "Scorching Dragonfire")


class DamagePlaneswalkerExileTests(InheritedNativeFixture, unittest.TestCase):
    prevent = death_witnesses.DamageDeathExileNativeTests.prevent

    @classmethod
    def use_database(cls, path):
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Planeswalker exile witnesses", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"), DeckEntry("Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "damage-planeswalker-exile.sqlite3"
        build_fixture_database(ROOT / "tests/fixtures/damage-planeswalker-exile-cards.json", path)
        cls.use_database(path)

    def test_four_originals_exile_the_actual_planeswalker_at_zero_loyalty(self):
        for index, name in enumerate(ORIGINALS):
            with self.subTest(name=name):
                game = self.session(704005900 + index)
                game.state.config.semantic_policy = "trusted_only"
                spell = self.add(game.engine, name, zone="hand")
                walker = self.add(game.engine, "Jace Beleren", seat="B")
                walker.counters["loyalty"] = 2
                walker.annotations["loyalty_initialized"] = True
                for card in (spell, walker):
                    self.assert_closed(game, card)
                self.prepare(game, mana={"C": 1, "R": 1})
                self.checkpoint(game)
                self.cast_for(game, "A", spell, [walker.ref])
                self.assertIsNone(self.resolve(game))
                self.assertEqual("exile", game.state.cards[walker.object_id].zone)
                self.replay(game, load=True)

    def test_prevented_damage_still_locks_the_announced_planeswalker(self):
        game = self.session(704005910)
        game.state.config.semantic_policy = "trusted_only"
        bolt = self.add(game.engine, "Flame-Blessed Bolt", zone="hand")
        destroy = self.add(game.engine, "Hero's Downfall", zone="hand")
        walker = self.add(game.engine, "Jace Beleren", seat="B")
        walker.counters["loyalty"] = 3
        walker.annotations["loyalty_initialized"] = True
        save = self.add(game.engine, "Healing Salve", seat="B", zone="hand")
        self.add(game.engine, "Plains", seat="B")
        self.add(game.engine, "Swamp", ref="first-swamp")
        self.add(game.engine, "Swamp", ref="second-swamp")
        self.add(game.engine, "Wastes")
        for card in (bolt, destroy, walker, save):
            self.assert_closed(game, card)
        self.prepare(game, mana={"R": 1})
        self.checkpoint(game)
        self.cast_for(game, "A", bolt, [walker.ref])
        self.prevent(game, "B", save, walker)
        self.assertIsNone(self.resolve(game))
        self.assertEqual(3, game.state.cards[walker.object_id].counters["loyalty"])
        rule, = [effect for effect in game.state.continuous_effects if isinstance(effect, ResolvedDamageDeathExile)]
        self.assertEqual(walker.logical_object_id, rule.locked_objects[0].logical_object_id)
        game = self.replay(game, load=True)
        self.cast_for(game, "A", destroy, [walker.ref])
        self.assertIsNone(self.resolve(game))
        self.assertEqual("exile", game.state.cards[walker.object_id].zone)
        self.replay(game, load=True)

    def test_original_target_domain_and_followup_antecedent_must_agree(self):
        card = self.db.lookup(ORIGINALS[0], fuzzy=False)

        def compile_source(source):
            return compile_oracle_card(source, capability_registry=self.registry, capability_profile="commander_review")

        compiled = compile_source(card)
        self.assertEqual("exact", compiled.status, compiled.material_residuals)
        damage, = [node for node in compiled.faces[0].nodes if node.effects]
        self.assertEqual(DamageDeathExileScope.TARGET_CREATURE_OR_PLANESWALKER.value,
                         damage.effects[0][DAMAGE_DEATH_EXILE_FIELD])
        for text in (
            card.oracle_text.replace("target creature or planeswalker", "any target"),
            card.oracle_text.replace("that creature or planeswalker", "that creature"),
            card.oracle_text.replace("target creature or planeswalker", "target creature"),
        ):
            with self.subTest(text=text):
                self.assertNotEqual("exact", compile_source(replace(card, oracle_text=text)).status)
