from __future__ import annotations

"""Exact pinned entry trigger and actor-private creature keyword top search."""

from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
from random import Random
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from test_controller_creature_keyword_absence import register_card
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.qualified_library_search_model import (
    CREATURE_KEYWORD_TOP_SEARCH_CAPABILITY,
    QualifiedLibrarySearchSpec,
)
from quorune.record import authoritative_state_hash
from quorune.replacement.immutable import FrozenMap
from quorune.rules.capabilities import CapabilityRegistry, load_default_capability_registry
from quorune.rules.current_face_keywords import library_card_keyword_abilities
from quorune.rules.evidence import validate_capability_evidence_index
from quorune.rules.evidence_storage import read_evidence_document
from scripts.build_test_database import build_fixture_database
from scripts.update_capability_evidence import build_index, discover_tests


TRACKER = "Mwonvuli Beast Tracker"
PROFILE = "commander_review"
TEMPLATE = "creature-keyword-library-top-v1"
FIXTURE = ROOT / "tests/fixtures/creature-keyword-library-top-search-cards.json"
KEYWORD_EXAMPLES = (
    ("Giant Scorpion", "deathtouch"),
    ("Gladecover Scout", "hexproof"),
    ("Giant Spider", "reach"),
    ("Colossal Dreadmaw", "trample"),
    ("Knight of Grace", "hexproof"),
)
NONMATCHES = (TRACKER, "Prized Elephant", "Lotus Field")
VIEWERS = ("pilot:B", "pilot:C", "pilot:D", "spectator")


class CreatureKeywordLibraryTopSearchTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "keyword-top.sqlite3"
        build_fixture_database([
            FIXTURE,
            ROOT / "tests/fixtures/qualified-library-search-cards.json",
            ROOT / "tests/fixtures/typed-land-entry-cards.json",
        ], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition(
            "Single found top card",
            [DeckEntry("Isamaru, Hound of Konda", 1, "commander"), DeckEntry("Plains", 30)],
            ["Isamaru, Hound of Konda"],
        )

    def compile_tracker(self, registry, *, record=None):
        return compile_oracle_card(
            record if record is not None else self.db.lookup(TRACKER, fuzzy=False),
            capability_registry=registry,
            capability_profile=PROFILE,
        )

    def test_original_trigger_and_runtime_dependency_are_exact(self):
        record = self.db.lookup(TRACKER, fuzzy=False)
        result, semantic = register_card(self.db, self.registry, TRACKER)
        self.assertTrue(result["strict_capability_ready"], result["blockers"])
        program, = semantic.programs_for_oracle(record.oracle_id)
        self.assertEqual(TEMPLATE, program.provenance["template_id"])
        self.assertEqual(("permanent.enter.self", "battlefield"), (program.event, program.active_zone))
        self.assertIsNone(program.target_schema)
        self.assertIn(CREATURE_KEYWORD_TOP_SEARCH_CAPABILITY, program.capability_dependencies)
        self.assertTrue(any("couldn’t put Prized Elephant" in ruling.comment for ruling in self.db.rulings(record)))
        effect, = program.effects
        self.assertEqual(7, effect["schema_version"])
        self.assertEqual({"minimum": 1, "maximum": 1}, effect["count"])
        self.assertEqual("library_top", effect["destination"])
        self.assertIs(effect["reveal"], True)
        self.assertIs(effect["shuffle_before_placement"], True)
        self.assertIs(effect["shuffle_after"], False)
        self.assertEqual(2, effect["qualification"]["schema_version"])
        self.assertEqual(["deathtouch", "hexproof", "reach", "trample"], effect["qualification"]["keywords_any"])
        self.assertEqual(effect, QualifiedLibrarySearchSpec.from_effect(FrozenMap(effect)).effect())

        declared = tuple(cap for cap in program.capability_dependencies if cap != CREATURE_KEYWORD_TOP_SEARCH_CAPABILITY)
        changed = replace(
            program,
            capability_dependencies=declared,
            capability_closure=self.registry.closure(declared, profile=PROFILE).to_dict(),
        )
        binding = bind_semantic_program_runtime(changed, capability_registry=self.registry, profile=PROFILE)
        self.assertFalse(binding["strict"], binding)
        self.assertIn(f"capability:undeclared_runtime_dependency:{CREATURE_KEYWORD_TOP_SEARCH_CAPABILITY}",
                      binding["blockers"])

    def test_unsupported_qualities_order_and_transport_fail_closed(self):
        record = self.db.lookup(TRACKER, fuzzy=False)
        result = self.compile_tracker(self.registry, record=record)
        self.assertEqual("exact", result.status, result.material_residuals)
        effect, = result.faces[0].nodes[0].effects
        for before, after in (
            ("reach, or trample", "reach and trample"),
            ("reach, or trample", "reach, or menace"),
            ("and reveal it. Shuffle", "and shuffle. Reveal it"),
            ("put that card on top", "put that card on the bottom"),
            ("creature card", "permanent card"),
            ("on top.", "on top. Rewrite history."),
        ):
            with self.subTest(after=after):
                changed = self.compile_tracker(self.registry, record=replace(
                    record, oracle_text=record.oracle_text.replace(before, after),
                ))
                self.assertNotEqual("exact", changed.status, changed.material_residuals)
        wire_mutants = (
            {"reveal": False}, {"shuffle_after": True}, {"shuffle_before_placement": False},
            {"destination": "hand"}, {"searching_player": "B"}, {"count": {"minimum": 1, "maximum": 2}},
            {"qualification": {**effect["qualification"], "keywords_any": ["trample"]}},
            {"qualification": {**effect["qualification"], "schema_version": 1}},
        )
        for fields in wire_mutants:
            with self.subTest(fields=fields), self.assertRaises(ValueError):
                QualifiedLibrarySearchSpec.from_effect({**effect, **fields})

    def test_qualified_producer_omission_is_killed(self):
        self.assertEqual("exact", self.compile_tracker(self.registry).status)
        with patch(
            "quorune.compiler.qualified_library_search_templates.qualified_library_search_template",
            return_value=None,
        ) as omission:
            changed = self.compile_tracker(self.registry)
            self.assertTrue(omission.called)
            self.assertNotEqual("exact", changed.status)

    def test_regenerated_owner_withdrawal_leaves_existing_defender_transport(self):
        raw = json.loads((ROOT / "quorune/rules/capability-registry.json").read_bytes())
        withdrawn = deepcopy(raw)
        row = next(entry for entry in withdrawn["capabilities"] if entry["id"] == CREATURE_KEYWORD_TOP_SEARCH_CAPABILITY)
        row.update(status="blocked", blockers=["Test withdraws creature-keyword top search authority"])
        registry = CapabilityRegistry(withdrawn)
        evidence = build_index(
            registry_value=withdrawn,
            declaration_source=read_evidence_document(ROOT / "platform/capability-evidence-declarations.json"),
            discovered_test_ids=set(discover_tests(ROOT).values()),
        )
        registry.mark_evidence_verified(validate_capability_evidence_index(evidence, registry=registry))
        rejected = self.compile_tracker(registry)
        self.assertNotEqual("exact", rejected.status)
        blockers = [item for residual in rejected.material_residuals for item in residual.blockers]
        self.assertTrue(any(f"status:{CREATURE_KEYWORD_TOP_SEARCH_CAPABILITY}:blocked" in item for item in blockers),
                        blockers)
        self.assertFalse(any("evidence_index:unverified" in item for item in blockers), blockers)
        old = self.db.lookup("Shield-Wall Sentinel", fuzzy=False)
        accepted_control = compile_oracle_card(old, capability_registry=self.registry, capability_profile=PROFILE)
        control = compile_oracle_card(old, capability_registry=registry, capability_profile=PROFILE)
        self.assertEqual("exact", control.status, control.material_residuals)
        before, = (effect for node in accepted_control.faces[0].nodes for effect in node.effects)
        after, = (effect for node in control.faces[0].nodes for effect in node.effects)
        self.assertEqual(before, after)
        self.assertEqual("offer_optional_effect", after["op"])
        search, = after["effects"]
        self.assertEqual(("search", 7, 1),
                         (search["op"], search["schema_version"], search["qualification"]["schema_version"]))

    def scene(self, seed, *, matches=True):
        game = self.session(seed)
        engine = game.engine
        source = self.add(engine, TRACKER, zone="hand", ref="tracker-source")
        options = [self.add(engine, name, zone="library", ref=f"tracker-private-allowed-{index}")
                   for index, (name, _) in enumerate(KEYWORD_EXAMPLES) if matches]
        exclusions = [self.add(engine, name, zone="library", ref=f"tracker-private-excluded-{index}")
                      for index, name in enumerate(NONMATCHES)]
        foreign = self.add(engine, "Giant Scorpion", seat="B", zone="library", ref="tracker-foreign-library")
        for card in (*options, *exclusions, foreign):
            card.known_to.clear()
            card.revealed_to.clear()
        self.assert_closed(game, source)
        self.prepare(game, mana={"C": 1, "G": 2})
        self.checkpoint(game)
        self.cast_for(game, "A", source, [])
        self.resolve_top(game)
        self.assertEqual("battlefield", game.state.cards[source.object_id].zone)
        decision = self.resolve(game)
        self.assertEqual(("semantic.search", ["A"]), (decision.kind, decision.actors))
        return game, options, (*exclusions, foreign)

    def assert_private_offer(self, game, options, exclusions):
        decision = game.packet("pilot:A", full=True)["decision"]
        schema = decision["legal_actions"][0]["choice_schema"]
        self.assertEqual((0, min(1, len(options))), (schema["minimum"], schema["maximum"]))
        self.assertEqual({card.ref for card in options}, set(schema["legal_refs"]))
        for viewer in VIEWERS:
            packet = game.packet(viewer, full=True)
            self.assertIsNone(packet["decision"])
            serialized = json.dumps(packet)
            for card in (*options, *exclusions):
                self.assertNotIn(card.ref, serialized)

    def choose(self, game, cards, *, seat="A", accepted=True):
        before = authoritative_state_hash(game.state)
        result = game.act("pilot:" + seat, {"action_id": "choose", "search_cards": [card.ref for card in cards]})
        self.assertEqual(accepted, result.ok, result.summary)
        if not accepted:
            self.assertEqual(before, authoritative_state_hash(game.state))

    def result_order(self, game, chosen=None):
        player = game.state.players["A"]
        next_shuffle = player.stats.get("shuffle_count", 0) + 1
        remainder = [oid for oid in player.zones["library"] if chosen is None or oid != chosen.object_id]
        Random(f"{game.state.config.seed}|A|shuffle|{next_shuffle}").shuffle(remainder)
        return next_shuffle, remainder + ([chosen.object_id] if chosen else [])

    def assert_public_reveal(self, game, chosen, hidden, cursor):
        for viewer in ("pilot:A", *VIEWERS):
            packet = game.packet(viewer, full=True, cursor_key=cursor + viewer)
            reveal, = [event for event in packet["events"] if event["c"] == "library.search.reveal"]
            self.assertEqual([chosen.printed_name], reveal["d"]["names"])
            self.assertEqual([chosen.ref], reveal["d"]["objects"])
            serialized = json.dumps(packet)
            for card in hidden:
                self.assertNotIn(card.ref, serialized)
                self.assertNotIn(card.printed_name, json.dumps(reveal))

    def test_actual_printed_alternatives_reveal_then_shuffle_then_top_with_exact_replay(self):
        game, options, exclusions = self.scene(576420301)
        for card, (_, keyword) in zip(options, KEYWORD_EXAMPLES, strict=True):
            self.assertIn(keyword, library_card_keyword_abilities(game.engine, card))
        own_copy, grant_only, wrong_type, foreign = exclusions
        self.assertIn("Reach", self.db.lookup(TRACKER).keywords)
        self.assertIn("Hexproof", self.db.lookup(TRACKER).keywords)
        self.assertTrue(self.db.rulings(self.db.lookup(TRACKER)))
        self.assertEqual(set(), library_card_keyword_abilities(game.engine, own_copy))
        self.assertNotIn("trample", library_card_keyword_abilities(game.engine, grant_only))
        self.assertIn("hexproof", library_card_keyword_abilities(game.engine, wrong_type))
        self.assert_private_offer(game, options, exclusions)
        self.choose(game, [options[0]], seat="B", accepted=False)
        for invalid in (own_copy, grant_only, wrong_type, foreign):
            self.choose(game, [invalid], accepted=False)
        self.choose(game, options[:2], accepted=False)

        game = self.replay(game, load=True)
        self.assert_private_offer(game, options, exclusions)
        chosen = options[3]
        shuffle_count, expected_order = self.result_order(game, chosen)
        self.choose(game, [chosen])
        player = game.state.players["A"]
        self.assertEqual(expected_order, player.zones["library"])
        self.assertEqual(shuffle_count, player.stats["shuffle_count"])
        self.assertEqual("library", game.state.cards[chosen.object_id].zone)
        self.assertEqual(chosen.ref, game.state.cards[player.zones["library"][-1]].ref)
        reveal = next(event for event in reversed(game.state.events) if event.code == "library.search.reveal")
        shuffle = next(event for event in reversed(game.state.events) if event.code == "library.shuffle")
        placement = next(event for event in reversed(game.state.events) if event.code == "library.search")
        self.assertLess(reveal.event_id, shuffle.event_id)
        self.assertLess(shuffle.event_id, placement.event_id)
        self.assertEqual([chosen.ref], placement.details["objects"])
        next_card = game.state.cards[player.zones["library"][-2]]
        hidden = (*[card for card in options if card.ref != chosen.ref], *exclusions, next_card)
        self.assert_public_reveal(game, chosen, hidden, "live keyword search ")
        loaded = self.replay(game, load=True)
        self.assertEqual(expected_order, loaded.state.players["A"].zones["library"])
        self.assert_public_reveal(loaded, chosen, hidden, "loaded keyword search ")

    def test_legal_fail_to_find_still_shuffles_with_or_without_a_candidate(self):
        for matches in (True, False):
            with self.subTest(matches=matches):
                game, options, exclusions = self.scene(576420310 + matches, matches=matches)
                self.assert_private_offer(game, options, exclusions)
                game = self.replay(game, load=True)
                count, expected_order = self.result_order(game)
                self.choose(game, [])
                self.assertEqual(expected_order, game.state.players["A"].zones["library"])
                self.assertEqual(count, game.state.players["A"].stats["shuffle_count"])
                self.assertFalse(any(event.code == "library.search.reveal" for event in game.state.events))
                self.replay(game, load=True)

    def test_previously_offered_card_leaving_library_is_rejected_atomically(self):
        game, options, _ = self.scene(576420319)
        stale = options[0]
        # Inject only the canonical owner change between offer and commit.
        game.engine.move_card(stale.object_id, "hand", reason="Test stale search offer")
        self.choose(game, [stale], accepted=False)

    def test_metadata_substitution_mutant_is_killed_by_real_search_offer(self):
        def offered():
            game, options, exclusions = self.scene(576420322)
            self.assert_private_offer(game, options, exclusions)

        offered()

        def metadata_keywords(host, card):
            return {keyword.casefold() for keyword in host.card_record(card).keywords}

        with patch("quorune.rules.qualified_library_search.library_card_keyword_abilities",
                   side_effect=metadata_keywords), self.assertRaises(AssertionError):
            offered()
