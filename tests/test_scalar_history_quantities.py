"""CR 608.2c/h, 700.4 and 701.9a for typed turn-history result amounts."""

import json
from pathlib import Path
import tempfile
import unittest

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.scalar_effect_amount_model import (
    SCALAR_AMOUNT_CAPABILITY,
    ScalarAmountOrigin,
    ScalarEffectAmountSpec,
)
from scripts.build_test_database import build_fixture_database
from test_bound_effect_programs import record


class ScalarHistoryQuantityCompilerTests(unittest.TestCase):
    def test_counted_turn_history_is_typed_and_preserves_the_printed_span(self):
        registry = load_default_capability_registry()
        cases = (
            ("Draw a card for each card you've discarded this turn.", "count", "cards_discarded"),
            ("Draw a card for each card you\u2019ve discarded this turn.", "count", "cards_discarded"),
            ("Create a Treasure token for each creature that died this turn.", "quantity", "creatures_died"),
        )
        for text, result_slot, fact in cases:
            with self.subTest(text=text):
                ir = compile_oracle_card(record(text, "Sorcery"), capability_registry=registry)
                self.assertEqual("exact", ir.status, ir.material_residuals)
                node, = ir.faces[0].nodes
                self.assertEqual((0, len(text)), (node.span.start, node.span.end))
                self.assertIn(SCALAR_AMOUNT_CAPABILITY, node.capability_dependencies)
                amount = ScalarEffectAmountSpec.from_dict(node.effects[0][result_slot])
                self.assertEqual(ScalarAmountOrigin.HISTORY, amount.origin)
                self.assertEqual(fact, amount.history_fact)
                self.assertIsNone(amount.binding_id)
        for text in (
            "Draw a card for each nontoken creature that died this turn.",
            "Draw a card for each creature that died this way.",
            "Draw a card for each card target opponent discarded this turn.",
        ):
            with self.subTest(text=text):
                ir = compile_oracle_card(record(text, "Sorcery"), capability_registry=registry)
                self.assertNotEqual("exact", ir.status)


class ScalarHistoryQuantityActionTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        path = Path(cls.temporary.name) / "scalar-history-quantities.sqlite3"
        build_fixture_database(
            [
                ROOT / "tests/fixtures/bound-effect-program-cards.json",
                ROOT / "tests/fixtures/creature-power-multiple-cards.json",
                ROOT / "tests/fixtures/state-based-destination-cards.json",
                ROOT / "tests/fixtures/scalar-history-quantity-cards.json",
            ],
            path,
        )
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition(
            "Scalar history quantities",
            [DeckEntry("Generic Bound Commander", 1, "commander"), DeckEntry("Generic Bound Plains", 20)],
            ["Generic Bound Commander"],
        )

    def test_actual_death_counts_include_current_instruction_and_earlier_token_death(self):
        session = self.session(608270040)
        first = self.add(session.engine, "Deadly Embrace", zone="hand", ref="embrace-first")
        second = self.add(session.engine, "Deadly Embrace", zone="hand", ref="embrace-second")
        mahadi = self.add(session.engine, "Mahadi, Emporium Master", zone="hand")
        weaken = self.add(session.engine, "Disfigure", zone="hand")
        bounce = self.add(session.engine, "Unsummon", seat="B", zone="hand")
        escaped = self.add(session.engine, "Grizzly Bears", seat="B", ref="embrace-escaped")
        killed = self.add(session.engine, "Grizzly Bears", seat="C", ref="embrace-killed")
        token_ref, = session.engine.create_token("B", name="", copy_of=escaped.ref)
        token = next(card for card in session.state.cards.values() if card.ref == token_ref)
        for spell in (first, second, mahadi, weaken, bounce):
            self.assert_closed(session, spell)
        self.prepare(session, mana={"C": 7, "B": 6, "R": 1})
        session.state.players["B"].mana_pool["U"] = 1
        self.checkpoint(session)

        self.cast(session, "A", weaken, [token_ref])
        self.assertIsNone(self.resolve(session))
        self.assertTrue(
            token.object_id not in session.state.cards
            or session.state.cards[token.object_id].zone != "battlefield"
        )

        self.cast(session, "A", first, [escaped.ref])
        self.cast_for(session, "B", bounce, [escaped.ref])
        self.resolve_top(session)
        self.assertEqual("hand", session.state.cards[escaped.object_id].zone)
        hand = session.state.players["A"].zones["hand"]
        before = len(hand)
        self.assertIsNone(self.resolve(session))
        self.assertEqual(before, len(session.state.players["A"].zones["hand"]))

        self.cast(session, "A", second, [killed.ref])
        self.assertIsNone(self.resolve(session))
        self.assertEqual("graveyard", session.state.cards[killed.object_id].zone)
        self.assertEqual(before - 1 + 2, len(session.state.players["A"].zones["hand"]))

        self.cast(session, "A", mahadi)
        self.assertIsNone(self.resolve(session))
        self.until(session, lambda current: self.trigger_count(current, mahadi) == 1)
        session = self.replay(session, load=True)
        self.assertIsNone(self.resolve(session))
        treasures = [
            card for card in session.state.cards.values()
            if card.zone == "battlefield" and card.is_token
            and session.engine._effective_card_data(card)["name"] == "Treasure Token"
        ]
        self.assertEqual(["A", "A"], [card.controller for card in treasures])
        self.replay(session)

    def activate_misty(self, session, seat, source, discarded):
        self.priority(session, seat)
        actions = session.packet("pilot:" + seat, full=True)["decision"]["ctx"]["legal"]["actions"]
        offered = next(row for row in actions if row["id"].startswith("activate:" + source.ref + ":"))
        before = len(session.state.players[seat].zones["hand"])
        result = session.act(
            "pilot:" + seat, {"action_id": offered["id"], "cost_cards": [discarded.ref], "pay": "auto"}
        )
        self.assertTrue(result.ok, result.summary)
        self.assertTrue(session.state.cards[source.object_id].tapped)
        self.assertEqual("graveyard", session.state.cards[discarded.object_id].zone)
        self.assertIsNone(self.resolve(session))
        self.assertEqual(before, len(session.state.players[seat].zones["hand"]))

    def test_actual_discard_count_includes_paid_and_resumed_discards_of_the_controller(self):
        session = self.session(608270041)
        goblin = self.add(session.engine, "Green Goblin, Revenant")
        misty = self.add(session.engine, "Misty Knight, Hero for Hire", ref="misty-a")
        foreign_misty = self.add(session.engine, "Misty Knight, Hero for Hire", seat="B", ref="misty-b")
        a_cost = self.add(session.engine, "Generic Bound Body", zone="hand", ref="misty-paid-a")
        b_cost = self.add(session.engine, "Generic Bound Body", seat="B", zone="hand", ref="misty-paid-b")
        chosen = self.add(session.engine, "Generic Bound Body", zone="hand", ref="goblin-private-discard")
        for source in (goblin, misty, foreign_misty):
            source.acquired_control_turn_count = -1
            self.assert_closed(session, source)
        self.prepare(session, mana={"C": 2})
        session.state.players["B"].mana_pool["C"] = 2
        self.checkpoint(session)
        self.activate_misty(session, "A", misty, a_cost)
        self.activate_misty(session, "B", foreign_misty, b_cost)

        self.attack(session, [(goblin, "C")])
        self.assertEqual(1, self.trigger_count(session, goblin))
        self.assertEqual("choice.apnap", self.resolve(session).kind)
        self.assertEqual(["pilot:A"], list(session.pending_principals()))
        session = self.replay(session, load=True)
        self.assertNotIn(chosen.ref, json.dumps(session.packet("pilot:B", full=True)))
        before = authoritative_state_hash(session.state)
        command = {"action_id": "choose", "cards": [chosen.ref]}
        self.assertFalse(session.act("pilot:B", command).ok)
        self.assertEqual(before, authoritative_state_hash(session.state))
        hand = len(session.state.players["A"].zones["hand"])
        result = session.act("pilot:A", command)
        self.assertTrue(result.ok, result.summary)
        self.assertIsNone(self.resolve(session))
        self.assertEqual("graveyard", session.state.cards[chosen.object_id].zone)
        self.assertEqual(hand - 1 + 2, len(session.state.players["A"].zones["hand"]))
        self.replay(session)
