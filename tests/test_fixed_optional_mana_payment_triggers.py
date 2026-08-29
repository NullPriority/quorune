from __future__ import annotations

from pathlib import Path
import tempfile
import unittest
from unittest import mock

from common import keep_all, load_assets, make_session
from quorune.carddb import CardRecord
from quorune.compiler.optional_payment_templates import (
    FIXED_OPTIONAL_MANA_PAYMENT_CAPABILITY,
    FIXED_OPTIONAL_MANA_PAYMENT_MECHANIC,
    OPTIONAL_MANA_PAYMENT_OPERATION,
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
from quorune.semantic_choices.payments import PAYMENT_CHOICE_HANDLERS
from quorune.semantics import SemanticProgram


def payment_record(
    text: str,
    *,
    name: str = "Fixed Payment Witness",
    type_line: str = "Creature — Fixture",
) -> CardRecord:
    return CardRecord(
        oracle_id="fixture:fixed-optional-mana-payment",
        name=name,
        mana_cost="{1}{G}",
        mana_value=2.0,
        type_line=type_line,
        oracle_text=text,
        power="2" if "Creature" in type_line else None,
        toughness="2" if "Creature" in type_line else None,
        loyalty=None,
        defense=None,
        colors=("G",),
        color_identity=("G",),
        keywords=(),
        produced_mana=(),
        layout="normal",
        released_at="2026-01-01",
        legalities={"commander": "legal"},
        faces=(),
        raw={},
    )


def payment_handler():
    return next(
        handler
        for handler in PAYMENT_CHOICE_HANDLERS
        if handler.operation == OPTIONAL_MANA_PAYMENT_OPERATION
    )


def payment_query(*, payable: bool = True) -> SnapshotSemanticChoiceQuery:
    requirements = {
        "GENERIC": 0,
        "W": 0,
        "U": 0,
        "B": 0,
        "R": 0,
        "G": 1,
        "C": 0,
    }
    key = SnapshotSemanticChoiceQuery._cost_key("A", requirements)
    return SnapshotSemanticChoiceQuery(
        seat_order=("A", "B", "C", "D"),
        active_order=("A", "B", "C", "D"),
        affordable_costs=frozenset({key} if payable else ()),
    )


def payment_context(*, query=None) -> SemanticChoiceContext:
    return SemanticChoiceContext(
        actor="A",
        stack_ref="S-fixed-payment",
        stack_controller="A",
        stack_label="Fixed payment witness",
        source_ref="source-fixed-payment",
        card_ref=None,
        semantic_program_id="test:fixed-payment",
        semantic_program_version=1,
        query=query or payment_query(),
    )


def payment_continuation(effect) -> SemanticChoiceContinuation:
    return SemanticChoiceContinuation(
        handler_id="choice.payment.optional-fixed-effect.v1",
        handler_version=1,
        stack_ref="S-fixed-payment",
        effect=effect,
        remaining=(),
        destination=None,
        note="Fixed payment witness",
        semantic_frame=SemanticChoiceFrame(
            semantic_program_id="test:fixed-payment",
            semantic_program_version=1,
            stack_object="S-fixed-payment",
            instruction_pointer=0,
            controller="A",
        ),
    )


class FixedOptionalManaPaymentCompilerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.capabilities = load_default_capability_registry()

    def compile(self, text: str, *, name: str = "Fixed Payment Witness"):
        return compile_oracle_card(
            payment_record(text, name=name),
            capability_registry=self.capabilities,
            capability_profile="commander_review",
        )

    def test_fixed_payment_composes_across_normalized_trigger_families(self):
        fixtures = (
            (
                "Whenever you cast a creature spell, you may pay {G}. If you "
                "do, draw a card.",
                "draw",
            ),
            (
                "At the beginning of your upkeep, you may pay {2}. If you do, "
                "gain 2 life.",
                "life",
            ),
            (
                "When this creature enters, you may pay {1}. If you do, create "
                "a Treasure token.",
                "create_token",
            ),
            (
                "Whenever this creature attacks, you may pay {R}. If you do, "
                "target creature can't block this turn.",
                "grant_declaration_restriction_until_end_of_turn",
            ),
            (
                "Whenever this creature deals combat damage to a player, you "
                "may pay {U}. If you do, scry 1.",
                "scry",
            ),
            (
                "When this artifact is put into a graveyard from the "
                "battlefield, you may pay {1}{B}. If you do, return target card "
                "from your graveyard to your hand.",
                "return_graveyard_card_to_owner_hand",
            ),
        )
        for text, nested_operation in fixtures:
            with self.subTest(text=text):
                ir = self.compile(text)
                self.assertEqual("exact", ir.status, ir.material_residuals)
                node = ir.faces[0].nodes[0]
                self.assertEqual("triggered_ability", node.kind)
                wrapper = node.effects[0]
                self.assertEqual(OPTIONAL_MANA_PAYMENT_OPERATION, wrapper["op"])
                self.assertEqual("$controller", wrapper["player"])
                self.assertEqual(nested_operation, wrapper["effects"][0]["op"])
                self.assertTrue(any(wrapper["cost"].values()))
                self.assertIn(
                    FIXED_OPTIONAL_MANA_PAYMENT_CAPABILITY,
                    node.capability_dependencies,
                )
                self.assertIn(
                    FIXED_OPTIONAL_MANA_PAYMENT_MECHANIC,
                    node.mechanics,
                )

    def test_lifecrafters_bestiary_is_an_exact_real_card_witness(self):
        ir = self.compile(
            "At the beginning of your upkeep, scry 1.\n"
            "Whenever you cast a creature spell, you may pay {G}. If you do, "
            "draw a card.",
            name="Lifecrafter's Bestiary",
        )

        self.assertEqual("exact", ir.status, ir.material_residuals)
        self.assertEqual(2, len(ir.faces[0].nodes))
        wrapper = ir.faces[0].nodes[1].effects[0]
        self.assertEqual(OPTIONAL_MANA_PAYMENT_OPERATION, wrapper["op"])
        self.assertEqual(1, wrapper["cost"]["G"])

    def test_nonfixed_nested_and_nonexact_forms_remain_residual(self):
        fixtures = (
            "Whenever you cast a creature spell, you may pay {X}. If you do, "
            "draw a card.",
            "Whenever you cast a creature spell, you may pay {G/U}. If you do, "
            "draw a card.",
            "Whenever you cast a creature spell, you may pay {G/P}. If you do, "
            "draw a card.",
            "Whenever you cast a creature spell, you may pay {S}. If you do, "
            "draw a card.",
            "Whenever you cast a creature spell, you may pay {0}. If you do, "
            "draw a card.",
            "Whenever you cast a creature spell, you may pay 2 life. If you do, "
            "draw a card.",
            "Whenever you cast a creature spell, you may pay {G}. When you do, "
            "draw a card.",
            "Whenever you cast a creature spell, you may pay {G}. If you do, "
            "you may draw a card.",
            "Whenever you cast a creature spell, you may pay {G}. If you do, "
            "draw a card, then discard a card.",
        )
        for text in fixtures:
            with self.subTest(text=text):
                ir = self.compile(text)
                self.assertNotEqual("exact", ir.status)
                self.assertTrue(ir.material_residuals)

    def test_payment_wrapper_remains_trigger_only(self):
        fixtures = (
            (
                "You may pay {G}. If you do, draw a card.",
                "Instant",
            ),
            (
                "{T}: You may pay {G}. If you do, draw a card.",
                "Artifact",
            ),
        )
        for text, type_line in fixtures:
            with self.subTest(text=text):
                ir = compile_oracle_card(
                    payment_record(text, type_line=type_line),
                    capability_registry=self.capabilities,
                    capability_profile="commander_review",
                )
                self.assertNotEqual("exact", ir.status)
                self.assertTrue(ir.material_residuals)

    def test_compiler_shape_and_handler_mutants_fail_closed(self):
        text = (
            "Whenever you cast a creature spell, you may pay {G}. If you do, "
            "draw a card."
        )
        ir = self.compile(text)
        node = ir.faces[0].nodes[0]
        wrapper = dict(node.effects[0])
        nested = dict(wrapper["effects"][0])
        mutants = (
            ({**wrapper, "player": "$source.controller"}, node.target_schema),
            ({**wrapper, "unexpected": True}, node.target_schema),
            ({**wrapper, "cost": {"G": 1}}, node.target_schema),
            ({**wrapper, "cost": {**wrapper["cost"], "G": -1}}, node.target_schema),
            ({**wrapper, "cost": {key: 0 for key in wrapper["cost"]}}, node.target_schema),
            ({**wrapper, "effects": []}, node.target_schema),
            ({**wrapper, "effects": [{"op": "unknown"}]}, node.target_schema),
            (
                {
                    **wrapper,
                    "effects": [
                        {
                            "op": OPTIONAL_MANA_PAYMENT_OPERATION,
                            "player": "$controller",
                            "cost": wrapper["cost"],
                            "effects": [nested],
                        }
                    ],
                },
                node.target_schema,
            ),
        )
        for effect, schema in mutants:
            with self.subTest(effect=effect):
                self.assertNotIn(
                    FIXED_OPTIONAL_MANA_PAYMENT_CAPABILITY,
                    capability_dependencies_for_node(
                        effects=(effect,),
                        target_schema=schema,
                        mechanic_ids=node.mechanics,
                    ),
                )
        self.assertNotIn(
            FIXED_OPTIONAL_MANA_PAYMENT_CAPABILITY,
            capability_dependencies_for_node(
                effects=node.effects,
                target_schema=node.target_schema,
                mechanic_ids=tuple(
                    mechanic
                    for mechanic in node.mechanics
                    if mechanic != FIXED_OPTIONAL_MANA_PAYMENT_MECHANIC
                ),
            ),
        )
        with mock.patch(
            "quorune.compiler.optional_payment_templates.fixed_optional_mana_payment_template",
            return_value=None,
        ):
            mutant = self.compile(text)
        self.assertNotEqual("exact", mutant.status)


class FixedOptionalManaPaymentChoiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.handler = payment_handler()
        self.effect = {
            "op": OPTIONAL_MANA_PAYMENT_OPERATION,
            "player": "A",
            "cost": {
                "GENERIC": 0,
                "W": 0,
                "U": 0,
                "B": 0,
                "R": 0,
                "G": 1,
                "C": 0,
            },
            "effects": [
                {
                    "op": "draw",
                    "player": "A",
                    "count": 1,
                    "private": True,
                }
            ],
        }

    def test_pay_decline_unpayable_and_stale_affordability_are_atomic(self):
        prepared = self.handler.prepare(self.effect, payment_context())
        self.assertEqual((True, False), prepared.request.choice.legal_values)
        self.assertTrue(prepared.request.public_context["payable"])

        paid = self.handler.complete(
            payment_continuation(prepared.continuation_effect),
            {"pay": True},
            payment_query(),
        )
        declined = self.handler.complete(
            payment_continuation(prepared.continuation_effect),
            {"pay": False},
            payment_query(),
        )
        self.assertEqual("PayManaCostIntent", type(paid.intents[0]).__name__)
        self.assertEqual("draw", paid.prepend_effects[0]["op"])
        self.assertFalse(declined.intents)
        self.assertFalse(declined.prepend_effects)

        unpayable = self.handler.prepare(
            self.effect,
            payment_context(query=payment_query(payable=False)),
        )
        self.assertEqual((False,), unpayable.request.choice.legal_values)
        with self.assertRaisesRegex(SemanticChoiceError, "no longer payable"):
            self.handler.complete(
                payment_continuation(prepared.continuation_effect),
                {"pay": True},
                payment_query(payable=False),
            )

    def test_runtime_wrapper_rejects_wrong_actor_extra_fields_and_nesting(self):
        with self.assertRaisesRegex(SemanticChoiceError, "active controller"):
            self.handler.prepare(
                {**self.effect, "player": "B"},
                payment_context(),
            )
        with self.assertRaisesRegex(SemanticChoiceError, "Malformed"):
            self.handler.prepare(
                {**self.effect, "unexpected": True},
                payment_context(),
            )
        with self.assertRaisesRegex(SemanticChoiceError, "cannot nest"):
            self.handler.prepare(
                {**self.effect, "effects": [self.effect]},
                payment_context(),
            )


class FixedOptionalManaPaymentIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.db, cls.mishra, cls.zimone = load_assets()
        cls.capabilities = load_default_capability_registry()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.db.close()

    def test_real_card_payment_is_private_commits_before_draw_and_replays(self):
        ir = compile_oracle_card(
            payment_record(
                "Whenever you cast a creature spell, you may pay {G}. If you "
                "do, draw a card.",
                name="Lifecrafter's Bestiary",
                type_line="Artifact",
            ),
            capability_registry=self.capabilities,
            capability_profile="commander_review",
        )
        self.assertEqual("exact", ir.status, ir.material_residuals)
        effects = ir.faces[0].nodes[0].effects

        session = make_session(
            self.db,
            self.mishra,
            self.zimone,
            players=4,
            seed=14101,
            auto_pass_empty=False,
        )
        keep_all(session)
        engine = session.engine
        engine.permissions.invalidate_current()
        engine.state.pending_decision = None
        engine.state.priority_player = None
        engine.state.players["B"].mana_pool["G"] = 1
        program = SemanticProgram(
            key="test:lifecrafters-bestiary-payment",
            label="Lifecrafter's Bestiary payment",
            effects=list(effects),
            trust_level="provisional",
        )
        engine.semantics.put(program)
        item = StackItem(
            stack_id="lifecrafters-bestiary-payment",
            ref="S-lifecrafters-bestiary-payment",
            kind="triggered_ability",
            controller="B",
            label=program.label,
            semantic_key=program.key,
            visibility=["A", "B", "C", "D"],
        )
        engine.state.stack.append(item)
        hand_before = len(engine.state.players["B"].zones["hand"])

        engine._begin_resolve_item(
            item,
            program.effects,
            None,
            note="Lifecrafter's Bestiary payment",
        )

        self.assertEqual("semantic.choice", engine.state.pending_decision.kind)
        self.assertEqual(1, engine.state.players["B"].mana_pool["G"])
        self.assertEqual(hand_before, len(engine.state.players["B"].zones["hand"]))
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
                "pay": True,
                "reason": "Pay for Lifecrafter's Bestiary.",
            },
        )

        self.assertTrue(result.ok, result.summary)
        self.assertEqual(0, engine.state.players["B"].mana_pool["G"])
        self.assertEqual(
            hand_before + 1,
            len(engine.state.players["B"].zones["hand"]),
        )
        with tempfile.TemporaryDirectory() as temporary:
            record_dir = Path(temporary) / "lifecrafters-bestiary-payment"
            session.save(record_dir)
            replay = replay_record(record_dir, self.db, verify=True)
        self.assertTrue(replay["ok"], replay)


if __name__ == "__main__":
    unittest.main()
