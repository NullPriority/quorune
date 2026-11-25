"""Committed creature combat damage transfers the original land to its dealer's controller."""

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
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.recipient_control_shapes import DAMAGE_DEALER_CONTROL_CAPABILITY, damage_dealer_control_condition
from quorune.target_controller_reference import damage_dealer_controller_reference
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


NAME = "Contested War Zone"
CAPABILITY = DAMAGE_DEALER_CONTROL_CAPABILITY
PROFILE = "commander_review"
FIXTURES = ROOT / "tests/fixtures"


class CombatDamageDealerRecipientControlTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        folder = Path(cls.temporary.name)
        base = json.loads((FIXTURES / "bound-effect-program-cards.json").read_bytes())
        base["cards"] = [row for row in base["cards"] if row["name"] == "Generic Bound Plains"]
        base["rulings"] = []
        basic = folder / "base.json"
        basic.write_text(json.dumps(base), encoding="utf-8")
        path = folder / "combat-dealer-recipient-control.sqlite3"
        build_fixture_database([basic, FIXTURES / "combat-damage-dealer-recipient-control-cards.json"], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Committed damage dealer control", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"), DeckEntry("Generic Bound Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def program(self):
        card = self.db.lookup(NAME, fuzzy=False)
        result, semantics = register_card(self.db, self.registry, NAME)
        self.assertTrue(result["strict_capability_ready"], result["blockers"])
        programs = semantics.programs_for_oracle(card.oracle_id)
        dealer, = [item for item in programs if CAPABILITY in item.capability_dependencies]
        return card, programs, dealer

    def test_complete_original_keeps_both_activations_and_exact_damage_subject(self):
        record, programs, dealer = self.program()
        self.assertEqual(2, sum(item.event == "activate" for item in programs))
        self.assertEqual(damage_dealer_control_condition(), dealer.event_condition)
        payload, = dealer.effects
        self.assertEqual(4, payload["schema_version"])
        self.assertEqual("$source.zone_object", payload["card"])
        self.assertEqual(damage_dealer_controller_reference(), payload["controller"])
        for text in (
            record.oracle_text.replace("combat damage", "damage"),
            record.oracle_text.replace("to you", "to an opponent"),
            record.oracle_text.replace("this land", "target land"),
        ):
            ir = compile_oracle_card(replace(record, oracle_text=text), capability_registry=self.registry,
                                     capability_profile=PROFILE)
            self.assertFalse(any(CAPABILITY in node.capability_dependencies
                                 for face in ir.faces for node in face.nodes))

    def test_raw_damage_control_requires_its_recipient_subject_and_scope_authority(self):
        _, _, program = self.program()
        effect, = program.effects
        caps = [value for value in program.capability_dependencies if value != CAPABILITY]
        missing = replace(program, capability_dependencies=caps,
                          capability_closure=self.registry.closure(caps, profile=PROFILE).to_dict())
        result = bind_semantic_program_runtime(missing, capability_registry=self.registry, profile=PROFILE)
        self.assertFalse(result["strict"], result)
        self.assertIn("capability:undeclared_runtime_dependency:" + CAPABILITY, result["blockers"])
        for bad in (
            replace(program, event_condition={"field": "combat", "op": "truthy", "value": True}),
            replace(program, effects=({**effect, "controller": "$controller"},)),
            replace(program, effects=({**effect, "card": "$context.source"},)),
        ):
            binding = bind_semantic_program_runtime(bad, capability_registry=self.registry, profile=PROFILE)
            self.assertFalse(binding["strict"], binding)

    def activate_current(self, game, seat, source, *, mana_ability=False, targets=()):
        self.priority(game, seat)
        current = game.state.cards[source.object_id]
        ability, = [item for item in game.engine._activated_abilities(current) if item.mana_ability is mana_ability]
        key = f"activate:{current.ref}:{ability.ability_id}"
        rows = game.packet(f"pilot:{seat}", full=True)["decision"]["ctx"]["legal"]["actions"]
        self.assertIn(key, {row["id"] for row in rows})
        command = {"action_id": key, "pay": "auto", "targets": list(targets)}
        if mana_ability:
            command["mana_output"] = {"C": 1}
        accepted = game.act(f"pilot:{seat}", command)
        self.assertTrue(accepted.ok, accepted.summary)

    def combat(self, game, attackers):
        self.until(game, lambda current: current.state.pending_decision.kind == "combat.attackers")
        accepted = game.act("pilot:B", {"action_id": "attack", "attackers": {card.ref: "A" for card in attackers}})
        self.assertTrue(accepted.ok, accepted.summary)
        self.until(game, lambda current: current.state.pending_decision.kind == "combat.blockers")
        accepted = game.act("pilot:A", {"action_id": "block", "blocks": {}})
        self.assertTrue(accepted.ok, accepted.summary)

    def test_actual_independent_damage_triggers_keep_order_current_dealer_and_departure_controller(self):
        game = self.session(120460414)
        engine = game.engine
        land = self.add(engine, NAME)
        self.add(engine, "Grizzly Bears", ref="war-zone-available-blocker")
        live = self.add(engine, "Grizzly Bears", seat="B", ref="war-zone-current-dealer")
        departing = self.add(engine, "Grizzly Bears", seat="B", ref="war-zone-departed-dealer")
        caster = self.add(engine, "Prodigal Sorcerer", seat="B")
        for card in (live, departing, caster):
            card.acquired_control_turn_count = -1
        self.add(engine, "Island")
        control = self.add(engine, "Word of Seizing", seat="C", zone="hand")
        bounce = self.add(engine, "Boomerang", seat="D", zone="hand")
        private = self.add(engine, "Counterspell", seat="C", zone="hand", ref="war-zone-private")
        for index in range(5):
            self.add(engine, "Mountain", seat="C", ref=f"war-zone-control-mountain-{index}")
        for index in range(2):
            self.add(engine, "Island", seat="D", ref=f"war-zone-bounce-island-{index}")
        for card in (land, caster, control, bounce):
            self.assert_closed(game, card)
        self.prepare(game, actor="B", active="B")
        self.checkpoint(game)

        self.activate_current(game, "B", caster, targets=["A"])
        self.assertIsNone(self.resolve(game))
        self.assertEqual(39, game.state.players["A"].life)
        self.assertEqual("A", game.state.cards[land.object_id].controller)
        self.combat(game, (live, departing))
        self.activate_current(game, "A", land)
        self.resolve_top(game)
        for attacker in (live, departing):
            self.assertEqual(3, game.engine._numeric_stat(attacker.object_id, "power"))

        self.until(game, lambda current: current.state.pending_decision.kind == "trigger.order")
        self.assertEqual(33, game.state.players["A"].life)
        self.assertEqual(["A"], game.state.pending_decision.actors)
        group = game.state.pending_trigger_batches[0].groups[0]
        by_dealer = {item["context"]["card_object_id"]: item.ref for item in group.items}
        self.assertEqual({live.object_id, departing.object_id}, set(by_dealer))
        order = [by_dealer[departing.object_id], by_dealer[live.object_id]]
        action = {"action_id": "order", "order": order}
        before = authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:B", action).ok)
        self.assertFalse(game.act("pilot:A", {**action, "order": [order[0], order[0]]}).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        accepted = game.act("pilot:A", action)
        self.assertTrue(accepted.ok, accepted.summary)
        self.assertEqual(by_dealer[live.object_id], game.state.stack[-1].ref)

        self.cast_for(game, "C", control, [departing.ref])
        self.resolve_top(game)
        self.assertEqual("C", game.state.cards[departing.object_id].controller)
        self.cast_for(game, "D", bounce, [departing.ref])
        self.resolve_top(game)
        self.assertEqual("hand", game.state.cards[departing.object_id].zone)
        game = self.replay(game, load=True)
        self.resolve_top(game)
        self.assertEqual("B", game.state.cards[land.object_id].controller)
        self.assertTrue(game.state.cards[land.object_id].tapped)
        self.resolve_top(game)
        self.assertEqual("C", game.state.cards[land.object_id].controller)
        self.assertTrue(game.state.cards[land.object_id].tapped)
        self.assertEqual("A", game.state.cards[land.object_id].owner)
        for principal in ("pilot:A", "pilot:B", "pilot:D", "spectator"):
            self.assertNotIn(private.ref, json.dumps(game.packet(principal, full=True)))
        self.replay(game, load=True)

    def test_actual_original_land_leaving_prevents_transfer_and_its_mana_activation_is_independent(self):
        game = self.session(120460415)
        engine = game.engine
        land = self.add(engine, NAME)
        self.add(engine, "Grizzly Bears", ref="war-zone-available-blocker")
        attacker = self.add(engine, "Grizzly Bears", seat="B")
        attacker.acquired_control_turn_count = -1
        bounce = self.add(engine, "Boomerang", zone="hand")
        for index in range(2):
            self.add(engine, "Island", ref=f"war-zone-own-bounce-island-{index}")
        self.prepare(game, actor="A", active="B")
        self.checkpoint(game)
        self.activate_current(game, "A", land, mana_ability=True)
        self.assertEqual(1, game.state.players["A"].mana_pool["C"])
        self.combat(game, (attacker,))
        self.until(game, lambda current: current.state.step == "combat_damage"
                   and current.state.pending_decision.kind == "priority")
        trigger, = game.state.stack
        self.assertEqual("A", trigger.controller)
        old = game.state.cards[land.object_id].logical_object_id
        self.cast_for(game, "A", bounce, [land.ref])
        self.assertIsNone(self.resolve(game))
        current = game.state.cards[land.object_id]
        self.assertNotEqual(old, current.logical_object_id)
        self.assertEqual(("hand", "A"), (current.zone, current.controller))
        self.replay(game, load=True)

    def test_actual_ordered_transfer_kills_using_only_the_dealer_at_damage_time(self):
        with patch("quorune.target_controller_information.resolve_occurrence_controller_information", return_value="B"):
            with self.assertRaisesRegex(AssertionError, "'C' != 'B'"):
                self.test_actual_independent_damage_triggers_keep_order_current_dealer_and_departure_controller()
