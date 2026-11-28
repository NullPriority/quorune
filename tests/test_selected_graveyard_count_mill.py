"""Selected mill counts use each player's live public graveyard and the original Exhaust."""

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
from quorune.selected_graveyard_mill_count import SELECTED_GRAVEYARD_MILL_CAPABILITY, SelectedGraveyardMillCount
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


PROFILE = "commander_review"


class SelectedGraveyardCountMillTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "selected-graveyard-mill.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures/selected-graveyard-count-mill-cards.json"], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        commander = "Isamaru, Hound of Konda"
        cls.deck = DeckDefinition("Riverchurn Monument", [
            DeckEntry(commander, 1, "commander"), DeckEntry("Plains", 30),
        ], [commander])

    def start(self, seed):
        game = self.session(seed)
        source = self.add(game.engine, "Riverchurn Monument")
        crypt = self.add(game.engine, "Tormod's Crypt")
        key = self.add(game.engine, "Voltaic Key")
        for index in range(6):
            self.add(game.engine, "Island", ref=f"riverchurn-mana-{index}")
        for seat, count in (("A", 1), ("B", 3), ("C", 3), ("D", 4)):
            for index in range(count):
                self.add(game.engine, "Plains", seat=seat, zone="graveyard", ref=f"old-graveyard-{seat}-{index}")
        for card in (source, crypt, key):
            self.assert_closed(game, card)
        game.state.config.semantic_policy = "trusted_only"
        self.prepare(game)
        self.checkpoint(game)
        return game, source, crypt, key

    def offered(self, game, source):
        self.priority(game, "A")
        actions = game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        return {row["id"].rsplit(":", 1)[-1]: row["id"] for row in actions
                if row["id"].startswith(f"activate:{source.ref}:")}

    def activate(self, game, source, ability, players):
        action_id = self.offered(game, source)[ability]
        result = game.act("pilot:A", {"action_id": action_id, "targets": list(players), "pay": "auto"})
        self.assertTrue(result.ok, result.summary)

    def library_sizes(self, game):
        return {seat: len(game.state.players[seat].zones["library"]) for seat in "ABCD"}

    def test_actual_exhaust_uses_each_original_players_current_own_graveyard_count(self):
        game, source, crypt, key = self.start(76330001)
        self.assertEqual({"ab1", "ab2"}, set(self.offered(game, source)))
        self.activate(game, source, "ab2", ["A", "B", "C"])
        exhaust = game.state.stack[-1]
        self.assertEqual(["A", "B", "C"], exhaust.targets)
        self.assertTrue(game.state.cards[source.object_id].tapped)
        self.activate(game, crypt, "ab1", ["B"])
        self.resolve_top(game)
        self.assertEqual([], game.state.players["B"].zones["graveyard"])
        self.assertEqual(2, len(game.state.players["A"].zones["graveyard"]))
        before = self.library_sizes(game)
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        self.assertEqual({**before, "A": before["A"]-2, "C": before["C"]-3}, self.library_sizes(game))
        self.activate(game, key, "ab1", [source.ref])
        self.assertIsNone(self.resolve(game))
        self.assertFalse(game.state.cards[source.object_id].tapped)
        self.assertEqual({"ab1"}, set(self.offered(game, source)))
        before = self.library_sizes(game)
        self.activate(game, source, "ab1", ["C"])
        self.assertIsNone(self.resolve(game))
        self.assertEqual({**before, "C": before["C"]-2}, self.library_sizes(game))
        self.replay(game, load=True)

    def test_actual_zero_target_exhaust_still_consumes_the_original_activation(self):
        game, source, _, key = self.start(76330002)
        before = self.library_sizes(game)
        self.activate(game, source, "ab2", [])
        self.assertTrue(game.state.cards[source.object_id].tapped)
        self.assertIsNone(self.resolve(game))
        self.assertEqual(before, self.library_sizes(game))
        self.activate(game, key, "ab1", [source.ref])
        self.assertIsNone(self.resolve(game))
        self.assertEqual({"ab1"}, set(self.offered(game, source)))
        self.replay(game, load=True)

    def test_the_complete_original_owns_both_activations_and_requires_the_selected_count(self):
        cap = SELECTED_GRAVEYARD_MILL_CAPABILITY
        report, semantics = register_card(self.db, self.registry, "Riverchurn Monument")
        self.assertTrue(report["strict_capability_ready"], report["blockers"])
        self.assertEqual("capability_closed", report["trust_basis"])
        programs = semantics.programs_for_oracle(report["oracle_id"])
        program, = (row for row in programs if cap in row.capability_dependencies)
        self.assertEqual("any_number", program.target_schema["count_parameter"]["parameter"])
        effect, = program.effects
        self.assertEqual(SelectedGraveyardMillCount(), SelectedGraveyardMillCount.from_dict(effect["count"]))
        deps = tuple(key for key in program.capability_dependencies if key != cap)
        absent = replace(program, capability_dependencies=deps,
                         capability_closure=self.registry.closure(deps, profile=PROFILE).to_dict())
        bound = bind_semantic_program_runtime(absent, capability_registry=self.registry, profile=PROFILE)
        self.assertFalse(bound["strict"])
        self.assertIn(f"capability:undeclared_runtime_dependency:{cap}", bound["blockers"])
        card = self.db.lookup("Riverchurn Monument", fuzzy=False)
        for old, new in (("cards in their graveyard", "cards in exile"),
                         ("cards in their graveyard.", "cards in their graveyard. Rewrite history.")):
            changed = replace(card, oracle_text=card.oracle_text.replace(old, new))
            result = compile_oracle_card(changed, capability_registry=self.registry, capability_profile=PROFILE)
            self.assertNotEqual("exact", result.status)
