from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.carddb import CardDatabase
from quorune.continuous_effect_model import ContinuousEffectError
from quorune.declaration_rule_effects import ResolutionDeclarationRuleEffect
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.temporary_attack_requirements import (
    LIVE_OPPONENT_ATTACK_CAPABILITY, LIVE_OPPONENT_ATTACK_THIS_TURN_CAPABILITY,
)
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card

CARD_NAME = "Kardur, Doomscourge"
PROFILE = "commander_review"
BIDENT_ID = "e1afaef7-9fa3-4662-a95f-adfb0da9fd11"


class KardurLiveAttackTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "kardur.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures/kardur-live-attack-cards.json"], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        commander = "Isamaru, Hound of Konda"
        cls.deck = DeckDefinition("Live next-turn attack requirements", [
            DeckEntry(commander, 1, "commander"), DeckEntry("Plains", 30),
        ], [commander])

    def attack_window(self, game, seat):
        self.until(game, lambda current: current.state.pending_decision is not None
                   and current.state.pending_decision.kind == "combat.attackers"
                   and current.state.pending_decision.actors == [seat])

    def test_actual_late_opponent_attacker_and_death_replay_until_next_turn(self):
        game = self.session(50861101)
        host = game.engine
        for player in game.state.players.values():
            for object_id in tuple(player.zones["hand"])[4:]:
                host.move_card(object_id, "library", log=False)
        source = self.add(host, CARD_NAME, zone="hand")
        self.assert_closed(game, source)
        own = self.add(host, "Raging Goblin", ref="kardur-own-goblin")
        own.acquired_control_turn_count = -1
        late = self.add(host, "Raging Goblin", seat="B", zone="hand", ref="kardur-late-goblin")
        untouched = self.add(host, "Grizzly Bears", seat="B", ref="kardur-nonattacking-bear")
        first_bolt = self.add(host, "Lightning Bolt", seat="D", zone="hand", ref="kardur-early-removal")
        second_bolt = self.add(host, "Lightning Bolt", seat="D", zone="hand", ref="kardur-combat-removal")
        for seat, lands in (("B", 1), ("D", 2)):
            for index in range(lands):
                self.add(host, "Mountain", seat=seat, ref=f"kardur-mountain-{seat}-{index}")
        self.prepare(game, mana={"C": 2, "B": 1, "R": 1})
        self.checkpoint(game)
        self.cast_for(game, "A", source)
        self.resolve_top(game)
        self.until(game, lambda current: self.trigger_count(current, source) == 1)
        game = self.replay(game, load=True)
        self.resolve_top(game)
        initial, = [effect for effect in game.state.continuous_effects
                    if getattr(effect, "live_query", None) is not None
                    and getattr(effect, "expires_on_next_turn_of", None) == "A"]
        self.assertEqual(("A",), initial.live_query.excluded_controllers)
        self.attack_window(game, "A")
        own_pass = game.act("pilot:A", {"action_id": "attack", "attackers": {}})
        self.assertTrue(own_pass.ok, own_pass.summary)
        self.until(game, lambda current: current.state.active_player == "B"
                   and current.state.phase == "precombat_main" and current.state.priority_player == "B")
        self.cast_for(game, "B", late)
        self.resolve_top(game)
        self.cast_for(game, "D", first_bolt, [untouched.ref])
        self.resolve_top(game)
        self.assertEqual("graveyard", game.state.cards[untouched.object_id].zone)
        self.assertEqual(0, self.trigger_count(game, source))
        self.attack_window(game, "B")
        game = self.replay(game, load=True)
        before = authoritative_state_hash(game.state)
        for targets in ({}, {late.ref: "A"}):
            denied = game.act("pilot:B", {"action_id": "attack", "attackers": targets})
            self.assertFalse(denied.ok, denied.summary)
            self.assertEqual(before, authoritative_state_hash(game.state))
        accepted = game.act("pilot:B", {"action_id": "attack", "attackers": {late.ref: "C"}})
        self.assertTrue(accepted.ok, accepted.summary)
        self.cast_for(game, "D", second_bolt, [late.ref])
        self.resolve_top(game)
        self.assertEqual("graveyard", game.state.cards[late.object_id].zone)
        self.until(game, lambda current: self.trigger_count(current, source) == 1)
        game = self.replay(game, load=True)
        self.resolve_top(game)
        self.assertEqual([41, 39, 39, 39], [game.state.players[seat].life for seat in "ABCD"])
        self.until(game, lambda current: current.state.active_player == "A" and current.state.turn_sequence > 1)
        self.assertNotIn(initial.effect_id, {effect.effect_id for effect in game.state.continuous_effects})
        self.replay(game, load=True)

    def test_live_requirement_owner_and_duration_remain_closed(self):
        card = self.db.lookup(CARD_NAME, fuzzy=False)
        options = {"capability_registry": self.registry, "capability_profile": PROFILE}
        ir = compile_oracle_card(card, **options)
        self.assertEqual("exact", ir.status, ir.material_residuals)
        report, semantics = register_card(self.db, self.registry, CARD_NAME)
        self.assertTrue(report["strict_capability_ready"], report)
        program, = (candidate for candidate in semantics.programs_for_oracle(card.oracle_id)
                    if LIVE_OPPONENT_ATTACK_CAPABILITY in candidate.capability_dependencies)
        effect, = program.effects
        for changed in (
            {**effect, "until_controller_next_turn": 1},
            {**effect, "set": {**effect["set"], "controller_relation": "actor"}},
        ):
            altered = replace(program, effects=(changed,))
            self.assertFalse(bind_semantic_program_runtime(
                altered, capability_registry=self.registry, profile=PROFILE)["strict"])
        remaining = tuple(cap for cap in program.capability_dependencies if cap != LIVE_OPPONENT_ATTACK_CAPABILITY)
        withdrawn = replace(program, capability_dependencies=remaining,
                            capability_closure=self.registry.closure(remaining, profile=PROFILE).to_dict())
        binding = bind_semantic_program_runtime(withdrawn, capability_registry=self.registry, profile=PROFILE)
        self.assertIn("capability:undeclared_runtime_dependency:" + LIVE_OPPONENT_ATTACK_CAPABILITY,
                      binding["blockers"])
        unsupported = replace(card, oracle_text=card.oracle_text.replace("until your next turn", "until end of turn"))
        self.assertNotEqual("exact", compile_oracle_card(unsupported, **options).status)
        with patch("quorune.compiler.temporary_declaration_templates.temporary_attack_requirement_template",
                   return_value=None):
            self.assertNotEqual("exact", compile_oracle_card(card, **options).status)


