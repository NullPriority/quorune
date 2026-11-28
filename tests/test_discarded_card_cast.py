from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.discarded_card_cast import DISCARDED_CARD_CAST, discarded_card_cast_effects
from scripts.build_test_database import build_fixture_database


class DiscardedCardCastTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        path = Path(cls.temporary.name) / "discarded-card-cast.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures" / name for name in (
            "bound-effect-program-cards.json", "targeted-graveyard-cast-cards.json",
        )], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Resolving a discarded card", [
            DeckEntry("Generic Bound Commander", 1, "commander"), DeckEntry("Generic Bound Plains", 30),
        ], ["Generic Bound Commander"])

    def _scene(self, seed, *, counters=3, selected="Lightning Bolt"):
        session = self.session(seed)
        source = self.add(session.engine, "Malcolm, Alluring Scoundrel", ref="malcolm-combat-source")
        source.acquired_control_turn_count = -1
        source.counters["chorus"] = counters
        discard = self.add(session.engine, selected, zone="hand", ref="malcolm-private-discard")
        bystander = self.add(session.engine, "Lightning Bolt", zone="graveyard", ref="malcolm-unrelated-spell")
        self.assert_closed(session, source)
        return session, source, discard, bystander

    def _combat(self, session, source):
        self.prepare(session)
        self.checkpoint(session)
        self.attack(session, [(source, "B")])
        self.until(session, lambda current: self.trigger_count(current, source) == 1)
        self.assertEqual("priority", session.state.pending_decision.kind)
        self.assertEqual("A", session.state.stack[-1].controller)

    def _choice(self, session, seat="A"):
        self.assertEqual("semantic.choice", session.state.pending_decision.kind)
        self.assertEqual((f"pilot:{seat}",), tuple(session.pending_principals()))
        return session.packet(f"pilot:{seat}", full=True)["decision"]

    def _accept(self, session, **fields):
        accepted = session.act("pilot:A", {"action_id": "choose", **fields})
        self.assertTrue(accepted.ok, accepted.summary)

    def test_discarded_card_cast_requires_exact_damage_event_and_ordered_body(self):
        record = self.db.by_oracle_id("3bba3b36-f8d7-4fd6-892a-797226210b6e")

        def witness():
            ir = compile_oracle_card(record, capability_registry=self.registry)
            self.assertEqual("exact", ir.status, ir.material_residuals)
            trigger, = (row for row in ir.faces[0].nodes if row.event == "damage.dealt.self")
            self.assertEqual(discarded_card_cast_effects(), tuple(trigger.effects))

        witness()
        for variant in (
            record.oracle_text.replace("combat damage", "damage"),
            record.oracle_text.replace("four or more", "three or more"),
            record.oracle_text.replace("Draw a card, then discard a card.", "Discard a card, then draw a card."),
            record.oracle_text.replace("chorus counters on Malcolm", "chorus counters on another creature"),
        ):
            ir = compile_oracle_card(replace(record, oracle_text=variant), capability_registry=self.registry)
            self.assertNotEqual("exact", ir.status)
        with patch("quorune.compiler.trigger_result_templates._source_combat_discard_cast_result", return_value=None):
            with self.assertRaises(AssertionError):
                witness()

    def test_actual_malcolm_counts_after_private_draw_and_casts_only_its_own_discard(self):
        session, source, discard, bystander = self._scene(82724001)
        old = discard.logical_object_id
        self._combat(session, source)
        count = len(session.state.players["A"].zones["hand"])
        self.resolve(session)
        choice = self._choice(session)
        self.assertEqual(DISCARDED_CARD_CAST, choice["ctx"]["operation"])
        self.assertEqual(4, session.state.cards[source.object_id].counters["chorus"])
        self.assertEqual(count + 1, len(session.state.players["A"].zones["hand"]))
        for seat in "BCD":
            packet = session.packet(f"pilot:{seat}", full=True)
            self.assertIsNone(packet["decision"])
            self.assertNotIn(discard.ref, json.dumps(packet))
        invalid = {"action_id": "choose", "cards": [bystander.ref]}
        before = authoritative_state_hash(session.state)
        self.assertFalse(session.act("pilot:A", invalid).ok)
        self.assertFalse(session.act("pilot:B", {**invalid, "cards": [discard.ref]}).ok)
        self.assertEqual(before, authoritative_state_hash(session.state))
        session = self.replay(session, load=True)

        self._accept(session, cards=[discard.ref])
        option, = self._choice(session)["ctx"]["cast_options"]
        self.assertEqual((discard.ref, "graveyard"), (option["card"], option["from"]))
        self.assertEqual(1, sum(event.object_incarnation == old
                                for event in session.engine._current_turn_history("card_discarded")))
        cast = {"cost_option": option["id"], "targets": ["C"]}
        request = {"action_id": "choose", "choice": "cast"}
        before = authoritative_state_hash(session.state)
        for wrong in ({"card": bystander.ref}, {"from": "exile"}, {"targets": ["absent"]}, {"x": 1}):
            self.assertFalse(session.act("pilot:A", {**request, "cast": {**cast, **wrong}}).ok, wrong)
        self.assertEqual(before, authoritative_state_hash(session.state))
        session = self.replay(session, load=True)
        life = session.state.players["C"].life
        self._accept(session, choice="cast", cast=cast)
        spell, = session.state.stack
        self.assertEqual(("A", discard.object_id), (spell.controller, spell.card_object_id))
        self.assertEqual("priority", session.state.pending_decision.kind)
        self.assertIsNone(self.resolve(session))
        self.assertEqual(life - 3, session.state.players["C"].life)
        self.replay(session, load=True)

    def test_actual_departed_malcolm_uses_last_known_chorus_and_preserves_trigger_controller(self):
        for scenario, starting, response_spell, mana_name, lands, eligible in (
            ("departed-at-three", 3, "Unsummon", "Island", 1, False),
            ("departed-at-four", 4, "Unsummon", "Island", 1, True),
            ("stolen-at-three", 3, "Turn Against", "Mountain", 5, True),
        ):
            with self.subTest(scenario=scenario):
                session, source, discard, _ = self._scene(82724100 + len(scenario), counters=starting)
                answer = self.add(session.engine, response_spell, seat="B", zone="hand")
                for index in range(lands):
                    self.add(session.engine, mana_name, seat="B", ref=f"malcolm-answer-mana-{index}")
                self._combat(session, source)
                self.cast(session, "B", answer, [source.ref])
                self.resolve_top(session)
                source_now = session.state.cards[source.object_id]
                if scenario.startswith("departed"):
                    self.assertEqual("hand", source_now.zone)
                    self.assertEqual({}, source_now.counters)
                else:
                    self.assertEqual(("battlefield", "B"), (source_now.zone, source_now.controller))
                session = self.replay(session, load=True)
                self.resolve(session)
                self._choice(session)
                self._accept(session, cards=[discard.ref])
                if eligible:
                    option, = self._choice(session)["ctx"]["cast_options"]
                    self.assertEqual(discard.ref, option["card"])
                    self._accept(session, choice="decline")
                self.assertFalse(session.state.stack)
                self.assertEqual("graveyard", session.state.cards[discard.object_id].zone)
                self.assertEqual("priority", session.state.pending_decision.kind)
                self.replay(session, load=True)

    def test_actual_discard_redirects_to_public_exile_but_hidden_library_grants_no_cast(self):
        for scenario, selected, destination, castable in (
            ("exile", "Lightning Bolt", "exile", True),
            ("library", "Blightsteel Colossus", "library", False),
            ("land", "Plains", "graveyard", False),
        ):
            with self.subTest(scenario=scenario):
                session, source, discard, _ = self._scene(82724140 + len(scenario), selected=selected)
                if scenario == "exile":
                    self.add(session.engine, "Rest in Peace", seat="D")
                self._combat(session, source)
                self.resolve(session)
                self._choice(session)
                self._accept(session, cards=[discard.ref])
                self.assertEqual(destination, session.state.cards[discard.object_id].zone)
                if castable:
                    option, = self._choice(session)["ctx"]["cast_options"]
                    self.assertEqual((discard.ref, destination), (option["card"], option["from"]))
                    self._accept(session, choice="cast", cast={"cost_option": option["id"], "targets": ["D"]})
                    self.assertEqual(discard.object_id, session.state.stack[-1].card_object_id)
                    self.assertIsNone(self.resolve(session))
                    self.assertEqual("exile", session.state.cards[discard.object_id].zone)
                else:
                    self.assertFalse(session.state.stack)
                    self.assertEqual("priority", session.state.pending_decision.kind)
                self.replay(session, load=True)

    def test_actual_discarded_spell_uses_mandatory_additional_cost_and_resumes_replacements(self):
        session, source, discard, _ = self._scene(82724200, selected="Village Rites")
        payment = self.add(session.engine, "Grizzly Bears", ref="malcolm-rite-payment")
        for index in range(2):
            self.add(session.engine, "Dauthi Voidwalker", seat="B", ref=f"malcolm-cost-replacement-{index}")
        self._combat(session, source)
        self.resolve(session)
        self._choice(session)
        self._accept(session, cards=[discard.ref])
        self.choose_replacements(session)
        option, = self._choice(session)["ctx"]["cast_options"]
        self.assertIn(payment.ref, option["choice_schema"]["sacrifice_cards"]["legal_refs"])
        request = {"action_id": "choose", "choice": "cast", "cast": {"cost_option": option["id"]}}
        before = authoritative_state_hash(session.state)
        self.assertFalse(session.act("pilot:A", request).ok)
        self.assertEqual(before, authoritative_state_hash(session.state))
        self._accept(session, choice="cast", cast={**request["cast"], "sacrifice_cards": [payment.ref]})
        self.assertEqual("replacement.order", session.state.pending_decision.kind)
        self.assertEqual("battlefield", session.state.cards[payment.object_id].zone)
        session = self.replay(session, load=True)
        self.choose_replacements(session)
        self.assertEqual("exile", session.state.cards[payment.object_id].zone)
        self.assertEqual(discard.object_id, session.state.stack[-1].card_object_id)
        self.assertEqual(1, sum(event.code == "cost.sacrifice" and event.details.get("object") == payment.ref
                                for event in session.state.events))
        terminal = self.resolve(session)
        if terminal is not None:
            self.assertEqual("replacement.order", terminal.kind)
            self.choose_replacements(session)
        self.assertFalse(session.state.stack)
        self.assertEqual("exile", session.state.cards[discard.object_id].zone)
        self.replay(session, load=True)

    def choose_replacements(self, session):
        while session.state.pending_decision.kind == "replacement.order":
            seat, = session.pending_principals()
            decision = session.packet(seat, full=True)["decision"]
            option = decision["ctx"]["options"][0]["id"]
            chosen = session.act(seat, {"action_id": "choose", "replacement": option})
            self.assertTrue(chosen.ok, chosen.summary)
