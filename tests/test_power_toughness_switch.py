from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from common import ROOT
import test_fixed_reveal_additional_costs as reveal
import test_ring_native_actions as turns
import test_self_public_zone_casting as actions
import test_splice as splice
from test_granted_skulk import record

from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.rules.capabilities import CapabilityRegistry, load_default_capability_registry
from quorune.rules.power_toughness_switch import SWITCH_CAPABILITY, SWITCH_SCHEMA_VERSION
from quorune.target_cardinality import ANY_NUMBER_TARGET_CAPABILITY
from scripts.build_test_database import build_fixture_database


TARGET = "Switch target creature's power and toughness until end of turn."
ANY_NUMBER = "Switch the power and toughness of each of any number of target creatures until end of turn."


class PowerToughnessSwitchCompilerTests(unittest.TestCase):
    def test_source_target_and_locked_set_switches_compile_with_closed_scopes(self):
        registry = load_default_capability_registry()
        for text, type_line, receiver in (
            (TARGET, "Instant", "card"),
            ("{U}: Switch this creature's power and toughness until end of turn.", "Creature — Human", "card"),
            ("Switch each creature's power and toughness until end of turn.", "Instant", "predicate"),
            (ANY_NUMBER, "Instant", "cards"),
        ):
            with self.subTest(text=text):
                ir = compile_oracle_card(record(text, type_line), capability_registry=registry)
                self.assertEqual("exact", ir.status, ir.material_residuals)
                node = next(node for node in ir.faces[0].nodes if node.effects)
                effect, = node.effects
                self.assertEqual(text[node.span.start:node.span.end], node.text)
                self.assertEqual(SWITCH_SCHEMA_VERSION, effect["schema_version"])
                self.assertIn(receiver, effect)
                self.assertIn(SWITCH_CAPABILITY, node.capability_dependencies)
                if receiver == "cards":
                    self.assertIn(ANY_NUMBER_TARGET_CAPABILITY, node.capability_dependencies)
        for text in ("Switch its power and toughness until end of turn.",
            "Switch target creature's power and toughness.", "Switch each player's power and toughness until end of turn."):
            with self.subTest(unsupported=text):
                self.assertNotEqual("exact", compile_oracle_card(record(text, "Instant"),
                    capability_registry=registry).status)

    def test_withdrawing_switch_authority_leaves_switch_residual(self):
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index, discover_tests

        raw = json.loads((ROOT / "quorune/rules/capability-registry.json").read_bytes())
        row = next(row for row in raw["capabilities"]
            if row["id"] == "continuous.resolution.fixed_characteristics_until_end_of_turn")
        row.update(status="blocked", blockers=["withdrawn for the dependency witness"])
        registry = CapabilityRegistry(raw)
        declarations = read_evidence_document(ROOT / "platform/capability-evidence-declarations.json")
        index = build_index(registry_value=raw, declaration_source=declarations,
            discovered_test_ids=set(discover_tests(ROOT).values()))
        registry.mark_evidence_verified(validate_capability_evidence_index(index, registry=registry))
        self.assertNotEqual("exact", compile_oracle_card(record(TARGET, "Instant"), capability_registry=registry).status)
        self.assertEqual("exact", compile_oracle_card(record("Flying", "Creature"), capability_registry=registry).status)

    def test_switch_producer_omission_is_killed(self):
        registry = load_default_capability_registry()
        card = record(TARGET, "Instant")
        self.assertEqual("exact", compile_oracle_card(card, capability_registry=registry).status)
        with patch("quorune.compiler.power_toughness_switch.power_toughness_switch_effect_template", return_value=None):
            self.assertNotEqual("exact", compile_oracle_card(card, capability_registry=registry).status)


