"""The original final chapter snapshots one public mana-value total for every opponent."""

from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.carddb import CardDatabase
from quorune.continuous_effect_model import ContinuousObjectIdentity
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.public_characteristic_aggregate_model import OTHER_PERMANENT_MANA_VALUE_SUM_CAPABILITY
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


NAME = "Summon: Bahamut"
PROFILE = "commander_review"


class SummonBahamutManaDamageTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "summon-bahamut.sqlite3"
        fixtures = ROOT / "tests/fixtures"
        build_fixture_database([
            fixtures / "fixed-one-counter-movement-cards.json",
            fixtures / "fixed-mana-result-cards.json",
            fixtures / "intrinsic-permanent-death-return-cards.json",
            fixtures / "summon-bahamut-mana-damage-cards.json",
        ], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        commander = "Isamaru, Hound of Konda"
        cls.deck = DeckDefinition("Summon Bahamut", [
            DeckEntry(commander, 1, "commander"), DeckEntry("Plains", 30),
        ], [commander])

    def test_mana_total_requires_its_original_chapter_source_and_opponents(self):
        card = self.db.lookup(NAME, fuzzy=False)
        report, semantics = register_card(self.db, self.registry, NAME)
        self.assertTrue(report["strict_capability_ready"], report["blockers"])
        chapter, = (row for row in semantics.programs_for_oracle(card.oracle_id)
                    if OTHER_PERMANENT_MANA_VALUE_SUM_CAPABILITY in row.capability_dependencies)
        self.assertEqual("saga.chapter.4", chapter.event)
        self.assertIsNone(chapter.target_schema)
        for altered in (replace(chapter,event="saga.chapter.3"),
                        replace(chapter,effects=[{**chapter.effects[0],"source":"$stack"}])):
            self.assertFalse(bind_semantic_program_runtime(
                altered, capability_registry=self.registry, profile=PROFILE,
            )["strict"])
        for before, after in (("other permanents", "permanents"), ("each opponent", "target opponent")):
            with self.subTest(after=after):
                changed = replace(card, oracle_text=card.oracle_text.replace(before, after))
                self.assertNotEqual("exact", compile_oracle_card(
                    changed, capability_registry=self.registry, capability_profile=PROFILE,
                ).status)

    def resolve_last_chapter(self, *, remove_source):
        game = self.session(47840101 + int(remove_source))
        saga = self.add(game.engine, NAME)
        saga.counters["lore"] = 3
        ring = self.add(game.engine, "Sol Ring")
        self.add(game.engine, "Grizzly Bears", ref="other-controlled-creature")
        self.add(game.engine, "Grizzly Bears", seat="C", ref="foreign-creature")
        self.add(game.engine, "Forest", ref="zero-mana-controlled-land")
        for index in range(2):
            self.add(game.engine, "Forest", seat="B", ref=f"response-forest-{index}")
        removal = self.add(game.engine, "Naturalize", zone="hand", seat="B", ref="private-response")
        for card in (saga, ring, removal):
            self.assert_closed(game, card)
        for player in game.state.players.values():
            for object_id in tuple(player.zones["hand"]):
                if object_id != removal.object_id:
                    game.engine.move_card(object_id, "library", semantic_events=False, log=False)
        previous = game.state.turn_order[game.state.turn_order.index("A") - 1]
        game.state.last_normal_turn_player = previous
        game.state.config.semantic_policy = "trusted_only"
        self.prepare(game, actor=previous, active=previous)
        self.checkpoint(game)
        self.until(game, lambda current: any(item.context.get("event") == "saga.chapter.4"
                                            for item in current.state.stack))
        before = {seat:player.life for seat,player in game.state.players.items()}
        game = self.replay(game, load=True)
        self.assertNotIn(removal.ref, str(game.packet("pilot:A", full=True)))
        self.cast(game, "B", removal, [(saga if remove_source else ring).ref])
        self.resolve_top(game)
        game = self.replay(game, load=True)
        self.settle(game)
        losses = {seat:before[seat] - player.life for seat,player in game.state.players.items()}
        self.assertEqual(0, losses.pop("A"))
        expected = 3 if remove_source else 2
        self.assertEqual({"B":expected, "C":expected, "D":expected}, losses)
        self.assertEqual("graveyard", game.state.cards[saga.object_id].zone)
        self.replay(game, load=True)

    def test_actual_damage_uses_current_other_permanents_and_replays_with_or_without_source(self):
        for remove_source in (False, True):
            with self.subTest(remove_source=remove_source):
                self.resolve_last_chapter(remove_source=remove_source)

    def test_native_damage_witness_detects_including_the_original_source(self):
        from quorune.public_characteristic_aggregates import current_public_characteristic_aggregate

        def including_source(host, controller, spec, *, excluded_source=None):
            if spec.schema_version == 3:
                excluded_source = ContinuousObjectIdentity("different-source", "different-source@0")
            return current_public_characteristic_aggregate(host, controller, spec, excluded_source=excluded_source)

        with patch("quorune.public_characteristic_aggregates.current_public_characteristic_aggregate", new=including_source):
            with self.assertRaisesRegex(AssertionError, r"'B': 2.*'B': 11"):
                self.resolve_last_chapter(remove_source=False)
