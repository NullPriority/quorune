"""Native evidence for intrinsic prevention and its scaled life result."""

from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.ability_fragments import CURRENT_ABILITY_FRAGMENT_COVERAGE
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


ORIGINAL = "Purity"


class IntrinsicNoncombatPreventionLifeGainTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "intrinsic-noncombat-prevention-life-gain.sqlite3"
        fixture = ROOT / "tests/fixtures/intrinsic-noncombat-prevention-life-gain-cards.json"
        build_fixture_database([fixture], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Intrinsic prevention and life", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"), DeckEntry("Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def test_complete_original_keeps_flying_graveyard_shuffle_and_intrinsic_prevention(self):
        card = self.db.lookup(ORIGINAL, fuzzy=False)
        report, semantics = register_card(self.db, self.registry, "Purity")
        self.assertTrue(report["strict_capability_ready"], report["blockers"])
        programs = semantics.programs_for_oracle(card.oracle_id)
        self.assertIn("object.enter_graveyard", {program.event for program in programs})
        prevention, = [program for program in programs if program.event == "damage"]
        self.assertIsNone(prevention.target_schema)
        self.assertEqual("battlefield", prevention.active_zone)
        self.assertTrue(prevention.handlers)
        for text in (
            card.oracle_text.replace("noncombat damage", "combat damage"),
            card.oracle_text.replace("dealt to you", "dealt to a creature you control"),
            card.oracle_text.replace("You gain life equal to the damage prevented this way.", "You gain 3 life."),
            card.oracle_text.replace("this way.", "this way. Draw a card."),
        ):
            ir = compile_oracle_card(replace(card, oracle_text=text), capability_registry=self.registry)
            self.assertNotEqual("exact", ir.status, text)

    def test_actual_borrowed_purity_protects_its_controller_from_noncombat_damage_only(self):
        game = self.session(61550510)
        source = self.add(game.engine, ORIGINAL)
        game.engine.change_control(source.object_id, "B", reason="Prepare a borrowed prevention source")
        first = self.add(game.engine, "Shock", seat="C", zone="hand", ref="purity-owner-shock")
        second = self.add(game.engine, "Shock", seat="C", zone="hand", ref="purity-controller-shock")
        attacker = self.add(game.engine, "Grizzly Bears", seat="C")
        attacker.acquired_control_turn_count = -1
        for card in (source, first, second, attacker):
            self.assert_closed(game, card)
        self.prepare(game, actor="C", active="C", mana={"R": 2})
        self.checkpoint(game)
        a_life = game.state.players["A"].life
        b_life = game.state.players["B"].life
        self.assertIn("flying", game.engine._combat_keywords(source))
        self.cast_for(game, "C", first, ["A"])
        self.resolve_top(game)
        self.assertEqual(a_life - 2, game.state.players["A"].life)
        self.cast_for(game, "C", second, ["B"])
        self.resolve_top(game)
        self.assertEqual(b_life + 2, game.state.players["B"].life)
        self.assertFalse(game.state.damage_prevention_shields)
        self.until(game, lambda current: current.state.pending_decision.kind == "combat.attackers")
        attack = game.act("pilot:C", {"action_id": "attack", "attackers": {attacker.ref: "B"}})
        self.assertTrue(attack.ok, attack.summary)
        self.until(game, lambda current: current.state.players["B"].life == b_life)
        self.assertEqual(a_life - 2, game.state.players["A"].life)
        self.replay(game, load=True)

    def test_actual_affected_player_can_apply_a_finite_shield_before_the_intrinsic_life_result(self):
        game = self.session(61550520)
        source = self.add(game.engine, ORIGINAL)
        healer = self.add(game.engine, "Samite Healer")
        healer.acquired_control_turn_count = -1
        shock = self.add(game.engine, "Shock", seat="B", zone="hand")
        for card in (source, healer, shock):
            self.assert_closed(game, card)
        self.prepare(game)
        game.state.players["B"].mana_pool["R"] = 1
        self.checkpoint(game)
        original = game.state.players["A"].life
        rows = game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        action, = [row["id"] for row in rows if row["id"].startswith(f"activate:{healer.ref}:")]
        activated = game.act("pilot:A", {"action_id": action, "targets": ["A"], "pay": "auto"})
        self.assertTrue(activated.ok, activated.summary)
        self.resolve_top(game)
        shield, = game.state.damage_prevention_shields
        self.cast_for(game, "B", shock, ["A"])
        pending = self.resolve(game)
        self.assertEqual(("replacement.order", ["A"]), (pending.kind, pending.actors))
        options = game.packet("pilot:A", full=True)["decision"]["ctx"]["options"]
        self.assertEqual(2, len(options))
        by_shield, = [option["id"] for option in options if option["id"] == shield.effect_id]
        by_source, = [option["id"] for option in options if option["id"] != shield.effect_id]
        for chosen, expected in ((by_shield, 1), (by_source, 2)):
            with self.subTest(first=chosen):
                branch = self.replay(game, load=True)
                command = {"action_id": "choose", "replacement": chosen}
                before = authoritative_state_hash(branch.state)
                self.assertFalse(branch.act("pilot:B", command).ok)
                self.assertEqual(before, authoritative_state_hash(branch.state))
                applied = branch.act("pilot:A", command)
                self.assertTrue(applied.ok, applied.summary)
                self.assertIsNone(self.resolve(branch))
                self.assertEqual(original + expected, branch.state.players["A"].life)
                self.assertFalse(branch.engine._current_turn_history("player_damaged"))
                self.replay(branch, load=True)

    def test_actual_unpreventable_damage_and_prohibited_life_gain_use_ordinary_damage_owners(self):
        for blocked in ("prevention", "gain"):
            with self.subTest(blocked=blocked):
                game = self.session(61550530 + (blocked == "gain"))
                purity = self.add(game.engine, ORIGINAL)
                shot = self.add(game.engine, "Shock", seat="B", zone="hand")
                pain = self.add(game.engine, "Flaring Pain", seat="B", zone="hand") if blocked == "prevention" else None
                prohibition = self.add(game.engine, "Giant Cindermaw", seat="B") if blocked == "gain" else None
                for card in (purity, shot, pain, prohibition):
                    if card is not None:
                        self.assert_closed(game, card)
                self.prepare(game)
                game.state.players["B"].mana_pool.update({"C": 1, "R": 2})
                self.checkpoint(game)
                before = game.state.players["A"].life
                if pain is not None:
                    self.cast_for(game, "B", pain, reserve={"R": 1})
                    self.resolve_top(game)
                self.cast_for(game, "B", shot, ["A"])
                self.assertIsNone(self.resolve(game))
                self.assertEqual(before - 2 * (blocked == "prevention"), game.state.players["A"].life)
                hits = game.engine._current_turn_history("player_damaged")
                self.assertEqual([2] if blocked == "prevention" else [], [event.amount for event in hits])
                self.replay(game, load=True)

    def test_actual_purity_composes_with_deflecting_palm_in_one_damage_batch(self):
        game = self.session(61550540)
        purity = self.add(game.engine, ORIGINAL)
        palm = self.add(game.engine, "Deflecting Palm", seat="B", zone="hand")
        syphon = self.add(game.engine, "Syphon Soul", seat="C", zone="hand")
        for card in (purity, palm, syphon):
            self.assert_closed(game, card)
        self.prepare(game, actor="C", active="C", mana={"C": 2, "B": 1})
        game.state.players["B"].mana_pool.update({"R": 1, "W": 1})
        self.checkpoint(game)
        self.cast_for(game, "C", syphon)
        original = game.state.stack[-1]
        self.cast_for(game, "B", palm)
        pending = self.resolve(game)
        self.assertEqual(("semantic.choice", ["B"]), (pending.kind, pending.actors))
        selected = game.act("pilot:B", {"action_id": "choose", "source": original.ref})
        self.assertTrue(selected.ok, selected.summary)
        shield, = game.state.damage_prevention_shields
        self.assertEqual("B", shield.controller)
        game = self.replay(game, load=True)
        self.resolve_top(game)
        self.assertEqual([42, 40, 40, 38], [game.state.players[seat].life for seat in "ABCD"])
        self.assertFalse(game.state.damage_prevention_shields)
        gain, = [event.details for event in game.state.events
                 if event.code == "damage.prevention.aftermath" and event.details.get("kind") == "gain_life"]
        self.assertEqual((purity.ref, "A", 2), (gain["source"], gain["subject"], gain["applied_amount"]))
        self.replay(game, load=True)

    def test_omitting_intrinsic_life_result_breaks_the_actual_composition(self):
        from unittest.mock import patch
        from quorune import damage_prevention_aftermath as owner

        original = owner._prevention_aftermath_sources

        def omit_intrinsic_gain(host, effects):
            return original(host, ())

        with patch.object(owner, "_prevention_aftermath_sources", side_effect=omit_intrinsic_gain):
            with self.assertRaisesRegex(AssertionError, r"\[42, 40, 40, 38\] != \[40, 40, 40, 38\]"):
                self.test_actual_purity_composes_with_deflecting_palm_in_one_damage_batch()

    def test_intrinsic_prevention_requires_its_complete_descriptor_and_trust(self):
        import json
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.damage_capability_shapes import (
            NONCOMBAT_PREVENTION_LIFE_GAIN_CAPABILITY, noncombat_prevention_life_gain_descriptor,
        )
        from quorune.semantic_runtime.context import SemanticNodeError
        from quorune.semantic_runtime.damage_replacements import NoncombatPreventionLifeGainHandler

        descriptor = noncombat_prevention_life_gain_descriptor()
        for malformed in (
            {**descriptor, "schema_version": True},
            {**descriptor, "condition": {"damage_kind": "noncombat", "recipient": "opponent"}},
            {**descriptor, "modification": {"amount": "all", "life_gain": "fixed"}},
        ):
            with self.subTest(malformed=malformed), self.assertRaises(SemanticNodeError):
                NoncombatPreventionLifeGainHandler().validate(malformed)
        card = self.db.lookup(ORIGINAL, fuzzy=False)
        control = compile_oracle_card(card, capability_registry=self.registry, capability_profile="commander_review")
        self.assertEqual("exact", control.status, control.material_residuals)
        raw = json.loads((ROOT / "quorune/rules/capability-registry.json").read_bytes())
        row = next(value for value in raw["capabilities"] if value["id"] == NONCOMBAT_PREVENTION_LIFE_GAIN_CAPABILITY)
        row.update(status="blocked", blockers=["Intrinsic prevention authority withdrawn"])
        rejected = compile_oracle_card(card, capability_registry=CapabilityRegistry(raw),
                                      capability_profile="commander_review")
        self.assertNotEqual("exact", rejected.status)

    def test_raw_intrinsic_result_requires_current_battlefield_ability_and_exact_controller_scope(self):
        _, semantics = register_card(self.db, self.registry, ORIGINAL)
        card = self.db.lookup(ORIGINAL, fuzzy=False)
        prevention, = [program for program in semantics.programs_for_oracle(card.oracle_id) if program.event == "damage"]
        for changed in (
            replace(prevention, active_zone="command"),
            replace(prevention, coverage=tuple(tag for tag in prevention.coverage
                                                if tag != CURRENT_ABILITY_FRAGMENT_COVERAGE)),
            replace(prevention, handlers=[]),
        ):
            report = bind_semantic_program_runtime(changed, capability_registry=self.registry, profile="commander_review")
            self.assertFalse(report["strict"], report)
            self.assertTrue(any("static_noncombat_prevention_life" in reason for reason in report["blockers"]), report)


    def test_actual_intrinsic_healing_and_reflection_follow_the_same_damage_batch(self):
        for nested_prevention in (False, True):
            with self.subTest(nested_prevention=nested_prevention):
                game = self.session(61550550 + nested_prevention)
                first = self.add(game.engine, ORIGINAL)
                nested = self.add(game.engine, ORIGINAL, seat="C", ref="purity-nested-source") if nested_prevention else None
                soul = self.add(game.engine, "Syphon Soul", seat="C", zone="hand")
                palm = self.add(game.engine, "Deflecting Palm", seat="B", zone="hand")
                for card in (first, nested, soul, palm):
                    if card is not None:
                        self.assert_closed(game, card)
                self.prepare(game, actor="C", active="C", mana={"C": 2, "B": 1})
                game.state.players["B"].mana_pool.update({"R": 1, "W": 1})
                self.checkpoint(game)
                self.cast_for(game, "C", soul, [])
                soul_stack = game.state.stack[-1].ref
                self.cast_for(game, "B", palm, [])
                decision = self.resolve(game)
                self.assertEqual(("semantic.choice", ["B"]), (decision.kind, decision.actors))
                chosen = game.act("pilot:B", {"action_id": "choose", "source": soul_stack})
                self.assertTrue(chosen.ok, chosen.summary)
                game = self.replay(game, load=True)
                self.resolve_top(game)
                self.assertFalse(game.state.damage_prevention_shields)
                self.assertEqual([42, 40, 44 if nested_prevention else 40, 38],
                                 [game.state.players[seat].life for seat in "ABCD"])
                events = [event.details for event in game.state.events if event.code == "damage.prevention.aftermath"]
                expected = {("gain_life", "A", 2), ("deal_damage", "C", 0 if nested_prevention else 2)}
                if nested_prevention:
                    expected.add(("gain_life", "C", 2))
                self.assertEqual(expected, {(event["kind"], event["subject"], event["applied_amount"]) for event in events})
                self.assertEqual("graveyard", game.state.cards[soul.object_id].zone)
                self.replay(game, load=True)
