"""Exercise optional target resolution and the ordered effects of a fixed land allowance."""

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
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.land_play_permissions import (
    TURN_LAND_PLAY_ALLOWANCE_CAPABILITY,
    additional_land_plays_this_turn,
)
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card
import test_fixed_one_counter_movement as movement_fixture


PROFILE = "commander_review"


class OrderedTurnLandAllowanceTests(InheritedNativeFixture, unittest.TestCase):
    reject = movement_fixture.FixedOneCounterMovementTests.reject
    ready = movement_fixture.FixedOneCounterMovementTests.ready
    lands = movement_fixture.FixedOneCounterMovementTests.lands

    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "ordered-turn-land-allowance.sqlite3"
        fixtures = ROOT / "tests/fixtures"
        build_fixture_database([
            fixtures / "fixed-one-counter-movement-cards.json",
            fixtures / "ordered-turn-land-allowance-cards.json",
        ], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        commander = "Isamaru, Hound of Konda"
        cls.deck = DeckDefinition("Ordered turn land allowance", [
            DeckEntry(commander, 1, "commander"), DeckEntry("Plains", 30),
        ], [commander])

    def test_scale_the_heights_binds_all_four_ordered_owners_and_rejects_unowned_linkage(self):
        card = self.db.lookup("Scale the Heights", fuzzy=False)

        def ir(record):
            return compile_oracle_card(record, capability_registry=self.registry, capability_profile=PROFILE)

        self.assertEqual("exact", ir(card).status)
        result, semantics = register_card(self.db, self.registry, "Scale the Heights")
        self.assertTrue(result["strict_capability_ready"], result["blockers"])
        program, = semantics.programs_for_oracle(card.oracle_id)
        self.assertEqual({"zones": ["battlefield"], "categories": ["permanent"],
                          "up_to": 1, "types_any": ["creature"]}, program.target_schema)
        self.assertEqual(["place_counters_on_targets", "life", "grant_cast_permission", "draw"],
                         [effect["op"] for effect in program.effects])
        self.assertIn(TURN_LAND_PLAY_ALLOWANCE_CAPABILITY, program.capability_dependencies)
        dependencies = tuple(cap for cap in program.capability_dependencies if cap != TURN_LAND_PLAY_ALLOWANCE_CAPABILITY)
        corrupted = [
            replace(program, capability_dependencies=dependencies,
                    capability_closure=self.registry.closure(dependencies, profile=PROFILE).to_dict()),
            replace(program, effects=[*program.effects[:2],
                {**program.effects[2], "player": "$target.0"}, *program.effects[3:]]),
        ]
        for changed in corrupted:
            self.assertFalse(bind_semantic_program_runtime(
                changed, capability_registry=self.registry, profile=PROFILE,
            )["strict"])
        for unsupported in (
            card.oracle_text.replace("this turn.", "on each of your turns."),
            card.oracle_text.replace("You gain 2 life.", "You may gain 2 life."),
            card.oracle_text.replace("You may play", "If you do, you may play"),
        ):
            with self.subTest(unsupported=unsupported):
                self.assertNotEqual("exact", ir(replace(card, oracle_text=unsupported)).status)
        with patch("quorune.compiler.closed_effect_programs._has_unowned_linkage", return_value=True):
            self.assertNotEqual("exact", ir(card).status)

    def scene(self, seed, *, stale=False):
        game = self.session(seed)
        for player in game.state.players.values():
            for object_id in tuple(player.zones["hand"]):
                game.engine.move_card(object_id, "library", log=False)
        source = self.add(game.engine, "Scale the Heights", zone="hand")
        self.lands(game, "Forest", 3)
        target = self.add(game.engine, "Grizzly Bears", seat="B", ref="scale-other-creature")
        draw = self.add(game.engine, "Forest", zone="library", ref="scale-private-draw")
        lands = [self.add(game.engine, "Plains", zone="hand", ref=f"scale-hand-land-{index}") for index in range(3)]
        known_programs = [source]
        bounce = None
        if stale:
            bounce = self.add(game.engine, "Unsummon", zone="hand", seat="B")
            self.lands(game, "Island", 1, seat="B")
            known_programs.append(bounce)
        self.ready(game, *known_programs)
        return game, source, target, draw, lands, bounce

    def test_actual_selected_creature_gets_its_counter_before_the_independent_results_and_replays(self):
        game, source, target, draw, lands, _ = self.scene(78205001)
        self.reject(game, "A", {"action_id": f"cast:{source.ref}", "targets": [lands[0].ref], "pay": "auto"})
        life_before = game.state.players["A"].life
        self.cast_for(game, "A", source, [target.ref])
        game = self.replay(game, load=True)
        offset = len(game.state.events)
        self.resolve_top(game)
        player = game.state.players["A"]
        self.assertEqual({"+1/+1": 1}, game.state.cards[target.object_id].counters)
        self.assertEqual(life_before + 2, player.life)
        self.assertEqual(1, additional_land_plays_this_turn(player.stats, game.state.turn_sequence))
        self.assertIn(draw.object_id, player.zones["hand"])
        codes = {"counter.add", "permission.additional_land_plays", "card.draw"}
        self.assertEqual(["counter.add", "permission.additional_land_plays", "card.draw"],
                         [event.code for event in game.state.events[offset:] if event.code in codes])
        self.assertNotIn(draw.ref, json.dumps(game.packet("pilot:B", full=True)))
        self.replay(game, load=True)

    def test_actual_zero_targets_allow_a_second_land_only_after_resolution_and_during_the_turn(self):
        game, source, target, draw, lands, _ = self.scene(78205002)
        life_before = game.state.players["A"].life
        current_turn = game.state.turn_sequence
        self.cast_for(game, "A", source)
        self.reject(game, "A", {"action_id": "play-land:" + lands[0].ref})
        self.resolve_top(game)

        player = game.state.players["A"]
        self.assertFalse(game.state.cards[target.object_id].counters)
        self.assertEqual(life_before + 2, player.life)
        self.assertEqual(1, additional_land_plays_this_turn(player.stats, current_turn))
        self.assertIn(draw.object_id, player.zones["hand"])
        self.priority(game, "A")
        for land in lands[:2]:
            played = game.act("pilot:A", {"action_id": "play-land:" + land.ref})
            self.assertTrue(played.ok, played.summary)
        self.reject(game, "A", {"action_id": "play-land:" + lands[2].ref})

        for _ in range(72):
            if game.state.turn_sequence > current_turn:
                break
            decision = game.state.pending_decision
            if decision.kind == "priority":
                command = {"action_id": "pass"}
            elif decision.kind == "combat.attackers":
                command = {"action_id": "attack", "attackers": {}}
            elif decision.kind == "combat.blockers":
                command = {"action_id": "block", "blocks": {}}
            else:
                self.fail(f"Unexpected turn transition decision {decision.kind}")
            principal, = game.pending_principals()
            accepted = game.act(principal, command)
            self.assertTrue(accepted.ok, accepted.summary)
        else:
            self.fail("The ordinary turn change did not occur")
        self.assertEqual(0, additional_land_plays_this_turn(game.state.players["A"].stats, game.state.turn_sequence))
        self.replay(game, load=True)

    def test_actual_chosen_creature_departure_fizzles_counter_life_allowance_and_draw(self):
        game, source, target, draw, _, bounce = self.scene(78205003, stale=True)
        player = game.state.players["A"]
        before = player.life, tuple(player.zones["library"])
        self.cast_for(game, "A", source, [target.ref])
        self.cast_for(game, "B", bounce, [target.ref])
        self.resolve_top(game)
        self.assertEqual("hand", game.state.cards[target.object_id].zone)
        game = self.replay(game, load=True)
        self.resolve_top(game)
        player = game.state.players["A"]
        self.assertEqual(before, (player.life, tuple(player.zones["library"])))
        self.assertIn(draw.object_id, player.zones["library"])
        self.assertFalse(game.state.cards[target.object_id].counters)
        self.assertEqual(0, additional_land_plays_this_turn(player.stats, game.state.turn_sequence))
        self.assertEqual("graveyard", game.state.cards[source.object_id].zone)
        self.replay(game, load=True)
