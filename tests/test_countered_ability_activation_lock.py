"""Original stack-ability counters can prohibit only a permanent's activations."""

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
from quorune.continuous_effect_model import ContinuousObjectIdentity
from quorune.deck import DeckDefinition, DeckEntry
from quorune.declaration_rule_effects import ResolvedActivationProhibition, continuous_journal_effect_from_dict
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.stack_counter_followup import COUNTERED_ABILITY_ACTIVATION_LOCK_CAPABILITY
from quorune.stack_counter import COUNTERED_ABILITY_ACTIVATION_LOCK_FIELD
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


PROFILE = "commander_review"


class CounteredAbilityActivationLockTests(InheritedNativeFixture, unittest.TestCase):
    playing = native_turns.NextPlayerUntapRestrictionTests.playing

    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "countered-ability-activation-lock.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures/countered-ability-activation-lock-cards.json"], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Countered ability activation prohibition", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"), DeckEntry("Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def test_complete_counter_activation_prohibition_originals_keep_split_second_and_draw(self):
        for name, mode in (("Trickbind", "counter_success"), ("Interdict", "counter_attempt")):
            with self.subTest(card=name):
                result, semantics = register_card(self.db, self.registry, name)
                self.assertTrue(result["strict_capability_ready"], result["blockers"])
                card = self.db.lookup(name, fuzzy=False)
                programs = semantics.programs_for_oracle(card.oracle_id)
                program, = (p for p in programs if p.effects and COUNTERED_ABILITY_ACTIVATION_LOCK_FIELD in p.effects[0])
                self.assertEqual(mode, program.effects[0][COUNTERED_ABILITY_ACTIVATION_LOCK_FIELD])
                if name == "Trickbind":
                    self.assertTrue(any("priority.stack.split_second" in p.capability_dependencies for p in programs))
                else:
                    self.assertTrue(any(effect.get("op") == "draw" for p in programs for effect in p.effects))
                dependencies = [cap for cap in program.capability_dependencies if cap != COUNTERED_ABILITY_ACTIVATION_LOCK_CAPABILITY]
                omitted = replace(program, capability_dependencies=dependencies,
                                  capability_closure=self.registry.closure(dependencies, profile=PROFILE).to_dict())
                report = bind_semantic_program_runtime(omitted, capability_registry=self.registry, profile=PROFILE)
                self.assertIn(f"capability:undeclared_runtime_dependency:{COUNTERED_ABILITY_ACTIVATION_LOCK_CAPABILITY}",
                              report["blockers"])
                invalid = replace(program, effects=[{**program.effects[0], COUNTERED_ABILITY_ACTIVATION_LOCK_FIELD: mode + "?"}, *program.effects[1:]])
                self.assertFalse(bind_semantic_program_runtime(
                    invalid, capability_registry=self.registry, profile=PROFILE,
                )["strict"])
                with patch("quorune.compiler.stack_counter_followup._ACTIVATION_LOCK_FOLLOWUPS", {}):
                    self.assertNotEqual("exact", compile_oracle_card(
                        card, capability_registry=self.registry, capability_profile=PROFILE,
                    ).status)

    def test_single_permanent_activation_prohibition_has_a_closed_turn_cleanup_wire(self):
        effect = ResolvedActivationProhibition("CE1", "stack-source", 21, ContinuousObjectIdentity("card", "original"))
        payload = effect.to_dict()
        self.assertEqual(effect, continuous_journal_effect_from_dict(payload))
        for invalid in ({**payload, "schema_version": True}, {**payload, "duration": "until_next_turn"},
                        {**payload, "subject": {"object_id": "card"}}):
            with self.assertRaises(ValueError):
                continuous_journal_effect_from_dict(invalid)

    def priority_actions(self, game, seat):
        self.priority(game, seat)
        return game.packet("pilot:" + seat, full=True)["decision"]["ctx"]["legal"]["actions"]

    def activation_id(self, game, card, *, mana):
        current = game.state.cards[card.object_id]
        ability, = (a for a in game.engine._activated_abilities(current) if a.mana_ability == mana)
        return f"activate:{current.ref}:{ability.ability_id}"

    def activate_basalts_untap(self, game, basalt, *, reserve=None):
        action_id = self.activation_id(game, basalt, mana=False)
        self.assertIn(action_id, {offer["id"] for offer in self.priority_actions(game, "B")})
        accepted = game.act("pilot:B", {"action_id": action_id, "pay": "auto", "reserve": reserve or {}})
        self.assertTrue(accepted.ok, accepted.summary)
        ability = game.state.stack[-1]
        self.assertEqual("activated_ability", ability.kind)
        return ability.ref

    def added_prohibitions(self, game):
        return [effect for effect in game.state.continuous_effects if isinstance(effect, ResolvedActivationProhibition)]

    def test_actual_trickbind_blocks_the_original_mana_and_other_activations_until_cleanup(self):
        game = self.playing(61126301)
        spell = self.add(game.engine, "Trickbind", zone="hand")
        basalt = self.add(game.engine, "Basalt Monolith", seat="B")
        unrelated = self.add(game.engine, "Sol Ring", seat="B")
        response = self.add(game.engine, "Unsummon", seat="B", zone="hand")
        target = self.add(game.engine, "Isamaru, Hound of Konda", seat="B", ref="response-creature")
        for card in (spell, basalt, unrelated, response, target):
            self.assert_closed(game, card)
        self.prepare(game, actor="B", active="B", mana={"C": 6, "U": 1})
        game.state.players["A"].mana_pool.update({"U": 2})
        self.checkpoint(game)
        mana = self.activation_id(game, basalt, mana=True)
        available = {row["id"] for row in self.priority_actions(game, "B")}
        self.assertIn(mana, available)
        self.assertIn(f"cast:{response.ref}", available)
        ability = self.activate_basalts_untap(game, basalt, reserve={"U": 1})
        self.cast_for(game, "A", spell, [ability])
        offered = {row["id"] for row in self.priority_actions(game, "B")}
        self.assertIn(mana, offered)
        self.assertNotIn(f"cast:{response.ref}", offered)
        self.assertEqual([], self.added_prohibitions(game))
        self.assertIsNone(self.resolve(game))
        rule, = self.added_prohibitions(game)
        self.assertEqual(basalt.logical_object_id, rule.subject.logical_object_id)
        game = self.replay(game, load=True)
        offered = {row["id"] for row in self.priority_actions(game, "B")}
        self.assertNotIn(mana, offered)
        self.assertNotIn(self.activation_id(game, basalt, mana=False), offered)
        self.assertIn(f"cast:{response.ref}", offered)
        self.assertIn(self.activation_id(game, unrelated, mana=True), offered)
        before = authoritative_state_hash(game.state)
        rejected = game.act("pilot:B", {"action_id": mana, "pay": "auto"})
        self.assertFalse(rejected.ok, rejected.summary)
        self.assertEqual(before, authoritative_state_hash(game.state))
        self.until(game, lambda value: value.state.active_player == "C" and value.state.step == "upkeep")
        self.assertIn(mana, {row["id"] for row in self.priority_actions(game, "B")})
        self.assertEqual([], self.added_prohibitions(game))
        self.replay(game, load=True)

    def test_actual_trickbind_can_counter_a_trigger_without_turning_off_later_triggers(self):
        game = self.playing(61126302)
        spell = self.add(game.engine, "Trickbind", zone="hand")
        disqualified = self.add(game.engine, "Interdict", zone="hand")
        marwyn = self.add(game.engine, "Marwyn, the Nurturer", seat="B")
        first = self.add(game.engine, "Llanowar Elves", seat="B", zone="hand", ref="first-elf")
        second = self.add(game.engine, "Llanowar Elves", seat="B", zone="hand", ref="second-elf")
        for card in (spell, disqualified, marwyn, first, second):
            self.assert_closed(game, card)
        marwyn.acquired_control_turn_count = -1
        self.prepare(game, actor="B", active="B", mana={"G": 2})
        game.state.players["A"].mana_pool.update({"U": 2})
        self.checkpoint(game)
        mana = self.activation_id(game, marwyn, mana=True)
        self.assertIn(mana, {row["id"] for row in self.priority_actions(game, "B")})
        self.cast_for(game, "B", first, reserve={"G": 1})
        self.resolve_top(game)
        trigger, = game.state.stack
        self.assertEqual("triggered_ability", trigger.kind)
        actions = self.priority_actions(game, "A")
        self.assertNotIn(f"cast:{disqualified.ref}", {row["id"] for row in actions})
        before = authoritative_state_hash(game.state)
        rejected = game.act("pilot:A", {"action_id": f"cast:{disqualified.ref}", "targets": [trigger.ref], "pay": "auto"})
        self.assertFalse(rejected.ok, rejected.summary)
        self.assertEqual(before, authoritative_state_hash(game.state))
        self.cast_for(game, "A", spell, [trigger.ref])
        self.assertIsNone(self.resolve(game))
        self.assertEqual(0, game.state.cards[marwyn.object_id].counters.get("+1/+1", 0))
        self.cast_for(game, "B", second)
        self.assertIsNone(self.resolve(game))
        self.assertEqual(1, game.state.cards[marwyn.object_id].counters.get("+1/+1", 0))
        self.assertNotIn(mana, {row["id"] for row in self.priority_actions(game, "B")})
        self.replay(game, load=True)

    def test_actual_interdict_counters_the_artifact_activation_and_draws_for_its_spell_controller(self):
        game = self.playing(61126303)
        spell = self.add(game.engine, "Interdict", zone="hand")
        basalt = self.add(game.engine, "Basalt Monolith", seat="B")
        for card in (spell, basalt):
            self.assert_closed(game, card)
        self.prepare(game, actor="B", active="B", mana={"C": 3})
        game.state.players["A"].mana_pool.update({"U": 2})
        self.checkpoint(game)
        ability = self.activate_basalts_untap(game, basalt)
        before = set(game.state.players["A"].zones["hand"])
        other_hand = tuple(game.state.players["B"].zones["hand"])
        self.cast_for(game, "A", spell, [ability])
        self.assertIsNone(self.resolve(game))
        self.assertEqual(1, len(set(game.state.players["A"].zones["hand"]) - before))
        self.assertEqual(other_hand, tuple(game.state.players["B"].zones["hand"]))
        self.assertEqual(1, len(self.added_prohibitions(game)))
        self.assertNotIn(self.activation_id(game, basalt, mana=True),
                         {row["id"] for row in self.priority_actions(game, "B")})
        self.replay(game, load=True)


    def test_actual_interdict_uses_departure_lki_without_prohibiting_the_returned_permanent(self):
        game = self.playing(61126304)
        spell = self.add(game.engine, "Interdict", zone="hand")
        elf = self.add(game.engine, "Llanowar Loamspeaker", seat="B")
        land = self.add(game.engine, "Forest", seat="B")
        blink = self.add(game.engine, "Cloudshift", seat="B", zone="hand")
        for card in (spell, elf, land, blink):
            self.assert_closed(game, card)
        elf.acquired_control_turn_count = -1
        self.prepare(game, actor="B", active="B", mana={"W": 1})
        game.state.players["A"].mana_pool.update({"U": 2})
        self.checkpoint(game)
        action_id = self.activation_id(game, elf, mana=False)
        self.assertIn(action_id, {row["id"] for row in self.priority_actions(game, "B")})
        activated = game.act("pilot:B", {"action_id": action_id, "targets": [land.ref], "pay": "auto"})
        self.assertTrue(activated.ok, activated.summary)
        ability = game.state.stack[-1]
        old_identity = elf.logical_object_id
        before = set(game.state.players["A"].zones["hand"])
        self.cast_for(game, "A", spell, [ability.ref])
        self.cast_for(game, "B", blink, [elf.ref])
        self.assertIsNone(self.resolve(game))
        current = game.state.cards[elf.object_id]
        self.assertNotEqual(old_identity, current.logical_object_id)
        self.assertEqual([], self.added_prohibitions(game))
        self.assertEqual(1, len(set(game.state.players["A"].zones["hand"]) - before))
        self.assertTrue(any(event.code == "stack.counter" for event in game.state.events))
        self.assertNotIn("creature", game.engine._effective_type_parts(game.state.cards[land.object_id])[0])
        self.replay(game, load=True)
