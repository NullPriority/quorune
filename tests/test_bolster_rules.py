from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from quorune.carddb import CardDatabase, CardRecord
from quorune.compiler.bolster_templates import fixed_bolster_effect_template
from quorune.errors import GameRuleError
from quorune.model import CardInstance, StackItem
from quorune.oracle_ir import compile_oracle_card, generated_programs
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
from quorune.rules.node_capability_shapes import (
    fixed_bolster_node_capabilities,
)
from quorune.semantics import SemanticProgram
from tests.common import DB_PATH, keep_all, load_assets, make_session


ROOT = Path(__file__).resolve().parents[1]
REGISTRY_PATH = ROOT / "quorune" / "rules" / "capability-registry.json"


def bolster_record(
    text: str,
    *,
    suffix: int,
    type_line: str = "Sorcery",
) -> CardRecord:
    return CardRecord(
        oracle_id=f"00000000-0000-4000-9000-{suffix:012d}",
        name=f"Generic Bolster Fixture {suffix}",
        mana_cost="{2}",
        mana_value=2.0,
        type_line=type_line,
        oracle_text=text,
        power="2" if "Creature" in type_line else None,
        toughness="2" if "Creature" in type_line else None,
        loyalty=None,
        defense=None,
        colors=(),
        color_identity=(),
        keywords=("Bolster",),
        produced_mana=(),
        layout="normal",
        released_at="2026-01-01",
        legalities={"commander": "legal"},
        faces=(),
        raw={},
    )


