"""Reiver Demon's exact cast origin and compound creature-set destruction."""

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


ORIGINAL = "Reiver Demon"


class NonartifactNonblackMassDestructionTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        database = Path(cls.temporary.name) / "nonartifact-nonblack-mass-destruction.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures/nonartifact-nonblack-mass-destruction-cards.json"], database)
        cls.db = CardDatabase(database)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Cast-origin mass destruction", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"), DeckEntry("Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def program(self):
        original = self.db.lookup(ORIGINAL, fuzzy=False)
        binding, semantics = register_card(self.db, self.registry, ORIGINAL)
        self.assertTrue(binding["strict_capability_ready"], binding["blockers"])
        program, = [item for item in semantics.programs_for_oracle(original.oracle_id)
                    if item.event == "permanent.enter.self"]
        return program

    def test_complete_original_composes_cast_origin_and_two_independent_exclusions(self):
        program = self.program()
        self.assertIsNotNone(program.event_condition)
        self.assertIsNone(program.target_schema)
        effect, = program.effects
        self.assertEqual("destroy_all", effect["op"])
        self.assertTrue(effect["regeneration_prohibited"])
        query = effect["set"]["query"]
        self.assertEqual(("creature",), tuple(query["types_all"]))
        self.assertEqual(("artifact",), tuple(query["excluded_types"]))
        self.assertEqual(("B",), tuple(query["colors_none"]))

    def test_actual_cast_destroys_the_live_eligible_set_and_prohibits_regeneration(self):
        game = self.session(70170081)
        source = self.add(game.engine, ORIGINAL, zone="hand")
        doomed = self.add(game.engine, "Grizzly Bears", seat="B", ref="reiver-doomed")
        converted = self.add(game.engine, "Grizzly Bears", ref="reiver-converted")
        artifact = self.add(game.engine, "Ornithopter", seat="B")
        black = self.add(game.engine, "Vampire Hexmage", seat="B")
        coating = self.add(game.engine, "Liquimetal Coating", seat="B")
        mending = self.add(game.engine, "Mending Touch", seat="B", zone="hand")
        self.add(game.engine, "Forest", seat="B")
        for card in (source, doomed, converted, artifact, black, coating, mending):
            self.assert_closed(game, card)
        self.prepare(game, mana={"C": 4, "B": 4})
        self.checkpoint(game)
        self.cast_for(game, "A", source)
        self.resolve_top(game)
        self.assertEqual(1, self.trigger_count(game, source))
        self.assertIn("flying", game.engine._combat_keywords(game.state.cards[source.object_id]))
        self.priority(game, "B")
        rows = game.packet("pilot:B", full=True)["decision"]["ctx"]["legal"]["actions"]
        activate, = [row["id"] for row in rows if row["id"].startswith(f"activate:{coating.ref}:")]
        applied = game.act("pilot:B", {"action_id": activate, "targets": [converted.ref], "pay": "auto"})
        self.assertTrue(applied.ok, applied.summary)
        self.resolve_top(game)
        self.cast_for(game, "B", mending, [doomed.ref])
        self.resolve_top(game)
        self.assertEqual(1, game.state.cards[doomed.object_id].regeneration_shields)
        game = self.replay(game, load=True)
        self.resolve_top(game)
        self.assertEqual("graveyard", game.state.cards[doomed.object_id].zone)
        for card in (source, converted, artifact, black):
            self.assertEqual("battlefield", game.state.cards[card.object_id].zone)
        self.replay(game, load=True)

    def test_actual_reanimation_keeps_the_creatures_and_creates_no_destruction_trigger(self):
        game = self.session(60340081)
        source = self.add(game.engine, ORIGINAL, zone="graveyard")
        reanimate = self.add(game.engine, "Reanimate", zone="hand")
        same = self.add(game.engine, "Grizzly Bears", ref="reiver-reanimate-local")
        other = self.add(game.engine, "Grizzly Bears", seat="B", ref="reiver-reanimate-foreign")
        for card in (source, reanimate, same, other):
            self.assert_closed(game, card)
        self.prepare(game, mana={"B": 1})
        self.checkpoint(game)
        self.cast_for(game, "A", reanimate, [source.ref])
        game = self.replay(game, load=True)
        self.resolve_top(game)
        self.assertEqual(0, self.trigger_count(game, source))
        for card in (source, same, other):
            self.assertEqual("battlefield", game.state.cards[card.object_id].zone)
        self.replay(game, load=True)
