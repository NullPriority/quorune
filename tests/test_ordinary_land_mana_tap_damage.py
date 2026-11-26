"""Original land mana damage waits for ordinary priority even during payment."""

from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.deck import DeckDefinition, DeckEntry
from quorune.mana_source_counter_payment_model import LAND_MANA_TAP_VERSION_FIELD
from quorune.model import GameState
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.record_state_provenance import format_state_versions
from quorune.rules.capabilities import load_default_capability_registry
from quorune.triggered_mana_model import (
    LAND_MANA_TAP_EVENT, land_mana_tap_condition, land_mana_tap_damage_effect,
    SOURCE_MANA_TAP_CAPABILITY, SOURCE_MANA_TAP_EVENT, source_mana_tap_condition,
    source_mana_tap_damage_effect,
)
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


MANABARBS = "Manabarbs"
BURNING = "Burning Earth"


class OrdinaryLandManaTapDamageTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "ordinary-land-mana-tap-damage.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures/ordinary-land-mana-tap-damage-cards.json"], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Mana before the trigger", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"), DeckEntry("Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def test_complete_originals_own_the_same_ordinary_occurrence_and_exact_filter(self):
        for name, nonbasic in ((MANABARBS, False), (BURNING, True)):
            with self.subTest(card=name):
                binding, semantics = register_card(self.db, self.registry, name)
                self.assertTrue(binding["strict_capability_ready"], binding["blockers"])
                card = self.db.lookup(name, fuzzy=False)
                trigger, = semantics.programs_for_oracle(card.oracle_id)
                self.assertEqual(LAND_MANA_TAP_EVENT, trigger.event)
                self.assertEqual(land_mana_tap_condition(nonbasic=nonbasic), trigger.event_condition)
                self.assertEqual([land_mana_tap_damage_effect()], trigger.effects)
                self.assertIsNone(trigger.target_schema)
                invalid = replace(trigger, event_condition={"field": "land_basic", "op": "eq", "value": True})
                self.assertFalse(bind_semantic_program_runtime(
                    invalid, capability_registry=self.registry, profile="commander_review",
                )["strict"])
                for changed in (
                    card.oracle_text.replace("for mana", "during their turn"),
                    card.oracle_text.replace("1 damage", "2 damage"),
                    card.oracle_text.replace("to that player", "to each opponent"),
                ):
                    ir = compile_oracle_card(
                        replace(card, oracle_text=changed), capability_registry=self.registry,
                        capability_profile="commander_review",
                    )
                    self.assertNotEqual("exact", ir.status)

    def sources(self, game, *, manabarbs_zone="battlefield"):
        manabarbs = self.add(game.engine, MANABARBS, zone=manabarbs_zone)
        burning = self.add(game.engine, BURNING, seat="B")
        for source in (manabarbs, burning):
            self.assert_closed(game, source)
        return manabarbs, burning

    def actions(self, game, seat):
        self.priority(game, seat)
        return game.packet("pilot:" + seat, full=True)["decision"]["ctx"]["legal"]["actions"]

    def activate(self, game, seat, source, *, ability=None, **fields):
        action, = (
            row["id"] for row in self.actions(game, seat)
            if row["id"].startswith(f"activate:{source.ref}:") and (ability is None or row.get("ability") == ability)
        )
        result = game.act("pilot:" + seat, {"action_id": action, **fields})
        self.assertTrue(result.ok, result.summary)

    def land_triggers(self, game):
        return [item for item in game.state.stack if item.context.get("event") == LAND_MANA_TAP_EVENT]

    def test_actual_manual_nonbasic_dual_produces_mana_before_either_ordinary_trigger(self):
        game = self.session(10612001)
        manabarbs, burning = self.sources(game, manabarbs_zone="hand")
        dual = self.add(game.engine, "Tropical Island", seat="B")
        creature = self.add(game.engine, "Runeclaw Bear", seat="B")
        response = self.add(game.engine, "Giant Growth", zone="hand", seat="B")
        self.assert_closed(game, response)
        self.prepare(game, mana={"C": 3, "R": 1})
        self.checkpoint(game)
        self.cast_for(game, "A", manabarbs)
        self.resolve_top(game)
        life = game.state.players["B"].life
        self.activate(game, "B", dual, ability="intrinsic_forest")
        self.assertEqual(1, game.state.players["B"].mana_pool["G"])
        self.assertEqual(life, game.state.players["B"].life)
        self.assertEqual([manabarbs.object_id, burning.object_id], [item.source_object_id for item in self.land_triggers(game)])
        self.assertEqual([(False, "B", dual.logical_object_id)] * 2,
                         [(item.context["land_basic"], item.context["player"], item.context["card_object_identity"])
                          for item in self.land_triggers(game)])
        rows = self.actions(game, "B")
        self.assertFalse(any(row.get("action") == "undo_mana" and row.get("source") == dual.ref for row in rows))
        before = authoritative_state_hash(game.state)
        rejected = game.act("pilot:B", {"a": "undo_mana", "source": dual.ref})
        self.assertFalse(rejected.ok, rejected.summary)
        self.assertEqual(before, authoritative_state_hash(game.state))
        game = self.replay(game, load=True)
        self.cast_for(game, "B", response, [creature.ref])
        self.resolve_top(game)
        self.assertEqual(5, int(game.engine._effective_card_data(game.state.cards[creature.object_id])["power"]))
        self.assertEqual((life, 0), (game.state.players["B"].life, game.state.players["B"].mana_pool["G"]))
        self.assertIsNone(self.resolve(game))
        self.assertEqual(life - 2, game.state.players["B"].life)
        self.replay(game, load=True)

    def test_actual_automatic_payment_places_all_triggers_over_the_paid_spell_in_apnap_order(self):
        game = self.session(10612002)
        manabarbs, burning = self.sources(game)
        basic = self.add(game.engine, "Mountain")
        dual = self.add(game.engine, "Tropical Island")
        spell = self.add(game.engine, "Lightning Strike", zone="hand")
        self.assert_closed(game, spell)
        self.prepare(game)
        self.checkpoint(game)
        life_a, life_b = (game.state.players[seat].life for seat in "AB")
        self.cast_for(game, "A", spell, ["B"])
        self.until(game, lambda current: len(self.land_triggers(current)) == 3)
        self.assertEqual(spell.object_id, game.state.stack[0].card_object_id)
        self.assertEqual([manabarbs.object_id, manabarbs.object_id, burning.object_id],
                         [item.source_object_id for item in self.land_triggers(game)])
        self.assertEqual({"A"}, {item.context["player"] for item in self.land_triggers(game)})
        self.assertTrue(all(game.state.cards[card.object_id].tapped for card in (basic, dual)))
        self.assertFalse(any(game.state.players["A"].mana_pool.values()))
        game = self.replay(game, load=True)
        for _ in range(3):
            self.resolve_top(game)
        self.assertEqual((life_a - 3, life_b), (game.state.players["A"].life, game.state.players["B"].life))
        self.assertEqual("stack", game.state.cards[spell.object_id].zone)
        self.resolve_top(game)
        self.assertEqual(life_b - 3, game.state.players["B"].life)
        self.replay(game, load=True)

    def test_actual_automatic_payment_finishes_the_activation_before_placing_its_trigger(self):
        game = self.session(10612006)
        manabarbs, _ = self.sources(game)
        forest = self.add(game.engine, "Forest")
        dual = self.add(game.engine, "Tropical Island", seat="B")
        tapper = self.add(game.engine, "Icy Manipulator")
        self.assert_closed(game, tapper)
        self.prepare(game)
        self.checkpoint(game)
        life = game.state.players["A"].life
        self.activate(game, "A", tapper, targets=[dual.ref], pay="auto")
        self.until(game, lambda current: len(self.land_triggers(current)) == 1)
        self.assertEqual([tapper.object_id, manabarbs.object_id],
                         [item.source_object_id for item in game.state.stack])
        self.assertTrue(game.state.cards[forest.object_id].tapped)
        self.assertTrue(game.state.cards[tapper.object_id].tapped)
        self.assertFalse(game.state.cards[dual.object_id].tapped)
        self.resolve_top(game)
        self.assertEqual(life - 1, game.state.players["A"].life)
        self.assertFalse(game.state.cards[dual.object_id].tapped)
        self.resolve_top(game)
        self.assertTrue(game.state.cards[dual.object_id].tapped)
        self.assertFalse(self.land_triggers(game))
        self.replay(game, load=True)

    def test_actual_zero_land_mana_nonland_mana_and_ordinary_tap_do_not_trigger(self):
        game = self.session(10612003)
        self.sources(game)
        zero = self.add(game.engine, "Gaea's Cradle")
        artifact = self.add(game.engine, "Sol Ring")
        tapper = self.add(game.engine, "Icy Manipulator")
        forest = self.add(game.engine, "Forest")
        for card in (zero, artifact, tapper):
            self.assert_closed(game, card)
        self.prepare(game, mana={"C": 1})
        self.checkpoint(game)
        life = game.state.players["A"].life
        self.activate(game, "A", zero)
        self.assertTrue(game.state.cards[zero.object_id].tapped)
        self.assertEqual(0, game.state.players["A"].mana_pool["G"])
        self.assertFalse(self.land_triggers(game))
        self.activate(game, "A", artifact)
        self.assertEqual(3, game.state.players["A"].mana_pool["C"])
        self.assertFalse(self.land_triggers(game))
        self.activate(game, "A", tapper, targets=[forest.ref], pay="auto")
        self.assertIsNone(self.resolve(game))
        self.assertTrue(game.state.cards[forest.object_id].tapped)
        self.assertFalse(self.land_triggers(game))
        self.assertEqual(life, game.state.players["A"].life)
        self.replay(game, load=True)

    def test_actual_multiple_mana_units_create_only_one_occurrence_per_observer(self):
        game = self.session(10612004)
        self.sources(game)
        tomb = self.add(game.engine, "Ancient Tomb")
        self.assert_closed(game, tomb)
        self.prepare(game)
        self.checkpoint(game)
        life = game.state.players["A"].life
        self.activate(game, "A", tomb)
        self.assertEqual(2, game.state.players["A"].mana_pool["C"])
        self.assertEqual(2, len(self.land_triggers(game)))
        self.assertEqual(life - 2, game.state.players["A"].life)
        self.assertIsNone(self.resolve(game))
        self.assertEqual(life - 4, game.state.players["A"].life)
        self.replay(game, load=True)

    def test_versionless_checkpoint_keeps_the_historical_reversible_mana_action(self):
        game = self.session(10612005)
        self.sources(game)
        land = self.add(game.engine, "Forest")
        self.prepare(game)
        game.state.land_mana_tap_event_version = None
        self.checkpoint(game)
        self.activate(game, "A", land)
        self.assertFalse(self.land_triggers(game))
        self.assertEqual(1, game.state.players["A"].mana_pool["G"])
        game = self.replay(game, load=True)
        self.assertNotIn(LAND_MANA_TAP_VERSION_FIELD, game.state.to_dict())
        self.assertNotIn(LAND_MANA_TAP_VERSION_FIELD, format_state_versions(game.state))
        for malformed in (None, False, 2):
            with self.assertRaises(ValueError):
                GameState.from_dict({**game.state.to_dict(), LAND_MANA_TAP_VERSION_FIELD: malformed})
        undo, = (row["id"] for row in self.actions(game, "A")
                 if row.get("action") == "undo_mana" and row.get("source") == land.ref)
        result = game.act("pilot:A", {"action_id": undo})
        self.assertTrue(result.ok, result.summary)
        self.assertEqual(0, game.state.players["A"].mana_pool["G"])
        self.assertFalse(game.state.cards[land.object_id].tapped)
        self.replay(game, load=True)



class OrdinarySourceManaTapDamageTests(InheritedNativeFixture, unittest.TestCase):
    actions = OrdinaryLandManaTapDamageTests.actions
    activate = OrdinaryLandManaTapDamageTests.activate

    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "ordinary-source-mana-tap.sqlite3"
        fixtures = ROOT / "tests/fixtures"
        build_fixture_database([
            fixtures / "ordinary-land-mana-tap-damage-cards.json",
            fixtures / "ordinary-source-mana-tap-damage-cards.json",
            fixtures / "activation-result-views-cards.json",
            fixtures / "activation-paid-result-slot-cards.json",
            fixtures / "temporary-cast-restriction-cards.json",
        ], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        commander = "Isamaru, Hound of Konda"
        cls.deck = DeckDefinition("Source taps for mana", [
            DeckEntry(commander, 1, "commander"), DeckEntry("Plains", 30),
        ], [commander])

    def druid(self, game, **source_kwargs):
        source = self.add(game.engine, "Zhur-Taa Druid", **source_kwargs)
        source.acquired_control_turn_count = -1
        self.assert_closed(game, source)
        return source

    def source_triggers(self, game):
        return [item for item in game.state.stack if item.context.get("event") == SOURCE_MANA_TAP_EVENT]

    def test_actual_druid_printed_mana_stays_after_responding_to_its_separate_trigger(self):
        import json

        game = self.session(106_121_001)
        source = self.druid(game, ref="druid-paid")
        decoy = self.druid(game, ref="same-name-druid")
        stifle = self.add(game.engine, "Stifle", seat="B", zone="hand")
        self.assert_closed(game, stifle)
        game.state.config.semantic_policy = "trusted_only"
        self.prepare(game)
        game.state.players["B"].mana_pool["U"] = 1
        self.checkpoint(game)

        action_id = f"activate:{source.ref}:ab1"
        before = authoritative_state_hash(game.state)
        for actor, fields in (("B", {}), ("A", {"mana_output": {"R": 1}})):
            denied = game.act("pilot:" + actor, {"action_id": action_id, **fields})
            self.assertFalse(denied.ok, denied.summary)
            self.assertEqual(before, authoritative_state_hash(game.state))
            self.assertFalse(self.source_triggers(game))

        self.activate(game, "A", source, ability="ab1")
        self.assertEqual(1, game.state.players["A"].mana_pool["G"])
        self.assertEqual([40, 40, 40, 40], [game.state.players[seat].life for seat in "ABCD"])
        trigger, = self.source_triggers(game)
        self.assertEqual((source.object_id, "A", source.logical_object_id),
                         (trigger.source_object_id, trigger.controller, trigger.context["card_object_identity"]))
        self.assertIsNone(trigger.card_object_id)
        self.assertFalse(game.state.cards[decoy.object_id].tapped)
        self.assertNotIn(stifle.ref, json.dumps(game.packet("pilot:A", full=True)))
        self.assertFalse(any(row.get("action") == "undo_mana" for row in self.actions(game, "A")))
        game = self.replay(game, load=True)

        self.cast_for(game, "B", stifle, [trigger.ref])
        self.resolve_top(game)
        self.assertFalse(game.state.stack)
        self.assertEqual(1, game.state.players["A"].mana_pool["G"])
        self.assertEqual([40, 40, 40, 40], [game.state.players[seat].life for seat in "ABCD"])
        self.replay(game, load=True)

    def test_actual_druid_gained_mana_pays_a_spell_before_its_trigger_is_placed(self):
        game = self.session(106_121_002)
        source = self.druid(game)
        grant = self.add(game.engine, "Cryptolith Rite")
        spell = self.add(game.engine, "Silence", zone="hand")
        for card in (grant, spell):
            self.assert_closed(game, card)
        game.state.config.semantic_policy = "trusted_only"
        self.prepare(game)
        self.checkpoint(game)

        self.cast_for(game, "A", spell)
        paid, trigger = game.state.stack
        self.assertEqual(spell.object_id, paid.card_object_id)
        self.assertEqual((SOURCE_MANA_TAP_EVENT, source.object_id, "A"),
                         (trigger.context["event"], trigger.source_object_id, trigger.controller))
        self.assertTrue(game.state.cards[source.object_id].tapped)
        self.assertFalse(any(game.state.players["A"].mana_pool.values()))
        self.assertEqual([40, 40, 40, 40], [game.state.players[seat].life for seat in "ABCD"])
        game = self.replay(game, load=True)
        self.resolve_top(game)
        self.assertEqual([40, 39, 39, 39], [game.state.players[seat].life for seat in "ABCD"])
        self.assertEqual(spell.object_id, game.state.stack[0].card_object_id)
        self.resolve_top(game)
        self.assertEqual("graveyard", game.state.cards[spell.object_id].zone)
        self.replay(game, load=True)

    def test_actual_ordinary_tap_and_another_creatures_mana_leave_the_druid_idle(self):
        game = self.session(106_121_003)
        source = self.druid(game)
        unrelated = self.add(game.engine, "Llanowar Elves")
        unrelated.acquired_control_turn_count = -1
        tapper = self.add(game.engine, "Icy Manipulator")
        game.state.config.semantic_policy = "trusted_only"
        self.prepare(game)
        self.checkpoint(game)

        self.activate(game, "A", unrelated, ability="ab1")
        self.assertEqual(1, game.state.players["A"].mana_pool["G"])
        self.assertFalse(game.state.stack)
        self.activate(game, "A", tapper, targets=[source.ref], pay="auto")
        self.resolve_top(game)
        self.assertTrue(game.state.cards[source.object_id].tapped)
        self.assertFalse(self.source_triggers(game))
        self.assertEqual([40, 40, 40, 40], [game.state.players[seat].life for seat in "ABCD"])
        self.replay(game, load=True)

    def test_actual_druid_as_a_forest_shares_ordinary_placement_with_manabarbs(self):
        game = self.session(106_121_004)
        source = self.druid(game)
        ashaya = self.add(game.engine, "Ashaya, Soul of the Wild")
        manabarbs = self.add(game.engine, MANABARBS, seat="B")
        for card in (ashaya, manabarbs):
            self.assert_closed(game, card)
        game.state.config.semantic_policy = "trusted_only"
        self.prepare(game)
        self.checkpoint(game)

        self.activate(game, "A", source, ability="intrinsic_forest")
        self.assertEqual(1, game.state.players["A"].mana_pool["G"])
        self.assertEqual(
            [(source.object_id, SOURCE_MANA_TAP_EVENT), (manabarbs.object_id, LAND_MANA_TAP_EVENT)],
            [(item.source_object_id, item.context["event"]) for item in game.state.stack],
        )
        self.assertEqual([40, 40, 40, 40], [game.state.players[seat].life for seat in "ABCD"])
        self.resolve_top(game)
        self.assertEqual([39, 40, 40, 40], [game.state.players[seat].life for seat in "ABCD"])
        self.resolve_top(game)
        self.assertEqual([39, 39, 39, 39], [game.state.players[seat].life for seat in "ABCD"])
        self.replay(game, load=True)

    def test_druid_compiler_and_runtime_require_the_exact_actor_and_source_incarnation(self):
        import json

        from quorune.rules.capabilities import CapabilityRegistry

        card = self.db.lookup("Zhur-Taa Druid", fuzzy=False)
        options = {"capability_registry": self.registry, "capability_profile": "commander_review"}
        ir = compile_oracle_card(card, **options)
        self.assertEqual("exact", ir.status, ir.material_residuals)
        mana, trigger = ir.faces[0].nodes
        self.assertEqual(("mana_ability", "activate"), (mana.kind, mana.event))
        self.assertEqual(SOURCE_MANA_TAP_EVENT, trigger.event)
        self.assertIn(SOURCE_MANA_TAP_CAPABILITY, trigger.capability_dependencies)
        self.assertEqual(source_mana_tap_condition(), trigger.event_condition)
        self.assertEqual((source_mana_tap_damage_effect(),), tuple(trigger.effects))

        for changed in (
            card.oracle_text.replace("this creature", "a creature"),
            card.oracle_text.replace(" for mana", ""),
            card.oracle_text.replace("each opponent", "each player"),
            card.oracle_text.replace("1 damage", "2 damage"),
        ):
            with self.subTest(oracle=changed):
                mutated = compile_oracle_card(replace(card, oracle_text=changed), **options)
                self.assertFalse(any(SOURCE_MANA_TAP_CAPABILITY in node.capability_dependencies
                                     for face in mutated.faces for node in face.nodes))
        raw = json.loads((ROOT / "quorune/rules/capability-registry.json").read_bytes())
        capability = next(row for row in raw["capabilities"] if row["id"] == SOURCE_MANA_TAP_CAPABILITY)
        capability.update(status="blocked", blockers=["source tap observer withdrawn"])
        withdrawn = CapabilityRegistry(raw)
        withdrawn.mark_evidence_verified("0" * 64)
        blocked = compile_oracle_card(card, capability_registry=withdrawn, capability_profile="commander_review")
        self.assertEqual([True, False], [node.exact for node in blocked.faces[0].nodes])
        self.assertTrue(any(SOURCE_MANA_TAP_CAPABILITY in blocker
                            for residual in blocked.material_residuals for blocker in residual.blockers))

        result, semantics = register_card(self.db, self.registry, "Zhur-Taa Druid")
        self.assertTrue(result["strict_capability_ready"], result)
        program, = [value for value in semantics.programs_for_oracle(card.oracle_id)
                    if value.event == SOURCE_MANA_TAP_EVENT]
        self.assertTrue(bind_semantic_program_runtime(program, capability_registry=self.registry,
                                                      profile="commander_review")["strict"])
        for change in (
            {"event": "permanent.tap"},
            {"event_condition": {"all": [source_mana_tap_condition()["all"][1]]}},
            {"effects": [{**source_mana_tap_damage_effect(), "amount": True}]},
        ):
            with self.subTest(runtime=change):
                mutated = bind_semantic_program_runtime(replace(program, **change),
                    capability_registry=self.registry, profile="commander_review")
                self.assertFalse(mutated["strict"], mutated)

    def test_omitting_the_successful_mana_tap_dispatch_breaks_the_original_witness(self):
        from unittest.mock import patch

        with patch("quorune.mana_activation.queue_mana_tap_triggers", return_value=None) as omitted:
            with self.assertRaises(AssertionError):
                self.test_actual_druid_as_a_forest_shares_ordinary_placement_with_manabarbs()
        omitted.assert_called_once()
