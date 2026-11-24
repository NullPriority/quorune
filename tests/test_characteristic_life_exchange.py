from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch
import unittest

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.attachments import attach_objects
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.effect_runtime import characteristic_life_exchange as resolution
from quorune.errors import GameRuleError
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.characteristic_life_exchange import (
    CHARACTERISTIC_LIFE_EXCHANGE_CAPABILITY, CharacteristicLifeExchangeSpec,
    characteristic_life_exchange_program_dependencies, plan_characteristic_life_exchange,
)
from scripts.build_test_database import build_fixture_database


class CharacteristicLifeExchangeTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "characteristic-life-exchange.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures/characteristic-life-exchange-cards.json"], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        commander = "Isamaru, Hound of Konda"
        cls.deck = DeckDefinition("Numerical exchange", [DeckEntry(commander, 1, "commander"),
                                                       DeckEntry("Plains", 30)], [commander])

    def activation(self, game, source, *targets):
        ability, = (row for row in game.engine._activated_abilities(source) if not row.mana_ability)
        return {"action_id": "activate:" + source.ref + ":" + ability.ability_id,
                "targets": list(targets), "pay": "auto"}

    def activate(self, game, source, *targets):
        result = game.act("pilot:A", self.activation(game, source, *targets))
        self.assertTrue(result.ok, result.summary)

    def attach(self, game, name, creature):
        enhancement = self.add(game.engine, name)
        self.assert_closed(game, enhancement)
        attach_objects(game.state.cards, enhancement, creature, source_timestamp=enhancement.zone_timestamp,
                       players=game.state.players)

    def test_printed_evra_exchanges_effective_power_and_sets_base_power(self):
        game = self.session(701120701)
        evra = self.add(game.engine, "Evra, Halcyon Witness")
        self.attach(game, "Bonesplitter", evra)
        self.assert_closed(game, evra)
        game.state.players["A"].life = 7
        self.prepare(game, mana={"C": 4})
        self.checkpoint(game)
        self.activate(game, evra)
        self.assertEqual(6, game.engine._numeric_stat(evra.object_id, "power"))
        self.assertIsNone(self.resolve(game))
        self.assertEqual((6, 9, 4), (game.state.players["A"].life,
            game.engine._numeric_stat(evra.object_id, "power"),
            game.engine._numeric_stat(evra.object_id, "toughness")))
        self.replay(game, load=True)

    def test_printed_tree_respects_original_incarnation_after_a_blink(self):
        game = self.session(701120702)
        tree = self.add(game.engine, "Tree of Redemption")
        response = self.add(game.engine, "Flicker of Fate", seat="B", zone="hand")
        for card in (tree, response):
            self.assert_closed(game, card)
        game.state.players["A"].life = 8
        self.prepare(game)
        game.state.players["B"].mana_pool.update({"C": 1, "W": 1})
        self.checkpoint(game)
        original = tree.logical_object_id
        self.activate(game, tree)
        self.cast_for(game, "B", response, [tree.ref])
        self.resolve_top(game)
        self.assertNotEqual(original, game.state.cards[tree.object_id].logical_object_id)
        self.assertIsNone(self.resolve(game))
        self.assertEqual((8, 13), (game.state.players["A"].life,
                                 game.engine._numeric_stat(tree.object_id, "toughness")))
        self.replay(game, load=True)

    def test_printed_perdition_keeps_its_announced_opponent_after_control_change(self):
        game = self.session(701120703)
        tree = self.add(game.engine, "Tree of Perdition")
        response = self.add(game.engine, "Word of Seizing", seat="C", zone="hand")
        for card in (tree, response):
            self.assert_closed(game, card)
        game.state.players["B"].life = 20
        self.prepare(game)
        game.state.players["C"].mana_pool.update({"C": 3, "R": 2})
        self.checkpoint(game)
        before = authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:A", self.activation(game, tree, "A")).ok)
        self.assertFalse(game.act("pilot:C", self.activation(game, tree, "B")).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        self.activate(game, tree, "B")
        self.cast_for(game, "C", response, [tree.ref])
        self.resolve_top(game)
        self.assertEqual("C", game.state.cards[tree.object_id].controller)
        self.assertIsNone(self.resolve(game))
        self.assertEqual((40, 13, 40, 40), tuple(game.state.players[s].life for s in "ABCD"))
        self.assertEqual(20, game.engine._numeric_stat(tree.object_id, "toughness"))
        self.replay(game, load=True)

    def test_prohibited_life_gain_cancels_the_whole_exchange(self):
        game = self.session(701120704)
        tree = self.add(game.engine, "Tree of Redemption")
        prohibition = self.add(game.engine, "Giant Cindermaw", seat="B")
        for card in (tree, prohibition):
            self.assert_closed(game, card)
        game.state.players["A"].life = 5
        self.prepare(game)
        self.checkpoint(game)
        self.activate(game, tree)
        self.assertIsNone(self.resolve(game))
        self.assertTrue(game.state.cards[tree.object_id].tapped)
        self.assertEqual((5, 13), (game.state.players["A"].life,
                                 game.engine._numeric_stat(tree.object_id, "toughness")), "both exchange values")
        self.replay(game, load=True)

    def test_replacement_pause_keeps_both_values_and_commits_after_exact_replay(self):
        game = self.session(701120705)
        tree = self.add(game.engine, "Tree of Redemption")
        doublers = [self.add(game.engine, "Boon Reflection", ref=f"exchange-reflection-{index}") for index in range(2)]
        for card in (tree, *doublers):
            self.assert_closed(game, card)
        game.state.players["A"].life = 10
        self.prepare(game)
        self.checkpoint(game)
        self.activate(game, tree)
        decision = self.resolve(game)
        self.assertEqual("replacement.order", decision.kind)
        self.assertEqual(["pilot:A"], list(game.pending_principals()))
        self.assertEqual((10, 13), (game.state.players["A"].life,
                                  game.engine._numeric_stat(tree.object_id, "toughness")))
        game = self.replay(game, load=True)
        option = game.packet("pilot:A", full=True)["decision"]["ctx"]["options"][0]["id"]
        before = authoritative_state_hash(game.state)
        choice = {"action_id": "choose", "replacement": option}
        self.assertFalse(game.act("pilot:B", choice).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))

        def fail_after_characteristic(host, prepared):
            self.assertEqual(10, host._numeric_stat(tree.object_id, "toughness"))
            raise GameRuleError("Injected exchange commit fault")

        with patch("quorune.effect_runtime.life_effects.commit_prepared_life_effect_changes", fail_after_characteristic):
            rejected = game.act("pilot:A", choice)
        self.assertFalse(rejected.ok)
        self.assertIn("Injected exchange commit fault", rejected.summary)
        self.assertEqual(before, authoritative_state_hash(game.state))
        result = game.act("pilot:A", choice)
        self.assertTrue(result.ok, result.summary)
        self.assertIsNone(self.resolve(game))
        self.assertEqual((22, 10), (game.state.players["A"].life,
                                  game.engine._numeric_stat(tree.object_id, "toughness")))
        self.replay(game, load=True)

    def test_exchange_shape_rejects_wrong_scope_and_preserves_numeric_boundaries(self):
        spec = CharacteristicLifeExchangeSpec("toughness", "target_opponent")
        valid = dict(event="activate", active_zone="battlefield", effects=(spec.to_instruction(),),
                     target_schema=spec.to_target_schema())
        self.assertIn(CHARACTERISTIC_LIFE_EXCHANGE_CAPABILITY,
                      characteristic_life_exchange_program_dependencies(SimpleNamespace(**valid)))
        for changes in (
            {"target_schema": None}, {"event": "step.begin"}, {"active_zone": "graveyard"},
            {"effects": ({**spec.to_instruction(), "player": "$controller"},)},
        ):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                characteristic_life_exchange_program_dependencies(SimpleNamespace(**{**valid, **changes}))
        negative = plan_characteristic_life_exchange(life_total=2, effective_characteristic=-1,
                                                     can_gain_life=True, can_lose_life=True)
        self.assertEqual((-3, 2), (negative.life_delta, negative.new_base_characteristic))
        self.assertIsNone(plan_characteristic_life_exchange(life_total=2, effective_characteristic=-1,
                                                            can_gain_life=True, can_lose_life=False))

    def test_missing_gain_gate_mutation_is_rejected_by_actual_exchange(self):
        def skip_prohibition(**values):
            return plan_characteristic_life_exchange(**{**values, "can_gain_life": True})

        with patch.object(resolution, "plan_characteristic_life_exchange", side_effect=skip_prohibition):
            with self.assertRaisesRegex(AssertionError, "both exchange values"):
                self.test_prohibited_life_gain_cancels_the_whole_exchange()
