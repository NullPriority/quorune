"""Ordinary spell targeting with one live creature-count comparison at resolution."""

from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.live_battlefield_quantity_model import LIVE_BATTLEFIELD_QUANTITY_CAPABILITY
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.stack_counter import CREATURE_COUNT_COUNTER_FIELD
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


ORIGINAL = "Unified Will"


class SpellControllerCreatureCountCounterTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        database = Path(cls.temporary.name) / "spell-controller-creature-count.sqlite3"
        build_fixture_database([
            ROOT / "tests/fixtures/spell-controller-creature-count-counter-cards.json",
            ROOT / "tests/fixtures/transmute-cards.json",
            ROOT / "tests/fixtures/mixed-stack-counter-cards.json",
            ROOT / "tests/fixtures/stack-target-change-payment-cards.json",
        ], database)
        cls.db = CardDatabase(database)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Current creature comparison", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"), DeckEntry("Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def test_complete_original_keeps_ordinary_single_target_and_closed_resolution_condition(self):
        bound, semantic = register_card(self.db, self.registry, ORIGINAL)
        self.assertTrue(bound["strict_capability_ready"], bound["blockers"])
        original = self.db.lookup(ORIGINAL, fuzzy=False)
        program, = semantic.programs_for_oracle(original.oracle_id)
        self.assertEqual(("resolve", "stack"), (program.event, program.active_zone))
        self.assertEqual({"zones": ["stack"], "categories": ["spell"], "source_exclusion": True, "count": 1},
                         program.target_schema)
        effect, = program.effects
        self.assertEqual({"op": "counter_stack_target", "stack": "$target.0", CREATURE_COUNT_COUNTER_FIELD: 1},
                         effect)
        self.assertLessEqual({LIVE_BATTLEFIELD_QUANTITY_CAPABILITY, "stack.counter.effect", "target.revalidate_resolution"},
                             set(program.capability_dependencies))
        for changes in (
            {"effects": [{**effect, CREATURE_COUNT_COUNTER_FIELD: True}]},
            {"effects": [{**effect, CREATURE_COUNT_COUNTER_FIELD: 2}]},
            {"effects": [{**effect, "stack": "$source"}]},
            {"target_schema": {**program.target_schema, "controller_relation": "opponent"}},
            {"event": "permanent.enter.self", "active_zone": "battlefield"},
        ):
            with self.subTest(changes=changes):
                bound = bind_semantic_program_runtime(
                    replace(program, **changes), capability_registry=self.registry, profile="commander_review",
                )
                self.assertFalse(bound["strict"], bound)
        for text in (
            original.oracle_text.replace("more creatures", "at least as many creatures"),
            original.oracle_text.replace("that spell's controller", "its owner"),
            original.oracle_text.replace("target spell", "target activated ability"),
        ):
            ir = compile_oracle_card(replace(original, oracle_text=text), capability_registry=self.registry)
            self.assertNotEqual("exact", ir.status)

    def stack_item(self, game, card):
        item, = (item for item in game.state.stack if item.card_object_id == card.object_id)
        return item

    def scene(self, seed, *, counts, response=None, takeover=False):
        game = self.session(seed)
        counter = self.add(game.engine, ORIGINAL, zone="hand", ref="creature-compare-counter")
        spell = self.add(game.engine, "Sol Ring", seat="B", zone="hand", ref="creature-compare-target")
        for seat, count in counts.items():
            for index in range(count):
                self.add(game.engine, "Runeclaw Bear", seat=seat, ref=f"creature-compare-{seat}-{index}")
        guards = [counter, spell]
        support = None
        if response:
            support = self.add(game.engine, response, zone="hand", ref="creature-compare-response")
            guards.append(support)
        if takeover:
            support = self.add(game.engine, "Aethersnatch", seat="C", zone="hand", ref="creature-compare-transfer")
            guards.append(support)
        for card in guards:
            self.assert_closed(game, card)
        self.prepare(game, active="B", actor="B", mana={"C": 1})
        game.state.players["A"].mana_pool.update({"C": 2 if response else 1, "U": 1, "W": int(bool(response))})
        if takeover:
            game.state.players["C"].mana_pool.update({"C": 4, "U": 2})
        self.checkpoint(game)
        self.cast(game, "B", spell)
        target = self.stack_item(game, spell).ref
        self.priority(game, "A")
        action = self.action(game, "A", counter)
        self.assertIn(target, action["target_schema"]["legal_refs"])
        command = {"action_id": action["id"], "targets": [target], "pay": "auto"}
        before = authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:D", command).ok)
        self.assertFalse(game.act("pilot:A", {**command, "targets": ["C"]}).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        result = game.act("pilot:A", command)
        self.assertTrue(result.ok, result.summary)
        return game, counter, spell, target, support

    def test_actual_counter_can_be_cast_while_behind_and_counts_response_tokens_at_resolution(self):
        game, counter, spell, _, alarm = self.scene(
            60821221, counts={"A": 0, "B": 1, "C": 3}, response="Raise the Alarm",
        )
        self.cast_for(game, "A", alarm, [])
        self.resolve_top(game)
        self.assertEqual(2, sum(card.zone == "battlefield" and card.controller == "A" and card.is_token
                                for card in game.state.cards.values()))
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        self.assertEqual(("graveyard", "graveyard"), (game.state.cards[counter.object_id].zone,
                                                       game.state.cards[spell.object_id].zone))
        self.replay(game, load=True)

    def test_actual_counter_does_nothing_at_equality_even_with_another_seats_creatures(self):
        game, _, spell, target, _ = self.scene(60821222, counts={"A": 1, "B": 1, "C": 3})
        self.resolve_top(game)
        self.assertTrue(any(item.ref == target for item in game.state.stack))
        self.assertIsNone(self.resolve(game))
        self.assertEqual(("battlefield", "B"), (game.state.cards[spell.object_id].zone,
                                                game.state.cards[spell.object_id].controller))
        self.replay(game, load=True)

    def test_actual_counter_compares_the_target_spells_new_controller_after_aethersnatch(self):
        game, _, spell, target, takeover = self.scene(
            60821223, counts={"A": 1, "B": 0, "C": 2}, takeover=True,
        )
        self.cast_for(game, "C", takeover, [target])
        self.resolve_top(game)
        self.assertEqual(("B", "C"), (game.state.cards[spell.object_id].owner, self.stack_item(game, spell).controller))
        game = self.replay(game, load=True)
        self.resolve_top(game)
        self.assertTrue(any(item.ref == target for item in game.state.stack))
        self.assertIsNone(self.resolve(game))
        self.assertEqual(("battlefield", "C"), (game.state.cards[spell.object_id].zone,
                                                game.state.cards[spell.object_id].controller))
        self.replay(game, load=True)

    def _whole_hand_payment_scene(self, seed, *, cards, takeover=False,
                                  spell_name="Sol Ring", spell_mana=None, counter_name="Perplex"):
        game = self.session(seed)
        counter = self.add(game.engine, counter_name, zone="hand", ref="whole-hand-counter")
        spell = self.add(game.engine, spell_name, seat="B", zone="hand", ref="whole-hand-target")
        for object_id in tuple(game.state.players["B"].zones["hand"]):
            if object_id != spell.object_id:
                game.engine.move_card(object_id, "library", log=False)
        hand = tuple(self.add(game.engine, "Runeclaw Bear", seat="B", zone="hand", ref=f"payer-private-{i}")
                     for i in range(cards))
        self.assert_closed(game, counter)
        self.assert_closed(game, spell)
        transfer = self.add(game.engine, "Aethersnatch", seat="C", zone="hand") if takeover else None
        self.prepare(game, active="B", actor="B", mana=spell_mana or {"C": 1})
        game.state.players["A"].mana_pool.update({"C": 1, "U": 1, "B": 1})
        if transfer is not None:
            game.state.players["C"].mana_pool.update({"C": 4, "U": 2})
        self.checkpoint(game)
        self.cast(game, "B", spell)
        targeted = self.stack_item(game, spell).ref
        self.cast_for(game, "A", counter, [targeted])
        if transfer is not None:
            self.cast_for(game, "C", transfer, [targeted])
            self.resolve_top(game)
        self.assertEqual("semantic.choice", self.resolve(game).kind)
        return game, counter, spell, targeted, hand

    def test_perplex_pays_an_entire_or_empty_private_hand_and_replays(self):
        import json
        from quorune.rules.whole_hand_counter_payment import HAND_PAYMENT_SNAPSHOT

        for count, pay in ((2, True), (0, True), (2, False)):
            with self.subTest(count=count, pay=pay):
                game, counter, spell, target, hand = self._whole_hand_payment_scene(60821250 + count + pay, cards=count)
                self.assertEqual(["pilot:B"], game.pending_principals())
                self.assertIn(HAND_PAYMENT_SNAPSHOT, str(game.state.pending_decision.continuation))
                for seat in "ACD":
                    packet = game.packet("pilot:" + seat, full=True)
                    self.assertIsNone(packet["decision"])
                    for card in hand:
                        self.assertNotIn(card.ref, json.dumps(packet))
                before = authoritative_state_hash(game.state)
                for seat, payload in (
                    ("A", {"action_id": "choose", "pay": True}),
                    ("B", {"action_id": "choose", "pay": "yes"}),
                ):
                    denied = game.act("pilot:" + seat, payload)
                    self.assertFalse(denied.ok, denied.summary)
                    self.assertEqual(before, authoritative_state_hash(game.state))
                game = self.replay(game, load=True)
                result = game.act("pilot:B", {"action_id": "choose", "pay": pay})
                self.assertTrue(result.ok, result.summary)
                self.assertEqual("graveyard", game.state.cards[counter.object_id].zone)
                self.assertEqual(pay, any(item.ref == target for item in game.state.stack))
                self.assertEqual("stack" if pay else "graveyard", game.state.cards[spell.object_id].zone)
                for card in hand:
                    self.assertEqual("graveyard" if pay else "hand", game.state.cards[card.object_id].zone)
                self.assertIsNone(self.resolve(game))
                self.assertEqual("battlefield" if pay else "graveyard", game.state.cards[spell.object_id].zone)
                self.replay(game, load=True)

    def test_perplex_gives_the_choice_to_the_targets_current_controller(self):
        game, _, spell, target, hand = self._whole_hand_payment_scene(60821256, cards=2, takeover=True)
        self.assertEqual(("B", "C"), (game.state.cards[spell.object_id].owner, self.stack_item(game, spell).controller))
        self.assertEqual(["pilot:C"], game.pending_principals())
        before = authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:B", {"action_id": "choose", "pay": False}).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        game = self.replay(game, load=True)
        accepted = game.act("pilot:C", {"action_id": "choose", "pay": False})
        self.assertTrue(accepted.ok, accepted.summary)
        self.assertFalse(any(item.ref == target for item in game.state.stack))
        self.assertEqual("graveyard", game.state.cards[spell.object_id].zone)
        self.assertTrue(all(game.state.cards[card.object_id].zone == "hand" for card in hand))
        self.replay(game, load=True)

    def test_perplex_decline_respects_the_canonical_counter_prohibition(self):
        game, _, spell, target, hand = self._whole_hand_payment_scene(
            60821257, cards=1, spell_name="Carnage Tyrant", spell_mana={"C": 4, "G": 2},
        )
        self.assertEqual(["pilot:B"], game.pending_principals())
        accepted = game.act("pilot:B", {"action_id": "choose", "pay": False})
        self.assertTrue(accepted.ok, accepted.summary)
        self.assertTrue(any(item.ref == target for item in game.state.stack))
        self.assertEqual("hand", game.state.cards[hand[0].object_id].zone)
        self.assertIsNone(self.resolve(game))
        self.assertEqual("battlefield", game.state.cards[spell.object_id].zone)
        self.replay(game, load=True)

    def test_mana_leak_native_payment_retains_hand_and_replays(self):
        game, _, spell, target, hand = self._whole_hand_payment_scene(
            60821259, cards=2, spell_mana={"C": 4}, counter_name="Mana Leak",
        )
        self.assertEqual(["pilot:B"], game.pending_principals())
        self.assertEqual(3, game.state.players["B"].mana_pool["C"])
        game = self.replay(game, load=True)
        accepted = game.act("pilot:B", {"action_id": "choose", "pay": True})
        self.assertTrue(accepted.ok, accepted.summary)
        self.assertTrue(any(item.ref == target for item in game.state.stack))
        self.assertEqual(0, game.state.players["B"].mana_pool["C"])
        self.assertTrue(all(game.state.cards[card.object_id].zone == "hand" for card in hand))
        self.assertIsNone(self.resolve(game))
        self.assertEqual("battlefield", game.state.cards[spell.object_id].zone)
        self.replay(game, load=True)

    def test_perplex_requires_the_single_spell_and_its_whole_hand_scope(self):
        from unittest.mock import patch
        from quorune.rules.whole_hand_counter_payment import CAPABILITY, FIELD, whole_hand_counter_effect
        from quorune.transmute import TRANSMUTE_CAPABILITY

        bound, semantic = register_card(self.db, self.registry, "Perplex")
        self.assertTrue(bound["strict_capability_ready"], bound["blockers"])
        original = self.db.lookup("Perplex", fuzzy=False)
        programs = semantic.programs_for_oracle(original.oracle_id)
        payment, = (row for row in programs if CAPABILITY in row.capability_dependencies)
        self.assertEqual((whole_hand_counter_effect(),), tuple(payment.effects))
        self.assertTrue(any(TRANSMUTE_CAPABILITY in row.capability_dependencies for row in programs))
        for changes in (
            {"effects": ({**payment.effects[0], FIELD: True},)},
            {"effects": ({**payment.effects[0], "player": "$controller"},)},
            {"effects": ({"op": "choose_objects", "then": list(payment.effects)},)},
            {"target_schema": {**payment.target_schema, "count": 2}},
            {"active_zone": "battlefield", "event": "permanent.enter.self"},
        ):
            binding = bind_semantic_program_runtime(replace(payment, **changes),
                capability_registry=self.registry, profile="commander_review")
            self.assertFalse(binding["strict"])
        for text in (
            original.oracle_text.replace("their hand", "two cards"),
            original.oracle_text.replace("its controller", "its owner"),
            original.oracle_text.replace("target spell", "target triggered ability"),
        ):
            ir = compile_oracle_card(replace(original, oracle_text=text), capability_registry=self.registry,
                                     capability_profile="commander_review")
            self.assertNotEqual("exact", ir.status)
        with patch("quorune.compiler.effect_template_composition.targeted_whole_hand_counter_payment_template",
                   return_value=None):
            ir = compile_oracle_card(original, capability_registry=self.registry, capability_profile="commander_review")
        self.assertNotEqual("exact", ir.status)
