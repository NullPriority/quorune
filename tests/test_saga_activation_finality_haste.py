"""Exact announced activation X and incarnation-bound Saga return riders."""

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.record import authoritative_state_hash
from quorune.rules.announced_mana_value_target import bind_announced_mana_value_limit, saga_activation_graveyard_schema
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database


class SagaActivationFinalityHasteTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        fixtures = ROOT / "tests" / "fixtures"
        path = Path(cls.temporary.name) / "saga-activation-finality-haste.sqlite3"
        build_fixture_database([
            fixtures / "bound-effect-program-cards.json",
            fixtures / "reflexive-payment-cards.json",
            fixtures / "saga-activation-finality-haste-cards.json",
        ], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Saga activation", [
            DeckEntry("Generic Bound Commander", 1, "commander"),
            DeckEntry("Generic Bound Plains", 30),
        ], ["Generic Bound Commander"])

    def test_exact_activated_saga_scope_rejects_open_target_and_untyped_values(self):
        schema = saga_activation_graveyard_schema()
        bound = bind_announced_mana_value_limit(schema, 0, activation=True)
        self.assertEqual((0, ["saga"]), (bound["mana_value"], bound["subtypes_any"]))
        for changed, announced in ((schema, True), (schema, -1),
                                   ({**schema, "owner_relation": "any"}, 2),
                                   ({**schema, "subtypes_any": []}, 2), ({**schema, "mana_value": 2}, 2)):
            with self.subTest(changed=changed, announced=announced), self.assertRaises(ValueError):
                bind_announced_mana_value_limit(changed, announced, activation=True)

    def test_original_activation_pays_exact_x_returns_with_finality_and_grants_haste(self):
        session = self.session(602122611)
        source = self.add(session.engine, "Rydia, Summoner of Mist")
        source.acquired_control_turn_count = -1
        own = self.add(session.engine, "Counter-entry Creature Saga", zone="graveyard", ref="own-saga")
        foreign = self.add(session.engine, "Counter-entry Creature Saga", seat="B", zone="graveyard", ref="foreign-saga")
        ordinary = self.add(session.engine, "Counter-entry Non-Saga", zone="graveyard")
        self.assert_closed(session, source)
        self.prepare(session, mana={"C": 3})
        self.checkpoint(session)
        decision = session.packet("pilot:A", full=True)["decision"]
        offer = next(action for action in decision["legal_actions"]
                     if action["id"].startswith(f"activate:{source.ref}:"))
        self.assertEqual({own.ref: {"minimum": 2, "maximum": 2}}, offer["target_schema"]["x_bounds_by_ref"])
        for ref, value in ((own.ref, 1), (own.ref, 3), (foreign.ref, 2), (ordinary.ref, 2)):
            before = authoritative_state_hash(session.state)
            bad = session.act("pilot:A", {"action_id": offer["id"], "targets": [ref], "x": value, "pay": "auto"})
            self.assertFalse(bad.ok, bad.summary)
            self.assertEqual(before, authoritative_state_hash(session.state))

        original_identity = own.logical_object_id
        paid = session.act("pilot:A", {"action_id": offer["id"], "targets": [own.ref], "x": 2, "pay": "auto"})
        self.assertTrue(paid.ok, paid.summary)
        self.assertTrue(session.state.cards[source.object_id].tapped)
        self.assertEqual(1, session.state.players["A"].mana_pool["C"])
        session = self.replay(session, load=True)
        replacement = self.resolve(session)
        self.assertEqual(("replacement.order", ["A"]), (replacement.kind, replacement.actors))
        self.assertEqual("graveyard", session.state.cards[own.object_id].zone)
        session = self.replay(session, load=True)
        options = session.packet("pilot:A", full=True)["decision"]["ctx"]["options"]
        finality = next(option["id"] for option in options if "effect-entry-counter" in option["id"])
        before = authoritative_state_hash(session.state)
        command = {"action_id": "choose", "choices": {"replacement": finality}}
        self.assertFalse(session.act("pilot:B", command).ok)
        self.assertEqual(before, authoritative_state_hash(session.state))
        ordered = session.act("pilot:A", command)
        self.assertTrue(ordered.ok, ordered.summary)
        self.assertIsNone(self.resolve(session))
        entered = session.state.cards[own.object_id]
        self.assertEqual(("A", "battlefield"), (entered.controller, entered.zone))
        self.assertNotEqual(original_identity, entered.logical_object_id)
        self.assertEqual(1, entered.counters["finality"])
        self.assertEqual(1, entered.counters["lore"])
        self.assertIn("haste", session.engine._combat_keywords(entered))
        self.assertFalse(any(trigger.active and trigger.label == "Delayed exile" for trigger in session.state.delayed_triggers))
        self.replay(session)