class PowerToughnessSwitchRuntimeTests(unittest.TestCase):
    register_fixture_cards_on_add = True
    session = reveal.FixedRevealCostRuntimeTests.session
    add = reveal.FixedRevealCostRuntimeTests.add
    fund = reveal.FixedRevealCostRuntimeTests.fund
    checkpoint = reveal.FixedRevealCostRuntimeTests.checkpoint
    resolve = reveal.FixedRevealCostRuntimeTests.resolve
    replay = reveal.FixedRevealCostRuntimeTests.replay
    cast = actions.SelfPublicZoneRuntimeTests.cast
    until = turns.RingNativeActionTests.until
    start_splice = splice.SpliceRuntimeTests.start_splice
    splice_actions = splice.SpliceRuntimeTests.splice_actions
    add_splice = splice.SpliceRuntimeTests.add_splice

    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        path = Path(cls.temporary.name) / "switch.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures/bound-effect-program-cards.json",
            ROOT / "tests/fixtures/additional-conditional-modifier-cards.json",
            ROOT / "tests/fixtures/all-blocker-requirement-cards.json",
            ROOT / "tests/fixtures/attack-declaration-power-cards.json",
            ROOT / "tests/fixtures/fixed-entwine-cards.json",
            ROOT / "tests/fixtures/fixed-stat-multiplier-cards.json",
            ROOT / "tests/fixtures/power-toughness-switch-cards.json"], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Switch witness", [DeckEntry("Generic Bound Commander", 1, "commander"),
            DeckEntry("Generic Bound Plains", 30)], ["Generic Bound Commander"])

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temporary.cleanup()

    @staticmethod
    def stats(session, card):
        return tuple(session.engine._numeric_stat(card.object_id, value) for value in ("power", "toughness"))

    assert_closed = actions.SelfPublicZoneRuntimeTests.assert_closed

    def test_stat_multiplier_uses_signed_effective_power_and_keeps_the_fixed_delta(self):
        session = self.session(7011001)
        target = self.add(session.engine, "Char-Rumbler", seat="B")
        fury = self.add(session.engine, "Unleash Fury", zone="hand")
        pump = self.add(session.engine, "Infuriate", zone="hand")
        private = self.add(session.engine, "Generic Bound Growth", seat="D", zone="hand", ref="stat-multiplier-private")
        self.assert_closed(session, fury)
        self.fund(session, {"C": 1, "R": 3})
        self.assertEqual((-1, 3), self.stats(session, target))
        self.cast(session, "A", fury, [target.ref])
        session = self.replay(session, load=True)
        self.assertIsNone(self.resolve(session))
        self.assertEqual((-2, 3), self.stats(session, target))
        self.cast(session, "A", pump, [target.ref])
        self.assertIsNone(self.resolve(session))
        self.assertEqual((1, 5), self.stats(session, target))
        self.assertNotIn(private.ref, json.dumps(session.packet("pilot:A", full=True)))
        turn = session.state.turn_sequence
        self.until(session, lambda game: game.state.turn_sequence > turn
            or game.state.pending_decision.kind == "cleanup.discard")
        if session.state.turn_sequence == turn:
            context = session.packet("pilot:A", full=True)["decision"]["ctx"]
            cards = [row["id"] for row in context["hand"][:context["count"]]]
            result = session.act("pilot:A", {"action_id": "discard", "cards": cards})
            self.assertTrue(result.ok, result.summary)
        self.assertGreater(session.state.turn_sequence, turn)
        self.assertEqual((-1, 3), self.stats(session, target))
        self.replay(session, load=True)

    def test_stat_multiplier_reads_switched_power_and_modifies_the_seventh_c_sublayer(self):
        session = self.session(7011002)
        target = self.add(session.engine, "Generic Bound Body", seat="B")
        fury = self.add(session.engine, "Unleash Fury", zone="hand")
        image = self.add(session.engine, "Twisted Image", zone="hand")
        self.assert_closed(session, fury)
        self.fund(session, {"C": 1, "R": 1, "U": 2})
        self.cast(session, "A", fury, [target.ref])
        self.cast(session, "A", image, [target.ref])
        self.assertIsNone(self.resolve(session))
        self.assertEqual((6, 8), self.stats(session, target))
        self.replay(session, load=True)

    def test_stat_multiplier_locks_the_current_set_and_reads_each_member_independently(self):
        session = self.session(7011003)
        positive = self.add(session.engine, "Generic Bound Body")
        negative = self.add(session.engine, "Char-Rumbler")
        opponent = self.add(session.engine, "Generic Bound Body", seat="B", ref="opponent-stats")
        late = self.add(session.engine, "Goblin Piker", zone="hand")
        spell = self.add(session.engine, "Double Trouble", zone="hand")
        self.assert_closed(session, spell)
        self.fund(session, {"C": 5, "R": 2})
        self.cast(session, "A", spell)
        self.assertIsNone(self.resolve(session))
        self.assertEqual((4, 6), self.stats(session, positive))
        self.assertEqual((-2, 3), self.stats(session, negative))
        self.assertEqual((2, 6), self.stats(session, opponent))
        self.cast(session, "A", late)
        self.assertIsNone(self.resolve(session))
        self.assertEqual((2, 1), self.stats(session, late))
        self.replay(session, load=True)

    def test_stat_multiplier_activates_on_the_original_targ_nar_incarnation(self):
        session = self.session(7011004)
        source = self.add(session.engine, "Targ Nar, Demon-Fang Gnoll")
        blink = self.add(session.engine, "Cloudshift", zone="hand")
        self.assert_closed(session, source)
        self.fund(session, {"C": 4, "R": 2, "G": 2, "W": 1})
        def double_source():
            actions_now = session.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
            offer = next(row for row in actions_now if row["id"].startswith(f"activate:{source.ref}:"))
            result = session.act("pilot:A", {"action_id": offer["id"], "pay": "manual",
                "payment": {"C": 2, "R": 1, "G": 1}})
            self.assertTrue(result.ok, result.summary)
        double_source()
        self.assertIsNone(self.resolve(session))
        self.assertEqual((4, 4), self.stats(session, source))
        original_identity = source.logical_object_id
        double_source()
        self.cast(session, "A", blink, [source.ref])
        self.assertIsNone(self.resolve(session))
        self.assertNotEqual(original_identity, session.state.cards[source.object_id].logical_object_id)
        self.assertEqual((2, 2), self.stats(session, source))
        self.replay(session, load=True)

    def test_stat_multiplier_requires_exact_capability_and_kills_the_unsigned_mutation(self):
        from quorune.card_programs.binding import bind_semantic_program_runtime
        from quorune.rules.fixed_stat_multipliers import (
            STAT_MULTIPLIER_CAPABILITY, FixedStatMultiplier, MultipliedStat,
        )

        self.assertEqual((-6, 4), FixedStatMultiplier(3, MultipliedStat.BOTH).delta(-3, 2))
        with self.assertRaises(ValueError):
            FixedStatMultiplier(True, MultipliedStat.POWER)
        session = self.session(7011005)
        source = self.add(session.engine, "Unleash Fury", zone="hand")
        self.assert_closed(session, source)
        program = next(p for p in session.engine.semantics.programs_for_oracle(source.oracle_id)
            if STAT_MULTIPLIER_CAPABILITY in p.capability_dependencies)
        deps = tuple(dep for dep in program.capability_dependencies if dep != STAT_MULTIPLIER_CAPABILITY)
        invalid = replace(program, capability_dependencies=deps,
            capability_closure=self.registry.closure(deps, profile="commander_review").to_dict())
        result = bind_semantic_program_runtime(invalid, capability_registry=self.registry, profile="commander_review")
        self.assertIn("capability:undeclared_runtime_dependency:" + STAT_MULTIPLIER_CAPABILITY, result["blockers"])
        original = self.db.lookup("Unleash Fury")
        with patch("quorune.compiler.fixed_stat_multipliers.fixed_stat_multiplier_effect_template", return_value=None):
            self.assertTrue(compile_oracle_card(original, capability_registry=self.registry).material_residuals)

    def test_real_switch_draw_and_spliced_switch_follow_layer_7d_then_expire(self):
        session = self.session(6130401)
        engine = session.engine
        target = self.add(engine, "Generic Bound Body", seat="B")
        target.counters["+1/+1"] = 1
        image = self.add(engine, "Twisted Image", zone="hand")
        pump = self.add(engine, "Infuriate", zone="hand")
        main = self.add(engine, "Reach Through Mists", zone="hand")
        donor = self.add(engine, "Strange Inversion", zone="hand")
        self.fund(session, {"U": 2, "R": 2, "C": 1})
        hand = len(session.state.players["A"].zones["hand"])
        self.assertEqual((3, 7), self.stats(session, target))
        self.cast(session, "A", image, [target.ref])
        self.assertIsNone(self.resolve(session))
        self.assertEqual((7, 3), self.stats(session, target))
        self.assertEqual(hand, len(session.state.players["A"].zones["hand"]))
        self.cast(session, "A", pump, [target.ref])
        self.assertIsNone(self.resolve(session))
        self.assertEqual((9, 6), self.stats(session, target))
        self.start_splice(session, main)
        self.add_splice(session, donor)
        spectator = session.packet("pilot:D", full=True)
        self.assertIsNone(spectator["decision"])
        self.assertNotIn(donor.ref, json.dumps(spectator))
        result = session.act("pilot:A", {"action_id": "cast", "targets": [target.ref], "pay": "auto"})
        self.assertTrue(result.ok, result.summary)
        self.assertIsNone(self.resolve(session))
        self.assertEqual((6, 9), self.stats(session, target))
        self.assertEqual("hand", session.state.cards[donor.object_id].zone)
        self.until(session, lambda game: game.state.pending_decision.kind == "cleanup.discard")
        cleanup = session.packet("pilot:A", full=True)["decision"]["ctx"]
        discarded = [row["id"] for row in cleanup["hand"][:cleanup["count"]]]
        result = session.act("pilot:A", {"a": "discard", "cards": discarded})
        self.assertTrue(result.ok, result.summary)
        self.assertEqual("B", session.state.active_player)
        self.assertEqual((3, 7), self.stats(session, target))
        self.replay(session, load=True)

    def test_global_switch_locks_current_creatures_and_triggers_zero_toughness_sba(self):
        session = self.session(6130402)
        source = self.add(session.engine, "Mannichi, the Fevered Dream")
        protected = self.add(session.engine, "Wall of Denial", seat="B")
        target = self.add(session.engine, "Generic Bound Body", seat="B")
        late = self.add(session.engine, "Ornithopter", zone="hand")
        self.fund(session, {"C": 1, "R": 1})
        actions_now = session.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        offer = next(row for row in actions_now if row["id"].startswith(f"activate:{source.ref}:"))
        result = session.act("pilot:A", {"action_id": offer["id"], "pay": "auto"})
        self.assertTrue(result.ok, result.summary)
        self.assertIsNone(self.resolve(session))
        self.assertEqual("graveyard", session.state.cards[protected.object_id].zone)
        self.assertEqual((6, 2), self.stats(session, target))
        self.cast(session, "A", late)
        self.assertIsNone(self.resolve(session))
        self.assertEqual("battlefield", session.state.cards[late.object_id].zone)
        self.assertEqual((0, 2), self.stats(session, late))
        self.replay(session, load=True)

    def test_any_number_combat_trigger_switches_only_surviving_targets(self):
        session = self.session(6130403)
        self.add(session.engine, "Inversion Behemoth")
        leaving = self.add(session.engine, "Generic Bound Body", seat="B", ref="departing-target")
        retained = self.add(session.engine, "Generic Bound Body", seat="B", ref="remaining-target")
        bounce = self.add(session.engine, "Unsummon", seat="B", zone="hand")
        self.add(session.engine, "Island", seat="B")
        self.fund(session, {})
        self.until(session, lambda game: game.state.pending_decision.kind == "semantic.target")
        result = session.act("pilot:A", {"action_id": "choose", "targets": [leaving.ref, retained.ref]})
        self.assertTrue(result.ok, result.summary)
        self.cast(session, "B", bounce, [leaving.ref])
        self.assertIsNone(self.resolve(session))
        self.assertEqual("hand", session.state.cards[leaving.object_id].zone)
        self.assertEqual((6, 2), self.stats(session, retained))
        self.replay(session, load=True)

    def test_entwine_combines_the_power_modifier_and_switch_on_the_same_target(self):
        session = self.session(6130405)
        target = self.add(session.engine, "Generic Bound Body", seat="B")
        target.counters["+1/+1"] = 5
        spell = self.add(session.engine, "Twisted Reflection", zone="hand")
        self.fund(session, {"C": 1, "U": 1, "B": 1})
        result = session.act("pilot:A", {"action_id": f"cast:{spell.ref}", "pay": "auto",
            "modes": ["mode_1", "mode_2"], "targets": [target.ref, target.ref]})
        self.assertTrue(result.ok, result.summary)
        self.assertIsNone(self.resolve(session))
        self.assertEqual((11, 1), self.stats(session, target))
        self.replay(session, load=True)

    def test_switch_and_draw_spell_fizzles_when_its_sole_target_leaves(self):
        session = self.session(6130404)
        target = self.add(session.engine, "Generic Bound Body", seat="B")
        image = self.add(session.engine, "Twisted Image", zone="hand")
        bounce = self.add(session.engine, "Unsummon", seat="B", zone="hand")
        session.state.players["B"].mana_pool["U"] = 1
        self.fund(session, {"U": 1})
        self.cast(session, "A", image, [target.ref])
        hand = len(session.state.players["A"].zones["hand"])
        self.cast(session, "B", bounce, [target.ref])
        self.assertIsNone(self.resolve(session))
        self.assertEqual(hand, len(session.state.players["A"].zones["hand"]))
        self.assertEqual("hand", session.state.cards[target.object_id].zone)
        self.replay(session, load=True)
