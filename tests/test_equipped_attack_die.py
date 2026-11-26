"""Native equipped attack, one committed die, growth and private draw replay."""

import json
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.equipped_attack_die import CAPABILITY, DRAW_FIELD, ROLL_FIELD
from scripts.build_test_database import build_fixture_database


class EquippedAttackDieTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "equipped-attack-die.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures/equipped-attack-die-cards.json"], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        commander = "Isamaru, Hound of Konda"
        cls.deck = DeckDefinition("Equipped attack die", [
            DeckEntry(commander, 1, "commander"), DeckEntry("Plains", 30),
        ], [commander])

    def compile(self, card):
        return compile_oracle_card(card, capability_registry=self.registry, capability_profile="commander_review")

    def test_exact_equipped_attack_d6_and_complete_order_fail_closed(self):
        record = self.db.lookup("Strength-Testing Hammer", fuzzy=False)
        ir = self.compile(record)
        self.assertEqual("exact", ir.status, ir.material_residuals)
        trigger, equip = ir.faces[0].nodes
        self.assertEqual("creature.attacks", trigger.event)
        self.assertIn(CAPABILITY, trigger.capability_dependencies)
        self.assertIsNone(trigger.target_schema)
        self.assertEqual(1, trigger.effects[0][ROLL_FIELD])
        self.assertEqual("$event.permanent_object", trigger.effects[0]["card"])
        self.assertEqual(1, trigger.effects[1][DRAW_FIELD])
        self.assertIs(True, trigger.effects[1]["private"])
        self.assertIn("attachment.equip.fixed_mana", equip.capability_dependencies)
        for old, new in (("six-sided", "twenty-sided"), ("equipped creature", "enchanted creature"),
                         ("Then if it has", "Before it has")):
            with self.subTest(old=old):
                altered = self.compile(replace(record, oracle_text=record.oracle_text.replace(old, new)))
                self.assertNotEqual("exact", altered.status)
        game = self.session(746040000)
        hammer = self.add(game.engine, record.name)
        self.assert_closed(game, hammer)
        program, = (row for row in game.engine.semantics.programs_for_oracle(hammer.oracle_id)
                    if row.event == "creature.attacks")
        for changed in (replace(program, event_condition=None), replace(program, effects=tuple(reversed(program.effects)))):
            with self.subTest(changed=changed.event_condition):
                result = bind_semantic_program_runtime(changed, capability_registry=self.registry, profile="commander_review")
                self.assertFalse(result["strict"], result)

    def start_attack(self, seed, *, scene):
        game = self.session(seed)
        hammer = self.add(game.engine, "Strength-Testing Hammer")
        attacker = self.add(game.engine, "Grizzly Bears", ref="hammer-attacker")
        attacker.acquired_control_turn_count = -1
        competitor = self.add(game.engine, "Grizzly Bears", seat="C", ref="hammer-other-creature")
        competitor.counters["+1/+1"] = {"stronger": 9, "replacement": 6, "blink": 1}[scene]
        private = self.add(game.engine, "Plains", zone="hand", ref="hammer-private-discard")
        response = None
        if scene == "replacement":
            self.add(game.engine, "Chains of Mephistopheles", seat="B")
            response = self.add(game.engine, "Disenchant", seat="B", zone="hand")
            for i in range(2):
                self.add(game.engine, "Plains", seat="B", ref=f"hammer-response-land-{i}")
        if scene == "blink":
            response = self.add(game.engine, "Cloudshift", zone="hand")
            self.add(game.engine, "Plains", ref="hammer-blink-land")
        for card in (hammer, response):
            if card:
                self.assert_closed(game, card)
        self.prepare(game, mana={"C": 3})
        self.checkpoint(game)
        offers = game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        equip, = [offer for offer in offers if offer["id"].startswith(f"activate:{hammer.ref}:")]
        result = game.act("pilot:A", {"action_id": equip["id"], "targets": [attacker.ref], "pay": "auto"})
        self.assertTrue(result.ok, result.summary)
        self.resolve_top(game)
        self.assertEqual(attacker.object_id, game.state.cards[hammer.object_id].attached_to)
        self.attack(game, [(attacker, "B")])
        self.assertEqual(1, self.trigger_count(game, hammer))
        trigger = game.state.stack[-1]
        self.assertFalse(self.roll_events(game, trigger))
        return game, hammer, attacker, competitor, private, response, trigger

    def roll_events(self, game, item):
        return [event for event in game.state.events
                if event.code == "die.roll" and event.details.get("stack") == item.ref]

    def roll(self, game, item):
        event, = self.roll_events(game, item)
        self.assertEqual(("A", 6), (event.details["player"], event.details["sides"]))
        self.assertIn(event.details["result"], range(1, 7))
        return event.details["result"]

    def test_actual_hammer_removal_keeps_original_attacker_and_draw_replacement_replays_once(self):
        game, hammer, attacker, competitor, private, response, trigger = self.start_attack(746040001, scene="replacement")
        self.cast_for(game, "B", response, [hammer.ref])
        self.resolve_top(game)
        self.assertEqual("graveyard", game.state.cards[hammer.object_id].zone)
        prior_power = game.engine._numeric_stat(attacker.object_id, "power")
        self.assertLess(prior_power, game.engine._numeric_stat(competitor.object_id, "power"))
        game = self.replay(game, load=True)
        pending = self.resolve(game)
        self.assertEqual(("draw.discard", ["A"]), (pending.kind, pending.actors))
        value = self.roll(game, trigger)
        self.assertEqual(prior_power + value, game.engine._numeric_stat(attacker.object_id, "power"))
        self.assertEqual(prior_power + value, game.engine._numeric_stat(competitor.object_id, "power"))
        self.assertNotIn(private.ref, json.dumps(game.packet("pilot:C", full=True)))
        before = authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:C", {"action_id": "choose", "card": private.ref}).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        game = self.replay(game, load=True)
        self.assertEqual(value, self.roll(game, trigger))
        library = game.state.players["A"].zones["library"]
        library_size, library_head = len(library), library[-1]
        choice = game.act("pilot:A", {"action_id": "choose", "card": private.ref})
        self.assertTrue(choice.ok, choice.summary)
        self.assertIn(library_head, game.state.players["A"].zones["hand"])
        self.assertEqual(library_size - 1, len(game.state.players["A"].zones["library"]))
        self.assertEqual("graveyard", game.state.cards[private.object_id].zone)
        self.assertEqual(value, self.roll(game, trigger))
        self.replay(game, load=True)

    def test_actual_greater_power_and_reentered_attacker_skip_the_draw(self):
        for number, scene in enumerate(("stronger", "blink")):
            with self.subTest(scene=scene):
                game, _, attacker, _, _, response, trigger = self.start_attack(746040010 + number, scene=scene)
                if response is not None:
                    old = game.state.cards[attacker.object_id].logical_object_id
                    self.cast_for(game, "A", response, [attacker.ref])
                    self.resolve_top(game)
                    self.assertNotEqual(old, game.state.cards[attacker.object_id].logical_object_id)
                game = self.replay(game, load=True)
                hand = tuple(game.state.players["A"].zones["hand"])
                self.assertIsNone(self.resolve(game))
                value = self.roll(game, trigger)
                self.assertEqual(2 if scene == "blink" else 2 + value,
                                 game.engine._numeric_stat(attacker.object_id, "power"))
                self.assertEqual(hand, tuple(game.state.players["A"].zones["hand"]))
                game = self.replay(game, load=True)
                self.assertEqual(value, self.roll(game, trigger))
