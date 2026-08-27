from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from common import ROOT, keep_all, make_session
from quorune.carddb import CardDatabase
from quorune.compiler.counter_placement_templates import (
    CounterPlacementSubject,
    FixedCounterPlacementBatchTemplate,
    FixedCounterPlacementTemplate,
    fixed_counter_placement_batch_effect_template,
)
from quorune.deck import DeckLoader
from quorune.errors import GameRuleError
from quorune.model import CardInstance, StackItem
from quorune.oracle_ir import compile_oracle_card, generated_programs
from quorune.projection import StateProjector
from quorune.record import (
    authoritative_state_hash,
    checkpoint_envelope,
    replay_record,
)
from quorune.replacement_effects import (
    ReplacementContinuation,
    ReplacementEffectError,
)
from quorune.rules.capabilities import (
    CapabilityRegistry,
    capability_dependencies_for_node,
    load_default_capability_registry,
)
from quorune.semantic_runtime import (
    CounterPlacementAmount,
    PlaceCounterBatchIntent,
    ReadOnlyHandlerContext,
    ReadOnlyRulesQuery,
)
from quorune.semantic_runtime.context import SemanticNodeError
from quorune.semantic_runtime.counter_placement_handlers import (
    FixedCounterPlacementBatchHandler,
)
from quorune.semantic_runtime.executor import execute_intent_plan
from quorune.semantic_choices.intent_replacement import (
    semantic_intent_identity,
    validate_semantic_intent_identity,
)
from quorune.semantic_choices.model import SemanticChoiceError
from quorune.semantics import SemanticProgram
from scripts.build_test_database import build_fixture_database


REGISTRY_PATH = ROOT / "quorune" / "rules" / "capability-registry.json"


def focused_card_database(directory: str) -> CardDatabase:
    database = Path(directory) / "fixed-multikind-counters.sqlite3"
    build_fixture_database(
        [
            ROOT / "tests" / "fixtures" / "scryfall-exact-lists.json",
            ROOT / "tests" / "fixtures" / "counter-replacement-cards.json",
        ],
        database,
    )
    return CardDatabase(database)


class FixedCounterBatchCompilerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.db = focused_card_database(cls.temporary.name)
        cls.base = cls.db.lookup("Sol Ring")
        cls.capabilities = load_default_capability_registry()

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temporary.cleanup()

    def compile(self, text: str, *, type_line: str = "Sorcery"):
        return compile_oracle_card(
            replace(
                self.base,
                name="Fixture",
                oracle_text=text,
                type_line=type_line,
                keywords=(),
                faces=(),
            ),
            capability_registry=self.capabilities,
            capability_profile="commander_review",
        )

    def test_spell_trigger_and_activated_contexts_share_fixed_counter_batch_lowering(
        self,
    ):
        contexts = (
            (
                "Put a +1/+1 counter and a lifelink counter on target creature.",
                "Sorcery",
                "spell_ability",
                "$target.0",
                (("+1/+1", 1), ("lifelink", 1)),
            ),
            (
                "When this creature enters, put a +1/+1 counter and a flying counter on this creature.",
                "Creature — Human",
                "triggered_ability",
                "$source",
                (("+1/+1", 1), ("flying", 1)),
            ),
            (
                "{2}, {T}: Put a charge counter and a flying counter on target artifact you control.",
                "Artifact",
                "activated_ability",
                "$target.0",
                (("charge", 1), ("flying", 1)),
            ),
        )
        for text, type_line, kind, card, placements in contexts:
            with self.subTest(kind=kind):
                ir = self.compile(text, type_line=type_line)
                node = next(
                    value
                    for value in ir.faces[0].nodes
                    if value.template_id
                    and value.template_id.startswith(
                        "place-fixed-counter-batch-"
                    )
                )
                self.assertEqual("exact", ir.status)
                self.assertEqual(kind, node.kind)
                self.assertEqual("place_counter_batch", node.effects[0]["op"])
                self.assertEqual(card, node.effects[0]["card"])
                self.assertEqual(
                    [
                        {"counter": counter, "amount": amount}
                        for counter, amount in placements
                    ],
                    node.effects[0]["placements"],
                )
                self.assertIn(
                    "counter.producer.fixed_multikind_effect",
                    node.capability_dependencies,
                )
                if card == "$target.0":
                    self.assertIn(
                        "target.revalidate_resolution",
                        node.capability_dependencies,
                    )
                self.assertEqual(text, text[node.span.start : node.span.end])

    def test_fixed_counter_batch_lowers_to_capability_closed_card_program(self):
        record = replace(
            self.base,
            name="Fixture",
            oracle_text=(
                "Put a +1/+1 counter and a lifelink counter on target "
                "creature."
            ),
            type_line="Sorcery",
            keywords=(),
            faces=(),
        )

        programs = generated_programs(
            self.db,
            record,
            trust_level="trusted",
            capability_registry=self.capabilities,
            capability_profile="commander_review",
        )

        self.assertEqual(1, len(programs))
        program = programs[0]
        self.assertEqual("trusted", program.trust_level)
        self.assertFalse(program.requires_arbiter)
        self.assertEqual(
            [
                "counter.characteristic.keyword",
                "counter.producer.fixed_multikind_effect",
                "damage.result.lifelink",
                "target.revalidate_resolution",
            ],
            program.capability_dependencies,
        )
        self.assertIsNotNone(program.capability_closure)
        assert program.capability_closure is not None
        self.assertTrue(program.capability_closure["trusted"])
        self.assertEqual(
            "capability_closure_verified",
            program.provenance["review_status"],
        )
        self.assertEqual(
            [
                {
                    "op": "place_counter_batch",
                    "card": "$target.0",
                    "placements": [
                        {"counter": "+1/+1", "amount": 1},
                        {"counter": "lifelink", "amount": 1},
                    ],
                    "source": "$source",
                }
            ],
            program.effects,
        )

    def test_two_and_three_kind_templates_preserve_printed_order(self):
        for text, expected in (
            (
                "Put two charge counters and a flying counter on target artifact.",
                (("charge", 2), ("flying", 1)),
            ),
            (
                "Put a +1/+1 counter, a menace counter, and a lifelink counter on Fixture.",
                (("+1/+1", 1), ("menace", 1), ("lifelink", 1)),
            ),
        ):
            with self.subTest(text=text):
                template = fixed_counter_placement_batch_effect_template(
                    text,
                    card_name="Fixture",
                )
                self.assertIsNotNone(template)
                assert template is not None
                self.assertEqual(expected, template.placements)
                self.assertEqual(
                    [name for name, _amount in expected],
                    [row["counter"] for row in template.effects[0]["placements"]],
                )

    def test_batch_template_is_deterministic_for_closed_fixed_amounts(self):
        for first in range(1, 11):
            for second in range(1, 4):
                first_plural = "counter" if first == 1 else "counters"
                second_plural = "counter" if second == 1 else "counters"
                text = (
                    f"Put {first} charge {first_plural} and {second} flying "
                    f"{second_plural} on target artifact."
                )
                with self.subTest(first=first, second=second):
                    left = fixed_counter_placement_batch_effect_template(
                        text,
                        card_name="Fixture",
                    )
                    right = fixed_counter_placement_batch_effect_template(
                        text,
                        card_name="Fixture",
                    )
                    self.assertIsNotNone(left)
                    self.assertEqual(left, right)
                    assert left is not None
                    self.assertEqual(
                        (("charge", first), ("flying", second)),
                        left.placements,
                    )
                    self.assertEqual(left.compiled(), right.compiled())

    def test_unsupported_fixed_counter_batch_variants_remain_material_residuals(
        self,
    ):
        texts = (
            "Put up to one +1/+1 counter and a lifelink counter on target creature.",
            "Put X charge counters and a flying counter on target artifact.",
            "Put a +1/+1 counter and another +1/+1 counter on target creature.",
            "Put a +1/+1 counter on target creature and a flying counter on another target creature.",
            "Put a +1/+1 counter, a flying counter, a vigilance counter, and a lifelink counter on target creature.",
            "Put a +1/+1 counter and a flying counter on target modified creature.",
        )
        for text in texts:
            with self.subTest(text=text):
                self.assertIsNone(
                    fixed_counter_placement_batch_effect_template(
                        text,
                        card_name="Fixture",
                    )
                )
                ir = self.compile(text)
                self.assertNotEqual("exact", ir.status)
                self.assertTrue(ir.material_residuals)

    def test_batch_shape_and_dependency_mutants_fail_closed(self):
        template = FixedCounterPlacementBatchTemplate(
            placements=(("+1/+1", 1), ("lifelink", 1)),
            subject_template=FixedCounterPlacementTemplate(
                count=1,
                counter_name="+1/+1",
                subject=CounterPlacementSubject.TARGET,
                permanent_type="creature",
            ),
        )
        expected = {
            "counter.characteristic.keyword",
            "counter.producer.fixed_multikind_effect",
            "damage.result.lifelink",
            "target.revalidate_resolution",
        }
        self.assertEqual(
            expected,
            set(
                capability_dependencies_for_node(
                    effects=template.effects,
                    target_schema=template.target_schema,
                    mechanic_ids=template.mechanics,
                )
            ),
        )
        effect = template.effects[0]
        for mutant in (
            {**effect, "placements": [{"counter": "+1/+1", "amount": True}, *effect["placements"][1:]]},
            {**effect, "placements": [*effect["placements"], effect["placements"][0]]},
            {**effect, "placements": effect["placements"][:1]},
            {**effect, "card": "$target.1"},
            {**effect, "extra": True},
        ):
            with self.subTest(mutant=mutant):
                self.assertNotIn(
                    "counter.producer.fixed_multikind_effect",
                    capability_dependencies_for_node(
                        effects=(mutant,),
                        target_schema=template.target_schema,
                        mechanic_ids=template.mechanics,
                    ),
                )

    def test_fixed_counter_batch_compiler_template_mutant_is_killed(self):
        def exact() -> None:
            self.assertEqual(
                "exact",
                self.compile(
                    "Put a +1/+1 counter and a lifelink counter on target creature."
                ).status,
            )

        exact()
        with patch(
            "quorune.compiler.resolution_effect_templates."
            "fixed_counter_placement_batch_effect_template",
            return_value=None,
        ):
            with self.assertRaises(AssertionError):
                exact()

        value = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
        dependency = next(
            row
            for row in value["capabilities"]
            if row["id"] == "counter.placement.quantity_replacement"
        )
        dependency["status"] = "blocked"
        dependency["blockers"] = ["test mutation"]
        ir = compile_oracle_card(
            replace(
                self.base,
                name="Fixture",
                oracle_text="Put a +1/+1 counter and a lifelink counter on target creature.",
                type_line="Sorcery",
                keywords=(),
                faces=(),
            ),
            capability_registry=CapabilityRegistry(value),
            capability_profile="commander_review",
        )
        self.assertNotEqual("exact", ir.status)


