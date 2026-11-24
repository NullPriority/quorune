from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database


class PriorityConcessionStabilizationTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "priority-concession.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures/priority-concession-stabilization-cards.json"], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        commander = "Isamaru, Hound of Konda"
        cls.deck = DeckDefinition("Priority concession", [
            DeckEntry(commander, 1, "commander"), DeckEntry("Plains", 30),
        ], [commander])

    def board(self, seed, *, observer=False, legacy=False):
        game = self.session(seed)
        source = self.add(game.engine, "Lord of Extinction")
        self.add(game.engine, "Plains", seat="B", zone="graveyard", ref="only-graveyard-card")
        grow = self.add(game.engine, "Giant Growth", seat="C", zone="hand")
        self.add(game.engine, "Grizzly Bears", seat="C")
        if observer:
            artist = self.add(game.engine, "Blood Artist")
            self.assert_closed(game, artist)
        else:
            artist = None
        for card in (source, grow):
            self.assert_closed(game, card)
        if legacy:
            game.state.priority_concession_stabilization_version = None
        game.state.players["C"].mana_pool["G"] = 1
        self.prepare(game)
        self.checkpoint(game)
        self.assertEqual(1, game.engine._numeric_stat(source.object_id, "toughness"))
        self.priority(game, "B")
        return game, source, artist

    def concede_at_priority(self, game):
        result = game.act("pilot:B", {"action_id": "concede", "confirm_concede": True})
        self.assertTrue(result.ok, result.summary)
        self.assertFalse(game.state.players["B"].in_game)

    def test_actual_concession_stabilizes_the_zero_toughness_creature_before_priority(self):
        game, source, _artist = self.board(704506031)
        self.concede_at_priority(game)
        self.assertEqual("graveyard", game.state.cards[source.object_id].zone)
        self.assertEqual(("priority", ["C"]), (game.state.pending_decision.kind, game.state.pending_decision.actors))
        events = [event.code for event in game.state.events
                  if event.code in {"player.eliminated", "state.creatures_died"}]
        self.assertEqual(["player.eliminated", "state.creatures_died"], events)
        reloaded = self.replay(game, load=True)
        self.assertEqual(1, reloaded.state.priority_concession_stabilization_version)

    def test_actual_concession_places_death_triggers_before_the_next_players_priority(self):
        game, source, artist = self.board(704506032, observer=True)
        self.concede_at_priority(game)
        self.assertEqual("graveyard", game.state.cards[source.object_id].zone)
        self.assertEqual(("semantic.target", ["A"]), (game.state.pending_decision.kind, game.state.pending_decision.actors))
        self.assertEqual("B", game.state.priority_concession_resume_from)
        game = self.replay(game, load=True)
        self.assertEqual("B", game.state.priority_concession_resume_from)
        result = game.act("pilot:A", {"action_id": "choose", "targets": ["C"]})
        self.assertTrue(result.ok, result.summary)
        self.assertEqual(1, self.trigger_count(game, artist))
        self.assertEqual(("priority", ["C"]), (game.state.pending_decision.kind, game.state.pending_decision.actors))
        self.assertIsNone(game.state.priority_concession_resume_from)
        self.assertIsNone(self.resolve(game))
        self.assertEqual((41, 39), (game.state.players["A"].life, game.state.players["C"].life))
        self.replay(game, load=True)

    def test_historical_absence_replays_the_recorded_priority_boundary(self):
        game, source, _artist = self.board(704506033, legacy=True)
        self.concede_at_priority(game)
        self.assertEqual("battlefield", game.state.cards[source.object_id].zone)
        self.assertEqual(0, game.engine._numeric_stat(source.object_id, "toughness"))
        reloaded = self.replay(game, load=True)
        self.assertIsNone(reloaded.state.priority_concession_stabilization_version)

    def test_checkpoint_can_resume_only_from_a_departed_recorded_seat(self):
        from quorune.turn_priority_model import read_priority_concession_resume

        saved = {"priority_concession_stabilization_version": 1, "priority_concession_resume_from": "B",
                 "turn_order": ["A", "B"], "players": {"A": {"in_game": True}, "B": {"in_game": False}}}
        self.assertEqual("B", read_priority_concession_resume(saved))
        for changes in ({"priority_concession_stabilization_version": True},
                        {"priority_concession_resume_from": "A"}, {"priority_concession_resume_from": "D"}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                read_priority_concession_resume({**saved, **changes})
        historical = {k: v for k, v in saved.items() if not k.startswith("priority_concession_")}
        self.assertIsNone(read_priority_concession_resume(historical))
