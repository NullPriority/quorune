"""Ordinary token creation followed by a nontargeted vigilance counter."""

import tempfile
import unittest
from pathlib import Path

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.permanent_resolution_choices import permanent_resolution_choice_capabilities
from quorune.replacement.immutable import thaw_value
from scripts.build_test_database import build_fixture_database


class SagaControlledKeywordCounterTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        path = Path(cls.temporary.name) / "saga-controlled-keyword.sqlite3"
        fixtures = ROOT / "tests" / "fixtures"
        build_fixture_database([
            fixtures / "bound-effect-program-cards.json",
            fixtures / "reflexive-payment-cards.json",
            fixtures / "power-toughness-switch-cards.json",
            fixtures / "saga-controlled-keyword-counter-cards.json",
        ], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Saga counter choice", [
            DeckEntry("Generic Bound Commander", 1, "commander"),
            DeckEntry("Generic Bound Plains", 30),
        ], ["Generic Bound Commander"])

    def test_malformed_order_and_result_fail_closed(self):
        card = self.db.lookup("Ajani Fells the Godsire", fuzzy=False)
        ir = compile_oracle_card(card, capability_registry=self.registry, capability_profile="commander_review")
        self.assertEqual("exact", ir.status, ir.material_residuals)
        chapter = next(node for face in ir.faces for node in face.nodes if node.event == "saga.chapter.2")
        self.assertIsNone(chapter.target_schema)
        token, choice = map(thaw_value, chapter.effects)
        self.assertEqual(("create_token", "choose_objects"), (token["op"], choice["op"]))
        self.assertIn("counter.characteristic.keyword", chapter.capability_dependencies)
        cases = (
            (choice, token),
            (token, {**choice, "minimum": 0}),
            (token, {**choice, "then": [{"op": "add_counter_selected", "counter": "flying", "amount": 1}]}),
        )
        for effects in cases:
            with self.subTest(effects=effects):
                self.assertEqual((), permanent_resolution_choice_capabilities(
                    effects=effects, target_schema=None, mechanics=chapter.mechanics,
                ))

    def test_original_chapters_create_token_before_untargeted_counter_choice_and_replay(self):
        session = self.session(714608111)
        source = self.add(session.engine, "Ajani Fells the Godsire", zone="hand")
        survivor = self.add(session.engine, "Wall of Denial", ref="controlled-shroud-recipient")
        hostile = self.add(session.engine, "Generic Bound Body", seat="B", ref="opponent-eligible-first-chapter")
        hostile.counters["+1/+1"] = 1
        ineligible = self.add(session.engine, "Generic Bound Body", seat="C", ref="opponent-below-three-power")
        second = self.add(session.engine, "Generic Reflexive Lore", zone="hand", ref="second-lore")
        third = self.add(session.engine, "Generic Reflexive Lore", zone="hand", ref="final-lore")
        self.assert_closed(session, source)
        self.prepare(session, mana={"C": 3, "W": 2})
        self.checkpoint(session)

        self.cast(session, "A", source)
        self.assertEqual("semantic.target", self.resolve(session).kind)
        invalid = {"action_id": "choose", "targets": [ineligible.ref]}
        before = authoritative_state_hash(session.state)
        self.assertFalse(session.act("pilot:A", invalid).ok)
        self.assertEqual(before, authoritative_state_hash(session.state))
        self.target(session, "A", [hostile.ref])
        self.assertIsNone(self.resolve(session))
        self.assertEqual("exile", session.state.cards[hostile.object_id].zone)

        self.cast(session, "A", second, [source.ref])
        decision = self.resolve(session)
        self.assertIsNotNone(decision)
        self.assertEqual("semantic.choice", decision.kind)
        self.assertFalse(session.state.stack[-1].targets)
        token, = (c for c in session.state.cards.values() if c.is_token and c.zone == "battlefield")
        self.assertEqual("A", token.controller)
        self.assertEqual((2, 1), tuple(session.engine._numeric_stat(token.object_id, stat)
                                      for stat in ("power", "toughness")))
        self.assertFalse(token.counters.get("vigilance", 0))

        for principal, refs in (
            ("pilot:B", [token.ref]),
            ("pilot:A", [ineligible.ref]),
            ("pilot:A", []),
        ):
            before = authoritative_state_hash(session.state)
            self.assertFalse(session.act(principal, {"action_id": "choose", "objects": refs}).ok)
            self.assertEqual(before, authoritative_state_hash(session.state))

        for recipient in (survivor, token):
            with self.subTest(recipient=recipient.ref):
                resumed = self.replay(session, load=True)
                result = resumed.act("pilot:A", {"action_id": "choose", "objects": [recipient.ref]})
                self.assertTrue(result.ok, result.summary)
                self.assertIsNone(self.resolve(resumed))
                chosen = resumed.state.cards[recipient.object_id]
                self.assertEqual(1, chosen.counters["vigilance"])
                if recipient is token:
                    self.assertIn("vigilance", resumed.engine._combat_keywords(chosen))
                self.assertEqual(1, sum(c.is_token and c.zone == "battlefield" for c in resumed.state.cards.values()))
                self.cast(resumed, "A", third, [source.ref])
                self.assertEqual("semantic.target", self.resolve(resumed).kind)
                self.target(resumed, "A", [token.ref])
                self.assertIsNone(self.resolve(resumed))
                self.assertIn("double strike", resumed.engine._combat_keywords(resumed.state.cards[token.object_id]))
                self.assertEqual("graveyard", resumed.state.cards[source.object_id].zone)
                self.replay(resumed)