class FixedCounterBatchRuntimeTests(unittest.TestCase):
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

    def add_permanent(self, engine, *, seat: str, name: str, ref: str):
        record = self.db.lookup(name)
        card = CardInstance(
            object_id=f"fixture:{ref}",
            ref=ref,
            oracle_id=record.oracle_id,
            printed_name=record.name,
            owner=seat,
            controller=seat,
            zone="battlefield",
            zone_timestamp=engine.state.event_sequence + 1,
            known_to=list(engine.seats),
            revealed_to=list(engine.seats),
        )
        engine.state.cards[card.object_id] = card
        engine.state.players[seat].zones["battlefield"].append(card.object_id)
        return card

    @staticmethod
    def context() -> ReadOnlyHandlerContext:
        return ReadOnlyHandlerContext(
            actor="A",
            default_reason="Fixed counter batch fixture",
            query=ReadOnlyRulesQuery(
                seats=("A", "B", "C", "D"),
                active_seats=("A", "B", "C", "D"),
                apnap_order=("A", "B", "C", "D"),
            ),
        )

    def plan(self, target_ref: str, source_ref: str):
        return FixedCounterPlacementBatchHandler().lower(
            {
                "op": "place_counter_batch",
                "card": target_ref,
                "placements": [
                    {"counter": "+1/+1", "amount": 1},
                    {"counter": "lifelink", "amount": 1},
                ],
                "source": source_ref,
            },
            self.context(),
        )

    def test_runtime_commits_fixed_counter_batch_atomically(self):
        session = self.session(12260901)
        engine = session.engine
        target = self.add_permanent(
            engine, seat="A", name="Island", ref="batch-target"
        )
        source = self.add_permanent(
            engine, seat="A", name="Doubling Season", ref="batch-source"
        )
        plan = self.plan(target.ref, source.ref)
        self.assertEqual(
            (
                PlaceCounterBatchIntent(
                    actor="A",
                    object_ref=target.ref,
                    placements=(
                        CounterPlacementAmount("+1/+1", 1),
                        CounterPlacementAmount("lifelink", 1),
                    ),
                    reason="Fixed counter batch fixture",
                    source_ref=source.ref,
                ),
            ),
            plan.intents,
        )
        execute_intent_plan(engine, plan)
        self.assertEqual(2, target.counters["+1/+1"])
        self.assertEqual(2, target.counters["lifelink"])

    def test_typed_counter_batch_rejects_malformed_entries_without_mutation(self):
        valid = {
            "op": "place_counter_batch",
            "card": "target",
            "placements": [
                {"counter": "+1/+1", "amount": 1},
                {"counter": "lifelink", "amount": 1},
            ],
            "source": "source",
        }
        for effect in (
            {**valid, "placements": valid["placements"][:1]},
            {**valid, "placements": [valid["placements"][0]] * 2},
            {**valid, "placements": [{"counter": "+1/+1", "amount": True}, valid["placements"][1]]},
            {**valid, "placements": [{"counter": "", "amount": 1}, valid["placements"][1]]},
            {**valid, "placements": [{"counter": "+1/+1", "amount": 1, "extra": True}, valid["placements"][1]]},
            {**valid, "unknown": True},
        ):
            with self.subTest(effect=effect):
                with self.assertRaises(SemanticNodeError):
                    FixedCounterPlacementBatchHandler().lower(
                        effect,
                        self.context(),
                    )

        caller_owned = {
            **valid,
            "placements": [dict(value) for value in valid["placements"]],
        }
        plan = FixedCounterPlacementBatchHandler().lower(
            caller_owned,
            self.context(),
        )
        caller_owned["placements"][0]["counter"] = "poison"
        caller_owned["placements"][1]["amount"] = 99
        intent = plan.intents[0]
        self.assertIsInstance(intent, PlaceCounterBatchIntent)
        self.assertEqual(
            (
                CounterPlacementAmount("+1/+1", 1),
                CounterPlacementAmount("lifelink", 1),
            ),
            intent.placements,
        )

    def test_counter_batch_continuation_identity_is_strict_and_canonical(self):
        intent = PlaceCounterBatchIntent(
            actor="A",
            object_ref="target",
            placements=(
                CounterPlacementAmount("+1/+1", 1),
                CounterPlacementAmount("lifelink", 1),
            ),
            reason="Counter batch identity",
            source_ref="source",
        )
        kind, identity = semantic_intent_identity(intent)
        self.assertEqual("place_counter_batch", kind)
        self.assertEqual(
            identity,
            validate_semantic_intent_identity(kind, identity),
        )
        for malformed in (
            {**identity, "unknown": True},
            {key: value for key, value in identity.items() if key != "source_ref"},
            {**identity, "placements": [{"counter": "+1/+1", "amount": True}, identity["placements"][1]]},
            {**identity, "placements": [identity["placements"][0]] * 2},
        ):
            with self.subTest(malformed=malformed):
                with self.assertRaises(SemanticChoiceError):
                    validate_semantic_intent_identity(kind, malformed)

    def test_stale_counter_batch_target_fails_before_mutation(self):
        session = self.session(12260902)
        engine = session.engine
        target = self.add_permanent(
            engine, seat="A", name="Island", ref="stale-batch-target"
        )
        plan = self.plan(target.ref, "departed-source")
        engine.state.players["A"].zones["battlefield"].remove(target.object_id)
        engine.state.players["A"].zones["graveyard"].append(target.object_id)
        target.zone = "graveyard"
        before = authoritative_state_hash(engine.state)
        with self.assertRaises(GameRuleError):
            execute_intent_plan(engine, plan)
        self.assertEqual(before, authoritative_state_hash(engine.state))
        self.assertEqual({}, target.counters)

    def test_counter_batch_result_does_not_require_source_to_remain(self):
        session = self.session(12260905)
        target = self.add_permanent(
            session.engine,
            seat="A",
            name="Island",
            ref="source-left-batch-target",
        )
        execute_intent_plan(
            session.engine,
            self.plan(target.ref, "source-that-left"),
        )
        self.assertEqual(1, target.counters["+1/+1"])
        self.assertEqual(1, target.counters["lifelink"])

    def _stage_replacement_batch(self, *, players: int, seed: int):
        session = self.session(seed, players=players)
        engine = session.engine
        target = self.add_permanent(
            engine, seat="A", name="Island", ref=f"batch-target-{seed}"
        )
        self.add_permanent(
            engine, seat="A", name="Doubling Season", ref=f"doubling-{seed}"
        )
        self.add_permanent(
            engine,
            seat="A",
            name="Doc Samson, Super Psychiatrist",
            ref=f"doc-{seed}",
        )
        program = SemanticProgram(
            key=f"fixture:counter-batch-{seed}",
            label="Fixed counter batch",
            effects=[
                {
                    "op": "place_counter_batch",
                    "card": target.ref,
                    "placements": [
                        {"counter": "+1/+1", "amount": 1},
                        {"counter": "lifelink", "amount": 1},
                    ],
                    "source": target.ref,
                }
            ],
            trust_level="provisional",
        )
        engine.semantics.put(program)
        engine.state.stack.append(
            StackItem(
                stack_id=f"counter-batch-{seed}",
                ref=f"S-counter-batch-{seed}",
                kind="triggered_ability",
                controller="A",
                label=program.label,
                semantic_key=program.key,
                visibility=list(engine.seats),
            )
        )
        return session, target

    def test_counter_batch_suspends_before_any_counter_is_placed(self):
        session, target = self._stage_replacement_batch(
            players=2, seed=12260903
        )
        engine = session.engine
        item = engine.state.stack[-1]
        engine._continue_resolution(
            stack_ref=item.ref,
            effects=[dict(value) for value in engine.semantics.get(item.semantic_key).effects],
            destination=None,
            note="",
        )
        self.assertEqual("replacement.order", engine.state.pending_decision.kind)
        self.assertEqual({}, target.counters)
        raw_continuation = engine.state.pending_decision.continuation
        ReplacementContinuation.from_dict(raw_continuation)
        intent = self.plan(target.ref, target.ref).intents[0]
        kind, identity = semantic_intent_identity(intent)
        synthetic = {
            "replacement_resume_kind": "semantic_intent_completion",
            "semantic_choice_continuation": {"schema_version": 1},
            "semantic_choice_actor": "A",
            "semantic_choice_response": {},
            "intent_index": 0,
            "semantic_intent_kind": kind,
            "semantic_intent": identity,
            "replacement_selections": [],
            "replacement_batch": raw_continuation["replacement_batch"],
            "replacement_effects": raw_continuation["replacement_effects"],
        }
        restored = ReplacementContinuation.from_dict(synthetic)
        self.assertEqual("place_counter_batch", restored.semantic_intent_kind)
        tampered = dict(synthetic)
        tampered["semantic_intent_kind"] = "future_counter_batch"
        with self.assertRaises(ReplacementEffectError):
            ReplacementContinuation.from_dict(tampered)

    def test_four_player_counter_batch_replacement_is_seat_scoped_and_replays(
        self,
    ):
        session, target = self._stage_replacement_batch(
            players=4, seed=12260904
        )
        engine = session.engine
        engine.state.active_player = "A"
        engine.state.phase = "precombat_main"
        engine.state.step = "main"
        engine._grant_priority("A")
        engine._issue_priority("A")
        session.initial_checkpoint = checkpoint_envelope(engine.state)
        session.commands.clear()
        session.decisions.clear()

        for seat in engine.seats:
            result = session.act(f"pilot:{seat}", {"action_id": "pass"})
            self.assertTrue(result.ok, result.summary)
        self.assertEqual("replacement.order", engine.state.pending_decision.kind)
        self.assertEqual({}, target.counters)
        projector = StateProjector(self.db, engine.state)
        projected = projector._decision("pilot:A")
        self.assertIsNotNone(projected)
        for seat in ("B", "C", "D"):
            self.assertIsNone(projector._decision(f"pilot:{seat}"))
        self.assertNotIn(target.object_id, json.dumps(projected, sort_keys=True))
        replacement_choices = 0
        while (
            engine.state.pending_decision is not None
            and engine.state.pending_decision.kind == "replacement.order"
        ):
            projected = StateProjector(self.db, engine.state)._decision(
                "pilot:A"
            )
            self.assertIsNotNone(projected)
            selected = projected["ctx"]["options"][0]["id"]
            result = session.act(
                "pilot:A",
                {
                    "action_id": "choose",
                    "choices": {"replacement": selected},
                },
            )
            self.assertTrue(result.ok, result.summary)
            replacement_choices += 1
            if (
                engine.state.pending_decision is not None
                and engine.state.pending_decision.kind == "replacement.order"
            ):
                self.assertEqual({}, target.counters)
        self.assertEqual(2, replacement_choices)
        expected_hash = authoritative_state_hash(engine.state)
        self.assertIn("+1/+1", target.counters)
        self.assertIn("lifelink", target.counters)

        with tempfile.TemporaryDirectory() as temporary:
            record_dir = Path(temporary) / "fixed-counter-batch-replay"
            session.save(record_dir)
            replay = replay_record(record_dir, self.db, verify=True)
        self.assertTrue(replay["ok"], replay)
        self.assertEqual(len(session.commands), replay["commands"])
        self.assertEqual(expected_hash, replay["final_state_hash"])


if __name__ == "__main__":
    unittest.main()
