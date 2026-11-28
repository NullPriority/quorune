from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.upkeep_power_counter_balance import CAPABILITY, upkeep_power_counter_effect
from scripts.build_test_database import build_fixture_database


CARD = "Lily Bowen, Raging Grandma"


class UpkeepPowerCounterBalanceTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        db = Path(cls.temporary.name) / "upkeep-power-counter-balance.sqlite3"
        fixtures = (
            "bound-effect-program-cards", "upkeep-power-counter-balance-cards",
            "counter-doubling-cards", "activation-restriction-material-cards",
            "battlefield-target-reduction-cards", "life-gain-counted-result-cards",
        )
        build_fixture_database([ROOT / f"tests/fixtures/{name}.json" for name in fixtures], db)
        cls.db = CardDatabase(db)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Upkeep counter balance", [
            DeckEntry("Generic Bound Commander", 1, "commander"), DeckEntry("Generic Bound Plains", 30),
        ], ["Generic Bound Commander"])

    def scene(self, seed, count):
        game = self.session(seed)
        source = self.add(game.engine, CARD)
        source.counters.update({"+1/+1": count, "charge": 3})
        hidden = self.add(game.engine, "Forest", seat="C", zone="hand", ref="power-balance-private")
        self.assert_closed(game, source)
        return game, source, hidden

    def ready_for_upkeep(self, game, current="D", following="A"):
        game.state.last_normal_turn_player = current
        self.prepare(game, actor=current, active=current)
        self.checkpoint(game)
        self.until(game, lambda session: session.state.step == "upkeep" and session.state.active_player == following)

    def test_actual_entry_and_exact_boundary_double_current_counters_with_ordinary_placement(self):
        game = self.session(50316023)
        original = self.add(game.engine, CARD, zone="hand")
        self.assert_closed(game, original)
        self.prepare(game, mana={"C": 3, "G": 1})
        self.checkpoint(game)
        self.cast(game, "A", original)
        self.resolve_top(game)
        self.assertEqual(2, game.state.cards[original.object_id].counters["+1/+1"])
        self.replay(game, load=True)

        game, source, hidden = self.scene(50316024, 16)
        enhancement = self.add(game.engine, "Hardened Scales")
        self.assert_closed(game, enhancement)
        self.ready_for_upkeep(game)
        self.assertEqual(1, self.trigger_count(game, source))
        game = self.replay(game, load=True)
        self.resolve_top(game)
        self.assertEqual({"+1/+1": 33, "charge": 3}, dict(game.state.cards[source.object_id].counters))
        self.assertEqual(40, game.state.players["A"].life)
        for seat in ("A", "B", "D"):
            self.assertNotIn(hidden.ref, json.dumps(game.packet(f"pilot:{seat}", full=True)))
        self.replay(game, load=True)

    def test_actual_response_crosses_threshold_and_life_uses_the_removed_counter_result(self):
        game, source, _ = self.scene(50316025, 14)
        growth = self.add(game.engine, "Giant Growth", zone="hand")
        self.add(game.engine, "Forest")
        boon = self.add(game.engine, "Boon Reflection")
        self.assert_closed(game, boon)
        self.ready_for_upkeep(game)
        self.assertEqual(1, self.trigger_count(game, source))
        self.assertEqual(14, game.engine._numeric_stat(source.object_id, "power"))
        self.cast(game, "A", growth, [source.ref])
        self.resolve_top(game)
        self.assertEqual(17, game.engine._numeric_stat(source.object_id, "power"))
        game = self.replay(game, load=True)
        self.resolve_top(game)
        self.assertEqual({"+1/+1": 1, "charge": 3}, dict(game.state.cards[source.object_id].counters))
        self.assertEqual([66, 40, 40, 40], [game.state.players[seat].life for seat in "ABCD"])
        self.replay(game, load=True)

    def test_actual_blink_and_another_players_upkeep_cannot_use_the_original_source(self):
        game, source, _ = self.scene(50316026, 17)
        blink = self.add(game.engine, "Cloudshift", zone="hand")
        self.add(game.engine, "Plains")
        self.ready_for_upkeep(game)
        identity = game.state.cards[source.object_id].logical_object_id
        self.assertEqual(1, self.trigger_count(game, source))
        self.cast(game, "A", blink, [source.ref])
        self.resolve_top(game)
        self.assertNotEqual(identity, game.state.cards[source.object_id].logical_object_id)
        game = self.replay(game, load=True)
        self.resolve_top(game)
        self.assertEqual({"+1/+1": 2}, dict(game.state.cards[source.object_id].counters))
        self.assertEqual(40, game.state.players["A"].life)
        self.replay(game, load=True)

        game, source, _ = self.scene(50316027, 17)
        self.ready_for_upkeep(game, current="A", following="B")
        self.assertEqual(0, self.trigger_count(game, source))
        self.replay(game, load=True)

    def test_printed_source_counter_and_both_branches_are_closed(self):
        original = self.db.lookup(CARD, fuzzy=False)
        result = compile_oracle_card(original, capability_registry=self.registry, capability_profile="commander_review")
        self.assertEqual("exact", result.status, result.material_residuals)
        trigger, = (node for face in result.faces for node in face.nodes if node.event == "step.begin")
        self.assertIn(CAPABILITY, trigger.capability_dependencies)
        self.assertEqual((upkeep_power_counter_effect(),), tuple(trigger.effects))
        for before, after in (
            ("16 or less", "16 or more"), ("Otherwise", "Additionally"), ("all but one", "all but two"),
            ("removed this way", "on Lily Bowen"), ("your upkeep", "each upkeep"),
        ):
            changed = replace(original, oracle_text=original.oracle_text.replace(before, after))
            self.assertNotEqual("exact", compile_oracle_card(changed, capability_registry=self.registry,
                                                            capability_profile="commander_review").status)

    def test_omitting_the_branch_binding_is_killed(self):
        with patch("quorune.compiler.upkeep_power_counter_results.upkeep_power_counter_result", return_value=None):
            with self.assertRaises(AssertionError):
                self.test_printed_source_counter_and_both_branches_are_closed()
