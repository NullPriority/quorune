from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from common import ROOT, keep_all, make_session
from quorune.carddb import CardDatabase
from quorune.compiler.counter_placement_templates import (
    FixedPlayerCounterPlacementTemplate,
    PlayerCounterPlacementSubject,
    fixed_player_counter_placement_effect_template,
)
from quorune.counter_state import player_counter_snapshot
from quorune.deck import DeckLoader
from quorune.errors import GameRuleError
from quorune.oracle_ir import compile_oracle_card
from quorune.model import StackItem
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
    PlacePlayerCountersIntent,
    ReadOnlyHandlerContext,
    ReadOnlyRulesQuery,
)
from quorune.semantic_runtime.context import SemanticNodeError
from quorune.semantic_runtime.counter_placement_handlers import (
    FixedPlayerCounterPlacementHandler,
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
    database = Path(directory) / "fixed-player-counter-placement.sqlite3"
    build_fixture_database(
        [ROOT / "tests" / "fixtures" / "scryfall-exact-lists.json"],
        database,
    )
    return CardDatabase(database)


class FixedPlayerCounterPlacementCompilerTests(unittest.TestCase):
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

    def test_spell_trigger_and_activated_contexts_share_fixed_player_counter_lowering(
        self,
    ):
        contexts = (
            (
                "You get {E}{E} (two energy counters).",
                "Sorcery",
                "spell_ability",
                "controller",
                None,
                2,
                "energy",
            ),
            (
                "When this creature enters, each player gets a poison counter.",
                "Creature — Rat",
                "triggered_ability",
                "each-player",
                None,
                1,
                "poison",
            ),
            (
                "{3}, {T}: You get {TK} (a ticket counter).",
                "Artifact",
                "activated_ability",
                "controller",
                None,
                1,
                "ticket",
            ),
            (
                "When this creature enters, target player gets two rad counters.",
                "Creature — Mutant",
                "triggered_ability",
                "target",
                {"zones": ["player"], "categories": ["player"], "count": 1},
                2,
                "rad",
            ),
            (
                "Each opponent gets a poison counter.",
                "Sorcery",
                "spell_ability",
                "each-opponent",
                None,
                1,
                "poison",
            ),
        )
        for text, type_line, kind, subjects, schema, amount, counter in contexts:
            with self.subTest(text=text):
                ir = self.compile(text, type_line=type_line)
                node = next(
                    value
                    for value in ir.faces[0].nodes
                    if value.template_id
                    and value.template_id.startswith(
                        "place-fixed-player-counter-"
                    )
                )
                self.assertEqual("exact", ir.status)
                self.assertTrue(node.exact)
                self.assertEqual(kind, node.kind)
                self.assertEqual("place_player_counters", node.effects[0]["op"])
                self.assertEqual(subjects, node.effects[0]["subjects"])
                self.assertEqual(amount, node.effects[0]["amount"])
                self.assertEqual(counter, node.effects[0]["counter"])
                self.assertEqual(schema, node.target_schema)
                self.assertIn(
                    "counter.producer.fixed_player_effect",
                    node.capability_dependencies,
                )
                if subjects == "target":
                    self.assertEqual("$target.0", node.effects[0]["target"])
                    self.assertIn(
                        "target.revalidate_resolution",
                        node.capability_dependencies,
                    )
                self.assertEqual(text, text[node.span.start : node.span.end])

    def test_fixed_player_counter_symbols_and_words_are_canonical(self):
        expected = (
            (
                "You get six {E} (energy counters).",
                PlayerCounterPlacementSubject.CONTROLLER,
                6,
                "energy",
                "any",
            ),
            (
                "Target opponent gets two poison counters.",
                PlayerCounterPlacementSubject.TARGET,
                2,
                "poison",
                "opponent",
            ),
            (
                "Each player gets four rad counters.",
                PlayerCounterPlacementSubject.EACH_PLAYER,
                4,
                "rad",
                "any",
            ),
            (
                "You get an experience counter.",
                PlayerCounterPlacementSubject.CONTROLLER,
                1,
                "experience",
                "any",
            ),
        )
        for text, subject, amount, counter, relation in expected:
            with self.subTest(text=text):
                first = fixed_player_counter_placement_effect_template(text)
                second = fixed_player_counter_placement_effect_template(text)
                self.assertIsNotNone(first)
                self.assertEqual(first, second)
                assert first is not None
                self.assertEqual(subject, first.subject)
                self.assertEqual(amount, first.count)
                self.assertEqual(counter, first.counter_name)
                self.assertEqual(relation, first.player_relation)
                self.assertEqual(first.compiled(), second.compiled())
        with self.assertRaises(ValueError):
            FixedPlayerCounterPlacementTemplate(
                count=1,
                counter_name=None,  # type: ignore[arg-type]
                subject=PlayerCounterPlacementSubject.CONTROLLER,
            )

    def test_unsupported_fixed_player_counter_variants_remain_material_residuals(
        self,
    ):
        compound='Target player gets a poison counter and draws a card.'
        self.assertIsNone(fixed_player_counter_placement_effect_template(compound))
        ir=self.compile(compound)
        self.assertEqual('exact',ir.status,ir.material_residuals)
        node=ir.faces[0].nodes[0]
        leaf=self.compile('Target player gets a poison counter.').faces[0].nodes[0]
        self.assertEqual(2,len(node.effects))
        self.assertEqual(leaf.effects[0],node.effects[0])
        self.assertEqual(('draw','$target.0',1),tuple(node.effects[1][key]for key in ('op','player','count')))
        self.assertEqual(1,node.target_schema['count'])
        texts = (
            "Each opponent gets X poison counters.",
            "You may get an energy counter.",
            "That player gets two rad counters.",
            "Defending player gets a poison counter.",
            "You get {E}{TK}.",
            "You get two {E}{E} (two energy counters).",
            "Target player gets a poison counter and discards a card at random.",
            "If you would get one or more counters, you get that many plus one instead.",
        )
        for text in texts:
            with self.subTest(text=text):
                self.assertIsNone(
                    fixed_player_counter_placement_effect_template(text)
                )
                ir = self.compile(text)
                self.assertNotEqual("exact", ir.status)
                self.assertTrue(ir.material_residuals)
        self.assertIsNone(
            fixed_player_counter_placement_effect_template(
                "You get {E}{E} (three energy counters)."
            )
        )

    def test_fixed_player_counter_shape_and_dependency_mutants_fail_closed(self):
        targeted = FixedPlayerCounterPlacementTemplate(
            count=2,
            counter_name="rad",
            subject=PlayerCounterPlacementSubject.TARGET,
            player_relation="opponent",
        )
        self.assertEqual(
            {
                "counter.producer.fixed_player_effect",
                "target.revalidate_resolution",
            },
            set(
                capability_dependencies_for_node(
                    effects=targeted.effects,
                    target_schema=targeted.target_schema,
                    mechanic_ids=targeted.mechanics,
                )
            ),
        )
        for effects, schema in (
            (({**targeted.effects[0], "amount": True},), targeted.target_schema),
            (({**targeted.effects[0], "amount": 0},), targeted.target_schema),
            (({**targeted.effects[0], "subjects": "that-player"},), None),
            (({**targeted.effects[0], "target": "$target.1"},), targeted.target_schema),
            (({**targeted.effects[0], "extra": True},), targeted.target_schema),
        ):
            with self.subTest(effects=effects):
                self.assertFalse(
                    capability_dependencies_for_node(
                        effects=effects,
                        target_schema=schema,
                        mechanic_ids=targeted.mechanics,
                    )
                )

        for blocked_id in (
            "counter.producer.fixed_player_effect",
            "counter.placement.quantity_replacement",
        ):
            with self.subTest(blocked_id=blocked_id):
                value = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
                capability = next(
                    row
                    for row in value["capabilities"]
                    if row["id"] == blocked_id
                )
                capability["status"] = "blocked"
                capability["blockers"] = ["test mutation"]
                ir = compile_oracle_card(
                    replace(
                        self.base,
                        name="Fixture",
                        oracle_text="Each opponent gets a poison counter.",
                        type_line="Sorcery",
                        keywords=(),
                        faces=(),
                    ),
                    capability_registry=CapabilityRegistry(value),
                    capability_profile="commander_review",
                )
                self.assertNotEqual("exact", ir.status)
                self.assertTrue(ir.material_residuals)

    def test_fixed_player_counter_compiler_mutant_is_killed(self):
        def exact() -> None:
            self.assertEqual(
                "exact",
                self.compile("Each opponent gets a poison counter.").status,
            )

        exact()
        with patch(
            "quorune.compiler.resolution_effect_templates."
            "fixed_player_counter_placement_effect_template",
            return_value=None,
        ):
            with self.assertRaises(AssertionError):
                exact()


class FixedPlayerCounterPlacementRuntimeTests(unittest.TestCase):
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

    @staticmethod
    def context(
        *,
        actor: str = "A",
        active: tuple[str, ...] = ("A", "B", "C", "D"),
        apnap: tuple[str, ...] = ("A", "B", "C", "D"),
    ) -> ReadOnlyHandlerContext:
        return ReadOnlyHandlerContext(
            actor=actor,
            default_reason="Fixed player counter fixture",
            query=ReadOnlyRulesQuery(
                seats=("A", "B", "C", "D"),
                active_seats=active,
                apnap_order=apnap,
            ),
        )

    @staticmethod
    def program(
        *,
        key: str,
        label: str,
        effect: dict[str, object],
        target_schema: dict[str, object] | None = None,
    ) -> SemanticProgram:
        return SemanticProgram(
            key=key,
            label=label,
            effects=[effect],
            target_schema=target_schema,
            trust_level="provisional",
        )

    def test_typed_player_counter_handler_uses_apnap_subject_order_and_counter_owner(
        self,
    ):
        session = self.session(12270801)
        engine = session.engine
        engine.state.active_player = "B"
        plan = FixedPlayerCounterPlacementHandler().lower(
            {
                "op": "place_player_counters",
                "subjects": "each-player",
                "counter": "energy",
                "amount": 2,
                "source": "departed-source",
            },
            self.context(apnap=("B", "C", "D", "A")),
        )
        self.assertEqual(
            (
                PlacePlayerCountersIntent(
                    actor="A",
                    player_ids=("B", "C", "D", "A"),
                    counter_name="energy",
                    amount=2,
                    reason="Fixed player counter fixture",
                    source_ref="departed-source",
                ),
            ),
            plan.intents,
        )

        execute_intent_plan(engine, plan)

        self.assertEqual(
            {seat: {"energy": 2} for seat in engine.seats},
            {
                seat: player_counter_snapshot(engine.state.players[seat])
                for seat in engine.seats
            },
        )
        counter_events = [
            event.details["player"]
            for event in engine.state.events
            if event.code == "counter.add"
        ]
        self.assertEqual(["B", "C", "D", "A"], counter_events[-4:])

    def test_fixed_player_counter_source_may_leave_before_result(self):
        session = self.session(12270802)
        plan = FixedPlayerCounterPlacementHandler().lower(
            {
                "op": "place_player_counters",
                "subjects": "controller",
                "counter": "experience",
                "amount": 1,
                "source": "source-that-left",
            },
            self.context(),
        )

        execute_intent_plan(session.engine, plan)

        self.assertEqual(1, session.engine.state.players["A"].counters["experience"])

    def test_typed_player_counter_handler_rejects_malformed_effects(self):
        valid = {
            "op": "place_player_counters",
            "subjects": "controller",
            "counter": "energy",
            "amount": 1,
            "source": "source",
        }
        malformed = (
            {**valid, "subjects": "that-player"},
            {**valid, "amount": True},
            {**valid, "amount": 0},
            {**valid, "amount": 0, "zero_is_noop": False},
            {**valid, "amount": 0, "zero_is_noop": True, "counter": "poison"},
            {**valid, "amount": 0, "zero_is_noop": True, "_replacement_selections": ["forged"]},
            {**valid, "counter": ""},
            {**valid, "source": None},
            {**valid, "unknown": 1},
            {**valid, "_replacement_selections": [1]},
            {**valid, "target": "B"},
            {**valid, "subjects": "target"},
        )
        for effect in malformed:
            with self.subTest(effect=effect):
                with self.assertRaises(SemanticNodeError):
                    FixedPlayerCounterPlacementHandler().lower(
                        effect,
                        self.context(),
                    )
        with self.assertRaises(ValueError):
            PlacePlayerCountersIntent(
                actor="A",
                player_ids="A",  # type: ignore[arg-type]
                counter_name="energy",
                amount=1,
                reason="malformed",
            )
        empty = FixedPlayerCounterPlacementHandler().lower(
            {**valid, "amount": 0, "zero_is_noop": True}, self.context())
        self.assertEqual((), empty.intents)

    def test_inactive_player_counter_target_rolls_back_without_mutation(self):
        session = self.session(12270803)
        engine = session.engine
        plan = FixedPlayerCounterPlacementHandler().lower(
            {
                "op": "place_player_counters",
                "subjects": "target",
                "target": "B",
                "counter": "rad",
                "amount": 2,
                "source": "source",
            },
            self.context(),
        )
        engine.state.players["B"].in_game = False
        before = authoritative_state_hash(engine.state)

        with self.assertRaises(GameRuleError):
            execute_intent_plan(engine, plan)

        self.assertEqual(before, authoritative_state_hash(engine.state))
        self.assertEqual({}, engine.state.players["B"].counters)

    def test_player_counter_placement_triggers_poison_sba_and_replay(self):
        session = self.session(12270804)
        engine = session.engine
        program = self.program(
            key="fixture:poison-each-opponent",
            label="Each opponent gets ten poison counters",
            effect={
                "op": "place_player_counters",
                "subjects": "each-opponent",
                "counter": "poison",
                "amount": 10,
                "source": "S-poison-each-opponent",
            },
        )
        engine.semantics.put(program)
        engine.state.stack.append(
            StackItem(
                stack_id="poison-each-opponent",
                ref="S-poison-each-opponent",
                kind="spell",
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

        self.assertTrue(engine.state.game_over)
        self.assertEqual("A", engine.state.winner)
        self.assertEqual(
            {"B": 10, "C": 10, "D": 10},
            {seat: engine.state.players[seat].poison for seat in ("B", "C", "D")},
        )
        expected_hash = authoritative_state_hash(engine.state)
        with tempfile.TemporaryDirectory() as temporary:
            record_dir = Path(temporary) / "fixed-player-poison-replay"
            session.save(record_dir)
            replay = replay_record(record_dir, self.db, verify=True)
        self.assertTrue(replay["ok"], replay)
        self.assertEqual(4, replay["commands"])
        self.assertEqual(expected_hash, replay["final_state_hash"])

    def test_target_player_counter_choice_is_seat_scoped_and_replays_exactly(
        self,
    ):
        session = self.session(12270805)
        engine = session.engine
        program = self.program(
            key="fixture:target-player-rad",
            label="Target player gets two rad counters",
            effect={
                "op": "place_player_counters",
                "subjects": "target",
                "target": "$target.0",
                "counter": "rad",
                "amount": 2,
                "source": "$source",
            },
            target_schema={
                "zones": ["player"],
                "categories": ["player"],
                "count": 1,
            },
        )
        engine.semantics.put(program)
        engine.state.stack.append(
            StackItem(
                stack_id="target-player-rad",
                ref="S-target-player-rad",
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
            "pilot:A",
            {"action_id": "choose", "targets": ["B"]},
        )
        self.assertTrue(result.ok, result.summary)
        for seat in engine.seats:
            result = session.act(f"pilot:{seat}", {"action_id": "pass"})
            self.assertTrue(result.ok, result.summary)
        self.assertEqual(2, engine.state.players["B"].counters["rad"])
        expected_hash = authoritative_state_hash(engine.state)
        with tempfile.TemporaryDirectory() as temporary:
            record_dir = Path(temporary) / "fixed-player-target-replay"
            session.save(record_dir)
            replay = replay_record(record_dir, self.db, verify=True)
        self.assertTrue(replay["ok"], replay)
        self.assertEqual(5, replay["commands"])
        self.assertEqual(expected_hash, replay["final_state_hash"])


if __name__ == "__main__":
    unittest.main()
