from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from common import ROOT, keep_all, make_session
from quorune.abilities import parse_activated_abilities
from quorune.carddb import CardDatabase, CardRecord
from quorune.compiler import activated_mana_nodes as activated_nodes_module
from quorune.deck import DeckLoader
from quorune.model import CardInstance
from quorune.oracle_ir import compile_oracle_card, register_generated_programs
from quorune.record import (
    authoritative_state_hash,
    checkpoint_envelope,
    replay_record,
)
from quorune.rules.capabilities import (
    CapabilityRegistry,
    load_default_capability_registry,
)
from quorune.self_zone_move import (
    compile_self_zone_move,
    SelfZoneMoveError,
    SelfZoneMoveIntent,
    SelfZoneMoveSpec,
    self_zone_move_handler_descriptor,
    SELF_ZONE_MOVE_ABILITY_HANDLER_ID,
    SELF_ZONE_MOVE_CAPABILITY_ID,
)
from quorune.semantic_runtime.context import (
    ReadOnlyHandlerContext,
    SemanticSourceContext,
)
from quorune.semantic_runtime.self_zone_move import (
    SelfZoneMoveAbilityHandler,
    SelfZoneMoveEffectHandler,
)
from scripts.build_test_database import build_fixture_database


REGISTRY_PATH = ROOT / "quorune" / "rules" / "capability-registry.json"
FIXTURE_PATH = ROOT / "tests" / "fixtures" / "self-zone-move-cards.json"
INTERACTION_FIXTURE_PATH = (
    ROOT / "tests" / "fixtures" / "self-zone-move-interaction-card.json"
)


def trusted_registry(value: dict | None = None) -> CapabilityRegistry:
    registry = CapabilityRegistry(
        value
        or json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
    )
    registry.mark_evidence_verified("0" * 64)
    return registry


