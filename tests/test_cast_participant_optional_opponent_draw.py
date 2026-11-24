"""Witness the optional draws of a real noncreature spell caster's opponents."""

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


HEARTWOOD = "Heartwood Storyteller"


class CastParticipantOptionalOpponentDrawTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "cast-opponent-optional-draw.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures/cast-participant-optional-opponent-draw-cards.json"], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Cast opponent optional draws", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"), DeckEntry("Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def test_complete_heartwood_keeps_the_untargeted_noncreature_cast_and_original_caster(self):
        from quorune.rules.event_outcome_shapes import noncreature_cast_condition

        card = self.db.lookup(HEARTWOOD, fuzzy=False)
        binding, semantics = register_card(self.db, self.registry, HEARTWOOD)
        self.assertTrue(binding["strict_capability_ready"], binding["blockers"])
        trigger, = [item for item in semantics.programs_for_oracle(card.oracle_id) if item.event == "spell.cast"]
        self.assertEqual(("battlefield", None, noncreature_cast_condition()), (
            trigger.active_zone, trigger.target_schema, trigger.event_condition,
        ))
        effect, = trigger.effects
        self.assertEqual(("offer_draw", 4, "$context.player", 1, True), (
            effect["op"], effect["event_optional_draw_version"], effect["cast_participant"],
            effect["count"], effect["private"],
        ))
        for text in (
            card.oracle_text.replace("a noncreature spell", "an artifact spell"),
            card.oracle_text.replace("that player's opponents", "your opponents"),
            card.oracle_text.replace("a card.", "two cards."),
            card.oracle_text + " Create a 2/2 green Bear creature token.",
        ):
            self.assertNotEqual("exact", compile_oracle_card(
                replace(card, oracle_text=text), capability_registry=self.registry,
            ).status, text)

    def test_raw_payload_cannot_select_recipients_or_start_with_a_chosen_draw(self):
        card = self.db.lookup(HEARTWOOD, fuzzy=False)
        _, semantics = register_card(self.db, self.registry, HEARTWOOD)
        trigger, = [item for item in semantics.programs_for_oracle(card.oracle_id) if item.event == "spell.cast"]
        effect, = trigger.effects
        for changed in (
            replace(trigger, event_condition=None),
            replace(trigger, effects=({**effect, "cast_participant": "$controller"},)),
            replace(trigger, effects=({**effect, "player": "C"},)),
            replace(trigger, effects=({**effect, "cast_draw_choices": ["draw"]},)),
            replace(trigger, effects=(effect, {"op": "draw", "player": "$controller", "count": 1})),
        ):
            with self.subTest(effects=changed.effects, condition=changed.event_condition):
                binding = bind_semantic_program_runtime(
                    changed, capability_registry=self.registry, profile="commander_review",
                )
                self.assertFalse(binding["strict"], binding)

    def hands(self, game):
        return {seat: len(player.zones["hand"]) for seat, player in game.state.players.items()}

    def choice(self, game, seat, previous):
        decision = game.state.pending_decision
        self.assertEqual(("semantic.choice", [seat]), (decision.kind, decision.actors),
                         "Only the next opponent may choose")
        context = game.packet("pilot:" + seat, full=True)["decision"]["ctx"]
        self.assertEqual(previous, context["previous_choices"])
        self.assertIsNone(game.packet("spectator", full=True)["decision"])

    def choose(self, game, seat, option):
        result = game.act("pilot:" + seat, {"action_id": "choose", "choice": option})
        self.assertTrue(result.ok, result.summary)

    def activate(self, game, seat, source, target):
        self.priority(game, seat)
        actions = game.packet("pilot:" + seat, full=True)["decision"]["ctx"]["legal"]["actions"]
        action, = [row["id"] for row in actions if row["id"].startswith("activate:" + source.ref + ":")]
        result = game.act("pilot:" + seat, {"action_id": action, "targets": [target], "pay": "auto"})
        self.assertTrue(result.ok, result.summary)

    def test_actual_choices_precede_draws_in_order_after_source_removal_and_spell_counter(self):
        game = self.session(61341001)
        engine = game.engine
        source = self.add(engine, HEARTWOOD, zone="hand")
        creature = self.add(engine, "Ambush Viper", seat="B", zone="hand")
        spell = self.add(engine, "Opt", seat="B", zone="hand")
        seal = self.add(engine, "Seal of Removal", seat="C")
        counter = self.add(engine, "Counterspell", seat="D", zone="hand")
        private = self.add(engine, "Grizzly Bears", seat="D", zone="library", ref="heartwood-private-draw")
        self.add(engine, "Island", seat="B", ref="heartwood-caster-blue")
        self.add(engine, "Forest", seat="B", ref="heartwood-caster-green")
        self.add(engine, "Island", seat="B", ref="heartwood-caster-spare-blue")
        self.add(engine, "Island", seat="D", ref="heartwood-counter-blue-1")
        self.add(engine, "Island", seat="D", ref="heartwood-counter-blue-2")
        for card in (source, creature, spell, seal, counter):
            self.assert_closed(game, card)
        self.prepare(game, mana={"C": 1, "G": 2})
        self.checkpoint(game)
        self.cast_for(game, "A", source)
        self.resolve_top(game)
        self.cast_for(game, "B", creature)
        self.assertEqual(["spell"], [item.kind for item in game.state.stack])
        self.resolve_top(game)
        self.cast_for(game, "B", spell)
        cast, trigger = game.state.stack
        self.assertEqual(source.object_id, trigger.source_object_id)
        self.activate(game, "C", seal, source.ref)
        self.resolve_top(game)
        self.assertEqual("hand", game.state.cards[source.object_id].zone)
        self.cast_for(game, "D", counter, [cast.ref])
        self.resolve_top(game)
        self.assertEqual("graveyard", game.state.cards[spell.object_id].zone)
        self.assertEqual([trigger.ref], [item.ref for item in game.state.stack])

        before = self.hands(game)
        self.resolve(game)
        self.choice(game, "A", [])
        old_hash = authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:B", {"action_id": "choose", "choice": "draw"}).ok)
        self.assertEqual(old_hash, authoritative_state_hash(game.state))
        self.choose(game, "A", "draw")
        self.assertEqual(before, self.hands(game))
        self.choice(game, "C", [{"player": "A", "choice": "draw"}])
        game = self.replay(game, load=True)
        self.choose(game, "C", "decline")
        self.assertEqual(before, self.hands(game))
        self.choice(game, "D", [{"player": "A", "choice": "draw"}, {"player": "C", "choice": "decline"}])
        offset = len(game.state.events)
        self.choose(game, "D", "draw")
        self.assertEqual(["A", "D"], [event.actor for event in game.state.events[offset:] if event.code == "card.draw"])
        self.assertEqual({seat: count + (seat in ("A", "D")) for seat, count in before.items()}, self.hands(game))
        self.assertIn(private.object_id, game.state.players["D"].zones["hand"])
        self.assertIn(private.ref, json.dumps(game.packet("pilot:D", full=True)))
        for viewer in ("pilot:A", "pilot:B", "pilot:C", "spectator"):
            self.assertNotIn(private.ref, json.dumps(game.packet(viewer, full=True)))
        self.replay(game, load=True)

    def test_actual_prohibited_draw_skips_choice_and_empty_library_draw_waits_for_other_chooser(self):
        game = self.session(61341002)
        engine = game.engine
        source = self.add(engine, HEARTWOOD, zone="hand")
        first = self.add(engine, "Reach Through Mists", zone="hand")
        spell = self.add(engine, "Opt", seat="B", zone="hand")
        spirit = self.add(engine, "Spirit of the Labyrinth")
        self.add(engine, "Island", seat="B")
        for card in (source, first, spell, spirit):
            self.assert_closed(game, card)
        for object_id in tuple(game.state.players["C"].zones["library"]):
            engine.move_card(object_id, "graveyard", log=False, reason="Initial empty-library witness")
        self.prepare(game, mana={"C": 1, "G": 2, "U": 1})
        self.checkpoint(game)
        self.cast_for(game, "A", first)
        self.resolve_top(game)
        self.cast_for(game, "A", source)
        self.resolve_top(game)
        self.cast_for(game, "B", spell)
        self.resolve(game)
        self.choice(game, "C", [])
        self.choose(game, "C", "draw")
        self.assertIn("C", game.engine.active_seats)
        self.choice(game, "D", [{"player": "C", "choice": "draw"}])
        before = self.hands(game)
        self.choose(game, "D", "draw")
        self.assertNotIn("C", game.engine.active_seats)
        self.assertEqual(before["A"], self.hands(game)["A"])
        self.assertEqual(before["D"] + 1, self.hands(game)["D"])
        self.replay(game, load=True)

    def test_wrong_caster_mutation_breaks_the_actual_choice_order(self):
        from quorune.semantic_choices import optional_draw

        original = optional_draw._cast_opponents

        def wrong_participant(query, participant):
            if participant == "B":
                return original(query, "A")
            return original(query, participant)

        with patch.object(optional_draw, "_cast_opponents", side_effect=wrong_participant):
            with self.assertRaisesRegex(AssertionError, "Only the next opponent"):
                self.test_actual_choices_precede_draws_in_order_after_source_removal_and_spell_counter()
