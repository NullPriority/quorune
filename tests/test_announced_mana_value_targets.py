"""Native casting and target validation for mana value limited by announced X."""

from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
import json
import unittest
from unittest.mock import patch

from common import ROOT
import test_qualified_hexproof as native
import test_splice as splice_tests
from test_controller_creature_keyword_absence import register_card
from quorune.rules.announced_mana_value_target import ANNOUNCED_MANA_VALUE_TARGET_CAPABILITY
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import CapabilityRegistry, load_default_capability_registry
from quorune.rules.evidence import validate_capability_evidence_index
from quorune.rules.evidence_storage import read_evidence_document
from scripts.build_test_database import build_fixture_database
from scripts.update_capability_evidence import build_index, discover_tests


EXILE = "Vault Passage"
CONTROL = "River Claim"
PROFILE = "commander_review"
FIXTURES = ROOT / "tests" / "fixtures"


class AnnouncedManaValueTargetTests(unittest.TestCase):
    register_fixture_cards_on_add = True
    session = native.QualifiedHexproofTests.session
    add = native.QualifiedHexproofTests.add
    prepare = native.QualifiedHexproofTests.prepare
    checkpoint = native.QualifiedHexproofTests.checkpoint
    resolve = native.QualifiedHexproofTests.resolve
    replay = native.QualifiedHexproofTests.replay
    priority = native.QualifiedHexproofTests.priority
    cast = native.QualifiedHexproofTests.cast
    start_splice = splice_tests.SpliceRuntimeTests.start_splice
    splice_actions = splice_tests.SpliceRuntimeTests.splice_actions
    add_splice = splice_tests.SpliceRuntimeTests.add_splice

    @classmethod
    def setUpClass(cls):
        cls.temp = TemporaryDirectory()
        path = Path(cls.temp.name) / "announced-target.sqlite3"
        build_fixture_database([FIXTURES / "bound-effect-program-cards.json",
                                FIXTURES / "announced-mana-value-target-cards.json",
                                FIXTURES / "fixed-stack-spell-copy-cards.json",
                                FIXTURES / "splice-cards.json"], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Announced target witness", [
            DeckEntry("Generic Bound Commander", 1, "commander"), DeckEntry("Generic Bound Plains", 30)
        ], ["Generic Bound Commander"])

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temp.cleanup()

    def cast_offer(self, session, spell, mana):
        self.prepare(session, mana=mana)
        self.checkpoint(session)
        actions = session.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        return next(row for row in actions if row["id"] == "cast:" + spell.ref)

    def test_mana_value_quote_and_private_credit_enforce_the_same_announced_x(self):
        session = self.session(601_291)
        spell = self.add(session.engine, EXILE, zone="hand")
        resource = self.add(session.engine, "Vault Ledger", zone="hand", ref="private-vault-ledger")
        free = self.add(session.engine, "Pebble Vault", seat="B", ref="free-vault")
        mid = self.add(session.engine, "Copper Vault", seat="B", ref="copper-vault")
        high = self.add(session.engine, "Steel Vault", seat="B", ref="steel-vault")
        action = self.cast_offer(session, spell, {"W": 1})
        option = action["cost_options"][0]
        self.assertEqual(2, option["choice_schema"]["x"]["maximum"])
        self.assertEqual({free.ref: {"minimum": 0, "maximum": 2},
                          mid.ref: {"minimum": 2, "maximum": 2}},
                         option["target_schema"]["x_bounds_by_ref"])
        self.assertNotIn(high.ref, option["target_schema"]["legal_refs"])
        for seat in "BCD":
            self.assertNotIn(resource.ref, json.dumps(session.packet("pilot:" + seat, full=True)))
        command = {"action_id": action["id"], "targets": [mid.ref], "x": 1,
                   "exile_cards": [resource.ref], "pay": "manual", "payment": {"W": 1}}
        before = authoritative_state_hash(session.state)
        rejected = session.act("pilot:A", command)
        self.assertFalse(rejected.ok, rejected.summary)
        self.assertEqual(before, authoritative_state_hash(session.state))
        accepted = session.act("pilot:A", {**command, "x": 2})
        self.assertTrue(accepted.ok, accepted.summary)
        item = session.state.stack[-1]
        self.assertEqual(2, item.x_value)
        self.assertEqual(2, item.context["target_schema_override"]["mana_value_max"])
        self.assertEqual("exile", session.state.cards[resource.object_id].zone)

        session = self.replay(session, load=True)
        self.assertIsNone(self.resolve(session))
        self.assertEqual("exile", session.state.cards[mid.object_id].zone)
        self.assertEqual("battlefield", session.state.cards[high.object_id].zone)
        self.replay(session, load=True)

    def test_target_tax_quotes_each_x_range_and_allows_a_zero_value_creature(self):
        session = self.session(601_292)
        spell = self.add(session.engine, CONTROL, zone="hand")
        zero = self.add(session.engine, "Surcharge Spark", seat="B", ref="taxed-zero")
        high = self.add(session.engine, "Surcharge Guard", seat="C", ref="taxed-two")
        ordinary = self.add(session.engine, "River Sentry", seat="D", ref="untaxed-three")
        action = self.cast_offer(session, spell, {"U": 1, "C": 3})
        target = action["target_schema"]
        self.assertEqual({zero.ref: {"minimum": 0, "maximum": 1},
                          ordinary.ref: {"minimum": 3, "maximum": 3}}, target["x_bounds_by_ref"])
        self.assertNotIn(high.ref, target["legal_refs"])
        before = authoritative_state_hash(session.state)
        denied = session.act("pilot:A", {"action_id": action["id"], "targets": [high.ref], "x": 2, "pay": "auto"})
        self.assertFalse(denied.ok, denied.summary)
        self.assertEqual(before, authoritative_state_hash(session.state))
        accepted = session.act("pilot:A", {"action_id": action["id"], "targets": [zero.ref], "x": 0,
                                            "pay": "manual", "payment": {"U": 1, "C": 2}})
        self.assertTrue(accepted.ok, accepted.summary)
        self.assertEqual(0, session.state.stack[-1].context["target_schema_override"]["mana_value_max"])
        session = self.replay(session, load=True)
        self.assertIsNone(self.resolve(session))
        self.assertEqual("A", session.state.cards[zero.object_id].controller)
        self.assertEqual("C", session.state.cards[high.object_id].controller)
        self.replay(session, load=True)


    def test_copied_mana_value_limit_retains_announced_x_when_retargeted(self):
        session = self.session(601_293)
        source = self.add(session.engine, CONTROL, zone="hand")
        echo = self.add(session.engine, "Reverberate", zone="hand")
        first = self.add(session.engine, "Surcharge Spark", seat="B", ref="copy-first")
        second = self.add(session.engine, "Surcharge Guard", seat="C", ref="copy-second")
        high = self.add(session.engine, "River Sentry", seat="D", ref="copy-over-limit")
        action = self.cast_offer(session, source, {"U": 1, "C": 4, "R": 2})
        sent = session.act("pilot:A", {"action_id": action["id"], "targets": [first.ref], "x": 2,
                                        "pay": "manual", "payment": {"U": 1, "C": 4}})
        self.assertTrue(sent.ok, sent.summary)
        original = session.state.stack[-1]
        self.cast(session, "A", echo, [original.ref], {"R": 2})
        pending = self.resolve(session)
        self.assertEqual("semantic.choice", pending.kind)
        before = authoritative_state_hash(session.state)
        self.assertFalse(session.act("pilot:A", {"action_id": "choose", "targets": [high.ref]}).ok)
        self.assertEqual(before, authoritative_state_hash(session.state))
        session = self.replay(session, load=True)
        chosen = session.act("pilot:A", {"action_id": "choose", "targets": [second.ref]})
        self.assertTrue(chosen.ok, chosen.summary)
        clone, = (item for item in session.state.stack if item.kind == "spell_copy")
        self.assertEqual(2, clone.x_value)
        self.assertEqual(2, clone.context["target_schema_override"]["mana_value_max"])
        self.assertEqual([second.ref], clone.targets)
        self.assertIsNone(self.resolve(session))
        self.assertEqual("A", session.state.cards[first.object_id].controller)
        self.assertEqual("A", session.state.cards[second.object_id].controller)
        self.assertEqual("D", session.state.cards[high.object_id].controller)
        self.replay(session, load=True)

    def test_live_mana_value_is_rechecked_when_the_same_permanent_turns_face_up(self):
        session = self.session(601_296)
        source = self.add(session.engine, CONTROL, zone="hand")
        folded = self.add(session.engine, "Folded Sentry", seat="B", zone="hand")
        self.prepare(session, actor="B", active="B", mana={"C": 4})
        session.state.players["A"].mana_pool["U"] = 1
        self.checkpoint(session)
        cast = session.act("pilot:B", {"action_id": "cast-morph:" + folded.ref, "pay": "auto"})
        self.assertTrue(cast.ok, cast.summary)
        self.assertIsNone(self.resolve(session))
        self.priority(session, "A")
        folded = session.state.cards[folded.object_id]
        identity = folded.logical_object_id
        self.assertTrue(folded.face_down)
        self.assertEqual(0, session.engine._target_snapshot(folded.ref)["mana_value"])
        response = {"action_id": "cast:" + source.ref, "targets": [folded.ref], "x": 0, "pay": "auto"}
        accepted = session.act("pilot:A", response)
        self.assertTrue(accepted.ok, accepted.summary)
        self.priority(session, "B")
        revealed = session.act("pilot:B", {"action_id": "turn-face-up:" + folded.ref, "pay": "auto"})
        self.assertTrue(revealed.ok, revealed.summary)
        current = session.state.cards[folded.object_id]
        self.assertEqual(identity, current.logical_object_id)
        self.assertEqual(5, session.engine._target_snapshot(current.ref)["mana_value"])
        session = self.replay(session, load=True)
        self.assertIsNone(self.resolve(session))
        self.assertEqual("B", session.state.cards[folded.object_id].controller)
        self.assertEqual("graveyard", session.state.cards[source.object_id].zone)
        self.replay(session, load=True)

    def test_splice_quote_retains_the_bound_group_and_includes_its_mana_price(self):
        session = self.session(601_297)
        source = self.add(session.engine, CONTROL, zone="hand")
        dream = self.add(session.engine, "Everdream", zone="hand")
        zero = self.add(session.engine, "Surcharge Spark", seat="B", ref="splice-taxed")
        low = self.add(session.engine, "Llanowar Elves", seat="B", ref="splice-low")
        high = self.add(session.engine, "River Sentry", seat="C", ref="splice-high")
        self.prepare(session, mana={"U": 2, "C": 4})
        self.checkpoint(session)
        self.start_splice(session, source)
        self.add_splice(session, dream)
        action = self.splice_actions(session)["cast"]
        option, = action["cost_options"]
        self.assertEqual(2, option["choice_schema"]["x"]["maximum"])
        target_schema = option["target_schema"]
        self.assertEqual({"minimum": 0, "maximum": 0}, target_schema["x_bounds_by_ref"][zero.ref])
        self.assertEqual({"minimum": 1, "maximum": 2}, target_schema["x_bounds_by_ref"][low.ref])
        self.assertNotIn(high.ref, target_schema["legal_refs"])
        self.assertEqual("target_0", target_schema["groups"][0]["id"])
        hand_before = len(session.state.players["A"].zones["hand"])
        result = session.act("pilot:A", {"action_id": "cast", "targets": [low.ref], "x": 2, "pay": "auto"})
        self.assertTrue(result.ok, result.summary)
        self.assertEqual(0, sum(session.state.players["A"].mana_pool.values()))
        self.assertIsNone(self.resolve(session))
        self.assertEqual("A", session.state.cards[low.object_id].controller)
        self.assertEqual(hand_before, len(session.state.players["A"].zones["hand"]))
        self.replay(session, load=True)

    def test_mana_value_limit_requires_printed_x_and_its_exact_target_authority(self):
        source = self.db.lookup(CONTROL)
        for name in (EXILE, CONTROL):
            binding, _ = register_card(self.db, self.registry, name)
            self.assertTrue(binding["strict_capability_ready"], binding["blockers"])
        cases = (replace(source, mana_cost="{U}"),
                 replace(source, oracle_text=source.oracle_text.replace("X or less", "less than X")),
                 replace(source, oracle_text=source.oracle_text + " Then draw a card."))
        for record in cases:
            result = compile_oracle_card(record, capability_registry=self.registry, capability_profile=PROFILE)
            self.assertNotEqual("exact", result.status)
        raw = json.loads((ROOT / "quorune/rules/capability-registry.json").read_bytes())
        capability = next(row for row in raw["capabilities"] if row["id"] == ANNOUNCED_MANA_VALUE_TARGET_CAPABILITY)
        capability.update(status="blocked", blockers=["Announced target authority withdrawn."])
        registry = CapabilityRegistry(raw)
        declarations = read_evidence_document(ROOT / "platform/capability-evidence-declarations.json")
        index = build_index(registry_value=raw, declaration_source=declarations, discovered_test_ids=set(discover_tests(ROOT).values()))
        registry.mark_evidence_verified(validate_capability_evidence_index(index, registry=registry))
        result = compile_oracle_card(source, capability_registry=registry, capability_profile=PROFILE)
        self.assertNotEqual("exact", result.status)
        with patch("quorune.compiler.announced_mana_value_target.announced_mana_value_target_template", return_value=None):
            result = compile_oracle_card(source, capability_registry=self.registry, capability_profile=PROFILE)
            self.assertNotEqual("exact", result.status)
