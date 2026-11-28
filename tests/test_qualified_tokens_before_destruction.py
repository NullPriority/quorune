"""Qualified current players create before the next live destruction query."""

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
from quorune.oracle_ir import compile_oracle_card
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.qualified_token_creators import CREATOR_QUERY_FIELD, CREATOR_SEATS_FIELD, QUALIFIED_TOKEN_CREATORS_CAPABILITY
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


PROFILE = "commander_review"


class QualifiedTokenCreatorsTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "qualified-tokens.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures/qualified-tokens-before-destruction-cards.json"], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        commander = "Isamaru, Hound of Konda"
        cls.deck = DeckDefinition("Qualified creators", [
            DeckEntry(commander, 1, "commander"), DeckEntry("Forest", 30),
        ], [commander])

    def ready(self, game, *cards):
        game.state.config.semantic_policy = "trusted_only"
        for card in cards:
            self.assert_closed(game, card)
        self.prepare(game, mana={"C": 2, "G": 2})
        self.checkpoint(game)

    def zones(self, game, *cards):
        return tuple(game.state.cards[card.object_id].zone for card in cards)

    def bears(self, game):
        return [card for card in game.state.cards.values()
                if card.zone == "battlefield" and card.is_token and card.printed_name == "Bear Token"]

    def test_each_actual_qualifying_controller_creates_once_and_new_artifact_bear_is_destroyed(self):
        game = self.session(709091401)
        fade = self.add(game.engine, "Fade from History", zone="hand")
        ring = self.add(game.engine, "Sol Ring")
        fervor = self.add(game.engine, "Fervor")
        anthem = self.add(game.engine, "Glorious Anthem", seat="B")
        ordinary = self.add(game.engine, "Grizzly Bears", seat="C")
        private = self.add(game.engine, "Sol Ring", seat="C", zone="hand", ref="held-nonqualifying-artifact")
        mycosynth = self.add(game.engine, "Encroaching Mycosynth", seat="D")
        self.ready(game, fade, mycosynth, anthem, fervor)
        self.cast(game, "A", fade)
        start = len(game.state.events)
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        events = game.state.events[start:]
        creation = [(i, event) for i, event in enumerate(events)
                    if event.code == "token.create" and event.details["base_name"] == "Bear Token"]
        self.assertEqual(["A", "B", "D"], [event.actor for _, event in creation])
        self.assertEqual([1, 1, 1], [event.details["base_quantity"] for _, event in creation])
        d_index, d_creation = creation[-1]
        d_ref, = d_creation.details["objects"]
        d_destruction, = (i for i, event in enumerate(events) if event.code == "permanent.destroyed"
                          and event.details["object"] == d_ref)
        self.assertLess(d_index, d_destruction)
        self.assertEqual(["A", "B"], sorted(card.controller for card in self.bears(game)))
        self.assertEqual(("graveyard", "graveyard", "graveyard", "graveyard", "battlefield", "hand"),
                         self.zones(game, ring, fervor, anthem, mycosynth, ordinary, private))
        for seat in "ABD":
            self.assertNotIn(private.ref, json.dumps(game.packet("pilot:" + seat, full=True)))
        self.replay(game, load=True)

    def test_actual_phase_out_leaves_no_qualified_controller_and_no_destruction_of_that_object(self):
        game = self.session(709091402)
        fade = self.add(game.engine, "Fade from History", zone="hand")
        artifact = self.add(game.engine, "Sol Ring", seat="B", ref="phased-original-artifact")
        phase = self.add(game.engine, "Reality Ripple", seat="B", zone="hand")
        in_hand = self.add(game.engine, "Glorious Anthem", seat="C", zone="hand")
        in_grave = self.add(game.engine, "Sol Ring", seat="D", zone="graveyard")
        game.state.players["B"].mana_pool.update({"C": 1, "U": 1})
        self.ready(game, fade, phase)
        self.cast(game, "A", fade)
        self.cast(game, "B", phase, [artifact.ref])
        self.resolve_top(game)
        self.assertTrue(game.state.cards[artifact.object_id].phased_out)
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        self.assertFalse(any(event.code == "token.create" for event in game.state.events))
        self.assertEqual(("battlefield", "hand", "graveyard", "graveyard"), self.zones(game, artifact, in_hand, in_grave, fade))
        self.assertTrue(game.state.cards[artifact.object_id].phased_out)
        self.replay(game, load=True)

    def test_complete_original_requires_live_query_and_then_independent_destruction(self):
        card = self.db.lookup("Fade from History", fuzzy=False)
        for profile in self.registry.profiles:
            ir = compile_oracle_card(card, capability_registry=self.registry, capability_profile=profile)
            self.assertEqual("exact", ir.status, ir.material_residuals)
            node, = ir.faces[0].nodes
            self.assertEqual(card.oracle_text, card.oracle_text[node.span.start:node.span.end])
            self.assertIsNone(node.target_schema)
            creation, destruction = node.effects
            self.assertEqual(["artifact", "enchantment"], creation[CREATOR_QUERY_FIELD]["predicate"]["types_any"])
            self.assertEqual("destroy_all", destruction["op"])
        report, semantics = register_card(self.db, self.registry, "Fade from History")
        self.assertTrue(report["strict_capability_ready"], report["blockers"])
        cap = QUALIFIED_TOKEN_CREATORS_CAPABILITY
        program, = (row for row in semantics.programs_for_oracle(card.oracle_id) if cap in row.capability_dependencies)
        deps = tuple(key for key in program.capability_dependencies if key != cap)
        missing = replace(program, capability_dependencies=deps,
                          capability_closure=self.registry.closure(deps, profile=PROFILE).to_dict())
        bound = bind_semantic_program_runtime(missing, capability_registry=self.registry, profile=PROFILE)
        self.assertFalse(bound["strict"])
        self.assertIn(f"capability:undeclared_runtime_dependency:{cap}", bound["blockers"])
        creation, destruction = program.effects
        for effects in ((creation,), (destruction, creation),
                        ({**creation, CREATOR_SEATS_FIELD: ["A"]}, destruction),
                        ({**creation, "token": {**creation["token"], "controller": "A"}}, destruction)):
            changed = replace(program, effects=effects)
            self.assertFalse(bind_semantic_program_runtime(changed, capability_registry=self.registry, profile=PROFILE)["strict"])
        for before, after in (("artifact or enchantment creates", "artifact and enchantment creates"),
                              ("Then destroy", "Then exile"), ("enchantments.", "enchantments. Draw a card.")):
            changed = replace(card, oracle_text=card.oracle_text.replace(before, after))
            self.assertNotEqual("exact", compile_oracle_card(changed, capability_registry=self.registry).status)
