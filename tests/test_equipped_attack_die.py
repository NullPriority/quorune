"""Native equipped attack, one committed die, growth and private draw replay."""

import json
from dataclasses import replace
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
from quorune.rules.die_roll_occurrence import plan_fixed_die_roll
from quorune.rules.equipped_attack_die import CAPABILITY, DRAW_FIELD, ROLL_FIELD
from quorune.rules.retained_die_roll import REPLACEMENT_CAPABILITY, RETAINED_EVENT, TRIGGER_CAPABILITY
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


WYLL = "Wyll, Blade of Frontiers"


class RetainedDiceRuntimeTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "retained-dice.sqlite3"
        fixtures = ROOT / "tests/fixtures"
        build_fixture_database([fixtures / "equipped-attack-die-cards.json",
                                fixtures / "single-d20-draw-cards.json"], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        commander = "Isamaru, Hound of Konda"
        cls.deck = DeckDefinition("Retained dice", [
            DeckEntry(commander, 1, "commander"), DeckEntry("Plains", 37),
        ], [commander])

    def _act(self, game, actor="A", *, valid=True, **command):
        before = authoritative_state_hash(game.state)
        result = game.act("pilot:" + actor, command)
        self.assertEqual(valid, result.ok, result.summary)
        if not valid:
            self.assertEqual(before, authoritative_state_hash(game.state))

    def _roll_event(self, game, trigger, *, sides, value):
        event, = [event for event in game.state.events if event.code == "die.roll"]
        self.assertEqual({"player": "A", "sides": sides, "result": value, "stack": trigger.ref},
                         event.details)

    def _hammer(self, seed):
        game = self.session(seed)
        hammer = self.add(game.engine, "Strength-Testing Hammer")
        attacker = self.add(game.engine, "Grizzly Bears", ref="retained-d6-attacker")
        attacker.acquired_control_turn_count = -1
        wyll = self.add(game.engine, WYLL, seat="B", ref="borrowed-wyll")
        foreign = self.add(game.engine, WYLL, seat="B", ref="opposing-wyll")
        game.engine.change_control(wyll.object_id, "A", reason="Borrow before the witnessed roll")
        guide = self.add(game.engine, "Pixie Guide")
        for source in (hammer, wyll, guide):
            self.assert_closed(game, source)
        self.prepare(game, mana={"C": 3})
        self.checkpoint(game)
        offers = game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        equip, = [offer["id"] for offer in offers if offer["id"].startswith(f"activate:{hammer.ref}:")]
        self._act(game, action_id=equip, targets=[attacker.ref], pay="auto")
        self.resolve_top(game)
        self.attack(game, [(attacker, "D")])
        trigger, = [item for item in game.state.stack if item.source_object_id == hammer.object_id]
        return game, attacker, wyll, foreign, trigger

    def test_original_closes_replacement_trigger_and_background_with_separate_authority(self):
        original = self.db.lookup(WYLL, fuzzy=False)
        binding, generated = register_card(self.db, self.registry, WYLL)
        self.assertTrue(binding["strict_capability_ready"], binding["blockers"])
        ir = compile_oracle_card(original, capability_registry=self.registry, capability_profile="commander_review")
        self.assertEqual("exact", ir.status, ir.material_residuals)
        for node in ir.faces[0].nodes:
            self.assertEqual(node.text, original.oracle_text[node.span.start:node.span.end])
        self.assertTrue(any(REPLACEMENT_CAPABILITY in node.capability_dependencies for node in ir.faces[0].nodes))
        self.assertTrue(any("format.commander.pairing.choose_background" in node.capability_dependencies
                            for node in ir.faces[0].nodes))
        trigger, = [program for program in generated.programs_for_oracle(original.oracle_id)
                    if program.event == RETAINED_EVENT]
        self.assertIn(TRIGGER_CAPABILITY, trigger.capability_dependencies)
        self.assertFalse(bind_semantic_program_runtime(
            replace(trigger, event_condition={"field": "player", "op": "eq", "value": "opponent"}),
            capability_registry=self.registry, profile="commander_review",
        )["strict"])
        with patch("quorune.compiler.retained_die_roll.extra_die_replacement_node", return_value=None):
            self.assertNotEqual("exact", compile_oracle_card(
                original, capability_registry=self.registry, capability_profile="commander_review",
            ).status)
        with patch("quorune.compiler.retained_die_roll.retained_dice_counter_node", return_value=None):
            self.assertNotEqual("exact", compile_oracle_card(
                original, capability_registry=self.registry, capability_profile="commander_review",
            ).status)

    def test_native_d6_uses_both_controller_replacements_and_replays_explicit_tie(self):
        for seed in range(706100, 706116):
            game, attacker, wyll, foreign, trigger = self._hammer(seed)
            expected = plan_fixed_die_roll(6, seed=seed, instruction_key=f"{trigger.ref}|0", seat="A",
                                           extra_rolls=2, ignore_lowest=2)
            ties = tuple(index for index, value in enumerate(expected.samples) if value == expected.single_result)
            if len(ties) == 2:
                break
        else:
            self.fail("The native fixture did not reach a tied retention boundary")
        pending = self.resolve(game)
        self.assertEqual(("semantic.choice", ["A"]), (pending.kind, pending.actors))
        decision = game.packet("pilot:A", full=True)["decision"]
        options = tuple(decision["legal_actions"][0]["choice_schema"]["legal_values"])
        self.assertEqual(tuple(f"die:{index + 1}" for index in ties), options)
        self.assertFalse(any(event.code == "die.roll" for event in game.state.events))
        self.assertEqual(0, game.state.cards[wyll.object_id].counters.get("+1/+1", 0))
        ignored = next(index for index in range(3) if index not in ties)
        self._act(game, valid=False, action_id="choose", choice=f"die:{ignored + 1}")
        self._act(game, "B", valid=False, action_id="choose", choice=options[0])
        game = self.replay(game, load=True)
        self._act(game, action_id="choose", choice=options[0])
        self.assertIsNone(self.resolve(game))
        self._roll_event(game, trigger, sides=6, value=expected.single_result)
        self.assertEqual(2 + expected.single_result, game.engine._numeric_stat(attacker.object_id, "power"))
        self.assertEqual(("B", "A", 1), (game.state.cards[wyll.object_id].owner,
                                         game.state.cards[wyll.object_id].controller,
                                         game.state.cards[wyll.object_id].counters.get("+1/+1", 0)))
        self.assertEqual(0, game.state.cards[foreign.object_id].counters.get("+1/+1", 0))
        self.replay(game, load=True)

    def test_real_d20_feeds_the_retained_face_to_draw_and_triggers_once(self):
        for seed in range(706120, 706132):
            game = self.session(seed)
            dragon = self.add(game.engine, "Ancient Silver Dragon")
            dragon.acquired_control_turn_count = -1
            wyll = self.add(game.engine, WYLL)
            for source in (dragon, wyll):
                self.assert_closed(game, source)
            self.prepare(game)
            self.checkpoint(game)
            self.attack(game, [(dragon, "D")])
            self.until(game, lambda current: self.trigger_count(current, dragon) == 1)
            trigger, = [item for item in game.state.stack if item.source_object_id == dragon.object_id]
            expected = plan_fixed_die_roll(20, seed=seed, instruction_key=f"{trigger.ref}|0", seat="A",
                                           extra_rolls=1, ignore_lowest=1)
            if expected.samples[1] > expected.samples[0]:
                break
        else:
            self.fail("The original Dragon fixture did not reach a larger extra result")
        hand = len(game.state.players["A"].zones["hand"])
        self.assertIsNone(self.resolve(game))
        self._roll_event(game, trigger, sides=20, value=expected.single_result)
        self.assertEqual(hand + expected.single_result, len(game.state.players["A"].zones["hand"]))
        self.assertEqual(1, game.state.cards[wyll.object_id].counters.get("+1/+1", 0))
        self.replay(game, load=True)


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


class NightShiftDiceTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        cls.addClassCleanup(cls.temporary.cleanup)
        fixtures = ROOT / "tests" / "fixtures"
        path = Path(cls.temporary.name) / "night-shift-dice.sqlite3"
        build_fixture_database([
            fixtures / "night-shift-dice-cards.json",
            fixtures / "thunderwave-cards.json",
            fixtures / "equipped-attack-die-cards.json",
        ], path)
        cls.db = CardDatabase(path)
        cls.addClassCleanup(cls.db.close)
        cls.registry = load_default_capability_registry()
        commander = "Isamaru, Hound of Konda"
        cls.deck = DeckDefinition("Night Shift dice", [
            DeckEntry(commander, 1, "commander"), DeckEntry("Plains", 30),
        ], [commander])

    def scene(self, natural, *, retained_tie=False, source_controller="A", second=False, two_sources=False):
        from quorune.rules.random_selection import roll_fixed_die

        def has_faces(seed):
            keys = ("S1|0", "S1|0|die:2") if retained_tie else ("S1|0",)
            return all(roll_fixed_die(20, seed=seed, instruction_key=key, seat="A") == natural for key in keys)

        seed = next(seed for seed in range(706_100, 710_100) if has_faces(seed))
        game = self.session(seed)
        source = self.add(game.engine, "Night Shift of the Living Dead", seat=source_controller, ref="NIGHT-SHIFT")
        if two_sources:
            self.add(game.engine, "Night Shift of the Living Dead", ref="NIGHT-SHIFT-SECOND-SOURCE")
        wave = self.add(game.engine, "Thunderwave", zone="hand", ref="NIGHT-SHIFT-WAVE")
        followup = (self.add(game.engine, "Thunderwave", zone="hand", ref="NIGHT-SHIFT-FOLLOWUP")
                    if second else None)
        latest_wyll = self.add(game.engine, "Wyll, Blade of Frontiers", ref="NIGHT-SHIFT-WYLL") if retained_tie else None
        if latest_wyll:
            latest_wyll.counters["+1/+1"] = 3
        own = self.add(game.engine, "Colossal Dreadmaw", ref="NIGHT-SHIFT-OWN")
        opponent = self.add(game.engine, "Colossal Dreadmaw", seat="B", ref="NIGHT-SHIFT-OPPONENT")
        private = self.add(game.engine, "Plains", seat="C", zone="hand", ref="NIGHT-SHIFT-PRIVATE")
        for card in (source, wave, latest_wyll):
            if card:
                self.assert_closed(game, card)
        game.state.config.semantic_policy = "trusted_only"
        self.prepare(game, mana={"C": 9, "R": 5})
        self.checkpoint(game)
        self.cast(game, "A", wave)
        self.assertEqual("S1", game.state.stack[-1].ref)
        self.assertEqual([], self.rolls(game))
        return game, source, own, opponent, private, latest_wyll, followup

    @staticmethod
    def rolls(game):
        return [event.details["result"] for event in game.state.events if event.code == "die.roll"]

    @staticmethod
    def paid(game):
        return [event for event in game.state.events
                if event.code == "life.pay" and event.details.get("reason") == "night_shift_die_adjustment"]

    @staticmethod
    def employees(game):
        from quorune.fixed_token_production import fixed_ordinary_token_name

        name = fixed_ordinary_token_name("Zombie Employee")
        return [card for card in game.state.cards.values()
                if card.zone == "battlefield" and card.is_token and card.printed_name == name]

    def choose(self, game, option):
        result = game.act("pilot:A", {"action_id": "choose", "choice": option})
        self.assertTrue(result.ok, result.summary)

    def adjustment_choice(self, game, value, source):
        choice = self.resolve(game)
        self.assertEqual(("semantic.choice", ["A"]), (choice.kind, choice.actors))
        packet = game.packet("pilot:A", full=True)["decision"]
        self.assertIn(f"Retained die results: {value}.", json.dumps(packet))
        options = packet["ctx"]["options"]
        self.assertEqual({"decline", f"{source.ref}:die:1:-1", f"{source.ref}:die:1:+1"},
                         {option["id"] for option in options})
        return packet

    def test_night_shift_pay_once_and_final_six_survive_reload(self):
        from unittest.mock import patch
        from quorune.rules.night_shift_dice import TURN_FIELD

        game, source, _, opponent, private, _, followup = self.scene(5, second=True)
        price_before = game.state.players["A"].life
        packet = self.adjustment_choice(game, 5, source)
        self.assertNotIn(private.ref, json.dumps(packet))
        self.assertIsNone(game.packet("pilot:B", full=True)["decision"])
        command = {"action_id": "choose", "choice": f"{source.ref}:die:1:+1"}
        before = authoritative_state_hash(game.state)
        for seat in "BCD":
            self.assertFalse(game.act("pilot:" + seat, command).ok)
        bad = {**command, "choice": f"{source.ref}:die:2:+1"}
        self.assertFalse(game.act("pilot:A", bad).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        self.assertEqual([], self.rolls(game))
        game = self.replay(game, load=True)
        with patch("quorune.rules.night_shift_dice.plan_replaced_game_die",
                   side_effect=AssertionError("Resuming cannot roll again")):
            self.choose(game, command["choice"])
            self.assertIsNone(self.resolve(game))
        self.assertEqual(price_before - 1, game.state.players["A"].life)
        self.assertEqual(1, len(self.paid(game)))
        self.assertEqual([6], self.rolls(game))
        token, = self.employees(game)
        self.assertEqual("A", token.controller)
        self.assertEqual(("2", "2"), tuple(str(game.engine._numeric_stat(token.object_id, key))
                                          for key in ("power", "toughness")))
        profile = game.engine._effective_card_data(token)
        self.assertEqual(({"creature"}, {"zombie", "employee"}), game.engine._effective_type_parts(token)[:2])
        self.assertEqual({"B"}, set(profile["colors"]))
        self.assertEqual(3, game.state.cards[opponent.object_id].marked_damage)
        self.assertEqual(game.state.turn_sequence, game.state.cards[source.object_id].annotations[TURN_FIELD]["turn"])
        self.cast(game, "A", followup)
        pending = self.resolve(game)
        if pending is not None:
            self.assertNotIn("Retained die results:", json.dumps(game.packet("pilot:A", full=True)))
            self.assertTrue(game.act("pilot:A", {"action_id": "choose", "objects": []}).ok)
            self.assertIsNone(self.resolve(game))
        self.assertEqual(2, len(self.rolls(game)))
        self.assertEqual(1, len(self.paid(game)))
        self.replay(game, load=True)

    def test_night_shift_adjusted_zero_and_twenty_one_skip_the_printed_thunderwave_table(self):
        for natural, direction in ((1, -1), (20, 1)):
            with self.subTest(natural=natural):
                game, source, own, opponent, _, _, _ = self.scene(natural)
                self.adjustment_choice(game, natural, source)
                game = self.replay(game, load=True)
                self.choose(game, f"{source.ref}:die:1:{direction:+d}")
                self.assertIsNone(self.resolve(game))
                self.assertEqual([natural + direction], self.rolls(game))
                self.assertEqual(1, len(self.paid(game)))
                self.assertEqual(0, game.state.cards[own.object_id].marked_damage)
                self.assertEqual(0, game.state.cards[opponent.object_id].marked_damage)
                self.assertEqual([], self.employees(game))
                self.replay(game, load=True)

    def test_night_shift_adjusts_only_after_wyll_finishes_the_tied_choice(self):
        from quorune.rules.night_shift_dice import CONTINUATION_CONTEXT, DieAdjustmentSelection

        game, source, _, _, _, wyll, _ = self.scene(5, retained_tie=True)
        pending = self.resolve(game)
        self.assertEqual(("semantic.choice", ["A"]), (pending.kind, pending.actors))
        choice = game.packet("pilot:A", full=True)["decision"]["ctx"]["options"]
        self.assertEqual({"die:1", "die:2"}, {row["id"] for row in choice})
        self.assertEqual([], self.rolls(game))
        game = self.replay(game, load=True)
        self.choose(game, "die:2")
        self.adjustment_choice(game, 5, source)
        item, = game.state.stack
        receipt = DieAdjustmentSelection.from_dict(item.context[CONTINUATION_CONTEXT])
        self.assertEqual((5, 5), receipt.occurrence.samples)
        self.assertEqual((0,), receipt.occurrence.ignored_indices)
        game = self.replay(game, load=True)
        self.choose(game, f"{source.ref}:die:1:+1")
        pending = self.resolve(game)
        if pending is not None and pending.kind == "trigger.order":
            order = [item.ref for batch in game.state.pending_trigger_batches for item in batch.items]
            self.assertEqual(2, len(order))
            result = game.act("pilot:A", {"action_id": "order", "triggers": order})
            self.assertTrue(result.ok, result.summary)
            self.assertIsNone(self.resolve(game))
        self.assertEqual([6], self.rolls(game))
        self.assertEqual(1, len(self.paid(game)))
        self.assertEqual(4, game.state.cards[wyll.object_id].counters.get("+1/+1", 0))
        self.assertEqual(1, len(self.employees(game)))
        self.replay(game, load=True)

    def test_night_shift_decline_keeps_the_natural_six_and_a_payment_changes_it(self):
        for controller, option, expected in (("A", "decline", 6), ("A", "decrease", 5), ("B", None, 6)):
            with self.subTest(controller=controller, option=option):
                game, source, _, _, _, _, _ = self.scene(6, source_controller=controller)
                if option is not None:
                    self.adjustment_choice(game, 6, source)
                    self.choose(game, option if option == "decline" else f"{source.ref}:die:1:-1")
                self.assertIsNone(self.resolve(game))
                self.assertEqual([expected], self.rolls(game))
                self.assertEqual(int(option == "decrease"), len(self.paid(game)))
                self.assertEqual(int(controller == "A" and option == "decline"), len(self.employees(game)))
                self.replay(game, load=True)

    def test_night_shift_two_sources_preserve_each_payment_in_the_same_roll(self):
        from quorune.rules.night_shift_dice import CONTINUATION_CONTEXT, DieAdjustmentSelection

        for natural, direction in ((4, 1), (1, -1)):
            with self.subTest(natural=natural):
                game, first, _, opponent, _, _, _ = self.scene(natural, two_sources=True)
                second = next(card for card in game.state.cards.values() if card.ref == "NIGHT-SHIFT-SECOND-SOURCE")
                price_before = game.state.players["A"].life
                pending = self.resolve(game)
                self.assertEqual(("semantic.choice", ["A"]), (pending.kind, pending.actors))
                options = game.packet("pilot:A", full=True)["decision"]["ctx"]["options"]
                self.assertEqual(5, len(options))
                self.choose(game, f"{first.ref}:die:1:{direction:+d}")
                self.adjustment_choice(game, natural + direction, second)
                self.assertEqual(price_before - 1, game.state.players["A"].life)
                self.assertEqual(1, len(self.paid(game)))
                self.assertEqual([], self.rolls(game))
                item, = game.state.stack
                receipt = item.context[CONTINUATION_CONTEXT]
                sealed = DieAdjustmentSelection.from_dict(receipt)
                self.assertEqual((natural,), sealed.occurrence.samples)
                self.assertEqual((natural + direction,), sealed.occurrence.results)
                with self.assertRaises(ValueError):
                    DieAdjustmentSelection.from_dict({**receipt, "results": [natural]})
                game = self.replay(game, load=True)
                self.choose(game, f"{second.ref}:die:1:{direction:+d}")
                pending = self.resolve(game)
                if pending is not None and pending.kind == "trigger.order":
                    refs = [item.ref for batch in game.state.pending_trigger_batches for item in batch.items]
                    self.assertEqual(2, len(refs))
                    self.assertTrue(game.act("pilot:A", {"action_id": "order", "triggers": refs}).ok)
                    self.assertIsNone(self.resolve(game))
                self.assertEqual([natural + 2 * direction], self.rolls(game))
                self.assertEqual(2, len(self.paid(game)))
                self.assertEqual(price_before - 2, game.state.players["A"].life)
                self.assertEqual(2 if direction > 0 else 0, len(self.employees(game)))
                self.assertEqual(3 if direction > 0 else 0, game.state.cards[opponent.object_id].marked_damage)
                self.replay(game, load=True)

    def test_night_shift_requires_both_exact_abilities_and_the_complete_token(self):
        from quorune.rules.night_shift_dice import ADJUSTMENT_CAPABILITY, TOKEN_CAPABILITY

        source = self.db.lookup("Night Shift of the Living Dead", fuzzy=False)

        def compile_card(record):
            return compile_oracle_card(record, capability_registry=self.registry, capability_profile="commander_review")

        from quorune.rules.die_roll_occurrence import FixedDieRollOccurrence
        from quorune.rules.night_shift_dice import DieAdjustmentSelection, PaidDieAdjustment

        numeric = FixedDieRollOccurrence("A", "S1|0", 6, (1, 6, 3), (0,), (6, 2))
        two_dice = DieAdjustmentSelection("S1", 0, 1, numeric, (PaidDieAdjustment("enchantment", 1, -1),))
        self.assertEqual((6, 2), DieAdjustmentSelection.from_dict(two_dice.to_dict()).occurrence.results)

        ir = compile_card(source)
        self.assertEqual("exact", ir.status, ir.material_residuals)
        adjust, trigger = ir.faces[0].nodes
        self.assertIn(ADJUSTMENT_CAPABILITY, adjust.capability_dependencies)
        self.assertIn(TOKEN_CAPABILITY, trigger.capability_dependencies)
        for original, changed in (("pay 1 life", "pay 2 life"), ("only once", "twice"),
                                  ("by 1", "by 2"), ("roll a 6", "roll a 5"), ("Zombie Employee", "Zombie")):
            with self.subTest(original=original):
                altered = replace(source, oracle_text=source.oracle_text.replace(original, changed))
                self.assertNotEqual("exact", compile_card(altered).status)
