"""An exact original cast can apply a closed preparation before its base result."""

from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.carddb import CardDatabase
from quorune.cast_origin_replacement import MAIN_PHASE_CAST_PREPARATION_VERSION
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


class FixedMainPhasePreparationTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "fixed-main-phase-preparations.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures/fixed-main-phase-preparations-cards.json"], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Exact main-phase preparation", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"), DeckEntry("Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def test_complete_originals_put_only_the_closed_preparation_before_the_original_instruction(self):
        for name, prefix, original in (
            ("Tail Swipe", "modify_stats_until_end_of_turn", "creature_power_damage"),
            ("Precognitive Perception", "scry", "draw"),
        ):
            with self.subTest(name=name):
                admitted, semantics = register_card(self.db, self.registry, name)
                self.assertTrue(admitted["strict_capability_ready"], admitted["blockers"])
                record = self.db.lookup(name, fuzzy=False)
                spell, = semantics.programs_for_oracle(record.oracle_id)
                instruction, = spell.effects
                self.assertEqual(MAIN_PHASE_CAST_PREPARATION_VERSION, instruction["schema_version"])
                self.assertEqual([prefix, original], [child["op"] for child in instruction["effects"]])
                self.assertEqual(instruction["otherwise_effects"], instruction["effects"][1:])
                forged = {**instruction, "effects": list(reversed(instruction["effects"]))}
                self.assertFalse(bind_semantic_program_runtime(
                    replace(spell, effects=[forged]), capability_registry=self.registry, profile="commander_review",
                )["strict"])
                changed = replace(record, oracle_text=record.oracle_text.replace("your main phase", "another player's main phase"))
                self.assertNotEqual("exact", compile_oracle_card(
                    changed, capability_registry=self.registry, capability_profile="commander_review",
                ).status)

    def current(self, game, card):
        return game.state.cards[card.object_id]

    def test_actual_main_phase_swipe_pumps_before_the_fight_and_other_main_phase_fights_normally(self):
        for actor, opposing, survives in (("A", "B", True), ("B", "A", False)):
            with self.subTest(actor=actor):
                game = self.session(60128001 + survives)
                spell = self.add(game.engine, "Tail Swipe", zone="hand", seat=actor)
                fighter = self.add(game.engine, "Grizzly Bears", seat=actor, ref="prepared-fighter")
                enemy = self.add(game.engine, "Grizzly Bears", seat=opposing, ref="prepared-opponent")
                self.assert_closed(game, spell)
                self.prepare(game, actor=actor, active="A", mana={"G": 1})
                self.checkpoint(game)
                self.cast_for(game, actor, spell, [fighter.ref, enemy.ref])
                self.assertIsNone(self.resolve(game))
                self.assertEqual("graveyard", self.current(game, enemy).zone)
                self.assertEqual("battlefield" if survives else "graveyard", self.current(game, fighter).zone)
                if survives:
                    self.assertEqual(3, game.engine._numeric_stat(fighter.object_id, "power"))
                    self.assertEqual(2, self.current(game, fighter).marked_damage)
                self.replay(game, load=True)

    def test_actual_swipe_keeps_the_legal_buff_and_omits_fight_when_either_target_leaves(self):
        game = self.session(60128003)
        spell = self.add(game.engine, "Tail Swipe", zone="hand")
        fighter = self.add(game.engine, "Grizzly Bears", ref="partial-fighter")
        enemy = self.add(game.engine, "Grizzly Bears", seat="B", ref="partial-opponent")
        bounce = self.add(game.engine, "Unsummon", zone="hand", seat="B")
        for card in (spell, bounce):
            self.assert_closed(game, card)
        self.prepare(game, mana={"G": 1})
        game.state.players["B"].mana_pool["U"] = 1
        self.checkpoint(game)
        self.cast_for(game, "A", spell, [fighter.ref, enemy.ref])
        without_enemy = self.replay(game, load=True)
        self.cast_for(without_enemy, "B", bounce, [enemy.ref])
        self.resolve_top(without_enemy)
        self.assertIsNone(self.resolve(without_enemy))
        self.assertEqual(("battlefield", "hand"), (self.current(without_enemy, fighter).zone,
                                                   self.current(without_enemy, enemy).zone))
        self.assertEqual(3, without_enemy.engine._numeric_stat(fighter.object_id, "power"))
        self.assertEqual(0, self.current(without_enemy, fighter).marked_damage)
        self.replay(without_enemy, load=True)

        self.cast_for(game, "B", bounce, [fighter.ref])
        self.resolve_top(game)
        self.assertIsNone(self.resolve(game))
        self.assertEqual(("hand", "battlefield"), (self.current(game, fighter).zone, self.current(game, enemy).zone))
        self.assertEqual(0, self.current(game, enemy).marked_damage)
        self.replay(game, load=True)

    def test_actual_perception_scries_privately_before_drawing_and_the_uncast_copy_only_draws(self):
        game = self.session(60128004)
        spell = self.add(game.engine, "Precognitive Perception", zone="hand")
        copy = self.add(game.engine, "Twincast", zone="hand", seat="B")
        for card in (spell, copy):
            self.assert_closed(game, card)
        self.prepare(game, mana={"C": 3, "U": 2})
        game.state.players["B"].mana_pool["U"] = 2
        self.checkpoint(game)
        self.cast_for(game, "A", spell)
        original = game.state.stack[-1]
        self.cast_for(game, "B", copy, [original.ref])
        initial_a = list(game.state.players["A"].zones["hand"])
        initial_b = list(game.state.players["B"].zones["hand"])
        self.resolve_top(game)
        self.assertEqual("spell_copy", game.state.stack[-1].kind)
        self.resolve_top(game)
        self.assertEqual(len(initial_b) + 3, len(game.state.players["B"].zones["hand"]))
        pending = self.resolve(game)
        self.assertEqual(["A"], pending.actors)
        self.assertEqual(initial_a, game.state.players["A"].zones["hand"])
        top = list(reversed(game.state.players["A"].zones["library"][-3:]))
        refs = [game.state.cards[object_id].ref for object_id in top]
        for viewer in ("pilot:B", "pilot:C", "spectator"):
            packet = game.packet(viewer, full=True)
            self.assertIsNone(packet["decision"])
            self.assertTrue(all(ref not in json.dumps(packet) for ref in refs))
        game = self.replay(game, load=True)
        before = authoritative_state_hash(game.state)
        selected = {"action_id": "choose", "cards": {"top": list(reversed(refs)), "bottom": []}}
        self.assertFalse(game.act("pilot:B", selected).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        result = game.act("pilot:A", selected)
        self.assertTrue(result.ok, result.summary)
        self.assertIsNone(self.resolve(game))
        self.assertEqual(initial_a + list(reversed(top)), game.state.players["A"].zones["hand"])
        self.replay(game, load=True)
