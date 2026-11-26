"""A complete landfall trigger with an ordered mana and second-resolution reveal."""

from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import unittest

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.ability_resolution_ordinal import (
    ABILITY_RESOLVED_HISTORY, SECOND_LAND_ENTRY_REVEAL_CAPABILITY, validate_second_ability_reveal,
)
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.replacement.immutable import FrozenMap, thaw_value
from quorune.resolution_mana_model import ResolutionManaChoiceSpec
from quorune.rules.ability_resolution_ordinal_shapes import second_ability_reveal_runtime_dependencies
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


NISSA = "Nissa, Resurgent Animist"
PROFILE = "commander_review"


class SecondLandfallRevealUntilSubtypeMatchTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "second-landfall-reveal.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures/second-landfall-reveal-until-subtype-match-cards.json"], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        commander = "Isamaru, Hound of Konda"
        cls.deck = DeckDefinition("Second landfall reveal", [
            DeckEntry(commander, 1, "commander"), DeckEntry("Plains", 30),
        ], [commander])

    def setup_library(self, game, names):
        for object_id in tuple(game.state.players["A"].zones["library"]):
            game.engine.move_card(object_id, "exile", reason="Arrange the initial focused library")
        return [self.add(game.engine, name, zone="library", ref=f"second-landfall-library-{i}")
                for i, name in enumerate(names)]

    def start(self, game, *cards, **kwargs):
        for card in cards:
            self.assert_closed(game, card)
        game.state.config.semantic_policy = "trusted_only"
        self.prepare(game, **kwargs)
        self.checkpoint(game)

    def act_ok(self, game, seat, request):
        result = game.act("pilot:" + seat, request)
        self.assertTrue(result.ok, result.summary)

    def play_land(self, game, land, seat="A"):
        self.priority(game, seat)
        actions = game.packet("pilot:" + seat, full=True)["decision"]["ctx"]["legal"]["actions"]
        offered, = (action for action in actions if action["id"] == "play-land:" + land.ref)
        self.act_ok(game, seat, {"action_id": offered["id"]})

    def copy_trigger(self, game, resonator):
        trigger, = game.state.stack
        self.priority(game, "A")
        actions = game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        offered, = (action for action in actions if action["id"].startswith("activate:" + resonator.ref + ":"))
        self.act_ok(game, "A", {"action_id": offered["id"], "targets": [trigger.ref], "pay": "auto"})
        self.resolve_top(game)
        self.assertEqual(2, len(game.state.stack))

    def resolution_events(self, game, source):
        return [event for event in game.state.turn_history.events
                if event.kind == ABILITY_RESOLVED_HISTORY and event.object_incarnation == source.logical_object_id]

    def open_mana_offer(self, game):
        pending = self.resolve(game)
        self.assertIsNotNone(pending)
        self.assertEqual(("semantic.choice", ["A"]), (pending.kind, pending.actors))
        action, = (action for action in pending.payload_by_actor["A"]["legal_actions"] if action["id"] == "choose")
        self.assertEqual(list("WUBRG"), list(action["choice_schema"]["legal_values"]))
        for seat in "BCD":
            self.assertIsNone(game.packet("pilot:" + seat, full=True)["decision"])

    def choose_color(self, game, color):
        pool = game.state.players["A"].mana_pool
        prior = pool.get(color, 0)
        self.act_ok(game, "A", {"action_id": "choose", "choice": color})
        self.assertEqual(prior + 1, game.state.players["A"].mana_pool.get(color, 0))

    def test_actual_copy_counts_and_second_reveal_preserves_the_unrevealed_suffix(self):
        for seed, match in (
            (61317601, "Nameless Inversion"), (61317602, "Llanowar Elves"), (61317603, "Fire Elemental"),
        ):
            with self.subTest(stopping_card=match):
                game = self.session(seed)
                source = self.add(game.engine, NISSA)
                resonator = self.add(game.engine, "Strionic Resonator")
                exploration = self.add(game.engine, "Exploration")
                forest = self.add(game.engine, "Forest", zone="hand")
                mountain = self.add(game.engine, "Mountain", zone="hand")
                suffix, hit, skip_a, skip_b = self.setup_library(game, [
                    "Llanowar Elves", match, "Plains", "Island",
                ])
                self.start(game, source, resonator, exploration, mana={"C": 2})
                self.play_land(game, forest)
                self.copy_trigger(game, resonator)
                self.assertEqual([], self.resolution_events(game, source))
                self.open_mana_offer(game)
                self.choose_color(game, "W")
                self.assertEqual("library", game.state.cards[hit.object_id].zone)

                self.open_mana_offer(game)
                for seat in "BCD":
                    prospective = game.packet("pilot:" + seat, full=True)
                    self.assertNotIn(hit.ref, json.dumps(prospective))
                    self.assertNotIn(suffix.ref, json.dumps(prospective))
                game = self.replay(game, load=True)
                before = authoritative_state_hash(game.state)
                denied = game.act("pilot:B", {"action_id": "choose", "choice": "G"})
                self.assertFalse(denied.ok, denied.summary)
                self.assertEqual(before, authoritative_state_hash(game.state))
                self.choose_color(game, "G")

                found = game.state.cards[hit.object_id]
                self.assertEqual(("hand", list("ABCD")), (found.zone, found.known_to))
                library = game.state.players["A"].zones["library"]
                self.assertEqual(suffix.object_id, library[-1])
                self.assertEqual({skip_a.object_id, skip_b.object_id}, set(library[:-1]))
                for seat in "BCD":
                    self.assertNotIn(suffix.ref, json.dumps(game.packet("pilot:" + seat, full=True)))

                self.play_land(game, mountain)
                self.open_mana_offer(game)
                self.choose_color(game, "U")
                self.assertEqual("library", game.state.cards[suffix.object_id].zone)
                self.assertEqual(3, len(self.resolution_events(game, source)))
                self.replay(game, load=True)

    def test_actual_no_match_random_bottom_keeps_every_card_and_no_shuffle(self):
        game = self.session(61317604)
        source = self.add(game.engine, NISSA)
        resonator = self.add(game.engine, "Strionic Resonator")
        forest = self.add(game.engine, "Forest", zone="hand")
        cards = self.setup_library(game, ["Plains", "Island", "Mountain"])
        self.start(game, source, resonator, mana={"C": 2})
        hand = set(game.state.players["A"].zones["hand"]) - {forest.object_id}
        shuffles = game.state.players["A"].stats.get("shuffle_count", 0)
        self.play_land(game, forest)
        self.copy_trigger(game, resonator)
        for color in ("W", "U"):
            self.open_mana_offer(game)
            self.choose_color(game, color)
        self.assertEqual(hand, set(game.state.players["A"].zones["hand"]))
        self.assertEqual({card.object_id for card in cards}, set(game.state.players["A"].zones["library"]))
        self.assertEqual(shuffles, game.state.players["A"].stats.get("shuffle_count", 0))
        for card in cards:
            current = game.state.cards[card.object_id]
            self.assertEqual(([], []), (current.known_to, current.revealed_to))
        for seat in "ABCD":
            self.assertFalse(game.packet("pilot:" + seat, full=True)["state"]["players"]["A"].get("known_top"))
        self.assertEqual(2, len(self.resolution_events(game, source)))
        self.replay(game, load=True)

    def test_actual_countered_landfall_does_not_advance_the_resolution_ordinal(self):
        game = self.session(61317605)
        source = self.add(game.engine, NISSA)
        exploration = self.add(game.engine, "Exploration")
        stifle = self.add(game.engine, "Stifle", seat="B", zone="hand")
        self.add(game.engine, "Island", seat="B")
        land_a = self.add(game.engine, "Forest", zone="hand")
        land_b = self.add(game.engine, "Plains", zone="hand")
        hit, = self.setup_library(game, ["Llanowar Elves"])
        self.start(game, source, exploration, stifle)
        self.play_land(game, land_a)
        trigger, = game.state.stack
        self.cast(game, "B", stifle, [trigger.ref])
        self.resolve_top(game)
        self.assertFalse(game.state.stack)
        self.assertEqual([], self.resolution_events(game, source))
        self.play_land(game, land_b)
        self.open_mana_offer(game)
        self.choose_color(game, "B")
        self.assertEqual("library", game.state.cards[hit.object_id].zone)
        self.assertEqual(1, len(self.resolution_events(game, source)))
        self.replay(game, load=True)

    def test_closed_original_requires_both_instructions_and_the_runtime_scope(self):
        card = self.db.lookup(NISSA, fuzzy=False)
        report, semantics = register_card(self.db, self.registry, "Nissa, Resurgent Animist")
        self.assertTrue(report["strict_capability_ready"], report["blockers"])
        self.assertEqual("capability_closed", report["trust_basis"])
        program, = semantics.programs_for_oracle(card.oracle_id)
        prefix, wrapper = program.effects
        self.assertEqual(("land.enter", "battlefield", None), (program.event, program.active_zone, program.target_schema))
        self.assertEqual(ResolutionManaChoiceSpec("one_color", 1), ResolutionManaChoiceSpec.from_effect(prefix))
        child, = validate_second_ability_reveal(wrapper)
        self.assertEqual("random", child["remainder_order"])
        self.assertIn(SECOND_LAND_ENTRY_REVEAL_CAPABILITY, second_ability_reveal_runtime_dependencies(program))
        forged = thaw_value(FrozenMap(wrapper))
        forged["effects"][0]["remainder_order"] = "chosen"
        for changes in ({"event": "creature.enter"}, {"effects": (prefix, forged)}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                second_ability_reveal_runtime_dependencies(replace(program, **changes))
        deps = tuple(cap for cap in program.capability_dependencies if cap != SECOND_LAND_ENTRY_REVEAL_CAPABILITY)
        absent = replace(program, capability_dependencies=deps,
                         capability_closure=self.registry.closure(deps, profile=PROFILE).to_dict())
        bound = bind_semantic_program_runtime(absent, capability_registry=self.registry, profile=PROFILE)
        self.assertFalse(bound["strict"])
        self.assertIn(f"capability:undeclared_runtime_dependency:{SECOND_LAND_ENTRY_REVEAL_CAPABILITY}", bound["blockers"])
        for old, new in (
            ("a land you control", "a creature you control"),
            ("the second time", "the third time"),
            ("an Elf or Elemental", "an Elf or Goblin"),
            ("in a random order.", "in any order."),
            ("in a random order.", "in a random order. Draw a card."),
        ):
            with self.subTest(new=new):
                changed = replace(card, oracle_text=card.oracle_text.replace(old, new))
                self.assertNotEqual("exact", compile_oracle_card(
                    changed, capability_registry=self.registry, capability_profile=PROFILE,
                ).status)
        with patch("quorune.compiler.resolution_condition_templates.second_ability_reveal_trigger_result", return_value=None):
            self.assertNotEqual("exact", compile_oracle_card(
                card, capability_registry=self.registry, capability_profile=PROFILE,
            ).status)
