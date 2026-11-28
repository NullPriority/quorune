from __future__ import annotations

import json
from pathlib import Path
from dataclasses import replace
import unittest
from unittest.mock import patch

from quorune.carddb import CardDatabase, CardRecord
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.counter_placement_event_model import PROLIFERATE_ORDER_VERSION_FIELD
from quorune.compiler.proliferate_templates import (
    single_proliferate_effect_template,
)
from quorune.oracle_ir import compile_oracle_card, generated_programs
from quorune.rules.capabilities import (
    CapabilityRegistry,
    load_default_capability_registry,
)
from quorune.rules.node_capability_shapes import (
    single_proliferate_node_capabilities,
)
from tests.common import DB_PATH


ROOT = Path(__file__).resolve().parents[1]
REGISTRY_PATH = ROOT / "quorune" / "rules" / "capability-registry.json"


def proliferate_record(
    text: str,
    *,
    suffix: int,
    type_line: str = "Sorcery",
    name: str | None = None,
    mana_cost: str = "{2}",
    mana_value: float = 2.0,
    keywords: tuple[str, ...] = (),
) -> CardRecord:
    return CardRecord(
        oracle_id=f"00000000-0000-4000-9000-{suffix:012d}",
        name=name or f"Generic Proliferate Fixture {suffix}",
        mana_cost=mana_cost,
        mana_value=mana_value,
        type_line=type_line,
        oracle_text=text,
        power="2" if "Creature" in type_line else None,
        toughness="2" if "Creature" in type_line else None,
        loyalty=None,
        defense=None,
        colors=(),
        color_identity=(),
        keywords=keywords,
        produced_mana=(),
        layout="normal",
        released_at="2026-01-01",
        legalities={"commander": "legal"},
        faces=(),
        raw={},
    )


class ProliferateCompilerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.registry_value = json.loads(
            REGISTRY_PATH.read_text(encoding="utf-8")
        )
        cls.capabilities = load_default_capability_registry()
        cls.database = CardDatabase(DB_PATH)

    @classmethod
    def tearDownClass(cls):
        cls.database.close()

    def test_ripples_requires_one_exact_untargeted_controller_recipient_followup(self):
        from dataclasses import replace
        from quorune.card_programs.binding import bind_semantic_program_runtime
        from quorune.rules.proliferate_phase_out import PROLIFERATE_PHASE_OUT_CAPABILITY

        card = self.database.lookup("Ripples of Potential", fuzzy=False)
        compiled = compile_oracle_card(card, capability_registry=self.capabilities, capability_profile="commander_review")
        self.assertEqual("exact", compiled.status, compiled.material_residuals)
        node, = compiled.faces[0].nodes
        self.assertIsNone(node.target_schema)
        self.assertIn(PROLIFERATE_PHASE_OUT_CAPABILITY,node.capability_dependencies)
        program, = generated_programs(self.database,card,trust_level="trusted",
            capability_registry=self.capabilities,capability_profile="commander_review")
        deps = tuple(capability for capability in program.capability_dependencies
                     if capability != PROLIFERATE_PHASE_OUT_CAPABILITY)
        closure = self.capabilities.closure(deps,profile="commander_review").to_dict()
        stripped = replace(program,capability_dependencies=deps,capability_closure=closure)
        self.assertFalse(bind_semantic_program_runtime(stripped,capability_registry=self.capabilities,profile="commander_review")["strict"])
        for old,new in (("you control","an opponent controls"),("a counter put on them this way","any counters"),
                        ("any number","exactly one")):
            with self.subTest(old=old,new=new):
                edited = replace(card,oracle_text=card.oracle_text.replace(old,new),
                    faces=tuple({**face,"oracle_text":face["oracle_text"].replace(old,new)} for face in card.faces))
                self.assertNotEqual("exact",compile_oracle_card(edited,capability_registry=self.capabilities,
                    capability_profile="commander_review").status)
        raw=json.loads(REGISTRY_PATH.read_bytes())
        next(entry for entry in raw["capabilities"] if entry["id"]==PROLIFERATE_PHASE_OUT_CAPABILITY).update(
            status="blocked",blockers=["Counter-recipient phase-out owner withdrawn"])
        withdrawn=CapabilityRegistry(raw)
        withdrawn.mark_evidence_verified("0"*64)
        self.assertNotEqual("exact",compile_oracle_card(card,capability_registry=withdrawn,
            capability_profile="commander_review").status)
        simple=proliferate_record("Proliferate.",suffix=911)
        self.assertEqual("exact",compile_oracle_card(simple,capability_registry=withdrawn,
            capability_profile="commander_review").status)

    def test_generic_proliferate_program_is_source_spanned_and_capability_closed(
        self,
    ):
        fixtures = (
            proliferate_record("Proliferate.", suffix=1),
            proliferate_record(
                "When this creature enters, proliferate.",
                suffix=2,
                type_line="Creature — Phyrexian",
            ),
            proliferate_record(
                "{2}: Proliferate.",
                suffix=3,
                type_line="Artifact",
            ),
        )
        expected_kinds = (
            "spell_ability",
            "triggered_ability",
            "activated_ability",
        )
        for record, expected_kind in zip(fixtures, expected_kinds, strict=True):
            with self.subTest(kind=expected_kind):
                ir = compile_oracle_card(
                    record,
                    capability_registry=self.capabilities,
                    capability_profile="commander_review",
                )
                nodes = [node for face in ir.faces for node in face.nodes]
                node = next(
                    value
                    for value in nodes
                    if value.template_id == "proliferate-once-v1"
                )
                self.assertEqual("exact", ir.status)
                self.assertEqual(expected_kind, node.kind)
                self.assertEqual(
                    ({"op": "proliferate", PROLIFERATE_ORDER_VERSION_FIELD: 1},),
                    node.effects,
                )
                self.assertIn(
                    "counter.producer.proliferate",
                    node.capability_dependencies,
                )
                if expected_kind == "triggered_ability":
                    self.assertTrue(
                        {
                            "trigger.event.normalized_zone_change",
                            "trigger.placement.apnap",
                        }.issubset(node.capability_dependencies)
                    )
                self.assertEqual(
                    record.oracle_text,
                    record.oracle_text[node.span.start : node.span.end],
                )
                program = next(
                    value
                    for value in generated_programs(
                        self.database,
                        record,
                        trust_level="trusted",
                        capability_registry=self.capabilities,
                        capability_profile="commander_review",
                    )
                    if value.provenance.get("template_id")
                    == "proliferate-once-v1"
                )
                self.assertTrue(program.capability_closure["trusted"])
                self.assertEqual(
                    {
                        "line": 1,
                        "start": 0,
                        "end": len(record.oracle_text),
                    },
                    program.provenance["source_span"],
                )

    def test_fixed_activation_composes_two_ordinary_instructions(self):
        text = "{3}, {T}: Proliferate twice."
        record = proliferate_record(text, suffix=4, type_line="Artifact")
        ir = compile_oracle_card(
            record, capability_registry=self.capabilities, capability_profile="commander_review",
        )
        self.assertEqual("exact", ir.status, ir.material_residuals)
        node, = ir.faces[0].nodes
        self.assertEqual("activated_ability", node.kind)
        self.assertEqual("fixed-proliferate-sequence-v1", node.template_id)
        self.assertEqual(text, text[node.span.start:node.span.end])
        self.assertIsNone(node.target_schema)
        effect = {"op": "proliferate", PROLIFERATE_ORDER_VERSION_FIELD: 1}
        self.assertEqual((effect, effect), node.effects)
        self.assertTrue({
            "counter.producer.proliferate", "resolution.effect_sequence.fixed_clauses",
        }.issubset(node.capability_dependencies))
        self.assertIsNone(single_proliferate_effect_template("Proliferate twice."))

    def test_fixed_activation_runtime_requires_both_versioned_instructions(self):
        record = proliferate_record("{3}, {T}: Proliferate twice.", suffix=5, type_line="Artifact")
        program, = (value for value in generated_programs(
            self.database, record, trust_level="trusted", capability_registry=self.capabilities,
            capability_profile="commander_review",
        ) if value.provenance.get("template_id") == "fixed-proliferate-sequence-v1")

        def binding(candidate):
            return bind_semantic_program_runtime(
                candidate, capability_registry=self.capabilities, profile="commander_review",
            )

        bound = binding(program)
        self.assertTrue(bound["strict"], bound["blockers"])
        for effects in (program.effects[:1], [program.effects[0], {"op": "proliferate"}],
                        [program.effects[0], {"op": "draw", "count": 1}]):
            with self.subTest(effects=effects):
                self.assertFalse(binding(replace(program, effects=effects))["strict"])
        missing = "resolution.effect_sequence.fixed_clauses"
        dependencies = tuple(item for item in program.capability_dependencies if item != missing)
        omitted = replace(
            program, capability_dependencies=dependencies,
            capability_closure=self.capabilities.closure(dependencies, profile="commander_review").to_dict(),
        )
        self.assertIn(f"capability:undeclared_runtime_dependency:{missing}", binding(omitted)["blockers"])

    def test_fixed_activation_requires_the_compound_lowering(self):
        record = proliferate_record("{3}, {T}: Proliferate twice.", suffix=6, type_line="Artifact")
        options = {
            "capability_registry": self.capabilities, "capability_profile": "commander_review",
        }
        self.assertEqual("exact", compile_oracle_card(record, **options).status)
        with patch("quorune.compiler.fixed_effect_clause_sequences.fixed_proliferate_sequence_template",
                   return_value=None):
            self.assertTrue(compile_oracle_card(record, **options).material_residuals)

    def test_fixed_activation_keeps_compound_proliferate_boundaries(self):
        for suffix, result in enumerate((
            "Proliferate three times.",
            "You may proliferate twice.",
            "Target player proliferates twice.",
            "Proliferate twice, then draw a card.",
            "Proliferate, then proliferate again.",
            "If you would proliferate, proliferate twice instead.",
        ), start=50):
            with self.subTest(result=result):
                record = proliferate_record(f"{{3}}, {{T}}: {result}", suffix=suffix, type_line="Artifact")
                ir = compile_oracle_card(
                    record, capability_registry=self.capabilities, capability_profile="commander_review",
                )
                self.assertNotEqual("exact", ir.status)
                self.assertTrue(ir.material_residuals)

    def test_unsupported_proliferate_variants_remain_material_residuals(self):
        for suffix, text in enumerate(
            (
                "Proliferate twice.",
                "Proliferate, then proliferate again.",
                "Target player proliferates.",
                "If you would proliferate, proliferate twice instead.",
                "Proliferate X times.",
            ),
            start=10,
        ):
            with self.subTest(text=text):
                self.assertIsNone(single_proliferate_effect_template(text))
                ir = compile_oracle_card(
                    proliferate_record(text, suffix=suffix),
                    capability_registry=self.capabilities,
                    capability_profile="commander_review",
                )
                self.assertNotEqual("exact", ir.status)
                self.assertTrue(ir.material_residuals)

    def test_representative_commander_cards_use_the_generic_family(self):
        expected = (
            (
                proliferate_record(
                    "Proliferate. (Choose any number of permanents and/or "
                    "players, then give each another counter of each kind "
                    "already there.)\nDraw a card.",
                    suffix=40,
                    name="Contentious Plan",
                    mana_cost="{1}{U}",
                    keywords=("Proliferate",),
                ),
                "spell_ability",
            ),
            (
                proliferate_record(
                    "When this creature enters, proliferate. (Choose any "
                    "number of permanents and/or players, then give each "
                    "another counter of each kind already there.)",
                    suffix=41,
                    name="Kiora's Dambreaker",
                    mana_cost="{5}{U}",
                    mana_value=6.0,
                    type_line="Creature — Leviathan",
                    keywords=("Proliferate",),
                ),
                "triggered_ability",
            ),
            (
                proliferate_record(
                    "Flying\nInfect (This creature deals damage to creatures "
                    "in the form of -1/-1 counters and to players in the "
                    "form of poison counters.)\n{3}{U}: Proliferate. "
                    "(Choose any number of permanents and/or players, then "
                    "give each another counter of each kind already there.)",
                    suffix=42,
                    name="Viral Drake",
                    mana_cost="{3}{U}",
                    mana_value=4.0,
                    type_line="Creature — Phyrexian Drake",
                    keywords=("Flying", "Proliferate", "Infect"),
                ),
                "activated_ability",
            ),
        )
        for record, kind in expected:
            with self.subTest(name=record.name):
                ir = compile_oracle_card(
                    record,
                    capability_registry=self.capabilities,
                    capability_profile="commander_review",
                )
                node = next(
                    node
                    for face in ir.faces
                    for node in face.nodes
                    if node.template_id == "proliferate-once-v1"
                )
                self.assertEqual("exact", ir.status)
                self.assertEqual(kind, node.kind)
                self.assertEqual(
                    ({"op": "proliferate", PROLIFERATE_ORDER_VERSION_FIELD: 1},),
                    node.effects,
                )

    def test_proliferate_capability_shape_rejects_mutations(self):
        for effects, target_schema, mechanics in (
            (({"op": "proliferate", "times": 2},), None, ("proliferate",)),
            (({"op": "proliferate", PROLIFERATE_ORDER_VERSION_FIELD: True},), None, ("proliferate",)),
            (({"op": "proliferate", PROLIFERATE_ORDER_VERSION_FIELD: 2},), None, ("proliferate",)),
            (({"op": "proliferate"},), {"count": 1}, ("proliferate",)),
            (({"op": "proliferate"},), None, ()),
            (({"op": "proliferate"}, {"op": "draw", "count": 1}), None, ("proliferate",)),
        ):
            with self.subTest(effects=effects):
                self.assertEqual(
                    (),
                    single_proliferate_node_capabilities(
                        effects=effects,
                        target_schema=target_schema,
                        mechanic_ids=mechanics,
                    ),
                )

    def test_proliferate_dependency_mutation_fails_closed(self):
        value = json.loads(json.dumps(self.registry_value))
        dependency = next(
            row
            for row in value["capabilities"]
            if row["id"] == "counter.placement.quantity_replacement"
        )
        dependency["status"] = "blocked"
        dependency["blockers"] = ["test mutation"]
        ir = compile_oracle_card(
            proliferate_record("Proliferate.", suffix=20),
            capability_registry=CapabilityRegistry(value),
            capability_profile="commander_review",
        )
        self.assertNotEqual("exact", ir.status)
        self.assertTrue(
            any(
                "counter.placement.quantity_replacement" in blocker
                for residual in ir.material_residuals
                for blocker in residual.blockers
            )
        )

    def test_proliferate_compiler_mutant_is_killed(self):
        record = proliferate_record("Proliferate.", suffix=30)

        def assert_exact() -> None:
            ir = compile_oracle_card(
                record,
                capability_registry=self.capabilities,
                capability_profile="commander_review",
            )
            self.assertEqual("exact", ir.status)

        assert_exact()
        with patch(
            "quorune.compiler.resolution_effect_templates."
            "single_proliferate_effect_template",
            return_value=None,
        ):
            with self.assertRaises(AssertionError):
                assert_exact()


if __name__ == "__main__":
    unittest.main()
