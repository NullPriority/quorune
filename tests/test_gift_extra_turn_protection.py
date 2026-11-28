"""Native ordering, payment and replay for an extra-turn Gift spell."""

from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
import test_next_personal_turn_protection as retained

from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.gift_card_model import GIFT_CARD_PROMISE_FIELD, GIFT_EXTRA_TURN_COST_FIELD
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.gift_extra_turn_spells import DEPENDENCIES, GIFT_EXTRA_TURN_PRICE, gift_extra_turn_protection_effects
from quorune.semantic_runtime.life_prohibitions import life_payment_prohibited
from scripts.build_test_database import build_fixture_database


CARD = "Perch Protection"
PROTECTION = "protection_from_everything_until_next_turn"


class GiftExtraTurnProtectionTests(InheritedNativeFixture, unittest.TestCase):
    until_following_turn = retained.NextPersonalTurnProtectionTests.until_following_turn

    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        database = Path(cls.temporary.name) / "gift-extra-turn-protection.sqlite3"
        fixtures = ROOT / "tests/fixtures"
        build_fixture_database([fixtures / name for name in (
            "next-personal-turn-protection-cards.json", "gift-extra-turn-protection-cards.json",
            "ability-source-counter-cards.json",
        )], database)
        cls.db = CardDatabase(database)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Extra-turn Gift", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"), DeckEntry("Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def start(self, game, *cards):
        game.state.config.semantic_policy = "trusted_only"
        for card in cards:
            self.assert_closed(game, card)
        self.prepare(game, mana={"C": 4, "W": 2})
        self.checkpoint(game)

    def cast_gift_spell(self, game, source, recipient=None):
        self.priority(game, "A")
        offer = self.action(game, "A", source)
        option = "normal" if recipient is None else f"normal+gift:{recipient}"
        result = game.act("pilot:A", {"action_id": offer["id"], "cost_option": option, "pay": "auto"})
        self.assertTrue(result.ok, result.summary)
        return game.state.stack[-1]

    def birds(self, game, seat):
        birds = [card for card in game.state.cards.values() if card.is_token and card.zone == "battlefield" and card.controller == seat]
        self.assertEqual(4, len(birds))
        for bird in birds:
            data = game.engine._effective_card_data(bird)
            self.assertEqual(({"creature"}, {"bird"}, set()), game.engine._type_parts(data["type_line"]))
            self.assertEqual((["U"], "2", "2", ["Flying"]),
                             (list(data["colors"]), data["power"], data["toughness"], list(data["keywords"])))
        return birds

    def assert_protected(self, game, seat, expected):
        self.assertIs(bool(game.state.players[seat].stats.get(PROTECTION)), expected)
        self.assertIs(life_payment_prohibited(game.engine, seat), expected)

    def test_whole_source_caps_and_nearby_variants_fail_closed(self):
        record = self.db.lookup(CARD, fuzzy=False)
        ir = compile_oracle_card(record, capability_registry=self.registry, capability_profile="commander_review")
        self.assertEqual("exact", ir.status, ir.material_residuals)
        node, = ir.faces[0].nodes
        self.assertEqual(record.oracle_text, record.oracle_text[node.span.start:node.span.end])
        self.assertEqual({GIFT_EXTRA_TURN_COST_FIELD: GIFT_EXTRA_TURN_PRICE}, node.cost)
        self.assertIsNone(node.target_schema)
        self.assertEqual(gift_extra_turn_protection_effects(), tuple(node.effects))
        self.assertTrue(set(DEPENDENCIES) <= set(node.capability_dependencies))
        for old, new in (
            ("Gift an extra turn", "Gift a card"), ("four 2/2", "three 2/2"),
            ("If the gift was promised,", "Then"),
            ("all permanents you control", "all creatures you control"),
            ("until your next turn", "until end of turn"), ("Exile " + CARD, "Exile another spell"),
        ):
            with self.subTest(new=new):
                changed = replace(record, oracle_text=record.oracle_text.replace(old, new))
                self.assertNotEqual("exact", compile_oracle_card(
                    changed, capability_registry=self.registry, capability_profile="commander_review",
                ).status)
        normal = self.session(70217400)
        source = self.add(normal.engine, CARD, zone="hand")
        self.assert_closed(normal, source)
        program, = normal.engine.semantics.programs_for_oracle(record.oracle_id)
        broken = replace(program, cost_schema={GIFT_EXTRA_TURN_COST_FIELD: {"schema_version": True}})
        binding = bind_semantic_program_runtime(broken, capability_registry=self.registry, profile="commander_review")
        self.assertFalse(binding["strict"])
        with patch("quorune.compiler.gift_extra_turn_spells._matches_gift_protection_text", return_value=False):
            self.assertNotEqual("exact", compile_oracle_card(
                record, capability_registry=self.registry, capability_profile="commander_review",
            ).status)

    def test_promised_extra_turn_precedes_the_bird_batch_and_all_controlled_phasing(self):
        game = self.session(70217401)
        source = self.add(game.engine, CARD, zone="hand")
        land = self.add(game.engine, "Plains", ref="phased-land")
        ring = self.add(game.engine, "Sol Ring")
        outsider = self.add(game.engine, "Sol Ring", seat="D", ref="untouched-opponent")
        ring.tapped = True
        self.start(game, source)
        self.priority(game, "A")
        offer = self.action(game, "A", source)
        options = {option["id"]: option for option in offer["cost_options"]}
        self.assertEqual({"normal", "normal+gift:B", "normal+gift:C", "normal+gift:D"}, set(options))
        self.assertIn("promise an extra turn", options["normal+gift:D"]["label"])
        before = authoritative_state_hash(game.state)
        forbidden = game.act("pilot:A", {"action_id": offer["id"], "cost_option": "normal+gift:A", "pay": "auto"})
        self.assertFalse(forbidden.ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        cast = self.cast_gift_spell(game, source, "D")
        self.assertFalse(cast.targets)
        self.assertEqual({"schema_version": 1, "recipient": "D"}, cast.context[GIFT_CARD_PROMISE_FIELD])
        self.assertFalse(game.state.extra_turns)
        game = self.replay(game, load=True)
        self.resolve_top(game)
        self.assertEqual("exile", game.state.cards[source.object_id].zone)
        gifted, = game.state.extra_turns
        self.assertEqual("D", gifted.player)
        birds = self.birds(game, "A")
        phased = {land.ref, ring.ref, *(bird.ref for bird in birds)}
        for card in (land, ring, *birds):
            self.assertTrue(game.state.cards[card.object_id].phased_out)
        self.assertFalse(game.state.cards[outsider.object_id].phased_out)
        self.assert_protected(game, "A", True)
        self.assert_protected(game, "D", False)
        relevant = [event for event in game.state.events if event.code in {
            "turn.extra.scheduled", "token.create", "permanent.phase_out", "player.protection",
        }]
        self.assertEqual(["turn.extra.scheduled", "token.create", "permanent.phase_out", "player.protection"],
                         [event.code for event in relevant])
        self.assertEqual(phased, set(relevant[2].details["out"]))
        original = game.state.turn_sequence
        self.until_following_turn(game, original, "D")
        self.assertEqual(gifted.turn_id, game.state.current_turn.turn_id)
        self.assert_protected(game, "A", True)
        self.assertTrue(game.state.cards[ring.object_id].phased_out)
        self.until_following_turn(game, original, "A")
        self.assert_protected(game, "A", False)
        self.assertFalse(game.state.cards[ring.object_id].tapped)
        for card in (land, ring, *birds):
            live = game.state.cards[card.object_id]
            self.assertFalse(live.phased_out)
            self.assertEqual(card.logical_object_id, live.logical_object_id)
        self.replay(game, load=True)

    def test_declined_gift_creates_visible_birds_without_protection_or_extra_turn(self):
        game = self.session(70217402)
        source = self.add(game.engine, CARD, zone="hand")
        ring = self.add(game.engine, "Sol Ring")
        self.start(game, source)
        cast = self.cast_gift_spell(game, source)
        self.assertEqual({"schema_version": 1, "recipient": None}, cast.context[GIFT_CARD_PROMISE_FIELD])
        self.resolve_top(game)
        self.assertEqual("exile", game.state.cards[source.object_id].zone)
        self.assertFalse(game.state.extra_turns)
        self.assert_protected(game, "A", False)
        self.assertFalse(game.state.cards[ring.object_id].phased_out)
        self.assertTrue(all(not card.phased_out for card in self.birds(game, "A")))
        self.replay(game, load=True)

    def test_copy_gifts_same_player_and_countered_original_gives_nothing(self):
        game = self.session(70217403)
        source = self.add(game.engine, CARD, zone="hand")
        copier = self.add(game.engine, "Twincast", seat="D", zone="hand")
        counter = self.add(game.engine, "Counterspell", seat="B", zone="hand")
        own_ring = self.add(game.engine, "Sol Ring")
        copier_ring = self.add(game.engine, "Sol Ring", seat="D", ref="copy-control-ring")
        game.state.players["B"].mana_pool["U"] = 2
        game.state.players["D"].mana_pool["U"] = 2
        self.start(game, source, copier, counter)
        original = self.cast_gift_spell(game, source, "D")
        self.cast_for(game, "D", copier, [original.ref])
        self.resolve_top(game)
        copy = game.state.stack[-1]
        self.assertEqual(("spell_copy", "D"), (copy.kind, copy.controller))
        self.assertEqual(original.context[GIFT_CARD_PROMISE_FIELD], copy.context[GIFT_CARD_PROMISE_FIELD])
        game = self.replay(game, load=True)
        self.resolve_top(game)
        self.assertEqual(["D"], [turn.player for turn in game.state.extra_turns])
        self.assert_protected(game, "A", False)
        self.assert_protected(game, "D", True)
        self.assertTrue(game.state.cards[copier_ring.object_id].phased_out)
        self.assertTrue(all(card.phased_out for card in self.birds(game, "D")))
        self.cast_for(game, "B", counter, [original.ref])
        self.resolve_top(game)
        self.assertFalse(game.state.stack)
        self.assertEqual("graveyard", game.state.cards[source.object_id].zone)
        self.assertEqual(["D"], [turn.player for turn in game.state.extra_turns])
        self.assert_protected(game, "A", False)
        self.assertFalse(game.state.cards[own_ring.object_id].phased_out)
        self.assertFalse(any(card.is_token and card.controller == "A" for card in game.state.cards.values()))
        self.replay(game, load=True)

    def test_departed_recipient_leaves_promise_and_controller_results_intact(self):
        game = self.session(70217404)
        source = self.add(game.engine, CARD, zone="hand")
        self.start(game, source)
        self.cast_gift_spell(game, source, "D")
        self.priority(game, "D")
        departure = game.act("pilot:D", {"action_id": "concede", "choices": {"confirm_concede": True}})
        self.assertTrue(departure.ok, departure.summary)
        self.assertFalse(game.state.players["D"].in_game)
        game = self.replay(game, load=True)
        self.resolve_top(game)
        self.assertFalse(game.state.extra_turns)
        self.assertEqual("exile", game.state.cards[source.object_id].zone)
        self.assert_protected(game, "A", True)
        self.assertTrue(all(card.phased_out for card in self.birds(game, "A")))
        self.replay(game, load=True)
