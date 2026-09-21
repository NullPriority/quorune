from __future__ import annotations

from pathlib import Path
import tempfile
import unittest
from unittest import mock

from common import keep_all, load_assets, make_session
from quorune.ability_fragments import StaticComponentSpec
from quorune.card_programs import compile_card_program
from quorune.carddb import CardRecord
from quorune.compiler.action_permission_templates import (
    static_action_permission_handler,
)
from quorune.continuous_effect_state import commit_continuous_effect
from quorune.continuous_effects import (
    ContinuousEffect,
    ContinuousEffectDuration,
    ContinuousEffectOrigin,
    ContinuousObjectIdentity,
    ContinuousOperation,
    Layer,
)
from quorune.model import CardInstance
from quorune.oracle_ir import register_generated_programs
from quorune.record import checkpoint_envelope, replay_record
from quorune.rules.land_play_permissions import (
    reset_additional_land_play_permissions,
)
from quorune.rules.capabilities import load_default_capability_registry
from quorune.semantic_runtime.action_permissions import (
    ACTION_PERMISSION_EVENT,
    ACTIVATE_CONTROLLED_CREATURE_AS_HASTE_HANDLER_ID,
    ADDITIONAL_LAND_PLAY_HANDLER_ID,
    LIBRARY_TOP_ACTION_HANDLER_ID,
    LIBRARY_TOP_VISIBILITY_HANDLER_ID,
    LAND_PLAY_FROM_OWN_GRAVEYARD_HANDLER_ID,
    LAND_PLAY_ACCOUNTING_STAT,
    USED_ADDITIONAL_LAND_PLAY_SLOTS_STAT,
    ActionPermissionKind,
    additional_land_play_permission_slots,
    controller_action_permissions,
    default_action_permission_registry,
    land_play_accounting,
    land_play_permission_options,
)
from quorune.semantic_runtime.context import SemanticNodeError


class _NoRulingsDatabase:
    @staticmethod
    def rulings(record):
        del record
        return ()


def _permanent(
    text: str,
    *,
    suffix: int,
    name: str = "Action Permission Fixture",
    faces: tuple[dict[str, object], ...] = (),
) -> CardRecord:
    return CardRecord(
        oracle_id=f"00000000-0000-4000-8000-{suffix:012d}",
        name=name,
        mana_cost="{3}",
        mana_value=3.0,
        type_line="Artifact",
        oracle_text=text,
        power=None,
        toughness=None,
        loyalty=None,
        defense=None,
        colors=(),
        color_identity=(),
        keywords=(),
        produced_mana=(),
        layout="transform" if faces else "normal",
        released_at="2026-01-01",
        legalities={"commander": "legal"},
        faces=faces,
        raw={},
    )


