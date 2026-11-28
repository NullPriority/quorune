"""Native optional Ward sacrifice and the original targeting spell."""

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
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import CapabilityRegistry, load_default_capability_registry
from quorune.rules.evidence import validate_capability_evidence_index
from quorune.rules.evidence_storage import read_evidence_document
from quorune.trigger_participation import WardSpec, WardSacrificeScope
from quorune.zone_transitions import ZoneTransitionOwner
from scripts.build_test_database import build_fixture_database
from scripts.update_capability_evidence import build_index, discover_tests


CAPABILITY = "trigger.keyword.ward.fixed_sacrifice"


class WardSacrificeTests(InheritedNativeFixture, unittest.TestCase):
    creature_ward = "Trial Oath Sentinel"
    value_ward = "Trial Astral Guard"
    creature = "Trial Small Witness"
    artifact = "Trial Copper Seal"
    zero = "Trial Empty Vessel"
    growth = "Trial Ward Mark"
    counter = "Trial Ward Interruption"
    basic = "Generic Bound Plains"
    creature_death_trigger = False

    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        database = Path(cls.temporary.name) / "ward-sacrifice.sqlite3"
        fixtures = ROOT / "tests/fixtures"
        build_fixture_database([
            fixtures / "bound-effect-program-cards.json",
            fixtures / "ward-sacrifice-cards.json",
            fixtures / "blight-cards.json",
        ], database)
        cls.db = CardDatabase(database)
        cls.registry = load_default_capability_registry()
        name = "Generic Bound Commander"
        cls.deck = DeckDefinition("Ward sacrifice", [
            DeckEntry(name, 1, "commander"), DeckEntry(cls.basic, 30),
        ], [name])

    def _begin(self, game, *programs):
        game.state.config.semantic_policy = "trusted_only"
        for card in programs:
            self.assert_closed(game, card)
        self.prepare(game, mana={"G": 1})
        self.checkpoint(game)

    def _target(self, game, ward, spell):
        self.cast(game, "A", spell, [ward.ref])
        instances = [item for item in game.state.stack if item.semantic_key == "builtin:ward"]
        self.assertEqual(1, len(instances))
        return instances[0].context["target_stack"]

    def _choice(self, game):
        pending = game.state.pending_decision
        self.assertEqual(("semantic.choice", ["A"]), (pending.kind, pending.actors))
        payload = pending.payload_by_actor["A"]
        self.assertEqual(("ward_counter_unless_pay", "sacrifice"), (payload["operation"], payload["payment_kind"]))
        offered, = (action for action in payload["legal_actions"] if action["id"] == "choose")
        return set(offered["choice_schema"]["legal_refs"])

    def _choose(self, game, cards, *, actor="A", expected=True):
        before = authoritative_state_hash(game.state)
        command = {"action_id": "choose", "cards": list(cards)}
        response = game.act("pilot:" + actor, command)
        self.assertEqual(expected, response.ok, response.summary)
        if not expected:
            self.assertEqual(before, authoritative_state_hash(game.state))

    def test_auntie_ool_ward_blight_pays_from_the_targeter_and_replays_the_departed_creature(self):
        game = self.session(702_210_68)
        engine = game.engine
        ward = self.add(engine, "Auntie Ool, Cursewretch", seat="B")
        cost = self.add(engine, "Trial Empty Vessel", seat="C")
        decoy = self.add(engine, self.creature, seat="D", ref="BLIGHT-PUBLIC-DECOY")
        hidden = self.add(engine, self.creature, seat="D", zone="hand", ref="BLIGHT-HIDDEN-DECOY")
        spell = self.add(engine, self.growth, zone="hand")
        engine.change_control(cost.object_id, "A", reason="The targeter controls the Blight creature")
        life = {seat: game.state.players[seat].life for seat in ("A", "B", "C")}
        self._begin(game, ward, spell)
        original_spell = self._target(game, ward, spell)
        self.assertIsNotNone(self.resolve(game))
        pending = game.state.pending_decision
        self.assertEqual(("semantic.choice", ["A"]), (pending.kind, pending.actors))
        payload = pending.payload_by_actor["A"]
        self.assertEqual(("ward_counter_unless_pay", "blight"), (payload["operation"], payload["payment_kind"]))
        offered, = (action for action in payload["legal_actions"] if action["id"] == "choose")
        self.assertEqual([cost.ref], list(offered["choice_schema"]["legal_refs"]))
        self.assertIsNone(game.packet("pilot:B", full=True)["decision"])
        self.assertNotIn(hidden.ref, json.dumps(game.packet("pilot:A", full=True)))
        self._choose(game, [cost.ref], actor="B", expected=False)
        self._choose(game, [decoy.ref], expected=False)

        declined = self.replay(game, load=True)
        self._choose(declined, [])
        self.assertNotIn(original_spell, [item.ref for item in declined.state.stack])
        self.assertEqual("battlefield", declined.state.cards[cost.object_id].zone)
        self.replay(declined)

        game = self.replay(game, load=True)
        self._choose(game, [cost.ref])
        self.assertIn(original_spell, [item.ref for item in game.state.stack])
        self.assertIn(cost.object_id, game.state.players["C"].zones["graveyard"])
        trigger, = (item for item in game.state.stack if item.context.get("event") == "counter.put")
        self.assertEqual((2, "-1/-1"), (trigger.context["amount"], trigger.context["counter"]))
        self.assertEqual("A", trigger.context["occurrence_controller_information_lki"]["information"]["controller"])
        game = self.replay(game, load=True)
        self.resolve_top(game)
        self.assertEqual(life["A"] - 1, game.state.players["A"].life)
        self.assertEqual(life["B"], game.state.players["B"].life)
        self.assertEqual(life["C"], game.state.players["C"].life)
        self.resolve_top(game)
        self.replay(game)

    def test_auntie_ool_counter_result_draws_once_for_its_current_controller(self):
        game = self.session(122_101_68)
        engine = game.engine
        auntie = self.add(engine, "Auntie Ool, Cursewretch", ref="BLIGHT-DRAW-AUNTIE")
        ward = self.add(engine, "Auntie Ool, Cursewretch", seat="B", ref="BLIGHT-OPPOSING-AUNTIE")
        cost = self.add(engine, "Generic Bound Body")
        spell = self.add(engine, self.growth, zone="hand")
        life = game.state.players["A"].life
        self._begin(game, auntie, ward, spell)
        self._target(game, ward, spell)
        self.assertIsNotNone(self.resolve(game))
        self._choose(game, [cost.ref])
        self.assertEqual(2, game.state.cards[cost.object_id].counters["-1/-1"])
        triggers = [item for item in game.state.stack if item.context.get("event") == "counter.put"]
        self.assertCountEqual(["A", "B"], [item.controller for item in triggers])
        visible = len(game.state.players["A"].zones["hand"])
        game = self.replay(game, load=True)
        self.resolve_top(game)
        self.resolve_top(game)
        self.assertEqual(visible + 1, len(game.state.players["A"].zones["hand"]))
        self.assertEqual(life - 1, game.state.players["A"].life)
        self.resolve_top(game)
        self.replay(game)

    def test_auntie_ool_requires_typed_blight_and_the_complete_counter_recipient_result(self):
        from quorune.compiler.ward_cost_templates import fixed_ward_spec
        from quorune.rules.counter_recipient_control import (
            COUNTER_RECIPIENT_CONTROL_MECHANICS, counter_recipient_control_node_capabilities,
            counter_recipient_control_instructions,
        )

        spec = fixed_ward_spec("Ward—Blight 2.")
        self.assertEqual({"schema_version": 4, "blight_amount": 2}, spec.to_dict())
        self.assertEqual(spec, WardSpec.from_dict(spec.to_dict()))
        for printed in ("Ward—Blight 0.", "Ward—Blight X.", "Ward—Blight 2 and pay 1 life."):
            self.assertIsNone(fixed_ward_spec(printed))
        for amount in (False, True, 0, -1, "2"):
            with self.subTest(amount=amount), self.assertRaises(ValueError):
                WardSpec.from_dict({"schema_version": 4, "blight_amount": amount})
        effects = counter_recipient_control_instructions()
        def capabilities(steps):
            return counter_recipient_control_node_capabilities(
                effects=steps, target_schema=None, mechanic_ids=COUNTER_RECIPIENT_CONTROL_MECHANICS,
            )

        self.assertTrue(capabilities(effects))
        self.assertFalse(capabilities(effects[:1]))
        self.assertFalse(capabilities((effects[0], {**effects[1], "recipient": "$controller"})))
        original = self.db.lookup("Auntie Ool, Cursewretch")
        self.assertEqual("exact", compile_oracle_card(original, capability_registry=self.registry).status)
        for owner in ("quorune.compiler.event_counter_results.counter_recipient_control_result",
                      "quorune.compiler.ability_keyword_fragments.fixed_ward_spec"):
            with self.subTest(owner=owner), patch(owner, return_value=None):
                self.assertNotEqual("exact", compile_oracle_card(original, capability_registry=self.registry).status)

    def test_auntie_ool_authority_withdrawal_keeps_both_printed_abilities_material(self):
        raw = json.loads((ROOT / "quorune/rules/capability-registry.json").read_bytes())
        authority = {"trigger.keyword.ward.fixed_blight", "trigger.counter_recipient.current_controller_branch"}
        for row in raw["capabilities"]:
            if row["id"] in authority:
                row.update(status="blocked", blockers=["The isolated witness withdraws this typed authority."])
        registry = CapabilityRegistry(raw)
        declarations = read_evidence_document(ROOT / "platform/capability-evidence-declarations.json")
        evidence = build_index(registry_value=raw, declaration_source=declarations,
                               discovered_test_ids=set(discover_tests(ROOT).values()))
        registry.mark_evidence_verified(validate_capability_evidence_index(evidence, registry=registry))
        original = self.db.lookup("Auntie Ool, Cursewretch")
        ir = compile_oracle_card(original, capability_registry=registry, capability_profile="commander_review")
        self.assertNotEqual("exact", ir.status)
        residuals = " ".join(str(row) for row in ir.material_residuals)
        self.assertIn("Blight 2", residuals)
        self.assertIn("its controller loses 1 life", residuals)

    def test_ward_sacrifice_accepts_a_current_controlled_creature_and_preserves_the_spell(self):
        game = self.session(702_210_41)
        engine = game.engine
        ward = self.add(engine, self.creature_ward, seat="B", ref="CREATURE-WARD")
        paid = self.add(engine, self.creature, seat="C", ref="BORROWED-COST")
        another = self.add(engine, self.zero, ref="OTHER-CREATURE")
        wrong_type = self.add(engine, self.artifact, ref="WRONG-TYPE")
        enemy = self.add(engine, self.creature, seat="D", ref="WRONG-CONTROLLER")
        secret = self.add(engine, self.creature, seat="D", zone="hand", ref="PRIVATE-NONCOST")
        spell = self.add(engine, self.growth, zone="hand", ref="TARGETING-SPELL")
        engine.change_control(paid.object_id, "A", reason="The payer controls the potential sacrifice")
        printed_power = engine._numeric_stat(ward.object_id, "power")
        starting_life = game.state.players["D"].life
        ward_life = game.state.players["B"].life
        self._begin(game, ward, spell)
        ref = self._target(game, ward, spell)
        self.assertIsNotNone(self.resolve(game))
        self.assertEqual({paid.ref, another.ref}, self._choice(game))
        self.assertNotIn(secret.ref, json.dumps(game.packet("pilot:A", full=True)))
        self.assertIsNone(game.packet("pilot:B", full=True)["decision"])
        for actor, choices in (("B", [paid.ref]), ("A", [wrong_type.ref]),
                               ("A", [enemy.ref]), ("A", [paid.ref, another.ref])):
            self._choose(game, choices, actor=actor, expected=False)
        game = self.replay(game, load=True)
        self._choose(game, [paid.ref])
        self.assertIn(ref, [item.ref for item in game.state.stack])
        self.assertEqual("graveyard", game.state.cards[paid.object_id].zone)
        self.assertIn(paid.object_id, game.state.players["C"].zones["graveyard"])
        pending = self.resolve(game)
        if self.creature_death_trigger:
            self.assertEqual(("semantic.target", ["B"]), (pending.kind, pending.actors))
            chosen = game.act("pilot:B", {"action_id": "choose", "targets": ["D"]})
            self.assertTrue(chosen.ok, chosen.summary)
            self.assertIsNone(self.resolve(game))
            self.assertEqual(starting_life - 2, game.state.players["D"].life)
            self.assertEqual(ward_life + 2, game.state.players["B"].life)
        else:
            self.assertIsNone(pending)
        self.assertEqual(printed_power + 3, game.engine._numeric_stat(ward.object_id, "power"))
        self.replay(game, load=True)

    def test_food_ward_requires_current_controlled_food_and_replays_paid_and_declined(self):
        for paid in (False, True):
            with self.subTest(paid=paid):
                game = self.session(702_210_49 + int(paid))
                engine = game.engine
                ward = self.add(engine, "Trial Food Keeper", seat="B", ref="FOOD-WARD")
                meal = self.add(engine, "Trial Cooked Meal", seat="C", ref="FOOD-MEAL")
                artisan = self.add(engine, "Trial Food Artisan", ref="FOOD-ARTISAN")
                other_artifact = self.add(engine, self.artifact, ref="FOOD-WRONG-ARTIFACT")
                other_creature = self.add(engine, self.creature, ref="FOOD-WRONG-CREATURE")
                opponent_food = self.add(engine, "Trial Cooked Meal", seat="D", ref="FOOD-OPPONENT")
                private = self.add(engine, "Trial Food Artisan", seat="D", zone="hand", ref="FOOD-PRIVATE")
                spell = self.add(engine, self.growth, zone="hand", ref="FOOD-TARGETING-SPELL")
                engine.change_control(meal.object_id, "A", reason="The payer controls the Food")
                initial_power = engine._numeric_stat(ward.object_id, "power")
                self._begin(game, ward, spell)
                ref = self._target(game, ward, spell)
                self.assertIsNotNone(self.resolve(game))
                self.assertEqual({meal.ref, artisan.ref}, self._choice(game))
                self.assertNotIn(private.ref, json.dumps(game.packet("pilot:A", full=True)))
                self.assertIsNone(game.packet("pilot:B", full=True)["decision"])
                for actor, refs in (
                    ("B", [meal.ref]), ("A", [other_artifact.ref]), ("A", [other_creature.ref]),
                    ("A", [opponent_food.ref]), ("A", [meal.ref, artisan.ref]),
                ):
                    self._choose(game, refs, actor=actor, expected=False)
                game = self.replay(game, load=True)
                self._choose(game, [meal.ref] if paid else [])
                self.assertEqual(paid, any(item.ref == ref for item in game.state.stack))
                self.assertIsNone(self.resolve(game))
                self.assertEqual("graveyard" if paid else "battlefield", game.state.cards[meal.object_id].zone)
                if paid:
                    self.assertIn(meal.object_id, game.state.players["C"].zones["graveyard"])
                self.assertEqual("battlefield", game.state.cards[artisan.object_id].zone)
                self.assertEqual("graveyard", game.state.cards[spell.object_id].zone)
                self.assertEqual(initial_power + (3 if paid else 0), game.engine._numeric_stat(ward.object_id, "power"))
                self.replay(game, load=True)

    def test_positive_mana_value_ward_offers_a_public_artifact_and_excludes_zero_value(self):
        game = self.session(702_210_42)
        engine = game.engine
        ward = self.add(engine, self.value_ward, seat="B", ref="VALUE-WARD")
        paid = self.add(engine, self.artifact, ref="POSITIVE-VALUE-COST")
        zero = self.add(engine, self.zero, ref="ZERO-VALUE-CREATURE")
        self.add(engine, self.basic, ref="ZERO-VALUE-LAND")
        spell = self.add(engine, self.growth, zone="hand", ref="VALUE-TARGETING-SPELL")
        printed = engine._numeric_stat(ward.object_id, "power")
        self._begin(game, ward, spell)
        self._target(game, ward, spell)
        self.assertIsNotNone(self.resolve(game))
        self.assertEqual({paid.ref}, self._choice(game))
        self._choose(game, [zero.ref], expected=False)
        game = self.replay(game, load=True)
        self._choose(game, [paid.ref])
        self.assertIsNone(self.resolve(game))
        self.assertEqual("graveyard", game.state.cards[paid.object_id].zone)
        self.assertEqual("battlefield", game.state.cards[zero.object_id].zone)
        self.assertEqual(printed + 3, game.engine._numeric_stat(ward.object_id, "power"))
        self.replay(game, load=True)

    def test_ward_sacrifice_rejects_a_changed_incarnation_and_decline_counters_only_the_spell(self):
        game = self.session(702_210_43)
        engine = game.engine
        ward = self.add(engine, self.value_ward, seat="B", ref="STALE-COST-WARD")
        moved = self.add(engine, self.artifact, ref="REENTERED-COST")
        spell = self.add(engine, self.growth, zone="hand", ref="DECLINED-SPELL")
        printed = engine._numeric_stat(ward.object_id, "power")
        self._begin(game, ward, spell)
        ref = self._target(game, ward, spell)
        self.assertIsNotNone(self.resolve(game))
        self.assertEqual({moved.ref}, self._choice(game))
        old_identity = moved.logical_object_id
        owner = ZoneTransitionOwner(engine)
        owner.move_card(moved.object_id, "exile")
        owner.move_card(moved.object_id, "battlefield")
        self.assertNotEqual(old_identity, moved.logical_object_id)
        self.checkpoint(game)
        game = self.replay(game, load=True)
        self._choose(game, [moved.ref], expected=False)
        self.assertIn(ref, [item.ref for item in game.state.stack])
        self._choose(game, [])
        self.assertIsNone(self.resolve(game))
        self.assertEqual("battlefield", game.state.cards[moved.object_id].zone)
        self.assertEqual("graveyard", game.state.cards[spell.object_id].zone)
        self.assertEqual(printed, game.engine._numeric_stat(ward.object_id, "power"))
        self.replay(game, load=True)

    def test_ward_sacrifice_does_not_request_payment_for_a_departed_targeting_spell(self):
        game = self.session(702_210_44)
        engine = game.engine
        ward = self.add(engine, self.creature_ward, seat="B", ref="ORPHANED-WARD")
        paid = self.add(engine, self.creature, ref="UNUSED-COST")
        spell = self.add(engine, self.growth, zone="hand", ref="COUNTERED-BEFORE-WARD")
        counter = self.add(engine, self.counter, seat="C", zone="hand", ref="COUNTER-TO-WARD")
        engine.state.players["C"].mana_pool["U"] = 2
        self._begin(game, ward, spell, counter)
        ref = self._target(game, ward, spell)
        self.cast(game, "C", counter, [ref])
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        self.assertEqual("battlefield", game.state.cards[paid.object_id].zone)
        self.assertEqual("graveyard", game.state.cards[spell.object_id].zone)
        self.replay(game, load=True)

    def test_ward_sacrifice_compilation_requires_its_typed_handler_and_verified_authority(self):
        def compile_card(card, registry):
            return compile_oracle_card(card, capability_registry=registry, capability_profile="commander_review")

        for name, scope in ((self.creature_ward, WardSacrificeScope.CREATURE),
                            (self.value_ward, WardSacrificeScope.PERMANENT_WITH_POSITIVE_MANA_VALUE),
                            ("Trial Food Keeper", WardSacrificeScope.FOOD_ARTIFACT)):
            card = self.db.lookup(name)
            ir = compile_card(card, self.registry)
            self.assertEqual("exact", ir.status, ir.material_residuals)
            fragment, = (handler for node in ir.faces[0].nodes for handler in node.handlers
                         if handler["handler_id"] == "ability.trigger.ward.fixed-sacrifice.v1")
            self.assertEqual(WardSpec(sacrifice_scope=scope), WardSpec.from_dict(fragment["fragment"]["value"]))
        raw = json.loads((ROOT / "quorune/rules/capability-registry.json").read_bytes())
        row = next(row for row in raw["capabilities"] if row["id"] == CAPABILITY)
        row.update(status="blocked", blockers=["Ward sacrifice authority withdrawn."])
        registry = CapabilityRegistry(raw)
        declarations = read_evidence_document(ROOT / "platform/capability-evidence-declarations.json")
        index = build_index(registry_value=raw, declaration_source=declarations,
                            discovered_test_ids=set(discover_tests(ROOT).values()))
        registry.mark_evidence_verified(validate_capability_evidence_index(index, registry=registry))
        card = self.db.lookup(self.creature_ward)
        self.assertNotEqual("exact", compile_card(card, registry).status)
        food = self.db.lookup("Trial Food Keeper")
        self.assertNotEqual("exact", compile_card(food, registry).status)
        from dataclasses import replace
        for cost in ("Sacrifice two Foods", "Sacrifice an untapped Food", "Sacrifice a Food or Treasure"):
            with self.subTest(cost=cost):
                malformed = replace(food, oracle_text=f"Ward—{cost}.")
                self.assertNotEqual("exact", compile_card(malformed, self.registry).status)
        with patch("quorune.compiler.ward_cost_templates.ward_sacrifice_scope", return_value=None):
            self.assertNotEqual("exact", compile_card(card, self.registry).status)
