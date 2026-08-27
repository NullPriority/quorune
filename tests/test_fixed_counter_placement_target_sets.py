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
    FixedCounterPlacementTargetSetTemplate,
    fixed_counter_placement_target_set_effect_template,
)
from quorune.counter_placement_targets import (
    CounterPlacementTargetSetError,
    snapshot_counter_placement_targets,
)
from quorune.deck import DeckLoader
from quorune.model import CardInstance, StackItem
from quorune.object_query import ObjectQueryResult
from quorune.oracle_ir import compile_oracle_card, register_generated_programs
from quorune.projection import StateProjector
from quorune.record import (
    authoritative_state_hash,
    checkpoint_envelope,
    replay_record,
)
from quorune.rules.capabilities import (
    CapabilityRegistry,
    capability_dependencies_for_node,
    load_default_capability_registry,
)
from quorune.semantic_runtime import (
    PlaceCountersOnTargetsIntent,
    ReadOnlyHandlerContext,
    ReadOnlyRulesQuery,
)
from quorune.semantic_runtime.context import SemanticNodeError
from quorune.semantic_runtime.counter_placement_handlers import (
    FixedCounterPlacementTargetSetHandler,
)
from quorune.semantic_runtime.executor import execute_intent_plan
from quorune.semantics import SemanticProgram
from scripts.build_test_database import build_fixture_database


REGISTRY_PATH = ROOT / "quorune" / "rules" / "capability-registry.json"


def focused_card_database(directory: str) -> CardDatabase:
    database = Path(directory) / "fixed-counter-placement-target-sets.sqlite3"
    build_fixture_database(
        [
            ROOT / "tests" / "fixtures" / "scryfall-exact-lists.json",
            ROOT / "tests" / "fixtures" / "counter-replacement-cards.json",
            ROOT / "tests" / "fixtures" / "optional-counter-targets.json",
        ],
        database,
    )
    return CardDatabase(database)


def _row(ref: str, *, controller: str) -> ObjectQueryResult:
    return ObjectQueryResult(
        object_id=f"object:{ref}",
        logical_object_id=f"logical:{ref}",
        ref=ref,
        printed_name=ref,
        owner=controller,
        controller=controller,
        zone="battlefield",
        types=("creature",),
    )


class _TargetQuery:
    def __init__(self, rows: tuple[ObjectQueryResult, ...]):
        self.rows = rows

    def counter_target_active_seats(self) -> tuple[str, ...]:
        return ("A", "B", "C", "D")

    def counter_target_apnap_order(self) -> tuple[str, ...]:
        return ("C", "D", "A", "B")

    def counter_target_object_rows(
        self,
        actor: str,
        refs: tuple[str, ...],
    ) -> tuple[ObjectQueryResult, ...]:
        if actor != "A":
            raise AssertionError("Unexpected actor")
        by_ref = {row.ref: row for row in self.rows}
        return tuple(by_ref[ref] for ref in refs if ref in by_ref)


class FixedCounterPlacementTargetSetModelTests(unittest.TestCase):
    def test_snapshot_is_immutable_and_canonical_across_input_order(self):
        rows = (
            _row("b-target", controller="B"),
            _row("d-target", controller="D"),
            _row("c-target", controller="C"),
        )
        first = snapshot_counter_placement_targets(
            _TargetQuery(rows),
            actor="A",
            refs=("b-target", "d-target", "c-target"),
            maximum_targets=3,
        )
        second = snapshot_counter_placement_targets(
            _TargetQuery(tuple(reversed(rows))),
            actor="A",
            refs=("c-target", "b-target", "d-target"),
            maximum_targets=3,
        )
        self.assertEqual(first, second)
        self.assertEqual(first.fingerprint, second.fingerprint)
        self.assertEqual(
            ["c-target", "d-target", "b-target"],
            [value.ref for value in first.permanents],
        )

    def test_snapshot_rejects_malformed_target_sets(self):
        query = _TargetQuery((_row("one", controller="B"),))
        for refs, maximum in (
            (("one", "one"), 2),
            (("one",), True),
            (("one",), 0),
            (("one", "missing"), 2),
            (("one", "missing"), 1),
        ):
            with self.subTest(refs=refs, maximum=maximum):
                with self.assertRaises(CounterPlacementTargetSetError):
                    snapshot_counter_placement_targets(
                        query,
                        actor="A",
                        refs=refs,
                        maximum_targets=maximum,
                    )


