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
    fixed_counter_placement_effect_template,
)
from quorune.compiler.direct_target import DirectPermanentTargetSpec
from quorune.deck import DeckLoader
from quorune.errors import GameRuleError
from quorune.model import CardInstance, StackItem
from quorune.oracle_ir import compile_oracle_card
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
from quorune.semantics import SemanticProgram
from quorune.targets import TargetGroup
from scripts.build_test_database import build_fixture_database


REGISTRY_PATH = ROOT / "quorune" / "rules" / "capability-registry.json"


def focused_card_database(directory: str) -> CardDatabase:
    database = Path(directory) / "counter-target-predicates.sqlite3"
    build_fixture_database(
        [
            ROOT / "tests" / "fixtures" / "scryfall-exact-lists.json",
            ROOT / "tests" / "fixtures" / "counter-replacement-cards.json",
        ],
        database,
    )
    return CardDatabase(database)


class CounterTargetPredicateCompilerTests(unittest.TestCase):
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

    def compile(self, text: str, *, registry=None):
        return compile_oracle_card(
            replace(
                self.base,
                name="Fixture",
                oracle_text=text,
                type_line="Sorcery",
                keywords=(),
                faces=(),
            ),
            capability_registry=registry or self.capabilities,
            capability_profile="commander_review",
        )

    def test_closed_direct_target_grammar_compiles_typed_predicates(self):
        cases = (
            (
                "Put a +1/+1 counter on target artifact or creature you control.",
                {
                    "types_any": ["artifact", "creature"],
                    "controller_relation": "you",
                },
            ),
            (
                "Put a +1/+1 counter on target artifact, enchantment, or land.",
                {"types_any": ["artifact", "enchantment", "land"]},
            ),
            (
                "Put a +1/+1 counter on target enchantment creature.",
                {"types_all": ["creature", "enchantment"]},
            ),
            (
                "Put a +1/+1 counter on another target creature with flying.",
                {
                    "types_all": ["creature"],
                    "keywords_all": ["flying"],
                    "source_exclusion": True,
                },
            ),
            (
                "Put a +1/+1 counter on target Mount or Vehicle.",
                {"subtypes_any": ["mount", "vehicle"]},
            ),
            (
                "Put a +1/+1 counter on target Bird, Cat, Dog, Goat, Ox, or Snake.",
                {
                    "subtypes_any": [
                        "bird",
                        "cat",
                        "dog",
                        "goat",
                        "ox",
                        "snake",
                    ]
                },
            ),
        )
        for text, expected in cases:
            with self.subTest(text=text):
                template = fixed_counter_placement_effect_template(
                    text,
                    card_name="Fixture",
                )
                self.assertIsNotNone(template)
                assert template is not None
                self.assertIsInstance(
                    template.target_spec,
                    DirectPermanentTargetSpec,
                )
                self.assertTrue(
                    expected.items() <= template.target_schema.items()
                )
                ir = self.compile(text)
                self.assertEqual("exact", ir.status)
                node = ir.faces[0].nodes[0]
                self.assertEqual(text, text[node.span.start : node.span.end])
                self.assertIn(
                    "target.permanent.characteristic_predicate",
                    node.capability_dependencies,
                )

    def test_public_state_direct_targets_compile_typed_predicates(self):
        cases = (
            (
                "Put two +1/+1 counters on target non-Human creature that entered this turn.",
                {
                    "types_any": ["creature"],
                    "subtypes_none": ["human"],
                    "state_predicate": {
                        "entered_this_turn": True,
                        "tapped": None,
                        "counter_name": None,
                        "minimum_counter_count": None,
                    },
                },
            ),
            (
                "Put a +1/+1 counter on target non-Elf creature.",
                {
                    "types_any": ["creature"],
                    "subtypes_none": ["elf"],
                },
            ),
            (
                "Put a +1/+1 counter on target colorless creature that entered this turn.",
                {
                    "types_any": ["creature"],
                    "colorless": True,
                    "state_predicate": {
                        "entered_this_turn": True,
                        "tapped": None,
                        "counter_name": None,
                        "minimum_counter_count": None,
                    },
                },
            ),
            (
                "Put a +1/+1 counter on target creature with a +1/+1 counter on it.",
                {
                    "types_any": ["creature"],
                    "state_predicate": {
                        "entered_this_turn": False,
                        "tapped": None,
                        "counter_name": "+1/+1",
                        "minimum_counter_count": 1,
                    },
                },
            ),
            (
                "Put a stun counter on target tapped creature.",
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
            (
                "Put a bounty counter on target nonblack creature.",
                {
                    "types_any": ["creature"],
                    "colors_none": ["B"],
                },
            ),
        )
        for text, expected in cases:
            with self.subTest(text=text):
                template = fixed_counter_placement_effect_template(
                    text,
                    card_name="Fixture",
                )
                self.assertIsNotNone(template)
                assert template is not None
                self.assertTrue(expected.items() <= template.target_schema.items())
                ir = self.compile(text)
                self.assertEqual("exact", ir.status)
                node = ir.faces[0].nodes[0]
                if "state_predicate" in expected:
                    self.assertIn(
                        "state_query.permanent.public_state_predicate",
                        node.capability_dependencies,
                    )
                if any(
                    key in expected
                    for key in ("subtypes_none", "colorless", "colors_none")
                ):
                    self.assertIn(
                        "target.permanent.characteristic_predicate",
                        node.capability_dependencies,
                    )

    def test_unsupported_and_malformed_direct_target_predicates_fail_closed(self):
        unsupported = (
            "target modified creature",
            "target nontoken creature",
            "target commander creature",
            "target equipped creature",
            "target enchanted creature",
            "target creature with flying and vigilance",
            "target creature with a counter on it",
            "target creature that entered last turn",
            "target artifact or Vehicle",
        )
        for subject in unsupported:
            text = f"Put a +1/+1 counter on {subject}."
            with self.subTest(text=text):
                self.assertIsNone(
                    fixed_counter_placement_effect_template(
                        text,
                        card_name="Fixture",
                    )
                )
                ir = self.compile(text)
                self.assertNotEqual("exact", ir.status)
                self.assertTrue(ir.material_residuals)

        template = fixed_counter_placement_effect_template(
            "Put a +1/+1 counter on target artifact or creature you control.",
            card_name="Fixture",
        )
        assert template is not None and template.target_schema is not None
        malformed = (
            {**template.target_schema, "unknown": True},
            {**template.target_schema, "types_any": ["creature", "artifact"]},
            {**template.target_schema, "types_any": ["dragon"]},
            {**template.target_schema, "keywords_all": ["flying"]},
            {**template.target_schema, "source_exclusion": "yes"},
            {
                **template.target_schema,
                "state_predicate": {
                    "entered_this_turn": True,
                    "tapped": True,
                    "counter_name": None,
                    "minimum_counter_count": None,
                },
            },
        )
        for schema in malformed:
            with self.subTest(schema=schema):
                self.assertFalse(
                    capability_dependencies_for_node(
                        effects=template.effects,
                        target_schema=schema,
                        mechanic_ids=template.mechanics,
                    )
                )
        with self.assertRaisesRegex(ValueError, "unknown fields"):
            TargetGroup.from_mapping(
                {**template.target_schema, "runtime_oracle_hint": "creature"}
            )

    def test_target_predicate_capability_dependency_fails_closed(self):
        text = (
            "Put a +1/+1 counter on target artifact or creature you control."
        )
        exact = self.compile(text)
        self.assertEqual("exact", exact.status)
        registry_value = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
        capability = next(
            row
            for row in registry_value["capabilities"]
            if row["id"] == "target.permanent.characteristic_predicate"
        )
        capability["status"] = "blocked"
        capability["blockers"] = ["focused dependency mutation"]
        blocked = self.compile(text, registry=CapabilityRegistry(registry_value))
        self.assertNotEqual("exact", blocked.status)
        self.assertTrue(blocked.material_residuals)

    def test_public_state_target_capability_dependency_fails_closed(self):
        text = (
            "Put a +1/+1 counter on target creature with a +1/+1 counter on it."
        )
        self.assertEqual("exact", self.compile(text).status)
        registry_value = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
        capability = next(
            row
            for row in registry_value["capabilities"]
            if row["id"] == "state_query.permanent.public_state_predicate"
        )
        capability["status"] = "blocked"
        capability["blockers"] = ["focused dependency mutation"]
        blocked = self.compile(text, registry=CapabilityRegistry(registry_value))
        self.assertNotEqual("exact", blocked.status)
        self.assertTrue(blocked.material_residuals)

    def test_compound_target_sequence_harvests_enchantment_creature(self):
        text = (
            "Put a +1/+1 counter on target enchantment creature. "
            "It gains trample until end of turn."
        )
        ir = self.compile(text, registry=self.capabilities)
        self.assertEqual("exact", ir.status)
        self.assertFalse(ir.material_residuals)
        node = ir.faces[0].nodes[0]
        self.assertEqual(
            "fixed-target-counter-characteristics-sequence-v1",
            node.template_id,
        )
        self.assertEqual(
            ["creature", "enchantment"],
            node.target_schema["types_all"],
        )
        self.assertTrue(
            {
                "combat.damage.assignment.trample",
                "resolution.effect_sequence.fixed_target",
                "target.permanent.characteristic_predicate",
                "target.revalidate_resolution",
            }.issubset(node.capability_dependencies)
        )
        self.assertEqual(text, text[node.span.start : node.span.end])

    def test_typed_target_parser_mutation_is_killed(self):
        text = "Put a +1/+1 counter on target Mount or Vehicle."

        def exact() -> None:
            self.assertEqual("exact", self.compile(text).status)

        exact()
        with patch(
            "quorune.compiler.counter_placement_templates.direct_permanent_target_spec",
            return_value=None,
        ):
            with self.assertRaises(AssertionError):
                exact()


class CounterTargetPredicateRuntimeTests(unittest.TestCase):
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

    def session(self, seed: int):
        session = make_session(
            self.db,
            self.mishra,
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
    def template(text: str):
        template = fixed_counter_placement_effect_template(
            text,
            card_name="Fixture",
        )
        assert template is not None and template.target_schema is not None
        return template

    def test_current_effective_characteristics_govern_offer_command_and_resolution(
        self,
    ):
        session = self.session(11520501)
        engine = session.engine
        source = self.add_permanent(
            engine,
            seat="A",
            name="Sol Ring",
            ref="predicate-source",
        )
        target = self.add_permanent(
            engine,
            seat="A",
            name="Mishra, Eminent One",
            ref="predicate-target",
        )
        target.temporary_keywords.append("Flying")
        template = self.template(
            "Put a +1/+1 counter on another target creature with flying."
        )
        schema = dict(template.target_schema)
        public = engine._public_target_schema(
            "A",
            schema,
            source_ref=source.ref,
        )
        self.assertIsNotNone(public)
        assert public is not None
        self.assertIn(target.ref, public["legal_refs"])
        self.assertNotIn(source.ref, public["legal_refs"])
        selected, grouped = engine._validate_semantic_targets(
            "A",
            None,
            [target.ref],
            source_ref=source.ref,
            target_schema=schema,
        )
        self.assertEqual([target.ref], selected)

        program = SemanticProgram(
            key="fixture:current-target-characteristics",
            label="Current target characteristics",
            effects=[dict(template.effects[0])],
            target_schema=schema,
            trust_level="provisional",
        )
        engine.semantics.put(program)
        item = StackItem(
            stack_id="current-target-characteristics",
            ref="S-current-target-characteristics",
            kind="triggered_ability",
            controller="A",
            label=program.label,
            semantic_key=program.key,
            targets=selected,
            visibility=list(engine.seats),
            context={
                "target_groups": grouped,
                "target_snapshots": {
                    target.ref: engine._target_snapshot(target.ref)
                },
                "targets_revalidated": False,
                "targets_chosen_at_creation": True,
            },
        )
        engine.state.stack.append(item)
        target.temporary_keywords.clear()
        before_counters = dict(target.counters)
        with self.assertRaises(GameRuleError):
            engine._validate_semantic_targets(
                "A",
                None,
                [target.ref],
                source_ref=source.ref,
                target_schema=schema,
            )
        self.assertFalse(engine._revalidate_resolution_targets(item))
        self.assertNotIn(item, engine.state.stack)
        self.assertEqual(before_counters, target.counters)

        type_template = self.template(
            "Put a +1/+1 counter on target artifact or creature you control."
        )
        land = self.add_permanent(
            engine,
            seat="A",
            name="Island",
            ref="animated-target",
        )
        group = TargetGroup.from_mapping(type_template.target_schema)
        self.assertNotIn(
            land.ref,
            engine._target_candidates("A", group, source_ref=source.ref),
        )
        land.annotations["continuous_add_types"] = ["Artifact"]
        self.assertIn(
            land.ref,
            engine._target_candidates("A", group, source_ref=source.ref),
        )

    def test_public_state_targets_share_offer_command_and_resolution_revalidation(
        self,
    ):
        session = self.session(11520503)
        engine = session.engine
        source = self.add_permanent(
            engine,
            seat="A",
            name="Sol Ring",
            ref="public-state-source",
        )
        current = self.add_permanent(
            engine,
            seat="A",
            name="Island",
            ref="entered-current-turn",
        )
        current.annotations["continuous_add_types"] = ["Creature"]
        previous = self.add_permanent(
            engine,
            seat="A",
            name="Island",
            ref="entered-previous-turn",
        )
        previous.annotations["continuous_add_types"] = ["Creature"]
        self.assertGreater(engine.state.turn_sequence, 0)
        current.entered_battlefield_turn_sequence = engine.state.turn_sequence
        previous.entered_battlefield_turn_sequence = max(
            0, engine.state.turn_sequence - 1
        )
        human = self.add_permanent(
            engine,
            seat="A",
            name="Mishra, Eminent One",
            ref="human-entered-current-turn",
        )
        human.entered_battlefield_turn_sequence = engine.state.turn_sequence
        template = self.template(
            "Put a +1/+1 counter on target colorless creature that entered this turn."
        )
        schema = dict(template.target_schema)
        group = TargetGroup.from_mapping(schema)
        candidates = engine._target_candidates(
            "A",
            group,
            source_ref=source.ref,
        )
        self.assertIn(current.ref, candidates)
        self.assertNotIn(previous.ref, candidates)
        self.assertNotIn(human.ref, candidates)
        nonhuman = self.template(
            "Put two +1/+1 counters on target non-Human creature that entered this turn."
        )
        nonhuman_candidates = engine._target_candidates(
            "A",
            TargetGroup.from_mapping(nonhuman.target_schema),
            source_ref=source.ref,
        )
        self.assertIn(current.ref, nonhuman_candidates)
        self.assertNotIn(human.ref, nonhuman_candidates)
        nonblack = self.template(
            "Put a bounty counter on target nonblack creature."
        )
        nonblack_candidates = engine._target_candidates(
            "A",
            TargetGroup.from_mapping(nonblack.target_schema),
            source_ref=source.ref,
        )
        self.assertIn(current.ref, nonblack_candidates)
        self.assertNotIn(human.ref, nonblack_candidates)
        selected, grouped = engine._validate_semantic_targets(
            "A",
            None,
            [current.ref],
            source_ref=source.ref,
            target_schema=schema,
        )
        program = SemanticProgram(
            key="fixture:public-state-target-revalidation",
            label="Public state target revalidation",
            effects=[dict(template.effects[0])],
            target_schema=schema,
            trust_level="provisional",
        )
        engine.semantics.put(program)
        item = StackItem(
            stack_id="public-state-target-revalidation",
            ref="S-public-state-target-revalidation",
            kind="triggered_ability",
            controller="A",
            label=program.label,
            semantic_key=program.key,
            source_object_id=source.object_id,
            targets=selected,
            visibility=list(engine.seats),
            context={
                "target_groups": grouped,
                "target_snapshots": {
                    current.ref: engine._target_snapshot(current.ref)
                },
                "targets_revalidated": False,
                "targets_chosen_at_creation": True,
            },
        )
        engine.state.stack.append(item)
        engine.state.turn_sequence += 1
        self.assertFalse(engine._revalidate_resolution_targets(item))
        self.assertNotIn(item, engine.state.stack)
        self.assertEqual({}, current.counters)

    def test_public_state_predicate_runtime_mutation_is_killed(self):
        session = self.session(11520504)
        engine = session.engine
        current = self.add_permanent(
            engine,
            seat="A",
            name="Scute Swarm",
            ref="mutation-current-entry",
        )
        previous = self.add_permanent(
            engine,
            seat="A",
            name="Scute Swarm",
            ref="mutation-previous-entry",
        )
        current.entered_battlefield_turn_sequence = engine.state.turn_sequence
        previous.entered_battlefield_turn_sequence = max(
            0, engine.state.turn_sequence - 1
        )
        template = self.template(
            "Put a +1/+1 counter on target creature that entered this turn."
        )
        group = TargetGroup.from_mapping(template.target_schema)

        def exact() -> None:
            self.assertEqual(
                [current.ref],
                [
                    ref
                    for ref in engine._target_candidates(
                        "A",
                        group,
                        source_ref=None,
                    )
                    if ref in {current.ref, previous.ref}
                ],
            )

        exact()
        with patch(
            "quorune.selection.targeting.permanent_state_predicate_matches",
            return_value=True,
        ):
            with self.assertRaises(AssertionError):
                exact()

    def test_four_player_target_predicate_privacy_rollback_and_replay(self):
        session = self.session(11520502)
        engine = session.engine
        legal = self.add_permanent(
            engine,
            seat="A",
            name="Scute Swarm",
            ref="controlled-counter-target",
        )
        legal.counters["+1/+1"] = 1
        illegal = self.add_permanent(
            engine,
            seat="B",
            name="Scute Swarm",
            ref="opposing-counter-target",
        )
        illegal.counters["+1/+1"] = 1
        missing_counter = self.add_permanent(
            engine,
            seat="A",
            name="Scute Swarm",
            ref="controlled-missing-counter-target",
        )
        template = self.template(
            "Put a +1/+1 counter on target creature you control with a +1/+1 counter on it."
        )
        program = SemanticProgram(
            key="fixture:counter-target-predicate-replay",
            label="Typed counter target predicate",
            effects=[dict(template.effects[0])],
            target_schema=dict(template.target_schema),
            trust_level="provisional",
        )
        engine.semantics.put(program)
        engine.state.stack.append(
            StackItem(
                stack_id="counter-target-predicate-replay",
                ref="S-counter-target-predicate-replay",
                kind="triggered_ability",
                controller="A",
                label=program.label,
                semantic_key=program.key,
                visibility=list(engine.seats),
                context={"trigger_target_selection_pending": True},
            )
        )
        engine.state.active_player = "A"
        engine.state.phase = "precombat_main"
        engine.state.step = "main"
        self.assertTrue(engine._begin_pending_trigger_target_selection())
        projector = StateProjector(self.db, engine.state)
        projected = projector._decision("pilot:A")
        self.assertIsNotNone(projected)
        assert projected is not None
        self.assertIn(legal.ref, projected["ctx"]["target_schema"]["legal_refs"])
        self.assertNotIn(
            illegal.ref,
            projected["ctx"]["target_schema"]["legal_refs"],
        )
        self.assertNotIn(
            missing_counter.ref,
            projected["ctx"]["target_schema"]["legal_refs"],
        )
        for seat in ("B", "C", "D"):
            self.assertIsNone(projector._decision(f"pilot:{seat}"))
        hidden = {
            engine.state.cards[object_id].object_id
            for seat in ("B", "C", "D")
            for object_id in engine.state.players[seat].zones["hand"]
        }
        projected_json = json.dumps(projected, sort_keys=True)
        self.assertTrue(
            all(object_id not in projected_json for object_id in hidden)
        )
        session.initial_checkpoint = checkpoint_envelope(engine.state)
        session.commands.clear()
        session.decisions.clear()

        before = authoritative_state_hash(engine.state)
        rejected = session.act(
            "pilot:A",
            {"action_id": "choose", "targets": [illegal.ref]},
        )
        self.assertFalse(rejected.ok)
        self.assertEqual(before, authoritative_state_hash(engine.state))
        # Transaction rollback restores an isolated authoritative state tree;
        # discard pre-rollback object aliases before asserting later mutation.
        legal = engine.state.cards[legal.object_id]
        illegal = engine.state.cards[illegal.object_id]
        missing_counter = engine.state.cards[missing_counter.object_id]
        accepted = session.act(
            "pilot:A",
            {"action_id": "choose", "targets": [legal.ref]},
        )
        self.assertTrue(accepted.ok, accepted.summary)
        for seat in engine.seats:
            passed = session.act(f"pilot:{seat}", {"action_id": "pass"})
            self.assertTrue(passed.ok, passed.summary)
        self.assertEqual(
            2,
            legal.counters.get("+1/+1", 0),
            {
                "stack": [item.ref for item in engine.state.stack],
                "pending": (
                    engine.state.pending_decision.kind
                    if engine.state.pending_decision is not None
                    else None
                ),
                "events": [event.code for event in engine.state.events[-8:]],
            },
        )
        self.assertEqual(1, illegal.counters["+1/+1"])
        self.assertNotIn("+1/+1", missing_counter.counters)
        expected_hash = authoritative_state_hash(engine.state)

        with tempfile.TemporaryDirectory() as temporary:
            record_dir = Path(temporary) / "counter-target-predicate-replay"
            session.save(record_dir)
            replay = replay_record(record_dir, self.db, verify=True)
        self.assertTrue(replay["ok"], replay)
        self.assertEqual(5, replay["commands"])
        self.assertEqual(expected_hash, replay["final_state_hash"])


if __name__ == "__main__":
    unittest.main()
