from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from common import ROOT
from quorune.carddb import CardDatabase
from quorune.compiler.blight_templates import fixed_blight_effect_template
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.blight_model import BLIGHT_CAPABILITY
from quorune.rules.blight_shapes import fixed_blight_node_capabilities
from quorune.rules.capabilities import CapabilityRegistry, load_default_capability_registry
from scripts.build_test_database import build_fixture_database
import test_fixed_counter_additional_costs as counter_witnesses
import test_reflexive_payments as reflexive_witnesses


class FixedBlightCompilerTests(unittest.TestCase):
    def test_fixed_blight_requires_one_bound_controller_and_positive_amount(self):
        _, effects, schema, mechanics = fixed_blight_effect_template("Blight 2.")
        self.assertEqual((BLIGHT_CAPABILITY,), fixed_blight_node_capabilities(
            effects=effects, target_schema=schema, mechanic_ids=mechanics,
        ))
        for text in ("Blight 0.", "Blight X.", "Target creature blights 2.", "Blight 2, then draw a card."):
            with self.subTest(text=text):
                self.assertIsNone(fixed_blight_effect_template(text))
        for fields in ({"player": "$target.0"}, {"amount": True}, {"schema_version": 2}, {"target": "$target.0"}):
            with self.subTest(fields=fields):
                self.assertEqual((), fixed_blight_node_capabilities(
                    effects=({**effects[0], **fields},), target_schema=None, mechanic_ids=mechanics,
                ))


