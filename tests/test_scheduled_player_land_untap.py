"""Rising Waters authorizes the actual upkeep participant to untap one legal land."""

from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
import test_next_player_untap_restrictions as untap_fixture
from quorune.carddb import CardDatabase
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.deck import DeckDefinition, DeckEntry
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.untargeted_untap_model import scheduled_land_untap_effect
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


ORIGINAL = "Rising Waters"


class ScheduledPlayerLandUntapTests(InheritedNativeFixture, unittest.TestCase):
    playing = untap_fixture.NextPlayerUntapRestrictionTests.playing
    activate = untap_fixture.NextPlayerUntapRestrictionTests.activate

    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "scheduled-player-land-untap.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures/scheduled-player-land-untap-cards.json"], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("The actual scheduled player untaps", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"), DeckEntry("Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def test_complete_original_keeps_separate_static_and_mandatory_upkeep_results(self):
        admitted, semantics = register_card(self.db, self.registry, ORIGINAL)
        self.assertTrue(admitted["strict_capability_ready"], admitted["blockers"])
        card = self.db.lookup(ORIGINAL, fuzzy=False)
        rows = semantics.programs_for_oracle(card.oracle_id)
        self.assertEqual({"untap.step", "step.begin"}, {row.event for row in rows})
        trigger, = (row for row in rows if row.event == "step.begin")
        self.assertIsNone(trigger.target_schema)
        self.assertEqual([scheduled_land_untap_effect()], trigger.effects)
        self.assertEqual({"all": [{"field": "step", "op": "eq", "value": "upkeep"}]}, trigger.event_condition)
        for changed in (replace(trigger, event_condition={"all": [{"field": "step", "op": "eq", "value": "draw"}]}),
                        replace(trigger, effects=[{**trigger.effects[0], "player": "$controller"}]),
                        replace(trigger, effects=[{**trigger.effects[0], "controller_scope": "any"}])):
            self.assertFalse(bind_semantic_program_runtime(
                changed, capability_registry=self.registry, profile="commander_review",
            )["strict"])

    def opponent_upkeep(self, game, source):
        self.until(game, lambda current: current.state.active_player == "B"
                   and current.state.step == "upkeep" and self.trigger_count(current, source) == 1)

    def test_actual_upkeep_player_must_untap_one_live_legal_land_after_the_source_leaves(self):
        game = self.playing(70126061)
        source = self.add(game.engine, ORIGINAL, zone="hand")
        ice = self.add(game.engine, "Frozen in Ice", zone="hand")
        removal = self.add(game.engine, "Disenchant", zone="hand", seat="B")
        forest = self.add(game.engine, "Forest", seat="B", ref="waters-forest")
        swamp = self.add(game.engine, "Swamp", seat="B")
        arbor = self.add(game.engine, "Dryad Arbor", seat="B")
        plains = self.add(game.engine, "Plains", seat="B")
        island = self.add(game.engine, "Island", seat="B")
        ring = self.add(game.engine, "Sol Ring", seat="B")
        outsider = self.add(game.engine, "Forest", ref="waters-other-controller")
        outsider.tapped = True
        for card in (source, ice, removal, arbor, ring):
            self.assert_closed(game, card)
        self.prepare(game, mana={"C": 5, "U": 2})
        self.checkpoint(game)
        self.cast_for(game, "A", ice, [arbor.ref], reserve={"C": 3, "U": 1})
        self.assertIsNone(self.resolve(game))
        self.assertTrue(game.state.cards[arbor.object_id].tapped)
        self.cast_for(game, "A", source)
        self.resolve_top(game)
        for card in (forest, swamp, ring):
            self.activate(game, "B", card)
        self.opponent_upkeep(game, source)
        self.assertEqual([True, True, True, False, False, False],
                         [game.state.cards[card.object_id].tapped for card in (forest, swamp, arbor, plains, island, ring)])
        trigger, = (item for item in game.state.stack if item.source_object_id == source.object_id)
        self.assertEqual(("A", "B", []), (trigger.controller, trigger.context["player"], trigger.targets))
        self.activate(game, "B", ring)
        self.activate(game, "B", plains)
        self.cast_for(game, "B", removal, [source.ref])
        self.resolve_top(game)
        self.assertEqual("graveyard", game.state.cards[source.object_id].zone)
        decision = self.resolve(game)
        self.assertEqual(("semantic.choice", ["B"]), (decision.kind, decision.actors))
        self.assertIsNone(game.packet("pilot:A", full=True)["decision"])
        view = game.packet("pilot:B", full=True)["decision"]["ctx"]
        self.assertEqual({forest.ref, swamp.ref, plains.ref}, {row["id"] for row in view["objects"]})
        before = authoritative_state_hash(game.state)
        for seat, refs in (("A", [forest.ref]), ("B", []), ("B", [arbor.ref]), ("B", [island.ref]),
                           ("B", [outsider.ref]), ("B", [forest.ref, swamp.ref])):
            rejected = game.act("pilot:" + seat, {"action_id": "choose", "objects": refs})
            self.assertFalse(rejected.ok, rejected.summary)
            self.assertEqual(before, authoritative_state_hash(game.state))
        game = self.replay(game, load=True)
        accepted = game.act("pilot:B", {"action_id": "choose", "objects": [plains.ref]})
        self.assertTrue(accepted.ok, accepted.summary)
        self.assertIsNone(self.resolve(game))
        self.assertEqual([True, True, True, False, False],
                         [game.state.cards[card.object_id].tapped for card in (forest, swamp, arbor, plains, island)])
        self.replay(game, load=True)

    def test_actual_upkeep_with_no_legal_tapped_land_finishes_without_a_choice(self):
        game = self.playing(70126062)
        source = self.add(game.engine, ORIGINAL)
        island = self.add(game.engine, "Island", seat="B")
        self.assert_closed(game, source)
        self.prepare(game)
        self.checkpoint(game)
        self.opponent_upkeep(game, source)
        self.assertIsNone(self.resolve(game))
        self.assertFalse(game.state.cards[island.object_id].tapped)
        self.replay(game, load=True)
