from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from common import ROOT, keep_all, make_session
from quorune.ability_fragments import StaticComponentSpec
from quorune.attachments import attach_objects
from quorune.carddb import CardDatabase, CardRecord
from quorune.continuous_effect_state import (
    commit_continuous_effect,
    expire_end_of_turn_continuous_effects,
)
from quorune.continuous_effects import (
    ContinuousEffect,
    ContinuousEffectDuration,
    ContinuousEffectOrigin,
    ContinuousObjectIdentity,
    ContinuousOperation,
    Layer,
)
from quorune.day_night import synchronize_day_night
from quorune.day_night_model import (
    DAYBOUND_TEMPLATE_ID,
    DAY_NIGHT_CAPABILITY_ID,
    NIGHTBOUND_TEMPLATE_ID,
)
from quorune.deck import DeckLoader
from quorune.model import (
    CardInstance,
    TurnEntry,
    TurnHistory,
    TurnHistoryEvent,
)
from quorune.oracle_ir import (
    compile_oracle_card,
    register_generated_programs,
)
from quorune.permanent_transform import commit_transform_batch
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
from quorune.session import CommanderSession
from quorune.spell_history_transform import (
    SPELL_HISTORY_TRANSFORM_CAPABILITY_ID,
    SPELL_HISTORY_TRANSFORM_COVERAGE,
)
from scripts.build_test_database import build_fixture_database


FIXTURE = ROOT / "tests" / "fixtures" / "spell-history-transformations-cards.json"
REGISTRY = ROOT / "quorune" / "rules" / "capability-registry.json"


def focused_database(directory: str) -> CardDatabase:
    path = Path(directory) / "spell-history-transformations.sqlite3"
    build_fixture_database(
        [
            ROOT / "tests" / "fixtures" / "scryfall-exact-lists.json",
            FIXTURE,
        ],
        path,
    )
    return CardDatabase(path)


