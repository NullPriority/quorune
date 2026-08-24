from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from common import ROOT, keep_all, make_session
from quorune.carddb import CardDatabase
from quorune.compiler.counter_removal_templates import (
    AllCounterRemovalTemplate,
    all_counter_removal_effect_template,
    FixedCounterRemovalTemplate,
    fixed_counter_removal_effect_template,
)
from quorune.compiler.unlock_frontier import _clause_families
from quorune.counter_removal import (
    AllCounterRemovalResult,
    CounterRemovalError,
    CounterRemovalResult,
)
from quorune.deck import DeckLoader
from quorune.errors import GameRuleError
from quorune.model import CardInstance, StackItem
from quorune.oracle_ir import compile_oracle_card
from quorune.record import (
    authoritative_state_hash,
    checkpoint_envelope,
    replay_record,
)
from quorune.rules.capabilities import (
    capability_dependencies_for_node,
    load_default_capability_registry,
)
from quorune.semantic_runtime import (
    ReadOnlyHandlerContext,
    ReadOnlyRulesQuery,
    RemoveAllCountersIntent,
    RemoveCountersIntent,
)
from quorune.semantic_runtime.context import SemanticNodeError
from quorune.semantic_runtime.counter_removal_handlers import (
    AllCounterRemovalHandler,
    FixedCounterRemovalHandler,
)
from quorune.semantic_runtime.executor import execute_intent_plan
from quorune.semantics import SemanticProgram
from scripts.build_test_database import build_fixture_database


def focused_card_database(directory: str) -> CardDatabase:
    database = Path(directory) / "fixed-counter-removal.sqlite3"
    build_fixture_database(
        [ROOT / "tests" / "fixtures" / "scryfall-exact-lists.json"],
        database,
    )
    return CardDatabase(database)


