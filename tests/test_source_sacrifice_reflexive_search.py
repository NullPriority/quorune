"""A performed self-entry sacrifice queues an independently interactable search."""

from dataclasses import replace
import json
from pathlib import Path
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
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.source_sacrifice_reflexive_search import CAPABILITY, FIELD, validate_child
from scripts.build_test_database import build_fixture_database


CARD = "Riveteers Overlook"
PROFILE = "commander_review"
PINNED_TRIPLES = {
    "Brokers Hideout": ("forest", "island", "plains"),
    "Cabaretti Courtyard": ("forest", "mountain", "plains"),
    "Maestros Theater": ("island", "mountain", "swamp"),
    "Obscura Storefront": ("island", "plains", "swamp"),
    CARD: ("forest", "mountain", "swamp"),
}


class SourceSacrificeReflexiveSearchTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "source-sacrifice-reflexive-search.sqlite3"
        fixtures = ROOT / "tests" / "fixtures"
        build_fixture_database([
            fixtures / "bound-effect-program-cards.json",
            fixtures / "activation-paid-result-slot-cards.json",
            fixtures / "activation-target-view-cards.json",
            fixtures / "committed-apnap-sacrifice-token-count-cards.json",
            fixtures / "source-sacrifice-reflexive-search-cards.json",
        ], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Source sacrifice search", [
            DeckEntry("Generic Bound Commander", 1, "commander"),
            DeckEntry("Generic Bound Plains", 30),
        ], ["Generic Bound Commander"])

    def enter(self, game, source, *, mana=None):
        self.assert_closed(game, source)
        game.state.config.semantic_policy = "trusted_only"
        self.prepare(game)
        game.state.players["B"].mana_pool.update(mana or {})
        self.checkpoint(game)
        command = {"action_id": f"play-land:{source.ref}"}
        outcome = game.act("pilot:A", command)
        self.assertTrue(outcome.ok, outcome.summary)
        initial, = game.state.stack
        self.assertEqual(("triggered_ability", source.object_id, "A"),
                         (initial.kind, initial.source_object_id, initial.controller))
        return initial

    def assert_child(self, game, original, source):
        child, = game.state.stack
        self.assertNotEqual(original.ref, child.ref)
        self.assertEqual((source.object_id, "A", "trigger.reflexive", original.ref),
                         (child.source_object_id, child.controller, child.context["event"],
                          child.context["reflexive_parent"]))
        self.assertEqual("priority", game.state.pending_decision.kind)
        return child

    @staticmethod
    def later_events(game, sequence, code):
        return [event for event in game.state.events if event.event_id > sequence and event.code == code]

    def test_redirected_sacrifice_search_keeps_basic_choices_private_and_commits_in_order(self):
        game = self.session(60312081)
        source = self.add(game.engine, CARD, zone="hand")
        mountain = self.add(game.engine, "Mountain", zone="library")
        island = self.add(game.engine, "Island", zone="library")
        taiga = self.add(game.engine, "Taiga", zone="library")
        self.add(game.engine, "Rest in Peace", seat="D")
        self.add(game.engine, "Leyline of the Void", seat="D")
        life = game.state.players["A"].life
        original = self.enter(game, source)

        pending = self.resolve(game)
        self.assertEqual(("replacement.order", ["A"]), (pending.kind, pending.actors))
        self.assertEqual("battlefield", game.state.cards[source.object_id].zone)
        self.assertEqual([original.ref], [item.ref for item in game.state.stack])
        game = self.replay(game, load=True)
        options = game.packet("pilot:A", full=True)["decision"]["ctx"]["options"]
        self.assertGreaterEqual(len(options), 2)
        outcome = game.act("pilot:A", {"action_id": "choose", "replacement": options[0]["id"]})
        self.assertTrue(outcome.ok, outcome.summary)
        self.assertEqual("exile", game.state.cards[source.object_id].zone)
        self.assert_child(game, original, source)
        self.assertEqual(life, game.state.players["A"].life)
        self.resolve(game)
        game = self.replay(game, load=True)
        packet = game.packet("pilot:A", full=True)["decision"]
        schema = packet["legal_actions"][0]["choice_schema"]
        self.assertTrue(schema["rules_may_fail_to_find"])
        self.assertEqual([mountain.ref], schema["legal_refs"])
        self.assertIsNone(game.packet("pilot:B", full=True)["decision"])
        self.assertNotIn(mountain.ref, json.dumps(game.packet("pilot:B", full=True)))
        before = authoritative_state_hash(game.state)
        for seat, card in (("B", mountain), ("A", island), ("A", taiga)):
            self.assertFalse(game.act(f"pilot:{seat}", {"action_id": "choose", "search_cards": [card.ref]}).ok)
            self.assertEqual(before, authoritative_state_hash(game.state))
        sequence = game.state.event_sequence
        outcome = game.act("pilot:A", {"action_id": "choose", "search_cards": [mountain.ref]})
        self.assertTrue(outcome.ok, outcome.summary)
        self.assertIsNone(self.resolve(game))
        arrived = game.state.cards[mountain.object_id]
        self.assertEqual(("battlefield", "A", True), (arrived.zone, arrived.controller, arrived.tapped))
        self.assertEqual(life + 1, game.state.players["A"].life)
        shuffle, = self.later_events(game, sequence, "library.shuffle")
        changed_life = next(event for event in game.state.events if event.event_id > sequence
                            and event.actor == "A" and event.code == "effect.life")
        self.assertLess(shuffle.event_id, changed_life.event_id)
        self.replay(game, load=True)

    def test_failed_to_find_still_shuffles_then_gains_one_life(self):
        game = self.session(60312082)
        source = self.add(game.engine, CARD, zone="hand")
        mountain = self.add(game.engine, "Mountain", zone="library")
        original = self.enter(game, source)
        self.resolve_top(game)
        self.assert_child(game, original, source)
        self.assertEqual("graveyard", game.state.cards[source.object_id].zone)
        self.resolve(game)
        before_life = game.state.players["A"].life
        sequence = game.state.event_sequence
        outcome = game.act("pilot:A", {"action_id": "choose", "search_cards": []})
        self.assertTrue(outcome.ok, outcome.summary)
        self.assertIsNone(self.resolve(game))
        self.assertEqual("library", game.state.cards[mountain.object_id].zone)
        self.assertEqual(before_life + 1, game.state.players["A"].life)
        self.assertEqual(1, len(self.later_events(game, sequence, "library.shuffle")))
        self.replay(game, load=True)

    def test_removing_the_original_before_sacrifice_creates_no_reflexive_trigger(self):
        game = self.session(60312083)
        source = self.add(game.engine, CARD, zone="hand")
        answer = self.add(game.engine, "Boomerang", seat="B", zone="hand")
        self.assert_closed(game, answer)
        self.enter(game, source, mana={"U": 2})
        self.cast(game, "B", answer, [game.state.cards[source.object_id].ref])
        self.resolve_top(game)
        game = self.replay(game, load=True)
        sequence = game.state.event_sequence
        life = game.state.players["A"].life
        self.assertIsNone(self.resolve(game))
        self.assertEqual("hand", game.state.cards[source.object_id].zone)
        self.assertFalse(game.state.stack)
        self.assertEqual(life, game.state.players["A"].life)
        self.assertEqual([], self.later_events(game, sequence, "library.shuffle"))
        self.replay(game, load=True)

    def test_countering_the_separate_reflexive_trigger_leaves_earlier_sacrifice_committed(self):
        game = self.session(60312084)
        source = self.add(game.engine, CARD, zone="hand")
        answer = self.add(game.engine, "Stifle", seat="B", zone="hand")
        self.assert_closed(game, answer)
        self.add(game.engine, "Mountain", zone="library")
        original = self.enter(game, source, mana={"U": 1})
        self.resolve_top(game)
        child = self.assert_child(game, original, source)
        game = self.replay(game, load=True)
        sequence = game.state.event_sequence
        life = game.state.players["A"].life
        self.cast(game, "B", answer, [child.ref])
        self.assertIsNone(self.resolve(game))
        self.assertEqual("graveyard", game.state.cards[source.object_id].zone)
        self.assertEqual(life, game.state.players["A"].life)
        self.assertEqual([], self.later_events(game, sequence, "library.shuffle"))
        self.replay(game, load=True)

    def test_all_five_exact_sources_and_withdrawn_reflexive_authority(self):
        for name, expected in PINNED_TRIPLES.items():
            with self.subTest(name=name):
                card = self.db.lookup(name, fuzzy=False)
                ir = self.compile_card(card)
                self.assertEqual("exact", ir.status, ir.material_residuals)
                node, = ir.faces[0].nodes
                self.assertEqual(("permanent.enter.self", "battlefield"), (node.event, node.active_zone))
                self.assertEqual((0, len(card.oracle_text)), (node.span.start, node.span.end))
                self.assertIn(CAPABILITY, node.capability_dependencies)
                child, _owned = validate_child(node.effects[0][FIELD])
                self.assertEqual(expected, child.effects[0]["selector"]["subtypes_any"])
                report, semantics = register_card(self.db, self.registry, name)
                self.assertTrue(report["strict_capability_ready"], report)
                self.assertEqual("capability_closed", report["trust_basis"])
                program, = semantics.programs_for_oracle(card.oracle_id)
                deps = tuple(cap for cap in program.capability_dependencies if cap != CAPABILITY)
                absent = replace(program, capability_dependencies=deps,
                                 capability_closure=self.registry.closure(deps, profile=PROFILE).to_dict())
                binding = bind_semantic_program_runtime(absent, capability_registry=self.registry, profile=PROFILE)
                self.assertIn("capability:undeclared_runtime_dependency:" + CAPABILITY, binding["blockers"])
        card = self.db.lookup(CARD, fuzzy=False)
        for old, new in (
            ("Swamp, Mountain, or Forest", "Swamp, Mountain, or Swamp"),
            ("a basic", "a"), ("battlefield tapped", "battlefield"),
            ("gain 1 life", "gain 2 life"), ("When you do", "If you do"),
        ):
            self.assertNotEqual("exact", self.compile_card(replace(card, oracle_text=card.oracle_text.replace(old, new))).status)
        with patch("quorune.compiler.source_sacrifice_reflexive_search.fixed_library_search_effect_template", return_value=None):
            self.assertNotEqual("exact", self.compile_card(card).status)

    def compile_card(self, card):
        return compile_oracle_card(card, capability_registry=self.registry, capability_profile=PROFILE)
