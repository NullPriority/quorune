from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from common import ROOT
from quorune.abilities import ActivatedAbility, parse_activated_abilities
from quorune.carddb import CardDatabase
from quorune.card_programs import bind_card_program_runtime
from quorune.card_programs.adapters import compile_best_available_card_program
from quorune.compiler.activated_mill_costs import fixed_mill_activation_cost
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import CapabilityRegistry, load_default_capability_registry
from scripts.build_test_database import build_fixture_database


class MillLibraryActivationCostTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        db = Path(cls.temporary.name) / "mill-library-price.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures/bound-effect-program-cards.json",
            ROOT / "tests/fixtures/mill-library-activation-cost-cards.json"], db)
        cls.db = CardDatabase(db)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Mill library cost witnesses",
            [DeckEntry("Generic Bound Commander", 1, "commander"), DeckEntry("Generic Bound Plains", 30)],
            ["Generic Bound Commander"])

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temporary.cleanup()

    from test_bound_effect_programs import BoundEffectProgramRuntimeTests as _witnesses
    session = _witnesses.session
    add = _witnesses.add
    ready = _witnesses.ready
    checkpoint = _witnesses.checkpoint
    resolve = _witnesses.resolve
    replay = _witnesses.replay

    def test_mill_library_cost_is_required_and_produces_stack_mana(self):
        source = self.db.lookup("Millikin", fuzzy=False)

        def assert_exact():
            ir = compile_oracle_card(source, capability_registry=self.registry, capability_profile="commander_review")
            self.assertEqual("exact", ir.status, ir.material_residuals)
            return ir.faces[0].nodes[0]

        node = assert_exact()
        self.assertEqual("activated_ability", node.kind)
        self.assertEqual(1, node.cost["mill_library_cost"])
        self.assertEqual("mana", node.effects[0]["op"])
        with patch("quorune.compiler.activated_mill_costs.fixed_mill_activation_cost", side_effect=lambda ability: ability):
            with self.assertRaises(AssertionError):
                assert_exact()
        original, = parse_activated_abilities(card_name=source.name, oracle_text=source.oracle_text)
        descriptor = fixed_mill_activation_cost(original).to_dict()
        self.assertFalse(ActivatedAbility.from_dict(descriptor).mana_ability)
        for value in (False, 0, 2, None):
            with self.subTest(value=value), self.assertRaises(ValueError):
                ActivatedAbility.from_dict({**descriptor, "mill_library_cost": value})
        raw = json.loads((ROOT / "quorune/rules/capability-registry.json").read_bytes())
        next(row for row in raw["capabilities"] if row["id"] == "activation.library_mill.fixed_cost").update(
            status="blocked", blockers=["Mill cost deliberately unavailable"])
        registry = CapabilityRegistry(raw)
        registry.mark_evidence_verified("0" * 64)
        self.assertNotEqual("exact", compile_oracle_card(source, capability_registry=registry, capability_profile="commander_review").status)

    def test_original_millikin_mills_before_priority_and_adds_mana_at_resolution(self):
        session = self.session(283071)
        source = self.add(session.engine, "Millikin")
        program = compile_best_available_card_program(self.db, self.db.lookup(source.printed_name, fuzzy=False),
            semantic_registry=session.engine.semantics, capability_registry=self.registry, capability_profile="commander_review")
        binding = bind_card_program_runtime(program, capability_registry=self.registry, profile="commander_review")
        self.assertTrue(binding["strict_capability_ready"], binding)
        action = self.ready(session, source, {})
        self.assertFalse(action.get("mana_ability"))
        self.assertEqual(1, action["cost_summary"]["mill_library"])
        self.assertFalse("cost_cards" in (action.get("choice_schema") or {}))
        library = list(session.state.players["A"].zones["library"])
        self.checkpoint(session)
        before = authoritative_state_hash(session.state)
        rejected = session.act("pilot:B", {"action_id": action["id"]})
        self.assertFalse(rejected.ok, rejected.summary)
        self.assertEqual(before, authoritative_state_hash(session.state))
        paid = session.act("pilot:A", {"action_id": action["id"]})
        self.assertTrue(paid.ok, paid.summary)
        self.assertEqual(library[:-1], session.state.players["A"].zones["library"])
        self.assertEqual("graveyard", session.state.cards[library[-1]].zone)
        self.assertTrue(session.state.cards[source.object_id].tapped)
        self.assertEqual("activated_ability", session.state.stack[-1].kind)
        self.assertEqual(0, session.state.players["A"].mana_pool.get("C", 0))
        session = self.replay(session, load=True)
        self.resolve(session)
        self.assertEqual(1, session.state.players["A"].mana_pool.get("C", 0))
        self.replay(session, load=True)

    def test_millikin_replacement_choice_seals_the_top_card_and_replays(self):
        session = self.session(283072)
        source = self.add(session.engine, "Millikin")
        self.add(session.engine, "Rest in Peace", seat="B")
        self.add(session.engine, "Leyline of the Void", seat="C")
        action = self.ready(session, source, {})
        library = list(session.state.players["A"].zones["library"])
        self.checkpoint(session)
        suspended = session.act("pilot:A", {"action_id": action["id"]})
        self.assertTrue(suspended.ok, suspended.summary)
        self.assertEqual("replacement.order", session.state.pending_decision.kind)
        self.assertEqual(library, session.state.players["A"].zones["library"])
        self.assertFalse(session.state.cards[source.object_id].tapped)
        self.assertIsNone(session.packet("pilot:C", full=True).get("decision"))
        session = self.replay(session, load=True)
        packet = session.packet("pilot:A", full=True)["decision"]
        option = packet["ctx"]["options"][0]["id"]
        choice = {"action_id": "choose", "choices": {"replacement": option}}
        before = authoritative_state_hash(session.state)
        rejected = session.act("pilot:C", choice)
        self.assertFalse(rejected.ok, rejected.summary)
        self.assertEqual(before, authoritative_state_hash(session.state))
        paid = session.act("pilot:A", choice)
        self.assertTrue(paid.ok, paid.summary)
        self.assertEqual(library[:-1], session.state.players["A"].zones["library"])
        self.assertEqual("exile", session.state.cards[library[-1]].zone)
        self.assertTrue(session.state.cards[source.object_id].tapped)
        self.assertEqual(0, session.state.players["A"].mana_pool.get("C", 0))
        self.resolve(session)
        self.assertEqual(1, session.state.players["A"].mana_pool.get("C", 0))
        self.replay(session, load=True)

    def test_millikin_cannot_pay_with_an_empty_library(self):
        session = self.session(283073)
        source = self.add(session.engine, "Millikin")
        self.ready(session, source, {})
        engine = session.engine
        for object_id in tuple(session.state.players["A"].zones["library"]):
            engine.move_card(object_id, "outside", log=False)
        engine.permissions.invalidate_current()
        session.state.pending_decision = None
        engine._grant_priority("A")
        engine.pump()
        self.checkpoint(session)
        actions = session.packet("pilot:A", full=True)["decision"]["legal_actions"]
        self.assertFalse(any(row["id"].startswith("activate:" + source.ref + ":") for row in actions))
        self.assertFalse(session.state.cards[source.object_id].tapped)
        self.assertEqual([], session.state.stack)
        self.replay(session, load=True)
