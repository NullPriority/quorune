"""Native payment and source-zone authority for six fixed restricted green mana."""

from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
import test_next_player_untap_restrictions as native_turns
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.mana_provenance import restricted_mana
from quorune.mana_restrictions import ability_mana_spend_context, mana_restriction_allows
from quorune.mana_spend_rules import ManaSpendRule, ManaSpendRuleKind, decode_mana_spend_rule
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


CASTLE = "Castle Garenbrig"


class FixedCreatureSpellOrBattlefieldAbilityManaTests(InheritedNativeFixture, unittest.TestCase):
    playing = native_turns.NextPlayerUntapRestrictionTests.playing

    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "fixed-creature-spell-or-battlefield-ability-mana.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures/fixed-creature-spell-or-battlefield-ability-mana-cards.json"], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Restricted creature mana", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"), DeckEntry("Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def actions(self, game):
        self.priority(game, "A")
        return game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]

    def activations(self, game, card):
        return [row for row in self.actions(game) if row["id"].startswith(f"activate:{card.ref}:")]

    def activate(self, game, action):
        result = game.act("pilot:A", {"action_id": action["id"], "pay": "auto"})
        self.assertTrue(result.ok, result.summary)

    def pool(self, game):
        return {color: quantity for color, quantity in game.state.players["A"].mana_pool.items() if quantity}

    def test_actual_paid_six_green_funds_a_creature_spell_and_a_battlefield_creature_ability(self):
        game = self.playing(61127299)
        castle = self.add(game.engine, CASTLE)
        wall = self.add(game.engine, "Giant Spider", zone="hand")
        creature = self.add(game.engine, "Rootwalla")
        sorcery = self.add(game.engine, "Harmonize", zone="hand")
        cycling = self.add(game.engine, "Greater Sandwurm", zone="hand")
        tome = self.add(game.engine, "Jayemdae Tome")
        for card in (castle, wall, creature, sorcery, cycling, tome):
            self.assert_closed(game, card)
        self.prepare(game, actor="A", active="A", mana={"C": 2, "G": 2})
        self.checkpoint(game)
        paid, = (action for action in self.activations(game, castle)
                 if any(action["cost_summary"].get("m", {}).values()))
        before = authoritative_state_hash(game.state)
        foreign = game.act("pilot:B", {"action_id": paid["id"], "pay": "auto"})
        self.assertFalse(foreign.ok, foreign.summary)
        self.assertEqual(before, authoritative_state_hash(game.state))
        self.activate(game, paid)
        self.assertEqual([], game.state.stack)
        self.assertTrue(game.state.cards[castle.object_id].tapped)
        self.assertEqual({"G": 6}, self.pool(game))
        by_rule = restricted_mana(game.state.players["A"])
        encoded, = by_rule.keys()
        self.assertEqual(ManaSpendRuleKind.CREATURE_SPELL_OR_BATTLEFIELD_ABILITY, decode_mana_spend_rule(encoded).kind)
        self.assertEqual(6, by_rule[encoded]["G"])
        game = self.replay(game, load=True)

        offered = {row["id"] for row in self.actions(game)}
        self.assertNotIn("cast:" + sorcery.ref, offered)
        self.assertFalse(self.activations(game, tome))
        self.assertFalse(self.activations(game, cycling))
        before = authoritative_state_hash(game.state)
        denied = game.act("pilot:A", {"action_id": "cast:" + sorcery.ref, "pay": "auto"})
        self.assertFalse(denied.ok, denied.summary)
        self.assertEqual(before, authoritative_state_hash(game.state))

        self.cast_for(game, "A", wall)
        self.assertIsNone(self.resolve(game))
        self.assertEqual("battlefield", game.state.cards[wall.object_id].zone)
        self.assertEqual({"G": 2}, self.pool(game))
        ability, = self.activations(game, creature)
        self.activate(game, ability)
        self.assertIsNone(self.resolve(game))
        self.assertEqual(4, game.engine._numeric_stat(creature.object_id, "power"))
        self.assertEqual({}, self.pool(game))
        self.assertEqual({}, restricted_mana(game.state.players["A"]))
        self.replay(game, load=True)

    def test_actual_forest_entry_and_ordinary_green_are_preserved(self):
        for seed, has_forest in ((61127300, False), (61127301, True)):
            with self.subTest(has_forest=has_forest):
                game = self.playing(seed)
                castle = self.add(game.engine, CASTLE, zone="hand")
                fog = self.add(game.engine, "Fog", zone="hand")
                if has_forest:
                    self.add(game.engine, "Forest")
                self.assert_closed(game, castle)
                self.prepare(game, actor="A", active="A")
                self.checkpoint(game)
                land, = (row for row in self.actions(game) if row["id"] == "play-land:" + castle.ref)
                played = game.act("pilot:A", {"action_id": land["id"]})
                self.assertTrue(played.ok, played.summary)
                self.assertEqual(not has_forest, game.state.cards[castle.object_id].tapped)
                if has_forest:
                    free, = self.activations(game, castle)
                    self.activate(game, free)
                    self.assertEqual({"G": 1}, self.pool(game))
                    self.assertEqual({}, restricted_mana(game.state.players["A"]))
                    self.cast_for(game, "A", fog)
                    self.assertIsNone(self.resolve(game))
                    self.assertEqual("graveyard", game.state.cards[fog.object_id].zone)
                self.replay(game, load=True)

    def test_complete_original_requires_both_mana_abilities_and_the_exact_spend_rule(self):
        original = self.db.lookup(CASTLE, fuzzy=False)
        report, _ = register_card(self.db, self.registry, "Castle Garenbrig")
        self.assertTrue(report["strict_capability_ready"], report["blockers"])
        ir = compile_oracle_card(original, capability_registry=self.registry, capability_profile="commander_review")
        self.assertEqual("exact", ir.status)
        self.assertEqual(2, sum(node.kind == "mana_ability" for face in ir.faces for node in face.nodes))
        encoded = ManaSpendRule(ManaSpendRuleKind.CREATURE_SPELL_OR_BATTLEFIELD_ABILITY).encode()
        context = ability_mana_spend_context(
            "Artifact", source_zone="battlefield", source_type_line="Artifact Creature",
        )
        self.assertTrue(mana_restriction_allows(encoded, context))
        self.assertTrue(mana_restriction_allows("artifact_spell_or_ability", context))
        self.assertFalse(mana_restriction_allows(encoded, ability_mana_spend_context("Creature", source_zone="hand")))
        for old, new in (("activate abilities of creatures", "activate abilities of cards you own in exile"),
                         ("six {G}.", "six {G} and gain 1 life.")):
            with self.subTest(new=new):
                changed = replace(original, oracle_text=original.oracle_text.replace(old, new))
                self.assertNotEqual("exact", compile_oracle_card(
                    changed, capability_registry=self.registry, capability_profile="commander_review",
                ).status)
