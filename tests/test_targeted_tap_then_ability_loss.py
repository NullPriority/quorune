"""An original entry taps its target before a layer-six-only ability loss."""

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
from quorune.resolution_characteristic_model import TARGET_CREATURE_ABILITY_LOSS_VERSION
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


class TargetedTapThenAbilityLossTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "targeted-tap-then-ability-loss.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures/targeted-tap-then-ability-loss-cards.json"], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Tap followed by ability loss", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"), DeckEntry("Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def test_complete_original_keeps_flash_and_the_exact_original_target_and_order(self):
        admitted, semantics = register_card(self.db, self.registry, "Merfolk Trickster")
        self.assertTrue(admitted["strict_capability_ready"], admitted["blockers"])
        record = self.db.lookup("Merfolk Trickster", fuzzy=False)
        programs = semantics.programs_for_oracle(record.oracle_id)
        self.assertEqual(["cast.permission", "permanent.enter.self"], sorted(program.event for program in programs))
        trigger, = (program for program in programs if program.event == "permanent.enter.self")
        tap, loss = trigger.effects
        self.assertEqual({"op": "tap", "card": "$target.0"}, tap)
        self.assertEqual(TARGET_CREATURE_ABILITY_LOSS_VERSION, loss["schema_version"])
        self.assertEqual({"remove_all_abilities": True}, loss["characteristics"])
        self.assertEqual("opponent", trigger.target_schema["controller_relation"])
        for changed in (replace(trigger, effects=[loss, tap]), replace(trigger, effects=[{**tap, "card": "$target.1"}, loss])):
            self.assertFalse(bind_semantic_program_runtime(
                changed, capability_registry=self.registry, profile="commander_review",
            )["strict"])
        oracle = record.oracle_text.replace("an opponent controls", "you control")
        self.assertNotEqual("exact", compile_oracle_card(
            replace(record, oracle_text=oracle), capability_registry=self.registry,
            capability_profile="commander_review",
        ).status)

    def current(self, game, card):
        return game.state.cards[card.object_id]

    def stats(self, game, card):
        return tuple(game.engine._numeric_stat(card.object_id, key) for key in ("power", "toughness"))

    def cast_and_choose(self, game, source, target):
        self.cast_for(game, "A", source)
        self.target(game, "A", [target.ref])

    def test_actual_flash_preserves_queued_ability_but_removes_later_activation_even_after_untap(self):
        game = self.session(61316001)
        source = self.add(game.engine, "Merfolk Trickster", zone="hand")
        creature = self.add(game.engine, "Prodigal Pyromancer", seat="B")
        untap = self.add(game.engine, "Twiddle", seat="B", zone="hand")
        creature.acquired_control_turn_count = -1
        for card in (source, creature, untap):
            self.assert_closed(game, card)
        self.prepare(game, actor="B", active="B", mana={"U": 1})
        game.state.players["A"].mana_pool["U"] = 2
        self.checkpoint(game)
        actions = game.packet("pilot:B", full=True)["decision"]["ctx"]["legal"]["actions"]
        activate, = (action for action in actions if action["id"].startswith(f"activate:{creature.ref}:"))
        action = {"action_id": activate["id"], "targets": ["A"], "pay": "auto"}
        result = game.act("pilot:B", action)
        self.assertTrue(result.ok, result.summary)
        self.assertTrue(self.current(game, creature).tapped)
        self.cast_and_choose(game, source, creature)
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        self.assertEqual(39, game.state.players["A"].life)
        self.assertEqual((1, 1), self.stats(game, creature))
        self.cast_for(game, "B", untap, [creature.ref])
        pending = self.resolve(game)
        self.assertEqual(("semantic.choice", ["B"]), (pending.kind, pending.actors))
        result = game.act("pilot:B", {"action_id": "choose", "choice": "untap"})
        self.assertTrue(result.ok, result.summary)
        self.assertIsNone(self.resolve(game))
        self.assertFalse(self.current(game, creature).tapped)
        self.priority(game, "B")
        actions = game.packet("pilot:B", full=True)["decision"]["ctx"]["legal"]["actions"]
        self.assertNotIn(activate["id"], {value["id"] for value in actions})
        before = authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:B", action).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        self.replay(game, load=True)

    def test_actual_tap_trigger_is_collected_while_the_target_still_has_its_ability(self):
        game = self.session(61316002)
        source = self.add(game.engine, "Merfolk Trickster", zone="hand")
        creature = self.add(game.engine, "Emmara, Soul of the Accord", seat="B")
        for card in (source, creature):
            self.assert_closed(game, card)
        self.prepare(game, mana={"U": 2})
        self.checkpoint(game)
        self.assertFalse(self.current(game, creature).tapped)
        self.cast_and_choose(game, source, creature)
        self.assertIsNone(self.resolve(game))
        self.assertTrue(self.current(game, creature).tapped)
        soldier, = (card for card in game.state.cards.values() if card.is_token and card.zone == "battlefield")
        self.assertEqual(("B", "B"), (soldier.controller, soldier.owner))
        self.assertIn("lifelink", game.engine._combat_keywords(soldier))
        self.assertEqual((2, 2), self.stats(game, creature))
        self.replay(game, load=True)

    def test_actual_ability_loss_removes_defining_statistics_without_setting_new_base_values(self):
        game = self.session(61316003)
        source = self.add(game.engine, "Merfolk Trickster", zone="hand")
        maro = self.add(game.engine, "Maro", seat="B")
        for card in (source, maro):
            self.assert_closed(game, card)
        self.prepare(game, mana={"U": 2})
        self.checkpoint(game)
        self.assertEqual((7, 7), self.stats(game, maro))
        self.cast_and_choose(game, source, maro)
        self.assertIsNone(self.resolve(game))
        self.assertEqual("graveyard", self.current(game, maro).zone)
        self.replay(game, load=True)
