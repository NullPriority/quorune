from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from common import ROOT, keep_all, make_session
from scripts.build_test_database import build_fixture_database
from quorune.carddb import CardDatabase
from quorune.rules.casting_additional_costs import (
    AdditionalCostError,
    FixedCounterPlacementAdditionalCost,
)
from quorune.compiler.spell_additional_cost_templates import (
    FixedCounterAdditionalCostTemplate,
    fixed_counter_additional_cost_template,
)
from quorune.deck import DeckLoader
from quorune.errors import GameRuleError
from quorune.model import CardInstance
from quorune.oracle_ir import (
    compile_oracle_card,
    generated_programs,
    register_generated_programs,
)
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


REGISTRY_PATH = ROOT / "quorune" / "rules" / "capability-registry.json"


def trusted_registry() -> CapabilityRegistry:
    registry = CapabilityRegistry.from_path(REGISTRY_PATH)
    registry.mark_evidence_verified("0" * 64)
    return registry


def counter_cost(
    counter_name: str = "-1/-1", amount: int = 1
) -> dict:
    return dict(
        FixedCounterAdditionalCostTemplate(
            amount=amount,
            counter_name=counter_name,
        ).cost_schema
    )


class FixedCounterAdditionalCostCompilerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        database = Path(cls.temporary.name) / "counter-cast-cost.sqlite3"
        build_fixture_database(
            [ROOT / "tests" / "fixtures" / "scryfall-exact-lists.json"],
            database,
        )
        cls.db = CardDatabase(database)
        cls.base = cls.db.lookup("Sol Ring")
        cls.capabilities = trusted_registry()

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temporary.cleanup()

    def compile(self, text: str):
        return compile_oracle_card(
            replace(
                self.base,
                name="Counter Cost Fixture",
                type_line="Sorcery",
                oracle_text=text,
                keywords=(),
                faces=(),
            ),
            capability_registry=self.capabilities,
            capability_profile="commander_review",
        )

    def test_counter_additional_cost_compiles_exact_source_spanned_node(self):
        examples = (
            (
                "As an additional cost to cast this spell, put a -1/-1 "
                "counter on a creature you control.\nDestroy target creature.",
                "-1/-1",
                1,
                "destroy",
            ),
            (
                "As an additional cost to cast this spell, put one -1/-1 "
                "counter on a creature you control.\nDraw two cards.",
                "-1/-1",
                1,
                "draw",
            ),
        )
        for text, counter_name, amount, effect_op in examples:
            with self.subTest(effect=effect_op):
                ir = self.compile(text)
                self.assertEqual("exact", ir.status)
                self.assertEqual(1, len(ir.faces[0].nodes))
                node = ir.faces[0].nodes[0]
                self.assertEqual(text, node.text)
                self.assertEqual(text, text[node.span.start : node.span.end])
                self.assertEqual(
                    counter_cost(counter_name, amount),
                    node.cost,
                )
                self.assertEqual(effect_op, node.effects[0]["op"])
                self.assertIn(
                    "casting.additional_cost.fixed_counter_placement",
                    node.capability_dependencies,
                )
                programs = generated_programs(
                    self.db,
                    replace(
                        self.base,
                        name="Counter Cost Fixture",
                        type_line="Sorcery",
                        oracle_text=text,
                        keywords=(),
                        faces=(),
                    ),
                    trust_level="trusted",
                    capability_registry=self.capabilities,
                    capability_profile="commander_review",
                )
                self.assertEqual(1, len(programs))
                self.assertEqual(counter_cost(), programs[0].cost_schema)
                self.assertFalse(programs[0].requires_arbiter)

    def test_counter_additional_cost_fails_closed_for_unsupported_grammar(self):
        examples = (
            "As an additional cost to cast this spell, put a -1/-1 counter "
            "on an artifact you control.\nDraw two cards.",
            "As an additional cost to cast this spell, you may put a -1/-1 "
            "counter on a creature you control.\nDraw two cards.",
            "As an additional cost to cast this spell, put X -1/-1 counters "
            "on a creature you control.\nDraw two cards.",
            "As an additional cost to cast this spell, put a -1/-1 counter "
            "on a creature you control or pay 2 life.\nDraw two cards.",
            "As an additional cost to cast this spell, put a -1/-1 counter "
            "on a creature you control.\nDraw a card.\nYou gain 1 life.",
        )
        for text in examples:
            with self.subTest(text=text):
                ir = self.compile(text)
                self.assertNotEqual("exact", ir.status)
                self.assertTrue(ir.material_residuals)
                if text.startswith("As an additional cost"):
                    self.assertFalse(
                        any(
                            node.exact and node.cost is None
                            for node in ir.faces[0].nodes
                        )
                    )

        descriptor = counter_cost()["additional_costs"][0]
        parsed = FixedCounterPlacementAdditionalCost.from_descriptor(
            descriptor
        )
        original = deepcopy(descriptor)
        original["predicate"]["types_all"].append("artifact")
        self.assertEqual(("creature",), parsed.predicate.types_all)
        for mutation in (
            {**descriptor, "amount": True},
            {**descriptor, "counter": None},
            {**descriptor, "choice_field": None},
            {**descriptor, "unknown": True},
        ):
            with self.subTest(mutation=mutation):
                with self.assertRaises(AdditionalCostError):
                    FixedCounterPlacementAdditionalCost.from_descriptor(
                        mutation
                    )

    def test_counter_additional_cost_capability_closure_fails_closed(self):
        text = (
            "As an additional cost to cast this spell, put a -1/-1 counter "
            "on a creature you control.\nDraw two cards."
        )
        value = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
        dependency = next(
            row
            for row in value["capabilities"]
            if row["id"] == "counter.placement.quantity_replacement"
        )
        dependency["status"] = "blocked"
        dependency["blockers"] = ["test mutation"]
        registry = CapabilityRegistry(value)
        registry.mark_evidence_verified("0" * 64)
        ir = compile_oracle_card(
            replace(
                self.base,
                name="Counter Cost Fixture",
                type_line="Sorcery",
                oracle_text=text,
                keywords=(),
                faces=(),
            ),
            capability_registry=registry,
            capability_profile="commander_review",
        )
        self.assertNotEqual("exact", ir.status)
        self.assertTrue(ir.material_residuals)

    def test_counter_additional_cost_compiler_mutant_is_killed(
        self,
    ):
        text = (
            "As an additional cost to cast this spell, put a -1/-1 counter "
            "on a creature you control.\nDraw two cards."
        )

        def assert_exact() -> None:
            ir = self.compile(text)
            self.assertEqual("exact", ir.status)
            node = ir.faces[0].nodes[0]
            self.assertEqual(text, text[node.span.start : node.span.end])
            self.assertEqual(counter_cost(), node.cost)

        assert_exact()
        with patch(
            "quorune.compiler.spell_additional_cost_nodes."
            "fixed_counter_additional_cost_template",
            return_value=None,
        ):
            with self.assertRaises(AssertionError):
                assert_exact()

    def test_fixed_counter_cost_template_property_is_deterministic(self):
        for amount in range(1, 11):
            word = str(amount)
            plural = "counter" if amount == 1 else "counters"
            text = (
                "As an additional cost to cast this spell, put "
                f"{word} charge {plural} on a creature you control."
            )
            first = fixed_counter_additional_cost_template(text)
            second = fixed_counter_additional_cost_template(text)
            self.assertEqual(first, second)
            self.assertIsNotNone(first)
            assert first is not None
            self.assertEqual(amount, first.amount)


class FixedCounterAdditionalCostRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        database = Path(cls.temporary.name) / "counter-cast-runtime.sqlite3"
        build_fixture_database(
            [
                ROOT / "tests" / "fixtures" / "scryfall-exact-lists.json",
                ROOT / "tests" / "fixtures" / "counter-replacement-cards.json",
            ],
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
    def tearDownClass(cls):
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

    def stage_spell_and_creature(self, session):
        engine = session.engine
        spell = next(
            card
            for card in engine.state.cards.values()
            if card.owner == "A" and card.printed_name == "Diabolic Intent"
        )
        engine.move_card(spell.object_id, "hand", log=False)
        program = engine.semantics.get(f"{spell.oracle_id}:spell:front")
        self.assertIsNotNone(program)
        program.cost_schema = counter_cost()
        creature_ref = engine.create_token(
            "A",
            name="Counter Cost Creature",
            characteristics={
                "type_line": "Token Creature",
                "power": "5",
                "toughness": "5",
            },
        )[0]
        creature = engine._resolve_object(
            "A", creature_ref, zones={"battlefield"}
        )
        engine.state.players["A"].mana_pool.update({"B": 1, "C": 1})
        return spell, creature

    def add_permanent(self, session, name: str, ref: str) -> CardInstance:
        engine = session.engine
        record = self.db.lookup(name)
        card = CardInstance(
            object_id=f"fixture:{ref}",
            ref=ref,
            oracle_id=record.oracle_id,
            printed_name=record.name,
            owner="A",
            controller="A",
            zone="battlefield",
            zone_timestamp=engine.state.event_sequence + 1,
            known_to=list(engine.seats),
            revealed_to=list(engine.seats),
        )
        engine.state.cards[card.object_id] = card
        engine.state.players["A"].zones["battlefield"].append(card.object_id)
        return card

    @staticmethod
    def issue_priority(session) -> None:
        engine = session.engine
        engine.permissions.invalidate_current()
        engine.state.pending_decision = None
        engine._grant_priority("A")
        engine._issue_priority("A")

    def test_counter_additional_cost_compiles_and_casts(self):
        session = self.session(601201)
        engine = session.engine
        spell, creature = self.stage_spell_and_creature(session)

        action = next(
            value
            for value in engine._priority_action_hints("A")["actions"]
            if value.get("card") == spell.ref
        )
        schema = action["cost_options"][0]["choice_schema"][
            "counter_cost_card"
        ]
        self.assertEqual([creature.ref], schema["legal_refs"])
        engine._cast(
            "A",
            {"card": spell.ref, "counter_cost_card": creature.ref},
        )
        self.assertEqual(1, creature.counters["-1/-1"])
        self.assertEqual("stack", spell.zone)
        self.assertEqual(0, engine.state.players["A"].mana_pool["B"])
        self.assertEqual(0, engine.state.players["A"].mana_pool["C"])
        cast_event = next(
            event
            for event in reversed(engine.state.events)
            if event.code == "stack.cast"
        )
        self.assertIn(
            creature.ref,
            cast_event.details["additional_cost_objects"],
        )

    def test_counter_additional_cost_offer_revalidates_effective_creature(self):
        session = self.session(601202)
        engine = session.engine
        spell, creature = self.stage_spell_and_creature(session)
        island = next(
            card
            for card in engine.state.cards.values()
            if card.owner == "A" and card.printed_name == "Island"
        )
        engine.move_card(island.object_id, "battlefield", log=False)
        action = next(
            value
            for value in engine._priority_action_hints("A")["actions"]
            if value.get("card") == spell.ref
        )
        refs = action["cost_options"][0]["choice_schema"][
            "counter_cost_card"
        ]["legal_refs"]
        self.assertIn(creature.ref, refs)
        self.assertNotIn(island.ref, refs)

        creature.phased_out = True
        before = authoritative_state_hash(engine.state)
        with self.assertRaises(GameRuleError):
            engine._cast(
                "A",
                {"card": spell.ref, "counter_cost_card": creature.ref},
            )
        self.assertEqual(before, authoritative_state_hash(engine.state))
        self.assertEqual("hand", spell.zone)
        self.assertEqual({}, creature.counters)

    def test_counter_additional_cost_replacement_suspends_before_mutation_and_replays(
        self,
    ):
        session = self.session(601203, players=4)
        engine = session.engine
        spell, creature = self.stage_spell_and_creature(session)
        self.add_permanent(session, "Doc Samson, Super Psychiatrist", "A-doc")
        self.add_permanent(session, "Vorinclex, Monstrous Raider", "A-vorinclex")
        register_generated_programs(
            self.db,
            engine.semantics,
            (self.db.lookup("Vorinclex, Monstrous Raider"),),
            trust_level="provisional",
            capability_registry=trusted_registry(),
            capability_profile=engine.state.config.review_profile,
            promote_exact_runtime_handlers=True,
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
                "counter_cost_card": creature.ref,
            },
        )
        self.assertTrue(result.ok, result.summary)
        self.assertEqual("replacement.order", engine.state.pending_decision.kind)
        spell = engine.state.cards[spell.object_id]
        creature = engine.state.cards[creature.object_id]
        self.assertEqual("hand", spell.zone)
        self.assertEqual({}, creature.counters)
        self.assertEqual(1, engine.state.players["A"].mana_pool["B"])
        self.assertEqual(1, engine.state.players["A"].mana_pool["C"])
        self.assertFalse(engine.state.stack)
        projector = StateProjector(self.db, engine.state)
        projected = projector._decision("pilot:A")
        self.assertIsNotNone(projected)
        for seat in ("B", "C", "D"):
            self.assertIsNone(projector._decision(f"pilot:{seat}"))
        selected = projected["ctx"]["options"][0]["id"]

        continuation = deepcopy(engine.state.pending_decision.continuation)
        restored = ReplacementContinuation.from_dict(continuation)
        self.assertEqual("priority_action_cost", restored.resume_kind)
        self.assertEqual("cast", restored.priority_action)
        tampered = deepcopy(continuation)
        tampered["replacement_batch"]["events"][0]["payload"][
            "effect_generated"
        ] = True
        with self.assertRaisesRegex(ReplacementEffectError, "continuation event"):
            ReplacementContinuation.from_dict(tampered)

        expected = 4 if "A-doc" in selected else 3
        with tempfile.TemporaryDirectory() as temporary:
            record_dir = Path(temporary) / "counter-cast-cost-record"
            session.save(record_dir)
            restarted = CommanderSession.load(self.db, record_dir)
            restarted_projector = StateProjector(
                self.db, restarted.engine.state
            )
            restarted_packet = restarted_projector._decision("pilot:A")
            self.assertIsNotNone(restarted_packet)
            for seat in ("B", "C", "D"):
                self.assertIsNone(
                    restarted_projector._decision(f"pilot:{seat}")
                )
            restarted_selection = restarted_packet["ctx"]["options"][0]["id"]
            self.assertEqual(selected, restarted_selection)
            result = restarted.act(
                "pilot:A",
                {"a": "choose", "replacement": restarted_selection},
            )
            self.assertTrue(result.ok, result.summary)
            current_creature = restarted.engine.state.cards[
                creature.object_id
            ]
            self.assertEqual(expected, current_creature.counters["-1/-1"])
            self.assertEqual(
                "stack", restarted.engine.state.cards[spell.object_id].zone
            )
            self.assertEqual(
                0, restarted.engine.state.players["A"].mana_pool["B"]
            )
            self.assertEqual(
                0, restarted.engine.state.players["A"].mana_pool["C"]
            )
            expected_hash = authoritative_state_hash(restarted.engine.state)
            restarted.save(record_dir)
            replay = replay_record(record_dir, self.db, verify=True)
        self.assertTrue(replay["ok"], replay)
        self.assertEqual(expected_hash, replay["final_state_hash"])

    def test_counter_cost_provenance_mutant_is_killed(self):
        def paid_amount() -> int:
            session = self.session(601204)
            engine = session.engine
            spell, creature = self.stage_spell_and_creature(session)
            self.add_permanent(session, "Doubling Season", "A-doubling")
            engine._cast(
                "A",
                {"card": spell.ref, "counter_cost_card": creature.ref},
            )
            current = engine.state.cards[creature.object_id]
            return current.counters["-1/-1"]

        self.assertEqual(1, paid_amount())
        original = __import__(
            "quorune.rules.casting.commit",
            fromlist=["CounterPlacementRequest"],
        ).CounterPlacementRequest

        def effect_generated_mutant(**kwargs):
            kwargs["effect_generated"] = True
            return original(**kwargs)

        with patch(
            "quorune.rules.casting.commit.CounterPlacementRequest",
            side_effect=effect_generated_mutant,
        ):
            with self.assertRaises(AssertionError):
                self.assertEqual(1, paid_amount())


if __name__ == "__main__":
    unittest.main()
