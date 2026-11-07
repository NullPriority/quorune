"""Closed alternatives share one counted search and its private choice."""

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from common import ROOT
import inherited_composition_fixture as actions
from quorune.carddb import CardDatabase
from quorune.compiler.library_search_templates import fixed_counted_library_search_effect_template
from quorune.deck import DeckDefinition, DeckEntry
from quorune.library_search_model import (
    DISJUNCTIVE_LIBRARY_SEARCH_CAPABILITY_ID,
    FIXED_COUNTED_LIBRARY_SEARCH_CAPABILITY_ID,
    FixedCountedLibrarySearchTemplate,
    counted_search_bound_dependencies,
)
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database


class DisjunctiveLibrarySearchModelTests(unittest.TestCase):
    text = (
        "Search your library for a basic Plains card or a creature card with mana value 1 or less, "
        "reveal it, put it into your hand, then shuffle."
    )

    def test_disjunctive_search_codec_binds_qualifiers_and_one_shared_count(self):
        template = fixed_counted_library_search_effect_template(self.text)
        self.assertIsNotNone(template)
        effect = template.effect()
        self.assertEqual(3, effect["schema_version"])
        self.assertEqual({"minimum": 1, "maximum": 1}, effect["count"])
        self.assertEqual([
            {"types": ["land"], "subtypes_any": ["plains"], "supertypes": ["basic"]},
            {"types": ["creature"], "mana_value": {"maximum": 1}},
        ], effect["selector"]["alternatives"])
        self.assertEqual(template, FixedCountedLibrarySearchTemplate.from_effect(effect))
        self.assertEqual(
            (FIXED_COUNTED_LIBRARY_SEARCH_CAPABILITY_ID, DISJUNCTIVE_LIBRARY_SEARCH_CAPABILITY_ID),
            counted_search_bound_dependencies(effect),
        )
        for malformed in (
            {**effect, "schema_version": 2},
            {**effect, "selector": {"alternatives": [effect["selector"], {"types": ["land"]}]}},
            {**effect, "selector": {"alternatives": [{"types": ["land"]}, {"types": ["land"]}]}},
            {**effect, "selector": {"alternatives": [{}, {"types": ["land"]}]}},
            {**effect, "selector": {**effect["selector"], "names": ["Other Card"]}},
        ):
            with self.subTest(malformed=malformed), self.assertRaises(ValueError):
                counted_search_bound_dependencies(malformed)
        for unsupported in (
            self.text.replace("or a creature", "and/or a creature"),
            self.text.replace("basic Plains card or a creature card with mana value 1 or less", "legendary artifact or creature card"),
        ):
            self.assertIsNone(fixed_counted_library_search_effect_template(unsupported))

    def test_shared_type_and_color_words_qualify_the_whole_list(self):
        types = fixed_counted_library_search_effect_template(
            "Search your library for up to two artifact, creature, and/or enchantment cards with mana value 1 or less, "
            "reveal them, put them into your hand, then shuffle."
        )
        colors = fixed_counted_library_search_effect_template(
            "Search your library for a blue or black creature card, reveal it, put it into your hand, then shuffle."
        )
        self.assertEqual(2, types.effect()["schema_version"])
        self.assertEqual(
            {"types_any": ["artifact", "creature", "enchantment"], "mana_value": {"maximum": 1}},
            types.effect()["selector"],
        )
        self.assertEqual({"types": ["creature"], "colors_any": ["B", "U"]}, colors.effect()["selector"])

    def test_omitting_the_alternative_parser_is_detected(self):
        with patch("quorune.compiler.library_search_templates._disjunctive_selector", return_value=None):
            with self.assertRaises(AssertionError):
                self.test_disjunctive_search_codec_binds_qualifiers_and_one_shared_count()


