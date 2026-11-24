"""Folio mills each opponent's own current hand size in the shared batch."""

from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune import milling
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.maximum_hand_size import NO_MAXIMUM_HAND_SIZE_CAPABILITY_ID
from quorune.oracle_ir import compile_oracle_card
from quorune.query_effect_amount_model import OPPONENT_HAND_MILL_CAPABILITY, OpponentHandMillCount
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


NAME = "Folio of Fancies"
PROFILE = "commander_review"
CAPABILITY = OPPONENT_HAND_MILL_CAPABILITY


class OpponentHandSizeBatchMillTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "opponent-current-hand-mill.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures/opponent-hand-size-batch-mill-cards.json"], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Per-opponent hand size Mill", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"), DeckEntry("Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def programs(self):
        card = self.db.lookup(NAME, fuzzy=False)
        binding, semantics = register_card(self.db, self.registry, NAME)
        self.assertTrue(binding["strict_capability_ready"], binding["blockers"])
        programs = semantics.programs_for_oracle(card.oracle_id)
        activations = [program for program in programs if program.event == "activate"]
        self.assertEqual(2, len(activations))
        self.assertTrue(any(NO_MAXIMUM_HAND_SIZE_CAPABILITY_ID in item.capability_dependencies for item in programs))
        draw, = [program for program in activations if program.effects[0]["op"] == "draw_each_player"]
        mill, = [program for program in activations if program.effects[0]["op"] == "mill"]
        return card, draw, mill

    def test_complete_original_preserves_both_paid_activations_and_global_cleanup(self):
        card, _draw, program = self.programs()
        self.assertIsNone(program.target_schema)
        self.assertIn(CAPABILITY, program.capability_dependencies)
        effect, = program.effects
        self.assertEqual({
            "op": "mill", "scope": "controller_opponents", "count": OpponentHandMillCount().to_dict(),
        }, effect)
        for text in (
            card.oracle_text.replace("Each opponent mills", "Each player mills"),
            card.oracle_text.replace("Each opponent mills", "Target opponent mills"),
            card.oracle_text.replace("in their hand", "in your hand"),
        ):
            ir = compile_oracle_card(replace(card, oracle_text=text), capability_registry=self.registry)
            self.assertFalse(any(CAPABILITY in node.capability_dependencies for face in ir.faces for node in face.nodes))

    def test_raw_hand_count_refuses_alternate_scopes_and_requires_its_own_capability(self):
        _, _draw, program = self.programs()
        mill, = program.effects
        for altered in (
            replace(program, effects=({**mill, "scope": "all_active_players"},)),
            replace(program, effects=({**mill, "player": "$target.0"},)),
            replace(program, effects=({**mill, "count": {**mill["count"], "schema_version": True}},)),
            replace(program, target_schema={"zones": ["player"], "categories": ["player"], "count": 1}),
        ):
            binding = bind_semantic_program_runtime(altered, capability_registry=self.registry, profile=PROFILE)
            self.assertFalse(binding["strict"], binding)
        for withheld in (CAPABILITY, "zone.mill.fixed"):
            caps = [item for item in program.capability_dependencies if item != withheld]
            altered = replace(program, capability_dependencies=caps,
                              capability_closure=self.registry.closure(caps, profile=PROFILE).to_dict())
            binding = bind_semantic_program_runtime(altered, capability_registry=self.registry, profile=PROFILE)
            self.assertFalse(binding["strict"], binding)
            self.assertIn("capability:undeclared_runtime_dependency:" + withheld, binding["blockers"])

    def scene(self, seed, *, mill_mode):
        game = self.session(seed)
        engine = game.engine
        folio = self.add(engine, NAME, zone="hand")
        for index in range(7):
            self.add(engine, "Island", ref=f"folio-payment-{index}")
        hidden = response = None
        if mill_mode:
            for seat in "BCD":
                for object_id in tuple(game.state.players[seat].zones["hand"]):
                    engine.move_card(object_id, "library", log=False)
            for object_id in tuple(game.state.players["C"].zones["library"])[:-2]:
                engine.move_card(object_id, "exile", log=False)
            hidden = self.add(engine, "Counterspell", seat="B", zone="hand", ref="folio-private-hand")
            self.add(engine, "Plains", seat="B", zone="hand", ref="folio-other-b-hand")
            response = self.add(engine, "Lightning Bolt", seat="B", zone="hand")
            self.add(engine, "Mountain", seat="B")
            for index in range(4):
                self.add(engine, "Plains", seat="C", zone="hand", ref=f"folio-c-hand-{index}")
            leyline = self.add(engine, "Leyline of the Void", seat="B")
            self.assert_closed(game, leyline)
            self.assert_closed(game, response)
        self.assert_closed(game, folio)
        self.prepare(game)
        self.checkpoint(game)
        self.cast_for(game, "A", folio)
        self.resolve_top(game)
        return game, folio, hidden, response

    def activate(self, game, folio, *, x=None):
        self.priority(game, "A")
        source = game.state.cards[folio.object_id]
        ability, = [item for item in game.engine._activated_abilities(source)
                    if bool(item.announced_x_symbols) == (x is not None)]
        action = f"activate:{source.ref}:{ability.ability_id}"
        offered = game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        self.assertIn(action, {entry["id"] for entry in offered})
        command = {"action_id": action, "pay": "auto"}
        if x is not None:
            command["x"] = x
        before = authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:C", command).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        lands = [card.object_id for card in game.state.cards.values()
                 if card.controller == "A" and card.zone == "battlefield" and card.printed_name == "Island"]
        untapped = sum(not game.state.cards[identity].tapped for identity in lands)
        result = game.act("pilot:A", command)
        self.assertTrue(result.ok, result.summary)
        self.assertTrue(game.state.cards[folio.object_id].tapped)
        self.assertEqual(3 if x is None else 2 * x,
                         untapped - sum(not game.state.cards[identity].tapped for identity in lands))

    def mill_scene(self):
        game, folio, hidden, response = self.scene(70117110, mill_mode=True)
        self.assertEqual((3, 4, 0), tuple(len(game.state.players[seat].zones["hand"]) for seat in "BCD"))
        self.activate(game, folio)
        self.cast_for(game, "B", response, ["A"])
        self.resolve_top(game)
        self.assertEqual(2, len(game.state.players["B"].zones["hand"]))
        tops = {seat: tuple(reversed(game.state.players[seat].zones["library"][-2:])) for seat in "BC"}
        libraries = {seat: tuple(game.state.players[seat].zones["library"]) for seat in "AD"}
        with patch.object(milling, "mill_player_batch", wraps=milling.mill_player_batch) as batch:
            self.resolve_top(game)
        batch.assert_called_once()
        requests = batch.call_args.args[1]
        self.assertEqual([("B", 2), ("C", 4), ("D", 0)], [(item.player, item.count) for item in requests],
                         "Each opponent needs its own current hand count")
        self.assertTrue(all(game.state.cards[identity].zone == "graveyard" for identity in tops["B"]))
        self.assertTrue(all(game.state.cards[identity].zone == "exile" for identity in tops["C"]))
        self.assertEqual([], game.state.players["C"].zones["library"])
        self.assertTrue(game.state.players["C"].in_game)
        for seat in "AD":
            self.assertEqual(libraries[seat], tuple(game.state.players[seat].zones["library"]))
        for principal in ("pilot:A", "pilot:C", "pilot:D", "spectator"):
            self.assertNotIn(hidden.ref, json.dumps(game.packet(principal, full=True)))
        self.assertIn(hidden.ref, json.dumps(game.packet("pilot:B", full=True)))
        return game

    def test_actual_mill_reads_each_opponent_after_a_response_and_commits_one_replaced_batch(self):
        self.replay(self.mill_scene(), load=True)

    def test_actual_double_x_payment_keeps_the_independent_all_player_draw(self):
        game, folio, _hidden, _response = self.scene(70117111, mill_mode=False)
        sizes = [len(game.state.players[seat].zones["hand"]) for seat in "ABCD"]
        self.activate(game, folio, x=2)
        self.resolve_top(game)
        self.assertEqual([size + 2 for size in sizes], [len(game.state.players[seat].zones["hand"]) for seat in "ABCD"])
        options = game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        self.assertFalse(any(item["id"].startswith(f"activate:{folio.ref}:") for item in options))
        self.replay(game, load=True)

    def test_actual_unequal_batch_kills_using_the_controllers_count_for_everyone(self):
        original = milling.mill_request

        def substitute(host, *, actor, player, count, reason):
            if isinstance(count, OpponentHandMillCount):
                count = len(host.state.players[actor].zones["hand"])
            return original(host, actor=actor, player=player, count=count, reason=reason)

        with patch.object(milling, "mill_request", substitute):
            with self.assertRaisesRegex(AssertionError, "Each opponent needs its own current hand count"):
                self.mill_scene()
