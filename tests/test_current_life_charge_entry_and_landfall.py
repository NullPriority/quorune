"""CR 614.12, 608.2h and 119.5 support both printed Eternity Vessel abilities."""

from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.entry_counter_model import (
    CURRENT_LIFE_ENTRY_COUNTER_CAPABILITY, DynamicEntryCounterAmountSpec,
)
from quorune.entry_counters import dynamic_entry_counter_amount
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.source_charge_life_totals import (
    SOURCE_CHARGE_LIFE_CAPABILITY, SOURCE_CHARGE_LIFE_CONTEXT,
)
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


NAME = "Eternity Vessel"
PROFILE = "commander_review"
FIXTURES = ROOT / "tests/fixtures"


class CurrentLifeChargeEntryAndLandfallTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        folder = Path(cls.temporary.name)
        base = json.loads((FIXTURES / "bound-effect-program-cards.json").read_bytes())
        base["cards"] = [row for row in base["cards"] if row["name"] == "Generic Bound Plains"]
        base["rulings"] = []
        basic = folder / "base.json"
        basic.write_text(json.dumps(base), encoding="utf-8")
        path = folder / "vessel.sqlite3"
        build_fixture_database([basic, FIXTURES / "current-life-charge-entry-and-landfall-cards.json"], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Entry life charge", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"), DeckEntry("Generic Bound Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def programs(self):
        record = self.db.lookup(NAME, fuzzy=False)
        result, semantics = register_card(self.db, self.registry, NAME)
        self.assertTrue(result["strict_capability_ready"], result["blockers"])
        programs = {program.event: program for program in semantics.programs_for_oracle(record.oracle_id)}
        return record, programs["zone.change"], programs["land.enter"]

    def test_complete_vessel_requires_both_closed_original_abilities(self):
        record, entry, trigger = self.programs()
        self.assertIn(CURRENT_LIFE_ENTRY_COUNTER_CAPABILITY, entry.capability_dependencies)
        self.assertIn(SOURCE_CHARGE_LIFE_CAPABILITY, trigger.capability_dependencies)
        descriptor, = entry.handlers
        self.assertTrue(descriptor["requires_complete_card_program"])
        self.assertEqual("charge", descriptor["counter_name"])
        self.assertEqual("controller_life_total", descriptor["amount_spec"]["value_source"])
        self.assertEqual("offer_optional_effect", trigger.effects[0]["op"])
        for text in (
            record.oracle_text.replace("your life total.", "an opponent's life total.", 1),
            record.oracle_text.replace("on this artifact.", "on a target artifact."),
            record.oracle_text + " Invent an unsupported instruction.",
        ):
            with self.subTest(text=text):
                value = compile_oracle_card(replace(record, oracle_text=text), capability_registry=self.registry,
                                            capability_profile=PROFILE)
                self.assertNotEqual("exact", value.status)

    def test_raw_entry_and_trigger_reject_widening_and_read_entering_controller(self):
        _, entry, trigger = self.programs()
        descriptor, = entry.handlers
        amount = descriptor["amount_spec"]
        for altered in (
            {key: value for key, value in descriptor.items() if key != "requires_complete_card_program"},
            {**descriptor, "amount_spec": {**amount, "coefficient": 2}},
        ):
            with self.assertRaises(ValueError):
                replace(entry, handlers=(altered,))
        changed_trigger = replace(trigger, event="permanent.enter")
        result = bind_semantic_program_runtime(changed_trigger, capability_registry=self.registry, profile=PROFILE)
        self.assertFalse(result["strict"], result)
        self.assertTrue(any("runtime_source_charge_life_total:invalid" in item for item in result["blockers"]))
        spec = DynamicEntryCounterAmountSpec.from_dict(amount)
        source = SimpleNamespace(zone="hand", owner="A", controller="A")
        state = SimpleNamespace(stack=(), players={"A": SimpleNamespace(life=8), "B": SimpleNamespace(life=15)})
        host = SimpleNamespace(state=state)
        self.assertEqual(15, dynamic_entry_counter_amount(host, card=source, destination_controller="B",
                                                         amount_spec=spec))
        state.players["B"].life = -2
        self.assertEqual(0, dynamic_entry_counter_amount(host, card=source, destination_controller="B",
                                                        amount_spec=spec))

    def test_actual_vessel_reads_entry_life_and_latest_departed_charge_counters(self):
        game = self.session(614126082)
        vessel = self.add(game.engine, NAME, zone="hand")
        land = self.add(game.engine, "Forest", zone="hand")
        core = self.add(game.engine, "Coretapper")
        first_bolt = self.add(game.engine, "Lightning Bolt", seat="B", zone="hand", ref="vessel-first-bolt")
        second_bolt = self.add(game.engine, "Lightning Bolt", seat="B", zone="hand", ref="vessel-second-bolt")
        bounce = self.add(game.engine, "Boomerang", seat="B", zone="hand")
        private = self.add(game.engine, "Counterspell", seat="C", zone="hand", ref="vessel-private")
        for card in (vessel, core, bounce):
            self.assert_closed(game, card)
        self.prepare(game, mana={"C": 6})
        game.state.players["B"].mana_pool.update({"R": 2, "U": 2})
        self.checkpoint(game)
        self.cast_for(game, "A", vessel)
        self.cast_for(game, "B", first_bolt, ["A"])
        self.resolve_top(game)
        self.assertEqual(37, game.state.players["A"].life)
        self.resolve_top(game)
        self.assertEqual(37, game.state.cards[vessel.object_id].counters["charge"])
        self.priority(game, "A")
        played = game.act("pilot:A", {"action_id": f"play-land:{land.ref}"})
        self.assertTrue(played.ok, played.summary)

        self.priority(game, "A")
        ability, = [ability for ability in game.engine._activated_abilities(core) if ability.sacrifice_source]
        action = f"activate:{core.ref}:{ability.ability_id}"
        offers = game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        self.assertIn(action, [row["id"] for row in offers])
        activated = game.act("pilot:A", {"action_id": action, "targets": [vessel.ref], "pay": "auto"})
        self.assertTrue(activated.ok, activated.summary)
        self.assertEqual("graveyard", game.state.cards[core.object_id].zone)
        self.resolve_top(game)
        self.assertEqual(39, game.state.cards[vessel.object_id].counters["charge"])
        self.cast_for(game, "B", bounce, [vessel.ref])
        self.resolve_top(game)
        self.assertEqual("hand", game.state.cards[vessel.object_id].zone)
        self.assertEqual(39, game.state.stack[-1].context[SOURCE_CHARGE_LIFE_CONTEXT]["departure_count"])
        self.cast_for(game, "B", second_bolt, ["A"])
        self.resolve_top(game)
        self.assertEqual(34, game.state.players["A"].life)
        pending = self.resolve(game)
        self.assertEqual(("semantic.choice", ["A"]), (pending.kind, list(pending.actors)))
        game = self.replay(game, load=True)
        command = {"action_id": "choose", "choice": "apply"}
        before = authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:B", command).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        result = game.act("pilot:A", command)
        self.assertTrue(result.ok, result.summary)
        self.assertEqual(39, game.state.players["A"].life)
        for viewer in ("pilot:A", "pilot:B", "pilot:D", "spectator"):
            self.assertNotIn(private.ref, json.dumps(game.packet(viewer, full=True)))
        self.replay(game, load=True)

    def test_actual_land_put_from_search_triggers_live_charge_choice_and_decline(self):
        game = self.session(614126083)
        vessel = self.add(game.engine, NAME, zone="hand")
        search = self.add(game.engine, "Rampant Growth", zone="hand")
        forest = self.add(game.engine, "Forest", zone="library", ref="vessel-grown-forest")
        bolt = self.add(game.engine, "Lightning Bolt", seat="B", zone="hand")
        self.assert_closed(game, vessel)
        self.prepare(game, mana={"C": 7, "G": 1})
        game.state.players["B"].mana_pool["R"] = 1
        self.checkpoint(game)
        self.cast_for(game, "A", vessel, reserve={"C": 1, "G": 1})
        self.resolve_top(game)
        self.assertEqual(40, game.state.cards[vessel.object_id].counters["charge"])
        self.cast_for(game, "A", search)
        pending = self.resolve(game)
        self.assertEqual("semantic.search", pending.kind)
        found = game.act("pilot:A", {"action_id": "choose", "search_cards": [forest.ref]})
        self.assertTrue(found.ok, found.summary)
        self.assertEqual("battlefield", game.state.cards[forest.object_id].zone)
        self.cast_for(game, "B", bolt, ["A"])
        self.resolve_top(game)
        pending = self.resolve(game)
        self.assertEqual(("semantic.choice", ["A"]), (pending.kind, list(pending.actors)))
        resumed = self.replay(game, load=True)
        for session, choice, expected in ((game, "decline", 37), (resumed, "apply", 40)):
            result = session.act("pilot:A", {"action_id": "choose", "choice": choice})
            self.assertTrue(result.ok, result.summary)
            self.assertEqual(expected, session.state.players["A"].life)
            self.replay(session, load=True)

    def test_native_vessel_witness_kills_entry_amount_as_landfall_mutation(self):
        from quorune.rules import source_charge_life_totals

        entry_total = {"kind": "fixed", "schema_version": 1, "total": 37}
        with patch.object(source_charge_life_totals, "resolve_source_charge_life_value",
                          return_value=entry_total):
            with self.assertRaisesRegex(AssertionError, "39 != 37"):
                self.test_actual_vessel_reads_entry_life_and_latest_departed_charge_counters()


    def test_native_vessel_witness_kills_announcement_life_mutation(self):
        from quorune.semantic_runtime import self_entry_counters

        amount = self_entry_counters.dynamic_entry_counter_amount

        def life_before_the_response(host, **kwargs):
            if kwargs["amount_spec"].value_source.value == "controller_life_total":
                return 40
            return amount(host, **kwargs)

        with patch.object(self_entry_counters, "dynamic_entry_counter_amount",
                          side_effect=life_before_the_response):
            with self.assertRaisesRegex(AssertionError, "37 != 40"):
                self.test_actual_vessel_reads_entry_life_and_latest_departed_charge_counters()
