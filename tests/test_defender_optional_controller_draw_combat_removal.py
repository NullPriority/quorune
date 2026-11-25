"""Exercise a real defender's choice for the source controller's private draw."""

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
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


HEAVY = "Shakedown Heavy"


class DefenderOptionalControllerDrawCombatRemovalTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "defender-controller-draw.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures/defender-optional-controller-draw-combat-removal-cards.json"], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Defender chooses controller draw", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"), DeckEntry("Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def test_complete_original_keeps_menace_and_the_two_distinct_attack_seats(self):
        card = self.db.lookup(HEAVY, fuzzy=False)
        binding, semantics = register_card(self.db, self.registry, HEAVY)
        self.assertTrue(binding["strict_capability_ready"], binding["blockers"])
        trigger, = [program for program in semantics.programs_for_oracle(card.oracle_id)
                    if program.event == "creature.attacks"]
        self.assertIsNone(trigger.target_schema)
        self.assertEqual(({"op": "offer_draw", "event_optional_draw_version": 5,
                           "player": "$context.defending_player", "drawer": "$controller",
                           "count": 1, "private": True},), tuple(trigger.effects))
        self.assertLessEqual({"combat.effect.remove_from_combat", "zone.draw.library_to_hand",
                              "resolution.event_player.optional_draw", "permanent.untap.effect"},
                             set(trigger.capability_dependencies))
        ir = compile_oracle_card(card, capability_registry=self.registry)
        self.assertIn("Menace", [node.text for face in ir.faces for node in face.nodes])
        for text in (
            card.oracle_text.replace("a card.", "two cards."),
            card.oracle_text.replace("have you draw", "draw"),
            card.oracle_text.replace("untap this creature and remove it", "remove this creature"),
            card.oracle_text + " Draw a card.",
        ):
            self.assertNotEqual("exact", compile_oracle_card(replace(card, oracle_text=text),
                                capability_registry=self.registry).status, text)

    def test_raw_choice_rejects_reassigned_drawer_occurrence_or_after_result(self):
        card = self.db.lookup(HEAVY, fuzzy=False)
        _, semantics = register_card(self.db, self.registry, HEAVY)
        trigger, = [program for program in semantics.programs_for_oracle(card.oracle_id)
                    if program.event == "creature.attacks"]
        effect, = trigger.effects
        retained = tuple(key for key in trigger.capability_dependencies if key != "combat.effect.remove_from_combat")
        for changed in (
            replace(trigger, event_condition=None),
            replace(trigger, effects=({**effect, "drawer": "$context.defending_player"},)),
            replace(trigger, effects=({**effect, "player": "$controller"},)),
            replace(trigger, effects=({**effect, "effects": [{"op": "draw", "player": "$controller"}]},)),
            replace(trigger, coverage=(), capability_dependencies=retained,
                    capability_closure=self.registry.closure(retained, profile="commander_review").to_dict()),
        ):
            bound = bind_semantic_program_runtime(changed, capability_registry=self.registry,
                                                  profile="commander_review")
            self.assertFalse(bound["strict"], bound)

    def enter(self, game, source, *controls, mana=None):
        for card in (source, *controls):
            self.assert_closed(game, card)
        self.prepare(game, mana=mana or {"C": 2, "B": 1})
        self.checkpoint(game)
        self.cast_for(game, "A", source)
        self.resolve_top(game)
        self.assertIn("menace", game.engine._combat_keywords(game.state.cards[source.object_id]))

    def attack_heavy(self, game, source):
        self.attack(game, [(source, "C")])
        self.assertEqual(1, self.trigger_count(game, source))
        self.assertTrue(game.state.cards[source.object_id].tapped)

    def offer(self, game):
        decision = self.resolve(game)
        self.assertEqual(("semantic.choice", ["C"]), (decision.kind, decision.actors))
        context = game.packet("pilot:C", full=True)["decision"]["ctx"]
        self.assertEqual("A", context["drawer"])
        return context

    def test_actual_defender_acceptance_draws_privately_and_removes_the_paid_attacker(self):
        game = self.session(61341501)
        source = self.add(game.engine, HEAVY, zone="hand")
        haste = self.add(game.engine, "Fervor")
        secret = self.add(game.engine, "Counterspell", zone="library", ref="shakedown-private-draw")
        self.enter(game, source, haste)
        self.attack_heavy(game, source)
        self.offer(game)
        before = {seat: len(player.zones["hand"]) for seat, player in game.state.players.items()}
        command = {"action_id": "choose", "choice": "draw"}
        digest = authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:A", command).ok)
        self.assertFalse(game.act("pilot:D", command).ok)
        self.assertEqual(digest, authoritative_state_hash(game.state))
        game = self.replay(game, load=True)
        offset = game.state.event_sequence
        accepted = game.act("pilot:C", command)
        self.assertTrue(accepted.ok, accepted.summary)
        self.assertEqual({seat: count + (seat == "A") for seat, count in before.items()},
                         {seat: len(player.zones["hand"]) for seat, player in game.state.players.items()})
        self.assertIn(secret.object_id, game.state.players["A"].zones["hand"])
        self.assertIn(secret.ref, json.dumps(game.packet("pilot:A", full=True)))
        for viewer in ("pilot:B", "pilot:C", "pilot:D", "spectator"):
            self.assertNotIn(secret.ref, json.dumps(game.packet(viewer, full=True)))
        self.assertNotIn(source.object_id, game.state.combat.attackers,
                         "An accepted draw must remove the original attacker")
        self.assertFalse(game.state.cards[source.object_id].tapped)
        events = [event.code for event in game.state.events if event.event_id > offset
                  and event.code in ("card.draw", "permanent.untap", "combat.remove")]
        self.assertEqual(["card.draw", "permanent.untap", "combat.remove"], events)
        self.replay(game, load=True)

    def test_actual_decline_and_prohibited_controller_draw_keep_the_source_attacking(self):
        for mode, seed in (("decline", 61341502), ("prohibited", 61341503)):
            with self.subTest(mode=mode):
                game = self.session(seed)
                source = self.add(game.engine, HEAVY, zone="hand")
                haste = self.add(game.engine, "Fervor")
                if mode == "prohibited":
                    spirit = self.add(game.engine, "Spirit of the Labyrinth")
                    draw = self.add(game.engine, "Reach Through Mists", zone="hand")
                    self.add(game.engine, "Island", ref="shakedown-draw-island-1")
                    self.add(game.engine, "Island", ref="shakedown-draw-island-2")
                    self.enter(game, source, haste, spirit, draw, mana={"C": 2, "B": 1, "U": 1})
                    self.cast_for(game, "A", draw)
                    self.resolve_top(game)
                else:
                    self.enter(game, source, haste)
                self.attack_heavy(game, source)
                before = len(game.state.players["A"].zones["hand"])
                if mode == "decline":
                    self.offer(game)
                    result = game.act("pilot:C", {"action_id": "choose", "choice": "decline"})
                    self.assertTrue(result.ok, result.summary)
                else:
                    self.resolve_top(game)
                self.assertEqual(before, len(game.state.players["A"].zones["hand"]))
                self.assertIn(source.object_id, game.state.combat.attackers)
                self.assertTrue(game.state.cards[source.object_id].tapped)
                self.replay(game, load=True)

    def test_actual_response_does_not_remove_the_defenders_option_when_the_source_is_gone(self):
        game = self.session(61341504)
        source = self.add(game.engine, HEAVY, zone="hand")
        haste = self.add(game.engine, "Fervor")
        bounce = self.add(game.engine, "Unsummon", seat="B", zone="hand")
        self.add(game.engine, "Island", seat="B")
        self.enter(game, source, haste, bounce)
        self.attack_heavy(game, source)
        self.cast_for(game, "B", bounce, [source.ref])
        self.resolve_top(game)
        self.assertEqual("hand", game.state.cards[source.object_id].zone)
        self.offer(game)
        before = len(game.state.players["A"].zones["hand"])
        result = game.act("pilot:C", {"action_id": "choose", "choice": "draw"})
        self.assertTrue(result.ok, result.summary)
        self.assertEqual(before + 1, len(game.state.players["A"].zones["hand"]))
        self.assertEqual("hand", game.state.cards[source.object_id].zone)
        self.replay(game, load=True)

    def test_actual_dredge_pauses_the_taken_offer_before_untap_and_removal(self):
        game = self.session(61341505)
        source = self.add(game.engine, HEAVY, zone="hand")
        haste = self.add(game.engine, "Fervor")
        loam = self.add(game.engine, "Life from the Loam", zone="graveyard")
        self.enter(game, source, haste, loam)
        self.attack_heavy(game, source)
        self.offer(game)
        before = len(game.state.players["A"].zones["library"])
        draw_history = len(game.state.players["A"].draw_history)
        result = game.act("pilot:C", {"action_id": "choose", "choice": "draw"})
        self.assertTrue(result.ok, result.summary)
        decision = game.state.pending_decision
        self.assertEqual(("draw.replacement", ["A"]), (decision.kind, decision.actors))
        self.assertIn(source.object_id, game.state.combat.attackers)
        self.assertTrue(game.state.cards[source.object_id].tapped)
        game = self.replay(game, load=True)
        result = game.act("pilot:A", {"action_id": "choose", "choice": loam.ref})
        self.assertTrue(result.ok, result.summary)
        self.assertEqual("hand", game.state.cards[loam.object_id].zone)
        self.assertEqual(before - 3, len(game.state.players["A"].zones["library"]))
        self.assertEqual(draw_history, len(game.state.players["A"].draw_history))
        self.assertNotIn(source.object_id, game.state.combat.attackers)
        self.assertFalse(game.state.cards[source.object_id].tapped)
        self.replay(game, load=True)

    def test_missing_combat_followup_is_killed_after_an_actual_defender_accepts(self):
        with patch("quorune.semantic_choices.optional_draw._defending_controller_draw_after_effects", return_value=()):
            with self.assertRaisesRegex(AssertionError, "An accepted draw must remove"):
                self.test_actual_defender_acceptance_draws_privately_and_removes_the_paid_attacker()
