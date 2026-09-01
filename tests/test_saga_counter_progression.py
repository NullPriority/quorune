from __future__ import annotations

import copy
from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from common import ROOT, keep_all, make_session
from scripts.build_test_database import build_fixture_database
from quorune.card_programs import CardProgram
from quorune.card_programs.adapters import compile_card_program
from quorune.carddb import CardDatabase, CardRecord
from quorune.counter_placement import (
    prepare_counter_placements as canonical_prepare_counter_placements,
)
from quorune.continuous_effect_model import (
    ContinuousEffect,
    ContinuousEffectDuration,
    ContinuousEffectOrigin,
    ContinuousObjectIdentity,
    ContinuousOperation,
    Layer,
)
from quorune.continuous_effect_state import commit_continuous_effect
from quorune.deck import DeckLoader
from quorune.engine import TURN_STEPS
from quorune.errors import GameRuleError
from quorune.entry_counter_model import (
    EntryCounterError,
    intrinsic_entry_counters,
)
from quorune.model import CardInstance, GameState, StackItem
from quorune.object_predicate import ObjectQuerySpec
from quorune.oracle_ir import compile_oracle_card, generated_programs
from quorune.read_ahead import saga_chapter_line, saga_chapter_numbers
from quorune.compiler.program_generation import register_generated_programs
from quorune.projection import StateProjector
from quorune.record import (
    authoritative_state_hash,
    checkpoint_envelope,
    replay_record,
)
from quorune.replacement import (
    AffectedObject,
    ReplaceableEvent,
    ReplacementClass,
    ReplacementEffect,
    SetField,
    apply_replacement,
    replacement_choice,
)
from quorune.replacement_effects import (
    ReplacementChoiceRequired,
    ReplacementContinuation,
    ReplacementEffectError,
)
from quorune.rules.capabilities import (
    CapabilityRegistry,
    load_default_capability_registry,
)
from quorune.session import CommanderSession
from quorune.saga_progression import (
    SagaProgressionError,
    advance_active_player_sagas,
    capture_saga_lore_turn_action,
    commit_saga_lore_turn_action,
    dispatch_saga_chapters,
)
from quorune.state_based_actions import evaluate_state_based_actions
from quorune.semantic_runtime import prepare_zone_change_replacement_batch
from quorune.semantic_runtime.entry_choices import (
    ReadAheadEntryChoiceHandler,
)
from quorune.semantics import SemanticProgram
from quorune.trigger_processing import enqueue_trigger_batch