class SpellHistoryTransformationTests(unittest.TestCase):
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
        cls.registry_value = json.loads(REGISTRY.read_text(encoding="utf-8"))

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temporary.cleanup()

    def session(self, seed: int, *, players: int = 4) -> CommanderSession:
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
        engine.state.pending_trigger_batches.clear()
        session.commands.clear()
        session.decisions.clear()
        records = (
            self.db.lookup("Sunlit Adept"),
            self.db.lookup("Village Watcher"),
            self.db.lookup("Arin of the Village"),
        )
        register_generated_programs(
            self.db,
            engine.semantics,
            records,
            trust_level="trusted",
            capability_registry=self.capabilities,
            capability_profile=engine.state.config.review_profile,
            promote_exact_runtime_handlers=True,
            promote_exact_effect_programs=True,
        )
        return session

    @staticmethod
    def add_card(
        session: CommanderSession,
        *,
        name: str,
        ref: str,
        owner: str,
        controller: str | None = None,
        zone: str = "battlefield",
        active_face: str | None = None,
    ) -> CardInstance:
        engine = session.engine
        record = engine.card_db.lookup(name)
        controller = controller or owner
        if active_face is None and zone == "battlefield" and record.faces:
            active_face = str(record.faces[0].get("name") or "") or None
        card = CardInstance(
            object_id=f"fixture:{ref}",
            ref=ref,
            oracle_id=record.oracle_id,
            printed_name=record.name,
            owner=owner,
            controller=controller,
            zone=zone,
            active_face=active_face,
            zone_timestamp=engine._next_zone_timestamp(),
            acquired_control_turn_count=-1,
            known_to=list(engine.seats),
            revealed_to=list(engine.seats),
        )
        engine.state.cards[card.object_id] = card
        zone_owner = controller if zone == "battlefield" else owner
        engine.state.players[zone_owner].zones[zone].append(card.object_id)
        return card

    @staticmethod
    def prepare_completed_turn(
        session: CommanderSession,
        *,
        sequence: int,
        active: str,
        spell_actors: tuple[str, ...] = (),
    ) -> None:
        engine = session.engine
        engine.permissions.invalidate_current()
        engine.state.pending_decision = None
        engine.state.priority_player = None
        engine.state.priority_passes = []
        engine.state.stack.clear()
        engine.state.pending_trigger_batches.clear()
        engine.state.current_turn = TurnEntry(
            turn_id=f"fixture-turn-{sequence}",
            player=active,
            created_sequence=max(0, sequence - 1),
        )
        engine.state.active_player = active
        engine.state.turn_sequence = sequence
        engine.state.phase = "ending"
        engine.state.step = "cleanup"
        engine.state.turn_history = TurnHistory(
            turn_sequence=sequence,
            events=[
                TurnHistoryEvent(kind="spell_cast", actor=actor)
                for actor in spell_actors
            ],
        )

    @staticmethod
    def begin_next_turn(session: CommanderSession, player: str) -> None:
        engine = session.engine
        engine._begin_turn(
            TurnEntry(
                turn_id=f"fixture-next-{engine.state.turn_sequence + 1}",
                player=player,
                created_sequence=engine.state.turn_sequence,
            )
        )

    @staticmethod
    def resolve_top(engine) -> None:
        engine.permissions.invalidate_current()
        engine.state.pending_decision = None
        engine.state.priority_player = None
        engine._prepare_stack_resolution()

    @staticmethod
    def pass_until_empty(session: CommanderSession, *, limit: int = 32) -> None:
        for _ in range(limit):
            if not session.state.stack and not session.state.pending_trigger_batches:
                return
            principals = session.pending_principals()
            if not principals:
                session.engine._grant_priority(session.state.active_player)
                principals = session.pending_principals()
            if not principals:
                raise AssertionError("Transformation resolution has no actor")
            result = session.act(principals[0], {"action_id": "pass"})
            if not result.ok:
                raise AssertionError(result.summary)
        raise AssertionError("Transformation resolution did not converge")

    def compile(self, record: CardRecord):
        return compile_oracle_card(
            record,
            capability_registry=self.capabilities,
            capability_profile="commander_review",
        )

    def test_compiler_lowers_paired_daybound_and_legacy_transform_nodes(self):
        bound = self.compile(self.db.lookup("Sunlit Adept"))
        self.assertEqual("exact", bound.status)
        bound_nodes = [node for face in bound.faces for node in face.nodes]
        self.assertEqual(
            {DAYBOUND_TEMPLATE_ID, NIGHTBOUND_TEMPLATE_ID},
            {
                node.template_id
                for node in bound_nodes
                if node.template_id in {DAYBOUND_TEMPLATE_ID, NIGHTBOUND_TEMPLATE_ID}
            },
        )
        self.assertTrue(
            all(
                DAY_NIGHT_CAPABILITY_ID in node.capability_dependencies
                and "current_ability_fragment_required" in node.runtime_coverage
                for node in bound_nodes
                if node.template_id in {DAYBOUND_TEMPLATE_ID, NIGHTBOUND_TEMPLATE_ID}
            )
        )

        for name in ("Village Watcher", "Arin of the Village"):
            with self.subTest(name=name):
                compiled = self.compile(self.db.lookup(name))
                self.assertEqual("exact", compiled.status)
                nodes = [node for face in compiled.faces for node in face.nodes]
                transforms = [
                    node
                    for node in nodes
                    if node.template_id
                    in {
                        "previous-turn-no-spells-self-transform-v1",
                        "previous-turn-player-two-spells-self-transform-v1",
                    }
                ]
                self.assertEqual(2, len(transforms))
                for node in transforms:
                    self.assertEqual("step.begin", node.event)
                    self.assertEqual(
                        SPELL_HISTORY_TRANSFORM_CAPABILITY_ID,
                        node.capability_dependencies[0],
                    )
                    self.assertIn(
                        SPELL_HISTORY_TRANSFORM_COVERAGE,
                        node.runtime_coverage,
                    )
                    self.assertEqual(
                        {
                            "op": "transform",
                            "card": "$source.zone_object",
                            "expected_transform_count": "$source.transform_count",
                        },
                        node.effects[0],
                    )

    def test_day_night_and_transform_grammar_exclusions_fail_closed(self):
        bound = self.db.lookup("Sunlit Adept")
        missing_back = replace(
            bound,
            faces=(
                deepcopy(bound.faces[0]),
                {
                    **deepcopy(bound.faces[1]),
                    "oracle_text": "Trample",
                },
            ),
        )
        modal = replace(bound, layout="modal_dfc")
        for record in (missing_back, modal):
            with self.subTest(record=record.layout):
                compiled = self.compile(record)
                self.assertNotEqual("exact", compiled.status)
                self.assertTrue(compiled.material_residuals)

        legacy = self.db.lookup("Village Watcher")
        malformed = replace(
            legacy,
            faces=(
                {
                    **deepcopy(legacy.faces[0]),
                    "oracle_text": (
                        "At the beginning of your upkeep, if no spells were "
                        "cast last turn, transform this creature."
                    ),
                },
                {
                    **deepcopy(legacy.faces[1]),
                    "oracle_text": (
                        "At the beginning of each upkeep, if a player cast "
                        "three or more spells last turn, convert this creature."
                    ),
                },
            ),
        )
        self.assertNotEqual("exact", self.compile(malformed).status)

        session = self.session(73101)
        engine = session.engine
        ordinary = self.add_card(
            session,
            name="Arcane Signet",
            ref="ordinary",
            owner="A",
        )
        copied = self.add_card(
            session,
            name="Village Watcher",
            ref="copied",
            owner="A",
        )
        copied.annotations["copied_from"] = "fixture"
        face_down = self.add_card(
            session,
            name="Village Watcher",
            ref="face-down",
            owner="A",
        )
        face_down.face_down = True
        phased = self.add_card(
            session,
            name="Village Watcher",
            ref="phased",
            owner="A",
        )
        phased.phased_out = True
        bound_card = self.add_card(
            session,
            name="Sunlit Adept",
            ref="bound",
            owner="A",
        )
        for card in (ordinary, copied, face_down, phased, bound_card):
            with self.subTest(card=card.ref):
                self.assertEqual(
                    (),
                    commit_transform_batch(
                        engine,
                        (card,),
                        reason="excluded transform fixture",
                        day_night_instruction=False,
                        expected_transform_counts={card.object_id: 0},
                    ),
                )

    def test_transform_capability_and_compiler_mutations_fail_closed(self):
        value = deepcopy(self.registry_value)
        capability = next(
            row
            for row in value["capabilities"]
            if row["id"] == "permanent.transform.face_change"
        )
        capability["status"] = "blocked"
        capability["blockers"] = ["focused mutation witness"]
        blocked = compile_oracle_card(
            self.db.lookup("Village Watcher"),
            capability_registry=CapabilityRegistry(value),
            capability_profile="commander_review",
        )
        self.assertNotEqual("exact", blocked.status)

        def assert_compiles() -> None:
            compiled = self.compile(self.db.lookup("Village Watcher"))
            self.assertTrue(
                any(
                    node.template_id
                    == "previous-turn-no-spells-self-transform-v1"
                    for face in compiled.faces
                    for node in face.nodes
                )
            )

        assert_compiles()
        with mock.patch(
            "quorune.oracle_ir.previous_turn_transform_trigger_node",
            return_value=None,
        ):
            with self.assertRaises(AssertionError):
                assert_compiles()

    def test_day_night_entry_untap_and_shared_component_applicability(self):
        initial_night = self.session(73120)
        nightbound = self.add_card(
            initial_night,
            name="Sunlit Adept",
            ref="initial-nightbound",
            owner="A",
            active_face="Moonlit Adept",
        )
        designation, _ = synchronize_day_night(
            initial_night.engine,
            reason="initial Nightbound fixture",
        )
        self.assertEqual("night", designation)
        self.assertEqual("Moonlit Adept", nightbound.active_face)

        simultaneous = self.session(73121)
        daybound = self.add_card(
            simultaneous,
            name="Sunlit Adept",
            ref="initial-daybound",
            owner="A",
        )
        simultaneous_nightbound = self.add_card(
            simultaneous,
            name="Sunlit Adept",
            ref="simultaneous-nightbound",
            owner="B",
            active_face="Moonlit Adept",
        )
        designation, _ = synchronize_day_night(
            simultaneous.engine,
            reason="simultaneous bound fixture",
        )
        self.assertEqual("day", designation)
        self.assertEqual("Sunlit Adept", daybound.active_face)
        self.assertEqual("Sunlit Adept", simultaneous_nightbound.active_face)

        session = self.session(73102)
        engine = session.engine
        direct = self.add_card(
            session,
            name="Sunlit Adept",
            ref="direct-bound",
            owner="B",
        )
        designation, _ = synchronize_day_night(
            engine,
            reason="initial bound fixture",
        )
        self.assertEqual("day", designation)

        entering = self.add_card(
            session,
            name="Sunlit Adept",
            ref="night-entry",
            owner="B",
            zone="hand",
        )
        engine.state.day_night = "night"
        engine.move_card(
            entering.object_id,
            "battlefield",
            controller="B",
            semantic_events=True,
        )
        self.assertEqual("Moonlit Adept", entering.active_face)
        self.assertEqual(4, engine._numeric_stat(entering.object_id, "power"))

        direct.active_face = "Sunlit Adept"
        daybound_program = next(
            program
            for program in engine.semantics.programs_for_oracle(
                direct.oracle_id,
                active_zone="battlefield",
            )
            if program.provenance.get("template_id") == DAYBOUND_TEMPLATE_ID
        )
        commit_continuous_effect(
            engine.state,
            ContinuousEffect(
                effect_id="fixture:remove-daybound",
                source_id="fixture:remove-daybound",
                layer=Layer.ABILITY,
                sublayer="6",
                timestamp=engine._next_zone_timestamp(),
                operations=(
                    ContinuousOperation(
                        "remove_ability_fragment",
                        {
                            "kind": "static_component",
                            "value": StaticComponentSpec(
                                daybound_program.key
                            ).to_dict(),
                        },
                    ),
                ),
                origin=ContinuousEffectOrigin.RESOLUTION,
                duration=ContinuousEffectDuration.UNTIL_END_OF_TURN,
                locked_objects=(
                    ContinuousObjectIdentity(
                        object_id=direct.object_id,
                        logical_object_id=direct.logical_object_id,
                    ),
                ),
            ),
        )
        synchronize_day_night(engine, reason="removed bound component")
        self.assertEqual("Sunlit Adept", direct.active_face)
        self.assertEqual(1, expire_end_of_turn_continuous_effects(engine.state))
        synchronize_day_night(engine, reason="restored bound component")
        self.assertEqual("Moonlit Adept", direct.active_face)

        direct.tapped = True
        self.prepare_completed_turn(
            session,
            sequence=4,
            active="A",
            spell_actors=("B",),
        )
        engine.state.day_night = "day"
        direct.active_face = "Sunlit Adept"
        self.begin_next_turn(session, "B")
        self.assertEqual("night", engine.state.day_night)
        self.assertEqual("Moonlit Adept", direct.active_face)
        self.assertFalse(direct.tapped)
        codes = [event.code for event in engine.state.events]
        self.assertLess(
            max(index for index, code in enumerate(codes) if code == "game.day_night"),
            max(index for index, code in enumerate(codes) if code == "permanent.untap"),
        )

        self.prepare_completed_turn(
            session,
            sequence=5,
            active="B",
            spell_actors=("B", "B", "C"),
        )
        engine.change_control(
            direct.object_id,
            "C",
            reason="untap ordering fixture",
        )
        direct.tapped = True
        self.begin_next_turn(session, "C")
        self.assertEqual("day", engine.state.day_night)
        self.assertEqual("Sunlit Adept", direct.active_face)
        self.assertFalse(direct.tapped)

    def _legacy_trigger_session(self, seed: int, ref: str):
        session = self.session(seed)
        source = self.add_card(
            session,
            name="Village Watcher",
            ref=ref,
            owner="A",
        )
        self.prepare_completed_turn(
            session,
            sequence=1,
            active="A",
        )
        self.begin_next_turn(session, "B")
        self.assertEqual(1, len(session.state.stack))
        return session, source

    def test_legacy_transform_intervening_identity_and_transform_count(self):
        session, source = self._legacy_trigger_session(73103, "stale-transform")
        engine = session.engine
        self.assertEqual(
            1,
            len(
                commit_transform_batch(
                    engine,
                    (source,),
                    reason="intervening transform fixture",
                    day_night_instruction=False,
                    expected_transform_counts={source.object_id: 0},
                )
            ),
        )
        self.assertEqual("Village Ravager", source.active_face)
        self.resolve_top(engine)
        self.assertEqual("Village Ravager", source.active_face)
        self.assertEqual(1, source.transform_count)

        absent, absent_source = self._legacy_trigger_session(
            73104,
            "absent-transform",
        )
        absent.engine.move_card(
            absent_source.object_id,
            "graveyard",
            semantic_events=True,
        )
        self.resolve_top(absent.engine)
        self.assertEqual("graveyard", absent_source.zone)

        returned, returned_source = self._legacy_trigger_session(
            73105,
            "returned-transform",
        )
        old_identity = returned_source.logical_object_id
        returned.engine.move_card(
            returned_source.object_id,
            "graveyard",
            semantic_events=True,
        )
        returned.engine.move_card(
            returned_source.object_id,
            "battlefield",
            controller="A",
            semantic_events=True,
        )
        self.assertNotEqual(old_identity, returned_source.logical_object_id)
        self.resolve_top(returned.engine)
        self.assertEqual("Village Watcher", returned_source.active_face)
        self.assertEqual(0, returned_source.transform_count)

        night_session = self.session(73107)
        night_source = self.add_card(
            night_session,
            name="Village Watcher",
            ref="night-history",
            owner="A",
            active_face="Village Ravager",
        )
        self.prepare_completed_turn(
            night_session,
            sequence=1,
            active="A",
            spell_actors=("B", "B"),
        )
        self.begin_next_turn(night_session, "C")
        self.assertEqual(1, len(night_session.state.stack))
        self.resolve_top(night_session.engine)
        self.assertEqual("Village Watcher", night_source.active_face)

        split_session = self.session(73108)
        self.add_card(
            split_session,
            name="Village Watcher",
            ref="split-history",
            owner="A",
            active_face="Village Ravager",
        )
        self.prepare_completed_turn(
            split_session,
            sequence=1,
            active="A",
            spell_actors=("B", "C"),
        )
        self.begin_next_turn(split_session, "D")
        self.assertEqual([], split_session.state.stack)

    def test_simultaneous_transform_apnap_state_and_replay(self):
        session = self.session(73106)
        engine = session.engine
        bound_a = self.add_card(
            session,
            name="Sunlit Adept",
            ref="bound-a",
            owner="A",
        )
        bound_c = self.add_card(
            session,
            name="Sunlit Adept",
            ref="bound-c",
            owner="C",
        )
        legacy_a = self.add_card(
            session,
            name="Village Watcher",
            ref="legacy-a",
            owner="A",
        )
        legacy_c = self.add_card(
            session,
            name="Village Watcher",
            ref="legacy-c",
            owner="C",
        )
        aura = self.add_card(
            session,
            name="Lightning Greaves",
            ref="attached-aura",
            owner="A",
        )
        attach_objects(
            engine.state.cards,
            aura,
            bound_a,
            source_timestamp=engine._next_zone_timestamp(),
            players=engine.state.players,
        )
        bound_a.counters["+1/+1"] = 2
        bound_a.marked_damage = 1
        timestamp = bound_a.zone_timestamp
        identity = bound_a.logical_object_id

        self.prepare_completed_turn(
            session,
            sequence=1,
            active="A",
        )
        engine.state.day_night = "day"
        with mock.patch.object(
            engine,
            "_dispatch_semantic_event",
            wraps=engine._dispatch_semantic_event,
        ) as dispatch:
            self.begin_next_turn(session, "B")
        transform_events = [
            call
            for call in dispatch.call_args_list
            if call.args and call.args[0] == "permanent.transforms"
        ]
        self.assertEqual(2, len(transform_events))
        self.assertEqual("night", engine.state.day_night)
        self.assertEqual("Moonlit Adept", bound_a.active_face)
        self.assertEqual("Moonlit Adept", bound_c.active_face)
        self.assertEqual(identity, bound_a.logical_object_id)
        self.assertEqual(timestamp, bound_a.zone_timestamp)
        self.assertEqual(2, bound_a.counters["+1/+1"])
        self.assertEqual(1, bound_a.marked_damage)
        self.assertEqual(aura.object_id, bound_a.attachments[0])
        self.assertEqual(bound_a.object_id, aura.attached_to)
        self.assertEqual(
            ["C", "A"],
            [item.controller for item in engine.state.stack],
        )
        self.assertEqual(
            {legacy_a.object_id, legacy_c.object_id},
            {item.source_object_id for item in engine.state.stack},
        )

        for seat in engine.seats:
            state = StateProjector(self.db, engine.state)._snapshot(
                f"pilot:{seat}"
            )
            self.assertEqual("night", state["game"]["day_night"])
            self.assertIn("Moonlit Adept", json.dumps(state))
            self.assertNotIn(bound_a.object_id, json.dumps(state))

        engine.pump()
        self.assertTrue(session.pending_principals())
        session.initial_checkpoint = checkpoint_envelope(engine.state)
        session.commands.clear()
        session.decisions.clear()
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary) / "spell-history-transform-record"
            session.save(directory)
            loaded = CommanderSession.load(self.db, directory)
            self.assertEqual("night", loaded.state.day_night)
            self.assertEqual(
                engine.state.turn_history.to_dict(),
                loaded.state.turn_history.to_dict(),
            )
            self.pass_until_empty(loaded)
            expected_hash = authoritative_state_hash(loaded.state)
            loaded.save(directory)
            replay = replay_record(directory, self.db, verify=True)
        self.assertTrue(replay["ok"], replay)
        self.assertEqual(expected_hash, replay["final_state_hash"])


if __name__ == "__main__":
    unittest.main()
