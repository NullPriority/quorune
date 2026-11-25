"""Play a fixed upkeep payment and the ordinary controlled return on decline."""

from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
import test_scheduled_player_triggers as scheduled_witnesses
from quorune.carddb import CardDatabase
from quorune.compiler.scheduled_life_return import scheduled_life_return_result
from quorune.compiler.scheduled_player_triggers import scheduled_player_binding_spec
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.scheduled_life_return import (
    SCHEDULED_LIFE_RETURN_CAPABILITY, scheduled_life_return_node_dependencies,
)
from scripts.build_test_database import build_fixture_database


class ScheduledLifeReturnTests(InheritedNativeFixture, unittest.TestCase):
    names = ("Generic Scheduled Life Return", "Generic Scheduled Life Return Option")
    enter_upkeep_from_untap = scheduled_witnesses.ScheduledPlayerTriggerRuntimeTests.enter_upkeep_from_untap

    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        database = Path(cls.temporary.name) / "scheduled-life-return.sqlite3"
        fixtures = ROOT / "tests" / "fixtures"
        build_fixture_database([
            fixtures / "bound-effect-program-cards.json", fixtures / "scheduled-life-return-cards.json",
        ], database)
        cls.configure_assets(database)

    @classmethod
    def configure_assets(cls, database):
        cls.db = CardDatabase(database)
        cls.registry = load_default_capability_registry()
        ruler = "Generic Bound Commander"
        cls.deck = DeckDefinition("Scheduled upkeep life return", [
            DeckEntry(ruler, 1, "commander"), DeckEntry("Generic Bound Plains", 30),
        ], [ruler])

    def setup_upkeep(self, seed, *, variant=0, life=12, borrowed=True):
        game = self.session(seed)
        engine = game.engine
        source = self.add(engine, self.names[variant], ref="UPKEEP-LIFE")
        own = self.add(engine, "Generic Bound Plains", ref="PAYER-LAND", seat="B") if borrowed else None
        loan = self.add(engine, "Generic Bound Plains", ref="BORROWED", seat="C") if borrowed else None
        foreign = self.add(engine, "Generic Bound Plains", ref="UNRELATED", seat="D")
        self.assert_closed(game, source)
        game.state.config.semantic_policy = "trusted_only"
        game.state.players["B"].life = life
        if loan is not None:
            engine.change_control(loan.object_id, "B", reason="Prepare a controlled permanent with another owner")
        self.enter_upkeep_from_untap(game, "B")
        trigger, = [item for item in game.state.stack if item.source_object_id == source.object_id]
        self.assertEqual(("A", "B", "upkeep"), (trigger.controller, trigger.context["player"], trigger.context["step"]))
        return game, own, loan, foreign

    def payment(self, game, paid):
        result = game.act("pilot:B", {"action_id": "choose", "pay": paid})
        self.assertTrue(result.ok, result.summary)

    def test_pay_keeps_controlled_permanents_and_replays_pending_choice(self):
        game, own, loan, _ = self.setup_upkeep(11826001)
        self.assertEqual(["B"], self.resolve(game).actors)
        self.assertEqual(2, game.packet("pilot:B", full=True)["decision"]["ctx"]["life_cost"])
        for viewer in "ACD":
            self.assertIsNone(game.packet(f"pilot:{viewer}", full=True)["decision"])
        game = self.replay(game, load=True)
        before = authoritative_state_hash(game.state)
        bad = game.act("pilot:A", {"action_id": "choose", "pay": True})
        self.assertFalse(bad.ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        self.payment(game, True)
        self.assertFalse(game.state.stack)
        self.assertEqual(10, game.state.players["B"].life)
        self.assertEqual("battlefield", game.state.cards[own.object_id].zone)
        self.assertEqual("battlefield", game.state.cards[loan.object_id].zone)
        self.replay(game, load=True)

    def test_decline_can_return_a_borrowed_permanent_only_to_its_owner(self):
        game, own, loan, foreign = self.setup_upkeep(11826002, variant=1, life=1)
        self.assertEqual(["B"], self.resolve(game).actors)
        self.assertFalse(game.packet("pilot:B", full=True)["decision"]["ctx"]["payable"])
        before = authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:B", {"action_id": "choose", "pay": True}).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        self.payment(game, False)
        self.assertEqual(["B"], game.state.pending_decision.actors)
        game = self.replay(game, load=True)
        before = authoritative_state_hash(game.state)
        bad = game.act("pilot:B", {"action_id": "choose", "cards": [foreign.ref]})
        self.assertFalse(bad.ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        result = game.act("pilot:B", {"action_id": "choose", "cards": [loan.ref]})
        self.assertTrue(result.ok, result.summary)
        self.assertFalse(game.state.stack)
        self.assertEqual(1, game.state.players["B"].life)
        self.assertEqual("battlefield", game.state.cards[own.object_id].zone)
        self.assertIn(loan.object_id, game.state.players["C"].zones["hand"])
        self.assertNotIn(loan.object_id, game.state.players["B"].zones["hand"])
        self.replay(game, load=True)

    def test_empty_controlled_set_and_departed_participant_complete_without_a_return(self):
        game, _, _, foreign = self.setup_upkeep(11826003, borrowed=False)
        self.assertEqual(["B"], self.resolve(game).actors)
        self.payment(game, False)
        self.assertFalse(game.state.stack)
        self.assertEqual("battlefield", game.state.cards[foreign.object_id].zone)
        self.replay(game, load=True)
        game, _, _, foreign = self.setup_upkeep(11826004, borrowed=False)
        result = game.act("pilot:B", {"action_id": "concede", "choices": {"confirm_concede": True}})
        self.assertTrue(result.ok, result.summary)
        self.assertIsNone(self.resolve(game))
        self.assertFalse(game.state.stack)
        self.assertEqual("battlefield", game.state.cards[foreign.object_id].zone)
        self.assertTrue(any(event.code == "effect.player.skipped" and event.details["player"] == "B"
                            for event in game.state.events))
        self.replay(game, load=True)

    def test_only_the_sealed_upkeep_life_return_promotes(self):
        for name in self.names:
            card = self.db.lookup(name, fuzzy=False)
            compiled = compile_oracle_card(card,capability_registry=self.registry,capability_profile="commander_review")
            self.assertEqual("exact", compiled.status, compiled.material_residuals)
            self.assertTrue(any(SCHEDULED_LIFE_RETURN_CAPABILITY in node.capability_dependencies
                                for face in compiled.faces for node in face.nodes))
            binding = scheduled_player_binding_spec(card.oracle_text)
            (_, effects, schema, mechanics), _ = scheduled_life_return_result(binding, binding.body)
            raw = effects[0]
            self.assertIn(SCHEDULED_LIFE_RETURN_CAPABILITY, scheduled_life_return_node_dependencies(raw, schema, mechanics))
            self.assertEqual((), scheduled_life_return_node_dependencies({**raw,"player":"$controller"}, schema, mechanics))
            self.assertEqual((), scheduled_life_return_node_dependencies({**raw,"declined_effects":[
                {**raw["declined_effects"][0],"count":True},
            ]}, schema, mechanics))
            for old,new in (("each player's upkeep","your upkeep"),("a permanent","two permanents"),
                            ("2 life","X life"),("they control","an opponent controls")):
                text=card.oracle_text.replace(old,new)
                self.assertNotEqual(card.oracle_text,text)
                altered=replace(card,oracle_text=text)
                self.assertNotEqual("exact",compile_oracle_card(
                    altered,capability_registry=self.registry,capability_profile="commander_review").status)
            with patch("quorune.compiler.scheduled_life_return.scheduled_life_return_result",return_value=None):
                self.assertNotEqual("exact",compile_oracle_card(
                    card,capability_registry=self.registry,capability_profile="commander_review").status)
