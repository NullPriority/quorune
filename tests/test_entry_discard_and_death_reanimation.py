from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from common import ROOT
from quorune.carddb import CardDatabase
from quorune.card_programs import bind_card_program_runtime
from quorune.card_programs.adapters import compile_best_available_card_program
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database


class EntryDiscardAndDeathReanimationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        db = Path(cls.temporary.name) / "body-snatcher.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures/bound-effect-program-cards.json",
            ROOT / "tests/fixtures/entry-discard-and-death-reanimation-cards.json"], db)
        cls.db = CardDatabase(db)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Source entry and death", [DeckEntry("Generic Bound Commander", 1, "commander"),
            DeckEntry("Generic Bound Plains", 30)], ["Generic Bound Commander"])

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

    def test_body_snatcher_requires_both_intrinsic_source_triggers(self):
        record = self.db.lookup("Body Snatcher", fuzzy=False)

        def assert_exact():
            ir = compile_oracle_card(record, capability_registry=self.registry, capability_profile="commander_review")
            self.assertEqual("exact", ir.status, ir.material_residuals)
            return ir

        ir = assert_exact()
        entry, death = ir.faces[0].nodes
        self.assertIn("effect.choice.discard_creature_or_exile_source", entry.capability_dependencies)
        self.assertIn("zone.reanimate.after_source_death_exile", death.capability_dependencies)
        for name in ("quorune.rules.source_entry_discard_exile.entry_discard_exile_capabilities",
                     "quorune.rules.reanimation_capability_shapes.fixed_target_reanimation_node_capabilities"):
            with self.subTest(omitted=name), patch(name, return_value=()):
                with self.assertRaises(AssertionError):
                    assert_exact()

    def test_body_snatcher_entry_preserves_private_creature_payment_or_exiles_the_source(self):
        for index, pay in enumerate((True, False)):
            with self.subTest(pay=pay):
                session = self.session(283090 + index)
                engine = session.engine
                source = self.add(engine, "Body Snatcher", zone="hand")
                offered_card = self.add(engine, "Raging Goblin", zone="hand", ref="private-payment")
                wrong = self.add(engine, "Generic Bound Plains", zone="hand", ref="wrong-cost-type")
                program = compile_best_available_card_program(self.db, self.db.lookup(source.printed_name, fuzzy=False),
                    semantic_registry=engine.semantics, capability_registry=self.registry, capability_profile="commander_review")
                binding = bind_card_program_runtime(program, capability_registry=self.registry, profile="commander_review")
                self.assertTrue(binding["strict_capability_ready"], binding)
                cast = self.ready(session, source, {"B": 2, "C": 2})
                self.checkpoint(session)
                accepted = session.act("pilot:A", {"action_id": cast["id"], "pay": "auto"})
                self.assertTrue(accepted.ok, accepted.summary)
                self.assertEqual("semantic.choice", self.resolve(session).kind)
                self.assertIsNone(session.packet("pilot:B", full=True).get("decision"))
                self.assertNotIn(offered_card.ref, str(session.packet("pilot:B", full=True)))
                before = authoritative_state_hash(session.state)
                denied = session.act("pilot:A", {"action_id": "choose", "cards": [wrong.ref], "pay": True})
                self.assertFalse(denied.ok, denied.summary)
                self.assertEqual(before, authoritative_state_hash(session.state))
                session = self.replay(session, load=True)
                paid = session.act("pilot:A", {"action_id": "choose", "cards": [offered_card.ref] if pay else [], "pay": pay})
                self.assertTrue(paid.ok, paid.summary)
                self.assertEqual("battlefield" if pay else "exile", session.state.cards[source.object_id].zone)
                self.assertEqual("graveyard" if pay else "hand", session.state.cards[offered_card.object_id].zone)
                self.replay(session, load=True)

    def test_body_snatcher_death_exiles_the_original_and_returns_only_a_current_target(self):
        for index, self_target in enumerate((False, True)):
            with self.subTest(self_target=self_target):
                session = self.session(283092 + index)
                engine = session.engine
                source = self.add(engine, "Body Snatcher")
                recipient = self.add(engine, "Raging Goblin", zone="graveyard")
                wrong = self.add(engine, "Raging Goblin", seat="B", zone="graveyard", ref="other-graveyard")
                spell = self.add(engine, "Murder", zone="hand")
                cast = self.ready(session, spell, {"B": 2, "C": 1})
                self.checkpoint(session)
                result = session.act("pilot:A", {"action_id": cast["id"], "targets": [source.ref], "pay": "auto"})
                self.assertTrue(result.ok, result.summary)
                self.assertEqual("semantic.target", self.resolve(session).kind)
                before = authoritative_state_hash(session.state)
                rejected = session.act("pilot:A", {"action_id": "choose", "targets": [wrong.ref]})
                self.assertFalse(rejected.ok, rejected.summary)
                self.assertEqual(before, authoritative_state_hash(session.state))
                target = session.state.cards[source.object_id] if self_target else recipient
                chosen = session.act("pilot:A", {"action_id": "choose", "targets": [target.ref]})
                self.assertTrue(chosen.ok, chosen.summary)
                session = self.replay(session, load=True)
                self.resolve(session)
                self.assertEqual("exile", session.state.cards[source.object_id].zone)
                self.assertEqual("graveyard" if self_target else "battlefield", session.state.cards[recipient.object_id].zone)
                self.replay(session, load=True)
