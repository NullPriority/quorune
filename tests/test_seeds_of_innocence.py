from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.regeneration import create_regeneration_shield
from quorune.replacement.immutable import thaw_value
from quorune.rules.artifact_destruction_life import ARTIFACT_DESTRUCTION_LIFE_CAPABILITY
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card

SOURCE = "Seeds of Innocence"
PROFILE = "commander_review"


class SeedsOfInnocenceTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "seeds-of-innocence.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures/seeds-of-innocence-cards.json"], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        commander = "Isamaru, Hound of Konda"
        cls.deck = DeckDefinition("Original artifact set and controller life", [
            DeckEntry(commander, 1, "commander"), DeckEntry("Plains", 30),
        ], [commander])

    def life_deltas(self, game, before):
        return {seat: game.state.players[seat].life - before[seat] for seat in "ABCD"}

    def shares(self, game, start):
        result, = [event for event in game.state.events if event.event_id > start
                   and event.code == "effect.life" and "requested_deltas" in event.details]
        return result.details["requested_deltas"]

    def test_original_affected_set_includes_indestructible_shielded_and_redirected_artifacts(self):
        for index, use_ygra in enumerate((False, True)):
            with self.subTest(use_ygra=use_ygra):
                game = self.session(7315600 + index)
                host = game.engine
                spell = self.add(host, SOURCE, zone="hand", ref="seeds-spell")
                self.assert_closed(game, spell)
                ring = self.add(host, "Sol Ring", ref="seeds-borrowed-ring")
                host.change_control(ring.object_id, "B", reason="fixture separate controller")
                indestructible = self.add(host, "Darksteel Ingot", seat="B", ref="seeds-indestructible")
                shielded = self.add(host, "Gold Myr", seat="C", ref="seeds-shielded-myr")
                shielded.counters["shield"] = 1
                regen = self.add(host, "Copper Myr", seat="C", ref="seeds-regeneration-prohibited")
                create_regeneration_shield(host, regen.ref, actor="C", reason="fixture regeneration shield",
                                           logical_object_id=regen.logical_object_id)
                bear = self.add(host, "Grizzly Bears", seat="D", ref="seeds-live-artifact-type")
                if use_ygra:
                    ygra = self.add(host, "Ygra, Eater of All", ref="seeds-actual-food-grant")
                    self.assert_closed(game, ygra)
                    self.assert_closed(game, self.add(host, "Rest in Peace", seat="A", ref="seeds-redirect"))
                    self.assertIn("artifact", host._type_parts(host._effective_card_data(bear)["type_line"])[0])
                    pride = None
                else:
                    pride = self.add(host, "Ajani's Pridemate", seat="B", ref="seeds-one-life-event")
                    self.assert_closed(game, pride)
                self.prepare(game, mana={"C": 1, "G": 2})
                before = {seat: game.state.players[seat].life for seat in "ABCD"}
                self.checkpoint(game)
                self.cast_for(game, "A", spell)
                game = self.replay(game, load=True)
                start = game.state.event_sequence
                self.resolve_top(game)
                expected = {"A": 0, "B": 4, "C": 4, "D": 2 if use_ygra else 0}
                self.assertEqual(expected, self.life_deltas(game, before))
                self.assertEqual({seat: amount for seat, amount in expected.items() if amount}, self.shares(game, start))
                destination = "exile" if use_ygra else "graveyard"
                self.assertEqual((destination, destination), (game.state.cards[ring.object_id].zone,
                                                               game.state.cards[regen.object_id].zone))
                self.assertEqual("A", game.state.cards[ring.object_id].owner)
                for permanent in (indestructible, shielded):
                    self.assertEqual("battlefield", game.state.cards[permanent.object_id].zone)
                self.assertNotIn("shield", game.state.cards[shielded.object_id].counters)
                self.assertEqual("exile" if use_ygra else "battlefield", game.state.cards[bear.object_id].zone)
                if pride is not None:
                    triggers = [item for item in game.state.stack if item.source_object_id == pride.object_id]
                    self.assertEqual(1, len(triggers))
                    self.resolve_top(game)
                    self.assertEqual(1, game.state.cards[pride.object_id].counters["+1/+1"])
                self.replay(game, load=True)

    def test_surviving_artifact_uses_current_controller_after_the_control_source_leaves(self):
        game = self.session(7315603)
        host = game.engine
        spell = self.add(host, SOURCE, zone="hand", ref="seeds-current-result")
        survivor = self.add(host, "Darksteel Myr", seat="B", ref="seeds-surviving-controlled-artifact")
        shackles = self.add(host, "Vedalken Shackles", seat="C", ref="seeds-control-effect-source")
        self.add(host, "Island", seat="C", ref="seeds-shackles-island")
        self.assert_closed(game, spell)
        self.assert_closed(game, shackles)
        self.prepare(game, mana={"C": 1, "G": 2})
        game.state.players["C"].mana_pool.update({"C": 2})
        before = {seat: game.state.players[seat].life for seat in "ABCD"}
        self.checkpoint(game)
        self.cast_for(game, "A", spell)
        self.priority(game, "C")
        matrix = game.packet("pilot:C", full=True)["decision"]["ctx"]["legal"]["actions"]
        action, = [row for row in matrix if row["id"].startswith(f"activate:{shackles.ref}:")]
        outcome = game.act("pilot:C", {"action_id": action["id"], "targets": [survivor.ref], "pay": "auto"})
        self.assertTrue(outcome.ok, outcome.summary)
        self.resolve_top(game)
        self.assertEqual("C", game.state.cards[survivor.object_id].controller)
        game = self.replay(game, load=True)
        start = game.state.event_sequence
        self.resolve_top(game)
        self.assertEqual(("battlefield", "B"), (game.state.cards[survivor.object_id].zone,
                                                game.state.cards[survivor.object_id].controller))
        self.assertEqual("graveyard", game.state.cards[shackles.object_id].zone)
        self.assertEqual({"A": 0, "B": 3, "C": 3, "D": 0}, self.life_deltas(game, before))
        self.assertEqual({"B": 3, "C": 3}, self.shares(game, start))
        self.replay(game, load=True)

    def test_artifact_life_requires_its_original_scope_and_no_regeneration(self):
        card = self.db.lookup(SOURCE, fuzzy=False)
        options = {"capability_registry": self.registry, "capability_profile": PROFILE}
        self.assertEqual("exact", compile_oracle_card(card, **options).status)
        report, semantics = register_card(self.db, self.registry, SOURCE)
        self.assertTrue(report["strict_capability_ready"], report)
        program, = semantics.programs_for_oracle(card.oracle_id)
        effect, = deepcopy(thaw_value(program.effects))
        del effect["regeneration_prohibited"]
        report = bind_semantic_program_runtime(replace(program, effects=[effect]),
            capability_registry=self.registry, profile=PROFILE)
        self.assertFalse(report["strict"], report)
        deps = tuple(cap for cap in program.capability_dependencies if cap != ARTIFACT_DESTRUCTION_LIFE_CAPABILITY)
        omitted = replace(program, capability_dependencies=deps,
                          capability_closure=self.registry.closure(deps, profile=PROFILE).to_dict())
        report = bind_semantic_program_runtime(omitted, capability_registry=self.registry, profile=PROFILE)
        self.assertIn("capability:undeclared_runtime_dependency:" + ARTIFACT_DESTRUCTION_LIFE_CAPABILITY,
                      report["blockers"])
        with patch("quorune.compiler.destruction_followup_templates.affected_artifact_life_template", return_value=None):
            self.assertNotEqual("exact", compile_oracle_card(card, **options).status)
