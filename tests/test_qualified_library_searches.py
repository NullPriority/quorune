from __future__ import annotations

"""Pinned CR 202.3/205/701.23: one exact, actor-private restrictive query."""

from dataclasses import replace
from pathlib import Path
import json
import tempfile
import unittest
from unittest.mock import patch

from common import ROOT
from quorune.carddb import CardDatabase
from quorune.card_programs.adapters import compile_best_available_card_program
from quorune.card_programs.binding import bind_card_program_runtime
from quorune.deck import DeckDefinition, DeckEntry
from quorune.object_predicate import ObjectQuerySpec
from quorune.oracle_ir import compile_oracle_card, register_generated_programs
from quorune.rules.capabilities import load_default_capability_registry
from quorune.record import authoritative_state_hash
from quorune.semantics import SemanticRegistry
from scripts.build_test_database import build_fixture_database
import test_bound_effect_programs as witnesses
import test_attachment_followup_bindings as attachment_controls

FIXTURE = ROOT / "tests/fixtures/qualified-library-search-cards.json"
CANDIDATES = ("Shield-Wall Sentinel", "Sphinx Summoner", "Woodland Bellower", "Transit Mage", "Shrine Steward")
CONTROLS = ("Trophy Mage", "Fierce Empath", "Ranger-Captain of Eos")


class QualifiedLibrarySearchModelTests(unittest.TestCase):
    def test_closed_query_codec_keeps_every_qualification_and_destination(self):
        from quorune.qualified_library_search_model import QualifiedLibrarySearchSpec
        from quorune.replacement.immutable import FrozenMap
        cases = (
            QualifiedLibrarySearchSpec(ObjectQuerySpec(zones=("library",), types_all=("artifact", "creature")), "hand", True),
            QualifiedLibrarySearchSpec(ObjectQuerySpec(zones=("library",), types_all=("creature",), keywords_all=("defender",)), "hand", True),
            QualifiedLibrarySearchSpec(ObjectQuerySpec(zones=("library",), types_all=("creature",), colors_any=("G",),
                excluded_supertypes=("legendary",)), "battlefield", False, mana_value_max=3),
            QualifiedLibrarySearchSpec(ObjectQuerySpec(zones=("library",), types_all=("artifact",)), "hand", True,
                mana_values_any=(4, 5)),
            QualifiedLibrarySearchSpec(ObjectQuerySpec(zones=("library",), subtypes_any=("aura", "shrine")), "hand", True),
        )
        for spec in cases:
            with self.subTest(spec=spec):
                effect = spec.effect()
                self.assertEqual(7, effect["schema_version"])
                self.assertEqual(spec, QualifiedLibrarySearchSpec.from_effect(FrozenMap(effect)))
                self.assertEqual(spec.query, ObjectQuerySpec.from_dict(effect["qualification"]["query"]))
                self.assertEqual({"predicate": "qualified_library_query"}, effect["selector"])
                self.assertEqual({"minimum": 1, "maximum": 1}, effect["count"])
                self.assertTrue(effect["shuffle_after"])

    def test_unowned_zone_state_and_hidden_nonquality_predicates_are_rejected(self):
        from quorune.qualified_library_search_model import QualifiedLibrarySearchSpec
        for query in (ObjectQuerySpec(), ObjectQuerySpec(zones=("graveyard",), types_all=("creature",)),
                      ObjectQuerySpec(zones=("library",), types_all=("creature",), controller="B"),
                      ObjectQuerySpec(zones=("library",), types_all=("creature",), tapped=True),
                      ObjectQuerySpec(zones=("library",), excluded_types=("land",))):
            with self.subTest(query=query), self.assertRaises(ValueError):QualifiedLibrarySearchSpec(query, "hand", True)
        query = ObjectQuerySpec(zones=("library",), types_all=("creature",))
        for kwargs in (dict(mana_values_any=(3, 3)), dict(mana_values_any=(3, True)),
                       dict(mana_values_any=(3, 5), mana_value_max=5), dict(mana_value_max=-1)):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):QualifiedLibrarySearchSpec(query, "hand", True, **kwargs)

    def test_legacy_selector_codec_and_schema6_remain_unchanged(self):
        from quorune.library_search_model import search_selector
        from quorune.compiler.library_search_templates import fixed_library_search_effect_template
        from quorune.named_search_model import NamedZoneSearchSpec
        with self.assertRaises(ValueError):search_selector(ObjectQuerySpec(types_all=("creature",), keywords_all=("defender",)))
        old = fixed_library_search_effect_template("Search your library for a basic land card, reveal it, put it into your hand, then shuffle.")
        self.assertNotIn("qualification", old.compiled()[1][0])
        self.assertNotEqual(7, old.compiled()[1][0].get("schema_version"))
        self.assertEqual(6, NamedZoneSearchSpec("Command Tower", False).search_instruction("library")["schema_version"])


class QualifiedLibrarySearchCompilerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.registry = load_default_capability_registry()

    def test_exact_queries_compile_without_weakening_into_existing_selectors(self):
        cases = (
            "Search your library for a creature card with defender, reveal it, put it into your hand, then shuffle.",
            "Search your library for an artifact creature card, reveal it, put it into your hand, then shuffle.",
            "Search your library for a nonlegendary green creature card with mana value 3 or less, put it onto the battlefield, then shuffle.",
            "Search your library for an artifact card with mana value 4 or 5, reveal it, put it into your hand, then shuffle.",
            "Search your library for an Aura or Shrine card, reveal it, put it into your hand, then shuffle.",
        )
        for text in cases:
            with self.subTest(text=text):
                ir = compile_oracle_card(witnesses.record(text), capability_registry=self.registry, capability_profile="commander_review")
                self.assertEqual("exact", ir.status, ir.material_residuals)
                node, = ir.faces[0].nodes;effect, = node.effects
                self.assertEqual(7, effect["schema_version"])
                self.assertIn("qualification", effect)

    def test_new_lowering_and_query_authorities_cannot_be_withdrawn(self):
        from quorune.rules.capabilities import CapabilityRegistry
        text = "Search your library for an artifact creature card, reveal it, put it into your hand, then shuffle."
        def compile(registry):return compile_oracle_card(witnesses.record(text), capability_registry=registry,
            capability_profile="commander_review")
        self.assertEqual("exact", compile(self.registry).status)
        with patch("quorune.compiler.qualified_library_search_templates.qualified_library_search_template", return_value=None):
            self.assertNotEqual("exact", compile(self.registry).status)
        for missing in ("library.search.qualified_card", "library.search.fixed_counted", "zone.change.destination_replacement"):
            raw = json.loads((ROOT / "quorune/rules/capability-registry.json").read_text(encoding="utf-8"))
            next(row for row in raw["capabilities"] if row["id"] == missing).update(status="blocked", blockers=["qualified owner withdrawn"])
            registry = CapabilityRegistry(raw);registry.mark_evidence_verified(self.registry.evidence_fingerprint)
            with self.subTest(missing=missing):self.assertNotEqual("exact", compile(registry).status)

    def test_unknown_qualities_counts_and_destinations_stay_material(self):
        from quorune.compiler.qualified_library_search_templates import qualified_library_search_template
        for body in (
            "Search your library for a creature card with flying and defender, reveal it, put it into your hand, then shuffle.",
            "Search your library for a nonlegendary green creature card with mana value X or less, put it onto the battlefield, then shuffle.",
            "Search your library for an artifact card with mana value 4 or 5 or 6, reveal it, put it into your hand, then shuffle.",
            "Search your library for two artifact creature cards, reveal them, put them into your hand, then shuffle.",
            "Search your library for an Aura or Shrine card, put it onto the battlefield attached to this creature, then shuffle.",
        ):
            with self.subTest(body=body):self.assertIsNone(qualified_library_search_template(body))


