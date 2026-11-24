"""The sealed upkeep participant must return one current controlled creature."""

from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.object_predicate import ObjectQuerySpec
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


NAME = "Sunken Hope"
PROFILE = "commander_review"


class ScheduledPlayerCreatureReturnTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "scheduled-creature-return.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures/scheduled-player-creature-return-cards.json"], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Sealed upkeep return", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"), DeckEntry("Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def program(self):
        card = self.db.lookup(NAME, fuzzy=False)
        binding, semantics = register_card(self.db, self.registry, NAME)
        self.assertTrue(binding["strict_capability_ready"], binding["blockers"])
        program, = semantics.programs_for_oracle(card.oracle_id)
        return card, program

    def test_complete_original_has_one_sealed_mandatory_untargeted_return(self):
        card, program = self.program()
        self.assertEqual(("step.begin", "battlefield"), (program.event, program.active_zone))
        self.assertEqual({"all": [{"field": "step", "op": "eq", "value": "upkeep"}]}, program.event_condition)
        self.assertIsNone(program.target_schema)
        effect, = program.effects
        self.assertEqual(("choose_cards_apnap", "$controller", ["$context.player"], 1, "return_owner_hand"),
                         (effect["op"], effect["actor"], effect["players"], effect["count"], effect["then"]))
        self.assertEqual(ObjectQuerySpec(zones=("battlefield",), types_all=("creature",)),
                         ObjectQuerySpec.from_dict(effect["predicate"]))
        for text in (
            card.oracle_text.replace("each player's", "each opponent's"),
            card.oracle_text.replace("a creature they control", "a permanent they control"),
            card.oracle_text.replace("that player returns", "that player may return"),
        ):
            altered = replace(card, oracle_text=text)
            self.assertNotEqual("exact", compile_oracle_card(altered, capability_registry=self.registry).status)

    def test_raw_scope_requires_exact_upkeep_actor_and_creature_bounds(self):
        _, program = self.program()
        effect, = program.effects
        for altered in (
            replace(program, event_condition={"all": [{"field": "step", "op": "eq", "value": "end_step"}]}),
            replace(program, effects=({**effect, "players": ["$controller"]},)),
            replace(program, effects=({**effect, "count": 2},)),
            replace(program, effects=({**effect, "predicate": ObjectQuerySpec(zones=("battlefield",)).to_dict()},)),
        ):
            binding = bind_semantic_program_runtime(altered, capability_registry=self.registry, profile=PROFILE)
            self.assertFalse(binding["strict"], binding)

    def begin_upkeep(self, game, source):
        self.until(game, lambda current: current.state.active_player == "B" and current.state.step == "upkeep"
                   and any(item.source_object_id == source.object_id for item in current.state.stack))
        item, = [item for item in game.state.stack if item.source_object_id == source.object_id]
        self.assertEqual(("A", "B", []), (item.controller, item.context["player"], item.targets))

    def scene(self):
        game = self.session(60350031)
        engine = game.engine
        source = self.add(engine, NAME, zone="hand")
        donate = self.add(engine, "Donate", zone="hand")
        borrowed = self.add(engine, "Slippery Bogle")
        own = self.add(engine, "Grizzly Bears", seat="B", ref="return-b-own")
        foreign = self.add(engine, "Grizzly Bears", seat="C", ref="return-c-foreign")
        remove = self.add(engine, "Disenchant", seat="B", zone="hand")
        land = self.add(engine, "Plains", seat="B", ref="return-b-noncreature")
        self.add(engine, "Plains", seat="B", ref="return-b-payment")
        secret = self.add(engine, "Counterspell", seat="D", zone="hand", ref="unrelated-return-hand")
        for card in (source, donate, borrowed, remove):
            self.assert_closed(game, card)
        self.prepare(game, mana={"C": 5, "U": 3})
        self.checkpoint(game)
        self.cast_for(game, "A", donate, ["B", borrowed.ref], reserve={"C": 3, "U": 2})
        self.resolve_top(game)
        self.assertEqual(("A", "B"), (game.state.cards[borrowed.object_id].owner, game.state.cards[borrowed.object_id].controller))
        self.cast_for(game, "A", source)
        self.resolve_top(game)
        self.begin_upkeep(game, source)
        self.cast_for(game, "B", remove, [source.ref])
        self.resolve_top(game)
        self.assertEqual("graveyard", game.state.cards[source.object_id].zone)
        decision = self.resolve(game)
        self.assertIsNotNone(decision, "The actual upkeep participant needs the mandatory return")
        self.assertEqual(("choice.apnap", ["B"]), (decision.kind, decision.actors),
                         "The actual upkeep participant must assign the return")
        return game, borrowed, own, foreign, land, secret

    def test_actual_upkeep_player_returns_the_borrowed_hexproof_creature_to_its_owner_after_source_loss(self):
        game, borrowed, own, foreign, land, secret = self.scene()
        packet = game.packet("pilot:B", full=True)["decision"]["ctx"]
        self.assertEqual(1, packet["count"])
        self.assertEqual({borrowed.ref, own.ref}, set(packet["options"]))
        for viewer in ("pilot:A", "pilot:C", "pilot:D", "spectator"):
            self.assertIsNone(game.packet(viewer, full=True)["decision"])
        before = authoritative_state_hash(game.state)
        for seat, cards in (("A", [borrowed.ref]), ("B", []), ("B", [foreign.ref]),
                            ("B", [land.ref]), ("B", [borrowed.ref, own.ref])):
            result = game.act("pilot:" + seat, {"action_id": "choose", "cards": cards})
            self.assertFalse(result.ok, result.summary)
            self.assertEqual(before, authoritative_state_hash(game.state))
        game = self.replay(game, load=True)
        result = game.act("pilot:B", {"action_id": "choose", "cards": [borrowed.ref]})
        self.assertTrue(result.ok, result.summary)
        self.assertIn(borrowed.object_id, game.state.players["A"].zones["hand"])
        self.assertNotIn(borrowed.object_id, game.state.players["B"].zones["hand"])
        self.assertEqual("battlefield", game.state.cards[own.object_id].zone)
        self.assertEqual("battlefield", game.state.cards[foreign.object_id].zone)
        for viewer in ("pilot:A", "pilot:B", "pilot:C", "spectator"):
            self.assertNotIn(secret.ref, json.dumps(game.packet(viewer, full=True)))
        self.replay(game, load=True)

    def test_actual_upkeep_witness_kills_routing_the_choice_to_the_trigger_controller(self):
        from quorune.selection.apnap import ApnapChoiceOwnerMixin

        issue = ApnapChoiceOwnerMixin._issue_apnap_choice

        def trigger_controller_picks(host, *, effect, continuation):
            return issue(host, effect={**effect, "players": [effect["actor"]]}, continuation=continuation)

        with patch.object(ApnapChoiceOwnerMixin, "_issue_apnap_choice", trigger_controller_picks):
            with self.assertRaisesRegex(AssertionError, "actual upkeep participant"):
                self.scene()

    def test_actual_no_creature_and_departed_upkeep_actor_complete_without_a_choice(self):
        for departed in (False, True):
            with self.subTest(departed=departed):
                game = self.session(60350032 + departed)
                engine = game.engine
                source = self.add(engine, NAME, zone="hand")
                foreign = self.add(engine, "Grizzly Bears", seat="C", ref="return-no-actor-foreign")
                if departed:
                    self.add(engine, "Grizzly Bears", seat="B", ref="return-departed-creature")
                self.assert_closed(game, source)
                self.prepare(game, mana={"C": 3, "U": 2})
                self.checkpoint(game)
                self.cast_for(game, "A", source)
                self.resolve_top(game)
                self.begin_upkeep(game, source)
                if departed:
                    result = game.act("pilot:B", {"action_id": "concede", "choices": {"confirm_concede": True}})
                    self.assertTrue(result.ok, result.summary)
                    self.assertFalse(game.state.players["B"].in_game)
                self.assertIsNone(self.resolve(game))
                self.assertFalse(game.state.stack)
                self.assertEqual("battlefield", game.state.cards[foreign.object_id].zone)
                self.replay(game, load=True)
