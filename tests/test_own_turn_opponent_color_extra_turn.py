"""Native own-turn casting and sealed opponent spell colors for an extra turn."""

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
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


ORIGINAL = "Seedtime"
COLOR_FIELD = "opponent_cast_color"


class OwnTurnOpponentColorExtraTurnTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "opponent-color-extra-turn.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures/own-turn-opponent-color-extra-turn-cards.json"], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Seedtime witness", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"), DeckEntry("Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def source_programs(self):
        card = self.db.lookup(ORIGINAL, fuzzy=False)
        binding, semantics = register_card(self.db, self.registry, ORIGINAL)
        self.assertTrue(binding["strict_capability_ready"], binding["blockers"])
        programs = {program.event: program for program in semantics.programs_for_oracle(card.oracle_id)}
        self.assertEqual({"cast.restriction", "resolve"}, set(programs))
        return card, programs

    def test_entire_original_has_an_intrinsic_cast_restriction_and_one_closed_resolution(self):
        card, programs = self.source_programs()
        restriction, = programs["cast.restriction"].handlers
        self.assertEqual(3, restriction["intrinsic_condition_version"])
        self.assertEqual("controller_turn", restriction["source_condition"]["kind"])
        result, = programs["resolve"].effects
        self.assertEqual(("extra_turn", "$controller", "$source", 1, "U"), (
            result["op"], result["player"], result["source"], result["spell_color_history_version"],
            result[COLOR_FIELD],
        ))
        for text in (
            card.oracle_text.replace("during your turn", "during an opponent's turn"),
            card.oracle_text.replace("an opponent cast", "you cast"),
            card.oracle_text.replace("this turn", "last turn"),
            card.oracle_text.replace("a blue spell", "a blue or red spell"),
            card.oracle_text + " Rewrite the game history.",
        ):
            self.assertNotEqual("exact", compile_oracle_card(replace(card, oracle_text=text),
                                capability_registry=self.registry).status, text)

    def test_raw_resolving_shape_rejects_unauthorized_player_and_unsealed_condition(self):
        _, programs = self.source_programs()
        program = programs["resolve"]
        effect, = program.effects
        for changed in (
            replace(program, event="activate"),
            replace(program, effects=({**effect, "player": "B"},)),
            replace(program, effects=({**effect, COLOR_FIELD: "multicolor"},)),
            replace(program, effects=({**effect, "spell_color_history_version": True},)),
            replace(program, effects=({key: value for key, value in effect.items()
                                       if key != "spell_color_history_version"},)),
        ):
            bound = bind_semantic_program_runtime(changed, capability_registry=self.registry,
                                                  profile="commander_review")
            self.assertFalse(bound["strict"], bound)

    def world(self, seed, *, active="A", a_mana=None, b_mana=None, c_mana=None, options=()):
        game = self.session(seed)
        engine = game.engine
        spell = self.add(engine, ORIGINAL, zone="hand")
        named = {name: self.add(engine, name, seat=seat, zone="hand") for name, seat in options}
        forests = [self.add(engine, "Forest", ref=f"seedtime-forest-{index}") for index in range(2)]
        for card in (spell, *named.values()):
            self.assert_closed(game, card)
        engine.permissions.invalidate_current()
        game.state.pending_decision = None
        game.state.active_player = active
        game.state.started = True
        game.state.phase = "precombat_main"
        game.state.step = "main"
        game.state.phase_index = 3
        for seat, mana in (("A", a_mana), ("B", b_mana), ("C", c_mana)):
            game.state.players[seat].mana_pool.update(mana or {})
        engine._grant_priority("A")
        engine.pump()
        self.checkpoint(game)
        return game, spell, named, forests

    def offered(self, game, spell):
        self.priority(game, "A")
        decision = game.packet("pilot:A", full=True)["decision"]
        return any(row["id"] == "cast:" + spell.ref for row in decision["ctx"]["legal"]["actions"])

    def test_normal_offer_and_command_require_the_current_turn_to_belong_to_the_caster(self):
        game, spell, _, _ = self.world(61342301, active="B", a_mana={"G": 1, "C": 1})
        self.assertFalse(self.offered(game, spell))
        digest = authoritative_state_hash(game.state)
        result = game.act("pilot:A", {"action_id": "cast:" + spell.ref, "pay": "auto"})
        self.assertFalse(result.ok)
        self.assertEqual(digest, authoritative_state_hash(game.state))
        self.assertEqual("hand", game.state.cards[spell.object_id].zone)
        self.replay(game, load=True)

        game, spell, _, _ = self.world(61342302, a_mana={"G": 1, "C": 1})
        self.assertTrue(self.offered(game, spell), "The offer must not depend on a prior blue spell")
        self.cast_for(game, "A", spell, [])
        self.assertIsNone(self.resolve(game))
        self.assertFalse(game.state.extra_turns)
        self.assertEqual("graveyard", game.state.cards[spell.object_id].zone)
        self.replay(game, load=True)

    def test_actual_countered_blue_response_still_earns_the_current_extra_turn(self):
        blue = "Reach Through Mists"
        counter = "Lapse of Certainty"
        game, spell, cards, _ = self.world(
            61342303, a_mana={"G": 1, "C": 1}, b_mana={"U": 1}, c_mana={"C": 2, "W": 1},
            options=((blue, "B"), (counter, "C")),
        )
        self.assertTrue(self.offered(game, spell))
        self.cast_for(game, "A", spell, [])
        self.cast_for(game, "B", cards[blue], [])
        response = game.state.stack[-1].ref
        self.cast_for(game, "C", cards[counter], [response])
        self.resolve_top(game)
        self.assertNotIn(response, [item.ref for item in game.state.stack])
        self.assertEqual("library", game.state.cards[cards[blue].object_id].zone)
        self.assertFalse(game.state.extra_turns)
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        self.assertEqual(["A"], [turn.player for turn in game.state.extra_turns],
                         "A blue spell cast in response still qualifies when countered")
        turn, = game.state.extra_turns
        game = self.replay(game, load=True)
        self.until(game, lambda trial: trial.state.current_turn is not None and trial.state.current_turn.turn_id == turn.turn_id)
        self.assertTrue(game.state.current_turn.extra)
        self.assertEqual("A", game.state.active_player)
        self.replay(game, load=True)

    def test_own_blue_cast_and_opposing_blue_copy_do_not_count_as_opponent_blue_casts(self):
        blue = "Reach Through Mists"
        copier = "Reverberate"
        game, spell, cards, _ = self.world(
            61342304, a_mana={"G": 1, "C": 1, "U": 1}, b_mana={"R": 2},
            options=((blue, "A"), (copier, "B")),
        )
        self.cast_for(game, "A", cards[blue], [])
        blue_stack = game.state.stack[-1].ref
        self.cast_for(game, "B", cards[copier], [blue_stack])
        self.resolve_top(game)
        copied = next(item for item in game.state.stack if item.kind == "spell_copy")
        self.assertEqual("B", copied.controller)
        self.assertIsNone(self.resolve(game))
        self.assertTrue(self.offered(game, spell))
        self.cast_for(game, "A", spell, [])
        self.assertIsNone(self.resolve(game))
        self.assertFalse(game.state.extra_turns)
        self.replay(game, load=True)

    def test_copy_resolves_for_its_own_controller_during_the_original_casters_turn(self):
        blue = "Reach Through Mists"
        copier = "Reverberate"
        game, spell, cards, _ = self.world(
            61342306, a_mana={"G": 1, "C": 1, "U": 1}, b_mana={"R": 2},
            options=((blue, "A"), (copier, "B")),
        )
        self.cast_for(game, "A", cards[blue], [])
        self.resolve_top(game)
        self.cast_for(game, "A", spell, [])
        source = game.state.stack[-1].ref
        self.cast_for(game, "B", cards[copier], [source])
        self.resolve_top(game)
        self.assertEqual("B", next(item for item in game.state.stack if item.kind == "spell_copy").controller)
        self.assertIsNone(self.resolve(game))
        self.assertEqual(["B"], [turn.player for turn in game.state.extra_turns])
        self.assertEqual("A", game.state.active_player)
        self.replay(game, load=True)

    def test_actual_blue_casts_in_the_old_turn_do_not_qualify_in_the_new_extra_turn(self):
        warp = "Time Warp"
        game, spell, cards, _ = self.world(
            61342305, active="B", b_mana={"U": 2, "C": 3}, options=((warp, "B"),),
        )
        self.cast_for(game, "B", cards[warp], ["A"])
        self.assertIsNone(self.resolve(game))
        prior_cast = next(event for event in reversed(game.state.events) if event.code == "stack.cast")
        self.assertEqual("B", prior_cast.actor)
        self.assertIn("U", prior_cast.details["colors"])
        turn, = game.state.extra_turns
        self.until(game, lambda trial: trial.state.current_turn is not None
                   and trial.state.current_turn.turn_id == turn.turn_id and trial.state.step == "main")
        self.assertTrue(self.offered(game, spell))
        self.cast_for(game, "A", spell, [])
        self.assertIsNone(self.resolve(game))
        self.assertFalse(game.state.extra_turns)
        self.replay(game, load=True)

    def test_omitting_the_opposing_cast_history_fails_the_real_countered_response(self):
        with patch("quorune.public_history_query_model.current_opponent_spell_colors", return_value=frozenset()):
            with self.assertRaisesRegex(AssertionError, "A blue spell cast in response still qualifies when countered"):
                self.test_actual_countered_blue_response_still_earns_the_current_extra_turn()