class DisjunctiveLibrarySearchRuntimeTests(actions.InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        path = Path(cls.temporary.name) / "disjunctive-library-search.sqlite3"
        build_fixture_database([
            ROOT / "tests/fixtures/bound-effect-program-cards.json",
            ROOT / "tests/fixtures/qualified-entry-state-cards.json",
            ROOT / "tests/fixtures/intrinsic-mana-ability-loss-cards.json",
            ROOT / "tests/fixtures/kicked-entry-triggers.json",
            ROOT / "tests/fixtures/public-self-replacement-cards.json",
            ROOT / "tests/fixtures/disjunctive-library-search-cards.json",
        ], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Disjunctive counted search", [
            DeckEntry("Generic Bound Commander", 1, "commander"),
            DeckEntry("Generic Bound Plains", 30),
        ], ["Generic Bound Commander"])

    def prepare(self, session, **kwargs):
        session.state.config.semantic_policy = "trusted_only"
        super().prepare(session, **kwargs)

    def shuffles(self, session):
        return session.state.players["A"].stats.get("shuffle_count", 0)

    def choose(self, session, **fields):
        result = session.act("pilot:A", {"action_id": "choose", **fields})
        self.assertTrue(result.ok, result.summary)

    def legal_search_refs(self, session):
        self.assertEqual("semantic.search", self.resolve(session).kind)
        return session.packet("pilot:A", full=True)["decision"]["legal_actions"][0]["choice_schema"]["legal_refs"]

    def test_original_vine_decline_then_private_basic_or_gate_choice(self):
        session = self.session(7012301)
        first = self.add(session.engine, "Gatecreeper Vine", zone="hand", ref="declined-vine")
        second = self.add(session.engine, "Gatecreeper Vine", zone="hand", ref="accepted-vine")
        gate = self.add(session.engine, "Azorius Guildgate", zone="library", ref="private-search-gate")
        off_type = self.add(session.engine, "Dryad Arbor", zone="library", ref="private-nonbasic-land")
        self.assert_closed(session, first)
        self.prepare(session, mana={"C": 2, "G": 2})
        count = self.shuffles(session)
        self.checkpoint(session)

        self.cast(session, "A", first)
        self.assertEqual("semantic.choice", self.resolve(session).kind)
        self.choose(session, choice="decline")
        self.settle(session)
        self.assertEqual(count, self.shuffles(session))
        self.cast(session, "A", second)
        self.assertEqual("semantic.choice", self.resolve(session).kind)
        self.choose(session, choice="apply")
        legal = self.legal_search_refs(session)
        self.assertIn(gate.ref, legal)
        self.assertNotIn(off_type.ref, legal)
        self.assertGreater(len(legal), 1)
        for viewer in "BCD":
            self.assertNotIn(gate.ref, json.dumps(session.packet("pilot:" + viewer, full=True)))
        before = authoritative_state_hash(session.state)
        for principal, ref in (("pilot:B", gate.ref), ("pilot:A", off_type.ref)):
            self.assertFalse(session.act(principal, {"action_id": "choose", "search_cards": [ref]}).ok)
            self.assertEqual(before, authoritative_state_hash(session.state))
        session = self.replay(session, load=True)
        self.choose(session, search_cards=[gate.ref])
        self.settle(session)
        self.assertEqual("hand", session.state.cards[gate.object_id].zone)
        self.assertEqual(count + 1, self.shuffles(session))
        self.replay(session, load=True)

    def test_original_map_moves_two_deserts_tapped_from_one_branch(self):
        session = self.session(7012302)
        spell = self.add(session.engine, "Map the Frontier", zone="hand")
        lands = [self.add(session.engine, "Desert", zone="library", ref=f"search-desert-{index}") for index in range(2)]
        self.assert_closed(session, spell)
        self.prepare(session, mana={"C": 3, "G": 1})
        count = self.shuffles(session)
        self.checkpoint(session)
        self.cast(session, "A", spell)
        self.assertTrue({card.ref for card in lands}.issubset(self.legal_search_refs(session)))
        before = authoritative_state_hash(session.state)
        self.assertFalse(session.act("pilot:A", {"action_id": "choose", "search_cards": [lands[0].ref] * 2}).ok)
        self.assertEqual(before, authoritative_state_hash(session.state))
        session = self.replay(session, load=True)
        self.choose(session, search_cards=[card.ref for card in lands])
        self.settle(session)
        for land in lands:
            actual = session.state.cards[land.object_id]
            self.assertEqual(("battlefield", True), (actual.zone, actual.tapped))
        self.assertEqual(count + 1, self.shuffles(session))
        self.replay(session, load=True)

    def test_original_gearhulk_offers_overlapping_type_once_with_one_bound(self):
        session = self.session(7012303)
        spell = self.add(session.engine, "Brightglass Gearhulk", zone="hand")
        both = self.add(session.engine, "Memnite", zone="library")
        creature = self.add(session.engine, "Llanowar Elves", zone="library")
        instant = self.add(session.engine, "Generic Bound Growth", zone="library")
        expensive = self.add(session.engine, "Generic Bound Mentor", zone="library")
        self.assert_closed(session, spell)
        self.prepare(session, mana={"G": 2, "W": 2})
        count = self.shuffles(session)
        self.checkpoint(session)
        self.cast(session, "A", spell)
        self.assertEqual("semantic.choice", self.resolve(session).kind)
        self.choose(session, choice="apply")
        legal = self.legal_search_refs(session)
        self.assertEqual(1, legal.count(both.ref))
        self.assertIn(creature.ref, legal)
        self.assertNotIn(instant.ref, legal)
        self.assertNotIn(expensive.ref, legal)
        self.choose(session, search_cards=[both.ref, creature.ref])
        self.settle(session)
        for card in (both, creature):
            self.assertEqual("hand", session.state.cards[card.object_id].zone)
        self.assertEqual(count + 1, self.shuffles(session))
        self.replay(session, load=True)
