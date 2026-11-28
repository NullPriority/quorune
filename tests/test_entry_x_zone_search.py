"""Entry-X mixed-zone search on a native Siege, with private command replay."""

from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
import test_named_zone_searches as named_searches
from quorune.carddb import CardDatabase
from quorune.cast_x_search_model import (
    ENTRY_X_ZONE_SEARCH_CAPABILITY, CastXZoneSearchSelection, cast_x_zone_search_effect, decode_zone_search,
)
from quorune.deck import DeckDefinition, DeckEntry
from quorune.entry_x_amount_model import EntryXAmountSpec
from quorune.errors import GameRuleError
from quorune.oracle_ir import compile_oracle_card
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.cast_x_zone_searches import verify_resolving_cast_x_zone_search
from scripts.build_test_database import build_fixture_database


CARD = "Invasion of Ikoria // Zilortha, Apex of Ikoria"
FRONT = "Invasion of Ikoria"
BACK = "Zilortha, Apex of Ikoria"
FIXTURE = ROOT / "tests/fixtures/entry-x-zone-search-cards.json"
BASE_FIXTURE = ROOT / "tests/fixtures/bound-effect-program-cards.json"
NEIGHBORS = {
    "Grizzly Bears": "activation-restriction-material-cards.json",
    "Lightning Bolt": "activation-restriction-material-cards.json",
    "Dryad Arbor": "additional-conditional-modifier-cards.json",
    "Universal Automaton": "changeling-characteristics-cards.json",
    "Elite Vanguard": "chosen-type-event-cards.json",
    "Mindlock Orb": "library-search-rules-cards.json",
}


