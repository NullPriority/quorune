"""Witness a public roll feeding an ordinary draw before a durable player rule."""

from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.maximum_hand_size import effective_maximum_hand_size
from quorune.oracle_ir import compile_oracle_card
from quorune.permissions import PermissionDenied
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.random_selection import publish_resolution_die_roll, roll_fixed_die
from scripts.build_test_database import build_fixture_database

SOURCE = "Generic Storm Reckoner"
PROFILE = "commander_review"


class PublicResolutionDieRollTests(unittest.TestCase):
    def test_public_roll_ordinals_preserve_first_face_and_replay_order(self):
        events = []
        seed = 4611
        host = SimpleNamespace(
            state=SimpleNamespace(config=SimpleNamespace(seed=seed)),
            _log=lambda *event, **metadata: events.append((event, metadata)),
        )
        scope = dict(controller="A", stack_ref="S17", instruction_pointer=2, sides=20)
        expected_keys = ("S17|2", "S17|2|roll:2", "S17|2|roll:3")
        expected_faces = [roll_fixed_die(20, seed=seed, instruction_key=key, seat="A") for key in expected_keys]
        with patch("quorune.rules.random_selection.roll_fixed_die", wraps=roll_fixed_die) as roll:
            faces = [publish_resolution_die_roll(host, **scope, roll_ordinal=index) for index in (1, 2, 3)]
        self.assertEqual(expected_faces, faces)
        self.assertEqual(list(expected_keys), [call.kwargs["instruction_key"] for call in roll.call_args_list])
        self.assertEqual([
            (("A", "die.roll", f"A rolled {face} on a d20.",
              {"player": "A", "sides": 20, "result": face, "stack": "S17"}), {"importance": 2})
            for face in faces
        ], events)
        events.clear()
        self.assertEqual(faces[1], publish_resolution_die_roll(host, **scope, roll_ordinal=2))
        self.assertEqual(1, len(events))
        for value in (0, True, -1):
            with self.subTest(value=value), self.assertRaises(ValueError):
                publish_resolution_die_roll(host, **scope, roll_ordinal=value)
        self.assertEqual(1, len(events))


