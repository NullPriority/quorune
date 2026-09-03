from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from common import ROOT, keep_all, make_session
from quorune.carddb import CardDatabase, CardRecord
from quorune.compiler.direct_target import (
    DirectPermanentTargetSpec,
    direct_permanent_target_spec,
)
from quorune.compiler.destruction_templates import (
    targeted_destruction_effect_template,
)
from quorune.deck import DeckLoader
from quorune.errors import GameRuleError
from quorune.model import StackItem
from quorune.oracle_ir import compile_oracle_card
from quorune.record import (
    authoritative_state_hash,
    checkpoint_envelope,
    replay_record,
)
from quorune.rules.capabilities import CapabilityRegistry
from quorune.semantics import SemanticProgram
from quorune.targets import TargetGroup
from quorune.target_numeric import (
    TargetNumericCharacteristic,
    TargetNumericCharacteristicSpec,
    TargetNumericComparison,
)
from scripts.build_test_database import build_fixture_database


TARGET_CAPABILITY = "target.permanent.characteristic_predicate"
REGISTRY_PATH = (
    Path(__file__).resolve().parents[1]
    / "quorune"
    / "rules"
    / "capability-registry.json"
)


def current_capabilities() -> CapabilityRegistry:
    registry = CapabilityRegistry(
        json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
    )
    registry.mark_evidence_verified("0" * 64)
    return registry


def record(
    text: str,
    *,
    suffix: int,
    type_line: str = "Instant",
) -> CardRecord:
    return CardRecord(
        oracle_id=f"00000000-0000-4000-8000-{suffix:012d}",
        name=f"Fixed Mana Value Target {suffix}",
        mana_cost="{1}{B}",
        mana_value=2.0,
        oracle_text=text,
        type_line=type_line,
        power="2" if "Creature" in type_line else None,
        toughness="2" if "Creature" in type_line else None,
        loyalty=None,
        defense=None,
        colors=("B",),
        color_identity=("B",),
        keywords=(),
        produced_mana=(),
        layout="normal",
        released_at="2026-01-01",
        legalities={"commander": "legal"},
        faces=(),
        raw={},
    )


class FixedManaValueTargetCompilerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.capabilities = current_capabilities()

    def compile(self, value: CardRecord, *, registry=None):
        return compile_oracle_card(
            value,
            capability_registry=registry or self.capabilities,
            capability_profile="commander_review",
        )

    def test_fixed_mana_value_targets_compile_across_effect_contexts(self):
        fixtures = (
            record(
                "Destroy target nonland permanent with mana value 3 or less.",
                suffix=202_003_001,
            ),
            record(
                "When this creature enters, exile target permanent with "
                "mana value 4 or greater.",
                suffix=202_003_002,
                type_line="Creature — Test",
            ),
            record(
                "{1}, {T}: Tap target creature with mana value 2 or greater.",
                suffix=202_003_003,
                type_line="Artifact Creature — Test",
            ),
            record(
                "Choose one —\n"
                "• Destroy target creature with mana value 2 or less.\n"
                "• Exile target artifact with mana value 1.",
                suffix=202_003_004,
            ),
        )
        expected_numeric_fields = {
            "mana_value",
            "mana_value_min",
            "mana_value_max",
        }
        for fixture in fixtures:
            with self.subTest(text=fixture.oracle_text):
                ir = self.compile(fixture)
                self.assertEqual("exact", ir.status, ir.material_residuals)
                nodes = [
                    node
                    for node in ir.faces[0].nodes
                    if node.target_schema is not None
                ]
                self.assertTrue(nodes)
                for node in nodes:
                    modes = dict(node.target_schema).get("modes")
                    schemas = (
                        [definition for definition in modes.values()]
                        if isinstance(modes, dict)
                        else [node.target_schema]
                    )
                    self.assertTrue(
                        all(
                            expected_numeric_fields.intersection(schema)
                            for schema in schemas
                        )
                    )
                    self.assertIn(TARGET_CAPABILITY, node.capability_dependencies)

    def test_mana_value_target_model_roundtrips_and_rejects_competing_bounds(self):
        for subject in (
            "target permanent with mana value 4 or greater",
            "target creature with mana value 3 or less",
            "target artifact with mana value 1",
            "target nonland permanent with mana value 2 or less",
            "target creature an opponent controls with mana value 2 or less",
        ):
            with self.subTest(subject=subject):
                spec = direct_permanent_target_spec(subject)
                self.assertIsNotNone(spec)
                assert spec is not None
                self.assertEqual(
                    spec,
                    DirectPermanentTargetSpec.from_target_schema(
                        spec.to_target_schema()
                    ),
                )

        value = direct_permanent_target_spec(
            "target creature with mana value 3 or less"
        ).to_target_schema()
        value["mana_value_min"] = 1
        with self.assertRaisesRegex(ValueError, "mana-value"):
            DirectPermanentTargetSpec.from_target_schema(value)
        for invalid in (-1, 1.5, True):
            with self.subTest(invalid=invalid):
                with self.assertRaisesRegex(ValueError, "mana-value"):
                    DirectPermanentTargetSpec(
                        types_any=("creature",),
                        mana_value_max=invalid,
                    )

    def test_fixed_power_toughness_targets_compile_across_effect_contexts(self):
        fixtures = (
            (
                record(
                    "Destroy target creature with power 4 or greater.",
                    suffix=202_003_050,
                ),
                "spell_ability",
                ("power", "at_least", 4),
            ),
            (
                record(
                    "When this creature enters, exile target creature with "
                    "toughness 3 or less.",
                    suffix=202_003_051,
                    type_line="Creature — Test",
                ),
                "triggered_ability",
                ("toughness", "at_most", 3),
            ),
            (
                record(
                    "{1}, {T}: Tap target creature with power or toughness "
                    "4 or greater.",
                    suffix=202_003_052,
                    type_line="Artifact Creature — Test",
                ),
                "activated_ability",
                ("power_or_toughness", "at_least", 4),
            ),
            (
                record(
                    "Destroy target attacking or blocking creature with total "
                    "power and toughness 5 or less.",
                    suffix=202_003_053,
                ),
                "spell_ability",
                ("total_power_and_toughness", "at_most", 5),
            ),
        )
        for fixture, kind, expected in fixtures:
            with self.subTest(text=fixture.oracle_text):
                ir = self.compile(fixture)
                self.assertEqual("exact", ir.status, ir.material_residuals)
                node = ir.faces[0].nodes[0]
                self.assertEqual(kind, node.kind)
                numeric = node.target_schema["numeric_characteristic"]
                self.assertEqual(
                    {
                        "characteristic": expected[0],
                        "comparison": expected[1],
                        "value": expected[2],
                    },
                    numeric,
                )
                self.assertIn(TARGET_CAPABILITY, node.capability_dependencies)

    def test_numeric_target_model_roundtrips_and_rejects_open_forms(self):
        subjects = (
            "target creature with power 4 or greater",
            "target creature with toughness 3 or less",
            "target creature with power or toughness 4 or greater",
            "target creature with total power and toughness 5 or less",
            "target attacking creature with power 2 or less",
        )
        for subject in subjects:
            with self.subTest(subject=subject):
                spec = direct_permanent_target_spec(subject)
                self.assertIsNotNone(spec)
                assert spec is not None
                schema = spec.to_target_schema()
                self.assertEqual(
                    spec,
                    DirectPermanentTargetSpec.from_target_schema(schema),
                )
                self.assertIsInstance(
                    TargetGroup.from_mapping(schema).numeric_characteristic,
                    TargetNumericCharacteristicSpec,
                )

        for invalid in (
            "target artifact with power 4 or greater",
            "target artifact, enchantment, or creature with power 4 or greater",
            "target creature with power X or greater",
            "target creature with greatest power",
            "target creature with power 4",
        ):
            with self.subTest(invalid=invalid):
                self.assertIsNone(direct_permanent_target_spec(invalid))

        with self.assertRaisesRegex(ValueError, "cannot mix"):
            DirectPermanentTargetSpec(
                types_any=("creature",),
                mana_value_max=3,
                numeric_characteristic=TargetNumericCharacteristicSpec(
                    TargetNumericCharacteristic.POWER,
                    TargetNumericComparison.AT_LEAST,
                    4,
                ),
            )
        for invalid in (-1, 1.5, True):
            with self.subTest(value=invalid), self.assertRaisesRegex(
                ValueError,
                "nonnegative integer",
            ):
                TargetNumericCharacteristicSpec(
                    TargetNumericCharacteristic.POWER,
                    TargetNumericComparison.AT_LEAST,
                    invalid,
                )

    def test_adjacent_numeric_target_grammar_remains_material_residual(self):
        variants = (
            "Destroy target creature with mana value X or less.",
            "Destroy target creature with total mana value 3 or less.",
            "Destroy target creature if it has mana value 3 or less.",
            "Destroy target tapped creature with mana value 3 or less.",
            "Destroy target artifact, enchantment, or creature with power 3 "
            "or less.",
            "Return target creature card with mana value 3 or less from your "
            "graveyard to the battlefield.",
        )
        for index, text in enumerate(variants, start=1):
            with self.subTest(text=text):
                ir = self.compile(record(text, suffix=202_003_100 + index))
                self.assertNotEqual("exact", ir.status)
                self.assertTrue(ir.material_residuals)

    def test_mana_value_dependency_and_parser_mutations_fail_closed(self):
        fixture = record(
            "Destroy target creature with mana value 3 or less.",
            suffix=202_003_200,
        )
        value = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
        target = next(
            row for row in value["capabilities"] if row["id"] == TARGET_CAPABILITY
        )
        target["status"] = "blocked"
        target["blockers"] = ["focused mana-value mutation"]
        registry = CapabilityRegistry(value)
        registry.mark_evidence_verified("0" * 64)
        self.assertNotEqual("exact", self.compile(fixture, registry=registry).status)

        with patch(
            "quorune.compiler.direct_target._strip_mana_value_predicate",
            side_effect=lambda phrase: (phrase, {}),
        ):
            mutated = self.compile(fixture)
        self.assertNotEqual("exact", mutated.status)

        numeric = record(
            "Destroy target creature with power 3 or less.",
            suffix=202_003_201,
        )
        self.assertEqual("exact", self.compile(numeric).status)
        with patch(
            "quorune.compiler.direct_target."
            "_strip_numeric_characteristic_predicate",
            side_effect=lambda phrase: (phrase, None),
        ):
            self.assertNotEqual("exact", self.compile(numeric).status)


class FixedManaValueTargetRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        database = Path(cls.temporary.name) / "fixed-mana-value-target.sqlite3"
        build_fixture_database(
            [ROOT / "tests" / "fixtures" / "scryfall-exact-lists.json"],
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

    def test_current_copy_derived_mana_value_governs_offer_command_and_resolution(
        self,
    ):
        session = make_session(
            self.db,
            self.mishra,
            self.zimone,
            players=2,
            seed=20200301,
        )
        keep_all(session)
        engine = session.engine
        low = next(
            card
            for card in engine.state.cards.values()
            if card.owner == "B" and card.printed_name == "Sol Ring"
        )
        high = next(
            card
            for card in engine.state.cards.values()
            if card.owner == "B"
            and (
                candidate := self.db.by_oracle_id(card.oracle_id)
            ).is_permanent_spell
            and candidate.mana_value > 3
        )
        for card in (low, high):
            engine.move_card(card.object_id, "battlefield", controller="B")
        template = targeted_destruction_effect_template(
            "Destroy target nonland permanent with mana value 3 or less."
        )
        assert template is not None
        group = TargetGroup.from_mapping(template.target_schema)

        def legal_refs() -> tuple[str, ...]:
            return tuple(
                str(row["ref"])
                for row in engine._target_candidate_rows("A", group)
                if engine._target_row_matches(
                    "A",
                    group,
                    row,
                    source_ref=None,
                )
            )

        initial = legal_refs()
        self.assertIn(low.ref, initial)
        self.assertNotIn(high.ref, initial)
        public = engine._public_target_schema(
            "A",
            template.target_schema,
            source_ref=None,
        )
        self.assertIsNotNone(public)
        assert public is not None
        serialized = json.dumps(public, sort_keys=True)
        self.assertIn(low.ref, serialized)
        self.assertNotIn(low.object_id, serialized)

        low.annotations["copy_overrides"] = {"mana_value": 5}
        self.assertNotIn(low.ref, legal_refs())
        with self.assertRaises(GameRuleError):
            engine._validate_semantic_targets(
                "A",
                None,
                [low.ref],
                source_ref=None,
                target_schema=template.target_schema,
            )
        low.annotations.pop("copy_overrides")
        self.assertIn(low.ref, legal_refs())
        selected, grouped = engine._validate_semantic_targets(
            "A",
            None,
            [low.ref],
            source_ref=None,
            target_schema=template.target_schema,
        )
        program = SemanticProgram(
            key="fixture:fixed-mana-value-target",
            label="Fixed mana-value target",
            effects=[dict(template.effects[0])],
            target_schema=dict(template.target_schema),
            trust_level="provisional",
        )
        engine.semantics.put(program)
        item = StackItem(
            stack_id="fixed-mana-value-target",
            ref="S-fixed-mana-value-target",
            kind="triggered_ability",
            controller="A",
            label=program.label,
            semantic_key=program.key,
            targets=selected,
            visibility=list(engine.seats),
            context={
                "target_groups": grouped,
                "target_snapshots": {
                    low.ref: engine._target_snapshot(low.ref)
                },
                "targets_revalidated": False,
                "targets_chosen_at_creation": True,
            },
        )
        engine.state.stack.append(item)
        low.annotations["copy_overrides"] = {"mana_value": 5}
        self.assertFalse(engine._revalidate_resolution_targets(item))
        self.assertNotIn(item, engine.state.stack)
        self.assertEqual("battlefield", low.zone)

    def test_current_power_toughness_governs_offer_command_and_resolution(self):
        session = make_session(
            self.db,
            self.mishra,
            self.zimone,
            players=2,
            seed=20200302,
        )
        keep_all(session)
        engine = session.engine
        engine.permissions.invalidate_current()
        engine.state.pending_decision = None
        engine.state.priority_player = None
        engine.state.priority_passes = []

        def creature(name: str, power: str, toughness: str):
            ref = engine.create_token(
                "B",
                name=name,
                characteristics={
                    "type_line": "Token Creature — Test",
                    "power": power,
                    "toughness": toughness,
                },
            )[0]
            return engine._resolve_object("B", ref, zones={"battlefield"})

        low = creature("Low target", "3", "2")
        high = creature("High target", "5", "1")
        temporarily_high = creature("Temporary high target", "5", "1")
        tough = creature("Tough target", "1", "4")
        unknown = creature("Unknown target", "*", "4")
        dynamic_ref = engine.create_token(
            "B",
            name="Dynamic target",
            characteristics={
                "type_line": "Token Artifact Creature — Test",
                "power": "1",
                "toughness": "1",
                "ability_fragments": [
                    {
                        "kind": "dynamic_power_toughness",
                        "value": {
                            "schema_version": 1,
                            "count_kind": "controller_battlefield_artifacts",
                            "calculation": "per_matching_object",
                            "power": 1,
                            "toughness": 1,
                            "minimum_count": 0,
                        },
                    }
                ],
            },
        )[0]
        dynamic = engine._resolve_object(
            "B",
            dynamic_ref,
            zones={"battlefield"},
        )
        template = targeted_destruction_effect_template(
            "Destroy target creature with power 4 or greater."
        )
        assert template is not None
        group = TargetGroup.from_mapping(template.target_schema)

        def legal_refs(current_group=group) -> tuple[str, ...]:
            return tuple(
                str(row["ref"])
                for row in engine._target_candidate_rows("A", current_group)
                if engine._target_row_matches(
                    "A",
                    current_group,
                    row,
                    source_ref=None,
                )
            )

        self.assertNotIn(low.ref, legal_refs())
        self.assertIn(high.ref, legal_refs())
        self.assertNotIn(unknown.ref, legal_refs())

        # Target legality consumes the canonical current numeric boundary,
        # including counter and legacy duration deltas that are intentionally
        # not folded into the rendered characteristic mapping itself.
        engine.apply_effect(
            {
                "op": "place_counters",
                "card": low.ref,
                "counter": "+1/+1",
                "amount": 1,
                "source": "fixture:numeric-target-counter",
            },
            actor="A",
        )
        self.assertIn(low.ref, legal_refs())
        low.counters.clear()
        self.assertIn(temporarily_high.ref, legal_refs())
        engine.apply_effect(
            {
                "op": "modify_stats_until_end_of_turn",
                "card": temporarily_high.ref,
                "power": -2,
                "toughness": 0,
            },
            actor="A",
        )
        self.assertNotIn(temporarily_high.ref, legal_refs())
        public = engine._public_target_schema(
            "A",
            template.target_schema,
            source_ref=None,
        )
        serialized = json.dumps(public, sort_keys=True)
        self.assertIn(high.ref, serialized)
        self.assertNotIn(high.object_id, serialized)

        either = TargetGroup.from_mapping(
            direct_permanent_target_spec(
                "target creature with power or toughness 4 or greater"
            ).to_target_schema()
        )
        self.assertIn(tough.ref, legal_refs(either))
        self.assertIn(unknown.ref, legal_refs(either))
        dynamic_bound = TargetGroup.from_mapping(
            direct_permanent_target_spec(
                "target creature with power 2 or greater"
            ).to_target_schema()
        )
        self.assertIn(dynamic.ref, legal_refs(dynamic_bound))
        total = TargetGroup.from_mapping(
            direct_permanent_target_spec(
                "target creature with total power and toughness 5 or less"
            ).to_target_schema()
        )
        self.assertIn(low.ref, legal_refs(total))
        self.assertNotIn(high.ref, legal_refs(total))

        selected, grouped = engine._validate_semantic_targets(
            "A",
            None,
            [high.ref],
            source_ref=None,
            target_schema=template.target_schema,
        )
        program = SemanticProgram(
            key="fixture:fixed-power-target",
            label="Fixed power target",
            effects=[dict(template.effects[0])],
            target_schema=dict(template.target_schema),
            trust_level="provisional",
        )
        engine.semantics.put(program)
        item = StackItem(
            stack_id="fixed-power-target",
            ref="S-fixed-power-target",
            kind="triggered_ability",
            controller="A",
            label=program.label,
            semantic_key=program.key,
            targets=selected,
            visibility=list(engine.seats),
            context={
                "target_groups": grouped,
                "target_snapshots": {
                    high.ref: engine._target_snapshot(high.ref)
                },
                "targets_revalidated": False,
                "targets_chosen_at_creation": True,
            },
        )
        engine.state.stack.append(item)
        high.annotations["copy_overrides"] = {"power": "2"}
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
        self.assertNotIn(item, engine.state.stack)
        self.assertEqual("battlefield", high.zone)
        expected_hash = authoritative_state_hash(engine.state)
        with tempfile.TemporaryDirectory() as temporary:
            record_dir = Path(temporary) / "fixed-power-target"
            session.save(record_dir)
            replay = replay_record(record_dir, self.db, verify=True)
        self.assertTrue(replay["ok"], replay)
        self.assertEqual(len(engine.seats), replay["commands"])
        self.assertEqual(expected_hash, replay["final_state_hash"])


if __name__ == "__main__":
    unittest.main()
