"""Exercise one source and its resolving controller's complete graveyard."""

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


ORIGINAL = "Elixir of Immortality"
RECYCLING = "recycle_graveyards_into_libraries"


class SourceAndControllerGraveyardRecyclingTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "artifact-and-graveyard-recycling.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures/source-and-controller-graveyard-recycling-cards.json"], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Elixir recycling witness", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"), DeckEntry("Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def source_program(self):
        card = self.db.lookup(ORIGINAL, fuzzy=False)
        binding, semantics = register_card(self.db, self.registry, ORIGINAL)
        self.assertTrue(binding["strict_capability_ready"], binding["blockers"])
        program, = [item for item in semantics.programs_for_oracle(card.oracle_id) if item.event == "activate"]
        return card, program

    def test_original_registers_the_complete_paid_activation_and_ordered_result(self):
        card, program = self.source_program()
        self.assertIsNone(program.target_schema)
        self.assertTrue(program.cost_schema["tap_source"])
        life, recycling = program.effects
        self.assertEqual(("life", "$controller", 5), (life["op"], life["player"], life["delta"]))
        self.assertEqual((RECYCLING, "$controller", {"scope": "controller", "schema_version": 3,
                                                   "include_source": True}),
                         (recycling["op"], recycling["player"], recycling["recycling"]))
        for text in (
            card.oracle_text.replace("your graveyard", "target player's graveyard"),
            card.oracle_text.replace("their owner's library", "your library"),
            card.oracle_text.replace("Shuffle this artifact", "Shuffle target artifact"),
            card.oracle_text + " Rewrite the game history.",
        ):
            self.assertNotEqual("exact", compile_oracle_card(replace(card, oracle_text=text),
                                capability_registry=self.registry).status, text)

    def test_raw_recycling_scope_cannot_exchange_player_roles_or_drop_the_life_result(self):
        _, program = self.source_program()
        life, recycling = program.effects
        with self.assertRaisesRegex(ValueError, "Runtime handler event must match"):
            replace(program, event="resolve")
        for changed in (
            replace(program, effects=(recycling,)),
            replace(program, effects=(recycling, life)),
            replace(program, effects=(life, {**recycling, "player": "$target.0"})),
            replace(program, effects=(life, {**recycling, "recycling": {
                **recycling["recycling"], "scope": "all_players",
            }})),
            replace(program, effects=(life, {**recycling, "recycling": {
                **recycling["recycling"], "include_source": False,
            }})),
        ):
            bound = bind_semantic_program_runtime(changed, capability_registry=self.registry,
                                                  profile="commander_review")
            self.assertFalse(bound["strict"], bound)

    def scene(self, seed, *, donate=False, removal=False, repeat=False):
        game = self.session(seed)
        engine = game.engine
        source = self.add(engine, ORIGINAL)
        gift = self.add(engine, "Donate", zone="hand") if donate else None
        disenchant = self.add(engine, "Disenchant", seat="D", zone="hand") if removal else None
        twiddle = self.add(engine, "Twiddle", zone="hand") if repeat else None
        a_grave = self.add(engine, "Grizzly Bears", zone="graveyard", ref="elixir-owner-graveyard")
        b_grave = self.add(engine, "Savannah Lions", seat="B", zone="graveyard", ref="elixir-caster-graveyard")
        for card in (source, gift, disenchant, twiddle):
            if card:
                self.assert_closed(game, card)
        self.prepare(game, mana={"C": 4, "U": 1})
        game.state.players["B"].mana_pool["C"] = 2
        game.state.players["D"].mana_pool.update({"W": 1, "C": 1})
        self.checkpoint(game)
        if gift:
            self.cast_for(game, "A", gift, ["B", source.ref])
            self.resolve_top(game)
            self.assertEqual(("A", "B"), (game.state.cards[source.object_id].owner,
                                           game.state.cards[source.object_id].controller))
        return game, source, a_grave, b_grave, disenchant, twiddle

    def activate(self, game, source, seat):
        self.priority(game, seat)
        rows = game.packet("pilot:" + seat, full=True)["decision"]["ctx"]["legal"]["actions"]
        action, = [row["id"] for row in rows if row["id"].startswith("activate:" + source.ref + ":")]
        outcome = game.act("pilot:" + seat, {"action_id": action, "pay": "auto"})
        self.assertTrue(outcome.ok, outcome.summary)
        self.assertEqual(("battlefield", True), (game.state.cards[source.object_id].zone,
                                                 game.state.cards[source.object_id].tapped))

    def shuffle_counts(self, game):
        return {seat: game.state.players[seat].stats.get("shuffle_count", 0) for seat in "ABCD"}

    def test_actual_donated_elixir_recycles_its_own_card_and_the_resolving_players_graveyard(self):
        game, source, a_grave, b_grave, _, _ = self.scene(61343001, donate=True)
        before = self.shuffle_counts(game)
        life = game.state.players["B"].life
        self.activate(game, source, "B")
        self.assertEqual(0, game.state.players["B"].mana_pool["C"])
        game = self.replay(game, load=True)
        mark = game.state.event_sequence
        self.assertIsNone(self.resolve(game))
        self.assertEqual(life + 5, game.state.players["B"].life)
        self.assertIn(source.object_id, game.state.players["A"].zones["library"],
                      "The original donated artifact must go to its owner's library")
        self.assertIn(b_grave.object_id, game.state.players["B"].zones["library"])
        self.assertIn(a_grave.object_id, game.state.players["A"].zones["graveyard"])
        self.assertEqual({**before, "A": before["A"] + 1, "B": before["B"] + 1}, self.shuffle_counts(game))
        shuffles = [event for event in game.state.events if event.event_id > mark and event.code == "library.shuffle"]
        self.assertEqual(["B", "B"], [event.actor for event in shuffles])
        self.assertEqual({"A", "B"}, {player for event in shuffles for player in event.changed_players})
        self.replay(game, load=True)

    def test_actual_source_removal_preserves_current_graveyard_inclusion_and_owner_boundaries(self):
        for index, stolen in enumerate((False, True)):
            with self.subTest(stolen=stolen):
                game, source, a_grave, b_grave, removal, _ = self.scene(61343002 + index, donate=stolen, removal=True)
                actor = "B" if stolen else "A"
                life = game.state.players[actor].life
                before = self.shuffle_counts(game)
                self.activate(game, source, actor)
                self.cast_for(game, "D", removal, [source.ref])
                self.resolve_top(game)
                self.assertEqual("graveyard", game.state.cards[source.object_id].zone)
                game = self.replay(game, load=True)
                self.assertIsNone(self.resolve(game))
                self.assertEqual(life + 5, game.state.players[actor].life)
                expected = "graveyard" if stolen else "library"
                self.assertEqual(expected, game.state.cards[source.object_id].zone)
                self.assertEqual(expected, game.state.cards[a_grave.object_id].zone)
                self.assertEqual("library" if stolen else "graveyard", game.state.cards[b_grave.object_id].zone)
                self.assertEqual({**before, actor: before[actor] + 1}, self.shuffle_counts(game))
                self.replay(game, load=True)

    def test_second_actual_activation_resolves_after_the_source_leaves_and_still_shuffles(self):
        game, source, a_grave, _, _, twiddle = self.scene(61343004, repeat=True)
        life = game.state.players["A"].life
        shuffles = self.shuffle_counts(game)["A"]
        self.activate(game, source, "A")
        self.cast_for(game, "A", twiddle, [source.ref])
        decision = self.resolve(game)
        self.assertEqual("semantic.choice", decision.kind)
        result = game.act("pilot:A", {"action_id": "choose", "choice": "untap"})
        self.assertTrue(result.ok, result.summary)
        self.assertFalse(game.state.cards[source.object_id].tapped)
        self.activate(game, source, "A")
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        self.assertEqual(life + 10, game.state.players["A"].life)
        self.assertEqual(shuffles + 2, self.shuffle_counts(game)["A"])
        self.assertTrue(all(game.state.cards[card.object_id].zone == "library"
                            for card in (source, a_grave, twiddle)))
        self.replay(game, load=True)

    def test_omitting_the_source_recycling_group_fails_the_actual_stolen_artifact_result(self):
        with patch("quorune.rules.graveyard_recycling._present_recycling_source", return_value=None):
            with self.assertRaisesRegex(AssertionError, "The original donated artifact must go"):
                self.test_actual_donated_elixir_recycles_its_own_card_and_the_resolving_players_graveyard()
