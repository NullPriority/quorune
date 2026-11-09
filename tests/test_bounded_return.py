"""Bounded public return choices with pinned entry-trigger originals."""

from copy import deepcopy
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from test_granted_skulk import record
from quorune.carddb import CardDatabase
from quorune.compiler.controlled_return_templates import controlled_return_template
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import CapabilityRegistry, load_default_capability_registry
from quorune.rules.controlled_return import (
    CONTROLLED_RETURN_CAPABILITY,
    CONTROLLED_RETURN_FIELD,
    ControlledReturn,
    controlled_return_runtime_dependencies,
)
from scripts.build_test_database import build_fixture_database

TWO_LANDS = "When this creature enters, return up to two lands you control to their owner's hand."


class BoundedReturnCompilerTests(unittest.TestCase):
    def test_bounded_return_closes_the_count_controller_and_source_exclusion(self):
        registry = load_default_capability_registry()

        def witness():
            ir = compile_oracle_card(record(TWO_LANDS, "Creature"), capability_registry=registry)
            self.assertEqual("exact", ir.status, ir.material_residuals)
            node, = ir.faces[0].nodes
            self.assertIn(CONTROLLED_RETURN_CAPABILITY, node.capability_dependencies)
            self.assertIsNone(node.target_schema)
            effect, = node.effects
            scope = ControlledReturn.from_effect(effect, actor="$controller")
            self.assertEqual((0, 2), (scope.minimum, scope.maximum))
            self.assertEqual(("land",), scope.predicate.types_all)
            return effect

        effect = witness()
        self.assertEqual(
            (CONTROLLED_RETURN_CAPABILITY,),
            controlled_return_runtime_dependencies(SimpleNamespace(effects=(effect,))),
        )
        altered = deepcopy(effect)
        altered[CONTROLLED_RETURN_FIELD]["predicate"]["controller"] = "$opponent"
        with self.assertRaises(ValueError):
            controlled_return_runtime_dependencies(SimpleNamespace(effects=(altered,)))
        original = ControlledReturn.effect

        def shrink(self):
            result = original(self)
            result[CONTROLLED_RETURN_FIELD]["maximum"] = 1
            return result

        with patch.object(ControlledReturn, "effect", shrink), self.assertRaises(AssertionError):
            witness()
        template = controlled_return_template(
            "You may return another permanent you control to its owner's hand.", source_is_permanent=True,
        )
        self.assertEqual("$source.zone_object", template[1][0][CONTROLLED_RETURN_FIELD]["predicate"]["exclude_ref"])
        self.assertIsNone(controlled_return_template(
            "You may return another permanent you control to its owner's hand.", source_is_permanent=False,
        ))
        self.assertIsNone(controlled_return_template(
            "Return up to two target lands you control to their owner's hand.", source_is_permanent=True,
        ))

    def test_bounded_return_requires_its_declared_capability(self):
        registry = load_default_capability_registry()
        raw = json.loads((ROOT / "quorune/rules/capability-registry.json").read_text(encoding="utf-8"))
        owner = next(row for row in raw["capabilities"] if row["id"] == CONTROLLED_RETURN_CAPABILITY)
        owner.update(status="blocked", blockers=["Withdraw bounded return authority"])
        altered = CapabilityRegistry(raw)
        altered.mark_evidence_verified(registry.evidence_fingerprint)
        result = compile_oracle_card(record(TWO_LANDS, "Creature"), capability_registry=altered)
        self.assertNotEqual("exact", result.status)


class BoundedReturnNativeTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "bounded-return.sqlite3"
        build_fixture_database([
            ROOT / "tests/fixtures/bound-effect-program-cards.json",
            ROOT / "tests/fixtures/bounded-return-cards.json",
        ], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition(
            "Bounded return",
            [DeckEntry("Generic Bound Commander", 1, "commander"), DeckEntry("Generic Bound Plains", 30)],
            ["Generic Bound Commander"],
        )

    def test_actual_kazandu_stomper_can_decline_or_return_two_to_their_owners(self):
        session = self.session(608212301)
        stomper = self.add(session.engine, "Kazandu Stomper", zone="hand")
        first = self.add(session.engine, "Generic Bound Plains", ref="first-controlled-land")
        second = self.add(session.engine, "Generic Bound Plains", seat="B", ref="borrowed-land")
        third = self.add(session.engine, "Generic Bound Plains", ref="third-controlled-land")
        foreign = self.add(session.engine, "Generic Bound Plains", seat="B", ref="foreign-land")
        hidden = self.add(session.engine, "Generic Bound Growth", seat="D", zone="hand", ref="private-return-fixture")
        session.engine.change_control(second.object_id, "A", reason="bounded return fixture")
        self.assert_closed(session, stomper)
        self.prepare(session, mana={"C": 5, "G": 1})
        self.checkpoint(session)
        self.cast(session, "A", stomper)
        self.resolve_top(session)
        decision = self.resolve(session)
        self.assertEqual("semantic.choice", decision.kind)
        self.assertEqual(["A"], list(decision.actors))
        for seat in "BCD":
            self.assertIsNone(session.packet(f"pilot:{seat}", full=True)["decision"])
        self.assertNotIn(hidden.ref, json.dumps(session.packet("pilot:A", full=True)))
        before = authoritative_state_hash(session.state)
        for seat, refs in (("A", [first.ref, second.ref, third.ref]), ("A", [foreign.ref]), ("B", [first.ref])):
            result = session.act(f"pilot:{seat}", {"action_id": "choose", "objects": refs})
            self.assertFalse(result.ok, result.summary)
            self.assertEqual(before, authoritative_state_hash(session.state))

        declined = self.replay(session, load=True)
        result = declined.act("pilot:A", {"action_id": "choose", "objects": []})
        self.assertTrue(result.ok, result.summary)
        self.assertTrue(all(declined.state.cards[card.object_id].zone == "battlefield" for card in (first, second, third)))
        self.replay(declined, load=True)

        result = session.act("pilot:A", {"action_id": "choose", "objects": [first.ref, second.ref]})
        self.assertTrue(result.ok, result.summary)
        self.assertIn(first.object_id, session.state.players["A"].zones["hand"])
        self.assertIn(second.object_id, session.state.players["B"].zones["hand"])
        self.assertEqual("battlefield", session.state.cards[third.object_id].zone)
        self.assertEqual("battlefield", session.state.cards[foreign.object_id].zone)
        self.replay(session, load=True)

    def test_actual_aviary_mechanic_has_no_offer_when_only_the_source_qualifies(self):
        session = self.session(608212302)
        source = self.add(session.engine, "Aviary Mechanic", zone="hand")
        foreign = self.add(session.engine, "Generic Bound Plains", seat="B")
        self.assert_closed(session, source)
        self.prepare(session, mana={"C": 1, "W": 1})
        self.checkpoint(session)
        self.cast(session, "A", source)
        self.resolve_top(session)
        self.assertIsNone(self.resolve(session))
        self.assertEqual("battlefield", session.state.cards[source.object_id].zone)
        self.assertEqual("battlefield", session.state.cards[foreign.object_id].zone)
        before = authoritative_state_hash(session.state)
        result = session.act("pilot:A", {"action_id": "choose", "objects": [source.ref]})
        self.assertFalse(result.ok, result.summary)
        self.assertEqual(before, authoritative_state_hash(session.state))
        self.replay(session, load=True)

    def test_actual_time_wipe_moves_the_choice_before_destroying_remaining_creatures(self):
        for has_choice in (False, True):
            with self.subTest(has_choice=has_choice):
                session = self.session(608212303 + int(has_choice))
                spell = self.add(session.engine, "Time Wipe", zone="hand")
                own = self.add(session.engine, "Generic Bound Body", ref="returned-before-destruction") if has_choice else None
                foreign = self.add(session.engine, "Generic Bound Body", seat="B", ref="destroyed-after-choice")
                self.assert_closed(session, spell)
                self.prepare(session, mana={"C": 2, "W": 2, "U": 1})
                self.checkpoint(session)
                self.cast(session, "A", spell)
                decision = self.resolve(session)
                if has_choice:
                    self.assertEqual("semantic.choice", decision.kind)
                    self.assertEqual("battlefield", session.state.cards[foreign.object_id].zone)
                    before = authoritative_state_hash(session.state)
                    for invalid in ([], [foreign.ref]):
                        rejected = session.act("pilot:A", {"action_id": "choose", "objects": invalid})
                        self.assertFalse(rejected.ok, rejected.summary)
                        self.assertEqual(before, authoritative_state_hash(session.state))
                    result = session.act("pilot:A", {"action_id": "choose", "objects": [own.ref]})
                    self.assertTrue(result.ok, result.summary)
                    self.assertIsNone(self.resolve(session))
                    self.assertEqual("hand", session.state.cards[own.object_id].zone)
                else:
                    self.assertIsNone(decision)
                self.assertEqual("graveyard", session.state.cards[foreign.object_id].zone)
                self.replay(session, load=True)
