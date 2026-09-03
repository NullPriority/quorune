from __future__ import annotations

import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from common import keep_all, load_assets, make_session
from quorune.carddb import CardRecord
from quorune.compiler.direct_target import (
    DirectPermanentTargetSpec,
    direct_permanent_target_spec,
)
from quorune.compiler.destruction_templates import (
    targeted_destruction_effect_template,
)
from quorune.model import GameState, StackItem
from quorune.oracle_ir import compile_oracle_card
from quorune.record import (
    authoritative_state_hash,
    checkpoint_envelope,
    replay_record,
)
from quorune.rules.capabilities import CapabilityRegistry
from quorune.semantics import SemanticProgram
from quorune.session import CommanderSession
from quorune.target_history import (
    TargetDamageHistoryKind,
    TargetDamageHistorySpec,
)
from quorune.targets import TargetGroup


HISTORY_CAPABILITY = "target.permanent.damage_history_predicate"
REGISTRY_PATH = (
    Path(__file__).resolve().parents[1]
    / "quorune"
    / "rules"
    / "capability-registry.json"
)


def current_capabilities() -> CapabilityRegistry:
    registry = CapabilityRegistry(
        json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
    )
    registry.mark_evidence_verified("0" * 64)
    return registry


def record(
    text: str,
    *,
    suffix: int,
    type_line: str = "Instant",
) -> CardRecord:
    return CardRecord(
        oracle_id=f"00000000-0000-4000-8000-{suffix:012d}",
        name=f"Fixed Damage History Target {suffix}",
        mana_cost="{1}{B}",
        mana_value=2.0,
        oracle_text=text,
        type_line=type_line,
        power="2" if "Creature" in type_line else None,
        toughness="2" if "Creature" in type_line else None,
        loyalty=None,
        defense=None,
        colors=("B",),
        color_identity=("B",),
        keywords=(),
        produced_mana=(),
        layout="normal",
        released_at="2026-01-01",
        legalities={"commander": "legal"},
        faces=(),
        raw={},
    )


class FixedDamageHistoryTargetCompilerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.capabilities = current_capabilities()

    def compile(self, value: CardRecord, *, registry=None):
        return compile_oracle_card(
            value,
            capability_registry=registry or self.capabilities,
            capability_profile="commander_review",
        )

    def test_damage_history_targets_compile_across_effect_contexts(self):
        cases = (
            (
                record(
                    "Destroy target creature that was dealt damage this turn.",
                    suffix=162_001,
                ),
                "spell_ability",
                "was_dealt_damage",
            ),
            (
                record(
                    "When this creature enters, exile target creature that "
                    "dealt damage to you this turn.",
                    suffix=162_002,
                    type_line="Creature — Test",
                ),
                "triggered_ability",
                "dealt_damage_to_actor",
            ),
            (
                record(
                    "{1}, {T}: Tap target creature that dealt damage this turn.",
                    suffix=162_003,
                    type_line="Artifact Creature — Test",
                ),
                "activated_ability",
                "dealt_damage",
            ),
        )
        for fixture, kind, history_kind in cases:
            with self.subTest(text=fixture.oracle_text):
                ir = self.compile(fixture)
                self.assertEqual("exact", ir.status, ir.material_residuals)
                node = ir.faces[0].nodes[0]
                self.assertEqual(kind, node.kind)
                self.assertEqual(
                    {"kind": history_kind},
                    node.target_schema["damage_history"],
                )
                self.assertIn(
                    HISTORY_CAPABILITY,
                    node.capability_dependencies,
                )

    def test_damage_history_parser_and_runtime_mutations_fail_closed(self):
        fixture = record(
            "Destroy target creature that was dealt damage this turn.",
            suffix=162_010,
        )
        self.assertEqual("exact", self.compile(fixture).status)
        with patch(
            "quorune.compiler.direct_target._strip_damage_history_predicate",
            side_effect=lambda phrase: (phrase, None),
        ):
            self.assertNotEqual("exact", self.compile(fixture).status)

        value = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
        capability = next(
            row
            for row in value["capabilities"]
            if row["id"] == HISTORY_CAPABILITY
        )
        capability["status"] = "blocked"
        capability["blockers"] = ["focused damage-history mutation"]
        blocked = CapabilityRegistry(value)
        blocked.mark_evidence_verified("0" * 64)
        self.assertNotEqual(
            "exact",
            self.compile(fixture, registry=blocked).status,
        )

        spec = direct_permanent_target_spec(
            "target creature that dealt damage to you this turn"
        )
        assert spec is not None
        self.assertEqual(
            spec,
            DirectPermanentTargetSpec.from_target_schema(
                spec.to_target_schema()
            ),
        )
        malformed = spec.to_target_schema()
        malformed["damage_history"] = {"kind": "damaged_sometime"}
        with self.assertRaisesRegex(ValueError, "unsupported"):
            DirectPermanentTargetSpec.from_target_schema(malformed)

        for unsupported in (
            "target creature that was dealt damage last turn",
            "target creature that was dealt damage by a Goblin this turn",
            "target creature that dealt combat damage this turn",
            "target permanent that was dealt damage this turn",
            "target creature that dealt damage to an opponent this turn",
        ):
            with self.subTest(unsupported=unsupported):
                self.assertIsNone(direct_permanent_target_spec(unsupported))


class FixedDamageHistoryTargetRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db, cls.mishra, cls.zimone = load_assets()

    @classmethod
    def tearDownClass(cls):
        cls.db.close()

    def session(self, seed: int, *, players: int = 3):
        session = make_session(
            self.db,
            copy.deepcopy(self.mishra),
            copy.deepcopy(self.zimone),
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

    def creatures(self, engine, *, owner: str, count: int):
        result = []
        for card in engine.state.cards.values():
            if (
                card.owner != owner
                or not card.is_card_object
                or card.is_commander
            ):
                continue
            if not self.db.by_oracle_id(card.oracle_id).is_creature:
                continue
            engine.move_card(card.object_id, "battlefield", controller=owner)
            result.append(card)
            if len(result) == count:
                return tuple(result)
        self.fail(f"fixture deck lacks {count} creatures for {owner}")

    @staticmethod
    def group(subject: str) -> TargetGroup:
        spec = direct_permanent_target_spec(subject)
        assert spec is not None
        return TargetGroup.from_mapping(spec.to_target_schema())

    @staticmethod
    def legal_refs(engine, actor: str, group: TargetGroup) -> tuple[str, ...]:
        return tuple(
            str(row["ref"])
            for row in engine._target_candidate_rows(actor, group)
            if engine._target_row_matches(
                actor,
                group,
                row,
                source_ref=None,
            )
        )

    @staticmethod
    def damage(engine, *, actor: str, source, target, amount: int = 1):
        return engine.apply_effect(
            {
                "op": "damage",
                "source": source.ref,
                "target": target.ref if hasattr(target, "ref") else target,
                "amount": amount,
            },
            actor=actor,
        )

    def test_positive_damage_history_is_incarnation_bound_and_revalidated(self):
        session = self.session(162_101)
        engine = session.engine
        source, target = self.creatures(engine, owner="B", count=2)
        was_dealt = self.group(
            "target creature that was dealt damage this turn"
        )
        dealt = self.group("target creature that dealt damage this turn")
        dealt_to_actor = self.group(
            "target creature that dealt damage to you this turn"
        )

        self.assertNotIn(target.ref, self.legal_refs(engine, "A", was_dealt))
        self.damage(engine, actor="B", source=source, target=target)
        permanent_event = engine._current_turn_history("permanent_damaged")[-1]
        self.assertEqual(source.logical_object_id, permanent_event.object_incarnation)
        self.assertEqual(
            target.logical_object_id,
            permanent_event.target_object_incarnation,
        )
        self.assertIn(target.ref, self.legal_refs(engine, "A", was_dealt))
        self.assertIn(source.ref, self.legal_refs(engine, "A", dealt))
        self.assertNotIn(
            source.ref,
            self.legal_refs(engine, "A", dealt_to_actor),
        )

        self.damage(engine, actor="B", source=source, target="A")
        self.assertIn(
            source.ref,
            self.legal_refs(engine, "A", dealt_to_actor),
        )

        old_target_identity = target.logical_object_id
        engine.move_card(target.object_id, "hand")
        engine.move_card(target.object_id, "battlefield", controller="B")
        self.assertNotEqual(old_target_identity, target.logical_object_id)
        self.assertNotIn(target.ref, self.legal_refs(engine, "A", was_dealt))

        old_source_identity = source.logical_object_id
        engine.move_card(source.object_id, "hand")
        engine.move_card(source.object_id, "battlefield", controller="B")
        self.assertNotEqual(old_source_identity, source.logical_object_id)
        self.assertNotIn(source.ref, self.legal_refs(engine, "A", dealt))
        self.assertNotIn(
            source.ref,
            self.legal_refs(engine, "A", dealt_to_actor),
        )

    def test_prevented_and_previous_turn_damage_do_not_qualify(self):
        session = self.session(162_102)
        engine = session.engine
        source, target = self.creatures(engine, owner="B", count=2)
        was_dealt = self.group(
            "target creature that was dealt damage this turn"
        )
        engine.apply_effect(
            {
                "op": "create_damage_prevention_shield",
                "source": "fixture:prevent-history",
                "subject": target.ref,
                "mode": "all",
                "duration": "until_end_of_turn",
            },
            actor="A",
        )
        self.assertEqual(
            0,
            self.damage(engine, actor="B", source=source, target=target),
        )
        self.assertEqual((), engine._current_turn_history("permanent_damaged"))
        self.assertNotIn(target.ref, self.legal_refs(engine, "A", was_dealt))

        engine.state.damage_prevention_shields.clear()
        self.damage(engine, actor="B", source=source, target=target)
        self.assertIn(target.ref, self.legal_refs(engine, "A", was_dealt))
        engine.state.turn_sequence += 1
        self.assertNotIn(target.ref, self.legal_refs(engine, "A", was_dealt))

    def test_damage_history_destruction_and_draw_compose_atomically(self):
        fixture = record(
            "Destroy target creature that was dealt damage this turn. "
            "Draw a card.",
            suffix=162_020,
        )
        ir = compile_oracle_card(
            fixture,
            capability_registry=current_capabilities(),
            capability_profile="commander_review",
        )
        self.assertEqual("exact", ir.status, ir.material_residuals)
        node = ir.faces[0].nodes[0]
        self.assertEqual(
            ("destroy", "draw"),
            tuple(effect["op"] for effect in node.effects),
        )
        self.assertGreaterEqual(
            set(node.capability_dependencies),
            {
                HISTORY_CAPABILITY,
                "target.revalidate_resolution",
                "zone.draw.library_to_hand",
            },
        )

        def stage(seed: int):
            session = self.session(seed, players=4)
            engine = session.engine
            source, target = self.creatures(engine, owner="B", count=2)
            target.annotations["copy_overrides"] = {
                "power": "3",
                "toughness": "3",
            }
            self.damage(engine, actor="B", source=source, target=target)
            selected, grouped = engine._validate_semantic_targets(
                "A",
                None,
                [target.ref],
                source_ref=None,
                target_schema=node.target_schema,
            )
            program = SemanticProgram(
                key=f"fixture:damage-history-draw:{seed}",
                label="Damage history destruction and draw",
                effects=copy.deepcopy(node.effects),
                target_schema=copy.deepcopy(node.target_schema),
                trust_level="provisional",
            )
            engine.semantics.put(program)
            item = StackItem(
                stack_id=f"damage-history-draw:{seed}",
                ref=f"S-damage-history-draw:{seed}",
                kind="triggered_ability",
                controller="A",
                label=program.label,
                semantic_key=program.key,
                targets=selected,
                visibility=list(engine.seats),
                context={
                    "target_groups": grouped,
                    "target_snapshots": {
                        target.ref: engine._target_snapshot(target.ref)
                    },
                    "targets_revalidated": False,
                    "targets_chosen_at_creation": True,
                },
            )
            engine.state.stack.append(item)
            engine.state.active_player = "A"
            engine.state.phase = "precombat_main"
            engine.state.step = "main"
            return session, target, item

        def resolve(session) -> None:
            engine = session.engine
            engine._grant_priority("A")
            engine._issue_priority("A")
            session.initial_checkpoint = checkpoint_envelope(engine.state)
            session.commands.clear()
            session.decisions.clear()
            for seat in engine.seats:
                result = session.act(
                    f"pilot:{seat}",
                    {"action_id": "pass"},
                )
                self.assertTrue(result.ok, result.summary)

        session, target, item = stage(162_105)
        engine = session.engine
        hand_before = len(engine.state.players["A"].zones["hand"])
        drawn_id = engine.state.players["A"].zones["library"][-1]
        drawn = engine.state.cards[drawn_id]
        resolve(session)
        self.assertNotIn(item, engine.state.stack)
        self.assertEqual("graveyard", target.zone)
        self.assertEqual(
            hand_before + 1,
            len(engine.state.players["A"].zones["hand"]),
        )
        self.assertIn(drawn_id, engine.state.players["A"].zones["hand"])
        expected_hash = authoritative_state_hash(engine.state)
        with tempfile.TemporaryDirectory() as temporary:
            record_dir = Path(temporary) / "damage-history-draw"
            session.save(record_dir)
            replay = replay_record(record_dir, self.db, verify=True)
        self.assertTrue(replay["ok"], replay)
        self.assertEqual(expected_hash, replay["final_state_hash"])
        opposing_packet = json.dumps(session.packet("pilot:B", full=True))
        self.assertNotIn(drawn.object_id, opposing_packet)
        self.assertNotIn(drawn.ref, opposing_packet)

        stale, stale_target, stale_item = stage(162_106)
        stale_engine = stale.engine
        stale_hand = len(stale_engine.state.players["A"].zones["hand"])
        old_incarnation = stale_target.logical_object_id
        stale_engine.move_card(stale_target.object_id, "graveyard")
        stale_engine.move_card(
            stale_target.object_id,
            "battlefield",
            controller="B",
        )
        self.assertNotEqual(old_incarnation, stale_target.logical_object_id)
        resolve(stale)
        self.assertNotIn(stale_item, stale_engine.state.stack)
        self.assertEqual("battlefield", stale_target.zone)
        self.assertEqual(
            stale_hand,
            len(stale_engine.state.players["A"].zones["hand"]),
        )

    def test_damage_history_target_privacy_save_load_and_exact_replay(self):
        session = self.session(162_103, players=4)
        engine = session.engine
        source_ref, target_ref = engine.create_token(
            "B",
            name="Damage history source",
            characteristics={
                "type_line": "Token Creature — Test",
                "power": "3",
                "toughness": "3",
            },
            quantity=2,
        )
        source = engine._resolve_object(
            "B",
            source_ref,
            zones={"battlefield"},
        )
        target = engine._resolve_object(
            "B",
            target_ref,
            zones={"battlefield"},
        )
        program = SemanticProgram(
            key="fixture:damage-history-replay",
            label="Damage history replay fixture",
            effects=[
                {
                    "op": "damage",
                    "source": "$source",
                    "target": target.ref,
                    "amount": 1,
                }
            ],
            trust_level="provisional",
        )
        engine.semantics.put(program)
        engine.state.stack.append(
            StackItem(
                stack_id="damage-history-replay",
                ref="S-damage-history-replay",
                kind="triggered_ability",
                controller="B",
                label=program.label,
                source_object_id=source.object_id,
                semantic_key=program.key,
                visibility=list(engine.seats),
                context={
                    "source_logical_object_id": source.logical_object_id,
                },
            )
        )
        engine.state.active_player = "A"
        engine.state.phase = "precombat_main"
        engine.state.step = "main"
        engine._grant_priority("A")
        engine._issue_priority("A")
        session.initial_checkpoint = checkpoint_envelope(engine.state)
        session.commands.clear()
        session.decisions.clear()
        for seat in engine.seats:
            result = session.act(f"pilot:{seat}", {"action_id": "pass"})
            self.assertTrue(result.ok, result.summary)

        group = self.group("target creature that was dealt damage this turn")
        schema = direct_permanent_target_spec(
            "target creature that was dealt damage this turn"
        ).to_target_schema()
        payload = engine.state.to_dict()
        restored = GameState.from_dict(payload)
        self.assertEqual(
            authoritative_state_hash(engine.state),
            authoritative_state_hash(restored),
        )
        restored_event = restored.turn_history.events[-1]
        self.assertEqual("permanent_damaged", restored_event.kind)
        self.assertEqual(
            target.logical_object_id,
            restored_event.target_object_incarnation,
        )
        self.assertIn(target.ref, self.legal_refs(engine, "A", group))
        expected_hash = authoritative_state_hash(engine.state)

        with tempfile.TemporaryDirectory() as temporary:
            record_dir = Path(temporary) / "damage-history-target"
            session.save(record_dir)
            loaded = CommanderSession.load(self.db, record_dir)
            loaded_event = loaded.engine.state.turn_history.events[-1]
            self.assertEqual("permanent_damaged", loaded_event.kind)
            self.assertIn(
                target.ref,
                self.legal_refs(loaded.engine, "A", group),
            )
            replay = replay_record(record_dir, self.db, verify=True)
        self.assertTrue(replay["ok"], replay)
        self.assertEqual(len(engine.seats), replay["commands"])
        self.assertEqual(expected_hash, replay["final_state_hash"])

        public = engine._public_target_schema("A", schema, source_ref=None)
        serialized = json.dumps(public, sort_keys=True)
        self.assertIn(target.ref, serialized)
        self.assertNotIn(target.object_id, serialized)
        self.assertNotIn(target.logical_object_id, serialized)

        fresh = self.session(162_104).engine
        _source, fresh_target = self.creatures(fresh, owner="B", count=2)
        self.assertNotIn(
            fresh_target.ref,
            self.legal_refs(fresh, "A", group),
        )
        with patch(
            "quorune.selection.targeting.target_damage_history_matches",
            return_value=True,
        ):
            self.assertIn(
                fresh_target.ref,
                self.legal_refs(fresh, "A", group),
            )


if __name__ == "__main__":
    unittest.main()
