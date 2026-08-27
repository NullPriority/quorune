from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest
import uuid
from unittest.mock import patch

from common import keep_all, load_assets, make_session
from quorune.card_programs.adapters import compile_best_available_card_program
from quorune.carddb import CardRecord
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
    CapabilityRegistry,
    load_default_capability_registry,
)
from quorune.rules.library_surveillance import SurveilError
from quorune.rules.surveil_capability_shapes import (
    fixed_surveil_node_capabilities,
)
from quorune.semantic_runtime import SurveilLibraryIntent
from quorune.semantics import SemanticProgram, SemanticRegistry


SURVEIL_CAPABILITY = "library.surveil.fixed_controller"
REGISTRY_PATH = Path(__file__).parents[1] / "quorune" / "rules" / "capability-registry.json"


def base_surveil_record() -> CardRecord:
    return CardRecord(
        oracle_id="fixture:fixed-surveil-base",
        name="Fixed Surveil Base",
        mana_cost="{1}{U}",
        mana_value=2.0,
        type_line="Sorcery",
        oracle_text="Surveil 2.",
        power=None,
        toughness=None,
        loyalty=None,
        defense=None,
        colors=("U",),
        color_identity=("U",),
        keywords=("Surveil",),
        produced_mana=(),
        layout="normal",
        released_at="2026-01-01",
        legalities={"commander": "legal"},
        faces=(),
        raw={},
    )


class FixedSurveilCompilerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.capabilities = load_default_capability_registry()
        cls.base = base_surveil_record()

    def record(self, name: str, text: str, type_line: str) -> CardRecord:
        return replace(
            self.base,
            oracle_id=f"fixture:{name.casefold().replace(' ', '-')}",
            name=name,
            oracle_text=text,
            type_line=type_line,
        )

    def compile(self, record: CardRecord):
        return compile_oracle_card(
            record,
            capability_registry=self.capabilities,
            capability_profile="commander_review",
        )

    def test_fixed_surveil_compiles_across_spell_trigger_activation_and_sequence(self):
        fixtures = (
            self.record("Fixed Surveil Spell", "Surveil 2.", "Sorcery"),
            self.record(
                "Fixed Surveil Trigger",
                "When this artifact enters, surveil 2.",
                "Artifact",
            ),
            self.record(
                "Fixed Surveil Upkeep Trigger",
                "At the beginning of your upkeep, surveil 2.",
                "Enchantment",
            ),
            self.record(
                "Fixed Surveil Activation",
                "{2}, {T}: Surveil 1.",
                "Artifact",
            ),
            self.record(
                "Fixed Surveil Sequence",
                "Surveil 2. Draw a card.",
                "Sorcery",
            ),
        )
        for record in fixtures:
            with self.subTest(card=record.name):
                ir = self.compile(record)
                self.assertEqual("exact", ir.status)
                nodes = [node for face in ir.faces for node in face.nodes]
                self.assertTrue(nodes)
                surveil_effects = [
                    effect
                    for node in nodes
                    for effect in node.effects
                    if effect.get("op") == "surveil"
                ]
                self.assertEqual(1, len(surveil_effects))
                self.assertEqual("$controller", surveil_effects[0]["player"])
                self.assertGreater(surveil_effects[0]["count"], 0)
                self.assertTrue(
                    any(
                        SURVEIL_CAPABILITY in node.capability_dependencies
                        for node in nodes
                    )
                )

    def test_dynamic_targeted_linked_and_event_consumer_surveil_remain_residual(self):
        variants = (
            "Surveil 0.",
            "Surveil X.",
            "Target player surveils 2.",
            "Surveil 2, then draw a card for each card put into your graveyard this way.",
            "Whenever you surveil, draw a card.",
        )
        for index, text in enumerate(variants):
            with self.subTest(text=text):
                record = replace(
                    self.base,
                    oracle_id=f"fixture:unsupported-surveil-{index}",
                    oracle_text=text,
                    type_line=(
                        "Enchantment"
                        if text.casefold().startswith(("whenever", "at "))
                        else "Sorcery"
                    ),
                )
                ir = self.compile(record)
                self.assertNotEqual("exact", ir.status)
                self.assertTrue(ir.material_residuals)

    def test_surveil_shape_dependency_and_compiler_mutants_fail_closed(self):
        valid = ({"op": "surveil", "player": "$controller", "count": 2},)
        self.assertEqual(
            (SURVEIL_CAPABILITY,),
            fixed_surveil_node_capabilities(
                effects=valid,
                target_schema=None,
                mechanic_ids=("surveil",),
            ),
        )
        for effects, target, mechanics in (
            (({**valid[0], "count": True},), None, ("surveil",)),
            (({**valid[0], "count": 0},), None, ("surveil",)),
            (({**valid[0], "player": "$target.0"},), None, ("surveil",)),
            (({**valid[0], "extra": 1},), None, ("surveil",)),
            (valid, {"zones": ["player"]}, ("surveil",)),
            (valid, None, ()),
        ):
            with self.subTest(effects=effects, target=target, mechanics=mechanics):
                self.assertEqual(
                    (),
                    fixed_surveil_node_capabilities(
                        effects=effects,
                        target_schema=target,
                        mechanic_ids=mechanics,
                    ),
                )
        for dependency_id in (
            "trigger.event.normalized_zone_change",
            "trigger.placement.apnap",
            "zone.change.destination_replacement",
        ):
            value = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
            dependency = next(
                row
                for row in value["capabilities"]
                if row["id"] == dependency_id
            )
            dependency["status"] = "blocked"
            dependency["blockers"] = ["test mutation"]
            ir = compile_oracle_card(
                self.base,
                capability_registry=CapabilityRegistry(value),
                capability_profile="commander_review",
            )
            self.assertNotEqual("exact", ir.status)
        with patch(
            "quorune.oracle_ir.fixed_surveil_effect_template",
            return_value=None,
        ):
            ir = self.compile(self.base)
        self.assertNotEqual("exact", ir.status)


class FixedSurveilRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.db, cls.mishra, cls.zimone = load_assets()
        cls.capabilities = load_default_capability_registry()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.db.close()

    def session(self, seed: int, *, players: int = 4):
        session = make_session(
            self.db,
            self.mishra,
            self.zimone,
            players=players,
            seed=seed,
            auto_pass_empty=True,
        )
        keep_all(session)
        engine = session.engine
        engine.permissions.invalidate_current()
        engine.state.pending_decision = None
        engine.state.pending_trigger_batches.clear()
        engine.state.stack.clear()
        engine.state.priority_player = None
        engine.state.priority_passes = []
        session.commands.clear()
        session.decisions.clear()
        return session

    def begin_surveil(
        self,
        session,
        *,
        seat: str = "A",
        count: int = 3,
        expect_decision: bool = True,
        tail_effects: tuple[dict[str, object], ...] = (),
    ) -> tuple[str, ...]:
        engine = session.engine
        expected = tuple(
            engine.state.cards[object_id].ref
            for object_id in reversed(
                engine.state.players[seat].zones["library"][-count:]
            )
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
        key = f"test:surveil:{seat}:{count}"
        program = SemanticProgram(
            key=key,
            label=f"Surveil {count}",
            effects=[
                {"op": "surveil", "player": seat, "count": count},
                *tail_effects,
            ],
            destination="graveyard",
        )
        engine.semantics.put(program)
        item = StackItem(
            stack_id=uuid.uuid4().hex,
            ref=f"S-surveil-{seat}-{count}",
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
            note="typed Surveil regression",
        )
        if expect_decision:
            self.assertEqual(
                "semantic.choice",
                engine.state.pending_decision.kind,
            )
        return expected

    def register(self, engine, *names: str) -> None:
        register_generated_programs(
            self.db,
            engine.semantics,
            tuple(self.db.lookup(name) for name in names),
            trust_level="provisional",
            capability_registry=self.capabilities,
            capability_profile=engine.state.config.review_profile,
            promote_exact_runtime_handlers=True,
            promote_exact_trigger_programs=True,
            promote_exact_effect_programs=True,
            promote_exact_capability_declarations=True,
        )

    def add_card(self, engine, *, seat: str, name: str, ref: str):
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
        )
        engine.state.cards[card.object_id] = card
        engine.state.players[seat].zones["battlefield"].append(card.object_id)
        return card

    def test_surveil_orders_top_and_graveyard_and_replays_exactly(self):
        session = self.session(7012501)
        engine = session.engine
        expected = self.begin_surveil(session)
        schema = engine.state.pending_decision.payload_by_actor["A"][
            "legal_actions"
        ][0]["choice_schema"]
        self.assertEqual("ordered_partition", schema["shape"])
        self.assertEqual({"top", "graveyard"}, set(schema["partitions"]))
        self.assertNotIn("destination", schema)
        looked_cards = [
            next(card for card in engine.state.cards.values() if card.ref == ref)
            for ref in expected
        ]
        for seat in engine.seats:
            pending = json.dumps(
                StateProjector(self.db, engine.state)._snapshot(
                    f"pilot:{seat}"
                ),
                sort_keys=True,
            )
            self.assertTrue(
                all(card.object_id not in pending for card in looked_cards)
            )
            if seat != "A":
                self.assertTrue(all(ref not in pending for ref in expected))
        top = (expected[2],)
        graveyard = (expected[0], expected[1])
        session.initial_checkpoint = checkpoint_envelope(engine.state)
        session.commands.clear()
        session.decisions.clear()
        accepted = session.act(
            "pilot:A",
            {
                "action_id": "choose",
                "cards": {
                    "top": list(top),
                    "graveyard": list(graveyard),
                },
            },
        )
        self.assertTrue(accepted.ok, accepted.summary)
        self.assertEqual(
            list(top),
            [
                engine.state.cards[object_id].ref
                for object_id in reversed(
                    engine.state.players["A"].zones["library"][-len(top) :]
                )
            ],
        )
        self.assertEqual(
            list(graveyard),
            [
                engine.state.cards[object_id].ref
                for object_id in reversed(
                    engine.state.players["A"].zones["graveyard"]
                )
                if engine.state.cards[object_id].ref in graveyard
            ],
        )
        event = next(
            event
            for event in reversed(engine.state.events)
            if event.code == "library.surveil"
        )
        self.assertEqual(3, event.details["count"])
        self.assertEqual(3, event.details["looked_count"])
        self.assertEqual(list(graveyard), event.details["objects"])
        for seat in engine.seats:
            snapshot = StateProjector(self.db, engine.state)._snapshot(
                f"pilot:{seat}"
            )
            serialized = json.dumps(snapshot, sort_keys=True)
            self.assertTrue(
                all(
                    engine.state.cards[object_id].object_id not in serialized
                    for object_id in engine.state.players["A"].zones["graveyard"]
                )
            )
            if seat != "A":
                self.assertNotIn(top[0], serialized)
            self.assertTrue(all(ref in serialized for ref in graveyard))
        expected_hash = authoritative_state_hash(engine.state)
        with tempfile.TemporaryDirectory() as temporary:
            record = Path(temporary) / "surveil"
            session.save(record)
            replay = replay_record(record, self.db, verify=True)
        self.assertTrue(replay["ok"], replay)
        self.assertEqual(expected_hash, replay["final_state_hash"])

    def test_surveil_short_and_empty_library_emit_exact_events(self):
        short = self.session(7012502)
        engine = short.engine
        library = engine.state.players["A"].zones["library"]
        for object_id in list(library[:-2]):
            engine.move_card(object_id, "graveyard", log=False)
        expected = self.begin_surveil(short, count=5)
        self.assertEqual(2, len(expected))
        accepted = short.act(
            "pilot:A",
            {
                "action_id": "choose",
                "cards": {"top": list(expected), "graveyard": []},
            },
        )
        self.assertTrue(accepted.ok, accepted.summary)
        event = next(
            event
            for event in reversed(engine.state.events)
            if event.code == "library.surveil"
        )
        self.assertEqual(5, event.details["count"])
        self.assertEqual(2, event.details["looked_count"])

        empty = self.session(7012503)
        empty_engine = empty.engine
        for object_id in list(empty_engine.state.players["A"].zones["library"]):
            empty_engine.move_card(object_id, "graveyard", log=False)
        self.begin_surveil(empty, count=3, expect_decision=False)
        self.assertIsNone(empty_engine.state.pending_decision)
        event = next(
            event
            for event in reversed(empty_engine.state.events)
            if event.code == "library.surveil"
        )
        self.assertEqual(3, event.details["count"])
        self.assertEqual(0, event.details["looked_count"])

    def test_stale_identity_and_malformed_surveil_fail_before_mutation(self):
        session = self.session(7012504)
        engine = session.engine
        expected = self.begin_surveil(session)
        top = engine.state.cards[engine.state.players["A"].zones["library"][-1]]
        engine.move_card(top.object_id, "hand", log=False)
        engine.move_card(top.object_id, "library", position="top", log=False)
        before = authoritative_state_hash(engine.state)
        rejected = session.act(
            "pilot:A",
            {
                "action_id": "choose",
                "cards": {"top": list(expected), "graveyard": []},
            },
        )
        self.assertFalse(rejected.ok)
        self.assertEqual(before, authoritative_state_hash(engine.state))
        with self.assertRaises((SurveilError, TypeError, ValueError)):
            SurveilLibraryIntent(
                actor="A",
                player="A",
                arrangement=None,
                requested_count=0,
                reason="malformed Surveil",
            )
        self.assertEqual(before, authoritative_state_hash(engine.state))

    def test_surveil_destination_replacement_resumes_before_public_commit(self):
        session = self.session(7012505)
        engine = session.engine
        self.register(engine, "Dauthi Voidwalker")
        voidwalker = self.add_card(
            engine,
            seat="B",
            name="Dauthi Voidwalker",
            ref="surveil-voidwalker",
        )
        engine.create_token(
            "B",
            name="",
            copy_of=voidwalker.ref,
            reason="Surveil replacement-order witness",
        )
        expected = self.begin_surveil(session, count=1)
        accepted = session.act(
            "pilot:A",
            {
                "action_id": "choose",
                "cards": {"top": [], "graveyard": list(expected)},
            },
        )
        self.assertTrue(accepted.ok, accepted.summary)
        self.assertEqual("replacement.order", engine.state.pending_decision.kind)
        selected = next(
            card for card in engine.state.cards.values() if card.ref == expected[0]
        )
        self.assertEqual("library", selected.zone)
        self.assertIsNone(StateProjector(self.db, engine.state)._decision("pilot:B"))
        packet = json.dumps(
            StateProjector(self.db, engine.state)._snapshot("pilot:B"),
            sort_keys=True,
        )
        self.assertNotIn(selected.ref, packet)
        projected = StateProjector(self.db, engine.state)._decision("pilot:A")
        self.assertNotIn(selected.object_id, json.dumps(projected, sort_keys=True))
        replacement = projected["ctx"]["options"][0]["id"]
        accepted = session.act(
            "pilot:A",
            {
                "action_id": "choose",
                "choices": {"replacement": replacement},
            },
        )
        self.assertTrue(accepted.ok, accepted.summary)
        self.assertEqual("exile", selected.zone)
        self.assertEqual(1, selected.counters["void"])

    def test_surveil_then_draw_sequence_resumes_in_printed_order(self):
        session = self.session(7012508)
        engine = session.engine
        expected = self.begin_surveil(
            session,
            count=2,
            tail_effects=(
                {
                    "op": "draw",
                    "player": "A",
                    "count": 1,
                    "private": True,
                },
            ),
        )
        hand_before = len(engine.state.players["A"].zones["hand"])
        accepted = session.act(
            "pilot:A",
            {
                "action_id": "choose",
                "cards": {
                    "top": [expected[1]],
                    "graveyard": [expected[0]],
                },
            },
        )
        self.assertTrue(accepted.ok, accepted.summary)
        self.assertEqual("graveyard", next(
            card for card in engine.state.cards.values() if card.ref == expected[0]
        ).zone)
        self.assertEqual("hand", next(
            card for card in engine.state.cards.values() if card.ref == expected[1]
        ).zone)
        self.assertEqual(
            hand_before + 1,
            len(engine.state.players["A"].zones["hand"]),
        )

    def test_residual_surveil_pairs_remain_fail_closed_at_cardprogram_boundary(self):
        variants = (
            (
                "conditional",
                "Surveil 2.\nIf you control an artifact, draw a card.",
            ),
            (
                "multiple-targets",
                "Surveil 2.\nUp to two target players each draw a card.",
            ),
            (
                "event-consumer",
                "Surveil 2.\nWhenever you surveil, draw a card.",
            ),
        )
        base = base_surveil_record()
        for index, (label, text) in enumerate(variants):
            with self.subTest(label=label):
                record = replace(
                    base,
                    oracle_id=f"fixture:residual-surveil-{index}",
                    name=f"Residual Surveil {label}",
                    oracle_text=text,
                    type_line="Sorcery",
                )
                ir = compile_oracle_card(
                    record,
                    capability_registry=self.capabilities,
                    capability_profile="commander_review",
                )
                self.assertTrue(
                    any(
                        SURVEIL_CAPABILITY in node.capability_dependencies
                        for face in ir.faces
                        for node in face.nodes
                    )
                )
                program = compile_best_available_card_program(
                    self.db,
                    record,
                    semantic_registry=SemanticRegistry(),
                    capability_registry=self.capabilities,
                    capability_profile="commander_review",
                )
                self.assertEqual(
                    "unresolved",
                    program.trust_closure["trust_basis"],
                )
                self.assertTrue(program.residuals)
                self.assertTrue(
                    all(
                        ability.effects
                        and all(
                            effect.get("op") == "surveil"
                            for effect in ability.effects
                        )
                        for ability in program.abilities
                    )
                )

    def test_surveil_runtime_validation_mutant_is_killed(self):
        def stale_rejection(seed: int) -> None:
            session = self.session(seed)
            engine = session.engine
            expected = self.begin_surveil(session, count=1)
            top = engine.state.cards[
                engine.state.players["A"].zones["library"][-1]
            ]
            engine.move_card(top.object_id, "hand", log=False)
            engine.move_card(top.object_id, "library", position="top", log=False)
            before = authoritative_state_hash(engine.state)
            result = session.act(
                "pilot:A",
                {
                    "action_id": "choose",
                    "cards": {"top": list(expected), "graveyard": []},
                },
            )
            self.assertFalse(result.ok)
            self.assertEqual(before, authoritative_state_hash(engine.state))

        stale_rejection(7012506)
        with patch(
            "quorune.rules.library_surveillance._validated_looked_ids",
            side_effect=lambda host, actor, player, arrangement: {
                identity.ref: identity.object_id
                for identity in arrangement.looked
            },
        ):
            with self.assertRaises(AssertionError):
                stale_rejection(7012507)


if __name__ == "__main__":
    unittest.main()
