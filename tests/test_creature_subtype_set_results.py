from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch
import unittest

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.attachments import attach_objects
from quorune.carddb import CardDatabase
from quorune.creature_subtypes import CREATURE_SUBTYPES, canonical_creature_subtype
from quorune.deck import DeckDefinition, DeckEntry
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.creature_subtype_set_results import (
    CreatureSubtypeSetResult, creature_subtype_set_runtime_dependencies,
)
from scripts.build_test_database import build_fixture_database


class CreatureSubtypeSetResultsTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "creature-subtype-set-results.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures/creature-subtype-set-results-cards.json"], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        commander = "Isamaru, Hound of Konda"
        cls.deck = DeckDefinition("Temporary creature types", [DeckEntry(commander, 1, "commander"),
                                                             DeckEntry("Plains", 30)], [commander])

    def types(self, game, card):
        return game.engine._effective_type_parts(game.state.cards[card.object_id])

    def subtypes(self, game, card):
        return {canonical_creature_subtype(value) for value in self.types(game, card)[1]} - {None}

    def stats(self, game, card):
        return tuple(game.engine._numeric_stat(card.object_id, key) for key in ("power", "toughness"))

    def keywords(self, game, card):
        return {value.casefold() for value in game.engine._effective_card_data(game.state.cards[card.object_id])["keywords"]}

    def attach(self, game, name, card):
        attachment = self.add(game.engine, name)
        self.assert_closed(game, attachment)
        attach_objects(game.state.cards, attachment, card, source_timestamp=attachment.zone_timestamp,
                       players=game.state.players)

    def test_actual_amoeboid_adds_and_removes_types_without_changing_abilities(self):
        for add in (True, False):
            with self.subTest(add=add):
                game = self.session(205030101 + add)
                source = self.add(game.engine, "Amoeboid Changeling")
                target = self.add(game.engine, "Ornithopter") if add else source
                self.assert_closed(game, source)
                self.prepare(game)
                self.checkpoint(game)
                action = "gains all creature types" if add else "loses all creature types"
                ability, = [row for row in game.engine._activated_abilities(source) if action in row.effect_text.casefold()]
                result = game.act("pilot:A", {"action_id": "activate:" + source.ref + ":" + ability.ability_id,
                                              "targets": [target.ref], "pay": "auto"})
                self.assertTrue(result.ok, result.summary)
                self.assertIsNone(self.resolve(game))
                self.assertEqual(CREATURE_SUBTYPES if add else set(), self.subtypes(game, target))
                if add:
                    self.assertEqual({"artifact", "creature"}, set(self.types(game, target)[0]))
                    self.assertIn("flying", self.keywords(game, target))
                else:
                    self.assertIn("changeling", self.keywords(game, target))
                self.replay(game, load=True)

    def test_actual_nameless_inversion_changes_stats_and_removes_creature_types(self):
        game = self.session(205030103)
        spell = self.add(game.engine, "Nameless Inversion", zone="hand")
        bear = self.add(game.engine, "Grizzly Bears")
        self.attach(game, "Holy Strength", bear)
        self.assert_closed(game, spell)
        self.prepare(game, mana={"C": 1, "B": 1})
        self.checkpoint(game)
        self.cast_for(game, "A", spell, [bear.ref])
        self.assertIsNone(self.resolve(game))
        self.assertEqual((6, 1), self.stats(game, bear))
        self.assertFalse(self.subtypes(game, bear))
        self.assertEqual({"creature"}, set(self.types(game, bear)[0]))
        self.replay(game, load=True)

    def test_actual_blades_can_resolve_without_targets_or_with_one_remaining(self):
        for choose in (False, True):
            with self.subTest(choose=choose):
                game = self.session(205030104 + choose)
                spell = self.add(game.engine, "Blades of Velis Vel", zone="hand")
                bear = self.add(game.engine, "Grizzly Bears")
                giant = self.add(game.engine, "Hill Giant", seat="C")
                blink = self.add(game.engine, "Flicker of Fate", seat="B", zone="hand")
                for card in (spell, blink):
                    self.assert_closed(game, card)
                original = giant.logical_object_id
                self.prepare(game, mana={"C": 1, "R": 1})
                game.state.players["B"].mana_pool.update({"C": 1, "W": 1})
                self.checkpoint(game)
                self.cast_for(game, "A", spell, [bear.ref, giant.ref] if choose else [])
                if choose:
                    self.cast_for(game, "B", blink, [giant.ref])
                    self.resolve_top(game)
                    self.assertNotEqual(original, game.state.cards[giant.object_id].logical_object_id)
                self.assertIsNone(self.resolve(game))
                self.assertEqual(CREATURE_SUBTYPES if choose else {"bear"}, self.subtypes(game, bear))
                self.assertEqual((4, 2) if choose else (2, 2), self.stats(game, bear))
                self.assertEqual({"giant"}, self.subtypes(game, giant))
                self.replay(game, load=True)

    def test_actual_wings_sets_base_preserves_modifiers_and_expires(self):
        game = self.session(205030106)
        spell = self.add(game.engine, "Wings of Velis Vel", zone="hand")
        bear = self.add(game.engine, "Grizzly Bears")
        self.attach(game, "Bonesplitter", bear)
        bear.counters["+1/+1"] = 1
        self.assert_closed(game, spell)
        self.prepare(game, mana={"C": 1, "U": 1})
        self.checkpoint(game)
        self.cast_for(game, "A", spell, [bear.ref])
        self.assertIsNone(self.resolve(game))
        self.assertEqual((7, 5), self.stats(game, bear))
        self.assertEqual(CREATURE_SUBTYPES, self.subtypes(game, bear), "Wings grants all creature types")
        self.assertIn("flying", self.keywords(game, bear))
        turn = game.state.turn_sequence
        self.until(game, lambda current: current.state.turn_sequence > turn)
        self.assertEqual((5, 3), self.stats(game, bear))
        self.assertEqual({"bear"}, self.subtypes(game, bear))
        self.assertNotIn("flying", self.keywords(game, bear))
        self.replay(game, load=True)

    def test_actual_shields_locks_creatures_controlled_by_the_target_at_resolution(self):
        game = self.session(205030107)
        spell = self.add(game.engine, "Shields of Velis Vel", zone="hand")
        retained = self.add(game.engine, "Grizzly Bears", seat="C", ref="shield-retained")
        moved = self.add(game.engine, "Hill Giant", seat="C", ref="shield-moved")
        late = self.add(game.engine, "Grizzly Bears", seat="B", ref="shield-late")
        take = self.add(game.engine, "Word of Seizing", seat="B", zone="hand", ref="shield-take")
        borrow = self.add(game.engine, "Word of Seizing", seat="C", zone="hand", ref="shield-borrow")
        for card in (spell, take, borrow):
            self.assert_closed(game, card)
        self.prepare(game, mana={"W": 1})
        for seat in "BC":
            game.state.players[seat].mana_pool.update({"C": 3, "R": 2})
        self.checkpoint(game)
        self.cast_for(game, "A", spell, ["C"])
        self.cast_for(game, "B", take, [moved.ref])
        self.assertIsNone(self.resolve(game))
        self.assertEqual(CREATURE_SUBTYPES, self.subtypes(game, retained))
        self.assertEqual((2, 3), self.stats(game, retained))
        self.assertEqual({"giant"}, self.subtypes(game, moved))
        self.cast_for(game, "C", borrow, [late.ref])
        self.assertIsNone(self.resolve(game))
        self.assertEqual("C", game.state.cards[late.object_id].controller)
        self.assertEqual({"bear"}, self.subtypes(game, late))
        self.assertEqual((2, 2), self.stats(game, late))
        self.replay(game, load=True)

    def test_capability_rejects_incomplete_scope_or_invalid_version(self):
        spec = CreatureSubtypeSetResult("one_creature", "none")
        program = dict(event="activate", active_zone="battlefield", target_schema=spec.to_target_schema(),
                       effects=(spec.to_instruction(),), handlers=())
        self.assertTrue(creature_subtype_set_runtime_dependencies(SimpleNamespace(**program)))
        for changes in (
            {"event": "step.begin"},
            {"target_schema": {**spec.to_target_schema(), "count": True}},
            {"effects": ({**spec.to_instruction(), "schema_version": True},)},
            {"effects": ({**spec.to_instruction(), "subjects": "$source"},)},
        ):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                creature_subtype_set_runtime_dependencies(SimpleNamespace(**{**program, **changes}))

    def test_missing_type_layer_is_caught_by_the_printed_wings_spell(self):
        real = CreatureSubtypeSetResult.components

        def omit(spec):
            return tuple(component for component in real(spec) if component.sublayer != "4")

        with patch.object(CreatureSubtypeSetResult, "components", omit):
            with self.assertRaisesRegex(AssertionError, "Wings grants all creature types"):
                self.test_actual_wings_sets_base_preserves_modifiers_and_expires()