class FixedCounterPlacementTargetSetCompilerTests(unittest.TestCase):
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

    def test_spell_trigger_and_activated_contexts_share_target_set_lowering(
        self,
    ):
        contexts = (
            (
                "Put a +1/+1 counter on each of up to two target creatures.",
                "Sorcery",
                "spell_ability",
                2,
                {"types_any": ["creature"]},
            ),
            (
                "When this creature enters, put a +1/+1 counter on each of up to two target creatures you control.",
                "Creature — Human",
                "triggered_ability",
                2,
                {
                    "types_any": ["creature"],
                    "controller_relation": "you",
                },
            ),
            (
                "{2}, {T}: Put a charge counter on each of up to three target noncreature artifacts.",
                "Artifact",
                "activated_ability",
                3,
                {
                    "types_any": ["artifact"],
                    "types_none": ["creature"],
                },
            ),
            (
                "+1: Put a +1/+1 counter on up to one target creature.",
                "Legendary Planeswalker — Adept",
                "activated_ability",
                1,
                {"types_any": ["creature"]},
            ),
            (
                "Put a stun counter on up to one target tapped creature.",
                "Sorcery",
                "spell_ability",
                1,
                {
                    "types_any": ["creature"],
                    "state_predicate": {
                        "entered_this_turn": False,
                        "tapped": True,
                        "counter_name": None,
                        "minimum_counter_count": None,
                    },
                },
            ),
        )
        for text, type_line, kind, maximum, predicates in contexts:
            with self.subTest(text=text):
                ir = self.compile(text, type_line=type_line)
                node = next(
                    value
                    for value in ir.faces[0].nodes
                    if value.template_id
                    and value.template_id.startswith(
                        "place-fixed-counter-target-set-"
                    )
                )
                self.assertEqual("exact", ir.status)
                self.assertTrue(node.exact)
                self.assertEqual(kind, node.kind)
                self.assertEqual(
                    "place_counters_on_targets",
                    node.effects[0]["op"],
                )
                self.assertEqual("$targets", node.effects[0]["cards"])
                self.assertEqual(maximum, node.target_schema["up_to"])
                self.assertTrue(
                    predicates.items() <= node.target_schema.items()
                )
                self.assertIn(
                    "counter.producer.fixed_permanent_target_set_effect",
                    node.capability_dependencies,
                )
                self.assertIn(
                    "target.revalidate_resolution",
                    node.capability_dependencies,
                )
                if "state_predicate" in predicates:
                    self.assertIn(
                        "state_query.permanent.public_state_predicate",
                        node.capability_dependencies,
                    )
                if text.startswith("+1:"):
                    self.assertIn(
                        "activation.loyalty.positive_counter_cost",
                        node.capability_dependencies,
                    )
                self.assertEqual(text, text[node.span.start : node.span.end])

    def test_target_set_shape_and_dependency_mutants_fail_closed(self):
        template = fixed_counter_placement_target_set_effect_template(
            "Put a +1/+1 counter on each of up to two target creatures."
        )
        self.assertIsInstance(
            template, FixedCounterPlacementTargetSetTemplate
        )
        assert template is not None
        self.assertEqual(
            {
                "counter.producer.fixed_permanent_target_set_effect",
                "target.revalidate_resolution",
            },
            set(
                capability_dependencies_for_node(
                    effects=template.effects,
                    target_schema=template.target_schema,
                    mechanic_ids=template.mechanics,
                )
            ),
        )
        for effect in (
            {**template.effects[0], "maximum_targets": True},
            {**template.effects[0], "maximum_targets": 0},
            {**template.effects[0], "amount": True},
            {**template.effects[0], "cards": "$target.0"},
            {**template.effects[0], "unknown": True},
        ):
            with self.subTest(effect=effect):
                self.assertFalse(
                    capability_dependencies_for_node(
                        effects=(effect,),
                        target_schema=template.target_schema,
                        mechanic_ids=template.mechanics,
                    )
                )
        for schema in (
            {**template.target_schema, "types_any": []},
            {**template.target_schema, "types_none": []},
            {**template.target_schema, "types_any": ["creature", "land"]},
            {**template.target_schema, "controller_relation": "teammate"},
            {**template.target_schema, "unknown": True},
        ):
            with self.subTest(schema=schema):
                self.assertFalse(
                    capability_dependencies_for_node(
                        effects=template.effects,
                        target_schema=schema,
                        mechanic_ids=template.mechanics,
                    )
                )
        for dependency_id in (
            "counter.placement.quantity_replacement",
            "target.revalidate_resolution",
        ):
            value = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
            dependency = next(
                row
                for row in value["capabilities"]
                if row["id"] == dependency_id
            )
            dependency["status"] = "blocked"
            dependency["blockers"] = ["test mutation"]
            ir = compile_oracle_card(
                replace(
                    self.base,
                    name="Fixture",
                    oracle_text=(
                        "Put a +1/+1 counter on each of up to two target creatures."
                    ),
                    type_line="Sorcery",
                    keywords=(),
                    faces=(),
                ),
                capability_registry=CapabilityRegistry(value),
                capability_profile="commander_review",
            )
            self.assertNotEqual("exact", ir.status)
            self.assertTrue(ir.material_residuals)

        value = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
        dependency = next(
            row
            for row in value["capabilities"]
            if row["id"] == "state_query.permanent.public_state_predicate"
        )
        dependency["status"] = "blocked"
        dependency["blockers"] = ["test mutation"]
        blocked = compile_oracle_card(
            replace(
                self.base,
                name="Fixture",
                oracle_text=(
                    "Put a stun counter on up to one target tapped creature."
                ),
                type_line="Sorcery",
                keywords=(),
                faces=(),
            ),
            capability_registry=CapabilityRegistry(value),
            capability_profile="commander_review",
        )
        self.assertNotEqual("exact", blocked.status)
        self.assertTrue(blocked.material_residuals)

    def test_unsupported_target_set_variants_remain_material_residuals(self):
        texts = (
            "Put a +1/+1 counter on each of up to X target creatures.",
            "Put a +1/+1 counter on up to one target attacking creature.",
            "Put a +1/+1 counter on up to one target Dinosaur you control.",
            "Put a +1/+1 counter on each of up to two target attacking creatures.",
            "Put a +1/+1 counter on each of up to two target Merfolk you control.",
            "Put a +1/+1 counter on each of up to two target creatures with a counter on them.",
            "Put two +1/+1 counters on one target creature and one on another target creature.",
            "Put a +1/+1 counter on each of up to two target creatures, then untap them.",
        )
        for text in texts:
            with self.subTest(text=text):
                self.assertIsNone(
                    fixed_counter_placement_target_set_effect_template(text)
                )
                ir = self.compile(text)
                self.assertNotEqual("exact", ir.status)
                self.assertTrue(ir.material_residuals)

    def test_target_set_compiler_mutant_is_killed(self):
        def exact() -> None:
            self.assertEqual(
                "exact",
                self.compile(
                    "Put a +1/+1 counter on each of up to two target creatures."
                ).status,
            )

        exact()
        with patch(
            "quorune.compiler.resolution_effect_templates."
            "fixed_counter_placement_target_set_effect_template",
            return_value=None,
        ):
            with self.assertRaises(AssertionError):
                exact()


