"""Original nontoken entry counters and resolution-time repeated Proliferate."""

from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
import test_fixed_one_counter_movement as movement_fixture
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.external_entry_counter_model import EXTERNAL_NONTOKEN_ENTRY_COUNTER_CAPABILITY
from quorune.oracle_ir import compile_oracle_card
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.controlled_arrival_proliferate import CONTROLLED_ARRIVAL_PROLIFERATE_CAPABILITY
from quorune.semantic_runtime.activated_abilities import activated_abilities_from_descriptors
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


PROFILE = "commander_review"
NAME = "Tromell, Seymour's Butler"


class TromellEntryCounterProliferateTests(InheritedNativeFixture, unittest.TestCase):
    reject = movement_fixture.FixedOneCounterMovementTests.reject

    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        fixture = Path(cls.temporary.name) / "tromell.sqlite3"
        build_fixture_database([
            ROOT / "tests/fixtures/tromell-entry-counter-proliferate-cards.json",
            ROOT / "tests/fixtures/fixed-one-counter-movement-cards.json",
            ROOT / "tests/fixtures/fixed-direct-phasing-cards.json",
            ROOT / "tests/fixtures/activation-public-state-cards.json",
        ], fixture)
        cls.db = CardDatabase(fixture)
        cls.registry = load_default_capability_registry()
        commander = "Isamaru, Hound of Konda"
        cls.deck = DeckDefinition("Tromell", [DeckEntry(commander, 1, "commander"),
            DeckEntry("Plains", 30)], [commander])

    def start(self, game, *cards):
        for card in cards:
            self.assert_closed(game, card)
        game.state.config.semantic_policy = "trusted_only"
        self.prepare(game, actor="A", active="A")
        self.checkpoint(game)

    def live(self, game, card):
        return game.state.cards[card.object_id]

    def activate(self, game, source):
        action = f"activate:{source.ref}:ab2"
        self.priority(game, "A")
        accepted = game.act("pilot:A", {"action_id": action, "pay": "auto"})
        self.assertTrue(accepted.ok, accepted.summary)
        self.assertTrue(self.live(game, source).tapped)

    def lands(self, game, color, amount, *, seat="A"):
        for index in range(amount):
            self.add(game.engine, color, seat=seat, ref=f"tromell-mana-{seat}-{color.casefold()}-{index}")

    def cast_and_resolve(self, game, seat, spell, targets=()):
        self.cast(game, seat, spell, targets)
        self.assertIsNone(self.resolve(game))

    def test_tromell_printed_entry_and_activation_close_exact_source_and_cost(self):
        card = self.db.lookup(NAME, fuzzy=False)
        report, semantics = register_card(self.db, self.registry, NAME)
        self.assertTrue(report["strict_capability_ready"], report["blockers"])
        programs = semantics.programs_for_oracle(card.oracle_id)
        static, = (p for p in programs if EXTERNAL_NONTOKEN_ENTRY_COUNTER_CAPABILITY in p.capability_dependencies)
        activated, = (p for p in programs if CONTROLLED_ARRIVAL_PROLIFERATE_CAPABILITY in p.capability_dependencies)
        ability, = activated_abilities_from_descriptors(activated.handlers)
        self.assertTrue(ability.tap_source)
        self.assertFalse(ability.sorcery_speed)
        self.assertEqual({"GENERIC": 1}, {key: n for key, n in ability.mana.items() if n})
        self.assertIsNone(activated.target_schema)
        for program, cap in ((static, EXTERNAL_NONTOKEN_ENTRY_COUNTER_CAPABILITY),
                             (activated, CONTROLLED_ARRIVAL_PROLIFERATE_CAPABILITY)):
            deps = tuple(q for q in program.capability_dependencies if q != cap)
            changed = replace(program, capability_dependencies=deps,
                capability_closure=self.registry.closure(deps, profile=PROFILE).to_dict())
            self.assertFalse(bind_semantic_program_runtime(changed, capability_registry=self.registry, profile=PROFILE)["strict"])
        for old, new in (("{1}, {T}", "{2}, {T}"),
                         ("that entered this turn", "that dealt combat damage this turn")):
            with self.subTest(old=old):
                changed = replace(card, oracle_text=card.oracle_text.replace(old, new))
                self.assertNotEqual("exact", compile_oracle_card(changed, capability_registry=self.registry,
                                                               capability_profile=PROFILE).status)

    def test_actual_tromell_entry_counts_only_other_nontoken_creatures_under_its_controller(self):
        game = self.session(80455001)
        source = self.add(game.engine, NAME, zone="hand")
        own = self.add(game.engine, "Grizzly Bears", zone="hand", ref="tromell-own-entrant")
        creates = self.add(game.engine, "Raise the Alarm", zone="hand")
        foreign = self.add(game.engine, "Grizzly Bears", seat="B", ref="tromell-foreign-entrant")
        blink = self.add(game.engine, "Cloudshift", seat="B", zone="hand")
        self.lands(game, "Forest", 4)
        self.lands(game, "Plains", 3)
        self.lands(game, "Plains", 1, seat="B")
        self.start(game, source, creates, blink)
        self.cast_and_resolve(game, "A", source)
        self.assertEqual({}, self.live(game, source).counters)
        self.cast_and_resolve(game, "A", own)
        self.assertEqual({"+1/+1": 1}, self.live(game, own).counters)
        self.assertGreater(game.state.turn_sequence, 0)
        self.assertEqual(game.state.turn_sequence, self.live(game, own).entered_battlefield_turn_sequence)
        self.cast_and_resolve(game, "A", creates)
        tokens = tuple(card for card in game.state.cards.values() if card.is_token and card.zone == "battlefield")
        self.assertEqual(2, len(tokens))
        self.assertTrue(all(not card.counters for card in tokens))
        previous = self.live(game, foreign).logical_object_id
        self.cast_and_resolve(game, "B", blink, [foreign.ref])
        self.assertNotEqual(previous, self.live(game, foreign).logical_object_id)
        self.assertEqual({}, self.live(game, foreign).counters)
        self.replay(game, load=True)

    def choose_proliferate(self, game, refs):
        pending = self.resolve(game)
        self.assertIsNotNone(pending, "Each counted arrival needs its own Proliferate decision")
        self.assertEqual(("semantic.choice", ["A"]), (pending.kind, pending.actors))
        decision = game.packet("pilot:A", full=True)["decision"]["ctx"]
        self.assertEqual("proliferate", decision["operation"])
        for seat in "BCD":
            self.assertIsNone(game.packet("pilot:" + seat, full=True)["decision"])
        command = {"action_id": "choose", "objects": list(refs)}
        self.reject(game, "B", command)
        self.reject(game, "A", {**command, "objects": ["unoffered-tromell-permanent"]})
        accepted = game.act("pilot:A", command)
        self.assertTrue(accepted.ok, accepted.summary)

    def test_actual_tromell_counts_live_arrivals_once_and_each_decision_replays(self):
        game = self.session(80455002)
        source = self.add(game.engine, NAME)
        source.acquired_control_turn_count = -1
        first, second, departing = (self.add(game.engine, "Grizzly Bears", ref=f"tromell-arrival-{n}")
                                    for n in (1, 2, 3))
        token = self.add(game.engine, "Grizzly Bears", ref="tromell-current-token")
        token.is_token = True
        token.object_kind = "token"
        foreign = self.add(game.engine, "Grizzly Bears", seat="C", ref="tromell-current-opponent")
        bounce = self.add(game.engine, "Unsummon", seat="B", zone="hand", ref="tromell-private-response")
        recipient = self.add(game.engine, "Forest", ref="tromell-current-noncreature")
        recipient.counters.update({"charge": 1, "quest": 2})
        self.lands(game, "Forest", 1)
        self.lands(game, "Island", 1, seat="B")
        turn = game.state.turn_sequence
        self.assertGreater(turn, 0)
        for card in (first, second, departing, token, foreign, recipient):
            card.entered_battlefield_turn_sequence = turn
        for card in (first, second, departing):
            card.counters["+1/+1"] = 1
        self.start(game, source, bounce)
        self.assertNotIn(bounce.ref, json.dumps(game.packet("pilot:A", full=True)))
        self.activate(game, source)
        self.cast_and_resolve_reply(game, bounce, [departing.ref])
        self.choose_proliferate(game, [first.ref, recipient.ref])
        self.assertEqual({"+1/+1": 2}, self.live(game, first).counters)
        self.assertEqual({"charge": 2, "quest": 3}, self.live(game, recipient).counters)
        game = self.replay(game, load=True)
        self.choose_proliferate(game, [second.ref])
        self.assertIsNone(self.resolve(game))
        self.assertEqual({"+1/+1": 2}, self.live(game, second).counters)
        self.assertEqual("hand", self.live(game, departing).zone)
        self.replay(game, load=True)

    def cast_and_resolve_reply(self, game, spell, targets):
        self.cast(game, "B", spell, targets)
        self.resolve_top(game)

    def test_actual_tromell_zero_arrivals_still_pays_without_offering_proliferate(self):
        game = self.session(80455003)
        source = self.add(game.engine, NAME)
        source.acquired_control_turn_count = -1
        existing = self.add(game.engine, "Grizzly Bears")
        existing.counters["+1/+1"] = 1
        self.lands(game, "Forest", 1)
        self.start(game, source)
        self.activate(game, source)
        self.assertIsNone(self.resolve(game))
        self.assertEqual({"+1/+1": 1}, self.live(game, existing).counters)
        self.replay(game, load=True)

    def test_tromell_entry_witness_kills_treating_tokens_as_nontoken(self):
        from quorune.semantic_runtime.external_entry_counters import (
            ExternalEntryCounterHandler, ExternalNontokenEntryCounterHandler,
        )

        with patch.object(ExternalNontokenEntryCounterHandler, "source_replacement_effect",
                          ExternalEntryCounterHandler.source_replacement_effect), self.assertRaises(AssertionError):
            self.test_actual_tromell_entry_counts_only_other_nontoken_creatures_under_its_controller()

    def test_tromell_activation_witness_kills_collapsing_repeated_proliferation(self):
        from quorune.semantic_choices.controlled_arrival_proliferate import ControlledArrivalProliferateHandler

        original = ControlledArrivalProliferateHandler.prepare

        def once(handler, effect, context):
            prepared = original(handler, effect, context)
            return replace(prepared, auto_continue=replace(prepared.auto_continue,
                prepend_effects=prepared.auto_continue.prepend_effects[:1]))

        with patch.object(ControlledArrivalProliferateHandler, "prepare", once), self.assertRaisesRegex(
                AssertionError, "own Proliferate decision"):
            self.test_actual_tromell_counts_live_arrivals_once_and_each_decision_replays()
