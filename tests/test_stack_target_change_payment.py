from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.card_programs.admission import program_has_declared_complete_source_admission
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.replacement.immutable import thaw_value
from quorune.rules.capabilities import load_default_capability_registry
from quorune.stack_target_change_model import (
    OptionalStackTargetChangeSpec, SingleStackTargetChangeSpec,
    stack_target_change_payment_effect,
)
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


PROFILE = "commander_review"
NAME = "Divert"
FIXTURES = ROOT / "tests" / "fixtures"
PAYMENT = "effect.choice.optional_fixed_mana_payment"
ANNOUNCEMENT = "trigger.event.normalized_target_announcement"


class StackTargetChangePaymentTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        folder = Path(cls.temporary.name)
        support = {
            "ability-label-composition-cards.json": {"Island"},
            "ability-source-counter-cards.json": {"Shock"},
            "activation-restriction-material-cards.json": {"Memnite"},
            "additional-conditional-modifier-cards.json": {"Revitalize"},
            "bound-effect-program-cards.json": {"Generic Bound Plains"},
            "mandatory-single-stack-target-change-cards.json": {"Remove Soul"},
            "source-attack-alone-cards.json": {"Isamaru, Hound of Konda"},
            "stack-target-change-payment-cards.json": {"Divert", "Mana Leak"},
            "targeted-spell-control-cards.json": {"Aethersnatch"},
        }
        pieces = []
        for filename, names in support.items():
            data = json.loads((FIXTURES / filename).read_bytes())
            data["cards"] = [row for row in data["cards"] if row["name"] in names]
            assert {row["name"] for row in data["cards"]} == names
            ids = {row["oracle_id"] for row in data["cards"]}
            data["rulings"] = [row for row in data.get("rulings", ()) if row["oracle_id"] in ids]
            piece = folder / filename
            piece.write_text(json.dumps(data), encoding="utf-8")
            pieces.append(piece)
        path = folder / "stack-target-change-payment.sqlite3"
        build_fixture_database(pieces, path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Controller pays before target change", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"),
            DeckEntry("Generic Bound Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def original_program(self):
        original = self.db.lookup(NAME, fuzzy=False)
        binding, registry = register_card(self.db, self.registry, NAME)
        self.assertTrue(binding["strict_capability_ready"], binding)
        result, = registry.programs_for_oracle(original.oracle_id)
        return original, result

    def test_complete_divert_binds_current_controller_and_exact_unpaid_result(self):
        original, program = self.original_program()
        ir = compile_oracle_card(original, capability_registry=self.registry, capability_profile=PROFILE)
        self.assertEqual("exact", ir.status, ir.material_residuals)
        self.assertTrue(program_has_declared_complete_source_admission(program))
        self.assertEqual([stack_target_change_payment_effect()], thaw_value(program.effects))
        self.assertEqual(SingleStackTargetChangeSpec(False).target_schema, thaw_value(program.target_schema))
        self.assertTrue({PAYMENT, ANNOUNCEMENT}.issubset(program.capability_dependencies))

    def test_malformed_payment_actors_version_amount_and_subjects_fail_closed(self):
        _, program = self.original_program()
        effect, = thaw_value(program.effects)
        schema = thaw_value(program.target_schema)
        for changes in (
            {"effects": [{**effect, "player": "$controller"}]},
            {"effects": [{**effect, "schema_version": 15.0}]},
            {"effects": [{**effect, "cost": {**effect["cost"], "GENERIC": 1}}]},
            {"effects": [{**effect, "declined_effects": list(OptionalStackTargetChangeSpec(False).effects)}]},
            {"effects": [{**effect, "stack": "$source"}]},
            {"target_schema": {**schema, "categories": ["ability"]}},
            {"target_schema": {**schema, "predicate": "damageable"}},
        ):
            with self.subTest(changes=changes):
                outcome = bind_semantic_program_runtime(replace(program, **changes),
                    capability_registry=self.registry, profile=PROFILE)
                self.assertFalse(outcome["strict"], outcome)

    @staticmethod
    def _fund(game, seat, mana):
        game.state.players[seat].mana_pool.update(mana)

    def _spell(self, game, card):
        result, = (item for item in game.state.stack if item.card_object_id == card.object_id)
        return result

    def _choose(self, game, seat, fields):
        result = game.act("pilot:" + seat, {"action_id": "choose", **fields})
        self.assertTrue(result.ok, result.summary)

    def _decision_for(self, game, seat):
        decision = self.resolve(game)
        self.assertIsNotNone(decision)
        self.assertEqual(("semantic.choice", [seat]), (decision.kind, decision.actors))
        return decision.payload_by_actor[seat]

    def _price(self, game, seat, *, payable):
        payload = self._decision_for(game, seat)
        self.assertEqual(2, payload["cost"]["GENERIC"])
        self.assertEqual(payable, payload["payable"])
        schema = payload["legal_actions"][0]["choice_schema"]
        self.assertEqual("pay", schema["field"])
        self.assertEqual({True, False} if payable else {False}, set(schema["legal_values"]))

    def _new_target(self, game, seat, ref):
        payload = self._decision_for(game, seat)
        schema = payload["legal_actions"][0]["choice_schema"]
        self.assertFalse(schema["optional"])
        self.assertIn(ref, schema["target_schema"]["legal_refs"])

    def test_actual_payment_keeps_the_initial_target_and_declining_changes_it(self):
        for pay in (True, False):
            with self.subTest(pay=pay):
                game = self.session(77118001 if pay else 77118002)
                shock = self.add(game.engine, "Shock", zone="hand")
                divert = self.add(game.engine, "Divert", seat="B", zone="hand")
                for card in (shock, divert):
                    self.assert_closed(game, card)
                self.prepare(game, mana={"R": 1, "C": 2})
                self._fund(game, "B", {"U": 1})
                self.checkpoint(game)
                action = self.action(game, "A", shock)
                paid = game.act("pilot:A", {"action_id": action["id"], "targets": ["C"],
                                            "pay": "manual", "payment": {"R": 1}})
                self.assertTrue(paid.ok, paid.summary)
                subject = self._spell(game, shock)
                initial = deepcopy(subject.context["target_snapshots"])
                self.cast(game, "B", divert, [subject.ref])
                self._price(game, "A", payable=True)
                original_state = authoritative_state_hash(game.state)
                rejected = game.act("pilot:B", {"action_id": "choose", "pay": True})
                self.assertFalse(rejected.ok, rejected.summary)
                self.assertEqual(original_state, authoritative_state_hash(game.state))
                game = self.replay(game, load=True)
                self._choose(game, "A", {"pay": pay})
                self.assertEqual(0 if pay else 2, game.state.players["A"].mana_pool["C"])
                if pay:
                    item = self._spell(game, shock)
                    self.assertEqual(["C"], item.targets)
                    self.assertEqual(initial, item.context["target_snapshots"])
                else:
                    self._new_target(game, "B", "D")
                    self._choose(game, "B", {"targets": ["D"]})
                self.assertIsNone(self.resolve(game))
                self.assertEqual((38, 40) if pay else (40, 38),
                                 tuple(game.state.players[seat].life for seat in "CD"))
                self.replay(game, load=True)

    def test_actual_control_transfer_changes_payer_without_changing_the_retarget_chooser(self):
        game = self.session(77118003)
        shock = self.add(game.engine, "Shock", zone="hand")
        divert = self.add(game.engine, "Divert", seat="B", zone="hand")
        takeover = self.add(game.engine, "Aethersnatch", seat="C", zone="hand")
        for card in (shock, divert, takeover):
            self.assert_closed(game, card)
        self.prepare(game, mana={"R": 1})
        self._fund(game, "B", {"U": 1})
        self._fund(game, "C", {"C": 6, "U": 2})
        self.checkpoint(game)
        self.cast(game, "A", shock, ["D"])
        subject = self._spell(game, shock)
        receipt = deepcopy(subject.to_dict()["mana_spent_counts"])
        self.cast(game, "B", divert, [subject.ref])
        self.cast(game, "C", takeover, [subject.ref])
        self._decision_for(game, "C")
        self._choose(game, "C", {})
        self.assertEqual("C", self._spell(game, shock).controller)

        self._price(game, "C", payable=True)
        self._choose(game, "C", {"pay": False})
        self._new_target(game, "B", "A")
        unchanged = authoritative_state_hash(game.state)
        invalid = game.act("pilot:C", {"action_id": "choose", "targets": ["A"]})
        self.assertFalse(invalid.ok, invalid.summary)
        self.assertEqual(unchanged, authoritative_state_hash(game.state))
        self._choose(game, "B", {"targets": ["A"]})
        item = self._spell(game, shock)
        self.assertEqual(("C", receipt), (item.controller, item.to_dict()["mana_spent_counts"]))
        self.assertIsNone(self.resolve(game))
        self.assertEqual((38, 40), (game.state.players["A"].life, game.state.players["D"].life))
        self.replay(game, load=True)

    def test_actual_unpayable_controller_can_only_decline(self):
        game = self.session(77118004)
        shock = self.add(game.engine, "Shock", zone="hand")
        divert = self.add(game.engine, "Divert", seat="B", zone="hand")
        for card in (shock, divert):
            self.assert_closed(game, card)
        self.prepare(game, mana={"R": 1})
        self._fund(game, "B", {"U": 1})
        self.checkpoint(game)
        self.cast(game, "A", shock, ["C"])
        self.cast(game, "B", divert, [self._spell(game, shock).ref])
        self._price(game, "A", payable=False)
        before = authoritative_state_hash(game.state)
        rejected = game.act("pilot:A", {"action_id": "choose", "pay": True})
        self.assertFalse(rejected.ok, rejected.summary)
        self.assertEqual(before, authoritative_state_hash(game.state))
        self._choose(game, "A", {"pay": False})
        self._new_target(game, "B", "D")
        self._choose(game, "B", {"targets": ["D"]})
        self.assertIsNone(self.resolve(game))
        self.assertEqual(38, game.state.players["D"].life)
        self.replay(game, load=True)

    def test_actual_decline_with_no_different_target_keeps_the_old_assignment(self):
        game = self.session(77118005)
        creature = self.add(game.engine, "Memnite", zone="hand")
        counter = self.add(game.engine, "Remove Soul", seat="B", zone="hand")
        divert = self.add(game.engine, "Divert", seat="C", zone="hand")
        for card in (creature, counter, divert):
            self.assert_closed(game, card)
        self.prepare(game)
        self._fund(game, "B", {"C": 3, "U": 1})
        self._fund(game, "C", {"U": 1})
        self.checkpoint(game)
        self.cast(game, "A", creature)
        subject = self._spell(game, creature)
        self.cast(game, "B", counter, [subject.ref])
        self.cast(game, "C", divert, [self._spell(game, counter).ref])
        self._price(game, "B", payable=True)
        self._choose(game, "B", {"pay": False})
        self.assertEqual([subject.ref], self._spell(game, counter).targets)
        self.assertEqual("priority", game.state.pending_decision.kind)
        self.assertIsNone(self.resolve(game))
        self.assertEqual("graveyard", game.state.cards[creature.object_id].zone)
        self.replay(game, load=True)

    def test_disabling_exact_payment_lowering_restores_only_divert_residual(self):
        with patch("quorune.compiler.resolution_effect_templates._stack_target_change_payment_template", return_value=None):
            removed, _ = register_card(self.db, self.registry, NAME)
            retained, _ = register_card(self.db, self.registry, "Mana Leak")
            self.assertFalse(removed["strict_capability_ready"], removed)
            self.assertTrue(retained["strict_capability_ready"], retained["blockers"])

    def test_verified_withdrawals_require_payment_and_mandatory_target_owners(self):
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index, discover_tests

        raw = json.loads((ROOT / "quorune/rules/capability-registry.json").read_bytes())
        declarations = read_evidence_document(ROOT / "platform/capability-evidence-declarations.json")
        known = set(discover_tests(ROOT).values())
        for capability in (PAYMENT, ANNOUNCEMENT):
            with self.subTest(capability=capability):
                document = deepcopy(raw)
                next(row for row in document["capabilities"] if row["id"] == capability).update(
                    status="blocked", blockers=["Fixed target-change choice authority withdrawn"])
                registry = CapabilityRegistry(document)
                index = build_index(registry_value=document, declaration_source=declarations, discovered_test_ids=known)
                registry.mark_evidence_verified(validate_capability_evidence_index(index, registry=registry))
                closure = registry.closure((capability,), profile=PROFILE)
                self.assertIn("status:" + capability + ":blocked", closure.blockers)
                self.assertFalse(any("unverified" in reason for reason in closure.blockers))
                removed, _ = register_card(self.db, registry, NAME)
                retained, _ = register_card(self.db, registry, "Revitalize")
                self.assertFalse(removed["strict_capability_ready"], removed)
                self.assertTrue(retained["strict_capability_ready"], retained["blockers"])
