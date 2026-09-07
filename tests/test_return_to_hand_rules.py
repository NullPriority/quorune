from __future__ import annotations

import copy
from dataclasses import FrozenInstanceError
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from common import ROOT, keep_all, make_session
from quorune import return_to_hand as return_module
from quorune.carddb import CardDatabase
from quorune.deck import DeckLoader
from quorune.projection import StateProjector
from quorune.record import (
    authoritative_state_hash,
    checkpoint_envelope,
    replay_record,
)
from quorune.replacement.immutable import thaw_value
from quorune.return_to_hand import (
    commit_return_to_owner_hand,
    prepare_return_to_owner_hand,
    request_for_card,
    ReturnToHandError,
    return_permanent_to_owner_hand,
)
from quorune.semantic_runtime import (
    ReadOnlyHandlerContext,
    ReadOnlyRulesQuery,
    ReturnPermanentToOwnerHandIntent,
)
from quorune.semantic_runtime.context import SemanticNodeError
from quorune.semantic_runtime.return_to_hand_handlers import (
    ReturnPermanentToOwnerHandHandler,
)
from quorune.semantics import SemanticProgram
from scripts.build_test_database import build_fixture_database


def focused_card_database(directory: str) -> CardDatabase:
    database = Path(directory) / "return-to-owner-hand.sqlite3"
    build_fixture_database(
        [
            ROOT / "tests" / "fixtures" / "scryfall-exact-lists.json",
            ROOT / "tests" / "fixtures" / "targeted-return-to-hand-cards.json",
        ],
        database,
    )
    return CardDatabase(database)


class ReturnToOwnerHandRuleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.db = focused_card_database(cls.temporary.name)
        loader = DeckLoader(cls.db)
        cls.mishra = loader.load(
            ROOT / "examples" / "mishra-eminent-one.txt",
            commander="Mishra, Eminent One",
            deck_name="Mishra",
        )
        cls.zimone = loader.load(
            ROOT / "examples" / "zimone-and-dina.txt",
            commander="Zimone and Dina",
            deck_name="Zimone",
        )

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temporary.cleanup()

    def session(
        self,
        seed: int,
        *,
        players: int = 4,
        unsummon: bool = False,
        expanded_return: bool = False,
    ):
        mishra = copy.deepcopy(self.mishra)
        if unsummon or expanded_return:
            next(
                entry for entry in mishra.entries if entry.board == "mainboard"
            ).name = (
                "Expanded Battlefield Return Fixture"
                if expanded_return
                else "Unsummon"
            )
        session = make_session(
            self.db,
            mishra,
            self.zimone,
            players=players,
            seed=seed,
            auto_pass_empty=False,
        )
        keep_all(session)
        engine = session.engine
        engine.permissions.invalidate_current()
        engine.state.pending_decision = None
        engine.state.priority_player = None
        engine.state.priority_passes = []
        session.commands.clear()
        session.decisions.clear()
        return session

    @staticmethod
    def permanent(engine, seat: str, *, card_type: str = "creature"):
        return next(
            card
            for card in engine.state.cards.values()
            if card.owner == seat
            and card.zone != "command"
            and (record := engine.card_record(card)) is not None
            and card_type
            in record.type_line.casefold().split(" — ", 1)[0].split()
        )

    @staticmethod
    def put_on_battlefield(engine, card, *, controller: str | None = None):
        return engine.move_card(
            card.object_id,
            "battlefield",
            controller=controller or card.owner,
            tapped=False,
            log=False,
        )

    @staticmethod
    def pass_stack(session):
        while session.state.stack:
            principals = session.pending_principals()
            if not principals:
                raise AssertionError("Stack resolution stopped without priority")
            result = session.act(principals[0], {"action_id": "pass"})
            if not result.ok:
                raise AssertionError(result.summary)

    def assert_replays(self, session, label: str):
        expected_hash = authoritative_state_hash(session.state)
        with tempfile.TemporaryDirectory() as temporary:
            record_dir = Path(temporary) / label
            session.save(record_dir)
            replay = replay_record(record_dir, self.db, verify=True)
        self.assertTrue(replay["ok"], replay)
        self.assertEqual(expected_hash, replay["final_state_hash"])

    def test_model_is_immutable_canonical_and_caller_copy_isolated(self):
        session = self.session(7010901, players=3)
        engine = session.engine
        target = self.put_on_battlefield(
            engine,
            self.permanent(engine, "B"),
            controller="C",
        )
        selection = {
            "selection": "replacement-a",
            "event_path": [0],
        }
        plan = prepare_return_to_owner_hand(
            engine,
            request_for_card(target),
            actor="A",
            reason="copy isolation",
            replacement_selections=(selection,),
        )
        equivalent = prepare_return_to_owner_hand(
            engine,
            request_for_card(target),
            actor="A",
            reason="copy isolation",
            replacement_selections=(
                {"event_path": [0], "selection": "replacement-a"},
            ),
        )
        selection["event_path"].append(9)
        selection["selection"] = "replacement-b"

        self.assertEqual(plan, equivalent)
        self.assertEqual(
            {"event_path": [0], "selection": "replacement-a"},
            thaw_value(plan.replacement_selections[0]),
        )
        with self.assertRaises(FrozenInstanceError):
            plan.reason = "mutated"  # type: ignore[misc]

    def test_handler_lowers_one_strict_typed_return_intent(self):
        context = ReadOnlyHandlerContext(
            actor="A",
            default_reason="return fixture",
            query=ReadOnlyRulesQuery(
                seats=("A", "B", "C", "D"),
                active_seats=("A", "B", "C", "D"),
                apnap_order=("B", "C", "D", "A"),
            ),
        )
        plan = ReturnPermanentToOwnerHandHandler().lower(
            {"op": "bounce", "card": "B01"},
            context,
        )
        self.assertEqual(
            "generic.return-permanent-to-owner-hand.v1",
            plan.handler_id,
        )
        self.assertEqual(
            (
                ReturnPermanentToOwnerHandIntent(
                    actor="A",
                    object_ref="B01",
                    reason="return fixture",
                ),
            ),
            plan.intents,
        )
        for malformed in (
            {"op": "bounce", "card": ""},
            {"op": "bounce", "card": "B01", "reason": 4},
            {
                "op": "bounce",
                "card": "B01",
                "_replacement_selections": "replacement-a",
            },
            {"op": "bounce", "card": "B01", "destination": "hand"},
        ):
            with self.subTest(effect=malformed):
                with self.assertRaises(SemanticNodeError):
                    ReturnPermanentToOwnerHandHandler().lower(
                        malformed,
                        context,
                    )

    def test_controlled_permanent_returns_to_owners_hand(self):
        session = self.session(7010902)
        engine = session.engine
        target = self.put_on_battlefield(
            engine,
            self.permanent(engine, "B"),
            controller="C",
        )
        previous_logical_id = target.logical_object_id

        result = return_permanent_to_owner_hand(
            engine,
            target.ref,
            actor="A",
            reason="control-change witness",
        )

        self.assertEqual("B", result.owner)
        self.assertEqual("C", result.origin_controller)
        self.assertTrue(result.returned_to_hand)
        self.assertEqual("hand", target.zone)
        self.assertEqual("B", target.controller)
        self.assertIn(target.object_id, engine.state.players["B"].zones["hand"])
        self.assertNotIn(
            target.object_id,
            engine.state.players["C"].zones["battlefield"],
        )
        self.assertNotEqual(previous_logical_id, target.logical_object_id)
        self.assertEqual(
            1,
            sum(
                event.code == "permanent.return_to_owner_hand"
                for event in engine.state.events
            ),
        )

    def test_destination_replacement_preserves_typed_result(self):
        session = self.session(7010903)
        engine = session.engine
        target = self.put_on_battlefield(
            engine,
            self.permanent(engine, "A"),
        )
        source = self.put_on_battlefield(
            engine,
            self.permanent(engine, "B"),
        )
        engine.semantics.put(
            SemanticProgram(
                key="test:return-destination-replacement",
                label="Replace return destination",
                oracle_id=source.oracle_id,
                ability_id="static:front:return-destination",
                active_zone="battlefield",
                event="zone.change",
                trust_level="provisional",
                handlers=[
                    {
                        "handler_id": "replacement.zone.destination.v1",
                        "schema_version": 1,
                        "event": "zone.change",
                        "condition": {
                            "destination": "hand",
                            "object_kind": "card",
                            "owner_relation": "opponent",
                        },
                        "destination": "exile",
                        "counters": {"return-replacement": 1},
                    }
                ],
            )
        )

        with patch.object(
            type(engine),
            "semantic_program_is_current_trusted",
            return_value=True,
        ):
            result = return_permanent_to_owner_hand(
                engine,
                target.ref,
                actor="B",
                reason="destination replacement witness",
            )

        self.assertFalse(result.returned_to_hand)
        self.assertEqual("exile", result.destination)
        self.assertEqual("exile", target.zone)
        self.assertEqual(1, target.counters["return-replacement"])
        event = next(
            event
            for event in reversed(engine.state.events)
            if event.code == "permanent.return_to_owner_hand"
        )
        self.assertEqual("exile", event.details["destination"])
        self.assertEqual("hand", event.details["requested_destination"])

    def test_phased_and_stale_permanents_fail_before_mutation(self):
        session = self.session(7010904)
        engine = session.engine
        phased = self.put_on_battlefield(
            engine,
            self.permanent(engine, "B"),
        )
        phased.phased_out = True
        before = authoritative_state_hash(engine.state)
        with self.assertRaisesRegex(ReturnToHandError, "phased-in"):
            prepare_return_to_owner_hand(
                engine,
                request_for_card(phased),
                actor="A",
                reason="phasing witness",
            )
        self.assertEqual(before, authoritative_state_hash(engine.state))

        phased.phased_out = False
        plan = prepare_return_to_owner_hand(
            engine,
            request_for_card(phased),
            actor="A",
            reason="stale control witness",
        )
        phased.controller = "C"
        before = authoritative_state_hash(engine.state)
        with self.assertRaisesRegex(ReturnToHandError, "stale"):
            commit_return_to_owner_hand(engine, plan)
        self.assertEqual(before, authoritative_state_hash(engine.state))
        self.assertEqual("battlefield", phased.zone)

    def test_compiled_return_is_multiplayer_private_and_replays(self):
        session = self.session(7010905, unsummon=True)
        engine = session.engine
        source = next(
            card
            for card in engine.state.cards.values()
            if card.owner == "A" and card.printed_name == "Unsummon"
        )
        target = self.put_on_battlefield(
            engine,
            self.permanent(engine, "B"),
        )
        engine.move_card(source.object_id, "hand", log=False)
        engine.state.players["A"].mana_pool["U"] = 1
        engine.state.priority_player = "A"
        hints = engine._priority_action_hints("A")
        action = next(
            row for row in hints["actions"] if row.get("card") == source.ref
        )
        self.assertEqual("cast", action["action"])
        self.assertIn(target.ref, action["target_schema"]["legal_refs"])
        self.assertFalse(
            {"A", "B", "C", "D"}.intersection(
                action["target_schema"]["legal_refs"]
            )
        )
        engine._issue_priority("A", hints)
        session.initial_checkpoint = checkpoint_envelope(engine.state)
        session.commands.clear()
        session.decisions.clear()

        before = authoritative_state_hash(engine.state)
        rejected = session.act(
            "pilot:A",
            {
                "action_id": action["id"],
                "targets": ["B"],
                "pay": "manual",
                "payment": {"U": 1},
            },
        )
        self.assertFalse(rejected.ok)
        self.assertEqual(before, authoritative_state_hash(engine.state))

        accepted = session.act(
            "pilot:A",
            {
                "action_id": action["id"],
                "targets": [target.ref],
                "pay": "manual",
                "payment": {"U": 1},
            },
        )
        self.assertTrue(accepted.ok, accepted.summary)
        self.pass_stack(session)

        committed_target = engine.state.cards[target.object_id]
        committed_source = engine.state.cards[source.object_id]
        self.assertEqual(
            "hand",
            committed_target.zone,
            {
                "source_zone": committed_source.zone,
                "stack": [item.to_dict() for item in engine.state.stack],
                "recent_events": [
                    {
                        "code": event.code,
                        "summary": event.summary,
                        "details": event.details,
                    }
                    for event in engine.state.events[-20:]
                ],
            },
        )
        self.assertEqual("B", committed_target.owner)
        self.assertEqual("graveyard", committed_source.zone)
        self.assertIn(
            "permanent.return_to_owner_hand",
            [event.code for event in engine.state.events],
        )
        projector = StateProjector(self.db, engine.state)
        projected_b = projector._snapshot("pilot:B")
        projected_d = projector._snapshot("pilot:D")
        self.assertIn(
            target.ref,
            {row["id"] for row in projected_b["players"]["B"]["hand"]},
        )
        self.assertNotIn("hand", projected_d["players"]["B"])
        private_refs = {
            engine.state.cards[object_id].ref
            for object_id in engine.state.players["B"].zones["hand"]
            if object_id != target.object_id
        }
        serialized_d = json.dumps(projected_d, sort_keys=True)
        self.assertTrue(all(ref not in serialized_d for ref in private_refs))
        self.assert_replays(session, "targeted-return-to-hand-record")

    def test_expanded_target_offer_commit_revalidation_and_replay(self):
        def setup(seed: int):
            session = self.session(seed, expanded_return=True)
            engine = session.engine
            source = next(
                card
                for card in engine.state.cards.values()
                if card.owner == "A"
                and card.printed_name == "Expanded Battlefield Return Fixture"
            )
            own = self.put_on_battlefield(
                engine,
                self.permanent(engine, "A"),
            )
            opposing = self.put_on_battlefield(
                engine,
                self.permanent(engine, "B"),
            )
            own.tapped = True
            opposing.tapped = True
            engine.move_card(source.object_id, "hand", log=False)
            engine.state.players["A"].mana_pool["C"] = 1
            engine.state.players["A"].mana_pool["U"] = 1
            engine.state.priority_player = "A"
            hints = engine._priority_action_hints("A")
            action = next(
                row for row in hints["actions"] if row.get("card") == source.ref
            )
            self.assertEqual([opposing.ref], action["target_schema"]["legal_refs"])
            self.assertNotIn(own.ref, action["target_schema"]["legal_refs"])
            engine._issue_priority("A", hints)
            return session, source, opposing, action

        stale, stale_source, stale_target, stale_action = setup(701_091_001)
        stale_target.tapped = False
        before = authoritative_state_hash(stale.state)
        rejected = stale.act(
            "pilot:A",
            {
                "action_id": stale_action["id"],
                "targets": [stale_target.ref],
                "pay": "manual",
                "payment": {"C": 1, "U": 1},
            },
        )
        self.assertFalse(rejected.ok)
        self.assertEqual(before, authoritative_state_hash(stale.state))
        self.assertEqual("hand", stale_source.zone)

        session, source, target, action = setup(701_091_002)
        session.initial_checkpoint = checkpoint_envelope(session.state)
        session.commands.clear()
        session.decisions.clear()
        accepted = session.act(
            "pilot:A",
            {
                "action_id": action["id"],
                "targets": [target.ref],
                "pay": "manual",
                "payment": {"C": 1, "U": 1},
            },
        )
        self.assertTrue(accepted.ok, accepted.summary)
        self.pass_stack(session)
        self.assertEqual("hand", target.zone)
        self.assertEqual("B", target.owner)
        self.assertEqual("graveyard", source.zone)
        self.assert_replays(session, "expanded-targeted-return-record")

    def test_return_transaction_mutants_are_killed(self):
        session = self.session(7010906)
        engine = session.engine
        target = self.put_on_battlefield(
            engine,
            self.permanent(engine, "B"),
        )
        plan = prepare_return_to_owner_hand(
            engine,
            request_for_card(target),
            actor="A",
            reason="stale-validation mutation witness",
        )
        target.controller = "C"

        def assert_stale_rejected() -> None:
            before = authoritative_state_hash(engine.state)
            with self.assertRaises(ReturnToHandError):
                commit_return_to_owner_hand(engine, plan)
            self.assertEqual(before, authoritative_state_hash(engine.state))

        assert_stale_rejected()
        with patch.object(
            return_module,
            "validate_return_to_hand_plan",
            lambda _host, _plan: None,
        ):
            with self.assertRaises(AssertionError):
                assert_stale_rejected()


if __name__ == "__main__":
    unittest.main()
