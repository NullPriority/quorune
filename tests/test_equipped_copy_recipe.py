"""Equipped copy uses the right incarnation and distinct haste retention."""

from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import unittest

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
import test_next_player_untap_restrictions as native_turns
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


class EquippedCopyRecipeTests(InheritedNativeFixture, unittest.TestCase):
    playing = native_turns.NextPlayerUntapRestrictionTests.playing

    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "equipped-copy.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures/equipped-copy-recipe-cards.json"], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Equipped copy", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"), DeckEntry("Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def attached_helm(self, seed):
        game = self.playing(seed)
        helm = self.add(game.engine, "Helm of the Host", zone="hand")
        subject = self.add(game.engine, "Yargle, Glutton of Urborg")
        release = self.add(game.engine, "Disenchant", seat="B", zone="hand")
        bounce = self.add(game.engine, "Unsummon", seat="C", zone="hand")
        copy_spell = self.add(game.engine, "Cackling Counterpart", zone="hand")
        for card in (helm, subject, release, bounce, copy_spell):
            self.assert_closed(game, card)
        for index in range(3):
            self.add(game.engine, "Island", ref=f"copy-mana-{index}")
        for index in range(2):
            self.add(game.engine, "Plains", seat="B", ref=f"destroy-mana-{index}")
        self.add(game.engine, "Island", seat="C", ref="bounce-mana")
        self.prepare(game, actor="A", active="A", mana={"C": 9})
        self.checkpoint(game)
        self.cast_for(game, "A", helm)
        self.assertIsNone(self.resolve(game))
        self.priority(game, "A")
        actions = game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        equip, = (action for action in actions if action["id"].startswith("activate:" + helm.ref + ":"))
        paid = game.act("pilot:A", {"action_id": equip["id"], "targets": [subject.ref], "pay": "auto"})
        self.assertTrue(paid.ok, paid.summary)
        self.assertIsNone(self.resolve(game))
        self.assertEqual(subject.object_id, game.state.cards[helm.object_id].attached_to)
        self.until(game, lambda item: item.state.phase == "combat" and item.state.step == "beginning_combat"
                   and bool(item.state.stack))
        self.assertEqual("triggered_ability", game.state.stack[-1].kind)
        self.assertEqual(helm.object_id, game.state.stack[-1].source_object_id)
        return game, helm, subject, release, bounce, copy_spell

    def controlled_tokens(self, game):
        return [card for card in game.state.cards.values()
                if card.zone == "battlefield" and card.is_token and card.controller == "A"]

    def keywords(self, game, card):
        return {keyword.casefold() for keyword in game.engine._effective_card_data(card)["keywords"]}

    def legendary(self, game, card):
        data = game.engine._effective_card_data(card)
        return "legendary" in game.engine._type_parts(data["type_line"])[2]

    def test_actual_helm_copies_the_departed_creature_without_copiable_haste(self):
        report, semantics = register_card(self.db, self.registry, "Helm of the Host")
        self.assertTrue(report["strict_capability_ready"], report["blockers"])
        record = self.db.lookup("Helm of the Host", fuzzy=False)
        trigger, = (program for program in semantics.programs_for_oracle(record.oracle_id) if program.event == "step.begin")
        create, = trigger.effects
        recipe = create["copy_spec"]
        bogus = replace(trigger, effects=[{**create, "copy_spec": {**recipe, "attachment_kind": "enchanted"}}])
        self.assertFalse(bind_semantic_program_runtime(
            bogus, capability_registry=self.registry, profile="commander_review",
        )["strict"])
        with patch("quorune.compiler.token_copy_templates.fixed_number", return_value=2):
            self.assertNotEqual("exact", compile_oracle_card(
                record, capability_registry=self.registry, capability_profile="commander_review",
            ).status)

        game, helm, subject, release, bounce, copy_spell = self.attached_helm(61127263)
        self.cast_for(game, "B", release, [game.state.cards[helm.object_id].ref])
        self.resolve_top(game)
        self.assertEqual("graveyard", game.state.cards[helm.object_id].zone)
        self.cast_for(game, "C", bounce, [game.state.cards[subject.object_id].ref])
        self.resolve_top(game)
        self.assertEqual("hand", game.state.cards[subject.object_id].zone)
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        first, = self.controlled_tokens(game)
        self.assertEqual((9, 3), (game.engine._numeric_stat(first.object_id, "power"),
                                  game.engine._numeric_stat(first.object_id, "toughness")))
        self.assertFalse(self.legendary(game, first))
        self.assertIn("haste", self.keywords(game, first))

        self.cast_for(game, "A", copy_spell, [first.ref])
        self.assertIsNone(self.resolve(game))
        second, = (card for card in self.controlled_tokens(game) if card.object_id != first.object_id)
        self.assertFalse(self.legendary(game, second))
        self.assertNotIn("haste", self.keywords(game, second))
        self.until(game, lambda item: item.state.active_player == "B" and item.state.step == "upkeep")
        self.assertIn("haste", self.keywords(game, game.state.cards[first.object_id]))
        self.assertNotIn("haste", self.keywords(game, game.state.cards[second.object_id]))
        self.replay(game, load=True)

    def test_actual_live_helm_without_an_equipped_creature_creates_nothing(self):
        game, helm, subject, _, bounce, _ = self.attached_helm(61127264)
        self.cast_for(game, "C", bounce, [game.state.cards[subject.object_id].ref])
        self.resolve_top(game)
        self.assertEqual("battlefield", game.state.cards[helm.object_id].zone)
        self.assertIsNone(game.state.cards[helm.object_id].attached_to)
        self.assertIsNone(self.resolve(game))
        self.assertEqual([], self.controlled_tokens(game))
        self.replay(game, load=True)
