from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from common import ROOT
import test_attacking_card_entry as attacking
from test_granted_skulk import record
import test_self_public_zone_casting as actions

from quorune.activation_mana_cost import fixed_complex_activation_mana_options
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.fixed_effect_payment import FixedEffectPaymentSpec, compound_payment_payload_dependencies
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import CapabilityRegistry, load_default_capability_registry
from quorune.rules.hybrid_resolution_payment import HYBRID_RESOLUTION_PAYMENT_CAPABILITY
from scripts.build_test_database import build_fixture_database


TEXT = "Whenever this creature attacks, you may pay {W/B}{W/B}. If you do, draw a card."


def prices(spec):
    return {tuple(sorted(option.compact()["m"].items())) for option in spec.hybrid_mana_options}


class HybridResolutionPaymentCompilerTests(unittest.TestCase):
    def test_hybrid_payment_compiles_to_closed_mixed_and_two_brid_alternatives(self):
        registry = load_default_capability_registry()
        card = record(TEXT, "Creature — Human")
        ir = compile_oracle_card(card, capability_registry=registry)
        self.assertEqual("exact", ir.status, ir.material_residuals)
        node = next(node for node in ir.faces[0].nodes if node.effects)
        self.assertEqual(TEXT, node.text)
        effect, = node.effects
        spec = FixedEffectPaymentSpec.from_dict(effect["payment"])
        self.assertEqual({(("W", 2),), (("B", 1), ("W", 1)), (("B", 2),)}, prices(spec))
        self.assertIn(HYBRID_RESOLUTION_PAYMENT_CAPABILITY, node.capability_dependencies)
        self.assertIn(HYBRID_RESOLUTION_PAYMENT_CAPABILITY, compound_payment_payload_dependencies(effect))
        two_brid = compile_oracle_card(record(TEXT.replace("{W/B}{W/B}", "{2/W}"), "Creature — Human"),
            capability_registry=registry)
        self.assertEqual("exact", two_brid.status, two_brid.material_residuals)
        effect, = next(node.effects for node in two_brid.faces[0].nodes if node.effects)
        self.assertEqual({(("W", 1),), (("GENERIC", 2),)}, prices(FixedEffectPaymentSpec.from_dict(effect["payment"])))
        for cost in ("{W/P}", "{X}{W/B}", "{W/W}", "{S}{W/B}"):
            with self.subTest(cost=cost):
                unsupported = record(TEXT.replace("{W/B}{W/B}", cost), "Creature — Human")
                self.assertNotEqual("exact", compile_oracle_card(unsupported, capability_registry=registry).status)
        for change in ({"schema_version": True}, {"hybrid_symbols": ["W/P"]}, {"hybrid_symbols": []}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                FixedEffectPaymentSpec.from_dict({**spec.to_dict(), **change})

    def test_withdrawing_optional_payment_authority_leaves_hybrid_cost_residual(self):
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index, discover_tests

        raw = json.loads((ROOT / "quorune/rules/capability-registry.json").read_bytes())
        row = next(row for row in raw["capabilities"] if row["id"] == "effect.choice.optional_fixed_mana_payment")
        row.update(status="blocked", blockers=["withdrawn for the dependency witness"])
        registry = CapabilityRegistry(raw)
        declarations = read_evidence_document(ROOT / "platform/capability-evidence-declarations.json")
        evidence = build_index(registry_value=raw, declaration_source=declarations,
            discovered_test_ids=set(discover_tests(ROOT).values()))
        registry.mark_evidence_verified(validate_capability_evidence_index(evidence, registry=registry))
        self.assertNotEqual("exact", compile_oracle_card(record(TEXT, "Creature — Human"), capability_registry=registry).status)
        plain = "Whenever this creature attacks, draw a card."
        self.assertEqual("exact", compile_oracle_card(record(plain, "Creature — Human"), capability_registry=registry).status)

    def test_omitting_the_mixed_payment_alternative_is_killed(self):
        registry = load_default_capability_registry()
        card = record(TEXT, "Creature — Human")
        ir = compile_oracle_card(card, capability_registry=registry)
        effect, = next(node.effects for node in ir.faces[0].nodes if node.effects)

        def witness():
            actual = prices(FixedEffectPaymentSpec.from_dict(effect["payment"]))
            self.assertIn((("B", 1), ("W", 1)), actual)

        witness()

        def no_mixed(requirements, symbols):
            return tuple(option for option in fixed_complex_activation_mana_options(requirements, symbols)
                if not (option.requirements["W"] and option.requirements["B"]))

        with patch("quorune.rules.hybrid_resolution_payment.fixed_complex_activation_mana_options", side_effect=no_mixed):
            with self.assertRaises(AssertionError):
                witness()


class HybridResolutionPaymentNativeTests(unittest.TestCase):
    register_fixture_cards_on_add = True
    session = attacking.AttackingCardEntryNativeTests.session
    add = attacking.AttackingCardEntryNativeTests.add
    checkpoint = attacking.AttackingCardEntryNativeTests.checkpoint
    resolve = attacking.AttackingCardEntryNativeTests.resolve
    replay = attacking.AttackingCardEntryNativeTests.replay
    prepare = attacking.AttackingCardEntryNativeTests.prepare
    assert_closed = attacking.AttackingCardEntryNativeTests.assert_closed
    native_until = attacking.AttackingCardEntryNativeTests.native_until
    until = attacking.AttackingCardEntryNativeTests.until
    attack_player = attacking.AttackingCardEntryNativeTests.attack_player
    cast = actions.SelfPublicZoneRuntimeTests.cast

    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        path = Path(cls.temporary.name) / "hybrid-resolution-payment.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures/bound-effect-program-cards.json",
            ROOT / "tests/fixtures/hybrid-resolution-payment-cards.json"], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Hybrid payment witness", [DeckEntry("Generic Bound Commander", 1, "commander"),
            DeckEntry("Generic Bound Plains", 30)], ["Generic Bound Commander"])

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temporary.cleanup()

    def payable_option(self, session, expected):
        packet = session.packet("pilot:A", full=True)["decision"]
        options = packet["ctx"]["options"]
        self.assertIn("decline", {option["id"] for option in options})
        paid, = [option for option in options if option["id"] != "decline"]
        self.assertEqual(expected, paid["cost"])
        return paid["id"]

    def choose(self, session, option):
        result = session.act("pilot:A", {"action_id": "choose", "choice": option})
        self.assertTrue(result.ok, result.summary)

    def test_alesha_pays_the_only_mixed_option_then_chooses_a_fresh_defender(self):
        session = self.session(107401)
        engine = session.engine
        alesha = self.add(engine, "Alesha, Who Smiles at Death")
        alesha.acquired_control_turn_count = -1
        target = self.add(engine, "Generic Bound Body", zone="graveyard")
        plains = self.add(engine, "Plains")
        swamp = self.add(engine, "Swamp")
        self.assert_closed(session, alesha)
        self.prepare(session)
        self.checkpoint(session)
        self.attack_player(session, alesha, "B")
        self.assertEqual("semantic.target", session.state.pending_decision.kind)
        result = session.act("pilot:A", {"action_id": "choose", "targets": [target.ref]})
        self.assertTrue(result.ok, result.summary)
        self.assertEqual("semantic.choice", self.resolve(session).kind)
        mixed = self.payable_option(session, {"W": 1, "B": 1})
        self.assertIsNone(session.packet("pilot:D", full=True)["decision"])
        before = authoritative_state_hash(session.state)
        self.assertFalse(session.act("pilot:D", {"action_id": "choose", "choice": mixed}).ok)
        self.assertEqual(before, authoritative_state_hash(session.state))
        unavailable = "complex-1" if mixed != "complex-1" else "complex-2"
        self.assertFalse(session.act("pilot:A", {"action_id": "choose", "choice": unavailable}).ok)
        self.assertEqual(before, authoritative_state_hash(session.state))
        session = self.replay(session, load=True)
        self.choose(session, mixed)
        self.assertTrue(session.state.cards[plains.object_id].tapped)
        self.assertTrue(session.state.cards[swamp.object_id].tapped)
        self.assertEqual("graveyard", session.state.cards[target.object_id].zone)
        self.choose(session, "C")
        self.assertEqual("C", session.state.combat.attackers[target.object_id])
        self.assertTrue(session.state.cards[target.object_id].tapped)
        self.replay(session, load=True)

    def test_kels_declines_then_draws_from_its_own_sacrifice_for_one_blue(self):
        session = self.session(107402)
        engine = session.engine
        kels = self.add(engine, "Kels, Fight Fixer")
        body = self.add(engine, "Generic Bound Body")
        self.assert_closed(session, kels)
        self.prepare(session, mana={"U": 3})
        self.checkpoint(session)
        hand_count = len(session.state.players["A"].zones["hand"])
        for sacrificed, pay, expected_pool in ((body, False, 2), (kels, True, 0)):
            actions_now = session.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
            action = next(row for row in actions_now if row["id"].startswith(f"activate:{kels.ref}:"))
            result = session.act("pilot:A", {"action_id": action["id"], "cost_cards": [sacrificed.ref], "pay": "auto"})
            self.assertTrue(result.ok, result.summary)
            self.assertEqual("graveyard", session.state.cards[sacrificed.object_id].zone)
            self.assertEqual("semantic.choice", self.resolve(session).kind)
            blue = self.payable_option(session, {"U": 1})
            self.choose(session, blue if pay else "decline")
            self.assertEqual(hand_count + int(pay), len(session.state.players["A"].zones["hand"]))
            self.assertEqual(expected_pool, session.state.players["A"].mana_pool["U"])
            self.assertIsNone(self.resolve(session))
        self.replay(session, load=True)

    def test_shu_yun_pays_for_a_legal_target_and_skips_payment_for_an_illegal_one(self):
        for invalidate in (False, True):
            with self.subTest(invalidate=invalidate):
                session = self.session(107403 + invalidate)
                engine = session.engine
                source = self.add(engine, "Shu Yun, the Silent Tempest")
                spell = self.add(engine, "Reach Through Mists", zone="hand")
                target = self.add(engine, "Generic Bound Body", seat="B")
                bounce = self.add(engine, "Unsummon", seat="B", zone="hand")
                self.add(engine, "Island", seat="B")
                self.assert_closed(session, source)
                self.prepare(session, mana={"U": 1, "R": 1, "W": 1})
                self.checkpoint(session)
                self.cast(session, "A", spell)
                self.until(session, lambda game: game.state.pending_decision.kind == "semantic.target")
                result = session.act("pilot:A", {"action_id": "choose", "targets": [target.ref]})
                self.assertTrue(result.ok, result.summary)
                if invalidate:
                    self.cast(session, "B", bounce, [target.ref])
                    self.assertIsNone(self.resolve(session))
                    self.assertEqual("hand", session.state.cards[target.object_id].zone)
                    self.assertEqual((1, 1), tuple(session.state.players["A"].mana_pool[key] for key in ("R", "W")))
                else:
                    self.assertEqual("semantic.choice", self.resolve(session).kind)
                    self.choose(session, self.payable_option(session, {"R": 1, "W": 1}))
                    self.assertIsNone(self.resolve(session))
                    self.assertIn("double strike", session.engine._combat_keywords(session.state.cards[target.object_id]))
                    self.until(session, lambda game: game.state.active_player == "B" and game.state.step == "upkeep")
                    self.assertNotIn("double strike", session.engine._combat_keywords(session.state.cards[target.object_id]))
                self.replay(session, load=True)
