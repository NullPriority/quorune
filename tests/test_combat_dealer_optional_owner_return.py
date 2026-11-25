"""One optional owner return for each sealed controlled combat damage dealer."""

from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.event_counter_reference import EVENT_COUNTER_CONTEXT
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.occurrence_object_results import occurrence_object_runtime_dependencies
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


ORIGINAL = "Gossip's Talent"


class CombatDealerOptionalOwnerReturnTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        database = Path(cls.temporary.name) / "combat-dealer-optional-owner-return.sqlite3"
        fixtures = ROOT / "tests/fixtures"
        build_fixture_database([
            fixtures / "combat-dealer-optional-owner-return-cards.json",
            fixtures / "combat-prevention-source-power-ceiling-cards.json",
            fixtures / "opponent-life-gain-to-loss-cards.json",
            fixtures / "creature-power-at-most-controlled-islands-cards.json",
        ], database)
        cls.db = CardDatabase(database)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Combat dealer owner return", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"), DeckEntry("Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def test_complete_original_registers_only_the_exact_optional_combat_owner_return(self):
        bound, semantics = register_card(self.db, self.registry, ORIGINAL)
        self.assertTrue(bound["strict_capability_ready"], bound["blockers"])
        record = self.db.lookup(ORIGINAL, fuzzy=False)
        programs = semantics.programs_for_oracle(record.oracle_id)
        trigger, = (program for program in programs if program.event == "damage.dealt")
        self.assertEqual(
            {"creature.enter", "activate", "characteristics.evaluate", "creature.attacks", "damage.dealt"},
            {program.event for program in programs},
        )
        self.assertEqual(2, sum(program.event == "activate" for program in programs))
        self.assertIsNone(trigger.target_schema)
        wrapper, = trigger.effects
        exile, returning = wrapper["effects"]
        self.assertEqual(("offer_optional_effect", "$controller"), (wrapper["op"], wrapper["player"]))
        self.assertEqual("$event.permanent_object", exile["cards"])
        self.assertEqual(("exile", "return"), (exile["phase"], returning["phase"]))
        self.assertEqual(exile["binding_id"], returning["binding_id"])
        self.assertEqual("owner", returning["spec"]["controller"])
        self.assertEqual("immediate", returning["spec"]["timing"])
        for capability in ("effect.choice.optional_fixed", "trigger.event.normalized_damage",
                           "zone.linked_exile_return.fixed", "permanent.class.fixed_lifecycle"):
            self.assertIn(capability, trigger.capability_dependencies)
        condition = deepcopy(trigger.event_condition)
        condition["all"] = [clause for clause in condition["all"] if clause["field"] != "combat"]
        wrong_return = deepcopy(trigger.effects)
        for effect in wrong_return[0]["effects"]:
            effect["spec"]["controller"] = "effect_controller"
        for invalid in (
            replace(trigger, event_condition=condition),
            replace(trigger, active_zone="graveyard"),
            replace(trigger, effects=wrong_return),
        ):
            with self.assertRaises(ValueError):
                occurrence_object_runtime_dependencies(invalid)

    def reach_level_three(self, game, source):
        self.cast(game, "A", source)
        self.assertIsNone(self.resolve(game))
        for level in (2, 3):
            self.priority(game, "A")
            live = game.state.cards[source.object_id]
            ability = next(ability for ability in game.engine._activated_abilities(live)
                           if ability.effect_text == f"Level {level}")
            action_id = f"activate:{live.ref}:{ability.ability_id}"
            actions = game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
            self.assertIn(action_id, {action["id"] for action in actions})
            result = game.act("pilot:A", {"action_id": action_id, "pay": "auto"})
            self.assertTrue(result.ok, result.summary)
            self.assertIsNone(self.resolve(game))
            self.assertEqual(level, game.state.cards[source.object_id].class_level)

    def attack_with_class(self, game, assignments):
        self.attack(game, assignments)
        targets_ability = self.resolve(game)
        self.assertEqual("semantic.target", targets_ability.kind)
        self.target(game, "A", [assignments[0][0].ref])
        self.assertIsNone(self.resolve(game))

    def choose_return(self, game, choice, *, replay=False):
        pending = self.resolve(game)
        self.assertEqual(("semantic.choice", ["A"]), (pending.kind, pending.actors))
        command = {"action_id": "choose", "choice": choice}
        before = authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:B", command).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        if replay:
            game = self.replay(game, load=True)
        result = game.act("pilot:A", command)
        self.assertTrue(result.ok, result.summary)
        return game

    def test_actual_damage_orders_two_choices_and_returns_a_borrowed_creature_to_its_owner(self):
        game = self.session(71633221)
        source = self.add(game.engine, ORIGINAL, zone="hand")
        theft = self.add(game.engine, "Act of Treason", zone="hand")
        borrowed = self.add(game.engine, "Runeclaw Bear", seat="B", ref="borrowed-combat-dealer")
        own = self.add(game.engine, "Runeclaw Bear", ref="owned-combat-dealer")
        own.acquired_control_turn_count = -1
        for card in (source, theft):
            self.assert_closed(game, card)
        self.prepare(game, mana={"C": 7, "U": 3, "R": 1})
        self.checkpoint(game)

        self.reach_level_three(game, source)
        self.cast_for(game, "A", theft, [borrowed.ref])
        self.assertIsNone(self.resolve(game))
        self.assertEqual(("B", "A"), (game.state.cards[borrowed.object_id].owner,
                                     game.state.cards[borrowed.object_id].controller))
        old_borrowed = game.state.cards[borrowed.object_id].logical_object_id
        old_own = game.state.cards[own.object_id].logical_object_id
        self.attack_with_class(game, [(borrowed, "C"), (own, "B")])
        self.until(game, lambda current: current.state.pending_decision.kind == "trigger.order")
        self.assertEqual((38, 38), tuple(game.state.players[seat].life for seat in ("B", "C")))
        items = {item.event_facts["card"]: item for batch in game.state.pending_trigger_batches
                 for item in batch.items}
        self.assertEqual({borrowed.ref, own.ref}, set(items))
        self.assertEqual(
            {"reference": borrowed.ref, "logical_object_id": old_borrowed},
            dict(items[borrowed.ref].event_facts[EVENT_COUNTER_CONTEXT]),
        )
        result = game.act("pilot:A", {"action_id": "order",
                                     "triggers": [items[own.ref].ref, items[borrowed.ref].ref]})
        self.assertTrue(result.ok, result.summary)
        self.assertEqual(borrowed.ref, game.state.stack[-1].context["card"])
        game = self.choose_return(game, "apply", replay=True)
        returned = game.state.cards[borrowed.object_id]
        self.assertEqual(("battlefield", "B", "B"), (returned.zone, returned.owner, returned.controller))
        self.assertNotEqual(old_borrowed, returned.logical_object_id)
        self.assertNotIn(borrowed.object_id, game.state.combat.attackers)
        self.assertEqual(own.ref, game.state.stack[-1].context["card"])
        game = self.choose_return(game, "decline")
        self.assertEqual(old_own, game.state.cards[own.object_id].logical_object_id)
        self.assertEqual(0, self.trigger_count(game, source))
        self.replay(game, load=True)

    def test_actual_bounce_before_resolution_cannot_return_the_old_dealer(self):
        game = self.session(71633222)
        source = self.add(game.engine, ORIGINAL, zone="hand")
        attacker = self.add(game.engine, "Runeclaw Bear", ref="departing-combat-dealer")
        attacker.acquired_control_turn_count = -1
        bounce = self.add(game.engine, "Unsummon", zone="hand")
        self.add(game.engine, "Island", ref="combat-response-island")
        for card in (source, bounce):
            self.assert_closed(game, card)
        self.prepare(game, mana={"C": 5, "U": 3})
        self.checkpoint(game)

        self.reach_level_three(game, source)
        self.attack_with_class(game, [(attacker, "B")])
        self.until(game, lambda current: self.trigger_count(current, source) == 1)
        sealed = game.state.stack[-1].context[EVENT_COUNTER_CONTEXT]
        self.assertEqual(attacker.logical_object_id, sealed["logical_object_id"])
        self.cast_for(game, "A", bounce, [attacker.ref])
        self.resolve_top(game)
        self.assertEqual("hand", game.state.cards[attacker.object_id].zone)
        game = self.choose_return(game, "apply", replay=True)
        self.assertEqual("hand", game.state.cards[attacker.object_id].zone)
        self.assertEqual(0, self.trigger_count(game, source))
        self.replay(game, load=True)