class StaticActionPermissionCompilerTests(unittest.TestCase):
    def setUp(self):
        self.capabilities = load_default_capability_registry()

    def compile(self, record: CardRecord, *, trust_level: str = "trusted"):
        return compile_card_program(
            _NoRulingsDatabase(),
            record,
            capability_registry=self.capabilities,
            capability_profile="commander_review",
            trust_level=trust_level,
        )

    def test_closed_permission_sentences_compile_face_pinned_typed_programs(
        self,
    ):
        cases = (
            (
                "You may play lands from your graveyard.",
                LAND_PLAY_FROM_OWN_GRAVEYARD_HANDLER_ID,
                "land.play.from_own_graveyard",
            ),
            (
                "You may activate abilities of creatures you control as "
                "though those creatures had haste.",
                ACTIVATE_CONTROLLED_CREATURE_AS_HASTE_HANDLER_ID,
                "activation.permission.controlled_creature_as_haste",
            ),
        )
        for index, (text, handler_id, capability_id) in enumerate(cases, 1):
            with self.subTest(text=text):
                card_program = self.compile(
                    _permanent(text, suffix=116_300_000 + index)
                )
                self.assertEqual((), card_program.residuals)
                ability = next(
                    ability
                    for ability in card_program.abilities
                    if any(
                        descriptor.get("handler_id") == handler_id
                        for descriptor in ability.handlers
                    )
                )
                self.assertEqual("battlefield", ability.active_zone)
                self.assertEqual(ACTION_PERMISSION_EVENT, ability.event)
                self.assertEqual("front", ability.provenance["face_id"])
                self.assertEqual(1, ability.provenance["source_span"]["line"])
                self.assertEqual(
                    len(text),
                    ability.provenance["source_span"]["end"]
                    - ability.provenance["source_span"]["start"],
                )
                self.assertIn(capability_id, ability.capability_dependencies)

        back_text = cases[0][0]
        two_face = _permanent(
            f"Front text\n//\n{back_text}",
            suffix=116_300_003,
            name="Permission Front // Permission Back",
            faces=(
                {
                    "name": "Permission Front",
                    "mana_cost": "{3}",
                    "type_line": "Artifact",
                    "oracle_text": "Front text",
                    "keywords": [],
                },
                {
                    "name": "Permission Back",
                    "mana_cost": "",
                    "type_line": "Artifact",
                    "oracle_text": back_text,
                    "keywords": [],
                },
            ),
        )
        permission = next(
            ability
            for ability in self.compile(two_face).abilities
            if any(
                descriptor.get("handler_id")
                == LAND_PLAY_FROM_OWN_GRAVEYARD_HANDLER_ID
                for descriptor in ability.handlers
            )
        )
        self.assertEqual("Permission Back", permission.provenance["face_id"])

    def test_unsupported_permission_wording_and_malformed_descriptors_fail_closed(
        self,
    ):
        unsupported = (
            "You may play an additional land from your graveyard.",
            "You may play lands from graveyards.",
            "You may activate abilities of creatures your opponents control "
            "as though those creatures had haste.",
        )
        for index, text in enumerate(unsupported, 1):
            with self.subTest(text=text):
                card_program = self.compile(
                    _permanent(text, suffix=116_301_000 + index),
                    trust_level="provisional",
                )
                self.assertTrue(card_program.residuals)
                self.assertIn(
                    text,
                    {row["text"] for row in card_program.residuals},
                )
                self.assertFalse(
                    any(
                        ability.event == ACTION_PERMISSION_EVENT
                        for ability in card_program.abilities
                    )
                )

        compiled = static_action_permission_handler(
            "You may play lands from your graveyard."
        )
        self.assertIsNotNone(compiled)
        registry = default_action_permission_registry()
        descriptor = compiled[1]
        registry.validate(descriptor)
        malformed_values = (
            {**descriptor, "unknown": True},
            {**descriptor, "schema_version": True},
            {**descriptor, "schema_version": 2},
            {**descriptor, "event": "continuous"},
            {
                **descriptor,
                "permission": (
                    ActionPermissionKind.ACTIVATE_CONTROLLED_CREATURE_AS_HASTE.value
                ),
            },
        )
        for malformed in malformed_values:
            with self.subTest(malformed=malformed):
                with self.assertRaises(SemanticNodeError):
                    registry.validate(malformed)

    def test_action_permission_compiler_mutant_is_killed(self):
        records = (
            _permanent(
                "You may play lands from your graveyard.",
                suffix=116_302_001,
            ),
            _permanent(
                "You may activate abilities of creatures you control as "
                "though those creatures had haste.",
                suffix=116_302_002,
            ),
        )

        def assert_compiler_boundary() -> None:
            for record in records:
                card_program = self.compile(
                    record,
                    trust_level="provisional",
                )
                self.assertTrue(
                    any(
                        ability.event == ACTION_PERMISSION_EVENT
                        for ability in card_program.abilities
                    )
                )

        assert_compiler_boundary()
        with mock.patch(
            "quorune.compiler.runtime_templates."
            "static_action_permission_handler",
            return_value=None,
        ):
            with self.assertRaises(AssertionError):
                assert_compiler_boundary()


class PublicLibraryActionPermissionCompilerTests(unittest.TestCase):
    def setUp(self):
        self.capabilities = load_default_capability_registry()

    def compile(self, record: CardRecord, *, trust_level: str = "trusted"):
        return compile_card_program(
            _NoRulingsDatabase(),
            record,
            capability_registry=self.capabilities,
            capability_profile="commander_review",
            trust_level=trust_level,
        )

    def test_public_library_permission_contract_matrix(self):
        cases = (
            (
                "You may look at the top card of your library any time.",
                LIBRARY_TOP_VISIBILITY_HANDLER_ID,
            ),
            (
                "Players play with the top card of their libraries revealed.",
                LIBRARY_TOP_VISIBILITY_HANDLER_ID,
            ),
            (
                "You may play lands and cast creature spells from the top "
                "of your library.",
                LIBRARY_TOP_ACTION_HANDLER_ID,
            ),
            (
                "You may cast Cleric, Rogue, Warrior, and Wizard spells "
                "from the top of your library.",
                LIBRARY_TOP_ACTION_HANDLER_ID,
            ),
            (
                "You may cast Spider spells and noncreature spells from "
                "the top of your library.",
                LIBRARY_TOP_ACTION_HANDLER_ID,
            ),
            (
                "You may play an additional land on each of your turns.",
                ADDITIONAL_LAND_PLAY_HANDLER_ID,
            ),
            (
                "You may play two additional lands on each of your turns.",
                ADDITIONAL_LAND_PLAY_HANDLER_ID,
            ),
        )
        for index, (text, handler_id) in enumerate(cases, 1):
            with self.subTest(text=text):
                card_program = self.compile(
                    _permanent(text, suffix=116_310_000 + index)
                )
                self.assertEqual((), card_program.residuals)
                program = next(
                    ability
                    for ability in card_program.abilities
                    if any(
                        descriptor.get("handler_id") == handler_id
                        for descriptor in ability.handlers
                    )
                )
                self.assertIn(
                    "current_ability_fragment_required",
                    program.coverage,
                )

    def test_public_library_permission_grammar_and_descriptors_fail_closed(self):
        unsupported = (
            "You may look at the top two cards of your library any time.",
            "You may cast the named spell from the top of your library.",
            "You may cast spells from an opponent's library.",
            "You may play X additional lands on each of your turns.",
        )
        for index, text in enumerate(unsupported, 1):
            with self.subTest(text=text):
                program = self.compile(
                    _permanent(text, suffix=116_311_000 + index),
                    trust_level="provisional",
                )
                self.assertTrue(program.residuals)

        registry = default_action_permission_registry()
        compiled = static_action_permission_handler(
            "You may play an additional land on each of your turns."
        )
        assert compiled is not None
        descriptor = compiled[1]
        for value in (
            {**descriptor, "amount": True},
            {**descriptor, "amount": 3},
            {**descriptor, "unknown": True},
        ):
            with self.subTest(value=value):
                with self.assertRaises(SemanticNodeError):
                    registry.validate(value)

        visibility = static_action_permission_handler(
            "You may look at the top card of your library any time."
        )
        action = static_action_permission_handler(
            "You may play lands and cast creature spells from the top "
            "of your library."
        )
        assert visibility is not None and action is not None
        malformed_action = dict(action[1])
        malformed_action["spell_queries"] = [
            {
                **malformed_action["spell_queries"][0],
                "excluded_types": [],
            }
        ]
        malformed_land = dict(action[1])
        malformed_land["land_queries"] = [
            {
                **malformed_land["land_queries"][0],
                "types_all": [],
            }
        ]
        for value in (
            {**visibility[1], "schema_version": True},
            {**action[1], "schema_version": True},
            malformed_action,
            malformed_land,
        ):
            with self.subTest(value=value):
                with self.assertRaises(SemanticNodeError):
                    registry.validate(value)

    def test_public_library_permission_compiler_mutant_is_killed(self):
        record = _permanent(
            "You may play lands and cast creature spells from the top "
            "of your library.",
            suffix=116_312_001,
        )

        def assert_exact() -> None:
            self.assertEqual((), self.compile(record).residuals)

        assert_exact()
        with mock.patch(
            "quorune.compiler.runtime_templates."
            "static_action_permission_handler",
            return_value=None,
        ):
            with self.assertRaises((AssertionError, ValueError)):
                assert_exact()


class StaticActionPermissionRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db, cls.mishra, cls.zimone = load_assets()
        cls.capabilities = load_default_capability_registry()

    @classmethod
    def tearDownClass(cls):
        cls.db.close()

    def session(self, seed: int):
        session = make_session(
            self.db,
            self.mishra,
            self.zimone,
            players=4,
            seed=seed,
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
    def card(engine, owner: str, name: str):
        return next(
            card
            for card in engine.state.cards.values()
            if card.owner == owner and card.printed_name == name
        )

    def add_permanent(
        self,
        session,
        *,
        seat: str,
        name: str,
        ref: str,
    ) -> CardInstance:
        engine = session.engine
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
            acquired_control_turn_count=-1,
            known_to=list(engine.seats),
            revealed_to=list(engine.seats),
        )
        engine.state.cards[card.object_id] = card
        engine.state.players[seat].zones["battlefield"].append(card.object_id)
        register_generated_programs(
            self.db,
            engine.semantics,
            (record,),
            trust_level="provisional",
            capability_registry=self.capabilities,
            capability_profile=engine.state.config.review_profile,
            promote_exact_runtime_handlers=True,
            promote_exact_capability_declarations=True,
        )
        return card

    @staticmethod
    def prepare_main(session, seat: str) -> None:
        engine = session.engine
        engine.permissions.invalidate_current()
        engine.state.pending_decision = None
        engine.state.priority_player = None
        engine.state.priority_passes = []
        engine.state.active_player = seat
        engine.state.phase = "precombat_main"
        engine.state.step = "main"
        engine.state.started = True
        engine._grant_priority(seat)
        engine.pump()

    def test_land_play_permission_is_typed_active_and_seat_scoped(self):
        session = self.session(116_303_001)
        engine = session.engine
        crucible = self.add_permanent(
            session,
            seat="B",
            name="Crucible of Worlds",
            ref="B-crucible",
        )
        land = self.card(engine, "B", "Island")
        engine.move_card(land.object_id, "graveyard", log=False)
        engine.state.players["B"].land_plays_remaining = 1
        self.prepare_main(session, "B")

        permissions = controller_action_permissions(engine, "B")
        self.assertEqual(
            [ActionPermissionKind.LAND_PLAY_FROM_OWN_GRAVEYARD],
            [permission.kind for permission in permissions],
        )
        self.assertIn(land.ref, engine._priority_action_hints("B")["lands"])
        self.assertFalse(engine._compiled_land_play_permission("A", land))

        crucible.phased_out = True
        self.assertFalse(engine._compiled_land_play_permission("B", land))
        crucible.phased_out = False
        engine._play_land("B", {"card": land.ref, "from": "graveyard"})
        self.assertEqual("battlefield", land.zone)

    def test_creature_activation_permission_is_typed_active_and_seat_scoped(
        self,
    ):
        session = self.session(116_303_002)
        engine = session.engine
        elixir = self.add_permanent(
            session,
            seat="B",
            name="Thousand-Year Elixir",
            ref="B-elixir",
        )
        bird = self.card(engine, "B", "Birds of Paradise")
        engine.move_card(bird.object_id, "battlefield", controller="B", log=False)
        bird.acquired_control_turn_count = engine.state.players["B"].turns_begun
        ability = engine._activated_abilities(bird)[0]

        self.assertTrue(engine._may_activate_creature_as_haste("B", bird))
        self.assertFalse(engine._may_activate_creature_as_haste("A", bird))
        self.assertEqual(
            ("payable", None),
            engine._ability_availability("B", bird, ability),
        )

        elixir.phased_out = True
        self.assertFalse(engine._may_activate_creature_as_haste("B", bird))
        self.assertNotEqual(
            "payable",
            engine._ability_availability("B", bird, ability)[0],
        )

    def test_raw_oracle_text_without_typed_permission_fails_closed(self):
        session = self.session(116_303_003)
        engine = session.engine
        crucible = self.add_permanent(
            session,
            seat="B",
            name="Crucible of Worlds",
            ref="B-raw-crucible",
        )
        elixir = self.add_permanent(
            session,
            seat="B",
            name="Thousand-Year Elixir",
            ref="B-raw-elixir",
        )
        land = self.card(engine, "B", "Island")
        bird = self.card(engine, "B", "Birds of Paradise")
        engine.move_card(land.object_id, "graveyard", log=False)
        engine.move_card(bird.object_id, "battlefield", controller="B", log=False)

        self.assertIn(
            "play lands from your graveyard",
            self.db.lookup(crucible.printed_name).oracle_text.casefold(),
        )
        self.assertIn(
            "as though those creatures had haste",
            self.db.lookup(elixir.printed_name).oracle_text.casefold(),
        )
        for source in (crucible, elixir):
            for program in tuple(
                engine.semantics.runtime_handler_programs_for_oracle(
                    source.oracle_id,
                    active_zone="battlefield",
                    event=ACTION_PERMISSION_EVENT,
                )
            ):
                engine.semantics.remove(program.key)

        self.assertFalse(engine._compiled_land_play_permission("B", land))
        self.assertFalse(engine._may_activate_creature_as_haste("B", bird))

    def test_permission_offers_are_principal_scoped(self):
        session = self.session(116_303_005)
        engine = session.engine
        self.add_permanent(
            session,
            seat="B",
            name="Crucible of Worlds",
            ref="B-private-crucible",
        )
        self.add_permanent(
            session,
            seat="B",
            name="Thousand-Year Elixir",
            ref="B-private-elixir",
        )
        land = self.card(engine, "B", "Island")
        bird = self.card(engine, "B", "Birds of Paradise")
        engine.move_card(land.object_id, "graveyard", log=False)
        engine.move_card(bird.object_id, "battlefield", controller="B", log=False)
        bird.acquired_control_turn_count = engine.state.players["B"].turns_begun
        engine.state.players["B"].land_plays_remaining = 1
        self.prepare_main(session, "B")

        owner_decision = session.packet("pilot:B", full=True)["decision"]
        opposing_decision = session.packet("pilot:A", full=True)["decision"]
        owner_actions = owner_decision["ctx"]["legal"]["actions"]
        opposing_actions = (
            opposing_decision.get("ctx", {}).get("legal", {}).get("actions", [])
            if isinstance(opposing_decision, dict)
            else []
        )
        self.assertIn(
            land.ref,
            {
                action.get("card")
                for action in owner_actions
                if action.get("kind") == "play_land"
            },
        )
        self.assertIn(
            bird.ref,
            {
                action.get("source")
                for action in owner_actions
                if action.get("kind") == "activate"
            },
        )
        self.assertNotIn(
            land.ref,
            {action.get("card") for action in opposing_actions},
        )
        self.assertNotIn(
            bird.ref,
            {action.get("source") for action in opposing_actions},
        )

    def test_land_play_from_graveyard_replays_exactly(self):
        session = self.session(116_303_004)
        engine = session.engine
        self.add_permanent(
            session,
            seat="B",
            name="Crucible of Worlds",
            ref="B-replay-crucible",
        )
        land = self.card(engine, "B", "Island")
        engine.move_card(land.object_id, "graveyard", log=False)
        engine.state.players["B"].land_plays_remaining = 1
        self.prepare_main(session, "B")
        session.initial_checkpoint = checkpoint_envelope(session.state)
        session.commands.clear()
        session.decisions.clear()

        action = next(
            action
            for action in engine._priority_action_hints("B")["actions"]
            if action.get("kind") == "play_land"
            and action.get("card") == land.ref
        )
        result = session.act(
            "pilot:B",
            {"action_id": action["id"]},
        )
        self.assertTrue(result.ok, result.summary)
        self.assertEqual("battlefield", land.zone)

        with tempfile.TemporaryDirectory() as temporary:
            record_dir = Path(temporary) / "graveyard-land-play-replay"
            session.save(record_dir)
            replay = replay_record(record_dir, self.db, verify=True)
        self.assertTrue(replay["ok"], replay)


class PublicLibraryActionPermissionRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db, cls.mishra, cls.zimone = load_assets()
        cls.capabilities = load_default_capability_registry()

    @classmethod
    def tearDownClass(cls):
        cls.db.close()

    def session(self, seed: int):
        session = make_session(
            self.db,
            self.mishra,
            self.zimone,
            players=4,
            seed=seed,
        )
        keep_all(session)
        session.engine.permissions.invalidate_current()
        session.state.pending_decision = None
        session.state.priority_player = None
        session.state.priority_passes = []
        session.commands.clear()
        session.decisions.clear()
        return session

    def add_card(
        self,
        session,
        *,
        seat: str,
        name: str,
        ref: str,
        zone: str,
    ) -> CardInstance:
        engine = session.engine
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
            acquired_control_turn_count=-1,
            known_to=[],
            revealed_to=[],
        )
        engine.state.cards[card.object_id] = card
        engine.state.players[seat].zones[zone].append(card.object_id)
        register_generated_programs(
            self.db,
            engine.semantics,
            (record,),
            trust_level="provisional",
            capability_registry=self.capabilities,
            capability_profile=engine.state.config.review_profile,
            promote_exact_runtime_handlers=True,
            promote_exact_capability_declarations=True,
        )
        return card

    def prepare_main(self, session, seat: str) -> None:
        engine = session.engine
        engine.state.active_player = seat
        engine.state.phase = "precombat_main"
        engine.state.step = "main"
        engine.state.started = True
        engine.state.players[seat].mana_pool.update({"G": 2, "C": 4})
        engine._grant_priority(seat)
        engine.pump()

    def test_library_top_visibility_and_action_privacy_replay(self):
        session = self.session(116_313_001)
        engine = session.engine
        self.add_card(
            session,
            seat="B",
            name="Generic Library Action Permission",
            ref="B-library-actions",
            zone="battlefield",
        )
        top = self.add_card(
            session,
            seat="B",
            name="Generic Library-Top Creature",
            ref="B-top-creature",
            zone="library",
        )
        self.prepare_main(session, "B")

        owner = session.packet("pilot:B", full=True)
        opponent = session.packet("pilot:A", full=True)
        self.assertEqual(
            top.ref,
            owner["state"]["players"]["B"]["known_top"][0]["id"],
        )
        self.assertNotIn(
            "known_top",
            opponent["state"]["players"]["B"],
        )
        actions = owner["decision"]["ctx"]["legal"]["actions"]
        cast = next(
            action
            for action in actions
            if action.get("kind") == "cast" and action.get("card") == top.ref
        )
        self.assertFalse(
            any(
                action.get("card") == top.ref
                for action in (opponent.get("decision") or {})
                .get("ctx", {})
                .get("legal", {})
                .get("actions", [])
            )
        )
        session.initial_checkpoint = checkpoint_envelope(session.state)
        session.commands.clear()
        session.decisions.clear()
        result = session.act(
            "pilot:B",
            {"action_id": cast["id"], "pay": "auto"},
        )
        self.assertTrue(result.ok, result.summary)
        with tempfile.TemporaryDirectory() as temporary:
            record_dir = Path(temporary) / "library-top-cast-replay"
            session.save(record_dir)
            replay = replay_record(record_dir, self.db, verify=True)
        self.assertTrue(replay["ok"], replay)

        public_session = self.session(116_313_005)
        self.add_card(
            public_session,
            seat="C",
            name="Generic Library Reveal Permission",
            ref="C-public-library",
            zone="battlefield",
        )
        public_top = self.add_card(
            public_session,
            seat="A",
            name="Generic Library-Top Creature",
            ref="A-public-top",
            zone="library",
        )
        for principal in ("pilot:A", "pilot:B", "pilot:C", "pilot:D"):
            packet = public_session.packet(principal, full=True)
            self.assertEqual(
                public_top.ref,
                packet["state"]["players"]["A"]["known_top"][0]["id"],
            )

        land_session = self.session(116_313_006)
        self.add_card(
            land_session,
            seat="B",
            name="Generic Library Action Permission",
            ref="B-library-land-actions",
            zone="battlefield",
        )
        top_land = self.add_card(
            land_session,
            seat="B",
            name="Generic Library-Top Land",
            ref="B-library-top-land",
            zone="library",
        )
        self.prepare_main(land_session, "B")
        land_action = next(
            action
            for action in land_session.packet("pilot:B", full=True)[
                "decision"
            ]["ctx"]["legal"]["actions"]
            if action.get("kind") == "play_land"
            and action.get("card") == top_land.ref
        )
        result = land_session.act(
            "pilot:B",
            {"action_id": land_action["id"]},
        )
        self.assertTrue(result.ok, result.summary)
        self.assertEqual("battlefield", top_land.zone)

    def test_library_top_offer_commit_and_stale_identity_rollback(self):
        session = self.session(116_313_002)
        engine = session.engine
        self.add_card(
            session,
            seat="B",
            name="Generic Library Action Permission",
            ref="B-stale-actions",
            zone="battlefield",
        )
        top = self.add_card(
            session,
            seat="B",
            name="Generic Library-Top Creature",
            ref="B-stale-top",
            zone="library",
        )
        replacement = self.add_card(
            session,
            seat="B",
            name="Generic Library-Top Land",
            ref="B-replacement-top",
            zone="hand",
        )
        self.prepare_main(session, "B")
        self.assertTrue(engine._compiled_zone_cast_permission("B", top))
        stale_action_id = next(
            action["id"]
            for action in session.packet("pilot:B", full=True)["decision"][
                "ctx"
            ]["legal"]["actions"]
            if action.get("kind") == "cast" and action.get("card") == top.ref
        )
        engine.move_card(replacement.object_id, "library", log=False)
        self.assertFalse(engine._compiled_zone_cast_permission("B", top))
        stack_before = list(engine.state.stack)
        result = session.act(
            "pilot:B",
            {"action_id": stale_action_id, "pay": "auto"},
        )
        self.assertFalse(result.ok)
        self.assertIn("zone permission", result.summary)
        self.assertEqual(stack_before, engine.state.stack)
        self.assertEqual("library", top.zone)

    def test_additional_land_play_quota_revalidates_current_allowance_and_replays(
        self,
    ):
        session = self.session(116_313_003)
        engine = session.engine
        source = self.add_card(
            session,
            seat="B",
            name="Generic Additional Land Permission",
            ref="B-extra-land",
            zone="battlefield",
        )
        lands = [
            self.add_card(
                session,
                seat="B",
                name="Generic Library-Top Land",
                ref=f"B-land-{index}",
                zone="hand",
            )
            for index in range(1, 4)
        ]
        self.prepare_main(session, "B")
        options = land_play_permission_options(engine, "B")
        self.assertEqual(2, len(options))
        self.assertEqual(
            2,
            session.packet("pilot:B", full=True)["state"]["players"]["B"][
                "lands"
            ],
        )
        offered_land = next(
            action
            for action in session.packet("pilot:B", full=True)["decision"][
                "ctx"
            ]["legal"]["actions"]
            if action.get("kind") == "play_land"
            and action.get("card") == lands[0].ref
        )
        self.assertEqual("land-play-quota:0", offered_land["land_play_permission"])
        self.assertNotIn("land_play_permission", offered_land.get("choice_schema", {}))
        engine._play_land(
            "B",
            {
                "card": lands[0].ref,
                "from": "hand",
                "land_play_permission": options[0],
            },
        )
        extra = land_play_permission_options(engine, "B")
        self.assertEqual(1, len(extra))
        engine.move_card(source.object_id, "graveyard", log=False)
        self.assertEqual((), land_play_permission_options(engine, "B"))
        with self.assertRaisesRegex(Exception, "No land plays remain"):
            engine._play_land(
                "B",
                {
                    "card": lands[1].ref,
                    "from": "hand",
                    "land_play_permission": extra[0],
                },
            )
        self.assertEqual("hand", lands[1].zone)
        engine.move_card(source.object_id, "battlefield", controller="B", log=False)
        renewed = land_play_permission_options(engine, "B")
        self.assertEqual(extra, renewed)

        session.state.pending_decision = None
        engine.permissions.invalidate_current()
        engine.state.priority_player = "B"
        engine._grant_priority("B")
        engine.pump()
        action_id = next(
            action["id"]
            for action in session.packet("pilot:B", full=True)["decision"][
                "ctx"
            ]["legal"]["actions"]
            if action.get("kind") == "play_land"
            and action.get("card") == lands[1].ref
        )
        session.initial_checkpoint = checkpoint_envelope(session.state)
        session.commands.clear()
        session.decisions.clear()
        result = session.act("pilot:B", {"action_id": action_id})
        self.assertTrue(result.ok, result.summary)
        self.assertEqual(0, len(land_play_permission_options(engine, "B")))
        self.assertEqual(1, len(land_play_permission_options(engine, "A")))
        self.assertEqual(
            0,
            session.packet("pilot:B", full=True)["state"]["players"]["B"][
                "lands"
            ],
        )
        with tempfile.TemporaryDirectory() as temporary:
            record_dir = Path(temporary) / "additional-land-replay"
            session.save(record_dir)
            replay = replay_record(record_dir, self.db, verify=True)
        self.assertTrue(replay["ok"], replay)

    def test_additional_land_quota_counts_plays_not_source_slots(self):
        def fixture(seed: int, *, source_count: int = 1):
            session = self.session(seed)
            sources = [
                self.add_card(
                    session,
                    seat="B",
                    name="Generic Additional Land Permission",
                    ref=f"B-quota-source-{index}",
                    zone="battlefield",
                )
                for index in range(source_count)
            ]
            lands = [
                self.add_card(
                    session,
                    seat="B",
                    name="Generic Library-Top Land",
                    ref=f"B-quota-land-{index}",
                    zone="hand",
                )
                for index in range(4)
            ]
            self.prepare_main(session, "B")
            return session, sources, lands

        def offered_action(session, card):
            return next(
                action
                for action in session.packet("pilot:B", full=True)[
                    "decision"
                ]["ctx"]["legal"]["actions"]
                if action.get("kind") == "play_land"
                and action.get("card") == card.ref
            )

        session, (source,), lands = fixture(116_313_007)
        engine = session.engine
        for land in lands[:2]:
            action = offered_action(session, land)
            result = session.act("pilot:B", {"action_id": action["id"]})
            self.assertTrue(result.ok, result.summary)
        self.assertEqual((), land_play_permission_options(engine, "B"))
        engine.move_card(source.object_id, "graveyard", log=False)
        engine.move_card(source.object_id, "battlefield", controller="B", log=False)
        self.assertEqual((), land_play_permission_options(engine, "B"))
        before = engine.state.to_dict()
        with self.assertRaisesRegex(Exception, "No land plays remain"):
            engine._play_land("B", {"card": lands[2].ref, "from": "hand"})
        self.assertEqual(before, engine.state.to_dict())

        session, (source,), lands = fixture(116_313_008)
        engine = session.engine
        _first, extra = land_play_permission_options(engine, "B")
        engine._play_land(
            "B",
            {
                "card": lands[0].ref,
                "from": "hand",
                "land_play_permission": extra,
            },
        )
        engine.move_card(source.object_id, "graveyard", log=False)
        self.assertEqual((), land_play_permission_options(engine, "B"))

        engine.move_card(source.object_id, "battlefield", controller="B", log=False)
        self.assertEqual(1, len(land_play_permission_options(engine, "B")))
        engine._play_land("B", {"card": lands[1].ref, "from": "hand"})
        self.assertEqual((), land_play_permission_options(engine, "B"))

        session, sources, lands = fixture(116_313_009, source_count=2)
        engine = session.engine
        self.assertEqual(3, len(land_play_permission_options(engine, "B")))
        for expected_remaining, land in zip((2, 1, 0), lands[:3]):
            engine._play_land("B", {"card": land.ref, "from": "hand"})
            self.assertEqual(
                expected_remaining,
                len(land_play_permission_options(engine, "B")),
            )
        self.assertEqual("battlefield", sources[0].zone)

    def test_additional_land_quota_tracks_current_permission_state(self):
        session = self.session(116_313_010)
        engine = session.engine
        source = self.add_card(
            session,
            seat="B",
            name="Generic Additional Land Permission",
            ref="B-dynamic-quota-source",
            zone="battlefield",
        )
        lands = [
            self.add_card(
                session,
                seat="B",
                name="Generic Library-Top Land",
                ref=f"B-dynamic-quota-land-{index}",
                zone="hand",
            )
            for index in range(4)
        ]
        self.prepare_main(session, "B")
        self.assertEqual(2, len(land_play_permission_options(engine, "B")))

        engine.move_card(
            lands[0].object_id,
            "battlefield",
            controller="B",
            reason="put by effect",
            log=False,
        )
        self.assertEqual(2, len(land_play_permission_options(engine, "B")))
        engine.move_card(lands[0].object_id, "hand", log=False)
        engine._play_land("B", {"card": lands[0].ref, "from": "hand"})
        self.assertEqual(1, len(land_play_permission_options(engine, "B")))
        for principal in ("pilot:A", "pilot:B", "pilot:C", "pilot:D"):
            self.assertEqual(
                1,
                session.packet(principal, full=True)["state"]["players"]["B"][
                    "lands"
                ],
            )

        stale_action = next(
            action
            for action in session.packet("pilot:B", full=True)["decision"][
                "ctx"
            ]["legal"]["actions"]
            if action.get("kind") == "play_land"
            and action.get("card") == lands[1].ref
        )
        source.phased_out = True
        before = engine.state.to_dict()
        rejected = session.act("pilot:B", {"action_id": stale_action["id"]})
        self.assertFalse(rejected.ok)
        self.assertEqual(before, engine.state.to_dict())
        self.assertEqual("hand", lands[1].zone)
        source = engine.state.cards[source.object_id]
        source.phased_out = False
        self.assertEqual(1, len(land_play_permission_options(engine, "B")))

        programs = engine.semantics.runtime_handler_programs_for_oracle(
            source.oracle_id,
            active_zone="battlefield",
            event=ACTION_PERMISSION_EVENT,
        )
        self.assertEqual(1, len(programs))
        removal = commit_continuous_effect(
            session.state,
            ContinuousEffect(
                effect_id="fixture:remove-additional-land-permission",
                source_id="fixture:remove-additional-land-permission",
                layer=Layer.ABILITY,
                sublayer="6",
                timestamp=engine._next_zone_timestamp(),
                operations=(
                    ContinuousOperation(
                        "remove_ability_fragment",
                        {
                            "kind": "static_component",
                            "value": StaticComponentSpec(
                                programs[0].key
                            ).to_dict(),
                        },
                    ),
                ),
                origin=ContinuousEffectOrigin.RESOLUTION,
                duration=ContinuousEffectDuration.UNTIL_END_OF_TURN,
                locked_objects=(
                    ContinuousObjectIdentity(
                        source.object_id,
                        source.logical_object_id,
                    ),
                ),
            ),
        )
        self.assertEqual((), land_play_permission_options(engine, "B"))
        session.state.continuous_effects.remove(removal)
        self.assertEqual(1, len(land_play_permission_options(engine, "B")))

        engine.change_control(
            source.object_id,
            "A",
            reason="additional-land quota witness",
        )
        self.assertEqual((), land_play_permission_options(engine, "B"))
        self.assertEqual(2, len(land_play_permission_options(engine, "A")))
        engine.change_control(
            source.object_id,
            "B",
            reason="restore additional-land quota witness",
        )
        self.assertEqual(1, len(land_play_permission_options(engine, "B")))

        self.add_card(
            session,
            seat="B",
            name="Generic Two Additional Lands Permission",
            ref="B-two-extra-land-source",
            zone="battlefield",
        )
        self.assertEqual(3, len(land_play_permission_options(engine, "B")))
        engine.state.players["B"].land_plays_remaining = 1
        reset_additional_land_play_permissions(engine, "B")
        self.assertEqual(4, len(land_play_permission_options(engine, "B")))

    def test_additional_land_quota_adapts_legacy_slot_records_explicitly(self):
        session = self.session(116_313_011)
        engine = session.engine
        source = self.add_card(
            session,
            seat="B",
            name="Generic Additional Land Permission",
            ref="B-legacy-quota-source",
            zone="battlefield",
        )
        land = self.add_card(
            session,
            seat="B",
            name="Generic Library-Top Land",
            ref="B-legacy-quota-land",
            zone="hand",
        )
        self.prepare_main(session, "B")
        legacy_slot = additional_land_play_permission_slots(engine, "B")[0]
        player = engine.state.players["B"]
        player.land_plays_remaining = 1
        player.stats[USED_ADDITIONAL_LAND_PLAY_SLOTS_STAT] = [legacy_slot]
        self.assertEqual((1, 1), land_play_accounting(engine, "B"))
        self.assertEqual(1, len(land_play_permission_options(engine, "B")))

        engine.move_card(source.object_id, "graveyard", log=False)
        self.assertEqual((), land_play_permission_options(engine, "B"))
        engine.move_card(source.object_id, "battlefield", controller="B", log=False)
        current_legacy_slot = additional_land_play_permission_slots(engine, "B")[0]
        engine._play_land(
            "B",
            {
                "card": land.ref,
                "from": "hand",
                "land_play_permission": current_legacy_slot,
            },
        )
        self.assertNotIn(USED_ADDITIONAL_LAND_PLAY_SLOTS_STAT, player.stats)
        self.assertEqual(
            {"version": 2, "base_allowance": 1, "played": 2},
            player.stats[LAND_PLAY_ACCOUNTING_STAT],
        )

    def test_additional_land_quota_counts_supported_land_play_origins(self):
        session = self.session(116_313_012)
        engine = session.engine
        for name, ref in (
            ("Generic Two Additional Lands Permission", "B-origin-quota"),
            ("Generic Library Action Permission", "B-origin-library"),
            ("Crucible of Worlds", "B-origin-graveyard"),
        ):
            self.add_card(
                session,
                seat="B",
                name=name,
                ref=ref,
                zone="battlefield",
            )
        origins = ("hand", "library", "graveyard")
        lands = [
            self.add_card(
                session,
                seat="B",
                name="Generic Library-Top Land",
                ref=f"B-origin-land-{zone}",
                zone=zone,
            )
            for zone in origins
        ]
        put_land = self.add_card(
            session,
            seat="B",
            name="Generic Library-Top Land",
            ref="B-origin-land-put",
            zone="hand",
        )
        self.prepare_main(session, "B")
        self.assertEqual(3, len(land_play_permission_options(engine, "B")))
        engine.move_card(
            put_land.object_id,
            "battlefield",
            controller="B",
            reason="put by effect",
            log=False,
        )
        self.assertEqual(3, len(land_play_permission_options(engine, "B")))
        session.initial_checkpoint = checkpoint_envelope(session.state)
        session.commands.clear()
        session.decisions.clear()

        for expected_remaining, land in zip((2, 1, 0), lands):
            action = next(
                action
                for action in session.packet("pilot:B", full=True)[
                    "decision"
                ]["ctx"]["legal"]["actions"]
                if action.get("kind") == "play_land"
                and action.get("card") == land.ref
            )
            result = session.act("pilot:B", {"action_id": action["id"]})
            self.assertTrue(result.ok, result.summary)
            self.assertEqual(
                expected_remaining,
                len(land_play_permission_options(engine, "B")),
            )
        with tempfile.TemporaryDirectory() as temporary:
            record_dir = Path(temporary) / "additional-land-origins-replay"
            session.save(record_dir)
            replay = replay_record(record_dir, self.db, verify=True)
        self.assertTrue(replay["ok"], replay)

    def test_current_ability_removal_suppresses_library_permissions(self):
        session = self.session(116_313_004)
        engine = session.engine
        source = self.add_card(
            session,
            seat="B",
            name="Generic Library Action Permission",
            ref="B-removed-actions",
            zone="battlefield",
        )
        top = self.add_card(
            session,
            seat="B",
            name="Generic Library-Top Creature",
            ref="B-removed-top",
            zone="library",
        )
        self.assertTrue(engine._compiled_zone_cast_permission("B", top))
        self.assertEqual(
            top.ref,
            session.packet("pilot:B", full=True)["state"]["players"]["B"][
                "known_top"
            ][0]["id"],
        )
        programs = engine.semantics.runtime_handler_programs_for_oracle(
            source.oracle_id,
            active_zone="battlefield",
            event=ACTION_PERMISSION_EVENT,
        )
        self.assertEqual(2, len(programs))
        commit_continuous_effect(
            session.state,
            ContinuousEffect(
                effect_id="fixture:remove-library-action-permissions",
                source_id="fixture:remove-library-action-permissions",
                layer=Layer.ABILITY,
                sublayer="6",
                timestamp=engine._next_zone_timestamp(),
                operations=tuple(
                    ContinuousOperation(
                        "remove_ability_fragment",
                        {
                            "kind": "static_component",
                            "value": StaticComponentSpec(program.key).to_dict(),
                        },
                    )
                    for program in programs
                ),
                origin=ContinuousEffectOrigin.RESOLUTION,
                duration=ContinuousEffectDuration.UNTIL_END_OF_TURN,
                locked_objects=(
                    ContinuousObjectIdentity(
                        source.object_id,
                        source.logical_object_id,
                    ),
                ),
            ),
        )
        self.assertFalse(engine._compiled_zone_cast_permission("B", top))
        self.assertNotIn(
            "known_top",
            session.packet("pilot:B", full=True)["state"]["players"]["B"],
        )


if __name__ == "__main__":
    unittest.main()
