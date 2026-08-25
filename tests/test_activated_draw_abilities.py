from __future__ import annotations

import copy
from dataclasses import replace
from pathlib import Path
import tempfile
import unittest

from common import DB_PATH, keep_all, load_assets, make_session
from quorune.carddb import CardDatabase
from quorune.oracle_ir import (
    compile_oracle_card,
    register_generated_programs,
)
from quorune.record import (
    authoritative_state_hash,
    checkpoint_envelope,
    replay_record,
)
from quorune.rules.capabilities import (
    capability_dependencies_for_node,
    load_default_capability_registry,
)
from quorune.rules.node_capability_shapes import (
    fixed_draw_node_capabilities,
)
from quorune.semantics import SemanticRegistry


DRAW_CAPABILITY = "zone.draw.library_to_hand"
DRAW_ACTION_CAPABILITY = "zone.draw.specifically_drawn_card_actions"


class ActivatedDrawCompilerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db = CardDatabase(DB_PATH)
        cls.capabilities = load_default_capability_registry()
        cls.base = cls.db.lookup("Mind Stone")

    @classmethod
    def tearDownClass(cls):
        cls.db.close()

    def compile(self, oracle_text: str, *, type_line: str = "Artifact"):
        return compile_oracle_card(
            replace(
                self.base,
                oracle_id="fixture:activated-draw",
                name="Activated Draw Fixture",
                oracle_text=oracle_text,
                type_line=type_line,
                keywords=(),
            ),
            capability_registry=self.capabilities,
            capability_profile="commander_review",
        )

    def test_fixed_draw_shapes_are_strict_and_capability_scoped(self):
        controller = (
            {"op": "draw", "player": "$controller", "count": 1,
             "private": True},
        )
        target = (
            {"op": "draw", "player": "$target.0", "count": 2,
             "private": True},
        )
        optional_target = (
            {"op": "offer_draw", "player": "$controller",
             "drawer": "$target.0", "count": 1, "private": True},
        )
        target_schema = {
            "zones": ["player"],
            "categories": ["player"],
            "player_relation": "any",
            "count": 1,
        }
        each_player = ({"op": "draw_each_player", "count": 1},)
        drawn_card_actions = (
            {
                "op": "draw_with_actions",
                "player": "$controller",
                "count": 1,
                "private": True,
                "post_draw_actions": [
                    {"action": "reveal", "public": True},
                    {
                        "action": "discard_unless_type",
                        "card_type": "land",
                    },
                ],
            },
        )

        self.assertEqual(
            (DRAW_CAPABILITY,),
            fixed_draw_node_capabilities(
                effects=controller,
                target_schema=None,
                mechanic_ids=("cr-121-drawing-a-card",),
            ),
        )
        self.assertEqual(
            ("target.revalidate_resolution", DRAW_CAPABILITY),
            fixed_draw_node_capabilities(
                effects=target,
                target_schema=target_schema,
                mechanic_ids=(
                    "cr-115-targets",
                    "cr-121-drawing-a-card",
                ),
            ),
        )
        self.assertEqual(
            ("target.revalidate_resolution", DRAW_CAPABILITY),
            fixed_draw_node_capabilities(
                effects=optional_target,
                target_schema=target_schema,
                mechanic_ids=(
                    "cr-115-targets",
                    "cr-121-drawing-a-card",
                ),
            ),
        )
        self.assertEqual(
            (DRAW_CAPABILITY,),
            fixed_draw_node_capabilities(
                effects=each_player,
                target_schema=None,
                mechanic_ids=("cr-121-drawing-a-card",),
            ),
        )
        self.assertEqual(
            (DRAW_ACTION_CAPABILITY,),
            fixed_draw_node_capabilities(
                effects=drawn_card_actions,
                target_schema=None,
                mechanic_ids=("cr-121-drawing-a-card",),
            ),
        )

        malformed = (
            ({**controller[0], "count": 0},),
            ({**controller[0], "count": True},),
            ({**controller[0], "private": False},),
            ({**controller[0], "unknown": 1},),
            ({**controller[0], "player": "$target.0"},),
            ({**optional_target[0], "player": "$target.0"},),
            (
                {
                    **drawn_card_actions[0],
                    "post_draw_actions": list(
                        reversed(
                            drawn_card_actions[0]["post_draw_actions"]
                        )
                    ),
                },
            ),
            (
                {
                    **drawn_card_actions[0],
                    "post_draw_actions": [
                        {"action": "reveal", "public": True},
                    ],
                },
            ),
            ({**drawn_card_actions[0], "count": True},),
        )
        for effects in malformed:
            with self.subTest(effects=effects):
                self.assertEqual(
                    (),
                    fixed_draw_node_capabilities(
                        effects=effects,
                        target_schema=None,
                        mechanic_ids=("cr-121-drawing-a-card",),
                    ),
                )
                self.assertNotIn(
                    DRAW_CAPABILITY,
                    capability_dependencies_for_node(
                        effects=effects,
                        target_schema=None,
                        mechanic_ids=("cr-121-drawing-a-card",),
                    ),
                )
                self.assertNotIn(
                    DRAW_ACTION_CAPABILITY,
                    capability_dependencies_for_node(
                        effects=effects,
                        target_schema=None,
                        mechanic_ids=("cr-121-drawing-a-card",),
                    ),
                )

    def test_ordinary_activated_draw_is_exact_and_source_spanned(self):
        record = self.db.lookup("Mind Stone")
        ir = compile_oracle_card(
            record,
            capability_registry=self.capabilities,
            capability_profile="commander_review",
        )

        self.assertEqual("exact", ir.status)
        draw = ir.faces[0].nodes[1]
        self.assertEqual("activated_ability", draw.kind)
        self.assertEqual("draw-controller-v1", draw.template_id)
        self.assertEqual((DRAW_CAPABILITY,), draw.capability_dependencies)
        self.assertEqual(
            "{1}, {T}, Sacrifice this artifact: Draw a card.",
            record.oracle_text[draw.span.start : draw.span.end],
        )

    def test_each_player_and_target_draw_use_the_same_closed_family(self):
        cases = (
            (
                "{T}: Each player draws a card.",
                "draw-each-player-v1",
                {DRAW_CAPABILITY},
            ),
            (
                "{2}: Target player draws two cards.",
                "draw-target-player-v1",
                {DRAW_CAPABILITY, "target.revalidate_resolution"},
            ),
        )
        for text, template, capabilities in cases:
            with self.subTest(text=text):
                ir = self.compile(text)
                self.assertEqual("exact", ir.status)
                node = ir.faces[0].nodes[0]
                self.assertEqual(template, node.template_id)
                self.assertEqual(capabilities, set(node.capability_dependencies))

    def test_drawn_card_action_template_is_exact_and_source_spanned(self):
        expected_text = (
            "{T}: Draw a card and reveal it. If it isn't a land card, "
            "discard it."
        )
        ir = self.compile(expected_text)

        self.assertEqual("exact", ir.status)
        self.assertEqual(1, len(ir.faces[0].nodes))
        node = ir.faces[0].nodes[0]
        self.assertEqual("activated_ability", node.kind)
        self.assertEqual(
            "draw-reveal-discard-unless-land-controller-v1",
            node.template_id,
        )
        self.assertEqual((DRAW_ACTION_CAPABILITY,), node.capability_dependencies)
        self.assertEqual(
            expected_text,
            expected_text[node.span.start : node.span.end],
        )

    def test_fixed_draw_discard_sequence_uses_shared_sequence_owner(self):
        ir = self.compile("{T}: Draw a card, then discard a card.")

        self.assertEqual("exact", ir.status, ir.material_residuals)
        node = ir.faces[0].nodes[0]
        self.assertEqual(
            "fixed-controller-draw-effect-sequence-v1",
            node.template_id,
        )
        self.assertEqual(
            ("draw", "choose_cards_apnap"),
            tuple(effect["op"] for effect in node.effects),
        )
        self.assertIn(
            "resolution.effect_sequence.fixed_controller",
            node.capability_dependencies,
        )
        self.assertIn(
            "choice.affected_player.fixed_discard",
            node.capability_dependencies,
        )
        self.assertIn(DRAW_CAPABILITY, node.capability_dependencies)

    def test_dynamic_and_noncanonical_draw_wording_remains_residual(self):
        for text in (
            "{T}: Draw X cards.",
            "{T}: Draw a card for each creature you control.",
            "{T}: Draw a card and reveal it.",
        ):
            with self.subTest(text=text):
                ir = self.compile(text)
                self.assertNotEqual("exact", ir.status)
                self.assertTrue(ir.material_residuals)

    def test_only_unique_whole_effect_programs_are_promoted(self):
        registry = SemanticRegistry(include_builtin_packs=False)
        result = register_generated_programs(
            self.db,
            registry,
            (self.db.lookup("Mind Stone"),),
            capability_registry=self.capabilities,
            capability_profile="commander_review",
            promote_exact_effect_programs=True,
        )
        draw = next(
            program
            for program in registry.programs()
            if program.effects
            and program.effects[0].get("op") == "draw"
        )
        self.assertEqual("trusted", draw.trust_level)
        self.assertFalse(draw.requires_arbiter)
        self.assertEqual(1, result["exact_fixed_draw_programs_promoted"])

        sequence_registry = SemanticRegistry(include_builtin_packs=False)
        sequence = register_generated_programs(
            self.db,
            sequence_registry,
            (self.db.lookup("Resupply"),),
            capability_registry=self.capabilities,
            capability_profile="commander_review",
            promote_exact_effect_programs=True,
        )
        self.assertEqual(0, sequence["exact_fixed_draw_programs_promoted"])
        self.assertEqual(1, sequence["exact_effect_programs_promoted"])
        sequence_program = next(iter(sequence_registry.programs()))
        self.assertEqual(
            ("life", "draw"),
            tuple(effect["op"] for effect in sequence_program.effects),
        )
        self.assertEqual("trusted", sequence_program.trust_level)
        self.assertFalse(sequence_program.requires_arbiter)


class ActivatedDrawRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db, cls.mishra, cls.zimone = load_assets()

    @classmethod
    def tearDownClass(cls):
        cls.db.close()

    def session_with_cards(
        self,
        *,
        a_cards: tuple[str, ...],
        b_cards: tuple[str, ...] = (),
        players: int = 2,
        seed: int,
    ):
        mishra = copy.deepcopy(self.mishra)
        zimone = copy.deepcopy(self.zimone)
        for deck, names in ((mishra, a_cards), (zimone, b_cards)):
            entries = [
                entry for entry in deck.entries if entry.board == "mainboard"
            ]
            for entry, name in zip(
                entries[: len(names)], names, strict=True
            ):
                entry.name = name
        session = make_session(
            self.db,
            mishra,
            zimone,
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
    def card(engine, seat: str, name: str):
        return next(
            card
            for card in engine.state.cards.values()
            if card.owner == seat and card.printed_name == name
        )

    @staticmethod
    def prepare_priority(session, source, *, mana: int = 0):
        engine = session.engine
        engine.move_card(
            source.object_id,
            "battlefield",
            controller="A",
            tapped=False,
            log=False,
        )
        engine.state.players["A"].turns_begun = 1
        source.acquired_control_turn_count = 0
        engine.state.players["A"].mana_pool["C"] = mana
        engine.state.active_player = "A"
        engine.state.started = True
        engine.state.phase = "precombat_main"
        engine.state.step = "main"
        engine._grant_priority("A")
        engine.pump()

    @staticmethod
    def action(session, action_id: str):
        packet = session.packet("pilot:A", full=True)
        return next(
            action
            for action in packet["decision"]["ctx"]["legal"]["actions"]
            if action["id"] == action_id
        )

    @staticmethod
    def pass_until(session, predicate, *, limit: int = 24):
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

    def assert_replays(self, session):
        expected_hash = authoritative_state_hash(session.state)
        with tempfile.TemporaryDirectory() as temporary:
            record_dir = Path(temporary) / "activated-draw-record"
            session.save(record_dir)
            replay = replay_record(record_dir, self.db, verify=True)
        self.assertTrue(replay["ok"], replay)
        self.assertEqual(expected_hash, replay["final_state_hash"])

    def test_sacrifice_draw_activation_is_offered_resolves_and_replays(self):
        session = self.session_with_cards(
            a_cards=("Mind Stone",), seed=121601
        )
        engine = session.engine
        source = self.card(engine, "A", "Mind Stone")
        self.prepare_priority(session, source, mana=1)
        action_id = f"activate:{source.ref}:ab2"
        self.action(session, action_id)
        top = engine.state.players["A"].zones["library"][-1]
        hand_before = len(engine.state.players["A"].zones["hand"])
        session.initial_checkpoint = checkpoint_envelope(engine.state)
        session.commands.clear()
        session.decisions.clear()

        result = session.act("pilot:A", {"action_id": action_id})
        self.assertTrue(result.ok, result.summary)
        self.assertEqual("graveyard", source.zone)
        self.assertEqual(0, engine.state.players["A"].mana_pool["C"])
        self.assertTrue(engine.state.stack)

        self.pass_until(session, lambda: not engine.state.stack)

        self.assertEqual(
            hand_before + 1, len(engine.state.players["A"].zones["hand"])
        )
        self.assertIn(top, engine.state.players["A"].zones["hand"])
        opponent = session.packet("pilot:B", full=True)
        self.assertNotIn("hand", opponent["state"]["players"]["A"])
        self.assert_replays(session)

    def test_unpayable_draw_activation_is_not_offered_and_rejects_atomically(self):
        session = self.session_with_cards(
            a_cards=("Mind Stone",), seed=121606
        )
        engine = session.engine
        source = self.card(engine, "A", "Mind Stone")
        self.prepare_priority(session, source)
        action_id = f"activate:{source.ref}:ab2"
        packet = session.packet("pilot:A", full=True)
        offered = {
            action["id"]
            for action in packet["decision"]["ctx"]["legal"]["actions"]
        }
        self.assertNotIn(action_id, offered)

        result = session.act("pilot:A", {"action_id": action_id})

        self.assertFalse(result.ok)
        self.assertEqual("battlefield", source.zone)
        self.assertFalse(source.tapped)
        self.assertFalse(engine.state.stack)
        self.assertEqual(0, sum(engine.state.players["A"].mana_pool.values()))

    def test_target_player_draw_validates_target_resolves_privately_and_replays(self):
        session = self.session_with_cards(
            a_cards=("Limestone Golem",), players=4, seed=121607
        )
        engine = session.engine
        source = self.card(engine, "A", "Limestone Golem")
        self.prepare_priority(session, source, mana=2)
        action_id = f"activate:{source.ref}:ab1"
        action = self.action(session, action_id)
        self.assertEqual(
            {"A", "B", "C", "D"},
            set(action["target_schema"]["legal_refs"]),
        )
        target_top = engine.state.players["B"].zones["library"][-1]
        target_top_ref = engine.state.cards[target_top].ref
        a_hand_before = len(engine.state.players["A"].zones["hand"])
        b_hand_before = len(engine.state.players["B"].zones["hand"])
        session.initial_checkpoint = checkpoint_envelope(engine.state)
        session.commands.clear()
        session.decisions.clear()
        state_before = authoritative_state_hash(engine.state)

        rejected = session.act(
            "pilot:A",
            {"action_id": action_id, "targets": [source.ref]},
        )
        self.assertFalse(rejected.ok)
        self.assertEqual(state_before, authoritative_state_hash(engine.state))
        source = engine.state.cards[source.object_id]

        result = session.act(
            "pilot:A",
            {"action_id": action_id, "targets": ["B"]},
        )
        self.assertTrue(result.ok, result.summary)
        self.assertEqual("graveyard", source.zone)
        self.pass_until(session, lambda: not engine.state.stack)

        self.assertEqual(
            a_hand_before, len(engine.state.players["A"].zones["hand"])
        )
        self.assertEqual(
            b_hand_before + 1, len(engine.state.players["B"].zones["hand"])
        )
        self.assertIn(target_top, engine.state.players["B"].zones["hand"])
        self.assertNotIn(
            "hand", session.packet("pilot:A", full=True)["state"]["players"]["B"]
        )
        self.assertIn(
            target_top_ref,
            {
                card["id"]
                for card in session.packet("pilot:B", full=True)["state"]["players"][
                    "B"
                ]["hand"]
            },
        )
        self.assert_replays(session)

    def test_draw_replacement_completes_after_cost_and_before_resolution(self):
        session = self.session_with_cards(
            a_cards=("Mind Stone", "Life from the Loam"), seed=121602
        )
        engine = session.engine
        source = self.card(engine, "A", "Mind Stone")
        dredge = self.card(engine, "A", "Life from the Loam")
        engine.move_card(
            dredge.object_id,
            "graveyard",
            log=False,
            semantic_events=False,
        )
        self.prepare_priority(session, source, mana=1)
        action_id = f"activate:{source.ref}:ab2"
        top = engine.state.players["A"].zones["library"][-1]
        session.initial_checkpoint = checkpoint_envelope(engine.state)
        session.commands.clear()
        session.decisions.clear()

        result = session.act("pilot:A", {"action_id": action_id})
        self.assertTrue(result.ok, result.summary)
        self.pass_until(
            session,
            lambda: bool(
                engine.state.pending_decision
                and engine.state.pending_decision.kind == "draw.replacement"
            ),
        )

        self.assertEqual("graveyard", source.zone)
        self.assertTrue(engine.state.stack)
        affected = session.packet("pilot:A", full=True)
        opponent = session.packet("pilot:B", full=True)
        self.assertEqual("draw.replacement", affected["decision"]["kind"])
        self.assertIsNone(opponent["decision"])
        choice = session.act(
            "pilot:A",
            {"action_id": "choose", "choice": dredge.ref},
        )
        self.assertTrue(choice.ok, choice.summary)
        self.assertEqual("hand", dredge.zone)
        self.assertIn(top, engine.state.players["A"].zones["graveyard"])
        self.assertNotIn(top, engine.state.players["A"].zones["hand"])
        self.assertFalse(engine.state.stack)
        self.assert_replays(session)

    def test_draw_prohibition_does_not_make_activation_illegal(self):
        session = self.session_with_cards(
            a_cards=("Mind Stone",),
            b_cards=("Spirit of the Labyrinth",),
            seed=121603,
        )
        engine = session.engine
        source = self.card(engine, "A", "Mind Stone")
        restriction = self.card(engine, "B", "Spirit of the Labyrinth")
        engine.move_card(
            restriction.object_id,
            "battlefield",
            controller="B",
            log=False,
        )
        engine._begin_draw_sequence("A", 1, reason="use first draw allowance")
        self.prepare_priority(session, source, mana=1)
        action_id = f"activate:{source.ref}:ab2"
        self.action(session, action_id)
        hand_before = len(engine.state.players["A"].zones["hand"])
        session.initial_checkpoint = checkpoint_envelope(engine.state)
        session.commands.clear()
        session.decisions.clear()

        result = session.act("pilot:A", {"action_id": action_id})
        self.assertTrue(result.ok, result.summary)
        self.pass_until(session, lambda: not engine.state.stack)

        self.assertEqual("graveyard", source.zone)
        self.assertEqual(
            hand_before, len(engine.state.players["A"].zones["hand"])
        )
        self.assertTrue(
            any(event.code == "card.draw.prohibited" for event in engine.state.events)
        )
        self.assert_replays(session)

    def test_empty_library_does_not_remove_the_activated_ability(self):
        session = self.session_with_cards(
            a_cards=("Mind Stone",), seed=121605
        )
        engine = session.engine
        source = self.card(engine, "A", "Mind Stone")
        for object_id in list(engine.state.players["A"].zones["library"]):
            engine.move_card(
                object_id,
                "exile",
                log=False,
                semantic_events=False,
            )
        self.prepare_priority(session, source, mana=1)
        action_id = f"activate:{source.ref}:ab2"
        self.action(session, action_id)
        session.initial_checkpoint = checkpoint_envelope(engine.state)
        session.commands.clear()
        session.decisions.clear()

        result = session.act("pilot:A", {"action_id": action_id})
        self.assertTrue(result.ok, result.summary)
        self.pass_until(session, lambda: not engine.state.stack)

        self.assertNotEqual("battlefield", source.zone)
        self.assertTrue(engine.state.players["A"].attempted_empty_draw)
        self.assertNotIn("A", engine.active_seats)
        self.assertTrue(
            any(event.code == "card.draw.empty" for event in engine.state.events)
        )
        self.assert_replays(session)

    def test_each_player_activation_draws_in_four_player_game(self):
        session = self.session_with_cards(
            a_cards=("Temple Bell",), players=4, seed=121604
        )
        engine = session.engine
        source = self.card(engine, "A", "Temple Bell")
        self.prepare_priority(session, source)
        action_id = f"activate:{source.ref}:ab1"
        self.action(session, action_id)
        before = {
            seat: len(engine.state.players[seat].zones["hand"])
            for seat in ("A", "B", "C", "D")
        }
        session.initial_checkpoint = checkpoint_envelope(engine.state)
        session.commands.clear()
        session.decisions.clear()

        result = session.act("pilot:A", {"action_id": action_id})
        self.assertTrue(result.ok, result.summary)
        self.pass_until(session, lambda: not engine.state.stack)

        self.assertEqual(
            {seat: count + 1 for seat, count in before.items()},
            {
                seat: len(engine.state.players[seat].zones["hand"])
                for seat in ("A", "B", "C", "D")
            },
        )
        self.assert_replays(session)


if __name__ == "__main__":
    unittest.main()
