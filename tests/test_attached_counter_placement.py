from __future__ import annotations

import copy
from dataclasses import replace
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

from common import ROOT, keep_all, load_assets, make_session
from quorune.attachment_references import (
    AttachmentReferenceError,
    AttachmentReferenceKind,
    AttachmentReferenceSpec,
    SourceAttachmentSnapshot,
    attachment_reference_specs,
    capture_source_attachment_snapshot,
    required_attachment_relation,
    resolve_source_attachment,
)
from quorune.attachments import (
    attach_objects,
    clear_object_attachment_relations,
)
from quorune.characteristic_evaluation import type_parts
from quorune.carddb import CardDatabase
from quorune.compiler.counter_placement_templates import (
    CounterPlacementSubject,
    FixedCounterPlacementTemplate,
    fixed_counter_placement_effect_template,
)
from quorune.deck import DeckLoader
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
    capability_dependencies_for_node,
    load_default_capability_registry,
)
from quorune.rules import node_capability_shapes
from quorune.semantic_runtime.values import resolve_semantic_value
from scripts.build_test_database import build_fixture_database


REGISTRY_PATH = (
    Path(__file__).resolve().parents[1]
    / "quorune"
    / "rules"
    / "capability-registry.json"
)


def focused_runtime_assets(directory: str):
    database = Path(directory) / "attached-counter-placement.sqlite3"
    build_fixture_database(
        [
            ROOT / "tests" / "fixtures" / "scryfall-exact-lists.json",
            ROOT / "tests" / "fixtures" / "counter-replacement-cards.json",
            ROOT / "tests" / "fixtures" / "attachment-counter-cards.json",
        ],
        database,
    )
    card_db = CardDatabase(database)
    loader = DeckLoader(card_db)
    mishra = loader.load(
        ROOT / "examples" / "mishra-eminent-one.txt",
        commander="Mishra, Eminent One",
        deck_name="Mishra",
    )
    zimone = loader.load(
        ROOT / "examples" / "zimone-and-dina.txt",
        commander="Zimone and Dina",
        deck_name="Zimone",
    )
    return card_db, mishra, zimone


def permanent(
    object_id: str,
    ref: str,
    *,
    owner: str = "A",
) -> CardInstance:
    return CardInstance(
        object_id=object_id,
        ref=ref,
        oracle_id=f"oracle:{object_id}",
        printed_name=ref,
        owner=owner,
        controller=owner,
        zone="battlefield",
        known_to=["A", "B", "C", "D"],
        revealed_to=["A", "B", "C", "D"],
    )