class FixedCounterRemovalCompilerTests(unittest.TestCase):
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

    def test_fixed_counter_removal_compiles_across_spell_trigger_and_activation(
        self,
    ):
        contexts = (
            (
                "Remove a -1/-1 counter from target creature.",
                "Sorcery",
                "spell_ability",
                1,
                "-1/-1",
            ),
            (
                "When this creature enters, remove a charge counter from target artifact.",
                "Creature — Human",
                "triggered_ability",
                1,
                "charge",
            ),
            (
                "{T}: Remove two charge counters from target artifact you control.",
                "Artifact",
                "activated_ability",
                2,
                "charge",
            ),
        )
        for text, type_line, kind, amount, counter_name in contexts:
            with self.subTest(kind=kind):
                ir = self.compile(text, type_line=type_line)
                node = next(
                    value
                    for value in ir.faces[0].nodes
                    if value.template_id
                    and value.template_id.startswith("remove-fixed-counter-")
                )
                self.assertEqual("exact", ir.status)
                self.assertTrue(node.exact)
                self.assertEqual(kind, node.kind)
                self.assertEqual(
                    {
                        "op": "remove_counters",
                        "card": "$target.0",
                        "counter": counter_name,
                        "amount": amount,
                        "source": "$source",
                    },
                    node.effects[0],
                )
                self.assertTrue(
                    {
                        "counter.removal.fixed_effect",
                        "target.revalidate_resolution",
                    }.issubset(node.capability_dependencies)
                )
                self.assertEqual(text, text[node.span.start : node.span.end])

    def test_closed_counter_removal_target_grammar_is_shared(self):
        expected = (
            (
                "Remove one charge counter from target artifact you control.",
                {"types_any": ["artifact"], "controller_relation": "you"},
            ),
            (
                "Remove two -1/-1 counters from another target creature an opponent controls.",
                {
                    "types_any": ["creature"],
                    "controller_relation": "opponent",
                    "source_exclusion": True,
                },
            ),
            (
                "Remove a time counter from target Fungus.",
                {"subtypes_any": ["fungus"]},
            ),
        )
        for text, fields in expected:
            with self.subTest(text=text):
                template = fixed_counter_removal_effect_template(text)
                self.assertIsNotNone(template)
                assert template is not None
                for field, value in fields.items():
                    self.assertEqual(value, template.target_schema[field])

    def test_unsupported_fixed_counter_removal_variants_remain_residual(self):
        texts = (
            "Remove a counter from target creature.",
            "Remove up to one +1/+1 counter from target creature.",
            "Remove all charge counters from target artifact.",
            "Remove X charge counters from target artifact.",
            "Remove a poison counter from target player.",
            "Remove a +1/+1 counter from this creature.",
            "You may remove a +1/+1 counter from target creature.",
            "Move a +1/+1 counter from target creature.",
        )
        for text in texts:
            with self.subTest(text=text):
                self.assertIsNone(fixed_counter_removal_effect_template(text))
                ir = self.compile(text)
                self.assertNotEqual("exact", ir.status)
                self.assertTrue(ir.material_residuals)

    def test_fixed_counter_removal_shape_fails_closed(self):
        template = fixed_counter_removal_effect_template(
            "Remove two charge counters from target artifact you control."
        )
        self.assertIsInstance(template, FixedCounterRemovalTemplate)
        assert template is not None
        expected = {
            "counter.removal.fixed_effect",
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
            ({**template.effects[0], "counter": ""},),
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

    def test_fixed_counter_removal_compiler_mutant_is_killed(self):
        def exact() -> None:
            self.assertEqual(
                "exact",
                self.compile(
                    "Remove a -1/-1 counter from target creature."
                ).status,
            )

        exact()
        with patch(
            "quorune.compiler.resolution_effect_templates."
            "fixed_counter_removal_effect_template",
            return_value=None,
        ):
            with self.assertRaises(AssertionError):
                exact()

    def test_all_counter_removal_compiles_and_promotes_complete_fixture(self):
        text = (
            "First strike\n"
            "Sacrifice this creature: Remove all counters from target permanent."
        )
        ir = self.compile(text, type_line="Creature — Vampire Shaman")
        node = next(
            value
            for value in ir.faces[0].nodes
            if value.template_id == "remove-all-counters-permanent-v1"
        )
        self.assertEqual("exact", ir.status)
        self.assertTrue(node.exact)
        self.assertEqual("activated_ability", node.kind)
        self.assertEqual(
            {
                "op": "remove_all_counters",
                "card": "$target.0",
                "source": "$source",
            },
            node.effects[0],
        )
        self.assertTrue(
            {
                "counter.removal.all_effect",
                "target.revalidate_resolution",
            }.issubset(node.capability_dependencies)
        )

    def test_all_counter_removal_shape_and_compiler_mutant_fail_closed(self):
        template = all_counter_removal_effect_template(
            "Remove all counters from target permanent."
        )
        self.assertIsInstance(template, AllCounterRemovalTemplate)
        assert template is not None
        expected = {
            "counter.removal.all_effect",
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

        def exact() -> None:
            self.assertEqual(
                "exact",
                self.compile(
                    "Sacrifice this creature: Remove all counters from target permanent.",
                    type_line="Creature — Vampire Shaman",
                ).status,
            )

        exact()
        with patch(
            "quorune.compiler.resolution_effect_templates."
            "all_counter_removal_effect_template",
            return_value=None,
        ):
            with self.assertRaises(AssertionError):
                exact()

    def test_unsupported_all_counter_removal_variants_remain_residual(self):
        composed = (
            "Remove all counters from target permanent, then draw a card."
        )
        self.assertIsNone(all_counter_removal_effect_template(composed))
        texts = (
            "Remove all charge counters from target artifact.",
            "Remove all counters from target player.",
            "Remove all counters from this creature.",
            "Remove all counters from each creature.",
            "Remove all counters from up to one target permanent.",
            "You may remove all counters from target permanent.",
        )
        for text in texts:
            with self.subTest(text=text):
                self.assertIsNone(all_counter_removal_effect_template(text))
                ir = self.compile(text)
                self.assertNotEqual("exact", ir.status)
                self.assertTrue(ir.material_residuals)

    def test_unlock_frontier_groups_counter_removal_by_reusable_family(self):
        counter = _clause_families(
            "Remove a charge counter from target artifact.",
            kind="effect_clause",
            reason="unparsed",
        )
        unrelated = _clause_families(
            "Remove target creature from combat.",
            kind="effect_clause",
            reason="unparsed",
        )
        self.assertIn("effect_clause:remove-counter", counter)
        self.assertNotIn("effect_clause:remove-counter", unrelated)


class FixedCounterRemovalRuntimeTests(unittest.TestCase):
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

    @staticmethod
    def context(*, players: int = 4) -> ReadOnlyHandlerContext:
        seats = tuple(chr(ord("A") + index) for index in range(players))
        return ReadOnlyHandlerContext(
            actor="A",
            default_reason="Fixed counter removal fixture",
            query=ReadOnlyRulesQuery(
                seats=seats,
                active_seats=seats,
                apnap_order=seats,
            ),
        )

    @staticmethod
    def effect(target: str, *, amount: int = 2) -> dict[str, object]:
        return {
            "op": "remove_counters",
            "card": target,
            "counter": "charge",
            "amount": amount,
            "source": "fixture-source",
        }

    def test_fixed_counter_removal_does_as_much_as_possible(self):
        session = self.session(12270201)
        target = self.add_permanent(
            session.engine,
            seat="B",
            name="Island",
            ref="partial-removal-target",
        )
        target.counters["charge"] = 1
        plan = FixedCounterRemovalHandler().lower(
            self.effect(target.ref, amount=3),
            self.context(players=2),
        )
        self.assertEqual(
            (
                RemoveCountersIntent(
                    actor="A",
                    object_ref=target.ref,
                    counter_name="charge",
                    amount=3,
                    reason="Fixed counter removal fixture",
                    source_ref="fixture-source",
                ),
            ),
            plan.intents,
        )

        result = execute_intent_plan(session.engine, plan)

        self.assertEqual((3, 1, 1, 0), (
            result.requested,
            result.removed,
            result.before,
            result.after,
        ))
        self.assertNotIn("charge", target.counters)
        zero = execute_intent_plan(session.engine, plan)
        self.assertEqual((3, 0, 0, 0), (
            zero.requested,
            zero.removed,
            zero.before,
            zero.after,
        ))

    def test_fixed_counter_removal_handler_rejects_malformed_effects(self):
        valid = self.effect("target")
        for effect in (
            {**valid, "amount": True},
            {**valid, "amount": 0},
            {**valid, "counter": ""},
            {**valid, "source": None},
            {**valid, "unknown": 1},
            {**valid, "_replacement_selections": ["decline:fixture"]},
        ):
            with self.subTest(effect=effect):
                with self.assertRaises(SemanticNodeError):
                    FixedCounterRemovalHandler().lower(
                        effect,
                        self.context(players=2),
                    )

    def test_all_counter_removal_commits_every_kind_and_handles_empty_target(
        self,
    ):
        session = self.session(12270205)
        target = self.add_permanent(
            session.engine,
            seat="B",
            name="Island",
            ref="all-counter-removal-target",
        )
        target.counters.update({"charge": 2, "+1/+1": 1})
        plan = AllCounterRemovalHandler().lower(
            {
                "op": "remove_all_counters",
                "card": target.ref,
                "source": "fixture-source",
            },
            self.context(players=2),
        )
        self.assertEqual(
            (
                RemoveAllCountersIntent(
                    actor="A",
                    object_ref=target.ref,
                    reason="Fixed counter removal fixture",
                    source_ref="fixture-source",
                ),
            ),
            plan.intents,
        )
        result = execute_intent_plan(session.engine, plan)
        self.assertEqual((("+1/+1", 1), ("charge", 2)), result.removed)
        self.assertEqual(3, result.total_removed)
        self.assertEqual({}, target.counters)
        empty = execute_intent_plan(session.engine, plan)
        self.assertEqual((), empty.removed)
        self.assertEqual(0, empty.total_removed)

    def test_all_counter_removal_handler_rejects_malformed_effects(self):
        valid = {
            "op": "remove_all_counters",
            "card": "target",
            "source": "fixture-source",
        }
        for effect in (
            {**valid, "card": ""},
            {**valid, "source": None},
            {**valid, "counter": "charge"},
            {**valid, "unknown": True},
            {**valid, "_replacement_selections": ["decline:fixture"]},
        ):
            with self.subTest(effect=effect):
                with self.assertRaises(SemanticNodeError):
                    AllCounterRemovalHandler().lower(
                        effect,
                        self.context(players=2),
                    )

    def test_all_counter_removal_result_requires_canonical_immutable_shape(
        self,
    ):
        malformed = (
            {"object_id": "target", "removed": [["charge", 1]]},
            {"object_id": "target", "removed": (("charge",),)},
            {"object_id": "target", "removed": (("Charge", 1),)},
            {"object_id": "target", "removed": ((" charge ", 1),)},
            {"object_id": "target", "removed": (("charge", True),)},
            {"object_id": "target", "removed": (("charge", 0),)},
            {
                "object_id": "target",
                "removed": (("charge", 1), ("+1/+1", 1)),
            },
            {
                "object_id": "target",
                "removed": (("charge", 1), ("charge", 2)),
            },
        )
        for fields in malformed:
            with self.subTest(fields=fields):
                with self.assertRaises(CounterRemovalError):
                    AllCounterRemovalResult(**fields)  # type: ignore[arg-type]

    def test_counter_removal_rejects_malformed_state_before_mutation(self):
        for index, malformed in enumerate((True, "1", -1)):
            with self.subTest(malformed=malformed):
                session = self.session(12270220 + index)
                target = self.add_permanent(
                    session.engine,
                    seat="B",
                    name="Island",
                    ref=f"malformed-counter-target-{index}",
                )
                target.counters["charge"] = malformed  # type: ignore[assignment]
                plan = FixedCounterRemovalHandler().lower(
                    self.effect(target.ref),
                    self.context(players=2),
                )

                with self.assertRaises(GameRuleError):
                    execute_intent_plan(session.engine, plan)

                self.assertEqual(malformed, target.counters["charge"])

    def test_fixed_counter_removal_preserves_target_revalidation_and_siege_trigger(
        self,
    ):
        session = self.session(12270202)
        engine = session.engine
        stale = self.add_permanent(
            engine,
            seat="B",
            name="Scute Swarm",
            ref="stale-removal-target",
        )
        stale.counters["charge"] = 2
        template = fixed_counter_removal_effect_template(
            "Remove two charge counters from target creature."
        )
        self.assertIsNotNone(template)
        assert template is not None
        program = SemanticProgram(
            key="fixture:fixed-removal-target-revalidation",
            label="Fixed removal target revalidation",
            effects=[dict(template.effects[0])],
            target_schema=dict(template.target_schema),
            trust_level="provisional",
        )
        engine.semantics.put(program)
        item = StackItem(
            stack_id="fixed-removal-target-revalidation",
            ref="S-fixed-removal-target-revalidation",
            kind="triggered_ability",
            controller="A",
            label=program.label,
            semantic_key=program.key,
            targets=[stale.ref],
            visibility=list(engine.seats),
            context={
                "target_groups": {"target_0": [stale.ref]},
                "target_snapshots": {
                    stale.ref: engine._target_snapshot(stale.ref)
                },
                "targets_revalidated": False,
                "targets_chosen_at_creation": True,
            },
        )
        engine.state.stack.append(item)
        stale_plan = FixedCounterRemovalHandler().lower(
            self.effect(stale.ref),
            self.context(players=2),
        )
        engine.state.players["B"].zones["battlefield"].remove(stale.object_id)
        engine.state.players["B"].zones["graveyard"].append(stale.object_id)
        stale.zone = "graveyard"
        engine._begin_resolve_item(
            item,
            [dict(value) for value in program.effects],
            None,
        )
        self.assertEqual(2, stale.counters["charge"])
        self.assertTrue(
            any(event.code == "target.illegal" for event in engine.state.events)
        )
        before = authoritative_state_hash(engine.state)
        with self.assertRaises(GameRuleError):
            execute_intent_plan(engine, stale_plan)
        self.assertEqual(before, authoritative_state_hash(engine.state))
        self.assertEqual(2, stale.counters["charge"])

        battle_ref = engine.create_token(
            "B",
            name="Removal Battle",
            battle_protector="A",
            characteristics={
                "type_line": "Token Battle — Siege",
                "defense": "1",
            },
        )[0]
        battle = engine._resolve_object("A", battle_ref, zones={"battlefield"})
        battle_plan = FixedCounterRemovalHandler().lower(
            {
                "op": "remove_counters",
                "card": battle.ref,
                "counter": "defense",
                "amount": 3,
                "source": "fixture-source",
            },
            self.context(players=2),
        )
        execute_intent_plan(engine, battle_plan)
        self.assertNotIn("defense", battle.counters)
        self.assertFalse(engine._stabilize())
        trigger = next(
            item
            for item in engine.state.stack
            if item.semantic_key == "builtin:siege-defeated"
        )
        self.assertEqual(battle.object_id, trigger.source_object_id)

    def test_fixed_counter_removal_replay_is_exact_in_four_player_game(self):
        session = self.session(12270203, players=4)
        engine = session.engine
        target = self.add_permanent(
            engine,
            seat="C",
            name="Island",
            ref="four-player-removal-target",
        )
        target.counters["charge"] = 3
        program = SemanticProgram(
            key="fixture:fixed-counter-removal-replay",
            label="Fixed counter removal replay",
            effects=[self.effect(target.ref)],
            trust_level="provisional",
        )
        engine.semantics.put(program)
        engine.state.stack.append(
            StackItem(
                stack_id="fixed-counter-removal-replay",
                ref="S-fixed-counter-removal-replay",
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
        self.assertEqual(1, target.counters["charge"])
        expected_hash = authoritative_state_hash(engine.state)

        with tempfile.TemporaryDirectory() as temporary:
            record_dir = Path(temporary) / "fixed-counter-removal-replay"
            session.save(record_dir)
            replay = replay_record(record_dir, self.db, verify=True)
        self.assertTrue(replay["ok"], replay)
        self.assertEqual(4, replay["commands"])
        self.assertEqual(expected_hash, replay["final_state_hash"])

    def test_fixed_counter_removal_owner_mutant_is_killed(self):
        session = self.session(12270204)
        target = self.add_permanent(
            session.engine,
            seat="A",
            name="Island",
            ref="owner-mutation-target",
        )
        target.counters["charge"] = 2
        plan = FixedCounterRemovalHandler().lower(
            self.effect(target.ref),
            self.context(players=2),
        )
        with patch(
            "quorune.semantic_choices.intent_host.commit_counter_removal_effect",
            return_value=CounterRemovalResult(
                object_id=target.object_id,
                counter_name="charge",
                requested=2,
                removed=2,
                before=2,
                after=0,
            ),
        ):
            with self.assertRaises(AssertionError):
                execute_intent_plan(session.engine, plan)
                self.assertNotIn("charge", target.counters)

    def test_all_counter_removal_revalidates_target_and_replays_in_four_player_game(
        self,
    ):
        stale_session = self.session(12270206)
        stale_engine = stale_session.engine
        stale = self.add_permanent(
            stale_engine,
            seat="B",
            name="Scute Swarm",
            ref="all-counter-stale-target",
        )
        stale.counters["charge"] = 2
        template = all_counter_removal_effect_template(
            "Remove all counters from target creature."
        )
        self.assertIsNotNone(template)
        assert template is not None
        stale_program = SemanticProgram(
            key="fixture:all-counter-stale-target",
            label="All-counter stale target",
            effects=[
                {
                    "op": "remove_all_counters",
                    "card": stale.ref,
                    "source": "fixture-source",
                }
            ],
            target_schema=dict(template.target_schema),
            trust_level="provisional",
        )
        stale_engine.semantics.put(stale_program)
        stale_item = StackItem(
            stack_id="all-counter-stale-target",
            ref="S-all-counter-stale-target",
            kind="triggered_ability",
            controller="A",
            label=stale_program.label,
            semantic_key=stale_program.key,
            targets=[stale.ref],
            visibility=list(stale_engine.seats),
            context={
                "target_groups": {"target_0": [stale.ref]},
                "target_snapshots": {
                    stale.ref: stale_engine._target_snapshot(stale.ref)
                },
                "targets_revalidated": False,
                "targets_chosen_at_creation": True,
            },
        )
        stale_engine.state.stack.append(stale_item)
        stale_engine.move_card(
            stale.object_id,
            "graveyard",
            reason="response",
        )
        # The zone change correctly removed the battlefield incarnation's
        # counters. A counter on the new graveyard incarnation now witnesses
        # that target revalidation prevents the effect from touching it.
        stale.counters["charge"] = 2
        stale_engine._begin_resolve_item(
            stale_item,
            [dict(value) for value in stale_program.effects],
            None,
        )
        self.assertEqual(2, stale.counters["charge"])
        self.assertTrue(
            any(
                event.code == "target.illegal"
                for event in stale_engine.state.events
            )
        )

        session = self.session(12270207, players=4)
        engine = session.engine
        target = self.add_permanent(
            engine,
            seat="C",
            name="Scute Swarm",
            ref="all-counter-replay-target",
        )
        target.counters.update({"charge": 3, "+1/+1": 2})
        program = SemanticProgram(
            key="fixture:all-counter-removal-replay",
            label="All-counter removal replay",
            effects=[
                {
                    "op": "remove_all_counters",
                    "card": target.ref,
                    "source": "fixture-source",
                }
            ],
            target_schema=dict(template.target_schema),
            trust_level="provisional",
        )
        engine.semantics.put(program)
        engine.state.stack.append(
            StackItem(
                stack_id="all-counter-removal-replay",
                ref="S-all-counter-removal-replay",
                kind="triggered_ability",
                controller="A",
                label=program.label,
                semantic_key=program.key,
                targets=[target.ref],
                visibility=list(engine.seats),
                context={
                    "target_groups": {"target_0": [target.ref]},
                    "target_snapshots": {
                        target.ref: engine._target_snapshot(target.ref)
                    },
                    "targets_revalidated": False,
                    "targets_chosen_at_creation": True,
                },
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
        self.assertEqual({}, target.counters)
        expected_hash = authoritative_state_hash(engine.state)
        with tempfile.TemporaryDirectory() as temporary:
            record_dir = Path(temporary) / "all-counter-removal-replay"
            session.save(record_dir)
            replay = replay_record(record_dir, self.db, verify=True)
        self.assertTrue(replay["ok"], replay)
        self.assertEqual(4, replay["commands"])
        self.assertEqual(expected_hash, replay["final_state_hash"])

    def test_all_counter_removal_owner_mutant_is_killed(self):
        session = self.session(12270208)
        target = self.add_permanent(
            session.engine,
            seat="A",
            name="Island",
            ref="all-counter-owner-mutation-target",
        )
        target.counters.update({"charge": 2, "+1/+1": 1})
        plan = AllCounterRemovalHandler().lower(
            {
                "op": "remove_all_counters",
                "card": target.ref,
                "source": "fixture-source",
            },
            self.context(players=2),
        )
        with patch(
            "quorune.semantic_choices.intent_host."
            "commit_all_counter_removal_effect",
            return_value=AllCounterRemovalResult(
                object_id=target.object_id,
                removed=(("+1/+1", 1), ("charge", 2)),
            ),
        ):
            with self.assertRaises(AssertionError):
                execute_intent_plan(session.engine, plan)
                self.assertEqual({}, target.counters)


if __name__ == "__main__":
    unittest.main()
