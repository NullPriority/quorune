from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from common import ROOT, keep_all, make_session
from quorune.ability_fragments import CURRENT_ABILITY_FRAGMENT_COVERAGE
from quorune.carddb import CardDatabase
from quorune.compiler.fixed_self_entry_counter_templates import (
    DYNAMIC_SELF_ENTRY_COUNTER_CAPABILITY,
    DYNAMIC_SELF_ENTRY_COUNTER_TEMPLATE,
    FIXED_SELF_ENTRY_COUNTER_CAPABILITY,
    FIXED_SELF_ENTRY_COUNTER_TEMPLATE,
    dynamic_self_entry_counter_handler,
    fixed_self_entry_counter_handler,
)
from quorune.deck import DeckLoader
from quorune.continuous_effect_model import (
    ContinuousEffect,
    ContinuousEffectDuration,
    ContinuousEffectOrigin,
    ContinuousObjectIdentity,
    ContinuousOperation,
    Layer,
)
from quorune.continuous_effect_state import commit_continuous_effect
from quorune.fixed_keyword_entry_counters import (
    FIXED_KEYWORD_ENTRY_CAPABILITY,
    FixedKeywordEntryCounterError,
    FixedKeywordEntryCounterSpec,
)
from quorune.entry_counter_model import (
    DynamicEntryCounterAmountSpec,
    DynamicEntryCounterCalculation,
    DynamicEntryCounterValueSource,
)
from quorune.entry_counters import dynamic_entry_counter_amount
from quorune.model import CardInstance, StackItem
from quorune.object_predicate import ObjectQuerySpec
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
from quorune.semantic_runtime import zone_replacements
from quorune.semantic_runtime.self_entry_counters import (
    DYNAMIC_SELF_ENTRY_COUNTER_HANDLER_ID,
    DynamicSelfEntryCounterHandler,
)
from quorune.semantic_runtime.context import SemanticNodeError
from scripts.build_test_database import build_fixture_database


REGISTRY_PATH = ROOT / "quorune" / "rules" / "capability-registry.json"
TEMPLATE_IDS = {
    "fading-fixed-entry-counter-v1",
    "graft-fixed-entry-counter-v1",
    "vanishing-fixed-entry-counter-v1",
}
ALL_ENTRY_TEMPLATE_IDS = TEMPLATE_IDS | {
    DYNAMIC_SELF_ENTRY_COUNTER_TEMPLATE,
    FIXED_SELF_ENTRY_COUNTER_TEMPLATE,
}


def focused_database(directory: str) -> CardDatabase:
    database = Path(directory) / "fixed-keyword-entry.sqlite3"
    build_fixture_database(
        [
            ROOT / "tests" / "fixtures" / "scryfall-exact-lists.json",
            ROOT / "tests" / "fixtures" / "counter-replacement-cards.json",
            ROOT
            / "tests"
            / "fixtures"
            / "fixed-keyword-entry-counter-cards.json",
            ROOT
            / "tests"
            / "fixtures"
            / "fixed-self-entry-counter-cards.json",
        ],
        database,
    )
    return CardDatabase(database)


class FixedKeywordEntryCompilerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.db = focused_database(cls.temporary.name)
        cls.capabilities = load_default_capability_registry()

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temporary.cleanup()

    def test_fixed_keyword_entry_models_and_compiler_split_are_closed(self):
        expected = {
            "Fading Entry Fixture": ("fading", 5, "fade", "702.32a"),
            "Graft Entry Fixture": ("graft", 2, "+1/+1", "702.58a"),
            "Vanishing Entry Fixture": ("vanishing", 3, "time", "702.63a"),
        }
        for name, (mechanic, amount, counter_name, rule_id) in expected.items():
            with self.subTest(name=name):
                spec = FixedKeywordEntryCounterSpec(mechanic, amount)
                self.assertEqual(counter_name, spec.counter_name)
                self.assertEqual(rule_id, spec.rule_id)
                descriptor = spec.handler_descriptor()
                self.assertEqual(counter_name, descriptor["counter_name"])
                self.assertEqual(amount, descriptor["amount"])
                self.assertIs(False, descriptor["optional"])

                record = self.db.lookup(name)
                ir = compile_oracle_card(
                    record,
                    capability_registry=self.capabilities,
                    capability_profile="commander_review",
                )
                entry = next(
                    node
                    for node in ir.faces[0].nodes
                    if node.template_id in TEMPLATE_IDS
                )
                lifecycle = next(
                    node
                    for node in ir.faces[0].nodes
                    if node.node_id.endswith(":lifecycle")
                )
                self.assertTrue(entry.exact)
                self.assertEqual((FIXED_KEYWORD_ENTRY_CAPABILITY,), entry.capability_dependencies)
                self.assertEqual((mechanic,), entry.mechanics)
                self.assertEqual((mechanic,), lifecycle.mechanics)
                self.assertTrue(lifecycle.residual_ids)
                keyword_end = record.oracle_text.index(" (")
                self.assertEqual(
                    (0, keyword_end),
                    (entry.span.start, entry.span.end),
                )
                programs = [
                    program
                    for program in generated_programs(
                        self.db,
                        record,
                        trust_level="trusted",
                        capability_registry=self.capabilities,
                        capability_profile="commander_review",
                    )
                    if program.provenance.get("template_id") in TEMPLATE_IDS
                ]
                self.assertEqual(1, len(programs))
                self.assertTrue(programs[0].capability_closure["trusted"])

        repeated = replace(
            self.db.lookup("Fading Entry Fixture"),
            oracle_text="Fading 2, Fading 3",
            keywords=("Fading",),
        )
        repeated_programs = [
            program
            for program in generated_programs(
                self.db,
                repeated,
                trust_level="trusted",
                capability_registry=self.capabilities,
                capability_profile="commander_review",
            )
            if program.provenance.get("template_id")
            == "fading-fixed-entry-counter-v1"
        ]
        self.assertEqual(2, len(repeated_programs))
        self.assertEqual(2, len({program.key for program in repeated_programs}))
        self.assertEqual(
            [2, 3],
            sorted(program.handlers[0]["amount"] for program in repeated_programs),
        )

        for mechanic, amount in (("fading", 0), ("unknown", 1), ("graft", True)):
            with self.subTest(mechanic=mechanic, amount=amount):
                with self.assertRaises(FixedKeywordEntryCounterError):
                    FixedKeywordEntryCounterSpec(mechanic, amount)

    def test_nonfixed_forms_and_remaining_lifecycles_stay_material(self):
        source = self.db.lookup("Vanishing Entry Fixture")
        for text, keyword in (
            ("Vanishing", "Vanishing"),
            ("Vanishing X", "Vanishing"),
            ("Fading 0", "Fading"),
            ("Graft X", "Graft"),
        ):
            with self.subTest(text=text):
                ir = compile_oracle_card(
                    replace(source, oracle_text=text, keywords=(keyword,)),
                    capability_registry=self.capabilities,
                    capability_profile="commander_review",
                )
                self.assertFalse(
                    any(node.template_id in TEMPLATE_IDS for node in ir.faces[0].nodes)
                )
                self.assertTrue(ir.material_residuals)

        for name in (
            "Fading Entry Fixture",
            "Graft Entry Fixture",
            "Vanishing Entry Fixture",
        ):
            ir = compile_oracle_card(
                self.db.lookup(name),
                capability_registry=self.capabilities,
                capability_profile="commander_review",
            )
            self.assertNotEqual("exact", ir.status)
            self.assertTrue(
                any(
                    "remaining-lifecycle" in blocker
                    for residual in ir.material_residuals
                    for blocker in residual.blockers
                )
            )

    def test_fixed_keyword_entry_dependency_and_compiler_mutations_fail_closed(self):
        record = self.db.lookup("Graft Entry Fixture")
        value = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
        dependency = next(
            row
            for row in value["capabilities"]
            if row["id"] == "counter.placement.quantity_replacement"
        )
        dependency["status"] = "blocked"
        dependency["blockers"] = ["test mutation"]
        ir = compile_oracle_card(
            record,
            capability_registry=CapabilityRegistry(value),
            capability_profile="commander_review",
        )
        entry = next(
            node for node in ir.faces[0].nodes if node.template_id in TEMPLATE_IDS
        )
        self.assertFalse(entry.exact)
        self.assertTrue(entry.residual_ids)

        with patch("quorune.oracle_ir.fixed_keyword_entry_nodes", return_value=()):
            mutated = compile_oracle_card(
                record,
                capability_registry=self.capabilities,
                capability_profile="commander_review",
            )
        self.assertFalse(
            any(node.template_id in TEMPLATE_IDS for node in mutated.faces[0].nodes)
        )

    def test_fixed_self_entry_counter_grammar_is_closed(self):
        expected = {
            "Fixed Counter Entrant": ("+1/+1", 3, False),
            "Fixed Charge Vessel": ("charge", 3, False),
            "Named Counter Entrant": ("deathtouch", 1, True),
        }
        for name, (counter_name, amount, keyword_counter) in expected.items():
            with self.subTest(name=name):
                record = self.db.lookup(name)
                compiled = fixed_self_entry_counter_handler(
                    record.oracle_text,
                    source_name=record.name,
                )
                self.assertIsNotNone(compiled)
                assert compiled is not None
                self.assertEqual(FIXED_SELF_ENTRY_COUNTER_TEMPLATE, compiled[0])
                self.assertEqual(counter_name, compiled[1]["counter_name"])
                self.assertEqual(amount, compiled[1]["amount"])
                self.assertIs(False, compiled[1]["optional"])

                ir = compile_oracle_card(
                    record,
                    capability_registry=self.capabilities,
                    capability_profile="commander_review",
                )
                node = next(
                    node
                    for node in ir.faces[0].nodes
                    if node.template_id == FIXED_SELF_ENTRY_COUNTER_TEMPLATE
                )
                self.assertTrue(node.exact, ir.material_residuals)
                self.assertEqual("replacement_effect", node.kind)
                self.assertEqual("zone.change", node.event)
                self.assertEqual("all", node.active_zone)
                self.assertIn(
                    FIXED_SELF_ENTRY_COUNTER_CAPABILITY,
                    node.capability_dependencies,
                )
                self.assertEqual(
                    keyword_counter,
                    "counter.characteristic.keyword"
                    in node.capability_dependencies,
                )
                self.assertEqual(
                    record.oracle_text,
                    record.oracle_text[node.span.start : node.span.end],
                )
                programs = [
                    program
                    for program in generated_programs(
                        self.db,
                        record,
                        trust_level="trusted",
                        capability_registry=self.capabilities,
                        capability_profile="commander_review",
                    )
                    if program.provenance.get("template_id")
                    == FIXED_SELF_ENTRY_COUNTER_TEMPLATE
                ]
                self.assertEqual(1, len(programs))
                self.assertTrue(programs[0].capability_closure["trusted"])

    def test_unsupported_fixed_self_entry_forms_and_dependency_fail_closed(self):
        source = self.db.lookup("Fixed Counter Entrant")
        unsupported = (
            "This creature enters with X +1/+1 counters on it, where X is its power.",
            "This creature enters with zero +1/+1 counters on it.",
            "This creature enters with eleven +1/+1 counters on it.",
            "This creature enters with an additional +1/+1 counter on it.",
            "If you cast this spell, this creature enters with a +1/+1 counter on it.",
            "You may have this creature enter with a +1/+1 counter on it.",
            "Another creature enters with a +1/+1 counter on it.",
            "This creature enters with a +1/+1 counter and a shield counter on it.",
            "This creature enters with two +1/+1 counter on it.",
        )
        for text in unsupported:
            with self.subTest(text=text):
                ir = compile_oracle_card(
                    replace(source, oracle_text=text),
                    capability_registry=self.capabilities,
                    capability_profile="commander_review",
                )
                self.assertFalse(
                    any(
                        node.template_id == FIXED_SELF_ENTRY_COUNTER_TEMPLATE
                        for node in ir.faces[0].nodes
                    )
                )
                self.assertTrue(ir.material_residuals)

        value = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
        dependency = next(
            row
            for row in value["capabilities"]
            if row["id"] == "counter.placement.quantity_replacement"
        )
        dependency["status"] = "blocked"
        dependency["blockers"] = ["test mutation"]
        ir = compile_oracle_card(
            source,
            capability_registry=CapabilityRegistry(value),
            capability_profile="commander_review",
        )
        node = next(
            node
            for node in ir.faces[0].nodes
            if node.template_id == FIXED_SELF_ENTRY_COUNTER_TEMPLATE
        )
        self.assertFalse(node.exact)
        self.assertTrue(node.residual_ids)

    def test_fixed_self_entry_compiler_mutation_is_killed(self):
        record = self.db.lookup("Fixed Counter Entrant")
        current = compile_oracle_card(
            record,
            capability_registry=self.capabilities,
            capability_profile="commander_review",
        )
        self.assertTrue(
            any(
                node.template_id == FIXED_SELF_ENTRY_COUNTER_TEMPLATE
                for node in current.faces[0].nodes
            )
        )
        with patch(
            "quorune.compiler.runtime_templates."
            "fixed_self_entry_counter_handler",
            return_value=None,
        ):
            mutated = compile_oracle_card(
                record,
                capability_registry=self.capabilities,
                capability_profile="commander_review",
            )
        self.assertFalse(
            any(
                node.template_id == FIXED_SELF_ENTRY_COUNTER_TEMPLATE
                for node in mutated.faces[0].nodes
            )
        )
        self.assertTrue(mutated.material_residuals)

    def test_dynamic_self_entry_counter_grammar_and_capability_closure(self):
        cases = {
            "Dynamic X Entrant": ("cast_x", "multiply", 1, None),
            "Dynamic Converge Entrant": (
                "mana_colors_spent",
                "multiply",
                2,
                None,
            ),
            "Dynamic Raid Entrant": (
                "controller_attacked",
                "fixed_if_at_least",
                2,
                1,
            ),
            "Dynamic Grave Count Entrant": (
                "public_query",
                "multiply",
                1,
                None,
            ),
            "Dynamic Death Count Entrant": (
                "creatures_died",
                "multiply",
                1,
                None,
            ),
            "Dynamic Hand Cast Entrant": (
                "cast_from_hand",
                "fixed_if_at_least",
                2,
                1,
            ),
        }
        for name, expected in cases.items():
            with self.subTest(name=name):
                record = self.db.lookup(name)
                compiled = dynamic_self_entry_counter_handler(
                    record.oracle_text,
                    source_name=record.name,
                )
                self.assertIsNotNone(compiled)
                assert compiled is not None
                descriptor = compiled[1]
                node = DynamicSelfEntryCounterHandler().validate(descriptor)
                self.assertEqual(expected[0], node.amount_spec.value_source.value)
                self.assertEqual(expected[1], node.amount_spec.calculation.value)
                self.assertEqual(expected[2], node.amount_spec.coefficient)
                self.assertEqual(expected[3], node.amount_spec.minimum)
                ir = compile_oracle_card(
                    record,
                    capability_registry=self.capabilities,
                    capability_profile="commander_review",
                )
                self.assertEqual("exact", ir.status, ir.material_residuals)
                dynamic = next(
                    candidate
                    for candidate in ir.faces[0].nodes
                    if candidate.template_id
                    == DYNAMIC_SELF_ENTRY_COUNTER_TEMPLATE
                )
                self.assertEqual("replacement_effect", dynamic.kind)
                self.assertIn(
                    DYNAMIC_SELF_ENTRY_COUNTER_CAPABILITY,
                    dynamic.capability_dependencies,
                )
                self.assertEqual(
                    DYNAMIC_SELF_ENTRY_COUNTER_HANDLER_ID,
                    dynamic.handlers[0]["handler_id"],
                )
                self.assertIn(
                    CURRENT_ABILITY_FRAGMENT_COVERAGE,
                    dynamic.runtime_coverage,
                )

        descriptor = dynamic_self_entry_counter_handler(
            self.db.lookup("Dynamic X Entrant").oracle_text,
            source_name="Dynamic X Entrant",
        )[1]
        malformed = json.loads(json.dumps(descriptor))
        malformed["amount_spec"].pop("offset")
        with self.assertRaisesRegex(SemanticNodeError, "closed schema"):
            DynamicSelfEntryCounterHandler().validate(malformed)

        closed_texts = {
            "This creature enters with X +1/+1 counters on it, where X is the total life lost by your opponents this turn.": (
                "opponents_life_lost",
                "multiply",
                1,
                0,
                None,
            ),
            "This creature enters with a +1/+1 counter on it for each other spell cast this turn.": (
                "other_spells_cast",
                "multiply",
                1,
                0,
                None,
            ),
            "This creature enters with two +1/+1 counters on it if you've cast another spell this turn.": (
                "controller_other_spells_cast",
                "fixed_if_at_least",
                2,
                0,
                1,
            ),
            "This creature enters with two +1/+1 counters on it if you've cast two or more spells this turn.": (
                "controller_spells_cast",
                "fixed_if_at_least",
                2,
                0,
                2,
            ),
            "This creature enters with two +1/+1 counters on it if an opponent lost life this turn.": (
                "opponents_life_lost",
                "fixed_if_at_least",
                2,
                0,
                1,
            ),
            "This creature enters with two +1/+1 counters on it if it wasn't cast or no mana was spent to cast it.": (
                "mana_was_spent",
                "fixed_if_below",
                2,
                0,
                1,
            ),
            "This creature enters with two +1/+1 counters on it unless two or more colors of mana were spent to cast it.": (
                "mana_colors_spent",
                "fixed_if_below",
                2,
                0,
                2,
            ),
            "This creature enters with a +1/+1 counter on it plus an additional +1/+1 counter on it for each other creature you control.": (
                "public_query",
                "multiply",
                1,
                1,
                None,
            ),
        }
        for text, expected in closed_texts.items():
            with self.subTest(text=text):
                compiled = dynamic_self_entry_counter_handler(
                    text,
                    source_name="Dynamic X Entrant",
                )
                self.assertIsNotNone(compiled)
                assert compiled is not None
                amount = DynamicSelfEntryCounterHandler().validate(
                    compiled[1]
                ).amount_spec
                self.assertEqual(
                    expected,
                    (
                        amount.value_source.value,
                        amount.calculation.value,
                        amount.coefficient,
                        amount.offset,
                        amount.minimum,
                    ),
                )

    def test_dynamic_self_entry_counter_exclusions_fail_closed(self):
        source = self.db.lookup("Dynamic X Entrant")
        unsupported = (
            "This creature enters with X +1/+1 counters on it, where X is its power.",
            "This creature enters with a +1/+1 counter on it for each time it was kicked.",
            "This creature enters with two +1/+1 counters on it if a permanent left the battlefield under your control this turn.",
            "This creature enters with X +1/+1 counters on it, where X is the amount of life you've gained this turn.",
            "This creature enters with your choice of a flying counter or a reach counter on it.",
            "Each other creature you control enters with an additional +1/+1 counter on it.",
            "This creature enters with a +1/+1 counter and a shield counter on it.",
        )
        for text in unsupported:
            with self.subTest(text=text):
                ir = compile_oracle_card(
                    replace(source, oracle_text=text),
                    capability_registry=self.capabilities,
                    capability_profile="commander_review",
                )
                self.assertNotEqual("exact", ir.status)
                self.assertFalse(
                    any(
                        node.template_id == DYNAMIC_SELF_ENTRY_COUNTER_TEMPLATE
                        for node in ir.faces[0].nodes
                    )
                )
                self.assertTrue(ir.material_residuals)

        value = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
        dependency = next(
            row
            for row in value["capabilities"]
            if row["id"] == "state_query.permanent.public_state_predicate"
        )
        dependency["status"] = "blocked"
        dependency["blockers"] = ["test mutation"]
        ir = compile_oracle_card(
            self.db.lookup("Dynamic Grave Count Entrant"),
            capability_registry=CapabilityRegistry(value),
            capability_profile="commander_review",
        )
        dynamic = next(
            node
            for node in ir.faces[0].nodes
            if node.template_id == DYNAMIC_SELF_ENTRY_COUNTER_TEMPLATE
        )
        self.assertFalse(dynamic.exact)
        self.assertTrue(dynamic.residual_ids)

    def test_dynamic_self_entry_counter_compiler_mutant_is_killed(self):
        record = self.db.lookup("Dynamic X Entrant")

        def assert_dynamic() -> None:
            ir = compile_oracle_card(
                record,
                capability_registry=self.capabilities,
                capability_profile="commander_review",
            )
            self.assertTrue(
                any(
                    node.template_id == DYNAMIC_SELF_ENTRY_COUNTER_TEMPLATE
                    for node in ir.faces[0].nodes
                )
            )

        assert_dynamic()
        with patch(
            "quorune.compiler.runtime_templates."
            "dynamic_self_entry_counter_handler",
            return_value=None,
        ):
            with self.assertRaises(AssertionError):
                assert_dynamic()


