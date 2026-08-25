from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from common import ROOT, keep_all, make_session
from quorune.abilities import parse_activated_abilities
from quorune.activation_usage import ActivationLimit
from quorune.carddb import CardDatabase
from quorune.compiler.activated_mana_nodes import _activated_effect_material
from quorune.compiler.counter_placement_templates import (
    CounterPlacementSubject,
    FixedCounterPlacementTemplate,
    fixed_counter_placement_effect_template,
)
from quorune.compiler.unlock_frontier import _clause_families
from quorune.deck import DeckLoader
from quorune.errors import GameRuleError
from quorune.model import CardInstance, StackItem
from quorune.oracle_ir import compile_oracle_card, register_generated_programs
from quorune.projection import StateProjector
from quorune.record import (
    authoritative_state_hash,
    checkpoint_envelope,
    replay_record,
)
from quorune.rules.capabilities import (
    CapabilityRegistry,
    capability_dependencies_for_node,
    load_default_capability_registry,
)
from quorune.semantic_runtime import (
    PlaceCountersIntent,
    ReadOnlyHandlerContext,
    ReadOnlyRulesQuery,
)
from quorune.semantic_runtime.counter_placement_handlers import (
    FixedCounterPlacementHandler,
)
from quorune.semantic_runtime.context import SemanticNodeError
from quorune.semantic_runtime.executor import execute_intent_plan
from quorune.semantics import SemanticProgram
from scripts.build_test_database import build_fixture_database


REGISTRY_PATH = ROOT / "quorune" / "rules" / "capability-registry.json"


def focused_card_database(directory: str) -> CardDatabase:
    database = Path(directory) / "fixed-counter-placement.sqlite3"
    build_fixture_database(
        [
            ROOT / "tests" / "fixtures" / "scryfall-exact-lists.json",
            ROOT / "tests" / "fixtures" / "counter-replacement-cards.json",
            ROOT
            / "tests"
            / "fixtures"
            / "typed-counter-activation-tails.json",
        ],
        database,
    )
    return CardDatabase(database)


class FixedCounterPlacementCompilerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.db = focused_card_database(cls.temporary.name)
        cls.base = cls.db.lookup("Sol Ring")
        cls.capabilities = load_default_capability_registry()

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temporary.cleanup()

    def compile(self, text: str, *, type_line: str = "Sorcery"):
        return compile_oracle_card(
            replace(
                self.base,
                name="Fixture",
                oracle_text=text,
                type_line=type_line,
                keywords=(),
                faces=(),
            ),
            capability_registry=self.capabilities,
            capability_profile="commander_review",
        )

    def test_spell_trigger_and_activated_contexts_share_fixed_counter_lowering(
        self,
    ):
        contexts = (
            (
                "Put a +1/+1 counter on target creature.",
                "Sorcery",
                "spell_ability",
                "$target.0",
                1,
                "+1/+1",
            ),
            (
                "When this creature enters, put a shield counter on this creature.",
                "Creature — Human",
                "triggered_ability",
                "$source",
                1,
                "shield",
            ),
            (
                "{2}, {T}: Put two charge counters on target artifact you control.",
                "Artifact",
                "activated_ability",
                "$target.0",
                2,
                "charge",
            ),
            (
                "Put two loyalty counters on target planeswalker you control.",
                "Sorcery",
                "spell_ability",
                "$target.0",
                2,
                "loyalty",
            ),
            (
                "When this creature enters, put a defense counter on target battle.",
                "Creature — Human",
                "triggered_ability",
                "$target.0",
                1,
                "defense",
            ),
        )
        for text, type_line, kind, card, amount, counter_name in contexts:
            with self.subTest(kind=kind):
                ir = self.compile(text, type_line=type_line)
                node = next(
                    value
                    for value in ir.faces[0].nodes
                    if value.template_id
                    and value.template_id.startswith("place-fixed-counter-")
                )
                self.assertEqual("exact", ir.status)
                self.assertTrue(node.exact)
                self.assertEqual(kind, node.kind)
                self.assertEqual(
                    {
                        "op": "place_counters",
                        "card": card,
                        "counter": counter_name,
                        "amount": amount,
                        "source": "$source",
                    },
                    node.effects[0],
                )
                self.assertIn(
                    "counter.producer.fixed_effect",
                    node.capability_dependencies,
                )
                if card == "$target.0":
                    self.assertIn(
                        "target.revalidate_resolution",
                        node.capability_dependencies,
                    )
                self.assertEqual(text, text[node.span.start : node.span.end])

    def test_closed_subject_grammar_uses_typed_types_subtypes_and_relations(self):
        expected = (
            (
                "Put a +1/+1 counter on Fixture.",
                CounterPlacementSubject.SOURCE,
                None,
            ),
            (
                "Put one spore counter on target Fungus.",
                CounterPlacementSubject.TARGET,
                {"subtypes_any": ["fungus"]},
            ),
            (
                "Put three charge counters on another target artifact an opponent controls.",
                CounterPlacementSubject.TARGET,
                {
                    "types_any": ["artifact"],
                    "controller_relation": "opponent",
                    "source_exclusion": True,
                },
            ),
            (
                "Put two loyalty counters on target planeswalker you control.",
                CounterPlacementSubject.TARGET,
                {
                    "types_any": ["planeswalker"],
                    "controller_relation": "you",
                },
            ),
            (
                "Put a defense counter on target battle.",
                CounterPlacementSubject.TARGET,
                {"types_any": ["battle"]},
            ),
        )
        for text, subject, schema_fields in expected:
            with self.subTest(text=text):
                template = fixed_counter_placement_effect_template(
                    text,
                    card_name="Fixture",
                )
                self.assertIsNotNone(template)
                assert template is not None
                self.assertEqual(subject, template.subject)
                if schema_fields is not None:
                    self.assertTrue(
                        schema_fields.items()
                        <= dict(template.target_schema or {}).items()
                    )

    def test_source_subtype_descriptors_share_physical_source_lowering(self):
        expected = {
            "Aura": "enchantment",
            "Equipment": "artifact",
            "Saga": "enchantment",
            "Spacecraft": "artifact",
            "Vehicle": "artifact",
        }
        for descriptor, card_type in expected.items():
            with self.subTest(descriptor=descriptor):
                template = fixed_counter_placement_effect_template(
                    f"Put a charge counter on this {descriptor}.",
                    card_name="Source Fixture",
                )
                self.assertIsNotNone(template)
                assert template is not None
                self.assertIs(CounterPlacementSubject.SOURCE, template.subject)
                self.assertEqual(card_type, template.permanent_type)
                self.assertIsNone(template.target_schema)
                self.assertEqual("$source", template.effects[0]["card"])

    def test_pinned_source_descriptors_lower_across_trigger_and_activation_contexts(
        self,
    ):
        expected = {
            "Archery Training": ("triggered_ability", True),
            "Dreadmobile": ("activated_ability", False),
            "Festering Wound": ("triggered_ability", True),
            "Fylgja": ("activated_ability", True),
            "Gavel of the Righteous": ("triggered_ability", True),
            "Incendiary": ("triggered_ability", True),
            "Mace of the Valiant": ("triggered_ability", True),
            "Momentum": ("triggered_ability", True),
            "Private Research": ("triggered_ability", True),
            "Tourach's Gate": ("activated_ability", True),
            "Traveling Plague": ("triggered_ability", True),
            "War Balloon": ("activated_ability", True),
        }
        for name, (kind, exact) in expected.items():
            with self.subTest(name=name):
                record = self.db.lookup(name, fuzzy=False)
                ir = compile_oracle_card(
                    record,
                    capability_registry=self.capabilities,
                    capability_profile="commander_review",
                )
                nodes = tuple(
                    node
                    for face in ir.faces
                    for node in face.nodes
                    if node.kind == kind
                    and node.template_id is not None
                    and node.template_id.startswith(
                        ("fixed-counter-", "place-fixed-counter-source-")
                    )
                )
                self.assertEqual(1, len(nodes))
                node = nodes[0]
                self.assertIs(exact, node.exact)
                serialized_effects = json.dumps(node.effects, sort_keys=True)
                self.assertIn('"card": "$source"', serialized_effects)
                self.assertEqual(
                    node.text,
                    record.oracle_text[node.span.start : node.span.end],
                )

    def test_typed_activation_restriction_tails_preserve_full_source_spans(self):
        expected = {
            "Foggy Swamp Vinebender": False,
            "Invigorating Hot Spring": False,
            "Licia, Sanguine Tribune": True,
            "Tetzimoc, Primal Death": False,
            "Urtet, Remnant of Memnarch": True,
        }
        for name, exact in expected.items():
            with self.subTest(name=name):
                record = self.db.lookup(name, fuzzy=False)
                ir = compile_oracle_card(
                    record,
                    capability_registry=self.capabilities,
                    capability_profile="commander_review",
                )
                node = next(
                    node
                    for face in ir.faces
                    for node in face.nodes
                    if node.kind == "activated_ability"
                    and any(
                        effect.get("op")
                        in {"place_counters", "place_counters_on_set"}
                        for effect in node.effects
                    )
                )
                self.assertIs(exact, node.exact)
                self.assertEqual(
                    node.text,
                    record.oracle_text[node.span.start : node.span.end],
                )
                face = next(
                    face for face in ir.faces if node in face.nodes
                )
                self.assertFalse(
                    any(
                        residual.kind == "effect"
                        and residual.span.line == node.span.line
                        for residual in face.residuals
                    )
                )

        licia = self.db.lookup("Licia, Sanguine Tribune", fuzzy=False)
        ability = parse_activated_abilities(
            card_name=licia.name,
            oracle_text=licia.oracle_text,
            keywords=licia.keywords,
        )[0]
        self.assertIs(ActivationLimit.ONCE_PER_TURN, ability.activation_limit)
        self.assertEqual(
            "Put three +1/+1 counters on Licia",
            _activated_effect_material(ability),
        )
        detached = replace(ability, activation_conditions=())
        self.assertEqual(
            ability.effect_text,
            _activated_effect_material(detached),
        )

    def test_closed_activation_restriction_vocabulary_lowers_exactly(self):
        restrictions = (
            "Activate only as a sorcery.",
            "Activate only as a sorcery and only once each turn.",
            "Activate only during your turn.",
            "Activate only during your turn and only once each turn.",
            "Activate only once each turn.",
            "Activate only if it's not your turn.",
            "Activate only if you created a token this turn.",
            (
                "Activate only if there are four or more card types among "
                "cards in your graveyard."
            ),
            "Activate only if you control an artifact.",
            "Activate only if you control three or more creatures.",
        )
        for restriction in restrictions:
            with self.subTest(restriction=restriction):
                text = (
                    "{1}: Put a +1/+1 counter on this creature. "
                    f"{restriction}"
                )
                ir = self.compile(text, type_line="Creature — Human")
                node = ir.faces[0].nodes[0]
                self.assertEqual("exact", ir.status)
                self.assertTrue(node.exact)
                self.assertEqual(text, node.text)
                self.assertEqual(text, text[node.span.start : node.span.end])
                self.assertEqual("place_counters", node.effects[0]["op"])

    def test_unrepresented_activation_restriction_tails_remain_residual(self):
        variants = (
            "Activate only during your upkeep.",
            "Activate only during any upkeep step.",
            "Activate only once.",
            "Activate only during the declare blockers step.",
            "Activate only if this creature entered this turn.",
            "Activate only as a sorcery and only if you've cast a spell this turn.",
            "Activate only if an opponent lost life this turn and only once each turn.",
        )
        for restriction in variants:
            with self.subTest(restriction=restriction):
                text = (
                    "{1}: Put a +1/+1 counter on this creature. "
                    f"{restriction}"
                )
                ir = self.compile(text, type_line="Creature — Human")
                node = ir.faces[0].nodes[0]
                self.assertIsNone(node.template_id)
                self.assertNotEqual("exact", ir.status)
                self.assertTrue(ir.material_residuals)

    def test_fixed_counter_template_property_is_deterministic_for_exact_counts(
        self,
    ):
        subjects = (
            "target creature",
            "target artifact you control",
            "another target land an opponent controls",
            "target planeswalker",
            "target battle",
        )
        for amount in range(1, 11):
            plural = "counter" if amount == 1 else "counters"
            for subject in subjects:
                text = f"Put {amount} charge {plural} on {subject}."
                with self.subTest(amount=amount, subject=subject):
                    first = fixed_counter_placement_effect_template(
                        text,
                        card_name="Fixture",
                    )
                    second = fixed_counter_placement_effect_template(
                        text,
                        card_name="Fixture",
                    )
                    self.assertIsNotNone(first)
                    self.assertEqual(first, second)
                    assert first is not None
                    self.assertEqual(amount, first.effects[0]["amount"])
                    self.assertEqual(first.compiled(), second.compiled())

    def test_unsupported_fixed_counter_variants_remain_material_residuals(self):
        texts = (
            "Put up to one +1/+1 counter on target creature.",
            "Put X charge counters on target artifact.",
            "Put a poison counter on target player.",
            "Put a +1/+1 counter on target modified creature.",
            "Move a +1/+1 counter onto target creature.",
            "You may put a +1/+1 counter on target creature.",
        )
        for text in texts:
            with self.subTest(text=text):
                self.assertIsNone(
                    fixed_counter_placement_effect_template(
                        text,
                        card_name="Fixture",
                    )
                )
                ir = self.compile(text)
                self.assertNotEqual("exact", ir.status)
                self.assertTrue(ir.material_residuals)

    def test_fixed_counter_shape_and_dependency_mutants_fail_closed(self):
        template = FixedCounterPlacementTemplate(
            count=2,
            counter_name="charge",
            subject=CounterPlacementSubject.TARGET,
            permanent_type="artifact",
            controller_relation="you",
        )
        expected = {
            "counter.producer.fixed_effect",
            "target.revalidate_resolution",
        }
        self.assertEqual(
            expected,
            set(
                capability_dependencies_for_node(
                    effects=template.effects,
                    target_schema=template.target_schema,
                    mechanic_ids=template.mechanics,
                )
            ),
        )
        for effects in (
            ({**template.effects[0], "amount": True},),
            ({**template.effects[0], "amount": 0},),
            ({**template.effects[0], "card": "$target.1"},),
            ({**template.effects[0], "source": "$controller"},),
            ({**template.effects[0], "extra": True},),
        ):
            with self.subTest(effects=effects):
                self.assertFalse(
                    capability_dependencies_for_node(
                        effects=effects,
                        target_schema=template.target_schema,
                        mechanic_ids=template.mechanics,
                    )
                )
        value = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
        dependency = next(
            row
            for row in value["capabilities"]
            if row["id"] == "counter.placement.quantity_replacement"
        )
        dependency["status"] = "blocked"
        dependency["blockers"] = ["test mutation"]
        ir = compile_oracle_card(
            replace(
                self.base,
                name="Fixture",
                oracle_text="Put a +1/+1 counter on target creature.",
                type_line="Sorcery",
                keywords=(),
                faces=(),
            ),
            capability_registry=CapabilityRegistry(value),
            capability_profile="commander_review",
        )
        self.assertNotEqual("exact", ir.status)
        self.assertTrue(ir.material_residuals)

    def test_fixed_counter_compiler_template_mutant_is_killed(self):
        def exact() -> None:
            self.assertEqual(
                "exact",
                self.compile(
                    "Put a +1/+1 counter on target creature."
                ).status,
            )

        exact()
        with patch(
            "quorune.compiler.resolution_effect_templates."
            "fixed_counter_placement_effect_template",
            return_value=None,
        ):
            with self.assertRaises(AssertionError):
                exact()

    def test_unlock_frontier_distinguishes_counters_from_zone_placement(self):
        counter = _clause_families(
            "Put a +1/+1 counter on target creature.",
            kind="effect_clause",
            reason="unparsed",
        )
        battlefield = _clause_families(
            "Put a land card from your hand onto the battlefield.",
            kind="effect_clause",
            reason="unparsed",
        )
        self.assertIn("effect_clause:put-counter", counter)
        self.assertNotIn("effect_clause:put-counter", battlefield)
        self.assertIn(
            "effect_clause:put-onto-battlefield",
            battlefield,
        )


