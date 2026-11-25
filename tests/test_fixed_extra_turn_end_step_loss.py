"""Exercise the delayed loss bound to one real extra turn."""

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


ORIGINALS = ("Final Fortune", "Last Chance", "Warrior's Oath")


class FixedExtraTurnEndStepLossTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "fixed-extra-turn-loss.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures/fixed-extra-turn-end-step-loss-cards.json"], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Fixed extra turn end-step loss", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"), DeckEntry("Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def test_complete_originals_bind_exactly_one_controller_turn_and_one_delayed_loss(self):
        for name in ORIGINALS:
            with self.subTest(card=name):
                card = self.db.lookup(name, fuzzy=False)
                binding, semantics = register_card(self.db, self.registry, name)
                self.assertTrue(binding["strict_capability_ready"], binding["blockers"])
                program, = [row for row in semantics.programs_for_oracle(card.oracle_id) if row.event == "resolve"]
                effect, = program.effects
                self.assertIsNone(program.target_schema)
                self.assertEqual({"op": "extra_turn", "player": "$controller", "source": "$source",
                                  "extra_turn_end_step_loss_version": 1}, effect)
                self.assertLessEqual({"turn.extra.fixed_after_current", "game.loss.effect", "trigger.placement.apnap"},
                                     set(program.capability_dependencies))
                for text in (
                    card.oracle_text.replace("that turn's end step", "the next end step"),
                    card.oracle_text.replace("an extra turn", "two extra turns"),
                    card.oracle_text.replace("Take", "Target player takes"),
                    card.oracle_text + " Exile all permanents.",
                ):
                    self.assertNotEqual("exact", compile_oracle_card(
                        replace(card, oracle_text=text), capability_registry=self.registry,
                    ).status, text)
        extra = self.db.lookup("Chance for Glory", fuzzy=False)
        ir = compile_oracle_card(extra, capability_registry=self.registry)
        self.assertNotEqual("exact", ir.status)

    def test_raw_loss_shape_rejects_other_timing_recipients_versions_and_undeclared_authority(self):
        card = self.db.lookup("Final Fortune", fuzzy=False)
        _, semantics = register_card(self.db, self.registry, card.name)
        program, = [row for row in semantics.programs_for_oracle(card.oracle_id) if row.event == "resolve"]
        effect, = program.effects
        retained = tuple(key for key in program.capability_dependencies if key != "game.loss.effect")
        variations = (
            replace(program, coverage=(), capability_dependencies=retained,
                    capability_closure=self.registry.closure(retained, profile="commander_review").to_dict()),
            replace(program, effects=({**effect, "player": "$target.0"},)),
            replace(program, effects=({**effect, "extra_turn_end_step_loss_version": True},)),
            replace(program, effects=({**effect, "step": "upkeep"},)),
            replace(program, effects=(effect, effect)),
        )
        for changed in variations:
            with self.subTest(effects=changed.effects):
                binding = bind_semantic_program_runtime(
                    changed, capability_registry=self.registry, profile="commander_review",
                )
                self.assertFalse(binding["strict"], binding)

    def advance_to(self, game, predicate):
        for _ in range(4):
            self.until(game, lambda current: predicate(current)
                       or (current.state.pending_decision is not None
                           and current.state.pending_decision.kind == "cleanup.discard"))
            if predicate(game):
                return
            principal = game.pending_principals()[0]
            context = game.packet(principal, full=True)["decision"]["ctx"]
            cards = [card["id"] for card in context["hand"][:context["count"]]]
            result = game.act(principal, {"action_id": "discard", "cards": cards})
            self.assertTrue(result.ok, result.summary)
        self.fail("The exact turn boundary did not follow cleanup")

    def end_step_of(self, game, turn_id):
        self.advance_to(game, lambda current: current.state.current_turn is not None
                   and current.state.current_turn.turn_id == turn_id
                   and current.state.step == "end_step"
                   and current.state.pending_decision is not None
                   and current.state.pending_decision.kind == "priority")

    def ready(self, game, mana, *cards):
        for card in cards:
            self.assert_closed(game, card)
        self.prepare(game, mana=mana)
        self.checkpoint(game)

    def test_actual_final_fortune_waits_for_its_own_extra_turn_and_stifle_can_counter_the_loss(self):
        game = self.session(61341301)
        engine = game.engine
        fortune = self.add(engine, "Final Fortune", zone="hand")
        warp = self.add(engine, "Time Warp", zone="hand")
        stifle = self.add(engine, "Stifle", seat="B", zone="hand")
        private = self.add(engine, "Counterspell", seat="D", zone="hand", ref="extra-turn-private-hand")
        self.add(engine, "Island", seat="B")
        self.ready(game, {"C": 3, "U": 2, "R": 2}, fortune, warp, stifle)
        self.cast_for(game, "A", fortune)
        self.resolve_top(game)
        marked, = game.state.extra_turns
        self.assertEqual("A", marked.player)
        self.cast_for(game, "A", warp, ["A"])
        self.resolve_top(game)
        ordinary, still_marked = game.state.extra_turns
        self.assertEqual(marked.turn_id, still_marked.turn_id)
        self.assertEqual("A", ordinary.player)
        self.assertNotEqual(ordinary.turn_id, marked.turn_id)
        self.assertEqual("graveyard", game.state.cards[fortune.object_id].zone)

        self.end_step_of(game, ordinary.turn_id)
        self.assertIn("A", game.engine.active_seats)
        self.assertFalse(game.state.stack, "Another extra turn cannot receive the delayed loss")
        self.end_step_of(game, marked.turn_id)
        loss, = game.state.stack
        self.assertEqual(("triggered_ability", "A"), (loss.kind, loss.controller))
        self.assertEqual([{"op": "lose_game", "player": "A"}], loss.context["dynamic_effects"])
        self.assertIn("A", game.engine.active_seats)
        game = self.replay(game, load=True)
        self.priority(game, "B")
        offered = self.action(game, "B", stifle)
        command = {"action_id": offered["id"], "targets": [loss.ref], "pay": "auto"}
        before = authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:C", command).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        self.cast_for(game, "B", stifle, [loss.ref])
        self.resolve_top(game)
        self.assertIn("A", game.engine.active_seats)
        self.assertFalse(game.state.stack)
        for viewer in ("pilot:A", "pilot:B", "pilot:C", "spectator"):
            self.assertNotIn(private.ref, json.dumps(game.packet(viewer, full=True)))
        self.replay(game, load=True)

    def test_actual_meditate_skips_last_chances_exact_turn_and_retires_the_loss(self):
        game = self.session(61341302)
        engine = game.engine
        spell = self.add(engine, "Last Chance", zone="hand")
        meditate = self.add(engine, "Meditate", zone="hand")
        self.ready(game, {"C": 2, "U": 1, "R": 2}, spell, meditate)
        self.cast_for(game, "A", spell)
        self.resolve_top(game)
        marked, = game.state.extra_turns
        self.cast_for(game, "A", meditate)
        self.resolve_top(game)
        self.advance_to(game, lambda current: current.state.active_player == "B")
        self.assertIn("A", game.engine.active_seats)
        self.assertFalse(game.state.extra_turns)
        self.assertFalse(game.state.current_turn.extra)
        skipped, = [event for event in game.state.events if event.code == "turn.skip.applied"]
        self.assertEqual(marked.turn_id, skipped.details["turn"])
        self.end_step_of(game, game.state.current_turn.turn_id)
        self.assertFalse(game.state.stack)
        self.assertFalse(any(trigger.active and trigger.controller == "A"
                             for trigger in game.state.delayed_triggers))
        self.replay(game, load=True)

    def test_actual_warriors_oath_eliminates_only_its_original_controller_on_resolution(self):
        game = self.session(61341303)
        spell = self.add(game.engine, "Warrior's Oath", zone="hand")
        self.ready(game, {"R": 2}, spell)
        self.cast_for(game, "A", spell)
        self.resolve_top(game)
        marked, = game.state.extra_turns
        self.end_step_of(game, marked.turn_id)
        self.assertIn("A", game.engine.active_seats)
        self.assertEqual(1, len(game.state.stack))
        game = self.replay(game, load=True)
        self.resolve_top(game)
        self.assertEqual(["B", "C", "D"], game.engine.active_seats)
        self.assertFalse(game.state.game_over)
        self.replay(game, load=True)

    def test_wrong_extra_turn_identity_is_killed_by_the_actual_final_fortune_witness(self):
        from quorune.rules import extra_turn_shapes

        exact_turn = extra_turn_shapes.extra_turn_end_step_loss_trigger_turn

        def next_turn(trigger):
            turn = exact_turn(trigger)
            return "X" + str(int(turn[1:]) + 1)

        with patch.object(extra_turn_shapes, "extra_turn_end_step_loss_trigger_turn", side_effect=next_turn):
            with self.assertRaisesRegex(AssertionError, "Another extra turn"):
                self.test_actual_final_fortune_waits_for_its_own_extra_turn_and_stifle_can_counter_the_loss()
