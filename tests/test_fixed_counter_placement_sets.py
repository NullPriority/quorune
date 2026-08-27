from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from common import ROOT, keep_all, make_session
from quorune.affected_permanents import (
    AffectedPermanentSetSpec,
    PermanentControllerRelation,
)
from quorune.carddb import CardDatabase
from quorune.compiler.counter_placement_templates import (
    FixedCounterPlacementSetTemplate,
    fixed_counter_placement_set_effect_template,
)
from quorune.counter_placement_sets import (
    CounterPlacementSetError,
    snapshot_counter_placement_set,
)
from quorune.deck import DeckLoader
from quorune.errors import GameRuleError
from quorune.model import CardInstance, StackItem
from quorune.object_predicate import (
    ObjectQuerySpec,
    PermanentStatePredicateSpec,
)
from quorune.object_query import ObjectQueryResult
from quorune.oracle_ir import compile_oracle_card
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
    PlaceCountersOnSetIntent,
    ReadOnlyHandlerContext,
    ReadOnlyRulesQuery,
)
from quorune.semantic_runtime.context import SemanticNodeError
from quorune.semantic_runtime.counter_placement_handlers import (
    FixedCounterPlacementSetHandler,
)
from quorune.semantic_runtime.executor import execute_intent_plan
from quorune.semantics import SemanticProgram
from scripts.build_test_database import build_fixture_database


REGISTRY_PATH = ROOT / "quorune" / "rules" / "capability-registry.json"


def _projected_string_values(value: object) -> set[str]:
    if isinstance(value, str):
        return {value}
    if isinstance(value, dict):
        return {
            item
            for child in value.values()
            for item in _projected_string_values(child)
        }
    if isinstance(value, (list, tuple)):
        return {
            item for child in value for item in _projected_string_values(child)
        }
    return set()


def focused_card_database(directory: str) -> CardDatabase:
    database = Path(directory) / "fixed-counter-placement-sets.sqlite3"
    build_fixture_database(
        [
            ROOT / "tests" / "fixtures" / "scryfall-exact-lists.json",
            ROOT / "tests" / "fixtures" / "counter-replacement-cards.json",
        ],
        database,
    )
    return CardDatabase(database)


class _SnapshotQuery:
    def __init__(self, rows: tuple[ObjectQueryResult, ...]):
        self.rows = rows

    def affected_permanent_active_seats(self) -> tuple[str, ...]:
        return ("A", "B", "C", "D")

    def affected_permanent_apnap_order(self) -> tuple[str, ...]:
        return ("C", "D", "A", "B")

    def affected_permanent_object_rows(
        self, actor: str
    ) -> tuple[ObjectQueryResult, ...]:
        if actor != "A":
            raise AssertionError("Unexpected actor")
        return self.rows


def _row(
    ref: str,
    *,
    controller: str,
    types: tuple[str, ...] = ("creature",),
    subtypes: tuple[str, ...] = (),
    token: bool = False,
    phased_out: bool = False,
    counters: dict[str, int] | None = None,
    entered_this_turn: bool = False,
) -> ObjectQueryResult:
    return ObjectQueryResult(
        object_id=f"object:{ref}",
        logical_object_id=f"logical:{ref}",
        ref=ref,
        printed_name=ref,
        owner=controller,
        controller=controller,
        zone="battlefield",
        types=types,
        subtypes=subtypes,
        token=token,
        phased_out=phased_out,
        counters=counters or {},
        entered_this_turn=entered_this_turn,
    )


