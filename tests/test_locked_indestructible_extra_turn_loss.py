"""Lock the indefinite grant to current creature incarnations before one extra turn."""

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
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


CHANCE = "Chance for Glory"


class LockedIndestructibleExtraTurnLossTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "locked-indestructible-extra-turn-loss.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures/locked-indestructible-extra-turn-loss-cards.json"], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Locked Indestructible and extra turn", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"), DeckEntry("Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def test_complete_original_keeps_the_locked_grant_and_independent_extra_turn_loss(self):
        card = self.db.lookup(CHANCE, fuzzy=False)
        binding, semantics = register_card(self.db, self.registry, CHANCE)
        self.assertTrue(binding["strict_capability_ready"], binding["blockers"])
        program, = [row for row in semantics.programs_for_oracle(card.oracle_id) if row.event == "resolve"]
        grant, turn = program.effects
        self.assertEqual({"op": "grant_zone_object_keyword", "schema_version": 2,
                          "scope": "controlled_creature_set", "keyword": "indestructible"}, grant)
        self.assertEqual({"op": "extra_turn", "player": "$controller", "source": "$source",
                          "extra_turn_end_step_loss_version": 1}, turn)
        self.assertIsNone(program.target_schema)
        self.assertLessEqual({"continuous.resolution.fixed_keyword_zone_object", "permanent.indestructible.ordinary",
                              "turn.extra.fixed_after_current", "game.loss.effect"}, set(program.capability_dependencies))
        for text in (
            card.oracle_text.replace("Creatures you control", "Creatures opponents control"),
            card.oracle_text.replace("indestructible.", "flying."),
            card.oracle_text.replace("indestructible.", "indestructible until end of turn."),
            card.oracle_text.replace("that turn's end step", "the next end step"),
            card.oracle_text + " Draw two cards.",
        ):
            self.assertNotEqual("exact", compile_oracle_card(replace(card, oracle_text=text),
                                capability_registry=self.registry).status, text)

    def test_raw_grant_and_delayed_loss_cannot_be_reordered_or_rebound(self):
        card = self.db.lookup(CHANCE, fuzzy=False)
        _, semantics = register_card(self.db, self.registry, CHANCE)
        program, = [row for row in semantics.programs_for_oracle(card.oracle_id) if row.event == "resolve"]
        grant, turn = program.effects
        retained = tuple(key for key in program.capability_dependencies if key != "permanent.indestructible.ordinary")
        for changed in (
            replace(program, effects=(turn, grant)),
            replace(program, effects=({**grant, "card": "$target.0"}, turn)),
            replace(program, effects=({**grant, "scope": "any_creature"}, turn)),
            replace(program, effects=(grant, {**turn, "player": "$target.0"})),
            replace(program, coverage=(), capability_dependencies=retained,
                    capability_closure=self.registry.closure(retained, profile="commander_review").to_dict()),
        ):
            bound = bind_semantic_program_runtime(changed, capability_registry=self.registry,
                                                  profile="commander_review")
            self.assertFalse(bound["strict"], bound)

    def indestructible(self, game, card):
        return "indestructible" in game.engine._combat_keywords(game.state.cards[card.object_id])

    def test_actual_chance_keeps_only_original_creature_incarnations_through_control_and_turn_end(self):
        game = self.session(61341801)
        engine = game.engine
        chance = self.add(engine, CHANCE, zone="hand")
        early = self.add(engine, "Grizzly Bears", ref="chance-original-protected-creature")
        blinked = self.add(engine, "Savannah Lions")
        opponent = self.add(engine, "Llanowar Elves", seat="B")
        later = self.add(engine, "Goblin Piker", zone="hand")
        donate = self.add(engine, "Donate", zone="hand")
        cloudshift = self.add(engine, "Cloudshift", zone="hand")
        wrath = self.add(engine, "Wrath of God", zone="hand")
        stifle = self.add(engine, "Stifle", seat="B", zone="hand")
        self.add(engine, "Island", seat="B")
        for card in (chance, donate, cloudshift, wrath, stifle, later):
            self.assert_closed(game, card)
        self.prepare(game, mana={"C": 16, "U": 8, "W": 12, "R": 8})
        self.checkpoint(game)
        self.cast_for(game, "A", chance)
        self.resolve_top(game)
        turn, = game.state.extra_turns
        self.assertTrue(self.indestructible(game, early), "The original set receives the indefinite grant")
        self.assertTrue(self.indestructible(game, blinked))
        self.assertFalse(self.indestructible(game, opponent))

        self.cast_for(game, "A", donate, ["B", early.ref])
        self.resolve_top(game)
        self.assertEqual("B", game.state.cards[early.object_id].controller)
        self.assertTrue(self.indestructible(game, early))
        identity = game.state.cards[blinked.object_id].logical_object_id
        self.cast_for(game, "A", cloudshift, [blinked.ref])
        self.resolve_top(game)
        self.assertNotEqual(identity, game.state.cards[blinked.object_id].logical_object_id)
        self.assertFalse(self.indestructible(game, blinked))
        self.cast_for(game, "A", later)
        self.resolve_top(game)
        self.assertFalse(self.indestructible(game, later))

        self.cast_for(game, "A", wrath)
        self.resolve_top(game)
        self.assertEqual("battlefield", game.state.cards[early.object_id].zone)
        self.assertTrue(all(game.state.cards[card.object_id].zone == "graveyard"
                            for card in (blinked, later, opponent)))

        self.until(game, lambda current: current.state.current_turn is not None
                   and current.state.current_turn.turn_id == turn.turn_id
                   and current.state.step == "end_step"
                   and current.state.pending_decision.kind == "priority")
        loss, = game.state.stack
        self.assertEqual([{"op": "lose_game", "player": "A"}], loss.context["dynamic_effects"])
        game = self.replay(game, load=True)
        self.cast_for(game, "B", stifle, [loss.ref])
        self.resolve_top(game)
        self.assertIn("A", game.engine.active_seats)
        self.assertTrue(self.indestructible(game, early))
        self.replay(game, load=True)

    def test_omitting_the_locked_grant_is_killed_by_the_original_paid_witness(self):
        with patch("quorune.zone_object_keyword_grants.commit_controlled_creature_indestructible", return_value=()):
            with self.assertRaisesRegex(AssertionError, "The original set receives"):
                self.test_actual_chance_keeps_only_original_creature_incarnations_through_control_and_turn_end()
