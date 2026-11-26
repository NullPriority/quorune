"""The fixed Goblin result depends on the actual private top-land outcome."""

from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.deck import DeckDefinition, DeckEntry
from quorune.library_battlefield_model import private_top_land_or_goblin_effect
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


ORIGINAL = "Rulik Mons, Warren Chief"


class PrivateTopLandOrGoblinTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "private-top-land-or-goblin.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures/private-top-land-or-goblin-cards.json"], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Private top land and Goblin", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"), DeckEntry("Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def test_complete_original_owns_the_attacking_source_and_exact_goblin_result(self):
        result, semantics = register_card(self.db, self.registry, ORIGINAL)
        self.assertTrue(result["strict_capability_ready"], result["blockers"])
        record = self.db.lookup(ORIGINAL, fuzzy=False)
        ability, = (p for p in semantics.programs_for_oracle(record.oracle_id) if p.event == "creature.attacks")
        effect, = ability.effects
        self.assertEqual(private_top_land_or_goblin_effect(), effect)
        self.assertEqual({"field": "card", "op": "eq", "value": "$source.ref"}, ability.event_condition)
        self.assertIsNone(ability.target_schema)
        self.assertIn("token.creation.fixed_definition", ability.capability_dependencies)
        changed = {**effect, "otherwise_token": {**effect["otherwise_token"], "quantity": 2}}
        self.assertFalse(bind_semantic_program_runtime(
            replace(ability, effects=[changed]), capability_registry=self.registry, profile="commander_review",
        )["strict"])

    def scene(self, seed, top_name, *, doubled=False):
        game = self.session(seed)
        source = self.add(game.engine, ORIGINAL, zone="hand")
        self.assert_closed(game, source)
        self.assert_closed(game, self.add(game.engine, "Fervor"))
        if doubled:
            self.assert_closed(game, self.add(game.engine, "Doubling Season"))
        top = None
        if top_name is None:
            for object_id in tuple(game.state.players["A"].zones["library"]):
                game.engine.move_card(object_id, "exile", log=False, semantic_events=False)
        else:
            top = self.add(game.engine, top_name, zone="library", ref="rulik-private-top")
        self.prepare(game, mana={"C": 1, "R": 1, "G": 2})
        self.checkpoint(game)
        self.cast_for(game, "A", source)
        self.resolve_top(game)
        self.attack(game, [(source, "B")])
        self.until(game, lambda current: self.trigger_count(current, source) == 1)
        self.assertFalse(game.state.stack[-1].targets)
        self.assertEqual("declare_attackers", game.state.step)
        return game, top

    def choose_top(self, game, top, options, choice):
        decision = self.resolve(game)
        self.assertEqual(("semantic.choice", ["A"]), (decision.kind, decision.actors))
        ctx = game.packet("pilot:A", full=True)["decision"]["ctx"]
        self.assertEqual([{"id": top.ref, "name": top.printed_name}], ctx["objects"])
        action, = ctx["legal_actions"]
        self.assertEqual(options, tuple(action["choice_schema"]["legal_values"]))
        for seat in "BCD":
            packet = game.packet(f"pilot:{seat}", full=True)
            self.assertIsNone(packet["decision"])
            self.assertNotIn(top.ref, json.dumps(packet))
        command = {"action_id": action["id"], "choice": choice}
        before = authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:B", command).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        if "put_onto_battlefield" not in options:
            self.assertFalse(game.act("pilot:A", {**command, "choice": "put_onto_battlefield"}).ok)
            self.assertEqual(before, authoritative_state_hash(game.state))
        game = self.replay(game, load=True)
        result = game.act("pilot:A", command)
        self.assertTrue(result.ok, result.summary)
        self.assertIsNone(self.resolve(game))
        return game

    def goblins(self, game):
        matches = [card for card in game.state.cards.values() if card.is_token and card.zone == "battlefield"]
        for card in matches:
            data = game.engine._effective_card_data(card)
            self.assertIn("goblin", game.engine._type_parts(data["type_line"])[1])
            self.assertEqual(("A", "A", {"R"}), (card.owner, card.controller, set(data["colors"])))
            self.assertEqual((1, 1), tuple(game.engine._numeric_stat(card.object_id, stat)
                                           for stat in ("power", "toughness")))
        return matches

    def test_actual_declined_land_or_nonland_stays_private_and_creates_the_replacement_tokens(self):
        for index, name in enumerate(("Sol Ring", "Forest")):
            with self.subTest(top=name):
                game, top = self.scene(40104101 + index, name, doubled=True)
                original = top.logical_object_id
                options = ("keep_and_create_goblin",) if index == 0 else ("put_onto_battlefield", "keep_and_create_goblin")
                game = self.choose_top(game, top, options, "keep_and_create_goblin")
                self.assertEqual(2, len(self.goblins(game)))
                self.assertEqual(top.object_id, game.state.players["A"].zones["library"][-1])
                self.assertEqual(original, game.state.cards[top.object_id].logical_object_id)
                self.assertNotIn(top.ref, json.dumps(game.packet("pilot:B", full=True)))
                self.replay(game, load=True)

    def test_actual_committed_land_entrant_prevents_the_independent_goblin_result(self):
        game, top = self.scene(40104103, "Forest")
        old = top.logical_object_id
        game = self.choose_top(game, top, ("put_onto_battlefield", "keep_and_create_goblin"), "put_onto_battlefield")
        current = game.state.cards[top.object_id]
        self.assertEqual(("battlefield", "A", True), (current.zone, current.controller, current.tapped))
        self.assertNotEqual(old, current.logical_object_id)
        self.assertEqual([], self.goblins(game))
        self.replay(game, load=True)

    def test_actual_empty_library_creates_one_goblin_without_an_inspection_choice(self):
        game, _ = self.scene(40104104, None)
        before = len(game.state.events)
        self.assertIsNone(self.resolve(game))
        self.assertEqual(1, len(self.goblins(game)))
        self.assertNotIn("library.look", {event.code for event in game.state.events[before:]})
        self.assertEqual([], game.state.players["A"].zones["library"])
        self.assertIn("A", game.state.active_seats())
        self.replay(game, load=True)