class FixedCounterPlacementTargetSetRuntimeTests(unittest.TestCase):
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

    def session(self, seed: int, *, players: int = 4):
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

    def add_permanent(
        self,
        engine,
        *,
        seat: str,
        name: str,
        ref: str,
    ) -> CardInstance:
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
    def context(
        *,
        apnap: tuple[str, ...] = ("A", "B", "C", "D"),
    ) -> ReadOnlyHandlerContext:
        return ReadOnlyHandlerContext(
            actor="A",
            default_reason="Fixed counter-target fixture",
            query=ReadOnlyRulesQuery(
                seats=("A", "B", "C", "D"),
                active_seats=("A", "B", "C", "D"),
                apnap_order=apnap,
            ),
        )

    @staticmethod
    def effect(
        refs: list[str] | tuple[str, ...] | str,
        *,
        maximum: int = 3,
        source: str = "departed-source",
    ) -> dict[str, object]:
        return {
            "op": "place_counters_on_targets",
            "cards": refs if isinstance(refs, str) else list(refs),
            "maximum_targets": maximum,
            "counter": "+1/+1",
            "amount": 1,
            "source": source,
        }

    @staticmethod
    def target_schema(maximum: int = 3) -> dict[str, object]:
        return {
            "zones": ["battlefield"],
            "categories": ["permanent"],
            "types_any": ["creature"],
            "up_to": maximum,
        }

    def stack_item(
        self,
        engine,
        *,
        program: SemanticProgram,
        refs: list[str],
        source: CardInstance | None = None,
    ) -> StackItem:
        item = StackItem(
            stack_id=f"stack:{program.key}",
            ref=f"S-{program.key}",
            kind="triggered_ability",
            controller="A",
            label=program.label,
            semantic_key=program.key,
            source_object_id=source.object_id if source is not None else None,
            targets=list(refs),
            visibility=list(engine.seats),
            context={
                "target_groups": {"target_0": list(refs)},
                "target_snapshots": {
                    ref: engine._target_snapshot(ref) for ref in refs
                },
                "targets_revalidated": False,
                "targets_chosen_at_creation": True,
            },
        )
        engine.state.stack.append(item)
        return item

    @staticmethod
    def pass_until(session, predicate, *, limit: int = 24) -> None:
        for _ in range(limit):
            if predicate():
                return
            principals = session.pending_principals()
            if not principals:
                raise AssertionError("Resolution stopped without a decision")
            result = session.act(principals[0], {"action_id": "pass"})
            if not result.ok:
                raise AssertionError(result.summary)
        raise AssertionError("Resolution did not reach the expected state")

    def test_runtime_canonicalizes_still_legal_targets_in_apnap_order(self):
        session = self.session(12290801)
        engine = session.engine
        engine.state.active_player = "C"
        targets = {
            seat: self.add_permanent(
                engine,
                seat=seat,
                name="Scute Swarm",
                ref=f"{seat.lower()}-counter-target",
            )
            for seat in ("B", "C", "D")
        }
        plan = FixedCounterPlacementTargetSetHandler().lower(
            self.effect(
                [
                    targets["B"].ref,
                    targets["D"].ref,
                    targets["C"].ref,
                ]
            ),
            self.context(apnap=("C", "D", "A", "B")),
        )
        self.assertEqual(
            (
                PlaceCountersOnTargetsIntent(
                    actor="A",
                    object_refs=(
                        targets["B"].ref,
                        targets["D"].ref,
                        targets["C"].ref,
                    ),
                    maximum_targets=3,
                    counter_name="+1/+1",
                    amount=1,
                    reason="Fixed counter-target fixture",
                    source_ref="departed-source",
                ),
            ),
            plan.intents,
        )
        execute_intent_plan(engine, plan)
        self.assertEqual(
            [
                targets["C"].ref,
                targets["D"].ref,
                targets["B"].ref,
            ],
            [
                event.details["object"]
                for event in engine.state.events
                if event.code == "counter.add"
            ][-3:],
        )

    def test_handler_and_snapshot_reject_malformed_inputs_without_mutation(
        self,
    ):
        session = self.session(12290802)
        engine = session.engine
        target = self.add_permanent(
            engine,
            seat="A",
            name="Scute Swarm",
            ref="malformed-counter-target",
        )
        valid = self.effect([target.ref], maximum=2)
        malformed = (
            {**valid, "maximum_targets": True},
            {**valid, "maximum_targets": 0},
            {**valid, "cards": [target.ref, target.ref]},
            {**valid, "cards": [target.ref, "extra", "third"]},
            {**valid, "cards": [1]},
            {**valid, "amount": True},
            {**valid, "counter": ""},
            {**valid, "source": None},
            {**valid, "_replacement_selections": [1]},
            {**valid, "unknown": True},
        )
        before = authoritative_state_hash(engine.state)
        for effect in malformed:
            with self.subTest(effect=effect):
                with self.assertRaises(SemanticNodeError):
                    FixedCounterPlacementTargetSetHandler().lower(
                        effect,
                        self.context(),
                    )
                self.assertEqual(before, authoritative_state_hash(engine.state))
        self.assertEqual({}, target.counters)

    def test_partial_target_revalidation_places_only_on_still_legal_targets(
        self,
    ):
        session = self.session(12290803)
        engine = session.engine
        first = self.add_permanent(
            engine,
            seat="B",
            name="Scute Swarm",
            ref="illegal-counter-target",
        )
        second = self.add_permanent(
            engine,
            seat="C",
            name="Scute Swarm",
            ref="legal-counter-target",
        )
        program = SemanticProgram(
            key="fixture:partial-counter-target-set",
            label="Partial counter target set",
            effects=[
                self.effect("$targets", maximum=2, source="$source")
            ],
            target_schema=self.target_schema(2),
            trust_level="provisional",
        )
        engine.semantics.put(program)
        item = self.stack_item(
            engine,
            program=program,
            refs=[first.ref, second.ref],
        )
        engine.move_card(first.object_id, "graveyard", reason="response")

        engine._begin_resolve_item(
            item,
            [dict(value) for value in program.effects],
            None,
        )

        self.assertEqual({}, first.counters)
        self.assertEqual(1, second.counters["+1/+1"])
        self.assertTrue(
            any(event.code == "target.illegal" for event in engine.state.events)
        )

    def test_tapped_target_set_revalidates_public_state_before_placement(self):
        session = self.session(12290807)
        engine = session.engine
        tapped = self.add_permanent(
            engine,
            seat="B",
            name="Scute Swarm",
            ref="tapped-counter-target",
        )
        tapped.tapped = True
        untapped = self.add_permanent(
            engine,
            seat="C",
            name="Scute Swarm",
            ref="untapped-counter-target",
        )
        template = fixed_counter_placement_target_set_effect_template(
            "Put a stun counter on up to one target tapped creature."
        )
        self.assertIsNotNone(template)
        assert template is not None
        public = engine._public_target_schema(
            "A",
            template.target_schema,
            source_ref=None,
        )
        self.assertIsNotNone(public)
        assert public is not None
        self.assertIn(tapped.ref, public["legal_refs"])
        self.assertNotIn(untapped.ref, public["legal_refs"])
        program = SemanticProgram(
            key="fixture:tapped-counter-target-set",
            label="Tapped counter target set",
            effects=[dict(template.effects[0])],
            target_schema=dict(template.target_schema),
            trust_level="provisional",
        )
        engine.semantics.put(program)
        item = self.stack_item(
            engine,
            program=program,
            refs=[tapped.ref],
        )
        tapped.tapped = False

        engine._begin_resolve_item(
            item,
            [dict(value) for value in program.effects],
            None,
        )

        self.assertNotIn("stun", tapped.counters)
        self.assertFalse(any(value.ref == item.ref for value in engine.state.stack))
        self.assertTrue(
            any(event.code == "target.illegal" for event in engine.state.events)
        )
        tapped.tapped = True
        success_program = SemanticProgram(
            key="fixture:tapped-counter-target-set-success",
            label="Tapped counter target set success",
            effects=[dict(template.effects[0])],
            target_schema=dict(template.target_schema),
            trust_level="provisional",
        )
        engine.semantics.put(success_program)
        success = self.stack_item(
            engine,
            program=success_program,
            refs=[tapped.ref],
        )
        engine._begin_resolve_item(
            success,
            [dict(value) for value in success_program.effects],
            None,
        )
        self.assertEqual(1, tapped.counters["stun"])

    def test_all_original_targets_illegal_is_countered_before_counter_mutation(
        self,
    ):
        session = self.session(12290804)
        engine = session.engine
        target = self.add_permanent(
            engine,
            seat="B",
            name="Scute Swarm",
            ref="all-illegal-counter-target",
        )
        program = SemanticProgram(
            key="fixture:all-illegal-counter-target-set",
            label="All illegal counter target set",
            effects=[
                self.effect("$targets", maximum=2, source="$source")
            ],
            target_schema=self.target_schema(2),
            trust_level="provisional",
        )
        engine.semantics.put(program)
        item = self.stack_item(
            engine,
            program=program,
            refs=[target.ref],
        )
        engine.move_card(target.object_id, "graveyard", reason="response")

        engine._begin_resolve_item(
            item,
            [dict(value) for value in program.effects],
            None,
        )

        self.assertEqual({}, target.counters)
        self.assertFalse(any(value.ref == item.ref for value in engine.state.stack))
        self.assertFalse(
            any(event.code == "counter.add" for event in engine.state.events)
        )

    def test_target_set_suspends_for_quantity_replacement_and_replays(self):
        session = self.session(12290805)
        engine = session.engine
        target = self.add_permanent(
            engine,
            seat="A",
            name="Scute Swarm",
            ref="replacement-counter-target",
        )
        self.add_permanent(
            engine,
            seat="A",
            name="Doubling Season",
            ref="replacement-target-doubling",
        )
        self.add_permanent(
            engine,
            seat="A",
            name="Doc Samson, Super Psychiatrist",
            ref="replacement-target-doc",
        )
        program = SemanticProgram(
            key="fixture:replacement-counter-target-set",
            label="Replacement counter target set",
            effects=[
                self.effect("$targets", maximum=2, source="$source")
            ],
            target_schema=self.target_schema(2),
            trust_level="provisional",
        )
        engine.semantics.put(program)
        self.stack_item(
            engine,
            program=program,
            refs=[target.ref],
            source=target,
        )
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
        projector = StateProjector(self.db, engine.state)
        projected = projector._decision("pilot:A")
        self.assertIsNotNone(projected)
        for seat in ("B", "C", "D"):
            self.assertIsNone(projector._decision(f"pilot:{seat}"))
        serialized = json.dumps(projected, sort_keys=True)
        self.assertNotIn(target.object_id, serialized)
        hidden_refs = {
            engine.state.cards[object_id].ref
            for seat in ("B", "C", "D")
            for object_id in engine.state.players[seat].zones["hand"]
        }
        self.assertTrue(all(ref not in serialized for ref in hidden_refs))
        selected = projected["ctx"]["options"][0]["id"]
        result = session.act(
            "pilot:A",
            {
                "action_id": "choose",
                "choices": {"replacement": selected},
            },
        )
        self.assertTrue(result.ok, result.summary)
        self.assertGreater(target.counters["+1/+1"], 1)
        expected_hash = authoritative_state_hash(engine.state)

        with tempfile.TemporaryDirectory() as temporary:
            record_dir = Path(temporary) / "counter-target-set-replay"
            session.save(record_dir)
            replay = replay_record(record_dir, self.db, verify=True)
        self.assertTrue(replay["ok"], replay)
        self.assertEqual(5, replay["commands"])
        self.assertEqual(expected_hash, replay["final_state_hash"])

    def test_optional_target_loyalty_activation_pays_cost_without_a_target_and_replays(
        self,
    ):
        session = self.session(12290806)
        engine = session.engine
        record = self.db.lookup("Optional Counter Adept")
        register_generated_programs(
            self.db,
            engine.semantics,
            (record,),
            trust_level="provisional",
            capability_registry=load_default_capability_registry(),
            capability_profile=engine.state.config.review_profile,
            promote_exact_effect_programs=True,
        )
        source = self.add_permanent(
            engine,
            seat="A",
            name=record.name,
            ref="optional-counter-adept",
        )
        source.counters["loyalty"] = 3
        target = self.add_permanent(
            engine,
            seat="B",
            name="Scute Swarm",
            ref="optional-counter-target",
        )
        engine.state.active_player = "A"
        engine.state.phase = "precombat_main"
        engine.state.step = "main"
        engine._grant_priority("A")
        engine._issue_priority("A")
        action_id = f"activate:{source.ref}:ab1"
        action = next(
            value
            for value in session.packet("pilot:A", full=True)["decision"][
                "ctx"
            ]["legal"]["actions"]
            if value["id"] == action_id
        )
        self.assertEqual(1, action["target_schema"]["up_to"])
        self.assertEqual(0, action["target_schema"]["groups"][0]["min"])
        self.assertEqual(1, action["target_schema"]["groups"][0]["max"])
        self.assertIn(
            target.ref,
            action["target_schema"]["legal_refs"],
        )
        session.initial_checkpoint = checkpoint_envelope(engine.state)
        session.commands.clear()
        session.decisions.clear()

        result = session.act(
            "pilot:A",
            {"action_id": action_id, "targets": []},
        )
        self.assertTrue(result.ok, result.summary)
        self.assertEqual(4, source.counters["loyalty"])
        self.assertEqual([], engine.state.stack[-1].targets)

        self.pass_until(session, lambda: not engine.state.stack)

        self.assertEqual({}, target.counters)
        expected_hash = authoritative_state_hash(engine.state)
        with tempfile.TemporaryDirectory() as temporary:
            record_dir = Path(temporary) / "optional-counter-target-replay"
            session.save(record_dir)
            replay = replay_record(record_dir, self.db, verify=True)
        self.assertTrue(replay["ok"], replay)
        self.assertEqual(expected_hash, replay["final_state_hash"])


if __name__ == "__main__":
    unittest.main()
