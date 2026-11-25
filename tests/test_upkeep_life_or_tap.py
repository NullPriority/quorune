"""Native upkeep alternatives reuse source identity, fixed life and tapping owners."""

from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.compiler.source_maintenance_nodes import source_maintenance_template
from quorune.deck import DeckDefinition, DeckEntry
from quorune.engine import TURN_STEPS
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.source_maintenance import SOURCE_TAP_LIFE_DECLINE_CAPABILITY, SourceMaintenanceSpec
from scripts.build_test_database import build_fixture_database


class UpkeepLifeOrTapTests(InheritedNativeFixture, unittest.TestCase):
    names = {1: "Generic Upkeep Life Alternative 1", 2: "Generic Upkeep Life Alternative 2"}

    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        p = Path(cls.temporary.name) / "upkeep-life.sqlite3"
        fixtures = ROOT / "tests" / "fixtures"
        build_fixture_database([
            fixtures / "bound-effect-program-cards.json",
            fixtures / "source-maintenance-cards.json",
            fixtures / "upkeep-life-or-tap-cards.json",
        ], p)
        cls.configure_assets(p)

    @classmethod
    def configure_assets(cls, database):
        cls.db = CardDatabase(database)
        cls.registry = load_default_capability_registry()
        ruler = "Generic Bound Commander"
        cls.deck = DeckDefinition("Upkeep life alternative", [
            DeckEntry(ruler, 1, "commander"), DeckEntry("Generic Bound Plains", 30),
        ], [ruler])

    def upkeep(self, seed, *, price=1, life=12, active="A", blink=False):
        game = self.session(seed)
        source = self.add(game.engine, self.names[price], ref="UPKEEP-SOURCE")
        blink_card = self.add(game.engine, "Cloudshift", ref="BLINK", zone="hand") if blink else None
        self.assert_closed(game, source)
        game.state.config.semantic_policy = "trusted_only"
        game.state.players["A"].life = life
        self.prepare(game, mana={"W": 1})
        engine = game.engine
        engine.permissions.invalidate_current()
        game.state.pending_decision = None
        game.state.priority_player = None
        game.state.priority_passes = []
        game.state.active_player = active
        game.state.phase_index = TURN_STEPS.index(("beginning", "upkeep"))
        engine._enter_step()
        engine.pump()
        self.checkpoint(game)
        return game, source, blink_card

    def test_current_source_can_pay_or_tap_and_replay(self):
        for price, pay in ((1, True), (2, False)):
            with self.subTest(price=price, pay=pay):
                game, source, _ = self.upkeep(11819001 + price, price=price)
                self.assertEqual(["A"], self.resolve(game).actors)
                self.assertEqual(price, game.packet("pilot:A", full=True)["decision"]["ctx"]["life_cost"])
                for viewer in "BCD":
                    self.assertIsNone(game.packet(f"pilot:{viewer}", full=True)["decision"])
                game = self.replay(game, load=True)
                result = game.act("pilot:A", {"action_id": "choose", "pay": pay})
                self.assertTrue(result.ok, result.summary)
                self.assertEqual(12 - price if pay else 12, game.state.players["A"].life)
                self.assertIs(game.state.cards[source.object_id].tapped, not pay)
                self.replay(game, load=True)

    def test_unaffordable_payment_and_foreign_principal_reject_without_mutation(self):
        game, source, _ = self.upkeep(11819011, price=2, life=1)
        self.assertEqual(["A"], self.resolve(game).actors)
        self.assertFalse(game.packet("pilot:A", full=True)["decision"]["ctx"]["payable"])
        before = authoritative_state_hash(game.state)
        for principal, pay in (("pilot:A", True), ("pilot:B", False)):
            result = game.act(principal, {"action_id": "choose", "pay": pay})
            self.assertFalse(result.ok)
            self.assertEqual(before, authoritative_state_hash(game.state))
        result = game.act("pilot:A", {"action_id": "choose", "pay": False})
        self.assertTrue(result.ok, result.summary)
        self.assertTrue(game.state.cards[source.object_id].tapped)
        self.replay(game, load=True)

    def test_blinked_source_stays_untapped_and_opponent_upkeep_does_not_trigger(self):
        game, source, spell = self.upkeep(11819021, blink=True)
        original = source.logical_object_id
        self.cast_for(game, "A", spell, [source.ref])
        self.resolve_top(game)
        self.assertNotEqual(original, game.state.cards[source.object_id].logical_object_id)
        self.assertEqual(["A"], self.resolve(game).actors)
        result = game.act("pilot:A", {"action_id": "choose", "pay": False})
        self.assertTrue(result.ok, result.summary)
        self.assertFalse(game.state.cards[source.object_id].tapped)
        self.replay(game, load=True)
        other, _, _ = self.upkeep(11819022, active="B")
        self.assertFalse(other.state.stack)

    def test_changed_controller_taps_the_original_source_on_decline(self):
        game, source, _ = self.upkeep(11819031)
        original = source.logical_object_id
        game.engine.change_control(source.object_id, "B", reason="Control changes after the upkeep trigger")
        self.checkpoint(game)
        self.assertEqual(["A"], self.resolve(game).actors)
        result = game.act("pilot:A", {"action_id": "choose", "pay": False})
        self.assertTrue(result.ok, result.summary)
        current = game.state.cards[source.object_id]
        self.assertEqual(("B", original), (current.controller, current.logical_object_id))
        self.assertTrue(current.tapped)
        self.replay(game, load=True)

    def test_parser_gates_fixed_positive_self_tap_and_preserves_historical_schema(self):
        card = self.db.lookup(self.names[1], fuzzy=False)
        whole = compile_oracle_card(card, capability_registry=self.registry, capability_profile="commander_review")
        self.assertEqual("exact", whole.status, whole.material_residuals)
        self.assertTrue(any(SOURCE_TAP_LIFE_DECLINE_CAPABILITY in node.capability_dependencies
                            for face in whole.faces for node in face.nodes))
        template = source_maintenance_template(card.oracle_text, card_name=card.name)
        self.assertEqual("tap", SourceMaintenanceSpec.from_effect(template.spec.effect()).decline)
        former = SourceMaintenanceSpec().effect()
        self.assertEqual(former, SourceMaintenanceSpec.from_effect(former).effect())
        self.assertNotIn("decline", former)
        with self.assertRaises(ValueError):
            SourceMaintenanceSpec.from_effect({**former, "decline": "tap"})
        for old, new in (("your upkeep", "each upkeep"), ("1 life", "0 life"),
                         ("this creature", "target creature"), ("pay 1 life", "discard a card")):
            altered = replace(card, oracle_text=card.oracle_text.replace(old, new))
            self.assertIsNone(source_maintenance_template(altered.oracle_text, card_name=altered.name))
        with patch("quorune.compiler.source_maintenance_nodes._UPKEEP_TAP") as rejected:
            rejected.fullmatch.return_value = None
            mutation = compile_oracle_card(card, capability_registry=self.registry, capability_profile="commander_review")
            self.assertNotEqual("exact", mutation.status)
