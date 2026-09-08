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
    ReturnToHandError,
    commit_graveyard_card_return_to_owner_hand,
    prepare_graveyard_card_return_to_owner_hand,
    request_for_card,
    return_graveyard_card_to_owner_hand,
)
from quorune.semantic_runtime import (
    ReadOnlyHandlerContext,
    ReadOnlyRulesQuery,
    ReturnGraveyardCardToOwnerHandIntent,
)
from quorune.semantic_runtime.context import SemanticNodeError
from quorune.semantic_runtime.return_to_hand_handlers import (
    ReturnGraveyardCardToOwnerHandHandler,
)
from quorune.semantics import SemanticProgram
from scripts.build_test_database import build_fixture_database


def focused_card_database(directory: str) -> CardDatabase:
    database = Path(directory) / "graveyard-return.sqlite3"
    build_fixture_database(
        [
            ROOT / "tests" / "fixtures" / "scryfall-exact-lists.json",
            ROOT
            / "tests"
            / "fixtures"
            / "targeted-graveyard-return-cards.json",
        ],
        database,
    )
    return CardDatabase(database)


class GraveyardReturnRuleTests(unittest.TestCase):
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
        regrowth: bool = False,
        expanded_return: bool = False,
    ):
        mishra = copy.deepcopy(self.mishra)
        if regrowth or expanded_return:
            entries = [
                entry for entry in mishra.entries if entry.board == "mainboard"
            ]
            entries[0].name = (
                "Expanded Graveyard Return Fixture"
                if expanded_return
                else "Regrowth"
            )
            if expanded_return:
                entries[1].name = "Graveyard Goblin Target Fixture"
        session = make_session(
            self.db,
            mishra,
            self.zimone,
            players=4,
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
    def card(engine, seat: str, *, exclude=()):
        excluded = set(exclude)
        return next(
            card
            for card in engine.state.cards.values()
            if card.owner == seat
            and card.object_id not in excluded
            and card.zone != "command"
            and card.is_card_object
        )

    @staticmethod
    def land_card(engine, seat: str, *, exclude=()):
        excluded = set(exclude)
        return next(
            card
            for card in engine.state.cards.values()
            if card.owner == seat
            and card.object_id not in excluded
            and card.zone != "command"
            and card.is_card_object
            and "land"
            in engine._type_parts(
                str(engine._effective_card_data(card).get("type_line") or "")
            )[0]
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

    def test_handler_lowers_one_strict_graveyard_return_intent(self):
        context = ReadOnlyHandlerContext(
            actor="A",
            default_reason="graveyard return fixture",
            query=ReadOnlyRulesQuery(
                seats=("A", "B", "C", "D"),
                active_seats=("A", "B", "C", "D"),
                apnap_order=("B", "C", "D", "A"),
            ),
        )
        plan = ReturnGraveyardCardToOwnerHandHandler().lower(
            {
                "op": "return_graveyard_card_to_owner_hand",
                "card": "A07",
            },
            context,
        )
        self.assertEqual(
            "generic.return-graveyard-card-to-owner-hand.v1",
            plan.handler_id,
        )
        self.assertEqual(
            (
                ReturnGraveyardCardToOwnerHandIntent(
                    actor="A",
                    object_ref="A07",
                    reason="graveyard return fixture",
                ),
            ),
            plan.intents,
        )
        for malformed in (
            {"op": "return_graveyard_card_to_owner_hand", "card": ""},
            {
                "op": "return_graveyard_card_to_owner_hand",
                "card": "A07",
                "reason": 4,
            },
            {
                "op": "return_graveyard_card_to_owner_hand",
                "card": "A07",
                "destination": "hand",
            },
        ):
            with self.subTest(effect=malformed):
                with self.assertRaises(SemanticNodeError):
                    ReturnGraveyardCardToOwnerHandHandler().lower(
                        malformed,
                        context,
                    )

    def test_own_graveyard_card_returns_to_hand_with_new_identity(self):
        session = self.session(7030101)
        engine = session.engine
        target = self.card(engine, "A")
        engine.move_card(target.object_id, "graveyard", log=False)
        previous_logical_id = target.logical_object_id
        selection = {"selection": "replacement-a", "event_path": [0]}
        plan = prepare_graveyard_card_return_to_owner_hand(
            engine,
            request_for_card(target),
            actor="A",
            reason="typed graveyard return witness",
            replacement_selections=(selection,),
        )
        equivalent = prepare_graveyard_card_return_to_owner_hand(
            engine,
            request_for_card(target),
            actor="A",
            reason="typed graveyard return witness",
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

        commit_plan = prepare_graveyard_card_return_to_owner_hand(
            engine,
            request_for_card(target),
            actor="A",
            reason="typed graveyard return witness",
        )
        result = commit_graveyard_card_return_to_owner_hand(engine, commit_plan)
        self.assertTrue(result.returned_to_hand)
        self.assertEqual("A", result.owner)
        self.assertEqual("hand", target.zone)
        self.assertNotEqual(previous_logical_id, target.logical_object_id)
        self.assertIn(target.object_id, engine.state.players["A"].zones["hand"])
        event = next(
            event
            for event in reversed(engine.state.events)
            if event.code == "card.return_from_graveyard_to_owner_hand"
        )
        self.assertEqual("graveyard", event.details["origin"])
        self.assertEqual("hand", event.details["destination"])

    def test_opponent_noncard_and_stale_graveyard_objects_fail_before_mutation(self):
        session = self.session(7030102)
        engine = session.engine
        opponent = self.card(engine, "B")
        engine.move_card(opponent.object_id, "graveyard", log=False)
        before = authoritative_state_hash(engine.state)
        with self.assertRaisesRegex(ReturnToHandError, "owned"):
            return_graveyard_card_to_owner_hand(
                engine,
                opponent.ref,
                actor="A",
                reason="opponent graveyard witness",
            )
        self.assertEqual(before, authoritative_state_hash(engine.state))

        noncard = self.card(engine, "A")
        engine.move_card(noncard.object_id, "graveyard", log=False)
        noncard.object_kind = "token"
        before = authoritative_state_hash(engine.state)
        with self.assertRaisesRegex(ReturnToHandError, "physical card"):
            prepare_graveyard_card_return_to_owner_hand(
                engine,
                request_for_card(noncard),
                actor="A",
                reason="noncard graveyard witness",
            )
        self.assertEqual(before, authoritative_state_hash(engine.state))

        target = self.card(engine, "A", exclude=(noncard.object_id,))
        engine.move_card(target.object_id, "graveyard", log=False)
        plan = prepare_graveyard_card_return_to_owner_hand(
            engine,
            request_for_card(target),
            actor="A",
            reason="stale graveyard witness",
        )
        engine.move_card(target.object_id, "exile", log=False)
        engine.move_card(target.object_id, "graveyard", log=False)
        before = authoritative_state_hash(engine.state)
        with self.assertRaisesRegex(ReturnToHandError, "stale"):
            commit_graveyard_card_return_to_owner_hand(engine, plan)
        self.assertEqual(before, authoritative_state_hash(engine.state))
        self.assertEqual("graveyard", target.zone)

    def test_destination_replacement_preserves_graveyard_return_result(self):
        session = self.session(7030103)
        engine = session.engine
        target = self.card(engine, "A")
        engine.move_card(target.object_id, "graveyard", log=False)
        source = self.land_card(engine, "B")
        engine.move_card(source.object_id, "battlefield", log=False)
        engine.semantics.put(
            SemanticProgram(
                key="test:graveyard-return-destination-replacement",
                label="Replace graveyard return destination",
                oracle_id=source.oracle_id,
                ability_id="static:front:graveyard-return-destination",
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
                        "counters": {"graveyard-return-replacement": 1},
                    }
                ],
            )
        )

        with patch.object(
            type(engine),
            "semantic_program_is_current_trusted",
            return_value=True,
        ):
            result = return_graveyard_card_to_owner_hand(
                engine,
                target.ref,
                actor="A",
                reason="destination replacement witness",
            )

        self.assertFalse(result.returned_to_hand)
        self.assertEqual("exile", result.destination)
        self.assertEqual("exile", target.zone)
        self.assertEqual(1, target.counters["graveyard-return-replacement"])
        event = next(
            event
            for event in reversed(engine.state.events)
            if event.code == "card.return_from_graveyard_to_owner_hand"
        )
        self.assertEqual("exile", event.details["destination"])
        self.assertEqual("hand", event.details["requested_destination"])

    def test_compiled_regrowth_is_owner_scoped_private_and_replays(self):
        session = self.session(7030104, regrowth=True)
        engine = session.engine
        source = next(
            card
            for card in engine.state.cards.values()
            if card.owner == "A" and card.printed_name == "Regrowth"
        )
        own_target = self.card(engine, "A", exclude=(source.object_id,))
        opponent_target = self.card(engine, "B")
        engine.move_card(source.object_id, "hand", log=False)
        engine.move_card(own_target.object_id, "graveyard", log=False)
        engine.move_card(opponent_target.object_id, "graveyard", log=False)
        engine.state.players["A"].mana_pool["C"] = 1
        engine.state.players["A"].mana_pool["G"] = 1
        engine.state.active_player = "A"
        engine.state.phase = "precombat_main"
        engine.state.step = ""
        engine.state.priority_player = "A"
        hints = engine._priority_action_hints("A")
        action = next(
            row for row in hints["actions"] if row.get("card") == source.ref
        )
        self.assertEqual("cast", action["action"])
        self.assertIn(own_target.ref, action["target_schema"]["legal_refs"])
        self.assertNotIn(
            opponent_target.ref,
            action["target_schema"]["legal_refs"],
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
                "targets": [opponent_target.ref],
                "pay": "manual",
                "payment": {"C": 1, "G": 1},
            },
        )
        self.assertFalse(rejected.ok)
        self.assertEqual(before, authoritative_state_hash(engine.state))

        accepted = session.act(
            "pilot:A",
            {
                "action_id": action["id"],
                "targets": [own_target.ref],
                "pay": "manual",
                "payment": {"C": 1, "G": 1},
            },
        )
        self.assertTrue(accepted.ok, accepted.summary)
        self.pass_stack(session)

        self.assertEqual("hand", engine.state.cards[own_target.object_id].zone)
        self.assertEqual("graveyard", engine.state.cards[source.object_id].zone)
        self.assertEqual(
            "graveyard",
            engine.state.cards[opponent_target.object_id].zone,
        )
        projector = StateProjector(self.db, engine.state)
        projected_a = projector._snapshot("pilot:A")
        projected_c = projector._snapshot("pilot:C")
        self.assertIn(
            own_target.ref,
            {row["id"] for row in projected_a["players"]["A"]["hand"]},
        )
        self.assertNotIn("hand", projected_c["players"]["A"])
        serialized_c = json.dumps(projected_c["players"]["A"], sort_keys=True)
        private_refs = {
            engine.state.cards[object_id].ref
            for object_id in engine.state.players["A"].zones["hand"]
            if object_id != own_target.object_id
        }
        self.assertTrue(all(ref not in serialized_c for ref in private_refs))
        self.assert_replays(session, "targeted-graveyard-return-record")

    def test_characteristic_target_offer_commit_and_replay(self):
        def setup(seed: int):
            session = self.session(seed, expanded_return=True)
            engine = session.engine
            source = next(
                card
                for card in engine.state.cards.values()
                if card.owner == "A"
                and card.printed_name == "Expanded Graveyard Return Fixture"
            )
            target = next(
                card
                for card in engine.state.cards.values()
                if card.owner == "A"
                and card.printed_name == "Graveyard Goblin Target Fixture"
            )
            nonmatching = next(
                card
                for card in engine.state.cards.values()
                if card.owner == "A"
                and card.object_id not in {source.object_id, target.object_id}
                and card.zone != "command"
                and "goblin"
                not in str(
                    engine._effective_card_data(card).get("type_line") or ""
                ).casefold()
            )
            engine.move_card(source.object_id, "hand", log=False)
            engine.move_card(target.object_id, "graveyard", log=False)
            engine.move_card(nonmatching.object_id, "graveyard", log=False)
            engine.state.players["A"].mana_pool["C"] = 1
            engine.state.players["A"].mana_pool["B"] = 1
            engine.state.active_player = "A"
            engine.state.phase = "precombat_main"
            engine.state.step = "main"
            engine.state.priority_player = "A"
            hints = engine._priority_action_hints("A")
            action = next(
                row for row in hints["actions"] if row.get("card") == source.ref
            )
            self.assertEqual([target.ref], action["target_schema"]["legal_refs"])
            self.assertNotIn(
                nonmatching.ref,
                action["target_schema"]["legal_refs"],
            )
            engine._issue_priority("A", hints)
            return session, source, target, action

        stale, stale_source, stale_target, stale_action = setup(703_011_001)
        stale.engine.move_card(stale_target.object_id, "exile", log=False)
        before = authoritative_state_hash(stale.state)
        rejected = stale.act(
            "pilot:A",
            {
                "action_id": stale_action["id"],
                "targets": [stale_target.ref],
                "pay": "manual",
                "payment": {"B": 1, "C": 1},
            },
        )
        self.assertFalse(rejected.ok)
        self.assertEqual(before, authoritative_state_hash(stale.state))
        self.assertEqual("hand", stale_source.zone)

        session, source, target, action = setup(703_011_002)
        session.initial_checkpoint = checkpoint_envelope(session.state)
        session.commands.clear()
        session.decisions.clear()
        accepted = session.act(
            "pilot:A",
            {
                "action_id": action["id"],
                "targets": [target.ref],
                "pay": "manual",
                "payment": {"B": 1, "C": 1},
            },
        )
        self.assertTrue(accepted.ok, accepted.summary)
        self.pass_stack(session)
        self.assertEqual("hand", target.zone)
        self.assertEqual("graveyard", source.zone)
        projected = StateProjector(self.db, session.state)._snapshot("pilot:C")
        self.assertNotIn("hand", projected["players"]["A"])
        self.assert_replays(session, "expanded-graveyard-return-record")

    def test_graveyard_return_transaction_mutant_is_killed(self):
        session = self.session(7030105)
        engine = session.engine
        target = self.card(engine, "A")
        engine.move_card(target.object_id, "graveyard", log=False)
        plan = prepare_graveyard_card_return_to_owner_hand(
            engine,
            request_for_card(target),
            actor="A",
            reason="stale validation mutation witness",
        )
        engine.move_card(target.object_id, "exile", log=False)
        engine.move_card(target.object_id, "graveyard", log=False)

        def assert_stale_rejected() -> None:
            before = authoritative_state_hash(engine.state)
            with self.assertRaises(ReturnToHandError):
                commit_graveyard_card_return_to_owner_hand(engine, plan)
            self.assertEqual(before, authoritative_state_hash(engine.state))

        assert_stale_rejected()
        with patch.object(
            return_module,
            "validate_graveyard_card_return_to_hand_plan",
            lambda _host, _plan: None,
        ):
            with self.assertRaises(AssertionError):
                assert_stale_rejected()


if __name__ == "__main__":
    unittest.main()
