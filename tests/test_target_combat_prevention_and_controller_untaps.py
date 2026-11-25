"""Exercise a targeted combat shield and two controller-relative untaps."""

from dataclasses import replace
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
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


ORIGINAL = "Telekinesis"
RESTRAINT = "does_not_untap_next"


class TargetCombatPreventionAndControllerUntapsTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "target-combat-controller-untaps.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures/target-combat-prevention-and-controller-untaps-cards.json"], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Two controller untaps", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"), DeckEntry("Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def source_program(self):
        card = self.db.lookup(ORIGINAL, fuzzy=False)
        binding, semantics = register_card(self.db, self.registry, ORIGINAL)
        self.assertTrue(binding["strict_capability_ready"], binding["blockers"])
        program, = [item for item in semantics.programs_for_oracle(card.oracle_id) if item.event == "resolve"]
        return card, program

    def test_complete_original_keeps_one_target_and_three_ordered_results(self):
        card, program = self.source_program()
        self.assertEqual(["creature"], program.target_schema["types_any"])
        self.assertEqual(1, program.target_schema["count"])
        tap, shield, untap = program.effects
        self.assertEqual({"op": "tap", "card": "$target.0"}, tap)
        self.assertEqual(
            ("create_damage_prevention_shield", "combat", "until_end_of_turn", "$target.0", "*"),
            (shield["op"], shield["damage_kind"], shield["duration"], shield["chosen_source"], shield["subject"]),
        )
        self.assertEqual(["battlefield"], shield["source_predicate"]["zones"])
        self.assertTrue(shield["source_predicate"]["known_to_actor"])
        self.assertEqual({"op": "prohibit_next_untap", "card": "$target.0",
                          "schema_version": 2, "controller_untap_step_count": 2}, untap)
        for text in (
            card.oracle_text.replace("target creature", "target permanent"),
            card.oracle_text.replace("by that creature", "to that creature"),
            card.oracle_text.replace("next two untap", "next three untap"),
            card.oracle_text + " Draw a card.",
        ):
            with self.subTest(text=text):
                self.assertNotEqual("exact", compile_oracle_card(replace(card, oracle_text=text),
                                    capability_registry=self.registry).status)

    def test_raw_versioned_restriction_rejects_a_different_target_count_or_result_order(self):
        _, program = self.source_program()
        tap, shield, untap = program.effects
        for effects in (
            (tap, untap),
            (shield, tap, untap),
            (tap, {key: value for key, value in shield.items() if key != "source_predicate"}, untap),
            (tap, shield, {**untap, "card": "$target.1"}),
            (tap, shield, {**untap, "controller_untap_step_count": 3}),
            (tap, shield, {**untap, "schema_version": True}),
        ):
            with self.subTest(effects=effects):
                binding = bind_semantic_program_runtime(replace(program, effects=effects),
                    capability_registry=self.registry, profile="commander_review")
                self.assertFalse(binding["strict"], binding)

    def test_fend_off_prevents_only_the_original_chosen_attacker_through_replay(self):
        game = self.session(61343188)
        chosen = self.add(game.engine, "Grizzly Bears", ref="fend-original-target")
        other = self.add(game.engine, "Grizzly Bears", ref="fend-independent-attacker")
        chosen.acquired_control_turn_count = -1
        other.acquired_control_turn_count = -1
        spell = self.add(game.engine, "Fend Off", zone="hand")
        self.assert_closed(game, spell)
        self.prepare(game, mana={"C": 1, "W": 1})
        self.checkpoint(game)
        self.cast_for(game, "A", spell, [chosen.ref])
        self.resolve_top(game)
        shield, = game.state.damage_prevention_shields
        self.assertEqual(chosen.object_id, shield.chosen_source.object_id)
        game = self.replay(game, load=True)
        self.attack(game, [(chosen, "B"), (other, "D")])
        self.until(game, lambda match: match.state.step == "end_combat")
        self.assertEqual(40, game.state.players["B"].life)
        self.assertEqual(38, game.state.players["D"].life)
        self.replay(game, load=True)

    def test_actual_target_still_attacks_and_deals_noncombat_damage_through_the_shield(self):
        game = self.session(61343101)
        engine = game.engine
        target = self.add(engine, "Serra Angel")
        other = self.add(engine, "Grizzly Bears")
        elf = self.add(engine, "Llanowar Elves", seat="B")
        spell = self.add(engine, ORIGINAL, zone="hand")
        fight = self.add(engine, "Pit Fight", zone="hand")
        for index, name in enumerate(("Island", "Island", "Forest", "Plains")):
            self.add(engine, name, ref=f"telekinesis-combat-land-{index}")
        for card in (spell, fight, target):
            self.assert_closed(game, card)
        self.prepare(game)
        self.checkpoint(game)
        self.attack(game, [(target, "B"), (other, "B")])
        self.assertFalse(game.state.cards[target.object_id].tapped)
        self.cast_for(game, "A", spell, [target.ref])
        self.resolve_top(game)
        self.assertTrue(game.state.cards[target.object_id].tapped)
        self.assertEqual("B", game.state.cards[target.object_id].attacking)
        self.cast_for(game, "A", fight, [target.ref, elf.ref])
        self.resolve_top(game)
        self.assertEqual("graveyard", game.state.cards[elf.object_id].zone)
        self.assertEqual(1, game.state.cards[target.object_id].marked_damage)
        life = game.state.players["B"].life
        game = self.replay(game, load=True)
        self.until(game, lambda current: current.state.step == "end_combat")
        self.assertEqual(life - 2, game.state.players["B"].life)
        self.replay(game, load=True)

    def test_two_physical_untaps_follow_the_creature_across_an_actual_control_change(self):
        game = self.session(61343102)
        engine = game.engine
        for seat in "ABC":
            hand = tuple(game.state.players[seat].zones["hand"])
            for object_id in hand[:-3]:
                engine.move_card(object_id, "library", position="bottom", log=False, semantic_events=False)
        target = self.add(engine, "Grizzly Bears", seat="B")
        spell = self.add(engine, ORIGINAL, zone="hand")
        gift = self.add(engine, "Donate", seat="B", zone="hand")
        warp = self.add(engine, "Time Warp", seat="B", zone="hand")
        for index in range(8):
            self.add(engine, "Island", seat="B", ref=f"telekinesis-controller-land-{index}")
        for card in (spell, gift, warp):
            self.assert_closed(game, card)
        self.prepare(game, mana={"U": 2})
        self.checkpoint(game)
        self.cast_for(game, "A", spell, [target.ref])
        self.resolve_top(game)
        self.assertEqual((True, 2), (game.state.cards[target.object_id].tapped,
                                    game.state.cards[target.object_id].annotations[RESTRAINT]))

        self.until(game, lambda current: current.state.active_player == "B" and current.state.step == "upkeep")
        self.assertIs(game.state.cards[target.object_id].annotations[RESTRAINT], True)
        self.assertTrue(game.state.cards[target.object_id].tapped)
        self.until(game, lambda current: current.state.active_player == "B" and current.state.step == "main")
        self.cast_for(game, "B", gift, ["C", target.ref])
        self.resolve_top(game)
        self.assertEqual("C", game.state.cards[target.object_id].controller)
        self.cast_for(game, "B", warp, ["C"])
        self.resolve_top(game)
        extra, = game.state.extra_turns
        game = self.replay(game, load=True)
        self.until(game, lambda current: current.state.current_turn.turn_id == extra.turn_id
                   and current.state.step == "upkeep")
        self.assertTrue(game.state.cards[target.object_id].tapped)
        self.assertNotIn(RESTRAINT, game.state.cards[target.object_id].annotations)
        self.until(game, lambda current: current.state.active_player == "C"
                   and current.state.current_turn.turn_id != extra.turn_id and current.state.step == "upkeep")
        self.assertFalse(game.state.cards[target.object_id].tapped)
        self.replay(game, load=True)

    def test_actual_blink_invalidates_the_original_target_for_every_result(self):
        game = self.session(61343103)
        target = self.add(game.engine, "Grizzly Bears")
        spell = self.add(game.engine, ORIGINAL, zone="hand")
        blink = self.add(game.engine, "Cloudshift", zone="hand")
        for card in (spell, blink):
            self.assert_closed(game, card)
        self.prepare(game, mana={"U": 2, "W": 1})
        self.checkpoint(game)
        original = target.logical_object_id
        self.cast_for(game, "A", spell, [target.ref])
        self.cast_for(game, "A", blink, [target.ref])
        self.resolve_top(game)
        self.assertNotEqual(original, game.state.cards[target.object_id].logical_object_id)
        self.resolve_top(game)
        current = game.state.cards[target.object_id]
        self.assertFalse(current.tapped)
        self.assertNotIn(RESTRAINT, current.annotations)
        self.assertFalse(game.state.damage_prevention_shields)
        self.replay(game, load=True)