class BidentLiveAttackTests(InheritedNativeFixture, unittest.TestCase):
    attack_window = KardurLiveAttackTests.attack_window

    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "bident.sqlite3"
        fixtures = ROOT / "tests" / "fixtures"
        build_fixture_database(
            [
                fixtures / "bident-live-attack-cards.json",
                fixtures / "kardur-live-attack-cards.json",
                fixtures / "source-attack-alone-cards.json",
            ],
            path,
        )
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        commander = "Isamaru, Hound of Konda"
        cls.deck = DeckDefinition(
            "Live current-turn attack requirements",
            [DeckEntry(commander, 1, "commander"), DeckEntry("Plains", 30)],
            [commander],
        )

    def bident(self, game, *, zone="battlefield"):
        source = self.add(game.engine, "Bident of Thassa", zone=zone)
        self.assertEqual(BIDENT_ID, source.oracle_id)
        self.assert_closed(game, source)
        return source

    def activate_bident(self, game, source):
        self.priority(game, "A")
        actions = game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        offer, = (row for row in actions if row["id"].startswith(f"activate:{source.ref}:"))
        self.assertEqual({"GENERIC": 1, "U": 1}, offer["cost_summary"]["m"])
        self.assertFalse(offer.get("target_schema"))
        before = authoritative_state_hash(game.state)
        command = {"action_id": offer["id"], "pay": "auto"}
        self.assertFalse(game.act("pilot:B", command).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        accepted = game.act("pilot:A", command)
        self.assertTrue(accepted.ok, accepted.summary)
        self.assertTrue(game.state.cards[source.object_id].tapped)
        self.assertEqual("activated_ability", game.state.stack[-1].kind)
        self.assertFalse(game.state.stack[-1].targets)

    def test_actual_bident_forces_live_eligible_opponents_and_keeps_any_defender(self):
        game = self.session(448_508_101)
        for object_id in tuple(game.state.players["B"].zones["hand"])[5:]:
            game.engine.move_card(object_id, "library", log=False)
        source = self.bident(game)
        ready = self.add(game.engine, "Grizzly Bears", seat="B", ref="current-ready")
        ready.acquired_control_turn_count = -1
        tapped = self.add(game.engine, "Grizzly Bears", seat="B", ref="current-tapped")
        tapped.acquired_control_turn_count = -1
        tapped.tapped = True
        late = self.add(game.engine, "Raging Goblin", seat="B", zone="hand")
        sick = self.add(game.engine, "Grizzly Bears", seat="B", zone="hand", ref="new-nonhaste")
        later_turn = self.add(game.engine, "Raging Goblin", seat="C", ref="next-turn-optional")
        later_turn.acquired_control_turn_count = -1
        disenchant = self.add(game.engine, "Disenchant", seat="C", zone="hand")
        self.add(game.engine, "Island", seat="A")
        self.add(game.engine, "Plains", seat="A", ref="bident-generic-mana")
        self.add(game.engine, "Plains", seat="C", ref="removal-white-mana")
        self.add(game.engine, "Plains", seat="C", ref="removal-generic-mana")
        self.prepare(game, actor="B", active="B", mana={"R": 1, "G": 1, "C": 1})
        self.checkpoint(game)

        self.activate_bident(game, source)
        self.cast_for(game, "C", disenchant, [source.ref])
        self.resolve_top(game)
        self.assertEqual("graveyard", game.state.cards[source.object_id].zone)
        self.resolve_top(game)
        rule, = (effect for effect in game.state.continuous_effects
                 if isinstance(effect, ResolutionDeclarationRuleEffect) and effect.live_query is not None)
        self.assertEqual(("A",), rule.live_query.excluded_controllers)
        self.assertEqual("until_end_of_turn", rule.duration.value)
        self.assertEqual(8, rule.to_dict()["schema_version"])
        self.assertEqual(rule, ResolutionDeclarationRuleEffect.from_dict(rule.to_dict()))
        for malformed in (
            {**rule.to_dict(), "schema_version": 7},
            {**rule.to_dict(), "live_query": {**rule.to_dict()["live_query"], "excluded_controllers": []}},
        ):
            with self.assertRaises(ContinuousEffectError):
                ResolutionDeclarationRuleEffect.from_dict(malformed)

        self.cast_for(game, "B", late, reserve={"G": 1, "C": 1})
        self.resolve_top(game)
        self.cast_for(game, "B", sick)
        self.resolve_top(game)
        self.attack_window(game, "B")
        game = self.replay(game, load=True)
        before = authoritative_state_hash(game.state)
        for partial in ({}, {ready.ref: "A"}, {late.ref: "A"}):
            denied = game.act("pilot:B", {"action_id": "attack", "attackers": partial})
            self.assertFalse(denied.ok, denied.summary)
            self.assertEqual(before, authoritative_state_hash(game.state))
        command = {"action_id": "attack", "attackers": {ready.ref: "A", late.ref: "A"}}
        accepted = game.act("pilot:B", command)
        self.assertTrue(accepted.ok, accepted.summary)
        self.assertEqual({ready.object_id, late.object_id}, set(game.state.combat.attackers))
        self.attack_window(game, "C")
        self.assertNotIn(rule.effect_id, {effect.effect_id for effect in game.state.continuous_effects})
        accepted = game.act("pilot:C", {"action_id": "attack", "attackers": {}})
        self.assertTrue(accepted.ok, accepted.summary)
        self.replay(game, load=True)

    def test_actual_bident_keeps_private_optional_draw_and_own_creature_is_exempt(self):
        game = self.session(448_508_102)
        source = self.bident(game, zone="hand")
        fighter = self.add(game.engine, "Grizzly Bears", ref="bident-combat-draw")
        idle = self.add(game.engine, "Grizzly Bears", ref="bident-own-idle")
        fighter.acquired_control_turn_count = -1
        idle.acquired_control_turn_count = -1
        self.add(game.engine, "Island", seat="A")
        self.add(game.engine, "Plains", seat="A")
        self.prepare(game, mana={"C": 2, "U": 2})
        self.checkpoint(game)
        offer = self.action(game, "A", source)
        cast = game.act("pilot:A", {"action_id": offer["id"], "pay": "manual", "payment": {"C": 2, "U": 2}})
        self.assertTrue(cast.ok, cast.summary)
        self.resolve_top(game)
        self.activate_bident(game, source)
        self.resolve_top(game)
        self.attack_window(game, "A")
        accepted = game.act("pilot:A", {"action_id": "attack", "attackers": {fighter.ref: "B"}})
        self.assertTrue(accepted.ok, accepted.summary)
        self.until(game, lambda current: current.state.pending_decision is not None
                   and current.state.pending_decision.kind == "semantic.choice")
        self.assertEqual(["A"], game.state.pending_decision.actors)
        self.assertEqual(38, game.state.players["B"].life)
        drawn = game.state.players["A"].zones["library"][-1]
        game = self.replay(game, load=True)
        command = {"action_id": "choose", "choice": "apply"}
        before = authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:B", command).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        accepted = game.act("pilot:A", command)
        self.assertTrue(accepted.ok, accepted.summary)
        self.assertIn(drawn, game.state.players["A"].zones["hand"])
        for seat in "BCD":
            self.assertNotIn(game.state.cards[drawn].ref, json.dumps(game.packet("pilot:" + seat, full=True)))
        self.replay(game, load=True)

    def test_bident_current_turn_program_closes_both_abilities_and_rejects_scope_forgery(self):
        card = self.db.by_oracle_id(BIDENT_ID)
        options = {"capability_registry": self.registry, "capability_profile": PROFILE}
        report, semantics = register_card(self.db, self.registry, "Bident of Thassa")
        self.assertTrue(report["strict_capability_ready"], report)
        programs = semantics.programs_for_oracle(card.oracle_id)
        program, = (p for p in programs if LIVE_OPPONENT_ATTACK_THIS_TURN_CAPABILITY in p.capability_dependencies)
        draw, = (p for p in programs if "zone.draw.library_to_hand" in p.capability_dependencies)
        self.assertIn("effect.choice.optional_fixed", draw.capability_dependencies)
        self.assertIsNone(program.target_schema)
        effect, = program.effects
        for altered in (
            replace(program, effects=({**effect, "until_controller_next_turn": True},)),
            replace(program, effects=({**effect, "set": {**effect["set"], "controller_relation": "actor"}},)),
            replace(program, handlers=()),
        ):
            binding = bind_semantic_program_runtime(altered, capability_registry=self.registry, profile=PROFILE)
            self.assertFalse(binding["strict"], binding)
        remaining = tuple(cap for cap in program.capability_dependencies if cap != LIVE_OPPONENT_ATTACK_THIS_TURN_CAPABILITY)
        withdrawn = replace(program, capability_dependencies=remaining,
                            capability_closure=self.registry.closure(remaining, profile=PROFILE).to_dict())
        binding = bind_semantic_program_runtime(withdrawn, capability_registry=self.registry, profile=PROFILE)
        self.assertIn("capability:undeclared_runtime_dependency:" + LIVE_OPPONENT_ATTACK_THIS_TURN_CAPABILITY,
                      binding["blockers"])
        with patch("quorune.compiler.temporary_declaration_templates.temporary_attack_requirement_template", return_value=None):
            omitted = compile_oracle_card(card, **options)
            self.assertTrue(omitted.material_residuals)
            self.assertTrue(any("zone.draw.library_to_hand" in node.capability_dependencies for node in omitted.faces[0].nodes))
