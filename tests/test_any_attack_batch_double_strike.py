"""Native witnesses for a controller-independent coalesced attack trigger."""
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.attack_batch_model import OPTIONAL_ATTACK_BATCH_STRIKE_CAPABILITY
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database


NAME = "Duelist's Heritage"


class AnyAttackBatchDoubleStrikeTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        database = Path(cls.temporary.name) / "any-attack-batch.sqlite3"
        fixtures = ROOT / "tests" / "fixtures"
        build_fixture_database([fixtures/"bound-effect-program-cards.json", fixtures/"combat-removal-cards.json",
                                fixtures/"duelists-heritage-cards.json"], database)
        cls.db = CardDatabase(database)
        cls.registry = load_default_capability_registry()
        commander = "Isamaru, Hound of Konda"
        cls.deck = DeckDefinition("Any attack in a declaration",
            [DeckEntry(commander, 1, "commander"), DeckEntry("Generic Bound Plains", 30)], [commander])

    def declare(self, game, seat, assignments):
        self.until(game, lambda current: current.state.pending_decision is not None
                   and current.state.pending_decision.kind == "combat.attackers")
        reply = game.act("pilot:"+seat, {"action_id":"attack", "attackers":assignments})
        self.assertTrue(reply.ok, reply.summary)

    def reject(self, game, seat, command):
        checkpoint = authoritative_state_hash(game.state)
        reply = game.act("pilot:"+seat, command)
        self.assertFalse(reply.ok, reply.summary)
        self.assertEqual(checkpoint, authoritative_state_hash(game.state))

    def test_two_opposing_attackers_across_defenders_trigger_once_and_the_chosen_one_hits_twice(self):
        game = self.session(576791100)
        game.state.config.semantic_policy = "trusted_only"
        source = self.add(game.engine, NAME)
        first = self.add(game.engine, "Grizzly Bears", seat="B", ref="strike-first")
        second = self.add(game.engine, "Grizzly Bears", seat="B", ref="strike-second")
        idle = self.add(game.engine, "Grizzly Bears", seat="A", ref="strike-not-attacking")
        for card in (first, second):
            card.acquired_control_turn_count=-1
        self.assert_closed(game, source)
        self.prepare(game, active="B")
        self.checkpoint(game)
        self.declare(game, "B", {first.ref:"C", second.ref:"D"})
        self.until(game, lambda current: current.state.pending_decision is not None
                   and current.state.pending_decision.kind=="semantic.target")
        self.reject(game,"A",{"action_id":"choose","targets":[idle.ref]})
        self.target(game, "A", [first.ref])
        self.assertEqual(1, len(game.state.stack))
        trigger=game.state.stack[-1]
        self.assertEqual(("A", source.object_id), (trigger.controller, trigger.source_object_id))
        self.assertEqual("semantic.choice", self.resolve(game).kind)
        game = self.replay(game, load=True)
        self.reject(game, "B", {"action_id":"choose","choice":"apply"})
        result = game.act("pilot:A", {"action_id":"choose","choice":"apply"})
        self.assertTrue(result.ok, result.summary)
        self.assertIn("double strike", game.engine._combat_keywords(game.state.cards[first.object_id]))
        self.assertNotIn("double strike", game.engine._combat_keywords(game.state.cards[second.object_id]))
        self.until(game, lambda current: current.state.step=="end_combat")
        self.assertEqual((36,38),(game.state.players["C"].life,game.state.players["D"].life))
        self.replay(game, load=True)

    def test_no_attack_does_not_trigger_and_removal_from_combat_invalidates_a_chosen_target(self):
        game = self.session(576791101)
        game.state.config.semantic_policy = "trusted_only"
        source = self.add(game.engine, NAME)
        attacker = self.add(game.engine, "Grizzly Bears")
        remove = self.add(game.engine, "Reconnaissance")
        rival = self.add(game.engine, "Grizzly Bears", seat="B", ref="declines-to-attack")
        rival.acquired_control_turn_count = -1
        attacker.acquired_control_turn_count=-1
        self.prepare(game)
        self.checkpoint(game)
        self.declare(game, "A", {attacker.ref:"B"})
        self.target(game, "A", [attacker.ref])
        self.priority(game, "A")
        offer, = [row for row in game.packet("pilot:A",full=True)["decision"]["ctx"]["legal"]["actions"]
                  if row["id"].startswith(f"activate:{remove.ref}:") and row.get("target_schema")]
        result=game.act("pilot:A",{"action_id":offer["id"],"targets":[attacker.ref],"pay":"auto"})
        self.assertTrue(result.ok,result.summary)
        game=self.replay(game,load=True)
        self.assertIsNone(self.resolve(game))
        live=game.state.cards[attacker.object_id]
        self.assertEqual("battlefield",live.zone)
        self.assertIsNone(live.attacking)
        self.assertNotIn("double strike",game.engine._combat_keywords(live))
        self.until(game, lambda current: current.state.active_player=="B" and current.state.step=="beginning_combat")
        self.declare(game, "B", {})
        self.assertFalse(any(item.source_object_id==source.object_id for item in game.state.stack))
        self.replay(game,load=True)

    def test_closed_envelope_and_compiler_route(self):
        record=self.db.lookup(NAME)
        profile="commander_review"
        ir=compile_oracle_card(record,capability_registry=self.registry,capability_profile=profile)
        self.assertEqual("exact",ir.status,ir.material_residuals)
        node,=ir.faces[0].nodes
        self.assertIn(OPTIONAL_ATTACK_BATCH_STRIKE_CAPABILITY,node.capability_dependencies)
        altered=replace(record,oracle_text=record.oracle_text.replace("attacking creature","creature"))
        revised=compile_oracle_card(altered,capability_registry=self.registry,capability_profile=profile)
        self.assertNotEqual("exact",revised.status)
        with patch("quorune.compiler.qualified_attack_batches.qualified_attack_batch_binding_spec",return_value=None):
            self.assertNotEqual("exact",compile_oracle_card(record,capability_registry=self.registry,
                                                           capability_profile=profile).status)
