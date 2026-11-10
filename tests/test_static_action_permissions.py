from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from common import ROOT, keep_all, load_assets, make_session
from inherited_composition_fixture import InheritedNativeFixture
from quorune.ability_fragments import StaticComponentSpec
from quorune.card_programs import compile_card_program
from quorune.carddb import CardDatabase, CardRecord
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
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import generated_programs, register_generated_programs
from quorune.record import authoritative_state_hash, checkpoint_envelope, replay_record
from quorune.rules.land_play_permissions import (
    reset_additional_land_play_permissions,
)
from quorune.rules.capabilities import load_default_capability_registry
from quorune.semantic_runtime.action_permissions import (
    ACTION_PERMISSION_EVENT,
    ACTIVATE_CONTROLLED_CREATURE_AS_HASTE_HANDLER_ID,
    ADDITIONAL_LAND_PLAY_HANDLER_ID,
    LIBRARY_TOP_ACTION_HANDLER_ID,
    LIBRARY_TOP_HISTORY_CAPABILITY,
    LIBRARY_TOP_MANA_VALUE_CAPABILITY,
    LIBRARY_TOP_VISIBILITY_HANDLER_ID,
    LAND_PLAY_FROM_OWN_GRAVEYARD_HANDLER_ID,
    LAND_PLAY_ACCOUNTING_STAT,
    USED_ADDITIONAL_LAND_PLAY_SLOTS_STAT,
    ActionPermissionKind,
    QUALIFIED_LIBRARY_TOP_CAPABILITY,
    LibraryTopQualifier,
    additional_land_play_permission_slots,
    controller_action_permissions,
    default_action_permission_registry,
    land_play_accounting,
    land_play_permission_options,
)
from quorune.semantic_runtime.context import SemanticNodeError
from scripts.build_test_database import build_fixture_database


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

    def test_qualified_library_top_descriptors_and_dependencies_are_closed(self):
        from quorune.card_programs.binding import bind_semantic_program_runtime

        cases = (
            ('As long as this Equipment is attached to a creature, you may cast creature spells from the top of your library.', 'attached_to_creature'),
            ('As long as an opponent controls more lands than you, you may play lands from the top of your library.', 'opponent_more_lands'),
            ('Coven — As long as you control three or more creatures with different powers, you may cast creature spells from the top of your library.', 'distinct_creature_powers'),
            ('You may cast creature spells of the chosen type from the top of your library.', 'chosen_creature_type'),
            ('You may play lands and cast spells with mana value 4 or greater from the top of your library.', 'minimum_spell_mana_value'),
            ('As long as this Vehicle attacked this turn, you may play the top card of your library.', 'source_attacked_this_turn'),
            ("As long as you've cast a spell this turn, you may play cards from the top of your library.", 'controller_cast_this_turn'),
        )
        registry = default_action_permission_registry()
        for index, (text, qualifier) in enumerate(cases):
            with self.subTest(qualifier=qualifier):
                record = _permanent(text, suffix=116_312_010 + index)
                compiled = self.compile(record)
                self.assertEqual((), compiled.residuals)
                program, = generated_programs(_NoRulingsDatabase(), record, trust_level='trusted',
                    capability_registry=self.capabilities, capability_profile='commander_review')
                descriptor, = program.handlers
                self.assertEqual((2, qualifier), (descriptor['schema_version'], descriptor['qualifier']))
                registry.validate(descriptor)
                self.assertTrue(bind_semantic_program_runtime(program, capability_registry=self.capabilities,
                    profile='commander_review')['strict'])
                required = [QUALIFIED_LIBRARY_TOP_CAPABILITY]
                if qualifier == 'minimum_spell_mana_value':
                    required.append(LIBRARY_TOP_MANA_VALUE_CAPABILITY)
                    with self.assertRaises(SemanticNodeError):
                        registry.validate({**descriptor, 'minimum_spell_mana_value': True})
                if qualifier in {'source_attacked_this_turn', 'controller_cast_this_turn'}:
                    required.append(LIBRARY_TOP_HISTORY_CAPABILITY)
                if qualifier == 'controller_cast_this_turn':
                    from quorune.public_history_query_model import PUBLIC_HISTORY_QUERY_CAPABILITY

                    required.append(PUBLIC_HISTORY_QUERY_CAPABILITY)
                for capability in required:
                    deps = tuple(value for value in program.capability_dependencies if value != capability)
                    withdrawn = replace(program, capability_dependencies=deps,
                        capability_closure=self.capabilities.closure(deps, profile='commander_review').to_dict())
                    result = bind_semantic_program_runtime(withdrawn, capability_registry=self.capabilities,
                        profile='commander_review')
                    self.assertFalse(result['strict'])
                    self.assertIn('capability:undeclared_runtime_dependency:' + capability, result['blockers'])
                with self.assertRaises(SemanticNodeError):
                    registry.validate({**descriptor, 'qualifier': 'unknown'})
        foreign = 'As long as Another Equipment is attached to a creature, you may cast spells from the top of your library.'
        self.assertTrue(self.compile(_permanent(foreign, suffix=116_312_018), trust_level='provisional').residuals)
        foreign_attack = 'As long as Another Vehicle attacked this turn, you may play the top card of your library.'
        self.assertTrue(self.compile(_permanent(foreign_attack, suffix=116_312_020), trust_level='provisional').residuals)
        compound = _permanent('You may look at the top card of your library any time, and you may play lands from the top of your library.',
            suffix=116_312_019)
        program, = generated_programs(_NoRulingsDatabase(), compound, trust_level='trusted',
            capability_registry=self.capabilities, capability_profile='commander_review')
        self.assertEqual((LIBRARY_TOP_VISIBILITY_HANDLER_ID, LIBRARY_TOP_ACTION_HANDLER_ID),
            tuple(row['handler_id'] for row in program.handlers))


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


class QualifiedLibraryTopNativeTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        path = Path(cls.temporary.name) / 'qualified-library-top.sqlite3'
        build_fixture_database([ROOT / 'tests/fixtures' / name for name in (
            'bound-effect-program-cards.json', 'public-library-action-permission-cards.json',
        )], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition('Qualified library top', [DeckEntry('Generic Bound Commander', 1, 'commander'),
            DeckEntry('Generic Bound Plains', 30)], ['Generic Bound Commander'])

    def add_top(self, session, name, ref='qualified-top'):
        card = self.add(session.engine, name, zone='library', ref=ref)
        card.known_to = []
        card.revealed_to = []
        return card

    def actions(self, session, seat='A'):
        packet = session.packet('pilot:' + seat, full=True)
        return (packet.get('decision') or {}).get('ctx', {}).get('legal', {}).get('actions', ())

    def offer(self, session, card, kind):
        return next((row for row in self.actions(session) if row.get('kind') == kind and row.get('card') == card.ref), None)

    def known_top(self, session, seat='A'):
        packet = session.packet('pilot:' + seat, full=True)
        return [row['id'] for row in packet['state']['players']['A'].get('known_top', ())]

    def take(self, session, action, **response):
        self.assertIsNotNone(action)
        result = session.act('pilot:A', {'action_id': action['id'], **response})
        self.assertTrue(result.ok, result.summary)

    def attach(self, session, source, bearer, suffix):
        self.priority(session, 'A')
        action = next((row for row in self.actions(session) if row['id'].startswith('activate:' + source.ref + ':')
            and (not suffix or row['id'].endswith(suffix))), None)
        self.take(session, action, targets=[bearer.ref], pay='auto')
        self.assertIsNone(self.resolve(session))
        self.assertEqual(bearer.object_id, session.state.cards[source.object_id].attached_to)

    def test_lunar_whale_requires_its_current_incarnations_attack(self):
        for source_attacks in (False, True):
            with self.subTest(source_attacks=source_attacks):
                session = self.session(847101 + source_attacks)
                source = self.add(session.engine, 'The Lunar Whale')
                other = self.add(session.engine, 'Grizzly Bears')
                self.add(session.engine, 'Mountain')
                self.add(session.engine, 'Plains')
                blink = self.add(session.engine, 'Cloudshift', zone='hand')
                later = self.add_top(session, 'Lightning Bolt', 'history-later')
                top = self.add_top(session, 'Lightning Bolt', 'history-top')
                for creature in (source, other):
                    creature.acquired_control_turn_count = -1
                self.assert_closed(session, source)
                self.prepare(session)
                self.checkpoint(session)
                self.assertEqual([top.ref], self.known_top(session))
                self.assertEqual([], self.known_top(session, 'D'))
                self.assertIsNone(self.offer(session, top, 'cast'))
                if source_attacks:
                    crew, = (ability for ability in session.engine._activated_abilities(source)
                        if ability.crew_threshold is not None)
                    action_id = f'activate:{source.ref}:{crew.ability_id}'
                    action = next(row for row in self.actions(session) if row['id'] == action_id)
                    self.take(session, action, cost_cards=[other.ref])
                    self.assertIsNone(self.resolve(session))
                    self.assertIsNone(self.offer(session, top, 'cast'))
                self.attack(session, [(source if source_attacks else other, 'B')])
                self.priority(session, 'A')
                if source_attacks:
                    self.cast(session, 'A', top, ['B'])
                    self.assertIsNone(self.resolve(session))
                    self.assertEqual('graveyard', session.state.cards[top.object_id].zone)
                    self.assertEqual([later.ref], self.known_top(session))
                    old_incarnation = source.logical_object_id
                    self.cast(session, 'A', blink, [source.ref])
                    self.assertIsNone(self.resolve(session))
                    self.assertNotEqual(old_incarnation, session.state.cards[source.object_id].logical_object_id)
                self.priority(session, 'A')
                denied = later if source_attacks else top
                self.assertEqual([denied.ref], self.known_top(session))
                self.assertIsNone(self.offer(session, denied, 'cast'))
                before = authoritative_state_hash(session.state)
                result = session.act('pilot:A', {'action_id': 'cast:' + denied.ref, 'targets': ['B'], 'pay': 'auto'})
                self.assertFalse(result.ok, result.summary)
                self.assertEqual(before, authoritative_state_hash(session.state))
                self.assertEqual([], self.known_top(session, 'D'))
                self.replay(session, load=True)

    def test_fortune_tellers_talent_requires_level_and_committed_controller_cast(self):
        session = self.session(847110)
        source = self.add(session.engine, "Fortune Teller's Talent")
        rival_spell = self.add(session.engine, 'Lightning Bolt', seat='B', zone='hand', ref='rival-cast')
        own_spell = self.add(session.engine, 'Lightning Bolt', zone='hand', ref='own-cast')
        self.add(session.engine, 'Mountain')
        land = self.add_top(session, 'Forest', 'history-land')
        self.assert_closed(session, source)
        self.prepare(session, mana={'C': 3, 'U': 1})
        session.state.players['B'].mana_pool['R'] = 1
        self.checkpoint(session)
        self.assertEqual([land.ref], self.known_top(session))
        self.assertEqual([], self.known_top(session, 'B'))
        self.assertIsNone(self.offer(session, land, 'play_land'))
        self.assertFalse(any(permission.source_ref == source.ref and permission.kind is ActionPermissionKind.LIBRARY_TOP_ACTION
            for permission in controller_action_permissions(session.engine, 'A')))
        self.cast(session, 'B', rival_spell, ['A'])
        self.assertIsNone(self.resolve(session))
        self.priority(session, 'A')
        level, = (ability for ability in session.engine._activated_abilities(source)
            if ability.effect_text == 'Level 2')
        level_id = f'activate:{source.ref}:{level.ability_id}'
        self.take(session, next(row for row in self.actions(session) if row['id'] == level_id), pay='auto')
        self.assertIsNone(self.resolve(session))
        self.assertEqual(2, session.state.cards[source.object_id].class_level)
        self.assertIsNone(self.offer(session, land, 'play_land'), 'An opponent cast does not satisfy the history gate')
        before = authoritative_state_hash(session.state)
        rejected = session.act('pilot:A', {'action_id': 'cast:' + own_spell.ref, 'targets': ['missing'], 'pay': 'auto'})
        self.assertFalse(rejected.ok, rejected.summary)
        self.assertEqual(before, authoritative_state_hash(session.state))
        self.assertIsNone(self.offer(session, land, 'play_land'))
        self.cast(session, 'A', own_spell, ['B'])
        self.assertIsNone(self.offer(session, land, 'play_land'))
        self.assertIsNone(self.resolve(session))
        self.take(session, self.offer(session, land, 'play_land'))
        self.assertEqual('battlefield', session.state.cards[land.object_id].zone)
        self.replay(session, load=True)

    def test_history_permission_gate_omission_is_detected(self):
        from quorune.semantic_runtime import action_permissions

        original = action_permissions._bound_library_top_queries

        def omit_controller_history(host, permission, *, spell):
            if permission.library_top_qualifier is LibraryTopQualifier.CONTROLLER_CAST_THIS_TURN:
                return permission.top_spell_queries if spell else permission.top_land_queries
            return original(host, permission, spell=spell)

        with mock.patch.object(action_permissions, '_bound_library_top_queries', side_effect=omit_controller_history):
            with self.assertRaisesRegex(AssertionError, 'An opponent cast does not satisfy the history gate'):
                self.test_fortune_tellers_talent_requires_level_and_committed_controller_cast()

    def test_reality_chip_reconfigures_and_stops_offering_after_attachment_leaves(self):
        session = self.session(826101)
        source = self.add(session.engine, 'The Reality Chip')
        bearer = self.add(session.engine, 'Grizzly Bears', ref='reconfigure-bearer')
        later = self.add_top(session, 'Grizzly Bears', 'later-library-creature')
        spell = self.add_top(session, 'Grizzly Bears', 'first-library-creature')
        land = self.add_top(session, 'Forest')
        bounce = self.add(session.engine, 'Unsummon', seat='B', zone='hand')
        self.assert_closed(session, source)
        self.prepare(session, mana={'C': 6, 'U': 1, 'G': 2})
        session.state.players['B'].mana_pool.update({'U': 1})
        self.checkpoint(session)
        self.assertEqual([land.ref], self.known_top(session))
        self.assertEqual([], self.known_top(session, 'B'))
        self.assertIsNone(self.offer(session, land, 'play_land'))
        self.attach(session, source, bearer, '-reconfigure-attach')
        self.take(session, self.offer(session, land, 'play_land'))
        self.assertEqual('battlefield', session.state.cards[land.object_id].zone)
        self.cast(session, 'A', spell)
        self.cast(session, 'B', bounce, [bearer.ref])
        self.assertIsNone(self.resolve(session))
        self.assertEqual('battlefield', session.state.cards[spell.object_id].zone)
        self.assertIsNone(session.state.cards[source.object_id].attached_to)
        self.priority(session, 'A')
        self.assertEqual([later.ref], self.known_top(session))
        self.assertIsNone(self.offer(session, later, 'cast'))
        self.replay(session, load=True)

    def test_summoning_materia_only_casts_creatures_while_equipped(self):
        for seed, name in enumerate(('Grizzly Bears', 'Lightning Bolt'), 826102):
            with self.subTest(name=name):
                session = self.session(seed)
                source = self.add(session.engine, 'Summoning Materia')
                bearer = self.add(session.engine, 'Grizzly Bears', ref='materia-bearer')
                spell = self.add_top(session, name)
                self.assert_closed(session, source)
                self.prepare(session, mana={'C': 5, 'G': 4, 'R': 3})
                self.checkpoint(session)
                self.assertEqual([spell.ref], self.known_top(session))
                self.assertIsNone(self.offer(session, spell, 'cast'))
                self.attach(session, source, bearer, '')
                if name == 'Grizzly Bears':
                    self.cast(session, 'A', spell)
                    self.assertIsNone(self.resolve(session))
                    self.assertEqual('battlefield', session.state.cards[spell.object_id].zone)
                else:
                    self.assertIsNone(self.offer(session, spell, 'cast'))
                self.replay(session)

    def test_verge_rangers_compares_one_opponent_and_rechecks_live_land_counts(self):
        session = self.session(826104)
        source = self.add(session.engine, 'Verge Rangers')
        own = self.add(session.engine, 'Forest')
        self.add(session.engine, 'Forest', seat='B', ref='first-opponent-land')
        self.add(session.engine, 'Forest', seat='C', ref='second-opponent-land')
        rain = self.add(session.engine, 'Stone Rain', zone='hand')
        top = self.add_top(session, 'Forest')
        self.assert_closed(session, source)
        self.prepare(session, mana={'C': 2, 'R': 1})
        self.checkpoint(session)
        self.assertEqual([top.ref], self.known_top(session))
        self.assertIsNone(self.offer(session, top, 'play_land'))
        self.cast(session, 'A', rain, [own.ref])
        self.assertIsNone(self.resolve(session))
        self.take(session, self.offer(session, top, 'play_land'))
        self.assertEqual('battlefield', session.state.cards[top.object_id].zone)
        self.assertEqual([], self.known_top(session, 'C'))
        self.replay(session)

    def test_augur_requires_three_current_distinct_powers_for_top_spell(self):
        session = self.session(826105)
        source = self.add(session.engine, 'Augur of Autumn')
        bear = self.add(session.engine, 'Grizzly Bears', ref='coven-two-power')
        self.add(session.engine, 'Elvish Mystic')
        growth = self.add(session.engine, 'Battlegrowth', zone='hand')
        spell = self.add_top(session, 'Grizzly Bears', 'coven-spell')
        land = self.add_top(session, 'Forest')
        self.assert_closed(session, source)
        self.prepare(session, mana={'C': 2, 'G': 2})
        self.checkpoint(session)
        self.take(session, self.offer(session, land, 'play_land'))
        self.assertIsNone(self.offer(session, spell, 'cast'), 'Two distinct powers cannot satisfy Coven')
        self.cast(session, 'A', growth, [bear.ref])
        self.assertIsNone(self.resolve(session))
        self.cast(session, 'A', spell)
        self.assertIsNone(self.resolve(session))
        self.assertEqual('battlefield', session.state.cards[spell.object_id].zone)
        self.replay(session)

    def test_qualified_permission_condition_omission_is_detected(self):
        with mock.patch('quorune.semantic_runtime.action_permissions._bound_library_top_queries',
                side_effect=lambda _host, permission, *, spell: permission.top_spell_queries if spell else permission.top_land_queries):
            with self.assertRaisesRegex(AssertionError, 'Two distinct powers cannot satisfy Coven'):
                self.test_augur_requires_three_current_distinct_powers_for_top_spell()

    def test_realmwalker_uses_the_chosen_type_on_the_actual_creature_spell(self):
        session = self.session(826106)
        source = self.add(session.engine, 'Realmwalker', zone='hand')
        draw = self.add(session.engine, 'Reach Through Mists', zone='hand')
        elf = self.add_top(session, 'Elvish Mystic', 'chosen-library-elf')
        bear = self.add_top(session, 'Grizzly Bears', 'not-chosen-library-bear')
        self.assert_closed(session, source)
        self.prepare(session, mana={'C': 4, 'G': 4, 'U': 4})
        self.checkpoint(session)
        self.assertEqual([], self.known_top(session))
        self.cast(session, 'A', source)
        decision = self.resolve(session)
        self.assertEqual('replacement.order', decision.kind)
        ctx = session.packet('pilot:A', full=True)['decision']['ctx']
        choice = next(row for row in ctx['options'] if row['id'].endswith(':elf'))
        self.take(session, {'id': 'choose'}, replacement=choice['id'])
        self.assertIsNone(self.resolve(session))
        self.assertEqual([bear.ref], self.known_top(session))
        self.assertIsNone(self.offer(session, bear, 'cast'))
        self.cast(session, 'A', draw)
        self.assertIsNone(self.resolve(session))
        self.cast(session, 'A', elf)
        self.assertIsNone(self.resolve(session))
        self.assertEqual('battlefield', session.state.cards[elf.object_id].zone)
        self.replay(session)

    def test_radha_looks_and_plays_one_land_through_the_ordinary_quota(self):
        session = self.session(826107)
        source = self.add(session.engine, 'Radha, Heart of Keld')
        next_land = self.add_top(session, 'Forest', 'second-radha-land')
        top = self.add_top(session, 'Forest', 'first-radha-land')
        self.assert_closed(session, source)
        self.prepare(session)
        self.checkpoint(session)
        self.assertEqual([top.ref], self.known_top(session))
        action = self.offer(session, top, 'play_land')
        before = authoritative_state_hash(session.state)
        self.assertFalse(session.act('pilot:D', {'action_id': action['id']}).ok)
        self.assertEqual(before, authoritative_state_hash(session.state))
        self.take(session, action)
        self.assertEqual([next_land.ref], self.known_top(session))
        self.assertIsNone(self.offer(session, next_land, 'play_land'))
        self.replay(session, load=True)

    def test_glarb_announces_a_payable_x_that_meets_spell_mana_value(self):
        session = self.session(826108)
        source = self.add(session.engine, "Glarb, Calamity's Augur")
        cheap = self.add_top(session, 'Lightning Bolt', 'ineligible-next-spell')
        spell = self.add_top(session, 'Blaze', 'announced-x-library-spell')
        land = self.add_top(session, 'Forest')
        self.assert_closed(session, source)
        self.prepare(session, mana={'C': 4, 'R': 2})
        self.checkpoint(session)
        self.take(session, self.offer(session, land, 'play_land'))
        self.assertEqual([spell.ref], self.known_top(session))
        self.assertEqual([], self.known_top(session, 'B'))
        action = self.offer(session, spell, 'cast')
        self.assertIsNotNone(action)
        option = next(row for row in action['cost_options'] if 'x' in row.get('choice_schema', {}))
        bounds = option['choice_schema']['x']
        self.assertEqual(3, bounds['minimum'], 'The mana-value gate must set the actual minimum X')
        self.assertGreaterEqual(bounds['maximum'], 3)
        command = {'action_id': action['id'], 'cost_option': option['id'], 'targets': ['B'], 'pay': 'auto'}
        before = authoritative_state_hash(session.state)
        self.assertFalse(session.act('pilot:A', {**command, 'x': 2}).ok)
        self.assertEqual(before, authoritative_state_hash(session.state))
        result = session.act('pilot:A', {**command, 'x': 3})
        self.assertTrue(result.ok, result.summary)
        self.assertEqual(3, session.state.stack[-1].x_value)
        self.assertIsNone(self.resolve(session))
        self.assertEqual(37, session.state.players['B'].life)
        self.assertEqual([cheap.ref], self.known_top(session))
        self.assertFalse(session.engine._compiled_zone_cast_permission('A', session.state.cards[cheap.object_id]))
        self.assertIsNone(self.offer(session, cheap, 'cast'))
        self.replay(session, load=True)

    def test_glarb_mana_value_gate_omission_is_detected(self):
        with mock.patch('quorune.semantic_runtime.action_permissions.controller_library_top_spell_minimum_x', return_value=0):
            with self.assertRaisesRegex(AssertionError, 'The mana-value gate must set the actual minimum X'):
                self.test_glarb_announces_a_payable_x_that_meets_spell_mana_value()


if __name__ == "__main__":
    unittest.main()
