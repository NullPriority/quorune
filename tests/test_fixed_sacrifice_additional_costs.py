from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from common import ROOT, keep_all, make_session
from scripts.build_test_database import build_fixture_database
from quorune.carddb import CardDatabase, CardRecord
from quorune.compiler.spell_additional_cost_templates import (
    FixedSacrificeAdditionalCostTemplate,
    fixed_sacrifice_additional_cost_template,
)
from quorune.deck import DeckLoader
from quorune.errors import GameRuleError
from quorune.oracle_ir import compile_oracle_card, generated_programs
from quorune.projection import StateProjector
from quorune.record import (
    authoritative_state_hash,
    checkpoint_envelope,
    replay_record,
)
from quorune.replacement.replay import ReplacementContinuation
from quorune.replacement_effects import ReplacementEffectError
from quorune.rules.capabilities import CapabilityRegistry
from quorune.session import CommanderSession
from quorune.rules.casting_additional_costs import (
    AdditionalCostError,
    FixedSacrificeAdditionalCost,
)


REGISTRY_PATH = ROOT / "quorune" / "rules" / "capability-registry.json"


def trusted_registry() -> CapabilityRegistry:
    registry = CapabilityRegistry.from_path(REGISTRY_PATH)
    registry.mark_evidence_verified("0" * 64)
    return registry


