from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from common import ROOT, keep_all, make_session, pass_current
from quorune.carddb import CardDatabase
from quorune.compiler.program_generation import register_generated_programs
from quorune.errors import GameRuleError
from quorune.impulse_access import (
    ImpulseAccessRequest,
    commit_fixed_impulse_access,
    expire_temporary_play_permissions,
    prepare_fixed_impulse_access,
    resolve_fixed_impulse_access,
    temporary_play_permission_is_current,
)
from quorune.impulse_access_model import ImpulseAccessDuration
from quorune.model import CardInstance
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
from quorune.semantic_runtime import ImpulseAccessIntent
from scripts.build_test_database import build_fixture_database


REGISTRY_PATH = ROOT / "quorune" / "rules" / "capability-registry.json"


def focused_card_database(directory: str) -> CardDatabase:
    database = Path(directory) / "fixed-impulse-access.sqlite3"
    build_fixture_database(
        [
            ROOT / "tests" / "fixtures" / "scryfall-exact-lists.json",
            ROOT / "tests" / "fixtures" / "fixed-impulse-access.json",
        ],
        database,
    )
    return CardDatabase(database)


class FixedImpulseAccessCompilerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.temporary = tempfile.TemporaryDirectory()
        cls.db = focused_card_database(cls.temporary.name)
        cls.capabilities = load_default_capability_registry()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.db.close()
        cls.temporary.cleanup()

    def test_fixed_impulse_access_compiles_across_spell_trigger_and_activation(
        self,
    ) -> None:
        expected = {
            "Fixed Impulse Spark": "fixed-impulse-access-turn-v1",
            "Fixed Impulse Wave": "fixed-impulse-access-next-turn-v1",
            "Fixed Impulse Adept": "fixed-impulse-access-turn-v1",
            "Fixed Impulse Engine": "fixed-impulse-access-next-turn-v1",
        }
        for name, template_id in expected.items():
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
                    if node.template_id == template_id
                ]
                self.assertEqual("exact", ir.status)
                self.assertEqual(1, len(nodes))
                node = nodes[0]
                self.assertTrue(node.exact)
                self.assertIn(
                    "zone.impulse_access.fixed",
                    node.capability_dependencies,
                )
                self.assertEqual("fixed_impulse_access", node.effects[0]["op"])
                self.assertGreater(node.effects[0]["count"], 0)
                span = record.oracle_text[node.span.start : node.span.end]
                self.assertIn("exile the top", span.casefold())
                programs = [
                    program
                    for program in generated_programs(
                        self.db,
                        record,
                        trust_level="trusted",
                        capability_registry=self.capabilities,
                        capability_profile="commander_review",
                    )
                    if program.provenance.get("template_id") == template_id
                ]
                self.assertEqual(1, len(programs))
                self.assertTrue(programs[0].capability_closure["trusted"])

    def test_open_and_restricted_impulse_access_remain_residual(self) -> None:
        base = self.db.lookup("Fixed Impulse Spark")
        variants = (
            "Exile the top X cards of your library. You may play them this turn.",
            "Exile the top two cards of target player's library. You may play them this turn.",
            "Exile the top two cards of your library. You may cast them this turn.",
            "Exile the top two cards of your library. You may play them without paying their mana costs.",
            "Exile the top two cards of your library. You may play one of them this turn.",
            "Exile the top two cards of your library. You may play them this turn. Draw a card.",
            "During your turn, exile the top two cards of your library. You may play them this turn.",
            "Exile the top card of your library. You may play them this turn.",
        )
        for index, text in enumerate(variants, start=1):
            with self.subTest(text=text):
                ir = compile_oracle_card(
                    replace(
                        base,
                        oracle_id=(
                            f"51000000-0000-4000-8000-{100 + index:012d}"
                        ),
                        oracle_text=text,
                    ),
                    capability_registry=self.capabilities,
                    capability_profile="commander_review",
                )
                self.assertNotEqual("exact", ir.status)
                self.assertTrue(ir.material_residuals)

    def test_impulse_access_dependency_and_compiler_mutations_fail_closed(
        self,
    ) -> None:
        record = self.db.lookup("Fixed Impulse Spark")
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
        with patch(
            "quorune.oracle_ir.fixed_impulse_access_effect_template",
            return_value=None,
        ):
            ir = compile_oracle_card(
                record,
                capability_registry=self.capabilities,
                capability_profile="commander_review",
            )
        self.assertNotEqual("exact", ir.status)
        self.assertFalse(
            any(
                node.template_id
                and node.template_id.startswith("fixed-impulse-access-")
                for face in ir.faces
                for node in face.nodes
            )
        )


class FixedImpulseAccessRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.temporary = tempfile.TemporaryDirectory()
        cls.db = focused_card_database(cls.temporary.name)
        from quorune.deck import DeckLoader

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
    def tearDownClass(cls) -> None:
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

    def add_card(
        self,
        engine,
        *,
        seat: str,
        name: str,
        ref: str,
        zone: str,
    ) -> CardInstance:
        record = self.db.lookup(name)
        card = CardInstance(
            object_id=f"fixed-impulse:{ref}",
            ref=ref,
            oracle_id=record.oracle_id,
            printed_name=record.name,
            owner=seat,
            controller=seat,
            zone=zone,
            zone_timestamp=engine.state.timestamp_sequence + 1,
            known_to=[seat],
        )
        engine.state.cards[card.object_id] = card
        engine.state.players[seat].zones[zone].append(card.object_id)
        return card

    @staticmethod
    def clear_library(engine, seat: str) -> None:
        for object_id in list(engine.state.players[seat].zones["library"]):
            engine.move_card(object_id, "outside", log=False)

    @staticmethod
    def issue_main_priority(engine, seat: str = "A") -> None:
        engine.state.active_player = seat
        engine.state.phase = "precombat_main"
        engine.state.step = "main"
        engine.state.priority_passes = []
        engine.state.players[seat].land_plays_remaining = 1
        engine.permissions.invalidate_current()
        engine.state.pending_decision = None
        engine._grant_priority(seat)
        engine._issue_priority(seat)

    @staticmethod
    def legal_actions(session, seat: str = "A") -> list[dict]:
        decision = session.packet(f"pilot:{seat}", full=True)["decision"]
        return list(decision["legal_actions"])

    @staticmethod
    def resolve_top(engine) -> None:
        engine.permissions.invalidate_current()
        engine.state.pending_decision = None
        engine.state.priority_player = None
        engine._prepare_stack_resolution()

    def cast_fixed_impulse_spark(self, engine, *, ref: str) -> CardInstance:
        self.register(engine, "Fixed Impulse Spark")
        source = self.add_card(
            engine,
            seat="A",
            name="Fixed Impulse Spark",
            ref=ref,
            zone="hand",
        )
        engine.state.active_player = "A"
        engine.state.phase = "precombat_main"
        engine.state.step = "main"
        engine.state.pending_decision = None
        engine.state.priority_player = "A"
        engine.state.priority_passes = []
        engine.state.players["A"].mana_pool.update({"C": 8, "R": 2})
        engine._cast("A", {"card": source.ref})
        return source

    def test_owner_commits_top_first_and_as_many_as_possible(self) -> None:
        session = self.session(7202501)
        engine = session.engine
        self.clear_library(engine, "A")
        bottom = self.add_card(
            engine,
            seat="A",
            name="Panharmonicon",
            ref="impulse-bottom",
            zone="library",
        )
        middle = self.add_card(
            engine,
            seat="A",
            name="Island",
            ref="impulse-middle",
            zone="library",
        )
        top = self.add_card(
            engine,
            seat="A",
            name="Sol Ring",
            ref="impulse-top",
            zone="library",
        )

        result = resolve_fixed_impulse_access(
            engine,
            ImpulseAccessRequest(
                actor="A",
                player="A",
                count=5,
                duration=ImpulseAccessDuration.END_OF_TURN,
                reason="fixed impulse-access owner test",
            ),
        )

        self.assertEqual((top.ref, middle.ref, bottom.ref), result.exiled_refs)
        self.assertEqual(result.exiled_refs, result.moved_refs)
        self.assertFalse(engine.state.players["A"].zones["library"])
        self.assertEqual(["exile", "exile", "exile"], [
            top.zone,
            middle.zone,
            bottom.zone,
        ])
        self.assertTrue(
            all(
                card.annotations["temporary_play_permission"]["player"] == "A"
                for card in (top, middle, bottom)
            )
        )

    def test_impulse_stale_plan_and_malformed_intent_fail_before_mutation(
        self,
    ) -> None:
        session = self.session(7202502)
        engine = session.engine
        self.clear_library(engine, "A")
        for index, name in enumerate(("Island", "Sol Ring", "Panharmonicon")):
            self.add_card(
                engine,
                seat="A",
                name=name,
                ref=f"stale-{index}",
                zone="library",
            )
        plan = prepare_fixed_impulse_access(
            engine,
            ImpulseAccessRequest(
                "A",
                "A",
                2,
                ImpulseAccessDuration.END_OF_TURN,
                "stale fixed impulse-access plan",
            ),
        )
        library = engine.state.players["A"].zones["library"]
        library.insert(0, library.pop())
        before = authoritative_state_hash(engine.state)

        with self.assertRaisesRegex(GameRuleError, "library top changed"):
            commit_fixed_impulse_access(engine, plan)
        self.assertEqual(before, authoritative_state_hash(engine.state))
        with self.assertRaises(ValueError):
            ImpulseAccessIntent(
                "A",
                "A",
                0,
                ImpulseAccessDuration.END_OF_TURN,
                "malformed fixed impulse access",
            )
        self.assertEqual(before, authoritative_state_hash(engine.state))

    def test_compiled_impulse_access_uses_ordinary_land_and_cast_owners(
        self,
    ) -> None:
        session = self.session(7202503)
        engine = session.engine
        self.clear_library(engine, "A")
        land = self.add_card(
            engine,
            seat="A",
            name="Island",
            ref="play-exiled-land",
            zone="library",
        )
        spell = self.add_card(
            engine,
            seat="A",
            name="Panharmonicon",
            ref="cast-exiled-spell",
            zone="library",
        )
        self.cast_fixed_impulse_spark(engine, ref="compiled-impulse")
        self.resolve_top(engine)

        self.assertEqual("exile", land.zone)
        self.assertEqual("exile", spell.zone)
        self.issue_main_priority(engine)
        actions = self.legal_actions(session)
        self.assertIn(f"play-land:{land.ref}", {row["id"] for row in actions})
        self.assertIn(f"cast:{spell.ref}", {row["id"] for row in actions})

        played = session.act(
            "pilot:A",
            {"action_id": f"play-land:{land.ref}"},
        )
        self.assertTrue(played.ok, played.summary)
        self.assertEqual("battlefield", land.zone)
        self.assertEqual(0, engine.state.players["A"].land_plays_remaining)
        self.assertNotIn("temporary_play_permission", land.annotations)

        self.issue_main_priority(engine)
        cast = session.act(
            "pilot:A",
            {
                "action_id": f"cast:{spell.ref}",
                "pay": "manual",
                "payment": {"C": 4},
            },
        )
        self.assertTrue(cast.ok, cast.summary)
        self.assertEqual("stack", spell.zone)
        self.assertNotIn("temporary_play_permission", spell.annotations)

    def test_permissions_expire_at_current_or_next_extra_turn_cleanup(
        self,
    ) -> None:
        session = self.session(7202504)
        engine = session.engine
        engine.state.active_player = "A"
        engine.state.players["A"].turns_begun = 4
        self.clear_library(engine, "A")
        current = self.add_card(
            engine,
            seat="A",
            name="Island",
            ref="expires-current",
            zone="library",
        )
        resolve_fixed_impulse_access(
            engine,
            ImpulseAccessRequest(
                "A",
                "A",
                1,
                ImpulseAccessDuration.END_OF_TURN,
                "current-turn duration test",
            ),
        )
        current_permission = current.annotations["temporary_play_permission"]
        self.assertTrue(
            temporary_play_permission_is_current(
                engine.state,
                "A",
                current,
                current_permission,
            )
        )
        engine.state.phase = "ending"
        engine.state.step = "cleanup"
        engine._finish_cleanup()
        self.assertNotIn("temporary_play_permission", current.annotations)

        engine.state.active_player = "A"
        next_turn = self.add_card(
            engine,
            seat="A",
            name="Panharmonicon",
            ref="expires-next-extra",
            zone="library",
        )
        resolve_fixed_impulse_access(
            engine,
            ImpulseAccessRequest(
                "A",
                "A",
                1,
                ImpulseAccessDuration.END_OF_NEXT_TURN,
                "next-turn duration test",
            ),
        )
        expire_temporary_play_permissions(engine.state, active_player="A")
        self.assertIn("temporary_play_permission", next_turn.annotations)

        engine.state.turn_sequence += 1
        engine.state.active_player = "B"
        expire_temporary_play_permissions(engine.state, active_player="B")
        self.assertIn("temporary_play_permission", next_turn.annotations)

        engine.state.turn_sequence += 1
        engine.state.active_player = "A"
        engine.state.players["A"].turns_begun += 1
        permission = next_turn.annotations["temporary_play_permission"]
        self.assertTrue(
            temporary_play_permission_is_current(
                engine.state,
                "A",
                next_turn,
                permission,
            )
        )
        expire_temporary_play_permissions(engine.state, active_player="A")
        self.assertNotIn("temporary_play_permission", next_turn.annotations)

    def test_all_illegal_target_prevents_impulse_access(self) -> None:
        session = self.session(7202506)
        engine = session.engine
        self.clear_library(engine, "A")
        top = self.add_card(
            engine,
            seat="A",
            name="Island",
            ref="target-gated-impulse-top",
            zone="library",
        )
        spell = self.add_card(
            engine,
            seat="A",
            name="Blazing Crescendo",
            ref="target-gated-impulse",
            zone="hand",
        )
        target = self.add_card(
            engine,
            seat="B",
            name="Bloom Tender",
            ref="departing-impulse-target",
            zone="battlefield",
        )
        self.register(engine, "Blazing Crescendo")
        engine.state.players["A"].mana_pool.update({"C": 1, "R": 1})
        self.issue_main_priority(engine)
        action = next(
            row
            for row in self.legal_actions(session)
            if row["id"] == f"cast:{spell.ref}"
        )
        self.assertIn(target.ref, action["target_schema"]["legal_refs"])
        cast = session.act(
            "pilot:A",
            {
                "action_id": action["id"],
                "targets": [target.ref],
                "pay": "manual",
                "payment": {"C": 1, "R": 1},
            },
        )
        self.assertTrue(cast.ok, cast.summary)
        self.assertEqual("stack", spell.zone)

        engine.move_card(
            target.object_id,
            "graveyard",
            reason="response",
            log=False,
        )
        self.resolve_top(engine)

        self.assertFalse(engine.state.stack)
        self.assertEqual("graveyard", spell.zone)
        self.assertEqual("library", top.zone)
        self.assertNotIn("temporary_play_permission", top.annotations)

    def test_four_player_impulse_access_is_public_privacy_safe_and_replays(
        self,
    ) -> None:
        session = self.session(7202505)
        engine = session.engine
        self.clear_library(engine, "A")
        first = self.add_card(
            engine,
            seat="A",
            name="Island",
            ref="public-impulse-land",
            zone="library",
        )
        second = self.add_card(
            engine,
            seat="A",
            name="Panharmonicon",
            ref="public-impulse-spell",
            zone="library",
        )
        self.cast_fixed_impulse_spark(engine, ref="replay-impulse")
        engine._issue_priority("A")
        session.initial_checkpoint = checkpoint_envelope(engine.state)
        session.commands.clear()
        session.decisions.clear()

        while any(item.kind == "spell" for item in engine.state.stack):
            pass_current(session)

        self.assertEqual(["exile", "exile"], [first.zone, second.zone])
        for seat in engine.seats:
            snapshot = StateProjector(self.db, engine.state)._snapshot(
                f"pilot:{seat}"
            )
            serialized = json.dumps(snapshot, sort_keys=True)
            self.assertNotIn(first.object_id, serialized)
            self.assertNotIn(second.object_id, serialized)
            self.assertIn(first.ref, serialized)
            self.assertIn(second.ref, serialized)
        expected_hash = authoritative_state_hash(engine.state)
        with tempfile.TemporaryDirectory() as temporary:
            record_dir = Path(temporary) / "fixed-impulse-record"
            session.save(record_dir)
            replay = replay_record(record_dir, self.db, verify=True)
        self.assertTrue(replay["ok"], replay)
        self.assertEqual(expected_hash, replay["final_state_hash"])


if __name__ == "__main__":
    unittest.main()
