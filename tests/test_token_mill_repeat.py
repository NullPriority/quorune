"""Behavioral witnesses for a token, a committed mill, and a source-scoped repeat."""

from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from common import ROOT
import test_qualified_hexproof as native
from test_controller_creature_keyword_absence import register_card
from quorune.carddb import CardDatabase
from quorune.characteristic_evaluation import type_parts
from quorune.compiler.token_mill_repeat_templates import fixed_token_mill_repeat_template
from quorune.continuous_effect_model import Layer
from quorune.deck import DeckDefinition, DeckEntry
from quorune.errors import GameRuleError
from quorune.milling import MillObjectIdentity, MilledCardResult, MillResult
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import CapabilityRegistry, load_default_capability_registry
from quorune.rules.evidence import validate_capability_evidence_index
from quorune.rules.evidence_storage import read_evidence_document
from quorune.rules.token_mill_repeat import (
    TOKEN_MILL_REPEAT_CAPABILITY, TOKEN_MILL_REPEAT_DEPENDENCIES,
    TOKEN_MILL_REPEAT_OPERATION, TokenMillRepeatSpec, has_public_milled_subtype,
)
from quorune.zone_transitions import ZoneTransitionOwner
from scripts.build_test_database import build_fixture_database
from scripts.update_capability_evidence import build_index, discover_tests


SOURCE = "Willow, Thread Keeper"
KINDRED = "Hollow Colony Echo"
OTHER = "Generic Bound Plains"
BOUNCE = "Mistfield Recall"
PROFILE = "commander_review"
FIXTURES = ROOT / "tests" / "fixtures"


