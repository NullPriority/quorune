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
from quorune.model import CardInstance, CombatState, StackItem
from quorune.oracle_ir import compile_oracle_card, generated_programs
from quorune.projection import StateProjector
from quorune.replacement_effects import ReplacementClass
from quorune.record import (
    authoritative_state_hash,
    checkpoint_envelope,
    replay_record,
)
from quorune.rules.capabilities import (
    CapabilityRegistry,
    load_default_capability_registry,
)
from quorune.semantic_runtime.block_restrictions import (
    BlockRestrictionContext,
    SelfCounterBlockRestrictionHandler,
    SelfCounterBlockRestrictionNode,
)
from quorune.semantic_runtime.context import SemanticNodeError
from quorune.semantic_runtime.self_entry_counters import (
    SelfEntryCounterHandler,
    SelfEntryCounterNode,
)
from quorune.semantic_runtime.zone_replacement_model import (
    ZoneChangeSubjectSnapshot,
)
from quorune.semantic_runtime import zone_replacements
from quorune.unleash import (
    unleash_block_handler_descriptor,
    unleash_entry_handler_descriptor,
)
from scripts.build_test_database import build_fixture_database


REGISTRY_PATH = ROOT / "quorune" / "rules" / "capability-registry.json"


def focused_database(directory: str) -> CardDatabase:
    database = Path(directory) / "unleash.sqlite3"
    build_fixture_database(
        [
            ROOT / "tests" / "fixtures" / "scryfall-exact-lists.json",
            ROOT / "tests" / "fixtures" / "counter-replacement-cards.json",
            ROOT / "tests" / "fixtures" / "unleash-cards.json",
        ],
        database,
    )
    return CardDatabase(database)


class UnleashCompilerAndModelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.db = focused_database(cls.temporary.name)
        cls.card = cls.db.lookup("Rakdos Cackler")
        cls.capabilities = load_default_capability_registry()

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temporary.cleanup()

    def test_ordinary_unleash_lowers_to_two_source_spanned_typed_programs(self):
        ir = compile_oracle_card(
            self.card,
            capability_registry=self.capabilities,
            capability_profile="commander_review",
        )
        nodes = [
            node
            for node in ir.faces[0].nodes
            if node.template_id in {
                "unleash-optional-entry-counter-v1",
                "unleash-self-counter-block-prohibition-v1",
            }
        ]
        self.assertEqual("exact", ir.status)
        self.assertEqual(2, len(nodes))
        self.assertEqual(
            {
                "counter.producer.optional_self_entry",
                "combat.block.self_counter_prohibition",
            },
            {node.capability_dependencies[0] for node in nodes},
        )
        keyword_end = self.card.oracle_text.index(" (")
        self.assertEqual(
            {(0, keyword_end)},
            {(node.span.start, node.span.end) for node in nodes},
        )
        programs = [
            program
            for program in generated_programs(
                self.db,
                self.card,
                trust_level="trusted",
                capability_registry=self.capabilities,
                capability_profile="commander_review",
            )
            if str(program.provenance.get("template_id", "")).startswith(
                "unleash-"
            )
        ]
        self.assertEqual(2, len(programs))
        self.assertEqual(2, len({program.key for program in programs}))
        self.assertTrue(
            all(program.capability_closure["trusted"] for program in programs)
        )

        repeated = replace(
            self.card,
            oracle_text="Unleash, Unleash",
            keywords=("Unleash",),
        )
        repeated_programs = [
            program
            for program in generated_programs(
                self.db,
                repeated,
                trust_level="trusted",
                capability_registry=self.capabilities,
                capability_profile="commander_review",
            )
            if str(program.provenance.get("template_id", "")).startswith(
                "unleash-"
            )
        ]
        self.assertEqual(4, len(repeated_programs))
        self.assertEqual(4, len({program.key for program in repeated_programs}))

    def test_unleash_models_and_descriptors_reject_malformed_values(self):
        self.assertEqual(
            "+1/+1", SelfEntryCounterNode(" +1/+1 ", 1, True, "702.98a").counter_name
        )
        context_source = {"+1/+1": 1}
        context = BlockRestrictionContext("U1", context_source)
        context_source["+1/+1"] = 9
        self.assertEqual(1, context.counters["+1/+1"])
        subject = ZoneChangeSubjectSnapshot(
            object_id="object:U1",
            object_ref="U1",
            logical_object_id="logical:U1",
            owner="A",
            controller="A",
            origin="stack",
            destination="battlefield",
            destination_controller="A",
            entry_face_id="front",
            object_types=("creature",),
            is_card_object=True,
        )
        effect = SelfEntryCounterHandler().subject_replacement_effect(
            unleash_entry_handler_descriptor(),
            subject=subject,
            component_id="program:unleash-entry:0",
        )
        self.assertEqual(ReplacementClass.OTHER, effect.replacement_class)
        for constructor, args in (
            (SelfEntryCounterNode, ("", 1, True, "702.98a")),
            (SelfEntryCounterNode, ("+1/+1", True, True, "702.98a")),
            (SelfEntryCounterNode, ("+1/+1", 1, 1, "702.98a")),
            (SelfCounterBlockRestrictionNode, ("+1/+1", 0, "702.98a")),
            (BlockRestrictionContext, ("U1", {"+1/+1": True})),
        ):
            with self.subTest(constructor=constructor.__name__):
                with self.assertRaises(SemanticNodeError):
                    constructor(*args)
        for handler, descriptor in (
            (SelfEntryCounterHandler(), unleash_entry_handler_descriptor()),
            (
                SelfCounterBlockRestrictionHandler(),
                unleash_block_handler_descriptor(),
            ),
        ):
            with self.subTest(handler=handler.handler_id):
                malformed = {**descriptor, "unknown": True}
                with self.assertRaises(SemanticNodeError):
                    handler.validate(malformed)

    def test_unsupported_unleash_wording_remains_material_residual(self):
        for text in ("Unleash 2", "Unleash — it enters tapped"):
            with self.subTest(text=text):
                ir = compile_oracle_card(
                    replace(
                        self.card,
                        oracle_text=text,
                        keywords=("Unleash",),
                    ),
                    capability_registry=self.capabilities,
                    capability_profile="commander_review",
                )
                self.assertNotEqual("exact", ir.status)
                self.assertTrue(
                    any(
                        "unleash-unsupported-wording" in blocker
                        for residual in ir.material_residuals
                        for blocker in residual.blockers
                    )
                )

    def test_unleash_dependencies_fail_closed(self):
        for capability_id in (
            "counter.producer.optional_self_entry",
            "combat.block.self_counter_prohibition",
        ):
            with self.subTest(capability_id=capability_id):
                value = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
                capability = next(
                    row
                    for row in value["capabilities"]
                    if row["id"] == capability_id
                )
                capability["status"] = "blocked"
                capability["blockers"] = ["test mutation"]
                ir = compile_oracle_card(
                    self.card,
                    capability_registry=CapabilityRegistry(value),
                    capability_profile="commander_review",
                )
                self.assertNotEqual("exact", ir.status)
                self.assertTrue(
                    any(
                        capability_id in blocker
                        for residual in ir.material_residuals
                        for blocker in residual.blockers
                    )
                )


class UnleashRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.db = focused_database(cls.temporary.name)
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
        engine.state.stack.clear()
        session.commands.clear()
        session.decisions.clear()
        return session

    def add_card(
        self,
        engine,
        *,
        seat: str,
        name: str,
        ref: str,
        zone: str,
        controller: str | None = None,
    ) -> CardInstance:
        record = self.db.lookup(name)
        current_controller = controller or seat
        public = zone in {"battlefield", "graveyard", "exile", "command", "stack"}
        card = CardInstance(
            object_id=f"fixture:{ref}",
            ref=ref,
            oracle_id=record.oracle_id,
            printed_name=record.name,
            owner=seat,
            controller=current_controller,
            zone=zone,
            zone_timestamp=engine.state.event_sequence + 1,
            known_to=list(engine.seats) if public else [seat],
            revealed_to=list(engine.seats) if public else [],
        )
        engine.state.cards[card.object_id] = card
        if zone in engine.state.players[seat].zones:
            engine.state.players[seat].zones[zone].append(card.object_id)
        return card

    def register_unleash(self, engine, card: CardInstance, *, repeated: bool = False):
        record = self.db.by_oracle_id(card.oracle_id)
        if repeated:
            record = replace(
                record,
                oracle_text="Unleash, Unleash",
                keywords=("Unleash",),
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
            if str(program.provenance.get("template_id", "")).startswith(
                "unleash-"
            )
        ]
        self.assertEqual(4 if repeated else 2, len(programs))
        for program in programs:
            engine.semantics.put(program)
        return programs

    def begin_entry(self, session, card: CardInstance) -> None:
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
            note="Unleash entry fixture",
        )

    @staticmethod
    def replacement_options(session, seat: str):
        decision = StateProjector(
            session.engine.card_db, session.state
        )._decision(
            f"pilot:{seat}"
        )
        return decision, [option["id"] for option in decision["ctx"]["options"]]

    def choose(self, session, seat: str, selection: str):
        result = session.act(
            f"pilot:{seat}",
            {
                "action_id": "choose",
                "choices": {"replacement": selection},
            },
        )
        self.assertTrue(result.ok, result.summary)

    def test_apply_or_decline_optional_entry_counter(self):
        for offset, apply_counter in enumerate((True, False)):
            with self.subTest(apply_counter=apply_counter):
                session = self.session(7029801 + offset)
                card = self.add_card(
                    session.engine,
                    seat="A",
                    name="Rakdos Cackler",
                    ref=f"choice-{offset}",
                    zone="stack",
                )
                self.register_unleash(session.engine, card)
                self.begin_entry(session, card)
                self.assertEqual(
                    "replacement.order", session.state.pending_decision.kind
                )
                _, options = self.replacement_options(session, "A")
                selection = next(
                    option
                    for option in options
                    if option.startswith("decline:") != apply_counter
                )
                self.choose(session, "A", selection)
                self.assertEqual("battlefield", card.zone)
                self.assertEqual(
                    1 if apply_counter else 0,
                    card.counters.get("+1/+1", 0),
                )

    def test_unleash_counter_uses_canonical_quantity_replacement_order(self):
        session = self.session(7029803)
        engine = session.engine
        card = self.add_card(
            engine,
            seat="A",
            name="Rakdos Cackler",
            ref="replacement-unleash",
            zone="stack",
        )
        doubling = self.add_card(
            engine,
            seat="A",
            name="Doubling Season",
            ref="replacement-doubling",
            zone="battlefield",
        )
        self.register_unleash(engine, card)
        self.begin_entry(session, card)
        _, options = self.replacement_options(session, "A")
        self.choose(
            session,
            "A",
            next(option for option in options if not option.startswith("decline:")),
        )
        while (
            engine.state.pending_decision is not None
            and engine.state.pending_decision.kind == "replacement.order"
        ):
            _, options = self.replacement_options(session, "A")
            self.choose(session, "A", options[0])
        self.assertEqual("battlefield", card.zone)
        self.assertEqual(2, card.counters["+1/+1"])
        self.assertEqual("battlefield", doubling.zone)
        attacker_ref = engine.create_token(
            "B",
            name="Replacement interaction attacker",
            characteristics={
                "type_line": "Token Creature — Test",
                "power": "2",
                "toughness": "2",
            },
        )[0]
        attacker = engine._resolve_object(
            "B", attacker_ref, zones={"battlefield"}
        )
        self.assertEqual(
            (False, "blocker_has_self_counter_prohibition"),
            engine._can_block(attacker, card),
        )

    def test_unleash_counter_prohibition_tracks_current_counter_state(self):
        session = self.session(7029804, players=4)
        engine = session.engine
        blocker = self.add_card(
            engine,
            seat="C",
            name="Rakdos Cackler",
            ref="current-unleash-blocker",
            zone="battlefield",
        )
        self.register_unleash(engine, blocker)
        attacker_ref = engine.create_token(
            "A",
            name="Current attacker",
            characteristics={
                "type_line": "Token Creature — Test",
                "power": "2",
                "toughness": "2",
            },
        )[0]
        attacker = engine._resolve_object("A", attacker_ref, zones={"battlefield"})
        self.assertTrue(engine._can_block(attacker, blocker)[0])
        blocker.counters["+1/+1"] = 1
        self.assertEqual(
            (False, "blocker_has_self_counter_prohibition"),
            engine._can_block(attacker, blocker),
        )
        del blocker.counters["+1/+1"]
        self.assertTrue(engine._can_block(attacker, blocker)[0])

    def test_unleash_block_offer_and_command_share_legality_and_rollback(self):
        session = self.session(7029805, players=4)
        engine = session.engine
        blocker = self.add_card(
            engine,
            seat="C",
            name="Rakdos Cackler",
            ref="offered-unleash-blocker",
            zone="battlefield",
        )
        blocker.counters["+1/+1"] = 1
        self.register_unleash(engine, blocker)
        attacker_ref = engine.create_token(
            "A",
            name="Offered attacker",
            characteristics={
                "type_line": "Token Creature — Test",
                "power": "2",
                "toughness": "2",
            },
        )[0]
        attacker = engine._resolve_object("A", attacker_ref, zones={"battlefield"})
        legal_blocker_ref = engine.create_token(
            "C",
            name="Legal blocker",
            characteristics={
                "type_line": "Token Creature — Test",
                "power": "1",
                "toughness": "1",
            },
        )[0]
        attacker.attacking = "C"
        engine.state.combat = CombatState(
            attackers_declared=True,
            had_attacking_creature=True,
            attackers={attacker.object_id: "C"},
            defending_players=["C"],
        )
        engine._begin_blocker_decisions()
        decision = session.packet("pilot:C", full=True)["decision"]
        self.assertIsNotNone(decision)
        self.assertNotIn(blocker.ref, decision["ctx"]["legal_blocks"])
        self.assertIn(legal_blocker_ref, decision["ctx"]["legal_blocks"])
        self.assertTrue(
            all(
                session.packet(f"pilot:{seat}", full=True)["decision"] is None
                for seat in ("A", "B", "D")
            )
        )
        before = authoritative_state_hash(session.state)
        rejected = session.act(
            "pilot:C",
            {"a": "block", "blk": {blocker.ref: attacker.ref}},
        )
        self.assertFalse(rejected.ok)
        self.assertIn("self_counter", rejected.summary)
        self.assertEqual(before, authoritative_state_hash(session.state))

    def test_unleash_choice_is_destination_controller_scoped_and_replays(self):
        session = self.session(7029806, players=4)
        engine = session.engine
        card = self.add_card(
            engine,
            seat="A",
            controller="C",
            name="Rakdos Cackler",
            ref="controlled-unleash",
            zone="stack",
        )
        self.register_unleash(engine, card)
        self.begin_entry(session, card)
        self.assertIsNone(
            StateProjector(self.db, engine.state)._decision("pilot:A")
        )
        projected, options = self.replacement_options(session, "C")
        self.assertIsNotNone(projected)
        self.assertNotIn(card.object_id, json.dumps(projected, sort_keys=True))
        session.initial_checkpoint = checkpoint_envelope(engine.state)
        session.commands.clear()
        session.decisions.clear()
        self.choose(
            session,
            "C",
            next(option for option in options if not option.startswith("decline:")),
        )
        self.assertEqual("battlefield", card.zone)
        self.assertEqual("C", card.controller)
        expected_hash = authoritative_state_hash(engine.state)
        with tempfile.TemporaryDirectory() as temporary:
            record_dir = Path(temporary) / "unleash-replay"
            session.save(record_dir)
            replay = replay_record(record_dir, self.db, verify=True)
        self.assertTrue(replay["ok"], replay)
        self.assertEqual(expected_hash, replay["final_state_hash"])

    def test_unleash_entry_runtime_mutation_is_killed(self):
        def assert_entry_counter(seed: int) -> None:
            session = self.session(seed)
            card = self.add_card(
                session.engine,
                seat="A",
                name="Rakdos Cackler",
                ref=f"mutant-{seed}",
                zone="stack",
            )
            self.register_unleash(session.engine, card)
            self.begin_entry(session, card)
            if session.engine.state.pending_decision is not None:
                _, options = self.replacement_options(session, "A")
                self.choose(
                    session,
                    "A",
                    next(
                        option
                        for option in options
                        if not option.startswith("decline:")
                    ),
                )
            self.assertEqual(1, card.counters.get("+1/+1", 0))

        assert_entry_counter(7029807)
        original = zone_replacements._zone_change_snapshot_effects

        def remove_self_entry_effects(host, subjects, active_sources, prospective_objects):
            return tuple(
                effect
                for effect in original(host, subjects, active_sources, prospective_objects)
                if not effect.effect_id.startswith(
                    "replacement.zone.self-entry-counter.v1:"
                )
            )

        with patch(
            "quorune.semantic_runtime.zone_replacements."
            "_zone_change_snapshot_effects",
            side_effect=remove_self_entry_effects,
        ):
            with self.assertRaises(AssertionError):
                assert_entry_counter(7029808)

    def test_unleash_block_runtime_mutation_is_killed(self):
        def assert_prohibited(seed: int) -> None:
            session = self.session(seed)
            engine = session.engine
            blocker = self.add_card(
                engine,
                seat="A",
                name="Rakdos Cackler",
                ref=f"block-mutant-{seed}",
                zone="battlefield",
            )
            blocker.counters["+1/+1"] = 1
            self.register_unleash(engine, blocker)
            attacker_ref = engine.create_token(
                "B",
                name="Mutation attacker",
                characteristics={
                    "type_line": "Token Creature — Test",
                    "power": "2",
                    "toughness": "2",
                },
            )[0]
            attacker = engine._resolve_object(
                "B", attacker_ref, zones={"battlefield"}
            )
            self.assertFalse(engine._can_block(attacker, blocker)[0])

        assert_prohibited(7029809)
        with patch(
            "quorune.semantic_runtime.block_restrictions."
            "SelfCounterBlockRestrictionHandler.lower",
            return_value=(),
        ):
            with self.assertRaises(AssertionError):
                assert_prohibited(7029810)


if __name__ == "__main__":
    unittest.main()
