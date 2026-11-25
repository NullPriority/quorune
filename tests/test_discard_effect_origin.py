"""Retain actual resolving discard provenance across native movement and replay."""

import unittest
from unittest.mock import patch

import test_whole_hand_discard as whole_hand_witnesses

from quorune.discard_effect_origin import (
    BOUND_DISCARD_EFFECT_ORIGIN, DISCARD_EFFECT_ORIGIN_VERSION_FIELD, DiscardEffectOrigin,
    OPPONENT_SOURCE_DISCARD_EVENT,
    bind_discard_effect_origin,
)
from quorune.record_state_provenance import format_state_versions, validate_state_versions
from quorune.zone_trigger_events import ZoneTransitionKind


class DiscardEffectOriginTests(unittest.TestCase):
    setUpClass = classmethod(whole_hand_witnesses.WholeHandDiscardRuntimeTests.setUpClass.__func__)
    tearDownClass = classmethod(whole_hand_witnesses.WholeHandDiscardRuntimeTests.tearDownClass.__func__)
    session = whole_hand_witnesses.WholeHandDiscardRuntimeTests.session
    add = whole_hand_witnesses.WholeHandDiscardRuntimeTests.add
    ready = whole_hand_witnesses.WholeHandDiscardRuntimeTests.ready
    checkpoint = whole_hand_witnesses.WholeHandDiscardRuntimeTests.checkpoint
    resolve = whole_hand_witnesses.WholeHandDiscardRuntimeTests.resolve
    replay = whole_hand_witnesses.WholeHandDiscardRuntimeTests.replay

    def collect_discards(self, engine, events, observed_event='card.discarded'):
        dispatch = engine._dispatch_semantic_event

        def observe(kind, context, **kwargs):
            if kind == observed_event:
                events.append(dict(context))
            return dispatch(kind, context, **kwargs)

        return patch.object(engine, "_dispatch_semantic_event", side_effect=observe)

    def play_whole_hand_spell(self, session):
        spell = self.add(session.engine, "Wit's End", zone="hand", ref="SOLE-DISCARD-SPELL")
        action = self.ready(session, spell, {"B": 2, "C": 5})
        self.checkpoint(session)
        result = session.act("pilot:A", {"action_id": action["id"], "targets": ["B"], "pay": "auto"})
        self.assertTrue(result.ok, result.summary)
        item, = session.state.stack
        return item

    def test_native_discard_uses_resolving_stack_source_and_legacy_omits_it(self):
        for version in (1, None):
            with self.subTest(version=version):
                game = self.session(70109001 + int(version or 0))
                game.state.discard_effect_origin_version = version
                item = self.play_whole_hand_spell(game)
                before = len(game.state.players["B"].zones["hand"])
                self.assertGreater(before, 0)
                events = []
                with self.collect_discards(game.engine, events):
                    self.assertIsNone(self.resolve(game))
                self.assertEqual(before, len(events))
                origin = DiscardEffectOrigin.from_resolving_stack(item).to_dict() if version else None
                self.assertTrue(all(row.get("discard_effect_origin") == origin for row in events))
                self.assertEqual([], game.state.players["B"].zones["hand"])
                fields = format_state_versions(game.state)
                self.assertEqual(version, fields.get(DISCARD_EFFECT_ORIGIN_VERSION_FIELD))
                validate_state_versions({"format": fields}, game.state)
                if version is None:
                    self.assertNotIn(DISCARD_EFFECT_ORIGIN_VERSION_FIELD, game.state.to_dict())
                game = self.replay(game, load=True)
                self.assertEqual(version, game.state.discard_effect_origin_version)

    def test_native_opponent_effect_emits_only_the_committed_public_source_event(self):
        game = self.session(70109041)
        trigger_source = self.add(game.engine, "Generic Bound Growth", seat="B", zone="hand", ref="AFFECTED-SOURCE")
        item = self.play_whole_hand_spell(game)
        events = []
        with self.collect_discards(game.engine, events, OPPONENT_SOURCE_DISCARD_EVENT):
            self.assertIsNone(self.resolve(game))
        current = game.state.cards[trigger_source.object_id]
        found, = [row for row in events if row["card"] == current.ref]
        self.assertEqual(current.logical_object_id, found["discarded_logical_object_id"])
        self.assertEqual("B", found["previous_controller"])
        self.assertEqual("A", found["causing_player"])
        self.assertEqual(item.ref, found["discard_effect_origin"]["stack_ref"])
        self.replay(game, load=True)

    def test_saved_replacement_order_keeps_origin_for_the_redirected_discard(self):
        game = self.session(70109019)
        engine = game.engine
        self.add(engine, "Dauthi Voidwalker")
        self.add(engine, "Dauthi Voidwalker", ref="SECOND-VOIDWALKER")
        for seat in "ABCD":
            for key in tuple(engine.state.players[seat].zones["hand"]):
                engine.move_card(key, "library", log=False)
        private = self.add(engine, "Generic Bound Growth", seat="B", zone="hand", ref="PENDING-PRIVATE")
        spell = self.add(engine, "Wheel of Fortune", zone="hand")
        action = self.ready(game, spell, {"R": 1, "C": 2})
        self.checkpoint(game)
        result = game.act("pilot:A", {"action_id": action["id"], "pay": "auto"})
        self.assertTrue(result.ok, result.summary)
        item, = game.state.stack
        pending = self.resolve(game)
        self.assertEqual("replacement.order", pending.kind)
        expected = DiscardEffectOrigin.from_resolving_stack(item).to_dict()
        self.assertEqual(expected, pending.continuation["effect"][BOUND_DISCARD_EFFECT_ORIGIN])
        self.assertNotIn(private.ref, str(game.packet("pilot:C", full=True)))
        self.assertEqual("hand", game.state.cards[private.object_id].zone)
        game = self.replay(game, load=True)
        decision = game.state.pending_decision
        self.assertEqual(expected, decision.continuation["effect"][BOUND_DISCARD_EFFECT_ORIGIN])
        bad = {**decision.continuation["effect"], BOUND_DISCARD_EFFECT_ORIGIN: {**expected, "controller": "C"}}
        with self.assertRaisesRegex(ValueError, "sealed origin changed"):
            bind_discard_effect_origin(game.state, item, bad)
        events = []
        with self.collect_discards(game.engine, events):
            for _ in range(16):
                decision = game.state.pending_decision
                if decision is None or decision.kind != "replacement.order":
                    break
                pilot, = game.pending_principals()
                option = game.packet(pilot, full=True)["decision"]["ctx"]["options"][0]["id"]
                result = game.act(pilot, {"action_id": "choose", "replacement": option})
                self.assertTrue(result.ok, result.summary)
            else:
                self.fail("Replacement journal did not finish")
            self.assertIsNone(self.resolve(game))
        self.assertEqual("exile", game.state.cards[private.object_id].zone)
        found, = [row for row in events if row["card"] == private.ref]
        self.assertEqual(expected, found["discard_effect_origin"])
        self.replay(game, load=True)

    def test_plain_hand_transaction_does_not_inherit_the_unrelated_stack_source(self):
        game = self.session(70109031)
        item = self.play_whole_hand_spell(game)
        self.assertEqual("A", item.controller)
        card_id = game.state.players["B"].zones["hand"][0]
        events = []
        with self.collect_discards(game.engine, events):
            game.engine._move_cards_simultaneously(
                [(card_id, "graveyard")], reason="Independent discard transaction",
                transition_kinds={card_id: ZoneTransitionKind.DISCARD},
            )
        event, = events
        self.assertNotIn("discard_effect_origin", event)
        self.assertEqual("graveyard", game.state.cards[card_id].zone)
