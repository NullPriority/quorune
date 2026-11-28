"""Fixed life Ward grants through normal spell targeting and replay."""

from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.compiler.continuous_templates import fixed_query_keyword_grant_handler
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.semantic_runtime.continuous_components import FixedQueryKeywordGrantHandler
from quorune.semantic_runtime.context import SemanticNodeError
from quorune.trigger_participation import WardSpec
from scripts.build_test_database import build_fixture_database


CARD = "Hexing Squelcher"
FIXTURE = "controlled-life-ward-cards.json"
SUPPORT = {
    "Unsummon": "ability-label-composition-cards.json",
    "Lightning Bolt": "activation-restriction-material-cards.json",
    "Counterspell": "source-cast-copy-cards.json",
}


class ControlledLifeWardGrantTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = TemporaryDirectory()
        work = Path(cls.temp.name)
        fixtures = ROOT / "tests/fixtures"
        base = fixtures / "bound-effect-program-cards.json"
        covered = {row["name"] for row in json.loads(base.read_bytes())["cards"]}
        neighbors = []
        for name, path in SUPPORT.items():
            if name not in covered:
                payload = json.loads((fixtures / path).read_bytes())
                card, = (row for row in payload["cards"] if row["name"] == name)
                neighbors.append(card)
        subset = work / "support.json"
        subset.write_text(json.dumps({"schema_version": 1, "cards": neighbors, "rulings": []}), encoding="utf8")
        db = work / "controlled-life-ward.sqlite3"
        build_fixture_database([base, fixtures / FIXTURE, subset], db)
        cls.db = CardDatabase(db)
        cls.registry = load_default_capability_registry()
        command = "Generic Bound Commander"
        cls.deck = DeckDefinition("Life Ward grant", [
            DeckEntry(command, 1, "commander"), DeckEntry("Generic Bound Plains", 30),
        ], [command])

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temp.cleanup()

    def test_exact_life_ward_grant_requires_original_typed_cost_and_recipient(self):
        card = self.db.lookup(CARD, fuzzy=False)
        def compile(record):
            return compile_oracle_card(record, capability_registry=self.registry, capability_profile="commander_review")
        ir = compile(card)
        self.assertEqual("exact", ir.status, ir.material_residuals)
        node = ir.faces[0].nodes[-1]
        self.assertIn("trigger.keyword.ward.fixed_nonmana", node.capability_dependencies)
        _, descriptor, _ = fixed_query_keyword_grant_handler(card.oracle_text.splitlines()[-1])
        handler = FixedQueryKeywordGrantHandler()
        self.assertEqual((WardSpec(life_payment=2),), handler.validate(descriptor).fragments)
        for change in ({"exclude_source": False}, {"target_controller": "any"}):
            invalid = {**descriptor, "condition": {**descriptor["condition"], **change}}
            with self.subTest(change=change), self.assertRaises(SemanticNodeError):
                handler.validate(invalid)
        invalid = {**descriptor, "modifier": {**descriptor["modifier"], "add_abilities": ["Ward—Pay 3 life"]}}
        with self.assertRaises(SemanticNodeError):
            handler.validate(invalid)
        wrong = replace(card, oracle_text=card.oracle_text.replace('have "Ward—Pay 2 life."', 'have "Ward—Discard a card."'))
        self.assertNotEqual("exact", compile(wrong).status)

    def test_actual_grant_pays_once_and_excludes_the_source_and_foreign_creature(self):
        game = self.session(702210901)
        game.state.config.semantic_policy = "trusted_only"
        source = self.add(game.engine, CARD)
        ally = self.add(game.engine, "Grizzly Bears", ref="ward-controlled-ally")
        foreign = self.add(game.engine, "Grizzly Bears", seat="B", ref="ward-foreign-creature")
        spells = [self.add(game.engine, "Unsummon", seat="B", zone="hand", ref=f"ward-bounce-{i}")
                  for i in range(3)]
        for card in (source, *spells):
            self.assert_closed(game, card)
        self.prepare(game)
        game.state.players["B"].mana_pool.update(U=3)
        self.checkpoint(game)
        self.cast_for(game, "B", spells[0], [foreign.ref])
        self.assertIsNone(self.resolve(game))
        self.assertEqual(40, game.state.players["B"].life)
        self.assertEqual("hand", game.state.cards[foreign.object_id].zone)
        for target, spell, pay in ((ally, spells[1], True), (source, spells[2], False)):
            self.cast_for(game, "B", spell, [target.ref])
            pending = self.resolve(game)
            self.assertEqual(["B"], pending.actors)
            self.assertEqual(1, sum(item.kind == "triggered_ability" for item in game.state.stack))
            game = self.replay(game, load=True)
            before = authoritative_state_hash(game.state)
            self.assertFalse(game.act("pilot:A", {"action_id": "choose", "pay": pay}).ok)
            self.assertEqual(before, authoritative_state_hash(game.state))
            result = game.act("pilot:B", {"action_id": "choose", "pay": pay, "plan": "RULES_CHOICE"})
            self.assertTrue(result.ok, result.summary)
            self.assertIsNone(self.resolve(game))
        self.assertEqual(38, game.state.players["B"].life)
        self.assertEqual("hand", game.state.cards[ally.object_id].zone)
        self.assertEqual("battlefield", game.state.cards[source.object_id].zone)
        self.replay(game, load=True)

    def test_actual_static_spell_protection_composes_with_ordinary_counter_target(self):
        game = self.session(702210902)
        game.state.config.semantic_policy = "trusted_only"
        source = self.add(game.engine, CARD)
        bolt = self.add(game.engine, "Lightning Bolt", zone="hand")
        counter = self.add(game.engine, "Counterspell", seat="B", zone="hand")
        for card in (source, bolt, counter):
            self.assert_closed(game, card)
        self.prepare(game, mana={"R": 1})
        game.state.players["B"].mana_pool.update(U=2)
        self.checkpoint(game)
        self.cast_for(game, "A", bolt, ["C"])
        original = game.state.stack[-1].ref
        self.cast_for(game, "B", counter, [original])
        self.resolve_top(game)
        self.assertEqual([original], [row.ref for row in game.state.stack])
        self.assertIsNone(self.resolve(game))
        self.assertEqual(37, game.state.players["C"].life)
        self.replay(game, load=True)