class EntryXZoneSearchTests(InheritedNativeFixture, unittest.TestCase):
    register_fixture_cards_on_add = True
    choose_zone = named_searches.NamedZoneSearchRuntimeTests.choose_zone
    take = named_searches.NamedZoneSearchRuntimeTests.take
    packet = named_searches.NamedZoneSearchRuntimeTests.packet
    reject = named_searches.NamedZoneSearchRuntimeTests.reject
    shuffles = named_searches.NamedZoneSearchRuntimeTests.shuffles

    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        work = Path(cls.temporary.name)
        neighbors = []
        for name, fixture in NEIGHBORS.items():
            value = json.loads((ROOT / "tests/fixtures" / fixture).read_bytes())
            record, = (row for row in value["cards"] if row["name"] == name)
            neighbors.append(record)
        subset = work / "neighbors.json"
        subset.write_text(json.dumps({"schema_version": 1, "cards": neighbors, "rulings": []}), encoding="utf8")
        database = work / "entry-x-zone-search.sqlite3"
        build_fixture_database([BASE_FIXTURE, FIXTURE, subset], database)
        cls.db = CardDatabase(database)
        cls.registry = load_default_capability_registry()
        commander = "Generic Bound Commander"
        cls.deck = DeckDefinition("Entry X Siege", [
            DeckEntry(commander, 1, "commander"), DeckEntry("Generic Bound Plains", 30),
        ], [commander])

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temporary.cleanup()

    def compile(self, card):
        return compile_oracle_card(card, capability_registry=self.registry, capability_profile="commander_review")

    def test_exact_siege_faces_and_typed_entry_bound(self):
        record = self.db.lookup(CARD, fuzzy=False)
        ir = self.compile(record)
        self.assertEqual("exact", ir.status, ir.material_residuals)
        front, back = ir.faces
        entry, = front.nodes
        self.assertEqual(("permanent.enter.self", "battlefield", None),
                         (entry.event, entry.active_zone, entry.target_schema))
        self.assertEqual((EntryXAmountSpec().to_dict(), True), decode_zone_search(entry.effects[0]))
        self.assertIn(ENTRY_X_ZONE_SEARCH_CAPABILITY, entry.capability_dependencies)
        self.assertEqual(record.faces[0]["oracle_text"].splitlines()[1],
                         record.faces[0]["oracle_text"][entry.span.start:entry.span.end])
        back_capabilities = {capability for node in back.nodes for capability in node.capability_dependencies}
        self.assertIn("combat.block.reach", back_capabilities)
        self.assertIn("combat.damage.assignment.as_unblocked", back_capabilities)

    def test_mutations_and_neighbor_grammar_fail_closed(self):
        record = self.db.lookup(CARD, fuzzy=False)
        front, back = record.faces
        for before, after in (
            ("non-Human creature", "creature"),
            ("X or less", "X or greater"),
            ("your library this way, shuffle", "either zone this way, shuffle"),
            ("You and others can attack it.", "Creatures you control have flying."),
        ):
            with self.subTest(after=after):
                text = front["oracle_text"].replace(before, after)
                self.assertNotEqual(front["oracle_text"], text)
                self.assertNotEqual("exact", self.compile(
                    replace(record, faces=({**front, "oracle_text": text}, back)),
                ).status)
        with patch("quorune.compiler.cast_x_search_templates.cast_x_zone_search_effect",
                   return_value=cast_x_zone_search_effect()):
            self.assertNotEqual("exact", self.compile(record).status)
        descriptor = CastXZoneSearchSelection(("library", "graveyard"), 2, non_human=True).effect()
        for invalid in (
            {**descriptor, "shuffle_after": False},
            {**descriptor, "selector": {**descriptor["selector"], "excluded_subtypes": ["elf"]}},
            {**descriptor, "count": {"minimum": 1, "maximum": 2}},
        ):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                CastXZoneSearchSelection.from_effect(invalid)

    def add_private(self, game, name, ref):
        card = self.add(game.engine, name, zone="library", ref=ref)
        card.known_to = []
        card.revealed_to = []
        return card

    def cast_siege(self, game, card, x):
        action = "cast:" + card.ref
        offered = self.packet(game)["legal"]["actions"]
        self.assertIn(action, {row["id"] for row in offered})
        cast = game.act("pilot:A", {"action_id": action, "x": x, "pay": "auto"})
        self.assertTrue(cast.ok, cast.summary)
        self.assertEqual(x, game.state.stack[-1].x_value)
        self.assertEqual("battle.enter_protector", self.resolve(game).kind)
        self.reject(game, "pilot:A", {"action_id": "choose", "protector": "A"})
        selection = game.act("pilot:A", {"action_id": "choose", "protector": "B"})
        self.assertTrue(selection.ok, selection.summary)
        self.assertEqual("semantic.choice", self.resolve(game).kind)
        battle = game.state.cards[card.object_id]
        self.assertEqual(("battlefield", FRONT, "B", 6), (
            battle.zone, battle.active_face, battle.battle_protector, battle.counters.get("defense"),
        ))

    def search_schema(self, game):
        self.assertEqual("semantic.search", game.state.pending_decision.kind)
        return self.packet(game)["legal_actions"][0]["choice_schema"]

    def test_both_zones_native_cast_and_transform_replay_preserve_hidden_identity(self):
        game = self.session(107310201)
        game.state.config.semantic_policy = "trusted_only"
        battle = self.add(game.engine, CARD, zone="hand")
        eligible = self.add_private(game, "Grizzly Bears", "ikoria-hidden-bear")
        public_match = self.add(game.engine, "Dryad Arbor", zone="graveyard", ref="ikoria-public-arbor")
        changeling = self.add_private(game, "Universal Automaton", "ikoria-hidden-human-by-cda")
        human = self.add(game.engine, "Elite Vanguard", zone="graveyard", ref="ikoria-public-human")
        foreign = self.add(game.engine, "Dryad Arbor", seat="B", zone="graveyard", ref="ikoria-foreign-arbor")
        bolts = [self.add(game.engine, "Lightning Bolt", seat="B", zone="hand", ref=f"ikoria-bolt-{i}")
                 for i in range(2)]
        for card in (battle, changeling, *bolts):
            self.assert_closed(game, card)
        game.state.players["B"].mana_pool.update(R=2)
        self.prepare(game, mana={"G": 2, "C": 2})
        self.checkpoint(game)
        self.cast_siege(game, battle, 2)
        self.reject(game, "pilot:B", {"action_id": "choose", "choice": "both"})
        for seat in "ABCD":
            self.assertNotIn(eligible.ref, json.dumps(game.packet("pilot:" + seat, full=True)))
        game = self.replay(game, load=True)
        before = self.shuffles(game)
        self.choose_zone(game, "both")
        schema = self.search_schema(game)
        self.assertEqual((1, 1), (schema["minimum"], schema["maximum"]))
        self.assertCountEqual([eligible.ref, public_match.ref], schema["legal_refs"])
        trigger = game.state.stack[-1]
        exact = CastXZoneSearchSelection(("library", "graveyard"), 2, non_human=True).effect()
        verify_resolving_cast_x_zone_search(game.engine, trigger, exact)
        with self.assertRaises(GameRuleError):
            verify_resolving_cast_x_zone_search(
                game.engine, trigger, CastXZoneSearchSelection(("library", "graveyard"), 3, non_human=True).effect(),
            )
        with self.assertRaises(GameRuleError):
            verify_resolving_cast_x_zone_search(
                game.engine, replace(trigger, context={**trigger.context, "source_logical_object_id": "stale@0"}), exact,
            )
        for seat in "BCD":
            projected = json.dumps(game.packet("pilot:" + seat, full=True))
            self.assertNotIn(eligible.ref, projected)
            self.assertNotIn(changeling.ref, projected)
        for denied in ([], [eligible.ref, public_match.ref], [changeling.ref], [human.ref], [foreign.ref]):
            self.reject(game, "pilot:A", {"action_id": "choose", "search_cards": denied})
        game = self.replay(game, load=True)
        self.take(game, [eligible.ref])
        self.assertEqual(before + 1, self.shuffles(game))
        self.assertEqual("battlefield", game.state.cards[eligible.object_id].zone)
        self.assertEqual("graveyard", game.state.cards[public_match.object_id].zone)
        self.assertEqual("library", game.state.cards[changeling.object_id].zone)

        for bolt in bolts:
            self.cast_for(game, "B", bolt, [game.state.cards[battle.object_id].ref])
            self.resolve_top(game)
        self.assertEqual("battle.siege_defeated", self.resolve(game).kind)
        self.assertEqual("exile", game.state.cards[battle.object_id].zone)
        game = self.replay(game, load=True)
        result = game.act("pilot:A", {"action_id": "cast", "plan": "RULES_CHOICE"})
        self.assertTrue(result.ok, result.summary)
        self.assertIsNone(self.resolve(game))
        reverse = game.state.cards[battle.object_id]
        self.assertEqual(("battlefield", BACK), (reverse.zone, reverse.active_face))
        self.assertIn("reach", game.engine._combat_keywords(reverse))
        self.replay(game, load=True)

    def test_hidden_library_may_fail_to_find_and_shuffles_once(self):
        game = self.session(107310202)
        game.state.config.semantic_policy = "trusted_only"
        battle = self.add(game.engine, CARD, zone="hand")
        eligible = self.add_private(game, "Dryad Arbor", "ikoria-hidden-zero")
        expensive = self.add_private(game, "Grizzly Bears", "ikoria-hidden-too-high")
        self.assert_closed(game, battle)
        self.prepare(game, mana={"G": 2})
        self.checkpoint(game)
        self.cast_siege(game, battle, 0)
        before = self.shuffles(game)
        self.choose_zone(game, "library")
        schema = self.search_schema(game)
        self.assertEqual((0, 1, [eligible.ref]), (schema["minimum"], schema["maximum"], schema["legal_refs"]))
        self.reject(game, "pilot:A", {"action_id": "choose", "search_cards": [expensive.ref]})
        game = self.replay(game, load=True)
        self.take(game, [])
        self.assertEqual(before + 1, self.shuffles(game))
        self.assertEqual("library", game.state.cards[eligible.object_id].zone)
        self.replay(game, load=True)

    def test_uncast_entry_uses_zero_and_graveyard_search_is_mandatory_under_prohibition(self):
        game = self.session(107310203)
        game.state.config.semantic_policy = "trusted_only"
        battle = self.add(game.engine, CARD, zone="graveyard")
        forbidden = self.add_private(game, "Dryad Arbor", "ikoria-prohibited-library")
        eligible = self.add(game.engine, "Dryad Arbor", zone="graveyard", ref="ikoria-uncast-zero")
        expensive = self.add(game.engine, "Grizzly Bears", zone="graveyard", ref="ikoria-uncast-too-high")
        orb = self.add(game.engine, "Mindlock Orb", seat="B")
        self.assert_closed(game, battle)
        self.assert_closed(game, orb)
        self.prepare(game, mana={})
        engine = game.engine
        engine.permissions.invalidate_current()
        engine.state.pending_decision = None
        engine.move_card(
            battle.object_id, "battlefield", controller="A", battle_protector="B", semantic_events=True,
        )
        engine._stabilize()
        engine._grant_priority("A")
        engine.pump()
        self.checkpoint(game)
        self.assertEqual("semantic.choice", self.resolve(game).kind)
        before = self.shuffles(game)
        for denied in ("both", "library"):
            self.reject(game, "pilot:A", {"action_id": "choose", "choice": denied})
        self.choose_zone(game, "graveyard")
        schema = self.search_schema(game)
        self.assertEqual((1, 1, [eligible.ref]), (schema["minimum"], schema["maximum"], schema["legal_refs"]))
        self.assertNotIn(forbidden.ref, json.dumps(self.packet(game)))
        for denied in ([], [expensive.ref], [forbidden.ref]):
            self.reject(game, "pilot:A", {"action_id": "choose", "search_cards": denied})
        game = self.replay(game, load=True)
        self.take(game, [eligible.ref])
        self.assertEqual(before, self.shuffles(game))
        self.assertEqual("battlefield", game.state.cards[eligible.object_id].zone)
        self.assertEqual("graveyard", game.state.cards[expensive.object_id].zone)
        self.replay(game, load=True)
