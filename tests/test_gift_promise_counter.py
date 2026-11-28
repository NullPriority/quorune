"""One declaration-bound Gift recipient, spell target branch, and ordered counter."""

from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from test_controller_creature_keyword_absence import register_card
from test_qualified_zone_event_queries import query_record
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.gift_card_model import GIFT_CARD_PROMISE_FIELD
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.gift_counter_spells import (
    GIFT_COUNTER_CAPABILITY, GIFT_COUNTER_DEPENDENCIES, gift_counter_cost_schema,
    gift_counter_effect, gift_counter_target_schema,
)
from scripts.build_test_database import build_fixture_database

CARD = "Long River's Pull"
PROFILE = "commander_review"
ORACLE = "Gift a card\nCounter target creature spell. If the gift was promised, instead counter target spell."


class GiftPromiseCounterTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        database = Path(cls.temporary.name) / "gift-promise-counter.sqlite3"
        fixtures = ROOT / "tests/fixtures"
        build_fixture_database([fixtures / name for name in (
            "bound-effect-program-cards.json", "gift-promise-counter-cards.json",
            "all-player-turn-rule-cards.json", "state-qualified-zone-cards.json",
            "attacking-card-entry-cards.json", "draw-rules-cards.json", "ability-source-counter-cards.json",
        )], database)
        cls.db = CardDatabase(database)
        cls.registry = load_default_capability_registry()
        commander = "Generic Bound Commander"
        cls.deck = DeckDefinition("Gift counter target branches", [
            DeckEntry(commander, 1, "commander"), DeckEntry("Generic Bound Plains", 30),
        ], [commander])

    def test_compiler_binds_both_target_branches_and_rejects_an_untrusted_gift_shape(self):
        record = replace(query_record(ORACLE), type_line="Instant", mana_cost="{U}{U}")
        ir = compile_oracle_card(record, capability_registry=self.registry, capability_profile=PROFILE)
        self.assertEqual("exact", ir.status, ir.material_residuals)
        node, = ir.faces[0].nodes
        self.assertEqual(ORACLE, ORACLE[node.span.start:node.span.end])
        self.assertEqual(gift_counter_target_schema(False), node.target_schema)
        self.assertEqual(gift_counter_cost_schema(), node.cost)
        self.assertEqual((gift_counter_effect(),), tuple(node.effects))
        self.assertTrue(set(GIFT_COUNTER_DEPENDENCIES).issubset(node.capability_dependencies))
        for old, new in (("Gift a card", "Gift a tapped Fish"), ("creature spell", "artifact spell"),
                         ("instead counter target spell", "instead counter target triggered ability")):
            with self.subTest(new=new):
                changed = replace(record, oracle_text=ORACLE.replace(old, new))
                self.assertNotEqual("exact", compile_oracle_card(
                    changed, capability_registry=self.registry, capability_profile=PROFILE).status)
        admitted, semantics = register_card(self.db, self.registry, CARD)
        self.assertTrue(admitted["strict_capability_ready"], admitted["blockers"])
        program, = semantics.programs_for_oracle(self.db.lookup(CARD, fuzzy=False).oracle_id)
        deps = [key for key in program.capability_dependencies if key != GIFT_COUNTER_CAPABILITY]
        unsupported = replace(program, capability_dependencies=deps,
                              capability_closure=self.registry.closure(deps, profile=PROFILE).to_dict())
        self.assertFalse(bind_semantic_program_runtime(unsupported, capability_registry=self.registry,
                                                       profile=PROFILE)["strict"])

    def start(self, game, *cards, river_mana=2):
        for card in cards:
            self.assert_closed(game, card)
        game.state.players["A"].mana_pool.update({"U": river_mana})
        self.prepare(game, active="B")
        self.checkpoint(game)

    def cast_river(self, game, spell, target, recipient=None):
        self.priority(game, "A")
        offer = self.action(game, "A", spell)
        option = "normal" if recipient is None else f"normal+gift:{recipient}"
        result = game.act("pilot:A", {"action_id": offer["id"], "cost_option": option,
                                     "targets": [target.ref], "pay": "auto"})
        self.assertTrue(result.ok, result.summary)
        return game.state.stack[-1]

    @staticmethod
    def draws(game, seat):
        return len(game.state.players[seat].draw_history)

    @staticmethod
    def gone(game, spell):
        return game.state.cards[spell.object_id].zone == "graveyard"

    def test_ordinary_targets_only_creatures_and_promised_targets_any_spell_with_draw_first(self):
        game = self.session(70217491)
        first = self.add(game.engine, CARD, zone="hand", ref="unpromised-river")
        second = self.add(game.engine, CARD, zone="hand", ref="promised-river")
        bear = self.add(game.engine, "Grizzly Bears", seat="B", zone="hand", ref="bear-spell")
        instant = self.add(game.engine, "Murder", seat="C", zone="hand")
        body = self.add(game.engine, "Grizzly Bears", ref="living-target")
        game.state.players["B"].mana_pool.update({"C": 1, "G": 1})
        game.state.players["C"].mana_pool.update({"C": 1, "B": 2})
        self.start(game, first, second, bear, instant, river_mana=4)
        self.cast_for(game, "B", bear)
        creature_target = game.state.stack[-1]
        self.cast_for(game, "C", instant, [body.ref])
        any_spell_target = game.state.stack[-1]
        self.priority(game, "A")

        offer = self.action(game, "A", first)
        options = {row["id"]: row for row in offer["cost_options"]}
        self.assertEqual({creature_target.ref}, set(options["normal"]["target_schema"]["legal_refs"]))
        self.assertEqual({creature_target.ref, any_spell_target.ref},
                         set(options["normal+gift:D"]["target_schema"]["legal_refs"]))
        before = authoritative_state_hash(game.state)
        for option, target in (("normal", any_spell_target), ("normal+gift:A", any_spell_target)):
            with self.subTest(option=option):
                result = game.act("pilot:A", {"action_id": offer["id"], "cost_option": option,
                                             "targets": [target.ref], "pay": "auto"})
                self.assertFalse(result.ok)
                self.assertEqual(before, authoritative_state_hash(game.state))
        start_draws = self.draws(game, "D")
        self.cast_river(game, first, creature_target)
        self.resolve_top(game)
        self.assertTrue(self.gone(game, bear))
        self.assertFalse(self.gone(game, instant))
        self.assertEqual(start_draws, self.draws(game, "D"))

        self.cast_river(game, second, any_spell_target, "D")
        game = self.replay(game, load=True)
        start_event = game.state.event_sequence
        self.assertIsNone(self.resolve(game))
        self.assertTrue(self.gone(game, instant))
        self.assertEqual(start_draws + 1, self.draws(game, "D"))
        events = [event for event in game.state.events if event.event_id > start_event]
        draw, = [event.event_id for event in events if event.code == "card.draw" and event.actor == "D"]
        counter, = [event.event_id for event in events if event.code == "stack.counter"]
        self.assertLess(draw, counter)
        self.replay(game, load=True)

    def test_copy_keeps_its_original_recipient_then_original_illegal_target_gives_no_gift(self):
        game = self.session(707101491)
        spell = self.add(game.engine, CARD, zone="hand")
        target = self.add(game.engine, "Murder", seat="B", zone="hand")
        body = self.add(game.engine, "Grizzly Bears")
        copier = self.add(game.engine, "Twincast", seat="D", zone="hand")
        game.state.players["B"].mana_pool.update({"C": 1, "B": 2})
        game.state.players["D"].mana_pool.update({"U": 2})
        self.start(game, spell, target, copier)
        self.cast_for(game, "B", target, [body.ref])
        intended = game.state.stack[-1]
        original = self.cast_river(game, spell, intended, "D")
        self.cast_for(game, "D", copier, [original.ref])
        pending = self.resolve(game)
        self.assertEqual(("semantic.choice", ["D"]), (pending.kind, pending.actors))
        game = self.replay(game, load=True)
        result = game.act("pilot:D", {"action_id": "choose", "targets": [intended.ref]})
        self.assertTrue(result.ok, result.summary)
        copy = game.state.stack[-1]
        self.assertEqual(("spell_copy", "D"), (copy.kind, copy.controller))
        self.assertEqual(original.context[GIFT_CARD_PROMISE_FIELD], copy.context[GIFT_CARD_PROMISE_FIELD])
        self.assertEqual(gift_counter_target_schema(True), copy.context["target_schema_override"])
        before = self.draws(game, "D")
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        self.assertTrue(self.gone(game, target))
        self.assertTrue(self.gone(game, spell))
        self.assertEqual(before + 1, self.draws(game, "D"))
        self.replay(game, load=True)

    def test_ordinary_draw_prevention_does_not_stop_the_independent_counter(self):
        game = self.session(609301491)
        spell = self.add(game.engine, CARD, zone="hand")
        target = self.add(game.engine, "Murder", seat="B", zone="hand")
        body = self.add(game.engine, "Grizzly Bears")
        limit = self.add(game.engine, "Spirit of the Labyrinth", seat="C")
        prior = self.add(game.engine, "Cremate", seat="D", zone="hand")
        spare = self.add(game.engine, "Grizzly Bears", seat="B", zone="graveyard", ref="cremate-card")
        game.state.players["B"].mana_pool.update({"C": 1, "B": 2})
        game.state.players["D"].mana_pool.update({"B": 1})
        self.start(game, spell, target, limit, prior)
        before = self.draws(game, "D")
        self.cast_for(game, "D", prior, [spare.ref])
        self.assertIsNone(self.resolve(game))
        self.assertEqual(before + 1, self.draws(game, "D"))
        self.cast_for(game, "B", target, [body.ref])
        self.cast_river(game, spell, game.state.stack[-1], "D")
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        self.assertTrue(self.gone(game, target))
        self.assertEqual(before + 1, self.draws(game, "D"))
        self.assertEqual("battlefield", game.state.cards[body.object_id].zone)
        self.replay(game, load=True)
