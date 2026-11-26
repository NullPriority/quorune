"""Physical exile plays and departure shuffles through original card commands."""

from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.event_subscriptions import PHYSICAL_EXILE_PLAY_TREASURE_TEMPLATE
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


class ExilePlayOwnerLibraryTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "exile-play-owner-library.sqlite3"
        fixtures = ROOT / "tests" / "fixtures"
        build_fixture_database([
            fixtures / "actual-card-play-and-fixed-cleanup-limit-cards.json",
            fixtures / "exile-play-owner-library-cards.json",
        ], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        commander = "Isamaru, Hound of Konda"
        cls.deck = DeckDefinition("Exile play and owner library", [
            DeckEntry(commander, 1, "commander"), DeckEntry("Plains", 30),
        ], [commander])

    def test_exact_play_requirements_reject_omitted_origin_and_nonbattlefield_departure(self):
        name = "Prosper, Tome-Bound"
        report, semantics = register_card(self.db, self.registry, name)
        self.assertTrue(report["strict_capability_ready"], report["blockers"])
        program, = [row for row in semantics.programs_for_oracle(report["oracle_id"])
                    if row.provenance.get("template_id") == PHYSICAL_EXILE_PLAY_TREASURE_TEMPLATE]
        for changed_event in (0, 1):
            for excluded_fact in ("from", "physical_card"):
                with self.subTest(event=changed_event, fact=excluded_fact):
                    events = list(program.event_condition["event_subscriptions"])
                    event = events[changed_event]
                    conditions = [item for item in event["condition"]["all"] if item["field"] != excluded_fact]
                    events[changed_event] = {**event, "condition": {"all": conditions}}
                    changed = replace(program, event_condition={"event_subscriptions": events})
                    bound = bind_semantic_program_runtime(changed, capability_registry=self.registry, profile="commander_review")
                    self.assertFalse(bound["strict"], bound)
        original = self.db.lookup("Lembas", fuzzy=False)
        for changed_text in (
                original.oracle_text.replace("from the battlefield", "from the hand"),
                original.oracle_text.replace("its owner shuffles it into their library", "you shuffle it into your library")):
            changed = replace(original, oracle_text=changed_text)
            self.assertNotEqual("exact", compile_oracle_card(changed, capability_registry=self.registry).status)

    def test_actual_borrowed_artifact_shuffles_before_sacrificing_controllers_life_gain(self):
        game = self.session(811260471)
        source = self.add(game.engine, "Lembas", seat="B")
        game.engine.change_control(source.object_id, "A", reason="Prepare borrowed artifact")
        self.assert_closed(game, source)
        self.prepare(game, mana={"C": 2})
        self.checkpoint(game)
        players = game.state.players
        before = {seat: players[seat].stats.get("shuffle_count", 0) for seat in "AB"}
        self.priority(game, "A")
        actions = game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        offer, = [row for row in actions if row["id"].startswith("activate:" + source.ref + ":")]
        activation = game.act("pilot:A", {"action_id": offer["id"], "pay": "auto"})
        self.assertTrue(activation.ok, activation.summary)
        self.assertEqual("graveyard", game.state.cards[source.object_id].zone)
        self.assertEqual(2, len(game.state.stack))
        self.resolve_top(game)
        self.assertIn(source.object_id, players["B"].zones["library"])
        self.assertEqual(before["B"] + 1, players["B"].stats.get("shuffle_count", 0))
        self.assertEqual(before["A"], players["A"].stats.get("shuffle_count", 0))
        self.assertEqual(40, players["A"].life)
        self.resolve_top(game)
        self.assertEqual((43, 40), (players["A"].life, players["B"].life))
        self.replay(game, load=True)

    def test_actual_exile_land_and_spell_trigger_once_each_but_hand_and_cast_copy_do_not(self):
        game = self.session(811260472)
        engine = game.engine
        source = self.add(engine, "Prosper, Tome-Bound")
        enabler = self.add(engine, "Act on Impulse", zone="hand")
        proxy = self.add(engine, "Arcane Proxy", zone="hand")
        grave = self.add(engine, "Shock", zone="graveyard", ref="prosper-shock-original")
        forest = self.add(engine, "Forest", zone="library", ref="prosper-forest")
        bear = self.add(engine, "Grizzly Bears", zone="library", ref="prosper-bear")
        library = game.state.players["A"].zones["library"]
        for card in (bear, forest):
            library.remove(card.object_id)
            library.append(card.object_id)
        for card in (source, enabler, proxy, bear):
            self.assert_closed(game, card)
        self.prepare(game, mana={"C": 5, "R": 1, "G": 1, "U": 2})
        self.checkpoint(game)
        self.cast_for(game, "A", enabler)
        self.assertEqual(1, len(game.state.stack))
        self.resolve_top(game)
        for card in (forest, bear):
            self.assertEqual("exile", game.state.cards[card.object_id].zone)
        self.priority(game, "A")
        land = game.act("pilot:A", {"action_id": "play-land:" + forest.ref})
        self.assertTrue(land.ok, land.summary)
        self.assertEqual(1, len(game.state.stack))
        self.resolve_top(game)
        self.cast_for(game, "A", bear)
        self.assertEqual(2, len(game.state.stack))
        self.settle(game)
        self.priority(game, "A")
        response = game.act("pilot:A", {"action_id": "cast-prototype:" + proxy.ref, "pay": "auto"})
        self.assertTrue(response.ok, response.summary)
        self.resolve_top(game)
        self.target(game, "A", [grave.ref])
        self.assertEqual("semantic.choice", self.resolve(game).kind)
        choice = game.packet("pilot:A", full=True)["decision"]
        option, = choice["ctx"]["cast_options"]
        cast = game.act("pilot:A", {"action_id": "choose", "choice": "cast",
                                    "cast": {"cost_option": option["id"], "targets": ["C"]}})
        self.assertTrue(cast.ok, cast.summary)
        self.assertEqual(1, len(game.state.stack))
        self.settle(game)
        treasures = [card for card in game.state.cards.values() if card.zone == "battlefield"
                     and card.is_token and card.printed_name == "Treasure Token"]
        self.assertEqual(["A", "A"], [card.controller for card in treasures])
        self.assertEqual(38, game.state.players["C"].life)
        self.replay(game, load=True)
