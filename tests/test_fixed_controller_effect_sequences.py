from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest
import uuid
from unittest.mock import patch

from common import keep_all, load_assets, make_session
from quorune.carddb import CardRecord
from quorune.compiler.fixed_controller_effect_sequences import (
    fixed_controller_effect_sequence_template,
)
from quorune.compiler.life_templates import fixed_life_effect_template
from quorune.model import StackItem
from quorune.oracle_ir import compile_oracle_card
from quorune.record import (
    authoritative_state_hash,
    checkpoint_envelope,
    replay_record,
)
from quorune.rules.capabilities import (
    capability_dependencies_for_node,
    load_default_capability_registry,
)
from quorune.rules.fixed_controller_effect_shapes import (
    fixed_controller_effect_sequence_node_capabilities,
    fixed_life_node_capabilities,
)
from quorune.semantics import SemanticProgram


SEQUENCE_CAPABILITY = "resolution.effect_sequence.fixed_controller"


class FixedControllerEffectSequenceCompilerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.capabilities = load_default_capability_registry()
        cls.base = CardRecord(
            oracle_id="fixture:fixed-controller-sequence",
            name="Fixed Controller Sequence",
            mana_cost="{1}{U}",
            mana_value=2.0,
            type_line="Sorcery",
            oracle_text="Scry 2, then draw a card.",
            power=None,
            toughness=None,
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

    def fixture(
        self,
        text: str,
        *,
        type_line: str = "Sorcery",
        name: str = "Fixed Controller Sequence",
    ) -> CardRecord:
        return replace(
            self.base,
            oracle_id=f"fixture:{abs(hash((text, type_line)))}",
            name=name,
            oracle_text=text,
            type_line=type_line,
        )

    def test_fixed_life_templates_and_shapes_are_closed(self):
        cases = (
            (
                "You gain 3 life.",
                {"op": "life", "player": "$controller", "delta": 3},
                ("cr-119-life",),
            ),
            (
                "Lose two life.",
                {
                    "op": "lose_life",
                    "player": "$controller",
                    "amount": 2,
                },
                ("cr-119-life",),
            ),
            (
                "Each opponent loses 1 life.",
                {"op": "lose_life_each_opponent", "amount": 1},
                (
                    "cr-119-life",
                    "cr-101-the-magic-golden-rules",
                ),
            ),
        )
        for text, effect, mechanics in cases:
            with self.subTest(text=text):
                template = fixed_life_effect_template(text)
                self.assertIsNotNone(template)
                self.assertEqual((effect,), template.compiled()[1])
                self.assertEqual(
                    ("life.change.effect",),
                    fixed_life_node_capabilities(
                        effects=(effect,),
                        target_schema=None,
                        mechanic_ids=mechanics,
                    ),
                )

        valid = {"op": "life", "player": "$controller", "delta": 1}
        for effect, target, mechanics in (
            ({**valid, "delta": True}, None, ("cr-119-life",)),
            ({**valid, "delta": 0}, None, ("cr-119-life",)),
            ({**valid, "delta": -1}, None, ("cr-119-life",)),
            ({**valid, "player": "$target.0"}, None, ("cr-119-life",)),
            ({**valid, "extra": 1}, None, ("cr-119-life",)),
            (valid, {"count": 1}, ("cr-119-life",)),
            (valid, None, ()),
            (
                {"op": "set_life", "player": "$controller", "amount": 20},
                None,
                ("cr-119-life",),
            ),
            (
                {"op": "lose_life_each_opponent", "amount": 1},
                None,
                ("cr-119-life",),
            ),
        ):
            with self.subTest(effect=effect, target=target, mechanics=mechanics):
                self.assertEqual(
                    (),
                    fixed_life_node_capabilities(
                        effects=(effect,),
                        target_schema=target,
                        mechanic_ids=mechanics,
                    ),
                )
        for text in (
            "You gain 0 life.",
            "You gain X life.",
            "You gain life equal to your devotion.",
            "Your life total becomes 20.",
            "Exchange life totals with target opponent.",
            "You may pay 2 life.",
        ):
            with self.subTest(text=text):
                self.assertIsNone(fixed_life_effect_template(text))

    def test_cr119_capability_requires_the_exact_fixed_life_shape(self):
        self.assertEqual(
            (),
            capability_dependencies_for_node(
                effects=(
                    {
                        "op": "set_life",
                        "player": "$controller",
                        "amount": 20,
                    },
                ),
                target_schema=None,
                mechanic_ids=("cr-119-life",),
            ),
        )

    def test_spell_trigger_and_activated_contexts_share_sequence_lowering(self):
        fixtures = (
            (
                self.fixture("Scry 2, then draw a card."),
                "spell_ability",
                ("scry", "draw"),
            ),
            (
                self.fixture(
                    "{T}: Draw a card and you lose 1 life.",
                    type_line="Artifact",
                ),
                "activated_ability",
                ("draw", "lose_life"),
            ),
            (
                self.fixture(
                    "When Fixed Controller Sequence enters, draw a card. Scry 1.",
                    type_line="Creature — Wizard",
                ),
                "triggered_ability",
                ("draw", "scry"),
            ),
        )
        for record, kind, operations in fixtures:
            with self.subTest(kind=kind):
                ir = compile_oracle_card(
                    record,
                    capability_registry=self.capabilities,
                    capability_profile="commander_review",
                )
                self.assertEqual("exact", ir.status, ir.material_residuals)
                node = ir.faces[0].nodes[0]
                self.assertEqual(kind, node.kind)
                self.assertEqual(1, node.span.line)
                self.assertEqual(
                    "fixed-controller-draw-effect-sequence-v1",
                    node.template_id,
                )
                self.assertEqual(
                    operations,
                    tuple(effect["op"] for effect in node.effects),
                )
                self.assertIn(SEQUENCE_CAPABILITY, node.capability_dependencies)
                self.assertIn(
                    "zone.draw.library_to_hand",
                    node.capability_dependencies,
                )

    def test_sequence_shape_rejects_open_and_malformed_variants(self):
        valid = (
            {"op": "scry", "player": "$controller", "count": 2},
            {
                "op": "draw",
                "player": "$controller",
                "count": 1,
                "private": True,
            },
        )
        self.assertEqual(
            (
                "library.scry.fixed_controller",
                SEQUENCE_CAPABILITY,
                "zone.draw.library_to_hand",
            ),
            fixed_controller_effect_sequence_node_capabilities(
                effects=valid,
                target_schema=None,
                mechanic_ids=(
                    "fixed-controller-effect-sequence",
                    "scry",
                    "cr-121-drawing-a-card",
                ),
            ),
        )
        mutants = (
            (({**valid[0], "count": True}, valid[1]), None),
            ((valid[0], {**valid[1], "private": False}), None),
            ((valid[0], {**valid[1], "player": "$target.0"}), None),
            ((valid[0], valid[1], valid[0]), None),
            ((valid[0], valid[0]), None),
            ((valid[1], valid[1]), None),
            ((valid[0], valid[1]), {"count": 1}),
        )
        for effects, target in mutants:
            with self.subTest(effects=effects, target=target):
                self.assertEqual(
                    (),
                    fixed_controller_effect_sequence_node_capabilities(
                        effects=effects,
                        target_schema=target,
                        mechanic_ids=("fixed-controller-effect-sequence",),
                    ),
                )

    def test_unsupported_sequence_wording_remains_a_precise_residual(self):
        composed = "Draw a card. Scry 1. You gain 1 life."
        self.assertIsNone(fixed_controller_effect_sequence_template(composed))
        for text in (
            "Draw cards equal to the number of creatures you control. Scry 1.",
            "Scry 1, then you may draw a card.",
            "Draw a card. Each player loses 1 life.",
            "Draw a card unless an opponent pays {1}.",
        ):
            with self.subTest(text=text):
                self.assertIsNone(fixed_controller_effect_sequence_template(text))
                ir = compile_oracle_card(
                    self.fixture(text),
                    capability_registry=self.capabilities,
                    capability_profile="commander_review",
                )
                self.assertNotEqual("exact", ir.status)
                self.assertTrue(ir.material_residuals)

    def test_sequence_compiler_and_shape_mutants_are_killed(self):
        record = self.fixture("Scry 2, then draw a card.")

        def assert_exact() -> None:
            ir = compile_oracle_card(
                record,
                capability_registry=self.capabilities,
                capability_profile="commander_review",
            )
            self.assertEqual("exact", ir.status, ir.material_residuals)
            self.assertEqual(
                "fixed-controller-draw-effect-sequence-v1",
                ir.faces[0].nodes[0].template_id,
            )

        assert_exact()
        with patch(
            "quorune.oracle_ir.fixed_controller_effect_sequence_template",
            return_value=None,
        ):
            with self.assertRaises(AssertionError):
                assert_exact()


class FixedControllerEffectSequenceRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.db, cls.mishra, cls.zimone = load_assets()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.db.close()

    def session(self, seed: int, *, players: int = 2):
        session = make_session(
            self.db,
            self.mishra,
            self.zimone,
            players=players,
            seed=seed,
            auto_pass_empty=True,
        )
        keep_all(session)
        session.engine.permissions.invalidate_current()
        session.engine.state.priority_player = None
        return session

    def begin_sequence(self, session, effects, *, seat: str = "A"):
        engine = session.engine
        card = next(
            card
            for card in engine.state.cards.values()
            if card.owner == seat
            and card.is_card_object
            and card.zone not in {"command", "outside", "library"}
        )
        starting_zone = card.zone
        engine._remove_from_zone(card)
        engine._reset_zone_change(card, "stack")
        card.zone = "stack"
        card.controller = seat
        card.known_to = list(engine.seats)
        card.revealed_to = list(engine.seats)
        key = f"test:fixed-controller-sequence:{session.state.config.seed}"
        program = SemanticProgram(
            key=key,
            label="Fixed controller sequence",
            effects=list(effects),
            destination="graveyard",
        )
        engine.semantics.put(program)
        item = StackItem(
            stack_id=uuid.uuid4().hex,
            ref=f"S-sequence-{session.state.config.seed}",
            kind="spell",
            controller=seat,
            label=program.label,
            card_object_id=card.object_id,
            semantic_key=key,
            default_destination="graveyard",
            visibility=list(engine.seats),
        )
        engine.state.stack.append(item)
        engine._begin_resolve_item(
            item,
            program.effects,
            program.destination,
            note="fixed controller sequence regression",
        )
        return card, starting_zone

    def test_draw_then_life_commits_in_printed_order(self):
        session = self.session(123001)
        engine = session.engine
        hand_before = len(engine.state.players["A"].zones["hand"])
        life_before = engine.state.players["A"].life
        event_start = len(engine.state.events)
        _source, starting_zone = self.begin_sequence(
            session,
            (
                {
                    "op": "draw",
                    "player": "A",
                    "count": 2,
                    "private": True,
                },
                {"op": "lose_life", "player": "A", "amount": 2},
            ),
        )
        self.assertEqual(
            hand_before + 2 - int(starting_zone == "hand"),
            len(engine.state.players["A"].zones["hand"]),
        )
        self.assertEqual(life_before - 2, engine.state.players["A"].life)
        relevant = [
            event.code
            for event in engine.state.events[event_start:]
            if event.code in {"card.draw.private", "effect.life"}
        ]
        self.assertEqual(
            ["card.draw.private", "card.draw.private", "effect.life"],
            relevant,
        )

    def test_scry_then_draw_resumes_privately_and_replays_exactly(self):
        session = self.session(123002, players=4)
        engine = session.engine
        library = engine.state.players["A"].zones["library"]
        expected = tuple(
            engine.state.cards[object_id].ref
            for object_id in reversed(library[-2:])
        )
        hand_before = len(engine.state.players["A"].zones["hand"])
        _source, starting_zone = self.begin_sequence(
            session,
            (
                {"op": "scry", "player": "A", "count": 2},
                {
                    "op": "draw",
                    "player": "A",
                    "count": 1,
                    "private": True,
                },
            ),
        )
        self.assertEqual("semantic.choice", engine.state.pending_decision.kind)
        continuation = json.dumps(
            engine.state.pending_decision.continuation,
            sort_keys=True,
            default=str,
        )
        self.assertIn('"op": "draw"', continuation)
        for seat in "BCD":
            packet = json.dumps(
                session.packet(f"pilot:{seat}", full=True),
                sort_keys=True,
            )
            self.assertTrue(all(ref not in packet for ref in expected))
        session.commands.clear()
        session.decisions.clear()
        session.initial_checkpoint = checkpoint_envelope(engine.state)
        accepted = session.act(
            "pilot:A",
            {
                "action_id": "choose",
                "cards": {"top": [expected[1]], "bottom": [expected[0]]},
            },
        )
        self.assertTrue(accepted.ok, accepted.summary)
        self.assertEqual(
            hand_before + 1 - int(starting_zone == "hand"),
            len(engine.state.players["A"].zones["hand"]),
        )
        drawn = next(
            event
            for event in reversed(engine.state.events)
            if event.code == "card.draw.private"
        )
        self.assertEqual(expected[1], drawn.details["objects"][0])
        for seat in "BCD":
            packet = json.dumps(
                session.packet(f"pilot:{seat}", full=True),
                sort_keys=True,
            )
            self.assertNotIn(expected[1], packet)
        expected_hash = authoritative_state_hash(session.state)
        with tempfile.TemporaryDirectory() as temporary:
            record_dir = Path(temporary) / "fixed-controller-sequence"
            session.save(record_dir)
            replay = replay_record(record_dir, self.db, verify=True)
        self.assertTrue(replay["ok"], replay)
        self.assertEqual(expected_hash, replay["final_state_hash"])

    def test_invalid_remaining_effect_rolls_back_the_choice_command(self):
        session = self.session(123003)
        engine = session.engine
        library_before = tuple(engine.state.players["A"].zones["library"])
        life_before = engine.state.players["A"].life
        expected = tuple(
            engine.state.cards[object_id].ref
            for object_id in reversed(library_before[-2:])
        )
        self.begin_sequence(
            session,
            (
                {"op": "scry", "player": "A", "count": 2},
                {
                    "op": "lose_life",
                    "player": "missing-seat",
                    "amount": 1,
                },
            ),
        )
        before = authoritative_state_hash(session.state)
        rejected = session.act(
            "pilot:A",
            {
                "action_id": "choose",
                "cards": {"top": [expected[1]], "bottom": [expected[0]]},
            },
        )
        self.assertFalse(rejected.ok)
        self.assertEqual(before, authoritative_state_hash(session.state))
        self.assertEqual(library_before, tuple(engine.state.players["A"].zones["library"]))
        self.assertEqual(life_before, engine.state.players["A"].life)


if __name__ == "__main__":
    unittest.main()
