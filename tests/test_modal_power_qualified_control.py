"""Native mode selection and one power snapshot across three ordered results."""

from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
import test_next_player_untap_restrictions as native_turns
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.object_predicate import ObjectQuerySpec
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.target_numeric import TargetNumericCharacteristic, TargetNumericCharacteristicSpec, TargetNumericComparison
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


MOB_RULE = "Mob Rule"


class ModalPowerQualifiedControlTests(InheritedNativeFixture, unittest.TestCase):
    playing = native_turns.NextPlayerUntapRestrictionTests.playing

    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "modal-power-qualified-control.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures/modal-power-qualified-control-cards.json"], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Modal power control", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"), DeckEntry("Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def cast_control(self, game, spell, mode, *, try_invalid=False):
        self.priority(game, "A")
        offered = self.action(game, "A", game.state.cards[spell.object_id])
        self.assertEqual(["mode_1", "mode_2"], offered["target_schema"]["legal_modes"])
        command = {"action_id": offered["id"], "modes": [mode], "pay": "auto"}
        if try_invalid:
            before = authoritative_state_hash(game.state)
            rejected = game.act("pilot:A", {**command, "modes": ["mode_1", "mode_2"]})
            self.assertFalse(rejected.ok, rejected.summary)
            self.assertEqual(before, authoritative_state_hash(game.state))
        result = game.act("pilot:A", command)
        self.assertTrue(result.ok, result.summary)

    def stats(self, game, card):
        return tuple(game.engine._numeric_stat(card.object_id, stat) for stat in ("power", "toughness"))

    def haste(self, game, card):
        current = game.state.cards[card.object_id]
        return "haste" in {kw.casefold() for kw in game.engine._effective_card_data(current)["keywords"]}

    def test_actual_low_mode_locks_the_set_before_control_changes_power(self):
        game = self.playing(61127282)
        spell = self.add(game.engine, MOB_RULE, zone="hand")
        anthem = self.add(game.engine, "Glorious Anthem")
        own = self.add(game.engine, "Grizzly Bears")
        foreign = self.add(game.engine, "Centaur Courser", seat="B")
        observer = self.add(game.engine, "Wake Thrasher", seat="B")
        high = self.add(game.engine, "Rumbling Baloth", seat="C")
        for card in (spell, anthem, own, foreign, observer, high):
            self.assert_closed(game, card)
        foreign.tapped = True
        high.tapped = True
        before_incarnation = (foreign.logical_object_id, foreign.zone_timestamp)
        self.prepare(game, actor="A", active="A", mana={"C": 4, "R": 2})
        self.checkpoint(game)
        self.assertEqual((3, 3), self.stats(game, foreign))
        self.assertEqual((3, 3), self.stats(game, own))
        self.cast_control(game, spell, "mode_2", try_invalid=True)
        game = self.replay(game, load=True)
        self.resolve_top(game)

        checked = game.state.cards[foreign.object_id]
        self.assertEqual(("A", "B", False), (checked.controller, checked.owner, checked.tapped))
        self.assertEqual(before_incarnation, (checked.logical_object_id, checked.zone_timestamp))
        self.assertEqual((4, 4), self.stats(game, foreign))
        self.assertTrue(self.haste(game, foreign))
        self.assertTrue(self.haste(game, own))
        self.assertTrue(self.haste(game, observer))
        self.assertEqual(("C", True), (game.state.cards[high.object_id].controller, game.state.cards[high.object_id].tapped))
        self.assertFalse(self.haste(game, high))
        own_trigger, = game.state.stack
        self.assertEqual((observer.object_id, "A"), (own_trigger.source_object_id, own_trigger.controller))
        self.assertIsNone(self.resolve(game))
        self.assertEqual((3, 3), self.stats(game, observer))

        turn = game.state.turn_sequence
        self.until(game, lambda item: item.state.turn_sequence > turn)
        self.assertEqual("B", game.state.cards[foreign.object_id].controller)
        self.assertFalse(self.haste(game, foreign))
        self.assertFalse(self.haste(game, own))
        self.replay(game, load=True)

    def test_actual_high_mode_uses_power_after_a_real_response_and_keeps_the_announced_mode(self):
        game = self.playing(61127283)
        spell = self.add(game.engine, MOB_RULE, zone="hand")
        changed = self.add(game.engine, "Centaur Courser", seat="B")
        boundary = self.add(game.engine, "Rumbling Baloth", seat="C")
        low = self.add(game.engine, "Grizzly Bears", seat="D")
        growth = self.add(game.engine, "Giant Growth", seat="B", zone="hand")
        for card in (spell, changed, boundary, low, growth):
            self.assert_closed(game, card)
        for card in (changed, boundary, low):
            card.tapped = True
        self.prepare(game, actor="A", active="A", mana={"C": 4, "R": 2})
        game.state.players["B"].mana_pool["G"] = 1
        self.checkpoint(game)

        self.cast_control(game, spell, "mode_1")
        self.cast_for(game, "B", growth, [changed.ref])
        self.resolve_top(game)
        self.assertEqual((6, 6), self.stats(game, changed))
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        for card in (changed, boundary):
            current = game.state.cards[card.object_id]
            self.assertEqual(("A", False), (current.controller, current.tapped))
            self.assertTrue(self.haste(game, card))
        other = game.state.cards[low.object_id]
        self.assertEqual(("D", True), (other.controller, other.tapped))
        self.assertFalse(self.haste(game, low))
        self.replay(game, load=True)

    def test_complete_original_requires_both_fixed_power_modes_and_the_printed_order(self):
        original = self.db.lookup(MOB_RULE, fuzzy=False)
        report, semantics = register_card(self.db, self.registry, "Mob Rule")
        self.assertTrue(report["strict_capability_ready"], report["blockers"])
        spell, = semantics.programs_for_oracle(original.oracle_id)
        modes = spell.target_schema["modes"]
        self.assertEqual(("mode_1", "mode_2"), tuple(modes))
        for mode, comparison, threshold in (("mode_1", TargetNumericComparison.AT_LEAST, 4),
                                            ("mode_2", TargetNumericComparison.AT_MOST, 3)):
            effect, = modes[mode]["effects"]
            self.assertEqual(("gain_control", "untap", "haste"), tuple(effect["steps"]))
            query = ObjectQuerySpec.from_dict(effect["predicate"])
            self.assertEqual(TargetNumericCharacteristicSpec(
                TargetNumericCharacteristic.POWER, comparison, threshold,
            ), query.numeric_characteristic)
        changed = replace(original, oracle_text=original.oracle_text.replace("power 4", "toughness 4"))
        self.assertNotEqual("exact", compile_oracle_card(
            changed, capability_registry=self.registry, capability_profile="commander_review",
        ).status)
        with patch("quorune.compiler.fixed_control_templates._fixed_power_control_predicate", return_value=None):
            self.assertNotEqual("exact", compile_oracle_card(
                original, capability_registry=self.registry, capability_profile="commander_review",
            ).status)
