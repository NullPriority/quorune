from __future__ import annotations

from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import casualty_fixture as casualty
from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.discover_model import ExileCastProgress
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.graveyard_card_targets import graveyard_copy_cast_runtime_dependencies
from quorune.rules.graveyard_copy_cast_model import GRAVEYARD_COPY_CAPABILITY, GRAVEYARD_COPY_OFFER
from quorune.rules.library_selection import LibrarySelectionObjectIdentity
from scripts.build_test_database import build_fixture_database


class GraveyardCopyCastCompilerTests(unittest.TestCase):
    def test_graveyard_copy_cast_scope_and_serialized_copy_progress_are_exact(self):
        from test_targeted_graveyard_return_effect_clauses import card_record
        from quorune.oracle_ir import compile_oracle_card

        registry = load_default_capability_registry()
        text = ("Exile target instant or sorcery card from an opponent's graveyard. "
                "Copy that card. You may cast the copy without paying its mana cost.")

        def compile_body(body):
            return compile_oracle_card(card_record(body), capability_registry=registry, capability_profile="commander_review")

        ir = compile_body(text)
        self.assertEqual("exact", ir.status, ir.material_residuals)
        node, = ir.faces[0].nodes
        effect, = node.effects
        self.assertEqual("opponent", effect["scope"])
        self.assertEqual(1, effect["copies"])
        self.assertIn(GRAVEYARD_COPY_CAPABILITY, graveyard_copy_cast_runtime_dependencies(SimpleNamespace(
            effects=node.effects, target_schema=node.target_schema,
        )))
        with self.assertRaises(ValueError):
            graveyard_copy_cast_runtime_dependencies(SimpleNamespace(
                effects=node.effects, target_schema={**node.target_schema, "owner_relation": "any"},
            ))
        self.assertNotEqual("exact", compile_body(text.replace("Copy that card.", "Copy that card twice.")).status)
        with patch("quorune.compiler.graveyard_copy_cast_templates.graveyard_copy_cast_template", return_value=None):
            self.assertNotEqual("exact", compile_body(text).status)
        identity = LibrarySelectionObjectIdentity("copy", "copy@0", "O1")
        old = ExileCastProgress(None, (identity,), "O1", "A", "free")
        self.assertEqual(4, old.to_dict()["schema_version"])
        self.assertEqual(old, ExileCastProgress.from_dict(old.to_dict()))
        copy = ExileCastProgress(None, (identity,), "O1", "A", "free", "S1")
        self.assertEqual(5, copy.to_dict()["schema_version"])
        self.assertEqual(copy, ExileCastProgress.from_dict(copy.to_dict()))
        with self.assertRaises(ValueError):
            ExileCastProgress.from_dict({**copy.to_dict(), "schema_version": 4})