class FixedCounterPlacementRuntimeTests(unittest.TestCase):
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
        session.commands.clear()
        session.decisions.clear()
        return session

    def add_permanent(
        self,
        engine,
        *,
        seat: str,
        name: str,
        ref: str,
    ) -> CardInstance:
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
            known_to=list(engine.seats),
            revealed_to=list(engine.seats),
        )
        engine.state.cards[card.object_id] = card
        engine.state.players[seat].zones["battlefield"].append(card.object_id)
        return card

    def add_compiled_permanent(
        self,
        session,
        *,
        seat: str,
        name: str,
        ref: str,
    ) -> CardInstance:
        card = self.add_permanent(
            session.engine,
            seat=seat,
            name=name,
            ref=ref,
        )
        register_generated_programs(
            self.db,
            session.engine.semantics,
            (self.db.lookup(name, fuzzy=False),),
            trust_level="provisional",
            capability_registry=self.capabilities,
            capability_profile=session.state.config.review_profile,
            promote_exact_runtime_handlers=True,
            promote_exact_effect_programs=True,
        )
        return card

    @staticmethod
    def prepare_priority(session, *, seat: str = "A") -> None:
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
    def pass_until(session, predicate, *, limit: int = 24) -> None:
        for _ in range(limit):
            if predicate():
                return
            principals = session.pending_principals()
            if not principals:
                raise AssertionError("Resolution stopped without a decision")
            result = session.act(principals[0], {"action_id": "pass"})
            if not result.ok:
                raise AssertionError(result.summary)
        raise AssertionError("Resolution did not reach the expected state")

    def choose_replacements(self, session) -> None:
        for _ in range(12):
            decision = session.engine.state.pending_decision
            if decision is None or decision.kind != "replacement.order":
                return
            packet = StateProjector(self.db, session.state)._decision("pilot:A")
            self.assertIsNotNone(packet)
            selected = packet["ctx"]["options"][0]["id"]
            result = session.act(
                "pilot:A",
                {
                    "action_id": "choose",
                    "choices": {"replacement": selected},
                },
            )
            self.assertTrue(result.ok, result.summary)
        self.fail("Replacement sequence did not converge")

    def stage_replacement(
        self,
        engine,
        *,
        name: str,
        ref: str,
    ) -> CardInstance:
        return self.add_permanent(
            engine,
            seat="A",
            name=name,
            ref=ref,
        )

    @staticmethod
    def context() -> ReadOnlyHandlerContext:
        return ReadOnlyHandlerContext(
            actor="A",
            default_reason="Fixed counter fixture",
            query=ReadOnlyRulesQuery(
                seats=("A", "B", "C", "D"),
                active_seats=("A", "B", "C", "D"),
                apnap_order=("A", "B", "C", "D"),
            ),
        )

    def test_typed_fixed_counter_handler_places_counters_without_domain_dispatch(
        self,
    ):
        session = self.session(12260801)
        engine = session.engine
        target = self.add_permanent(
            engine,
            seat="A",
            name="Island",
            ref="typed-counter-target",
        )
        source = self.stage_replacement(
            engine,
            name="Doubling Season",
            ref="typed-counter-source",
        )
        handler = FixedCounterPlacementHandler()
        plan = handler.lower(
            {
                "op": "place_counters",
                "card": target.ref,
                "counter": "charge",
                "amount": 2,
                "source": source.ref,
            },
            self.context(),
        )
        self.assertEqual(
            (
                PlaceCountersIntent(
                    actor="A",
                    object_refs=(target.ref,),
                    counter_name="charge",
                    amount=2,
                    reason="Fixed counter fixture",
                    source_ref=source.ref,
                ),
            ),
            plan.intents,
        )
        execute_intent_plan(engine, plan)
        self.assertEqual(4, target.counters["charge"])

    def test_typed_fixed_counter_handler_rejects_malformed_effects(self):
        valid = {
            "op": "place_counters",
            "card": "target",
            "counter": "+1/+1",
            "amount": 1,
            "source": "source",
        }
        for effect in (
            {**valid, "amount": True},
            {**valid, "amount": 0},
            {**valid, "counter": ""},
            {**valid, "source": None},
            {**valid, "unknown": 1},
        ):
            with self.subTest(effect=effect):
                with self.assertRaises(SemanticNodeError):
                    FixedCounterPlacementHandler().lower(
                        effect,
                        self.context(),
                    )

    def test_stale_fixed_counter_target_fails_before_mutation(self):
        session = self.session(12260804)
        engine = session.engine
        target = self.add_permanent(
            engine,
            seat="A",
            name="Island",
            ref="stale-counter-target",
        )
        plan = FixedCounterPlacementHandler().lower(
            {
                "op": "place_counters",
                "card": target.ref,
                "counter": "charge",
                "amount": 2,
                "source": "departed-source",
            },
            self.context(),
        )
        engine.state.players["A"].zones["battlefield"].remove(target.object_id)
        engine.state.players["A"].zones["graveyard"].append(target.object_id)
        target.zone = "graveyard"
        before = authoritative_state_hash(engine.state)

        with self.assertRaises(GameRuleError):
            execute_intent_plan(engine, plan)

        self.assertEqual(before, authoritative_state_hash(engine.state))
        self.assertEqual({}, target.counters)

    def test_fixed_counter_result_does_not_require_source_to_remain(self):
        session = self.session(12260805)
        engine = session.engine
        target = self.add_permanent(
            engine,
            seat="A",
            name="Island",
            ref="source-left-counter-target",
        )
        plan = FixedCounterPlacementHandler().lower(
            {
                "op": "place_counters",
                "card": target.ref,
                "counter": "charge",
                "amount": 2,
                "source": "source-that-left",
            },
            self.context(),
        )

        execute_intent_plan(engine, plan)

        self.assertEqual(2, target.counters["charge"])

    def test_fixed_counter_effect_suspends_for_quantity_replacement(self):
        session = self.session(12260802)
        engine = session.engine
        target = self.add_permanent(
            engine,
            seat="A",
            name="Island",
            ref="suspended-counter-target",
        )
        self.stage_replacement(
            engine,
            name="Doubling Season",
            ref="suspended-doubling",
        )
        self.stage_replacement(
            engine,
            name="Doc Samson, Super Psychiatrist",
            ref="suspended-doc",
        )
        program = SemanticProgram(
            key="fixture:fixed-counter-suspension",
            label="Fixed counter suspension",
            effects=[
                {
                    "op": "place_counters",
                    "card": target.ref,
                    "counter": "+1/+1",
                    "amount": 1,
                    "source": target.ref,
                }
            ],
            trust_level="provisional",
        )
        engine.semantics.put(program)
        item = StackItem(
            stack_id="fixed-counter-suspension",
            ref="S-fixed-counter-suspension",
            kind="triggered_ability",
            controller="A",
            label=program.label,
            semantic_key=program.key,
            visibility=list(engine.seats),
        )
        engine.state.stack.append(item)

        engine._continue_resolution(
            stack_ref=item.ref,
            effects=[dict(value) for value in program.effects],
            destination=None,
            note="",
        )

        self.assertEqual("replacement.order", engine.state.pending_decision.kind)
        self.assertNotIn("+1/+1", target.counters)

    def test_four_player_fixed_counter_replacement_choice_is_seat_scoped(self):
        session = self.session(12260803, players=4)
        engine = session.engine
        target = self.add_permanent(
            engine,
            seat="A",
            name="Island",
            ref="four-player-counter-target",
        )
        self.stage_replacement(
            engine,
            name="Doubling Season",
            ref="four-player-doubling",
        )
        self.stage_replacement(
            engine,
            name="Doc Samson, Super Psychiatrist",
            ref="four-player-doc",
        )
        program = SemanticProgram(
            key="fixture:fixed-counter-replay",
            label="Fixed counter replay",
            effects=[
                {
                    "op": "place_counters",
                    "card": target.ref,
                    "counter": "charge",
                    "amount": 1,
                    "source": target.ref,
                }
            ],
            trust_level="provisional",
        )
        engine.semantics.put(program)
        engine.state.stack.append(
            StackItem(
                stack_id="fixed-counter-replay",
                ref="S-fixed-counter-replay",
                kind="triggered_ability",
                controller="A",
                label=program.label,
                semantic_key=program.key,
                visibility=list(engine.seats),
            )
        )
        engine.state.active_player = "A"
        engine.state.phase = "precombat_main"
        engine.state.step = "main"
        engine._grant_priority("A")
        engine._issue_priority("A")
        session.initial_checkpoint = checkpoint_envelope(engine.state)
        session.commands.clear()
        session.decisions.clear()

        for seat in engine.seats:
            result = session.act(f"pilot:{seat}", {"action_id": "pass"})
            self.assertTrue(result.ok, result.summary)
        self.assertEqual("replacement.order", engine.state.pending_decision.kind)
        projector = StateProjector(self.db, engine.state)
        projected = projector._decision("pilot:A")
        self.assertIsNotNone(projected)
        for seat in ("B", "C", "D"):
            self.assertIsNone(projector._decision(f"pilot:{seat}"))
        self.assertNotIn(target.object_id, json.dumps(projected, sort_keys=True))
        selected = projected["ctx"]["options"][0]["id"]
        result = session.act(
            "pilot:A",
            {
                "action_id": "choose",
                "choices": {"replacement": selected},
            },
        )
        self.assertTrue(result.ok, result.summary)
        expected_hash = authoritative_state_hash(engine.state)
        self.assertGreater(target.counters["charge"], 1)

        with tempfile.TemporaryDirectory() as temporary:
            record_dir = Path(temporary) / "fixed-counter-replay"
            session.save(record_dir)
            replay = replay_record(record_dir, self.db, verify=True)
        self.assertTrue(replay["ok"], replay)
        self.assertEqual(5, replay["commands"])
        self.assertEqual(expected_hash, replay["final_state_hash"])

    def test_vehicle_self_activation_uses_source_identity_and_replays_replacement(
        self,
    ):
        session = self.session(12260806, players=4)
        source = self.add_compiled_permanent(
            session,
            seat="A",
            name="War Balloon",
            ref="A-war-balloon",
        )
        self.stage_replacement(
            session.engine,
            name="Doubling Season",
            ref="A-war-doubling",
        )
        self.stage_replacement(
            session.engine,
            name="Doc Samson, Super Psychiatrist",
            ref="A-war-doc",
        )
        session.state.players["A"].mana_pool["C"] = 1
        self.prepare_priority(session)
        session.initial_checkpoint = checkpoint_envelope(session.state)
        session.commands.clear()
        session.decisions.clear()

        action_id = f"activate:{source.ref}:ab2"
        actions = {
            action["id"]
            for action in session.packet("pilot:A", full=True)["decision"][
                "ctx"
            ]["legal"]["actions"]
        }
        self.assertIn(action_id, actions)
        result = session.act("pilot:A", {"action_id": action_id})
        self.assertTrue(result.ok, result.summary)
        self.pass_until(
            session,
            lambda: session.state.pending_decision is not None
            and session.state.pending_decision.kind == "replacement.order",
        )
        projected = StateProjector(self.db, session.state)._decision("pilot:A")
        self.assertIsNotNone(projected)
        self.assertNotIn(source.object_id, json.dumps(projected, sort_keys=True))
        for seat in ("B", "C", "D"):
            self.assertIsNone(
                StateProjector(self.db, session.state)._decision(f"pilot:{seat}")
            )
        self.choose_replacements(session)

        self.assertGreater(source.counters["fire"], 1)
        expected_hash = authoritative_state_hash(session.state)
        with tempfile.TemporaryDirectory() as temporary:
            record_dir = Path(temporary) / "vehicle-self-counter-record"
            session.save(record_dir)
            replay = replay_record(record_dir, self.db, verify=True)
        self.assertTrue(replay["ok"], replay)
        self.assertEqual(expected_hash, replay["final_state_hash"])

    def test_controller_turn_once_per_turn_counter_activation_is_authoritative(
        self,
    ):
        session = self.session(12260807)
        source = self.add_compiled_permanent(
            session,
            seat="A",
            name="Licia, Sanguine Tribune",
            ref="A-licia",
        )
        ability = next(
            ability
            for ability in session.engine._activated_abilities(source)
            if ability.ability_id == "ab3"
        )
        session.state.active_player = "B"
        self.assertEqual(
            ("unavailable", "only_during_your_turn"),
            session.engine._ability_availability("A", source, ability),
        )

        self.prepare_priority(session)
        self.assertEqual(
            ("payable", None),
            session.engine._ability_availability("A", source, ability),
        )
        life_before_activation = session.state.players["A"].life
        result = session.act(
            "pilot:A",
            {"action_id": f"activate:{source.ref}:{ability.ability_id}"},
        )
        self.assertTrue(result.ok, result.summary)
        self.pass_until(session, lambda: source.counters.get("+1/+1") == 3)
        self.assertEqual(
            life_before_activation - 5,
            session.state.players["A"].life,
        )
        self.assertEqual(
            ("unavailable", "already_activated_this_turn"),
            session.engine._ability_availability("A", source, ability),
        )
        session.state.turn_sequence += 1
        self.assertEqual(
            ("payable", None),
            session.engine._ability_availability("A", source, ability),
        )

if __name__ == "__main__":
    unittest.main()
