from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from common import ROOT, keep_all, make_session
from quorune.carddb import CardDatabase
from quorune.deck import DeckLoader
from quorune.model import CardInstance
from quorune.modular import (
    ModularError,
    ModularSpec,
    modular_counter_count,
    modular_counter_snapshot,
)
from quorune.object_query import ObjectQueryResult
from quorune.oracle_ir import compile_oracle_card, generated_programs
from quorune.projection import StateProjector
from quorune.record import (
    authoritative_state_hash,
    checkpoint_envelope,
    replay_record,
)
from quorune.replacement.immutable import FrozenMap
from quorune.replacement_effects import ReplacementChoiceRequired
from quorune.rules.capabilities import (
    CapabilityRegistry,
    load_default_capability_registry,
)
from quorune.semantic_choices.context import (
    SemanticChoiceContext,
    SnapshotSemanticChoiceQuery,
)
from quorune.semantic_choices.model import (
    SemanticChoiceContinuation,
    SemanticChoiceError,
    SemanticChoiceFrame,
)
from quorune.semantic_choices.modular import ModularCounterTransferHandler
from quorune.semantics import VALID_EFFECT_OPERATIONS
from scripts.build_test_database import build_fixture_database


REGISTRY_PATH = ROOT / "quorune" / "rules" / "capability-registry.json"


def focused_card_database(directory: str) -> CardDatabase:
    database = Path(directory) / "modular.sqlite3"
    build_fixture_database(
        [
            ROOT / "tests" / "fixtures" / "scryfall-exact-lists.json",
            ROOT / "tests" / "fixtures" / "counter-replacement-cards.json",
            ROOT / "tests" / "fixtures" / "modular-cards.json",
            ROOT / "tests" / "fixtures" / "modular-synthetic-cards.json",
        ],
        database,
    )
    return CardDatabase(database)


