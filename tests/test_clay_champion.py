from __future__ import annotations

"""CR 400.7d, 601.2h, 614.12. Independent pairs of mana actually spent."""

from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.entry_counter_model import (
    DynamicEntryCounterAmountSpec, DynamicEntryCounterCalculation, PAID_GREEN_PAIRS_ENTRY_CAPABILITY,
)
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import CapabilityRegistry, load_default_capability_registry
from quorune.source_entry_cast_information import (
    EntryCastInformation, PAID_WHITE_ENTRY_TARGET_CAPABILITY, SOURCE_ENTRY_CAST_INFORMATION_FIELD,
)
from quorune.spell_mana_counts import MANA_SPENT_COUNTS_CAPABILITY
from scripts.build_test_database import build_fixture_database
import test_paid_color_entry as paid_controls


class ClayChampionTests(InheritedNativeFixture, unittest.TestCase):
    actions = paid_controls.PaidColorEntryTests.actions
    binding = paid_controls.PaidColorEntryTests.binding
    private_replay = paid_controls.PaidColorEntryTests.private_replay

    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        path = Path(cls.temporary.name) / "clay-champion.sqlite3"
        build_fixture_database([
            ROOT / "tests/fixtures/bound-effect-program-cards.json",
            ROOT / "tests/fixtures/paid-color-pair-entry-cards.json",
        ], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition(
            "Clay Champion",
            [DeckEntry("Generic Bound Commander", 1, "commander"), DeckEntry("Generic Bound Plains", 30)],
            ["Generic Bound Commander"],
        )

    def prepare_clay(self, seed, payment, *, opponent_mana=None, removal=(), bring_back=False):
        session = self.session(seed)
        source = self.add(session.engine, "Clay Champion", zone="hand")
        self.assert_closed(session, source)
        bodies = tuple(self.add(session.engine, "Grizzly Bears", ref=f"clay-own-bear-{n}") for n in range(3))
        foreign = self.add(session.engine, "Grizzly Bears", seat="B", ref="clay-foreign-bear")
        private = self.add(session.engine, "Generic Bound Growth", seat="C", zone="hand", ref="clay-private-control")
        spells = tuple(self.add(session.engine, name, seat="B", zone="hand", ref=f"clay-removal-{n}")
                       for n, name in enumerate(removal))
        reanimation = self.add(session.engine, "Zombify", zone="hand") if bring_back else None
        mana = dict(payment)
        if bring_back:
            mana.update(B=1, C=3)
        self.prepare(session, mana=mana)
        session.state.players["B"].mana_pool.update(opponent_mana or {})
        self.checkpoint(session)
        return session, source, bodies, foreign, private, spells, reanimation

    def cast_paid(self, session, source, payment):
        offer = next((row for row in self.actions(session) if row["id"] == "cast:" + source.ref), None)
        self.assertIsNotNone(offer)
        command = {"action_id": offer["id"], "x": sum(payment.values()) - 4, "pay": "manual", "payment": payment}
        before = authoritative_state_hash(session.state)
        self.assertFalse(session.act("pilot:D", command).ok)
        self.assertEqual(before, authoritative_state_hash(session.state))
        result = session.act("pilot:A", command)
        self.assertTrue(result.ok, result.summary)
        item = session.state.stack[-1]
        current = session.state.cards[source.object_id]
        self.assertEqual(command["x"], item.x_value)
        self.assertEqual(payment.get("G", 0), item.mana_spent_counts.count_for(current, "G"))
        self.assertEqual(payment.get("W", 0), item.mana_spent_counts.count_for(current, "W"))
        return item.mana_spent_counts

    def wait_for_entry_targets(self, session):
        self.until(session, lambda game: game.state.pending_decision.kind == "semantic.target")
        self.assertEqual(("A",), tuple(session.state.pending_decision.actors))
        return session.packet("pilot:A", full=True)["decision"]["ctx"]["target_schema"]

    def counters(self, session, card):
        return session.state.cards[card.object_id].counters.get("+1/+1", 0)

    def trigger_info(self, session, source):
        entry, = (item for item in session.state.stack
                  if item.source_object_id == source.object_id and item.kind == "triggered_ability")
        return EntryCastInformation.from_dict(entry.context[SOURCE_ENTRY_CAST_INFORMATION_FIELD])

    def test_actual_four_green_four_white_places_six_before_targeting_and_two_on_each(self):
        payment = {"G": 4, "W": 4}
        session, source, bodies, foreign, private, _, _ = self.prepare_clay(795100, payment)
        receipt = self.cast_paid(session, source, payment)
        session = self.replay(session, load=True)
        schema = self.wait_for_entry_targets(session)
        source = session.state.cards[source.object_id]
        self.assertEqual("battlefield", source.zone)
        self.assertEqual(6, self.counters(session, source))
        self.assertEqual((0, 0, 0), tuple(self.counters(session, card) for card in bodies))
        self.assertEqual({card.ref for card in bodies}, set(schema["legal_refs"]))
        self.assertEqual(receipt, self.trigger_info(session, source).mana_spent_counts)
        before = authoritative_state_hash(session.state)
        for wrong in ((source.ref,), (foreign.ref,), (bodies[0].ref,) * 2, tuple(card.ref for card in bodies)):
            with self.subTest(wrong=wrong):
                self.assertFalse(session.act("pilot:A", {"action_id": "choose", "targets": list(wrong)}).ok)
                self.assertEqual(before, authoritative_state_hash(session.state))
        session = self.replay(session, load=True)
        self.target(session, "A", (bodies[0].ref, bodies[1].ref))
        self.assertIsNone(self.resolve(session))
        self.assertEqual((2, 2, 0), tuple(self.counters(session, card) for card in bodies))
        self.assertEqual(0, self.counters(session, foreign))
        self.assertEqual(6, self.counters(session, source))
        self.private_replay(session, private)

    def test_actual_odd_payment_survives_source_departure_and_revalidates_each_target(self):
        payment = {"G": 3, "W": 3}
        session, source, bodies, _, private, spells, _ = self.prepare_clay(
            795101, payment, opponent_mana={"U": 2}, removal=("Unsummon", "Unsummon"),
        )
        receipt = self.cast_paid(session, source, payment)
        self.wait_for_entry_targets(session)
        self.assertEqual(3, self.counters(session, source))
        old_source = session.state.cards[source.object_id].logical_object_id
        self.target(session, "A", (bodies[0].ref, bodies[1].ref))
        self.cast(session, "B", spells[0], [source.ref])
        self.resolve_top(session)
        self.cast(session, "B", spells[1], [bodies[1].ref])
        self.resolve_top(session)
        self.assertEqual("hand", session.state.cards[source.object_id].zone)
        self.assertEqual("hand", session.state.cards[bodies[1].object_id].zone)
        session = self.replay(session, load=True)
        info = self.trigger_info(session, source)
        self.assertEqual(old_source, info.source.logical_object_id)
        self.assertEqual(receipt, info.mana_spent_counts)
        self.assertIsNone(self.resolve(session))
        self.assertEqual((1, 0, 0), tuple(self.counters(session, card) for card in bodies))
        self.private_replay(session, private)

    def test_actual_mixed_payment_keeps_color_pairs_independent_at_zero_each(self):
        for index, (payment, green, white, choose) in enumerate((
            ({"G": 1, "W": 3}, 0, 1, True),
            ({"G": 3, "W": 1}, 3, 0, True),
            ({"C": 4}, 0, 0, False),
        )):
            with self.subTest(payment=payment):
                session, source, bodies, _, _, _, _ = self.prepare_clay(795110 + index, payment)
                self.cast_paid(session, source, payment)
                self.wait_for_entry_targets(session)
                self.assertEqual(green, self.counters(session, source))
                self.target(session, "A", (bodies[0].ref,) if choose else ())
                self.assertIsNone(self.resolve(session))
                self.assertEqual((white, 0, 0), tuple(self.counters(session, card) for card in bodies))
                self.replay(session, load=True)

    def test_actual_reanimation_after_green_and_white_payment_starts_a_new_zero_entry(self):
        payment = {"G": 2, "W": 2}
        session, source, bodies, _, private, spells, reanimation = self.prepare_clay(
            795120, payment, opponent_mana={"B": 2, "C": 1}, removal=("Murder",), bring_back=True,
        )
        self.cast_paid(session, source, payment)
        self.wait_for_entry_targets(session)
        self.assertEqual(3, self.counters(session, source))
        old_source = session.state.cards[source.object_id].logical_object_id
        self.target(session, "A", ())
        self.assertIsNone(self.resolve(session))
        self.assertEqual((0, 0, 0), tuple(self.counters(session, card) for card in bodies))
        self.cast(session, "B", spells[0], [source.ref])
        self.assertIsNone(self.resolve(session))
        self.assertEqual("graveyard", session.state.cards[source.object_id].zone)
        self.cast(session, "A", reanimation, [source.ref])
        self.wait_for_entry_targets(session)
        info = self.trigger_info(session, source)
        self.assertNotEqual(old_source, info.source.logical_object_id)
        self.assertIsNone(info.mana_spent_counts)
        self.assertEqual(0, self.counters(session, source))
        session = self.replay(session, load=True)
        self.target(session, "A", (bodies[0].ref,))
        self.assertIsNone(self.resolve(session))
        self.assertEqual(0, self.counters(session, bodies[0]))
        self.private_replay(session, private)

    def test_pinned_compiler_and_stripped_runtime_minima_require_both_pair_owners(self):
        record = self.db.lookup("Clay Champion", fuzzy=False)
        options = {"capability_registry": self.registry, "capability_profile": "commander_review"}
        ir = compile_oracle_card(record, **options)
        self.assertEqual("exact", ir.status, ir.material_residuals)
        replacement, trigger = ir.faces[0].nodes
        self.assertIn(PAID_GREEN_PAIRS_ENTRY_CAPABILITY, replacement.capability_dependencies)
        self.assertIn(PAID_WHITE_ENTRY_TARGET_CAPABILITY, trigger.capability_dependencies)
        for node in (replacement, trigger):
            self.assertIn(MANA_SPENT_COUNTS_CAPABILITY, node.capability_dependencies)
        self.assertIs(trigger.target_schema["source_exclusion"], True)
        self.assertEqual("you", trigger.target_schema["controller_relation"])
        self.assertEqual(2, trigger.target_schema["up_to"])
        self.assertEqual("permanent.enter.self", trigger.event)
        for bad in (
            record.oracle_text.replace("{G}{G}", "{G}{G}{G}"),
            record.oracle_text.replace("two other target creatures you control", "two target creatures you control"),
            record.oracle_text.replace("{W}{W}", "{W}{W}{W}"),
            record.oracle_text.replace("on each of them.", "on each of them. Rewrite history."),
        ):
            with self.subTest(text=bad):
                self.assertNotEqual("exact", compile_oracle_card(replace(record, oracle_text=bad), **options).status)

        session = self.session(795130)
        source = self.add(session.engine, record.name, zone="hand")
        binding = self.binding(session, source)
        self.assertTrue(binding["strict_capability_ready"], binding.get("blockers"))
        programs = session.engine.semantics.programs_for_oracle(source.oracle_id)
        for capability in (PAID_GREEN_PAIRS_ENTRY_CAPABILITY, PAID_WHITE_ENTRY_TARGET_CAPABILITY):
            program, = (program for program in programs if capability in program.capability_dependencies)
            deps = tuple(dep for dep in program.capability_dependencies if dep != capability)
            stripped = replace(program, capability_dependencies=deps,
                               capability_closure=self.registry.closure(deps, profile="commander_review").to_dict())
            verdict = bind_semantic_program_runtime(stripped, capability_registry=self.registry, profile="commander_review")
            self.assertFalse(verdict["strict"])
            self.assertIn("capability:undeclared_runtime_dependency:" + capability, verdict["blockers"])
        green, = (program for program in programs if PAID_GREEN_PAIRS_ENTRY_CAPABILITY in program.capability_dependencies)
        white, = (program for program in programs if PAID_WHITE_ENTRY_TARGET_CAPABILITY in program.capability_dependencies)
        handler, = green.handlers
        unpaired = {**handler, "amount_spec": {**handler["amount_spec"], "calculation": "multiply"}}
        for changed in (
            replace(green, handlers=(unpaired,)),
            replace(white, provenance={**white.provenance, "requires_complete_card_program": False}),
            replace(white, provenance={**white.provenance, "granted_only": True}),
        ):
            with self.subTest(changed=changed.key):
                verdict = bind_semantic_program_runtime(
                    changed, capability_registry=self.registry, profile="commander_review"
                )
                self.assertFalse(verdict["strict"])
                self.assertTrue(any("runtime_paid_color_entry:invalid" in b for b in verdict["blockers"]))
        for schema in ({**white.target_schema, "source_exclusion": False},
                       {**white.target_schema, "controller_relation": "any"},
                       {**white.target_schema, "up_to": 1}):
            with self.subTest(schema=schema):
                verdict = bind_semantic_program_runtime(
                    replace(white, target_schema=schema), capability_registry=self.registry, profile="commander_review"
                )
                self.assertFalse(verdict["strict"])
                self.assertTrue(any("runtime_paid_color_entry:invalid" in blocker for blocker in verdict["blockers"]))

    def test_verified_registry_withdrawals_reject_complete_original_admission(self):
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index, discover_tests

        value = json.loads((ROOT / "quorune/rules/capability-registry.json").read_text(encoding="utf-8"))
        declarations = read_evidence_document(ROOT / "platform/capability-evidence-declarations.json")
        known = set(discover_tests(ROOT).values())
        original = self.db.lookup("Clay Champion", fuzzy=False)
        retained = self.db.lookup("Grizzly Bears", fuzzy=False)
        for capability in (PAID_GREEN_PAIRS_ENTRY_CAPABILITY, PAID_WHITE_ENTRY_TARGET_CAPABILITY):
            with self.subTest(capability=capability):
                raw = deepcopy(value)
                next(row for row in raw["capabilities"] if row["id"] == capability).update(
                    status="blocked", blockers=["pair effect authority withdrawn"],
                )
                registry = CapabilityRegistry(raw)
                index = build_index(registry_value=raw, declaration_source=declarations, discovered_test_ids=known)
                registry.mark_evidence_verified(validate_capability_evidence_index(index, registry=registry))
                options = {"capability_registry": registry, "capability_profile": "commander_review"}
                self.assertNotEqual("exact", compile_oracle_card(original, **options).status)
                self.assertEqual("exact", compile_oracle_card(retained, **options).status)

    def test_fresh_unpaired_green_and_unpaired_white_payment_mutants_are_killed(self):
        method = "test_actual_four_green_four_white_places_six_before_targeting_and_two_on_each"
        original = DynamicEntryCounterAmountSpec.amount

        def omit_green_pairing(spec, value):
            if spec.calculation is DynamicEntryCounterCalculation.PAID_GREEN_PAIRS:
                return spec.coefficient * value
            return original(spec, value)

        for mutation in (
            patch.object(DynamicEntryCounterAmountSpec, "amount", omit_green_pairing),
            patch("quorune.rules.counter_target_set_shapes.original_entry_white_pairs", return_value=4),
        ):
            with mutation, self.assertRaises(AssertionError):
                getattr(type(self)(method), method)()


if __name__ == "__main__":
    unittest.main()
