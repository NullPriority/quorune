from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch
import unittest

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.event_spell_copy_model import EVENT_SPELL_COPY_CONTEXT
from quorune.record import authoritative_state_hash
from quorune.rules import cast_source_spell_copy
from quorune.rules.capabilities import load_default_capability_registry
from quorune.selection import copy_targets
from scripts.build_test_database import build_fixture_database


class SourceCastCopyTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "source-cast-copy.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures/source-cast-copy-cards.json"], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        commander = "Isamaru, Hound of Konda"
        cls.deck = DeckDefinition(
            "Fixed spell receiver", [DeckEntry(commander, 1, "commander"), DeckEntry("Plains", 30)], [commander],
        )

    def board(self, seed, spell_name):
        game = self.session(seed)
        source = self.add(game.engine, "Ivy, Gleeful Spellthief", seat="A")
        bearer = self.add(game.engine, "Grizzly Bears", seat="C")
        spell = self.add(game.engine, spell_name, seat="B", zone="hand")
        for card in (source, bearer, spell):
            self.assert_closed(game, card)
        return game, source, bearer, spell

    def start(self, game, spell, targets, mana):
        self.prepare(game, actor="B", active="B", mana=mana)
        self.checkpoint(game)
        self.cast_for(game, "B", spell, targets)

    def copy_choice(self, game):
        pending = self.resolve(game)
        self.assertEqual("semantic.choice", pending.kind)
        self.assertEqual(["A"], pending.actors)

    def apply_copy(self, game, source, count):
        self.assertTrue(game.act("pilot:A", {"action_id": "choose", "choice": "apply"}).ok)
        copies = [item for item in game.state.stack if item.kind == "spell_copy"]
        self.assertEqual(1, len(copies))
        copy, = copies
        self.assertEqual(("A", [source.ref] * count), (copy.controller, list(copy.targets)))
        return copy

    def test_actual_ivy_copies_each_announced_slot_to_its_original_incarnation(self):
        game, source, bearer, spell = self.board(707100501, "Martial Glory")
        self.start(game, spell, [bearer.ref, bearer.ref], {"R": 1, "W": 1})
        self.assertEqual(1, self.trigger_count(game, source))
        self.copy_choice(game)
        game = self.replay(game, load=True)
        initial = authoritative_state_hash(game.state)
        for principal, action in (
            ("pilot:B", {"action_id": "choose", "choice": "apply"}),
            ("pilot:A", {"action_id": "choose", "choice": "apply", "targets": [bearer.ref, bearer.ref]}),
        ):
            with self.subTest(principal=principal):
                self.assertFalse(game.act(principal, action).ok)
                self.assertEqual(initial, authoritative_state_hash(game.state))
        copy = self.apply_copy(game, source, 2)
        original, = (item for item in game.state.stack if item.kind == "spell")
        self.assertEqual(("B", [bearer.ref, bearer.ref]), (original.controller, list(original.targets)))
        self.assertEqual(
            {key: [source.ref for _ in values] for key, values in original.context["target_groups"].items()},
            copy.context["target_groups"],
        )
        self.assertEqual(0, self.trigger_count(game, source))
        self.resolve_top(game)
        self.assertEqual((5, 4), tuple(game.engine._numeric_stat(source.object_id, key) for key in ("power", "toughness")))
        self.resolve_top(game)
        self.assertEqual((5, 5), tuple(game.engine._numeric_stat(bearer.object_id, key) for key in ("power", "toughness")))
        self.replay(game, load=True)

    def test_actual_ivy_can_copy_the_retained_spell_after_the_original_is_countered(self):
        game, source, bearer, spell = self.board(707100502, "Giant Growth")
        counterspell = self.add(game.engine, "Counterspell", seat="D", zone="hand")
        self.assert_closed(game, counterspell)
        game.state.players["D"].mana_pool.update({"U": 2})
        self.start(game, spell, [bearer.ref], {"G": 1})
        original, = (item for item in game.state.stack if item.kind == "spell")
        self.cast_for(game, "D", counterspell, [original.ref])
        self.resolve_top(game)
        trigger, = (item for item in game.state.stack if item.kind == "triggered_ability")
        self.assertTrue(trigger.context[EVENT_SPELL_COPY_CONTEXT]["departed"])
        self.assertNotIn(original.ref, [item.ref for item in game.state.stack])
        self.copy_choice(game)
        self.apply_copy(game, source, 1)
        self.resolve_top(game)
        self.assertEqual(5, game.engine._numeric_stat(source.object_id, "power"))
        self.assertEqual(2, game.engine._numeric_stat(bearer.object_id, "power"))
        self.replay(game, load=True)

    def test_actual_ivy_does_not_retarget_its_new_incarnation_or_offer_an_illegal_copy(self):
        for seed, intervention in ((707100503, "Flicker of Fate"), (707100504, "Cast Down")):
            with self.subTest(intervention=intervention):
                game, source, bearer, spell = self.board(
                    seed, "Giant Growth" if intervention == "Flicker of Fate" else "Cast Down",
                )
                original_incarnation = source.logical_object_id
                if intervention == "Flicker of Fate":
                    flicker = self.add(game.engine, intervention, seat="D", zone="hand")
                    self.assert_closed(game, flicker)
                    game.state.players["D"].mana_pool.update({"W": 1, "C": 1})
                    self.start(game, spell, [bearer.ref], {"G": 1})
                    self.cast_for(game, "D", flicker, [source.ref])
                    self.resolve_top(game)
                    self.assertNotEqual(original_incarnation, game.state.cards[source.object_id].logical_object_id)
                else:
                    self.start(game, spell, [bearer.ref], {"B": 1, "C": 1})
                self.assertEqual(1, self.trigger_count(game, source))
                self.assertIsNone(self.resolve(game))
                self.assertEqual("battlefield", game.state.cards[source.object_id].zone)
                self.assertEqual(2, game.engine._numeric_stat(source.object_id, "power"))
                self.assertFalse(any(item.kind == "spell_copy" for item in game.state.stack))
                self.replay(game, load=True)

    def test_actual_ivy_copies_an_aura_spell_into_a_token_on_the_fixed_receiver(self):
        game, source, bearer, spell = self.board(707100505, "Holy Strength")
        self.start(game, spell, [bearer.ref], {"W": 1})
        self.copy_choice(game)
        copy = self.apply_copy(game, source, 1)
        self.resolve_top(game)
        token = game.state.cards[copy.card_object_id]
        self.assertTrue(token.is_token)
        self.assertEqual(("A", "battlefield", source.object_id), (token.controller, token.zone, token.attached_to))
        self.resolve_top(game)
        self.assertEqual(bearer.object_id, game.state.cards[spell.object_id].attached_to)
        self.replay(game, load=True)

    def test_actual_ivy_ignores_itself_and_multiple_distinct_targets_and_can_decline(self):
        for seed, scenario in ((707100506, "self"), (707100507, "distinct"), (707100508, "decline")):
            with self.subTest(scenario=scenario):
                game, source, bearer, spell = self.board(
                    seed, "Martial Glory" if scenario == "distinct" else "Giant Growth",
                )
                if scenario == "distinct":
                    rival = self.add(game.engine, "Hill Giant", seat="D")
                    self.start(game, spell, [bearer.ref, rival.ref], {"W": 1, "R": 1})
                else:
                    self.start(game, spell, [source.ref if scenario == "self" else bearer.ref], {"G": 1})
                if scenario == "decline":
                    self.copy_choice(game)
                    self.assertTrue(game.act("pilot:A", {"action_id": "choose", "choice": "decline"}).ok)
                else:
                    self.assertEqual(0, self.trigger_count(game, source))
                self.assertIsNone(self.resolve(game))
                self.assertEqual(5 if scenario == "self" else 2, game.engine._numeric_stat(source.object_id, "power"))
                self.replay(game, load=True)

    def test_fixed_copy_requires_its_exact_cast_trigger_and_reference(self):
        effect = cast_source_spell_copy.source_cast_copy_effect()
        base = {
            "effects": (effect,), "event": "spell.cast", "active_zone": "battlefield",
            "target_schema": None, "cost_schema": None, "coverage": (),
            "event_condition": cast_source_spell_copy.source_cast_copy_condition(),
        }
        self.assertIn(cast_source_spell_copy.SOURCE_CAST_COPY_CAPABILITY,
                      cast_source_spell_copy.source_cast_copy_program_dependencies(SimpleNamespace(**base)))
        for changes in (
            {"effects": ({**effect, "stack": "$target.0"},)},
            {"effects": ({**effect, cast_source_spell_copy.SOURCE_CAST_COPY_FIELD: True},)},
            {"event_condition": {"field": "controller", "op": "eq", "value": "$controller"}},
            {"target_schema": {}},
            {"event": "spell.copy"},
        ):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                cast_source_spell_copy.source_cast_copy_program_dependencies(SimpleNamespace(**{**base, **changes}))

    def test_retaining_instead_of_replacing_slots_breaks_the_actual_fixed_copy(self):
        original = copy_targets.prepare_copy_targets

        def keep_old_targets(*args, **kwargs):
            return original(*args, **{**kwargs, "submitted": None})

        with patch.object(copy_targets, "prepare_copy_targets", side_effect=keep_old_targets):
            with self.assertRaisesRegex(AssertionError, "ivy.*grizzly|grizzly.*ivy"):
                self.test_actual_ivy_copies_each_announced_slot_to_its_original_incarnation()