class ModularCompilerTests(unittest.TestCase):
    def test_modular_entry_declares_registered_counter_replacement_dependency(self):
        ir=compile_oracle_card(replace(self.db.lookup('Arcbound Worker'),oracle_text='Modular 2'),
            capability_registry=load_default_capability_registry(),capability_profile='commander_review')
        entry=next(node for node in ir.faces[0].nodes if node.template_id=='modular-fixed-entry-counter-v1')
        self.assertIn('counter.placement.quantity_replacement',entry.capability_dependencies)
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.db = focused_card_database(cls.temporary.name)
        cls.record = cls.db.lookup("Arcbound Worker")
        cls.capabilities = load_default_capability_registry()

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temporary.cleanup()

    def test_modular_model_compiler_and_multiple_instances_are_closed(self):
        with self.assertRaises(ModularError):
            ModularSpec(0)
        with self.assertRaises(ModularError):
            ModularSpec(True)
        self.assertEqual(
            {"+1/+1": 2, "charge": 1},
            dict(modular_counter_snapshot({"charge": 1, "+1/+1": 2})),
        )
        with self.assertRaises(ModularError):
            modular_counter_snapshot({"charge": 0, " charge ": 1})
        self.assertEqual(2, modular_counter_count({"+1/+1": 2}))

        text = "Modular 1, Modular 2"
        record = replace(
            self.record,
            oracle_text=text,
            keywords=("Modular",),
        )
        ir = compile_oracle_card(
            record,
            capability_registry=self.capabilities,
            capability_profile="commander_review",
        )
        nodes = [
            node
            for node in ir.faces[0].nodes
            if node.template_id
            in {
                "modular-fixed-entry-counter-v1",
                "modular-lki-counter-transfer-v1",
            }
        ]
        self.assertEqual("exact", ir.status)
        self.assertEqual(4, len(nodes))
        self.assertEqual(4, len({node.node_id for node in nodes}))
        self.assertEqual(
            ["modular 1", "modular 1", "modular 2", "modular 2"],
            sorted(
                text[node.span.start : node.span.end].casefold()
                for node in nodes
            ),
        )
        entry = [
            node for node in nodes if node.event == "zone.change"
        ]
        departure = [
            node
            for node in nodes
            if node.event == "permanent.graveyard.self"
        ]
        self.assertEqual([1, 2], [node.handlers[0]["amount"] for node in entry])
        self.assertTrue(
            all(node.handlers[0]["optional"] is False for node in entry)
        )
        self.assertEqual(2, len(departure))
        self.assertTrue(
            all(
                node.target_schema["types_all"]
                == ["artifact", "creature"]
                and node.effects[0]["op"]
                == "offer_modular_counter_transfer"
                and node.capability_dependencies
                == ("counter.producer.modular",)
                for node in departure
            )
        )
        programs = [
            program
            for program in generated_programs(
                self.db,
                record,
                trust_level="trusted",
                capability_registry=self.capabilities,
                capability_profile="commander_review",
            )
            if program.provenance.get("template_id")
            in {
                "modular-fixed-entry-counter-v1",
                "modular-lki-counter-transfer-v1",
            }
        ]
        self.assertEqual(4, len(programs))
        self.assertEqual(4, len({program.key for program in programs}))

    def test_modular_sunburst_and_malformed_values_fail_closed(self):
        for text in (
            "Modular 0",
            "Modular X",
            "Modular — Sunburst",
            "Modular — Whenever this enters",
        ):
            with self.subTest(text=text):
                ir = compile_oracle_card(
                    replace(
                        self.record,
                        oracle_text=text,
                        keywords=("Modular",),
                    ),
                    capability_registry=self.capabilities,
                    capability_profile="commander_review",
                )
                self.assertNotEqual("exact", ir.status)
                self.assertTrue(ir.material_residuals)
                self.assertFalse(
                    any(
                        node.handlers or node.effects
                        for node in ir.faces[0].nodes
                        if "modular" in node.mechanics
                    )
                )

    def test_modular_dependency_and_compiler_mutations_fail_closed(self):
        self.assertNotIn(
            "offer_modular_counter_transfer",
            VALID_EFFECT_OPERATIONS,
        )
        value = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
        dependency = next(
            row
            for row in value["capabilities"]
            if row["id"] == "target.revalidate_resolution"
        )
        dependency["status"] = "blocked"
        dependency["blockers"] = ["test mutation"]
        ir = compile_oracle_card(
            self.record,
            capability_registry=CapabilityRegistry(value),
            capability_profile="commander_review",
        )
        self.assertNotEqual("exact", ir.status)
        self.assertTrue(
            any(
                "target.revalidate_resolution" in blocker
                for residual in ir.material_residuals
                for blocker in residual.blockers
            )
        )

        with patch("quorune.oracle_ir.modular_keyword_nodes", return_value=()):
            ir = compile_oracle_card(
                self.record,
                capability_registry=self.capabilities,
                capability_profile="commander_review",
            )
        self.assertFalse(
            any(
                node.template_id
                in {
                    "modular-fixed-entry-counter-v1",
                    "modular-lki-counter-transfer-v1",
                }
                for node in ir.faces[0].nodes
            )
        )


class ModularChoiceTests(unittest.TestCase):
    @staticmethod
    def query(*, types=("artifact", "creature"), phased_out=False):
        return SnapshotSemanticChoiceQuery(
            seat_order=("A", "B"),
            active_order=("A", "B"),
            object_rows=(
                ObjectQueryResult(
                    object_id="target-id",
                    logical_object_id="target@1",
                    ref="target",
                    printed_name="Target",
                    owner="B",
                    controller="B",
                    zone="battlefield",
                    types=types,
                    phased_out=phased_out,
                ),
            ),
        )

    @staticmethod
    def effect(**updates):
        value = {
            "op": "offer_modular_counter_transfer",
            "player": "A",
            "card": "target",
            "amount": 3,
            "counter_snapshot": {"+1/+1": 3, "charge": 2},
            "source": "S-modular",
            "rule_id": "702.43a",
        }
        value.update(updates)
        return value

    @staticmethod
    def context(query):
        return SemanticChoiceContext(
            actor="A",
            stack_ref="S-modular",
            stack_controller="A",
            stack_label="Modular",
            source_ref="modular-source",
            card_ref=None,
            semantic_program_id="test:modular",
            semantic_program_version=1,
            query=query,
        )

    @staticmethod
    def continuation(handler, effect):
        return SemanticChoiceContinuation(
            handler_id=handler.handler_id,
            handler_version=handler.schema_version,
            stack_ref="S-modular",
            effect=effect,
            remaining=(),
            destination=None,
            note="Modular",
            semantic_frame=SemanticChoiceFrame(
                semantic_program_id="test:modular",
                semantic_program_version=1,
                stack_object="S-modular",
                instruction_pointer=0,
                controller="A",
            ),
        )

    def test_modular_target_and_lki_identity_are_strict(self):
        handler = ModularCounterTransferHandler()
        with self.assertRaises(SemanticChoiceError):
            handler.prepare(
                self.effect(amount=2),
                self.context(self.query()),
            )
        with self.assertRaises(SemanticChoiceError):
            handler.prepare(
                self.effect(extra=True),
                self.context(self.query()),
            )

        prepared = handler.prepare(
            self.effect(),
            self.context(self.query()),
        )
        self.assertIsNotNone(prepared.request)
        continuation = self.continuation(
            handler, prepared.continuation_effect
        )
        completion = handler.complete(
            continuation,
            {"choice": "put"},
            self.query(),
        )
        self.assertEqual(3, completion.prepend_effects[0]["amount"])
        with self.assertRaises(SemanticChoiceError):
            handler.complete(
                continuation,
                {"choice": "put"},
                self.query(types=("artifact",)),
            )


