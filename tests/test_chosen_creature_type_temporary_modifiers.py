"""One chosen creature type selects a locked temporary modifier set."""

import json
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
from quorune.record import authoritative_state_hash
from quorune.resolution_characteristic_model import CHOSEN_CREATURE_TYPE_MODIFIER_FIELD, ChosenCreatureTypeModifier
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


UNITY = "Tribal Unity"
FORCEMAGE = "Tribal Forcemage"


class ChosenCreatureTypeTemporaryModifierTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "chosen-type-modifiers.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures/chosen-creature-type-temporary-modifiers-cards.json"], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Chosen type temporary modifiers", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"), DeckEntry("Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def test_complete_originals_keep_their_cast_x_and_morph_authority(self):
        for name, expected in ((UNITY, {"resolve"}), (FORCEMAGE, {"morph.action", "permanent.turned_face_up"})):
            with self.subTest(card=name):
                card = self.db.lookup(name, fuzzy=False)
                binding, semantics = register_card(self.db, self.registry, name)
                self.assertTrue(binding["strict_capability_ready"], binding["blockers"])
                programs = semantics.programs_for_oracle(card.oracle_id)
                self.assertEqual(expected, {program.event for program in programs})
                choice, = [program for program in programs if program.effects]
                effect, = choice.effects
                self.assertIsNone(choice.target_schema)
                self.assertEqual("choose_creature_type", effect["op"])
                self.assertIn("choice.creature_type.fixed_battlefield_result", choice.capability_dependencies)
                self.assertIn("continuous.resolution.fixed_characteristics_until_end_of_turn", choice.capability_dependencies)
                if name == UNITY:
                    self.assertIn("quantity_expression.declared_effect_amount", choice.capability_dependencies)
                    alternate = replace(card, mana_cost="{2}{G}")
                else:
                    self.assertIn("combat.damage.assignment.trample", choice.capability_dependencies)
                    alternate = replace(card, oracle_text=card.oracle_text.replace("trample", "shroud"))
                denied = compile_oracle_card(alternate, capability_registry=self.registry)
                self.assertNotEqual("exact", denied.status)

    def test_raw_modifier_denies_targeted_and_unowned_declared_x_scopes(self):
        card = self.db.lookup(UNITY, fuzzy=False)
        _, semantics = register_card(self.db, self.registry, UNITY)
        program, = semantics.programs_for_oracle(card.oracle_id)
        effect, = program.effects
        payload = effect[CHOSEN_CREATURE_TYPE_MODIFIER_FIELD]
        self.assertIn("quantity_expression.declared_effect_amount", program.capability_dependencies)
        for altered in (
            replace(program, target_schema={"zones": ["battlefield"], "types_all": ["creature"], "count": 1}),
            replace(program, event="permanent.turned_face_up", active_zone="battlefield",
                    event_condition={"field": "card", "op": "eq", "value": "$source.ref"}),
            replace(program, effects=({**effect, CHOSEN_CREATURE_TYPE_MODIFIER_FIELD: {
                **payload, "amount": {**payload["amount"], "coefficient": -1}}},)),
            replace(program, effects=({**effect, CHOSEN_CREATURE_TYPE_MODIFIER_FIELD: {
                **payload, "trample": True}},)),
        ):
            result = bind_semantic_program_runtime(altered, capability_registry=self.registry, profile="commander_review")
            self.assertFalse(result["strict"], result)

    def stats(self, game, creature):
        return tuple(game.engine._numeric_stat(creature.object_id, name) for name in ("power", "toughness"))

    def settle_type(self, game, subtype):
        decision = self.resolve(game)
        self.assertEqual(("semantic.choice", ["A"]), (decision.kind, decision.actors))
        packet = game.packet("pilot:A", full=True)["decision"]
        legal_types = set(packet["legal_actions"][0]["choice_schema"]["legal_values"])
        self.assertIn(subtype, legal_types)
        self.assertNotIn("Laser", legal_types)
        for seat in ("B", "C", "D"):
            self.assertIsNone(game.packet("pilot:" + seat, full=True)["decision"])
        before = authoritative_state_hash(game.state)
        for seat, value in (("B", subtype), ("A", "Laser")):
            result = game.act("pilot:" + seat, {"action_id": "choose", "creature_type": value})
            self.assertFalse(result.ok, result.summary)
            self.assertEqual(before, authoritative_state_hash(game.state))
        result = game.act("pilot:A", {"action_id": "choose", "creature_type": subtype})
        self.assertTrue(result.ok, result.summary)
        self.assertFalse(game.state.stack)

    def cast_x(self, game, source, *, amount, reserve=None):
        self.priority(game, "A")
        offer = self.action(game, "A", source)
        command = {"action_id": offer["id"], "pay": "auto", "x": amount, "reserve": reserve or {}}
        result = game.act("pilot:A", command)
        self.assertTrue(result.ok, result.summary)
        spell, = [item for item in game.state.stack if item.card_object_id == source.object_id]
        self.assertEqual(amount, spell.x_value)

    def test_actual_paid_unity_locks_current_elves_and_excludes_later_and_returned_incarnations(self):
        game = self.session(61120301)
        engine = game.engine
        unity = self.add(engine, UNITY, zone="hand")
        early = self.add(engine, "Llanowar Elves", ref="unity-first-elf")
        foreign_elf = self.add(engine, "Llanowar Elves", seat="B", ref="unity-foreign-elf")
        non_elf = self.add(engine, "Grizzly Bears", seat="C")
        arrival = self.add(engine, "Elvish Mystic", zone="hand")
        flicker = self.add(engine, "Flicker", zone="hand")
        private = self.add(engine, "Counterspell", seat="D", zone="hand", ref="unity-hidden-hand")
        for card in (unity, early, foreign_elf, arrival, flicker):
            self.assert_closed(game, card)
        self.prepare(game, mana={"C": 5, "G": 3, "W": 1})
        self.checkpoint(game)
        self.cast_x(game, unity, amount=2, reserve={"C": 1, "G": 1, "W": 1})
        self.assertEqual((1, 1), self.stats(game, early))
        self.settle_type(game, "Elf")
        self.assertEqual((3, 3), self.stats(game, early), "The selected type must boost the original Elf set")
        self.assertEqual((3, 3), self.stats(game, foreign_elf))
        self.assertEqual((2, 2), self.stats(game, non_elf))

        first = game.state.cards[early.object_id].logical_object_id
        self.cast_for(game, "A", flicker, [early.ref], reserve={"G": 1})
        self.resolve_top(game)
        self.assertNotEqual(first, game.state.cards[early.object_id].logical_object_id)
        self.assertEqual((1, 1), self.stats(game, early))
        self.cast_for(game, "A", arrival)
        self.resolve_top(game)
        self.assertEqual((1, 1), self.stats(game, arrival))
        self.assertEqual((3, 3), self.stats(game, foreign_elf))
        self.until(game, lambda current: current.state.active_player == "B"
                   and current.state.pending_decision.kind == "priority")
        self.assertEqual((1, 1), self.stats(game, foreign_elf))
        for viewer in ("pilot:A", "pilot:B", "pilot:C", "spectator"):
            self.assertNotIn(private.ref, json.dumps(game.packet(viewer, full=True)))
        self.replay(game, load=True)

    def test_actual_selected_type_witness_kills_boosting_a_different_subtype(self):
        modifier = ChosenCreatureTypeModifier.modifier_effect

        def wrong_type(scope, subtype):
            return modifier(scope, "bear")

        with patch.object(ChosenCreatureTypeModifier, "modifier_effect", wrong_type):
            with self.assertRaisesRegex(AssertionError, "selected type must boost"):
                self.test_actual_paid_unity_locks_current_elves_and_excludes_later_and_returned_incarnations()

    def test_actual_zero_x_still_resolves_the_choice_and_changes_no_statistics(self):
        game = self.session(61120302)
        unity = self.add(game.engine, UNITY, zone="hand")
        elf = self.add(game.engine, "Llanowar Elves", seat="B")
        self.assert_closed(game, unity)
        self.prepare(game, mana={"C": 2, "G": 1})
        self.checkpoint(game)
        self.cast_x(game, unity, amount=0)
        self.settle_type(game, "Elf")
        self.assertEqual((1, 1), self.stats(game, elf))

    def test_actual_paid_morph_stays_private_then_grants_statistics_and_trample_to_current_elves(self):
        game = self.session(70237034)
        engine = game.engine
        source = self.add(engine, FORCEMAGE, zone="hand")
        elf = self.add(engine, "Llanowar Elves", seat="B")
        non_elf = self.add(engine, "Grizzly Bears", seat="C")
        self.assert_closed(game, source)
        self.prepare(game, mana={"C": 4, "G": 1})
        self.checkpoint(game)
        cast = {"action_id": "cast-morph:" + source.ref, "pay": "auto", "reserve": {"C": 1, "G": 1}}
        actions = game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        self.assertIn(cast["action_id"], {row["id"] for row in actions})
        applied = game.act("pilot:A", cast)
        self.assertTrue(applied.ok, applied.summary)
        self.resolve_top(game)
        current = game.state.cards[source.object_id]
        self.assertTrue(current.face_down)
        incarnation = current.logical_object_id
        for principal in ("pilot:B", "pilot:C", "pilot:D", "spectator"):
            self.assertNotIn(FORCEMAGE, json.dumps(game.packet(principal, full=True)))
        self.assertEqual((1, 1), self.stats(game, elf))
        self.priority(game, "A")
        turn = {"action_id": "turn-face-up:" + current.ref, "pay": "auto"}
        actions = game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        self.assertIn(turn["action_id"], {row["id"] for row in actions})
        before = authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:B", turn).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        result = game.act("pilot:A", turn)
        self.assertTrue(result.ok, result.summary)
        self.assertEqual(incarnation, game.state.cards[source.object_id].logical_object_id)
        self.assertFalse(game.state.cards[source.object_id].face_down)
        self.settle_type(game, "Elf")
        for creature in (source, elf):
            self.assertEqual((3, 3), self.stats(game, creature))
            self.assertIn("trample", engine._combat_keywords(game.state.cards[creature.object_id]))
        self.assertEqual((2, 2), self.stats(game, non_elf))
        self.assertNotIn("trample", engine._combat_keywords(game.state.cards[non_elf.object_id]))
        self.until(game, lambda current: current.state.active_player == "B"
                   and current.state.pending_decision.kind == "priority")
        self.assertNotIn("trample", engine._combat_keywords(game.state.cards[elf.object_id]))
        self.assertEqual((1, 1), self.stats(game, elf))
        self.replay(game, load=True)
