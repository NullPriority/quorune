"""Witness a public roll feeding an ordinary draw before a durable player rule."""

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
from quorune.maximum_hand_size import effective_maximum_hand_size
from quorune.oracle_ir import compile_oracle_card
from quorune.permissions import PermissionDenied
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database

SOURCE = "Generic Storm Reckoner"
PROFILE = "commander_review"


class SingleD20DrawTests(InheritedNativeFixture, unittest.TestCase):
    source_name = SOURCE
    expected_power = 3
    casting_mana = {"U": 1, "C": 1}
    haste_provider = None

    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        cls.addClassCleanup(cls.temporary.cleanup)
        path = Path(cls.temporary.name) / "single-d20-draw.sqlite3"
        fixtures = ROOT / "tests" / "fixtures"
        build_fixture_database([
            fixtures / "single-d20-draw-cards.json",
            fixtures / "controller-hand-count-game-size-test-cards.json",
            fixtures / "draw-discard-replacement-cards.json",
            fixtures / "combat-damage-dealer-recipient-control-cards.json",
            fixtures / "source-keyword-choice-cards.json",
        ], path)
        cls.db = CardDatabase(path)
        cls.addClassCleanup(cls.db.close)
        cls.registry = load_default_capability_registry()
        commander = "Isamaru, Hound of Konda"
        cls.deck = DeckDefinition("Single d20 draw", [
            DeckEntry(commander, 1, "commander"), DeckEntry("Plains", 37),
        ], [commander])

    @classmethod
    def tearDownClass(cls):
        pass  # addClassCleanup also closes partial setup on failure.

    def roll_events(self, game):
        return tuple(event for event in game.state.events if event.code == "die.roll")

    def attack_for_trigger(self, game, source, defender="D"):
        self.attack(game, [(source, defender)])
        self.until(game, lambda current: any(
            item.kind == "triggered_ability" and item.source_object_id == source.object_id
            for item in current.state.stack
        ))
        trigger, = game.state.stack
        self.assertEqual("A", trigger.controller)
        self.assertFalse(self.roll_events(game), "The die must remain unrolled before resolution")
        return trigger

    def draw_fixture(self, seed):
        game = self.session(seed)
        source = self.add(game.engine, self.source_name, zone="hand")
        if self.haste_provider is not None:
            self.add(game.engine, self.haste_provider)
        private = self.add(game.engine, "Plains", zone="hand", ref="roll-controller-private")
        self.add(game.engine, "Plains", zone="hand", ref="roll-extra-hand")
        self.assert_closed(game, source)
        self.prepare(game, mana=self.casting_mana)
        return game, source, private

    def cast_fixture(self, game, source):
        self.checkpoint(game)
        self.cast_for(game, "A", source)
        self.assertIsNone(self.resolve(game))
        current = game.state.cards[source.object_id]
        self.assertEqual("battlefield", current.zone)
        return current

    def assert_public_roll(self, game, trigger):
        event, = self.roll_events(game)
        self.assertEqual(("A", 20, trigger.ref), (
            event.actor, event.details["sides"], event.details["stack"],
        ))
        face = event.details["result"]
        self.assertIn(face, range(1, 21))
        self.assertTrue(set("ABCD").issubset(event.visibility))
        return face

    def test_hand_count_draw_reads_current_hand_and_keeps_the_game_duration_rule(self):
        game = self.session(706013)
        spell = self.add(game.engine, "Test Hall Census", zone="hand")
        response = self.add(game.engine, "Unsummon", zone="hand")
        other = self.add(game.engine, "Isamaru, Hound of Konda", seat="B")
        self.assert_closed(game, spell)
        self.prepare(game, mana={"U": 2})
        self.checkpoint(game)
        self.cast_for(game, "A", spell)
        announced_hand_count = len(game.state.players["A"].zones["hand"])
        self.cast_for(game, "A", response, [other.ref])
        self.resolve_top(game)
        self.assertEqual("hand", game.state.cards[other.object_id].zone)
        hands = {seat: set(game.state.players[seat].zones["hand"]) for seat in "ABCD"}
        self.assertEqual(announced_hand_count - 1, len(hands["A"]))
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        drawn = set(game.state.players["A"].zones["hand"]) - hands["A"]
        self.assertEqual(len(hands["A"]) + 1, len(drawn))
        self.assertEqual("graveyard", game.state.cards[spell.object_id].zone)
        self.assertIsNone(effective_maximum_hand_size(game.engine, "A"))
        self.assertFalse(self.roll_events(game))
        for seat in "BCD":
            self.assertEqual(hands[seat], set(game.state.players[seat].zones["hand"]))
            self.assertEqual(7, effective_maximum_hand_size(game.engine, seat))
            packet = json.dumps(game.packet(f"pilot:{seat}", full=True))
            for object_id in drawn:
                self.assertNotIn(game.state.cards[object_id].ref, packet)
        count = len(game.state.players["A"].zones["hand"])
        self.assertGreater(count, 7)
        self.until(game, lambda current: current.state.active_player != "A"
                   or current.state.pending_decision.kind == "cleanup.discard")
        self.assertNotEqual("A", game.state.active_player)
        self.assertEqual(count, len(game.state.players["A"].zones["hand"]))
        game = self.replay(game, load=True)
        self.assertIsNone(effective_maximum_hand_size(game.engine, "A"))

    def test_unmodified_roll_draws_for_locked_controller_and_cleanup_survives_departure(self):
        game, source, private = self.draw_fixture(706001)
        theft = self.add(game.engine, "Word of Seizing", seat="B", zone="hand")
        for index in range(5):
            self.add(game.engine, "Mountain", seat="B", ref=f"roll-theft-mana-{index}")
        bounce = self.add(game.engine, "Unsummon", seat="C", zone="hand")
        self.add(game.engine, "Island", seat="C", ref="roll-bounce-mana")
        source = self.cast_fixture(game, source)
        self.assertIn("flying", game.engine._combat_keywords(source))
        life = game.state.players["D"].life
        trigger = self.attack_for_trigger(game, source)
        self.assertEqual(life - self.expected_power, game.state.players["D"].life)
        self.cast_for(game, "B", theft, [source.ref])
        self.resolve_top(game)
        self.assertEqual("B", game.state.cards[source.object_id].controller)
        hands = {seat: set(game.state.players[seat].zones["hand"]) for seat in "ABCD"}
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        face = self.assert_public_roll(game, trigger)
        drawn = set(game.state.players["A"].zones["hand"]) - hands["A"]
        self.assertEqual(face, len(drawn), "The draw count must equal the public face")
        for seat in "BCD":
            self.assertEqual(hands[seat], set(game.state.players[seat].zones["hand"]))
            packet = json.dumps(game.packet(f"pilot:{seat}", full=True))
            self.assertNotIn(private.ref, packet)
            for object_id in drawn:
                self.assertNotIn(game.state.cards[object_id].ref, packet)
        self.assertIsNone(effective_maximum_hand_size(game.engine, "A"))
        self.assertEqual(7, effective_maximum_hand_size(game.engine, "B"))

        self.cast_for(game, "C", bounce, [source.ref])
        self.assertIsNone(self.resolve(game))
        self.assertEqual("hand", game.state.cards[source.object_id].zone)
        hand_count = len(game.state.players["A"].zones["hand"])
        self.assertGreater(hand_count, 7)
        self.until(game, lambda current: current.state.active_player != "A"
                   or current.state.pending_decision.kind == "cleanup.discard")
        self.assertNotEqual("A", game.state.active_player, "Cleanup must not request a discard")
        self.assertEqual(hand_count, len(game.state.players["A"].zones["hand"]))
        game = self.replay(game, load=True)
        self.assertIsNone(effective_maximum_hand_size(game.engine, "A"))

    def test_real_draw_replacement_choice_reloads_with_one_roll_and_a_later_grant(self):
        game, source, private = self.draw_fixture(706002)
        replacement = self.add(game.engine, "Chains of Mephistopheles", seat="B")
        foreign = self.add(game.engine, "Island", seat="C", zone="hand")
        self.assert_closed(game, replacement)
        source = self.cast_fixture(game, source)
        trigger = self.attack_for_trigger(game, source)
        self.assertEqual("draw.discard", self.resolve(game).kind)
        face = self.assert_public_roll(game, trigger)
        self.assertEqual(7, effective_maximum_hand_size(game.engine, "A"))
        self.assertIsNone(game.packet("pilot:C", full=True)["decision"])
        self.assertNotIn(private.ref, json.dumps(game.packet("pilot:C", full=True)))
        before = authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:C", {"action_id": "choose", "card": private.ref}).ok)
        self.assertFalse(game.act("pilot:A", {"action_id": "choose", "card": foreign.ref}).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        game = self.replay(game, load=True)
        self.assertEqual(face, self.assert_public_roll(game, trigger))

        for index in range(face):
            decision = game.packet("pilot:A", full=True)["decision"]
            self.assertEqual("draw.discard", decision["kind"])
            card = game.state.cards[game.state.players["A"].zones["hand"][0]]
            capability = game.engine.permissions.capability_for("pilot:A")
            choice = game.act("pilot:A", {"action_id": "choose", "card": card.ref})
            self.assertTrue(choice.ok, choice.summary)
            if index == 0:
                before = authoritative_state_hash(game.state)
                with self.assertRaises(PermissionDenied):
                    game.engine.submit(
                        principal="pilot:A", token=capability.token,
                        action="choose", payload={"card": card.ref},
                    )
                self.assertEqual(before, authoritative_state_hash(game.state))
        self.assertFalse(game.state.stack)
        self.assertEqual(face, self.assert_public_roll(game, trigger))
        self.assertIsNone(effective_maximum_hand_size(game.engine, "A"))
        self.replay(game, load=True)

    def test_unsupported_dice_paragraphs_and_consumers_remain_residual(self):
        record = self.db.lookup(self.source_name, fuzzy=False)
        text = record.oracle_text
        for changed in (
            text.replace("a d20", "two d20s"),
            text.replace("a d20", "a d6"),
            text.replace("the result", "the natural result"),
            text.replace("rest of the game", "turn"),
            text.replace("combat damage to a player", "damage to a player"),
            text.replace("combat damage to a player", "combat damage to a planeswalker"),
            text + " Roll again.",
            "If you would roll one or more dice, instead roll one additional die and ignore the lowest roll.",
            "Whenever you roll one or more dice, draw a card.",
        ):
            with self.subTest(text=changed):
                self.assertNotEqual("exact", compile_oracle_card(
                    replace(record, oracle_text=changed),
                    capability_registry=self.registry, capability_profile=PROFILE,
                ).status)

    def test_runtime_authority_requires_both_ordered_effects_and_the_complete_event(self):
        from quorune.rules.die_roll_draw import CAPABILITY, MECHANIC, ROLL_FIELD
        from quorune.game_hand_size_model import CAPABILITY as HAND_SIZE_CAPABILITY

        game, source, _ = self.draw_fixture(706003)
        program, = (program for program in game.engine.semantics.programs_for_oracle(source.oracle_id)
                    if MECHANIC in program.coverage)
        def bind(candidate):
            return bind_semantic_program_runtime(
                candidate, capability_registry=self.registry, profile=PROFILE,
            )

        self.assertTrue(bind(program)["strict"])
        for capability in (CAPABILITY, HAND_SIZE_CAPABILITY):
            missing = [name for name in program.capability_dependencies if name != capability]
            candidate = replace(
                program, capability_dependencies=missing,
                capability_closure=self.registry.closure(missing, profile=PROFILE).to_dict(),
            )
            self.assertIn(f"capability:undeclared_runtime_dependency:{capability}", bind(candidate)["blockers"])
        invalid = (
            replace(program, event_condition=None),
            replace(program, effects=tuple(reversed(program.effects))),
            replace(program, effects=({**program.effects[0], ROLL_FIELD: True}, program.effects[1])),
            replace(program, effects=({**program.effects[0], "player": "B"}, program.effects[1])),
        )
        for candidate in invalid:
            self.assertFalse(bind(candidate)["strict"])

    def test_roll_amount_and_game_duration_owner_omissions_are_detected(self):
        from quorune.rules import die_roll_draw

        original = die_roll_draw.bind_die_draw

        def wrong_count(host, item, effect, *, instruction_pointer):
            draw = original(host, item, effect, instruction_pointer=instruction_pointer)
            return {**draw, "count": 0}

        with patch.object(die_roll_draw, "bind_die_draw", side_effect=wrong_count):
            with self.assertRaisesRegex(AssertionError, "draw count must equal the public face"):
                self.test_unmodified_roll_draws_for_locked_controller_and_cleanup_survives_departure()

        with patch("quorune.semantic_runtime.game_hand_size.commit_continuous_effect", return_value=None):
            with self.assertRaisesRegex(AssertionError, "is not None"):
                self.test_unmodified_roll_draws_for_locked_controller_and_cleanup_survives_departure()