def fixture_card(
    text: str,
    *,
    type_line: str = "Creature — Skeleton",
) -> CardRecord:
    return CardRecord(
        oracle_id="00000000-0000-4000-8000-000000701230",
        name="Self Zone Move Fixture",
        mana_cost="{1}{B}",
        mana_value=2.0,
        type_line=type_line,
        oracle_text=text,
        power="1" if "Creature" in type_line else None,
        toughness="1" if "Creature" in type_line else None,
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


def focused_card_database(directory: str) -> CardDatabase:
    database = Path(directory) / "self-zone-move.sqlite3"
    build_fixture_database(
        [
            ROOT / "tests" / "fixtures" / "scryfall-exact-lists.json",
            FIXTURE_PATH,
            INTERACTION_FIXTURE_PATH,
        ],
        database,
    )
    return CardDatabase(database)


class SelfZoneMoveModelTests(unittest.TestCase):
    def test_descriptor_and_effect_handler_are_closed_and_typed(self):
        ability = parse_activated_abilities(
            card_name="Self Zone Move Fixture",
            oracle_text=(
                "{1}{B}: Return this card from your graveyard to the "
                "battlefield tapped."
            ),
            keywords=(),
        )[0]
        spec = compile_self_zone_move(ability)
        self.assertIsNotNone(spec)
        assert spec is not None
        self.assertEqual(spec, SelfZoneMoveSpec.from_dict(spec.to_dict()))
        self.assertEqual(("graveyard",), spec.ability.zones)
        self.assertTrue(spec.requires_complete_card_program)
        descriptor = self_zone_move_handler_descriptor(spec)
        self.assertEqual(
            (spec,),
            SelfZoneMoveAbilityHandler().lower(descriptor, None),
        )

        context = ReadOnlyHandlerContext.from_sequences(
            actor="A",
            default_reason="Self-zone-move model test",
            seats=("A", "B"),
            active_seats=("A", "B"),
            apnap_order=("A", "B"),
            source=SemanticSourceContext(
                stack_ref="S1",
                object_id="object:1",
                logical_object_id="object:1@0",
                card_ref="C1",
            ),
        )
        plan = SelfZoneMoveEffectHandler().lower(spec.effect(), context)
        self.assertEqual(
            SelfZoneMoveIntent(
                actor="A",
                stack_ref="S1",
                object_id="object:1",
                card_ref="C1",
                logical_object_id="object:1@0",
                origin="graveyard",
                destination="battlefield",
                tapped=True,
                source_form="card",
            ),
            plan.intents[0],
        )

        malformed = spec.to_dict()
        malformed["tapped"] = 1
        with self.assertRaisesRegex(SelfZoneMoveError, "malformed"):
            SelfZoneMoveSpec.from_dict(malformed)
        unknown = spec.to_dict()
        unknown["unknown"] = True
        with self.assertRaisesRegex(SelfZoneMoveError, "closed shape"):
            SelfZoneMoveSpec.from_dict(unknown)
        wrong_zone = spec.to_dict()
        wrong_zone["ability"]["zones"] = ["battlefield"]
        with self.assertRaisesRegex(SelfZoneMoveError, "active zone"):
            SelfZoneMoveSpec.from_dict(wrong_zone)
        with self.assertRaisesRegex(ValueError, "operation changed"):
            SelfZoneMoveEffectHandler().lower(
                {**spec.effect(), "op": "untyped_move"},
                context,
            )


class SelfZoneMoveCompilerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.registry_value = json.loads(
            REGISTRY_PATH.read_text(encoding="utf-8")
        )
        self.capabilities = trusted_registry(self.registry_value)

    def compile(
        self,
        text: str,
        *,
        type_line: str = "Creature — Skeleton",
        capabilities=None,
    ):
        return compile_oracle_card(
            fixture_card(text, type_line=type_line),
            capability_registry=capabilities or self.capabilities,
            capability_profile="commander_review",
        )

    @staticmethod
    def self_move_node(ir):
        return next(
            node
            for face in ir.faces
            for node in face.nodes
            if "self-zone-move" in node.mechanics
        )

    def test_supported_self_zone_moves_compile_source_spanned_programs(self):
        cases = (
            (
                "{2}{B}{B}: Return this card from your graveyard to your hand.",
                "Creature — Skeleton",
                "graveyard",
                "hand",
                False,
                "card",
                False,
            ),
            (
                "{1}{B}: Return this card from your graveyard to the battlefield tapped.",
                "Creature — Skeleton",
                "graveyard",
                "battlefield",
                True,
                "card",
                True,
            ),
            (
                "{W}: Return this Aura to its owner's hand.",
                "Enchantment — Aura",
                "battlefield",
                "hand",
                False,
                "aura",
                False,
            ),
        )
        for text, type_line, origin, destination, tapped, source_form, complete in cases:
            with self.subTest(text=text):
                ir = self.compile(text, type_line=type_line)
                node = self.self_move_node(ir)
                self.assertTrue(node.exact, ir.material_residuals)
                self.assertEqual("activated_ability", node.kind)
                self.assertEqual(origin, node.active_zone)
                self.assertEqual("activated-self-zone-move-v1", node.template_id)
                self.assertEqual(
                    (SELF_ZONE_MOVE_CAPABILITY_ID,),
                    node.capability_dependencies,
                )
                self.assertEqual(text, node.text)
                self.assertEqual(
                    {
                        "op": "self_zone_move",
                        "origin": origin,
                        "destination": destination,
                        "tapped": tapped,
                        "source_form": source_form,
                    },
                    node.effects[0],
                )
                self.assertEqual(
                    complete,
                    node.handlers[0]["requires_complete_card_program"],
                )
                self.assertEqual(
                    SELF_ZONE_MOVE_ABILITY_HANDLER_ID,
                    node.handlers[0]["handler_id"],
                )

    def test_unsupported_self_zone_move_shapes_remain_residual(self):
        unsupported = (
            "{1}{B}: Return this card from your graveyard to the battlefield.",
            "{B}: Return this creature from your graveyard to your hand.",
            "{2}{B}: Return this card from a graveyard to its owner's hand.",
            "{2}{B}: Return this card and another card from your graveyard to your hand.",
        )
        for text in unsupported:
            with self.subTest(text=text):
                ir = self.compile(text)
                self.assertTrue(ir.material_residuals)
                self.assertFalse(
                    any(
                        "self-zone-move" in node.mechanics
                        for face in ir.faces
                        for node in face.nodes
                    )
                )

    def test_self_zone_move_dependency_and_compiler_mutations_fail_closed(self):
        capability = next(
            row
            for row in self.registry_value["capabilities"]
            if row["id"] == SELF_ZONE_MOVE_CAPABILITY_ID
        )
        text = (
            "{1}{B}: Return this card from your graveyard to the "
            "battlefield tapped."
        )
        for blocked in (SELF_ZONE_MOVE_CAPABILITY_ID, *capability["dependencies"]):
            with self.subTest(blocked=blocked):
                value = deepcopy(self.registry_value)
                row = next(
                    item for item in value["capabilities"] if item["id"] == blocked
                )
                row["status"] = "blocked"
                row["blockers"] = ["focused self-zone-move dependency mutation"]
                ir = self.compile(text, capabilities=trusted_registry(value))
                node = self.self_move_node(ir)
                self.assertFalse(node.exact)
                self.assertTrue(node.residual_ids)

        def assert_exact() -> None:
            ir = self.compile(text)
            nodes = [
                node
                for face in ir.faces
                for node in face.nodes
                if "self-zone-move" in node.mechanics
            ]
            self.assertEqual(1, len(nodes))
            self.assertTrue(nodes[0].exact)

        assert_exact()
        with mock.patch.object(
            activated_nodes_module,
            "compile_self_zone_move",
            return_value=None,
        ):
            with self.assertRaises(AssertionError):
                assert_exact()


class SelfZoneMoveRuntimeTests(unittest.TestCase):
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
        session.engine.permissions.invalidate_current()
        session.engine.state.pending_decision = None
        session.engine.state.priority_player = None
        session.engine.state.priority_passes = []
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
        zone: str = "graveyard",
        controller: str | None = None,
        promote_triggers: bool = False,
    ) -> CardInstance:
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
            known_to=list(engine.seats),
            revealed_to=list(engine.seats),
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
            promote_exact_trigger_programs=promote_triggers,
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
    def action_for(session, card: CardInstance, ability_id: str):
        return next(
            action
            for action in session.engine._priority_action_hints(card.owner)["actions"]
            if action.get("source") == card.ref
            and action.get("ability") == ability_id
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
        raise AssertionError("Self-zone-move activation did not resolve")

    def direct_activate(self, session, card: CardInstance, ability_id: str) -> None:
        engine = session.engine
        action = self.action_for(session, card, ability_id)
        engine.permissions.invalidate_current()
        engine._activate(card.owner, action)
        engine.state.priority_player = None
        engine._prepare_stack_resolution()

    def test_graveyard_to_hand_is_owner_scoped_private_and_replays(self):
        session = self.session(70123001, players=4)
        engine = session.engine
        card = self.add_card(
            session,
            name="Clattering Augur",
            ref="SELF-HAND",
        )
        engine.state.players["B"].mana_pool["B"] = 4
        self.prepare_main(session)
        session.initial_checkpoint = checkpoint_envelope(engine.state)
        session.commands.clear()
        session.decisions.clear()

        action = self.action_for(session, card, "ab3")
        self.assertFalse(
            any(
                row.get("source") == card.ref
                for row in engine._priority_action_hints("A")["actions"]
            )
        )
        result = session.act("pilot:B", {"action_id": action["id"]})
        self.assertTrue(result.ok, result.summary)
        self.assertEqual("graveyard", card.zone)
        self.resolve_stack_with_passes(session)

        self.assertEqual("hand", card.zone)
        self.assertIn(card.object_id, engine.state.players["B"].zones["hand"])
        for principal in ("pilot:A", "pilot:B", "pilot:C", "pilot:D"):
            packet = json.dumps(session.packet(principal, full=True), sort_keys=True)
            self.assertNotIn(card.object_id, packet)
        expected_hash = authoritative_state_hash(engine.state)
        with tempfile.TemporaryDirectory() as temporary:
            game_dir = Path(temporary) / "self-zone-move-replay"
            session.save(game_dir)
            replay = replay_record(game_dir, self.db, verify=True)
        self.assertTrue(replay["ok"], replay)
        self.assertEqual(expected_hash, replay["final_state_hash"])

    def test_tapped_reanimation_and_aura_return_use_zone_owners(self):
        session = self.session(70123002, players=2)
        engine = session.engine
        skeleton = self.add_card(
            session,
            name="Reassembling Skeleton",
            ref="SELF-BATTLEFIELD",
        )
        target = self.add_card(
            session,
            name="Clattering Augur",
            ref="SELF-AURA-TARGET",
            zone="battlefield",
        )
        aura = self.add_card(
            session,
            name="Conviction",
            ref="SELF-AURA",
            zone="battlefield",
        )
        aura.attached_to = target.object_id
        target.attachments.append(aura.object_id)
        engine.state.players["B"].mana_pool.update({"B": 2, "W": 2})
        self.prepare_main(session)

        self.direct_activate(session, skeleton, "ab1")
        self.assertEqual("battlefield", skeleton.zone)
        self.assertTrue(skeleton.tapped)
        self.prepare_main(session)
        self.direct_activate(session, aura, "ab3")
        self.assertEqual("hand", aura.zone)
        self.assertIsNone(aura.attached_to)
        self.assertNotIn(aura.object_id, target.attachments)
        self.assertIn(aura.object_id, engine.state.players["B"].zones["hand"])

    def test_partial_reanimation_and_stale_sources_fail_closed(self):
        session = self.session(70123003, players=2)
        engine = session.engine
        complete = self.add_card(
            session,
            name="Reassembling Skeleton",
            ref="SELF-COMPLETE",
        )
        partial = self.add_card(
            session,
            name="Death Tyrant",
            ref="SELF-PARTIAL",
        )
        engine.state.players["B"].mana_pool["B"] = 8
        self.prepare_main(session)
        actions = engine._priority_action_hints("B")["actions"]
        self.assertTrue(any(row.get("source") == complete.ref for row in actions))
        self.assertFalse(any(row.get("source") == partial.ref for row in actions))
        program = next(
            candidate
            for candidate in engine.semantics.programs_for_oracle(
                partial.oracle_id,
                event="activate",
            )
            if any(
                descriptor.get("handler_id") == SELF_ZONE_MOVE_ABILITY_HANDLER_ID
                for descriptor in candidate.handlers
            )
        )
        self.assertEqual(
            "partial",
            program.provenance["card_program_admission"]["oracle_ir_status"],
        )

        action = self.action_for(session, complete, "ab1")
        engine.permissions.invalidate_current()
        engine._activate("B", action)
        engine.move_card(complete.object_id, "exile", log=False)
        engine.move_card(complete.object_id, "graveyard", log=False)
        engine.state.priority_player = None
        engine._prepare_stack_resolution()
        self.assertEqual("graveyard", complete.zone)
        self.assertFalse(complete.tapped)

    def test_target_revalidation_and_self_return_coexist_on_one_card(self):
        session = self.session(70123004, players=2)
        engine = session.engine
        tymaret = self.add_card(
            session,
            name="Tymaret, the Murder King",
            ref="SELF-TYMARET",
            zone="battlefield",
        )
        first_fodder = self.add_card(
            session,
            name="Clattering Augur",
            ref="SELF-FODDER-1",
            zone="battlefield",
        )
        second_fodder = self.add_card(
            session,
            name="Clattering Augur",
            ref="SELF-FODDER-2",
            zone="battlefield",
        )
        planeswalker = self.add_card(
            session,
            name="Daretti, Scrap Savant",
            ref="SELF-PLANESWALKER",
            seat="A",
            zone="battlefield",
        )
        planeswalker.counters["loyalty"] = 3
        engine.state.players["B"].mana_pool.update({"B": 6, "R": 2})
        self.prepare_main(session)

        engine.permissions.invalidate_current()
        engine._activate(
            "B",
            {
                "source": tymaret.ref,
                "ability": "ab1",
                "targets": [planeswalker.ref],
                "cost_cards": [first_fodder.ref],
            },
        )
        self.assertEqual("graveyard", first_fodder.zone)
        engine.move_card(
            planeswalker.object_id,
            "graveyard",
            reason="Target revalidation interaction fixture",
        )
        engine.state.priority_player = None
        engine._prepare_stack_resolution()
        self.assertFalse(engine.state.stack)
        self.assertEqual("graveyard", planeswalker.zone)

        engine.move_card(
            tymaret.object_id,
            "graveyard",
            reason="Self-zone-move interaction fixture",
        )
        self.prepare_main(session)
        action = self.action_for(session, tymaret, "ab2")
        engine.permissions.invalidate_current()
        engine._activate(
            "B",
            {
                "source": action["source"],
                "ability": action["ability"],
                "cost_cards": [second_fodder.ref],
            },
        )
        engine.state.priority_player = None
        engine._prepare_stack_resolution()
        self.assertEqual("hand", tymaret.zone)
        self.assertEqual("graveyard", second_fodder.zone)

    def test_world_breaker_cast_target_and_self_return_keep_independent_state(
        self,
    ):
        session = self.session(70123005, players=2)
        engine = session.engine
        world_breaker = self.add_card(
            session,
            name="World Breaker",
            ref="SELF-WORLD-BREAKER",
            zone="hand",
            promote_triggers=True,
        )
        stale_target = self.add_card(
            session,
            name="Sol Ring",
            ref="SELF-WORLD-BREAKER-TARGET",
            seat="A",
            zone="battlefield",
        )
        cost_land = self.add_card(
            session,
            name="Island",
            ref="SELF-WORLD-BREAKER-LAND",
            zone="battlefield",
        )
        engine.state.players["B"].mana_pool.update({"C": 6, "G": 1})
        self.prepare_main(session)

        engine.permissions.invalidate_current()
        engine.state.pending_decision = None
        engine.state.priority_player = "B"
        engine._cast("B", {"card": world_breaker.ref, "pay": "auto"})
        self.assertEqual("semantic.target", engine.state.pending_decision.kind)
        target_schema = engine.state.pending_decision.payload_by_actor["B"][
            "target_schema"
        ]
        self.assertIn(stale_target.ref, target_schema["legal_refs"])
        chosen = session.act(
            "pilot:B",
            {"action_id": "choose", "targets": [stale_target.ref]},
        )
        self.assertTrue(chosen.ok, chosen.summary)
        stale_target = engine.move_card(
            stale_target.object_id,
            "graveyard",
            reason="World Breaker target revalidation fixture",
        )
        self.resolve_stack_with_passes(session)

        self.assertEqual("graveyard", stale_target.zone)
        self.assertEqual("battlefield", world_breaker.zone)
        world_breaker = engine.move_card(
            world_breaker.object_id,
            "graveyard",
            reason="World Breaker self-return fixture",
        )
        engine.state.players["B"].mana_pool["C"] = 3
        self.prepare_main(session)
        action = self.action_for(session, world_breaker, "ab4")
        engine.permissions.invalidate_current()
        engine._activate(
            "B",
            {
                "source": action["source"],
                "ability": action["ability"],
                "cost_cards": [cost_land.ref],
            },
        )
        engine.state.priority_player = None
        engine._prepare_stack_resolution()

        self.assertEqual("hand", world_breaker.zone)
        self.assertEqual("graveyard", cost_land.zone)
        self.assertEqual(
            engine.state.to_dict(),
            type(engine.state).from_dict(engine.state.to_dict()).to_dict(),
        )


if __name__ == "__main__":
    unittest.main()