def fixture_card(text: str) -> CardRecord:
    return CardRecord(
        oracle_id="00000000-0000-4000-8000-000000601021",
        name="Fixed Sacrifice Fixture",
        mana_cost="{1}{B}",
        mana_value=2.0,
        type_line="Sorcery",
        oracle_text=text,
        power=None,
        toughness=None,
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


def sacrifice_cost(*types: str) -> dict:
    return dict(
        FixedSacrificeAdditionalCostTemplate(
            permanent_types=tuple(types),
        ).cost_schema
    )


class _NoRulingsDatabase:
    @staticmethod
    def rulings(_record: CardRecord) -> tuple[()]:
        return ()


class FixedSacrificeAdditionalCostCompilerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.capabilities = trusted_registry()

    def compile(self, text: str):
        return compile_oracle_card(
            fixture_card(text),
            capability_registry=self.capabilities,
            capability_profile="commander_review",
        )

    def test_fixed_sacrifice_cost_compiles_source_spanned_v2_program(self):
        examples = (
            ("a creature", ("creature",)),
            ("an artifact", ("artifact",)),
            ("a land", ("land",)),
            ("a creature or planeswalker", ("creature", "planeswalker")),
            ("an artifact or creature", ("artifact", "creature")),
            ("a permanent", ()),
        )
        for phrase, types in examples:
            text = (
                "As an additional cost to cast this spell, sacrifice "
                f"{phrase}.\nDraw two cards."
            )
            with self.subTest(phrase=phrase):
                ir = self.compile(text)
                self.assertEqual("exact", ir.status, ir.to_dict())
                self.assertEqual(1, len(ir.faces[0].nodes))
                node = ir.faces[0].nodes[0]
                self.assertEqual(text, node.text)
                self.assertEqual(text, text[node.span.start : node.span.end])
                self.assertEqual(sacrifice_cost(*types), node.cost)
                self.assertEqual("draw", node.effects[0]["op"])
                self.assertIn(
                    "casting.additional_cost.fixed_sacrifice",
                    node.capability_dependencies,
                )
                programs = generated_programs(
                    _NoRulingsDatabase(),  # type: ignore[arg-type]
                    fixture_card(text),
                    trust_level="trusted",
                    capability_registry=self.capabilities,
                    capability_profile="commander_review",
                )
                self.assertEqual(1, len(programs))
                self.assertEqual(sacrifice_cost(*types), programs[0].cost_schema)
                self.assertFalse(programs[0].requires_arbiter)

    def test_fixed_sacrifice_cost_grammar_fails_closed(self):
        unsupported = (
            "sacrifice two creatures with different powers",
            "sacrifice a modified creature",
            "sacrifice a creature with power 4 or greater",
            "sacrifice an tapped creature",
            "sacrifice a creature you control",
            "you may sacrifice a creature",
            "sacrifice a creature rather than pay this spell's mana cost",
        )
        for clause in unsupported:
            text = (
                f"As an additional cost to cast this spell, {clause}.\n"
                "Draw two cards."
            )
            with self.subTest(clause=clause):
                ir = self.compile(text)
                self.assertNotEqual("exact", ir.status)
                self.assertTrue(ir.material_residuals)

        for text in (
            "As an additional cost to cast this spell, sacrifice a creature.\n"
            "Draw a card.\nYou gain 1 life.",
            "As an additional cost to cast this spell, sacrifice a creature.",
            "As an additional cost to cast this spell, sacrifice an creature.\n"
            "Draw two cards.",
            "As an additional cost to cast this spell, sacrifice a artifact.\n"
            "Draw two cards.",
        ):
            with self.subTest(text=text):
                ir = self.compile(text)
                self.assertNotEqual("exact", ir.status)
                self.assertTrue(ir.material_residuals)

    def test_fixed_sacrifice_descriptor_is_closed_and_isolated(self):
        descriptor = sacrifice_cost("artifact", "creature")[
            "additional_costs"
        ][0]
        parsed = FixedSacrificeAdditionalCost.from_descriptor(descriptor)
        original = deepcopy(descriptor)
        original["predicate"]["types_any"].append("land")
        self.assertEqual(("artifact", "creature"), parsed.predicate.types_any)
        self.assertEqual(
            sacrifice_cost("artifact", "creature")["additional_costs"][0],
            parsed.to_descriptor(),
        )
        for mutation in (
            {**descriptor, "count": True},
            {**descriptor, "count": 2},
            {**descriptor, "choice_field": "cost_cards"},
            {**descriptor, "unknown": True},
            {
                **descriptor,
                "predicate": {
                    **descriptor["predicate"],
                    "types_any": ["instant"],
                },
            },
            {
                **descriptor,
                "predicate": {
                    **descriptor["predicate"],
                    "types_any": ["artifact", "creature", "land"],
                },
            },
            {
                **descriptor,
                "predicate": {
                    **descriptor["predicate"],
                    "tapped": False,
                },
            },
        ):
            with self.subTest(mutation=mutation):
                with self.assertRaises(AdditionalCostError):
                    FixedSacrificeAdditionalCost.from_descriptor(mutation)

    def test_fixed_sacrifice_capability_dependency_fails_closed(self):
        text = (
            "As an additional cost to cast this spell, sacrifice a creature.\n"
            "Draw two cards."
        )
        value = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
        dependency = next(
            row
            for row in value["capabilities"]
            if row["id"] == "zone.change.destination_replacement"
        )
        dependency["status"] = "blocked"
        dependency["blockers"] = ["test mutation"]
        registry = CapabilityRegistry(value)
        registry.mark_evidence_verified("0" * 64)
        ir = compile_oracle_card(
            fixture_card(text),
            capability_registry=registry,
            capability_profile="commander_review",
        )
        self.assertNotEqual("exact", ir.status)
        self.assertTrue(ir.material_residuals)

    def test_fixed_sacrifice_compiler_mutant_is_killed(self):
        text = (
            "As an additional cost to cast this spell, sacrifice a creature.\n"
            "Draw two cards."
        )

        def assert_exact() -> None:
            ir = self.compile(text)
            self.assertEqual("exact", ir.status)
            self.assertEqual(
                sacrifice_cost("creature"),
                ir.faces[0].nodes[0].cost,
            )

        assert_exact()
        with patch(
            "quorune.compiler.spell_additional_cost_nodes."
            "fixed_sacrifice_additional_cost_template",
            return_value=None,
        ):
            with self.assertRaises(AssertionError):
                assert_exact()

    def test_fixed_sacrifice_template_is_canonical(self):
        first = fixed_sacrifice_additional_cost_template(
            "As an additional cost to cast this spell, sacrifice "
            "a creature or artifact."
        )
        second = fixed_sacrifice_additional_cost_template(
            "As an additional cost to cast this spell, sacrifice "
            "a creature or artifact."
        )
        self.assertEqual(first, second)
        self.assertIsNotNone(first)
        assert first is not None
        self.assertEqual(("artifact", "creature"), first.permanent_types)


class FixedSacrificeAdditionalCostRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.temporary = tempfile.TemporaryDirectory()
        database = Path(cls.temporary.name) / "sacrifice-cast-cost.sqlite3"
        build_fixture_database(
            [ROOT / "tests" / "fixtures" / "scryfall-exact-lists.json"],
            database,
        )
        cls.db = CardDatabase(database)
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
    def tearDownClass(cls) -> None:
        cls.db.close()
        cls.temporary.cleanup()

    def session(self, seed: int, *, players: int = 2):
        session = make_session(
            self.db,
            self.zimone,
            self.mishra,
            players=players,
            seed=seed,
            auto_pass_empty=False,
        )
        keep_all(session)
        engine = session.engine
        engine.permissions.invalidate_current()
        engine.state.pending_decision = None
        engine.state.active_player = "A"
        engine.state.phase = "precombat_main"
        engine.state.step = "main"
        engine.state.stack.clear()
        engine.state.priority_player = "A"
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

    def stage_spell(self, session):
        engine = session.engine
        spell = self.card(engine, "A", "Diabolic Intent")
        engine.move_card(spell.object_id, "hand", log=False)
        program = engine.semantics.get(f"{spell.oracle_id}:spell:front")
        self.assertIsNotNone(program)
        self.assertEqual(sacrifice_cost("creature"), program.cost_schema)
        engine.state.players["A"].mana_pool.update({"B": 1, "C": 1})
        return spell

    @staticmethod
    def issue_priority(session) -> None:
        engine = session.engine
        engine.permissions.invalidate_current()
        engine.state.pending_decision = None
        engine._grant_priority("A")
        engine._issue_priority("A")

    def test_offer_and_submission_share_effective_type_legality(self):
        session = self.session(601211)
        engine = session.engine
        spell = self.stage_spell(session)
        witness_ref = engine.create_token(
            "A",
            name="Effective Type Witness",
            characteristics={"type_line": "Token Artifact"},
        )[0]
        witness = engine._resolve_object(
            "A", witness_ref, zones={"battlefield"}
        )
        witness.annotations["until_end_of_turn"] = {
            "add_types": ["Creature"]
        }

        action = next(
            value
            for value in engine._priority_action_hints("A")["actions"]
            if value.get("card") == spell.ref
        )
        legal = action["cost_options"][0]["choice_schema"][
            "sacrifice_cards"
        ]["legal_refs"]
        self.assertIn(witness.ref, legal)

        witness.annotations.pop("until_end_of_turn")
        before = authoritative_state_hash(engine.state)
        with self.assertRaises(GameRuleError):
            engine._cast(
                "A",
                {"card": spell.ref, "sacrifice_cards": [witness.ref]},
            )
        self.assertEqual(before, authoritative_state_hash(engine.state))
        self.assertEqual("hand", spell.zone)
        self.assertEqual("battlefield", witness.zone)

    def test_fixed_sacrifice_cost_commits_and_replays(self):
        session = self.session(601212)
        engine = session.engine
        spell = self.stage_spell(session)
        creature = self.card(engine, "A", "Birds of Paradise")
        engine.move_card(creature.object_id, "battlefield", controller="A", log=False)
        self.issue_priority(session)
        session.initial_checkpoint = checkpoint_envelope(engine.state)
        session.commands.clear()
        session.decisions.clear()

        result = session.act(
            "pilot:A",
            {
                "a": "cast",
                "card": spell.ref,
                "sacrifice_cards": [creature.ref],
            },
        )
        self.assertTrue(result.ok, result.summary)
        self.assertEqual("graveyard", creature.zone)
        self.assertEqual("stack", spell.zone)
        cast_event = next(
            event
            for event in reversed(engine.state.events)
            if event.code == "stack.cast"
        )
        self.assertEqual(
            [creature.ref], cast_event.details["additional_cost_objects"]
        )
        expected_hash = authoritative_state_hash(engine.state)

        with tempfile.TemporaryDirectory() as temporary:
            record_dir = Path(temporary) / "sacrifice-cast-cost-record"
            session.save(record_dir)
            replay = replay_record(record_dir, self.db, verify=True)
        self.assertTrue(replay["ok"], replay)
        self.assertEqual(expected_hash, replay["final_state_hash"])

    def test_sacrifice_is_not_destruction_and_uses_owner_graveyard(self):
        session = self.session(601214, players=4)
        engine = session.engine
        spell = self.stage_spell(session)
        creature = self.card(engine, "C", "Birds of Paradise")
        opposing_creature = self.card(engine, "B", "Goblin Engineer")
        engine.move_card(
            opposing_creature.object_id,
            "battlefield",
            controller="B",
            log=False,
        )
        engine.move_card(
            creature.object_id,
            "battlefield",
            controller="A",
            log=False,
        )
        creature.temporary_keywords.append("Indestructible")
        self.assertIn(
            "indestructible",
            {
                str(keyword).casefold()
                for keyword in engine._effective_card_data(creature)[
                    "keywords"
                ]
            },
        )
        self.issue_priority(session)

        action = next(
            value
            for value in engine._priority_action_hints("A")["actions"]
            if value.get("card") == spell.ref
        )
        legal = action["cost_options"][0]["choice_schema"][
            "sacrifice_cards"
        ]["legal_refs"]
        self.assertIn(creature.ref, legal)
        self.assertNotIn(opposing_creature.ref, legal)

        result = session.act(
            "pilot:A",
            {
                "a": "cast",
                "card": spell.ref,
                "sacrifice_cards": [creature.ref],
            },
        )
        self.assertTrue(result.ok, result.summary)
        self.assertEqual("C", creature.owner)
        self.assertEqual("graveyard", creature.zone)
        self.assertIn(
            creature.object_id,
            engine.state.players["C"].zones["graveyard"],
        )
        self.assertNotIn(
            creature.object_id,
            engine.state.players["A"].zones["graveyard"],
        )
        self.assertEqual("stack", spell.zone)

    def test_zone_replacement_suspends_cost_before_mutation_and_replays(self):
        session = self.session(601213, players=4)
        engine = session.engine
        spell = self.stage_spell(session)
        creature = self.card(engine, "A", "Birds of Paradise")
        engine.move_card(creature.object_id, "battlefield", controller="A", log=False)
        voidwalker = self.card(engine, "A", "Dauthi Voidwalker")
        engine.move_card(voidwalker.object_id, "battlefield", controller="B", log=False)
        engine.create_token(
            "B",
            name="",
            copy_of=voidwalker.ref,
            reason="fixed sacrifice replacement ordering witness",
        )
        self.issue_priority(session)
        session.initial_checkpoint = checkpoint_envelope(engine.state)
        session.commands.clear()
        session.decisions.clear()

        result = session.act(
            "pilot:A",
            {
                "a": "cast",
                "card": spell.ref,
                "sacrifice_cards": [creature.ref],
            },
        )
        self.assertTrue(result.ok, result.summary)
        self.assertEqual("replacement.order", engine.state.pending_decision.kind)
        self.assertEqual("battlefield", creature.zone)
        self.assertEqual("hand", spell.zone)
        self.assertFalse(engine.state.stack)
        self.assertEqual(1, engine.state.players["A"].mana_pool["B"])
        self.assertEqual(1, engine.state.players["A"].mana_pool["C"])

        projector = StateProjector(self.db, engine.state)
        projected = projector._decision("pilot:A")
        self.assertIsNotNone(projected)
        for seat in ("B", "C", "D"):
            self.assertIsNone(projector._decision(f"pilot:{seat}"))
        assert projected is not None
        selected = projected["ctx"]["options"][0]["id"]

        continuation = deepcopy(engine.state.pending_decision.continuation)
        restored = ReplacementContinuation.from_dict(continuation)
        self.assertEqual("priority_action_cost", restored.resume_kind)
        self.assertEqual("zone.change", restored.batch.events[0].kind)
        tampered = deepcopy(continuation)
        tampered["replacement_batch"]["events"][0]["payload"][
            "destination"
        ] = "hand"
        with self.assertRaisesRegex(
            ReplacementEffectError,
            "continuation event",
        ):
            ReplacementContinuation.from_dict(tampered)

        with tempfile.TemporaryDirectory() as temporary:
            record_dir = Path(temporary) / "sacrifice-replacement-record"
            session.save(record_dir)
            restarted = CommanderSession.load(self.db, record_dir)
            restarted_engine = restarted.engine
            restarted_projector = StateProjector(
                self.db, restarted_engine.state
            )
            restarted_packet = restarted_projector._decision("pilot:A")
            self.assertIsNotNone(restarted_packet)
            for seat in ("B", "C", "D"):
                self.assertIsNone(
                    restarted_projector._decision(f"pilot:{seat}")
                )
            restarted_selection = restarted_packet["ctx"]["options"][0]["id"]
            self.assertEqual(selected, restarted_selection)

            dispatched_destinations: list[tuple[str, str | None]] = []
            original_dispatch = restarted_engine._dispatch_zone_change_events

            def observe_dispatch(moved_card, *args, **kwargs):
                dispatched_destinations.append(
                    (moved_card.ref, kwargs.get("destination"))
                )
                return original_dispatch(moved_card, *args, **kwargs)

            with patch.object(
                restarted_engine,
                "_dispatch_zone_change_events",
                side_effect=observe_dispatch,
            ):
                result = restarted.act(
                    "pilot:A",
                    {"a": "choose", "replacement": restarted_selection},
                )
            self.assertTrue(result.ok, result.summary)
            current_creature = restarted_engine.state.cards[
                creature.object_id
            ]
            current_spell = restarted_engine.state.cards[spell.object_id]
            self.assertEqual("exile", current_creature.zone)
            self.assertEqual(1, current_creature.counters["void"])
            self.assertEqual("stack", current_spell.zone)
            self.assertIn(
                (current_creature.ref, "exile"),
                dispatched_destinations,
            )
            self.assertNotIn(
                (current_creature.ref, "graveyard"),
                dispatched_destinations,
            )
            expected_hash = authoritative_state_hash(restarted_engine.state)
            restarted.save(record_dir)
            replay = replay_record(record_dir, self.db, verify=True)
        self.assertTrue(replay["ok"], replay)
        self.assertEqual(expected_hash, replay["final_state_hash"])


if __name__ == "__main__":
    unittest.main()
