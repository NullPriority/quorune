"""Closed alternatives share one counted search and its private choice."""

from dataclasses import replace
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



class SharedLandTypeSearchRuntimeTests(actions.InheritedNativeFixture, unittest.TestCase):
    def prepare(self, game, **kwargs):
        game.state.config.semantic_policy = "trusted_only"
        super().prepare(game, **kwargs)

    shuffles = DisjunctiveLibrarySearchRuntimeTests.shuffles
    choose = DisjunctiveLibrarySearchRuntimeTests.choose
    legal_search_refs = DisjunctiveLibrarySearchRuntimeTests.legal_search_refs
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        path = Path(cls.temporary.name) / "shared-basic-land-search.sqlite3"
        build_fixture_database([
            ROOT / "tests/fixtures/bound-effect-program-cards.json",
            ROOT / "tests/fixtures/library-search-rules-cards.json",
            ROOT / "tests/fixtures/library-search-dispatch-cards.json",
            ROOT / "tests/fixtures/fixed-basic-type-setting-cards.json",
            ROOT / "tests/fixtures/fixed-qualified-stack-copy-cards.json",
            ROOT / "tests/fixtures/shared-land-type-search-cards.json",
        ], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Shared basic lands", [
            DeckEntry("Generic Bound Commander", 1, "commander"),
            DeckEntry("Generic Bound Plains", 30),
        ], ["Generic Bound Commander"])

    def activate_search(self, game, source):
        self.priority(game, "A")
        actions = game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        activation, = (action for action in actions if action["id"] == f"activate:{source.ref}:ab3")
        before = authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:C", {"action_id": activation["id"], "pay": "auto"}).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        result = game.act("pilot:A", {"action_id": activation["id"], "pay": "auto"})
        self.assertTrue(result.ok, result.summary)
        self.assertEqual("graveyard", game.state.cards[source.object_id].zone)
        self.assertEqual(0, sum(game.state.players["A"].mana_pool.values()))

    def selected_state(self, game, card):
        current = game.state.cards[card.object_id]
        return current.zone, current.tapped

    def test_actual_landscape_places_same_subtype_basic_lands_tapped_from_a_private_choice(self):
        game = self.session(70123021)
        source = self.add(game.engine, "Myriad Landscape")
        island = self.add(game.engine, "Island", zone="library", ref="shared-island")
        snow = self.add(game.engine, "Snow-Covered Island", zone="library", ref="shared-snow-island")
        mountain = self.add(game.engine, "Mountain", zone="library", ref="shared-mountain")
        wastes = [self.add(game.engine, "Wastes", zone="library", ref=f"shared-wastes-{i}") for i in range(2)]
        dual = self.add(game.engine, "Volcanic Island", zone="library", ref="shared-nonbasic")
        self.assert_closed(game, source)
        self.prepare(game, mana={"C": 2})
        baseline = self.shuffles(game)
        self.checkpoint(game)
        self.activate_search(game, source)
        legal = self.legal_search_refs(game)
        self.assertTrue({island.ref, snow.ref, mountain.ref, *(card.ref for card in wastes)} <= set(legal))
        self.assertNotIn(dual.ref, legal)
        decision = game.packet("pilot:A", full=True)["decision"]["legal_actions"][0]["choice_schema"]
        members = decision["pair_constraint"]["land_types"]
        self.assertEqual((0, 2), (decision["minimum"], decision["maximum"]))
        self.assertEqual(["island"], members[island.ref])
        self.assertEqual(["island"], members[snow.ref])
        self.assertEqual([], members[wastes[0].ref])
        for seat in "BCD":
            packet = json.dumps(game.packet("pilot:" + seat, full=True))
            self.assertNotIn(island.ref, packet)
            self.assertNotIn(snow.ref, packet)
        before = authoritative_state_hash(game.state)
        for seat, refs in (
            ("B", [island.ref, snow.ref]), ("A", [island.ref, mountain.ref]),
            ("A", [card.ref for card in wastes]), ("A", [island.ref, dual.ref]),
            ("A", [snow.ref, snow.ref]),
        ):
            result = game.act("pilot:" + seat, {"action_id": "choose", "search_cards": refs})
            self.assertFalse(result.ok, result.summary)
            self.assertEqual(before, authoritative_state_hash(game.state))
        game = self.replay(game, load=True)
        self.choose(game, search_cards=[snow.ref, island.ref])
        self.settle(game)
        for card in (island, snow):
            self.assertEqual(("battlefield", True), self.selected_state(game, card))
        for card in (mountain, *wastes, dual):
            self.assertEqual("library", game.state.cards[card.object_id].zone)
        self.assertEqual(baseline + 1, self.shuffles(game))
        self.replay(game, load=True)

    def test_actual_landscape_zero_or_one_and_prohibited_search_still_shuffle(self):
        for mode in ("zero", "single", "prohibited"):
            with self.subTest(mode=mode):
                game = self.session(70123022 + ("zero", "single", "prohibited").index(mode))
                source = self.add(game.engine, "Myriad Landscape")
                wastes = self.add(game.engine, "Wastes", zone="library", ref="shared-one-wastes")
                if mode == "prohibited":
                    obstacle = self.add(game.engine, "Mindlock Orb", seat="B")
                    self.assert_closed(game, obstacle)
                self.prepare(game, mana={"C": 2})
                baseline = self.shuffles(game)
                self.checkpoint(game)
                self.activate_search(game, source)
                if mode != "prohibited":
                    self.assertIn(wastes.ref, self.legal_search_refs(game))
                    self.choose(game, search_cards=[wastes.ref] if mode == "single" else [])
                else:
                    self.assertIsNone(self.resolve(game))
                self.settle(game)
                self.assertEqual(baseline + 1, self.shuffles(game))
                self.assertEqual(("battlefield", True) if mode == "single" else ("library", False),
                                 self.selected_state(game, wastes))
                self.replay(game, load=True)

    def test_actual_landscape_keeps_tapped_entry_and_ordinary_colorless_mana(self):
        game = self.session(70123025)
        source = self.add(game.engine, "Myriad Landscape", zone="hand")
        untap = self.add(game.engine, "Twiddle", seat="B", zone="hand")
        self.assert_closed(game, source)
        self.prepare(game)
        game.state.players["B"].mana_pool["U"] = 1
        self.checkpoint(game)
        play = game.act("pilot:A", {"action_id": "play-land:" + source.ref})
        self.assertTrue(play.ok, play.summary)
        self.assertEqual(("battlefield", True), self.selected_state(game, source))
        self.cast(game, "B", untap, [source.ref])
        self.assertEqual("semantic.choice", self.resolve(game).kind)
        choice = game.act("pilot:B", {"action_id": "choose", "choice": "untap"})
        self.assertTrue(choice.ok, choice.summary)
        self.priority(game, "A")
        actions = game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        self.assertIn("activate:" + source.ref + ":ab2", [action["id"] for action in actions])
        mana = game.act("pilot:A", {"action_id": "activate:" + source.ref + ":ab2"})
        self.assertTrue(mana.ok, mana.summary)
        self.assertEqual(1, game.state.players["A"].mana_pool["C"])
        self.assertEqual(("battlefield", True), self.selected_state(game, source))
        self.replay(game, load=True)

    def test_shared_land_type_requires_its_exact_descriptor_and_independent_authority(self):
        from quorune.card_programs.binding import bind_semantic_program_runtime
        from quorune.library_search_model import SHARED_LAND_TYPE_SEARCH_CAPABILITY as CAP, SHARED_LAND_TYPE_SEARCH_FIELD

        game = self.session(70123026)
        card = self.add(game.engine, "Myriad Landscape")
        self.assert_closed(game, card)
        (program,) = [value for value in game.engine.semantics.programs_for_oracle(card.oracle_id, event="activate")
                      if any(effect.get("op") == "search" for effect in value.effects)]
        (effect,) = program.effects
        self.assertTrue(effect[SHARED_LAND_TYPE_SEARCH_FIELD])
        for changed in (
            {**effect, SHARED_LAND_TYPE_SEARCH_FIELD: False},
            {**effect, "enters_tapped_override": False},
            {**effect, "selector": {"types": ["land"]}},
            {**effect, "count": {"minimum": 0, "maximum": 3}},
        ):
            with self.assertRaises(ValueError):
                counted_search_bound_dependencies(changed)
        deps = tuple(value for value in program.capability_dependencies if value != CAP)
        changed = replace(program, capability_dependencies=deps,
                          capability_closure=self.registry.closure(deps, profile="commander_review").to_dict())
        bound = bind_semantic_program_runtime(changed, capability_registry=self.registry, profile="commander_review")
        self.assertIn("capability:undeclared_runtime_dependency:" + CAP, bound["blockers"])