class SingleD20DrawTests(InheritedNativeFixture, unittest.TestCase):
    source_name = SOURCE
    expected_power = 3
    casting_mana = {"U": 1, "C": 1}
    haste_provider = None

    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        cls.addClassCleanup(cls.temporary.cleanup)
        path = Path(cls.temporary.name) / "single-d20-draw.sqlite3"
        fixtures = ROOT / "tests" / "fixtures"
        build_fixture_database([
            fixtures / "single-d20-draw-cards.json",
            fixtures / "controller-hand-count-game-size-test-cards.json",
            fixtures / "draw-discard-replacement-cards.json",
            fixtures / "combat-damage-dealer-recipient-control-cards.json",
            fixtures / "source-keyword-choice-cards.json",
        ], path)
        cls.db = CardDatabase(path)
        cls.addClassCleanup(cls.db.close)
        cls.registry = load_default_capability_registry()
        commander = "Isamaru, Hound of Konda"
        cls.deck = DeckDefinition("Single d20 draw", [
            DeckEntry(commander, 1, "commander"), DeckEntry("Plains", 37),
        ], [commander])

    @classmethod
    def tearDownClass(cls):
        pass  # addClassCleanup also closes partial setup on failure.

    def roll_events(self, game):
        return tuple(event for event in game.state.events if event.code == "die.roll")

    def test_delina_complete_table_and_child_share_closed_source_provenance(self):
        from quorune.rules.targeted_attack_die_copy import CLEANUP_TEMPLATE, ROLL_TEMPLATE

        card = self.db.lookup("Delina, Wild Mage", fuzzy=False)
        ir = compile_oracle_card(card, capability_registry=self.registry, capability_profile=PROFILE)
        self.assertEqual("exact", ir.status, ir.material_residuals)
        parent, child = ir.faces[0].nodes
        self.assertEqual((ROLL_TEMPLATE, CLEANUP_TEMPLATE), (parent.template_id, child.template_id))
        self.assertEqual(card.oracle_text, parent.text)
        self.assertEqual("At end of combat, exile this token.", child.text)
        self.assertEqual("you", parent.target_schema["controller_relation"])
        for altered in (
            card.oracle_text.replace("15—20", "16—20"),
            card.oracle_text.replace("You may roll again.", "Roll again."),
            card.oracle_text.replace("exile this token", "sacrifice this token"),
        ):
            self.assertNotEqual("exact", compile_oracle_card(
                replace(card, oracle_text=altered), capability_registry=self.registry, capability_profile=PROFILE,
            ).status)

    def test_delina_missing_intrinsic_trigger_mutation_leaves_the_whole_table_residual(self):
        card = self.db.lookup("Delina, Wild Mage", fuzzy=False)
        with patch("quorune.compiler.targeted_attack_die_copy.attached_granted_ability_plan", return_value=None):
            ir = compile_oracle_card(card, capability_registry=self.registry, capability_profile=PROFILE)
        self.assertNotEqual("exact", ir.status)
        self.assertEqual(3, len(ir.material_residuals))

    def delina_attack(self, seed, *, bounce=False):
        game = self.session(seed)
        source = self.add(game.engine, "Delina, Wild Mage")
        original = self.add(game.engine, "Isamaru, Hound of Konda", ref="delina-controlled-legend")
        foreign = self.add(game.engine, "Isamaru, Hound of Konda", seat="B", ref="delina-opposing-legend")
        if bounce:
            self.add(game.engine, "Unsummon", seat="B", zone="hand", ref="delina-target-bounce")
            self.add(game.engine, "Island", seat="B", ref="delina-bounce-mana")
        self.assert_closed(game, source)
        self.prepare(game, mana={})
        self.checkpoint(game)
        self.attack(game, [(source, "D")])
        pending = game.state.pending_decision
        self.assertEqual(("semantic.target", ["A"]), (pending.kind, pending.actors))
        self.assertFalse(self.roll_events(game))
        before = authoritative_state_hash(game.state)
        result = game.act("pilot:A", {"action_id": "choose", "targets": [foreign.ref]})
        self.assertFalse(result.ok, result.summary)
        self.assertEqual(before, authoritative_state_hash(game.state))
        accepted = game.act("pilot:A", {"action_id": "choose", "targets": [original.ref]})
        self.assertTrue(accepted.ok, accepted.summary)
        return game, source, original

    def choose_delina_token_destination(self, game, seat):
        context = game.packet("pilot:A", full=True)["decision"]["ctx"]
        self.assertEqual("choose_fixed_attacking_token_destinations", context["operation"])
        self.assertIn(seat, context["destinations"])
        command = {"action_id": "choose", "attacking": {"attacking:0": seat}}
        before = authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:B", command).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        result = game.act("pilot:A", command)
        self.assertTrue(result.ok, result.summary)

    def test_delina_native_repeats_after_token_and_exiles_both_as_intrinsic_triggers(self):
        game, _, original = self.delina_attack(706005)
        self.assertEqual("semantic.choice", self.resolve(game).kind)
        self.assertGreaterEqual(self.roll_events(game)[0].details["result"], 15)
        self.choose_delina_token_destination(game, "C")
        first, = (card for card in game.state.cards.values() if card.zone == "battlefield" and card.is_token)
        self.assertEqual(1, len(self.roll_events(game)))
        self.assertEqual("semantic.choice", game.state.pending_decision.kind)
        self.assertEqual("offer_optional_effect", game.packet("pilot:A", full=True)["decision"]["ctx"]["operation"])
        game = self.replay(game, load=True)
        result = game.act("pilot:A", {"action_id": "choose", "choice": "apply"})
        self.assertTrue(result.ok, result.summary)
        self.assertEqual(2, len(self.roll_events(game)))
        self.choose_delina_token_destination(game, "B")
        if self.roll_events(game)[-1].details["result"] >= 15:
            decline = game.act("pilot:A", {"action_id": "choose", "choice": "decline"})
            self.assertTrue(decline.ok, decline.summary)
        tokens = [card for card in game.state.cards.values() if card.zone == "battlefield" and card.is_token]
        self.assertEqual(2, len(tokens))
        self.assertTrue(all(token.tapped and token.attacking for token in tokens))
        self.assertEqual({"B", "C"}, {game.state.combat.attackers[token.object_id] for token in tokens})
        self.assertTrue(all("Legendary" not in game.engine._effective_card_data(token)["type_line"] for token in tokens))
        self.assertIn(first.object_id, {token.object_id for token in tokens})
        self.assertEqual(original.oracle_id, tokens[0].oracle_id)
        for token in tokens:
            grant = game.engine._effective_card_data(token)["ability_fragments"]
            self.assertIn("At end of combat, exile this token.", json.dumps(grant))
        self.until(game, lambda current: current.state.step == "end_combat"
                   and all(current.state.cards.get(token.object_id) is None
                           or current.state.cards[token.object_id].zone != "battlefield" for token in tokens))
        self.assertEqual("battlefield", game.state.cards[original.object_id].zone)
        self.assertEqual(2, len(self.roll_events(game)))
        self.replay(game, load=True)

    def test_delina_native_high_roll_can_decline_after_one_token(self):
        game, _, _ = self.delina_attack(706005)
        self.assertEqual("semantic.choice", self.resolve(game).kind)
        self.assertGreaterEqual(self.roll_events(game)[0].details["result"], 15)
        self.choose_delina_token_destination(game, "B")
        result = game.act("pilot:A", {"action_id": "choose", "choice": "decline"})
        self.assertTrue(result.ok, result.summary)
        self.assertEqual(1, len(self.roll_events(game)))
        self.assertEqual(1, sum(card.is_token and card.zone == "battlefield" for card in game.state.cards.values()))
        self.replay(game, load=True)

    def test_delina_losing_the_announced_target_stops_the_roll_and_copy(self):
        game, _, original = self.delina_attack(706005, bounce=True)
        bounce = next(card for card in game.state.cards.values() if card.ref == "delina-target-bounce")
        self.cast_for(game, "B", bounce, [original.ref])
        self.assertIsNone(self.resolve(game))
        self.assertEqual("hand", game.state.cards[original.object_id].zone)
        self.assertFalse(self.roll_events(game))
        self.assertFalse(any(card.is_token for card in game.state.cards.values()))
        self.replay(game, load=True)

    def test_delina_low_roll_creates_the_same_token_without_a_repeat_offer(self):
        game, _, _ = self.delina_attack(706006)
        self.assertEqual("semantic.choice", self.resolve(game).kind)
        roll, = self.roll_events(game)
        self.assertEqual(14, roll.details["result"])
        self.choose_delina_token_destination(game, "B")
        self.assertNotEqual("semantic.choice", game.state.pending_decision.kind)
        token, = (card for card in game.state.cards.values() if card.is_token and card.zone == "battlefield")
        self.assertTrue(token.tapped)
        self.assertEqual("B", game.state.combat.attackers[token.object_id])
        self.replay(game, load=True)

    def attack_for_trigger(self, game, source, defender="D"):
        self.attack(game, [(source, defender)])
        self.until(game, lambda current: any(
            item.kind == "triggered_ability" and item.source_object_id == source.object_id
            for item in current.state.stack
        ))
        trigger, = game.state.stack
        self.assertEqual("A", trigger.controller)
        self.assertFalse(self.roll_events(game), "The die must remain unrolled before resolution")
        return trigger

    def draw_fixture(self, seed):
        game = self.session(seed)
        source = self.add(game.engine, self.source_name, zone="hand")
        if self.haste_provider is not None:
            self.add(game.engine, self.haste_provider)
        private = self.add(game.engine, "Plains", zone="hand", ref="roll-controller-private")
        self.add(game.engine, "Plains", zone="hand", ref="roll-extra-hand")
        self.assert_closed(game, source)
        self.prepare(game, mana=self.casting_mana)
        return game, source, private

    def cast_fixture(self, game, source):
        self.checkpoint(game)
        self.cast_for(game, "A", source)
        self.assertIsNone(self.resolve(game))
        current = game.state.cards[source.object_id]
        self.assertEqual("battlefield", current.zone)
        return current

    def assert_public_roll(self, game, trigger):
        event, = self.roll_events(game)
        self.assertEqual(("A", 20, trigger.ref), (
            event.actor, event.details["sides"], event.details["stack"],
        ))
        face = event.details["result"]
        self.assertIn(face, range(1, 21))
        self.assertTrue(set("ABCD").issubset(event.visibility))
        return face

    def test_hand_count_draw_reads_current_hand_and_keeps_the_game_duration_rule(self):
        game = self.session(706013)
        spell = self.add(game.engine, "Test Hall Census", zone="hand")
        response = self.add(game.engine, "Unsummon", zone="hand")
        other = self.add(game.engine, "Isamaru, Hound of Konda", seat="B")
        self.assert_closed(game, spell)
        self.prepare(game, mana={"U": 2})
        self.checkpoint(game)
        self.cast_for(game, "A", spell)
        announced_hand_count = len(game.state.players["A"].zones["hand"])
        self.cast_for(game, "A", response, [other.ref])
        self.resolve_top(game)
        self.assertEqual("hand", game.state.cards[other.object_id].zone)
        hands = {seat: set(game.state.players[seat].zones["hand"]) for seat in "ABCD"}
        self.assertEqual(announced_hand_count - 1, len(hands["A"]))
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        drawn = set(game.state.players["A"].zones["hand"]) - hands["A"]
        self.assertEqual(len(hands["A"]) + 1, len(drawn))
        self.assertEqual("graveyard", game.state.cards[spell.object_id].zone)
        self.assertIsNone(effective_maximum_hand_size(game.engine, "A"))
        self.assertFalse(self.roll_events(game))
        for seat in "BCD":
            self.assertEqual(hands[seat], set(game.state.players[seat].zones["hand"]))
            self.assertEqual(7, effective_maximum_hand_size(game.engine, seat))
            packet = json.dumps(game.packet(f"pilot:{seat}", full=True))
            for object_id in drawn:
                self.assertNotIn(game.state.cards[object_id].ref, packet)
        count = len(game.state.players["A"].zones["hand"])
        self.assertGreater(count, 7)
        self.until(game, lambda current: current.state.active_player != "A"
                   or current.state.pending_decision.kind == "cleanup.discard")
        self.assertNotEqual("A", game.state.active_player)
        self.assertEqual(count, len(game.state.players["A"].zones["hand"]))
        game = self.replay(game, load=True)
        self.assertIsNone(effective_maximum_hand_size(game.engine, "A"))

    def test_unmodified_roll_draws_for_locked_controller_and_cleanup_survives_departure(self):
        game, source, private = self.draw_fixture(706001)
        theft = self.add(game.engine, "Word of Seizing", seat="B", zone="hand")
        for index in range(5):
            self.add(game.engine, "Mountain", seat="B", ref=f"roll-theft-mana-{index}")
        bounce = self.add(game.engine, "Unsummon", seat="C", zone="hand")
        self.add(game.engine, "Island", seat="C", ref="roll-bounce-mana")
        source = self.cast_fixture(game, source)
        self.assertIn("flying", game.engine._combat_keywords(source))
        life = game.state.players["D"].life
        trigger = self.attack_for_trigger(game, source)
        self.assertEqual(life - self.expected_power, game.state.players["D"].life)
        self.cast_for(game, "B", theft, [source.ref])
        self.resolve_top(game)
        self.assertEqual("B", game.state.cards[source.object_id].controller)
        hands = {seat: set(game.state.players[seat].zones["hand"]) for seat in "ABCD"}
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        face = self.assert_public_roll(game, trigger)
        drawn = set(game.state.players["A"].zones["hand"]) - hands["A"]
        self.assertEqual(face, len(drawn), "The draw count must equal the public face")
        for seat in "BCD":
            self.assertEqual(hands[seat], set(game.state.players[seat].zones["hand"]))
            packet = json.dumps(game.packet(f"pilot:{seat}", full=True))
            self.assertNotIn(private.ref, packet)
            for object_id in drawn:
                self.assertNotIn(game.state.cards[object_id].ref, packet)
        self.assertIsNone(effective_maximum_hand_size(game.engine, "A"))
        self.assertEqual(7, effective_maximum_hand_size(game.engine, "B"))

        self.cast_for(game, "C", bounce, [source.ref])
        self.assertIsNone(self.resolve(game))
        self.assertEqual("hand", game.state.cards[source.object_id].zone)
        hand_count = len(game.state.players["A"].zones["hand"])
        self.assertGreater(hand_count, 7)
        self.until(game, lambda current: current.state.active_player != "A"
                   or current.state.pending_decision.kind == "cleanup.discard")
        self.assertNotEqual("A", game.state.active_player, "Cleanup must not request a discard")
        self.assertEqual(hand_count, len(game.state.players["A"].zones["hand"]))
        game = self.replay(game, load=True)
        self.assertIsNone(effective_maximum_hand_size(game.engine, "A"))

    def test_real_draw_replacement_choice_reloads_with_one_roll_and_a_later_grant(self):
        game, source, private = self.draw_fixture(706002)
        replacement = self.add(game.engine, "Chains of Mephistopheles", seat="B")
        foreign = self.add(game.engine, "Island", seat="C", zone="hand")
        self.assert_closed(game, replacement)
        source = self.cast_fixture(game, source)
        trigger = self.attack_for_trigger(game, source)
        self.assertEqual("draw.discard", self.resolve(game).kind)
        face = self.assert_public_roll(game, trigger)
        self.assertEqual(7, effective_maximum_hand_size(game.engine, "A"))
        self.assertIsNone(game.packet("pilot:C", full=True)["decision"])
        self.assertNotIn(private.ref, json.dumps(game.packet("pilot:C", full=True)))
        before = authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:C", {"action_id": "choose", "card": private.ref}).ok)
        self.assertFalse(game.act("pilot:A", {"action_id": "choose", "card": foreign.ref}).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        game = self.replay(game, load=True)
        self.assertEqual(face, self.assert_public_roll(game, trigger))

        for index in range(face):
            decision = game.packet("pilot:A", full=True)["decision"]
            self.assertEqual("draw.discard", decision["kind"])
            card = game.state.cards[game.state.players["A"].zones["hand"][0]]
            capability = game.engine.permissions.capability_for("pilot:A")
            choice = game.act("pilot:A", {"action_id": "choose", "card": card.ref})
            self.assertTrue(choice.ok, choice.summary)
            if index == 0:
                before = authoritative_state_hash(game.state)
                with self.assertRaises(PermissionDenied):
                    game.engine.submit(
                        principal="pilot:A", token=capability.token,
                        action="choose", payload={"card": card.ref},
                    )
                self.assertEqual(before, authoritative_state_hash(game.state))
        self.assertFalse(game.state.stack)
        self.assertEqual(face, self.assert_public_roll(game, trigger))
        self.assertIsNone(effective_maximum_hand_size(game.engine, "A"))
        self.replay(game, load=True)

    def test_unsupported_dice_paragraphs_and_consumers_remain_residual(self):
        record = self.db.lookup(self.source_name, fuzzy=False)
        text = record.oracle_text
        for changed in (
            text.replace("a d20", "two d20s"),
            text.replace("a d20", "a d6"),
            text.replace("the result", "the natural result"),
            text.replace("rest of the game", "turn"),
            text.replace("combat damage to a player", "damage to a player"),
            text.replace("combat damage to a player", "combat damage to a planeswalker"),
            text + " Roll again.",
            "If you would roll one or more dice, instead roll one additional die and ignore the lowest roll.",
            "Whenever you roll one or more dice, draw a card.",
        ):
            with self.subTest(text=changed):
                self.assertNotEqual("exact", compile_oracle_card(
                    replace(record, oracle_text=changed),
                    capability_registry=self.registry, capability_profile=PROFILE,
                ).status)

    def test_runtime_authority_requires_both_ordered_effects_and_the_complete_event(self):
        from quorune.rules.die_roll_draw import CAPABILITY, MECHANIC, ROLL_FIELD
        from quorune.game_hand_size_model import CAPABILITY as HAND_SIZE_CAPABILITY

        game, source, _ = self.draw_fixture(706003)
        program, = (program for program in game.engine.semantics.programs_for_oracle(source.oracle_id)
                    if MECHANIC in program.coverage)
        def bind(candidate):
            return bind_semantic_program_runtime(
                candidate, capability_registry=self.registry, profile=PROFILE,
            )

        self.assertTrue(bind(program)["strict"])
        for capability in (CAPABILITY, HAND_SIZE_CAPABILITY):
            missing = [name for name in program.capability_dependencies if name != capability]
            candidate = replace(
                program, capability_dependencies=missing,
                capability_closure=self.registry.closure(missing, profile=PROFILE).to_dict(),
            )
            self.assertIn(f"capability:undeclared_runtime_dependency:{capability}", bind(candidate)["blockers"])
        invalid = (
            replace(program, event_condition=None),
            replace(program, effects=tuple(reversed(program.effects))),
            replace(program, effects=({**program.effects[0], ROLL_FIELD: True}, program.effects[1])),
            replace(program, effects=({**program.effects[0], "player": "B"}, program.effects[1])),
        )
        for candidate in invalid:
            self.assertFalse(bind(candidate)["strict"])

    def test_roll_amount_and_game_duration_owner_omissions_are_detected(self):
        from quorune.rules import die_roll_draw

        original = die_roll_draw.bind_die_draw

        def wrong_count(host, item, effect, *, instruction_pointer):
            draw = original(host, item, effect, instruction_pointer=instruction_pointer)
            return {**draw, "count": 0}

        with patch.object(die_roll_draw, "bind_die_draw", side_effect=wrong_count):
            with self.assertRaisesRegex(AssertionError, "draw count must equal the public face"):
                self.test_unmodified_roll_draws_for_locked_controller_and_cleanup_survives_departure()

        with patch("quorune.semantic_runtime.game_hand_size.commit_continuous_effect", return_value=None):
            with self.assertRaisesRegex(AssertionError, "is not None"):
                self.test_unmodified_roll_draws_for_locked_controller_and_cleanup_survives_departure()

class ThunderwaveTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        database = Path(cls.temporary.name) / "thunderwave.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures/thunderwave-cards.json"], database)
        cls.db = CardDatabase(database)
        cls.registry = load_default_capability_registry()
        leader = "Isamaru, Hound of Konda"
        cls.deck = DeckDefinition("A closed die table", [
            DeckEntry(leader, 1, "commander"), DeckEntry("Plains", 30),
        ], [leader])

    def scene_for_roll(self, face, *, shield=False):
        from quorune.rules.random_selection import roll_fixed_die

        seed = next(seed for seed in range(706_010, 707_000)
                    if roll_fixed_die(20, seed=seed, instruction_key="S1|0", seat="A") == face)
        game = self.session(seed)
        wave = self.add(game.engine, "Thunderwave", zone="hand")
        own = self.add(game.engine, "Colossal Dreadmaw", ref="DAMAGE-CONTROLLER")
        other_big = self.add(game.engine, "Colossal Dreadmaw", seat="C", ref="DAMAGE-OPPONENT-BIG")
        other_small = self.add(game.engine, "Slippery Bogle", seat="B", ref="DAMAGE-HEXPROOF")
        ring = self.add(game.engine, "Sol Ring", seat="D", ref="NOT-A-CREATURE")
        private = self.add(game.engine, "Grizzly Bears", seat="D", zone="hand", ref="PRIVATE-UNRELATED-HAND")
        self.assert_closed(game, wave)
        if shield:
            wanderer = self.add(game.engine, "The Wanderer", seat="B")
            wanderer.counters["loyalty"] = 5
            self.assert_closed(game, wanderer)
            other_small.counters["shield"] = 1
        game.state.config.semantic_policy = "trusted_only"
        self.prepare(game, mana={"C": 10, "R": 2})
        self.checkpoint(game)
        self.cast(game, "A", wave)
        item, = game.state.stack
        self.assertEqual(("S1", []), (item.ref, item.targets))
        self.assertFalse(self.rolls(game))
        return game, wave, (own, other_big, other_small), ring, private

    def rolls(self, game):
        return [event for event in game.state.events if event.code == "die.roll"]

    def assert_one_roll(self, game, expected):
        roll, = self.rolls(game)
        self.assertEqual(("A", "S1", 20, expected), (
            roll.actor, roll.details["stack"], roll.details["sides"], roll.details["result"],
        ))

    def reject(self, game, seat, command):
        before = authoritative_state_hash(game.state)
        result = game.act("pilot:" + seat, command)
        self.assertFalse(result.ok, result.summary)
        self.assertEqual(before, authoritative_state_hash(game.state))

    def test_actual_thunderwave_roll_ranges_and_untargeted_spare_replay(self):
        from quorune import fixed_damage_set

        for face in (1, 9, 10, 19, 20):
            with self.subTest(face=face):
                game, wave, (own, opposing_big, bogle), ring, private = self.scene_for_roll(face)
                rows = (own, opposing_big, bogle)
                spared = bogle if face == 10 else None
                if 10 <= face <= 19:
                    pending = self.resolve(game)
                    self.assertEqual(("semantic.choice", ["A"]), (pending.kind, pending.actors))
                    self.assert_one_roll(game, face)
                    for viewer in "BCD":
                        self.assertIsNone(game.packet("pilot:" + viewer, full=True)["decision"])
                    legal = pending.payload_by_actor["A"]["legal_actions"][0]["choice_schema"]["legal_refs"]
                    self.assertEqual({card.ref for card in rows}, set(legal))
                    self.reject(game, "A", {"action_id": "choose", "objects": [ring.ref]})
                    self.reject(game, "A", {"action_id": "choose", "objects": [own.ref, opposing_big.ref]})
                    self.reject(game, "B", {"action_id": "choose", "objects": [bogle.ref]})
                    self.reject(game, "A", {"action_id": "pass"})
                    game = self.replay(game, load=True)
                with patch.object(fixed_damage_set, "resolve_damage_batch", wraps=fixed_damage_set.resolve_damage_batch) as batch:
                    if 10 <= face <= 19:
                        choice = game.act("pilot:A", {"action_id": "choose", "objects": [spared.ref] if spared else []})
                        self.assertTrue(choice.ok, choice.summary)
                    self.assertIsNone(self.resolve(game))
                batch.assert_called_once()
                proposals = batch.call_args.args[1]
                excluded = {own.ref} if face == 20 else {spared.ref} if spared is not None else set()
                self.assertEqual({card.ref for card in rows} - excluded,
                                 {proposal.recipient.ref for proposal in proposals})
                self.assertEqual({(wave.object_id, 6 if face == 20 else 3, False)},
                                 {(proposal.source.object_id, proposal.amount, proposal.combat) for proposal in proposals})
                self.assert_one_roll(game, face)
                self.assertEqual("battlefield", game.state.cards[ring.object_id].zone)
                self.assertEqual(0 if face == 20 else 3, game.state.cards[own.object_id].marked_damage)
                self.assertEqual("graveyard" if face == 20 else "battlefield", game.state.cards[opposing_big.object_id].zone)
                self.assertEqual("battlefield" if spared else "graveyard", game.state.cards[bogle.object_id].zone)
                for viewer in "ABC":
                    self.assertNotIn(private.ref, json.dumps(game.packet("pilot:" + viewer, full=True)))
                self.replay(game, load=True)

    def test_actual_thunderwave_damage_replacement_resume_never_rolls_again(self):
        for face in (10, 20):
            with self.subTest(face=face):
                game, _wave, (own, _other, bogle), _ring, _private = self.scene_for_roll(face, shield=True)
                pending = self.resolve(game)
                if face == 10:
                    self.assertEqual(("semantic.choice", ["A"]), (pending.kind, pending.actors))
                    self.assert_one_roll(game, face)
                    game = self.replay(game, load=True)
                    choice = game.act("pilot:A", {"action_id": "choose", "objects": [own.ref]})
                    self.assertTrue(choice.ok, choice.summary)
                    pending = self.resolve(game)
                self.assertEqual(("replacement.order", ["B"]), (pending.kind, pending.actors))
                self.assert_one_roll(game, face)
                game = self.replay(game, load=True)
                options = game.packet("pilot:B", full=True)["decision"]["ctx"]["options"]
                chosen = next(row["id"] for row in options if "shield" in row["id"].lower())
                self.reject(game, "C", {"action_id": "choose", "choices": {"replacement": chosen}})
                acted = game.act("pilot:B", {"action_id": "choose", "choices": {"replacement": chosen}})
                self.assertTrue(acted.ok, acted.summary)
                self.assertIsNone(self.resolve(game))
                self.assertEqual("battlefield", game.state.cards[bogle.object_id].zone)
                self.assertEqual(0, game.state.cards[bogle.object_id].counters.get("shield", 0))
                self.assertEqual(0, game.state.cards[own.object_id].marked_damage)
                self.assert_one_roll(game, face)
                self.replay(game, load=True)

    def test_thunderwave_unmatched_adjusted_values_have_no_damage_row(self):
        from quorune.rules.die_damage_table import (
            OUTCOME_CONTEXT, CreatureDamageTableRoll, bind_die_damage_table, creature_damage_table_effect,
        )

        game, _wave, _creatures, _ring, _private = self.scene_for_roll(1)
        item, = game.state.stack
        item.context["currently_resolving"] = True
        for adjusted in (0, 21):
            with self.subTest(adjusted=adjusted):
                item.context[OUTCOME_CONTEXT] = CreatureDamageTableRoll(item.ref, 0, adjusted).to_dict()
                self.assertIsNone(bind_die_damage_table(
                    game.engine, item, creature_damage_table_effect(), instruction_pointer=0,
                ))
        self.assertFalse(self.rolls(game))

    def test_thunderwave_requires_the_full_source_table_and_runtime_authority(self):
        from quorune.rules.die_damage_table import CAPABILITY, TABLE_FIELD
        from test_controller_creature_keyword_absence import register_card

        card = self.db.lookup("Thunderwave", fuzzy=False)
        ir = compile_oracle_card(card, capability_registry=self.registry, capability_profile=PROFILE)
        self.assertEqual("exact", ir.status, ir.material_residuals)
        node, = ir.faces[0].nodes
        self.assertEqual(card.oracle_text, node.text)
        self.assertEqual(1, node.effects[0][TABLE_FIELD])
        self.assertIsNone(node.target_schema)
        report, registry = register_card(self.db, self.registry, card.name)
        self.assertTrue(report["strict_capability_ready"], report["blockers"])
        program, = registry.programs_for_oracle(card.oracle_id)
        deps = tuple(value for value in program.capability_dependencies if value != CAPABILITY)
        stripped = replace(program, capability_dependencies=deps,
                           capability_closure=self.registry.closure(deps, profile=PROFILE).to_dict())
        result = bind_semantic_program_runtime(stripped, capability_registry=self.registry, profile=PROFILE)
        self.assertFalse(result["strict"])
        self.assertIn("capability:undeclared_runtime_dependency:" + CAPABILITY, result["blockers"])
        cases = (
            card.oracle_text.replace("10—19", "11—19"),
            card.oracle_text.replace("You may choose a creature", "Choose target creature"),
            card.oracle_text.replace("deals 6 damage", "deals 5 damage"),
        )
        for text in cases:
            self.assertNotEqual("exact", compile_oracle_card(
                replace(card, oracle_text=text), capability_registry=self.registry, capability_profile=PROFILE,
            ).status)
        with patch("quorune.compiler.die_damage_table.compile_die_damage_table_face", return_value=None):
            self.assertNotEqual("exact", compile_oracle_card(card, capability_registry=self.registry,
                                                            capability_profile=PROFILE).status)
