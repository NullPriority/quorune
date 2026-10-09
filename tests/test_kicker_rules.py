from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from common import ROOT, keep_all, make_session
from quorune.carddb import CardDatabase
from quorune.compiled_kicker import compiled_fixed_mana_kicker_spec
from quorune.compiler import kicker_nodes as kicker_nodes_module
from quorune.deck import DeckLoader
from quorune.haste import has_effective_haste, summoning_sickness_prohibits_attack
from quorune.kicker import (
    compile_fixed_kicked_entry,
    compile_fixed_mana_kicker,
    FixedKickedEntrySpec,
    FixedManaKickerSpec,
    kicked_entry_handler_descriptor,
    kicker_cost_handler_descriptor,
    KICKED_ENTRY_CAPABILITY_ID,
    KICKED_ENTRY_HANDLER_ID,
    KICKER_CAPABILITY_ID,
    KICKER_CAST_OPTION_ID,
    KICKER_COST_HANDLER_ID,
    KickerError,
)
from quorune.model import CardInstance
from quorune.oracle_ir import compile_oracle_card, register_generated_programs
from quorune.record import authoritative_state_hash, checkpoint_envelope, replay_record
from quorune.rules.capabilities import CapabilityRegistry, load_default_capability_registry
from quorune.rules.casting.commit import commit_cast
from quorune.rules.casting.model import CastProposalError, CastProposalRequest
from quorune.rules.casting.proposal import build_cast_proposal
from quorune.semantic_runtime.context import SemanticNodeError
from quorune.semantic_runtime.kicker import (
    default_fixed_mana_kicker_registry,
    FixedKickedEntryHandler,
)
from quorune.semantic_runtime.zone_replacement_model import ZoneChangeSubjectSnapshot
from quorune.semantics import SemanticRegistry
from scripts.build_test_database import build_fixture_database


REGISTRY_PATH = ROOT / "quorune" / "rules" / "capability-registry.json"
FIXTURE_PATH = ROOT / "tests" / "fixtures" / "kicker-rules-cards.json"
PARTIAL_FIXTURE_PATH = ROOT / "tests" / "fixtures" / "kicker-partial-card.json"


def focused_card_database(directory: str) -> CardDatabase:
    database = Path(directory) / "kicker-rules.sqlite3"
    build_fixture_database(
        [
            ROOT / "tests" / "fixtures" / "scryfall-exact-lists.json",
            FIXTURE_PATH,
            PARTIAL_FIXTURE_PATH,
        ],
        database,
    )
    return CardDatabase(database)


class FixedKickerModelTests(unittest.TestCase):
    def test_fixed_kicker_and_entry_descriptors_are_strict(self):
        kicker = compile_fixed_mana_kicker(
            material_line="Kicker {2}{G}",
            oracle_line="Kicker {2}{G}",
            line_index=1,
        )
        self.assertIsNotNone(kicker)
        assert kicker is not None
        self.assertEqual(kicker, FixedManaKickerSpec.from_dict(kicker.to_dict()))
        self.assertEqual(
            (kicker,),
            default_fixed_mana_kicker_registry().lower(
                kicker_cost_handler_descriptor(kicker),
                None,
            ),
        )
        self.assertEqual(
            {"GENERIC": 2, "W": 0, "U": 0, "B": 0, "R": 0, "G": 1, "C": 0},
            kicker.cast_cost_option()["requirements"],
        )

        entry = compile_fixed_kicked_entry(
            "If this creature was kicked, it enters with three +1/+1 counters on it and with trample."
        )
        self.assertEqual(FixedKickedEntrySpec(3, "trample"), entry)
        assert entry is not None
        descriptor = kicked_entry_handler_descriptor(entry)
        subject = ZoneChangeSubjectSnapshot(
            object_id="object:1",
            object_ref="K1",
            logical_object_id="object:1@1",
            owner="A",
            controller="A",
            origin="stack",
            destination="battlefield",
            destination_controller="A",
            entry_face_id="front",
            object_types=("creature",),
            is_card_object=True,
            cast_option="kicked",
        )
        effect = FixedKickedEntryHandler().subject_replacement_effect(
            descriptor,
            subject=subject,
            component_id="test:kicker-entry",
        )
        self.assertEqual("SELF_REPLACEMENT", effect.replacement_class.name)
        self.assertEqual(
            ["create_affected_object_counter", "grant_affected_object_keyword"],
            [operation.to_dict()["op"] for operation in effect.operations],
        )

        malformed = kicker.to_dict()
        malformed["unknown"] = True
        with self.assertRaisesRegex(KickerError, "unknown"):
            FixedManaKickerSpec.from_dict(malformed)
        descriptor["unknown"] = True
        with self.assertRaisesRegex(SemanticNodeError, "unknown"):
            FixedKickedEntryHandler().validate(descriptor)


class FixedKickerCompilerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.db = focused_card_database(cls.temporary.name)
        cls.capabilities = load_default_capability_registry()
        cls.registry_value = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temporary.cleanup()

    def compile(self, name: str = "Kavu Titan", *, capabilities=None):
        return compile_oracle_card(
            self.db.lookup(name),
            capability_registry=capabilities or self.capabilities,
            capability_profile="commander_review",
        )

    def test_fixed_kicker_and_entry_compile_source_spanned_programs(self):
        ir = self.compile()
        self.assertEqual("exact", ir.status, ir.material_residuals)
        kicker = next(node for node in ir.faces[0].nodes if node.event == "cast.cost")
        entry = next(node for node in ir.faces[0].nodes if node.event == "zone.change")
        self.assertEqual("single-fixed-mana-kicker-v1", kicker.template_id)
        self.assertEqual((KICKER_CAPABILITY_ID,), kicker.capability_dependencies)
        self.assertEqual(KICKER_COST_HANDLER_ID, kicker.handlers[0]["handler_id"])
        self.assertEqual("fixed-kicked-counter-keyword-entry-v1", entry.template_id)
        self.assertEqual((KICKED_ENTRY_CAPABILITY_ID,), entry.capability_dependencies)
        self.assertEqual(KICKED_ENTRY_HANDLER_ID, entry.handlers[0]["handler_id"])

        registry = SemanticRegistry(include_builtin_packs=False)
        register_generated_programs(
            self.db,
            registry,
            (self.db.lookup("Kavu Titan"),),
            capability_registry=self.capabilities,
            capability_profile="commander_review",
            promote_exact_runtime_handlers=True,
            promote_exact_effect_programs=True,
        )
        program = next(
            program
            for program in registry.programs_for_oracle(
                self.db.lookup("Kavu Titan").oracle_id
            )
            if any(
                descriptor.get("handler_id") == KICKER_COST_HANDLER_ID
                for descriptor in program.handlers
            )
        )
        self.assertEqual("trusted", program.trust_level)
        self.assertEqual("exact", program.provenance["card_program_admission"]["oracle_ir_status"])

    def test_multi_and_variable_kicker_and_open_entry_results_remain_residual(self):
        record = self.db.lookup("Kavu Titan")
        for text in (
            "Kicker {R} and/or {G}\nIf this creature was kicked, it enters with three +1/+1 counters on it.",
            "Kicker {X}{U}\nIf this creature was kicked, it enters with X +1/+1 counters on it.",
            "Kicker—Sacrifice a creature.\nIf this creature was kicked, draw a card.",
        ):
            with self.subTest(text=text):
                mutated = replace(record, oracle_text=text, keywords=("Kicker",))
                ir = compile_oracle_card(
                    mutated,
                    capability_registry=self.capabilities,
                    capability_profile="commander_review",
                )
                self.assertNotEqual("exact", ir.status)
                self.assertTrue(ir.material_residuals)

    def test_kicker_dependency_and_compiler_mutations_fail_closed(self):
        for capability_id in (KICKER_CAPABILITY_ID, KICKED_ENTRY_CAPABILITY_ID):
            capability = next(
                row
                for row in self.registry_value["capabilities"]
                if row["id"] == capability_id
            )
            for blocked in (capability_id, *capability["dependencies"]):
                with self.subTest(capability=capability_id, blocked=blocked):
                    value = deepcopy(self.registry_value)
                    row = next(
                        item for item in value["capabilities"] if item["id"] == blocked
                    )
                    row["status"] = "blocked"
                    row["blockers"] = ["focused Kicker dependency mutation"]
                    ir = self.compile(capabilities=CapabilityRegistry(value))
                    relevant = next(
                        node
                        for node in ir.faces[0].nodes
                        if (
                            node.event == "cast.cost"
                            if capability_id == KICKER_CAPABILITY_ID
                            else node.event == "zone.change"
                        )
                    )
                    self.assertFalse(relevant.exact)
                    self.assertTrue(relevant.residual_ids)

        def assert_exact() -> None:
            self.assertEqual("exact", self.compile().status)

        assert_exact()
        with mock.patch.object(
            kicker_nodes_module,
            "compile_fixed_mana_kicker",
            return_value=None,
        ):
            with self.assertRaises(AssertionError):
                assert_exact()
        with mock.patch.object(
            kicker_nodes_module,
            "compile_fixed_kicked_entry",
            return_value=None,
        ):
            with self.assertRaises(AssertionError):
                assert_exact()