class GraveyardCopyCastRuntimeTests(InheritedNativeFixture, unittest.TestCase):
    casualty_action = casualty.CasualtyFixture.casualty_action
    cast_casualty = casualty.CasualtyFixture.cast_casualty
    choose_copy = casualty.CasualtyFixture.choose_copy

    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        path = Path(cls.temporary.name) / "graveyard-copy-cast.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures" / name for name in (
            "bound-effect-program-cards.json", "graveyard-copy-cast-cards.json", "discover-cards.json",
            "cascade-cards.json", "state-based-destination-cards.json", "casualty-cards.json",
            "fixed-reveal-additional-cost-cards.json",
        )], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Graveyard copy cast", [
            DeckEntry("Generic Bound Commander", 1, "commander"), DeckEntry("Generic Bound Plains", 30),
        ], ["Generic Bound Commander"])

    def _announce(self, session, source, targets, *, cost_option=None):
        offer = self.action(session, "A", source)
        command = {"action_id": offer["id"], "targets": targets, "pay": "auto"}
        if cost_option is not None:
            self.assertIn(cost_option, [row["id"] for row in offer["cost_options"]])
            command["cost_option"] = cost_option
        result = session.act("pilot:A", command)
        self.assertTrue(result.ok, result.summary)

    def _choice(self, session, *, optional):
        self.assertEqual("semantic.choice", self.resolve(session).kind)
        packet = session.packet("pilot:A", full=True)["decision"]
        self.assertEqual(GRAVEYARD_COPY_OFFER, packet["ctx"]["operation"])
        self.assertEqual(["cast", "decline"] if optional else ["cast"],
                         packet["legal_actions"][0]["choice_schema"]["legal_values"])
        for seat in "BCD":
            self.assertIsNone(session.packet(f"pilot:{seat}", full=True)["decision"])
        return packet["ctx"]["cast_options"]

    def _cast_copy(self, session, option, targets=()):
        accepted = session.act("pilot:A", {"action_id": "choose", "choice": "cast",
            "cast": {"cost_option": option["id"], "targets": list(targets)}})
        self.assertTrue(accepted.ok, accepted.summary)

    def test_original_mnemonic_deluge_casts_a_selected_subset_of_three_copies(self):
        session = self.session(33886701)
        source = self.add(session.engine, "Mnemonic Deluge", zone="hand")
        original = self.add(session.engine, "Lightning Bolt", seat="B", zone="graveyard")
        self.assert_closed(session, source)
        self.prepare(session, mana={"C": 6, "U": 3})
        self.checkpoint(session)
        self._announce(session, source, [original.ref])
        options = self._choice(session, optional=True)
        self.assertEqual(3, len({row["card"] for row in options}))
        self.assertEqual("exile", session.state.cards[original.object_id].zone)
        self.assertEqual(3, sum(card.zone == "exile" and card.is_card_copy for card in session.state.cards.values()))
        self.assertEqual("stack", session.state.cards[source.object_id].zone)
        session = self.replay(session, load=True)
        self._cast_copy(session, options[-1], ["C"])
        options = self._choice(session, optional=True)
        self.assertEqual(2, len({row["card"] for row in options}))
        self._cast_copy(session, options[0], ["D"])
        option, = self._choice(session, optional=True)
        self.assertTrue(option["card"])
        declined = session.act("pilot:A", {"action_id": "choose", "choice": "decline"})
        self.assertTrue(declined.ok, declined.summary)
        self.assertEqual("exile", session.state.cards[source.object_id].zone)
        self.assertEqual(2, len(session.state.stack))
        self.assertIsNone(self.resolve(session))
        self.assertEqual((37, 37), tuple(session.state.players[seat].life for seat in "CD"))
        self.assertFalse(any(card.zone == "exile" and card.is_card_copy for card in session.state.cards.values()))
        self.assertIn(original.object_id, session.state.players["B"].zones["exile"])
        self.replay(session, load=True)

    def test_original_spelltwine_requires_each_castable_copy_and_preserves_partial_targets(self):
        for removed in (False, True):
            with self.subTest(removed=removed):
                session = self.session(33886712 + removed)
                engine = session.engine
                source = self.add(engine, "Spelltwine", zone="hand")
                own = self.add(engine, "Lightning Bolt", zone="graveyard")
                other = self.add(engine, "Divination", seat="B", zone="graveyard")
                exile = self.add(engine, "Cremate", seat="B", zone="hand") if removed else None
                self.assert_closed(session, source)
                self.prepare(session, mana={"C": 5, "U": 1})
                session.state.players["B"].mana_pool["B"] = 1
                self.checkpoint(session)
                self._announce(session, source, {"clause_0": [own.ref], "clause_1": [other.ref]})
                if removed:
                    self.cast(session, "B", exile, [other.ref])
                options = self._choice(session, optional=False)
                self.assertEqual(1 if removed else 2, len(options))
                before = authoritative_state_hash(session.state)
                self.assertFalse(session.act("pilot:A", {"action_id": "choose", "choice": "decline"}).ok)
                self.assertEqual(before, authoritative_state_hash(session.state))
                if not removed:
                    by_ref = {card.ref: card for card in session.state.cards.values()}
                    first = next(row for row in options if by_ref[row["card"]].printed_name == "Divination")
                    self._cast_copy(session, first)
                    self.assertEqual(first["card"], session.state.cards[session.state.stack[-1].card_object_id].ref)
                    options = self._choice(session, optional=False)
                self._cast_copy(session, options[0], ["C"])
                self.assertEqual("exile", session.state.cards[source.object_id].zone)
                self.assertEqual(1 if removed else 2, len(session.state.stack))
                self.assertIsNone(self.resolve(session))
                self.assertEqual(37, session.state.players["C"].life)
                for card in (own, other):
                    self.assertIn(card.object_id, session.state.players[card.owner].zones["exile"])
                self.replay(session, load=True)

    def test_original_flawless_forgery_casualty_keeps_the_two_exile_and_copy_results_separate(self):
        session = self.session(33886722)
        engine = session.engine
        source = self.add(engine, "Flawless Forgery", zone="hand")
        sacrifice = self.add(engine, "Hill Giant")
        first = self.add(engine, "Lightning Bolt", seat="B", zone="graveyard", ref="forgery-B")
        second = self.add(engine, "Lightning Bolt", seat="C", zone="graveyard", ref="forgery-C")
        self.assert_closed(session, source)
        self.prepare(session, mana={"C": 4, "U": 4})
        self.checkpoint(session)
        self.cast_casualty(session, source, sacrifice, [first.ref])
        self.assertEqual("semantic.storm", self.resolve(session).kind)
        self.choose_copy(session, [second.ref])
        option, = self._choice(session, optional=True)
        self.assertEqual("exile", session.state.cards[second.object_id].zone)
        self.assertEqual("graveyard", session.state.cards[first.object_id].zone)
        self._cast_copy(session, option, ["D"])
        option, = self._choice(session, optional=True)
        self.assertEqual("exile", session.state.cards[first.object_id].zone)
        self._cast_copy(session, option, ["D"])
        self.assertIsNone(self.resolve(session))
        self.assertEqual(34, session.state.players["D"].life)
        self.assertEqual("graveyard", session.state.cards[source.object_id].zone)
        self.replay(session, load=True)

    def test_original_spelltwine_can_finish_without_a_hidden_quality_payment(self):
        import json

        session = self.session(33886731)
        engine = session.engine
        source = self.add(engine, "Spelltwine", zone="hand")
        own = self.add(engine, "Lightning Bolt", zone="graveyard")
        other = self.add(engine, "Thunderherd Migration", seat="B", zone="graveyard")
        hidden = self.add(engine, "Colossal Dreadmaw", zone="hand", ref="copy-private-dinosaur")
        initial_viewers = tuple(hidden.revealed_to)
        self.assert_closed(session, source)
        self.assert_closed(session, other)
        self.prepare(session, mana={"C": 5, "U": 1})
        self.checkpoint(session)
        self._announce(session, source, {"clause_0": [own.ref], "clause_1": [other.ref]})
        options = self._choice(session, optional=False)
        self.assertEqual(2, len(options))
        required, = [option for option in options if option["required_cast"]]
        self._cast_copy(session, required, ["C"])
        remaining, = self._choice(session, optional=True)
        self.assertFalse(remaining["required_cast"])
        self.assertIn(hidden.ref, remaining["choice_schema"]["reveal_cards"]["legal_refs"])
        for seat in "BCD":
            self.assertNotIn(hidden.ref, json.dumps(session.packet(f"pilot:{seat}", full=True)))
        session = self.replay(session, load=True)
        result = session.act("pilot:A", {"action_id": "choose", "choice": "decline"})
        self.assertTrue(result.ok, result.summary)
        self.assertIsNone(self.resolve(session))
        self.assertEqual("hand", session.state.cards[hidden.object_id].zone)
        self.assertEqual(initial_viewers, tuple(session.state.cards[hidden.object_id].revealed_to))
        self.assertIn(other.object_id, session.state.players["B"].zones["exile"])
        self.assertEqual(37, session.state.players["C"].life)
        self.replay(session, load=True)

    def test_original_mizzix_mastery_overload_keeps_terminal_exile_and_uses_no_targets(self):
        for overloaded in (False, True):
            with self.subTest(overloaded=overloaded):
                session = self.session(33886740 + overloaded)
                source = self.add(session.engine, "Mizzix's Mastery", zone="hand")
                first = self.add(session.engine, "Lightning Bolt", zone="graveyard")
                second = self.add(session.engine, "Divination", zone="graveyard")
                control = self.add(session.engine, "Generic Bound Body", zone="graveyard")
                other = self.add(session.engine, "Lightning Bolt", seat="B", zone="graveyard", ref="mastery-opponent")
                self.assert_closed(session, source)
                self.prepare(session, mana={"C": 5, "R": 3})
                self.checkpoint(session)
                self._announce(session, source, [] if overloaded else [first.ref],
                               cost_option="overload" if overloaded else "normal")
                self.assertEqual([] if overloaded else [first.ref], session.state.stack[-1].targets)
                options = self._choice(session, optional=True)
                self.assertEqual(2 if overloaded else 1, len(options))
                result = session.act("pilot:A", {"action_id": "choose", "choice": "decline"})
                self.assertTrue(result.ok, result.summary)
                self.assertEqual("exile", session.state.cards[source.object_id].zone)
                self.assertEqual("exile", session.state.cards[first.object_id].zone)
                self.assertEqual("exile" if overloaded else "graveyard", session.state.cards[second.object_id].zone)
                self.assertEqual("graveyard", session.state.cards[control.object_id].zone)
                self.assertEqual("graveyard", session.state.cards[other.object_id].zone)
                self.replay(session, load=True)
