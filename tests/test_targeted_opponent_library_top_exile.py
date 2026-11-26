"""Distinct opponent library supplier and original controller's ordinary play."""

from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.opponent_library_top_model import OPPONENT_TOP_EXILE_CAPABILITY, OpponentLibraryTopSpec
from quorune.oracle_ir import compile_oracle_card
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card
import test_monarch_movement_replacements as movement_fixture


PROFILE = "commander_review"


class TargetedOpponentLibraryTopExileTests(InheritedNativeFixture, unittest.TestCase):
    reject = movement_fixture.MonarchMovementReplacementTests.reject

    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "targeted-opponent-library-top.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures/targeted-opponent-library-top-exile-cards.json"], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        commander = "Isamaru, Hound of Konda"
        cls.deck = DeckDefinition("Opponent library top", [
            DeckEntry(commander, 1, "commander"), DeckEntry("Plains", 30),
        ], [commander])

    def top(self, game, name, *, seat="B"):
        card = self.add(game.engine, name, seat=seat, zone="library", ref="concealed-opponent-top")
        card.known_to = []
        card.revealed_to = []
        self.assertEqual(card.object_id, game.state.players[seat].zones["library"][-1])
        return card

    def ready(self, game, *cards, mana_land="Plains", mana_count=2):
        for card in cards:
            self.assert_closed(game, card)
        lands = tuple(self.add(game.engine, mana_land, ref=f"exile-budget-{index}") for index in range(mana_count))
        game.state.config.semantic_policy = "trusted_only"
        self.prepare(game)
        self.checkpoint(game)
        return lands

    def offers(self, game, seat="A"):
        self.priority(game, seat)
        packet = game.packet("pilot:" + seat, full=True)
        return {row["id"] for row in packet["decision"]["ctx"]["legal"]["actions"]}

    def activate(self, game, source, ability):
        action_id = f"activate:{source.ref}:{ability}"
        self.assertIn(action_id, self.offers(game))
        self.reject(game, "A", {"action_id": action_id, "targets": ["A"], "pay": "auto"})
        result = game.act("pilot:A", {"action_id": action_id, "targets": ["B"], "pay": "auto"})
        self.assertTrue(result.ok, result.summary)

    def test_actual_colorless_cast_exiles_only_the_original_opponent_top_without_access(self):
        game = self.session(76337001)
        harvester = self.add(game.engine, "Thought Harvester")
        spell = self.add(game.engine, "Mind Stone", zone="hand")
        hidden = self.top(game, "Forest")
        self.ready(game, harvester, spell)
        self.cast(game, "A", spell)
        self.target(game, "A", ["B"])
        self.assertEqual(("spell", "triggered_ability"), tuple(item.kind for item in game.state.stack))
        for seat in "ABCD":
            view = json.dumps(game.packet("pilot:" + seat, full=True))
            self.assertFalse(hidden.ref in view, f"The library top is visible to {seat}")
            self.assertFalse(hidden.object_id in view, f"The hidden physical identity is visible to {seat}")
        game = self.replay(game, load=True)
        self.resolve_top(game)
        current = game.state.cards[hidden.object_id]
        self.assertEqual("exile", current.zone)
        self.assertFalse(current.face_down)
        for seat in "ABCD":
            view = json.dumps(game.packet("pilot:" + seat, full=True))
            self.assertTrue(current.ref in view, f"The face-up exile is missing from {seat}'s view")
        self.assertNotIn("temporary_play_permission", current.annotations)
        self.assertEqual("stack", game.state.cards[spell.object_id].zone)
        self.assertIsNone(self.resolve(game))
        self.assertNotIn(f"play-land:{current.ref}", self.offers(game))
        self.replay(game, load=True)

    def test_actual_kanzashi_grants_only_the_controller_the_normal_land_play(self):
        game = self.session(76337002)
        source = self.add(game.engine, "Ornate Kanzashi")
        hidden = self.top(game, "Forest")
        self.ready(game, source)
        self.activate(game, source, "ab1")
        self.assertTrue(game.state.cards[source.object_id].tapped)
        self.assertIsNone(self.resolve(game))
        game = self.replay(game, load=True)
        current = game.state.cards[hidden.object_id]
        self.assertEqual(("exile", "B"), (current.zone, current.owner))
        self.assertEqual("A", current.annotations["temporary_play_permission"]["player"])
        self.assertFalse(current.annotations["temporary_play_permission"]["without_mana_cost"])
        action_id = f"play-land:{current.ref}"
        self.assertIn(action_id, self.offers(game))
        result = game.act("pilot:A", {"action_id": action_id})
        self.assertTrue(result.ok, result.summary)
        current = game.state.cards[hidden.object_id]
        self.assertEqual(("battlefield", "B", "A"), (current.zone, current.owner, current.controller))
        self.replay(game, load=True)

    def test_actual_clique_pays_the_untap_cost_and_foreign_spell_price_or_expires(self):
        for expires in (False, True):
            with self.subTest(expires=expires):
                game = self.session(76337010 + expires)
                source = self.add(game.engine, "Knacksaw Clique")
                source.tapped = True
                source.acquired_control_turn_count = -1
                target = self.add(game.engine, "Grizzly Bears")
                hidden = self.top(game, "Unsummon")
                lands = self.ready(game, source, mana_land="Island", mana_count=3)
                self.activate(game, source, "ab2")
                self.assertFalse(game.state.cards[source.object_id].tapped)
                self.assertNotIn(f"activate:{source.ref}:ab2", self.offers(game))
                self.assertIsNone(self.resolve(game))
                current = game.state.cards[hidden.object_id]
                self.assertEqual("exile", current.zone)
                action_id = f"cast:{current.ref}"
                self.assertIn(action_id, self.offers(game))
                if expires:
                    turn = game.state.turn_sequence
                    self.until(game, lambda session: session.state.turn_sequence > turn)
                    self.assertNotIn(action_id, self.offers(game))
                    self.assertEqual("exile", game.state.cards[hidden.object_id].zone)
                else:
                    self.assertEqual(2, sum(game.state.cards[land.object_id].tapped for land in lands))
                    result = game.act("pilot:A", {"action_id": action_id, "targets": [target.ref], "pay": "auto"})
                    self.assertTrue(result.ok, result.summary)
                    self.assertTrue(all(game.state.cards[land.object_id].tapped for land in lands))
                    self.assertIsNone(self.resolve(game))
                    self.assertEqual("hand", game.state.cards[target.object_id].zone)
                    self.assertEqual("graveyard", game.state.cards[hidden.object_id].zone)
                self.replay(game, load=True)

    def test_full_originals_keep_the_printed_supplier_and_controller_permission(self):
        cap = OPPONENT_TOP_EXILE_CAPABILITY
        for name, (report, semantics) in (
            ("Thought Harvester", register_card(self.db, self.registry, "Thought Harvester")),
            ("Ornate Kanzashi", register_card(self.db, self.registry, "Ornate Kanzashi")),
            ("Knacksaw Clique", register_card(self.db, self.registry, "Knacksaw Clique")),
        ):
            with self.subTest(original=name):
                self.assertTrue(report["strict_capability_ready"], report["blockers"])
                self.assertEqual("capability_closed", report["trust_basis"])
                program, = (row for row in semantics.programs_for_oracle(report["oracle_id"]) if cap in row.capability_dependencies)
                spec = OpponentLibraryTopSpec.from_effect(program.effects[0])
                self.assertEqual(name != "Thought Harvester", spec.allows_play)
                self.assertEqual("opponent", program.target_schema["player_relation"])
                deps = tuple(key for key in program.capability_dependencies if key != cap)
                absent = replace(program, capability_dependencies=deps,
                                 capability_closure=self.registry.closure(deps, profile=PROFILE).to_dict())
                bound = bind_semantic_program_runtime(absent, capability_registry=self.registry, profile=PROFILE)
                self.assertFalse(bound["strict"])
                self.assertIn(f"capability:undeclared_runtime_dependency:{cap}", bound["blockers"])
        card = self.db.lookup("Thought Harvester", fuzzy=False)
        for old, new in (("card of their library", "card of their library face down"),
                         ("you cast a colorless spell", "an opponent casts a colorless spell"),
                         ("of their library.", "of their library. Rewrite history.")):
            changed = replace(card, oracle_text=card.oracle_text.replace(old, new))
            result = compile_oracle_card(changed, capability_registry=self.registry, capability_profile=PROFILE)
            self.assertNotEqual("exact", result.status)
