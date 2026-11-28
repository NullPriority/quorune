from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.library_search_model import project_semantic_search_result_publication
from quorune.object_predicate import ObjectQuerySpec
from quorune.oracle_ir import compile_oracle_card
from quorune.qualified_library_search_model import (
    NONLEGENDARY_GRAVEYARD_SEARCH_CAPABILITY, NONLEGENDARY_LIBRARY_CARD_QUERY, QualifiedLibrarySearchSpec,
)
from quorune.record import authoritative_state_hash
from quorune.replacement.immutable import FrozenMap
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
from test_bound_effect_programs import record
from test_controller_creature_keyword_absence import register_card


CARD = "Unmarked Grave"
PROFILE = "commander_review"
OTHER_PRINCIPALS = ("pilot:B", "pilot:C", "pilot:D", "spectator")


class NonlegendaryLibraryGraveyardTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        folder = Path(cls.temporary.name)
        fixtures = ROOT / "tests/fixtures"
        support = {
            "nonlegendary-library-graveyard-cards.json": {CARD},
            "bound-effect-program-cards.json": {"Generic Bound Plains", "Grizzly Bears"},
            "source-attack-alone-cards.json": {"Isamaru, Hound of Konda"},
            "ability-source-counter-cards.json": {"Sol Ring"},
        }
        parts = []
        for filename, names in support.items():
            document = json.loads((fixtures / filename).read_bytes())
            document["cards"] = [card for card in document["cards"] if card["name"] in names]
            assert {card["name"] for card in document["cards"]} == names, filename
            oracle_ids = {card["oracle_id"] for card in document["cards"]}
            document["rulings"] = [r for r in document.get("rulings", ()) if r["oracle_id"] in oracle_ids]
            piece = folder / filename
            piece.write_text(json.dumps(document), encoding="utf-8")
            parts.append(piece)
        path = folder / "nonlegendary-graveyard-search.sqlite3"
        build_fixture_database(parts, path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        commander = "Isamaru, Hound of Konda"
        cls.deck = DeckDefinition("Find a nonlegendary card", [
            DeckEntry(commander, 1, "commander"), DeckEntry("Generic Bound Plains", 30),
        ], [commander])

    def test_pinned_spell_uses_exact_typed_query_and_required_authority(self):
        card = self.db.lookup(CARD, fuzzy=False)
        report, semantics = register_card(self.db, self.registry, CARD)
        self.assertTrue(report["strict_capability_ready"], report["blockers"])
        self.assertEqual("capability_closed", report["trust_basis"])
        program, = semantics.programs_for_oracle(card.oracle_id)
        effect, = program.effects
        self.assertIsNone(program.target_schema)
        self.assertEqual({"minimum": 1, "maximum": 1}, effect["count"])
        spec = QualifiedLibrarySearchSpec.from_effect(FrozenMap(effect))
        self.assertEqual(("graveyard", False, NONLEGENDARY_LIBRARY_CARD_QUERY), (spec.destination, spec.reveal, spec.query))
        self.assertTrue(effect["shuffle_after"])
        self.assertEqual(set(spec.capabilities()), set(program.capability_dependencies))
        missing = NONLEGENDARY_GRAVEYARD_SEARCH_CAPABILITY
        deps = tuple(value for value in program.capability_dependencies if value != missing)
        altered = replace(program, capability_dependencies=deps,
                          capability_closure=self.registry.closure(deps, profile=PROFILE).to_dict())
        rejected = bind_semantic_program_runtime(altered, capability_registry=self.registry, profile=PROFILE)
        self.assertFalse(rejected["strict"], rejected)
        self.assertIn(f"capability:undeclared_runtime_dependency:{missing}", rejected["blockers"])
        for changes in ({"reveal": True}, {"destination": "hand"}, {"count": {"minimum": 0, "maximum": 1}},
                        {"qualification": {**effect["qualification"], "query": ObjectQuerySpec(zones=("library",)).to_dict()}}):
            with self.subTest(changes=tuple(changes)), self.assertRaises(ValueError):
                QualifiedLibrarySearchSpec.from_effect({**effect, **changes})
        for old, new in (("a nonlegendary card", "two nonlegendary cards"), ("nonlegendary card", "nonlegendary creature card"),
                         ("put that card", "reveal that card, put that card"), ("your graveyard", "your hand")):
            with self.subTest(unrepresented=new):
                ir = compile_oracle_card(record(card.oracle_text.replace(old, new), "Sorcery"), capability_registry=self.registry)
                self.assertNotEqual("exact", ir.status)
        with patch("quorune.compiler.qualified_library_search_templates.qualified_library_search_template", return_value=None):
            self.assertNotEqual("exact", compile_oracle_card(card, capability_registry=self.registry).status)

    def test_publication_lists_only_actual_public_arrivals(self):
        spec = QualifiedLibrarySearchSpec(NONLEGENDARY_LIBRARY_CARD_QUERY, "graveyard", False)
        hidden = SimpleNamespace(ref="still-hidden", printed_name="Hidden", zone="library", face_down=False)
        actual = SimpleNamespace(ref="really-in-graveyard", printed_name="Public", zone="graveyard", face_down=False)
        for moved, count in ((hidden, 0), (actual, 1)):
            with self.subTest(actual=moved.zone):
                result = project_semantic_search_result_publication(
                    spec.effect(), source="S1", destination_spec="graveyard", destination="graveyard", reveal=False,
                    moved=(moved,), allocation_details={},
                )
                self.assertEqual(count, result.public_count)
                self.assertEqual(1, result.private_details["count"])
                self.assertEqual([moved.ref], result.private_details["objects"])
                self.assertEqual((actual,) if count else (), result.public_changed_objects)
                self.assertNotIn(hidden.ref, json.dumps(result.public_details))

    def start(self, seed):
        game = self.session(seed)
        spell = self.add(game.engine, CARD, zone="hand")
        artifact = self.add(game.engine, "Sol Ring", zone="library", ref="private-nonlegend-artifact")
        creature = self.add(game.engine, "Grizzly Bears", zone="library", ref="private-nonlegend-creature")
        legend = self.add(game.engine, "Isamaru, Hound of Konda", zone="library", ref="private-legendary-card")
        self.assert_closed(game, spell)
        game.state.config.semantic_policy = "trusted_only"
        self.prepare(game, mana={"C": 1, "B": 1})
        self.checkpoint(game)
        self.cast(game, "A", spell)
        self.assertEqual([], game.state.stack[-1].targets)
        pending = self.resolve(game)
        self.assertIsNotNone(pending)
        self.assertEqual(("semantic.search", ["A"]), (pending.kind, pending.actors))
        self.assert_private_offer(game, artifact, creature, legend)
        return game, spell, artifact, creature, legend

    def assert_private_offer(self, game, artifact, creature, legend):
        payload = game.state.pending_decision.payload_by_actor["A"]
        choice, = payload["legal_actions"]
        schema = choice["choice_schema"]
        self.assertEqual((0, 1, True), (schema["minimum"], schema["maximum"], schema["rules_may_fail_to_find"]))
        self.assertLessEqual({artifact.ref, creature.ref}, set(schema["legal_refs"]))
        self.assertNotIn(legend.ref, schema["legal_refs"])
        self.assertIn(legend.ref, json.dumps(game.packet("pilot:A", full=True)))
        for principal in OTHER_PRINCIPALS:
            projected = json.dumps(game.packet(principal, full=True))
            for hidden in (artifact, creature, legend):
                self.assertNotIn(hidden.ref, projected)

    def complete_search(self, game, refs, *, count):
        player = game.state.players["A"]
        shuffles = player.stats.get("shuffle_count", 0)
        cursor = len(game.state.events)
        response = game.act("pilot:A", {"action_id": "choose", "search_cards": refs})
        self.assertTrue(response.ok, response.summary)
        self.assertEqual(shuffles + 1, game.state.players["A"].stats["shuffle_count"])
        result, shuffle = [row for row in game.state.events[cursor:] if row.code in {"library.search", "library.shuffle"}]
        self.assertEqual(("library.search", "library.shuffle"), (result.code, shuffle.code))
        self.assertEqual((count, "graveyard", False), (result.details["count"], result.details["destination"], result.details["revealed"]))

    def test_actual_private_choice_moves_one_public_card_then_shuffles_and_replays(self):
        game, spell, artifact, creature, legend = self.start(1645001)
        game = self.replay(game, load=True)
        self.assert_private_offer(game, artifact, creature, legend)
        snapshot = authoritative_state_hash(game.state)
        for principal, refs in (("pilot:B", [artifact.ref]), ("pilot:A", [legend.ref]),
                                ("pilot:A", [artifact.ref, creature.ref]), ("pilot:A", [artifact.ref, artifact.ref])):
            with self.subTest(principal=principal, refs=refs):
                denied = game.act(principal, {"action_id": "choose", "search_cards": refs})
                self.assertFalse(denied.ok, denied.summary)
                self.assertEqual(snapshot, authoritative_state_hash(game.state))
        before = set(game.state.players["A"].zones["graveyard"])
        old_identity = game.state.cards[artifact.object_id].logical_object_id
        self.complete_search(game, [artifact.ref], count=1)
        self.assertEqual({artifact.object_id, spell.object_id}, set(game.state.players["A"].zones["graveyard"]) - before)
        self.assertNotEqual(old_identity, game.state.cards[artifact.object_id].logical_object_id)
        for hidden in (creature, legend):
            self.assertEqual("library", game.state.cards[hidden.object_id].zone)
        for principal in OTHER_PRINCIPALS:
            projected = json.dumps(game.packet(principal, full=True))
            self.assertIn(artifact.ref, projected)
            self.assertNotIn(creature.ref, projected)
            self.assertNotIn(legend.ref, projected)
        self.replay(game, load=True)

    def test_actual_failure_to_find_with_eligible_cards_still_shuffles_and_replays(self):
        game, spell, artifact, creature, legend = self.start(1645002)
        before = set(game.state.players["A"].zones["library"])
        self.complete_search(game, [], count=0)
        self.assertEqual(before, set(game.state.players["A"].zones["library"]))
        self.assertEqual("graveyard", game.state.cards[spell.object_id].zone)
        for principal in OTHER_PRINCIPALS:
            projected = json.dumps(game.packet(principal, full=True))
            for hidden in (artifact, creature, legend):
                self.assertNotIn(hidden.ref, projected)
        self.replay(game, load=True)