class FixedCounterPlacementSetModelTests(unittest.TestCase):
    def test_projection_privacy_comparison_uses_exact_values(self):
        projected = {"cap": "opaque-B11-suffix", "legal_refs": ["A", "B"]}
        self.assertNotIn("B11", _projected_string_values(projected))
        projected["private_ref"] = "B11"
        self.assertIn("B11", _projected_string_values(projected))

    def test_snapshot_is_immutable_canonical_and_source_excluding(self):
        spec = AffectedPermanentSetSpec(
            query=ObjectQuerySpec(
                zones=("battlefield",),
                types_all=("creature",),
            ),
            controller_relation=PermanentControllerRelation.OPPONENTS,
            exclude_source=True,
        )
        rows = (
            _row("b-creature", controller="B"),
            _row("a-source", controller="A"),
            _row("d-creature", controller="D"),
            _row("c-creature", controller="C"),
            _row("c-land", controller="C", types=("land",)),
            _row("d-phased", controller="D", phased_out=True),
        )
        first = snapshot_counter_placement_set(
            _SnapshotQuery(rows),
            actor="A",
            spec=spec,
            source_ref="a-source",
        )
        second = snapshot_counter_placement_set(
            _SnapshotQuery(tuple(reversed(rows))),
            actor="A",
            spec=spec,
            source_ref="a-source",
        )
        self.assertEqual(first, second)
        self.assertEqual(first.fingerprint, second.fingerprint)
        self.assertEqual(
            ["c-creature", "d-creature", "b-creature"],
            [value.ref for value in first.permanents],
        )
        serialized = spec.to_dict()
        serialized["query"]["types_all"].append("artifact")
        self.assertEqual(("creature",), spec.query.types_all)

    def test_snapshot_rejects_malformed_identity_without_mutation(self):
        spec = AffectedPermanentSetSpec(
            query=ObjectQuerySpec(zones=("battlefield",))
        )
        duplicate = replace(
            _row("first", controller="B"),
            ref="second",
            object_id="object:second",
        )
        with self.assertRaises(CounterPlacementSetError):
            snapshot_counter_placement_set(
                _SnapshotQuery(
                    (_row("first", controller="B"), duplicate)
                ),
                actor="A",
                spec=spec,
            )