class SagaCounterProgressionTests(unittest.TestCase):
    """CR 714.3a/714.3c lore counters and the CR 614.16 boundary."""

    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        database = Path(cls.temporary.name) / "saga-counters.sqlite3"
        build_fixture_database(
            [
                ROOT / "tests" / "fixtures" / "scryfall-exact-lists.json",
                ROOT
                / "tests"
                / "fixtures"
                / "counter-replacement-cards.json",
                ROOT
                / "tests"
                / "fixtures"
                / "read-ahead-saga-cards.json",
                ROOT
                / "tests"
                / "fixtures"
                / "ordinary-saga-chapter-programs.json",
            ],
            database,
        )
        cls.db = CardDatabase(database)
        cls.capabilities = load_default_capability_registry()
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
        session.commands.clear()
        session.decisions.clear()
        return session

    @staticmethod
    def card(engine, owner: str, name: str) -> CardInstance:
        return next(
            card
            for card in engine.state.cards.values()
            if card.owner == owner
            and card.is_card_object
            and card.printed_name == name
        )

    def add_permanent(
        self,
        engine,
        *,
        seat: str,
        name: str,
        ref: str,
    ) -> CardInstance:
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
            revealed_to=list(engine.seats),
        )
        engine.state.cards[card.object_id] = card
        engine.state.players[seat].zones["battlefield"].append(card.object_id)
        return card

    def add_saga(
        self,
        engine,
        *,
        seat: str,
        ref: str,
        zone: str = "exile",
        oracle_id: str | None = None,
    ) -> CardInstance:
        base = self.db.lookup("Island")
        card = CardInstance(
            object_id=f"fixture:{ref}",
            ref=ref,
            oracle_id=oracle_id or base.oracle_id,
            printed_name=ref,
            owner=seat,
            controller=seat,
            zone=zone,
            annotations={
                "copy_overrides": {
                    "name": ref,
                    "type_line": "Enchantment — Saga",
                    "oracle_text": "",
                }
            },
            known_to=list(engine.seats),
            revealed_to=list(engine.seats),
        )
        engine.state.cards[card.object_id] = card
        if zone in engine.state.players[seat].zones:
            engine.state.players[seat].zones[zone].append(card.object_id)
        return card

    def add_read_ahead_card(
        self,
        engine,
        *,
        seat: str,
        ref: str,
        zone: str = "stack",
        controller: str | None = None,
        object_kind: str = "card",
    ) -> CardInstance:
        record = self.db.lookup("Love Song of Night and Day")
        card = CardInstance(
            object_id=f"fixture:{ref}",
            ref=ref,
            oracle_id=record.oracle_id,
            printed_name=record.name,
            owner=seat,
            controller=controller or seat,
            zone=zone,
            object_kind=object_kind,
            known_to=list(engine.seats),
            revealed_to=list(engine.seats),
        )
        engine.state.cards[card.object_id] = card
        if zone in engine.state.players[seat].zones:
            engine.state.players[seat].zones[zone].append(card.object_id)
        return card

    def register_read_ahead(
        self,
        engine,
        card: CardInstance,
        *,
        include_chapters: bool = True,
    ) -> SemanticProgram:
        record = self.db.by_oracle_id(card.oracle_id)
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
            == "read-ahead-saga-entry-choice-v1"
        ]
        self.assertEqual(1, len(programs))
        handler_program = programs[0]
        engine.semantics.put(handler_program)
        if include_chapters:
            for chapter in (1, 2, 3):
                engine.semantics.put(
                    SemanticProgram(
                        key=f"test:read-ahead:chapter:{chapter}",
                        label=(
                            "Love Song of Night and Day "
                            f"chapter {chapter}"
                        ),
                        oracle_id=record.oracle_id,
                        ability_id=f"trigger:front:chapter-{chapter}",
                        active_zone="battlefield",
                        event=f"saga.chapter.{chapter}",
                        trust_level="trusted",
                        provenance={
                            **handler_program.provenance,
                            "authored_by": "read-ahead-test-boundary",
                            "review_status": "reviewed",
                            "face_id": "front",
                        },
                        tests=[
                            "test_read_ahead_choice_is_private_and_replays_exactly"
                        ],
                        coverage=["saga_chapter", "test_boundary"],
                    )
                )
        return handler_program

    def register_ordinary_saga_chapters(self, engine) -> CardRecord:
        record = self.db.lookup("Ordinary Saga Chapter Fixture", fuzzy=False)
        register_generated_programs(
            self.db,
            engine.semantics,
            (record,),
            trust_level="provisional",
            capability_registry=self.capabilities,
            capability_profile=engine.state.config.review_profile,
            promote_exact_runtime_handlers=True,
            promote_exact_trigger_programs=True,
            promote_exact_effect_programs=True,
            promote_exact_capability_declarations=True,
        )
        return record

    @staticmethod
    def begin_read_ahead_entry(session, card: CardInstance) -> None:
        engine = session.engine
        item = StackItem(
            stack_id=f"stack:{card.ref}",
            ref=f"S-{card.ref}",
            kind="spell",
            controller=card.controller,
            label=card.printed_name,
            card_object_id=card.object_id,
            default_destination="battlefield",
            visibility=list(engine.seats),
        )
        engine.state.stack.append(item)
        engine._continue_resolution(
            stack_ref=item.ref,
            effects=[],
            destination=None,
            note="Read Ahead entry fixture",
        )

    @staticmethod
    def replacement_options(session, seat: str):
        decision = StateProjector(session.engine.card_db, session.state)._decision(
            f"pilot:{seat}"
        )
        return decision, [
            option["id"] for option in decision["ctx"]["options"]
        ]

    def choose_replacement(self, session, seat: str, selection: str) -> None:
        result = session.act(
            f"pilot:{seat}",
            {"action_id": "choose", "choices": {"replacement": selection}},
        )
        self.assertTrue(result.ok, result.summary)

    def stage_competing_sources(self, engine, *, seat: str) -> None:
        prefix = seat.casefold()
        self.add_permanent(
            engine,
            seat=seat,
            name="Doubling Season",
            ref=f"{prefix}-doubling",
        )
        self.add_permanent(
            engine,
            seat=seat,
            name="Doc Samson, Super Psychiatrist",
            ref=f"{prefix}-doc",
        )

    @staticmethod
    def chapter_item(
        engine,
        saga: CardInstance,
        *,
        chapter: int = 3,
        logical_object_id: str | None = None,
    ) -> StackItem:
        program = next(
            value
            for value in engine.semantics.programs_for_oracle(
                saga.oracle_id
            )
            if value.event == f"saga.chapter.{chapter}"
            and engine.semantic_program_is_current_trusted(value)
        )
        return StackItem(
            stack_id=f"chapter-{chapter}-{saga.object_id}",
            ref=f"S-chapter-{chapter}-{saga.ref}",
            kind="triggered_ability",
            controller=saga.controller,
            label=f"{saga.printed_name} chapter {chapter}",
            source_object_id=saga.object_id,
            semantic_key=program.key,
            visibility=list(engine.seats),
            context={
                "source_logical_object_id": (
                    logical_object_id
                    if logical_object_id is not None
                    else saga.logical_object_id
                )
            },
        )

    @staticmethod
    def saga_record(*, read_ahead: bool = False) -> CardRecord:
        return CardRecord(
            oracle_id=(
                "00000000-0000-4000-8000-714300000001"
                if not read_ahead
                else "00000000-0000-4000-8000-714300000002"
            ),
            name="Saga Card-Form Fixture",
            mana_cost="{2}",
            mana_value=2.0,
            type_line="Enchantment — Saga",
            oracle_text=(
                "Lifelink\n"
                "(As this Saga enters and after your draw step, add a lore "
                "counter. Sacrifice after III.)"
            ),
            power=None,
            toughness=None,
            loyalty=None,
            defense=None,
            colors=(),
            color_identity=(),
            keywords=(
                ("Lifelink", "Read Ahead")
                if read_ahead
                else ("Lifelink",)
            ),
            produced_mana=(),
            layout="normal",
            released_at="2026-01-01",
            legalities={"commander": "legal"},
            faces=(),
            raw={},
        )

    def test_ordinary_saga_card_form_is_source_spanned_and_closed(self):
        record = self.saga_record()
        program = compile_card_program(
            self.db,
            record,
            capability_registry=self.capabilities,
            capability_profile="commander_review",
            trust_level="trusted",
        )

        self.assertIn(
            "counter.producer.saga_lore",
            program.capability_dependencies,
        )
        self.assertTrue(program.trust_closure["trusted"])
        self.assertEqual((), program.residuals)
        ability = next(
            value
            for value in program.to_dict()["abilities"]
            if value["runtime"]["provenance"].get("source_kind")
            == "type_line"
        )
        self.assertEqual(
            {"line": 1, "start": 0, "end": len(record.type_line)},
            ability["source_span"],
        )
        self.assertEqual(
            "lore",
            ability["runtime"]["provenance"]
            ["card_form_descriptor"]["counter_name"],
        )
        self.assertEqual(
            program.to_dict(),
            CardProgram.from_dict(program.to_dict()).to_dict(),
        )

    def test_ordinary_saga_chapter_programs_compile_exact(self):
        record = self.db.lookup("Ordinary Saga Chapter Fixture", fuzzy=False)
        ir = compile_card_program(
            self.db,
            record,
            capability_registry=self.capabilities,
            capability_profile="commander_review",
            trust_level="trusted",
        )

        chapter_abilities = [
            ability
            for ability in ir.abilities
            if str(ability.event or "").startswith("saga.chapter.")
        ]
        self.assertEqual(
            ["saga.chapter.1", "saga.chapter.2", "saga.chapter.3"],
            [ability.event for ability in chapter_abilities],
        )
        self.assertTrue(
            all(ability.trust_level == "trusted" for ability in chapter_abilities)
        )
        self.assertTrue(
            all(
                "counter.producer.saga_lore"
                in ability.capability_dependencies
                and "trigger.placement.apnap"
                in ability.capability_dependencies
                for ability in chapter_abilities
            )
        )
        self.assertEqual(
            chapter_abilities[1].provenance["source_span"],
            chapter_abilities[2].provenance["source_span"],
        )
        self.assertEqual((), ir.residuals)
        self.assertEqual(ir.to_dict(), CardProgram.from_dict(ir.to_dict()).to_dict())

    def test_compiled_ordinary_saga_chapter_dispatches_and_resolves(self):
        session = self.session(7143031)
        engine = session.engine
        record = self.register_ordinary_saga_chapters(engine)
        saga = self.add_saga(
            engine,
            seat="A",
            ref="ordinary-saga-chapter",
            zone="battlefield",
            oracle_id=record.oracle_id,
        )
        saga.counters["lore"] = 0
        life_before = engine.state.players["A"].life

        advance_active_player_sagas(engine, "A")

        self.assertEqual(1, saga.counters["lore"])
        self.assertEqual(1, len(engine.state.pending_trigger_batches))
        occurrence = engine.state.pending_trigger_batches[0].items[0]
        program = engine.semantics.get(occurrence.source_ability_id)
        self.assertIsNotNone(program)
        self.assertEqual("saga.chapter.1", program.event)
        self.assertEqual(
            ["A", "B"],
            list(engine.state.pending_trigger_batches[0].apnap_order),
        )

        engine._grant_priority("A")
        item = engine.state.stack[-1]
        resolved = engine.semantics.get(item.semantic_key)
        self.assertIsNotNone(resolved)
        engine._begin_resolve_item(
            item,
            resolved.effects,
            resolved.destination,
            note="compiled ordinary Saga chapter",
        )

        self.assertEqual(life_before + 2, engine.state.players["A"].life)

    def test_ordinary_saga_chapter_grammar_fails_closed(self):
        record = self.db.lookup("Ordinary Saga Chapter Fixture", fuzzy=False)
        parsed = saga_chapter_line("II, III — Draw a card.")
        self.assertIsNotNone(parsed)
        self.assertEqual((2, 3), parsed.chapters)
        self.assertEqual("Draw a card.", parsed.body)
        for malformed in (
            "III, II — Draw a card.",
            "I, I — Draw a card.",
            "XI — Draw a card.",
            "I —",
        ):
            with self.subTest(malformed=malformed):
                self.assertIsNone(saga_chapter_line(malformed))
                self.assertEqual(
                    (),
                    saga_chapter_numbers(
                        ("I — Draw a card.", malformed)
                    ),
                )

        for changed in (
            replace(record, type_line="Enchantment"),
            replace(record, oracle_text="I — Choose a card name."),
        ):
            with self.subTest(type_line=changed.type_line):
                ir = compile_oracle_card(
                    changed,
                    capability_registry=self.capabilities,
                    capability_profile="commander_review",
                )
                self.assertNotEqual("exact", ir.status)
                self.assertFalse(
                    any(
                        node.exact
                        and str(node.event).startswith("saga.chapter.")
                        for face in ir.faces
                        for node in face.nodes
                    )
                )

    def test_compiled_saga_chapters_batch_in_apnap_order_and_round_trip(self):
        session = self.session(7143032, players=4)
        engine = session.engine
        record = self.register_ordinary_saga_chapters(engine)
        trigger_batch = []
        for seat in ("B", "A"):
            saga = self.add_saga(
                engine,
                seat=seat,
                ref=f"ordinary-saga-{seat.casefold()}",
                zone="battlefield",
                oracle_id=record.oracle_id,
            )
            saga.counters["lore"] = 1
            dispatch_saga_chapters(
                engine,
                saga,
                previous_lore=0,
                trigger_batch=trigger_batch,
            )
        enqueue_trigger_batch(engine, trigger_batch)

        batch = engine.state.pending_trigger_batches[0]
        self.assertEqual(
            ["A", "B"],
            [group.controller for group in batch.groups],
        )
        self.assertEqual(
            {"saga.chapter.1"},
            {item.normalized_event_id for item in batch.items},
        )
        checkpoint = engine.state.to_dict()
        self.assertEqual(checkpoint, GameState.from_dict(checkpoint).to_dict())

    def test_read_ahead_and_untrusted_chapters_fail_closed(self):
        record = self.db.lookup("Love Song of Night and Day")
        malformed = replace(
            record,
            oracle_text="\n".join(
                line
                for line in record.oracle_text.splitlines()
                if not line.startswith("II ")
            ),
        )
        program = compile_card_program(
            self.db,
            malformed,
            capability_registry=self.capabilities,
            capability_profile="commander_review",
            trust_level="provisional",
        )
        residual = next(
            value
            for value in program.residuals
            if value["text"].startswith("Read ahead")
        )
        self.assertTrue(residual["material"])
        self.assertIn(
            "mechanic:read-ahead-unrepresented-final-chapter",
            residual["blockers"],
        )
        self.assertIn("contiguous chapter symbols", residual["reason"])

        session = self.session(7143001)
        engine = session.engine
        unsupported = self.add_saga(
            engine,
            seat="A",
            ref="untrusted-saga",
            zone="battlefield",
        )
        before = authoritative_state_hash(engine.state)
        with self.assertRaisesRegex(
            SagaProgressionError, "trusted typed chapter programs"
        ):
            capture_saga_lore_turn_action(engine, "A")
        self.assertEqual(0, unsupported.counters.get("lore", 0))
        self.assertEqual(before, authoritative_state_hash(engine.state))

        with self.assertRaisesRegex(EntryCounterError, "Read Ahead"):
            intrinsic_entry_counters(
                {},
                card_types=("enchantment",),
                card_subtypes=("saga",),
                keywords=("Read Ahead",),
            )

    def test_read_ahead_compiles_one_exact_source_pinned_choice(self):
        record = self.db.lookup("Love Song of Night and Day")
        program = compile_card_program(
            self.db,
            record,
            capability_registry=self.capabilities,
            capability_profile="commander_review",
            trust_level="provisional",
        )
        ability = next(
            value
            for value in program.to_dict()["abilities"]
            if value["runtime"]["provenance"].get("template_id")
            == "read-ahead-saga-entry-choice-v1"
        )
        self.assertEqual(
            [
                "counter.producer.saga_lore",
                "state_based.saga_final_chapter",
            ],
            ability["capability_dependencies"],
        )
        self.assertEqual(
            [1, 2, 3],
            ability["runtime"]["handlers"][0]["chapter_numbers"],
        )
        self.assertEqual(
            {"line": 1, "start": 0, "end": len(record.oracle_text.splitlines()[0])},
            ability["source_span"],
        )
        self.assertFalse(
            any(
                value["kind"] == "card_form_rule"
                for value in program.residuals
            )
        )

        raw = json.loads(
            (ROOT / "quorune" / "rules" / "capability-registry.json")
            .read_text(encoding="utf-8")
        )
        dependency = next(
            value
            for value in raw["capabilities"]
            if value["id"] == "counter.placement.quantity_replacement"
        )
        dependency["status"] = "blocked"
        dependency["blockers"] = ["test mutation"]
        registry = CapabilityRegistry(raw)
        registry.mark_evidence_verified("0" * 64)
        blocked = compile_card_program(
            self.db,
            record,
            capability_registry=registry,
            capability_profile="commander_review",
            trust_level="provisional",
        )
        read_ahead_residual = next(
            value
            for value in blocked.residuals
            if value["text"].startswith("Read ahead")
        )
        self.assertTrue(
            any(
                "counter.placement.quantity_replacement" in blocker
                for blocker in read_ahead_residual["blockers"]
            )
        )

    def test_read_ahead_choice_is_private_and_replays_exactly(self):
        session = self.session(7143030, players=4)
        engine = session.engine
        card = self.add_read_ahead_card(
            engine,
            seat="A",
            controller="C",
            ref="read-ahead-private",
        )
        self.register_read_ahead(engine, card)
        self.begin_read_ahead_entry(session, card)

        self.assertIsNone(
            StateProjector(self.db, engine.state)._decision("pilot:A")
        )
        projected, options = self.replacement_options(session, "C")
        self.assertIsNotNone(projected)
        self.assertNotIn(card.object_id, json.dumps(projected, sort_keys=True))
        self.assertTrue(
            all(
                session.packet(f"pilot:{seat}", full=True)["decision"]
                is None
                for seat in ("A", "B", "D")
            )
        )
        session.initial_checkpoint = checkpoint_envelope(engine.state)
        session.commands.clear()
        session.decisions.clear()
        self.choose_replacement(
            session,
            "C",
            next(value for value in options if value.endswith(":chapter:3")),
        )

        self.assertEqual("battlefield", card.zone)
        self.assertEqual("C", card.controller)
        self.assertEqual(3, card.counters.get("lore"))
        labels = [
            item.label
            for batch in engine.state.pending_trigger_batches
            for item in batch.items
        ] + [item.label for item in engine.state.stack]
        self.assertEqual(
            ["Love Song of Night and Day chapter 3"],
            labels,
        )
        expected_hash = authoritative_state_hash(engine.state)
        with tempfile.TemporaryDirectory() as temporary:
            record_dir = Path(temporary) / "read-ahead-replay"
            session.save(record_dir)
            replay = replay_record(record_dir, self.db, verify=True)
        self.assertTrue(replay["ok"], replay)
        self.assertEqual(expected_hash, replay["final_state_hash"])

    def test_read_ahead_quantity_replacement_uses_exact_chapter_gate(self):
        session = self.session(7143031)
        engine = session.engine
        self.add_permanent(
            engine,
            seat="A",
            name="Doubling Season",
            ref="read-ahead-doubling",
        )
        card = self.add_read_ahead_card(
            engine,
            seat="A",
            ref="read-ahead-doubled",
        )
        self.register_read_ahead(engine, card)
        self.begin_read_ahead_entry(session, card)
        _projected, options = self.replacement_options(session, "A")
        self.choose_replacement(
            session,
            "A",
            next(value for value in options if value.endswith(":chapter:3")),
        )

        self.assertEqual("graveyard", card.zone)
        self.assertFalse(
            any(
                "chapter" in item.label.casefold()
                for item in engine.state.stack
            )
        )
        self.assertFalse(engine.state.pending_trigger_batches)
        self.assertTrue(
            any(
                event.code == "replacement.apply"
                and any(
                    counter.get("name") == "lore"
                    and counter.get("amount") == 6
                    for counter in event.details.get("counters", [])
                )
                for event in engine.state.events
            )
        )
        self.assertTrue(
            any(
                event.code == "state.saga_sacrificed"
                for event in engine.state.events
            )
        )

    def test_read_ahead_runtime_boundaries_fail_before_mutation(self):
        session = self.session(7143032)
        engine = session.engine
        card = self.add_read_ahead_card(
            engine,
            seat="A",
            ref="read-ahead-missing-chapters",
            zone="exile",
        )
        self.register_read_ahead(engine, card, include_chapters=False)
        before = authoritative_state_hash(engine.state)
        with self.assertRaisesRegex(
            GameRuleError, "matching trusted typed chapter programs"
        ):
            engine.move_card(
                card.object_id,
                "battlefield",
                controller="A",
                semantic_events=True,
            )
        self.assertEqual(before, authoritative_state_hash(engine.state))
        self.assertEqual("exile", card.zone)

        copied = self.add_read_ahead_card(
            engine,
            seat="A",
            ref="read-ahead-copy",
            zone="exile",
            object_kind="card_copy",
        )
        self.register_read_ahead(engine, copied)
        copied_before = authoritative_state_hash(engine.state)
        with self.assertRaisesRegex(
            GameRuleError, "Copied or granted Read Ahead"
        ):
            engine.move_card(
                copied.object_id,
                "battlefield",
                controller="A",
                semantic_events=True,
            )
        self.assertEqual(copied_before, authoritative_state_hash(engine.state))

        descriptor = {
            "handler_id": "replacement.entry.read-ahead.v1",
            "schema_version": 1,
            "event": "zone.change",
            "chapter_numbers": [1, 3],
            "counter_name": "lore",
            "rule_id": "714.3b",
        }
        with self.assertRaisesRegex(
            Exception, "contiguous positive chapter numbers"
        ):
            ReadAheadEntryChoiceHandler().validate(descriptor)
        with self.assertRaisesRegex(Exception, "unknown fields"):
            ReadAheadEntryChoiceHandler().validate(
                {**descriptor, "chapter_numbers": [1, 2, 3], "extra": True}
            )

    def test_entry_and_precombat_lore_use_distinct_counter_paths(self):
        session = self.session(7143002)
        engine = session.engine
        saga = self.card(engine, "A", "Urza's Saga")
        engine.move_card(
            saga.object_id,
            "battlefield",
            controller="A",
            log=False,
            semantic_events=True,
        )

        self.assertEqual(1, saga.counters["lore"])
        self.assertTrue(engine.state.pending_trigger_batches)
        self.assertIn(
            "chapter I",
            engine.state.pending_trigger_batches[-1].items[0].label,
        )
        engine.state.pending_trigger_batches.clear()
        engine.state.stack.clear()

        advance_active_player_sagas(engine, "A")

        self.assertEqual(2, saga.counters["lore"])
        self.assertTrue(engine.state.pending_trigger_batches)
        self.assertIn(
            "chapter II",
            engine.state.pending_trigger_batches[-1].items[0].label,
        )

    def test_saga_entry_counter_uses_quantity_replacement_but_turn_action_does_not(
        self,
    ):
        session = self.session(7143003)
        engine = session.engine
        self.add_permanent(
            engine,
            seat="A",
            name="Doubling Season",
            ref="a-doubling",
        )
        saga = self.card(engine, "A", "Urza's Saga")
        engine.move_card(
            saga.object_id,
            "battlefield",
            controller="A",
            log=False,
            semantic_events=False,
        )

        self.assertEqual(2, saga.counters["lore"])
        advance_active_player_sagas(engine, "A")
        self.assertEqual(3, saga.counters["lore"])

    def test_saga_turn_action_applies_unqualified_but_not_effect_only_replacement(
        self,
    ):
        session = self.session(7143018)
        engine = session.engine
        saga = self.card(engine, "A", "Urza's Saga")
        engine.move_card(
            saga.object_id,
            "battlefield",
            controller="A",
            log=False,
            semantic_events=False,
        )
        self.add_permanent(
            engine,
            seat="A",
            name="Doubling Season",
            ref="a-effect-only",
        )
        self.add_permanent(
            engine,
            seat="A",
            name="Doc Samson, Super Psychiatrist",
            ref="a-unqualified",
        )
        event_sequence = engine.state.event_sequence

        advance_active_player_sagas(engine, "A")

        self.assertEqual(3, saga.counters["lore"])
        replacements = [
            event
            for event in engine.state.events
            if event.event_id > event_sequence
            and event.code == "replacement.apply"
        ]
        self.assertEqual(1, len(replacements))
        self.assertEqual(
            "a-unqualified",
            replacements[0].details["source"],
        )

    def test_saga_turn_replacement_choice_is_private_resumable_and_exact(
        self,
    ):
        session = self.session(7143019, players=4)
        engine = session.engine
        saga = self.card(engine, "A", "Urza's Saga")
        engine.move_card(
            saga.object_id,
            "battlefield",
            controller="A",
            log=False,
            semantic_events=False,
        )
        engine.state.pending_trigger_batches.clear()
        engine.state.stack.clear()
        self.add_permanent(
            engine,
            seat="A",
            name="Doc Samson, Super Psychiatrist",
            ref="a-doc-turn",
        )
        self.add_permanent(
            engine,
            seat="A",
            name="Doc Samson, Super Psychiatrist",
            ref="a-second-doc-turn",
        )
        engine.state.active_player = "A"
        engine.state.phase_index = TURN_STEPS.index(
            ("precombat_main", "main")
        )
        engine._enter_step()

        self.assertEqual("precombat_main", engine.state.phase)
        self.assertEqual("replacement.order", engine.state.pending_decision.kind)
        self.assertEqual(1, saga.counters["lore"])
        projector = StateProjector(self.db, engine.state)
        projected_a = projector._decision("pilot:A")
        self.assertIsNotNone(projected_a)
        for seat in ("B", "C", "D"):
            self.assertIsNone(projector._decision(f"pilot:{seat}"))
        serialized = json.dumps(projected_a, sort_keys=True)
        self.assertNotIn("turn_action_frame", serialized)
        self.assertNotIn("replacement_batch", serialized)
        self.assertNotIn(saga.object_id, serialized)

        malformed = copy.deepcopy(engine.state.pending_decision.continuation)
        malformed["turn_action_frame"]["unknown"] = True
        with self.assertRaises(ReplacementEffectError):
            ReplacementContinuation.from_dict(malformed)
        malformed = copy.deepcopy(engine.state.pending_decision.continuation)
        malformed["held_triggers"] = [{"unknown": True}]
        with self.assertRaises(ReplacementEffectError):
            ReplacementContinuation.from_dict(malformed)
        malformed = copy.deepcopy(engine.state.pending_decision.continuation)
        malformed["held_triggers"] = [
            StackItem(
                stack_id="held-trigger-id",
                ref="held-trigger-ref",
                kind="triggered_ability",
                controller="A",
                label="Held trigger",
                targets=[4],
            ).to_dict()
        ]
        with self.assertRaises(ReplacementEffectError):
            ReplacementContinuation.from_dict(malformed)

        session.initial_checkpoint = checkpoint_envelope(engine.state)
        session.commands.clear()
        session.decisions.clear()
        with tempfile.TemporaryDirectory() as temporary:
            record_dir = Path(temporary) / "saga-turn-replacement"
            session.save(record_dir)
            restarted = CommanderSession.load(self.db, record_dir)
            restarted_projection = StateProjector(
                self.db, restarted.engine.state
            )._decision("pilot:A")
            selection = restarted_projection["ctx"]["options"][0]["id"]
            result = restarted.act(
                "pilot:A",
                {
                    "action_id": "choose",
                    "choices": {"replacement": selection},
                },
            )
            self.assertTrue(result.ok, result.summary)
            restarted_saga = restarted.engine.state.cards[saga.object_id]
            self.assertEqual(4, restarted_saga.counters["lore"])
            expected_hash = authoritative_state_hash(restarted.engine.state)
            restarted.save(record_dir)
            replay = replay_record(record_dir, self.db, verify=True)
        self.assertTrue(replay["ok"], replay)
        self.assertEqual(expected_hash, replay["final_state_hash"])

    def test_simultaneous_saga_turn_replacements_pin_event_ids_and_replay(
        self,
    ):
        session = self.session(7143022, players=4)
        engine = session.engine
        first = self.card(engine, "A", "Urza's Saga")
        engine.move_card(
            first.object_id,
            "battlefield",
            controller="A",
            log=False,
            semantic_events=False,
        )
        second = CardInstance(
            object_id="fixture:second-turn-replacement-saga",
            ref="second-turn-replacement-saga",
            oracle_id=first.oracle_id,
            printed_name=first.printed_name,
            owner="A",
            controller="A",
            zone="battlefield",
            known_to=list(engine.seats),
            revealed_to=list(engine.seats),
            counters={"lore": 1},
        )
        engine.state.cards[second.object_id] = second
        engine.state.players["A"].zones["battlefield"].append(
            second.object_id
        )
        engine.state.pending_trigger_batches.clear()
        engine.state.stack.clear()
        for ref in ("first-doc-batch", "second-doc-batch"):
            self.add_permanent(
                engine,
                seat="A",
                name="Doc Samson, Super Psychiatrist",
                ref=ref,
            )
        engine.state.active_player = "A"
        engine.state.phase_index = TURN_STEPS.index(
            ("precombat_main", "main")
        )
        engine._enter_step()

        self.assertEqual("replacement.order", engine.state.pending_decision.kind)
        self.assertEqual(1, first.counters["lore"])
        self.assertEqual(1, second.counters["lore"])
        session.initial_checkpoint = checkpoint_envelope(engine.state)
        session.commands.clear()
        session.decisions.clear()

        choices = 0
        while engine.state.pending_decision.kind == "replacement.order":
            packet = session.packet("pilot:A", full=True)["decision"]
            context = packet["ctx"]
            response = {
                "replacement": context["options"][0]["id"],
            }
            event_options = context.get("event_order_options") or []
            if event_options:
                response["replacement_event"] = event_options[-1]
            result = session.act(
                "pilot:A",
                {
                    "action_id": "choose",
                    "choices": response,
                },
            )
            self.assertTrue(result.ok, result.summary)
            choices += 1
            self.assertLessEqual(choices, 4)

        self.assertGreaterEqual(choices, 2)
        self.assertEqual(4, first.counters["lore"])
        self.assertEqual(4, second.counters["lore"])
        expected_hash = authoritative_state_hash(engine.state)
        with tempfile.TemporaryDirectory() as temporary:
            record_dir = Path(temporary) / "saga-turn-event-order"
            session.save(record_dir)
            replay = replay_record(record_dir, self.db, verify=True)
        self.assertTrue(replay["ok"], replay)
        self.assertEqual(expected_hash, replay["final_state_hash"])

    def test_multiple_sagas_commit_lore_before_any_chapter_dispatch(self):
        session = self.session(7143004)
        engine = session.engine
        first = self.card(engine, "A", "Urza's Saga")
        engine.move_card(
            first.object_id,
            "battlefield",
            controller="A",
            log=False,
            semantic_events=False,
        )
        second = CardInstance(
            object_id="fixture:second-urzas-saga",
            ref="second-urzas-saga",
            oracle_id=first.oracle_id,
            printed_name=first.printed_name,
            owner="A",
            controller="A",
            zone="battlefield",
            known_to=list(engine.seats),
            revealed_to=list(engine.seats),
            counters={"lore": 1},
        )
        engine.state.cards[second.object_id] = second
        engine.state.players["A"].zones["battlefield"].append(
            second.object_id
        )
        observed: list[tuple[int, int]] = []
        waiting_triggers: list[StackItem] = []
        original = dispatch_saga_chapters

        def observe(*args, **kwargs):
            observed.append(
                (first.counters["lore"], second.counters["lore"])
            )
            return original(*args, **kwargs)

        with patch(
            "quorune.saga_progression.dispatch_saga_chapters",
            side_effect=observe,
        ):
            advance_active_player_sagas(
                engine,
                "A",
                trigger_batch=waiting_triggers,
            )

        self.assertEqual((2, 2), observed[0])
        self.assertEqual((2, 2), observed[-1])
        self.assertEqual(2, len(waiting_triggers))
        self.assertEqual([], engine.state.pending_trigger_batches)

    def test_stale_saga_snapshot_rolls_back_without_partial_mutation(self):
        session = self.session(7143005)
        engine = session.engine
        first = self.card(engine, "A", "Urza's Saga")
        engine.move_card(
            first.object_id,
            "battlefield",
            controller="A",
            log=False,
            semantic_events=False,
        )
        second = CardInstance(
            object_id="fixture:stale-urzas-saga",
            ref="stale-urzas-saga",
            oracle_id=first.oracle_id,
            printed_name=first.printed_name,
            owner="A",
            controller="A",
            zone="battlefield",
            known_to=list(engine.seats),
            revealed_to=list(engine.seats),
            counters={"lore": 1},
        )
        engine.state.cards[second.object_id] = second
        engine.state.players["A"].zones["battlefield"].append(
            second.object_id
        )
        action = capture_saga_lore_turn_action(engine, "A")
        second.controller = "B"
        before = authoritative_state_hash(engine.state)

        with self.assertRaisesRegex(
            SagaProgressionError, "snapshot changed before commit"
        ):
            commit_saga_lore_turn_action(engine, action)

        self.assertEqual(1, first.counters["lore"])
        self.assertEqual(1, second.counters["lore"])
        self.assertEqual(before, authoritative_state_hash(engine.state))

    def test_competing_saga_entry_replacements_order_to_three_or_four(self):
        results: set[int] = set()
        for seed in (7143006, 7143007):
            session = self.session(seed)
            engine = session.engine
            self.stage_competing_sources(engine, seat="A")
            saga = self.add_saga(
                engine,
                seat="A",
                ref=f"saga-order-{seed}",
            )
            before = authoritative_state_hash(engine.state)
            with self.assertRaises(ReplacementChoiceRequired) as raised:
                engine.move_card(
                    saga.object_id,
                    "battlefield",
                    controller="A",
                    semantic_events=False,
                )
            self.assertEqual(before, authoritative_state_hash(engine.state))
            selected = raised.exception.pending.choice.options[seed % 2]
            engine.move_card(
                saga.object_id,
                "battlefield",
                controller="A",
                replacement_selections=(selected,),
                semantic_events=False,
            )
            results.add(saga.counters["lore"])
        self.assertEqual({3, 4}, results)

    def test_destination_redirect_retargets_nested_saga_counter(self):
        event = ReplaceableEvent(
            event_id="saga-entry-retarget",
            kind="zone.change",
            affected_player=None,
            affected_object=AffectedObject(
                object_id="saga-object",
                owner="A",
                controller=None,
            ),
            payload={
                "origin": "graveyard",
                "destination": "battlefield",
                "destination_controller": "A",
                "object_kind": "card",
                "object_ref": "saga-ref",
                "object_types": ["enchantment", "saga"],
                "logical_object_id": "saga-object:1",
                "owner": "A",
            },
        )
        counter = intrinsic_entry_counters(
            {},
            card_types=("enchantment",),
            card_subtypes=("saga",),
        )[0]
        from quorune.entry_counters import intrinsic_entry_counter_effects

        entry = intrinsic_entry_counter_effects(
            object_ref="saga-ref",
            destination_controller="A",
            counters=(counter,),
        )[0]
        created = apply_replacement(
            replacement_choice(event, (entry,)),
            (entry,),
            entry.effect_id,
        )
        redirect = ReplacementEffect(
            effect_id="redirect-saga-to-exile",
            source_id="replacement-source",
            event_kind="zone.change",
            replacement_class=ReplacementClass.OTHER,
            conditions={"destination": {"eq": "battlefield"}},
            operations=(SetField(field="destination", value="exile"),),
        )
        redirected = apply_replacement(
            replacement_choice(created, (redirect,)),
            (redirect,),
            redirect.effect_id,
        )

        self.assertEqual("exile", redirected.payload["destination"])
        self.assertEqual("exile", redirected.children[0].payload["target_zone"])

    def test_four_player_saga_entry_replacement_choices_follow_apnap(self):
        session = self.session(7143008, players=4)
        engine = session.engine
        self.stage_competing_sources(engine, seat="A")
        self.stage_competing_sources(engine, seat="B")
        saga_a = self.add_saga(engine, seat="A", ref="saga-apnap-a")
        saga_b = self.add_saga(engine, seat="B", ref="saga-apnap-b")
        changes = (
            (saga_b.object_id, "battlefield"),
            (saga_a.object_id, "battlefield"),
        )
        controllers = {saga_a.object_id: "A", saga_b.object_id: "B"}
        before = authoritative_state_hash(engine.state)

        with self.assertRaises(ReplacementChoiceRequired) as first:
            prepare_zone_change_replacement_batch(
                engine,
                changes,
                destination_controllers=controllers,
                error_type=EntryCounterError,
            )
        self.assertEqual("A", first.exception.pending.choice.chooser)
        first_selection = first.exception.pending.choice.options[0]
        with self.assertRaises(ReplacementChoiceRequired) as second:
            prepare_zone_change_replacement_batch(
                engine,
                changes,
                destination_controllers=controllers,
                selections=(first_selection,),
                error_type=EntryCounterError,
            )
        self.assertEqual("B", second.exception.pending.choice.chooser)
        self.assertEqual(before, authoritative_state_hash(engine.state))

    def test_four_player_precombat_lore_only_advances_active_players_sagas(
        self,
    ):
        session = self.session(7143013, players=4)
        engine = session.engine
        active_saga = self.card(engine, "A", "Urza's Saga")
        engine.move_card(
            active_saga.object_id,
            "battlefield",
            controller="A",
            log=False,
            semantic_events=False,
        )
        other_saga = CardInstance(
            object_id="fixture:nonactive-urzas-saga",
            ref="nonactive-urzas-saga",
            oracle_id=active_saga.oracle_id,
            printed_name=active_saga.printed_name,
            owner="B",
            controller="B",
            zone="battlefield",
            known_to=list(engine.seats),
            revealed_to=list(engine.seats),
            counters={"lore": 1},
        )
        engine.state.cards[other_saga.object_id] = other_saga
        engine.state.players["B"].zones["battlefield"].append(
            other_saga.object_id
        )

        advance_active_player_sagas(engine, "A")

        self.assertEqual(2, active_saga.counters["lore"])
        self.assertEqual(1, other_saga.counters["lore"])

    def test_removed_current_chapter_abilities_do_not_dispatch_or_block_lore(self):
        session = self.session(7143014)
        engine = session.engine
        record = self.register_ordinary_saga_chapters(engine)
        saga = self.add_saga(
            engine,
            seat="A",
            ref="removed-chapters",
            zone="battlefield",
            oracle_id=record.oracle_id,
        )
        identity = ContinuousObjectIdentity(
            object_id=saga.object_id,
            logical_object_id=saga.logical_object_id,
        )
        commit_continuous_effect(
            engine.state,
            ContinuousEffect(
                effect_id="fixture:remove-saga-chapters",
                source_id="fixture:chapter-removal-source",
                layer=Layer.ABILITY,
                sublayer="6",
                timestamp=engine._next_zone_timestamp(),
                operations=(ContinuousOperation("remove_all_abilities"),),
                origin=ContinuousEffectOrigin.RESOLUTION,
                duration=ContinuousEffectDuration.UNTIL_END_OF_TURN,
                applies=ObjectQuerySpec(zones=("battlefield",)),
                locked_objects=(identity,),
            ),
        )

        changed = advance_active_player_sagas(engine, "A")

        self.assertEqual((saga.object_id,), changed)
        self.assertEqual(1, saga.counters["lore"])
        self.assertFalse(engine.state.stack)
        self.assertFalse(engine.state.pending_trigger_batches)

    def test_current_copy_face_without_chapter_ability_does_not_dispatch(self):
        session = self.session(7143015)
        engine = session.engine
        record = self.register_ordinary_saga_chapters(engine)
        saga = self.add_saga(
            engine,
            seat="A",
            ref="copied-saga-face",
            zone="battlefield",
            oracle_id=record.oracle_id,
        )
        saga.annotations["copy_overrides"] = {
            "name": "Copied Saga Face",
            "type_line": "Enchantment — Saga",
            "ability_fragments": [],
        }
        saga.counters["lore"] = 1

        dispatch_saga_chapters(
            engine,
            saga,
            previous_lore=0,
            trigger_batch=[],
        )

        self.assertFalse(engine.state.stack)
        self.assertFalse(engine.state.pending_trigger_batches)

    def test_removed_final_chapter_ability_suppresses_sba_and_restore_does_not_replay(self):
        session = self.session(7143016)
        engine = session.engine
        record = self.register_ordinary_saga_chapters(engine)
        saga = self.add_saga(
            engine,
            seat="A",
            ref="removed-final-chapter",
            zone="battlefield",
            oracle_id=record.oracle_id,
        )
        saga.counters["lore"] = 2
        identity = ContinuousObjectIdentity(
            object_id=saga.object_id,
            logical_object_id=saga.logical_object_id,
        )
        effect = ContinuousEffect(
            effect_id="fixture:remove-final-saga-chapter",
            source_id="fixture:chapter-removal-source",
            layer=Layer.ABILITY,
            sublayer="6",
            timestamp=engine._next_zone_timestamp(),
            operations=(ContinuousOperation("remove_all_abilities"),),
            origin=ContinuousEffectOrigin.RESOLUTION,
            duration=ContinuousEffectDuration.UNTIL_END_OF_TURN,
            applies=ObjectQuerySpec(zones=("battlefield",)),
            locked_objects=(identity,),
        )
        commit_continuous_effect(engine.state, effect)

        advance_active_player_sagas(engine, "A")
        self.assertEqual(3, saga.counters["lore"])
        self.assertFalse(engine.state.stack)
        self.assertFalse(engine.state.pending_trigger_batches)
        self.assertFalse(engine._stabilize())
        self.assertEqual("battlefield", saga.zone)
        assert engine.state.continuous_effects is not None
        engine.state.continuous_effects.remove(effect)
        self.assertFalse(engine._stabilize())

        self.assertEqual("graveyard", saga.zone)
        self.assertFalse(engine.state.stack)
        self.assertFalse(engine.state.pending_trigger_batches)

    def test_final_chapter_sba_waits_then_sacrifices_under_current_control(self):
        session = self.session(7144001)
        engine = session.engine
        saga = self.card(engine, "A", "Urza's Saga")
        engine.move_card(
            saga.object_id,
            "battlefield",
            controller="B",
            log=False,
            semantic_events=False,
        )
        engine.state.pending_trigger_batches.clear()
        engine.state.stack.clear()
        saga.counters["lore"] = 3
        chapter = self.chapter_item(engine, saga)
        engine.state.stack.append(chapter)

        self.assertFalse(engine._stabilize())
        self.assertEqual("battlefield", saga.zone)

        engine.state.stack.remove(chapter)
        self.assertFalse(engine._stabilize())

        self.assertEqual("graveyard", saga.zone)
        self.assertIn(
            saga.object_id, engine.state.players["A"].zones["graveyard"]
        )
        event = next(
            value
            for value in reversed(engine.state.events)
            if value.code == "state.saga_sacrificed"
        )
        self.assertEqual([saga.ref], event.details["objects"])

    def test_waiting_final_chapter_batch_defers_before_stack_placement(self):
        session = self.session(7144012)
        engine = session.engine
        saga = self.card(engine, "A", "Urza's Saga")
        engine.move_card(
            saga.object_id,
            "battlefield",
            controller="A",
            log=False,
            semantic_events=False,
        )
        engine.state.pending_trigger_batches.clear()
        engine.state.stack.clear()
        saga.counters["lore"] = 3
        enqueue_trigger_batch(engine, [self.chapter_item(engine, saga)])

        self.assertFalse(engine._stabilize())

        self.assertEqual("battlefield", saga.zone)
        self.assertTrue(
            engine.state.stack or engine.state.pending_trigger_batches
        )

    def test_old_incarnation_chapter_does_not_protect_reentered_saga(self):
        session = self.session(7144002)
        engine = session.engine
        saga = self.card(engine, "A", "Urza's Saga")
        engine.move_card(
            saga.object_id,
            "battlefield",
            controller="A",
            log=False,
            semantic_events=False,
        )
        engine.state.pending_trigger_batches.clear()
        saga.counters["lore"] = 3
        engine.state.stack.append(
            self.chapter_item(
                engine,
                saga,
                logical_object_id="previous-incarnation",
            )
        )

        self.assertFalse(engine._stabilize())
        self.assertEqual("graveyard", saga.zone)

    def test_completed_phased_out_saga_waits_until_it_phases_in(self):
        session = self.session(7144003)
        engine = session.engine
        saga = self.card(engine, "A", "Urza's Saga")
        engine.move_card(
            saga.object_id,
            "battlefield",
            controller="A",
            log=False,
            semantic_events=False,
        )
        engine.state.pending_trigger_batches.clear()
        saga.counters["lore"] = 3
        saga.phased_out = True

        self.assertFalse(engine._stabilize())
        self.assertEqual("battlefield", saga.zone)

        saga.phased_out = False
        self.assertFalse(engine._stabilize())
        self.assertEqual("graveyard", saga.zone)

    def test_countered_final_chapter_uses_the_next_sba_check(self):
        session = self.session(7144004)
        engine = session.engine
        saga = self.card(engine, "A", "Urza's Saga")
        engine.move_card(
            saga.object_id,
            "battlefield",
            controller="A",
            log=False,
            semantic_events=False,
        )
        engine.state.pending_trigger_batches.clear()
        engine.state.stack.clear()
        saga.counters["lore"] = 3
        chapter = self.chapter_item(engine, saga)
        engine.state.stack.append(chapter)

        engine._counter_stack_item(chapter.ref, as_rule=True)
        self.assertEqual("battlefield", saga.zone)
        self.assertFalse(engine._stabilize())
        self.assertEqual("graveyard", saga.zone)

    def test_source_leaving_before_chapter_resolution_is_not_sacrificed_twice(self):
        session = self.session(7144005)
        engine = session.engine
        saga = self.card(engine, "A", "Urza's Saga")
        engine.move_card(
            saga.object_id,
            "battlefield",
            controller="A",
            log=False,
            semantic_events=False,
        )
        engine.state.pending_trigger_batches.clear()
        saga.counters["lore"] = 3
        chapter = self.chapter_item(engine, saga)
        engine.state.stack.append(chapter)
        engine.move_card(
            saga.object_id,
            "exile",
            reason="source leaves before final chapter resolves",
            semantic_events=True,
        )
        engine.state.stack.remove(chapter)

        self.assertFalse(engine._stabilize())
        self.assertEqual("exile", saga.zone)
        self.assertFalse(
            any(
                value.code == "state.saga_sacrificed"
                for value in engine.state.events
            )
        )

    def test_final_chapter_sacrifice_uses_destination_replacement(self):
        session = self.session(7144011)
        engine = session.engine
        saga = self.card(engine, "A", "Urza's Saga")
        engine.move_card(
            saga.object_id,
            "battlefield",
            controller="A",
            log=False,
            semantic_events=False,
        )
        source = self.add_permanent(
            engine,
            seat="B",
            name="Birds of Paradise",
            ref="saga-graveyard-replacement",
        )
        engine.semantics.put(
            SemanticProgram(
                key="test:saga-graveyard-replacement",
                label="Replace completed Saga destination",
                oracle_id=source.oracle_id,
                ability_id="static:front:saga-destination",
                active_zone="battlefield",
                event="zone.change",
                trust_level="provisional",
                handlers=[
                    {
                        "handler_id": "replacement.zone.destination.v1",
                        "schema_version": 1,
                        "event": "zone.change",
                        "condition": {
                            "destination": "graveyard",
                            "object_kind": "card",
                            "owner_relation": "opponent",
                        },
                        "destination": "exile",
                        "counters": {},
                    }
                ],
            )
        )
        engine.state.pending_trigger_batches.clear()
        engine.state.stack.clear()
        saga.counters["lore"] = 3

        original_trust = engine.semantic_program_is_current_trusted
        with patch.object(
            type(engine),
            "semantic_program_is_current_trusted",
            autospec=True,
            side_effect=lambda runtime, program: (
                program.key == "test:saga-graveyard-replacement"
                or original_trust(program)
            ),
        ), patch.object(
            engine,
            "_dispatch_semantic_event",
            wraps=engine._dispatch_semantic_event,
        ) as dispatch:
            self.assertFalse(engine._stabilize())

        self.assertEqual("exile", saga.zone)
        self.assertIn(
            "permanent.sacrificed",
            [call.args[0] for call in dispatch.call_args_list],
        )

    def test_four_player_completed_sagas_move_in_one_sba_batch(self):
        session = self.session(7144006, players=4)
        engine = session.engine
        first = self.card(engine, "A", "Urza's Saga")
        engine.move_card(
            first.object_id,
            "battlefield",
            controller="A",
            log=False,
            semantic_events=False,
        )
        second = CardInstance(
            object_id="fixture:completed-saga-b",
            ref="completed-saga-b",
            oracle_id=first.oracle_id,
            printed_name=first.printed_name,
            owner="B",
            controller="B",
            zone="battlefield",
            known_to=list(engine.seats),
            revealed_to=list(engine.seats),
            counters={"lore": 3},
        )
        engine.state.cards[second.object_id] = second
        engine.state.players["B"].zones["battlefield"].append(
            second.object_id
        )
        engine.state.pending_trigger_batches.clear()
        engine.state.stack.clear()
        first.counters["lore"] = 3

        self.assertFalse(engine._stabilize())

        self.assertEqual("graveyard", first.zone)
        self.assertEqual("graveyard", second.zone)
        self.assertEqual(first.zone_timestamp, second.zone_timestamp)
        event = next(
            value
            for value in reversed(engine.state.events)
            if value.code == "state.saga_sacrificed"
        )
        self.assertEqual(
            [first.ref, second.ref], event.details["objects"]
        )

    def test_saga_lifecycle_snapshot_staleness_rolls_back_before_mutation(self):
        session = self.session(7144007)
        engine = session.engine
        saga = self.card(engine, "A", "Urza's Saga")
        engine.move_card(
            saga.object_id,
            "battlefield",
            controller="A",
            log=False,
            semantic_events=False,
        )
        engine.state.pending_trigger_batches.clear()
        saga.counters["lore"] = 3
        batch = evaluate_state_based_actions(
            permanents=engine._permanent_sba_snapshots(),
            objects=engine._object_sba_snapshots(),
        )
        saga.controller = "B"
        before = authoritative_state_hash(engine.state)

        from quorune.state_based_execution import (
            StateBasedExecutionError,
            prepare_state_based_execution,
        )

        with self.assertRaisesRegex(
            StateBasedExecutionError, "snapshot changed"
        ):
            prepare_state_based_execution(engine, batch)
        self.assertEqual(before, authoritative_state_hash(engine.state))
        self.assertEqual("battlefield", saga.zone)

    def test_final_chapter_completion_replays_exactly(self):
        session = self.session(7144008)
        engine = session.engine
        saga = self.card(engine, "A", "Urza's Saga")
        engine.move_card(
            saga.object_id,
            "battlefield",
            controller="A",
            log=False,
            semantic_events=False,
        )
        engine.state.pending_trigger_batches.clear()
        engine.state.stack.clear()
        saga.counters["lore"] = 3
        engine.state.stack.append(
            self.chapter_item(engine, saga, chapter=1)
        )
        engine._grant_priority("A")
        engine.pump()
        session.initial_checkpoint = checkpoint_envelope(engine.state)
        session.commands.clear()
        session.decisions.clear()

        for seat in ("A", "B"):
            result = session.act(
                f"pilot:{seat}",
                {"a": "pass", "reason": "Resolve the chapter."},
            )
            self.assertTrue(result.ok, result.summary)

        self.assertEqual("graveyard", saga.zone)
        expected_hash = authoritative_state_hash(engine.state)
        with tempfile.TemporaryDirectory() as temporary:
            record_dir = Path(temporary) / "saga-final-record"
            session.save(record_dir)
            replay = replay_record(record_dir, self.db, verify=True)
        self.assertTrue(replay["ok"], replay)
        self.assertEqual(expected_hash, replay["final_state_hash"])

    def test_saga_lifecycle_mutation_is_killed(self):
        def final_saga_moves(seed: int) -> None:
            session = self.session(seed)
            engine = session.engine
            saga = self.card(engine, "A", "Urza's Saga")
            engine.move_card(
                saga.object_id,
                "battlefield",
                controller="A",
                log=False,
                semantic_events=False,
            )
            engine.state.pending_trigger_batches.clear()
            saga.counters["lore"] = 3
            engine._stabilize()
            self.assertEqual("graveyard", saga.zone)

        final_saga_moves(7144009)
        with patch(
            "quorune.saga_lifecycle."
            "SagaFinalChapterSnapshot.requires_sacrifice",
            new_callable=lambda: property(lambda _self: False),
        ):
            with self.assertRaises(AssertionError):
                final_saga_moves(7144010)

    def test_saga_entry_replacement_choice_replays_exactly(self):
        session = self.session(7143009, players=4)
        engine = session.engine
        self.stage_competing_sources(engine, seat="A")
        saga = self.card(engine, "A", "Urza's Saga")
        engine._remove_from_zone(saga)
        engine._reset_zone_change(saga, "stack")
        saga.zone = "stack"
        saga.controller = "A"
        saga.known_to = list(engine.seats)
        saga.revealed_to = list(engine.seats)
        item = StackItem(
            stack_id="saga-resolution-stack",
            ref="S-saga-resolution",
            kind="spell",
            controller="A",
            label="Saga Entry Fixture",
            card_object_id=saga.object_id,
            default_destination="battlefield",
            visibility=list(engine.seats),
        )
        engine.state.stack.append(item)
        engine._continue_resolution(
            stack_ref=item.ref,
            effects=[],
            destination=None,
            note="Saga entry replacement replay",
        )

        self.assertEqual("replacement.order", engine.state.pending_decision.kind)
        projector = StateProjector(self.db, engine.state)
        projected_a = projector._decision("pilot:A")
        self.assertIsNotNone(projected_a)
        for seat in ("B", "C", "D"):
            self.assertIsNone(projector._decision(f"pilot:{seat}"))
        serialized = json.dumps(projected_a, sort_keys=True)
        self.assertNotIn("replacement_batch", serialized)
        self.assertNotIn(saga.object_id, serialized)

        session.initial_checkpoint = checkpoint_envelope(engine.state)
        session.commands.clear()
        session.decisions.clear()
        selection = projected_a["ctx"]["options"][0]["id"]
        result = session.act(
            "pilot:A",
            {
                "action_id": "choose",
                "choices": {"replacement": selection},
            },
        )
        self.assertTrue(result.ok, result.summary)
        self.assertEqual("battlefield", saga.zone)
        self.assertIn(saga.counters["lore"], {3, 4})
        expected_hash = authoritative_state_hash(engine.state)

        with tempfile.TemporaryDirectory() as temporary:
            record_dir = Path(temporary) / "saga-entry-record"
            session.save(record_dir)
            replay = replay_record(record_dir, self.db, verify=True)
        self.assertTrue(replay["ok"], replay)
        self.assertEqual(expected_hash, replay["final_state_hash"])

    def test_saga_entry_replacement_continuation_survives_process_restart(self):
        session = self.session(7143017, players=4)
        engine = session.engine
        self.stage_competing_sources(engine, seat="A")
        saga = self.card(engine, "A", "Urza's Saga")
        engine._remove_from_zone(saga)
        engine._reset_zone_change(saga, "stack")
        saga.zone = "stack"
        saga.controller = "A"
        saga.known_to = list(engine.seats)
        saga.revealed_to = list(engine.seats)
        item = StackItem(
            stack_id="saga-restart-stack",
            ref="S-saga-restart",
            kind="spell",
            controller="A",
            label="Saga Restart Fixture",
            card_object_id=saga.object_id,
            default_destination="battlefield",
            visibility=list(engine.seats),
        )
        engine.state.stack.append(item)
        engine._continue_resolution(
            stack_ref=item.ref,
            effects=[],
            destination=None,
            note="Saga entry restart",
        )
        self.assertEqual("replacement.order", engine.state.pending_decision.kind)
        session.initial_checkpoint = checkpoint_envelope(engine.state)
        session.commands.clear()
        session.decisions.clear()

        with tempfile.TemporaryDirectory() as temporary:
            record_dir = Path(temporary) / "saga-entry-restart"
            session.save(record_dir)
            restarted = CommanderSession.load(self.db, record_dir)
            projector = StateProjector(self.db, restarted.engine.state)
            projected_a = projector._decision("pilot:A")
            self.assertIsNotNone(projected_a)
            for seat in ("B", "C", "D"):
                self.assertIsNone(projector._decision(f"pilot:{seat}"))
            selection = projected_a["ctx"]["options"][0]["id"]
            result = restarted.act(
                "pilot:A",
                {
                    "action_id": "choose",
                    "choices": {"replacement": selection},
                },
            )
            self.assertTrue(result.ok, result.summary)
            restarted_saga = restarted.engine.state.cards[saga.object_id]
            self.assertEqual("battlefield", restarted_saga.zone)
            self.assertIn(restarted_saga.counters["lore"], {3, 4})
            restarted.save(record_dir)
            replay = replay_record(record_dir, self.db, verify=True)
        self.assertTrue(replay["ok"], replay)
        self.assertEqual(
            authoritative_state_hash(restarted.engine.state),
            replay["final_state_hash"],
        )

    def test_precombat_saga_progression_replays_exactly(self):
        session = self.session(7143014)
        engine = session.engine
        saga = self.card(engine, "A", "Urza's Saga")
        engine.move_card(
            saga.object_id,
            "battlefield",
            controller="A",
            log=False,
            semantic_events=False,
        )
        engine.state.active_player = "A"
        engine.state.phase_index = TURN_STEPS.index(("beginning", "draw"))
        engine.state.stack.clear()
        engine.state.pending_trigger_batches.clear()
        engine._grant_priority("A")
        engine.pump()
        session.initial_checkpoint = checkpoint_envelope(engine.state)
        session.commands.clear()
        session.decisions.clear()

        for seat in ("A", "B"):
            result = session.act(
                f"pilot:{seat}",
                {"a": "pass", "reason": "Advance to the main phase."},
            )
            self.assertTrue(result.ok, result.summary)

        self.assertEqual(2, saga.counters["lore"])
        expected_hash = authoritative_state_hash(engine.state)
        with tempfile.TemporaryDirectory() as temporary:
            record_dir = Path(temporary) / "saga-precombat-record"
            session.save(record_dir)
            replay = replay_record(record_dir, self.db, verify=True)
        self.assertTrue(replay["ok"], replay)
        self.assertEqual(expected_hash, replay["final_state_hash"])

    def test_saga_counter_producer_mutations_are_killed(self):
        def assert_entry_counter_created(seed: int) -> None:
            session = self.session(seed)
            engine = session.engine
            saga = self.add_saga(
                engine,
                seat="A",
                ref=f"saga-mutation-{seed}",
            )
            engine.move_card(
                saga.object_id,
                "battlefield",
                controller="A",
                semantic_events=False,
            )
            self.assertEqual(1, saga.counters.get("lore", 0))

        assert_entry_counter_created(7143015)
        with patch(
            "quorune.semantic_runtime.zone_replacements."
            "intrinsic_entry_counter_effects",
            return_value=(),
        ):
            with self.assertRaises(AssertionError):
                assert_entry_counter_created(7143016)

        def assert_turn_action_is_not_effect_generated(seed: int) -> None:
            session = self.session(seed)
            engine = session.engine
            saga = self.card(engine, "A", "Urza's Saga")
            engine.move_card(
                saga.object_id,
                "battlefield",
                controller="A",
                log=False,
                semantic_events=False,
            )
            self.add_permanent(
                engine,
                seat="A",
                name="Doubling Season",
                ref=f"doubling-mutation-{seed}",
            )
            advance_active_player_sagas(engine, "A")
            self.assertEqual(2, saga.counters["lore"])

        def mutate_rule_action_to_effect(host, requests, **kwargs):
            return canonical_prepare_counter_placements(
                host,
                tuple(
                    replace(request, effect_generated=True)
                    for request in requests
                ),
                **kwargs,
            )

        assert_turn_action_is_not_effect_generated(7143020)
        with patch(
            "quorune.saga_progression.prepare_counter_placements",
            side_effect=mutate_rule_action_to_effect,
        ):
            with self.assertRaises(AssertionError):
                assert_turn_action_is_not_effect_generated(7143021)

    def test_blocked_dependency_prevents_trusted_saga_card_form(self):
        registry_value = json.loads(
            (ROOT / "quorune" / "rules" / "capability-registry.json")
            .read_text(encoding="utf-8")
        )
        dependency = next(
            row
            for row in registry_value["capabilities"]
            if row["id"] == "counter.placement.quantity_replacement"
        )
        dependency["status"] = "blocked"
        dependency["blockers"] = ["test mutation"]
        registry = CapabilityRegistry(copy.deepcopy(registry_value))
        registry.mark_evidence_verified("0" * 64)

        with self.assertRaisesRegex(
            ValueError, "intrinsic entry-counter capability is blocked"
        ):
            compile_card_program(
                self.db,
                self.saga_record(),
                capability_registry=registry,
                capability_profile="commander_review",
                trust_level="trusted",
            )

    def test_blocked_final_lifecycle_prevents_trusted_saga_card_form(self):
        registry_value = json.loads(
            (ROOT / "quorune" / "rules" / "capability-registry.json")
            .read_text(encoding="utf-8")
        )
        dependency = next(
            row
            for row in registry_value["capabilities"]
            if row["id"] == "state_based.saga_final_chapter"
        )
        dependency["status"] = "blocked"
        dependency["blockers"] = ["test mutation"]
        registry = CapabilityRegistry(copy.deepcopy(registry_value))
        registry.mark_evidence_verified("0" * 64)

        with self.assertRaisesRegex(
            ValueError, "intrinsic entry-counter capability is blocked"
        ):
            compile_card_program(
                self.db,
                self.saga_record(),
                capability_registry=registry,
                capability_profile="commander_review",
                trust_level="trusted",
            )


if __name__ == "__main__":
    unittest.main()
