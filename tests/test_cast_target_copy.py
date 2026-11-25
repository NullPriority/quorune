from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch
import unittest

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules import cast_target_copy
from quorune.token_copy_references import COPY_REFERENCE_CONTEXT
from scripts.build_test_database import build_fixture_database


class CastTargetCopyTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "cast-target-copy.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures/cast-target-copy-cards.json"], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        commander = "Isamaru, Hound of Konda"
        cls.deck = DeckDefinition("Cast target copy", [
            DeckEntry(commander, 1, "commander"), DeckEntry("Plains", 30),
        ], [commander])

    def model_copy_trigger(self, game, source):
        matching = [item for item in game.state.stack
                    if item.kind == "triggered_ability" and item.source_object_id == source.object_id]
        self.assertEqual(1, len(matching), "One distinct qualifying cast target must trigger exactly once")
        trigger, = matching
        self.assertFalse(trigger.targets)
        return trigger

    def battlefield_tokens(self, game):
        return [card for card in game.state.cards.values() if card.is_token and card.zone == "battlefield"]

    def effective_name(self, game, card):
        return game.engine._effective_card_data(game.state.cards[card.object_id])["name"]

    def test_actual_duplimancy_copies_a_controlled_artifact_before_the_original_spell_resolves(self):
        game = self.session(115090621)
        source = self.add(game.engine, "Vesuvan Duplimancy")
        model = self.add(game.engine, "Sol Ring")
        shatter = self.add(game.engine, "Shatter", zone="hand")
        for card in (source, model, shatter):
            self.assert_closed(game, card)
        self.prepare(game, mana={"R": 1, "C": 1})
        self.checkpoint(game)
        self.cast_for(game, "A", shatter, [model.ref])
        trigger = self.model_copy_trigger(game, source)
        known = trigger.context[COPY_REFERENCE_CONTEXT]["event_object"]
        self.assertEqual((model.ref, model.logical_object_id), (known["ref"], known["logical_object_id"]))
        game = self.replay(game, load=True)
        self.resolve_top(game)
        copy, = self.battlefield_tokens(game)
        self.assertEqual(("A", "A", "Sol Ring"), (copy.owner, copy.controller, self.effective_name(game, copy)))
        self.assertEqual("battlefield", game.state.cards[model.object_id].zone)
        self.assertIsNone(self.resolve(game))
        self.assertEqual("graveyard", game.state.cards[model.object_id].zone)
        self.assertEqual("battlefield", game.state.cards[copy.object_id].zone)
        self.replay(game, load=True)

    def test_actual_duplimancy_counts_the_same_target_once_and_removes_legendary(self):
        game = self.session(115090622)
        source = self.add(game.engine, "Vesuvan Duplimancy")
        model = self.add(game.engine, "Isamaru, Hound of Konda", ref="legendary-model")
        model.counters["+1/+1"] = 1
        glory = self.add(game.engine, "Martial Glory", zone="hand")
        for card in (source, model, glory):
            self.assert_closed(game, card)
        self.prepare(game, mana={"R": 1, "W": 1})
        self.checkpoint(game)
        self.cast_for(game, "A", glory, [model.ref, model.ref])
        trigger = self.model_copy_trigger(game, source)
        self.assertEqual([model.ref, model.ref], list(trigger.context["targets"]))
        game = self.replay(game, load=True)
        self.resolve_top(game)
        copy, = self.battlefield_tokens(game)
        values = game.engine._effective_card_data(copy)
        self.assertEqual("Isamaru, Hound of Konda", values["name"])
        self.assertNotIn("legendary", game.engine._type_parts(values["type_line"])[2])
        self.assertEqual((2, 2), (game.engine._numeric_stat(copy.object_id, "power"),
                                  game.engine._numeric_stat(copy.object_id, "toughness")))
        self.assertEqual(0, copy.counters.get("+1/+1", 0))
        self.assertIsNone(self.resolve(game))
        self.replay(game, load=True)

    def test_actual_duplimancy_ignores_two_distinct_targets_and_an_opponents_permanent(self):
        for index, mode in enumerate(("two_targets", "wrong_controller", "wrong_type")):
            with self.subTest(mode=mode):
                game = self.session(115090623 + index)
                source = self.add(game.engine, "Vesuvan Duplimancy")
                own = self.add(game.engine, "Grizzly Bears", ref="first-creature")
                if mode == "two_targets":
                    second = self.add(game.engine, "Grizzly Bears", ref="second-creature")
                    spell = self.add(game.engine, "Martial Glory", zone="hand")
                    mana, targets = {"R": 1, "W": 1}, [own.ref, second.ref]
                elif mode == "wrong_controller":
                    rival = self.add(game.engine, "Hill Giant", seat="C")
                    spell = self.add(game.engine, "Giant Growth", zone="hand")
                    mana, targets = {"G": 1}, [rival.ref]
                else:
                    spell = self.add(game.engine, "Disenchant", zone="hand")
                    mana, targets = {"W": 1, "C": 1}, [source.ref]
                for card in (source, spell):
                    self.assert_closed(game, card)
                self.prepare(game, mana=mana)
                self.checkpoint(game)
                self.cast_for(game, "A", spell, targets)
                self.assertEqual(0, self.trigger_count(game, source))
                self.assertIsNone(self.resolve(game))
                self.assertEqual([], self.battlefield_tokens(game))
                self.replay(game, load=True)

    def test_actual_duplimancy_keeps_the_original_reference_after_redirect_changes_the_spell(self):
        game = self.session(115090626)
        source = self.add(game.engine, "Vesuvan Duplimancy")
        model = self.add(game.engine, "Isamaru, Hound of Konda", ref="original-legend")
        other = self.add(game.engine, "Hill Giant", seat="B")
        growth = self.add(game.engine, "Giant Growth", zone="hand")
        redirect = self.add(game.engine, "Redirect", seat="C", zone="hand")
        for card in (source, model, growth, redirect):
            self.assert_closed(game, card)
        game.state.players["C"].mana_pool.update({"U": 2})
        self.prepare(game, mana={"G": 1})
        self.checkpoint(game)
        self.cast_for(game, "A", growth, [model.ref])
        original, = [item for item in game.state.stack if item.card_object_id == growth.object_id]
        self.cast_for(game, "C", redirect, [original.ref])
        decision = self.resolve(game)
        self.assertEqual(["C"], decision.actors)
        game = self.replay(game, load=True)
        command = {"action_id": "choose", "targets": [other.ref]}
        before = authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:B", command).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        result = game.act("pilot:C", command)
        self.assertTrue(result.ok, result.summary)
        item, = [item for item in game.state.stack if item.card_object_id == growth.object_id]
        self.assertEqual([other.ref], item.targets)
        trigger = self.model_copy_trigger(game, source)
        self.assertEqual(model.ref, trigger.context[COPY_REFERENCE_CONTEXT]["event_object"]["ref"])
        self.resolve_top(game)
        copy, = self.battlefield_tokens(game)
        self.assertEqual("Isamaru, Hound of Konda", self.effective_name(game, copy))
        self.assertIsNone(self.resolve(game))
        self.replay(game, load=True)

    def test_actual_duplimancy_reads_last_copiable_values_of_the_original_incarnation(self):
        for index, response in enumerate(("Flicker of Fate", "Reality Ripple")):
            with self.subTest(response=response):
                game = self.session(115090627 + index)
                source = self.add(game.engine, "Vesuvan Duplimancy")
                model = self.add(game.engine, "Isamaru, Hound of Konda", ref="departing-legend")
                other = self.add(game.engine, "Hill Giant", seat="D")
                growth = self.add(game.engine, "Giant Growth", zone="hand")
                change = self.add(game.engine, "Cytoshape", seat="B", zone="hand")
                remove = self.add(game.engine, response, seat="C", zone="hand")
                for card in (source, model, growth, change, remove):
                    self.assert_closed(game, card)
                game.state.players["B"].mana_pool.update({"G": 1, "U": 1, "C": 1})
                game.state.players["C"].mana_pool.update({"W": 1, "U": 1, "C": 2})
                self.prepare(game, mana={"G": 1})
                self.checkpoint(game)
                logical_id = model.logical_object_id
                self.cast_for(game, "A", growth, [model.ref])
                first = self.model_copy_trigger(game, source)
                first_snapshot = first.context[COPY_REFERENCE_CONTEXT]["event_object"]["snapshot"]
                self.assertEqual("Isamaru, Hound of Konda", first_snapshot["characteristics"]["name"])
                self.cast_for(game, "B", change, [model.ref])
                decision = self.resolve(game)
                self.assertEqual(["B"], decision.actors)
                result = game.act("pilot:B", {"action_id": "choose", "card": other.ref})
                self.assertTrue(result.ok, result.summary)
                self.assertEqual("Hill Giant", self.effective_name(game, model))
                self.cast_for(game, "C", remove, [model.ref])
                self.resolve_top(game)
                current = game.state.cards[model.object_id]
                if response == "Flicker of Fate":
                    self.assertNotEqual(logical_id, current.logical_object_id)
                    self.assertEqual("Isamaru, Hound of Konda", self.effective_name(game, current))
                else:
                    self.assertEqual(logical_id, current.logical_object_id)
                    self.assertTrue(current.phased_out)
                saved = self.model_copy_trigger(game, source).context[COPY_REFERENCE_CONTEXT]["event_object"]
                self.assertEqual(logical_id, saved["logical_object_id"])
                self.assertEqual("Hill Giant", saved["snapshot"]["characteristics"]["name"])
                game = self.replay(game, load=True)
                self.resolve_top(game)
                copy, = self.battlefield_tokens(game)
                self.assertEqual("Hill Giant", self.effective_name(game, copy))
                self.assertEqual("A", copy.controller)
                self.assertIsNone(self.resolve(game))
                self.replay(game, load=True)

    def test_cast_target_copy_requires_its_exact_spell_trigger_and_untargeted_result(self):
        effect = cast_target_copy.cast_target_copy_effect()
        condition = cast_target_copy.cast_target_copy_condition()
        scope = dict(effects=(effect,), event="spell.cast", active_zone="battlefield",
                     target_schema=None, event_condition=condition, coverage=())
        self.assertIn(cast_target_copy.CAST_TARGET_COPY_CAPABILITY,
                      cast_target_copy.cast_target_copy_runtime_dependencies(SimpleNamespace(**scope)))
        for change in ({"event": "spell.copy"}, {"target_schema": {}}, {"event_condition": {}},
                       {"effects": ({**effect, "quantity": True},)}, {"effects": (effect, effect)}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                cast_target_copy.cast_target_copy_runtime_dependencies(SimpleNamespace(**{**scope, **change}))

        item = SimpleNamespace(context={"event": "spell.cast", "targets": ["original"]}, targets=[])
        original = {"ref": "original", "zone": "battlefield", "object_id": "model", "logical_object_id": "model@3"}
        cast_target_copy.validate_cast_target_copy_reference(item, original)
        for change in ({"ref": "replacement"}, {"logical_object_id": "different@3"}, {"logical_object_id": "model@"}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                cast_target_copy.validate_cast_target_copy_reference(item, {**original, **change})
        item.context["targets"] = []
        with self.assertRaises(ValueError):
            cast_target_copy.validate_cast_target_copy_reference(item, {**original, "ref": None})

    def test_counting_target_slots_instead_of_distinct_targets_breaks_the_actual_duplicate_cast(self):
        def count_slots(targets):
            return targets[0] if isinstance(targets, (list, tuple)) and len(targets) == 1 else None

        with patch.object(cast_target_copy, "_sole_announced_target", side_effect=count_slots):
            with self.assertRaisesRegex(AssertionError, "One distinct qualifying cast target"):
                self.test_actual_duplimancy_counts_the_same_target_once_and_removes_legendary()
