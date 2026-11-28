"""Native evidence for one damage trigger over both declared combat participants."""

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
from quorune.replacement.immutable import thaw_value
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.event_subscriptions import FixedEventSubscriptionSet
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


ORIGINAL = "Powerstone Minefield"
SKITTERSPIKE = "Giggling Skitterspike"


class CreatureAttacksOrBlocksDamageTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        database = Path(cls.temporary.name) / "creature-attacks-or-blocks-damage.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures/creature-attacks-or-blocks-damage-cards.json"], database)
        cls.db = CardDatabase(database)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Damage to combat participants", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"), DeckEntry("Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def program(self):
        card = self.db.lookup(ORIGINAL, fuzzy=False)
        binding, semantics = register_card(self.db, self.registry, ORIGINAL)
        self.assertTrue(binding["strict_capability_ready"], binding["blockers"])
        program, = semantics.programs_for_oracle(card.oracle_id)
        return program

    def test_skitterspike_requires_all_three_exact_routes_and_its_grouped_damage_authority(self):
        from quorune.rules.source_power_opponent_damage import (
            SOURCE_POWER_OPPONENT_DAMAGE_CAPABILITY, source_power_opponent_damage_effect,
        )

        card = self.db.lookup(SKITTERSPIKE, fuzzy=False)
        bound, semantics = register_card(self.db, self.registry, SKITTERSPIKE)
        self.assertTrue(bound["strict_capability_ready"], bound["blockers"])
        program, = (p for p in semantics.programs_for_oracle(card.oracle_id)
                    if SOURCE_POWER_OPPONENT_DAMAGE_CAPABILITY in p.capability_dependencies)
        subscriptions = FixedEventSubscriptionSet.from_condition(program.event_condition)
        self.assertEqual(
            ["creature.attacks", "creature.blocks", "object.became_target"],
            [row.event for row in subscriptions.subscriptions],
        )
        self.assertEqual({"all": [
            {"field": "card", "op": "eq", "value": "$source.ref"},
            {"field": "stack_kind", "op": "in", "value": ["spell", "spell_copy"]},
        ]}, thaw_value(subscriptions.subscriptions[-1].condition))
        self.assertEqual([source_power_opponent_damage_effect()], thaw_value(program.effects))
        self.assertIsNone(program.target_schema)
        wrong = thaw_value(program.event_condition)
        wrong["event_subscriptions"][-1]["condition"]["all"][-1]["value"].append("activated_ability")
        without = tuple(value for value in program.capability_dependencies if value != SOURCE_POWER_OPPONENT_DAMAGE_CAPABILITY)
        for mutated in (
            replace(program, event_condition=wrong),
            replace(program, effects=({**program.effects[0], "source": "$target.0"},)),
            replace(program, capability_dependencies=without,
                    capability_closure=self.registry.closure(without, profile="commander_review").to_dict()),
        ):
            binding = bind_semantic_program_runtime(mutated, capability_registry=self.registry, profile="commander_review")
            self.assertFalse(binding["strict"], binding)
        with patch("quorune.compiler.fixed_public_multi_event_bindings._source_combat_or_spell_target_spec", return_value=None):
            hidden = compile_oracle_card(card, capability_registry=self.registry, capability_profile="commander_review")
            self.assertEqual("partial", hidden.status, hidden.material_residuals)

    def _skitter_activation(self, game, actor, source, targets=()):
        self.priority(game, actor)
        legal = game.packet("pilot:" + actor, full=True)["decision"]["ctx"]["legal"]["actions"]
        action, = (row for row in legal if row["id"].startswith("activate:" + source.ref + ":"))
        command = {"action_id": action["id"], "pay": "auto", "targets": list(targets)}
        before = authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:D", command).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        accepted = game.act("pilot:" + actor, command)
        self.assertTrue(accepted.ok, accepted.summary)

    def test_skitterspike_attacks_and_spell_targets_with_live_and_unavailable_source_replay(self):
        for removal in ("Unsummon", "Reality Ripple"):
            with self.subTest(removal=removal):
                game = self.session(603_020_05 + len(removal))
                spike = self.add(game.engine, SKITTERSPIKE)
                spike.acquired_control_turn_count = -1
                response = self.add(game.engine, removal, seat="B", zone="hand")
                hidden = self.add(game.engine, "Cloudshift", seat="D", zone="hand", ref="skitterspike-hidden-response")
                for index in range(5):
                    self.add(game.engine, "Plains", ref=f"spike-white-land-{index}")
                for index in range(2):
                    self.add(game.engine, "Island", seat="B", ref=f"spike-blue-land-{index}")
                for original in (spike, response):
                    self.assert_closed(game, original)
                game.state.config.semantic_policy = "trusted_only"
                self.prepare(game)
                self.checkpoint(game)
                life = {seat: game.state.players[seat].life for seat in "ABCD"}

                self.attack(game, [(spike, "B")])
                self.assertEqual(1, self.trigger_count(game, spike))
                self._skitter_activation(game, "A", spike)
                self.resolve_top(game)
                self.assertEqual(5, game.state.cards[spike.object_id].monstrous_value)
                self.assertEqual(1, self.trigger_count(game, spike))

                self.cast_for(game, "B", response, [spike.ref])
                self.assertEqual(2, self.trigger_count(game, spike))
                game = self.replay(game, load=True)
                self.resolve_top(game)
                for seat in "BCD":
                    self.assertEqual(life[seat] - 6, game.state.players[seat].life)
                self.resolve_top(game)
                current = game.state.cards[spike.object_id]
                self.assertEqual(("hand", False) if removal == "Unsummon" else ("battlefield", True),
                                 (current.zone, current.phased_out))
                self.assertEqual(1, self.trigger_count(game, spike))
                game = self.replay(game, load=True)
                self.resolve_top(game)
                self.assertEqual(life["A"], game.state.players["A"].life)
                for seat in "BCD":
                    self.assertEqual(life[seat] - 12, game.state.players[seat].life)
                groups = [event.details["dealt_amounts"] for event in game.state.events
                          if event.code == "effect.creature_power_damage"
                          and event.details.get("source") == spike.ref]
                self.assertEqual([[6, 6, 6], [6, 6, 6]], groups)
                self.assertNotIn(hidden.ref, json.dumps(game.packet("pilot:B", full=True)))
                self.replay(game, load=True)

    def test_skitterspike_blocks_once_and_an_activated_ability_target_does_not_trigger(self):
        game = self.session(509_011_07)
        spike = self.add(game.engine, SKITTERSPIKE, seat="B")
        attacker = self.add(game.engine, "Giant Spider")
        pinger = self.add(game.engine, "Prodigal Sorcerer", seat="C")
        pinger.acquired_control_turn_count = -1
        for original in (spike, attacker, pinger):
            self.assert_closed(game, original)
        game.state.config.semantic_policy = "trusted_only"
        self.prepare(game)
        self.checkpoint(game)
        life = {seat: game.state.players[seat].life for seat in "ABCD"}

        self.attack(game, [(attacker, "B")])
        self.assertEqual(0, self.trigger_count(game, spike))
        self._skitter_activation(game, "C", pinger, [spike.ref])
        self.assertEqual(0, self.trigger_count(game, spike))
        self.resolve_top(game)
        self.assertEqual(1, game.state.cards[spike.object_id].marked_damage)
        self.until(game, lambda current: current.state.pending_decision is not None
                   and current.state.pending_decision.kind == "combat.blockers")
        result = game.act("pilot:B", {"action_id": "block", "blocks": {spike.ref: [attacker.ref]}})
        self.assertTrue(result.ok, result.summary)
        self.assertEqual(1, self.trigger_count(game, spike))
        game = self.replay(game, load=True)
        self.resolve_top(game)
        self.assertEqual(life["B"], game.state.players["B"].life)
        for seat in "ACD":
            self.assertEqual(life[seat] - 1, game.state.players[seat].life)
        self.replay(game, load=True)

    def test_whole_original_uses_one_untargeted_program_with_two_combat_occurrences(self):
        program = self.program()
        subscriptions = FixedEventSubscriptionSet.from_condition(program.event_condition)
        self.assertEqual({"creature.attacks", "creature.blocks"}, {row.event for row in subscriptions.subscriptions})
        self.assertEqual("battlefield", program.active_zone)
        self.assertIsNone(program.target_schema)
        self.assertEqual(({"op": "damage", "source": "$source", "target": "$event.permanent_object", "amount": 2},),
                         tuple(dict(effect) for effect in program.effects))

    def test_raw_combat_program_requires_both_branches_and_the_original_participant(self):
        program = self.program()
        for altered in (
            replace(program, event_condition=None),
            replace(program, active_zone="graveyard"),
            replace(program, effects=({'op': 'damage', 'source': '$source', 'target': '$context.card', 'amount': 2},)),
        ):
            binding = bind_semantic_program_runtime(altered, capability_registry=self.registry, profile="commander_review")
            self.assertFalse(binding["strict"], binding)

    def test_actual_two_attackers_and_a_single_blocker_resolve_their_own_damage_once(self):
        game = self.session(50933031)
        observer = self.add(game.engine, ORIGINAL, zone="hand")
        spiders = [self.add(game.engine, "Giant Spider", ref=f"minefield-attacker-{n}") for n in range(2)]
        wall = self.add(game.engine, "Wall of Glare", seat="B")
        remove = self.add(game.engine, "Disenchant", seat="B", zone="hand")
        self.add(game.engine, "Plains", seat="B", ref="minefield-dis-enchant-1")
        self.add(game.engine, "Plains", seat="B", ref="minefield-dis-enchant-2")
        for card in (observer, *spiders, wall, remove):
            self.assert_closed(game, card)
        self.prepare(game, mana={"C": 2, "R": 1, "W": 1})
        self.checkpoint(game)
        self.cast_for(game, "A", observer)
        self.resolve_top(game)
        self.attack(game, [(spider, "B") for spider in spiders])
        self.assertEqual(2, self.trigger_count(game, observer))
        game = self.replay(game, load=True)
        for _ in spiders:
            self.resolve_top(game)
        self.assertEqual([2, 2], [game.state.cards[spider.object_id].marked_damage for spider in spiders])
        self.until(game, lambda current: current.state.pending_decision is not None
                   and current.state.pending_decision.kind == "combat.blockers")
        command = {"action_id": "block", "blocks": {wall.ref: [spider.ref for spider in spiders]}}
        before = authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:C", command).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        result = game.act("pilot:B", command)
        self.assertTrue(result.ok, result.summary)
        self.assertEqual(1, self.trigger_count(game, observer))
        self.cast_for(game, "B", remove, [observer.ref])
        self.resolve_top(game)
        self.assertEqual("graveyard", game.state.cards[observer.object_id].zone)
        game = self.replay(game, load=True)
        self.resolve_top(game)
        self.assertEqual(2, game.state.cards[wall.object_id].marked_damage)
        self.replay(game, load=True)

    def test_actual_blinked_attacker_keeps_its_new_incarnation_outside_the_waiting_damage(self):
        game = self.session(50833041)
        observer = self.add(game.engine, ORIGINAL, seat="C")
        spider = self.add(game.engine, "Giant Spider")
        blink = self.add(game.engine, "Cloudshift", zone="hand")
        self.add(game.engine, "Plains")
        for card in (observer, spider, blink):
            self.assert_closed(game, card)
        self.prepare(game)
        self.checkpoint(game)
        self.attack(game, [(spider, "B")])
        self.assertEqual(1, self.trigger_count(game, observer))
        before = game.state.cards[spider.object_id].logical_object_id
        self.cast_for(game, "A", blink, [spider.ref])
        self.resolve_top(game)
        self.assertNotEqual(before, game.state.cards[spider.object_id].logical_object_id)
        game = self.replay(game, load=True)
        self.resolve_top(game)
        current = game.state.cards[spider.object_id]
        self.assertEqual(("battlefield", 0), (current.zone, current.marked_damage))
        self.replay(game, load=True)
