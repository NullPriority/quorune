"""One optional private draw for the original creature that dealt combat damage."""

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
from quorune.rules.event_optional_draw_shapes import DAMAGE_DEALER_DRAW_CAPABILITY as CAPABILITY
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


NAME = "Edric, Spymaster of Trest"
PROFILE = "commander_review"
FIXTURES = ROOT / "tests/fixtures"


class CommittedDamageDealerOptionalDrawTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        folder = Path(cls.temporary.name)
        base = json.loads((FIXTURES / "bound-effect-program-cards.json").read_bytes())
        base["cards"] = [row for row in base["cards"] if row["name"] == "Generic Bound Plains"]
        base["rulings"] = []
        basic = folder / "base.json"
        basic.write_text(json.dumps(base), encoding="utf-8")
        path = folder / "edric.sqlite3"
        build_fixture_database([basic, FIXTURES / "committed-damage-dealer-optional-draw-cards.json"], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Damage dealer draws", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"), DeckEntry("Generic Bound Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def program(self):
        record = self.db.lookup(NAME, fuzzy=False)
        result, semantics = register_card(self.db, self.registry, NAME)
        self.assertTrue(result["strict_capability_ready"], result["blockers"])
        program, = semantics.programs_for_oracle(record.oracle_id)
        return record, program

    def test_complete_original_owns_only_committed_creature_combat_damage_and_one_optional_draw(self):
        record, program = self.program()
        self.assertIn(CAPABILITY, program.capability_dependencies)
        self.assertEqual("damage.dealt", program.event)
        effect, = program.effects
        self.assertEqual(3, effect["event_optional_draw_version"])
        self.assertEqual(effect["player"], effect["drawer"])
        self.assertEqual({"kind": "damage_dealer_controller_information", "schema_version": 1}, effect["player"])
        self.assertEqual(1, effect["count"])
        self.assertIs(effect["private"], True)
        for text in (
            record.oracle_text.replace("combat damage", "damage"),
            record.oracle_text.replace("one of your opponents", "a planeswalker"),
            record.oracle_text.replace("may draw a card", "may pay 1 life. If they do, they draw a card"),
            record.oracle_text + " Invent an unsupported instruction.",
        ):
            with self.subTest(text=text):
                ir = compile_oracle_card(replace(record, oracle_text=text), capability_registry=self.registry,
                                        capability_profile=PROFILE)
                self.assertNotEqual("exact", ir.status)

    def test_raw_beneficiary_and_subscription_require_the_same_original_dealer(self):
        _, program = self.program()
        effect, = program.effects
        for changed in (
            replace(program, event="damage.dealt.self"),
            replace(program, event_condition={"field": "combat", "op": "truthy", "value": True}),
            replace(program, effects=({**effect, "player": "$controller", "drawer": "$controller"},)),
            replace(program, effects=({**effect, "count": 2},)),
        ):
            result = bind_semantic_program_runtime(changed, capability_registry=self.registry, profile=PROFILE)
            self.assertFalse(result["strict"], result)
        deps = [cap for cap in program.capability_dependencies if cap != CAPABILITY]
        changed = replace(program, capability_dependencies=deps,
                          capability_closure=self.registry.closure(deps, profile=PROFILE).to_dict())
        result = bind_semantic_program_runtime(changed, capability_registry=self.registry, profile=PROFILE)
        self.assertFalse(result["strict"], result)
        self.assertIn("capability:undeclared_runtime_dependency:" + CAPABILITY, result["blockers"])

    def combat_scene(self, seed, victim, *, fog=False):
        game = self.session(seed)
        edric = self.add(game.engine, NAME)
        attacker = self.add(game.engine, "Grizzly Bears", seat="B")
        attacker.acquired_control_turn_count = -1
        self.add(game.engine, "Grizzly Bears", seat=victim, ref="edric-available-blocker")
        private = self.add(game.engine, "Counterspell", seat="D", zone="hand", ref="edric-unrevealed")
        control = self.add(game.engine, "Word of Seizing", seat="C", zone="hand")
        bounce = self.add(game.engine, "Boomerang", seat="D", zone="hand")
        for index in range(5):
            self.add(game.engine, "Mountain", seat="C", ref=f"edric-mountain-{index}")
        for index in range(2):
            self.add(game.engine, "Island", seat="D", ref=f"edric-island-{index}")
        if fog:
            shield = self.add(game.engine, "Fog", seat="C", zone="hand")
            self.add(game.engine, "Forest", seat="C", ref="edric-fog-forest")
            self.assert_closed(game, shield)
        self.assert_closed(game, edric)
        self.prepare(game, actor="B", active="B")
        self.checkpoint(game)
        self.until(game, lambda s: s.state.pending_decision.kind == "combat.attackers")
        attack = game.act("pilot:B", {"action_id": "attack", "attackers": {attacker.ref: victim}})
        self.assertTrue(attack.ok, attack.summary)
        self.until(game, lambda s: s.state.pending_decision.kind == "combat.blockers")
        block = game.act("pilot:" + victim, {"action_id": "block", "blocks": {}})
        self.assertTrue(block.ok, block.summary)
        if fog:
            self.cast_for(game, "C", shield)
            self.resolve_top(game)
        self.until(game, lambda s: s.state.step == "combat_damage" and
                   s.state.pending_decision.kind == "priority")
        return game, attacker, control, bounce, private

    def test_actual_third_party_damage_uses_current_and_departure_controller(self):
        for leaves in (False, True):
            with self.subTest(leaves=leaves):
                game, attacker, control, bounce, private = self.combat_scene(120460301 + leaves, "C")
                self.assertEqual(38, game.state.players["C"].life)
                trigger, = game.state.stack
                self.assertEqual("A", trigger.controller)
                self.assertIn(CAPABILITY, game.engine.semantics.get(trigger.semantic_key).capability_dependencies)
                self.cast_for(game, "C", control, [attacker.ref])
                self.resolve_top(game)
                self.assertEqual("C", game.state.cards[attacker.object_id].controller)
                if leaves:
                    self.cast_for(game, "D", bounce, [attacker.ref])
                    self.resolve_top(game)
                    self.assertEqual("hand", game.state.cards[attacker.object_id].zone)
                pending = self.resolve(game)
                self.assertEqual(("semantic.choice", ["C"]), (pending.kind, list(pending.actors)))
                before = authoritative_state_hash(game.state)
                command = {"action_id": "choose", "choice": "draw"}
                for principal in ("pilot:A", "pilot:B", "pilot:D"):
                    self.assertFalse(game.act(principal, command).ok)
                self.assertFalse(game.act("pilot:C", {**command, "drawer": "A"}).ok)
                self.assertEqual(before, authoritative_state_hash(game.state))
                game = self.replay(game, load=True)
                hand = set(game.state.players["C"].zones["hand"])
                result = game.act("pilot:C", command)
                self.assertTrue(result.ok, result.summary)
                drawn, = set(game.state.players["C"].zones["hand"]) - hand
                ref = game.state.cards[drawn].ref
                self.assertIn(json.dumps(ref), json.dumps(game.packet("pilot:C", full=True)))
                for viewer in ("pilot:A", "pilot:B", "pilot:D", "spectator"):
                    self.assertNotIn(json.dumps(ref), json.dumps(game.packet(viewer, full=True)))
                self.assertNotIn(private.ref, json.dumps(game.packet("pilot:C", full=True)))
                self.replay(game, load=True)

    def test_actual_decline_and_damage_to_source_controller_or_prevented_damage(self):
        for victim, fog in (("A", False), ("C", True)):
            game, _, _, _, _ = self.combat_scene(120460305 + fog, victim, fog=fog)
            self.assertEqual(40 if fog else 38, game.state.players[victim].life)
            self.assertFalse(game.state.stack)
            self.replay(game, load=True)
        game, _, _, _, _ = self.combat_scene(120460307, "C")
        pending = self.resolve(game)
        self.assertEqual(("semantic.choice", ["B"]), (pending.kind, list(pending.actors)))
        hands = {seat: tuple(player.zones["hand"]) for seat, player in game.state.players.items()}
        result = game.act("pilot:B", {"action_id": "choose", "choice": "decline"})
        self.assertTrue(result.ok, result.summary)
        self.assertEqual(hands, {seat: tuple(player.zones["hand"]) for seat, player in game.state.players.items()})
        self.replay(game, load=True)

    def test_native_dealer_draw_witness_kills_original_event_controller_mutation(self):
        from quorune import target_controller_information

        original = target_controller_information.resolve_occurrence_controller_information

        def event_controller(host, value, item, **kwargs):
            if target_controller_information.is_damage_dealer_controller_reference(value):
                return item.context["source_controller"]
            return original(host, value, item, **kwargs)

        with patch.object(target_controller_information, "resolve_occurrence_controller_information",
                          side_effect=event_controller):
            witness = self.defaultTestResult()
            case = type(self)("test_actual_third_party_damage_uses_current_and_departure_controller")
            case.run(witness)
        self.assertFalse(witness.errors, witness.errors)
        self.assertEqual(2, len(witness.failures))
        for _, explanation in witness.failures:
            self.assertIn("('semantic.choice', ['B'])", explanation)