class TokenMillRepeatTests(unittest.TestCase):
    register_fixture_cards_on_add = True
    session = native.QualifiedHexproofTests.session
    add = native.QualifiedHexproofTests.add
    prepare = native.QualifiedHexproofTests.prepare
    checkpoint = native.QualifiedHexproofTests.checkpoint
    priority = native.QualifiedHexproofTests.priority
    cast = native.QualifiedHexproofTests.cast
    resolve = native.QualifiedHexproofTests.resolve
    replay = native.QualifiedHexproofTests.replay

    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        database = Path(cls.temporary.name) / "fixed-token-mill-repeat.sqlite3"
        build_fixture_database([FIXTURES / "bound-effect-program-cards.json",
                                FIXTURES / "token-mill-repeat-cards.json"], database)
        cls.db = CardDatabase(database)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Thread Keeper experiment", [
            DeckEntry("Generic Bound Commander", 1, "commander"),
            DeckEntry(OTHER, 30),
        ], ["Generic Bound Commander"])

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temporary.cleanup()

    def scene(self, seed, order, *, counter_choices=False, departure=False):
        session = self.session(seed)
        engine = session.engine
        source = self.add(engine, SOURCE, zone="hand")
        library = session.state.players["A"].zones["library"]
        ZoneTransitionOwner(engine).move_cards_simultaneously(
            [(obj, "exile") for obj in tuple(library)], reason="Set the sealed test library", log=False,
        )
        objects = [self.add(engine, name, zone="library", ref=f"repeat-library-{i}")
                   for i, name in enumerate(reversed(order))]
        self.assertEqual([session.state.cards[obj].printed_name for obj in reversed(library)], list(order))
        hidden = self.add(engine, KINDRED, seat="D", zone="hand", ref="repeat-foreign-private")
        bounce = self.add(engine, BOUNCE, seat="B", zone="hand") if departure else None
        if counter_choices:
            self.add(engine, "Generic Bound Counter Adder")
            self.add(engine, "Generic Bound Counter Doubler")
        self.prepare(session, mana={"C": 1, "G": 1})
        if departure:
            session.state.players["B"].mana_pool["U"] = 1
        self.checkpoint(session)
        self.cast(session, "A", source, payment={"C": 1, "G": 1})
        pending = self.resolve(session)
        # An effect that modifies loyalty on entry can require a choice before this fixture starts.
        if counter_choices:
            self.finish_counter_choices(session, pending)
        else:
            self.assertIsNone(pending)
        source = session.state.cards[source.object_id]
        self.assertEqual(source.zone, "battlefield")
        return session, source, tuple(reversed(objects)), hidden, bounce

    def finish_counter_choices(self, session, pending):
        for _ in range(5):
            if pending is None or pending.kind != "replacement.order":
                break
            options = session.packet("pilot:A", full=True)["decision"]["ctx"]["options"]
            result = session.act("pilot:A", {"action_id": "choose", "replacement": options[0]["id"]})
            self.assertTrue(result.ok, result.summary)
            pending = self.resolve(session)
        self.assertIsNone(pending)

    def activate_repeat(self, session, source):
        self.priority(session, "A")
        actions = session.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        options = [row["id"] for row in actions if row["id"].startswith("activate:" + source.ref + ":")]
        self.assertEqual(len(options), 1, actions)
        result = session.act("pilot:A", {"action_id": options[0], "pay": "auto"})
        self.assertTrue(result.ok, result.summary)

    def tokens(self, session):
        return tuple(card for card in session.state.cards.values()
                     if card.zone == "battlefield" and card.is_token and card.controller == "A")

    def assert_hidden(self, session, hidden):
        for seat in "ABC":
            self.assertNotIn(hidden.ref, json.dumps(session.packet("pilot:" + seat, full=True)))
        self.assertIn(hidden.ref, json.dumps(session.packet("pilot:D", full=True)))

    def test_native_repeat_matches_kindred_last_card_then_creates_final_token_on_empty_mill(self):
        session, source, (last,), hidden, _ = self.scene(701_017_21, (KINDRED,))
        self.assertEqual(source.counters["loyalty"], 3)
        self.activate_repeat(session, source)
        self.assertEqual(session.state.cards[source.object_id].counters["loyalty"], 4)
        session = self.replay(session, load=True)
        self.assertIsNone(self.resolve(session))
        self.assertEqual(len(self.tokens(session)), 2)
        self.assertEqual(session.state.cards[source.object_id].counters["loyalty"], 5)
        self.assertEqual(session.state.cards[last.object_id].zone, "graveyard")
        self.assertFalse(session.state.players["A"].zones["library"])
        self.assert_hidden(session, hidden)
        self.replay(session, load=True)

    def test_native_repeat_keeps_going_after_source_departure_and_stops_on_other_subtype(self):
        session, source, (matching, other), hidden, bounce = self.scene(
            701_017_22, (KINDRED, OTHER), departure=True,
        )
        initial = source.logical_object_id
        self.activate_repeat(session, source)
        self.cast(session, "B", bounce, targets=[source.ref], payment={"U": 1})
        session = self.replay(session, load=True)
        self.assertIsNone(self.resolve(session))
        current = session.state.cards[source.object_id]
        self.assertEqual(current.zone, "hand")
        self.assertNotEqual(current.logical_object_id, initial)
        self.assertEqual(current.counters, {})
        self.assertEqual(len(self.tokens(session)), 2)
        self.assertEqual([session.state.cards[obj.object_id].zone for obj in (matching, other)],
                         ["graveyard", "graveyard"])
        self.assert_hidden(session, hidden)
        self.replay(session)

    def test_counter_replacement_after_mill_resumes_once_and_keeps_the_prior_mill(self):
        session, source, (first, next_card), hidden, _ = self.scene(
            701_017_23, (KINDRED, OTHER), counter_choices=True,
        )
        self.activate_repeat(session, source)
        paid = session.state.cards[source.object_id].counters["loyalty"]
        pending = self.resolve(session)
        self.assertEqual(pending.kind, "replacement.order")
        self.assertEqual(len(self.tokens(session)), 1)
        self.assertEqual(session.state.cards[first.object_id].zone, "graveyard")
        self.assertEqual(session.state.cards[next_card.object_id].zone, "library")
        options = session.packet("pilot:A", full=True)["decision"]["ctx"]["options"]
        before = authoritative_state_hash(session.state)
        wrong = session.act("pilot:B", {"action_id": "choose", "replacement": options[0]["id"]})
        self.assertFalse(wrong.ok)
        self.assertEqual(authoritative_state_hash(session.state), before)
        self.assert_hidden(session, hidden)
        session = self.replay(session, load=True)
        self.finish_counter_choices(session, session.state.pending_decision)
        self.assertEqual(len(self.tokens(session)), 2)
        self.assertIn(session.state.cards[source.object_id].counters["loyalty"], (paid + 3, paid + 4))
        self.assertEqual(session.state.cards[next_card.object_id].zone, "graveyard")
        self.assertFalse(session.state.players["A"].zones["library"])
        self.replay(session, load=True)

    def test_native_failed_mill_rolls_back_token_and_pass_before_retry(self):
        session, source, (first,), _, _ = self.scene(701_017_24, (OTHER,))
        self.activate_repeat(session, source)
        for _ in range(3):
            principal, = session.pending_principals()
            result = session.act(principal, {"action_id": "pass"})
            self.assertTrue(result.ok, result.summary)
        principal, = session.pending_principals()
        before = authoritative_state_hash(session.state)
        with patch("quorune.milling.mill_cards", side_effect=GameRuleError("Failed mill witness")):
            failed = session.act(principal, {"action_id": "pass"})
        self.assertFalse(failed.ok)
        self.assertIn("Failed mill witness", failed.summary)
        self.assertEqual(authoritative_state_hash(session.state), before)
        self.assertFalse(self.tokens(session))
        self.assertEqual(session.state.cards[first.object_id].zone, "library")
        self.assertTrue(session.act(principal, {"action_id": "pass"}).ok)
        self.assertIsNone(self.resolve(session))
        self.assertEqual(len(self.tokens(session)), 1)
        self.replay(session)

    def test_repeat_source_descriptor_scope_and_child_authority_fail_closed(self):
        source = self.db.lookup(SOURCE)
        body = source.oracle_text.splitlines()[1].split(":",1)[1].strip()
        template = fixed_token_mill_repeat_template(body, card_name=SOURCE)
        self.assertIsNotNone(template)
        initial, repeat = template[1]
        self.assertEqual(TokenMillRepeatSpec.from_effect(repeat).to_effect(), repeat)
        self.assertEqual(initial, repeat["token"])
        for raw in (dict(repeat, schema_version=True), dict(repeat, subtype="chosen"),
                    dict(repeat, token=dict(initial, controller="B")), dict(repeat, target="a")):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                TokenMillRepeatSpec.from_effect(raw)
        for invalid in (body.replace("mill a card", "mill two cards"),
                        body.replace("on Willow", "on Cedar"), body+" Draw a card."):
            self.assertIsNone(fixed_token_mill_repeat_template(invalid, card_name=SOURCE))
        binding, _ = register_card(self.db, self.registry, SOURCE)
        self.assertTrue(binding["strict_capability_ready"], binding["blockers"])
        raw = json.loads((ROOT/"quorune/rules/capability-registry.json").read_bytes())
        declarations = read_evidence_document(ROOT/"platform/capability-evidence-declarations.json")
        ids = set(discover_tests(ROOT).values())
        for capability in (TOKEN_MILL_REPEAT_CAPABILITY, *TOKEN_MILL_REPEAT_DEPENDENCIES):
            with self.subTest(removed=capability):
                modified = json.loads(json.dumps(raw))
                row = next(r for r in modified["capabilities"] if r["id"]==capability)
                row.update(status="blocked", blockers=["Repeat owner withdrawn for scope evidence."])
                registry = CapabilityRegistry(modified)
                index = build_index(registry_value=modified, declaration_source=declarations, discovered_test_ids=ids)
                registry.mark_evidence_verified(validate_capability_evidence_index(index, registry=registry))
                ir = compile_oracle_card(source, capability_registry=registry, capability_profile=PROFILE)
                self.assertNotEqual(ir.status, "exact")
        with patch("quorune.compiler.token_mill_repeat_templates.fixed_token_mill_repeat_template",
                   return_value=None):
            ir = compile_oracle_card(source, capability_registry=self.registry, capability_profile=PROFILE)
            self.assertNotEqual(ir.status, "exact")

    def test_mill_result_uses_only_current_revealed_public_incarnations(self):
        seen = []
        cards = {}

        def snapshot(card, *, maximum_layer):
            self.assertIs(maximum_layer, Layer.TYPE)
            seen.append(card.logical_object_id)
            return {"type_line": card.type_line}

        def result(identity, zone, *, face_down=False, epoch=1):
            cards[identity] = SimpleNamespace(
                logical_object_id=f"{identity}@{epoch}", zone=zone,
                type_line="Kindred Instant \u2014 Insect", face_down=face_down,
            )
            return MilledCardResult(MillObjectIdentity(identity, f"{identity}@0", identity), zone, f"{identity}@1")

        private = result("hidden-hand", "hand")
        facedown = result("hidden-exile", "exile", face_down=True)
        stale = result("old-object", "graveyard", epoch=2)
        public = result("public-kindred", "exile")
        host = SimpleNamespace(state=SimpleNamespace(cards=cards),
                               _effective_card_data=snapshot, _type_parts=type_parts)
        self.assertFalse(has_public_milled_subtype(host, MillResult("A", 1, ()), "insect"))
        self.assertFalse(has_public_milled_subtype(host, MillResult("A", 3, (private, facedown, stale)), "insect"))
        self.assertEqual(seen, [])
        self.assertTrue(has_public_milled_subtype(host, MillResult("A", 2, (private, public)), "insect"))
        self.assertEqual(seen, ["public-kindred@1"])
