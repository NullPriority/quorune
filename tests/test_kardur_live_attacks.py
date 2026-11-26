from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.temporary_attack_requirements import LIVE_OPPONENT_ATTACK_CAPABILITY
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card

CARD_NAME = "Kardur, Doomscourge"
PROFILE = "commander_review"


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
