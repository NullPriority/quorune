"""Native rules and replay for the caused-by-controller Gift occurrence."""

from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture

from quorune.ability_fragments import CURRENT_ABILITY_FRAGMENT_COVERAGE
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.gift_occurrence import GIFT_GIVEN_CAPABILITY, GIFT_GIVEN_EVENT, gift_giver_condition
from scripts.build_test_database import build_fixture_database

CARD = "Jolly Gerbils"
PROFILE = "commander_review"


class GiftOccurrenceTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        database = Path(cls.temporary.name) / "gift-occurrence.sqlite3"
        fixtures = ROOT / "tests/fixtures"
        build_fixture_database([fixtures / name for name in (
            "bound-effect-program-cards.json", "jolly-gerbils-cards.json", "all-player-turn-rule-cards.json",
            "gift-promise-counter-cards.json", "state-qualified-zone-cards.json", "attacking-card-entry-cards.json",
            "draw-rules-cards.json", "ability-source-counter-cards.json", "counted-library-search-cards.json",
        )], database)
        cls.db = CardDatabase(database)
        cls.registry = load_default_capability_registry()
        commander = "Generic Bound Commander"
        cls.deck = DeckDefinition("Gift occurrence", [
            DeckEntry(commander, 1, "commander"), DeckEntry("Generic Bound Plains", 30),
        ], [commander])

    def start(self, game, *cards, active="B"):
        game.state.config.semantic_policy = "trusted_only"
        for card in cards:
            self.assert_closed(game, card)
        self.prepare(game, active=active)
        self.checkpoint(game)

    def cast_gift(self, game, source, recipient=None, targets=()):
        seat = source.owner
        self.priority(game, seat)
        offer = self.action(game, seat, source)
        option = "normal" if recipient is None else f"normal+gift:{recipient}"
        response = {"action_id": offer["id"], "cost_option": option, "pay": "auto"}
        if targets:
            response["targets"] = list(targets)
        result = game.act(f"pilot:{seat}", response)
        self.assertTrue(result.ok, result.summary)
        return game.state.stack[-1]

    @staticmethod
    def draw_count(game, seat):
        return len(game.state.players[seat].draw_history)

    @staticmethod
    def observers(game):
        return [item for item in game.state.stack if item.context.get("event") == GIFT_GIVEN_EVENT]

    def assert_observer(self, game, source, giver, gift):
        item, = self.observers(game)
        self.assertEqual(source.object_id, item.source_object_id)
        self.assertEqual("triggered_ability", item.kind)
        self.assertEqual(giver, item.controller)
        self.assertEqual(gift.ref, item.context["gift_stack_ref"])
        return item

    def test_whole_observer_source_and_missing_authority_fail_closed(self):
        card = self.db.lookup(CARD, fuzzy=False)
        settings = {"capability_registry": self.registry, "capability_profile": PROFILE}
        ir = compile_oracle_card(card, **settings)
        self.assertEqual("exact", ir.status, ir.material_residuals)
        node, = ir.faces[0].nodes
        self.assertEqual(card.oracle_text, card.oracle_text[node.span.start:node.span.end])
        self.assertEqual(GIFT_GIVEN_EVENT, node.event)
        self.assertIn(CURRENT_ABILITY_FRAGMENT_COVERAGE, node.runtime_coverage)
        self.assertEqual(gift_giver_condition(), node.event_condition)
        self.assertEqual(({"op": "draw", "player": "$controller", "count": 1, "private": True},),
                         tuple(node.effects))
        for text in ("Whenever you promise a gift, draw a card.",
                     "Whenever an opponent gives a gift, draw a card.",
                     "Whenever you give a gift, draw two cards."):
            self.assertNotEqual("exact", compile_oracle_card(replace(card, oracle_text=text), **settings).status)
        game = self.session(702174301)
        source = self.add(game.engine, CARD)
        self.assert_closed(game, source)
        program, = game.engine.semantics.programs_for_oracle(card.oracle_id)
        deps = [key for key in program.capability_dependencies if key != GIFT_GIVEN_CAPABILITY]
        omitted = replace(program, capability_dependencies=deps,
                          capability_closure=self.registry.closure(deps, profile=PROFILE).to_dict())
        invalid = bind_semantic_program_runtime(omitted, capability_registry=self.registry, profile=PROFILE)
        self.assertFalse(invalid["strict"])
        self.assertIn("capability:undeclared_runtime_dependency:" + GIFT_GIVEN_CAPABILITY, invalid["blockers"])
        altered = replace(program, effects=[{"op": "draw", "player": "$controller", "count": True, "private": True}])
        self.assertFalse(bind_semantic_program_runtime(
            altered, capability_registry=self.registry, profile=PROFILE,
        )["strict"])
        with patch("quorune.compiler.fixed_public_action_event_bindings._gift_given_draw_spec", return_value=None):
            self.assertNotEqual("exact", compile_oracle_card(card, **settings).status)

    def test_recipient_draw_prevention_still_triggers_only_givers_private_draw(self):
        game = self.session(702174302)
        giver = self.add(game.engine, CARD, ref="giver-observer")
        observer = self.add(game.engine, CARD, seat="D", ref="other-observer")
        gift = self.add(game.engine, "Dawn's Truce", zone="hand")
        limit = self.add(game.engine, "Spirit of the Labyrinth", seat="C")
        cremate = self.add(game.engine, "Cremate", seat="D", zone="hand")
        spare = self.add(game.engine, "Grizzly Bears", seat="C", zone="graveyard")
        game.state.players["A"].mana_pool.update({"C": 1, "W": 1})
        game.state.players["D"].mana_pool["B"] = 1
        self.start(game, giver, observer, gift, limit, cremate)
        before_recipient = self.draw_count(game, "D")
        self.cast_for(game, "D", cremate, [spare.ref])
        self.resolve_top(game)
        recipient_draws = self.draw_count(game, "D")
        self.assertEqual(before_recipient + 1, recipient_draws)
        item = self.cast_gift(game, gift, "D")
        self.assertEqual([], self.observers(game))
        self.resolve_top(game)
        self.assertEqual(recipient_draws, self.draw_count(game, "D"))
        self.assert_observer(game, giver, "A", item)
        game = self.replay(game, load=True)
        hand = set(game.state.players["A"].zones["hand"])
        self.resolve_top(game)
        drawn, = set(game.state.players["A"].zones["hand"]) - hand
        ref = game.state.cards[drawn].ref
        self.assertIn(ref, json.dumps(game.packet("pilot:A", full=True)))
        for seat in "BCD":
            self.assertNotIn(ref, json.dumps(game.packet(f"pilot:{seat}", full=True)))
        self.replay(game)

    def test_declined_countered_and_fizzled_spells_create_no_occurrence(self):
        game = self.session(702174303)
        giver = self.add(game.engine, CARD)
        declined = self.add(game.engine, "Dawn's Truce", zone="hand", ref="declined")
        countered = self.add(game.engine, "Dawn's Truce", zone="hand", ref="countered")
        fizzled = self.add(game.engine, "Parting Gust", zone="hand")
        counter = self.add(game.engine, "Counterspell", seat="B", zone="hand")
        bounce = self.add(game.engine, "Unsummon", seat="B", zone="hand")
        target = self.add(game.engine, "Grizzly Bears", seat="C")
        game.state.players["A"].mana_pool["W"] = 6
        game.state.players["B"].mana_pool["U"] = 3
        self.start(game, giver, declined, countered, fizzled, counter, bounce)
        recipient_draws = self.draw_count(game, "D")
        self.cast_gift(game, declined)
        self.resolve_top(game)
        self.assertEqual([], self.observers(game))
        item = self.cast_gift(game, countered, "D")
        self.cast_for(game, "B", counter, [item.ref])
        self.resolve_top(game)
        self.assertEqual([], self.observers(game))
        self.cast_gift(game, fizzled, "D", (target.ref,))
        self.cast_for(game, "B", bounce, [target.ref])
        self.resolve_top(game)
        self.resolve_top(game)
        self.assertEqual("graveyard", game.state.cards[fizzled.object_id].zone)
        self.assertEqual([], self.observers(game))
        self.assertEqual(recipient_draws, self.draw_count(game, "D"))
        self.assertFalse(any(card.is_token for card in game.state.cards.values()))
        self.replay(game)

    def test_copy_uses_its_controller_and_the_original_can_fizzle(self):
        game = self.session(702174304)
        original_observer = self.add(game.engine, CARD, ref="original-observer")
        copy_observer = self.add(game.engine, CARD, seat="D", ref="copy-observer")
        gift = self.add(game.engine, "Long River's Pull", zone="hand")
        murder = self.add(game.engine, "Murder", seat="B", zone="hand")
        body = self.add(game.engine, "Grizzly Bears", seat="C")
        copier = self.add(game.engine, "Twincast", seat="D", zone="hand")
        game.state.players["A"].mana_pool["U"] = 2
        game.state.players["B"].mana_pool.update({"C": 1, "B": 2})
        game.state.players["D"].mana_pool["U"] = 2
        self.start(game, original_observer, copy_observer, gift, murder, copier)
        self.cast_for(game, "B", murder, [body.ref])
        target = game.state.stack[-1]
        original = self.cast_gift(game, gift, "C", (target.ref,))
        self.cast_for(game, "D", copier, [original.ref])
        pending = self.resolve(game)
        self.assertEqual(("semantic.choice", ["D"]), (pending.kind, pending.actors))
        result = game.act("pilot:D", {"action_id": "choose", "targets": [target.ref]})
        self.assertTrue(result.ok, result.summary)
        copy = game.state.stack[-1]
        self.assertEqual(("spell_copy", "D"), (copy.kind, copy.controller))
        self.assertEqual([], self.observers(game))
        before = {seat: self.draw_count(game, seat) for seat in "ACD"}
        self.resolve_top(game)
        self.assert_observer(game, copy_observer, "D", copy)
        self.assertEqual(before["C"] + 1, self.draw_count(game, "C"))
        self.assertEqual(before["D"], self.draw_count(game, "D"))
        self.resolve_top(game)
        self.assertEqual(before["D"] + 1, self.draw_count(game, "D"))
        self.resolve_top(game)
        self.assertEqual([], self.observers(game))
        self.assertEqual(before["A"], self.draw_count(game, "A"))
        self.replay(game)

    def test_after_gift_exile_keeps_the_already_observed_trigger(self):
        game = self.session(702174305)
        giver = self.add(game.engine, CARD)
        gift = self.add(game.engine, "Parting Gust", zone="hand")
        game.state.players["A"].mana_pool["W"] = 2
        self.start(game, giver, gift)
        item = self.cast_gift(game, gift, "C", (giver.ref,))
        self.resolve_top(game)
        self.assertEqual("exile", game.state.cards[giver.object_id].zone)
        self.assertTrue(any(card.is_token and card.controller == "C" and card.tapped
                            for card in game.state.cards.values()))
        self.assert_observer(game, giver, "A", item)
        before = self.draw_count(game, "A")
        self.resolve_top(game)
        self.assertEqual(before + 1, self.draw_count(game, "A"))
        self.replay(game)
