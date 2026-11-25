"""Native controller, target-selection and replay proof for any-number phase out."""

from pathlib import Path
from tempfile import TemporaryDirectory
import json
import unittest
from unittest.mock import patch

from common import ROOT
import test_announced_phase_out as controls
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.phasing_model import PHASE_OUT_ANY_NUMBER_SET_CAPABILITY
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import CapabilityRegistry, load_default_capability_registry
from quorune.rules.evidence import validate_capability_evidence_index
from quorune.rules.evidence_storage import read_evidence_document
from scripts.build_test_database import build_fixture_database
from scripts.update_capability_evidence import build_index, discover_tests


class AnyNumberPhaseOutRuntimeTests(unittest.TestCase):
    witness = controls.AnnouncedPhaseOutRuntimeTests
    register_fixture_cards_on_add = True
    session = witness.session
    add = witness.add
    prepare = witness.prepare
    checkpoint = witness.checkpoint
    resolve = witness.resolve
    replay = witness.replay
    binding = witness.binding
    actions = witness.actions
    action = witness.action
    screen = "Hush Lattice"
    guardian = "Threadfold Sentinel"
    creature = "Filament Watch"
    land = "Generic Bound Plains"
    artifact = "Bonesplitter"

    @classmethod
    def setUpClass(cls):
        cls.temp = TemporaryDirectory()
        path = Path(cls.temp.name) / "chosen-phase.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures" / name for name in (
            "bound-effect-program-cards.json",
            "ability-kind-cost-modifier-cards.json",
            "any-number-phase-out-cards.json",
        )], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Any-number phasing", [
            DeckEntry("Generic Bound Commander", 1, "commander"), DeckEntry("Generic Bound Plains", 30),
        ], ["Generic Bound Commander"])

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temp.cleanup()

    def test_convoke_phases_only_controlled_nonlands_in_one_batch_and_replays(self):
        session = self.session(702_281)
        spell = self.add(session.engine, self.screen, zone="hand")
        first = self.add(session.engine, self.creature, ref="chosen-convoke-first")
        second = self.add(session.engine, self.creature, ref="chosen-convoke-second")
        artifact = self.add(session.engine, self.artifact, ref="chosen-noncreature")
        invalid = [self.add(session.engine, self.land, ref="chosen-land"),
                   self.add(session.engine, self.creature, seat="B", ref="chosen-opponent")]
        invalid[0].tapped = True
        self.prepare(session, mana={"C": 2})
        self.checkpoint(session)
        bound = self.binding(session, spell)
        self.assertTrue(bound["strict_capability_ready"], bound["blockers"])
        action = self.action(session, spell)
        self.assertEqual({first.ref, second.ref, artifact.ref}, set(action["target_schema"]["legal_refs"]))
        payment = {"action_id": action["id"], "convoke_cards": [first.ref, second.ref], "pay": "auto"}
        before = authoritative_state_hash(session.state)
        for card in invalid:
            rejected = session.act("pilot:A", {**payment, "targets": [card.ref]})
            self.assertFalse(rejected.ok, rejected.summary)
            self.assertEqual(before, authoritative_state_hash(session.state))
        sent = session.act("pilot:A", {**payment, "targets": [first.ref, second.ref, artifact.ref]})
        self.assertTrue(sent.ok, sent.summary)
        self.assertTrue(all(session.state.cards[c.object_id].tapped for c in (first, second)))
        self.assertEqual(0, sum(session.state.players["A"].mana_pool.values()))
        session = self.replay(session, load=True)
        self.assertIsNone(self.resolve(session))
        effects = [e.details["out"] for e in session.state.events if e.code == "permanent.phase_out"]
        self.assertEqual([{first.ref, second.ref, artifact.ref}], [set(refs) for refs in effects])
        self.assertTrue(all(not session.state.cards[c.object_id].phased_out for c in invalid))
        self.replay(session, load=True)

    def test_flash_entry_choice_excludes_its_source_then_phases_and_replays(self):
        session = self.session(702_282)
        spell = self.add(session.engine, self.guardian, zone="hand", ref="chosen-entry-source")
        first = self.add(session.engine, self.creature, ref="chosen-entry-first")
        second = self.add(session.engine, self.creature, ref="chosen-entry-second")
        artifact = self.add(session.engine, self.artifact, ref="chosen-entry-artifact")
        foreign = self.add(session.engine, self.creature, seat="B", ref="chosen-entry-opponent")
        self.prepare(session, actor="A", active="B", mana={"C": 1, "W": 2})
        self.checkpoint(session)
        bound = self.binding(session, spell)
        self.assertTrue(bound["strict_capability_ready"], bound["blockers"])
        accepted = session.act("pilot:A", {"action_id": self.action(session, spell)["id"], "pay": "auto"})
        self.assertTrue(accepted.ok, accepted.summary)
        pending = self.resolve(session)
        self.assertEqual("semantic.target", pending.kind)
        schema = pending.payload_by_actor["A"]["target_schema"]
        self.assertEqual({first.ref, second.ref}, set(schema["legal_refs"]))
        before = authoritative_state_hash(session.state)
        for actor, target in (("A", spell), ("A", foreign), ("A", artifact), ("B", first)):
            denied = session.act("pilot:" + actor, {"action_id": "choose", "targets": [target.ref]})
            self.assertFalse(denied.ok, denied.summary)
            self.assertEqual(before, authoritative_state_hash(session.state))
        session = self.replay(session, load=True)
        chosen = session.act("pilot:A", {"action_id": "choose", "targets": [first.ref, second.ref]})
        self.assertTrue(chosen.ok, chosen.summary)
        self.assertIsNone(self.resolve(session))
        self.assertFalse(session.state.cards[spell.object_id].phased_out)
        self.assertTrue(all(session.state.cards[target.object_id].phased_out for target in (first, second)))
        self.replay(session, load=True)

    def test_any_number_phasing_authority_is_scope_bound_and_withdraws_both(self):
        raw = json.loads((ROOT / "quorune/rules/capability-registry.json").read_bytes())
        row = next(r for r in raw["capabilities"] if r["id"] == PHASE_OUT_ANY_NUMBER_SET_CAPABILITY)
        row.update(status="blocked", blockers=["Any-number phasing authority withdrawn."])
        withdrawn = CapabilityRegistry(raw)
        declarations = read_evidence_document(ROOT / "platform/capability-evidence-declarations.json")
        index = build_index(registry_value=raw, declaration_source=declarations,
                            discovered_test_ids=set(discover_tests(ROOT).values()))
        withdrawn.mark_evidence_verified(validate_capability_evidence_index(index, registry=withdrawn))
        for name in (self.screen, self.guardian):
            with self.subTest(name=name):
                card = self.db.lookup(name)
                exact = compile_oracle_card(card, capability_registry=self.registry, capability_profile="commander_review")
                self.assertEqual("exact", exact.status, exact.material_residuals)
                changed = compile_oracle_card(card, capability_registry=withdrawn, capability_profile="commander_review")
                self.assertNotEqual("exact", changed.status)
                with patch("quorune.compiler.any_number_target_sets.any_number_target_set_template", return_value=None):
                    omitted = compile_oracle_card(card, capability_registry=self.registry, capability_profile="commander_review")
                    self.assertNotEqual("exact", omitted.status)
