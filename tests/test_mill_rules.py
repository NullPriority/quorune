from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from common import ROOT, keep_all, make_session, pass_current
from quorune.card_programs.adapters import compile_best_available_card_program
from quorune.carddb import CardDatabase
from quorune.compiler.program_generation import register_generated_programs
from quorune.deck import DeckLoader
from quorune.errors import GameRuleError
from quorune.milling import MillRequest, commit_mill, mill_cards, prepare_mill
from quorune.model import CardInstance
from quorune.oracle_ir import compile_oracle_card, generated_programs
from quorune.projection import StateProjector
from quorune.record import authoritative_state_hash, checkpoint_envelope, replay_record
from quorune.rules.capabilities import CapabilityRegistry, load_default_capability_registry
from quorune.semantic_runtime import MillCardsIntent
from quorune.semantics import SemanticRegistry
from scripts.build_test_database import build_fixture_database


REGISTRY_PATH = ROOT / "quorune" / "rules" / "capability-registry.json"


def focused_card_database(directory: str) -> CardDatabase:
    database = Path(directory) / "mill.sqlite3"
    build_fixture_database(
        [
            ROOT / "tests" / "fixtures" / "scryfall-exact-lists.json",
            ROOT / "tests" / "fixtures" / "mill-cards.json",
        ],
        database,
    )
    return CardDatabase(database)


class FixedMillCompilerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.db = focused_card_database(cls.temporary.name)
        cls.capabilities = load_default_capability_registry()

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temporary.cleanup()

    def test_fixed_mill_compiles_across_spell_trigger_and_activation(self):
        expected = {
            "Fixed Target Mill": ["mill-fixed-target-any-v1"],
            "Fixed Opponent Mill": ["mill-fixed-target-opponent-v1"],
            "Fixed Mill Adept": ["mill-fixed-target-any-v1"],
            "Fixed Mill Device": [
                "mill-fixed-controller-v1",
                "mill-fixed-target-any-v1",
            ],
        }
        for name, templates in expected.items():
            with self.subTest(card=name):
                record = self.db.lookup(name)
                ir = compile_oracle_card(
                    record,
                    capability_registry=self.capabilities,
                    capability_profile="commander_review",
                )
                nodes = [
                    node
                    for face in ir.faces
                    for node in face.nodes
                    if node.template_id in templates
                ]
                self.assertEqual("exact", ir.status)
                self.assertEqual(templates, [node.template_id for node in nodes])
                for node in nodes:
                    self.assertTrue(node.exact)
                    self.assertIn("zone.mill.fixed", node.capability_dependencies)
                    self.assertEqual("mill", node.effects[0]["op"])
                    self.assertGreater(node.effects[0]["count"], 0)
                    span = record.oracle_text[node.span.start : node.span.end]
                    self.assertIn("mill", span.casefold())
                programs = [
                    program
                    for program in generated_programs(
                        self.db,
                        record,
                        trust_level="trusted",
                        capability_registry=self.capabilities,
                        capability_profile="commander_review",
                    )
                    if program.provenance.get("template_id") in templates
                ]
                self.assertEqual(len(templates), len(programs))
                self.assertTrue(
                    all(
                        program.capability_closure["trusted"]
                        for program in programs
                    )
                )

    def test_dynamic_optional_group_and_linked_mill_remain_residual(self):
        base = self.db.lookup("Fixed Target Mill")
        variants = (
            "Mill X cards.",
            "Each player mills a card.",
            "Target player mills half their library, rounded down.",
            "Target player mills two card.",
            "Mill a card, then draw cards equal to its mana value.",
            "Target player mills five cards, then returns one of them to hand.",
        )
        for index, text in enumerate(variants, start=1):
            with self.subTest(text=text):
                ir = compile_oracle_card(
                    replace(
                        base,
                        oracle_id=f"50000000-0000-4000-8000-{100 + index:012d}",
                        oracle_text=text,
                    ),
                    capability_registry=self.capabilities,
                    capability_profile="commander_review",
                )
                self.assertNotEqual("exact", ir.status)
                self.assertTrue(ir.material_residuals)

    def test_mill_dependency_and_compiler_mutations_fail_closed(self):
        record = self.db.lookup("Fixed Target Mill")
        for dependency_id in (
            "trigger.event.normalized_zone_change",
            "trigger.placement.apnap",
            "zone.change.destination_replacement",
        ):
            with self.subTest(dependency=dependency_id):
                value = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
                dependency = next(
                    row
                    for row in value["capabilities"]
                    if row["id"] == dependency_id
                )
                dependency["status"] = "blocked"
                dependency["blockers"] = ["test mutation"]
                ir = compile_oracle_card(
                    record,
                    capability_registry=CapabilityRegistry(value),
                    capability_profile="commander_review",
                )
                self.assertNotEqual("exact", ir.status)
                self.assertTrue(
                    any(
                        dependency_id in blocker
                        for residual in ir.material_residuals
                        for blocker in residual.blockers
                    )
                )
        with patch("quorune.oracle_ir.fixed_mill_effect_template", return_value=None):
            ir = compile_oracle_card(
                record,
                capability_registry=self.capabilities,
                capability_profile="commander_review",
            )
        self.assertNotEqual("exact", ir.status)
        self.assertFalse(
            any(
                node.template_id and node.template_id.startswith("mill-fixed-")
                for face in ir.faces
                for node in face.nodes
            )
        )

    def test_residual_mill_pairs_remain_fail_closed_at_cardprogram_boundary(self):
        base = self.db.lookup("Fixed Target Mill")
        variants = (
            (
                "conditional",
                "Target player mills five cards.\n"
                "If you control an artifact, draw a card.",
            ),
            (
                "multiple-targets",
                "Target player mills five cards.\n"
                "Up to two target players each draw a card.",
            ),
            (
                "target-predicate",
                "Target player mills five cards.\n"
                "Return target creature card from a graveyard to its owner's hand.",
            ),
        )
        for index, (label, text) in enumerate(variants, start=1):
            with self.subTest(label=label):
                record = replace(
                    base,
                    oracle_id=(
                        f"50000000-0000-4000-8000-{200 + index:012d}"
                    ),
                    oracle_text=text,
                )
                ir = compile_oracle_card(
                    record,
                    capability_registry=self.capabilities,
                    capability_profile="commander_review",
                )
                self.assertTrue(
                    any(
                        "zone.mill.fixed" in node.capability_dependencies
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
                self.assertFalse(program.abilities)


class FixedMillRuntimeTests(unittest.TestCase):
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

    def add_card(self, engine, *, seat: str, name: str, ref: str, zone: str):
        record = self.db.lookup(name)
        card = CardInstance(
            object_id=f"fixture:{ref}",
            ref=ref,
            oracle_id=record.oracle_id,
            printed_name=record.name,
            owner=seat,
            controller=seat,
            zone=zone,
            zone_timestamp=engine.state.event_sequence + 1,
            known_to=[seat],
        )
        engine.state.cards[card.object_id] = card
        engine.state.players[seat].zones[zone].append(card.object_id)
        return card

    @staticmethod
    def isolate_library(engine, seat: str, count: int):
        library = engine.state.players[seat].zones["library"]
        keep = set(library[-count:])
        for object_id in list(library):
            if object_id not in keep:
                engine.move_card(object_id, "outside", log=False)
        return tuple(
            engine.state.cards[object_id]
            for object_id in reversed(
                engine.state.players[seat].zones["library"]
            )
        )

    def cast_target_mill(self, engine, *, target: str = "B"):
        self.register(engine, "Fixed Target Mill")
        spell = self.add_card(
            engine,
            seat="A",
            name="Fixed Target Mill",
            ref=f"fixed-mill-{engine.state.config.seed}",
            zone="hand",
        )
        engine.state.active_player = "A"
        engine.state.phase = "precombat_main"
        engine.state.step = "main"
        engine.state.priority_player = "A"
        engine.state.players["A"].mana_pool["U"] = 1
        engine._cast("A", {"card": spell.ref, "targets": [target]})
        return spell

    def add_exile_replacement(self, engine):
        name = "Mill Exile Replacement"
        self.register(engine, name)
        return self.add_card(
            engine,
            seat="A",
            name=name,
            ref=f"mill-replacement-{engine.state.config.seed}",
            zone="battlefield",
        )

    @staticmethod
    def resolve_top(engine):
        engine.permissions.invalidate_current()
        engine.state.pending_decision = None
        engine.state.priority_player = None
        engine._prepare_stack_resolution()

    def test_mill_commits_top_first_and_as_many_as_possible(self):
        session = self.session(7011701)
        engine = session.engine
        top_first = self.isolate_library(engine, "B", 3)

        result = mill_cards(
            engine,
            MillRequest(
                actor="A",
                player="B",
                count=5,
                reason="fixed Mill owner test",
            ),
        )

        self.assertEqual(3, result.actual_count)
        self.assertEqual(
            tuple(card.ref for card in top_first),
            result.refs,
        )
        self.assertFalse(engine.state.players["B"].zones["library"])
        self.assertEqual(
            [card.object_id for card in top_first],
            engine.state.players["B"].zones["graveyard"][-3:],
        )
        event = next(
            event
            for event in reversed(engine.state.events)
            if event.code == "card.mill"
        )
        self.assertEqual(3, event.details["count"])
        self.assertEqual(list(result.refs), event.details["objects"])

    def test_stale_plan_and_malformed_intent_fail_before_mutation(self):
        session = self.session(7011702)
        engine = session.engine
        self.isolate_library(engine, "B", 3)
        plan = prepare_mill(
            engine,
            MillRequest("A", "B", 2, "stale fixed Mill plan"),
        )
        top_id = engine.state.players["B"].zones["library"][-1]
        engine.move_card(top_id, "library", position="bottom", log=False)
        before = authoritative_state_hash(engine.state)

        with self.assertRaisesRegex(GameRuleError, "library top changed"):
            commit_mill(engine, plan)
        self.assertEqual(before, authoritative_state_hash(engine.state))
        with self.assertRaises(ValueError):
            MillCardsIntent("A", "B", 0, "malformed fixed Mill")
        self.assertEqual(before, authoritative_state_hash(engine.state))

    def test_target_mill_revalidates_and_zone_replacement_preserves_actual_result(self):
        stale_session = self.session(7011703)
        stale_engine = stale_session.engine
        stale_top = self.isolate_library(stale_engine, "B", 2)
        self.cast_target_mill(stale_engine)
        stale_engine.state.players["B"].in_game = False
        stale_engine.permissions.invalidate_current()
        stale_engine.state.pending_decision = None
        stale_engine.state.priority_player = None
        stale_engine._prepare_stack_resolution()
        self.assertEqual(
            ["library", "library"],
            [card.zone for card in stale_top],
        )

        session = self.session(7011713)
        engine = session.engine
        top_first = self.isolate_library(engine, "B", 2)
        self.add_exile_replacement(engine)
        self.cast_target_mill(engine)
        engine.permissions.invalidate_current()
        engine.state.pending_decision = None
        engine.state.priority_player = None
        engine._prepare_stack_resolution()

        self.assertEqual(["exile", "exile"], [card.zone for card in top_first])
        self.assertTrue(all(card.counters["void"] == 1 for card in top_first))
        self.assertFalse(engine.state.players["B"].zones["graveyard"])
        event = next(
            event
            for event in reversed(engine.state.events)
            if event.code == "card.mill"
        )
        self.assertEqual([card.ref for card in top_first], event.details["objects"])

    def test_aura_attachment_and_triggered_mill_compose(self):
        session = self.session(7011705)
        engine = session.engine
        self.register(engine, "Fixed Mill Aura")
        aura = self.add_card(
            engine,
            seat="A",
            name="Fixed Mill Aura",
            ref="fixed-mill-aura",
            zone="hand",
        )
        target_ref = engine.create_token(
            "A",
            name="Fixed Mill Aura Target",
            characteristics={
                "type_line": "Token Creature — Test",
                "power": "2",
                "toughness": "2",
            },
        )[0]
        target = next(
            card for card in engine.state.cards.values() if card.ref == target_ref
        )
        top_first = self.isolate_library(engine, "A", 2)
        engine.state.active_player = "A"
        engine.state.phase = "precombat_main"
        engine.state.step = "main"
        engine.state.priority_player = "A"
        engine.state.players["A"].mana_pool["U"] = 2
        engine._cast("A", {"card": aura.ref, "targets": [target.ref]})

        self.resolve_top(engine)
        self.assertEqual(target.object_id, aura.attached_to)
        self.assertIn(aura.object_id, target.attachments)
        self.assertTrue(engine.state.stack)
        self.resolve_top(engine)

        self.assertEqual(["graveyard", "graveyard"], [c.zone for c in top_first])
        self.assertEqual(target.object_id, aura.attached_to)

    def test_hexproof_target_legality_and_activated_mill_compose(self):
        session = self.session(7011706)
        engine = session.engine
        self.register(engine, "Fixed Mill Hexproofer")
        source = self.add_card(
            engine,
            seat="A",
            name="Fixed Mill Hexproofer",
            ref="fixed-mill-hexproofer",
            zone="battlefield",
        )
        opposing_ref = engine.create_token(
            "B",
            name="Opposing Target Source",
            characteristics={"type_line": "Token Artifact"},
        )[0]
        schema = engine._public_target_schema(
            "B",
            {
                "zones": ["battlefield"],
                "categories": ["permanent"],
                "count": 1,
            },
            source_ref=opposing_ref,
        )
        self.assertIsNotNone(schema)
        self.assertNotIn(source.ref, schema["legal_refs"])
        top_first = self.isolate_library(engine, "B", 2)
        engine.state.active_player = "A"
        engine.state.phase = "precombat_main"
        engine.state.step = "main"
        engine.state.priority_player = "A"
        engine.state.players["A"].turns_begun = 1
        source.acquired_control_turn_count = 0
        ability = engine._activated_abilities(source)[0]
        engine._activate(
            "A",
            {
                "source": source.ref,
                "ability": ability.ability_id,
                "targets": ["B"],
            },
        )

        self.resolve_top(engine)

        self.assertEqual(["graveyard", "graveyard"], [c.zone for c in top_first])
        self.assertTrue(source.tapped)

    def test_four_player_target_mill_is_public_privacy_safe_and_replays(self):
        session = self.session(7011704)
        engine = session.engine
        top_first = self.isolate_library(engine, "B", 2)
        self.add_exile_replacement(engine)
        self.cast_target_mill(engine)
        engine._issue_priority("A")
        session.initial_checkpoint = checkpoint_envelope(engine.state)
        session.commands.clear()
        session.decisions.clear()

        while any(item.kind == "spell" for item in engine.state.stack):
            pass_current(session)

        self.assertEqual(["exile", "exile"], [card.zone for card in top_first])
        for seat in engine.seats:
            snapshot = StateProjector(self.db, engine.state)._snapshot(
                f"pilot:{seat}"
            )
            serialized = json.dumps(snapshot, sort_keys=True)
            self.assertNotIn(top_first[0].object_id, serialized)
            self.assertNotIn(top_first[1].object_id, serialized)
            self.assertIn(top_first[0].ref, serialized)
            self.assertIn(top_first[1].ref, serialized)
        expected_hash = authoritative_state_hash(engine.state)
        with tempfile.TemporaryDirectory() as temporary:
            record_dir = Path(temporary) / "fixed-mill-record"
            session.save(record_dir)
            replay = replay_record(record_dir, self.db, verify=True)
        self.assertTrue(replay["ok"], replay)
        self.assertEqual(expected_hash, replay["final_state_hash"])


if __name__ == "__main__":
    unittest.main()
