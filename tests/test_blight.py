from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from common import ROOT
from quorune.abilities import CostChoice
from quorune.carddb import CardDatabase
from quorune.compiler.blight_templates import fixed_blight_effect_template
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.replacement.model import ReplacementEffectError
from quorune.replacement.replay import ReplacementContinuation
from quorune.rules.blight_model import BLIGHT_CAPABILITY
from quorune.rules.blight_shapes import fixed_blight_node_capabilities
from quorune.rules.capabilities import CapabilityRegistry, load_default_capability_registry
from quorune.rules.selected_counter_costs import BLIGHT_ACTIVATION_COST_CAPABILITY, BLIGHT_ACTIVATION_COST_KIND
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


    def test_blight_activation_choice_round_trips_and_rejects_malformed_shapes(self):
        choice = CostChoice(BLIGHT_ACTIVATION_COST_KIND, card_type="creature", counter_amount=2)
        self.assertEqual(choice, CostChoice.from_dict(choice.to_dict()))
        self.assertEqual({"k": "blight", "n": 1, "z": "battlefield", "t": "creature", "amount": 2}, choice.compact())
        for changes in (
            {"counter_amount": True}, {"counter_amount": None}, {"counter_amount": 0}, {"count": 2},
            {"card_type": "land"}, {"zone": "graveyard"}, {"another": True}, {"kind": "sacrifice"},
        ):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                CostChoice.from_dict({**choice.to_dict(), **changes})


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
            ROOT / "tests/fixtures/fixed-mana-result-cards.json",
            ROOT / "tests/fixtures/fixed-self-cost-surface-cards.json",
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
        goblins = [card for card in session.state.cards.values() if card.is_token and card.zone == "battlefield"]
        self.assertEqual(4, len(goblins))
        for goblin in goblins:
            self.assertEqual("Goblin Token", session.engine.display_name(goblin.object_id))
            self.assertEqual("Goblin Token", session.engine._copyable_characteristics(goblin)["name"])
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


    def _activation_offer(self, session, source):
        actions = session.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        return next((row for row in actions if row.get("source") == source.ref and row["id"].startswith("activate:")), None)

    def test_original_blight_activation_cost_closes_and_capability_withdrawal_blocks(self):
        value = json.loads((ROOT / "quorune/rules/capability-registry.json").read_bytes())
        capability = next(row for row in value["capabilities"] if row["id"] == BLIGHT_ACTIVATION_COST_CAPABILITY)
        capability.update(status="blocked", blockers=["test activation cost capability withdrawal"])
        withdrawn = CapabilityRegistry(value)
        withdrawn.mark_evidence_verified("0" * 64)
        for name in ("Sting-Slinger", "Evershrike's Gift", "Gristle Glutton"):
            with self.subTest(name=name):
                card = self.db.lookup(name, fuzzy=False)
                ir = compile_oracle_card(card, capability_registry=self.registry, capability_profile="commander_review")
                self.assertEqual("exact", ir.status, ir.material_residuals)
                node, = [node for face in ir.faces for node in face.nodes if node.kind == "activated_ability"]
                self.assertIn(BLIGHT_ACTIVATION_COST_CAPABILITY, node.capability_dependencies)
                self.assertEqual(1, node.cost["choices"][0]["n"])
                blocked = compile_oracle_card(card, capability_registry=withdrawn, capability_profile="commander_review")
                self.assertNotEqual("exact", blocked.status)

    def test_actual_sting_slinger_blight_replacement_preserves_unpaid_frame_and_replays(self):
        from quorune.damage_modifier_state import DamageModifierDuration, PreventionMode
        from quorune.damage_prevention_creation import (
            PreventionShieldCreationRequest, PreventionSubjectAllocation,
            commit_prevention_shield_creation, plan_prevention_shield_creation,
        )

        session = self.session(737101)
        engine = session.engine
        source = self.add(engine, "Sting-Slinger")
        payer = self.add(engine, "Ancient Brontodon")
        foreign = self.add(engine, "Generic Bound Body", seat="B")
        private = self.add(engine, "Generic Bound Growth", seat="C", zone="hand")
        doc = self.add(engine, "Doc Samson, Super Psychiatrist")
        vorinclex = self.add(engine, "Vorinclex, Monstrous Raider")
        season = self.add(engine, "Doubling Season")
        reef = self.add(engine, "Shivan Reef")
        for card in (source, doc, season, reef):
            self.assert_normal_admission(session, card)
        replacement, = engine.semantics.runtime_handler_programs_for_oracle(
            vorinclex.oracle_id, active_zone="battlefield", event="counter.place",
        )
        self.assertTrue(engine.semantic_program_is_current_trusted(replacement))
        self.assertIn("counter.placement.quantity_replacement", replacement.capability_dependencies)
        for label in ("one", "two"):
            request = PreventionShieldCreationRequest(
                source_id=f"fixture:blight-mana:{label}", controller="A",
                mode=PreventionMode.AMOUNT, duration=DamageModifierDuration.UNTIL_END_OF_TURN,
                subjects=(PreventionSubjectAllocation("A", 1),), label=f"Prevent {label}",
            )
            commit_prevention_shield_creation(engine, plan_prevention_shield_creation(engine, request))
        self.prepare(session, mana={"C": 1})
        self.checkpoint(session)
        action = self._activation_offer(session, source)
        self.assertIsNotNone(action)
        offer = action["choice_schema"]["cost_cards"]
        self.assertEqual((1, 1), (offer["minimum"], offer["maximum"]))
        self.assertIn(payer.ref, offer["legal_refs"])
        self.assertIn(source.ref, offer["legal_refs"])
        self.assertNotIn(foreign.ref, offer["legal_refs"])
        before = authoritative_state_hash(session.state)
        command = {"action_id": action["id"], "cost_cards": [payer.ref], "pay": "auto"}
        for principal, change in (
            ("pilot:B", {}),
            ("pilot:A", {"cost_cards": [foreign.ref]}),
            ("pilot:A", {"cost_cards": [source.ref, payer.ref]}),
            ("pilot:A", {"pay": "manual", "payment": {"C": 1}}),
        ):
            denied = session.act(principal, {**command, **change})
            self.assertFalse(denied.ok, denied.summary)
            self.assertEqual(before, authoritative_state_hash(session.state))
        life = {seat: player.life for seat, player in session.state.players.items()}
        accepted = session.act("pilot:A", command)
        self.assertTrue(accepted.ok, accepted.summary)
        self.assertEqual("replacement.order", session.state.pending_decision.kind)
        first = ReplacementContinuation.from_dict(session.state.pending_decision.continuation)
        self.assertEqual("mana_payment", first.resume_kind)
        self.assertFalse(session.state.cards[reef.object_id].tapped)
        self.assertFalse(session.state.cards[source.object_id].tapped)
        mana_options = session.packet("pilot:A", full=True)["decision"]["ctx"]["options"]
        self.assertEqual(2, len(mana_options))
        session = self.replay(session, load=True)
        self.choose(session, replacement=mana_options[0]["id"])
        self.assertEqual("replacement.order", session.state.pending_decision.kind)
        self.assertEqual(["pilot:A"], session.pending_principals())
        self.assertFalse(session.state.cards[reef.object_id].tapped)
        self.assertFalse(session.state.cards[source.object_id].tapped)
        self.assertEqual({}, session.state.cards[payer.object_id].counters)
        self.assertEqual(1, sum(session.state.players["A"].mana_pool.values()))
        self.assertFalse(session.state.stack)
        options = session.packet("pilot:A", full=True)["decision"]["ctx"]["options"]
        self.assertEqual(2, len(options))
        choice = next(row["id"] for row in options if doc.ref in row["id"])
        packet = session.packet("pilot:B", full=True)
        self.assertIsNone(packet["decision"])
        self.assertNotIn(private.ref, json.dumps(packet))
        continuation = session.state.pending_decision.continuation
        restored = ReplacementContinuation.from_dict(continuation)
        self.assertEqual("priority_action_cost", restored.resume_kind)
        for change in ("amount", "creature", "counter_kind"):
            forged = deepcopy(continuation)
            if change == "amount":
                forged["replacement_batch"]["events"][0]["payload"]["amount"] = 2
            elif change == "counter_kind":
                forged["replacement_batch"]["events"][0]["payload"]["counter_name"] = "loyalty"
            else:
                forged["priority_response"]["cost_cards"] = [source.ref]
            with self.subTest(change=change), self.assertRaises(ReplacementEffectError):
                ReplacementContinuation.from_dict(forged)

        session = self.replay(session, load=True)
        before = authoritative_state_hash(session.state)
        for principal, selection in (("pilot:B", choice), ("pilot:A", "unknown-replacement")):
            denied = session.act(principal, {"action_id": "choose", "replacement": selection})
            self.assertFalse(denied.ok, denied.summary)
            self.assertEqual(before, authoritative_state_hash(session.state))
        self.choose(session, replacement=choice)
        # CR 614.16: the changed payment now also admits effect-only doubling.
        self.assertEqual("replacement.order", session.state.pending_decision.kind)
        self.assertFalse(session.state.cards[source.object_id].tapped)
        self.assertEqual({}, session.state.cards[payer.object_id].counters)
        self.assertEqual(1, sum(session.state.players["A"].mana_pool.values()))
        self.assertFalse(session.state.cards[reef.object_id].tapped)
        self.assertFalse(session.state.stack)
        options = session.packet("pilot:A", full=True)["decision"]["ctx"]["options"]
        self.assertEqual(2, len(options))
        second_choice = next(row["id"] for row in options if season.ref in row["id"])
        self.assertTrue(any(vorinclex.ref in row["id"] for row in options))
        forged = deepcopy(session.state.pending_decision.continuation)
        forged["replacement_batch"]["events"][0]["payload"]["amount"] = 3
        with self.assertRaises(ReplacementEffectError):
            ReplacementContinuation.from_dict(forged)
        session = self.replay(session, load=True)
        self.choose(session, replacement=second_choice)
        self.assertTrue(session.state.cards[reef.object_id].tapped)
        self.assertTrue(session.state.cards[source.object_id].tapped)
        self.assertEqual(8, session.state.cards[payer.object_id].counters["-1/-1"])
        self.assertEqual(0, sum(session.state.players["A"].mana_pool.values()))
        item, = session.state.stack
        self.assertFalse(item.targets)
        self.assertEqual(life, {seat: player.life for seat, player in session.state.players.items()})
        self.assertIsNone(self.resolve(session))
        self.assertEqual({seat: amount if seat == "A" else amount - 2 for seat, amount in life.items()},
                         {seat: player.life for seat, player in session.state.players.items()})
        self.replay(session)

    def test_actual_evershrikes_gift_requires_legal_payer_and_can_pay_lethally(self):
        session = self.session(737102)
        engine = session.engine
        source = self.add(engine, "Evershrike's Gift", zone="graveyard")
        cantor = self.add(engine, "Wild Cantor")
        for card in (source, cantor):
            self.assert_normal_admission(session, card)
        self.prepare(session, mana={"C": 1})
        self.assertIsNone(self._activation_offer(session, source), "Cantor cannot fund mana and Blight")
        payer = self.add(engine, "Gladecover Scout")
        solemnity = self.add(engine, "Solemnity", seat="B")
        for card in (payer, solemnity):
            self.assert_normal_admission(session, card)
        self.prepare(session, mana={"C": 1})
        self.assertIsNone(self._activation_offer(session, source))
        engine.move_card(solemnity.object_id, "graveyard", reason="test removes counter prohibition")
        self.prepare(session, mana={"C": 1})
        self.checkpoint(session)
        action = self._activation_offer(session, source)
        self.assertIsNotNone(action)
        self.assertEqual([payer.ref], action["choice_schema"]["cost_cards"]["legal_refs"])
        accepted = session.act("pilot:A", {"action_id": action["id"], "cost_cards": [payer.ref], "pay": "auto"})
        self.assertTrue(accepted.ok, accepted.summary)
        self.assertEqual("graveyard", session.state.cards[cantor.object_id].zone)
        self.assertEqual("graveyard", session.state.cards[payer.object_id].zone)
        self.assertEqual("graveyard", session.state.cards[source.object_id].zone)
        added = next(event for event in reversed(session.state.events) if event.code == "counter.add")
        self.assertEqual((payer.ref, 2, 2), (added.details["object"], added.details["requested"], added.details["placed"]))
        self.assertIsNone(self.resolve(session))
        self.assertEqual("hand", session.state.cards[source.object_id].zone)
        self.replay(session)

    def test_actual_gristle_glutton_pays_on_its_tapped_source_before_private_result(self):
        session = self.session(737103)
        source = self.add(session.engine, "Gristle Glutton")
        discard = self.add(session.engine, "Generic Bound Growth", zone="hand")
        self.assert_normal_admission(session, source)
        self.prepare(session)
        self.checkpoint(session)
        action = self._activation_offer(session, source)
        self.assertIsNotNone(action)
        self.assertIn(source.ref, action["choice_schema"]["cost_cards"]["legal_refs"])
        drawn = session.state.players["A"].zones["library"][-1]
        accepted = session.act("pilot:A", {"action_id": action["id"], "cost_cards": [source.ref]})
        self.assertTrue(accepted.ok, accepted.summary)
        current = session.state.cards[source.object_id]
        self.assertTrue(current.tapped)
        self.assertEqual(1, current.counters.get("-1/-1", 0), "The activation must commit its Blight payment")
        self.assertEqual("hand", session.state.cards[discard.object_id].zone)
        self.assertEqual("library", session.state.cards[drawn].zone)
        self.assertEqual("semantic.choice", self.resolve(session).kind)
        self.assertIsNone(session.packet("pilot:B", full=True)["decision"])
        self.choose(session, cards=[discard.ref])
        self.assertIsNone(self.resolve(session))
        self.assertEqual("graveyard", session.state.cards[discard.object_id].zone)
        self.assertEqual("hand", session.state.cards[drawn].zone)
        self.replay(session)

    def test_native_blight_activation_detects_omitted_counter_commit(self):
        with patch("quorune.counter_placement.commit_prepared_counter_placements", return_value=()):
            with self.assertRaisesRegex(AssertionError, "must commit its Blight payment"):
                self.test_actual_gristle_glutton_pays_on_its_tapped_source_before_private_result()
