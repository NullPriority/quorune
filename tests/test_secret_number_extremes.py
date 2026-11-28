"""Native secret commitments, public revelation and sequential wheel resolution."""

from dataclasses import replace
import json
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
from quorune.errors import GameRuleError
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.secret_number_choices import CAPABILITY, DEPENDENCIES, parse_secret_number_response
from quorune.stack_resolution import arbiter_resolution_effects
from scripts.build_test_database import build_fixture_database

CARD = "Wheel of Misfortune"
PROFILE = "commander_review"
REVEAL = "semantic.secret_numbers.revealed"


class SecretNumberExtremesTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "secret-numbers.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures" / name for name in (
            "bound-effect-program-cards.json", "secret-number-extremes-cards.json",
        )], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        commander = "Generic Bound Commander"
        cls.deck = DeckDefinition("Private number choices", [
            DeckEntry(commander, 1, "commander"), DeckEntry("Generic Bound Plains", 30),
        ], [commander])

    def test_compile_the_complete_source_relative_instruction_and_fail_closed(self):
        pinned = self.db.lookup(CARD, fuzzy=False)
        printed = pinned.oracle_text.replace(CARD, "Generic Query Observer")
        record = replace(query_record(printed), type_line="Sorcery", mana_cost="{2}{R}")
        ir = compile_oracle_card(record, capability_registry=self.registry, capability_profile=PROFILE)
        self.assertEqual("exact", ir.status, ir.material_residuals)
        node, = ir.faces[0].nodes
        self.assertEqual(printed, printed[node.span.start:node.span.end])
        self.assertTrue(set(DEPENDENCIES).issubset(node.capability_dependencies))
        for before, after in (
            ("secretly chooses", "publicly chooses"),
            ("simultaneously", "one at a time"),
            ("Generic Query Observer deals", "Another permanent deals"),
            ("didn't choose the lowest", "chose the lowest"),
        ):
            with self.subTest(after=after):
                changed = replace(record, oracle_text=printed.replace(before, after))
                self.assertNotEqual("exact", compile_oracle_card(
                    changed, capability_registry=self.registry, capability_profile=PROFILE).status)
        self.assertEqual(9007199254740993, parse_secret_number_response({"number": "9007199254740993"}))
        with self.assertRaisesRegex(GameRuleError, "exact registered"):
            arbiter_resolution_effects(node.effects)

        binding, semantics = register_card(self.db, self.registry, CARD)
        self.assertTrue(binding["strict_capability_ready"], binding["blockers"])
        program, = semantics.programs_for_oracle(pinned.oracle_id)
        for removed in DEPENDENCIES:
            with self.subTest(removed=removed):
                deps = [key for key in program.capability_dependencies if key != removed]
                changed = replace(program, capability_dependencies=deps,
                                  capability_closure=self.registry.closure(deps, profile=PROFILE).to_dict())
                self.assertFalse(bind_semantic_program_runtime(
                    changed, capability_registry=self.registry, profile=PROFILE)["strict"])

    def begin(self, seed, *, protected=None):
        game = self.session(seed)
        spell = self.add(game.engine, CARD, zone="hand")
        self.assert_closed(game, spell)
        for seat in "ABCD":
            keep = int(seat != "B")
            regular = [object_id for object_id in game.state.players[seat].zones["hand"]
                       if object_id != spell.object_id]
            for object_id in regular[keep:]:
                game.engine.move_card(object_id, "library", position="bottom", log=False)
        if protected:
            game.state.players[protected].stats["protection_from_everything_until_next_turn"] = True
        self.prepare(game, mana={"C": 2, "R": 1})
        self.checkpoint(game)
        self.cast(game, "A", spell)
        pending = self.resolve(game)
        self.assertEqual(("semantic.choice", list("ABCD")), (pending.kind, pending.actors))
        self.assertEqual([f"pilot:{seat}" for seat in "ABCD"], game.pending_principals())
        for seat in "ABCD":
            decision = game.packet(f"pilot:{seat}", full=True)["decision"]
            self.assertEqual((pending.decision_id, 1), (decision["id"], decision["sim"]))
            self.assertEqual({"field": "number", "type": "string", "nonempty": True},
                             decision["ctx"]["legal_actions"][0]["choice_schema"])
        return game, spell

    def choose(self, game, seat, number):
        receipt = game.act(f"pilot:{seat}", {"action_id": "choose", "choices": {"number": number}})
        self.assertTrue(receipt.ok, receipt.summary)

    def assert_secret(self, game, seats, value):
        self.assertFalse(any(event.code == REVEAL for event in game.state.events))
        for seat in seats:
            packet = game.packet(f"pilot:{seat}", full=True)
            serialized = json.dumps(packet)
            self.assertNotIn(value, serialized)
            self.assertNotIn('"number": 7', serialized)
            self.assertNotIn('"number": "7"', serialized)
            self.assertNotIn('"responses":', serialized)
            self.assertNotIn('"number_commitments":', serialized)
            recent = game.projector.event_page(f"pilot:{seat}", after=game.state.event_sequence - 8)
            self.assertFalse(any(row["code"] == REVEAL for row in recent["events"]))
            self.assertNotIn(value, json.dumps(recent))

    def assert_public_reveal(self, game, after):
        expected = "All players reveal their chosen numbers. A chose 0, B chose 7, C chose 0, D chose 7."
        for principal in ("pilot:A", "pilot:B", "pilot:C", "pilot:D", "spectator"):
            page = game.projector.event_page(principal, after=after)
            self.assertEqual([expected], [row["summary"] for row in page["events"] if row["code"] == REVEAL])

    def test_four_seats_keep_commitments_private_then_resolve_both_ties_in_order(self):
        game, spell = self.begin(107120401)
        start = game.state.event_sequence
        history_start = len(game.state.turn_history.events)
        held = {seat: tuple(game.state.players[seat].zones["hand"]) for seat in "ABCD"}
        self.choose(game, "D", "000000007")
        self.assert_secret(game, "ABC", "000000007")
        before = authoritative_state_hash(game.state)
        cap = game.packet("pilot:B", full=True)["decision"]["cap"]
        for bad in (-1, True, "1.5", "-1", "+7", "", "７", None):
            with self.subTest(invalid_number=bad):
                result = game.act("pilot:B", {"action_id": "choose", "choices": {"number": bad}})
                self.assertFalse(result.ok)
                self.assertEqual(before, authoritative_state_hash(game.state))
                self.assertEqual(cap, game.packet("pilot:B", full=True)["decision"]["cap"])
        self.assertFalse(game.act("pilot:D", {"action_id": "choose", "number": "23"}).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))

        game = self.replay(game, load=True)
        self.assertEqual(["pilot:A", "pilot:B", "pilot:C"], game.pending_principals())
        self.assert_secret(game, "ABC", "000000007")
        self.choose(game, "B", "7")
        self.choose(game, "A", "0")
        self.assert_secret(game, "C", "000000007")
        self.assertEqual([40] * 4, [game.state.players[seat].life for seat in "ABCD"])
        self.assertEqual(held, {seat: tuple(game.state.players[seat].zones["hand"]) for seat in "ABCD"})
        self.choose(game, "C", "0")

        self.assertEqual([40, 33, 40, 33], [game.state.players[seat].life for seat in "ABCD"])
        self.assertEqual([1, 7, 1, 7], [len(game.state.players[seat].zones["hand"]) for seat in "ABCD"])
        self.assertTrue(set(held["D"]).issubset(game.state.players["D"].zones["graveyard"]))
        self.assertEqual([held[seat] for seat in "AC"],
                         [tuple(game.state.players[seat].zones["hand"]) for seat in "AC"])
        self.assertEqual("graveyard", game.state.cards[spell.object_id].zone)
        events = [event for event in game.state.events if event.event_id > start]
        reveal, = [event for event in events if event.code == REVEAL]
        self.assertEqual(("0", "7"), (reveal.details["lowest"], reveal.details["highest"]))
        self.assertEqual((("A", "0"), ("B", "7"), ("C", "0"), ("D", "7")), reveal.details["choices"])
        history = game.state.turn_history.events[history_start:]
        damage = [row for row in history if row.kind == "player_damaged"]
        self.assertEqual([("B", 7), ("D", 7)], [(row.target, row.amount) for row in damage])
        self.assertEqual([("A", ("sorcery",))] * 2, [(row.actor, row.types) for row in damage])
        actions = {"player_damaged", "card_discarded", "card_drawn"}
        self.assertEqual(["player_damaged"] * 2 + ["card_discarded"] + ["card_drawn"] * 14,
                         [row.kind for row in history if row.kind in actions])
        draws = [event for event in events if event.code == "card.draw"]
        self.assertEqual(["B"] * 7 + ["D"] * 7, [event.actor for event in draws])
        self.assertLess(reveal.event_id, draws[0].event_id)
        self.assert_public_reveal(game, start)
        reloaded = self.replay(game, load=True)
        self.assert_public_reveal(reloaded, start)

    def test_equal_numbers_preserve_every_hand_and_zero_deals_no_damage(self):
        for amount in (0, 4):
            with self.subTest(amount=amount):
                game, _ = self.begin(107120410 + amount)
                start = len(game.state.events)
                held = {seat: tuple(game.state.players[seat].zones["hand"]) for seat in "ABCD"}
                for seat in "ABCD":
                    self.choose(game, seat, str(amount))
                self.assertEqual([40 - amount] * 4, [game.state.players[seat].life for seat in "ABCD"])
                self.assertEqual(held, {seat: tuple(game.state.players[seat].zones["hand"]) for seat in "ABCD"})
                events = game.state.events[start:]
                self.assertEqual(1, sum(event.code == REVEAL for event in events))
                damage = [row for row in game.state.turn_history.events if row.kind == "player_damaged"]
                self.assertEqual([amount] * 4 if amount else [], [row.amount for row in damage])
                self.assertFalse(any(event.code == "card.draw" for event in events))
                self.replay(game, load=True)

    def test_preventing_tied_high_damage_does_not_change_redraw_eligibility(self):
        game, _ = self.begin(107120421, protected="D")
        for seat, amount in zip("ABCD", ("0", "7", "4", "7")):
            self.choose(game, seat, amount)
        self.assertEqual([40, 33, 40, 40], [game.state.players[seat].life for seat in "ABCD"])
        self.assertEqual([1, 7, 7, 7], [len(game.state.players[seat].zones["hand"]) for seat in "ABCD"])
        self.replay(game, load=True)
