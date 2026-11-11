"""Created tokens own separate Firebending triggers and combat-retained mana."""

from pathlib import Path
import tempfile
import unittest

from common import ROOT
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
import test_ability_source_counter_targets as native
import test_provoke_rules as combat


class FirebendingTokenRuntimeTests(unittest.TestCase):
    register_fixture_cards_on_add = True
    session = native.AbilitySourceCounterRuntimeTests.session
    add = native.AbilitySourceCounterRuntimeTests.add
    prepare = native.AbilitySourceCounterRuntimeTests.prepare
    checkpoint = native.AbilitySourceCounterRuntimeTests.checkpoint
    priority = native.AbilitySourceCounterRuntimeTests.priority
    actions = native.AbilitySourceCounterRuntimeTests.actions
    cast = native.AbilitySourceCounterRuntimeTests.cast
    binding = native.AbilitySourceCounterRuntimeTests.binding
    reject = native.AbilitySourceCounterRuntimeTests.reject
    settle = native.AbilitySourceCounterRuntimeTests.settle
    replay = native.AbilitySourceCounterRuntimeTests.replay
    private_replay = native.AbilitySourceCounterRuntimeTests.private_replay
    advance = combat.ProvokeTests.advance

    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        path = Path(cls.temporary.name) / "firebending-tokens.sqlite3"
        build_fixture_database([
            ROOT / "tests/fixtures/bound-effect-program-cards.json",
            ROOT / "tests/fixtures/firebending-token-cards.json",
        ], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Intrinsic Firebending tokens", [
            DeckEntry("Generic Bound Commander", 1, "commander"),
            DeckEntry("Generic Bound Plains", 30),
        ], ["Generic Bound Commander"])

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temporary.cleanup()

    def test_original_created_token_triggers_survive_sacrifice_and_retain_mana_until_combat_ends(self):
        session = self.session(979_001)
        source = self.add(session.engine, "Fire Nation Attacks", zone="hand")
        haste = self.add(session.engine, "Fervor")
        sacrifice = self.add(session.engine, "Goblin Bombardment")
        private = self.add(session.engine, "Generic Bound Growth", seat="C", zone="hand", ref="firebending-private-C")
        for card in (source, haste, sacrifice):
            self.assertTrue(self.binding(session, card)["strict_capability_ready"], card.printed_name)
        self.prepare(session, mana={"C": 10, "R": 10})
        self.checkpoint(session)
        self.cast(session, source)
        self.assertIsNone(self.settle(session))
        tokens = [card for card in session.state.cards.values() if card.zone == "battlefield" and card.is_token]
        self.assertEqual(2, len(tokens))
        for token in tokens:
            self.assertEqual(("A", "A"), (token.owner, token.controller))
            self.assertEqual((2, 2), (session.engine._numeric_stat(token.object_id, "power"),
                session.engine._numeric_stat(token.object_id, "toughness")))
        self.advance(session, lambda current: current.state.pending_decision is not None
            and current.state.pending_decision.kind == "combat.attackers")
        self.assertEqual(0, session.state.players["A"].mana_pool["R"])
        attack = {"action_id": "attack", "attacks": {tokens[0].ref: "B", tokens[1].ref: "C"}}
        self.reject(session, attack, seat="D")
        result = session.act("pilot:A", attack)
        self.assertTrue(result.ok, result.summary)
        pending = [*session.state.stack,
            *(item for batch in session.state.pending_trigger_batches for item in batch.items)]
        triggers = [item for item in pending if item.source_object_id in {token.object_id for token in tokens}]
        self.assertEqual(2, len(triggers))
        self.assertEqual({token.object_id for token in tokens}, {item.source_object_id for item in triggers})
        self.assertTrue(all(item.controller == "A" for item in triggers))
        session = self.private_replay(session, private)
        if session.state.pending_decision.kind == "trigger.order":
            rows = session.packet("pilot:A", full=True)["decision"]["ctx"]["triggers"]
            command = {"action_id": "order", "triggers": [row["id"] for row in rows]}
            self.reject(session, command, seat="D")
            result = session.act("pilot:A", command)
            self.assertTrue(result.ok, result.summary)
        self.priority(session, "A")
        row = next(row for row in self.actions(session) if row["id"].startswith("activate:" + sacrifice.ref + ":"))
        command = {"action_id": row["id"], "cost_cards": [tokens[0].ref], "targets": ["D"], "pay": "auto"}
        self.reject(session, command, seat="D")
        result = session.act("pilot:A", command)
        self.assertTrue(result.ok, result.summary)
        self.assertNotIn(tokens[0].object_id, session.state.players["A"].zones["battlefield"])
        session = self.replay(session, load=True)
        self.assertIsNone(self.settle(session))
        self.assertEqual(2, session.state.players["A"].mana_pool["R"])
        self.assertEqual(39, session.state.players["D"].life)
        self.advance(session, lambda current: current.state.step == "end_combat"
            and current.state.pending_decision is not None and current.state.pending_decision.kind == "priority")
        self.assertEqual(2, session.state.players["A"].mana_pool["R"])
        self.assertEqual({"A": 40, "B": 40, "C": 38, "D": 39},
            {seat: session.state.players[seat].life for seat in "ABCD"})
        self.advance(session, lambda current: current.state.phase == "postcombat_main"
            and current.state.pending_decision is not None and current.state.pending_decision.kind == "priority")
        self.assertEqual(0, session.state.players["A"].mana_pool["R"])
        self.private_replay(session, private)
