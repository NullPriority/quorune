from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from common import ROOT
from quorune.carddb import CardDatabase
from quorune.card_programs import bind_card_program_runtime
from quorune.card_programs.adapters import compile_best_available_card_program
from quorune.deck import DeckDefinition, DeckEntry
from quorune.linked_exile_characteristic_model import CAPABILITY
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import CapabilityRegistry, load_default_capability_registry
from scripts.build_test_database import build_fixture_database


class LinkedExileCharacteristicsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        db = Path(cls.temporary.name) / "linked-exile-characteristics.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures/bound-effect-program-cards.json",
            ROOT / "tests/fixtures/linked-exile-characteristics-cards.json"], db)
        cls.db = CardDatabase(db)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Public exile characteristics",
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

    def test_linked_exile_layer_requires_the_complete_source_pair(self):
        record = self.db.lookup("Duplicant", fuzzy=False)

        def assert_exact():
            compiled = compile_oracle_card(record, capability_registry=self.registry, capability_profile="commander_review")
            self.assertEqual("exact", compiled.status, compiled.material_residuals)
            return compiled.faces[0].nodes

        entry, static = assert_exact()
        self.assertEqual(entry.effects[0]["effects"][0]["binding_id"], static.handlers[0]["binding_id"])
        self.assertIn(CAPABILITY, static.capability_dependencies)
        with patch("quorune.compiler.linked_exile_characteristics.paired_linked_exile_characteristics", side_effect=lambda nodes, *_a, **_k: nodes):
            with self.assertRaises(AssertionError):
                assert_exact()
        raw = json.loads((ROOT / "quorune/rules/capability-registry.json").read_bytes())
        next(row for row in raw["capabilities"] if row["id"] == CAPABILITY).update(
            status="blocked", blockers=["linked characteristic source deliberately unavailable"])
        missing = CapabilityRegistry(raw)
        missing.mark_evidence_verified("0" * 64)
        independently_supported = compile_oracle_card(record, capability_registry=missing, capability_profile="commander_review")
        self.assertNotEqual("exact", independently_supported.status)

    def test_the_latest_current_face_up_creature_link_supplies_the_layer(self):
        from quorune.continuous_effect_model import ContinuousObjectIdentity
        from quorune.linked_ability_exile_state import exile_for_linked_ability
        from quorune.linked_exile_characteristic_model import HANDLER_ID

        session = self.session(283081)
        engine = session.engine
        source = self.add(engine, "Duplicant")
        earlier = self.add(engine, "Raging Goblin", seat="B", ref="z-first")
        latest = self.add(engine, "Generic Bound Body", seat="C", ref="a-next")
        other = self.add(engine, "Generic Bound Plains", seat="D", ref="noncreature-afterward")
        spec = next(handler for program in engine.semantics.programs_for_oracle(source.oracle_id)
                    for handler in program.handlers if handler["handler_id"] == HANDLER_ID)
        identity = ContinuousObjectIdentity(source.object_id, source.logical_object_id)
        for card in (earlier, latest, other):
            exile_for_linked_ability(engine, card=card, source=identity,
                                    ability_pair=spec["binding_id"], reason="linked characteristics fixture")
        expected = engine._effective_card_data(session.state.cards[latest.object_id])
        initial = engine._effective_card_data(session.state.cards[earlier.object_id])
        current = engine._effective_card_data(session.state.cards[source.object_id])
        self.assertNotEqual(initial["power"], expected["power"])
        self.assertEqual((expected["power"], expected["toughness"]), (current["power"], current["toughness"]))
        exiled = session.state.cards[latest.object_id]
        exiled.face_down = True
        hidden = engine._effective_card_data(session.state.cards[source.object_id])
        self.assertEqual((initial["power"], initial["toughness"]), (hidden["power"], hidden["toughness"]))
        self.checkpoint(session)
        self.replay(session, load=True)

    def test_original_duplicant_adopts_only_the_public_link_and_reverts_after_its_departure(self):
        session = self.session(283080)
        engine = session.engine
        source = self.add(engine, "Duplicant", zone="hand")
        target = self.add(engine, "Raging Goblin", seat="B")
        wrong = self.add(engine, "Generic Bound Plains", seat="D")
        private = self.add(engine, "Generic Bound Growth", seat="C", zone="hand", ref="private-C-duplicant")
        release = self.add(engine, "Pull from Eternity", zone="hand")
        program = compile_best_available_card_program(self.db, self.db.lookup(source.printed_name, fuzzy=False),
            semantic_registry=engine.semantics, capability_registry=self.registry, capability_profile="commander_review")
        binding = bind_card_program_runtime(program, capability_registry=self.registry, profile="commander_review")
        self.assertTrue(binding["strict_capability_ready"], binding)
        cast = self.ready(session, source, {"C": 6, "W": 1})
        self.checkpoint(session)
        paid = session.act("pilot:A", {"action_id": cast["id"], "pay": "manual", "payment": {"C": 6}})
        self.assertTrue(paid.ok, paid.summary)
        self.assertEqual("semantic.target", self.resolve(session).kind)
        before = authoritative_state_hash(session.state)
        illegal = session.act("pilot:A", {"action_id": "choose", "targets": [wrong.ref]})
        self.assertFalse(illegal.ok, illegal.summary)
        self.assertEqual(before, authoritative_state_hash(session.state))
        chosen = session.act("pilot:A", {"action_id": "choose", "targets": [target.ref]})
        self.assertTrue(chosen.ok, chosen.summary)
        self.assertEqual("semantic.choice", self.resolve(session).kind)
        self.assertIsNone(session.packet("pilot:C", full=True).get("decision"))
        session = self.replay(session, load=True)
        applied = session.act("pilot:A", {"action_id": "choose", "choice": "apply"})
        self.assertTrue(applied.ok, applied.summary)
        self.assertEqual("exile", session.state.cards[target.object_id].zone)
        current = session.state.cards[source.object_id]
        copied = session.engine._effective_card_data(current)
        self.assertEqual(("1", "1"), (str(copied["power"]), str(copied["toughness"])))
        self.assertEqual({"goblin", "berserker", "shapeshifter"}, session.engine._type_parts(copied["type_line"])[1])
        self.assertNotIn(private.ref, str(session.packet("pilot:A", full=True)))
        actions = session.packet("pilot:A", full=True)["decision"]["legal_actions"]
        clear = next(action for action in actions if action.get("card") == release.ref)
        result = session.act("pilot:A", {"action_id": clear["id"], "targets": [session.state.cards[target.object_id].ref],
                                         "pay": "auto"})
        self.assertTrue(result.ok, result.summary)
        self.resolve(session)
        self.assertEqual("graveyard", session.state.cards[target.object_id].zone)
        reset = session.engine._effective_card_data(session.state.cards[source.object_id])
        self.assertEqual(("2", "4"), (str(reset["power"]), str(reset["toughness"])))
        self.assertEqual({"shapeshifter"}, session.engine._type_parts(reset["type_line"])[1])
        self.replay(session, load=True)
