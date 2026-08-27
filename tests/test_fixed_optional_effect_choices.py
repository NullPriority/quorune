from __future__ import annotations

from pathlib import Path
import tempfile
import unittest
from unittest import mock

from common import keep_all, load_assets, make_session
from quorune.carddb import CardRecord
from quorune.compiler.optional_effect_templates import (
    FIXED_OPTIONAL_EFFECT_CAPABILITY,
    FIXED_OPTIONAL_EFFECT_MECHANIC,
    OPTIONAL_EFFECT_OPERATION,
)
from quorune.model import StackItem
from quorune.oracle_ir import compile_oracle_card
from quorune.projection import StateProjector
from quorune.record import checkpoint_envelope, replay_record
from quorune.rules.capabilities import (
    capability_dependencies_for_node,
    load_default_capability_registry,
)
from quorune.semantic_choices import (
    SemanticChoiceContext,
    SemanticChoiceContinuation,
    SemanticChoiceError,
    SemanticChoiceFrame,
    SnapshotSemanticChoiceQuery,
)
from quorune.semantic_choices.optional_effect import OptionalEffectHandler
from quorune.semantics import SemanticProgram


def optional_record(
    text: str,
    *,
    type_line: str = "Instant",
) -> CardRecord:
    return CardRecord(
        oracle_id="fixture:fixed-optional-effect-choice",
        name="Optional Effect Witness",
        mana_cost="{2}{U}",
        mana_value=3.0,
        type_line=type_line,
        oracle_text=text,
        power="2" if "Creature" in type_line else None,
        toughness="2" if "Creature" in type_line else None,
        loyalty=None,
        defense=None,
        colors=("U",),
        color_identity=("U",),
        keywords=(),
        produced_mana=(),
        layout="normal",
        released_at="2026-01-01",
        legalities={"commander": "legal"},
        faces=(),
        raw={},
    )


def choice_query() -> SnapshotSemanticChoiceQuery:
    return SnapshotSemanticChoiceQuery(
        seat_order=("A", "B", "C", "D"),
        active_order=("A", "B", "C", "D"),
    )


def choice_context(*, actor: str = "A") -> SemanticChoiceContext:
    return SemanticChoiceContext(
        actor=actor,
        stack_ref="S-optional-effect",
        stack_controller=actor,
        stack_label="Optional effect witness",
        source_ref="source-optional-effect",
        card_ref=None,
        semantic_program_id="test:optional-effect",
        semantic_program_version=1,
        query=choice_query(),
    )


def choice_continuation(effect) -> SemanticChoiceContinuation:
    return SemanticChoiceContinuation(
        handler_id="choice.effect.optional-fixed.v1",
        handler_version=1,
        stack_ref="S-optional-effect",
        effect=effect,
        remaining=(),
        destination=None,
        note="Optional effect witness",
        semantic_frame=SemanticChoiceFrame(
            semantic_program_id="test:optional-effect",
            semantic_program_version=1,
            stack_object="S-optional-effect",
            instruction_pointer=0,
            controller="A",
        ),
    )


class FixedOptionalEffectCompilerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.capabilities = load_default_capability_registry()

    def compile(self, text: str, *, type_line: str = "Instant"):
        return compile_oracle_card(
            optional_record(text, type_line=type_line),
            capability_registry=self.capabilities,
            capability_profile="commander_review",
        )

    def test_spell_trigger_and_activation_share_optional_effect_lowering(self):
        fixtures = (
            (
                "You may destroy target artifact.",
                "Instant",
                "spell_ability",
                "destroy",
            ),
            (
                "When this creature enters, you may return target creature "
                "to its owner's hand.",
                "Creature — Wizard",
                "triggered_ability",
                "bounce",
            ),
            (
                "{2}, {T}: You may create a Treasure token.",
                "Artifact",
                "activated_ability",
                "create_token",
            ),
            (
                "Whenever you cast a noncreature spell, you may gain 2 life.",
                "Creature — Wizard",
                "triggered_ability",
                "life",
            ),
        )
        for text, type_line, kind, nested_operation in fixtures:
            with self.subTest(text=text):
                ir = self.compile(text, type_line=type_line)
                self.assertEqual("exact", ir.status, ir.material_residuals)
                node = ir.faces[0].nodes[0]
                self.assertEqual(kind, node.kind)
                self.assertEqual(OPTIONAL_EFFECT_OPERATION, node.effects[0]["op"])
                self.assertEqual("$controller", node.effects[0]["player"])
                self.assertEqual(
                    nested_operation,
                    node.effects[0]["effects"][0]["op"],
                )
                self.assertIn(
                    FIXED_OPTIONAL_EFFECT_CAPABILITY,
                    node.capability_dependencies,
                )
                self.assertIn(FIXED_OPTIONAL_EFFECT_MECHANIC, node.mechanics)

    def test_optional_clause_preserves_mandatory_sequence_sibling(self):
        text = (
            "When this creature enters, you may destroy target artifact. "
            "Draw a card."
        )
        ir = self.compile(text, type_line="Creature — Wizard")

        self.assertEqual("exact", ir.status, ir.material_residuals)
        node = ir.faces[0].nodes[0]
        self.assertEqual(2, len(node.effects))
        self.assertEqual(OPTIONAL_EFFECT_OPERATION, node.effects[0]["op"])
        self.assertEqual("destroy", node.effects[0]["effects"][0]["op"])
        self.assertEqual("draw", node.effects[1]["op"])
        self.assertIn(
            "resolution.effect_sequence.fixed_clauses",
            node.capability_dependencies,
        )

    def test_existing_optional_counter_identity_is_preserved(self):
        ir = self.compile(
            "Whenever you cast a noncreature spell, you may put a +1/+1 "
            "counter on this creature.",
            type_line="Creature — Wizard",
        )

        self.assertEqual("exact", ir.status, ir.material_residuals)
        node = ir.faces[0].nodes[0]
        self.assertEqual("offer_optional_counter_placement", node.effects[0]["op"])
        self.assertNotIn(FIXED_OPTIONAL_EFFECT_CAPABILITY, node.capability_dependencies)

    def test_optional_effect_exclusions_remain_material(self):
        fixtures = (
            "You may pay {1}. If you do, draw a card.",
            "You may choose target creature.",
            "You may have target creature gain flying until end of turn.",
            "You may destroy target artifact if you control a Wizard.",
            "You may destroy target artifact and you may draw a card.",
            "You may destroy target artifact. If you do, draw a card.",
        )
        for text in fixtures:
            with self.subTest(text=text):
                ir = self.compile(text)
                self.assertNotEqual("exact", ir.status)
                self.assertTrue(ir.material_residuals)

    def test_optional_effect_shape_and_handler_mutants_fail_closed(self):
        text = "You may destroy target artifact."
        ir = self.compile(text)
        node = ir.faces[0].nodes[0]
        expected = set(node.capability_dependencies)
        self.assertIn(FIXED_OPTIONAL_EFFECT_CAPABILITY, expected)

        wrapper = dict(node.effects[0])
        nested = dict(wrapper["effects"][0])
        mutants = (
            ({**wrapper, "player": "$source.controller"}, node.target_schema),
            ({**wrapper, "unexpected": True}, node.target_schema),
            ({**wrapper, "effects": []}, node.target_schema),
            ({**wrapper, "effects": [{"op": "unknown"}]}, node.target_schema),
            (
                {
                    **wrapper,
                    "effects": [
                        {
                            "op": OPTIONAL_EFFECT_OPERATION,
                            "player": "$controller",
                            "effects": [nested],
                        }
                    ],
                },
                node.target_schema,
            ),
            (wrapper, None),
        )
        for effect, schema in mutants:
            with self.subTest(effect=effect, schema=schema):
                self.assertFalse(
                    capability_dependencies_for_node(
                        effects=(effect,),
                        target_schema=schema,
                        mechanic_ids=node.mechanics,
                    )
                )
        self.assertFalse(
            capability_dependencies_for_node(
                effects=node.effects,
                target_schema=node.target_schema,
                mechanic_ids=tuple(
                    mechanic
                    for mechanic in node.mechanics
                    if mechanic != FIXED_OPTIONAL_EFFECT_MECHANIC
                ),
            )
        )

        with mock.patch(
            "quorune.oracle_ir.fixed_optional_effect_template",
            return_value=None,
        ):
            mutant = self.compile(text)
        self.assertNotEqual("exact", mutant.status)

        handler = OptionalEffectHandler()
        malformed = {
            "op": OPTIONAL_EFFECT_OPERATION,
            "player": "A",
            "effects": [{"op": "unknown"}],
        }
        with self.assertRaisesRegex(
            SemanticChoiceError,
            "not represented",
        ):
            handler.prepare(malformed, choice_context())


class FixedOptionalEffectChoiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.handler = OptionalEffectHandler()
        self.effect = {
            "op": OPTIONAL_EFFECT_OPERATION,
            "player": "A",
            "effects": [
                {
                    "op": "draw",
                    "player": "A",
                    "count": 1,
                    "private": True,
                }
            ],
        }

    def test_optional_choice_apply_and_decline_resume_exact_effect(self):
        prepared = self.handler.prepare(self.effect, choice_context())

        self.assertEqual(
            ("apply", "decline"),
            prepared.request.choice.legal_values,
        )
        applied = self.handler.complete(
            choice_continuation(self.effect),
            {"choice": "apply"},
            choice_query(),
        )
        declined = self.handler.complete(
            choice_continuation(self.effect),
            {"choice": "decline"},
            choice_query(),
        )
        self.assertEqual("draw", applied.prepend_effects[0]["op"])
        self.assertFalse(declined.prepend_effects)

    def test_optional_effect_handler_rejects_wrong_chooser_and_nested_choice(self):
        with self.assertRaisesRegex(SemanticChoiceError, "active controller"):
            self.handler.prepare(self.effect, choice_context(actor="B"))
        with self.assertRaisesRegex(SemanticChoiceError, "cannot nest"):
            self.handler.prepare(
                {
                    **self.effect,
                    "effects": [
                        {
                            "op": OPTIONAL_EFFECT_OPERATION,
                            "player": "A",
                            "effects": self.effect["effects"],
                        }
                    ],
                },
                choice_context(),
            )
        with self.assertRaisesRegex(SemanticChoiceError, "fields are malformed"):
            self.handler.prepare(
                {**self.effect, "unexpected": True},
                choice_context(),
            )


class FixedOptionalEffectIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.db, cls.mishra, cls.zimone = load_assets()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.db.close()

    def test_optional_effect_choice_is_controller_scoped_atomic_private_and_replays(
        self,
    ):
        session = make_session(
            self.db,
            self.mishra,
            self.zimone,
            players=4,
            seed=13601,
            auto_pass_empty=False,
        )
        keep_all(session)
        engine = session.engine
        engine.permissions.invalidate_current()
        engine.state.pending_decision = None
        engine.state.priority_player = None
        program = SemanticProgram(
            key="test:fixed-optional-effect-replay",
            label="Fixed optional effect replay witness",
            effects=[
                {
                    "op": OPTIONAL_EFFECT_OPERATION,
                    "player": "$controller",
                    "effects": [
                        {
                            "op": "draw",
                            "player": "$controller",
                            "count": 1,
                            "private": True,
                        }
                    ],
                }
            ],
            trust_level="provisional",
        )
        engine.semantics.put(program)
        item = StackItem(
            stack_id="fixed-optional-effect-replay",
            ref="S-fixed-optional-effect-replay",
            kind="triggered_ability",
            controller="B",
            label=program.label,
            semantic_key=program.key,
            visibility=["A", "B", "C", "D"],
        )
        engine.state.stack.append(item)
        before = len(engine.state.players["B"].zones["hand"])

        engine._begin_resolve_item(
            item,
            program.effects,
            None,
            note="fixed optional effect replay",
        )

        self.assertEqual("semantic.choice", engine.state.pending_decision.kind)
        self.assertEqual(before, len(engine.state.players["B"].zones["hand"]))
        projector = StateProjector(self.db, engine.state)
        self.assertIsNotNone(projector._decision("pilot:B"))
        for seat in ("A", "C", "D"):
            with self.subTest(seat=seat):
                self.assertIsNone(projector._decision(f"pilot:{seat}"))
        session.initial_checkpoint = checkpoint_envelope(engine.state)
        session.commands.clear()
        session.decisions.clear()

        result = session.act(
            "pilot:B",
            {
                "action_id": "choose",
                "choice": "apply",
                "reason": "Apply the represented optional effect.",
            },
        )

        self.assertTrue(result.ok, result.summary)
        self.assertEqual(
            before + 1,
            len(engine.state.players["B"].zones["hand"]),
        )
        with tempfile.TemporaryDirectory() as temporary:
            record_dir = Path(temporary) / "fixed-optional-effect-replay"
            session.save(record_dir)
            replay = replay_record(record_dir, self.db, verify=True)
        self.assertTrue(replay["ok"], replay)


if __name__ == "__main__":
    unittest.main()
