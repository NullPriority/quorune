"""The original spell checks its live distinct land types after its tapped search."""

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.flashback import FLASHBACK_CAST_OPTION_ID
from quorune.record import authoritative_state_hash
from quorune.resolution_conditions import THREE_BASIC_LAND_TYPES_CONDITION
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


ORIGINAL = "Path to the Festival"


class DomainAfterLandSearchTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "domain-after-land-search.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures/domain-after-land-search-cards.json"], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Search then count basic types", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"), DeckEntry("Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def test_complete_original_preserves_search_before_distinct_current_land_types(self):
        result, semantics = register_card(self.db, self.registry, ORIGINAL)
        self.assertTrue(result["strict_capability_ready"], result["blockers"])
        record = self.db.lookup(ORIGINAL, fuzzy=False)
        program, = (row for row in semantics.programs_for_oracle(record.oracle_id) if row.event == "resolve")
        self.assertIsNone(program.target_schema)
        first, condition = program.effects
        self.assertEqual(("search", "battlefield"), (first["op"], first["destination"]))
        self.assertEqual("apply_if_public_condition", condition["op"])
        self.assertEqual(THREE_BASIC_LAND_TYPES_CONDITION.to_dict(), condition["condition"])
        self.assertEqual([{"op": "scry", "player": "$controller", "count": 1}], condition["effects"])

    def scene(self, seed, own_lands, searched, *, flashback=False):
        game = self.session(seed)
        spell = self.add(game.engine, ORIGINAL, zone="graveyard" if flashback else "hand")
        for index, name in enumerate(own_lands):
            self.add(game.engine, name, ref=f"domain-existing-{index}")
        self.add(game.engine, "Swamp", seat="B", ref="unrelated-far-land")
        chosen = self.add(game.engine, searched, zone="library", ref="domain-private-search-choice")
        self.assert_closed(game, spell)
        self.prepare(game, mana={"C": 4 if flashback else 2, "G": 1})
        self.checkpoint(game)
        if flashback:
            offer = self.action(game, "A", spell)
            result = game.act("pilot:A", {
                "action_id": offer["id"], "from": "graveyard",
                "cost_option": FLASHBACK_CAST_OPTION_ID, "pay": "auto",
            })
            self.assertTrue(result.ok, result.summary)
        else:
            self.cast_for(game, "A", spell)
        self.assertEqual("stack", game.state.cards[spell.object_id].zone)
        return game, spell, chosen

    def search(self, game, land):
        pending = self.resolve(game)
        self.assertEqual(("semantic.search", ["A"]), (pending.kind, pending.actors))
        packet = game.packet("pilot:A", full=True)
        schema = packet["decision"]["legal_actions"][0]["choice_schema"]
        self.assertIn(land.ref, schema["legal_refs"])
        self.assertNotIn(land.ref, json.dumps(game.packet("pilot:B", full=True)))
        command = {"action_id": "choose", "search_cards": [land.ref]}
        before = authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:B", command).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        shuffles = game.state.players["A"].stats.get("shuffle_count", 0)
        game = self.replay(game, load=True)
        result = game.act("pilot:A", command)
        self.assertTrue(result.ok, result.summary)
        entered = game.state.cards[land.object_id]
        self.assertEqual(("battlefield", "A", True), (entered.zone, entered.controller, entered.tapped))
        self.assertEqual(shuffles + 1, game.state.players["A"].stats.get("shuffle_count", 0))
        return game

    def scry(self, game):
        pending = self.resolve(game)
        self.assertEqual(("semantic.choice", ["A"]), (pending.kind, pending.actors))
        ctx = game.packet("pilot:A", full=True)["decision"]["ctx"]
        self.assertEqual("scry", ctx["operation"])
        front, = ctx["objects"]
        ref = front["id"]
        top = game.state.cards[game.state.players["A"].zones["library"][-1]]
        self.assertEqual(top.ref, ref)
        self.assertNotIn(ref, json.dumps(game.packet("pilot:B", full=True)))
        command = {"action_id": "choose", "cards": {"top": [], "bottom": [ref]}}
        before = authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:B", command).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        game = self.replay(game, load=True)
        result = game.act("pilot:A", command)
        self.assertTrue(result.ok, result.summary)
        self.assertIsNone(self.resolve(game))
        self.assertEqual(top.object_id, game.state.players["A"].zones["library"][0])
        return self.replay(game, load=True)

    def test_actual_search_adds_the_third_type_before_private_scry(self):
        game, spell, land = self.scene(30506051, ("Forest", "Mountain"), "Island")
        game = self.search(game, land)
        self.assertEqual("stack", game.state.cards[spell.object_id].zone)
        game = self.scry(game)
        self.assertEqual("graveyard", game.state.cards[spell.object_id].zone)

    def test_actual_duplicate_types_and_opposing_lands_cannot_enable_scry(self):
        game, spell, land = self.scene(30506052, ("Forest", "Forest"), "Mountain")
        game = self.search(game, land)
        self.assertIsNone(self.resolve(game))
        self.assertEqual("graveyard", game.state.cards[spell.object_id].zone)
        self.replay(game, load=True)

    def test_actual_flashback_uses_live_dual_land_types_and_exiles_after_scry(self):
        game, spell, land = self.scene(30506053, ("Breeding Pool",), "Mountain", flashback=True)
        game = self.search(game, land)
        self.assertEqual("stack", game.state.cards[spell.object_id].zone)
        game = self.scry(game)
        self.assertEqual("exile", game.state.cards[spell.object_id].zone)
