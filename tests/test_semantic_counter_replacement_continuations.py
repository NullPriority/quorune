from __future__ import annotations

import copy
import json
from pathlib import Path
import tempfile
import unittest

from common import ROOT, keep_all, make_session
from scripts.build_test_database import build_fixture_database
from quorune.carddb import CardDatabase
from quorune.deck import DeckLoader
from quorune.model import CardInstance, StackItem
from quorune.oracle_ir import register_generated_programs
from quorune.projection import StateProjector
from quorune.record import (
    authoritative_state_hash,
    checkpoint_envelope,
    replay_record,
)
from quorune.semantic_choices.counter_coordination import (
    validate_counter_intent_identity,
)
from quorune.semantic_choices.model import SemanticChoiceError
from quorune.semantics import SemanticProgram
from quorune.rules.capabilities import load_default_capability_registry


class SemanticCounterReplacementContinuationTests(unittest.TestCase):
    """Counter-producing semantic choices suspend before authoritative mutation."""

    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        database = Path(cls.temporary.name) / "semantic-counter.sqlite3"
        build_fixture_database(
            [
                ROOT / "tests" / "fixtures" / "scryfall-exact-lists.json",
                ROOT
                / "tests"
                / "fixtures"
                / "counter-replacement-cards.json",
                ROOT / "tests" / "fixtures" / "damage-result-cards.json",
            ],
            database,
        )
        cls.db = CardDatabase(database)
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

    def session(self, seed: int, *, players: int = 2):
        session = make_session(
            self.db,
            self.mishra,
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

    def add_permanent(self, engine, *, name: str, ref: str) -> CardInstance:
        record = self.db.lookup(name)
        from common import register_current_fixture_programs
        register_current_fixture_programs(self.db, engine.semantics, (record,),
                                          profile=engine.state.config.review_profile)
        card = CardInstance(
            object_id=f"fixture:{ref}",
            ref=ref,
            oracle_id=record.oracle_id,
            printed_name=record.name,
            owner="A",
            controller="A",
            zone="battlefield",
            zone_timestamp=engine.state.event_sequence + 1,
            known_to=list(engine.seats),
            revealed_to=list(engine.seats),
        )
        engine.state.cards[card.object_id] = card
        engine.state.players["A"].zones["battlefield"].append(card.object_id)
        return card

    def begin_fabricate(self, session):
        engine = session.engine
        target = self.add_permanent(engine, name="Island", ref="fabricator")
        self.add_permanent(engine, name="Doubling Season", ref="doubling")
        self.add_permanent(
            engine,
            name="Doc Samson, Super Psychiatrist",
            ref="additional",
        )
        program = SemanticProgram(
            key="test:semantic-counter-fabricate",
            label="Fabricate with competing replacements",
            effects=[{"op": "fabricate", "amount": 1}],
            trust_level="provisional",
        )
        engine.semantics.put(program)
        item = StackItem(
            stack_id="semantic-counter-fabricate",
            ref="S-semantic-counter-fabricate",
            kind="triggered_ability",
            controller="A",
            label=program.label,
            semantic_key=program.key,
            source_object_id=target.object_id,
            visibility=["A", "B"],
        )
        engine.state.stack.append(item)
        engine._continue_resolution(
            stack_ref=item.ref,
            effects=[dict(value) for value in program.effects],
            destination=None,
            note="",
        )
        self.assertEqual("semantic.choice", engine.state.pending_decision.kind)
        return target, item

    def begin_semantic_life_replacement(self, session):
        engine = session.engine
        target = self.add_permanent(
            engine,
            name="Island",
            ref="life-replacement-island",
        )
        register_generated_programs(
            self.db,
            engine.semantics,
            (self.db.lookup("Boon Reflection"),),
            capability_registry=load_default_capability_registry(),
            capability_profile="commander_review",
            promote_exact_runtime_handlers=True,
        )
        self.add_permanent(engine, name="Boon Reflection", ref="boon-one")
        self.add_permanent(engine, name="Boon Reflection", ref="boon-two")
        program = SemanticProgram(
            key="test:semantic-life-replacement",
            label="Choose an Island, then gain life",
            effects=[
                {
                    "op": "choose_objects",
                    "player": "A",
                    "selector": {
                        "zones": ["battlefield"],
                        "categories": ["permanent"],
                        "types_any": ["land"],
                        "controller_relation": "you",
                        "min": 1,
                        "max": 1,
                    },
                    "then": [
                        {
                            "op": "life_if_selected_subtype",
                            "subtype": "island",
                            "amount": 2,
                        }
                    ],
                }
            ],
            trust_level="provisional",
        )
        engine.semantics.put(program)
        item = StackItem(
            stack_id="semantic-life-replacement",
            ref="S-semantic-life-replacement",
            kind="triggered_ability",
            controller="A",
            label=program.label,
            semantic_key=program.key,
            source_object_id=target.object_id,
            visibility=["A", "B"],
        )
        engine.state.stack.append(item)
        engine._continue_resolution(
            stack_ref=item.ref,
            effects=[dict(value) for value in program.effects],
            destination=None,
            note="",
        )
        return target, item

    def choose_counter(self, session):
        result = session.act(
            "pilot:A",
            {
                "action_id": "choose",
                "choice": "counter",
                "plan": "DEVELOP_BOARD",
                "reason": "Keep the counters on the source.",
            },
        )
        self.assertTrue(result.ok, result.summary)
        self.assertEqual(
            "replacement.order", session.engine.state.pending_decision.kind
        )

    def select_replacement(self, session):
        projected = StateProjector(
            self.db, session.engine.state
        )._decision("pilot:A")
        selected = projected["ctx"]["options"][0]["id"]
        result = session.act(
            "pilot:A",
            {
                "action_id": "choose",
                "choices": {"replacement": selected},
            },
        )
        self.assertTrue(result.ok, result.summary)
        return projected

    def test_fabricate_suspends_before_mutation_and_projection_is_seat_scoped(
        self,
    ):
        session = self.session(12261421)
        target, item = self.begin_fabricate(session)
        self.choose_counter(session)

        self.assertNotIn("+1/+1", target.counters)
        projector = StateProjector(self.db, session.engine.state)
        projected_a = projector._decision("pilot:A")
        self.assertIsNone(projector._decision("pilot:B"))
        serialized = json.dumps(projected_a, sort_keys=True)
        self.assertNotIn("semantic_choice_response", serialized)
        self.assertNotIn("counter_intent", serialized)
        self.assertNotIn(target.object_id, serialized)

        before = authoritative_state_hash(session.engine.state)
        capability = session.engine.permissions.capability_for("pilot:A")
        rejected = session.engine.try_submit(
            token=capability.token,
            principal="pilot:A",
            action="choose",
            payload={"replacement": "unknown"},
        )
        self.assertFalse(rejected.ok)
        self.assertEqual(before, authoritative_state_hash(session.engine.state))

        self.select_replacement(session)
        current_target = session.engine.state.cards[target.object_id]
        self.assertGreater(current_target.counters["+1/+1"], 1)
        self.assertFalse(
            any(candidate.ref == item.ref for candidate in session.engine.state.stack)
        )

    def test_fabricate_counter_replacement_replays_exactly(self):
        session = self.session(12261422)
        target, _item = self.begin_fabricate(session)
        session.initial_checkpoint = checkpoint_envelope(session.engine.state)
        session.commands.clear()
        session.decisions.clear()

        self.choose_counter(session)
        self.select_replacement(session)
        expected_hash = authoritative_state_hash(session.engine.state)
        expected_counters = target.counters["+1/+1"]

        with tempfile.TemporaryDirectory() as temporary:
            record_dir = Path(temporary) / "semantic-counter-replay"
            session.save(record_dir)
            replay = replay_record(record_dir, self.db, verify=True)
        self.assertTrue(replay["ok"], replay)
        self.assertEqual(2, replay["commands"])
        self.assertEqual(expected_hash, replay["final_state_hash"])
        self.assertGreater(expected_counters, 1)

    def test_counter_resume_executes_later_typed_completion_intents_once(self):
        session = self.session(12261424)
        engine = session.engine
        target = self.add_permanent(engine, name="Island", ref="chosen-island")
        self.add_permanent(engine, name="Doubling Season", ref="doubling")
        self.add_permanent(
            engine,
            name="Doc Samson, Super Psychiatrist",
            ref="additional",
        )
        program = SemanticProgram(
            key="test:semantic-counter-then-life",
            label="Choose a land, add a counter, then gain life",
            effects=[
                {
                    "op": "choose_objects",
                    "player": "A",
                    "selector": {
                        "zones": ["battlefield"],
                        "categories": ["permanent"],
                        "types_any": ["land"],
                        "controller_relation": "you",
                        "min": 1,
                        "max": 1,
                    },
                    "then": [
                        {
                            "op": "add_counter_selected",
                            "counter": "charge",
                            "amount": 1,
                        },
                        {
                            "op": "life_if_selected_subtype",
                            "subtype": "island",
                            "amount": 2,
                        },
                    ],
                }
            ],
            trust_level="provisional",
        )
        engine.semantics.put(program)
        item = StackItem(
            stack_id="semantic-counter-then-life",
            ref="S-semantic-counter-then-life",
            kind="triggered_ability",
            controller="A",
            label=program.label,
            semantic_key=program.key,
            source_object_id=target.object_id,
            visibility=["A", "B"],
        )
        engine.state.stack.append(item)
        engine._continue_resolution(
            stack_ref=item.ref,
            effects=[dict(value) for value in program.effects],
            destination=None,
            note="",
        )
        before_life = engine.state.players["A"].life
        result = session.act(
            "pilot:A",
            {
                "action_id": "choose",
                "objects": [target.ref],
                "plan": "DEVELOP_BOARD",
                "reason": "Choose the Island.",
            },
        )
        self.assertTrue(result.ok, result.summary)
        self.assertEqual("replacement.order", engine.state.pending_decision.kind)
        self.select_replacement(session)

        self.assertGreater(target.counters["charge"], 1)
        self.assertEqual(before_life + 2, engine.state.players["A"].life)
        choice_events = [
            event
            for event in engine.state.events
            if event.code == "semantic.objects.chosen"
        ]
        self.assertEqual(1, len(choice_events))

    def test_semantic_life_intent_replacement_suspends_and_replays_exactly(
        self,
    ):
        session = self.session(12261426)
        engine = session.engine
        target, item = self.begin_semantic_life_replacement(session)
        before_life = engine.state.players["A"].life
        session.initial_checkpoint = checkpoint_envelope(engine.state)
        session.commands.clear()
        session.decisions.clear()

        result = session.act(
            "pilot:A",
            {
                "action_id": "choose",
                "objects": [target.ref],
                "plan": "DEVELOP_BOARD",
                "reason": "Choose the Island.",
            },
        )
        self.assertTrue(result.ok, result.summary)
        self.assertEqual(before_life, engine.state.players["A"].life)
        self.assertEqual("replacement.order", engine.state.pending_decision.kind)
        continuation = engine.state.pending_decision.continuation
        self.assertEqual("life_change", continuation["semantic_intent_kind"])
        self.assertEqual(2, continuation["semantic_intent"]["amount"])

        projector = StateProjector(self.db, engine.state)
        projected = projector._decision("pilot:A")
        self.assertIsNone(projector._decision("pilot:B"))
        serialized = json.dumps(projected, sort_keys=True)
        for forbidden in (
            "semantic_choice_response",
            "semantic_intent",
            "replacement_batch",
            "replacement_effects",
        ):
            self.assertNotIn(forbidden, serialized)
        self.assertNotIn(target.object_id, serialized)

        self.select_replacement(session)
        self.assertEqual(before_life + 8, engine.state.players["A"].life)
        self.assertFalse(
            any(candidate.ref == item.ref for candidate in engine.state.stack)
        )
        choice_events = [
            event
            for event in engine.state.events
            if event.code == "semantic.objects.chosen"
        ]
        self.assertEqual(1, len(choice_events))
        expected_hash = authoritative_state_hash(engine.state)

        with tempfile.TemporaryDirectory() as temporary:
            record_dir = Path(temporary) / "semantic-life-replacement-replay"
            session.save(record_dir)
            replay = replay_record(record_dir, self.db, verify=True)
        self.assertTrue(replay["ok"], replay)
        self.assertEqual(2, replay["commands"])
        self.assertEqual(expected_hash, replay["final_state_hash"])

    def test_tampered_semantic_life_intent_fails_closed_without_mutation(self):
        session = self.session(12261427)
        engine = session.engine
        target, _item = self.begin_semantic_life_replacement(session)
        before_life = engine.state.players["A"].life
        result = session.act(
            "pilot:A",
            {
                "action_id": "choose",
                "objects": [target.ref],
                "plan": "DEVELOP_BOARD",
                "reason": "Choose the Island.",
            },
        )
        self.assertTrue(result.ok, result.summary)
        decision = engine.state.pending_decision
        self.assertEqual("replacement.order", decision.kind)
        decision.continuation["semantic_intent"]["amount"] = 3
        projected = StateProjector(self.db, engine.state)._decision("pilot:A")
        selected = projected["ctx"]["options"][0]["id"]
        before = authoritative_state_hash(engine.state)
        capability = engine.permissions.capability_for("pilot:A")

        rejected = engine.try_submit(
            token=capability.token,
            principal="pilot:A",
            action="choose",
            payload={"replacement": selected},
        )

        self.assertFalse(rejected.ok)
        self.assertIn("changed before replacement resume", rejected.summary)
        self.assertEqual(before, authoritative_state_hash(engine.state))
        self.assertEqual(before_life, engine.state.players["A"].life)

    def test_tampered_counter_intent_fails_closed_without_mutation(self):
        session = self.session(12261423)
        target, _item = self.begin_fabricate(session)
        self.choose_counter(session)
        decision = session.engine.state.pending_decision
        decision.continuation["counter_intent"]["amount"] = 2
        projected = StateProjector(
            self.db, session.engine.state
        )._decision("pilot:A")
        selected = projected["ctx"]["options"][0]["id"]
        before = authoritative_state_hash(session.engine.state)
        capability = session.engine.permissions.capability_for("pilot:A")
        rejected = session.engine.try_submit(
            token=capability.token,
            principal="pilot:A",
            action="choose",
            payload={"replacement": selected},
        )
        self.assertFalse(rejected.ok)
        self.assertIn("changed before replacement resume", rejected.summary)
        self.assertEqual(before, authoritative_state_hash(session.engine.state))
        self.assertNotIn("+1/+1", target.counters)

    def test_semantic_counter_completion_identity_mutant_is_killed(self):
        session = self.session(12261425)
        target, _item = self.begin_fabricate(session)
        self.choose_counter(session)
        decision = session.engine.state.pending_decision
        decision.continuation["counter_intent_hash"] = "0" * 64
        projected = StateProjector(
            self.db, session.engine.state
        )._decision("pilot:A")
        selected = projected["ctx"]["options"][0]["id"]
        before = authoritative_state_hash(session.engine.state)
        capability = session.engine.permissions.capability_for("pilot:A")
        rejected = session.engine.try_submit(
            token=capability.token,
            principal="pilot:A",
            action="choose",
            payload={"replacement": selected},
        )
        self.assertFalse(rejected.ok)
        self.assertEqual(before, authoritative_state_hash(session.engine.state))
        self.assertNotIn("+1/+1", target.counters)

    def test_counter_intent_identity_rejects_unknown_boolean_and_duplicate_data(
        self,
    ):
        valid = {
            "actor": "A",
            "object_refs": ["A01"],
            "counter_name": "+1/+1",
            "amount": 1,
            "reason": "fabricate",
            "source_ref": "A01",
        }
        self.assertEqual(valid, validate_counter_intent_identity(valid))
        for label, mutate, message in (
            (
                "unknown field",
                lambda row: row.update({"future": True}),
                "unknown future",
            ),
            (
                "boolean amount",
                lambda row: row.update({"amount": True}),
                "malformed",
            ),
            (
                "duplicate refs",
                lambda row: row.update({"object_refs": ["A01", "A01"]}),
                "malformed",
            ),
        ):
            with self.subTest(label=label):
                malformed = copy.deepcopy(valid)
                mutate(malformed)
                with self.assertRaisesRegex(SemanticChoiceError, message):
                    validate_counter_intent_identity(malformed)


if __name__ == "__main__":
    unittest.main()