class FixedKeywordEntryRuntimeTests(unittest.TestCase):
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

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temporary.cleanup()

    def session(self, seed: int, *, players: int = 2):
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
        engine.state.pending_trigger_batches.clear()
        engine.state.stack.clear()
        session.commands.clear()
        session.decisions.clear()
        return session

    def add_card(
        self,
        engine,
        *,
        seat: str,
        name: str,
        ref: str,
        zone: str,
        controller: str | None = None,
    ) -> CardInstance:
        record = self.db.lookup(name)
        current_controller = controller or seat
        public = zone in {"battlefield", "graveyard", "exile", "command", "stack"}
        card = CardInstance(
            object_id=f"fixture:{ref}",
            ref=ref,
            oracle_id=record.oracle_id,
            printed_name=record.name,
            owner=seat,
            controller=current_controller,
            zone=zone,
            zone_timestamp=engine.state.event_sequence + 1,
            known_to=list(engine.seats) if public else [seat],
            revealed_to=list(engine.seats) if public else [],
        )
        engine.state.cards[card.object_id] = card
        if zone in engine.state.players[seat].zones:
            engine.state.players[seat].zones[zone].append(card.object_id)
        return card

    def register_entry(self, engine, card: CardInstance) -> None:
        programs = [
            program
            for program in generated_programs(
                self.db,
                self.db.by_oracle_id(card.oracle_id),
                trust_level="trusted",
                capability_registry=self.capabilities,
                capability_profile="commander_review",
            )
            if program.provenance.get("template_id") in ALL_ENTRY_TEMPLATE_IDS
        ]
        self.assertEqual(1, len(programs))
        engine.semantics.put(programs[0])

    def begin_entry(
        self,
        session,
        card: CardInstance,
        *,
        x_value: int | None = None,
        mana_colors_spent: tuple[str, ...] = (),
        cast_origin: str = "hand",
        mana_spent_total: int | None = None,
    ) -> None:
        item = StackItem(
            stack_id=f"stack:{card.ref}",
            ref=f"S-{card.ref}",
            kind="spell",
            controller=card.controller,
            label=card.printed_name,
            card_object_id=card.object_id,
            x_value=x_value,
            default_destination="battlefield",
            visibility=list(session.engine.seats),
            mana_colors_spent=mana_colors_spent,
            context={
                "cast_origin": cast_origin,
                "mana_spent_total": (
                    len(mana_colors_spent)
                    if mana_spent_total is None
                    else mana_spent_total
                ),
            },
        )
        session.engine.state.stack.append(item)
        session.engine._continue_resolution(
            stack_ref=item.ref,
            effects=[],
            destination=None,
            note="Fixed keyword entry fixture",
        )

    @staticmethod
    def remove_all_abilities(
        engine,
        card: CardInstance,
        *,
        zones: tuple[str, ...] = (),
    ) -> None:
        commit_continuous_effect(
            engine.state,
            ContinuousEffect(
                effect_id=f"test:dynamic-entry-remove:{card.ref}",
                source_id="test:dynamic-entry-ability-removal",
                layer=Layer.ABILITY,
                sublayer="6",
                timestamp=engine._next_zone_timestamp(),
                operations=(ContinuousOperation("remove_all_abilities"),),
                origin=ContinuousEffectOrigin.RESOLUTION,
                duration=ContinuousEffectDuration.UNTIL_END_OF_TURN,
                applies=ObjectQuerySpec(zones=zones),
                locked_objects=(
                    ContinuousObjectIdentity(
                        object_id=card.object_id,
                        logical_object_id=card.logical_object_id,
                    ),
                ),
            ),
        )

    @staticmethod
    def replacement_options(session, seat: str) -> tuple[dict, list[str]]:
        decision = StateProjector(session.engine.card_db, session.state)._decision(
            f"pilot:{seat}"
        )
        return decision, [option["id"] for option in decision["ctx"]["options"]]

    def finish_replacements(self, session, seat: str) -> None:
        for _ in range(8):
            decision = session.state.pending_decision
            if decision is None or decision.kind != "replacement.order":
                return
            _projected, options = self.replacement_options(session, seat)
            result = session.act(
                f"pilot:{seat}",
                {
                    "action_id": "choose",
                    "choices": {"replacement": options[0]},
                },
            )
            self.assertTrue(result.ok, result.summary)
        self.fail("Fixed keyword entry replacement ordering did not converge")

    def test_fixed_keyword_entries_use_quantity_replacement(self):
        session = self.session(7025801)
        engine = session.engine
        card = self.add_card(
            engine,
            seat="A",
            name="Fading Entry Fixture",
            ref="fading-replacement",
            zone="stack",
        )
        self.add_card(
            engine,
            seat="A",
            name="Doubling Season",
            ref="doubling-season",
            zone="battlefield",
        )
        self.register_entry(engine, card)
        self.begin_entry(session, card)
        self.finish_replacements(session, "A")
        self.assertEqual("battlefield", card.zone)
        self.assertEqual(10, card.counters.get("fade"))

    def test_four_player_fixed_keyword_entry_is_seat_scoped_and_replays_exactly(self):
        session = self.session(7026301, players=4)
        engine = session.engine
        card = self.add_card(
            engine,
            seat="A",
            controller="C",
            name="Vanishing Entry Fixture",
            ref="controlled-vanishing",
            zone="stack",
        )
        self.add_card(
            engine,
            seat="C",
            name="Doubling Season",
            ref="controlled-vanishing-doubling",
            zone="battlefield",
        )
        self.add_card(
            engine,
            seat="C",
            name="Doc Samson, Super Psychiatrist",
            ref="controlled-vanishing-addition",
            zone="battlefield",
        )
        self.register_entry(engine, card)
        self.begin_entry(session, card)
        for seat in ("A", "B", "D"):
            self.assertIsNone(
                StateProjector(self.db, engine.state)._decision(f"pilot:{seat}")
            )
        projected, _options = self.replacement_options(session, "C")
        self.assertIsNotNone(projected)
        self.assertNotIn(card.object_id, json.dumps(projected, sort_keys=True))

        session.initial_checkpoint = checkpoint_envelope(engine.state)
        session.commands.clear()
        session.decisions.clear()
        self.finish_replacements(session, "C")
        self.assertEqual("battlefield", card.zone)
        self.assertEqual("C", card.controller)
        self.assertIn(card.counters.get("time"), {7, 8})
        expected_hash = authoritative_state_hash(engine.state)
        with tempfile.TemporaryDirectory() as temporary:
            record_dir = Path(temporary) / "fixed-keyword-entry-replay"
            session.save(record_dir)
            replay = replay_record(record_dir, self.db, verify=True)
        self.assertTrue(replay["ok"], replay)
        self.assertEqual(expected_hash, replay["final_state_hash"])

    def test_stale_fixed_keyword_entry_choice_aborts_without_card_mutation(self):
        session = self.session(7023201)
        engine = session.engine
        card = self.add_card(
            engine,
            seat="A",
            name="Fading Entry Fixture",
            ref="stale-fading",
            zone="stack",
        )
        self.add_card(
            engine,
            seat="A",
            name="Doubling Season",
            ref="stale-fading-doubling",
            zone="battlefield",
        )
        self.add_card(
            engine,
            seat="A",
            name="Doc Samson, Super Psychiatrist",
            ref="stale-fading-addition",
            zone="battlefield",
        )
        self.register_entry(engine, card)
        self.begin_entry(session, card)
        engine.move_card(card.object_id, "graveyard", log=False)
        before_cards = {
            object_id: (
                current.zone,
                current.logical_object_id,
                current.controller,
                dict(current.counters),
            )
            for object_id, current in engine.state.cards.items()
        }
        result = None
        for _ in range(8):
            if (
                engine.state.pending_decision is None
                or engine.state.pending_decision.kind != "replacement.order"
            ):
                break
            _projected, options = self.replacement_options(session, "A")
            result = session.act(
                "pilot:A",
                {
                    "action_id": "choose",
                    "choices": {"replacement": options[0]},
                },
            )
            if not result.ok:
                break
        self.assertIsNotNone(result)
        self.assertTrue(result.ok, result.summary)
        self.assertEqual(
            before_cards,
            {
                object_id: (
                    current.zone,
                    current.logical_object_id,
                    current.controller,
                    dict(current.counters),
                )
                for object_id, current in engine.state.cards.items()
            },
        )
        self.assertEqual("graveyard", card.zone)
        self.assertFalse(card.counters)

    def test_fixed_keyword_entry_runtime_mutation_is_killed(self):
        def assert_counter(seed: int) -> None:
            session = self.session(seed)
            card = self.add_card(
                session.engine,
                seat="A",
                name="Graft Entry Fixture",
                ref=f"graft-mutant-{seed}",
                zone="stack",
            )
            self.register_entry(session.engine, card)
            self.begin_entry(session, card)
            self.finish_replacements(session, "A")
            self.assertEqual(2, card.counters.get("+1/+1", 0))

        assert_counter(7025802)
        original = zone_replacements._zone_change_snapshot_effects

        def remove_fixed_entry_effects(host, subjects, active_sources):
            return tuple(
                effect
                for effect in original(host, subjects, active_sources)
                if not effect.effect_id.startswith(
                    "replacement.zone.self-entry-counter.v1:"
                )
            )

        with patch(
            "quorune.semantic_runtime.zone_replacements."
            "_zone_change_snapshot_effects",
            side_effect=remove_fixed_entry_effects,
        ):
            with self.assertRaises(AssertionError):
                assert_counter(7025803)

    def test_fixed_self_entry_keyword_counter_uses_shared_characteristics(self):
        session = self.session(6140100)
        engine = session.engine
        card = self.add_card(
            engine,
            seat="A",
            name="Named Counter Entrant",
            ref="fixed-self-entry-keyword",
            zone="stack",
        )
        self.register_entry(engine, card)
        self.begin_entry(session, card)
        self.finish_replacements(session, "A")
        self.assertEqual(1, card.counters.get("deathtouch"))
        self.assertIn("Deathtouch", engine._effective_card_data(card)["keywords"])

    def test_fixed_self_entry_uses_quantity_replacement(self):
        session = self.session(6140101)
        engine = session.engine
        card = self.add_card(
            engine,
            seat="A",
            name="Fixed Counter Entrant",
            ref="fixed-self-entry",
            zone="stack",
        )
        self.add_card(
            engine,
            seat="A",
            name="Doubling Season",
            ref="fixed-self-entry-doubling",
            zone="battlefield",
        )
        self.register_entry(engine, card)
        self.begin_entry(session, card)
        self.finish_replacements(session, "A")
        self.assertEqual("battlefield", card.zone)
        self.assertEqual(6, card.counters.get("+1/+1"))

    def test_four_player_fixed_self_entry_is_private_and_replays_exactly(self):
        session = self.session(6140102, players=4)
        engine = session.engine
        card = self.add_card(
            engine,
            seat="A",
            controller="C",
            name="Fixed Charge Vessel",
            ref="fixed-charge-controlled",
            zone="stack",
        )
        self.add_card(
            engine,
            seat="C",
            name="Doubling Season",
            ref="fixed-charge-doubling",
            zone="battlefield",
        )
        self.add_card(
            engine,
            seat="C",
            name="Doc Samson, Super Psychiatrist",
            ref="fixed-charge-addition",
            zone="battlefield",
        )
        self.register_entry(engine, card)
        self.begin_entry(session, card)
        for seat in ("A", "B", "D"):
            self.assertIsNone(
                StateProjector(self.db, engine.state)._decision(f"pilot:{seat}")
            )
        projected, _options = self.replacement_options(session, "C")
        self.assertIsNotNone(projected)
        self.assertNotIn(card.object_id, json.dumps(projected, sort_keys=True))

        session.initial_checkpoint = checkpoint_envelope(engine.state)
        session.commands.clear()
        session.decisions.clear()
        self.finish_replacements(session, "C")
        self.assertEqual("battlefield", card.zone)
        self.assertEqual("C", card.controller)
        self.assertIn(card.counters.get("charge"), {7, 8})
        expected_hash = authoritative_state_hash(engine.state)
        with tempfile.TemporaryDirectory() as temporary:
            record_dir = Path(temporary) / "fixed-self-entry-replay"
            session.save(record_dir)
            replay = replay_record(record_dir, self.db, verify=True)
        self.assertTrue(replay["ok"], replay)
        self.assertEqual(expected_hash, replay["final_state_hash"])

    def test_stale_fixed_self_entry_aborts_without_card_mutation(self):
        session = self.session(6140103)
        engine = session.engine
        card = self.add_card(
            engine,
            seat="A",
            name="Fixed Counter Entrant",
            ref="stale-fixed-self-entry",
            zone="stack",
        )
        self.add_card(
            engine,
            seat="A",
            name="Doubling Season",
            ref="stale-fixed-self-entry-doubling",
            zone="battlefield",
        )
        self.add_card(
            engine,
            seat="A",
            name="Doc Samson, Super Psychiatrist",
            ref="stale-fixed-self-entry-addition",
            zone="battlefield",
        )
        self.register_entry(engine, card)
        self.begin_entry(session, card)
        engine.move_card(card.object_id, "graveyard", log=False)
        before_cards = {
            object_id: (
                current.zone,
                current.logical_object_id,
                current.controller,
                dict(current.counters),
            )
            for object_id, current in engine.state.cards.items()
        }
        result = None
        for _ in range(8):
            decision = engine.state.pending_decision
            if decision is None or decision.kind != "replacement.order":
                break
            _projected, options = self.replacement_options(session, "A")
            result = session.act(
                "pilot:A",
                {
                    "action_id": "choose",
                    "choices": {"replacement": options[0]},
                },
            )
            if not result.ok:
                break
        self.assertIsNotNone(result)
        self.assertTrue(result.ok, result.summary)
        self.assertEqual(
            before_cards,
            {
                object_id: (
                    current.zone,
                    current.logical_object_id,
                    current.controller,
                    dict(current.counters),
                )
                for object_id, current in engine.state.cards.items()
            },
        )
        self.assertEqual("graveyard", card.zone)
        self.assertFalse(card.counters)

    def test_fixed_self_entry_runtime_mutation_is_killed(self):
        def assert_counter(seed: int) -> None:
            session = self.session(seed)
            card = self.add_card(
                session.engine,
                seat="A",
                name="Fixed Counter Entrant",
                ref=f"fixed-self-mutant-{seed}",
                zone="stack",
            )
            self.register_entry(session.engine, card)
            self.begin_entry(session, card)
            self.finish_replacements(session, "A")
            self.assertEqual(3, card.counters.get("+1/+1", 0))

        assert_counter(6140104)
        original = zone_replacements._zone_change_snapshot_effects

        def remove_fixed_entry_effects(host, subjects, active_sources):
            return tuple(
                effect
                for effect in original(host, subjects, active_sources)
                if not effect.effect_id.startswith(
                    "replacement.zone.self-entry-counter.v1:"
                )
            )

        with patch(
            "quorune.semantic_runtime.zone_replacements."
            "_zone_change_snapshot_effects",
            side_effect=remove_fixed_entry_effects,
        ):
            with self.assertRaises(AssertionError):
                assert_counter(6140105)

    def test_dynamic_x_and_query_amounts_use_frozen_preentry_state(self):
        x_session = self.session(6140201)
        x_card = self.add_card(
            x_session.engine,
            seat="A",
            name="Dynamic X Entrant",
            ref="dynamic-x",
            zone="stack",
        )
        self.register_entry(x_session.engine, x_card)
        self.begin_entry(x_session, x_card, x_value=3, mana_spent_total=4)
        self.assertEqual(3, x_card.counters.get("+1/+1"))

        converge_session = self.session(6140202)
        converge = self.add_card(
            converge_session.engine,
            seat="A",
            name="Dynamic Converge Entrant",
            ref="dynamic-converge",
            zone="stack",
        )
        self.register_entry(converge_session.engine, converge)
        self.begin_entry(
            converge_session,
            converge,
            mana_colors_spent=("W", "U", "G"),
            mana_spent_total=4,
        )
        self.assertEqual(6, converge.counters.get("+1/+1"))

        query_session = self.session(6140203)
        engine = query_session.engine
        for current in tuple(engine.state.cards.values()):
            if (
                current.zone == "battlefield"
                and current.controller == "A"
                and "creature"
                in engine._type_parts(
                    str(
                        engine._effective_card_data(current).get("type_line")
                        or ""
                    )
                )[0]
            ):
                engine.move_card(current.object_id, "hand", log=False)
        self.add_card(
            engine,
            seat="A",
            name="Fixed Counter Entrant",
            ref="query-creature",
            zone="battlefield",
        )
        animated = self.add_card(
            engine,
            seat="A",
            name="Fixed Charge Vessel",
            ref="query-animated-artifact",
            zone="battlefield",
        )
        engine.apply_effect(
            {"op": "add_type", "card": animated.ref, "type": "Creature"},
            actor="A",
        )
        entrant = self.add_card(
            engine,
            seat="A",
            name="Dynamic Battlefield Count Entrant",
            ref="dynamic-query",
            zone="stack",
        )
        for ref in ("dynamic-query-double-one", "dynamic-query-double-two"):
            self.add_card(
                engine,
                seat="A",
                name="Doubling Season",
                ref=ref,
                zone="battlefield",
            )
        self.register_entry(engine, entrant)
        self.begin_entry(query_session, entrant, mana_spent_total=4)
        self.assertIsNotNone(engine.state.pending_decision)
        with patch(
            "quorune.semantic_runtime.self_entry_counters."
            "dynamic_entry_counter_amount",
            side_effect=AssertionError("dynamic entry amount was recomputed"),
        ):
            self.finish_replacements(query_session, "A")
        self.assertEqual(8, entrant.counters.get("+1/+1"))

    def test_dynamic_history_and_cast_provenance_gate_entry_counters(self):
        absent_session = self.session(6140204)
        absent = self.add_card(
            absent_session.engine,
            seat="A",
            name="Dynamic Raid Entrant",
            ref="raid-absent",
            zone="stack",
        )
        self.register_entry(absent_session.engine, absent)
        self.begin_entry(absent_session, absent, mana_spent_total=3)
        self.assertNotIn("+1/+1", absent.counters)

        raid_session = self.session(6140205)
        raid_session.engine._record_turn_history("creature_attacked", actor="A")
        raid = self.add_card(
            raid_session.engine,
            seat="A",
            name="Dynamic Raid Entrant",
            ref="raid-present",
            zone="stack",
        )
        self.register_entry(raid_session.engine, raid)
        self.begin_entry(raid_session, raid, mana_spent_total=3)
        self.assertEqual(2, raid.counters.get("+1/+1"))

        death_session = self.session(6140206)
        death_session.engine._record_turn_history("creature_died", actor="A")
        death_session.engine._record_turn_history("creature_died", actor="B")
        death = self.add_card(
            death_session.engine,
            seat="A",
            name="Dynamic Death Count Entrant",
            ref="death-count",
            zone="stack",
        )
        self.register_entry(death_session.engine, death)
        self.begin_entry(death_session, death, mana_spent_total=3)
        self.assertEqual(2, death.counters.get("+1/+1"))

        hand_session = self.session(6140207)
        hand = self.add_card(
            hand_session.engine,
            seat="A",
            name="Dynamic Hand Cast Entrant",
            ref="hand-cast",
            zone="stack",
        )
        self.register_entry(hand_session.engine, hand)
        self.begin_entry(
            hand_session,
            hand,
            cast_origin="hand",
            mana_spent_total=3,
        )
        self.assertEqual(2, hand.counters.get("+1/+1"))

        grave_session = self.session(6140208)
        grave = self.add_card(
            grave_session.engine,
            seat="A",
            name="Dynamic Hand Cast Entrant",
            ref="grave-cast",
            zone="stack",
        )
        self.register_entry(grave_session.engine, grave)
        self.begin_entry(
            grave_session,
            grave,
            cast_origin="graveyard",
            mana_spent_total=3,
        )
        self.assertNotIn("+1/+1", grave.counters)

    def test_dynamic_amount_model_covers_each_closed_public_fact(self):
        session = self.session(6140219)
        engine = session.engine
        card = self.add_card(
            engine,
            seat="A",
            name="Dynamic X Entrant",
            ref="dynamic-amount-facts",
            zone="stack",
        )
        item = StackItem(
            stack_id="stack:dynamic-amount-facts",
            ref="S-dynamic-amount-facts",
            kind="spell",
            controller="A",
            label=card.printed_name,
            card_object_id=card.object_id,
            x_value=3,
            default_destination="battlefield",
            visibility=list(engine.seats),
            mana_colors_spent=("W", "U", "G"),
            context={"cast_origin": "hand", "mana_spent_total": 4},
        )
        engine.state.stack.append(item)
        engine._record_turn_history("creature_attacked", actor="A")
        engine._record_turn_history("creature_died", actor="A")
        engine._record_turn_history("creature_died", actor="B")
        engine._record_turn_history(
            "spell_cast",
            actor="A",
            object_incarnation=card.logical_object_id,
        )
        engine._record_turn_history(
            "spell_cast", actor="A", object_incarnation="other:A"
        )
        engine._record_turn_history(
            "spell_cast", actor="B", object_incarnation="other:B"
        )
        engine._record_turn_history(
            "player_lost_life",
            actor="B",
            target="B",
            target_kind="player",
            amount=4,
        )
        engine._record_turn_history(
            "player_lost_life",
            actor="A",
            target="A",
            target_kind="player",
            amount=2,
        )

        cases = (
            ("cast_x", "multiply", 2, None, 6),
            ("mana_colors_spent", "multiply", 1, None, 3),
            ("controller_attacked", "fixed_if_at_least", 2, 1, 2),
            ("controller_other_spells_cast", "multiply", 1, None, 1),
            ("controller_spells_cast", "multiply", 1, None, 2),
            ("creatures_died", "multiply", 1, None, 2),
            ("other_spells_cast", "multiply", 1, None, 2),
            ("opponents_life_lost", "multiply", 1, None, 4),
            ("cast_from_hand", "fixed_if_at_least", 2, 1, 2),
            ("mana_was_spent", "fixed_if_below", 2, 1, 0),
        )
        for source, calculation, coefficient, minimum, expected in cases:
            with self.subTest(source=source):
                spec = DynamicEntryCounterAmountSpec(
                    value_source=DynamicEntryCounterValueSource(source),
                    calculation=DynamicEntryCounterCalculation(calculation),
                    coefficient=coefficient,
                    minimum=minimum,
                )
                self.assertEqual(
                    expected,
                    dynamic_entry_counter_amount(
                        engine,
                        card=card,
                        destination_controller="A",
                        amount_spec=spec,
                        mana_colors_spent=item.mana_colors_spent,
                    ),
                )

        engine.state.stack.remove(item)
        card.zone = "graveyard"
        engine.state.turn_history.events.clear()
        engine._record_turn_history(
            "spell_cast", actor="A", object_incarnation="only-other:A"
        )
        another = DynamicEntryCounterAmountSpec(
            value_source=(
                DynamicEntryCounterValueSource.CONTROLLER_OTHER_SPELLS_CAST
            ),
            calculation=DynamicEntryCounterCalculation.FIXED_IF_AT_LEAST,
            coefficient=2,
            minimum=1,
        )
        self.assertEqual(
            2,
            dynamic_entry_counter_amount(
                engine,
                card=card,
                destination_controller="A",
                amount_spec=another,
            ),
        )

    def test_dynamic_entry_uses_committed_cast_x_and_payment_provenance(self):
        session = self.session(6140215)
        engine = session.engine
        card = self.add_card(
            engine,
            seat="A",
            name="Dynamic X Entrant",
            ref="dynamic-cast-path",
            zone="hand",
        )
        self.register_entry(engine, card)
        engine.state.players["A"].mana_pool.update({"C": 3, "G": 1})
        engine.state.active_player = "A"
        engine.state.phase = "precombat_main"
        engine.state.step = "main"
        engine.state.pending_decision = None
        engine.state.priority_player = "A"
        engine.state.priority_passes = []
        engine._cast("A", {"card": card.ref, "x": 3, "pay": "auto"})
        item = next(
            candidate
            for candidate in engine.state.stack
            if candidate.card_object_id == card.object_id
        )
        self.assertEqual(3, item.x_value)
        self.assertEqual("hand", item.context["cast_origin"])
        self.assertEqual(4, item.context["mana_spent_total"])
        engine._continue_resolution(
            stack_ref=item.ref,
            effects=[],
            destination=None,
            note="dynamic entry cast path",
        )
        self.assertEqual("battlefield", card.zone)
        self.assertEqual(3, card.counters.get("+1/+1"))

    def test_dynamic_entry_spell_copy_preserves_x_without_cast_provenance(self):
        copy_session = self.session(6140216)
        copy_engine = copy_session.engine
        original = self.add_card(
            copy_engine,
            seat="A",
            name="Dynamic X Entrant",
            ref="dynamic-copy-original",
            zone="stack",
        )
        self.register_entry(copy_engine, original)
        original_item = StackItem(
            stack_id="stack:dynamic-copy-original",
            ref="S-dynamic-copy-original",
            kind="spell",
            controller="A",
            label=original.printed_name,
            card_object_id=original.object_id,
            x_value=3,
            default_destination="battlefield",
            visibility=list(copy_engine.seats),
            mana_colors_spent=("G",),
            context={"cast_origin": "hand", "mana_spent_total": 4},
        )
        copy_engine.state.stack.append(original_item)
        copied_item = copy_engine._copy_stack_item(
            controller="A",
            target=original_item,
            targets=[],
            target_groups={},
            reason="dynamic entry copy boundary",
        )
        copy_object = copy_engine.state.cards[copied_item.card_object_id]
        copy_engine._continue_resolution(
            stack_ref=copied_item.ref,
            effects=[],
            destination=None,
            note="dynamic entry copy resolution",
        )
        self.assertEqual("battlefield", copy_object.zone)
        self.assertTrue(copy_object.is_token)
        self.assertEqual(3, copy_object.counters.get("+1/+1"))
        self.assertIn(original_item, copy_engine.state.stack)

        hand_session = self.session(6140218)
        hand_engine = hand_session.engine
        hand_original = self.add_card(
            hand_engine,
            seat="A",
            name="Dynamic Hand Cast Entrant",
            ref="dynamic-hand-copy-original",
            zone="stack",
        )
        self.register_entry(hand_engine, hand_original)
        hand_original_item = StackItem(
            stack_id="stack:dynamic-hand-copy-original",
            ref="S-dynamic-hand-copy-original",
            kind="spell",
            controller="A",
            label=hand_original.printed_name,
            card_object_id=hand_original.object_id,
            default_destination="battlefield",
            visibility=list(hand_engine.seats),
            context={"cast_origin": "hand", "mana_spent_total": 3},
        )
        hand_engine.state.stack.append(hand_original_item)
        hand_copy = hand_engine._copy_stack_item(
            controller="A",
            target=hand_original_item,
            targets=[],
            target_groups={},
            reason="dynamic entry copy cast-provenance boundary",
        )
        hand_copy_object = hand_engine.state.cards[hand_copy.card_object_id]
        hand_engine._continue_resolution(
            stack_ref=hand_copy.ref,
            effects=[],
            destination=None,
            note="dynamic hand-entry copy resolution",
        )
        self.assertEqual("battlefield", hand_copy_object.zone)
        self.assertNotIn("+1/+1", hand_copy_object.counters)

    def test_dynamic_entry_removed_current_ability_is_inapplicable(self):
        removed_session = self.session(6140217)
        removed = self.add_card(
            removed_session.engine,
            seat="A",
            name="Dynamic X Entrant",
            ref="dynamic-entry-removed",
            zone="stack",
        )
        self.register_entry(removed_session.engine, removed)
        self.remove_all_abilities(removed_session.engine, removed)
        self.assertEqual(
            (), removed_session.engine._effective_static_component_keys(removed)
        )
        self.begin_entry(
            removed_session,
            removed,
            x_value=3,
            mana_spent_total=4,
        )
        self.assertEqual("battlefield", removed.zone)
        self.assertNotIn("+1/+1", removed.counters)

    def test_dynamic_entry_ignores_origin_only_ability_removal(self):
        session = self.session(6140220)
        engine = session.engine
        entrant = self.add_card(
            engine,
            seat="A",
            name="Dynamic Grave Count Entrant",
            ref="dynamic-origin-only-removal",
            zone="graveyard",
        )
        self.add_card(
            engine,
            seat="A",
            name="Fixed Counter Entrant",
            ref="dynamic-origin-only-companion",
            zone="graveyard",
        )
        self.register_entry(engine, entrant)
        self.remove_all_abilities(
            engine,
            entrant,
            zones=("graveyard",),
        )

        engine.move_card(
            entrant.object_id,
            "battlefield",
            controller="A",
            log=False,
        )

        self.assertEqual("battlefield", entrant.zone)
        self.assertEqual(2, entrant.counters.get("+1/+1"))

    def test_dynamic_entry_obeys_prospective_battlefield_ability_removal(self):
        session = self.session(6140221)
        engine = session.engine
        entrant = self.add_card(
            engine,
            seat="A",
            name="Dynamic X Entrant",
            ref="dynamic-battlefield-removal",
            zone="stack",
        )
        self.register_entry(engine, entrant)
        self.remove_all_abilities(
            engine,
            entrant,
            zones=("battlefield",),
        )

        self.begin_entry(
            session,
            entrant,
            x_value=3,
            mana_spent_total=4,
        )

        self.assertEqual("battlefield", entrant.zone)
        self.assertNotIn("+1/+1", entrant.counters)
        self.assertEqual((), engine._effective_static_component_keys(entrant))

    def test_permanent_spell_characteristic_change_carries_through_entry(self):
        session = self.session(6140222)
        engine = session.engine
        entrant = self.add_card(
            engine,
            seat="A",
            name="Dynamic X Entrant",
            ref="dynamic-entry-type-change",
            zone="stack",
        )
        self.register_entry(engine, entrant)
        commit_continuous_effect(
            engine.state,
            ContinuousEffect(
                effect_id="test:dynamic-entry-type-change",
                source_id="test:dynamic-entry-type-change",
                layer=Layer.TYPE,
                sublayer="4",
                timestamp=engine._next_zone_timestamp(),
                operations=(
                    ContinuousOperation(
                        "add_types",
                        ["Artifact"],
                        field="card_types",
                    ),
                ),
                origin=ContinuousEffectOrigin.RESOLUTION,
                duration=ContinuousEffectDuration.UNTIL_END_OF_TURN,
                locked_objects=(
                    ContinuousObjectIdentity(
                        object_id=entrant.object_id,
                        logical_object_id=entrant.logical_object_id,
                    ),
                ),
            ),
        )

        self.begin_entry(
            session,
            entrant,
            x_value=3,
            mana_spent_total=4,
        )

        types, _subtypes, _supertypes = engine._type_parts(
            str(engine._effective_card_data(entrant).get("type_line") or "")
        )
        self.assertEqual("battlefield", entrant.zone)
        self.assertIn("artifact", types)
        self.assertEqual(3, entrant.counters.get("+1/+1"))

    def test_dynamic_zero_and_stale_entry_leave_no_counter_mutation(self):
        zero_session = self.session(6140209)
        zero = self.add_card(
            zero_session.engine,
            seat="A",
            name="Dynamic X Entrant",
            ref="dynamic-zero",
            zone="stack",
        )
        self.add_card(
            zero_session.engine,
            seat="A",
            name="Doubling Season",
            ref="dynamic-zero-doubling",
            zone="battlefield",
        )
        self.register_entry(zero_session.engine, zero)
        self.begin_entry(zero_session, zero, x_value=0, mana_spent_total=1)
        self.assertIsNone(zero_session.engine.state.pending_decision)
        self.assertEqual("battlefield", zero.zone)
        self.assertFalse(zero.counters)

        noncast_session = self.session(6140210)
        noncast = self.add_card(
            noncast_session.engine,
            seat="A",
            name="Dynamic X Entrant",
            ref="dynamic-noncast",
            zone="graveyard",
        )
        self.register_entry(noncast_session.engine, noncast)
        noncast_session.engine.move_card(
            noncast.object_id,
            "battlefield",
            controller="A",
            log=False,
        )
        self.assertFalse(noncast.counters)

        stale_session = self.session(6140211)
        stale = self.add_card(
            stale_session.engine,
            seat="A",
            name="Dynamic X Entrant",
            ref="dynamic-stale",
            zone="stack",
        )
        for name, ref in (
            ("Doubling Season", "dynamic-stale-doubling"),
            ("Doc Samson, Super Psychiatrist", "dynamic-stale-addition"),
        ):
            self.add_card(
                stale_session.engine,
                seat="A",
                name=name,
                ref=ref,
                zone="battlefield",
            )
        self.register_entry(stale_session.engine, stale)
        self.begin_entry(
            stale_session,
            stale,
            x_value=3,
            mana_spent_total=4,
        )
        stale_session.engine.move_card(stale.object_id, "graveyard", log=False)
        self.finish_replacements(stale_session, "A")
        self.assertEqual("graveyard", stale.zone)
        self.assertFalse(stale.counters)

    def test_dynamic_entry_replacement_choice_is_private_and_replays(self):
        session = self.session(6140212, players=4)
        engine = session.engine
        card = self.add_card(
            engine,
            seat="A",
            controller="C",
            name="Dynamic X Entrant",
            ref="dynamic-private",
            zone="stack",
        )
        for name, ref in (
            ("Doubling Season", "dynamic-private-doubling"),
            ("Doc Samson, Super Psychiatrist", "dynamic-private-addition"),
        ):
            self.add_card(
                engine,
                seat="C",
                name=name,
                ref=ref,
                zone="battlefield",
            )
        self.register_entry(engine, card)
        self.begin_entry(session, card, x_value=3, mana_spent_total=4)
        for seat in ("A", "B", "D"):
            self.assertIsNone(
                StateProjector(self.db, engine.state)._decision(f"pilot:{seat}")
            )
        projected, _options = self.replacement_options(session, "C")
        self.assertIsNotNone(projected)
        self.assertNotIn(card.object_id, json.dumps(projected, sort_keys=True))

        session.initial_checkpoint = checkpoint_envelope(engine.state)
        session.commands.clear()
        session.decisions.clear()
        self.finish_replacements(session, "C")
        self.assertEqual("battlefield", card.zone)
        self.assertEqual("C", card.controller)
        self.assertIn(card.counters.get("+1/+1"), {7, 8})
        expected_hash = authoritative_state_hash(engine.state)
        with tempfile.TemporaryDirectory() as temporary:
            record_dir = Path(temporary) / "dynamic-entry-replay"
            session.save(record_dir)
            replay = replay_record(record_dir, self.db, verify=True)
        self.assertTrue(replay["ok"], replay)
        self.assertEqual(expected_hash, replay["final_state_hash"])

    def test_dynamic_entry_amount_mutants_are_killed(self):
        def assert_amount(seed: int) -> None:
            session = self.session(seed)
            card = self.add_card(
                session.engine,
                seat="A",
                name="Dynamic X Entrant",
                ref=f"dynamic-mutant-{seed}",
                zone="stack",
            )
            self.register_entry(session.engine, card)
            self.begin_entry(session, card, x_value=3, mana_spent_total=4)
            self.assertEqual(3, card.counters.get("+1/+1", 0))

        assert_amount(6140213)
        with patch(
            "quorune.semantic_runtime.self_entry_counters."
            "dynamic_entry_counter_amount",
            return_value=1,
        ):
            with self.assertRaises(AssertionError):
                assert_amount(6140214)


if __name__ == "__main__":
    unittest.main()