class FixedCounterPlacementSetCompilerTests(unittest.TestCase):
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

    def test_spell_trigger_and_activated_contexts_share_fixed_counter_set_lowering(
        self,
    ):
        contexts = (
            (
                "Put a +1/+1 counter on each creature you control.",
                "Sorcery",
                "spell_ability",
                PermanentControllerRelation.ACTOR,
                ("creature",),
                (),
                False,
            ),
            (
                "When this creature enters, put a +1/+1 counter on each other Elf you control.",
                "Creature — Human",
                "triggered_ability",
                PermanentControllerRelation.ACTOR,
                (),
                ("elf",),
                True,
            ),
            (
                "{T}: Put two charge counters on each artifact creature you control.",
                "Artifact",
                "activated_ability",
                PermanentControllerRelation.ACTOR,
                ("artifact", "creature"),
                (),
                False,
            ),
            (
                "Put a -1/-1 counter on each creature target player controls.",
                "Sorcery",
                "spell_ability",
                PermanentControllerRelation.TARGET_PLAYER,
                ("creature",),
                (),
                False,
            ),
        )
        for (
            text,
            type_line,
            kind,
            relation,
            types,
            subtypes,
            exclude_source,
        ) in contexts:
            with self.subTest(text=text):
                ir = self.compile(text, type_line=type_line)
                node = next(
                    value
                    for value in ir.faces[0].nodes
                    if value.template_id
                    and value.template_id.startswith(
                        "place-fixed-counter-set-"
                    )
                )
                self.assertEqual("exact", ir.status)
                self.assertTrue(node.exact)
                self.assertEqual(kind, node.kind)
                self.assertEqual(
                    "place_counters_on_set", node.effects[0]["op"]
                )
                spec = AffectedPermanentSetSpec.from_dict(
                    node.effects[0]["set"]
                )
                self.assertEqual(relation, spec.controller_relation)
                self.assertEqual(types, spec.query.types_all)
                self.assertEqual(subtypes, spec.query.subtypes_all)
                self.assertEqual(exclude_source, spec.exclude_source)
                self.assertIn(
                    "counter.producer.fixed_permanent_set_effect",
                    node.capability_dependencies,
                )
                if relation is PermanentControllerRelation.TARGET_PLAYER:
                    self.assertIn(
                        "target.revalidate_resolution",
                        node.capability_dependencies,
                    )
                self.assertEqual(text, text[node.span.start : node.span.end])

    def test_closed_counter_set_predicates_are_typed_and_deterministic(self):
        cases = {
            "each legendary creature you control": {
                "supertypes_all": ["legendary"]
            },
            "each token creature you control": {"token": True},
            "each untapped creature you control": {"tapped": False},
            "each green creature you control": {"colors_any": ["G"]},
            "each creature you control with flying": {
                "keywords_all": ["flying"]
            },
            "each Saga you control": {"subtypes_all": ["saga"]},
            "each Equipment you control": {
                "subtypes_all": ["equipment"]
            },
        }
        for subject, expected in cases.items():
            text = f"Put a +1/+1 counter on {subject}."
            with self.subTest(text=text):
                first = fixed_counter_placement_set_effect_template(text)
                second = fixed_counter_placement_set_effect_template(text)
                self.assertIsNotNone(first)
                self.assertEqual(first, second)
                assert first is not None
                query = first.spec.query.canonical_dict()
                self.assertTrue(expected.items() <= query.items())
                self.assertEqual(first.compiled(), second.compiled())

    def test_public_state_counter_sets_are_typed_and_deterministic(self):
        cases = (
            (
                "Put a +1/+1 counter on each creature you control with a +1/+1 counter on it.",
                PermanentControllerRelation.ACTOR,
                (),
                (),
                {
                    "entered_this_turn": False,
                    "tapped": None,
                    "counter_name": "+1/+1",
                    "minimum_counter_count": 1,
                },
            ),
            (
                "Put a +1/+1 counter on each green creature that entered this turn.",
                PermanentControllerRelation.ANY,
                ("G",),
                (),
                {
                    "entered_this_turn": True,
                    "tapped": None,
                    "counter_name": None,
                    "minimum_counter_count": None,
                },
            ),
            (
                "Put a +1/+1 counter on each Frog, Rabbit, Raccoon, or Squirrel you control that entered the battlefield this turn.",
                PermanentControllerRelation.ACTOR,
                (),
                ("frog", "rabbit", "raccoon", "squirrel"),
                {
                    "entered_this_turn": True,
                    "tapped": None,
                    "counter_name": None,
                    "minimum_counter_count": None,
                },
            ),
        )
        for text, relation, colors, subtypes, expected_state in cases:
            with self.subTest(text=text):
                first = fixed_counter_placement_set_effect_template(text)
                second = fixed_counter_placement_set_effect_template(text)
                self.assertIsNotNone(first)
                self.assertEqual(first, second)
                assert first is not None
                self.assertEqual(relation, first.spec.controller_relation)
                self.assertEqual(colors, first.spec.query.colors_any)
                self.assertEqual(subtypes, first.spec.query.subtypes_any)
                assert first.spec.query.state_predicate is not None
                self.assertEqual(
                    expected_state,
                    first.spec.query.state_predicate.to_dict(),
                )
                dependencies = capability_dependencies_for_node(
                    effects=first.effects,
                    target_schema=first.target_schema,
                    mechanic_ids=first.mechanics,
                )
                self.assertIn(
                    "state_query.permanent.public_state_predicate",
                    dependencies,
                )
                ir = self.compile(text)
                self.assertEqual("exact", ir.status)
                self.assertEqual(first.compiled(), second.compiled())

        value = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
        dependency = next(
            row
            for row in value["capabilities"]
            if row["id"] == "state_query.permanent.public_state_predicate"
        )
        dependency["status"] = "blocked"
        dependency["blockers"] = ["focused dependency mutation"]
        blocked = compile_oracle_card(
            replace(
                self.base,
                name="Fixture",
                oracle_text=(
                    "Put a +1/+1 counter on each creature that entered this turn."
                ),
                type_line="Sorcery",
                keywords=(),
                faces=(),
            ),
            capability_registry=CapabilityRegistry(value),
            capability_profile="commander_review",
        )
        self.assertNotEqual("exact", blocked.status)
        self.assertTrue(blocked.material_residuals)

    def test_target_player_set_has_exact_target_schema(self):
        template = fixed_counter_placement_set_effect_template(
            "Put a -1/-1 counter on each creature target opponent controls."
        )
        self.assertIsInstance(template, FixedCounterPlacementSetTemplate)
        assert template is not None
        self.assertEqual(
            {
                "zones": ["player"],
                "categories": ["player"],
                "count": 1,
                "player_relation": "opponent",
            },
            template.target_schema,
        )

    def test_unsupported_counter_set_variants_remain_material_residuals(self):
        texts = (
            "Put X +1/+1 counters on each creature you control.",
            "Put a +1/+1 counter on each modified creature you control.",
            "Put a +1/+1 counter on each attacking creature.",
            "Put a +1/+1 counter on each face-down creature you control.",
            "Put a +1/+1 counter on each colorless creature you control.",
            "Put two time counters on each permanent with a time counter on it.",
            "Put a +1/+1 counter on each of them.",
            "Put a +1/+1 counter on each Cat and Dog you control.",
            "Put a +1/+1 counter on each creature you control and a loyalty counter on each planeswalker you control.",
        )
        for text in texts:
            with self.subTest(text=text):
                self.assertIsNone(
                    fixed_counter_placement_set_effect_template(text)
                )
                ir = self.compile(text)
                self.assertNotEqual("exact", ir.status)
                self.assertTrue(ir.material_residuals)

    def test_counter_set_shape_and_dependency_mutants_fail_closed(self):
        template = fixed_counter_placement_set_effect_template(
            "Put a +1/+1 counter on each creature you control."
        )
        self.assertIsNotNone(template)
        assert template is not None
        self.assertEqual(
            {"counter.producer.fixed_permanent_set_effect"},
            set(
                capability_dependencies_for_node(
                    effects=template.effects,
                    target_schema=template.target_schema,
                    mechanic_ids=template.mechanics,
                )
            ),
        )
        for effect in (
            {**template.effects[0], "amount": True},
            {**template.effects[0], "amount": 0},
            {**template.effects[0], "counter": ""},
            {**template.effects[0], "source": "$controller"},
            {**template.effects[0], "unknown": True},
        ):
            with self.subTest(effect=effect):
                self.assertFalse(
                    capability_dependencies_for_node(
                        effects=(effect,),
                        target_schema=None,
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
                oracle_text=(
                    "Put a +1/+1 counter on each creature you control."
                ),
                type_line="Sorcery",
                keywords=(),
                faces=(),
            ),
            capability_registry=CapabilityRegistry(value),
            capability_profile="commander_review",
        )
        self.assertNotEqual("exact", ir.status)
        self.assertTrue(ir.material_residuals)

    def test_fixed_counter_set_compiler_mutant_is_killed(self):
        def exact() -> None:
            self.assertEqual(
                "exact",
                self.compile(
                    "Put a +1/+1 counter on each creature you control."
                ).status,
            )

        exact()
        with patch(
            "quorune.compiler.resolution_effect_templates."
            "fixed_counter_placement_set_effect_template",
            return_value=None,
        ):
            with self.assertRaises(AssertionError):
                exact()


