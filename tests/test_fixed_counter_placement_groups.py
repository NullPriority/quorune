from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from common import ROOT, keep_all, make_session
from quorune.carddb import CardDatabase
from quorune.compiler.counter_placement_group_templates import (
    FixedCounterPlacementGroupTemplate,
    fixed_counter_placement_group_effect_template,
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
from quorune.rules.capabilities import (
    CapabilityRegistry,
    capability_covered_mechanics,
    capability_dependencies_for_node,
    load_default_capability_registry,
)
from quorune.semantic_runtime import (
    PlaceCountersIntent,
    ReadOnlyHandlerContext,
    ReadOnlyRulesQuery,
)
from quorune.semantic_runtime.context import SemanticNodeError
from quorune.semantic_runtime.counter_placement_handlers import (
    FixedCounterPlacementHandler,
)
from quorune.semantic_runtime.executor import execute_intent_plan
from quorune.semantics import SemanticProgram
from scripts.build_test_database import build_fixture_database


REGISTRY_PATH = ROOT / "quorune" / "rules" / "capability-registry.json"


def focused_card_database(directory: str) -> CardDatabase:
    database = Path(directory) / "fixed-counter-placement-groups.sqlite3"
    build_fixture_database(
        [
            ROOT / "tests" / "fixtures" / "scryfall-exact-lists.json",
            ROOT / "tests" / "fixtures" / "counter-replacement-cards.json",
        ],
        database,
    )
    return CardDatabase(database)


class FixedCounterPlacementGroupCompilerTests(unittest.TestCase):
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

    @staticmethod
    def group_node(ir):
        return next(
            node
            for node in ir.faces[0].nodes
            if node.template_id
            and node.template_id.startswith("place-fixed-counter-group-")
        )

    def test_spell_trigger_and_activated_contexts_share_fixed_counter_group_lowering(
        self,
    ):
        contexts = (
            (
                "Put two +1/+1 counters on target creature and two +1/+1 counters on another target creature.",
                "Sorcery",
                "spell_ability",
                ["$target.0", "$target.1"],
                True,
            ),
            (
                "When this creature enters, put a charge counter on target artifact you control and a charge counter on target artifact an opponent controls.",
                "Artifact Creature — Construct",
                "triggered_ability",
                ["$target.0", "$target.1"],
                False,
            ),
            (
                "{2}, {T}: Put a +1/+1 counter on this creature and a +1/+1 counter on up to one target commander creature you control.",
                "Artifact Creature — Mole",
                "activated_ability",
                ["$source.zone_object", "$target.0"],
                False,
            ),
        )
        for text, type_line, kind, cards, globally_distinct in contexts:
            with self.subTest(kind=kind):
                ir = self.compile(text, type_line=type_line)
                node = self.group_node(ir)
                self.assertEqual("exact", ir.status)
                self.assertEqual(kind, node.kind)
                self.assertEqual(cards, node.effects[0]["cards"])
                self.assertEqual("place_counters", node.effects[0]["op"])
                self.assertEqual(
                    globally_distinct,
                    bool(node.target_schema.get("globally_distinct", False)),
                )
                self.assertIn(
                    "counter.producer.fixed_permanent_group_effect",
                    node.capability_dependencies,
                )
                self.assertIn(
                    "target.revalidate_resolution",
                    node.capability_dependencies,
                )
                self.assertEqual(text, text[node.span.start : node.span.end])

        drill = self.group_node(self.compile(contexts[-1][0], type_line=contexts[-1][1]))
        group = drill.target_schema["groups"][0]
        self.assertEqual(["creature"], group["types_any"])
        self.assertTrue(group["commander"])
        self.assertEqual("you", group["controller_relation"])
        self.assertEqual(0, group["min"])
        self.assertEqual(1, group["max"])

    def test_repeated_target_instances_preserve_cr_115_3_reuse_and_printed_distinctness(
        self,
    ):
        reusable = fixed_counter_placement_group_effect_template(
            "Put a +1/+1 counter on target creature and a +1/+1 counter on up to one target Merfolk.",
            card_name="Fixture",
            source_is_permanent=False,
        )
        distinct = fixed_counter_placement_group_effect_template(
            "Put two +1/+1 counters on target creature and two +1/+1 counters on another target creature.",
            card_name="Fixture",
            source_is_permanent=False,
        )
        self.assertIsNotNone(reusable)
        self.assertIsNotNone(distinct)
        assert reusable is not None and distinct is not None
        self.assertNotIn("globally_distinct", reusable.target_schema)
        self.assertTrue(distinct.target_schema["globally_distinct"])
        self.assertEqual(0, reusable.target_schema["groups"][1]["min"])
        self.assertEqual(1, reusable.target_schema["groups"][1]["max"])

    def test_template_is_immutable_deterministic_and_order_sensitive(self):
        text = (
            "Put a charge counter on target artifact you control and a charge "
            "counter on target artifact an opponent controls."
        )
        first = fixed_counter_placement_group_effect_template(
            text,
            card_name="Fixture",
            source_is_permanent=False,
        )
        second = fixed_counter_placement_group_effect_template(
            text,
            card_name="Fixture",
            source_is_permanent=False,
        )
        reversed_template = fixed_counter_placement_group_effect_template(
            "Put a charge counter on target artifact an opponent controls and a charge counter on target artifact you control.",
            card_name="Fixture",
            source_is_permanent=False,
        )
        self.assertEqual(first, second)
        self.assertIsNotNone(first)
        self.assertIsNotNone(reversed_template)
        assert first is not None and reversed_template is not None
        self.assertEqual(first.compiled(), second.compiled())
        self.assertNotEqual(first.template_id, reversed_template.template_id)
        caller_owned = list(first.placements)
        caller_owned.reverse()
        self.assertNotEqual(tuple(caller_owned), first.placements)
        with self.assertRaises(FrozenInstanceError):
            first.globally_distinct = True

    def test_unsupported_group_variants_remain_material_residuals(self):
        cases = (
            (
                "Put a +1/+1 counter on target creature, two +1/+1 counters on another target creature, and three +1/+1 counters on a third target creature.",
                "Sorcery",
            ),
            (
                "Put a +1/+1 counter on target creature and a lifelink counter on another target creature.",
                "Sorcery",
            ),
            (
                "Put X charge counters on target artifact and X charge counters on another target artifact.",
                "Sorcery",
            ),
            (
                "Put a +1/+1 counter on up to one target creature and a +1/+1 counter on target creature.",
                "Sorcery",
            ),
            (
                "Put a +1/+1 counter on this creature and a +1/+1 counter on target creature.",
                "Sorcery",
            ),
            (
                "Put a +1/+1 counter on target creature and a +1/+1 counter on another target creature and draw a card.",
                "Sorcery",
            ),
            (
                "Put a +1/+1 counter on target modified creature and a +1/+1 counter on another target modified creature.",
                "Sorcery",
            ),
            (
                "Put a +1/+1 counter on target creature, a +1/+1 counter on another target creature, and a +1/+1 counter on target creature.",
                "Sorcery",
            ),
        )
        for text, type_line in cases:
            with self.subTest(text=text):
                self.assertIsNone(
                    fixed_counter_placement_group_effect_template(
                        text,
                        card_name="Fixture",
                        source_is_permanent=type_line != "Sorcery",
                    )
                )
                ir = self.compile(text, type_line=type_line)
                self.assertNotEqual("exact", ir.status)
                self.assertTrue(ir.material_residuals)

    def test_group_shape_and_dependency_mutants_fail_closed(self):
        template = fixed_counter_placement_group_effect_template(
            "Put a +1/+1 counter on target creature and a +1/+1 counter on up to one target commander creature you control.",
            card_name="Fixture",
            source_is_permanent=False,
        )
        self.assertIsNotNone(template)
        assert template is not None
        expected = {
            "counter.producer.fixed_permanent_group_effect",
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
        effect_mutants = (
            {**effect, "cards": ["$target.1", "$target.0"]},
            {**effect, "cards": ["$target.0", True]},
            {**effect, "cards": ["$source.zone_object"] * 2},
            {**effect, "amount": True},
            {**effect, "extra": True},
        )
        for mutant in effect_mutants:
            with self.subTest(mutant=mutant):
                self.assertNotIn(
                    "counter.producer.fixed_permanent_group_effect",
                    capability_dependencies_for_node(
                        effects=(mutant,),
                        target_schema=template.target_schema,
                        mechanic_ids=template.mechanics,
                    ),
                )
        schema_mutants = (
            {**template.target_schema, "globally_distinct": False},
            {
                **template.target_schema,
                "groups": [
                    {**template.target_schema["groups"][0], "min": 0},
                    template.target_schema["groups"][1],
                ],
            },
            {
                **template.target_schema,
                "groups": [
                    template.target_schema["groups"][0],
                    {**template.target_schema["groups"][1], "commander": False},
                ],
            },
        )
        for mutant in schema_mutants:
            with self.subTest(mutant=mutant):
                self.assertNotIn(
                    "counter.producer.fixed_permanent_group_effect",
                    capability_dependencies_for_node(
                        effects=template.effects,
                        target_schema=mutant,
                        mechanic_ids=template.mechanics,
                    ),
                )

    def test_fixed_counter_group_lowers_to_capability_closed_card_program(self):
        record = replace(
            self.base,
            name="Fixture",
            oracle_text=(
                "Put two +1/+1 counters on target creature and two +1/+1 "
                "counters on another target creature."
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
        self.assertIn(
            "counter.producer.fixed_permanent_group_effect",
            program.capability_dependencies,
        )
        self.assertIsNotNone(program.capability_closure)
        assert program.capability_closure is not None
        self.assertTrue(program.capability_closure["trusted"])
        self.assertIn(
            "cr-122-counters",
            capability_covered_mechanics(program.capability_dependencies),
        )

    def test_fixed_counter_group_compiler_template_mutant_is_killed(self):
        text = (
            "Put two +1/+1 counters on target creature and two +1/+1 "
            "counters on another target creature."
        )

        def exact() -> None:
            self.assertEqual("exact", self.compile(text).status)

        exact()
        with patch(
            "quorune.compiler.counter_resolution_effect_templates."
            "fixed_counter_placement_group_effect_template",
            return_value=None,
        ):
            with self.assertRaises(AssertionError):
                exact()

        value = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
        dependency = next(
            row
            for row in value["capabilities"]
            if row["id"] == "target.revalidate_resolution"
        )
        dependency["status"] = "blocked"
        dependency["blockers"] = ["test mutation"]
        ir = compile_oracle_card(
            replace(
                self.base,
                name="Fixture",
                oracle_text=text,
                type_line="Sorcery",
                keywords=(),
                faces=(),
            ),
            capability_registry=CapabilityRegistry(value),
            capability_profile="commander_review",
        )
        self.assertNotEqual("exact", ir.status)


class FixedCounterPlacementGroupRuntimeTests(unittest.TestCase):
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

    @staticmethod
    def context() -> ReadOnlyHandlerContext:
        return ReadOnlyHandlerContext(
            actor="A",
            default_reason="Fixed counter group fixture",
            query=ReadOnlyRulesQuery(
                seats=("A", "B", "C", "D"),
                active_seats=("A", "B", "C", "D"),
                apnap_order=("A", "B", "C", "D"),
            ),
        )

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
    def target_schema(*, optional_second: bool = False, distinct: bool = False):
        second = {
            "id": "target_1",
            "zones": ["battlefield"],
            "categories": ["permanent"],
            "types_any": ["creature"],
            **(
                {"min": 0, "max": 1}
                if optional_second
                else {"count": 1}
            ),
        }
        return {
            "groups": [
                {
                    "id": "target_0",
                    "zones": ["battlefield"],
                    "categories": ["permanent"],
                    "types_any": ["creature"],
                    "count": 1,
                },
                second,
            ],
            **({"globally_distinct": True} if distinct else {}),
        }

    def stack_item(
        self,
        engine,
        *,
        program: SemanticProgram,
        refs: list[str],
        groups: dict[str, list[str]],
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
                "target_groups": groups,
                "target_snapshots": {
                    ref: engine._target_snapshot(ref) for ref in set(refs)
                },
                "targets_revalidated": False,
                "targets_chosen_at_creation": True,
                **(
                    {"source_logical_object_id": source.logical_object_id}
                    if source is not None
                    else {}
                ),
            },
        )
        engine.state.stack.append(item)
        return item

    @staticmethod
    def effect(cards, *, source="source"):
        return {
            "op": "place_counters",
            "cards": list(cards),
            "counter": "+1/+1",
            "amount": 1,
            "source": source,
        }

    def test_group_handler_commits_same_target_twice_in_one_atomic_batch(self):
        session = self.session(12310101)
        engine = session.engine
        target = self.add_permanent(
            engine,
            seat="A",
            name="Scute Swarm",
            ref="reused-counter-target",
        )
        program = SemanticProgram(
            key="fixture:reused-counter-target",
            label="Reused counter target",
            effects=[self.effect(["$target.0", "$target.1"])],
            target_schema=self.target_schema(),
            trust_level="provisional",
        )
        selected, grouped = engine._validate_semantic_targets(
            "A",
            program,
            [
                {"group": "target_0", "ref": target.ref},
                {"group": "target_1", "ref": target.ref},
            ],
            source_ref="S-fixture",
        )
        self.assertEqual([target.ref, target.ref], selected)
        self.assertEqual(
            {"target_0": [target.ref], "target_1": [target.ref]},
            grouped,
        )
        plan = FixedCounterPlacementHandler().lower(
            self.effect(selected),
            self.context(),
        )
        self.assertEqual(
            (
                PlaceCountersIntent(
                    actor="A",
                    object_refs=(target.ref, target.ref),
                    counter_name="+1/+1",
                    amount=1,
                    reason="Fixed counter group fixture",
                    source_ref="source",
                ),
            ),
            plan.intents,
        )
        execute_intent_plan(engine, plan)
        self.assertEqual(2, target.counters["+1/+1"])
        self.assertEqual(
            2,
            len(
                [
                    event
                    for event in engine.state.events
                    if event.code == "counter.add"
                    and event.details.get("object") == target.ref
                ]
            ),
        )

    def test_printed_another_target_rejects_reuse(self):
        session = self.session(12310102)
        engine = session.engine
        target = self.add_permanent(
            engine,
            seat="A",
            name="Scute Swarm",
            ref="distinct-counter-target",
        )
        program = SemanticProgram(
            key="fixture:distinct-counter-target",
            label="Distinct counter targets",
            effects=[self.effect(["$target.0", "$target.1"])],
            target_schema=self.target_schema(distinct=True),
            trust_level="provisional",
        )
        with self.assertRaises(GameRuleError):
            engine._validate_semantic_targets(
                "A",
                program,
                [
                    {"group": "target_0", "ref": target.ref},
                    {"group": "target_1", "ref": target.ref},
                ],
                source_ref="S-fixture",
            )

    def test_group_handler_rejects_malformed_inputs_without_mutation(self):
        session = self.session(12310103)
        engine = session.engine
        target = self.add_permanent(
            engine,
            seat="A",
            name="Scute Swarm",
            ref="malformed-group-target",
        )
        valid = self.effect([target.ref, target.ref])
        malformed = (
            {**valid, "cards": target.ref},
            {**valid, "cards": [target.ref]},
            {**valid, "cards": [target.ref, True]},
            {**valid, "cards": [target.ref] * 4},
            {**valid, "amount": True},
            {**valid, "counter": ""},
            {**valid, "source": None},
            {**valid, "_replacement_selections": [1]},
            {**valid, "unknown": True},
        )
        before = authoritative_state_hash(engine.state)
        for effect in malformed:
            with self.subTest(effect=effect):
                with self.assertRaises((SemanticNodeError, ValueError)):
                    FixedCounterPlacementHandler().lower(
                        effect,
                        self.context(),
                    )
                self.assertEqual(before, authoritative_state_hash(engine.state))
        self.assertEqual({}, target.counters)

        plan = FixedCounterPlacementHandler().lower(
            self.effect([target.ref, "missing-counter-target"]),
            self.context(),
        )
        with self.assertRaises(GameRuleError):
            execute_intent_plan(engine, plan)
        self.assertEqual(before, authoritative_state_hash(engine.state))
        self.assertEqual({}, target.counters)

    def test_source_departure_skips_source_and_preserves_target_result(self):
        session = self.session(12310104)
        engine = session.engine
        source = self.add_permanent(
            engine,
            seat="A",
            name="Scute Swarm",
            ref="departing-counter-source",
        )
        target = self.add_permanent(
            engine,
            seat="A",
            name="Scute Swarm",
            ref="remaining-counter-target",
        )
        program = SemanticProgram(
            key="fixture:source-departure-counter-group",
            label="Source departure counter group",
            effects=[
                self.effect(
                    ["$source.zone_object", "$target.0"],
                    source="$source",
                )
            ],
            target_schema={"groups": [self.target_schema()["groups"][0]]},
            trust_level="provisional",
        )
        engine.semantics.put(program)
        item = self.stack_item(
            engine,
            program=program,
            refs=[target.ref],
            groups={"target_0": [target.ref]},
            source=source,
        )
        engine.move_card(source.object_id, "graveyard", reason="response")

        engine._begin_resolve_item(
            item,
            [dict(value) for value in program.effects],
            None,
        )

        self.assertEqual({}, source.counters)
        self.assertEqual(1, target.counters["+1/+1"])

    def test_partial_target_revalidation_preserves_remaining_subject(self):
        session = self.session(12310105)
        engine = session.engine
        first = self.add_permanent(
            engine,
            seat="A",
            name="Scute Swarm",
            ref="departing-group-target",
        )
        second = self.add_permanent(
            engine,
            seat="A",
            name="Scute Swarm",
            ref="remaining-group-target",
        )
        program = SemanticProgram(
            key="fixture:partial-counter-group",
            label="Partial counter group",
            effects=[
                self.effect(["$target.0", "$target.1"], source="$source")
            ],
            target_schema=self.target_schema(),
            trust_level="provisional",
        )
        engine.semantics.put(program)
        item = self.stack_item(
            engine,
            program=program,
            refs=[first.ref, second.ref],
            groups={"target_0": [first.ref], "target_1": [second.ref]},
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

    def test_four_player_group_replacement_is_seat_scoped_and_replays(self):
        session = self.session(12310106, players=4)
        engine = session.engine
        target = self.add_permanent(
            engine,
            seat="A",
            name="Scute Swarm",
            ref="four-player-group-target",
        )
        self.add_permanent(
            engine,
            seat="A",
            name="Doubling Season",
            ref="four-player-group-doubling",
        )
        self.add_permanent(
            engine,
            seat="A",
            name="Doc Samson, Super Psychiatrist",
            ref="four-player-group-doc",
        )
        program = SemanticProgram(
            key="fixture:four-player-counter-group",
            label="Four-player counter group",
            effects=[
                self.effect(["$target.0", "$target.1"], source="$source")
            ],
            target_schema=self.target_schema(),
            trust_level="provisional",
        )
        engine.semantics.put(program)
        self.stack_item(
            engine,
            program=program,
            refs=[target.ref, target.ref],
            groups={"target_0": [target.ref], "target_1": [target.ref]},
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
        replacement_choices = 0
        projector = StateProjector(self.db, engine.state)
        while (
            engine.state.pending_decision is not None
            and engine.state.pending_decision.kind == "replacement.order"
        ):
            projected = projector._decision("pilot:A")
            self.assertIsNotNone(projected)
            for seat in ("B", "C", "D"):
                self.assertIsNone(projector._decision(f"pilot:{seat}"))
            self.assertNotIn(
                target.object_id,
                json.dumps(projected, sort_keys=True),
            )
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
        self.assertGreaterEqual(replacement_choices, 2)
        self.assertGreater(target.counters["+1/+1"], 2)
        expected_hash = authoritative_state_hash(engine.state)

        with tempfile.TemporaryDirectory() as temporary:
            record_dir = Path(temporary) / "fixed-counter-group-replay"
            session.save(record_dir)
            replay = replay_record(record_dir, self.db, verify=True)
        self.assertTrue(replay["ok"], replay)
        self.assertEqual(len(session.commands), replay["commands"])
        self.assertEqual(expected_hash, replay["final_state_hash"])


if __name__ == "__main__":
    unittest.main()
