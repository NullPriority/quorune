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
            "ability-source-counter-cards.json",
            "activation-view-equivalence-cards.json",
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

    def _talon_activation_offer(self, session, source):
        for _ in range(8):
            if session.pending_principals() == ["pilot:A"]:
                break
            accepted = session.act(session.pending_principals()[0], {"action_id": "pass"})
            self.assertTrue(accepted.ok, accepted.summary)
        else:
            self.fail("The hand source controller did not receive priority")
        context = session.packet("pilot:A", full=True)["decision"]["ctx"]
        return next(row for row in context["legal"]["actions"] if row.get("source") == source.ref
                    and row.get("from") == "hand" and row["id"].startswith("activate:"))

    def _talon_activate(self, session, source):
        action = self._talon_activation_offer(session, source)
        accepted = session.act("pilot:A", {"action_id": action["id"], "pay": "auto"})
        self.assertTrue(accepted.ok, accepted.summary)
        return session.state.stack[-1]

    def _talon_resolve_top(self, session):
        reference = session.state.stack[-1].ref
        for _ in range(12):
            if not any(item.ref == reference for item in session.state.stack):
                return
            self.assertEqual("priority", session.state.pending_decision.kind)
            accepted = session.act(session.pending_principals()[0], {"action_id": "pass"})
            self.assertTrue(accepted.ok, accepted.summary)
        self.fail("Top stack item did not resolve")

    def test_talon_hand_offer_reveal_and_optional_entry_targets_use_normal_replay(self):
        for take_target in (False, True):
            with self.subTest(take_target=take_target):
                session = self.session(702_290 + take_target)
                source = self.add(session.engine, "Talon Gates of Madara", zone="hand", ref="talon-source")
                basic = self.add(session.engine, self.land, zone="hand", ref="first-played-land")
                creature = self.add(session.engine, self.creature, seat="B", ref="phase-creature")
                other = self.add(session.engine, "Generic Bound Growth", zone="hand", ref="talon-private")
                artifact = self.add(session.engine, self.artifact, seat="B", ref="unphase-artifact")
                before_incarnation = creature.logical_object_id
                bound = self.binding(session, source)
                self.assertTrue(bound["strict_capability_ready"], bound["blockers"])
                self.prepare(session, mana={"C": 4})
                self.checkpoint(session)
                play = session.act("pilot:A", {"action_id": "play-land:" + basic.ref})
                self.assertTrue(play.ok, play.summary)
                action = self._talon_activation_offer(session, source)
                self.assertEqual(4, action["cost_summary"]["m"]["GENERIC"])
                packet = session.packet("pilot:B", full=True)
                self.assertNotIn(source.ref, json.dumps(packet))
                self.assertNotIn(other.ref, json.dumps(packet))
                before = authoritative_state_hash(session.state)
                wrong = session.act("pilot:B", {"action_id": action["id"], "pay": "auto"})
                self.assertFalse(wrong.ok, wrong.summary)
                self.assertEqual(before, authoritative_state_hash(session.state))
                item = self._talon_activate(session, source)
                self.assertEqual([], item.targets)
                self.assertEqual("hand", session.state.cards[source.object_id].zone)
                self.assertIn("B", session.state.cards[source.object_id].revealed_to)
                after = json.dumps(session.packet("pilot:B", full=True))
                self.assertIn(source.ref, after)
                self.assertNotIn(other.ref, after)
                session = self.replay(session, load=True)
                self.assertEqual("semantic.target", self.resolve(session).kind)
                self.assertEqual("battlefield", session.state.cards[source.object_id].zone)
                offered = session.state.pending_decision.payload_by_actor["A"]["target_schema"]
                self.assertEqual(1, offered["up_to"])
                self.assertIn(creature.ref, offered["legal_refs"])
                self.assertNotIn(artifact.ref, offered["legal_refs"])
                before = authoritative_state_hash(session.state)
                invalid = session.act("pilot:A", {"action_id": "choose", "targets": [artifact.ref]})
                self.assertFalse(invalid.ok, invalid.summary)
                self.assertEqual(before, authoritative_state_hash(session.state))
                session = self.replay(session, load=True)
                result = session.act("pilot:A", {"action_id": "choose",
                    "targets": [creature.ref] if take_target else []})
                self.assertTrue(result.ok, result.summary)
                self.assertIsNone(self.resolve(session))
                target = session.state.cards[creature.object_id]
                self.assertEqual((take_target, "battlefield", before_incarnation),
                                 (target.phased_out, target.zone, target.logical_object_id))
                self.assertEqual(0, sum(session.state.players["A"].mana_pool.values()))
                self.replay(session)

    def test_talon_countered_activation_ends_only_its_stack_reveal(self):
        from test_self_public_zone_casting import SelfPublicZoneRuntimeTests

        session = self.session(702_293)
        source = self.add(session.engine, "Talon Gates of Madara", zone="hand", ref="countered-talon")
        stifle = self.add(session.engine, "Stifle", seat="B", zone="hand")
        prior = tuple(source.revealed_to)
        session.state.players["B"].mana_pool["U"] = 1
        self.prepare(session, actor="A", active="B", mana={"C": 4})
        self.checkpoint(session)
        item = self._talon_activate(session, source)
        self.assertIn("B", session.state.cards[source.object_id].revealed_to)
        SelfPublicZoneRuntimeTests.cast(self, session, "B", stifle, [item.ref])
        self.assertIsNone(self.resolve(session))
        physical = session.state.cards[source.object_id]
        self.assertEqual(("hand", prior, source.logical_object_id),
                         (physical.zone, tuple(physical.revealed_to), physical.logical_object_id))
        self.replay(session)

    def test_talon_activation_cannot_move_the_returned_hand_incarnation(self):
        from test_self_public_zone_casting import SelfPublicZoneRuntimeTests

        session = self.session(702_294)
        source = self.add(session.engine, "Talon Gates of Madara", zone="hand", ref="stale-talon")
        bounce = self.add(session.engine, "Boomerang", seat="B", zone="hand")
        self.add(session.engine, self.creature, seat="B", ref="optional-creature")
        original_hand_incarnation = source.logical_object_id
        session.state.players["B"].mana_pool["U"] = 2
        self.prepare(session, mana={"C": 8})
        self.checkpoint(session)
        older = self._talon_activate(session, source)
        newer = self._talon_activate(session, source)
        self.assertNotEqual(older.ref, newer.ref)
        self._talon_resolve_top(session)
        self.assertEqual("semantic.target", session.state.pending_decision.kind)
        empty = session.act("pilot:A", {"action_id": "choose", "targets": []})
        self.assertTrue(empty.ok, empty.summary)
        while session.state.stack[-1].ref != older.ref:
            self._talon_resolve_top(session)
        self.assertEqual("battlefield", session.state.cards[source.object_id].zone)
        SelfPublicZoneRuntimeTests.cast(self, session, "B", bounce, [source.ref])
        self._talon_resolve_top(session)
        returned = session.state.cards[source.object_id]
        self.assertEqual("hand", returned.zone)
        new_incarnation = returned.logical_object_id
        self.assertNotEqual(original_hand_incarnation, new_incarnation)
        session = self.replay(session, load=True)
        self.assertIsNone(self.resolve(session))
        current = session.state.cards[source.object_id]
        self.assertEqual(("hand", new_incarnation), (current.zone, current.logical_object_id))
        self.assertFalse(session.state.stack)
        self.replay(session)

    def test_talon_fixed_optional_phase_and_revealed_land_price_fail_closed(self):
        from dataclasses import replace
        from quorune.card_programs.binding import bind_semantic_program_runtime
        from quorune.phasing_model import OPTIONAL_ENTRY_PHASE_OUT_CAPABILITY
        from quorune.rules.revealed_hand_zone_move import CAPABILITY

        card = self.db.lookup("Talon Gates of Madara")
        options = {"capability_registry": self.registry, "capability_profile": "commander_review"}
        exact = compile_oracle_card(card, **options)
        self.assertEqual("exact", exact.status, exact.material_residuals)
        session = self.session(702_295)
        self.add(session.engine, card.name, zone="hand")
        programs = session.engine.semantics.programs_for_oracle(card.oracle_id)
        phase, = (program for program in programs if OPTIONAL_ENTRY_PHASE_OUT_CAPABILITY in program.capability_dependencies)
        land, = (program for program in programs if CAPABILITY in program.capability_dependencies)
        for program in (
            replace(phase, target_schema={**phase.target_schema, "count": 2}),
            replace(phase, event="permanent.leave"),
            replace(land, effects=({**land.effects[0], "revealed_hand_land_entry": True},)),
            replace(land, cost_schema={**land.cost_schema, "text": "{1}"}),
        ):
            bound = bind_semantic_program_runtime(program, capability_registry=self.registry, profile="commander_review")
            self.assertFalse(bound["strict"])
        for producer in (
            "quorune.compiler.source_self_effect_templates._optional_entry_phase_out",
            "quorune.compiler.activated_mana_nodes.compile_self_zone_move",
        ):
            with patch(producer, return_value=None):
                omitted = compile_oracle_card(card, **options)
            self.assertNotEqual("exact", omitted.status)
