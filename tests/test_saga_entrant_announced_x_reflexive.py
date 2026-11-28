"""Optional printed X retains one creature entry across a separate trigger."""

from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.record import authoritative_state_hash
from quorune.reflexive_trigger_model import QueueReflexiveTriggerIntent
from quorune.replacement.immutable import FrozenMap
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.entrant_x_reflexive import (
    ENTRANT_X_COUNTER_CONTEXT, EnteredCreatureReceipt, bind_paid_entrant_counters, entrant_x_child,
)
from scripts.build_test_database import build_fixture_database


class SagaEntrantAnnouncedXReflexiveTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "saga-entrant-announced-x-reflexive.sqlite3"
        fixtures = ROOT / "tests" / "fixtures"
        build_fixture_database([
            fixtures / "bound-effect-program-cards.json",
            fixtures / "reflexive-payment-cards.json",
            fixtures / "saga-entrant-announced-x-reflexive-cards.json",
        ], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Reflexive entrant", [
            DeckEntry("Generic Bound Commander", 1, "commander"),
            DeckEntry("Generic Bound Plains", 30),
        ], ["Generic Bound Commander"])

    def test_zero_requires_a_real_mana_receipt_and_rejects_untyped_or_missing_values(self):
        from quorune.semantic_choices.completed_choice import _intent_data

        child = FrozenMap(entrant_x_child().to_dict())
        valid = QueueReflexiveTriggerIntent(controller="A", parent_stack_ref="S7", program=child, paid_mana=0)
        self.assertEqual(0, _intent_data(valid)["paid_mana"])
        for bad in (None, True, -1):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                QueueReflexiveTriggerIntent(controller="A", parent_stack_ref="S7", program=child, paid_mana=bad)

    def test_rebinding_to_the_returned_incarnation_is_a_killed_mutation(self):
        current = SimpleNamespace(ref="entered", zone="battlefield", phased_out=False,
                                  logical_object_id="same-physical@3", zone_change_counter=3)
        host = SimpleNamespace(state=SimpleNamespace(cards={"same-physical":current}))
        item = SimpleNamespace(kind="triggered_ability", context={
            "event": "trigger.reflexive", ENTRANT_X_COUNTER_CONTEXT: {
                "entrant": EnteredCreatureReceipt("entered", "same-physical@1", 1).to_dict(), "paid_x": 2,
            },
        })
        effect, = entrant_x_child().effects

        def assert_stale_entrant_is_empty():
            self.assertIsNone(bind_paid_entrant_counters(host, item, effect))

        assert_stale_entrant_is_empty()
        with patch("quorune.rules.entrant_x_reflexive.EnteredCreatureReceipt.from_dict",
                   return_value=EnteredCreatureReceipt("entered", "same-physical@3", 3)):
            with self.assertRaises(AssertionError):
                assert_stale_entrant_is_empty()

    def test_original_transforms_and_replays_decline_zero_positive_and_blinked_entrant(self):
        session = self.session(603122602)
        source = self.add(session.engine, "Jugan Defends the Temple // Remnant of the Rising Star", zone="hand")
        second = self.add(session.engine, "Generic Reflexive Lore", zone="hand", ref="jugan-second-lore")
        final = self.add(session.engine, "Generic Reflexive Lore", zone="hand", ref="jugan-final-lore")
        own = self.add(session.engine, "Reflexive Flash Entrant", zone="hand", ref="jugan-own-entrant")
        foreign = self.add(session.engine, "Reflexive Flash Entrant", zone="hand", seat="B", ref="jugan-foreign-entrant")
        blink = self.add(session.engine, "Cloudshift", zone="hand", ref="jugan-reenter-spell")
        self.assert_closed(session, source)
        self.prepare(session, mana={"C": 5, "G": 4, "W": 2})
        self.checkpoint(session)
        self.cast(session, "A", source)
        self.assertIsNone(self.resolve(session))
        token, = (card for card in session.state.cards.values() if card.is_token and card.zone == "battlefield")
        self.assertTrue(any(ability.mana_ability and "{G}" in ability.effect_text
                            for ability in session.engine._activated_abilities(token)))

        self.cast(session, "A", second, [source.ref])
        self.assertEqual("semantic.target", self.resolve(session).kind)
        self.target(session, "A", [token.ref])
        self.assertIsNone(self.resolve(session))
        self.assertEqual(1, session.state.cards[token.object_id].counters["+1/+1"])
        self.cast(session, "A", final, [source.ref])
        self.assertIsNone(self.resolve(session))
        transformed = session.state.cards[source.object_id]
        self.assertEqual("Remnant of the Rising Star", session.engine._effective_card_data(transformed)["name"])
        self.assertIn("flying", session.engine._combat_keywords(transformed))

        self.cast(session, "B", foreign)
        self.assertIsNone(self.resolve(session))
        self.cast(session, "A", own)
        self.assertEqual("semantic.choice", self.resolve(session).kind)
        packet = session.packet("pilot:A", full=True)
        action = next(row for row in packet["decision"]["legal_actions"] if row["id"] == "choose")
        names = {field["name"]: field for field in action["form"]["fields"]}
        self.assertEqual({"pay", "x"}, set(names))
        self.assertGreaterEqual(names["x"]["maximum"], 2)
        original = session.state.cards[own.object_id].logical_object_id
        for principal, pair in (("pilot:B", {"pay": True, "x": 0}),
                                ("pilot:A", {"pay": True, "x": True}),
                                ("pilot:A", {"pay": False, "x": 2}),
                                ("pilot:A", {"pay": True, "x": names["x"]["maximum"] + 1})):
            before = authoritative_state_hash(session.state)
            self.assertFalse(session.act(principal, {"action_id": "choose", **pair}).ok)
            self.assertEqual(before, authoritative_state_hash(session.state))

        for paid, amount, reenter in ((False, 0, False), (True, 0, False), (True, 2, False), (True, 2, True)):
            with self.subTest(paid=paid, amount=amount, reenter=reenter):
                resumed = self.replay(session, load=True)
                mana_before = sum(resumed.state.players["A"].mana_pool.values())
                result = resumed.act("pilot:A", {"action_id": "choose", "pay": paid, "x": amount})
                self.assertTrue(result.ok, result.summary)
                self.assertEqual(mana_before - amount, sum(resumed.state.players["A"].mana_pool.values()))
                if paid:
                    child, = resumed.state.stack
                    self.assertFalse(child.targets)
                    self.assertEqual("trigger.reflexive", child.context["event"])
                    receipt = child.context[ENTRANT_X_COUNTER_CONTEXT]
                    self.assertEqual((amount, original), (receipt["paid_x"], receipt["entrant"]["logical_object_id"]))
                    resumed = self.replay(resumed, load=True)
                else:
                    self.assertFalse(resumed.state.stack)
                if reenter:
                    self.cast(resumed, "A", blink, [resumed.state.cards[own.object_id].ref])
                    self.resolve_top(resumed)
                    self.assertNotEqual(original, resumed.state.cards[own.object_id].logical_object_id)
                    self.assertEqual("semantic.choice", self.resolve(resumed).kind)
                    declined = resumed.act("pilot:A", {"action_id": "choose", "pay": False, "x": 0})
                    self.assertTrue(declined.ok, declined.summary)
                self.assertIsNone(self.resolve(resumed))
                self.assertEqual(0 if reenter else amount,
                                 resumed.state.cards[own.object_id].counters.get("+1/+1", 0))
                self.replay(resumed)