class AttachmentReferenceModelTests(unittest.TestCase):
    def setUp(self):
        self.source = permanent("aura", "Aura")
        self.first = permanent("first", "First")
        self.second = permanent("second", "Second")
        self.cards = {
            card.object_id: card
            for card in (self.source, self.first, self.second)
        }
        attach_objects(
            self.cards,
            self.source,
            self.first,
            source_timestamp=1,
        )
        self.spec = AttachmentReferenceSpec(
            AttachmentReferenceKind.ENCHANTED,
            "creature",
        )

    def test_live_relation_and_source_departure_use_current_or_last_known_identity(
        self,
    ):
        snapshot = capture_source_attachment_snapshot(
            self.cards,
            self.source,
            AttachmentReferenceKind.ENCHANTED,
        )
        attach_objects(
            self.cards,
            self.source,
            self.second,
            source_timestamp=2,
        )
        live = resolve_source_attachment(
            self.cards,
            snapshot.to_dict(),
            self.spec,
            source_object_id=self.source.object_id,
            source_logical_object_id=snapshot.source.logical_object_id,
        )
        self.assertIs(self.second, live)

        attach_objects(
            self.cards,
            self.source,
            self.first,
            source_timestamp=3,
        )
        departed_snapshot = capture_source_attachment_snapshot(
            self.cards,
            self.source,
            AttachmentReferenceKind.ENCHANTED,
        )
        clear_object_attachment_relations(self.cards, self.source)
        self.source.zone_change_counter += 1
        self.source.zone = "graveyard"
        last_known = resolve_source_attachment(
            self.cards,
            departed_snapshot.to_dict(),
            self.spec,
            source_object_id=self.source.object_id,
            source_logical_object_id=(
                departed_snapshot.source.logical_object_id
            ),
        )
        self.assertIs(self.first, last_known)

        self.first.zone_change_counter += 1
        self.first.zone = "graveyard"
        self.assertIsNone(
            resolve_source_attachment(
                self.cards,
                departed_snapshot.to_dict(),
                self.spec,
                source_object_id=self.source.object_id,
                source_logical_object_id=(
                    departed_snapshot.source.logical_object_id
                ),
            )
        )

    def test_attachment_reference_model_rejects_malformed_and_stale_identity(
        self,
    ):
        snapshot = capture_source_attachment_snapshot(
            self.cards,
            self.source,
            AttachmentReferenceKind.ENCHANTED,
        )
        self.assertEqual(
            snapshot,
            SourceAttachmentSnapshot.from_dict(snapshot.to_dict()),
        )
        self.assertEqual(
            snapshot.to_dict(),
            SourceAttachmentSnapshot.from_dict(
                dict(reversed(tuple(snapshot.to_dict().items())))
            ).to_dict(),
        )
        malformed = (
            {**snapshot.to_dict(), "unknown": True},
            {**snapshot.to_dict(), "schema_version": True},
            {**snapshot.to_dict(), "source": {"object_id": "aura"}},
        )
        for value in malformed:
            with self.subTest(value=value):
                with self.assertRaises(AttachmentReferenceError):
                    SourceAttachmentSnapshot.from_dict(value)
        with self.assertRaisesRegex(AttachmentReferenceError, "disagree"):
            resolve_source_attachment(
                self.cards,
                snapshot.to_dict(),
                AttachmentReferenceSpec(
                    AttachmentReferenceKind.EQUIPPED,
                    "creature",
                ),
                source_object_id=self.source.object_id,
                source_logical_object_id=snapshot.source.logical_object_id,
            )
        with self.assertRaisesRegex(AttachmentReferenceError, "stale"):
            resolve_source_attachment(
                self.cards,
                snapshot.to_dict(),
                self.spec,
                source_object_id=self.source.object_id,
                source_logical_object_id="aura@999",
            )

    def test_attachment_identity_mutant_is_killed(self):
        snapshot = capture_source_attachment_snapshot(
            self.cards,
            self.source,
            AttachmentReferenceKind.ENCHANTED,
        )

        def exact_live_relation() -> None:
            self.assertIs(
                self.first,
                resolve_source_attachment(
                    self.cards,
                    snapshot.to_dict(),
                    self.spec,
                    source_object_id=self.source.object_id,
                    source_logical_object_id=(
                        snapshot.source.logical_object_id
                    ),
                ),
            )

        exact_live_relation()
        with patch(
            "quorune.attachment_references.attached_object_identity",
            return_value=None,
        ):
            with self.assertRaises(AssertionError):
                exact_live_relation()

    def test_semantic_value_checks_required_current_card_type(self):
        snapshot = capture_source_attachment_snapshot(
            self.cards,
            self.source,
            AttachmentReferenceKind.ENCHANTED,
        )

        class Host:
            state = SimpleNamespace(cards=self.cards)

            @staticmethod
            def _target_snapshot(target_ref: str):
                return {
                    "ref": target_ref,
                    "type_line": "Creature — Human",
                }

            @staticmethod
            def _type_parts(type_line: str):
                return type_parts(type_line)

        item = StackItem(
            stack_id="stack:attachment",
            ref="S-attachment",
            kind="activated_ability",
            controller="A",
            label="Attachment fixture",
            source_object_id=self.source.object_id,
            context={
                "source_logical_object_id": self.source.logical_object_id,
                "source_attachment_snapshot": snapshot.to_dict(),
            },
        )
        self.assertEqual(
            self.first.ref,
            resolve_semantic_value(Host(), self.spec.to_dict(), item),
        )

        class LandHost(Host):
            @staticmethod
            def _target_snapshot(target_ref: str):
                return {"ref": target_ref, "type_line": "Land"}

        self.assertIsNone(
            resolve_semantic_value(LandHost(), self.spec.to_dict(), item)
        )

    def test_nested_attachment_reference_discovery_is_closed(self):
        effect = {
            "op": "place_counters",
            "card": self.spec.to_dict(),
            "counter": "+1/+1",
            "amount": 1,
            "source": "$source",
        }
        self.assertEqual(
            (self.spec,),
            attachment_reference_specs((effect,)),
        )
        self.assertIs(
            AttachmentReferenceKind.ENCHANTED,
            required_attachment_relation((effect,)),
        )
        mixed = (
            effect,
            {
                **effect,
                "card": AttachmentReferenceSpec(
                    AttachmentReferenceKind.EQUIPPED,
                    "creature",
                ).to_dict(),
            },
        )
        with self.assertRaisesRegex(AttachmentReferenceError, "mix"):
            required_attachment_relation(mixed)


class AttachedCounterCompilerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db, _mishra, _zimone = load_assets()
        cls.base = cls.db.lookup("Sol Ring")
        cls.capabilities = load_default_capability_registry()

    @classmethod
    def tearDownClass(cls):
        cls.db.close()

    def compile(self, text: str, *, type_line: str):
        return compile_oracle_card(
            replace(
                self.base,
                name="Fixture",
                oracle_text=text,
                type_line=type_line,
                keywords=(),
                faces=(),
            ),
            capability_registry=self.capabilities,
            capability_profile="commander_review",
        )

    def test_compiler_lowers_closed_enchanted_equipped_and_fortified_subjects(
        self,
    ):
        cases = (
            (
                "Enchantment — Aura",
                "{1}: Put a +1/+1 counter on enchanted creature.",
                AttachmentReferenceKind.ENCHANTED,
                "creature",
            ),
            (
                "Artifact — Equipment",
                "{1}: Put a charge counter on equipped creature.",
                AttachmentReferenceKind.EQUIPPED,
                "creature",
            ),
            (
                "Artifact — Fortification",
                "{1}: Put two brick counters on fortified land.",
                AttachmentReferenceKind.FORTIFIED,
                "land",
            ),
            (
                "Enchantment — Aura",
                "When this Aura enters, put a shield counter on enchanted permanent.",
                AttachmentReferenceKind.ENCHANTED,
                "permanent",
            ),
        )
        for type_line, text, relation, required_type in cases:
            with self.subTest(type_line=type_line, text=text):
                ir = self.compile(text, type_line=type_line)
                node = next(
                    node
                    for node in ir.faces[0].nodes
                    if node.template_id
                    and "counter-attached" in node.template_id
                )
                self.assertTrue(node.exact)
                self.assertEqual(text, text[node.span.start : node.span.end])
                self.assertEqual(
                    AttachmentReferenceSpec(
                        relation,
                        required_type,
                    ).to_dict(),
                    node.effects[0]["card"],
                )
                self.assertIn(
                    "counter.producer.fixed_attached_effect",
                    node.capability_dependencies,
                )

    def test_unsupported_attachment_subjects_remain_material_residuals(self):
        cases = (
            (
                "Sorcery",
                "Put a +1/+1 counter on enchanted creature.",
            ),
            (
                "Enchantment — Aura",
                "Put a +1/+1 counter on equipped creature.",
            ),
            (
                "Enchantment — Aura Equipment",
                "Put a +1/+1 counter on enchanted creature.",
            ),
            (
                "Enchantment — Aura",
                "Put a +1/+1 counter on enchanted modified creature.",
            ),
            (
                "Enchantment — Aura",
                "Put a +1/+1 counter on enchanted player.",
            ),
            (
                "Artifact — Equipment",
                "Put a +1/+1 counter on unequipped creature.",
            ),
        )
        for type_line, text in cases:
            with self.subTest(type_line=type_line, text=text):
                ir = self.compile(text, type_line=type_line)
                self.assertNotEqual("exact", ir.status)
                self.assertTrue(ir.material_residuals)

    def test_attached_template_and_capability_shapes_fail_closed(self):
        template = fixed_counter_placement_effect_template(
            "Put a +1/+1 counter on enchanted creature.",
            card_name="Fixture",
            source_attachment_relation=AttachmentReferenceKind.ENCHANTED,
        )
        self.assertIsNotNone(template)
        assert template is not None
        self.assertIs(CounterPlacementSubject.ATTACHED, template.subject)
        self.assertEqual(
            ("counter.producer.fixed_attached_effect", "counter.producer.fixed_effect"),
            capability_dependencies_for_node(
                effects=template.effects,
                target_schema=template.target_schema,
                mechanic_ids=template.mechanics,
            ),
        )
        malformed = dict(template.effects[0])
        malformed["card"] = {
            **dict(malformed["card"]),
            "unknown": True,
        }
        self.assertFalse(
            capability_dependencies_for_node(
                effects=(malformed,),
                target_schema=None,
                mechanic_ids=template.mechanics,
            )
        )
        with self.assertRaisesRegex(ValueError, "relation"):
            FixedCounterPlacementTemplate(
                count=1,
                counter_name="+1/+1",
                subject=CounterPlacementSubject.ATTACHED,
                permanent_type="creature",
            )

    def test_attachment_compiler_and_dependency_mutants_are_killed(self):
        def exact() -> None:
            self.assertEqual(
                "exact",
                self.compile(
                    "{1}: Put a +1/+1 counter on enchanted creature.",
                    type_line="Enchantment — Aura",
                ).status,
            )

        exact()
        with patch(
            "quorune.compiler.counter_resolution_effect_templates."
            "fixed_counter_placement_effect_template",
            return_value=None,
        ):
            with self.assertRaises(AssertionError):
                exact()

        registry_value = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
        dependency = next(
            row
            for row in registry_value["capabilities"]
            if row["id"] == "attachment.reference.current_or_lki"
        )
        dependency["status"] = "blocked"
        dependency["blockers"] = ["test mutation"]
        ir = compile_oracle_card(
            replace(
                self.base,
                name="Fixture",
                oracle_text=(
                    "{1}: Put a +1/+1 counter on enchanted creature."
                ),
                type_line="Enchantment — Aura",
                keywords=(),
                faces=(),
            ),
            capability_registry=CapabilityRegistry(registry_value),
            capability_profile="commander_review",
        )
        self.assertNotEqual("exact", ir.status)
        self.assertTrue(ir.material_residuals)

        template = fixed_counter_placement_effect_template(
            "Put a +1/+1 counter on enchanted creature.",
            card_name="Fixture",
            source_attachment_relation=AttachmentReferenceKind.ENCHANTED,
        )
        assert template is not None

        def attached_shape() -> tuple[str, ...]:
            return capability_dependencies_for_node(
                effects=template.effects,
                target_schema=None,
                mechanic_ids=template.mechanics,
            )

        self.assertEqual(
            ("counter.producer.fixed_attached_effect", "counter.producer.fixed_effect"),
            attached_shape(),
        )
        with patch.object(
            AttachmentReferenceSpec,
            "from_dict",
            side_effect=AttachmentReferenceError("mutation"),
        ):
            self.assertFalse(attached_shape())


class AttachedCounterRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.db, cls.mishra, cls.zimone = focused_runtime_assets(
            cls.temporary.name
        )

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temporary.cleanup()

    def session_with_card(
        self,
        card_name: str,
        *,
        players: int,
        seed: int,
    ):
        deck = copy.deepcopy(self.mishra)
        replaceable = next(
            entry for entry in deck.entries if entry.board == "mainboard"
        )
        replaceable.name = card_name
        session = make_session(
            self.db,
            deck,
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
        session.commands.clear()
        session.decisions.clear()
        return session

    def session_with_daily_regimen(self, *, players: int, seed: int):
        return self.session_with_card(
            "Daily Regimen",
            players=players,
            seed=seed,
        )

    @staticmethod
    def card(engine, *, seat: str, name: str):
        return next(
            card
            for card in engine.state.cards.values()
            if card.owner == seat and card.printed_name == name
        )

    def prepare_activation(self, session):
        engine = session.engine
        source = self.card(engine, seat="A", name="Daily Regimen")
        target = next(
            card
            for card in engine.state.cards.values()
            if card.owner == "A"
            and card.object_id != source.object_id
            and (
                record := engine.card_record(card)
            ) is not None
            and "creature" in type_parts(record.type_line)[0]
        )
        engine.move_card(
            target.object_id,
            "battlefield",
            controller="A",
            log=False,
        )
        engine.move_card(
            source.object_id,
            "battlefield",
            controller="A",
            aura_target_ref=target.ref,
            log=False,
        )
        self.assertEqual(target.object_id, source.attached_to)
        engine.state.players["A"].mana_pool.update({"C": 1, "W": 1})
        engine.state.active_player = "A"
        engine.state.started = True
        engine.state.phase = "precombat_main"
        engine.state.step = "main"
        engine._grant_priority("A")
        engine.pump()
        return source, target

    @staticmethod
    def action(session, source):
        packet = session.packet("pilot:A", full=True)
        return next(
            action
            for action in packet["decision"]["ctx"]["legal"]["actions"]
            if action["id"].startswith(f"activate:{source.ref}:")
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

    def assert_replays(self, session, *, expected_commands: int | None = None):
        expected_hash = authoritative_state_hash(session.state)
        with tempfile.TemporaryDirectory() as temporary:
            record_dir = Path(temporary) / "attached-counter-record"
            session.save(record_dir)
            replay = replay_record(record_dir, self.db, verify=True)
        self.assertTrue(replay["ok"], replay)
        self.assertEqual(expected_hash, replay["final_state_hash"])
        if expected_commands is not None:
            self.assertEqual(expected_commands, replay["commands"])

    def test_compiled_activation_places_counter_on_enchanted_creature(self):
        session = self.session_with_daily_regimen(players=2, seed=12270801)
        engine = session.engine
        source, target = self.prepare_activation(session)
        program = next(
            program
            for program in engine.semantics.programs_for_oracle(
                source.oracle_id
            )
            if program.event == "activate"
        )
        self.assertEqual("trusted", program.trust_level)
        action = self.action(session, source)
        self.assertNotIn("target_schema", action)
        session.initial_checkpoint = checkpoint_envelope(engine.state)
        session.commands.clear()
        session.decisions.clear()

        result = session.act("pilot:A", {"action_id": action["id"]})
        self.assertTrue(result.ok, result.summary)
        stack_item = engine.state.stack[-1]
        snapshot = SourceAttachmentSnapshot.from_dict(
            stack_item.context["source_attachment_snapshot"]
        )
        self.assertEqual(source.logical_object_id, snapshot.source.logical_object_id)
        self.assertEqual(target.logical_object_id, snapshot.attached_object.logical_object_id)
        self.pass_until(session, lambda: not engine.state.stack)

        self.assertEqual(1, target.counters["+1/+1"])
        self.assert_replays(session)

    def test_source_departure_uses_pinned_attachment_during_resolution(self):
        session = self.session_with_daily_regimen(players=2, seed=12270804)
        engine = session.engine
        source, target = self.prepare_activation(session)
        action = self.action(session, source)

        result = session.act("pilot:A", {"action_id": action["id"]})
        self.assertTrue(result.ok, result.summary)
        pinned_source_identity = engine.state.stack[-1].context[
            "source_logical_object_id"
        ]
        engine.move_card(source.object_id, "graveyard", log=False)
        self.assertNotEqual(
            pinned_source_identity,
            source.logical_object_id,
        )
        engine.permissions.invalidate_current()
        engine.state.pending_decision = None
        engine._grant_priority("A")
        engine.pump()
        session.initial_checkpoint = checkpoint_envelope(engine.state)
        session.commands.clear()
        session.decisions.clear()

        self.pass_until(session, lambda: not engine.state.stack)

        self.assertEqual(1, target.counters["+1/+1"])
        self.assert_replays(session)

    def test_trigger_pins_attachment_before_enqueue_and_replays(self):
        session = self.session_with_card(
            "Hydra's Growth",
            players=2,
            seed=12270803,
        )
        engine = session.engine
        source = self.card(engine, seat="A", name="Hydra's Growth")
        target = next(
            card
            for card in engine.state.cards.values()
            if card.owner == "A"
            and card.object_id != source.object_id
            and (
                record := engine.card_record(card)
            ) is not None
            and "creature" in type_parts(record.type_line)[0]
        )
        engine.move_card(
            target.object_id,
            "battlefield",
            controller="A",
            log=False,
        )
        engine.move_card(
            source.object_id,
            "battlefield",
            controller="A",
            aura_target_ref=target.ref,
            semantic_events=True,
            log=False,
        )
        engine._stabilize()
        item = next(
            item
            for item in engine.state.stack
            if item.source_object_id == source.object_id
            and item.semantic_key
            and "front:n2" in item.semantic_key
        )
        snapshot = SourceAttachmentSnapshot.from_dict(
            item.context["source_attachment_snapshot"]
        )
        self.assertEqual(
            target.logical_object_id,
            snapshot.attached_object.logical_object_id,
        )
        engine.state.active_player = "A"
        engine.state.started = True
        engine.state.phase = "precombat_main"
        engine.state.step = "main"
        engine._grant_priority("A")
        engine._issue_priority("A")
        session.initial_checkpoint = checkpoint_envelope(engine.state)
        session.commands.clear()
        session.decisions.clear()

        self.pass_until(session, lambda: not engine.state.stack)

        self.assertEqual(1, target.counters["+1/+1"])
        self.assert_replays(session)

    def add_replacement(self, engine, *, name: str, ref: str):
        record = self.db.lookup(name)
        card = CardInstance(
            object_id=f"fixture:{ref}",
            ref=ref,
            oracle_id=record.oracle_id,
            printed_name=record.name,
            owner="A",
            controller="A",
            zone="battlefield",
            known_to=list(engine.seats),
            revealed_to=list(engine.seats),
        )
        engine.state.cards[card.object_id] = card
        engine.state.players["A"].zones["battlefield"].append(card.object_id)
        return card

    def test_attachment_counter_replacement_is_seat_scoped_and_replays(self):
        session = self.session_with_daily_regimen(players=4, seed=12270802)
        engine = session.engine
        source, target = self.prepare_activation(session)
        self.add_replacement(
            engine,
            name="Doubling Season",
            ref="attached-doubling",
        )
        self.add_replacement(
            engine,
            name="Doc Samson, Super Psychiatrist",
            ref="attached-doc",
        )
        action = self.action(session, source)
        session.initial_checkpoint = checkpoint_envelope(engine.state)
        session.commands.clear()
        session.decisions.clear()

        result = session.act("pilot:A", {"action_id": action["id"]})
        self.assertTrue(result.ok, result.summary)
        self.pass_until(
            session,
            lambda: (
                engine.state.pending_decision is not None
                and engine.state.pending_decision.kind == "replacement.order"
            ),
        )
        projector = StateProjector(self.db, engine.state)
        projected = projector._decision("pilot:A")
        self.assertIsNotNone(projected)
        for seat in ("B", "C", "D"):
            self.assertIsNone(projector._decision(f"pilot:{seat}"))
        self.assertNotIn(target.object_id, json.dumps(projected, sort_keys=True))
        selected = projected["ctx"]["options"][0]["id"]
        result = session.act(
            "pilot:A",
            {
                "action_id": "choose",
                "choices": {"replacement": selected},
            },
        )
        self.assertTrue(result.ok, result.summary)
        self.assertGreater(target.counters["+1/+1"], 1)
        self.assert_replays(session)


if __name__ == "__main__":
    unittest.main()
