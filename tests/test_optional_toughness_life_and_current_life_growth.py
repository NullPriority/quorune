"""CR 119.5 and 608.2 support both printed Loxodon Lifechanter results."""

from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.carddb import CardDatabase
from quorune.current_life_effect_amount_model import CURRENT_LIFE_AMOUNT_CAPABILITY
from quorune.deck import DeckDefinition, DeckEntry
from quorune.life_total_setting_model import (
    CONTROLLED_TOUGHNESS_LIFE_CAPABILITY as CAPABILITY, LifeTotalValueSpec, plan_life_total_setting,
)
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


NAME = "Loxodon Lifechanter"
PROFILE = "commander_review"
FIXTURES = ROOT / "tests/fixtures"


class OptionalToughnessLifeAndCurrentLifeGrowthTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        folder = Path(cls.temporary.name)
        base = json.loads((FIXTURES / "bound-effect-program-cards.json").read_bytes())
        base["cards"] = [row for row in base["cards"] if row["name"] == "Generic Bound Plains"]
        base["rulings"] = []
        basic = folder / "base.json"
        basic.write_text(json.dumps(base), encoding="utf-8")
        path = folder / "toughness-life.sqlite3"
        build_fixture_database([basic, FIXTURES / "optional-toughness-life-and-current-life-growth-cards.json"], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Current creature toughness", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"), DeckEntry("Generic Bound Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def programs(self):
        record = self.db.lookup(NAME, fuzzy=False)
        result, semantics = register_card(self.db, self.registry, NAME)
        self.assertTrue(result["strict_capability_ready"], result["blockers"])
        trigger, activation = sorted(
            semantics.programs_for_oracle(record.oracle_id), key=lambda program: program.event, reverse=True,
        )
        return record, trigger, activation

    def test_complete_card_keeps_optional_total_and_locked_source_growth(self):
        record, trigger, activation = self.programs()
        self.assertIn(CAPABILITY, trigger.capability_dependencies)
        self.assertIn(CURRENT_LIFE_AMOUNT_CAPABILITY, activation.capability_dependencies)
        wrapper, = trigger.effects
        self.assertEqual("offer_optional_effect", wrapper["op"])
        self.assertEqual("controlled_creature_toughness", wrapper["effects"][0]["value"]["kind"])
        growth, = activation.effects
        self.assertEqual("$source.zone_object", growth["card"])
        self.assertEqual(growth["power"], growth["toughness"])
        self.assertEqual({"W": 1, "GENERIC": 5}, {k: v for k, v in activation.cost_schema["mana"].items() if v})
        for text in (
            record.oracle_text.replace("total toughness", "total power"),
            record.oracle_text.replace("+X/+X", "+X/+1"),
            record.oracle_text + " Invent an unsupported result.",
        ):
            ir = compile_oracle_card(replace(record, oracle_text=text), capability_registry=self.registry,
                                    capability_profile=PROFILE)
            self.assertNotEqual("exact", ir.status)

    def test_raw_source_growth_rejects_mixed_slots_and_total_can_be_signed(self):
        _, _, activation = self.programs()
        growth, = activation.effects
        for tough in (1, {**growth["toughness"], "binding_id": "unrelated"}):
            changed = replace(activation, effects=({**growth, "toughness": tough},))
            result = bind_semantic_program_runtime(changed, capability_registry=self.registry, profile=PROFILE)
            self.assertFalse(result["strict"], result)
        change, = plan_life_total_setting(
            LifeTotalValueSpec("controlled_creature_toughness"),
            ("A",), {"A": 4}, starting_life=40, counts={"A": -2},
        )
        self.assertEqual((-2, -6), (change.requested_total, change.delta))

    def test_actual_source_growth_locks_life_after_response_before_optional_total(self):
        game = self.session(119506082)
        source = self.add(game.engine, NAME, zone="hand")
        self.add(game.engine, "Grizzly Bears", ref="loxodon-owned-bear")
        self.add(game.engine, "Grizzly Bears", seat="B", ref="loxodon-foreign-bear")
        bolt = self.add(game.engine, "Lightning Bolt", seat="B", zone="hand")
        private = self.add(game.engine, "Counterspell", seat="C", zone="hand", ref="loxodon-private")
        self.assert_closed(game, source)
        self.prepare(game, mana={"C": 10, "W": 2})
        game.state.players["B"].mana_pool["R"] = 1
        self.checkpoint(game)
        self.cast_for(game, "A", source, reserve={"C": 5, "W": 1})
        self.resolve_top(game)
        self.priority(game, "A")
        tapless, = [row for row in game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
                    if row["id"].startswith(f"activate:{source.ref}:")]
        activated = game.act("pilot:A", {"action_id": tapless["id"], "pay": "auto"})
        self.assertTrue(activated.ok, activated.summary)
        self.cast_for(game, "B", bolt, ["A"])
        self.resolve_top(game)
        self.assertEqual(37, game.state.players["A"].life)
        self.resolve_top(game)
        self.assertEqual(43, game.engine._numeric_stat(source.object_id, "toughness"))
        pending = self.resolve(game)
        self.assertEqual(("semantic.choice", ["A"]), (pending.kind, list(pending.actors)))
        command = {"action_id": "choose", "choice": "apply"}
        before = authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:B", command).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        game = self.replay(game, load=True)
        chosen = game.act("pilot:A", command)
        self.assertTrue(chosen.ok, chosen.summary)
        self.assertEqual(45, game.state.players["A"].life)
        self.assertEqual(41, game.engine._numeric_stat(source.object_id, "power"))
        self.assertEqual(43, game.engine._numeric_stat(source.object_id, "toughness"))
        for viewer in ("pilot:A", "pilot:B", "pilot:D", "spectator"):
            self.assertNotIn(private.ref, json.dumps(game.packet(viewer, full=True)))
        self.replay(game, load=True)

    def test_actual_optional_total_keeps_trigger_after_source_leaves_and_permits_decline(self):
        game = self.session(119506083)
        source = self.add(game.engine, NAME, zone="hand")
        self.add(game.engine, "Grizzly Bears")
        bounce = self.add(game.engine, "Boomerang", seat="B", zone="hand")
        self.assert_closed(game, source)
        self.prepare(game, mana={"C": 5, "W": 1})
        game.state.players["B"].mana_pool["U"] = 2
        self.checkpoint(game)
        self.cast_for(game, "A", source)
        self.resolve_top(game)
        self.cast_for(game, "B", bounce, [source.ref])
        self.resolve_top(game)
        self.assertEqual("hand", game.state.cards[source.object_id].zone)
        pending = self.resolve(game)
        self.assertEqual(("semantic.choice", ["A"]), (pending.kind, list(pending.actors)))
        resumed = self.replay(game, load=True)
        for session, choice, expected in ((game, "decline", 40), (resumed, "apply", 2)):
            result = session.act("pilot:A", {"action_id": "choose", "choice": choice})
            self.assertTrue(result.ok, result.summary)
            self.assertEqual(expected, session.state.players["A"].life)
            self.replay(session, load=True)

    def test_native_toughness_witness_kills_card_count_mutation(self):
        from quorune.effect_runtime import life_total_setting

        count = life_total_setting._counts
        with patch.object(life_total_setting, "_counts", side_effect=lambda host, kind, seats: count(
            host, "creatures", seats,
        )):
            with self.assertRaisesRegex(AssertionError, "45 != 2"):
                self.test_actual_source_growth_locks_life_after_response_before_optional_total()
