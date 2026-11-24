"""Native evidence for one graveyard-transition subscription and its damage result."""

from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.ability_fragments import CURRENT_ABILITY_FRAGMENT_COVERAGE
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


ORIGINAL = "Syr Konrad, the Grim"
LOOKBACK = "card.leave_graveyard.lookback.v1"


class GraveyardCreatureTransitionOpponentDamageTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        database = Path(cls.temporary.name) / "graveyard-creature-transition-opponent-damage.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures/graveyard-creature-transition-opponent-damage-cards.json"], database)
        cls.db = CardDatabase(database)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Graveyard transition damage", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"), DeckEntry("Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def program(self):
        original = self.db.lookup(ORIGINAL, fuzzy=False)
        binding, semantics = register_card(self.db, self.registry, ORIGINAL)
        self.assertTrue(binding["strict_capability_ready"], binding["blockers"])
        programs = semantics.programs_for_oracle(original.oracle_id)
        trigger, = [program for program in programs if program.event == LOOKBACK]
        self.assertTrue(any(program.event == "activate" for program in programs))
        return original, trigger

    def test_complete_original_has_three_exact_subscriptions_and_retains_its_mill_ability(self):
        original, program = self.program()
        self.assertIn(CURRENT_ABILITY_FRAGMENT_COVERAGE, program.coverage)
        self.assertIsNone(program.target_schema)
        events = program.event_condition["event_subscriptions"]
        self.assertEqual({LOOKBACK, "creature.dies", "card.enter_graveyard"}, {row["event"] for row in events})
        self.assertEqual(3, len(events))
        for text in (
            original.oracle_text.replace("another creature dies", "a creature dies"),
            original.oracle_text.replace("anywhere other than the battlefield", "anywhere"),
            original.oracle_text.replace("leaves your graveyard", "leaves an opponent's graveyard"),
            original.oracle_text.replace("each opponent.", "each opponent. Rewrite history."),
        ):
            ir = compile_oracle_card(replace(original, oracle_text=text), capability_registry=self.registry)
            self.assertNotEqual("exact", ir.status, text)

    def test_raw_subscription_cannot_drop_a_branch_or_the_current_source_authority(self):
        _, program = self.program()
        events = program.event_condition["event_subscriptions"]
        for changed in (
            replace(program, event_condition={"event_subscriptions": events[:-1]}),
            replace(program, coverage=tuple(tag for tag in program.coverage if tag != CURRENT_ABILITY_FRAGMENT_COVERAGE)),
            replace(program, effects=({**program.effects[0], "amount": 2},)),
        ):
            binding = bind_semantic_program_runtime(changed, capability_registry=self.registry, profile="commander_review")
            self.assertFalse(binding["strict"], binding)

    def test_zone_owner_uses_the_pre_move_source_for_a_graveyard_departure_batch(self):
        for source_leaves in (False, True):
            with self.subTest(source_leaves=source_leaves):
                game = self.session(60361021 + source_leaves)
                source = self.add(game.engine, ORIGINAL, zone="battlefield" if source_leaves else "graveyard")
                creature = self.add(game.engine, "Grizzly Bears", zone="graveyard")
                self.assert_closed(game, source)
                self.prepare(game)
                original_incarnation = source.logical_object_id
                # Exercise the normalized batch contract directly. The native
                # Crypt witness below covers the offered game action and replay.
                game.engine._move_cards_simultaneously(
                    [(source.object_id, "exile" if source_leaves else "battlefield"),
                     (creature.object_id, "exile")],
                    reason="Verify the graveyard departure source snapshot", log=False,
                )
                triggers = [item for batch in game.state.pending_trigger_batches for item in batch.items
                            if item.source_object_id == source.object_id]
                expected = [(LOOKBACK, "battlefield", original_incarnation)] if source_leaves else []
                self.assertEqual(expected, [(item.normalized_event_id, item.source_zone, item.source_logical_object_id)
                                            for item in triggers])

    def activate(self, game, seat, source, targets=()):
        self.priority(game, seat)
        rows = game.packet("pilot:" + seat, full=True)["decision"]["ctx"]["legal"]["actions"]
        offered, = [row for row in rows if row["id"].startswith(f"activate:{source.ref}:")]
        result = game.act("pilot:" + seat, {
            "action_id": offered["id"], "targets": list(targets), "pay": "auto",
        })
        self.assertTrue(result.ok, result.summary)

    def order(self, game, seat, count):
        decision = game.state.pending_decision
        self.assertEqual(("trigger.order", [seat]), (decision.kind, decision.actors))
        options = game.packet("pilot:" + seat, full=True)["decision"]["ctx"]["triggers"]
        self.assertEqual(count, len(options))
        order = game.act("pilot:" + seat, {"action_id": "order", "triggers": [row["id"] for row in options]})
        self.assertTrue(order.ok, order.summary)

    def assert_result(self, game, source, *, controller, count):
        stack = [item for item in game.state.stack if item.source_object_id == source.object_id]
        self.assertEqual(count, len(stack))
        self.assertTrue(all(item.controller == controller and item.kind == "triggered_ability" and not item.targets
                            for item in stack), stack)
        life = {seat: game.state.players[seat].life for seat in "ABCD"}
        for _ in range(count):
            self.resolve_top(game)
        self.assertEqual({seat: total - (count if seat != controller else 0) for seat, total in life.items()},
                         {seat: game.state.players[seat].life for seat in "ABCD"})

    def test_actual_original_mill_counts_arriving_creature_cards_separately_and_no_others(self):
        game = self.session(60361031)
        source = self.add(game.engine, ORIGINAL)
        first = self.add(game.engine, "Grizzly Bears", seat="A", zone="library", ref="konrad-hidden-bear")
        other = self.add(game.engine, "Giant Spider", seat="C", zone="library", ref="konrad-hidden-spider")
        artifacts = self.add(game.engine, "Sol Ring", seat="B", zone="library", ref="konrad-hidden-artifact")
        land = self.add(game.engine, "Forest", seat="D", zone="library", ref="konrad-hidden-forest")
        for card in (source, first, other, artifacts, land):
            self.assert_closed(game, card)
        self.prepare(game, mana={"C": 1, "B": 1})
        self.checkpoint(game)
        foreign_view = json.dumps(game.packet("pilot:B", full=True))
        self.assertNotIn(first.ref, foreign_view)
        self.assertNotIn(other.ref, foreign_view)
        self.activate(game, "A", source)
        self.assertEqual("trigger.order", self.resolve(game).kind)
        game = self.replay(game, load=True)
        for card in (first, other, artifacts, land):
            self.assertEqual("graveyard", game.state.cards[card.object_id].zone)
        self.order(game, "A", 2)
        self.assert_result(game, source, controller="A", count=2)
        self.replay(game, load=True)

    def test_actual_simultaneous_deaths_include_each_other_creature_and_exclude_the_source(self):
        game = self.session(60361041)
        source = self.add(game.engine, ORIGINAL)
        creature = self.add(game.engine, "Giant Spider", seat="A")
        second = self.add(game.engine, "Grizzly Bears", seat="C")
        wipe = self.add(game.engine, "Damnation", seat="B", zone="hand")
        for card in (source, creature, second, wipe):
            self.assert_closed(game, card)
        self.prepare(game, actor="B", active="B", mana={"C": 2, "B": 2})
        self.checkpoint(game)
        self.cast_for(game, "B", wipe)
        self.assertEqual("trigger.order", self.resolve(game).kind)
        for card in (source, creature, second):
            self.assertEqual("graveyard", game.state.cards[card.object_id].zone)
        game = self.replay(game, load=True)
        self.order(game, "A", 2)
        self.assert_result(game, source, controller="A", count=2)
        self.replay(game, load=True)

    def test_actual_graveyard_departure_tracks_the_borrowed_sources_controller_and_creature_type(self):
        game = self.session(60361051)
        source = self.add(game.engine, ORIGINAL)
        game.engine.change_control(source.object_id, "B", reason="Prepare borrowed graveyard observer")
        owners_creature = self.add(game.engine, "Grizzly Bears", seat="A", zone="graveyard")
        controllers_creature = self.add(game.engine, "Giant Spider", seat="B", zone="graveyard")
        controllers_spell = self.add(game.engine, "Shock", seat="B", zone="graveyard")
        first = self.add(game.engine, "Tormod's Crypt", seat="C", ref="konrad-first-crypt")
        second = self.add(game.engine, "Tormod's Crypt", seat="C", ref="konrad-second-crypt")
        for card in (source, owners_creature, controllers_creature, controllers_spell, first, second):
            self.assert_closed(game, card)
        self.prepare(game, actor="C", active="C")
        self.checkpoint(game)
        self.activate(game, "C", first, ["A"])
        self.resolve_top(game)
        self.assertEqual("exile", game.state.cards[owners_creature.object_id].zone)
        self.assert_result(game, source, controller="B", count=0)
        self.activate(game, "C", second, ["B"])
        self.resolve_top(game)
        for card in (controllers_spell, controllers_creature):
            self.assertEqual("exile", game.state.cards[card.object_id].zone)
        trigger, = game.state.stack
        self.assertEqual(LOOKBACK, trigger.context["event"])
        game = self.replay(game, load=True)
        self.assert_result(game, source, controller="B", count=1)
        self.replay(game, load=True)
