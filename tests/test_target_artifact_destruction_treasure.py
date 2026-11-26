"""Actual target type and committed destruction decide one ordinary Treasure."""

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
from quorune.record import authoritative_state_hash
from quorune.rules.artifact_destruction_treasure import (
    ARTIFACT_DESTRUCTION_TREASURE_CAPABILITY, ArtifactDestructionTreasure,
)
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


PROFILE = "commander_review"


class TargetArtifactDestructionTreasureTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "target-artifact-destruction-treasure.sqlite3"
        fixture = ROOT / "tests/fixtures/target-artifact-destruction-treasure-cards.json"
        build_fixture_database([fixture], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        commander = "Isamaru, Hound of Konda"
        cls.deck = DeckDefinition("Committed artifact Treasure", [
            DeckEntry(commander, 1, "commander"), DeckEntry("Plains", 30),
        ], [commander])

    def ready(self, game, *cards):
        game.state.config.semantic_policy = "trusted_only"
        for card in cards:
            self.assert_closed(game, card)
        game.state.players["B"].mana_pool["U"] = 2
        self.prepare(game, mana={"C": 3, "R": 1})
        self.checkpoint(game)

    def reject(self, game, seat, command):
        before = authoritative_state_hash(game.state)
        result = game.act("pilot:" + seat, command)
        self.assertFalse(result.ok, result.summary)
        self.assertEqual(before, authoritative_state_hash(game.state))

    def treasure(self, game, expected):
        tokens = [card for card in game.state.cards.values()
                  if card.is_token and card.zone == "battlefield" and card.printed_name == "Treasure Token"]
        self.assertEqual(expected, len(tokens))
        self.assertTrue(all((card.owner, card.controller) == ("A", "A") and not card.tapped for card in tokens))

    def test_paid_spell_distinguishes_ordinary_land_artifact_land_and_indestructible_artifact_land(self):
        variants = (("Plains", "graveyard", 0), ("Great Furnace", "graveyard", 1),
                    ("Darksteel Citadel", "battlefield", 0))
        for index, (name, destination, expected) in enumerate(variants):
            with self.subTest(target=name):
                game = self.session(76348001 + index)
                spell = self.add(game.engine, "Smashing Success", zone="hand")
                target = self.add(game.engine, name, seat="B")
                rejected = self.add(game.engine, "Grizzly Bears", seat="C")
                self.ready(game, spell, target)
                self.priority(game, "A")
                action = self.action(game, "A", spell)
                legal = action["target_schema"]["groups"][0]["legal_refs"]
                self.assertIn(target.ref, legal)
                self.assertNotIn(rejected.ref, legal)
                self.reject(game, "A", {"action_id": action["id"], "targets": [rejected.ref], "pay": "auto"})
                self.cast(game, "A", spell, [target.ref])
                self.assertIsNone(self.resolve(game))
                self.assertEqual(destination, game.state.cards[target.object_id].zone)
                self.assertEqual("battlefield", game.state.cards[rejected.object_id].zone)
                self.treasure(game, expected)
                self.replay(game, load=True)

    def test_paid_live_artifact_change_pays_the_treasure_after_actual_redirected_destruction(self):
        game = self.session(76348010)
        spell = self.add(game.engine, "Smashing Success", zone="hand")
        target = self.add(game.engine, "Plains", seat="B")
        coating = self.add(game.engine, "Liquimetal Coating", seat="B")
        rest = self.add(game.engine, "Rest in Peace", seat="B")
        leyline = self.add(game.engine, "Leyline of the Void", seat="C")
        hidden = self.add(game.engine, "Boomerang", seat="D", zone="hand", ref="artifact-result-unrelated-private")
        self.ready(game, spell, coating, rest, leyline)
        self.cast(game, "A", spell, [target.ref])
        self.priority(game, "B")
        offers = game.packet("pilot:B", full=True)["decision"]["ctx"]["legal"]["actions"]
        action, = (row for row in offers if row["id"].startswith(f"activate:{coating.ref}:"))
        result = game.act("pilot:B", {"action_id": action["id"], "targets": [target.ref], "pay": "auto"})
        self.assertTrue(result.ok, result.summary)
        self.resolve_top(game)
        types, _, _ = game.engine._type_parts(game.engine._effective_card_data(target)["type_line"])
        self.assertEqual({"artifact", "land"}, set(types))
        pending = self.resolve(game)
        self.assertEqual(("replacement.order", ["B"]), (pending.kind, pending.actors))
        self.assertEqual("battlefield", game.state.cards[target.object_id].zone)
        self.treasure(game, 0)
        for principal in ("pilot:A", "pilot:B", "pilot:C", "spectator"):
            self.assertNotIn(hidden.ref, json.dumps(game.packet(principal, full=True)))
        game = self.replay(game, load=True)
        options = game.packet("pilot:B", full=True)["decision"]["ctx"]["options"]
        self.assertEqual(2, len(options))
        command = {"action_id": "choose", "replacement": options[0]["id"]}
        self.reject(game, "A", command)
        self.reject(game, "B", {**command, "replacement": "unoffered"})
        result = game.act("pilot:B", command)
        self.assertTrue(result.ok, result.summary)
        pending = self.resolve(game)
        if pending is not None:
            self.assertEqual("replacement.order", pending.kind)
            principal = game.pending_principals()[0]
            options = game.packet(principal, full=True)["decision"]["ctx"]["options"]
            result = game.act(principal, {"action_id": "choose", "replacement": options[0]["id"]})
            self.assertTrue(result.ok, result.summary)
            self.assertIsNone(self.resolve(game))
        self.assertEqual("exile", game.state.cards[target.object_id].zone)
        self.treasure(game, 1)
        lost, = (event for event in game.state.events if event.code == "permanent.destroyed")
        created, = (event for event in game.state.events if event.code == "token.create")
        self.assertLess(game.state.events.index(lost), game.state.events.index(created))
        self.replay(game, load=True)

    def test_paid_fizzled_target_never_destroys_an_alternative_or_creates_the_token(self):
        game = self.session(76348011)
        spell = self.add(game.engine, "Smashing Success", zone="hand")
        original = self.add(game.engine, "Great Furnace", seat="B", ref="artifact-result-original")
        other = self.add(game.engine, "Great Furnace", seat="C", ref="artifact-result-alternative")
        response = self.add(game.engine, "Boomerang", seat="B", zone="hand")
        self.ready(game, spell, response)
        self.cast(game, "A", spell, [original.ref])
        self.cast(game, "B", response, [original.ref])
        self.resolve_top(game)
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        self.assertEqual("hand", game.state.cards[original.object_id].zone)
        self.assertEqual("battlefield", game.state.cards[other.object_id].zone)
        self.treasure(game, 0)
        self.replay(game, load=True)

    def test_complete_original_requires_the_committed_artifact_result_and_independent_authority(self):
        cap = ARTIFACT_DESTRUCTION_TREASURE_CAPABILITY
        report, programs = register_card(self.db, self.registry, "Smashing Success")
        self.assertTrue(report["strict_capability_ready"], report["blockers"])
        self.assertEqual("capability_closed", report["trust_basis"])
        program, = (row for row in programs.programs_for_oracle(report["oracle_id"])
                    if cap in row.capability_dependencies)
        outcome = ArtifactDestructionTreasure.from_dict(program.effects[0]["followup"])
        self.assertEqual("Treasure Token", outcome.token["name"])
        self.assertEqual({"artifact", "land"}, set(program.target_schema["types_any"]))
        deps = tuple(key for key in program.capability_dependencies if key != cap)
        unavailable = replace(program, capability_dependencies=deps,
                              capability_closure=self.registry.closure(deps, profile=PROFILE).to_dict())
        result = bind_semantic_program_runtime(unavailable, capability_registry=self.registry, profile=PROFILE)
        self.assertFalse(result["strict"])
        self.assertIn(f"capability:undeclared_runtime_dependency:{cap}", result["blockers"])
        original = self.db.lookup("Smashing Success", fuzzy=False)
        for before, after in (("If an artifact", "If a land"), ("create a Treasure", "create two Treasures"),
                              ("artifact or land", "artifact or creature"), ("Treasure token.", "Treasure token. Rewrite history.")):
            with self.subTest(unsupported=after):
                candidate = replace(original, oracle_text=original.oracle_text.replace(before, after))
                lowered = compile_oracle_card(candidate, capability_registry=self.registry, capability_profile=PROFILE)
                self.assertNotEqual("exact", lowered.status)