class FixedCounterPlacementSetRuntimeTests(unittest.TestCase):
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

    @staticmethod
    def context(
        *,
        active: tuple[str, ...] = ("A", "B", "C", "D"),
        apnap: tuple[str, ...] = ("A", "B", "C", "D"),
    ) -> ReadOnlyHandlerContext:
        return ReadOnlyHandlerContext(
            actor="A",
            default_reason="Fixed counter-set fixture",
            query=ReadOnlyRulesQuery(
                seats=("A", "B", "C", "D"),
                active_seats=active,
                apnap_order=apnap,
            ),
        )

    @staticmethod
    def creature_set(
        relation: PermanentControllerRelation,
        *,
        target_controller: str | None = None,
        exclude_source: bool = False,
    ) -> AffectedPermanentSetSpec:
        return AffectedPermanentSetSpec(
            query=ObjectQuerySpec(
                zones=("battlefield",),
                types_all=("creature",),
            ),
            controller_relation=relation,
            target_controller=target_controller,
            exclude_source=exclude_source,
        )

    @staticmethod
    def effect(
        spec: AffectedPermanentSetSpec,
        *,
        source: str = "departed-source",
    ) -> dict[str, object]:
        return {
            "op": "place_counters_on_set",
            "source": source,
            "set": spec.to_dict(),
            "counter": "+1/+1",
            "amount": 1,
        }

    def test_fixed_counter_set_resolves_canonical_apnap_batch(self):
        session = self.session(12280801)
        engine = session.engine
        engine.state.active_player = "C"
        creatures = {
            seat: self.add_permanent(
                engine,
                seat=seat,
                name="Scute Swarm",
                ref=f"{seat.lower()}-set-creature",
            )
            for seat in engine.seats
        }
        self.add_permanent(
            engine,
            seat="D",
            name="Island",
            ref="d-set-land",
        )
        spec = self.creature_set(PermanentControllerRelation.OPPONENTS)
        plan = FixedCounterPlacementSetHandler().lower(
            self.effect(spec),
            self.context(apnap=("C", "D", "A", "B")),
        )
        self.assertEqual(
            (
                PlaceCountersOnSetIntent(
                    actor="A",
                    spec=spec,
                    counter_name="+1/+1",
                    amount=1,
                    reason="Fixed counter-set fixture",
                    source_ref="departed-source",
                ),
            ),
            plan.intents,
        )

        execute_intent_plan(engine, plan)

        self.assertEqual(0, creatures["A"].counters.get("+1/+1", 0))
        self.assertEqual(
            {"B": 1, "C": 1, "D": 1},
            {
                seat: creatures[seat].counters["+1/+1"]
                for seat in ("B", "C", "D")
            },
        )
        self.assertEqual(
            ["c-set-creature", "d-set-creature", "b-set-creature"],
            [
                event.details["object"]
                for event in engine.state.events
                if event.code == "counter.add"
            ][-3:],
        )

    def test_public_state_counter_set_resolves_current_membership_once(self):
        session = self.session(12280805)
        engine = session.engine
        engine.state.active_player = "C"
        self.assertGreater(engine.state.turn_sequence, 0)
        current = self.add_permanent(
            engine,
            seat="B",
            name="Scute Swarm",
            ref="current-entry-set-creature",
        )
        current.entered_battlefield_turn_sequence = engine.state.turn_sequence
        previous = self.add_permanent(
            engine,
            seat="C",
            name="Scute Swarm",
            ref="previous-entry-set-creature",
        )
        previous.entered_battlefield_turn_sequence = max(
            0, engine.state.turn_sequence - 1
        )
        template = fixed_counter_placement_set_effect_template(
            "Put a +1/+1 counter on each creature that entered this turn."
        )
        self.assertIsNotNone(template)
        assert template is not None
        plan = FixedCounterPlacementSetHandler().lower(
            self.effect(template.spec),
            self.context(apnap=("C", "D", "A", "B")),
        )
        late = self.add_permanent(
            engine,
            seat="D",
            name="Scute Swarm",
            ref="late-current-entry-set-creature",
        )
        late.entered_battlefield_turn_sequence = engine.state.turn_sequence

        execute_intent_plan(engine, plan)

        self.assertEqual(1, current.counters["+1/+1"])
        self.assertNotIn("+1/+1", previous.counters)
        self.assertEqual(1, late.counters["+1/+1"])
        self.assertEqual(
            [late.ref, current.ref],
            [
                event.details["object"]
                for event in engine.state.events
                if event.code == "counter.add"
            ][-2:],
        )

    def test_typed_counter_set_handler_rejects_malformed_effects(self):
        valid = self.effect(
            self.creature_set(PermanentControllerRelation.ACTOR)
        )
        malformed_set = dict(valid["set"])
        malformed_set["unknown"] = True
        malformed = (
            {**valid, "amount": True},
            {**valid, "amount": 0},
            {**valid, "counter": ""},
            {**valid, "source": None},
            {**valid, "unknown": True},
            {**valid, "set": malformed_set},
            {**valid, "_replacement_selections": [1]},
        )
        for effect in malformed:
            with self.subTest(effect=effect):
                with self.assertRaises(SemanticNodeError):
                    FixedCounterPlacementSetHandler().lower(
                        effect,
                        self.context(),
                    )

    def test_inactive_target_player_counter_set_rolls_back_without_mutation(
        self,
    ):
        session = self.session(12280802)
        engine = session.engine
        target = self.add_permanent(
            engine,
            seat="B",
            name="Scute Swarm",
            ref="inactive-target-creature",
        )
        spec = self.creature_set(
            PermanentControllerRelation.TARGET_PLAYER,
            target_controller="B",
        )
        plan = FixedCounterPlacementSetHandler().lower(
            self.effect(spec),
            self.context(),
        )
        engine.state.players["B"].in_game = False
        before = authoritative_state_hash(engine.state)

        with self.assertRaises(GameRuleError):
            execute_intent_plan(engine, plan)

        self.assertEqual(before, authoritative_state_hash(engine.state))
        self.assertEqual({}, target.counters)

    def test_fixed_counter_set_suspends_for_quantity_replacement(self):
        session = self.session(12280803)
        engine = session.engine
        source = self.add_permanent(
            engine,
            seat="A",
            name="Scute Swarm",
            ref="counter-set-source",
        )
        other = self.add_permanent(
            engine,
            seat="A",
            name="Scute Swarm",
            ref="counter-set-other",
        )
        other.entered_battlefield_turn_sequence = engine.state.turn_sequence
        self.add_permanent(
            engine,
            seat="A",
            name="Doubling Season",
            ref="counter-set-doubling",
        )
        self.add_permanent(
            engine,
            seat="A",
            name="Doc Samson, Super Psychiatrist",
            ref="counter-set-doc",
        )
        spec = AffectedPermanentSetSpec(
            query=ObjectQuerySpec(
                zones=("battlefield",),
                types_all=("creature",),
                state_predicate=PermanentStatePredicateSpec(
                    entered_this_turn=True
                ),
            ),
            controller_relation=PermanentControllerRelation.ACTOR,
            exclude_source=True,
        )
        program = SemanticProgram(
            key="fixture:fixed-counter-set-suspension",
            label="Fixed counter-set suspension",
            effects=[self.effect(spec, source=source.ref)],
            trust_level="provisional",
        )
        engine.semantics.put(program)
        item = StackItem(
            stack_id="fixed-counter-set-suspension",
            ref="S-fixed-counter-set-suspension",
            kind="triggered_ability",
            controller="A",
            label=program.label,
            semantic_key=program.key,
            source_object_id=source.object_id,
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
        self.assertTrue(
            all(
                "+1/+1" not in card.counters
                for card in engine.state.cards.values()
                if card.zone == "battlefield"
            )
        )

    def test_target_player_counter_set_is_seat_scoped_and_replays_exactly(
        self,
    ):
        session = self.session(12280804)
        engine = session.engine
        targets = (
            self.add_permanent(
                engine,
                seat="B",
                name="Scute Swarm",
                ref="b-target-set-one",
            ),
            self.add_permanent(
                engine,
                seat="B",
                name="Scute Swarm",
                ref="b-target-set-two",
            ),
        )
        other = self.add_permanent(
            engine,
            seat="C",
            name="Scute Swarm",
            ref="c-untargeted-set",
        )
        targets[0].counters["+1/+1"] = 1
        spec = AffectedPermanentSetSpec(
            query=ObjectQuerySpec(
                zones=("battlefield",),
                types_all=("creature",),
                state_predicate=PermanentStatePredicateSpec(
                    counter_name="+1/+1",
                    minimum_counter_count=1,
                ),
            ),
            controller_relation=PermanentControllerRelation.TARGET_PLAYER,
            target_controller="$target.0",
        )
        program = SemanticProgram(
            key="fixture:target-player-counter-set",
            label="Target player's creatures get counters",
            effects=[self.effect(spec, source="$source")],
            target_schema={
                "zones": ["player"],
                "categories": ["player"],
                "count": 1,
                "player_relation": "any",
            },
            trust_level="provisional",
        )
        engine.semantics.put(program)
        engine.state.stack.append(
            StackItem(
                stack_id="target-player-counter-set",
                ref="S-target-player-counter-set",
                kind="triggered_ability",
                controller="A",
                label=program.label,
                semantic_key=program.key,
                visibility=list(engine.seats),
                context={"trigger_target_selection_pending": True},
            )
        )
        engine.state.active_player = "A"
        engine.state.phase = "precombat_main"
        engine.state.step = "main"
        self.assertTrue(engine._begin_pending_trigger_target_selection())
        projector = StateProjector(self.db, engine.state)
        decision = projector._decision("pilot:A")
        self.assertIsNotNone(decision)
        self.assertEqual("semantic.target", decision["kind"])
        for seat in ("B", "C", "D"):
            self.assertIsNone(projector._decision(f"pilot:{seat}"))
        hidden_refs = {
            engine.state.cards[object_id].ref
            for seat in ("B", "C", "D")
            for object_id in engine.state.players[seat].zones["hand"]
        }
        projected_values = _projected_string_values(decision)
        self.assertTrue(hidden_refs.isdisjoint(projected_values))
        session.initial_checkpoint = checkpoint_envelope(engine.state)
        session.commands.clear()
        session.decisions.clear()

        result = session.act(
            "pilot:A", {"action_id": "choose", "targets": ["B"]}
        )
        self.assertTrue(result.ok, result.summary)
        for seat in engine.seats:
            result = session.act(f"pilot:{seat}", {"action_id": "pass"})
            self.assertTrue(result.ok, result.summary)
        self.assertEqual(
            [2, 0],
            [card.counters.get("+1/+1", 0) for card in targets],
        )
        self.assertEqual(0, other.counters.get("+1/+1", 0))
        expected_hash = authoritative_state_hash(engine.state)

        with tempfile.TemporaryDirectory() as temporary:
            record_dir = Path(temporary) / "fixed-counter-set-replay"
            session.save(record_dir)
            replay = replay_record(record_dir, self.db, verify=True)
        self.assertTrue(replay["ok"], replay)
        self.assertEqual(5, replay["commands"])
        self.assertEqual(expected_hash, replay["final_state_hash"])


if __name__ == "__main__":
    unittest.main()
