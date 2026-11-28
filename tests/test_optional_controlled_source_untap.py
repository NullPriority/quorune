"""An optional original entry target uses the existing controller-bound untap duration."""

from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
import test_next_player_untap_restrictions as native_turns
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.rules.capabilities import load_default_capability_registry
from quorune.source_tap_effect import SOURCE_CONTROL_UNTAP_FIELD
from quorune.source_untap_rule_model import ResolvedSourceUntapRestraint
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


class OptionalControlledSourceUntapTests(InheritedNativeFixture, unittest.TestCase):
    playing = native_turns.NextPlayerUntapRestrictionTests.playing

    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "optional-controlled-source-untap.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures/optional-controlled-source-untap-cards.json"], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Optional controlled-source untap", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"), DeckEntry("Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def test_complete_ty_lee_preserves_flash_prowess_and_the_exact_optional_creature_target(self):
        report, semantics = register_card(self.db, self.registry, "Ty Lee, Chi Blocker")
        self.assertTrue(report["strict_capability_ready"], report["blockers"])
        record = self.db.lookup("Ty Lee, Chi Blocker", fuzzy=False)
        programs = semantics.programs_for_oracle(record.oracle_id)
        self.assertGreaterEqual(len(programs), 3)
        self.assertTrue(any(p.event == "cast.permission" for p in programs))
        entry, = (p for p in programs if p.event == "permanent.enter.self")
        self.assertEqual({"zones": ["battlefield"], "categories": ["permanent"],
                          "types_any": ["creature"], "up_to": 1}, entry.target_schema)
        self.assertEqual(2, entry.effects[0][SOURCE_CONTROL_UNTAP_FIELD])
        changed = replace(entry, effects=[{**entry.effects[0], SOURCE_CONTROL_UNTAP_FIELD: 1}])
        self.assertFalse(bind_semantic_program_runtime(
            changed, capability_registry=self.registry, profile="commander_review",
        )["strict"])
        with patch("quorune.compiler.source_tap_duration._OPTIONAL_CONTROLLED_SOURCE_TARGET") as missing:
            missing.fullmatch.return_value = None
            self.assertNotEqual("exact", compile_oracle_card(
                record, capability_registry=self.registry, capability_profile="commander_review",
            ).status)

    def current(self, game, card):
        return game.state.cards[card.object_id]

    def restraints(self, game):
        return [effect for effect in game.state.continuous_effects if isinstance(effect, ResolvedSourceUntapRestraint)]

    def test_actual_ty_lee_flashes_on_an_opponents_turn_and_can_decline_all_targets(self):
        game = self.playing(61126801)
        source = self.add(game.engine, "Ty Lee, Chi Blocker", zone="hand")
        own = self.add(game.engine, "Grizzly Bears")
        other = self.add(game.engine, "Wind Drake", seat="B")
        for card in (source, own, other):
            self.assert_closed(game, card)
        self.prepare(game, actor="B", active="B")
        game.state.players["A"].mana_pool.update({"C": 2, "U": 1})
        self.checkpoint(game)
        self.cast_for(game, "A", source)
        self.target(game, "A", [])
        self.assertIsNone(self.resolve(game))
        self.assertEqual("B", game.state.active_player)
        self.assertEqual("battlefield", self.current(game, source).zone)
        for candidate in (own, other):
            self.assertFalse(self.current(game, candidate).tapped)
        self.assertEqual([], self.restraints(game))
        self.replay(game, load=True)

    def test_actual_ty_lee_holds_an_owned_original_but_a_blink_revalidates_and_triggers_prowess(self):
        game = self.playing(61126802)
        source = self.add(game.engine, "Ty Lee, Chi Blocker", zone="hand")
        own = self.add(game.engine, "Grizzly Bears")
        blink = self.add(game.engine, "Cloudshift", zone="hand")
        for card in (source, own, blink):
            self.assert_closed(game, card)
        self.prepare(game, actor="B", active="B")
        game.state.players["A"].mana_pool.update({"C": 2, "U": 1, "W": 1})
        self.checkpoint(game)
        self.cast_for(game, "A", source, reserve={"W": 1})
        original = own.logical_object_id
        self.target(game, "A", [own.ref])

        retained = self.replay(game, load=True)
        self.assertIsNone(self.resolve(retained))
        rule, = self.restraints(retained)
        self.assertEqual("A", rule.duration_controller)
        self.assertEqual(own.logical_object_id, rule.locked_objects[0].logical_object_id)
        for seat in ("C", "D", "A"):
            self.until(retained, lambda value: value.state.active_player == seat and value.state.step == "upkeep")
        self.assertTrue(self.current(retained, own).tapped)
        self.replay(retained, load=True)

        self.cast_for(game, "A", blink, [own.ref])
        self.assertIsNone(self.resolve(game))
        self.assertNotEqual(original, self.current(game, own).logical_object_id)
        self.assertFalse(self.current(game, own).tapped)
        self.assertEqual([], self.restraints(game))
        self.assertEqual(3, game.engine._numeric_stat(source.object_id, "power"))
        self.replay(game, load=True)