class QualifiedLibrarySearchRegistrationTests(unittest.TestCase):
    def test_original_five_close_without_counting_existing_controls_or_unknown_neighbor(self):
        registry = load_default_capability_registry();semantics = SemanticRegistry()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "complete-search-queries.sqlite3"
            build_fixture_database([FIXTURE], path)
            with CardDatabase(path) as db:
                for name in (*CANDIDATES, *CONTROLS, "Vision Quest"):
                    with self.subTest(name=name):
                        card = db.lookup(name, fuzzy=False)
                        register_generated_programs(db, semantics, (card,), trust_level="provisional",
                            capability_registry=registry, capability_profile="commander_review",
                            promote_exact_runtime_handlers=True, promote_exact_trigger_programs=True,
                            promote_exact_effect_programs=True, promote_exact_capability_declarations=True)
                        program = compile_best_available_card_program(db, card, semantic_registry=semantics,
                            capability_registry=registry, capability_profile="commander_review")
                        bound = bind_card_program_runtime(program, capability_registry=registry, profile="commander_review")
                        self.assertEqual(name != "Vision Quest", bool(bound["strict_capability_ready"]
                            and bound["trust_basis"] == "capability_closed"), bound["blockers"])


class QualifiedLibrarySearchRuntimeTests(unittest.TestCase):
    register_fixture_cards_on_add = True
    session = attachment_controls.AttachmentFollowupRuntimeTests.session
    add = attachment_controls.AttachmentFollowupRuntimeTests.add
    prepare = attachment_controls.AttachmentFollowupRuntimeTests.prepare
    closed = attachment_controls.AttachmentFollowupRuntimeTests.closed
    actions = attachment_controls.AttachmentFollowupRuntimeTests.actions
    cast_offered = attachment_controls.AttachmentFollowupRuntimeTests.cast_offered
    checkpoint = attachment_controls.AttachmentFollowupRuntimeTests.checkpoint
    resolve = attachment_controls.AttachmentFollowupRuntimeTests.resolve
    replay = attachment_controls.AttachmentFollowupRuntimeTests.replay

    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory();path = Path(cls.temporary.name) / "qualified-library.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures/bound-effect-program-cards.json", FIXTURE], path)
        cls.db = CardDatabase(path);cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Qualified search witnesses", [DeckEntry("Generic Bound Commander", 1, "commander"),
            DeckEntry("Generic Bound Plains", 30)], ["Generic Bound Commander"])

    @classmethod
    def tearDownClass(cls):cls.db.close();cls.temporary.cleanup()

    def world(self, seed, name, choices, *, controls=()):
        session = self.session(seed);spell = self.add(session.engine, name, zone="hand")
        cards = {choice:self.add(session.engine, choice, zone="library", ref=f"qualified-choice-{i}")
                 for i, choice in enumerate(choices)}
        extras = [self.add(session.engine, control, seat="B", ref=f"qualified-control-{i}")
                  for i, control in enumerate(controls)]
        self.prepare(session, spell, {color: 20 for color in ("C", "W", "U", "B", "R", "G")})
        return session, spell, cards, extras

    def packet(self, session):return session.packet("pilot:A", full=True)["decision"]["ctx"]

    def begin(self, session, spell, *, apply=True):
        self.cast_offered(session, spell)
        self.assertEqual("semantic.choice", self.resolve(session).kind)
        result = session.act("pilot:A", {"action_id": "choose", "choice": "apply" if apply else "decline"})
        self.assertTrue(result.ok, result.summary)
        if apply:self.assertEqual("semantic.search", session.state.pending_decision.kind)
        else:self.assertIsNone(self.resolve(session))

    def reject(self, session, principal, response):
        before = authoritative_state_hash(session.state);result = session.act(principal, response)
        self.assertFalse(result.ok, result.summary);self.assertEqual(before, authoritative_state_hash(session.state))

    def take(self, session, cards):
        result = session.act("pilot:A", {"action_id": "choose", "search_cards": [card.ref for card in cards]})
        self.assertTrue(result.ok, result.summary);self.assertIsNone(self.resolve(session))

    def test_original_hand_searches_offer_exact_conjunction_keyword_mana_or_subtypes(self):
        cases = (
            ("Sphinx Summoner", ("Ornithopter", "Sol Ring", "Wall of Omens"), {"Ornithopter"}),
            ("Shield-Wall Sentinel", ("Wall of Omens", "Ornithopter", "Sol Ring"), {"Wall of Omens"}),
            ("Transit Mage", ("Thran Dynamo", "Gilded Lotus", "Worn Powerstone", "Birgi, God of Storytelling"), {"Thran Dynamo", "Gilded Lotus"}),
            ("Shrine Steward", ("Pacifism", "Honden of Seeing Winds", "Sol Ring", "Llanowar Elves"), {"Pacifism", "Honden of Seeing Winds"}),
        )
        for index, (name, choices, expected) in enumerate(cases):
            with self.subTest(name=name):
                session, spell, cards, _ = self.world(296100 + index, name, choices)
                self.checkpoint(session);self.begin(session, spell)
                schema = self.packet(session)["legal_actions"][0]["choice_schema"]
                self.assertEqual({cards[n].ref for n in expected}, set(schema["legal_refs"]))
                self.assertEqual(0, schema["minimum"]);self.assertEqual(1, schema["maximum"])
                for seat in "BCD":
                    for card in cards.values():self.assertNotIn(card.ref, str(session.packet("pilot:" + seat, full=True)))
                bad = next(card for name, card in cards.items() if name not in expected)
                self.reject(session, "pilot:A", {"action_id": "choose", "search_cards": [bad.ref]})
                chosen = cards[next(iter(sorted(expected)))]
                self.reject(session, "pilot:B", {"action_id": "choose", "search_cards": [chosen.ref]})
                session = self.replay(session, load=True);self.take(session, [chosen])
                self.assertEqual("hand", session.state.cards[chosen.object_id].zone)
                self.replay(session, load=True)

    def test_woodland_requires_green_nonlegendary_creature_and_front_face_mana_value(self):
        choices = ("Llanowar Elves", "Elvish Archdruid", "Dryad Arbor", "Ivy Elemental",
                   "Ezuri, Renegade Leader", "Thran Dynamo", "Ornithopter", "Wall of Omens")
        expected = {"Llanowar Elves", "Elvish Archdruid", "Dryad Arbor", "Ivy Elemental"}
        session, spell, cards, _ = self.world(296110, "Woodland Bellower", choices)
        self.closed(session, cards["Llanowar Elves"]);self.checkpoint(session);self.begin(session, spell)
        schema = self.packet(session)["legal_actions"][0]["choice_schema"]
        self.assertEqual({cards[n].ref for n in expected}, set(schema["legal_refs"]))
        for name in set(choices) - expected:
            self.reject(session, "pilot:A", {"action_id": "choose", "search_cards": [cards[name].ref]})
        self.take(session, [cards["Llanowar Elves"]])
        result = session.state.cards[cards["Llanowar Elves"].object_id]
        self.assertEqual("battlefield", result.zone);self.assertEqual("A", result.controller);self.assertFalse(result.tapped)
        self.replay(session, load=True)

    def test_optional_decline_and_private_failure_keep_their_distinct_shuffle_behavior(self):
        for index, decline in enumerate((True, False)):
            with self.subTest(decline=decline):
                session, spell, cards, _ = self.world(296120 + index, "Sphinx Summoner", ("Ornithopter", "Sol Ring"))
                self.checkpoint(session);before = session.state.players["A"].stats.get("shuffle_count", 0)
                self.begin(session, spell, apply=not decline)
                if not decline:self.take(session, [])
                self.assertEqual(before + int(not decline), session.state.players["A"].stats.get("shuffle_count", 0))
                self.assertEqual("library", session.state.cards[cards["Ornithopter"].object_id].zone)
                self.replay(session, load=True)

    def test_real_search_prohibition_skips_optional_search_without_shuffle(self):
        session, spell, cards, extras = self.world(296130, "Shield-Wall Sentinel", ("Wall of Omens",), controls=("Mindlock Orb",))
        self.closed(session, extras[0]);self.checkpoint(session);before = session.state.players["A"].stats.get("shuffle_count", 0)
        self.cast_offered(session, spell);self.assertIsNone(self.resolve(session))
        self.assertEqual("library", session.state.cards[cards["Wall of Omens"].object_id].zone)
        self.assertEqual(before, session.state.players["A"].stats.get("shuffle_count", 0));self.replay(session, load=True)

    def test_real_search_window_limits_the_new_query_and_still_shuffles_the_entire_library(self):
        session, spell, cards, extras = self.world(296140, "Transit Mage", ("Thran Dynamo",), controls=("Aven Mindcensor",))
        for index in range(4):self.add(session.engine, "Generic Bound Plains", zone="library", ref=f"qualified-top-{index}")
        self.closed(session, extras[0]);self.checkpoint(session);before = session.state.players["A"].stats.get("shuffle_count", 0)
        self.begin(session, spell)
        self.assertNotIn(cards["Thran Dynamo"].ref, str(self.packet(session)))
        self.reject(session, "pilot:A", {"action_id": "choose", "search_cards": [cards["Thran Dynamo"].ref]})
        self.take(session, []);self.assertEqual(before + 1, session.state.players["A"].stats.get("shuffle_count", 0))
        self.replay(session, load=True)

    def test_battlefield_result_uses_real_entry_and_queues_its_original_trigger(self):
        session, spell, cards, _ = self.world(296150, "Woodland Bellower", ("Wall of Blossoms",))
        self.closed(session, cards["Wall of Blossoms"]);self.checkpoint(session)
        before = len(session.state.players["A"].draw_history);self.begin(session, spell)
        self.take(session, [cards["Wall of Blossoms"]])
        self.assertEqual("battlefield", session.state.cards[cards["Wall of Blossoms"].object_id].zone)
        self.assertEqual(before + 1, len(session.state.players["A"].draw_history));self.replay(session, load=True)

    def test_recursive_payload_minimum_requires_new_authority_without_coverage(self):
        from quorune.card_programs.binding import bind_semantic_program_runtime
        session, spell, _, _ = self.world(296160, "Sphinx Summoner", ("Ornithopter",))
        missing = "library.search.qualified_card"
        program = next(p for p in session.engine.semantics.programs_for_oracle(spell.oracle_id) if missing in p.capability_dependencies)
        deps = tuple(c for c in program.capability_dependencies if c != missing)
        changed = replace(program, coverage=(), capability_dependencies=deps,
            capability_closure=self.registry.closure(deps, profile="commander_review").to_dict())
        bound = bind_semantic_program_runtime(changed, capability_registry=self.registry, profile="commander_review")
        self.assertFalse(bound["strict"]);self.assertIn("capability:undeclared_runtime_dependency:" + missing, bound["blockers"])

    def test_query_conjunction_omission_is_killed_by_real_offered_candidates(self):
        session, spell, cards, _ = self.world(296170, "Sphinx Summoner", ("Ornithopter", "Sol Ring", "Wall of Omens"))
        self.checkpoint(session)
        with patch("quorune.rules.qualified_library_search.object_matches_query",
                   side_effect=lambda row,query:bool(set(row.types).intersection(query.types_all))):
            self.begin(session, spell)
            schema = self.packet(session)["legal_actions"][0]["choice_schema"]
            with self.assertRaises(AssertionError):self.assertNotIn(cards["Sol Ring"].ref, schema["legal_refs"])


if __name__ == "__main__":unittest.main()
