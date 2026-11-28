"""Actual committed land sacrifices supply an ordinary colorless resolution result."""

from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import unittest

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.any_number_sacrifice import (
    ANY_NUMBER_SACRIFICE_FIELD, COMMITTED_LAND_SACRIFICE_MANA_CAPABILITY, any_number_sacrifice_parts,
)
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


PROFILE = "commander_review"


class CommittedLandSacrificeManaTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "committed-land-sacrifice-mana.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures/committed-land-sacrifice-mana-cards.json"], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        commander = "Isamaru, Hound of Konda"
        cls.deck = DeckDefinition("Committed land mana", [
            DeckEntry(commander, 1, "commander"), DeckEntry("Plains", 30),
        ], [commander])

    def start(self, game, *cards):
        for card in cards:
            self.assert_closed(game, card)
        game.state.config.semantic_policy = "trusted_only"
        self.prepare(game, mana={"C": 1, "R": 1})
        self.checkpoint(game)

    def choose(self, game, legal, refs):
        decision = self.resolve(game)
        self.assertEqual(("choice.apnap", ["A"]), (decision.kind, decision.actors))
        context = game.packet("pilot:A", full=True)["decision"]["ctx"]
        self.assertEqual((0, len(legal)), (context["minimum"], context["count"]))
        self.assertEqual(set(legal), set(context["options"]))
        self.assertEqual(0, sum(game.state.players["A"].mana_pool.values()))
        game = self.replay(game, load=True)
        command = {"action_id": "choose", "cards": list(refs)}
        before = authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:B", command).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        result = game.act("pilot:A", command)
        self.assertTrue(result.ok, result.summary)
        self.assertIsNone(self.resolve(game))
        return game

    def test_actual_redirected_lands_fund_another_actual_cast_without_tapping_the_spare_land(self):
        game = self.session(76291301)
        spell = self.add(game.engine, "Mana Seism", zone="hand")
        artifact = self.add(game.engine, "Mind Stone", zone="hand")
        redirect = self.add(game.engine, "Rest in Peace", seat="D")
        lands = tuple(self.add(game.engine, "Forest", ref=f"seism-land-{index}") for index in range(3))
        nonland = self.add(game.engine, "Grizzly Bears")
        foreign = self.add(game.engine, "Plains", seat="B")
        self.start(game, spell, artifact, redirect)
        self.cast(game, "A", spell)
        game = self.choose(game, [card.ref for card in lands], [lands[0].ref, lands[1].ref])
        self.assertEqual(2, game.state.players["A"].mana_pool["C"])
        self.assertEqual(0, sum(game.state.players["B"].mana_pool.values()))
        self.assertEqual(("exile", "exile", "battlefield"),
                         tuple(game.state.cards[card.object_id].zone for card in lands))
        self.assertEqual("battlefield", game.state.cards[nonland.object_id].zone)
        self.assertEqual("battlefield", game.state.cards[foreign.object_id].zone)
        self.cast(game, "A", artifact)
        self.assertIsNone(self.resolve(game))
        self.assertEqual("battlefield", game.state.cards[artifact.object_id].zone)
        self.assertFalse(game.state.cards[lands[2].object_id].tapped)
        self.assertEqual(0, sum(game.state.players["A"].mana_pool.values()))
        self.replay(game, load=True)

    def test_actual_zero_and_eleven_are_both_legal_resolution_quantities(self):
        for count in (0, 11):
            with self.subTest(count=count):
                game = self.session(76291310 + count)
                spell = self.add(game.engine, "Mana Seism", zone="hand")
                artifact = self.add(game.engine, "Mind Stone", zone="hand")
                lands = tuple(self.add(game.engine, "Forest", ref=f"tapped-choice-land-{index}")
                              for index in range(max(2, count)))
                for land in lands:
                    land.tapped = True
                self.start(game, spell, artifact)
                self.cast(game, "A", spell)
                game = self.choose(game, [card.ref for card in lands], [card.ref for card in lands[:count]])
                self.assertEqual(count, game.state.players["A"].mana_pool["C"])
                self.priority(game, "A")
                decision = game.packet("pilot:A", full=True)["decision"]
                offered = [row["id"] for row in decision["ctx"]["legal"]["actions"]]
                self.assertEqual(bool(count), "cast:" + artifact.ref in offered)
                expected = "graveyard" if count else "battlefield"
                self.assertTrue(all(game.state.cards[card.object_id].zone == expected for card in lands))
                self.replay(game, load=True)

    def test_complete_source_requires_the_exact_lands_color_and_independent_authority(self):
        card = self.db.lookup("Mana Seism", fuzzy=False)
        report, semantics = register_card(self.db, self.registry, "Mana Seism")
        self.assertTrue(report["strict_capability_ready"], report)
        self.assertEqual("capability_closed", report["trust_basis"])
        program, = (program for program in semantics.programs_for_oracle(card.oracle_id)
                    if COMMITTED_LAND_SACRIFICE_MANA_CAPABILITY in program.capability_dependencies)
        effect, = program.effects
        domain, recipe = any_number_sacrifice_parts(effect)
        self.assertEqual(("lands", "C"), (domain, recipe["color"]))
        self.assertEqual(2, effect[ANY_NUMBER_SACRIFICE_FIELD]["schema_version"])
        deps = tuple(cap for cap in program.capability_dependencies if cap != COMMITTED_LAND_SACRIFICE_MANA_CAPABILITY)
        absent = replace(program, capability_dependencies=deps,
                         capability_closure=self.registry.closure(deps, profile=PROFILE).to_dict())
        binding = bind_semantic_program_runtime(absent, capability_registry=self.registry, profile=PROFILE)
        self.assertFalse(binding["strict"])
        self.assertIn(f"capability:undeclared_runtime_dependency:{COMMITTED_LAND_SACRIFICE_MANA_CAPABILITY}",
                      binding["blockers"])
        for old,new in (("lands","creatures"), ("{C}","{G}"), ("that much","twice that much"),
                        ("{C}.","{C}. Rewrite history.")):
            changed = replace(card, oracle_text=card.oracle_text.replace(old,new))
            self.assertNotEqual("exact", compile_oracle_card(
                changed, capability_registry=self.registry, capability_profile=PROFILE,
            ).status)
        with patch("quorune.compiler.resolution_effect_templates.any_number_sacrifice_template", return_value=None):
            self.assertNotEqual("exact", compile_oracle_card(
                card, capability_registry=self.registry, capability_profile=PROFILE,
            ).status)