class FixedBlightRuntimeTests(unittest.TestCase):
    register_fixture_cards_on_add = True
    Witnesses = reflexive_witnesses.ReflexivePaymentRuntimeTests
    session = Witnesses.session
    add = Witnesses.add
    prepare = Witnesses.prepare
    advance_to = Witnesses.advance_to
    choose = Witnesses.choose
    checkpoint = Witnesses.checkpoint
    resolve = Witnesses.resolve
    replay = Witnesses.replay
    assert_normal_admission = counter_witnesses.CounterCostReplacementChainTests.assert_normal_admission

    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "blight.sqlite3"
        build_fixture_database([
            ROOT / "tests/fixtures/bound-effect-program-cards.json",
            ROOT / "tests/fixtures/counter-prohibition-cards.json",
            ROOT / "tests/fixtures/counter-replacement-cards.json",
            ROOT / "tests/fixtures/blight-cards.json",
        ], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Blight", [
            DeckEntry("Generic Bound Commander", 1, "commander"),
            DeckEntry("Generic Bound Plains", 16),
        ], ["Generic Bound Commander"])

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temporary.cleanup()

    def _cast(self, session, card):
        actions = session.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        action = next(row for row in actions if row.get("card") == card.ref)
        accepted = session.act("pilot:A", {
            "action_id": action["id"], "cost_option": action["cost_options"][0]["id"], "pay": "auto",
        })
        self.assertTrue(accepted.ok, accepted.summary)

    def _choice_schema(self, session):
        self.assertEqual(["pilot:A"], session.pending_principals())
        return session.packet("pilot:A", full=True)["decision"]["ctx"]["legal_actions"][0]["choice_schema"]

    def test_actual_sourbread_blight_is_one_nontargeted_cost_and_resolves_before_lethal_stabilization(self):
        session = self.session(734101)
        engine = session.engine
        source = self.add(engine, "Sourbread Auntie", zone="hand")
        small = self.add(engine, "Gladecover Scout")
        rival = self.add(engine, "Generic Bound Body", seat="B")
        season = self.add(engine, "Doubling Season")
        private = self.add(engine, "Generic Bound Growth", seat="D", zone="hand")
        for card in (source, small, rival, season):
            self.assert_normal_admission(session, card)
        self.prepare(session, mana={"C": 20, "B": 4, "R": 4})
        self.checkpoint(session)
        self._cast(session, source)
        self.assertEqual("semantic.choice", self.resolve(session).kind)
        self.assertFalse(session.state.stack[-1].targets)
        offered = self._choice_schema(session)
        self.assertEqual({small.ref, source.ref}, set(offered["legal_refs"]))
        self.assertEqual(1, offered["maximum"])
        before = authoritative_state_hash(session.state)
        for principal, refs in (("pilot:B", [small.ref]), ("pilot:A", [rival.ref]), ("pilot:A", [small.ref, source.ref])):
            rejected = session.act(principal, {"action_id": "choose", "cards": refs})
            self.assertFalse(rejected.ok)
            self.assertEqual(before, authoritative_state_hash(session.state))
        packet = session.packet("pilot:B", full=True)
        self.assertIsNone(packet["decision"])
        self.assertNotIn(private.ref, json.dumps(packet))
        session = self.replay(session, load=True)
        event_start = len(session.state.events)
        self.choose(session, cards=[small.ref])
        self.assertIsNone(self.resolve(session))
        events = [event for event in session.state.events[event_start:]
                  if event.code in {"counter.add", "token.create", "state.creatures_died"}]
        self.assertEqual(["counter.add", "token.create", "state.creatures_died"], [event.code for event in events])
        self.assertEqual((small.ref, 2), (events[0].details["object"], events[0].details["placed"]))
        self.assertEqual([small.ref], events[2].details["objects"])
        self.assertEqual("graveyard", session.state.cards[small.object_id].zone)
        self.assertEqual(4, sum(card.is_token and card.zone == "battlefield" for card in session.state.cards.values()))
        self.replay(session)

    def test_actual_blight_payment_is_unavailable_under_counter_prohibition(self):
        session = self.session(734110)
        source = self.add(session.engine, "Sourbread Auntie", zone="hand")
        solemnity = self.add(session.engine, "Solemnity", seat="B")
        for card in (source, solemnity):
            self.assert_normal_admission(session, card)
        self.prepare(session, mana={"C": 20, "B": 4, "R": 4})
        self.checkpoint(session)
        self._cast(session, source)
        self.assertEqual("semantic.choice", self.resolve(session).kind)
        self.assertEqual([], self._choice_schema(session)["legal_refs"])
        before = authoritative_state_hash(session.state)
        rejected = session.act("pilot:A", {"action_id": "choose", "cards": [source.ref]})
        self.assertFalse(rejected.ok)
        self.assertEqual(before, authoritative_state_hash(session.state))
        self.choose(session, cards=[])
        self.assertIsNone(self.resolve(session))
        self.assertFalse(any(card.is_token for card in session.state.cards.values()))
        self.replay(session)

    def test_actual_warren_blight_pays_before_the_reflexive_target_is_offered(self):
        session = self.session(734120)
        engine = session.engine
        source = self.add(engine, "Warren Torchmaster")
        price = self.add(engine, "Gladecover Scout")
        recipient = self.add(engine, "Generic Bound Body", ref="blight-haste-recipient")
        for card in (source, price, recipient):
            self.assert_normal_admission(session, card)
        self.prepare(session)
        self.checkpoint(session)
        self.advance_to(session, "semantic.choice")
        parent = session.state.stack[-1]
        self.assertFalse(parent.targets)
        self.assertIn(price.ref, self._choice_schema(session)["legal_refs"])
        self.choose(session, cards=[price.ref])
        self.assertEqual("semantic.target", session.state.pending_decision.kind)
        child = session.state.stack[-1]
        self.assertEqual(parent.ref, child.context["reflexive_parent"])
        self.assertFalse(any(item.ref == parent.ref for item in session.state.stack))
        self.assertEqual("graveyard", session.state.cards[price.object_id].zone)
        legal = session.packet("pilot:A", full=True)["decision"]["ctx"]["target_schema"]["legal_refs"]
        self.assertNotIn(price.ref, legal)
        self.assertIn(recipient.ref, legal)
        session = self.replay(session, load=True)
        self.choose(session, targets=[recipient.ref])
        self.assertIsNone(self.resolve(session))
        self.assertIn("haste", session.engine._combat_keywords(session.state.cards[recipient.object_id]))
        self.replay(session)

    def test_actual_chaos_declined_mana_runs_mandatory_effect_blight(self):
        session = self.session(734130)
        engine = session.engine
        source = self.add(engine, "Chaos Spewer", zone="hand")
        payer = self.add(engine, "Ancient Brontodon")
        season = self.add(engine, "Doubling Season")
        for card in (source, payer, season):
            self.assert_normal_admission(session, card)
        self.prepare(session, mana={"C": 20, "B": 4, "R": 4})
        self.checkpoint(session)
        self._cast(session, source)
        self.assertEqual("semantic.choice", self.resolve(session).kind)
        self.choose(session, pay=False)
        self.assertEqual("semantic.choice", self.resolve(session).kind)
        offered = self._choice_schema(session)
        self.assertEqual("cards", offered["field"])
        self.assertIn(payer.ref, offered["legal_refs"])
        before = authoritative_state_hash(session.state)
        self.assertFalse(session.act("pilot:A", {"action_id": "choose", "cards": []}).ok)
        self.assertEqual(before, authoritative_state_hash(session.state))
        self.choose(session, cards=[payer.ref])
        self.assertIsNone(self.resolve(session))
        self.assertEqual(4, session.state.cards[payer.object_id].counters["-1/-1"])
        self.replay(session)

    def test_original_blight_producer_and_capability_withdrawal_keep_residuals(self):
        card = self.db.lookup("Sourbread Auntie", fuzzy=False)

        def compile_with(registry):
            return compile_oracle_card(card, capability_registry=registry, capability_profile="commander_review")

        self.assertEqual("exact", compile_with(self.registry).status)
        with patch("quorune.compiler.fixed_effect_payment_templates.fixed_blight_effect_template", return_value=None):
            self.assertTrue(compile_with(self.registry).material_residuals)
        value = json.loads((ROOT / "quorune/rules/capability-registry.json").read_bytes())
        capability = next(row for row in value["capabilities"] if row["id"] == BLIGHT_CAPABILITY)
        capability.update(status="blocked", blockers=["test capability withdrawal"])
        registry = CapabilityRegistry(value)
        registry.mark_evidence_verified("0" * 64)
        self.assertTrue(compile_with(registry).material_residuals)