class BolsterCompilerTests(unittest.TestCase):
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

    def test_fixed_bolster_compiles_in_spell_trigger_and_activated_contexts(
        self,
    ):
        fixtures = (
            (bolster_record("Bolster 3.", suffix=1), "spell_ability", 3),
            (
                bolster_record(
                    "When this creature enters, bolster 1.",
                    suffix=2,
                    type_line="Creature — Bird Soldier",
                ),
                "triggered_ability",
                1,
            ),
            (
                bolster_record(
                    "{2}: Bolster 2.",
                    suffix=3,
                    type_line="Artifact",
                ),
                "activated_ability",
                2,
            ),
        )
        for record, expected_kind, amount in fixtures:
            with self.subTest(kind=expected_kind):
                ir = compile_oracle_card(
                    record,
                    capability_registry=self.capabilities,
                    capability_profile="commander_review",
                )
                node = next(
                    node
                    for face in ir.faces
                    for node in face.nodes
                    if node.template_id == f"bolster-fixed-{amount}-v1"
                )
                self.assertEqual("exact", ir.status)
                self.assertEqual(expected_kind, node.kind)
                self.assertEqual(
                    (
                        {
                            "op": "fixed_bolster",
                            "player": "$controller",
                            "amount": amount,
                        },
                    ),
                    node.effects,
                )
                self.assertIn(
                    "counter.producer.bolster",
                    node.capability_dependencies,
                )
                if expected_kind == "triggered_ability":
                    self.assertIn(
                        "trigger.placement.apnap",
                        node.capability_dependencies,
                    )
                self.assertEqual(
                    record.oracle_text,
                    record.oracle_text[node.span.start : node.span.end],
                )
                program = next(
                    program
                    for program in generated_programs(
                        self.database,
                        record,
                        trust_level="trusted",
                        capability_registry=self.capabilities,
                        capability_profile="commander_review",
                    )
                    if program.provenance.get("template_id")
                    == f"bolster-fixed-{amount}-v1"
                )
                self.assertTrue(program.capability_closure["trusted"])

    def test_unsupported_bolster_variants_remain_material_residuals(self):
        for suffix, text in enumerate(
            (
                "Bolster X.",
                "Bolster 0.",
                "Bolster -1.",
                "Target creature bolsters 1.",
                "Bolster 1 twice.",
                "If you control a creature of the chosen type, bolster 2.",
                "Bolster 1, then bolster 1 again.",
            ),
            start=10,
        ):
            with self.subTest(text=text):
                self.assertIsNone(fixed_bolster_effect_template(text))
                ir = compile_oracle_card(
                    bolster_record(text, suffix=suffix),
                    capability_registry=self.capabilities,
                    capability_profile="commander_review",
                )
                self.assertNotEqual("exact", ir.status)
                self.assertTrue(ir.material_residuals)

    def test_fixed_public_condition_bolster_is_positive_integration(self):
        text = "If you control a Dragon, bolster 2."
        self.assertIsNone(fixed_bolster_effect_template(text))
        ir = compile_oracle_card(bolster_record(text, suffix=31),
            capability_registry=self.capabilities, capability_profile="commander_review")
        self.assertEqual("exact", ir.status, ir.material_residuals)
        effect = ir.faces[0].nodes[0].effects[0]
        self.assertEqual("apply_if_public_condition", effect["op"])
        self.assertEqual("fixed_bolster", effect["effects"][0]["op"])

    def test_bolster_compiler_mutant_is_killed(self):
        record = bolster_record("Bolster 2.", suffix=30)

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
            "fixed_bolster_effect_template",
            return_value=None,
        ):
            with self.assertRaises(AssertionError):
                assert_exact()

    def test_bolster_dependency_and_shape_mutations_fail_closed(self):
        valid = (
            {
                "op": "fixed_bolster",
                "player": "$controller",
                "amount": 2,
            },
        )
        self.assertEqual(
            ("counter.producer.bolster",),
            fixed_bolster_node_capabilities(
                effects=valid,
                target_schema=None,
                mechanic_ids=("bolster", "cr-122-counters"),
            ),
        )
        for effects, target_schema, mechanics in (
            (
                ({**valid[0], "future": True},),
                None,
                ("bolster", "cr-122-counters"),
            ),
            (
                ({**valid[0], "amount": True},),
                None,
                ("bolster", "cr-122-counters"),
            ),
            (
                ({**valid[0], "player": "$active"},),
                None,
                ("bolster", "cr-122-counters"),
            ),
            (valid, {"count": 1}, ("bolster", "cr-122-counters")),
            (valid, None, ("cr-122-counters",)),
        ):
            with self.subTest(effects=effects):
                self.assertEqual(
                    (),
                    fixed_bolster_node_capabilities(
                        effects=effects,
                        target_schema=target_schema,
                        mechanic_ids=mechanics,
                    ),
                )

        value = json.loads(json.dumps(self.registry_value))
        dependency = next(
            row
            for row in value["capabilities"]
            if row["id"] == "counter.placement.quantity_replacement"
        )
        dependency["status"] = "blocked"
        dependency["blockers"] = ["test mutation"]
        ir = compile_oracle_card(
            bolster_record("Bolster 2.", suffix=31),
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


class BolsterRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.database, cls.mishra, cls.zimone = load_assets()

    @classmethod
    def tearDownClass(cls):
        cls.database.close()

    def session(self, seed: int, *, players: int = 2):
        session = make_session(
            self.database,
            self.mishra,
            self.zimone,
            players=players,
            seed=seed,
        )
        keep_all(session)
        engine = session.engine
        engine.permissions.invalidate_current()
        engine.state.pending_decision = None
        engine.state.priority_player = None
        engine.state.priority_passes = []
        engine.state.stack.clear()
        engine.semantics.put(
            SemanticProgram(
                key="fixture:bolster",
                label="Bolster fixture",
                effects=[
                    {
                        "op": "fixed_bolster",
                        "player": "$controller",
                        "amount": 2,
                    }
                ],
            )
        )
        return session

    def add_permanent(
        self,
        engine,
        *,
        owner: str,
        name: str,
        ref: str,
    ) -> CardInstance:
        record = self.database.lookup(name)
        card = CardInstance(
            object_id=f"fixture:{ref}",
            ref=ref,
            oracle_id=record.oracle_id,
            printed_name=record.name,
            owner=owner,
            controller=owner,
            zone="battlefield",
            zone_timestamp=engine.state.event_sequence + 1,
            known_to=list(engine.seats),
            revealed_to=list(engine.seats),
        )
        engine.state.cards[card.object_id] = card
        engine.state.players[owner].zones["battlefield"].append(card.object_id)
        return card

    def stack_bolster(self, engine, *, actor: str = "A") -> None:
        source = next(
            card
            for card in engine.state.cards.values()
            if card.controller == actor and card.zone == "battlefield"
        )
        ref = engine._next_ref("S")
        engine.state.stack.append(
            StackItem(
                stack_id=engine._stable_runtime_id("stack", ref),
                ref=ref,
                kind="activated_ability",
                controller=actor,
                label="Bolster fixture",
                source_object_id=source.object_id,
                semantic_key="fixture:bolster",
                visibility=list(engine.seats),
                context={
                    "source_logical_object_id": source.logical_object_id
                },
            )
        )
        engine._prepare_stack_resolution()

    @staticmethod
    def choose(session, seat: str, ref: str):
        return session.act(
            f"pilot:{seat}",
            {
                "action_id": "choose",
                "objects": [ref],
                "plan": "BOLSTER",
                "reason": "Choose a creature tied for least toughness.",
            },
        )

    def choose_replacement(self, session, seat: str) -> None:
        packet = StateProjector(
            self.database, session.engine.state
        )._decision(f"pilot:{seat}")
        selected = packet["ctx"]["options"][0]["id"]
        result = session.act(
            f"pilot:{seat}",
            {
                "action_id": "choose",
                "replacement": selected,
                "plan": "ORDER_REPLACEMENTS",
                "reason": "Choose the counter replacement order.",
            },
        )
        self.assertTrue(result.ok, result.summary)

    def test_bolster_offers_every_creature_tied_for_least_effective_toughness(
        self,
    ):
        session = self.session(7013901)
        engine = session.engine
        elf = self.add_permanent(
            engine, owner="A", name="Elves of Deep Shadow", ref="least-elf"
        )
        scute = self.add_permanent(
            engine, owner="A", name="Scute Swarm", ref="least-scute"
        )
        deathrite = self.add_permanent(
            engine, owner="A", name="Deathrite Shaman", ref="larger-shaman"
        )
        self.add_permanent(
            engine, owner="B", name="Goblin Engineer", ref="opposing-creature"
        )
        self.stack_bolster(engine)

        packet = StateProjector(self.database, engine.state)._decision(
            "pilot:A"
        )
        self.assertEqual(
            [elf.ref, scute.ref],
            packet["legal_actions"][0]["choice_schema"]["legal_refs"],
        )
        self.assertNotIn(
            deathrite.ref,
            packet["legal_actions"][0]["choice_schema"]["legal_refs"],
        )
        result = self.choose(session, "A", scute.ref)
        self.assertTrue(result.ok, result.summary)
        self.assertEqual(2, scute.counters["+1/+1"])
        self.assertNotIn("+1/+1", elf.counters)

    def test_bolster_with_no_controlled_creature_auto_continues(self):
        session = self.session(7013905)
        engine = session.engine
        self.add_permanent(
            engine,
            owner="A",
            name="Sensei's Divining Top",
            ref="noncreature-source",
        )
        opposing = self.add_permanent(
            engine,
            owner="B",
            name="Goblin Engineer",
            ref="opposing-only-creature",
        )

        self.stack_bolster(engine)

        self.assertIsNone(engine.state.pending_decision)
        self.assertFalse(engine.state.stack)
        self.assertFalse(opposing.counters)

    def test_bolster_shape_and_snapshot_mutations_fail_closed(self):
        session = self.session(7013902)
        engine = session.engine
        elf = self.add_permanent(
            engine, owner="A", name="Elves of Deep Shadow", ref="stale-elf"
        )
        scute = self.add_permanent(
            engine, owner="A", name="Scute Swarm", ref="stale-scute"
        )
        self.stack_bolster(engine)
        before_stack = tuple(item.ref for item in engine.state.stack)

        scute.counters["+1/+1"] = 1
        result = self.choose(session, "A", elf.ref)

        self.assertFalse(result.ok)
        self.assertIn("effective toughness changed", result.summary)
        current_elf = engine.state.cards[elf.object_id]
        current_scute = engine.state.cards[scute.object_id]
        self.assertNotIn("+1/+1", current_elf.counters)
        self.assertEqual(1, current_scute.counters["+1/+1"])
        self.assertEqual(before_stack, tuple(item.ref for item in engine.state.stack))

    def test_unresolved_effective_toughness_fails_before_choice(self):
        session = self.session(7013904)
        engine = session.engine
        unresolved = self.add_permanent(
            engine,
            owner="A",
            name="Elves of Deep Shadow",
            ref="unresolved-toughness",
        )
        self.add_permanent(
            engine,
            owner="A",
            name="Deathrite Shaman",
            ref="fixed-toughness",
        )
        unresolved.annotations["continuous_toughness"] = "*"

        with self.assertRaisesRegex(
            GameRuleError,
            "exact effective toughness",
        ):
            self.stack_bolster(engine)
        self.assertIsNone(engine.state.pending_decision)
        self.assertFalse(unresolved.counters)

    def test_bolster_quantity_replacement_is_seat_scoped_and_replays_exactly(
        self,
    ):
        session = self.session(7013903, players=4)
        engine = session.engine
        elf = self.add_permanent(
            engine, owner="A", name="Elves of Deep Shadow", ref="replay-elf"
        )
        self.add_permanent(
            engine, owner="A", name="Deathrite Shaman", ref="replay-shaman"
        )
        self.add_permanent(
            engine, owner="A", name="Doubling Season", ref="replay-double"
        )
        self.add_permanent(
            engine,
            owner="A",
            name="Doc Samson, Super Psychiatrist",
            ref="replay-add",
        )
        self.stack_bolster(engine)
        projector = StateProjector(self.database, engine.state)
        self.assertIsNotNone(projector._decision("pilot:A"))
        self.assertIsNone(projector._decision("pilot:B"))
        decision_without_capability = dict(projector._decision("pilot:A"))
        self.assertIsInstance(decision_without_capability.pop("cap"), str)
        initial_packet = json.dumps(decision_without_capability, sort_keys=True)
        for seat in engine.seats:
            for object_id in engine.state.players[seat].zones["hand"]:
                self.assertNotIn(engine.state.cards[object_id].ref, initial_packet)

        session.initial_checkpoint = checkpoint_envelope(engine.state)
        session.commands.clear()
        session.decisions.clear()
        result = self.choose(session, "A", elf.ref)
        self.assertTrue(result.ok, result.summary)
        self.assertEqual("replacement.order", engine.state.pending_decision.kind)
        self.assertNotIn("+1/+1", elf.counters)
        self.assertIsNone(
            StateProjector(self.database, engine.state)._decision("pilot:B")
        )
        self.choose_replacement(session, "A")
        self.assertEqual(6, elf.counters["+1/+1"])
        expected_hash = authoritative_state_hash(engine.state)

        with tempfile.TemporaryDirectory() as temporary:
            record_dir = Path(temporary) / "bolster-replay"
            session.save(record_dir)
            replay = replay_record(record_dir, self.database, verify=True)
        self.assertTrue(replay["ok"], replay)
        self.assertEqual(2, replay["commands"])
        self.assertEqual(expected_hash, replay["final_state_hash"])

    def test_bolster_privacy_distinguishes_opaque_tokens_from_private_fields(self):
        original_decision = StateProjector._decision

        def projected_decision(projector, principal, *, leak=False):
            decision = original_decision(projector, principal)
            if decision is None:
                return None
            hidden_ref = next(
                projector.state.cards[object_id].ref
                for player in projector.state.players.values()
                for object_id in player.zones["hand"]
            )
            decision = {
                **decision,
                "cap": f"c_{hidden_ref}_opaque_token_collision",
            }
            if leak:
                decision["ctx"] = {**decision["ctx"], "private_ref": hidden_ref}
            return decision

        with patch.object(StateProjector, "_decision", projected_decision):
            self.test_bolster_quantity_replacement_is_seat_scoped_and_replays_exactly()

        def leaked_decision(projector, principal):
            return projected_decision(projector, principal, leak=True)

        with patch.object(StateProjector, "_decision", leaked_decision):
            with self.assertRaisesRegex(AssertionError, "unexpectedly found"):
                self.test_bolster_quantity_replacement_is_seat_scoped_and_replays_exactly()


if __name__ == "__main__":
    unittest.main()
