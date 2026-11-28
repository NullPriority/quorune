"""Original land mana damage waits for ordinary priority even during payment."""

from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

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


    def orchard(self, game, *, seat="A"):
        source = self.add(game.engine, "Forbidden Orchard", seat=seat)
        self.assert_closed(game, source)
        game.state.config.semantic_policy = "trusted_only"
        return source

    def orchard_recipient(self, game, target):
        self.assertEqual("semantic.target", game.state.pending_decision.kind)
        previous = authoritative_state_hash(game.state)
        decisions = (("D", [target]), ("A", ["A"]), ("A", ["B", "C"]))
        for actor, targets in decisions:
            result = game.act("pilot:" + actor, {"action_id": "choose", "targets": targets})
            self.assertFalse(result.ok, result.summary)
            self.assertEqual(previous, authoritative_state_hash(game.state))
        self.target(game, "A", [target])
        trigger, = self.source_triggers(game)
        self.assertEqual([target], trigger.targets)
        return trigger

    def test_orchard_paid_mana_precedes_its_separate_target_and_selected_token(self):
        import json

        game = self.session(106_121_021)
        source = self.orchard(game)
        other = self.add(game.engine, "Forest")
        hidden = self.add(game.engine, "Stifle", seat="D", zone="hand", ref="orchard-hidden")
        self.prepare(game)
        self.checkpoint(game)
        self.activate(game, "A", other, ability="intrinsic_forest")
        self.assertFalse(self.source_triggers(game))
        self.activate(game, "A", source, ability="ab1", mana_output={"U": 1})
        self.assertEqual((1, 1), tuple(game.state.players["A"].mana_pool[color] for color in "GU"))
        self.assertTrue(game.state.cards[source.object_id].tapped)
        self.assertFalse(any(permanent.is_token for permanent in game.state.cards.values()))
        self.assertNotIn(hidden.ref, json.dumps(game.packet("pilot:A", full=True)))
        game = self.replay(game, load=True)
        trigger = self.orchard_recipient(game, "C")
        self.assertEqual((source.object_id, "A", source.logical_object_id),
                         (trigger.source_object_id, trigger.controller, trigger.context["card_object_identity"]))
        game = self.replay(game, load=True)
        self.resolve_top(game)
        token, = (permanent for permanent in game.state.cards.values() if permanent.is_token)
        self.assertEqual(("C", "C", "battlefield", "Spirit Token"),
                         (token.owner, token.controller, token.zone, token.printed_name))
        characteristics = game.engine._effective_card_data(token)
        self.assertEqual(([], "1", "1"),
                         (characteristics["colors"], str(characteristics["power"]), str(characteristics["toughness"])))
        self.assertTrue(characteristics["type_line"].endswith("Spirit"))
        self.assertEqual(1, game.state.players["A"].mana_pool["U"])
        self.replay(game, load=True)

    def test_orchard_automatic_payment_queues_the_counterable_trigger_above_the_paid_spell(self):
        game = self.session(106_121_023)
        source = self.orchard(game)
        paid = self.add(game.engine, "Silence", zone="hand")
        answer = self.add(game.engine, "Stifle", seat="B", zone="hand")
        for card in (paid, answer):
            self.assert_closed(game, card)
        self.prepare(game)
        game.state.players["B"].mana_pool["U"] = 1
        self.checkpoint(game)
        self.cast_for(game, "A", paid)
        self.assertTrue(game.state.cards[source.object_id].tapped)
        self.assertFalse(any(game.state.players["A"].mana_pool.values()))
        self.assertEqual(paid.object_id, game.state.stack[0].card_object_id)
        trigger = self.orchard_recipient(game, "B")
        self.assertEqual(trigger.ref, game.state.stack[-1].ref)
        game = self.replay(game, load=True)
        self.cast_for(game, "B", answer, [trigger.ref])
        self.resolve_top(game)
        self.assertEqual([paid.object_id], [item.card_object_id for item in game.state.stack])
        self.assertFalse(any(permanent.is_token for permanent in game.state.cards.values()))
        self.resolve_top(game)
        self.replay(game, load=True)

    def test_orchard_ordinary_tap_does_not_produce_the_token_trigger(self):
        game = self.session(106_121_024)
        source = self.orchard(game)
        self.add(game.engine, "Forest")
        tapper = self.add(game.engine, "Icy Manipulator")
        self.assert_closed(game, tapper)
        self.prepare(game)
        self.checkpoint(game)
        self.activate(game, "A", tapper, targets=[source.ref], pay="auto")
        self.resolve_top(game)
        self.assertTrue(game.state.cards[source.object_id].tapped)
        self.assertFalse(self.source_triggers(game))
        self.assertFalse(any(permanent.is_token for permanent in game.state.cards.values()))
        self.replay(game, load=True)

    def test_orchard_compiler_and_runtime_require_the_exact_source_and_opponent_target(self):
        from unittest.mock import patch
        from quorune.triggered_mana_model import SOURCE_MANA_TAP_TOKEN_CAPABILITY

        card = self.db.lookup("Forbidden Orchard", fuzzy=False)
        profile = "commander_review"
        binding, semantics = register_card(self.db, self.registry, card.name)
        self.assertTrue(binding["strict_capability_ready"], binding["blockers"])
        trigger, = (program for program in semantics.programs_for_oracle(card.oracle_id)
                    if program.event == SOURCE_MANA_TAP_EVENT)
        self.assertEqual(source_mana_tap_condition(), trigger.event_condition)
        self.assertEqual("opponent", trigger.target_schema["player_relation"])
        self.assertIn(SOURCE_MANA_TAP_TOKEN_CAPABILITY, trigger.capability_dependencies)
        stripped = tuple(dependency for dependency in trigger.capability_dependencies
                         if dependency != SOURCE_MANA_TAP_TOKEN_CAPABILITY)
        for changed in (
            replace(trigger, event_condition={"field": "player", "op": "eq", "value": "$source.controller"}),
            replace(trigger, target_schema={**trigger.target_schema, "player_relation": "any"}),
            replace(trigger, effects=[{**trigger.effects[0], "quantity": True}]),
            replace(trigger, capability_dependencies=stripped,
                    capability_closure=self.registry.closure(stripped, profile=profile).to_dict()),
        ):
            with self.subTest(changed=changed):
                self.assertFalse(bind_semantic_program_runtime(
                    changed, capability_registry=self.registry, profile=profile,
                )["strict"])
        for changed in (
            card.oracle_text.replace("target opponent", "target player"),
            card.oracle_text.replace("1/1", "2/2"),
            card.oracle_text.replace("for mana", "during your turn"),
        ):
            with self.subTest(changed=changed):
                self.assertNotEqual("exact", compile_oracle_card(
                    replace(card, oracle_text=changed), capability_registry=self.registry,
                ).status)
        with patch("quorune.compiler.fixed_public_action_event_bindings._source_mana_tap_token_spec",
                   return_value=None):
            self.assertNotEqual("exact", compile_oracle_card(card, capability_registry=self.registry).status)

    def test_orchard_requires_original_mana_tap_and_exact_target_token_result(self):
        from quorune.fixed_token_production import fixed_token_player_target_schema
        from quorune.triggered_mana_model import SOURCE_MANA_TAP_TARGET_TOKEN_CAPABILITY, source_mana_tap_spirit_effect

        name = "Forbidden Orchard"
        card = self.db.lookup(name, fuzzy=False)
        options = {"capability_registry": self.registry, "capability_profile": "commander_review"}
        ir = compile_oracle_card(card, **options)
        self.assertEqual("exact", ir.status, ir.material_residuals)
        mana, trigger = ir.faces[0].nodes
        self.assertEqual(("mana_ability", "activate"), (mana.kind, mana.event))
        self.assertEqual(SOURCE_MANA_TAP_EVENT, trigger.event)
        self.assertIn(SOURCE_MANA_TAP_TARGET_TOKEN_CAPABILITY, trigger.capability_dependencies)
        self.assertEqual(source_mana_tap_condition(), trigger.event_condition)
        self.assertEqual(fixed_token_player_target_schema("opponent"), trigger.target_schema)
        self.assertEqual((source_mana_tap_spirit_effect(),), tuple(trigger.effects))
        admitted, semantics = register_card(self.db, self.registry, name)
        self.assertTrue(admitted["strict_capability_ready"], admitted["blockers"])
        runtime, = semantics.programs_for_oracle(card.oracle_id, event=SOURCE_MANA_TAP_EVENT)
        invalid = replace(runtime, target_schema=fixed_token_player_target_schema("any"))
        self.assertFalse(bind_semantic_program_runtime(
            invalid, capability_registry=self.registry, profile="commander_review",
        )["strict"])
        for text in (
            card.oracle_text.replace("for mana", "during your turn"),
            card.oracle_text.replace("target opponent", "target player"),
            card.oracle_text.replace("a 1/1 colorless Spirit", "a 2/2 white Spirit"),
        ):
            self.assertNotEqual("exact", compile_oracle_card(replace(card, oracle_text=text), **options).status)
        with patch("quorune.compiler.fixed_public_action_event_bindings._source_mana_tap_target_token_spec", return_value=None):
            self.assertNotEqual("exact", compile_oracle_card(card, **options).status)

    def orchard(self, game):
        land = self.add(game.engine, "Forbidden Orchard")
        self.assert_closed(game, land)
        game.state.config.semantic_policy = "trusted_only"
        return land

    def choose_orchard_opponent(self, game, opponent):
        decision = game.state.pending_decision
        self.assertEqual(("semantic.target", ["A"]), (decision.kind, decision.actors))
        before = authoritative_state_hash(game.state)
        action = {"action_id": "choose", "targets": [opponent]}
        self.assertFalse(game.act("pilot:B", action).ok)
        self.assertFalse(game.act("pilot:A", {"action_id": "choose", "targets": ["A"]}).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        result = game.act("pilot:A", action)
        self.assertTrue(result.ok, result.summary)

    def assert_spirit(self, game, opponent):
        token, = (card for card in game.state.cards.values() if card.is_token and card.zone == "battlefield")
        self.assertEqual((opponent, opponent), (token.owner, token.controller))
        data = game.engine._effective_card_data(token)
        self.assertEqual("Creature — Spirit", data["type_line"])
        self.assertEqual([], list(data["colors"]))
        self.assertEqual((1, 1), tuple(game.engine._numeric_stat(token.object_id, stat)
                                       for stat in ("power", "toughness")))

    def test_orchard_explicit_mana_leaves_one_ordinary_opponent_target_and_replays(self):
        game = self.session(106_121_006)
        land = self.orchard(game)
        self.prepare(game, mana={})
        self.checkpoint(game)
        self.activate(game, "A", land, ability="ab1", mana_output={"U": 1})
        self.assertEqual(1, game.state.players["A"].mana_pool["U"])
        self.assertTrue(game.state.cards[land.object_id].tapped)
        self.assertFalse(any(card.is_token for card in game.state.cards.values()))
        game = self.replay(game, load=True)
        self.choose_orchard_opponent(game, "C")
        trigger, = self.source_triggers(game)
        self.assertEqual((land.object_id, ["C"]), (trigger.source_object_id, trigger.targets))
        self.resolve_top(game)
        self.assert_spirit(game, "C")
        self.replay(game, load=True)

    def test_orchard_payment_finishes_spell_then_places_target_and_other_trigger_apnap(self):
        game = self.session(106_121_007)
        land = self.orchard(game)
        manabarbs = self.add(game.engine, MANABARBS, seat="B")
        spell = self.add(game.engine, "Silence", zone="hand")
        for card in (manabarbs, spell):
            self.assert_closed(game, card)
        self.prepare(game, mana={})
        self.checkpoint(game)
        self.cast_for(game, "A", spell)
        self.assertEqual("stack", game.state.cards[spell.object_id].zone)
        self.assertEqual(spell.object_id, game.state.stack[0].card_object_id)
        self.assertTrue(game.state.cards[land.object_id].tapped)
        self.assertFalse(any(game.state.players["A"].mana_pool.values()))
        self.assertFalse(any(card.is_token for card in game.state.cards.values()))
        self.choose_orchard_opponent(game, "D")
        paid, orchard, other = game.state.stack
        self.assertEqual((spell.object_id, land.object_id, manabarbs.object_id),
                         (paid.card_object_id, orchard.source_object_id, other.source_object_id))
        self.assertEqual((SOURCE_MANA_TAP_EVENT, LAND_MANA_TAP_EVENT),
                         (orchard.context["event"], other.context["event"]))
        game = self.replay(game, load=True)
        self.resolve_top(game)
        self.assertEqual(39, game.state.players["A"].life)
        self.assertFalse(any(card.is_token for card in game.state.cards.values()))
        self.resolve_top(game)
        self.assert_spirit(game, "D")
        self.resolve_top(game)
        self.assertEqual("graveyard", game.state.cards[spell.object_id].zone)
        self.replay(game, load=True)

    def test_orchard_an_ordinary_tap_creates_no_trigger_and_replays(self):
        game = self.session(106_121_008)
        land = self.orchard(game)
        tapper = self.add(game.engine, "Icy Manipulator")
        self.assert_closed(game, tapper)
        self.prepare(game, mana={"C": 1})
        self.checkpoint(game)
        self.activate(game, "A", tapper, targets=[land.ref], pay="auto")
        self.resolve_top(game)
        self.assertTrue(game.state.cards[land.object_id].tapped)
        self.assertFalse(self.source_triggers(game))
        self.assertFalse(any(card.is_token for card in game.state.cards.values()))
        self.replay(game, load=True)

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


from optional_result_followup_fixture import OptionalResultFollowupFixture


NABBER = "Treasure Nabber"


class ArtifactManaTapControlNativeTests(InheritedNativeFixture, unittest.TestCase):
    actions = OrdinaryLandManaTapDamageTests.actions
    activate = OrdinaryLandManaTapDamageTests.activate
    finish_turn = OptionalResultFollowupFixture.finish_turn

    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        folder = Path(cls.temporary.name)
        fixture_root = ROOT / "tests" / "fixtures"
        support = {
            "ordinary-source-mana-tap-damage-cards.json": {NABBER},
            "ordinary-land-mana-tap-damage-cards.json": {"Plains", "Sol Ring", "Icy Manipulator", "Isamaru, Hound of Konda"},
            "destroy-original-controller-tokens-cards.json": {"Leaden Myr"},
            "activation-restriction-material-cards.json": {"Cloudshift"},
        }
        files = []
        for filename, names in support.items():
            doc = json.loads((fixture_root / filename).read_bytes())
            doc["cards"] = [row for row in doc["cards"] if row["name"] in names]
            assert names == {row["name"] for row in doc["cards"]}, filename
            ids = {row["oracle_id"] for row in doc["cards"]}
            doc["rulings"] = [row for row in doc.get("rulings", ()) if row["oracle_id"] in ids]
            part = folder / filename
            part.write_text(json.dumps(doc), encoding="utf-8")
            files.append(part)
        path = folder / "artifact-mana-tap-control.sqlite3"
        build_fixture_database(files, path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Opposing artifact mana control", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"), DeckEntry("Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def nabber(self, game, seat):
        card = self.add(game.engine, NABBER, seat=seat, ref=f"nabber-{seat}")
        self.assert_closed(game, card)
        return card

    @staticmethod
    def triggers(game):
        from quorune.rules.artifact_mana_tap_control import ARTIFACT_TAP_FACT

        return [item for item in game.state.stack if item.context.get(ARTIFACT_TAP_FACT) is True]

    @staticmethod
    def grants(game):
        from quorune.rules.artifact_mana_tap_control import DURATION

        return [effect for effect in game.state.continuous_effects or () if effect.duration is DURATION]

    def test_overlapping_turns_restore_older_control_at_each_acquiring_controllers_cleanup(self):
        game = self.session(10612961)
        self.nabber(game, "B")
        self.nabber(game, "C")
        ring = self.add(game.engine, "Sol Ring", ref="overlapping-original-ring")
        private = self.add(game.engine, "Plains", seat="D", zone="hand", ref="nabber-d-private")
        self.assert_closed(game, ring)
        self.prepare(game)
        self.checkpoint(game)
        self.activate(game, "A", ring)
        self.assertEqual(2, game.state.players["A"].mana_pool["C"])
        triggers = self.triggers(game)
        self.assertEqual(["B", "C"], [item.controller for item in triggers])
        self.assertTrue(all(not item.targets and item.context["player"] == "A" for item in triggers))
        self.resolve_top(game)
        self.assertEqual("C", game.state.cards[ring.object_id].controller)
        self.assertTrue(game.state.cards[ring.object_id].tapped)
        self.resolve_top(game)
        self.assertEqual("B", game.state.cards[ring.object_id].controller)
        self.assertTrue(game.state.cards[ring.object_id].tapped)
        self.assertEqual({"B", "C"}, {effect.operations[0].value for effect in self.grants(game)})
        self.assertNotIn(private.ref, json.dumps(game.packet("pilot:B", full=True)))
        game = self.replay(game, load=True)
        self.finish_turn(game)
        self.assertEqual(("B", "B"), (game.state.active_player, game.state.cards[ring.object_id].controller))
        self.until(game, lambda g: g.state.step == "end_step")
        self.assertEqual("B", game.state.cards[ring.object_id].controller)
        self.finish_turn(game)
        self.assertEqual(("C", "C"), (game.state.active_player, game.state.cards[ring.object_id].controller))
        self.assertEqual(["C"], [effect.operations[0].value for effect in self.grants(game)])
        self.finish_turn(game)
        self.assertEqual(("D", "A"), (game.state.active_player, game.state.cards[ring.object_id].controller))
        self.assertFalse(self.grants(game))
        self.replay(game, load=True)

    def test_current_turn_control_survives_cleanup_and_nonmana_taps_stay_unobserved(self):
        from quorune.continuous_effect_model import ContinuousEffect

        game = self.session(10612962)
        self.nabber(game, "A")
        ring = self.add(game.engine, "Sol Ring", seat="B", ref="nabber-opponent-ring")
        tapper = self.add(game.engine, "Icy Manipulator", seat="B", ref="nabber-nonmana-tapper")
        land = self.add(game.engine, "Plains", ref="nabber-nonmana-target")
        game.state.players["B"].mana_pool["C"] = 1
        self.assert_closed(game, tapper)
        self.prepare(game)
        self.checkpoint(game)
        self.activate(game, "B", tapper, targets=[land.ref], pay="manual", payment={"C": 1})
        self.assertFalse(self.triggers(game))
        self.assertIsNone(self.resolve(game))
        self.assertTrue(game.state.cards[tapper.object_id].tapped)
        self.assertTrue(game.state.cards[land.object_id].tapped)
        self.assertFalse(self.triggers(game))
        self.activate(game, "B", ring)
        self.assertEqual(2, game.state.players["B"].mana_pool["C"])
        trigger, = self.triggers(game)
        self.assertEqual(("A", "B", []), (trigger.controller, trigger.context["player"], trigger.targets))
        self.assertIsNone(self.resolve(game))
        effect, = self.grants(game)
        self.assertEqual(game.state.players["A"].turns_begun + 1, effect.controller_cleanup_turn)
        self.assertEqual(effect, ContinuousEffect.from_dict(effect.to_dict()))
        for payload in ({**effect.to_dict(), "controller_cleanup_turn": True},
                        {**effect.to_dict(), "duration": "until_end_of_turn"}):
            with self.assertRaises(ValueError):
                ContinuousEffect.from_dict(payload)
        self.finish_turn(game)
        self.assertEqual(("B", "A"), (game.state.active_player, game.state.cards[ring.object_id].controller))
        self.assertEqual([effect], self.grants(game))
        self.replay(game, load=True)

    def test_blinked_artifact_does_not_receive_the_old_mana_tap_control(self):
        game = self.session(10612963)
        self.nabber(game, "A")
        source = self.add(game.engine, "Leaden Myr", seat="B", ref="nabber-blink-artifact")
        source.acquired_control_turn_count = -1
        blink = self.add(game.engine, "Cloudshift", seat="B", zone="hand")
        game.state.players["B"].mana_pool["W"] = 1
        for card in (source, blink):
            self.assert_closed(game, card)
        self.prepare(game)
        self.checkpoint(game)
        self.activate(game, "B", source)
        trigger, = self.triggers(game)
        original = game.state.cards[source.object_id].logical_object_id
        self.assertEqual(original, trigger.context["card_object_identity"])
        self.cast_for(game, "B", blink, [source.ref])
        self.resolve_top(game)
        self.assertNotEqual(original, game.state.cards[source.object_id].logical_object_id)
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        self.assertEqual("B", game.state.cards[source.object_id].controller)
        self.assertFalse(self.grants(game))
        self.replay(game, load=True)

    def test_sacrificed_treasure_still_has_the_mana_tap_occurrence_without_a_control_result(self):
        from quorune.compiler.token_templates import fixed_token_creation_effect_template

        game = self.session(10612964)
        self.nabber(game, "A")
        definition = fixed_token_creation_effect_template("Create a Treasure token.").effect
        ref, = game.engine.create_token("B", name=definition["name"], quantity=1, characteristics=definition["characteristics"])
        token = next(card for card in game.state.cards.values() if card.ref == ref)
        self.prepare(game)
        self.checkpoint(game)
        self.activate(game, "B", token, mana_output={"G": 1}, pay="auto")
        self.assertEqual(1, game.state.players["B"].mana_pool["G"])
        self.assertFalse(any(card.ref == ref and card.zone == "battlefield" for card in game.state.cards.values()))
        trigger, = self.triggers(game)
        self.assertEqual(("A", ref, token.object_id), (trigger.controller, trigger.context["card"], trigger.context["mana_source_object_id"]))
        self.assertTrue(trigger.context["artifact_mana_tap_only"])
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        self.assertFalse(self.grants(game))
        self.replay(game, load=True)

    def test_exact_artifact_activation_scope_and_cleanup_receipt_require_independent_authority(self):
        from quorune.rules.artifact_mana_tap_control import CLEANUP_CONTROL_CAPABILITY, EVENT_CAPABILITY, FIELD

        card = self.db.lookup(NABBER, fuzzy=False)
        oracle = compile_oracle_card(card, capability_registry=self.registry, capability_profile="commander_review")
        self.assertEqual("exact", oracle.status, oracle.material_residuals)
        node, = oracle.faces[0].nodes
        self.assertIsNone(node.target_schema)
        self.assertEqual((0, len(card.oracle_text)), (node.span.start, node.span.end))
        for old, new in (("an opponent", "a player"), ("an artifact", "a spell"), ("for mana", "during combat"),
                         ("the end of your next turn", "your next turn")):
            altered = compile_oracle_card(replace(card, oracle_text=card.oracle_text.replace(old, new)), capability_registry=self.registry)
            self.assertNotEqual("exact", altered.status)
        game = self.session(10612965)
        source = self.nabber(game, "A")
        program, = game.engine.semantics.programs_for_oracle(source.oracle_id)
        for cap in (EVENT_CAPABILITY, CLEANUP_CONTROL_CAPABILITY):
            deps = tuple(value for value in program.capability_dependencies if value != cap)
            mutated = replace(program, capability_dependencies=deps,
                              capability_closure=self.registry.closure(deps, profile="commander_review").to_dict())
            binding = bind_semantic_program_runtime(mutated, capability_registry=self.registry, profile="commander_review")
            self.assertFalse(binding["strict"])
            self.assertIn(f"capability:undeclared_runtime_dependency:{cap}", binding["blockers"])
        effect, = program.effects
        malformed = replace(program, effects=({**effect, FIELD: True},))
        self.assertFalse(bind_semantic_program_runtime(malformed, capability_registry=self.registry, profile="commander_review")["strict"])

    def test_arbiter_cannot_supply_reserved_artifact_tap_control(self):
        from quorune.errors import GameRuleError
        from quorune.rules.artifact_mana_tap_control import DURATION, FIELD
        from quorune.stack_resolution import arbiter_resolution_effects

        reserved = {"op": "gain_control", "controller": "A", "card": "P0001", "duration": DURATION.value,
                    FIELD: {"object_id": "original", "logical_object_id": "original@1"}}
        nested = {"op": "offer_optional_effect", "player": "A", "effects": [reserved]}
        for effect in (reserved, nested):
            with self.subTest(op=effect["op"]):
                with self.assertRaisesRegex(GameRuleError, "requires a bound card program"):
                    arbiter_resolution_effects([effect])
        ordinary = {"op": "gain_control", "controller": "A", "card": "P0001", "duration": "until_end_of_turn"}
        self.assertEqual([ordinary], arbiter_resolution_effects([ordinary]))