class FixedKickerRuntimeTests(unittest.TestCase):
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

    def session(self, seed: int, *, players: int = 4):
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

    def add_card(
        self,
        session,
        *,
        name: str,
        ref: str,
        seat: str = "B",
        zone: str = "hand",
        controller: str | None = None,
    ):
        engine = session.engine
        record = self.db.lookup(name)
        card = CardInstance(
            object_id=f"fixture:{ref}",
            ref=ref,
            oracle_id=record.oracle_id,
            printed_name=record.name,
            owner=seat,
            controller=controller or seat,
            zone=zone,
            zone_timestamp=engine.state.event_sequence + 1,
            acquired_control_turn_count=-1,
            known_to=list(engine.seats) if zone != "hand" else [seat],
            revealed_to=list(engine.seats) if zone != "hand" else [],
        )
        engine.state.cards[card.object_id] = card
        engine.state.players[seat].zones[zone].append(card.object_id)
        register_generated_programs(
            self.db,
            engine.semantics,
            (record,),
            capability_registry=self.capabilities,
            capability_profile=engine.state.config.review_profile,
            promote_exact_runtime_handlers=True,
            promote_exact_effect_programs=True,
        )
        return card

    @staticmethod
    def prepare_main(session, seat: str = "B") -> None:
        engine = session.engine
        engine.state.active_player = seat
        engine.state.started = True
        engine.state.phase = "precombat_main"
        engine.state.step = "main"
        engine.state.stack.clear()
        engine.state.priority_passes = []
        engine.state.pending_decision = None
        engine.state.priority_player = None
        engine.permissions.invalidate_current()
        engine._grant_priority(seat)
        engine.pump()

    @staticmethod
    def cast_action(engine, card, seat: str = "B"):
        return next(
            action
            for action in engine._priority_action_hints(seat)["actions"]
            if action.get("card") == card.ref and action.get("action") == "cast"
        )

    @staticmethod
    def resolve_stack_with_passes(session) -> None:
        for _ in range(12):
            if not session.engine.state.stack:
                return
            principal = session.pending_principals()[0]
            result = session.act(principal, {"action_id": "pass"})
            if not result.ok:
                raise AssertionError(result.summary)
        raise AssertionError("Kicked spell did not resolve")

    def test_kicked_cost_entry_counters_keyword_and_replay(self):
        session = self.session(70233001, players=4)
        engine = session.engine
        card = self.add_card(session, name="Kavu Titan", ref="KICK1")
        engine.state.players["B"].mana_pool.update({"C": 3, "G": 2})
        self.prepare_main(session)

        action = self.cast_action(engine, card)
        options = {row["id"]: row for row in action["cost_options"]}
        self.assertEqual({"normal", KICKER_CAST_OPTION_ID}, set(options))
        self.assertEqual(3, options[KICKER_CAST_OPTION_ID]["requirements"]["GENERIC"])
        self.assertEqual(2, options[KICKER_CAST_OPTION_ID]["requirements"]["G"])
        self.assertFalse(
            any(
                row.get("card") == card.ref
                for row in engine._priority_action_hints("A")["actions"]
            )
        )
        session.initial_checkpoint = checkpoint_envelope(engine.state)
        session.commands.clear()
        session.decisions.clear()
        result = session.act(
            "pilot:B",
            {"action_id": action["id"], "cost_option": KICKER_CAST_OPTION_ID},
        )
        self.assertTrue(result.ok, result.summary)
        self.assertEqual(KICKER_CAST_OPTION_ID, engine.state.stack[-1].context["cost_option"])
        self.resolve_stack_with_passes(session)

        self.assertEqual("battlefield", card.zone)
        self.assertEqual(3, card.counters.get("+1/+1"))
        self.assertIn("trample", engine._effective_card_data(card)["keywords"])
        self.assertNotIn("kicker_paid", card.annotations)
        for principal in ("pilot:A", "pilot:B", "pilot:C", "pilot:D"):
            packet = json.dumps(session.packet(principal, full=True), sort_keys=True)
            self.assertIn('"+1/+1": 3', packet)
            self.assertNotIn(card.object_id, packet)

        expected_hash = authoritative_state_hash(engine.state)
        with tempfile.TemporaryDirectory() as temporary:
            game_dir = Path(temporary) / "kicker-replay"
            session.save(game_dir)
            replay = replay_record(game_dir, self.db, verify=True)
        self.assertTrue(replay["ok"], replay)
        self.assertEqual(expected_hash, replay["final_state_hash"])

    def test_normal_cast_omits_kicked_entry_results(self):
        session = self.session(70233002, players=2)
        engine = session.engine
        card = self.add_card(session, name="Kavu Titan", ref="KICK2")
        engine.state.players["B"].mana_pool.update({"C": 1, "G": 1})
        self.prepare_main(session)
        action = self.cast_action(engine, card)
        normal = next(row for row in action["cost_options"] if row["id"] == "normal")
        engine.permissions.invalidate_current()
        engine._cast("B", {"card": card.ref, "cost_option": normal["id"], "pay": "auto"})
        engine.state.priority_player = None
        engine._prepare_stack_resolution()
        self.assertEqual("battlefield", card.zone)
        self.assertNotIn("+1/+1", card.counters)
        self.assertNotIn("trample", engine._effective_card_data(card)["keywords"])

    def test_kicked_counter_replacement_is_nested_and_replays(self):
        session = self.session(70233003, players=4)
        engine = session.engine
        card = self.add_card(session, name="Kavu Titan", ref="KICK3")
        self.add_card(
            session,
            name="Branching Evolution",
            ref="KICK-BRANCH",
            seat="B",
            zone="battlefield",
        )
        engine.state.players["B"].mana_pool.update({"C": 3, "G": 2})
        self.prepare_main(session)
        engine.permissions.invalidate_current()
        engine._cast(
            "B",
            {"card": card.ref, "cost_option": KICKER_CAST_OPTION_ID, "pay": "auto"},
        )
        engine.state.priority_player = None
        engine._prepare_stack_resolution()
        self.assertEqual(6, card.counters.get("+1/+1"))
        self.assertTrue(
            any(
                event.code == "replacement.apply"
                and event.details.get("object") == card.ref
                for event in engine.state.events
            )
        )

    def test_partial_card_withholds_kicker_option(self):
        session = self.session(70233004, players=2)
        engine = session.engine
        card = self.add_card(session, name="Citanul Woodreaders", ref="KICK-PARTIAL")
        # The printed kicked-entry card is now exact. Preserve a real partial
        # admission by adding an independently unsupported sibling to its record.
        record = replace(self.db.lookup("Citanul Woodreaders"),
            oracle_text=self.db.lookup("Citanul Woodreaders").oracle_text + "\nWhenever a player sneezes, draw a card.")
        for program in engine.semantics.programs_for_oracle(card.oracle_id):
            engine.semantics.remove(program.key)
        register_generated_programs(self.db, engine.semantics, (record,),
            capability_registry=self.capabilities, capability_profile=engine.state.config.review_profile,
            promote_exact_runtime_handlers=True, promote_exact_effect_programs=True)
        engine.state.players["B"].mana_pool.update({"C": 4, "G": 2})
        self.prepare_main(session)
        action = self.cast_action(engine, card)
        self.assertEqual({"normal"}, {row["id"] for row in action["cost_options"]})
        self.assertIsNone(compiled_fixed_mana_kicker_spec(engine, card))
        kicker_program = next(
            program
            for program in engine.semantics.programs_for_oracle(card.oracle_id)
            if any(
                descriptor.get("handler_id") == KICKER_COST_HANDLER_ID
                for descriptor in program.handlers
            )
        )
        self.assertEqual(
            "partial",
            kicker_program.provenance["card_program_admission"]["oracle_ir_status"],
        )

    def test_kicked_haste_consumes_existing_attack_legality(self):
        session = self.session(70233005, players=2)
        engine = session.engine
        card = self.add_card(session, name="Pouncing Kavu", ref="KICK5")
        engine.state.players["B"].mana_pool.update({"C": 3, "R": 2})
        self.prepare_main(session)
        engine.permissions.invalidate_current()
        engine._cast(
            "B",
            {"card": card.ref, "cost_option": KICKER_CAST_OPTION_ID, "pay": "auto"},
        )
        engine.state.priority_player = None
        engine._prepare_stack_resolution()
        self.assertEqual(2, card.counters.get("+1/+1"))
        self.assertTrue(has_effective_haste(engine, card))
        self.assertFalse(summoning_sickness_prohibits_attack(engine, card))

    def test_stale_kicker_contract_rolls_back(self):
        session = self.session(70233006, players=2)
        engine = session.engine
        card = self.add_card(session, name="Kavu Titan", ref="KICK6")
        engine.state.players["B"].mana_pool.update({"C": 3, "G": 2})
        self.prepare_main(session)
        proposal = build_cast_proposal(
            engine,
            CastProposalRequest.from_submission(
                "B",
                {"card": card.ref, "cost_option": KICKER_CAST_OPTION_ID, "pay": "auto"},
            ),
        )
        card.annotations["copy_overrides"] = {"name": "Changed Kicker source"}
        before = authoritative_state_hash(engine.state)
        with self.assertRaisesRegex(CastProposalError, "Kicker contract changed"):
            commit_cast(
                engine,
                proposal,
                {"card": card.ref, "cost_option": KICKER_CAST_OPTION_ID, "pay": "auto"},
            )
        self.assertEqual(before, authoritative_state_hash(engine.state))
        self.assertEqual("hand", card.zone)


if __name__ == "__main__":
    unittest.main()
