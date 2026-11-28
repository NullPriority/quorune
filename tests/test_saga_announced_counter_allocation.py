"""Original public target shares survive replacement, target loss and replay."""

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.announced_counter_allocation import (
    ANNOUNCED_COUNTER_CAPABILITY,
    ANNOUNCED_COUNTER_CONTEXT,
    BOUND_COUNTER_SHARES,
    COUNTER_ALLOCATION_FIELD,
    announce_counter_allocation,
    announced_counter_effect,
    announced_counter_target_schema,
    bind_announced_counter_allocation,
)
from quorune.carddb import CardDatabase
from quorune.choice_forms import delegated_choice_fields
from quorune.deck import DeckDefinition, DeckEntry
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database


class SagaAnnouncedCounterAllocationTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        path = Path(cls.temporary.name) / "saga-counter-allocation.sqlite3"
        fixtures = ROOT / "tests" / "fixtures"
        build_fixture_database([
            fixtures / "bound-effect-program-cards.json",
            fixtures / "reflexive-payment-cards.json",
            fixtures / "saga-announced-counter-allocation-cards.json",
        ], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Saga announced shares", [
            DeckEntry("Generic Bound Commander", 1, "commander"),
            DeckEntry("Generic Bound Plains", 30),
        ], ["Generic Bound Commander"])

    def test_original_allocations_retain_slot_authority_after_a_target_is_lost(self):
        schema = announced_counter_target_schema()
        effect = announced_counter_effect()
        for targets, shares in (
            (("first",), (3,)),
            (("first", "second"), (1, 2)),
            (("first", "second"), (2, 1)),
            (("first", "second", "third"), (1, 1, 1)),
        ):
            with self.subTest(targets=targets, shares=shares):
                response = {COUNTER_ALLOCATION_FIELD: dict(zip(targets, shares, strict=True))}
                announcement = announce_counter_allocation(schema, targets, response, trigger_creation=True)
                current = list(targets)
                if len(current) > 1:
                    current[1] = None
                item = SimpleNamespace(targets=current, context={**announcement, "targets_revalidated": True})
                resolved = bind_announced_counter_allocation(effect, item)
                expected = {ref: share for idx, (ref, share) in enumerate(zip(targets, shares, strict=True)) if idx != 1}
                self.assertEqual(expected, resolved[BOUND_COUNTER_SHARES])
                with self.assertRaises(ValueError):
                    announce_counter_allocation(schema, targets, response, trigger_creation=False)
                corrupted = dict(announcement[ANNOUNCED_COUNTER_CONTEXT])
                corrupted["shares"] = [True, *shares[1:]]
                item.context[ANNOUNCED_COUNTER_CONTEXT] = corrupted
                with self.assertRaises(ValueError):
                    bind_announced_counter_allocation(effect, item)

    def test_swapping_original_shares_is_a_killed_mutation(self):
        from unittest.mock import patch

        item = SimpleNamespace(targets=["first", None], context={
            ANNOUNCED_COUNTER_CONTEXT: {"schema_version": 1, "shares": [1, 2]},
            "targets_revalidated": True,
        })

        def assert_small_original_share():
            result = bind_announced_counter_allocation(announced_counter_effect(), item)
            self.assertEqual({"first": 1}, result[BOUND_COUNTER_SHARES])

        assert_small_original_share()
        with patch("quorune.announced_counter_allocation.read_announced_counter_shares", return_value=(2, 1)):
            with self.assertRaises(AssertionError):
                assert_small_original_share()

    def test_replacement_intent_identity_round_trips_exact_counter_shares(self):
        from quorune.semantic_choices.intent_replacement import semantic_intent_identity, validate_semantic_intent_identity
        from quorune.semantic_runtime.intents import PlaceCountersOnTargetsIntent

        intent = PlaceCountersOnTargetsIntent(
            actor="A", object_refs=("first", "second"), maximum_targets=3,
            counter_name="+1/+1", amount=1, reason="Bound counter allocation", source_ref="source",
            target_shares={"first": 1, "second": 2},
        )
        kind, value = semantic_intent_identity(intent)
        self.assertEqual(value, validate_semantic_intent_identity(kind, value))
        with self.assertRaises(ValueError):
            validate_semantic_intent_identity(kind, {**value, BOUND_COUNTER_SHARES: {"first": 3}})

    def test_original_trigger_publishes_either_split_and_replays_the_surviving_share(self):
        session = self.session(603601311)
        source = self.add(session.engine, "Revival of the Ancestors", zone="hand")
        first = self.add(session.engine, "Generic Bound Body", ref="kept-saga-allocation-target")
        second = self.add(session.engine, "Generic Bound Body", ref="lost-saga-allocation-target")
        foreign = self.add(session.engine, "Generic Bound Body", seat="C", ref="foreign-counter-target")
        bounce = self.add(session.engine, "Unsummon", seat="B", zone="hand")
        increment = self.add(session.engine, "Generic Reflexive Lore", zone="hand", ref="revival-second-lore")
        final = self.add(session.engine, "Generic Reflexive Lore", zone="hand", ref="revival-final-lore")
        session.state.players["B"].mana_pool["U"] = 1
        self.assert_closed(session, source)
        programs = session.engine.semantics.programs_for_oracle(source.oracle_id)
        chapter = next(program for program in programs if program.event == "saga.chapter.2")
        self.assertIn(ANNOUNCED_COUNTER_CAPABILITY, chapter.capability_dependencies)
        self.prepare(session, mana={"C": 1, "W": 1, "B": 1, "G": 1})
        self.checkpoint(session)

        self.cast(session, "A", source)
        self.assertIsNone(self.resolve(session))
        tokens = [card for card in session.state.cards.values() if card.is_token and card.zone == "battlefield"]
        self.assertEqual(3, len(tokens))
        self.assertTrue(all(token.controller == "A" for token in tokens))

        self.cast(session, "A", increment, [source.ref])
        decision = self.resolve(session)
        self.assertIsNotNone(decision)
        self.assertEqual(("semantic.target", ["A"]), (decision.kind, decision.actors))
        packet = session.packet("pilot:A", full=True)
        context = packet["decision"]["ctx"]
        offered = next(row for row in packet["decision"]["legal_actions"] if row["id"] == "choose")
        field = next(row for row in offered["form"]["fields"] if row["control"] == "targets")
        self.assertEqual({"schema_version": 1, "total": 3}, field["schema"][COUNTER_ALLOCATION_FIELD])
        group, = field["schema"]["groups"]
        self.assertEqual((1, 3), (group["min"], group["max"]))
        self.assertIn(COUNTER_ALLOCATION_FIELD, delegated_choice_fields(
            offered, decision_kind="semantic.target", context=context,
        ))
        before = authoritative_state_hash(session.state)
        for principal, targets, shares in (
            ("pilot:B", [first.ref, second.ref], {first.ref: 2, second.ref: 1}),
            ("pilot:A", [first.ref, foreign.ref], {first.ref: 2, foreign.ref: 1}),
            ("pilot:A", [first.ref, first.ref], {first.ref: 3}),
            ("pilot:A", [first.ref, second.ref], {first.ref: 3, second.ref: 0}),
            ("pilot:A", [first.ref, second.ref], {first.ref: 1, second.ref: 1}),
            ("pilot:A", [first.ref, second.ref], {first.ref: 2}),
        ):
            result = session.act(principal, {"action_id": "choose", "targets": targets, COUNTER_ALLOCATION_FIELD: shares})
            self.assertFalse(result.ok, result.summary)
            self.assertEqual(before, authoritative_state_hash(session.state))

        for kept in (1, 2):
            with self.subTest(kept=kept):
                resumed = self.replay(session, load=True)
                accepted = resumed.act("pilot:A", {
                    "action_id": "choose", "targets": [first.ref, second.ref],
                    "choices": {COUNTER_ALLOCATION_FIELD: {first.ref: kept, second.ref: 3 - kept}},
                })
                self.assertTrue(accepted.ok, accepted.summary)
                for viewer in "ABCD":
                    public = resumed.packet(f"pilot:{viewer}", full=True)["state"]["stack"][0]
                    self.assertEqual(([first.ref, second.ref], [kept, 3 - kept]),
                                     (public["targets"], public["counter_shares"]))
                self.cast(resumed, "B", bounce, [second.ref])
                self.resolve_top(resumed)
                resumed = self.replay(resumed, load=True)
                self.assertIsNone(self.resolve(resumed))
                self.assertEqual(kept, resumed.state.cards[first.object_id].counters["+1/+1"])
                self.assertEqual("hand", resumed.state.cards[second.object_id].zone)
                self.assertEqual(0, resumed.state.cards[second.object_id].counters.get("+1/+1", 0))
                self.cast(resumed, "A", final, [source.ref])
                self.assertIsNone(self.resolve(resumed))
                for ref in (first.object_id, tokens[0].object_id):
                    self.assertLessEqual({"trample", "lifelink"},
                        resumed.engine._combat_keywords(resumed.state.cards[ref]))
                self.assertEqual("graveyard", resumed.state.cards[source.object_id].zone)
                self.replay(resumed)
