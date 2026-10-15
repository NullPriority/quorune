from __future__ import annotations

import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from common import DB_PATH, keep_all, load_assets, make_session
from quorune.ability_fragments import (
    AbilityFragmentError,
    CounterMaximumSpec,
    ability_fragment_from_dict,
    ability_fragment_to_dict,
)
from quorune.card_programs.adapters import compile_card_program
from quorune.carddb import CardDatabase, CardRecord
from quorune.counter_maximums import (
    CounterMaximumError,
)
from quorune.compiler.counter_maximum_templates import (
    parse_fixed_self_counter_maximum,
)
from quorune.model import StackItem
from quorune.oracle_ir import compile_oracle_card
from quorune.record import (
    authoritative_state_hash,
    checkpoint_envelope,
    replay_record,
)
from quorune.rules.capabilities import (
    CapabilityRegistry,
    load_default_capability_registry,
)
from quorune.semantics import SemanticProgram
from quorune.state_based_actions import (
    PermanentSnapshot,
    evaluate_permanent_state_based_actions,
)


ROOT = Path(__file__).resolve().parents[1]


def maximum_record(
    oracle_text: str = (
        "Rasputin can't have more than seven dream counters on it."
    ),
) -> CardRecord:
    return CardRecord(
        oracle_id="00000000-0000-4000-8000-000000704005",
        name="Rasputin Dreamweaver",
        mana_cost="{4}{W}{U}",
        mana_value=6.0,
        type_line="Legendary Creature — Human Wizard",
        oracle_text=oracle_text,
        power="4",
        toughness="1",
        loyalty=None,
        defense=None,
        colors=("U", "W"),
        color_identity=("U", "W"),
        keywords=(),
        produced_mana=(),
        layout="normal",
        released_at="1994-06-01",
        legalities={"commander": "legal"},
        faces=(),
        raw={},
    )


def maximum_fragment(counter: str, maximum: int) -> dict[str, object]:
    return ability_fragment_to_dict(CounterMaximumSpec(counter, maximum))


class CounterMaximumModelAndCompilerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db = CardDatabase(DB_PATH)
        cls.capabilities = load_default_capability_registry()

    @classmethod
    def tearDownClass(cls):
        cls.db.close()

    def test_model_compiler_and_source_reference_are_closed(self):
        spec = CounterMaximumSpec("Dream", 7)
        serialized = ability_fragment_to_dict(spec)
        self.assertEqual(spec, ability_fragment_from_dict(serialized))
        self.assertEqual(
            spec,
            parse_fixed_self_counter_maximum(
                "Rasputin can't have more than seven dream counters on it.",
                source_name="Rasputin Dreamweaver",
            ),
        )
        self.assertEqual(
            CounterMaximumSpec("+1/+1", 2),
            parse_fixed_self_counter_maximum(
                "This creature can’t have more than 2 +1/+1 counters on it.",
                source_name="Bounded Fixture",
            ),
        )

        record = maximum_record()
        compiled = compile_oracle_card(
            record,
            capability_registry=self.capabilities,
            capability_profile="commander_review",
        )
        self.assertEqual("exact", compiled.status)
        node = compiled.faces[0].nodes[0]
        self.assertEqual(
            record.oracle_text,
            record.oracle_text[node.span.start : node.span.end],
        )
        self.assertEqual(
            "ability.static.counter-maximum.v1",
            node.handlers[0]["handler_id"],
        )
        self.assertEqual(
            ("state_based.counter_maximum.fixed_self",),
            node.capability_dependencies,
        )
        program = compile_card_program(
            self.db,
            record,
            capability_registry=self.capabilities,
            capability_profile="commander_review",
            trust_level="trusted",
        )
        self.assertTrue(program.trust_closure["trusted"])
        self.assertEqual(
            ["state_based.counter_maximum.fixed_self"],
            program.to_dict()["capability_dependencies"],
        )

    def test_malformed_and_unsupported_grammar_fail_closed(self):
        with self.assertRaisesRegex(
            CounterMaximumError,
            "nonnegative integer",
        ):
            CounterMaximumSpec("dream", True)
        malformed = maximum_fragment("dream", 7)
        malformed["value"] = {
            **dict(malformed["value"]),
            "unexpected": "field",
        }
        with self.assertRaisesRegex(
            AbilityFragmentError,
            "closed schema",
        ):
            ability_fragment_from_dict(malformed)

        unsupported = (
            "Another permanent can't have more than seven dream counters on it.",
            "Rasputin can't have more than X dream counters on it.",
            "Rasputin can't have more than seven dream counters on it. Draw a card.",
            "Rasputin can't have more than seven counters on it.",
        )
        for text in unsupported:
            with self.subTest(text=text):
                self.assertIsNone(
                    parse_fixed_self_counter_maximum(
                        text,
                        source_name="Rasputin Dreamweaver",
                    )
                )
                compiled = compile_oracle_card(
                    maximum_record(text),
                    capability_registry=self.capabilities,
                    capability_profile="commander_review",
                )
                self.assertNotEqual("exact", compiled.status)
                self.assertTrue(compiled.material_residuals)

    def test_blocked_removal_dependency_prevents_trusted_program(self):
        registry_value = json.loads(
            (ROOT / "quorune" / "rules" / "capability-registry.json")
            .read_text(encoding="utf-8")
        )
        dependency = next(
            row
            for row in registry_value["capabilities"]
            if row["id"] == "counter.removal.rule_generated"
        )
        dependency["status"] = "blocked"
        dependency["blockers"] = ["test dependency mutation"]
        registry = CapabilityRegistry(copy.deepcopy(registry_value))
        registry.mark_evidence_verified("0" * 64)

        compiled = compile_oracle_card(
            maximum_record(),
            capability_registry=registry,
            capability_profile="commander_review",
        )
        self.assertNotEqual("exact", compiled.status)
        self.assertTrue(compiled.material_residuals)

    def test_malformed_snapshot_rolls_back_before_any_removal(self):
        snapshots = (
            PermanentSnapshot(
                "valid",
                counters={"dream": 9},
                counter_maximums={"dream": 7},
            ),
            PermanentSnapshot(
                "malformed",
                counters={"charge": 4},
                counter_maximums={"charge": True},
            ),
        )
        with self.assertRaisesRegex(ValueError, "integer value"):
            evaluate_permanent_state_based_actions(snapshots)
        self.assertEqual({"dream": 9}, snapshots[0].counters)
        self.assertEqual({"charge": 4}, snapshots[1].counters)


class CounterMaximumRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db, cls.mishra, cls.zimone = load_assets()

    @classmethod
    def tearDownClass(cls):
        cls.db.close()

    def session(self, seed: int, *, players: int = 4):
        session = make_session(
            self.db,
            self.mishra,
            self.zimone,
            seed=seed,
            players=players,
        )
        keep_all(session)
        session.engine.permissions.invalidate_current()
        session.engine.state.pending_decision = None
        session.engine.state.priority_player = None
        return session

    @staticmethod
    def token(engine, seat: str, ref: str, *fragments: dict[str, object]):
        ref = engine.create_token(
            seat,
            name=ref,
            characteristics={
                "type_line": "Token Creature — Test",
                "oracle_text": (
                    "Display text says this object can't have more than "
                    "ninety-nine counters on it."
                ),
                "ability_fragments": list(fragments),
                "power": "2",
                "toughness": "2",
            },
        )[0]
        return engine._resolve_object(
            seat,
            ref,
            zones={"battlefield"},
        )

    def test_runtime_uses_typed_fragment_not_display_text(self):
        engine = self.session(7045101, players=2).engine
        subject = self.token(
            engine,
            "A",
            "Typed Maximum",
            maximum_fragment("dream", 2),
        )
        subject.counters["dream"] = 5

        self.assertFalse(engine._stabilize())
        self.assertEqual(2, subject.counters["dream"])

    def test_strictest_current_grant_combines_with_opposing_pair_removal(self):
        engine = self.session(7045102, players=2).engine
        subject = self.token(
            engine,
            "A",
            "Layered Maximum",
            maximum_fragment("+1/+1", 7),
        )
        subject.annotations["granted_ability_fragments"] = [
            maximum_fragment("+1/+1", 2)
        ]
        subject.counters.update({"+1/+1": 10, "-1/-1": 4})

        self.assertFalse(engine._stabilize())
        self.assertEqual(2, subject.counters["+1/+1"])
        self.assertNotIn("-1/-1", subject.counters)

    def test_copy_and_layer_grant_use_current_typed_fragments(self):
        engine = self.session(7045103, players=2).engine
        subject = self.token(
            engine,
            "A",
            "Copy Maximum",
            maximum_fragment("dream", 7),
        )
        subject.annotations["copy_overrides"] = {
            "name": "Copied Maximum",
            "type_line": "Creature — Shapeshifter",
            "ability_fragments": [maximum_fragment("dream", 5)],
        }
        subject.annotations["granted_ability_fragments"] = [
            maximum_fragment("dream", 3)
        ]
        subject.counters["dream"] = 8

        self.assertFalse(engine._stabilize())
        self.assertEqual(3, subject.counters["dream"])

    def test_four_player_maximum_batch_replays_exactly(self):
        session = self.session(7045104)
        engine = session.engine
        subjects = [
            self.token(
                engine,
                seat,
                f"Maximum {seat}",
                maximum_fragment("dream", 7),
            )
            for seat in ("A", "C")
        ]
        for subject in subjects:
            subject.counters["dream"] = 7

        program = SemanticProgram(
            key="test:four-player-counter-maximum",
            label="Exceed two represented counter maximums",
            effects=[
                {
                    "op": "counter",
                    "card": subject.object_id,
                    "counter": "dream",
                    "delta": 2,
                }
                for subject in subjects
            ],
            trust_level="provisional",
        )
        engine.semantics.put(program)
        engine.state.stack.append(
            StackItem(
                stack_id="four-player-counter-maximum",
                ref="S-four-player-counter-maximum",
                kind="triggered",
                controller="A",
                label=program.label,
                source_object_id=subjects[0].object_id,
                semantic_key=program.key,
                visibility=list(engine.seats),
            )
        )
        engine.state.active_player = "A"
        engine.state.phase = "precombat_main"
        engine.state.step = "main"
        engine._grant_priority("A")
        engine._issue_priority("A")
        session.initial_checkpoint = checkpoint_envelope(engine.state)
        session.commands.clear()
        session.decisions.clear()

        for seat in ("A", "B", "C", "D"):
            result = session.act(
                f"pilot:{seat}",
                {
                    "action_id": "pass",
                    "reason": "Resolve the maximum-counter fixture.",
                    "plan": "HOLD",
                },
            )
            self.assertTrue(result.ok, result.summary)
        self.assertEqual([7, 7], [card.counters["dream"] for card in subjects])
        event = next(
            event
            for event in reversed(engine.state.events)
            if event.code == "state.counter_maximums"
        )
        self.assertEqual(
            [
                subject.ref
                for subject in sorted(
                    subjects,
                    key=lambda value: value.object_id,
                )
            ],
            [change["object"] for change in event.details["changes"]],
        )

        expected_hash = authoritative_state_hash(engine.state)
        with tempfile.TemporaryDirectory() as temporary:
            record_dir = Path(temporary) / "counter-maximum-record"
            session.save(record_dir)
            replay = replay_record(record_dir, self.db, verify=True)
        self.assertTrue(replay["ok"], replay)
        self.assertEqual(expected_hash, replay["final_state_hash"])

    def test_compiler_and_snapshot_consumer_mutations_are_killed(self):
        def assert_compiled() -> None:
            compiled = compile_oracle_card(
                maximum_record(),
                capability_registry=load_default_capability_registry(),
                capability_profile="commander_review",
            )
            self.assertEqual("exact", compiled.status)
            self.assertEqual(
                "ability.static.counter-maximum.v1",
                compiled.faces[0].nodes[0].handlers[0]["handler_id"],
            )

        assert_compiled()
        with patch(
            "quorune.compiler.runtime_templates.static_counter_maximum_handler",
            return_value=None,
        ):
            with self.assertRaises(AssertionError):
                assert_compiled()

        def assert_runtime(seed: int) -> None:
            engine = self.session(seed, players=2).engine
            subject = self.token(
                engine,
                "A",
                f"Mutation Maximum {seed}",
                maximum_fragment("dream", 2),
            )
            subject.counters["dream"] = 5
            engine._stabilize()
            self.assertEqual(2, subject.counters["dream"])

        assert_runtime(7045110)
        with patch(
            "quorune.state_based_execution.counter_maximum_values",
            return_value={},
        ):
            with self.assertRaises(AssertionError):
                assert_runtime(7045111)


if __name__ == "__main__":
    unittest.main()
