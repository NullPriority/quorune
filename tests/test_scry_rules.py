from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import tempfile
import unittest
import uuid
from unittest.mock import patch

from common import keep_all, load_assets, make_session
from quorune.carddb import CardRecord
from quorune.model import StackItem
from quorune.oracle_ir import compile_oracle_card
from quorune.record import (
    authoritative_state_hash,
    checkpoint_envelope,
    replay_record,
)
from quorune.rules import capabilities as capabilities_module
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.node_capability_shapes import fixed_scry_node_capabilities
from quorune.semantics import SemanticProgram


SCRY_CAPABILITY = "library.scry.fixed_controller"


class ScryCompilerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.capabilities = load_default_capability_registry()
        cls.base = CardRecord(
            oracle_id="fixture:fixed-scry-base",
            name="Fixed Scry Base",
            mana_cost="{U}",
            mana_value=1.0,
            type_line="Sorcery",
            oracle_text="Scry 1.",
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

    def fixture(self, text: str = "Scry 3."):
        return replace(
            self.base,
            oracle_id="fixture:fixed-scry",
            name="Fixed Scry Fixture",
            oracle_text=text,
            type_line="Sorcery",
            keywords=(),
        )

    def test_fixed_scry_compiles_capability_closed(self):
        ir = compile_oracle_card(
            self.fixture(),
            capability_registry=self.capabilities,
            capability_profile="commander_review",
        )
        self.assertEqual("exact", ir.status)
        node = ir.faces[0].nodes[0]
        self.assertEqual("scry-controller-v1", node.template_id)
        self.assertEqual(1, node.span.line)
        self.assertEqual((SCRY_CAPABILITY,), node.capability_dependencies)
        self.assertEqual(
            ({"op": "scry", "player": "$controller", "count": 3},),
            node.effects,
        )

    def test_fixed_scry_shape_is_closed(self):
        valid = ({"op": "scry", "player": "$controller", "count": 2},)
        self.assertEqual(
            (SCRY_CAPABILITY,),
            fixed_scry_node_capabilities(
                effects=valid,
                target_schema=None,
                mechanic_ids=("scry",),
            ),
        )
        for effects, target, mechanics in (
            (({**valid[0], "count": True},), None, ("scry",)),
            (({**valid[0], "count": 0},), None, ("scry",)),
            (({**valid[0], "player": "$target.0"},), None, ("scry",)),
            (({**valid[0], "extra": 1},), None, ("scry",)),
            (valid, {"zones": ["player"]}, ("scry",)),
            (valid, None, ()),
        ):
            with self.subTest(effects=effects, target=target, mechanics=mechanics):
                self.assertEqual(
                    (),
                    fixed_scry_node_capabilities(
                        effects=effects,
                        target_schema=target,
                        mechanic_ids=mechanics,
                    ),
                )

    def test_scry_capability_gate_mutant_is_killed(self):
        def assert_exact() -> None:
            ir = compile_oracle_card(
                self.fixture(),
                capability_registry=self.capabilities,
                capability_profile="commander_review",
            )
            self.assertEqual("exact", ir.status)
            self.assertEqual(
                (SCRY_CAPABILITY,),
                ir.faces[0].nodes[0].capability_dependencies,
            )

        assert_exact()
        with patch.object(
            capabilities_module,
            "fixed_scry_node_capabilities",
            return_value=(),
        ):
            with self.assertRaises(AssertionError):
                assert_exact()


class ScryRuntimeTests(unittest.TestCase):
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

    def begin_scry(
        self,
        session,
        *,
        seat: str = "A",
        count: int = 3,
        expect_decision: bool = True,
    ) -> tuple[str, ...]:
        engine = session.engine
        expected = (
            tuple(
                engine.state.cards[object_id].ref
                for object_id in reversed(
                    engine.state.players[seat].zones["library"][-count:]
                )
            )
            if count
            else ()
        )
        card = next(
            card
            for card in engine.state.cards.values()
            if card.owner == seat
            and card.is_card_object
            and card.zone not in {"command", "outside", "library"}
        )
        engine._remove_from_zone(card)
        engine._reset_zone_change(card, "stack")
        card.zone = "stack"
        card.controller = seat
        card.known_to = list(engine.seats)
        card.revealed_to = list(engine.seats)
        key = f"test:scry:{seat}:{count}"
        program = SemanticProgram(
            key=key,
            label=f"Scry {count}",
            effects=[{"op": "scry", "player": seat, "count": count}],
            destination="graveyard",
        )
        engine.semantics.put(program)
        item = StackItem(
            stack_id=uuid.uuid4().hex,
            ref=f"S-scry-{seat}-{count}",
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
            note="typed Scry regression",
        )
        if expect_decision:
            self.assertEqual("semantic.choice", engine.state.pending_decision.kind)
        session.commands.clear()
        session.decisions.clear()
        session.initial_checkpoint = checkpoint_envelope(engine.state)
        return expected

    def test_scry_orders_top_and_bottom_and_replays_exactly(self):
        session = self.session(73001)
        expected = self.begin_scry(session)
        schema = session.state.pending_decision.payload_by_actor["A"][
            "legal_actions"
        ][0]["choice_schema"]
        self.assertEqual("ordered_partition", schema["shape"])
        self.assertEqual("library_bottom", schema["destination"])
        self.assertEqual(list(expected), schema["legal_refs"])
        top = (expected[2], expected[0])
        bottom = (expected[1],)
        accepted = session.act(
            "pilot:A",
            {
                "action_id": "choose",
                "cards": {"top": list(top), "bottom": list(bottom)},
                "plan": "FILTER_DRAW",
                "reason": "Choose both Scry groups and their exact order.",
            },
        )
        self.assertTrue(accepted.ok, accepted.summary)
        library = session.state.players["A"].zones["library"]
        self.assertEqual(list(top), [
            session.state.cards[object_id].ref
            for object_id in reversed(library[-2:])
        ])
        self.assertEqual(bottom[0], session.state.cards[library[0]].ref)
        with tempfile.TemporaryDirectory() as temporary:
            record = Path(temporary) / "scry"
            session.save(record)
            replay = replay_record(record, self.db, verify=True)
        self.assertTrue(replay["ok"], replay)

    def test_scry_zero_has_no_event_and_positive_empty_library_completes_event(self):
        zero = self.session(73002)
        self.begin_scry(zero, count=0, expect_decision=False)
        self.assertIsNone(zero.state.pending_decision)
        self.assertFalse(
            any(event.code == "library.scry" for event in zero.state.events)
        )

        empty = self.session(73003)
        engine = empty.engine
        for object_id in list(engine.state.players["A"].zones["library"]):
            engine.move_card(object_id, "graveyard", log=False)
        self.begin_scry(empty, count=3, expect_decision=False)
        self.assertIsNone(empty.state.pending_decision)
        self.assertTrue(
            any(event.code == "library.scry" for event in empty.state.events)
        )

    def test_scry_response_and_stale_library_fail_before_mutation(self):
        malformed = self.session(73004)
        expected = self.begin_scry(malformed)
        before = authoritative_state_hash(malformed.state)
        rejected = malformed.act(
            "pilot:A",
            {
                "action_id": "choose",
                "cards": {
                    "top": [expected[0], expected[0]],
                    "bottom": list(expected[1:]),
                },
            },
        )
        self.assertFalse(rejected.ok)
        self.assertEqual(before, authoritative_state_hash(malformed.state))

        stale = self.session(73005)
        expected = self.begin_scry(stale)
        library = stale.state.players["A"].zones["library"]
        library[-1], library[-2] = library[-2], library[-1]
        before = authoritative_state_hash(stale.state)
        rejected = stale.act(
            "pilot:A",
            {
                "action_id": "choose",
                "cards": {"top": list(expected), "bottom": []},
            },
        )
        self.assertFalse(rejected.ok)
        self.assertEqual(before, authoritative_state_hash(stale.state))

    def test_scry_more_than_library_uses_every_card(self):
        session = self.session(73006)
        engine = session.engine
        library = engine.state.players["A"].zones["library"]
        for object_id in list(library[:-2]):
            engine.move_card(object_id, "graveyard", log=False)
        expected = self.begin_scry(session, count=20)
        self.assertEqual(2, len(expected))
        accepted = session.act(
            "pilot:A",
            {
                "action_id": "choose",
                "cards": {"top": [], "bottom": list(reversed(expected))},
            },
        )
        self.assertTrue(accepted.ok, accepted.summary)
        self.assertEqual(
            list(reversed(expected)),
            [engine.state.cards[object_id].ref for object_id in library],
        )

    def test_four_player_scry_is_private_and_seat_scoped(self):
        session = self.session(73007, players=4)
        expected = self.begin_scry(session, seat="C", count=2)
        for seat in "ABD":
            packet = str(session.packet(f"pilot:{seat}", full=True))
            self.assertTrue(all(ref not in packet for ref in expected))
        self.assertEqual(["C"], session.state.pending_decision.actors)
        accepted = session.act(
            "pilot:C",
            {
                "action_id": "choose",
                "cards": {"top": [expected[1]], "bottom": [expected[0]]},
            },
        )
        self.assertTrue(accepted.ok, accepted.summary)
        for seat in "ABD":
            packet = str(session.packet(f"pilot:{seat}", full=True))
            self.assertTrue(all(ref not in packet for ref in expected))

    def test_legacy_subset_response_preserves_historical_behavior(self):
        session = self.session(73008)
        expected = self.begin_scry(session, count=2)
        accepted = session.act(
            "pilot:A",
            {"action_id": "choose", "cards": [expected[0]]},
        )
        self.assertTrue(accepted.ok, accepted.summary)
        library = session.state.players["A"].zones["library"]
        self.assertEqual(expected[0], session.state.cards[library[0]].ref)
        self.assertEqual(expected[1], session.state.cards[library[-1]].ref)


if __name__ == "__main__":
    unittest.main()