class ModularRuntimeTests(unittest.TestCase):
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
        cls.capabilities = load_default_capability_registry()

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temporary.cleanup()

    def session(self, seed: int, *, players: int = 2):
        session = make_session(
            self.db,
            self.mishra,
            self.zimone,
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
        engine.state.pending_trigger_batches.clear()
        engine.state.stack.clear()
        session.commands.clear()
        session.decisions.clear()
        return session

    def add_card(self, engine, *, seat, name, ref, zone="battlefield"):
        record = self.db.lookup(name)
        visible = list(engine.seats) if zone == "battlefield" else [seat]
        card = CardInstance(
            object_id=f"fixture:{ref}",
            ref=ref,
            oracle_id=record.oracle_id,
            printed_name=record.name,
            owner=seat,
            controller=seat,
            zone=zone,
            zone_timestamp=engine.state.event_sequence + 1,
            known_to=visible,
            revealed_to=visible if zone == "battlefield" else [],
        )
        engine.state.cards[card.object_id] = card
        engine.state.players[seat].zones[zone].append(card.object_id)
        return card

    def register_modular(
        self,
        engine,
        *,
        name: str = "Arcbound Worker",
        expected_instances: int = 1,
    ):
        record = self.db.lookup(name)
        programs = [
            program
            for program in generated_programs(
                self.db,
                record,
                trust_level="trusted",
                capability_registry=self.capabilities,
                capability_profile="commander_review",
            )
            if program.provenance.get("template_id")
            in {
                "modular-fixed-entry-counter-v1",
                "modular-lki-counter-transfer-v1",
            }
        ]
        self.assertEqual(2 * expected_instances, len(programs))
        for program in programs:
            engine.semantics.put(program)
        return programs

    @staticmethod
    def move_with_replacement_order(
        engine,
        object_id: str,
        destination: str,
        **kwargs,
    ):
        selections = []
        for _ in range(8):
            try:
                return engine.move_card(
                    object_id,
                    destination,
                    replacement_selections=tuple(selections),
                    **kwargs,
                )
            except ReplacementChoiceRequired as required:
                selections.append(required.pending.choice.options[0])
        raise AssertionError("Modular replacement ordering did not converge")

    def test_multiple_modular_instances_enter_and_trigger_independently(self):
        session = self.session(7024300)
        engine = session.engine
        self.register_modular(
            engine,
            name="Modular Twin Fixture",
            expected_instances=2,
        )
        self.add_card(
            engine,
            seat="A",
            name="Brudiclad, Telchor Engineer",
            ref="multiple-modular-target",
        )
        source = self.add_card(
            engine,
            seat="A",
            name="Modular Twin Fixture",
            ref="multiple-modular-source",
            zone="graveyard",
        )

        self.move_with_replacement_order(
            engine,
            source.object_id,
            "battlefield",
            controller="A",
            log=False,
        )
        self.assertEqual(3, source.counters.get("+1/+1"))
        engine.move_card(
            source.object_id,
            "graveyard",
            log=False,
            semantic_events=True,
        )
        engine._stabilize()
        self.assertEqual(1, len(engine.state.pending_trigger_batches))
        self.assertEqual(
            2,
            len(engine.state.pending_trigger_batches[0].items),
        )
        self.assertEqual(
            2,
            len(
                {
                    item["semantic_key"]
                    for item in engine.state.pending_trigger_batches[0].items
                }
            ),
        )

    def test_noncreature_modular_permanent_uses_permanent_graveyard_event(self):
        session = self.session(7024304)
        engine = session.engine
        self.register_modular(engine, name="Power Depot")
        self.add_card(
            engine,
            seat="A",
            name="Brudiclad, Telchor Engineer",
            ref="power-depot-target",
        )
        source = self.add_card(
            engine,
            seat="A",
            name="Power Depot",
            ref="power-depot-source",
        )
        source.counters["+1/+1"] = 1

        engine.move_card(
            source.object_id,
            "graveyard",
            log=False,
            semantic_events=True,
        )

        self.assertEqual(1, len(engine.state.pending_trigger_batches))
        item = engine.state.pending_trigger_batches[0].items[0]
        self.assertEqual("permanent.graveyard", item["context"]["event"])
        self.assertEqual("A", item.controller)

    @staticmethod
    def _act_pending(
        session,
        *,
        target_ref: str,
        actor: str = "A",
        accept: bool = True,
    ):
        for _ in range(32):
            decision = session.state.pending_decision
            if decision is not None and decision.kind == "semantic.choice":
                return session.act(
                    f"pilot:{actor}",
                    {
                        "action_id": "choose",
                        "choice": "put" if accept else "decline",
                    },
                )
            principals = session.pending_principals()
            if not principals:
                session.engine._stabilize()
                principals = session.pending_principals()
            if not principals:
                continue
            principal = principals[0]
            decision = session.state.pending_decision
            if decision is not None and "target" in decision.kind:
                result = session.act(
                    principal,
                    {"action_id": "choose", "targets": [target_ref]},
                )
            elif decision is not None and decision.kind == "trigger.order":
                refs = [
                    item.ref
                    for item in session.engine.state.pending_trigger_batches[0].items
                ]
                result = session.act(
                    principal,
                    {"action_id": "order", "triggers": refs},
                )
            else:
                result = session.act(principal, {"action_id": "pass"})
            if not result.ok:
                return result
        raise AssertionError("Modular resolution did not reach its optional choice")

    def test_modular_entry_and_departure_transfer_use_canonical_counter_paths(self):
        session = self.session(7024301)
        engine = session.engine
        self.register_modular(engine)
        target = self.add_card(
            engine,
            seat="A",
            name="Brudiclad, Telchor Engineer",
            ref="modular-target",
        )
        source = self.add_card(
            engine,
            seat="A",
            name="Arcbound Worker",
            ref="modular-source",
            zone="graveyard",
        )

        engine.move_card(
            source.object_id,
            "battlefield",
            controller="A",
            log=False,
        )
        self.assertEqual(1, source.counters.get("+1/+1"))
        source.counters["+1/+1"] = 3
        engine.move_card(
            source.object_id,
            "graveyard",
            log=False,
            semantic_events=True,
        )
        engine._stabilize()
        result = self._act_pending(session, target_ref=target.ref)
        self.assertTrue(result.ok, result.summary)
        self.assertEqual(3, target.counters.get("+1/+1"))
        self.assertEqual("graveyard", source.zone)

    def test_modular_source_departure_control_and_target_revalidation(self):
        session = self.session(7024303)
        engine = session.engine
        self.register_modular(engine)
        target = self.add_card(
            engine,
            seat="B",
            name="Brudiclad, Telchor Engineer",
            ref="controlled-modular-target",
        )
        source = self.add_card(
            engine,
            seat="A",
            name="Arcbound Worker",
            ref="controlled-modular-source",
        )
        source.counters["+1/+1"] = 2
        engine.change_control(
            source.object_id,
            "B",
            reason="Modular control-change witness",
        )
        engine.move_card(
            source.object_id,
            "graveyard",
            log=False,
            semantic_events=True,
        )
        engine._stabilize()
        self.assertEqual(["pilot:B"], session.pending_principals())
        result = self._act_pending(
            session,
            target_ref=target.ref,
            actor="B",
        )
        self.assertTrue(result.ok, result.summary)
        self.assertEqual(2, target.counters.get("+1/+1"))

        handler = ModularCounterTransferHandler()
        query = SnapshotSemanticChoiceQuery(
            seat_order=("A", "B"),
            active_order=("A", "B"),
            object_rows=(
                ObjectQueryResult(
                    object_id="target-id",
                    logical_object_id="target@1",
                    ref="target",
                    printed_name="Target",
                    owner="B",
                    controller="B",
                    zone="battlefield",
                    types=("artifact", "creature"),
                ),
            ),
        )
        prepared = handler.prepare(
            {
                "op": handler.operation,
                "player": "A",
                "card": "target",
                "amount": 1,
                "counter_snapshot": {"+1/+1": 1},
                "source": "S-control",
                "rule_id": "702.43a",
            },
            SemanticChoiceContext(
                actor="A",
                stack_ref="S-control",
                stack_controller="A",
                stack_label="Modular",
                source_ref="departed-source",
                card_ref=None,
                semantic_program_id="test:modular-control",
                semantic_program_version=1,
                query=query,
            ),
        )
        self.assertIsNotNone(prepared.request)
        self.assertEqual("A", prepared.continuation_effect["player"])
        continuation = SemanticChoiceContinuation(
            handler_id=handler.handler_id,
            handler_version=handler.schema_version,
            stack_ref="S-control",
            effect=prepared.continuation_effect,
            remaining=(),
            destination=None,
            note="Modular control",
            semantic_frame=SemanticChoiceFrame(
                semantic_program_id="test:modular-control",
                semantic_program_version=1,
                stack_object="S-control",
                instruction_pointer=0,
                controller="A",
            ),
        )
        declined = handler.complete(continuation, {"choice": "decline"}, query)
        self.assertEqual((), declined.prepend_effects)
        changed_target = replace(query.object_rows[0], types=("artifact",))
        with self.assertRaises(SemanticChoiceError):
            handler.complete(
                continuation,
                {"choice": "put"},
                SnapshotSemanticChoiceQuery(
                    seat_order=("A", "B"),
                    active_order=("A", "B"),
                    object_rows=(changed_target,),
                ),
            )

    def test_four_player_modular_choice_is_controller_scoped_public_and_replays(self):
        session = self.session(7024302, players=4)
        engine = session.engine
        self.register_modular(engine)
        target = self.add_card(
            engine,
            seat="A",
            name="Brudiclad, Telchor Engineer",
            ref="four-player-modular-target",
        )
        source = self.add_card(
            engine,
            seat="A",
            name="Arcbound Worker",
            ref="four-player-modular-source",
        )
        source.counters["+1/+1"] = 2
        hidden = next(
            engine.state.cards[object_id]
            for object_id in engine.state.players["B"].zones["hand"]
        )
        engine.move_card(
            source.object_id,
            "graveyard",
            log=False,
            semantic_events=True,
        )
        engine._stabilize()
        session.initial_checkpoint = checkpoint_envelope(engine.state)
        session.commands.clear()
        session.decisions.clear()

        projected = StateProjector(self.db, engine.state)
        self.assertIsNotNone(projected._decision("pilot:A"))
        for seat in ("B", "C", "D"):
            self.assertIsNone(projected._decision(f"pilot:{seat}"))
        self.assertNotIn(
            hidden.ref,
            json.dumps(projected._decision("pilot:A"), sort_keys=True),
        )
        result = self._act_pending(session, target_ref=target.ref)
        self.assertTrue(result.ok, result.summary)
        self.assertEqual(2, target.counters.get("+1/+1"))
        expected_hash = authoritative_state_hash(engine.state)
        with tempfile.TemporaryDirectory() as temporary:
            record_dir = Path(temporary) / "modular-replay"
            session.save(record_dir)
            replay = replay_record(record_dir, self.db, verify=True)
        self.assertTrue(replay["ok"], replay)
        self.assertEqual(expected_hash, replay["final_state_hash"])


if __name__ == "__main__":
    unittest.main()
