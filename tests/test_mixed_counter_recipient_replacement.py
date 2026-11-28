"""Native witnesses for Lae'zel's mixed counter-recipient replacement."""

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
from quorune.replacement.applicability import condition_matches
from quorune.rules.capabilities import load_default_capability_registry
from quorune.semantic_runtime.counter_replacements import (
    MIXED_CONTROLLED_COUNTER_RECIPIENT_CAPABILITY, CounterPlacementEventSpec,
    CounterReplacementSourceContext, default_counter_placement_replacement_registry,
)
from scripts.build_test_database import build_fixture_database

PROFILE = "commander_review"


class MixedCounterRecipientReplacementTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "mixed-counter-recipient-replacement.sqlite3"
        fixture = ROOT / "tests/fixtures/mixed-counter-recipient-replacement-cards.json"
        build_fixture_database([fixture], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        commander = "Isamaru, Hound of Konda"
        cls.deck = DeckDefinition("Mixed counter recipients", [
            DeckEntry(commander, 1, "commander"),
            DeckEntry("Forest", 30),
        ], [commander])

    def begin(self, game, *cards, mana):
        for card in cards:
            self.assert_closed(game, card)
        game.state.config.semantic_policy = "trusted_only"
        for seat, pool in mana.items():
            game.state.players[seat].mana_pool.update(pool)
        self.prepare(game)
        self.checkpoint(game)

    def act(self, game, seat, command):
        result = game.act("pilot:" + seat, command)
        self.assertTrue(result.ok, result.summary)

    def reject(self, game, seat, command):
        before = authoritative_state_hash(game.state)
        result = game.act("pilot:" + seat, command)
        self.assertFalse(result.ok, result.summary)
        self.assertEqual(before, authoritative_state_hash(game.state))

    def counters(self, game, card):
        return game.state.cards[card.object_id].counters

    def test_actual_creature_entry_each_named_kind_and_player_energy_gain(self):
        game = self.session(12206001)
        laezel = self.add(game.engine, "Lae'zel, Vlaakith's Champion")
        arwen = self.add(game.engine, "Arwen, Mortal Queen", zone="hand")
        bear = self.add(game.engine, "Grizzly Bears")
        hydra = self.add(game.engine, "Bristling Hydra", zone="hand")
        self.begin(game, laezel, arwen, bear, hydra, mana={"A": {"C": 8, "G": 6, "W": 2}})

        self.cast(game, "A", arwen)
        self.resolve_top(game)
        self.assertEqual("battlefield", game.state.cards[arwen.object_id].zone)
        self.assertEqual({"indestructible": 2}, self.counters(game, arwen))

        self.priority(game, "A")
        actions = game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        current = game.state.cards[arwen.object_id]
        offer, = (row for row in actions if row["id"].startswith(f"activate:{current.ref}:"))
        command = {"action_id": offer["id"], "targets": [bear.ref], "pay": "auto"}
        self.act(game, "A", command)
        self.assertEqual({"indestructible": 1}, self.counters(game, arwen))
        self.assertEqual({}, self.counters(game, bear))
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        self.assertEqual({"+1/+1": 2, "lifelink": 2}, self.counters(game, bear))
        self.assertEqual({"indestructible": 1, "+1/+1": 2, "lifelink": 2},
                         self.counters(game, arwen))

        self.assertEqual(0, game.state.players["A"].energy)
        self.cast(game, "A", hydra)
        self.assertIsNone(self.resolve(game))
        self.assertEqual("battlefield", game.state.cards[hydra.object_id].zone)
        self.assertEqual([4, 0, 0, 0], [game.state.players[seat].energy for seat in "ABCD"])
        self.replay(game, load=True)

    def test_actual_foreign_placer_or_recipient_gets_only_the_original_counters(self):
        game = self.session(12206002)
        laezel = self.add(game.engine, "Lae'zel, Vlaakith's Champion")
        a_bear = self.add(game.engine, "Grizzly Bears", ref="recipient-a-bear")
        b_bear = self.add(game.engine, "Grizzly Bears", seat="B", ref="recipient-b-bear")
        a_gift = self.add(game.engine, "Lifecrafter's Gift", zone="hand", ref="recipient-a-gift")
        b_gift = self.add(game.engine, "Lifecrafter's Gift", seat="B", zone="hand", ref="recipient-b-gift")
        self.begin(game, laezel, a_bear, b_bear, a_gift, b_gift,
                   mana={"A": {"C": 3, "G": 1}, "B": {"C": 3, "G": 1}})

        self.cast(game, "B", b_gift, [a_bear.ref])
        self.assertIsNone(self.resolve(game))
        self.assertEqual({"+1/+1": 1}, self.counters(game, a_bear))
        self.assertEqual({}, self.counters(game, b_bear))
        game = self.replay(game, load=True)

        self.cast(game, "A", a_gift, [b_bear.ref])
        self.assertIsNone(self.resolve(game))
        self.assertEqual({"+1/+1": 1}, self.counters(game, b_bear))
        self.assertEqual({"+1/+1": 3}, self.counters(game, a_bear))
        self.assertEqual({}, self.counters(game, laezel))
        self.replay(game, load=True)

    def test_actual_prospective_walker_loyalty_obeys_both_replacement_orders(self):
        for add_first in (True, False):
            with self.subTest(add_first=add_first):
                game = self.session(12206003 + int(add_first))
                laezel = self.add(game.engine, "Lae'zel, Vlaakith's Champion")
                season = self.add(game.engine, "Doubling Season")
                walker = self.add(game.engine, "Jace Beleren", zone="hand")
                self.begin(game, laezel, season, walker, mana={"A": {"C": 1, "U": 2}})
                self.cast(game, "A", walker)
                decision = self.resolve(game)
                self.assertIsNotNone(decision)
                self.assertEqual(("replacement.order", ["A"]), (decision.kind, decision.actors))
                self.assertEqual("stack", game.state.cards[walker.object_id].zone)
                self.assertEqual({}, self.counters(game, walker))
                options = game.packet("pilot:A", full=True)["decision"]["ctx"]["options"]
                self.assertLessEqual({laezel.ref, season.ref}, {row["source"] for row in options})
                first = laezel if add_first else season
                option, = (row for row in options if row["source"] == first.ref)
                command = {"action_id": "choose", "replacement": option["id"]}
                self.reject(game, "B", command)
                game = self.replay(game, load=True)
                self.act(game, "A", command)
                self.assertIsNone(self.resolve(game))
                self.assertEqual("battlefield", game.state.cards[walker.object_id].zone)
                self.assertEqual({"loyalty": 8 if add_first else 7}, self.counters(game, walker))
                self.replay(game, load=True)

    def test_complete_original_requires_one_mixed_recipient_replacement_identity(self):
        game = self.session(12206005)
        source = self.add(game.engine, "Lae'zel, Vlaakith's Champion")
        self.assert_closed(game, source)
        cap = MIXED_CONTROLLED_COUNTER_RECIPIENT_CAPABILITY
        program, = (program for program in game.engine.semantics.programs_for_oracle(source.oracle_id)
                    if cap in program.capability_dependencies)
        self.assertEqual("counter.place", program.event)
        descriptor, = program.handlers
        effect = default_counter_placement_replacement_registry().replacement_effect(
            descriptor, CounterReplacementSourceContext(source.ref, "A", "replacement-unit"),
        )
        operation, = effect.operations
        self.assertEqual({"op": "add", "field": "amount", "amount": 1}, operation.to_dict())
        dual = CounterPlacementEventSpec(
            event_id="recipient-shape", subject_kind="permanent", subject_id="dual-type", placing_player="A",
            counter_name="+1/+1", amount=1, source_ref=None, effect_generated=True,
            owner="A", controller="A", target_zone="battlefield", target_types=("creature", "planeswalker"),
        )
        self.assertTrue(condition_matches(effect.conditions, dual.event()))
        self.assertFalse(condition_matches(effect.conditions, replace(dual, target_types=("artifact",)).event()))
        self.assertFalse(condition_matches(effect.conditions, replace(dual, target_zone="graveyard").event()))
        remaining = tuple(key for key in program.capability_dependencies if key != cap)
        altered = replace(program, capability_dependencies=remaining,
                          capability_closure=self.registry.closure(remaining, profile=PROFILE).to_dict()
                          if remaining else None)
        missing = bind_semantic_program_runtime(altered, capability_registry=self.registry, profile=PROFILE)
        self.assertFalse(missing["strict"])
        self.assertIn(f"capability:undeclared_runtime_dependency:{cap}", missing["blockers"])
        card = self.db.lookup("Lae'zel, Vlaakith's Champion", fuzzy=False)
        for needle, substitute in (
            ("would put one or more counters", "would put two or more counters"),
            ("or on yourself", "or on any player"),
            ("that many plus one", "twice that many"),
        ):
            with self.subTest(change=substitute):
                changed = replace(card, oracle_text=card.oracle_text.replace(needle, substitute))
                result = compile_oracle_card(changed, capability_registry=self.registry, capability_profile=PROFILE)
                self.assertNotEqual("exact", result.status)
