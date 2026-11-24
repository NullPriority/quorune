from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import unittest

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.attachments import attach_objects
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules import aura_attachment_targets as aura_target
from quorune.targets import target_plan
from scripts.build_test_database import build_fixture_database


class AttachedAuraDestructionTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "attached-aura-destruction.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures/attached-aura-destruction-cards.json"], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        commander = "Isamaru, Hound of Konda"
        cls.deck = DeckDefinition("Attached Aura targets", [DeckEntry(commander, 1, "commander"),
                                                          DeckEntry("Plains", 30)], [commander])

    def attach(self, game, name, bearer, *, seat, ref):
        aura = self.add(game.engine, name, seat=seat, ref=ref)
        self.assert_closed(game, aura)
        attach_objects(game.state.cards, aura, bearer, source_timestamp=aura.zone_timestamp, players=game.state.players)
        return aura

    def activate(self, game, source, target):
        ability, = (ability for ability in game.engine._activated_abilities(source) if not ability.mana_ability)
        action = {"action_id": "activate:" + source.ref + ":" + ability.ability_id,
                  "targets": [target.ref], "pay": "auto"}
        return game.act("pilot:A", action)

    def test_actual_elf_and_harpist_distinguish_land_and_creature_attachments(self):
        for seed, name, land_required in ((115030401, "Savaen Elves", True), (115030402, "Devout Harpist", False)):
            with self.subTest(source=name):
                game = self.session(seed)
                source = self.add(game.engine, name)
                target_land = self.add(game.engine, "Forest", seat="B")
                target_creature = self.add(game.engine, "Grizzly Bears", seat="C")
                land_aura = self.attach(game, "Wild Growth", target_land, seat="C", ref="land-aura")
                creature_aura = self.attach(game, "Holy Strength", target_creature, seat="B", ref="creature-aura")
                self.assert_closed(game, source)
                self.prepare(game, mana={"G": 2})
                self.checkpoint(game)
                ready, wrong = (land_aura, creature_aura) if land_required else (creature_aura, land_aura)
                before = authoritative_state_hash(game.state)
                self.assertFalse(self.activate(game, source, wrong).ok, "attachment type is required")
                self.assertEqual(before, authoritative_state_hash(game.state))
                self.assertFalse(self.activate(game, source, target_creature).ok)
                result = self.activate(game, source, ready)
                self.assertTrue(result.ok, result.summary)
                game = self.replay(game, load=True)
                self.assertIsNone(self.resolve(game))
                self.assertEqual("graveyard", game.state.cards[ready.object_id].zone)
                self.assertEqual("battlefield", game.state.cards[wrong.object_id].zone)
                for bearer in (target_land, target_creature):
                    self.assertEqual("battlefield", game.state.cards[bearer.object_id].zone)
                self.replay(game, load=True)

    def test_actual_miracle_worker_uses_the_enchanted_creatures_controller(self):
        game = self.session(115030403)
        source = self.add(game.engine, "Miracle Worker")
        own = self.add(game.engine, "Grizzly Bears", ref="my-bear")
        foreign = self.add(game.engine, "Grizzly Bears", seat="B", ref="opposing-bear")
        own_target = self.attach(game, "Holy Strength", own, seat="B", ref="foreign-aura-on-my-bear")
        other_target = self.attach(game, "Holy Strength", foreign, seat="A", ref="my-aura-on-opposing-bear")
        self.assert_closed(game, source)
        self.prepare(game)
        self.checkpoint(game)
        before = authoritative_state_hash(game.state)
        self.assertFalse(self.activate(game, source, other_target).ok, "the attached creature must be controlled")
        self.assertEqual(before, authoritative_state_hash(game.state))
        result = self.activate(game, source, own_target)
        self.assertTrue(result.ok, result.summary)
        self.assertIsNone(self.resolve(game))
        self.assertEqual("graveyard", game.state.cards[own_target.object_id].zone)
        self.assertEqual("battlefield", game.state.cards[other_target.object_id].zone)
        self.replay(game, load=True)

    def test_actual_miracle_worker_revalidates_control_of_the_attached_creature(self):
        game = self.session(115030404)
        source = self.add(game.engine, "Miracle Worker")
        bear = self.add(game.engine, "Grizzly Bears")
        aura = self.attach(game, "Holy Strength", bear, seat="C", ref="live-aura")
        response = self.add(game.engine, "Word of Seizing", seat="B", zone="hand")
        for card in (source, response):
            self.assert_closed(game, card)
        self.prepare(game)
        game.state.players["B"].mana_pool.update({"C": 3, "R": 2})
        self.checkpoint(game)
        result = self.activate(game, source, aura)
        self.assertTrue(result.ok, result.summary)
        self.cast_for(game, "B", response, [bear.ref])
        self.resolve_top(game)
        self.assertEqual("B", game.state.cards[bear.object_id].controller)
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        current = game.state.cards[aura.object_id]
        self.assertEqual(("battlefield", bear.object_id), (current.zone, current.attached_to))
        self.replay(game, load=True)

    def test_attachment_capability_requires_the_aura_target_and_direct_destruction(self):
        spec = aura_target.AuraAttachmentTargetSpec("creature", "you")
        schema = spec.to_target_schema()
        effects = ({"op": "destroy", "card": "$target.0"},)
        self.assertIn(aura_target.AURA_ATTACHMENT_TARGET_CAPABILITY,
                      aura_target.aura_attachment_destruction_dependencies(effects, schema))
        for invalid in (
            {**schema, "subtypes_any": ["equipment"]},
            {**schema, "count": 2},
            {**schema, aura_target.AURA_ATTACHMENT_TARGET_FIELD: {**spec.to_dict(), "attached_type": "player"}},
        ):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                target_plan(invalid)
        with self.assertRaises(ValueError):
            aura_target.aura_attachment_destruction_dependencies(({"op": "destroy", "card": "$source"},), schema)

    def test_actual_aura_destruction_kills_removal_of_the_attachment_filter(self):
        with patch.object(aura_target, "aura_attachment_target_matches", return_value=True):
            with self.assertRaisesRegex(AssertionError, "the attached creature must be controlled"):
                self.test_actual_miracle_worker_uses_the_enchanted_creatures_controller()
