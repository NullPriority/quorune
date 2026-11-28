"""Actual attacking creature exile and the affected player's private bounded search."""

from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import unittest

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.library_search_model import validate_committed_attacking_exile_search
from quorune.oracle_ir import compile_oracle_card
from quorune.public_zone_moves import PublicZoneMoveSetSpec
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.public_exile_result_model import ATTACKING_EXILE_SEARCH_CAPABILITY, PublicExileFollowup
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


SETTLE = "Settle the Wreckage"
PROFILE = "commander_review"


class AttackingCreatureExileOptionalSearchTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "attacking-exile-optional-search.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures/attacking-creature-exile-optional-search-cards.json"], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        commander = "Isamaru, Hound of Konda"
        cls.deck = DeckDefinition("Attacking creature exile", [
            DeckEntry(commander, 1, "commander"), DeckEntry("Plains", 30),
        ], [commander])

    def start(self, game, *cards):
        for card in cards:
            self.assert_closed(game, card)
        for i in range(4):
            self.add(game.engine, "Plains", seat="C", ref=f"settle-c-plain-{i}")
        game.state.config.semantic_policy = "trusted_only"
        self.prepare(game, mana={"G": 3, "W": 2, "C": 7})
        self.checkpoint(game)

    def act_ok(self, game, seat, command):
        result = game.act("pilot:" + seat, command)
        self.assertTrue(result.ok, result.summary)

    def shuffles(self, game):
        return {seat: player.stats.get("shuffle_count", 0) for seat, player in game.state.players.items()}

    def expect_optional(self, decision):
        self.assertIsNotNone(decision)
        self.assertEqual(("semantic.choice", ["A"]), (decision.kind, decision.actors))
        choose, = (row for row in decision.payload_by_actor["A"]["legal_actions"] if row["id"] == "choose")
        self.assertEqual(["apply", "decline"], list(choose["choice_schema"]["legal_values"]))

    def expect_search(self, game, maximum):
        decision = game.state.pending_decision
        self.assertEqual(("semantic.search", ["A"]), (decision.kind, decision.actors))
        schema = decision.payload_by_actor["A"]["legal_actions"][0]["choice_schema"]
        self.assertEqual((0, maximum), (schema["minimum"], schema["maximum"]))
        return schema

    def test_actual_attacking_token_animated_land_and_hexproof_creature_set_the_private_bound(self):
        game = self.session(61341501)
        spell = self.add(game.engine, SETTLE, seat="C", zone="hand")
        token_spell = self.add(game.engine, "Raise the Alarm", zone="hand")
        bogle = self.add(game.engine, "Slippery Bogle")
        land = self.add(game.engine, "Treetop Village")
        fervor = self.add(game.engine, "Fervor")
        nonattacker = self.add(game.engine, "Grizzly Bears")
        forest = self.add(game.engine, "Forest", zone="library", ref="settle-a-private-forest")
        island = self.add(game.engine, "Island", zone="library", ref="settle-a-private-island")
        nonbasic = self.add(game.engine, "Command Tower", zone="library", ref="settle-a-private-nonbasic")
        foreign = self.add(game.engine, "Wastes", seat="B", zone="library", ref="settle-b-private-basic")
        self.start(game, spell, token_spell, bogle, land, fervor)
        self.cast(game, "A", token_spell)
        self.assertIsNone(self.resolve(game))
        soldiers = [card for card in game.state.cards.values()
                    if card.zone == "battlefield" and card.is_token and card.printed_name == "Soldier Token"]
        self.assertEqual(2, len(soldiers))
        self.priority(game, "A")
        actions = game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        animation, = (row for row in actions if row["id"].startswith("activate:" + land.ref + ":")
                      and row.get("cost_summary", {}).get("m"))
        self.act_ok(game, "A", {"action_id": animation["id"], "pay": "auto"})
        self.assertIsNone(self.resolve(game))
        self.attack(game, [(soldiers[0], "B"), (bogle, "B"), (land, "D")])
        before = self.shuffles(game)
        self.cast(game, "C", spell, ["A"])
        self.expect_optional(self.resolve(game))
        for card in (land, bogle):
            self.assertEqual("exile", game.state.cards[card.object_id].zone)
        self.assertNotEqual("battlefield", game.state.cards[soldiers[0].object_id].zone)
        self.assertEqual(["battlefield", "battlefield"],
                         [game.state.cards[card.object_id].zone for card in (nonattacker, soldiers[1])])
        rejected_state = authoritative_state_hash(game.state)
        reply = {"action_id": "choose", "choice": "apply"}
        self.assertFalse(game.act("pilot:C", reply).ok)
        self.assertEqual(rejected_state, authoritative_state_hash(game.state))
        self.act_ok(game, "A", reply)
        schema = self.expect_search(game, 3)
        self.assertIn(forest.ref, schema["legal_refs"])
        self.assertIn(island.ref, schema["legal_refs"])
        self.assertNotIn(nonbasic.ref, schema["legal_refs"])
        self.assertNotIn(foreign.ref, schema["legal_refs"])
        self.assertNotIn(foreign.ref, json.dumps(game.packet("pilot:A", full=True)))
        for seat in "BCD":
            packet = json.dumps(game.packet("pilot:" + seat, full=True))
            self.assertNotIn(forest.ref, packet)
            self.assertNotIn(island.ref, packet)
        game = self.replay(game, load=True)
        rejected_state = authoritative_state_hash(game.state)
        for seats, refs in (("C", [forest.ref]), ("A", [forest.ref, forest.ref]), ("A", [foreign.ref]), ("A", [nonbasic.ref])):
            result = game.act("pilot:" + seats, {"action_id": "choose", "search_cards": refs})
            self.assertFalse(result.ok, result.summary)
            self.assertEqual(rejected_state, authoritative_state_hash(game.state))
        self.act_ok(game, "A", {"action_id": "choose", "search_cards": [forest.ref, island.ref]})
        self.assertIsNone(self.resolve(game))
        for card in (forest, island):
            current = game.state.cards[card.object_id]
            self.assertEqual(("battlefield", "A", True), (current.zone, current.controller, current.tapped))
        self.assertEqual({seat: value + int(seat == "A") for seat, value in before.items()}, self.shuffles(game))
        self.replay(game, load=True)

    def test_actual_commander_counts_with_the_regular_return_to_command(self):
        game = self.session(61341502)
        spell = self.add(game.engine, SETTLE, seat="C", zone="hand")
        fervor = self.add(game.engine, "Fervor")
        commander_id, = game.state.players["A"].zones["command"]
        commander = game.state.cards[commander_id]
        find = self.add(game.engine, "Forest", zone="library", ref="settle-commander-private-basic")
        self.start(game, spell, fervor, commander)
        self.cast(game, "A", commander)
        self.assertIsNone(self.resolve(game))
        self.attack(game, [(commander, "B")])
        self.cast(game, "C", spell, ["A"])
        self.expect_optional(self.resolve(game))
        self.assertEqual("exile", game.state.cards[commander_id].zone)
        self.act_ok(game, "A", {"action_id": "choose", "choice": "apply"})
        self.expect_search(game, 1)
        self.act_ok(game, "A", {"action_id": "choose", "search_cards": [find.ref]})
        self.assertEqual("state.commander_zone", game.state.pending_decision.kind)
        game = self.replay(game, load=True)
        decision = game.packet("pilot:A", full=True)["decision"]
        command, = (row for row in decision["legal_actions"] if row["id"] == "command")
        self.act_ok(game, "A", {"action_id": command["id"]})
        self.assertEqual("command", game.state.cards[commander_id].zone)
        self.assertTrue(game.state.cards[find.object_id].tapped)
        self.replay(game, load=True)

    def test_actual_zero_attacking_creatures_can_accept_zero_results_or_decline_without_shuffle(self):
        for seed, choice in ((61341503, "decline"), (61341504, "apply")):
            with self.subTest(choice=choice):
                game = self.session(seed)
                spell = self.add(game.engine, SETTLE, seat="C", zone="hand")
                creature = self.add(game.engine, "Grizzly Bears")
                self.start(game, spell)
                before = self.shuffles(game)
                self.cast(game, "C", spell, ["A"])
                self.expect_optional(self.resolve(game))
                self.act_ok(game, "A", {"action_id": "choose", "choice": choice})
                if choice == "apply":
                    self.expect_search(game, 0)
                    self.act_ok(game, "A", {"action_id": "choose", "search_cards": []})
                self.assertIsNone(self.resolve(game))
                self.assertEqual("battlefield", game.state.cards[creature.object_id].zone)
                self.assertEqual({seat: count + int(seat == "A" and choice == "apply")
                                  for seat, count in before.items()}, self.shuffles(game))
                self.replay(game, load=True)

    def test_complete_original_requires_a_player_and_the_committed_search_owner(self):
        original = self.db.lookup(SETTLE, fuzzy=False)
        report, semantics = register_card(self.db, self.registry, "Settle the Wreckage")
        self.assertTrue(report["strict_capability_ready"], report["blockers"])
        self.assertEqual("capability_closed", report["trust_basis"])
        program, = semantics.programs_for_oracle(original.oracle_id)
        self.assertEqual(["player"], list(program.target_schema["categories"]))
        effect, = program.effects
        followup = PublicExileFollowup.from_dict(effect["followup"])
        followup.validate_move(PublicZoneMoveSetSpec.from_dict(effect["set"]))
        self.assertEqual(2, followup.schema_version)
        wrapped, = followup.from_committed_count(12, target_player="A").effects
        leaf, = wrapped["effects"]
        validate_committed_attacking_exile_search(leaf)
        self.assertEqual(12, leaf["count"]["maximum"])
        deps = tuple(cap for cap in program.capability_dependencies if cap != ATTACKING_EXILE_SEARCH_CAPABILITY)
        incomplete = replace(program, capability_dependencies=deps,
                             capability_closure=self.registry.closure(deps, profile=PROFILE).to_dict())
        bound = bind_semantic_program_runtime(incomplete, capability_registry=self.registry, profile=PROFILE)
        self.assertFalse(bound["strict"])
        self.assertIn(f"capability:undeclared_runtime_dependency:{ATTACKING_EXILE_SEARCH_CAPABILITY}", bound["blockers"])
        for before, after in (
            ("attacking creatures target player controls", "creatures target player controls"),
            ("That player may search their library", "You may search your library"),
            ("battlefield tapped", "battlefield"),
            ("then shuffle.", "then shuffle. Change the name of each card in your hand."),
        ):
            with self.subTest(after=after):
                changed = replace(original, oracle_text=original.oracle_text.replace(before, after))
                self.assertNotEqual("exact", compile_oracle_card(
                    changed, capability_registry=self.registry, capability_profile=PROFILE,
                ).status)
        with patch("quorune.compiler.public_exile_followup_templates.attacking_creature_exile_search_template", return_value=None):
            self.assertNotEqual("exact", compile_oracle_card(
                original, capability_registry=self.registry, capability_profile=PROFILE,
            ).status)
