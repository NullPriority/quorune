"""A source's linked entry color funds immediate mana for the actual land controller."""

from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import unittest

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.triggered_mana_model import (
    SOURCE_CHOSEN_TRIGGERED_MANA_CAPABILITY, fixed_triggered_mana_runtime_dependencies, fixed_triggered_mana_spec,
)
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


PROFILE = "commander_review"


class SourceChosenTriggeredManaTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "source-chosen-mana.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures/source-chosen-tap-mana-cards.json"], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        commander = "Isamaru, Hound of Konda"
        cls.deck = DeckDefinition("Source-chosen tap mana", [
            DeckEntry(commander, 1, "commander"), DeckEntry("Plains", 30),
        ], [commander])

    def start(self, game, *cards, mana):
        for card in cards:
            self.assert_closed(game, card)
        game.state.config.semantic_policy = "trusted_only"
        self.prepare(game, mana=mana)
        self.checkpoint(game)

    def choose_color(self, game, color):
        decision = self.resolve(game)
        self.assertEqual(("replacement.order", ["A"]), (decision.kind, decision.actors))
        choice, = (row["id"] for row in game.packet("pilot:A", full=True)["decision"]["ctx"]["options"]
                   if row["id"].endswith(":" + color))
        command = {"action_id": "choose", "choices": {"replacement": choice}}
        initial = authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:B", command).ok)
        self.assertEqual(initial, authoritative_state_hash(game.state))
        result = game.act("pilot:A", command)
        self.assertTrue(result.ok, result.summary)
        self.assertIsNone(self.resolve(game))

    def mana_action(self, game, card, seat="B"):
        self.priority(game, seat)
        actions = game.packet("pilot:" + seat, full=True)["decision"]["ctx"]["legal"]["actions"]
        action, = (row for row in actions if row.get("source") == game.state.cards[card.object_id].ref)
        return {"action_id": action["id"]}

    def activate_land(self, game, card, bundle):
        command = {**self.mana_action(game, card), "mana_output": bundle}
        result = game.act("pilot:B", command)
        self.assertTrue(result.ok, result.summary)
        self.assertFalse(game.state.stack)
        self.assertFalse(game.state.pending_trigger_batches)

    def test_actual_aura_funds_the_foreign_forest_controllers_immediate_cast(self):
        for direct in (True, False):
            with self.subTest(direct=direct):
                game = self.session(76231201 + direct)
                aura = self.add(game.engine, "Utopia Sprawl", zone="hand")
                forest = self.add(game.engine, "Forest", seat="B", ref="foreign-aura-forest")
                plain = self.add(game.engine, "Forest", seat="B", ref="untouched-foreign-forest")
                spell = self.add(game.engine, "Opt", seat="B", zone="hand")
                self.start(game, aura, spell, mana={"G": 1})
                self.cast(game, "A", aura, [forest.ref])
                game = self.replay(game, load=True)
                self.choose_color(game, "U")
                command = self.mana_action(game, forest)
                initial = authoritative_state_hash(game.state)
                self.assertFalse(game.act("pilot:B", {**command, "mana_output": {"U": 1, "G": 1}}).ok)
                self.assertEqual(initial, authoritative_state_hash(game.state))
                if direct:
                    self.activate_land(game, forest, {"G": 1})
                    self.assertEqual(1, game.state.players["B"].mana_pool["U"])
                self.cast(game, "B", spell)
                self.assertEqual(["spell"], [item.kind for item in game.state.stack])
                self.assertEqual((1, 0), tuple(game.state.players["B"].mana_pool[color] for color in "GU"))
                self.assertEqual(0, sum(game.state.players["A"].mana_pool.values()))
                self.assertTrue(game.state.cards[forest.object_id].tapped)
                self.assertFalse(game.state.cards[plain.object_id].tapped)
                event, = (row for row in game.state.events if row.code == "mana.triggered")
                self.assertEqual({"U": 1}, event.details["bundle"])
                self.assertEqual(game.state.cards[aura.object_id].ref, event.details["source"])
                self.replay(game, load=True)

    def test_actual_gauntlet_requires_basic_land_and_the_real_produced_color_for_every_controller(self):
        game = self.session(76231204)
        gauntlet = self.add(game.engine, "Gauntlet of Power", zone="hand")
        mountain = self.add(game.engine, "Mountain", seat="B")
        furnace = self.add(game.engine, "Great Furnace", seat="B")
        island = self.add(game.engine, "Island", seat="B")
        goblin = self.add(game.engine, "Raging Goblin", seat="B")
        self.start(game, gauntlet, mana={"C": 5})
        self.cast(game, "A", gauntlet)
        self.choose_color(game, "R")
        self.assertEqual(2, game.engine._numeric_stat(goblin.object_id, "power"))
        self.activate_land(game, furnace, {"R": 1})
        self.assertEqual(1, game.state.players["B"].mana_pool["R"])
        self.activate_land(game, island, {"U": 1})
        self.assertEqual(1, game.state.players["B"].mana_pool["U"])
        game = self.replay(game, load=True)
        self.activate_land(game, mountain, {"R": 1})
        self.assertEqual(3, game.state.players["B"].mana_pool["R"])
        self.assertEqual(0, sum(game.state.players["A"].mana_pool.values()))
        event, = (row for row in game.state.events if row.code == "mana.triggered")
        self.assertEqual((mountain.ref, {"R": 1}), (event.details["producer"], event.details["bundle"]))
        self.replay(game, load=True)

    def test_actual_gauntlet_without_linked_entry_choice_produces_no_bonus(self):
        game = self.session(76231205)
        gauntlet = self.add(game.engine, "Gauntlet of Power")
        mountain = self.add(game.engine, "Mountain", seat="B")
        self.start(game, gauntlet, mana={})
        self.activate_land(game, mountain, {"R": 1})
        self.assertEqual(1, game.state.players["B"].mana_pool["R"])
        self.assertFalse(any(row.code == "mana.triggered" for row in game.state.events))
        self.replay(game, load=True)

    def test_complete_originals_require_linked_entry_and_independent_mana_authority(self):
        for report, semantics in (
            register_card(self.db, self.registry, "Utopia Sprawl"),
            register_card(self.db, self.registry, "Gauntlet of Power"),
        ):
            with self.subTest(oracle_id=report["oracle_id"]):
                self.assertTrue(report["strict_capability_ready"], report["blockers"])
                self.assertEqual("capability_closed", report["trust_basis"])
                program, = (program for program in semantics.programs_for_oracle(report["oracle_id"])
                            if SOURCE_CHOSEN_TRIGGERED_MANA_CAPABILITY in program.capability_dependencies)
                self.assertIn(SOURCE_CHOSEN_TRIGGERED_MANA_CAPABILITY, fixed_triggered_mana_runtime_dependencies(program))
                self.assertEqual(3, fixed_triggered_mana_spec(program).to_dict()["schema_version"])
                deps = ("mana.triggered.fixed_output",)
                absent = replace(program, capability_dependencies=deps,
                                 capability_closure=self.registry.closure(deps, profile=PROFILE).to_dict())
                binding = bind_semantic_program_runtime(absent, capability_registry=self.registry, profile=PROFILE)
                self.assertFalse(binding["strict"])
                self.assertIn(
                    f"capability:undeclared_runtime_dependency:{SOURCE_CHOSEN_TRIGGERED_MANA_CAPABILITY}", binding["blockers"],
                )
        card = self.db.lookup("Utopia Sprawl", fuzzy=False)
        for old, new in (
            ("As this Aura enters, choose a color.\n", ""), ("enchanted Forest", "enchanted artifact"),
            ("one mana of the chosen color", "two mana of the chosen color"),
            ("one mana of the chosen color.", "one mana of the chosen color. Draw a card."),
        ):
            changed = replace(card, oracle_text=card.oracle_text.replace(old, new))
            self.assertNotEqual("exact", compile_oracle_card(
                changed, capability_registry=self.registry, capability_profile=PROFILE,
            ).status)
        with patch("quorune.compiler.triggered_mana_nodes._source_chosen_spec", return_value=None):
            self.assertNotEqual("exact", compile_oracle_card(
                card, capability_registry=self.registry, capability_profile=PROFILE,
            ).status)


class TurnLandManaTriggerTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        folder = Path(cls.temporary.name)
        fixtures = ROOT / "tests/fixtures"
        parts = [fixtures / "source-chosen-tap-mana-cards.json"]
        donor_cards = {
            "source-self-entry-fixed-counter-cards.json": {"Underground Sea"},
            "fixed-triggered-mana-cards.json": {"Snow-Covered Island"},
            "ability-source-counter-cards.json": {"Counterspell", "Sol Ring"},
            "activation-restriction-material-cards.json": {"Lightning Bolt"},
            "activation-paid-result-slot-cards.json": {"Swamp"},
            "tap-for-mana-production-multipliers-cards.json": {"Mana Reflection"},
        }
        for filename, names in donor_cards.items():
            document = json.loads((fixtures / filename).read_bytes())
            document["cards"] = [row for row in document["cards"] if row["name"] in names]
            assert {row["name"] for row in document["cards"]} == names
            ids = {row["oracle_id"] for row in document["cards"]}
            document["rulings"] = [row for row in document.get("rulings", ()) if row["oracle_id"] in ids]
            filtered = folder / filename
            filtered.write_text(json.dumps(document), encoding="utf-8")
            parts.append(filtered)
        path = folder / "turn-land-mana.sqlite3"
        build_fixture_database(parts, path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        commander = "Isamaru, Hound of Konda"
        cls.deck = DeckDefinition("Turn land mana", [
            DeckEntry(commander, 1, "commander"), DeckEntry("Plains", 30),
        ], [commander])

    def land_action(self, game, seat, card, *, ability=None):
        self.priority(game, seat)
        actions = game.packet("pilot:" + seat, full=True)["decision"]["ctx"]["legal"]["actions"]
        return next(row for row in actions if row.get("source") == card.ref and row.get("mana_ability")
                    and (ability is None or row["ability"] == ability))

    def tap_for_mana(self, game, seat, card, bundle, *, ability=None):
        action = self.land_action(game, seat, card, ability=ability)
        command = {"action_id": action["id"], "mana_output": bundle}
        outcome = game.act("pilot:" + seat, command)
        self.assertTrue(outcome.ok, outcome.summary)

    def test_high_tide_accumulates_with_replacement_and_expires_after_cleanup(self):
        from quorune.all_player_turn_rule_model import ResolvedTurnLandManaTrigger

        game = self.session(60544001)
        game.state.config.semantic_policy = "trusted_only"
        first = self.add(game.engine, "High Tide", zone="hand", ref="turn-tide-first")
        second = self.add(game.engine, "High Tide", zone="hand", ref="turn-tide-second")
        reflection = self.add(game.engine, "Mana Reflection")
        sea = self.add(game.engine, "Underground Sea", seat="B")
        snow = self.add(game.engine, "Snow-Covered Island")
        late = self.add(game.engine, "Island", zone="hand", ref="turn-tide-late")
        next_turn = self.add(game.engine, "Island", seat="B", ref="turn-tide-next-turn")
        forest = self.add(game.engine, "Forest", seat="C")
        ring = self.add(game.engine, "Sol Ring")
        private = self.add(game.engine, "Counterspell", seat="D", zone="hand", ref="turn-tide-private")
        for card in (first, reflection, sea):
            self.assert_closed(game, card)
        self.prepare(game, mana={"U": 2})
        self.checkpoint(game)
        self.cast_for(game, "A", first)
        self.assertFalse(any(isinstance(effect, ResolvedTurnLandManaTrigger)
                             for effect in game.state.continuous_effects))
        self.assertIsNone(self.resolve(game))
        self.cast_for(game, "A", second)
        self.assertIsNone(self.resolve(game))
        effects = tuple(effect for effect in game.state.continuous_effects
                        if isinstance(effect, ResolvedTurnLandManaTrigger))
        self.assertEqual(2, len({effect.effect_id for effect in effects}))
        self.assertTrue(all(not effect.snow for effect in effects))
        game = self.replay(game, load=True)

        self.tap_for_mana(game, "B", sea, {"B": 1}, ability="intrinsic_swamp")
        self.assertEqual((1, 2), tuple(game.state.players["B"].mana_pool[symbol] for symbol in "BU"))
        self.assertEqual(0, game.state.players["A"].mana_pool["U"])
        self.tap_for_mana(game, "A", snow, {"U": 1})
        self.assertEqual(4, game.state.players["A"].mana_pool["U"])
        self.assertEqual(2, game.engine._snow_mana_pool("A", None)["U"])
        play = game.act("pilot:A", {"action_id": "play-land:" + late.ref})
        self.assertTrue(play.ok, play.summary)
        self.tap_for_mana(game, "A", late, {"U": 1})
        self.assertEqual(8, game.state.players["A"].mana_pool["U"])
        self.tap_for_mana(game, "A", ring, {"C": 2})
        self.assertEqual(4, game.state.players["A"].mana_pool["C"])
        self.assertEqual(8, game.state.players["A"].mana_pool["U"])
        self.tap_for_mana(game, "C", forest, {"G": 1})
        self.assertEqual((1, 0), tuple(game.state.players["C"].mana_pool[symbol] for symbol in "GU"))
        for seat in "ABC":
            self.assertNotIn(private.ref, json.dumps(game.packet("pilot:" + seat, full=True)))
        self.assertFalse(game.state.stack)
        before = game.state.turn_sequence
        self.until(game, lambda current: current.state.turn_sequence > before)
        self.assertFalse(any(isinstance(effect, ResolvedTurnLandManaTrigger)
                             for effect in game.state.continuous_effects))
        self.tap_for_mana(game, "B", next_turn, {"U": 1})
        self.assertEqual(1, game.state.players["B"].mana_pool["U"])
        self.replay(game, load=True)

    def test_high_tide_auto_payment_consumes_its_immediate_bonus_from_one_island(self):
        game = self.session(60544002)
        game.state.config.semantic_policy = "trusted_only"
        tide = self.add(game.engine, "High Tide", zone="hand")
        counter = self.add(game.engine, "Counterspell", zone="hand")
        island = self.add(game.engine, "Island")
        bolt = self.add(game.engine, "Lightning Bolt", seat="B", zone="hand")
        self.prepare(game, mana={"U": 1})
        game.state.players["B"].mana_pool["R"] = 1
        self.checkpoint(game)
        self.cast_for(game, "A", tide)
        self.assertIsNone(self.resolve(game))
        self.cast_for(game, "B", bolt, ["C"])
        stack = game.state.stack[-1]
        self.priority(game, "A")
        actions = game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        offer = next(row for row in actions if row.get("card") == counter.ref)
        command = {"action_id": offer["id"], "targets": [stack.ref], "pay": "auto"}
        before = authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:B", command).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        result = game.act("pilot:A", command)
        self.assertTrue(result.ok, result.summary)
        self.assertTrue(game.state.cards[island.object_id].tapped)
        self.assertEqual(0, game.state.players["A"].mana_pool["U"])
        triggered, = (event for event in game.state.events if event.code == "mana.triggered")
        self.assertEqual((island.ref, {"U": 1}), (triggered.details["producer"], triggered.details["bundle"]))
        self.assertIsNone(self.resolve(game))
        self.assertEqual(40, game.state.players["C"].life)
        self.replay(game, load=True)

    def test_bubbling_muck_reuses_the_live_subtype_and_fixed_output_branch(self):
        game = self.session(60544003)
        game.state.config.semantic_policy = "trusted_only"
        muck = self.add(game.engine, "Bubbling Muck", zone="hand")
        swamp = self.add(game.engine, "Swamp", seat="B")
        island = self.add(game.engine, "Island", seat="B")
        self.assert_closed(game, muck)
        self.prepare(game, mana={"B": 1})
        self.checkpoint(game)
        self.cast_for(game, "A", muck)
        self.assertIsNone(self.resolve(game))
        self.tap_for_mana(game, "B", swamp, {"B": 1})
        self.tap_for_mana(game, "B", island, {"U": 1})
        self.assertEqual((2, 1), tuple(game.state.players["B"].mana_pool[symbol] for symbol in "BU"))
        self.replay(game, load=True)

    def test_turn_land_mana_grammar_binding_and_journal_decode_fail_closed(self):
        from test_fixed_public_alternative_costs import card_record
        from quorune.all_player_turn_rule_model import (
            TURN_LAND_MANA_TRIGGER_CAPABILITY, ResolvedTurnLandManaTrigger, TurnLandManaTriggerSpec,
        )

        oracle = "Until end of turn, whenever a player taps an Island for mana, that player adds an additional {U}."
        record = card_record(oracle, type_line="Instant")
        options = {"capability_registry": self.registry, "capability_profile": PROFILE}
        self.assertEqual("exact", compile_oracle_card(record, **options).status)
        for text in (oracle.replace("Until end of turn", "Until your next turn"),
                     oracle.replace("a player taps", "an opponent taps"),
                     oracle.replace("{U}", "{U}{U}"), oracle.replace("Island", "Gate")):
            self.assertNotEqual("exact", compile_oracle_card(replace(record, oracle_text=text), **options).status)
        with patch("quorune.compiler.all_player_turn_rules._TURN_LAND_MANA") as matcher:
            matcher.fullmatch.return_value = None
            self.assertNotEqual("exact", compile_oracle_card(record, **options).status)
        report, semantics = register_card(self.db, self.registry, "High Tide")
        self.assertTrue(report["strict_capability_ready"], report)
        program, = semantics.programs_for_oracle(self.db.lookup("High Tide").oracle_id)
        effect, = program.effects
        reject = bind_semantic_program_runtime(replace(program, effects=[{**effect, "schema_version": True}]),
                                               capability_registry=self.registry, profile=PROFILE)
        self.assertFalse(reject["strict"], reject)
        caps = tuple(cap for cap in program.capability_dependencies if cap != TURN_LAND_MANA_TRIGGER_CAPABILITY)
        changed = replace(program, capability_dependencies=caps,
                          capability_closure=self.registry.closure(caps, profile=PROFILE).to_dict() if caps else None)
        reject = bind_semantic_program_runtime(changed, capability_registry=self.registry, profile=PROFILE)
        self.assertIn("capability:undeclared_runtime_dependency:" + TURN_LAND_MANA_TRIGGER_CAPABILITY,
                      reject["blockers"])
        journal = ResolvedTurnLandManaTrigger("effect", "stack", 1, TurnLandManaTriggerSpec("island", "U"))
        raw = journal.to_dict()
        self.assertEqual(journal, ResolvedTurnLandManaTrigger.from_dict(raw))
        for broken in ({**raw, "snow": 1}, {**raw, "schema_version": True},
                       {**raw, "spec": {"land_subtype": "gate", "symbol": "U"}}):
            with self.subTest(broken=broken), self.assertRaises(ValueError):
                ResolvedTurnLandManaTrigger.from_dict(broken)
