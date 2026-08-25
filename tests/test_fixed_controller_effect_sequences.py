from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest
import uuid
from unittest.mock import patch

from common import keep_all, load_assets, make_session
from quorune.carddb import CardRecord
from quorune.compiler.affected_player_discard_templates import (
    fixed_controller_discard_effect_template,
)
from quorune.compiler.fixed_controller_effect_sequences import (
    fixed_controller_effect_sequence_template,
)
from quorune.compiler.life_templates import fixed_life_effect_template
from quorune.compiler.program_generation import register_generated_programs
from quorune.model import CardInstance, StackItem
from quorune.oracle_ir import compile_oracle_card
from quorune.projection import StateProjector
from quorune.record import (
    authoritative_state_hash,
    checkpoint_envelope,
    replay_record,
)
from quorune.rules.capabilities import (
    capability_dependencies_for_node,
    load_default_capability_registry,
)
from quorune.rules.affected_player_discard_capability_shapes import (
    fixed_affected_player_discard_node_capabilities,
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
            (
                self.fixture("Draw two cards, then discard two cards."),
                "spell_ability",
                ("draw", "choose_cards_apnap"),
            ),
            (
                self.fixture(
                    "{T}: Draw three cards, then discard four cards.",
                    type_line="Artifact",
                ),
                "activated_ability",
                ("draw", "choose_cards_apnap"),
            ),
            (
                self.fixture(
                    "When Fixed Controller Sequence enters, discard a card, "
                    "then draw a card.",
                    type_line="Creature — Wizard",
                ),
                "triggered_ability",
                ("choose_cards_apnap", "draw"),
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
                if "choose_cards_apnap" in operations:
                    self.assertIn(
                        "choice.affected_player.fixed_discard",
                        node.capability_dependencies,
                    )
                    self.assertIn(
                        "zone.change.destination_replacement",
                        node.capability_dependencies,
                    )

    def test_controller_discard_leaf_and_sequence_shape_are_closed(self):
        template = fixed_controller_discard_effect_template(
            "You discard four cards."
        )
        self.assertIsNotNone(template)
        assert template is not None
        self.assertEqual(4, template.count)
        discard = template.effects[0]
        draw = {
            "op": "draw",
            "player": "$controller",
            "count": 3,
            "private": True,
        }
        self.assertEqual(["$controller"], discard["players"])
        self.assertEqual(
            (),
            fixed_affected_player_discard_node_capabilities(
                effects=(discard,),
                target_schema=None,
                mechanic_ids=(
                    "fixed-affected-player-discard",
                    "cr-402-hand",
                ),
            ),
        )
        self.assertEqual(
            (
                "choice.affected_player.fixed_discard",
                SEQUENCE_CAPABILITY,
                "zone.change.destination_replacement",
                "zone.draw.library_to_hand",
            ),
            fixed_controller_effect_sequence_node_capabilities(
                effects=(draw, discard),
                target_schema=None,
                mechanic_ids=(
                    "fixed-controller-effect-sequence",
                    "fixed-affected-player-discard",
                    "cr-121-drawing-a-card",
                    "cr-402-hand",
                ),
            ),
        )

        mutations = []
        for field, value in (
            ("players", "all"),
            ("players", "opponents"),
            ("players", ["$target.0"]),
            ("count", 5),
            ("hidden", False),
            ("then", "sacrifice"),
        ):
            mutant = deepcopy(discard)
            mutant[field] = value
            if field == "players" and value == ["$target.0"]:
                mutant["target"] = "$target.0"
            mutations.append(mutant)
        for mutant in mutations:
            with self.subTest(mutant=mutant):
                self.assertEqual(
                    (),
                    fixed_controller_effect_sequence_node_capabilities(
                        effects=(draw, mutant),
                        target_schema=None,
                        mechanic_ids=(
                            "fixed-controller-effect-sequence",
                            "fixed-affected-player-discard",
                            "cr-121-drawing-a-card",
                            "cr-402-hand",
                        ),
                    ),
                )
        for text in (
            "Draw a card, then target player discards a card.",
            "Draw a card, then each player discards a card.",
        ):
            with self.subTest(text=text):
                self.assertIsNone(
                    fixed_controller_effect_sequence_template(text)
                )
                ir = compile_oracle_card(
                    self.fixture(text),
                    capability_registry=self.capabilities,
                    capability_profile="commander_review",
                )
                self.assertTrue(
                    all(
                        SEQUENCE_CAPABILITY not in node.capability_dependencies
                        for face in ir.faces
                        for node in face.nodes
                    )
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
            "Draw two cards, then discard a card at random.",
            "Draw two cards, then discard your hand.",
            "Draw two cards, then discard two cards if you control a Wizard.",
            "Draw a card, then discard five cards.",
            "Discard a card, then draw a card, then lose 1 life.",
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
        records = (
            self.fixture("Scry 2, then draw a card."),
            self.fixture("Draw two cards, then discard a card."),
        )

        def assert_exact() -> None:
            for record in records:
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
        with patch(
            "quorune.compiler.fixed_controller_effect_sequences."
            "fixed_controller_discard_effect_template",
            return_value=None,
        ):
            ir = compile_oracle_card(
                records[1],
                capability_registry=self.capabilities,
                capability_profile="commander_review",
            )
            self.assertNotEqual("exact", ir.status)


class FixedControllerEffectSequenceRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.db, cls.mishra, cls.zimone = load_assets()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.db.close()

    def session(
        self,
        seed: int,
        *,
        players: int = 2,
        auto_pass_empty: bool = True,
    ):
        session = make_session(
            self.db,
            self.mishra,
            self.zimone,
            players=players,
            seed=seed,
            auto_pass_empty=auto_pass_empty,
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

    @staticmethod
    def choose(session, seat: str, refs: list[str]):
        return session.act(
            f"pilot:{seat}",
            {"action_id": "choose", "cards": refs},
        )

    def assert_replays(self, session, label: str) -> None:
        expected_hash = authoritative_state_hash(session.state)
        with tempfile.TemporaryDirectory() as temporary:
            record_dir = Path(temporary) / label
            session.save(record_dir)
            replay = replay_record(record_dir, self.db, verify=True)
        self.assertTrue(replay["ok"], replay)
        self.assertEqual(expected_hash, replay["final_state_hash"])

    def install_opponent_voidwalker(self, engine) -> CardInstance:
        record = self.db.lookup("Dauthi Voidwalker")
        register_generated_programs(
            self.db,
            engine.semantics,
            (record,),
            trust_level="provisional",
            capability_registry=load_default_capability_registry(),
            capability_profile=engine.state.config.review_profile,
            promote_exact_runtime_handlers=True,
            promote_exact_capability_declarations=True,
            promote_exact_effect_programs=True,
        )
        card = CardInstance(
            object_id="fixture:sequence-voidwalker",
            ref="sequence-voidwalker",
            oracle_id=record.oracle_id,
            printed_name=record.name,
            owner="B",
            controller="B",
            zone="battlefield",
            zone_timestamp=engine.state.event_sequence + 1,
            known_to=list(engine.seats),
            revealed_to=list(engine.seats),
        )
        engine.state.cards[card.object_id] = card
        engine.state.players["B"].zones["battlefield"].append(card.object_id)
        return card

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

    def test_draw_then_controller_discard_is_private_and_replays_exactly(self):
        session = self.session(123004, players=4, auto_pass_empty=False)
        engine = session.engine
        self.install_opponent_voidwalker(engine)
        library = engine.state.players["A"].zones["library"]
        drawn_refs = tuple(
            engine.state.cards[object_id].ref
            for object_id in reversed(library[-2:])
        )
        event_start = len(engine.state.events)
        template = fixed_controller_effect_sequence_template(
            "Draw two cards, then discard a card."
        )
        self.assertIsNotNone(template)
        assert template is not None
        self.begin_sequence(session, template.effects)

        self.assertEqual("choice.apnap", engine.state.pending_decision.kind)
        self.assertEqual(["pilot:A"], session.pending_principals())
        decision = StateProjector(self.db, engine.state)._decision("pilot:A")
        self.assertIsNotNone(decision)
        assert decision is not None
        serialized = json.dumps(decision, sort_keys=True)
        for ref in drawn_refs:
            self.assertIn(ref, serialized)
        for seat in "BCD":
            self.assertIsNone(
                StateProjector(self.db, engine.state)._decision(f"pilot:{seat}")
            )
            packet = json.dumps(
                session.packet(f"pilot:{seat}", full=True),
                sort_keys=True,
            )
            self.assertTrue(all(ref not in packet for ref in drawn_refs))

        session.initial_checkpoint = checkpoint_envelope(engine.state)
        session.commands.clear()
        session.decisions.clear()
        accepted = self.choose(session, "A", [drawn_refs[0]])
        self.assertTrue(accepted.ok, accepted.summary)
        self.assertEqual(
            "exile",
            engine._resolve_object("A", drawn_refs[0]).zone,
        )
        self.assertEqual(
            1,
            engine._resolve_object("A", drawn_refs[0]).counters["void"],
        )
        self.assertEqual("hand", engine._resolve_object("A", drawn_refs[1]).zone)
        relevant = [
            event.code
            for event in engine.state.events[event_start:]
            if event.code in {"card.draw.private", "choice.discard"}
        ]
        self.assertEqual(
            ["card.draw.private", "card.draw.private", "choice.discard"],
            relevant,
        )
        self.assert_replays(session, "draw-then-controller-discard")

    def test_controller_discard_then_draw_commits_in_printed_order(self):
        session = self.session(123005, players=4, auto_pass_empty=False)
        engine = session.engine
        event_start = len(engine.state.events)
        template = fixed_controller_effect_sequence_template(
            "Discard a card, then draw a card."
        )
        self.assertIsNotNone(template)
        assert template is not None
        self.begin_sequence(session, template.effects)
        discarded = engine.state.cards[
            engine.state.players["A"].zones["hand"][0]
        ]

        self.assertFalse(
            any(
                event.code == "card.draw.private"
                for event in engine.state.events[event_start:]
            )
        )
        accepted = self.choose(session, "A", [discarded.ref])
        self.assertTrue(accepted.ok, accepted.summary)
        self.assertEqual("graveyard", discarded.zone)
        relevant = [
            event.code
            for event in engine.state.events[event_start:]
            if event.code in {"choice.discard", "card.draw.private"}
        ]
        self.assertEqual(["choice.discard", "card.draw.private"], relevant)

    def test_stale_controller_discard_choice_rolls_back_without_resuming(self):
        session = self.session(123006, players=4, auto_pass_empty=False)
        engine = session.engine
        template = fixed_controller_effect_sequence_template(
            "Discard a card, then draw a card."
        )
        self.assertIsNotNone(template)
        assert template is not None
        self.begin_sequence(session, template.effects)
        stale = engine.state.cards[
            engine.state.players["A"].zones["hand"][0]
        ]
        engine.move_card(
            stale.object_id,
            "graveyard",
            reason="focused stale controller discard choice",
        )
        before = authoritative_state_hash(engine.state)
        event_count = len(engine.state.events)

        rejected = self.choose(session, "A", [stale.ref])

        self.assertFalse(rejected.ok)
        self.assertEqual(before, authoritative_state_hash(engine.state))
        self.assertEqual(event_count, len(engine.state.events))


if __name__ == "__main__":
    unittest.main()
