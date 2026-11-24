"""Committed Devour entries supply their own creature count to exact triggers."""

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
from quorune.devour import (
    DEVOUR_CAPABILITY, DEVOUR_ENTRY_RECEIPT_FIELD, DEVOUR_ENTRY_RESULT_CAPABILITY,
    DevourEntryAmount, DevourEntryReceipt, resolve_devour_entry_amount,
)
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card
import test_devour as native_devour


NAMES = ("Skullmulcher", "Marrow Chomper", "Tar Fiend")
PROFILE = "commander_review"
FIXTURES = ROOT / "tests/fixtures"


class DevourEntryCountedResultsTests(InheritedNativeFixture, unittest.TestCase):
    choose_devour = native_devour.DevourRuntimeTests.choose_devour
    devour_legal_refs = native_devour.DevourRuntimeTests.devour_legal_refs

    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        folder = Path(cls.temporary.name)
        base = json.loads((FIXTURES / "bound-effect-program-cards.json").read_bytes())
        base["cards"] = [row for row in base["cards"] if row["name"] == "Generic Bound Plains"]
        base["rulings"] = []
        basic = folder / "base.json"
        basic.write_text(json.dumps(base), encoding="utf-8")
        path = folder / "devour-entry-results.sqlite3"
        build_fixture_database([basic, FIXTURES / "devour-entry-counted-results-cards.json"], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Devour entry results", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"), DeckEntry("Generic Bound Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def program(self, name):
        original = self.db.lookup(name, fuzzy=False)
        result, semantics = register_card(self.db, self.registry, name)
        self.assertTrue(result["strict_capability_ready"], result["blockers"])
        programs = semantics.programs_for_oracle(original.oracle_id)
        entry, = [p for p in programs if DEVOUR_ENTRY_RESULT_CAPABILITY in p.capability_dependencies]
        self.assertEqual(("permanent.enter.self", "battlefield"), (entry.event, entry.active_zone))
        self.assertTrue(any(p.event == "zone.change" and DEVOUR_CAPABILITY in p.capability_dependencies
                            for p in programs), programs)
        return original, entry

    def test_original_devour_and_counted_entry_siblings_register_together(self):
        for name, op, key, coefficient in zip(NAMES, ("draw", "life", "choose_cards_apnap"),
                                               ("count", "delta", "count"), (1, 2, 1)):
            with self.subTest(name=name):
                original, program = self.program(name)
                effect, = program.effects
                self.assertEqual(op, effect["op"])
                self.assertEqual(DevourEntryAmount(coefficient), DevourEntryAmount.from_dict(effect[key]))
                for text in (
                    original.oracle_text.replace("for each creature it devoured", "for each Goblin it devoured"),
                    original.oracle_text.replace("for each creature it devoured", "for each creature that died this turn"),
                    original.oracle_text.replace("Devour ", "Devour artifact "),
                ):
                    value = compile_oracle_card(replace(original, oracle_text=text), capability_registry=self.registry,
                                                capability_profile=PROFILE)
                    self.assertFalse(any(DEVOUR_ENTRY_RESULT_CAPABILITY in n.capability_dependencies
                                         for face in value.faces for n in face.nodes), text)

    def test_raw_devour_amount_requires_current_entry_origin_and_independent_authority(self):
        from types import SimpleNamespace

        _, program = self.program("Skullmulcher")
        deps = [cap for cap in program.capability_dependencies if cap != DEVOUR_ENTRY_RESULT_CAPABILITY]
        missing = replace(program, capability_dependencies=deps,
                          capability_closure=self.registry.closure(deps, profile=PROFILE).to_dict())
        denied = bind_semantic_program_runtime(missing, capability_registry=self.registry, profile=PROFILE)
        self.assertFalse(denied["strict"], denied)
        self.assertIn("capability:undeclared_runtime_dependency:" + DEVOUR_ENTRY_RESULT_CAPABILITY, denied["blockers"])
        changed = bind_semantic_program_runtime(replace(program, event="step.begin"),
                                               capability_registry=self.registry, profile=PROFILE)
        self.assertFalse(changed["strict"], changed)
        amount = DevourEntryAmount().to_dict()
        item = SimpleNamespace(kind="triggered_ability", source_object_id="source", context={
            "event": "permanent.enter", "card_object_identity": "source@2",
            DEVOUR_ENTRY_RECEIPT_FIELD: DevourEntryReceipt("source", "source@2", 2).to_dict(),
        })
        self.assertEqual(2, resolve_devour_entry_amount(amount, item))
        for key, value in (("event", "permanent.leave"), ("card_object_identity", "source@3"),
                           (DEVOUR_ENTRY_RECEIPT_FIELD, {"schema_version": 1, "object_id": "other",
                                                         "logical_object_id": "source@2", "count": 2})):
            forged = SimpleNamespace(kind=item.kind, source_object_id=item.source_object_id,
                                     context={**item.context, key: value})
            with self.assertRaises(ValueError):
                resolve_devour_entry_amount(amount, forged)
        with self.assertRaises(ValueError):
            resolve_devour_entry_amount({**amount, "coefficient": True}, item)

    def open_devour(self, game, source, *, targets=(), reserve=None):
        self.cast_for(game, "A", source, list(targets), reserve=reserve)
        pending = self.resolve(game)
        self.assertIsNotNone(pending)
        self.assertEqual(("replacement.order", ["A"]), (pending.kind, pending.actors))

    def test_actual_skullmulcher_draw_uses_redirected_sacrifices_after_departure(self):
        game = self.session(702820301)
        source = self.add(game.engine, "Skullmulcher", zone="hand")
        bounce = self.add(game.engine, "Boomerang", zone="hand")
        first = self.add(game.engine, "Grizzly Bears")
        second = self.add(game.engine, "Runeclaw Bear")
        foreign = self.add(game.engine, "Grizzly Bears", seat="B", ref="devour-foreign")
        secret = self.add(game.engine, "Counterspell", seat="D", zone="hand", ref="devour-draw-secret")
        scales = self.add(game.engine, "Hardened Scales")
        rest = self.add(game.engine, "Rest in Peace", seat="C")
        for card in (source, bounce, scales, rest):
            self.assert_closed(game, card)
        self.prepare(game, mana={"C": 4, "G": 1, "U": 2})
        self.checkpoint(game)
        self.open_devour(game, source, reserve={"U": 2})
        self.assertEqual({first.ref, second.ref}, set(self.devour_legal_refs(game)))
        option = next(row for row in game.packet("pilot:A", full=True)["decision"]["ctx"]["options"]
                      if "replacement.zone.devour-entry.v1:" in row["id"] and not row["decline"])
        command = {"action_id": "choose", "replacement": option["id"], "replacement_objects": [foreign.ref]}
        before = authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:B", command).ok)
        self.assertFalse(game.act("pilot:A", command).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        game = self.replay(game, load=True)
        self.choose_devour(game, [first.ref, second.ref])
        self.assertEqual(["exile", "exile"], [game.state.cards[c.object_id].zone for c in (first, second)])
        entrant = game.state.cards[source.object_id]
        self.assertEqual(3, entrant.counters["+1/+1"])
        self.assertEqual(2, DevourEntryReceipt.from_dict(entrant.annotations[DEVOUR_ENTRY_RECEIPT_FIELD]).count)
        before_draw = len(game.state.players["A"].zones["hand"])
        self.cast_for(game, "A", bounce, [source.ref])
        self.assertIsNone(self.resolve(game))
        self.assertEqual("hand", game.state.cards[source.object_id].zone)
        self.assertEqual(before_draw + 2, len(game.state.players["A"].zones["hand"]))
        for principal in ("pilot:A", "pilot:B", "pilot:C", "spectator"):
            self.assertNotIn(secret.ref, json.dumps(game.packet(principal, full=True)))
        self.replay(game, load=True)

    def marrow_result(self, number):
        game = self.session(702820310 + number)
        source = self.add(game.engine, "Marrow Chomper", zone="hand")
        feed = [self.add(game.engine, name) for name in ("Grizzly Bears", "Runeclaw Bear")]
        self.add(game.engine, "Hardened Scales")
        self.prepare(game, mana={"C": 3, "B": 1, "G": 1})
        self.checkpoint(game)
        self.open_devour(game, source)
        self.choose_devour(game, [card.ref for card in feed[:number]])
        self.assertIsNone(self.resolve(game))
        self.assertEqual(40 + 2 * number, game.state.players["A"].life)
        current = game.state.cards[source.object_id]
        self.assertEqual((2 * number + 1) if number else 0, current.counters.get("+1/+1", 0))
        self.assertEqual(number, DevourEntryReceipt.from_dict(current.annotations[DEVOUR_ENTRY_RECEIPT_FIELD]).count)
        self.replay(game, load=True)

    def test_actual_marrow_chomper_scales_life_and_accepts_zero(self):
        for number in (2, 0):
            with self.subTest(number=number):
                self.marrow_result(number)

    def test_actual_tar_fiend_gives_only_target_player_the_private_counted_discard(self):
        game = self.session(702820325)
        source = self.add(game.engine, "Tar Fiend", zone="hand")
        food = [self.add(game.engine, name) for name in ("Grizzly Bears", "Runeclaw Bear")]
        target_hand = [self.add(game.engine, name, seat="B", zone="hand", ref=f"tar-private-b-{index}")
                       for index, name in enumerate(("Lightning Bolt", "Counterspell", "Boomerang"))]
        other = self.add(game.engine, "Counterspell", seat="C", zone="hand", ref="tar-private-c")
        self.prepare(game, mana={"C": 5, "B": 1})
        self.checkpoint(game)
        self.open_devour(game, source)
        self.choose_devour(game, [card.ref for card in food])
        pending = self.resolve(game)
        self.assertEqual(("semantic.target", ["A"]), (pending.kind, pending.actors))
        self.target(game, "A", ["B"])
        pending = self.resolve(game)
        self.assertEqual(("choice.apnap", ["B"]), (pending.kind, pending.actors))
        for principal in ("pilot:A", "pilot:C", "pilot:D", "spectator"):
            view = json.dumps(game.packet(principal, full=True))
            for card in target_hand:
                self.assertNotIn(card.ref, view)
        self.assertNotIn(other.ref, json.dumps(game.packet("pilot:B", full=True)))
        before = authoritative_state_hash(game.state)
        command = {"action_id": "choose", "cards": [card.ref for card in target_hand[:2]]}
        self.assertFalse(game.act("pilot:A", command).ok)
        self.assertFalse(game.act("pilot:B", {**command, "cards": [target_hand[0].ref, other.ref]}).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        game = self.replay(game, load=True)
        accepted = game.act("pilot:B", command)
        self.assertTrue(accepted.ok, accepted.summary)
        self.assertIsNone(self.resolve(game))
        self.assertEqual(["graveyard", "graveyard", "hand"], [game.state.cards[c.object_id].zone for c in target_hand])
        self.assertEqual("hand", game.state.cards[other.object_id].zone)
        self.assertEqual(4, game.state.cards[source.object_id].counters["+1/+1"])
        self.replay(game, load=True)

    def test_actual_copy_token_gets_a_distinct_devour_receipt_and_draw(self):
        game = self.session(702820332)
        original = self.add(game.engine, "Skullmulcher")
        spell = self.add(game.engine, "Cackling Counterpart", zone="hand")
        meal = self.add(game.engine, "Grizzly Bears")
        original.counters["+1/+1"] = 7
        self.prepare(game, mana={"C": 1, "U": 2})
        self.checkpoint(game)
        before_cast = len(game.state.players["A"].zones["hand"])
        self.open_devour(game, spell, targets=[original.ref])
        self.choose_devour(game, [meal.ref])
        self.assertIsNone(self.resolve(game))
        token, = [card for card in game.state.cards.values()
                  if card.is_token and card.zone == "battlefield" and card.controller == "A"]
        receipt = DevourEntryReceipt.from_dict(token.annotations[DEVOUR_ENTRY_RECEIPT_FIELD])
        self.assertEqual((token.object_id, token.logical_object_id, 1),
                         (receipt.object_id, receipt.logical_object_id, receipt.count))
        self.assertEqual(1, token.counters["+1/+1"])
        self.assertEqual(7, game.state.cards[original.object_id].counters["+1/+1"])
        self.assertNotIn(DEVOUR_ENTRY_RECEIPT_FIELD, game.state.cards[original.object_id].annotations)
        self.assertEqual(before_cast, len(game.state.players["A"].zones["hand"]))
        self.replay(game, load=True)

    def test_actual_life_gain_kills_omitting_committed_devour_count(self):
        with patch("quorune.zone_devour.devoured_entry_creature_count", return_value=0):
            with self.assertRaisesRegex(AssertionError, "44 != 40"):
                self.marrow_result(2)
