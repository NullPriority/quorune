from __future__ import annotations

from pathlib import Path
import tempfile
import unittest
from unittest import mock

from common import keep_all, load_assets, make_session
from quorune.card_programs import compile_card_program
from quorune.carddb import CardRecord
from quorune.compiler.activation_restriction_templates import (
    static_activation_restriction_handler,
)
from quorune.errors import GameRuleError
from quorune.record import checkpoint_envelope, replay_record
from quorune.rules.capabilities import load_default_capability_registry
from quorune.semantic_runtime.activation_restrictions import (
    ACTIVATION_PERMISSION_EVENT,
    CHOSEN_NAME_NONMANA_PROHIBITION_HANDLER_ID,
    FIXED_PUBLIC_ACTIVATION_PROHIBITION_HANDLER_ID,
    current_activation_prohibitions,
    default_activation_restriction_registry,
)
from quorune.semantic_runtime.context import SemanticNodeError


_RESTRICTION_TEXT = (
    "Activated abilities of sources with the chosen name can't be activated "
    "unless they're mana abilities."
)


class _NoRulingsDatabase:
    @staticmethod
    def rulings(record):
        del record
        return ()


def _permanent(
    text: str,
    *,
    suffix: int,
    name: str = "Activation Restriction Fixture",
    faces: tuple[dict[str, object], ...] = (),
) -> CardRecord:
    return CardRecord(
        oracle_id=f"00000000-0000-4000-8000-{suffix:012d}",
        name=name,
        mana_cost="{1}",
        mana_value=1.0,
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


class StaticActivationRestrictionCompilerTests(unittest.TestCase):
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

    def test_closed_restriction_compiles_face_pinned_typed_program(self):
        program = self.compile(
            _permanent(_RESTRICTION_TEXT, suffix=116_400_001)
        )
        self.assertEqual((), program.residuals)
        ability = next(
            ability
            for ability in program.abilities
            if any(
                descriptor.get("handler_id")
                == CHOSEN_NAME_NONMANA_PROHIBITION_HANDLER_ID
                for descriptor in ability.handlers
            )
        )
        self.assertEqual("battlefield", ability.active_zone)
        self.assertEqual(ACTIVATION_PERMISSION_EVENT, ability.event)
        self.assertEqual("front", ability.provenance["face_id"])
        self.assertEqual(1, ability.provenance["source_span"]["line"])
        self.assertIn(
            "activation.restriction.chosen_name_nonmana",
            ability.capability_dependencies,
        )

        two_face = _permanent(
            f"Front text\n//\n{_RESTRICTION_TEXT}",
            suffix=116_400_002,
            name="Restriction Front // Restriction Back",
            faces=(
                {
                    "name": "Restriction Front",
                    "mana_cost": "{1}",
                    "type_line": "Artifact",
                    "oracle_text": "Front text",
                    "keywords": [],
                },
                {
                    "name": "Restriction Back",
                    "mana_cost": "",
                    "type_line": "Artifact",
                    "oracle_text": _RESTRICTION_TEXT,
                    "keywords": [],
                },
            ),
        )
        restriction = next(
            ability
            for ability in self.compile(two_face).abilities
            if any(
                descriptor.get("handler_id")
                == CHOSEN_NAME_NONMANA_PROHIBITION_HANDLER_ID
                for descriptor in ability.handlers
            )
        )
        self.assertEqual(
            "Restriction Back",
            restriction.provenance["face_id"],
        )

        all_abilities = self.compile(
            _permanent(
                "Activated abilities of sources with the chosen name can't "
                "be activated.",
                suffix=116_400_003,
            )
        )
        self.assertEqual((), all_abilities.residuals)
        self.assertTrue(
            any(
                descriptor.get("handler_id")
                == FIXED_PUBLIC_ACTIVATION_PROHIBITION_HANDLER_ID
                for ability in all_abilities.abilities
                for descriptor in ability.handlers
            )
        )

    def test_unsupported_restriction_wording_and_malformed_descriptors_fail_closed(
        self,
    ):
        unsupported = (
            "Triggered abilities of sources with the chosen name can't "
            "trigger.",
            "Activated abilities of artifacts with the chosen name can't be "
            "activated unless they're mana abilities.",
            "Activated abilities of sources with a named card can't be "
            "activated unless they're mana abilities.",
        )
        for index, text in enumerate(unsupported, 1):
            with self.subTest(text=text):
                program = self.compile(
                    _permanent(text, suffix=116_401_000 + index),
                    trust_level="provisional",
                )
                self.assertTrue(program.residuals)
                self.assertFalse(
                    any(
                        ability.event == ACTIVATION_PERMISSION_EVENT
                        for ability in program.abilities
                    )
                )

        compiled = static_activation_restriction_handler(_RESTRICTION_TEXT)
        self.assertIsNotNone(compiled)
        descriptor = compiled[1]
        registry = default_activation_restriction_registry()
        registry.validate(descriptor)
        malformed_values = (
            {**descriptor, "unknown": True},
            {**descriptor, "schema_version": True},
            {**descriptor, "schema_version": 2},
            {**descriptor, "event": "continuous"},
            {**descriptor, "source_name_relation": "printed_name"},
            {**descriptor, "ability_scope": "all"},
        )
        for malformed in malformed_values:
            with self.subTest(malformed=malformed):
                with self.assertRaises(SemanticNodeError):
                    registry.validate(malformed)

    def test_activation_restriction_compiler_mutant_is_killed(self):
        record = _permanent(_RESTRICTION_TEXT, suffix=116_402_001)

        def assert_compiler_boundary() -> None:
            program = self.compile(record, trust_level="provisional")
            self.assertTrue(
                any(
                    ability.event == ACTIVATION_PERMISSION_EVENT
                    for ability in program.abilities
                )
            )

        assert_compiler_boundary()
        with mock.patch(
            "quorune.compiler.runtime_templates."
            "static_activation_restriction_handler",
            return_value=None,
        ):
            with self.assertRaises(AssertionError):
                assert_compiler_boundary()


class StaticActivationRestrictionRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db, cls.mishra, cls.zimone = load_assets()

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

    @staticmethod
    def resolve_top(engine) -> None:
        engine.state.priority_player = None
        engine._prepare_stack_resolution()

    def prepare_named_choice(self, session):
        engine = session.engine
        needle = self.card(engine, "A", "Pithing Needle")
        engine.move_card(needle.object_id, "hand", log=False)
        engine.state.players["A"].mana_pool["C"] = 1
        self.prepare_main(session, "A")
        # Direct fixture casts bypass the submitted priority action, so retire
        # that issued decision before resolution creates the scalar choice.
        engine.permissions.invalidate_current()
        engine._cast("A", {"card": needle.ref})
        self.resolve_top(engine)
        self.assertIsNotNone(engine.state.pending_decision)
        return needle

    def test_chosen_name_restriction_is_global_typed_and_mana_exempt(self):
        session = self.session(116_403_001)
        engine = session.engine
        needle = self.card(engine, "A", "Pithing Needle")
        top = self.card(engine, "C", "Sensei's Divining Top")
        sol_ring = self.card(engine, "C", "Sol Ring")
        engine.move_card(needle.object_id, "battlefield", controller="A", log=False)
        engine.move_card(top.object_id, "battlefield", controller="C", log=False)
        engine.move_card(
            sol_ring.object_id,
            "battlefield",
            controller="C",
            log=False,
        )
        engine.state.players["C"].mana_pool["C"] = 1
        self.prepare_main(session, "C")

        needle.annotations["chosen_name"] = "  SENSEI'S divining top  "
        top_ability = next(
            ability
            for ability in engine._activated_abilities(top)
            if not ability.mana_ability
        )
        prohibitions = current_activation_prohibitions(
            engine,
            top,
            top_ability,
        )
        self.assertEqual(
            [needle.ref],
            [value.restriction_source_ref for value in prohibitions],
        )
        self.assertEqual(
            ("unavailable", "named_ability_prohibition"),
            engine._ability_availability("C", top, top_ability),
        )
        before_pool = dict(engine.state.players["C"].mana_pool)
        before_stack = list(engine.state.stack)
        with self.assertRaisesRegex(GameRuleError, "named_ability_prohibition"):
            engine._activate(
                "C",
                {
                    "source": top.ref,
                    "ability": top_ability.ability_id,
                    "pay": "auto",
                },
            )
        self.assertEqual(before_pool, engine.state.players["C"].mana_pool)
        self.assertEqual(before_stack, engine.state.stack)

        needle.annotations["chosen_name"] = "Sol Ring"
        mana_ability = next(
            ability
            for ability in engine._activated_abilities(sol_ring)
            if ability.mana_ability
        )
        self.assertEqual(
            ("payable", None),
            engine._ability_availability("C", sol_ring, mana_ability),
        )

        needle.annotations["chosen_name"] = "Sensei's Divining Top"
        needle.phased_out = True
        self.assertEqual(
            ("payable", None),
            engine._ability_availability("C", top, top_ability),
        )

    def test_raw_oracle_text_without_typed_restriction_fails_closed(self):
        session = self.session(116_403_002)
        engine = session.engine
        needle = self.card(engine, "A", "Pithing Needle")
        top = self.card(engine, "C", "Sensei's Divining Top")
        engine.move_card(needle.object_id, "battlefield", controller="A", log=False)
        engine.move_card(top.object_id, "battlefield", controller="C", log=False)
        needle.annotations["chosen_name"] = "Sensei's Divining Top"
        engine.state.players["C"].mana_pool["C"] = 1
        self.prepare_main(session, "C")
        ability = next(
            ability
            for ability in engine._activated_abilities(top)
            if not ability.mana_ability
        )
        self.assertIn(
            "sources with the chosen name",
            self.db.lookup(needle.printed_name).oracle_text.casefold(),
        )
        programs = tuple(
            engine.semantics.runtime_handler_programs_for_oracle(
                needle.oracle_id,
                active_zone="battlefield",
                event=ACTIVATION_PERMISSION_EVENT,
            )
        )
        self.assertTrue(programs)
        for program in programs:
            engine.semantics.remove(program.key)
        self.assertEqual(
            ("payable", None),
            engine._ability_availability("C", top, ability),
        )

    def test_chosen_name_restriction_choice_is_principal_scoped(self):
        session = self.session(116_403_003)
        self.prepare_named_choice(session)
        self.assertEqual(["pilot:A"], session.pending_principals())

    def test_chosen_name_restriction_replays_exactly(self):
        session = self.session(116_403_004)
        engine = session.engine
        needle = self.prepare_named_choice(session)
        session.initial_checkpoint = checkpoint_envelope(session.state)
        session.commands.clear()
        session.decisions.clear()

        result = session.act(
            "pilot:A",
            {
                "action_id": "choose",
                "card_name": "Sensei's Divining Top",
                "plan": "PIN_ENGINE",
                "reason": "Choose the public activated-ability source.",
            },
        )
        self.assertTrue(result.ok, result.summary)
        self.assertEqual(
            "Sensei's Divining Top",
            needle.annotations["chosen_name"],
        )

        with tempfile.TemporaryDirectory() as temporary:
            record_dir = Path(temporary) / "chosen-name-restriction-replay"
            session.save(record_dir)
            replay = replay_record(record_dir, self.db, verify=True)
        self.assertTrue(replay["ok"], replay)


if __name__ == "__main__":
    unittest.main()
