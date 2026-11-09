from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import tempfile
import unittest
import json
from unittest.mock import patch

from common import ROOT, keep_all
from quorune.carddb import CardDatabase, CardRecord
from quorune.compiler.counter_templates import (
    CounterTarget,
    TargetedCounterEffectTemplate,
    is_intrinsically_uncounterable_spell,
    targeted_counter_effect_template,
)
from quorune.compiler.direct_target import (
    permanent_target_schema,
    stack_target_schema,
)
from quorune.oracle_ir import (
    compile_oracle_card,
    register_generated_programs,
)
from quorune.rules.capabilities import (
    capability_dependencies_for_node,
    load_default_capability_registry,
)
from quorune.semantics import SemanticRegistry
from scripts.build_test_database import build_fixture_database


def focused_card_database(directory: str) -> CardDatabase:
    database = Path(directory) / "targeted-counter.sqlite3"
    build_fixture_database(
        [
            ROOT / "tests" / "fixtures" / "scryfall-exact-lists.json",
            ROOT / "tests" / "fixtures" / "targeted-counter-cards.json",
        ],
        database,
    )
    return CardDatabase(database)


class StackControllerPaymentCompilerTests(unittest.TestCase):
    @staticmethod
    def record(text, *, type_line="Instant", mana_cost="{U}"):
        creature = "Creature" in type_line
        return CardRecord(
            oracle_id="00000000-0000-4000-8000-000000000941",
            name="Generic Stack Controller Payment", mana_cost=mana_cost, mana_value=1,
            type_line=type_line, oracle_text=text, power="2" if creature else None,
            toughness="2" if creature else None, loyalty=None, defense=None,
            colors=("U",), color_identity=("U",), keywords=(), produced_mana=(),
            layout="normal", released_at="2026-01-01", legalities={"commander":"legal"},
            faces=(), raw={},
        )

    def test_fixed_controller_payment_compiles_across_spell_trigger_and_activation(self):
        registry = load_default_capability_registry()
        for text, type_line in (
            ("Counter target spell unless its controller pays {3}.", "Instant"),
            ("When this creature enters, counter target noncreature spell unless its controller pays {2}.", "Creature — Wizard"),
            ("{U}, {T}: Counter target instant or sorcery spell unless its controller pays {1}.", "Creature — Wizard"),
        ):
            with self.subTest(text=text):
                compiled = compile_oracle_card(self.record(text, type_line=type_line),
                    capability_registry=registry, capability_profile="commander_review")
                self.assertEqual("exact", compiled.status, compiled.to_dict())
                effect = compiled.faces[0].nodes[0].effects[0]
                self.assertEqual("counter_unless_pay", effect["op"])
                self.assertEqual(2, effect["schema_version"])
                self.assertEqual("$target.current_controller.0", effect["player"])
                self.assertEqual("$target.0", effect["stack"])

    def test_controller_payment_parser_and_shape_are_closed(self):
        from quorune.compiler.counter_templates import targeted_controller_payment_template
        from quorune.rules.stack_controller_payment_shapes import stack_controller_payment_node_capabilities
        from quorune.rules.stack_controller_payment_cost import compiled_stack_controller_payment_cost

        for target in CounterTarget:
            template = targeted_controller_payment_template(
                f"Counter target {target.value} unless its controller pays {{2}}."
            )
            self.assertIsNotNone(template)
            name, effects, schema, mechanics = template.compiled()
            self.assertEqual(2, compiled_stack_controller_payment_cost(effects[0]).requirements["GENERIC"])
            self.assertIn("stack.counter.controller_payment", stack_controller_payment_node_capabilities(
                effects=effects, target_schema=schema, mechanic_ids=mechanics
            ))
            self.assertTrue(schema["source_exclusion"])
        for text in (
            "Counter target spell unless you pay {2}.",
            "Counter target spell unless its controller pays 2 life.",
            "Counter target spell unless its controller sacrifices a creature.",
            "Counter target spell unless its controller pays {2}. Draw a card.",
            "Counter target spell unless its controller pays {U/P}.",
            "Counter target spell unless its controller pays {X}.",
        ):
            self.assertIsNone(targeted_controller_payment_template(text), text)
        self.assertIsNone(targeted_controller_payment_template(
            "Counter target spell unless its controller pays {X}{2}.", cast_x_available=True
        ))
        with self.assertRaises(ValueError):
            targeted_controller_payment_template("Counter target spell unless its controller pays {1}.", cast_x_available=1)

    def test_controller_payment_cost_mutants_and_cast_x_are_strict(self):
        from copy import deepcopy
        from quorune.compiler.counter_templates import targeted_controller_payment_template
        from quorune.rules.stack_controller_payment_cost import compiled_stack_controller_payment_cost

        template = targeted_controller_payment_template(
            "Counter target spell unless its controller pays {X}.", cast_x_available=True
        )
        effect = template.compiled()[1][0]
        self.assertTrue(compiled_stack_controller_payment_cost(effect).uses_cast_x)
        for field, value in (("schema_version", True), ("player", "$controller"), ("stack", "$target.1")):
            mutant = deepcopy(effect)
            mutant[field] = value
            with self.assertRaises(ValueError):
                compiled_stack_controller_payment_cost(mutant)
        for amount in (True, -1, "2", {"kind":"cast_x_effect_amount","schema_version":1,"coefficient":-1}):
            mutant = deepcopy(effect)
            mutant["cost"]["GENERIC"] = amount
            with self.assertRaises(ValueError):
                compiled_stack_controller_payment_cost(mutant)
        mutant = deepcopy(effect)
        mutant["cost"]["U"] = mutant["cost"]["GENERIC"]
        with self.assertRaises(ValueError):
            compiled_stack_controller_payment_cost(mutant)

    def test_resolved_controller_payment_rejects_unknown_private_fields(self):
        from quorune.compiler.counter_templates import targeted_controller_payment_template
        from quorune.rules.stack_controller_payment_cost import resolved_stack_controller_payment_cost
        effect = dict(targeted_controller_payment_template(
            "Counter target spell unless its controller pays {3}."
        ).compiled()[1][0])
        effect.update(stack="VICTIM", player="B", _unknown=1)
        with self.assertRaisesRegex(ValueError, "fields"):
            resolved_stack_controller_payment_cost(effect)

    def test_controller_payment_missing_or_blocked_capability_withholds_trust(self):
        import json
        from quorune.rules.capabilities import CapabilityRegistry

        payload = json.loads((ROOT / "quorune/rules/capability-registry.json").read_text(encoding="utf-8"))
        for capability in payload["capabilities"]:
            if capability["id"] == "stack.counter.controller_payment":
                capability["status"] = "blocked"
                capability["blockers"] = ["Generic dependency-failure witness"]
        compiled = compile_oracle_card(
            self.record("Counter target spell unless its controller pays {3}."),
            capability_registry=CapabilityRegistry(payload), capability_profile="commander_review",
        )
        self.assertTrue(compiled.faces[0].nodes[0].lowerable)
        self.assertNotEqual("exact", compiled.status)
        self.assertIn("stack.counter.controller_payment", compiled.faces[0].nodes[0].capability_dependencies)


    def test_public_controller_quantity_payment_rejects_open_operands(self):
        from copy import deepcopy
        from quorune.compiler.counter_templates import targeted_controller_payment_template
        from quorune.rules.stack_controller_payment_shapes import stack_controller_payment_node_capabilities

        text = "Counter target spell unless its controller pays {2} plus an additional {1} for each Faerie you control."
        parsed = targeted_controller_payment_template(text, source_name=self.record(text).name)
        self.assertIsNotNone(parsed)
        _, effects, schema, mechanics = parsed.compiled()
        self.assertEqual(3, effects[0]["schema_version"])
        amount = effects[0]["cost"]["GENERIC"]["quantity"]
        self.assertEqual(2, amount["offset"])
        self.assertEqual(["faerie"], amount["query"]["subtypes_all"])
        self.assertIn("stack.counter.controller_quantity_payment", stack_controller_payment_node_capabilities(
            effects=effects, target_schema=schema, mechanic_ids=mechanics))
        for field, value in (("player", "$controller"), ("stack", "$target.1"), ("schema_version", 2)):
            with self.subTest(field=field):
                forged = {**effects[0], field: value}
                self.assertEqual((), stack_controller_payment_node_capabilities(
                    effects=(forged,), target_schema=schema, mechanic_ids=mechanics))
        altered = deepcopy(effects[0])
        altered["cost"]["U"] = 1
        self.assertEqual((), stack_controller_payment_node_capabilities(
            effects=(altered,), target_schema=schema, mechanic_ids=mechanics))
        for printed in (
            "Counter target spell unless its controller pays {1} for each blue card in your hand.",
            "Counter target activated ability unless its controller pays {1} for each card in your graveyard.",
        ):
            self.assertIsNone(targeted_controller_payment_template(printed, source_name=self.record(printed).name))


    def test_counter_payment_followup_shape_is_detached_and_rejects_unbound_payer(self):
        from copy import deepcopy
        from quorune.compiler.counter_templates import targeted_controller_payment_followup_template
        from quorune.oracle_ir import _effect_template
        from quorune.rules.stack_controller_payment_shapes import stack_controller_payment_node_capabilities

        compiled = targeted_controller_payment_followup_template(
            "Counter target spell unless its controller pays {1}. That player discards a card.",
            source_name="Frightful Delusion", cast_x_available=False,
            compile_component=lambda text: _effect_template(text,card_name="Frightful Delusion"),
        )
        self.assertIsNotNone(compiled)
        _, effects, schema, mechanics = compiled
        original=deepcopy(effects)
        self.assertIn("stack.counter.payment_followup",stack_controller_payment_node_capabilities(
            effects=effects,target_schema=schema,mechanic_ids=mechanics))
        self.assertEqual(original,effects)
        forged=deepcopy(effects[0])
        forged["counter_payment_followup"]["effect"]["players"]=["$controller"]
        self.assertEqual((),stack_controller_payment_node_capabilities(
            effects=(forged,),target_schema=schema,mechanic_ids=mechanics))


class StackControllerPaymentChoiceIsolationTests(unittest.TestCase):
    def prepare(self, *, payable=True, generic=3, payer="B"):
        from quorune.compiler.counter_templates import targeted_controller_payment_template
        from quorune.replacement.immutable import FrozenMap
        from quorune.semantic_choices.context import ChoiceStackView, SemanticChoiceContext, SnapshotSemanticChoiceQuery
        from quorune.semantic_choices.model import SemanticChoiceContinuation, SemanticChoiceFrame
        from quorune.semantic_choices.payments import PAYMENT_CHOICE_HANDLERS

        handler = next(value for value in PAYMENT_CHOICE_HANDLERS if value.operation == "counter_unless_pay")
        effect = dict(targeted_controller_payment_template(
            f"Counter target spell unless its controller pays {{{generic}}}."
        ).compiled()[1][0])
        effect.update(stack="VICTIM", player="B")
        query = SnapshotSemanticChoiceQuery(
            seat_order=tuple("ABCD"), active_order=tuple("ABCD"),
            stack_rows=(ChoiceStackView("VICTIM", payer, "Generic victim", "", (), ()),),
            affordable_costs=frozenset({SnapshotSemanticChoiceQuery._cost_key("B", effect["cost"])}) if payable else frozenset(),
        )
        context = SemanticChoiceContext(
            actor="B", stack_ref="COUNTER", stack_controller="A", stack_label="Generic counter",
            source_ref=None, card_ref=None, semantic_program_id="isolation", semantic_program_version=1,
            query=query,
        )
        prepared = handler.prepare(FrozenMap(effect), context)
        continuation = SemanticChoiceContinuation(
            handler_id=handler.handler_id, handler_version=handler.schema_version,
            stack_ref="COUNTER", effect=prepared.continuation_effect, remaining=(), destination=None,
            note="Generic isolated intent witness", semantic_frame=SemanticChoiceFrame("isolation", 1, "COUNTER", 0, "A"),
        )
        return handler, query, prepared, continuation

    def test_counter_payment_intents_keep_payer_and_countering_controller_distinct(self):
        handler, query, prepared, continuation = self.prepare()
        self.assertEqual((True, False), prepared.request.choice.legal_values)
        paid = handler.complete(continuation, {"pay":True}, query)
        self.assertEqual("B", paid.intents[0].actor)
        self.assertEqual("B", paid.intents[0].player)
        self.assertEqual(3, paid.intents[0].requirements["GENERIC"])
        declined = handler.complete(continuation, {"pay":False}, query)
        self.assertEqual("VICTIM", declined.intents[0].stack_ref)
        self.assertEqual("A", declined.intents[0].countered_by)

    def test_counter_payment_offer_and_completion_revalidate_affordability(self):
        from quorune.semantic_choices.model import SemanticChoiceError
        handler, query, prepared, continuation = self.prepare(payable=False)
        self.assertEqual((False,), prepared.request.choice.legal_values)
        with self.assertRaises(SemanticChoiceError):
            handler.complete(continuation, {"pay":True}, query)
        with self.assertRaises(SemanticChoiceError):
            handler.complete(continuation, {"pay":"false"}, query)
        handler, query, prepared, continuation = self.prepare(generic=0)
        self.assertEqual((True, False), prepared.request.choice.legal_values)
        self.assertEqual(0, handler.complete(continuation, {"pay":True}, query).intents[0].requirements["GENERIC"])

    def test_counter_payment_rejects_changed_controller_and_continuation_cost(self):
        from quorune.replacement.immutable import FrozenMap
        from quorune.semantic_choices.model import SemanticChoiceError
        with self.assertRaises(SemanticChoiceError):
            self.prepare(payer="C")
        handler, query, prepared, continuation = self.prepare()
        altered = dict(continuation.effect)
        altered["_requirements"] = {**dict(altered["_requirements"]), "GENERIC":1}
        with self.assertRaises(SemanticChoiceError):
            handler.complete(replace(continuation, effect=FrozenMap(altered)), {"pay":True}, query)
        altered = {**dict(continuation.effect), "_countering_controller":"C"}
        with self.assertRaises(SemanticChoiceError):
            handler.complete(replace(continuation, effect=FrozenMap(altered)), {"pay":False}, query)
        with self.assertRaises(SemanticChoiceError):
            handler.complete(continuation, {"pay":True}, replace(query, stack_rows=()))

    def test_counter_payment_cast_x_uses_the_existing_scalar_owner(self):
        from types import SimpleNamespace
        from quorune.compiler.counter_templates import targeted_controller_payment_template
        from quorune.semantic_runtime.values import resolve_semantic_value
        effect = targeted_controller_payment_template(
            "Counter target spell unless its controller pays {X}.", cast_x_available=True
        ).compiled()[1][0]
        for x in (0, 1, 7):
            cost = resolve_semantic_value(None, effect["cost"], SimpleNamespace(x_value=x))
            self.assertEqual(x, cost["GENERIC"])

    def test_counter_payment_wrong_countering_controller_mutant_is_killed(self):
        from unittest.mock import patch
        import quorune.semantic_choices.payments as owner
        original = owner.CounterStackIntent
        def wrong_controller(**values):
            values["countered_by"] = values["actor"]
            return original(**values)
        with patch.object(owner, "CounterStackIntent", side_effect=wrong_controller):
            with self.assertRaises(AssertionError):
                self.test_counter_payment_intents_keep_payer_and_countering_controller_distinct()

    def test_historical_counter_payment_v1_keeps_its_payload_execution(self):
        from quorune.replacement.immutable import FrozenMap
        from quorune.semantic_choices.context import SemanticChoiceContext
        from quorune.semantic_choices.model import SemanticChoiceContinuation
        handler, query, prepared, current = self.prepare()
        historical = FrozenMap({"op":"counter_unless_pay", "stack":"VICTIM", "player":"B", "cost":{"GENERIC":3}})
        context = SemanticChoiceContext(actor="B", stack_ref="COUNTER", stack_controller="A",
            stack_label="Generic historical counter", source_ref=None, card_ref=None,
            semantic_program_id="historical-isolation", semantic_program_version=1, query=query)
        old = handler.prepare(historical, context)
        self.assertNotIn("schema_version", old.continuation_effect)
        self.assertNotIn("_countering_controller", old.continuation_effect)
        continuation = replace(current, effect=old.continuation_effect)
        paid = handler.complete(continuation, {"pay":True}, query)
        self.assertEqual("B", paid.intents[0].player)
        self.assertEqual(3, paid.intents[0].requirements["GENERIC"])
        declined = handler.complete(continuation, {"pay":False}, query)
        self.assertEqual("B", declined.intents[0].countered_by)
        # This preserves the historical payload's execution contract, not a
        # new current-game rules claim or cross-version record replay claim.


class StackControllerPaymentActionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from quorune.deck import DeckDefinition, DeckEntry
        cls.temporary = tempfile.TemporaryDirectory()
        path = Path(cls.temporary.name) / "stack-payment.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures/stack-controller-payment-cards.json"], path)
        cls.db = CardDatabase(path)
        cls.deck = DeckDefinition("Generic payment review", [
            DeckEntry("Generic Payment Commander", 1, "commander"),
            DeckEntry("Generic Payment Island", 15),
        ], ["Generic Payment Commander"])

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temporary.cleanup()

    def session(self, seed):
        from copy import deepcopy
        from quorune.model import GameConfig
        from quorune.session import CommanderSession
        session = CommanderSession.create(self.db, {seat:deepcopy(self.deck) for seat in "ABCD"},
            first_player="B", seed=seed, config=GameConfig(seed=seed, auto_pass_empty_priority=False))
        keep_all(session)
        engine = session.engine
        engine.permissions.invalidate_current()
        engine.state.pending_decision = None
        engine.state.priority_player = None
        engine.state.priority_passes = []
        register_generated_programs(self.db, engine.semantics, tuple(self.db.iter_cards()),
            capability_registry=load_default_capability_registry(),
            capability_profile="commander_review", promote_exact_runtime_handlers=True,
            promote_exact_trigger_programs=True, promote_exact_effect_programs=True,
            promote_exact_capability_declarations=True)
        return session

    def add(self, engine, name, ref, *, seat="A", zone="hand"):
        from quorune.model import CardInstance
        row = self.db.lookup(name)
        card = CardInstance(object_id="stack-payment:"+ref, ref=ref, oracle_id=row.oracle_id,
            printed_name=row.name, owner=seat, controller=seat, zone=zone,
            zone_timestamp=engine._next_zone_timestamp(), known_to=list("ABCD") if zone=="battlefield" else [seat])
        engine.state.cards[card.object_id] = card
        engine.state.players[seat].zones[zone].append(card.object_id)
        return card

    def assert_trusted(self, engine, card):
        programs = tuple(program for program in engine.semantics.programs_for_oracle(card.oracle_id)
                         if not program.ability_id.startswith("ability:catalog:"))
        self.assertTrue(programs, card.printed_name)
        self.assertTrue(all(engine.semantic_program_is_current_trusted(p) for p in programs), card.printed_name)

    @staticmethod
    def current(session, card):
        # Rejected commands restore GameState and may replace CardInstance
        # objects; public references and logical pins, not Python aliases,
        # identify the authoritative object after transactional rollback.
        return session.state.cards[card.object_id]

    def offer(self, session, actor, action_id):
        session.engine.pump()
        decision = session.packet("pilot:"+actor, full=True)["decision"]
        self.assertIsNotNone(decision)
        return next(action for action in decision["ctx"]["legal"]["actions"] if action["id"]==action_id)

    def respond(self, session, source, victim, *, x=None, activated=False, modes=None, target_stack=None):
        for _ in range(8):
            if session.engine.state.priority_player == "A":
                break
            principal = session.pending_principals()[0]
            result = session.act(principal, {"action_id":"pass"})
            self.assertTrue(result.ok, result.summary)
        action_id = "cast:"+source.ref
        if activated:
            ability = session.engine._activated_abilities(source)[0]
            action_id = f"activate:{source.ref}:{ability.ability_id}"
        target = target_stack or next(item.ref for item in session.engine.state.stack if item.card_object_id==victim.object_id)
        action = self.offer(session, "A", action_id)
        command = {"action_id":action_id, "pay":"auto"}
        if modes is not None:
            command["modes"] = list(modes)
            command["targets"] = [target]
        elif action.get("target_schema") is not None:
            self.assertIn(target, action["target_schema"]["legal_refs"])
            command["targets"] = [target]
        if x is not None:
            command["x"] = x
        result = session.act("pilot:A", command)
        self.assertTrue(result.ok, result.summary)
        return target

    def setup(self, seed, *, source_name="Generic Fixed Controller Payment", x=None,
              resources=3, victim_name="Generic Payment Victim Creature", activated=False, mana_lands=0,
              current_payer=None, arrange=None, controller_generic=None, victim_x=None, victim_blue=1,
              victim_zone="hand", modes=None, respond_to_victim=True):
        from quorune.record import checkpoint_envelope
        session = self.session(seed)
        engine = session.engine
        source = self.add(engine, source_name, "COUNTER", zone="battlefield" if activated else "hand")
        victim = self.add(engine, victim_name, "VICTIM", seat="B", zone=victim_zone)
        self.assert_trusted(engine, source)
        self.assert_trusted(engine, victim)
        for index in range(mana_lands):
            self.add(engine, "Generic Payment Island", f"PAYER-MANA-{index}", seat="B", zone="battlefield")
        engine.state.active_player = "B"
        engine.state.started = True
        engine.state.phase = "precombat_main"
        engine.state.step = "main"
        engine.state.players["B"].mana_pool.update(U=victim_blue, C=resources)
        engine.state.players["A"].mana_pool.update(U=1, C=controller_generic if controller_generic is not None else x or 0)
        if arrange is not None:
            arrange(session)
        engine._grant_priority("B")
        action = self.offer(session, "B", "cast:"+victim.ref)
        session.initial_checkpoint = checkpoint_envelope(engine.state)
        session.commands.clear()
        session.decisions.clear()
        command = {"action_id":action["id"], "pay":"auto"}
        if victim_x is not None:
            command["x"] = victim_x
        result = session.act("pilot:B", command)
        self.assertTrue(result.ok, result.summary)
        if current_payer is not None:
            # Minimal-state owner diagnostic, not a claimed implementation of
            # an unsupported stack-control-changing spell. Replay starts
            # before A's real cast against this current-controller state.
            next(item for item in engine.state.stack if item.card_object_id==victim.object_id).controller = current_payer
            engine.state.players[current_payer].mana_pool["C"] = resources
            session.initial_checkpoint = checkpoint_envelope(engine.state)
            session.commands.clear()
            session.decisions.clear()
        if respond_to_victim:
            target = self.respond(session, source, victim, x=x, activated=activated, modes=modes)
        else:
            target = next(item.ref for item in engine.state.stack if item.card_object_id==victim.object_id)
        return session, source, victim, target

    def until_choice(self, session, *, trigger_target=None):
        for _ in range(20):
            decision = session.state.pending_decision
            if decision is not None and decision.kind == "semantic.choice":
                return decision
            if decision is not None and decision.kind == "semantic.target":
                self.assertIsNotNone(trigger_target)
                result = session.act(session.pending_principals()[0], {"action_id":"choose", "targets":[trigger_target]})
                self.assertTrue(result.ok, result.summary)
                continue
            self.assertTrue(session.state.stack, "No pending payment was produced")
            result = session.act(session.pending_principals()[0], {"action_id":"pass"})
            self.assertTrue(result.ok, result.summary)
        self.fail("Payment did not reach the canonical choice owner")

    def resolve_all(self, session):
        for _ in range(20):
            if not session.state.stack:
                return
            result = session.act(session.pending_principals()[0], {"action_id":"pass"})
            self.assertTrue(result.ok, result.summary)
        self.fail("Generic stack did not resolve")

    def replay(self, session):
        from quorune.record import authoritative_state_hash, replay_record
        from quorune.session import CommanderSession
        expected = authoritative_state_hash(session.state)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "stack-payment-record"
            session.save(path)
            loaded = CommanderSession.load(self.db, path)
            self.assertEqual(expected, authoritative_state_hash(loaded.state))
            result = replay_record(path, self.db, verify=True)
        self.assertTrue(result["ok"], result)
        self.assertEqual(expected, result["final_state_hash"])

    def test_trusted_stack_payment_pay_decline_projection_and_exact_replay(self):
        import json
        from quorune.projection import StateProjector
        from quorune.record import authoritative_state_hash
        for paid in (True, False):
            with self.subTest(paid=paid):
                session, source, victim, target = self.setup(24200001+paid)
                decision = self.until_choice(session)
                self.assertEqual(["pilot:B"], session.pending_principals())
                payload = session.packet("pilot:B", full=True)["decision"]
                self.assertEqual([True, False], payload["legal_actions"][0]["choice_schema"]["legal_values"])
                self.assertEqual(3, payload["ctx"]["cost"]["GENERIC"])
                before = authoritative_state_hash(session.state)
                for principal, command in (
                    ("pilot:C", {"action_id":"choose", "pay":True}),
                    ("pilot:B", {"action_id":"choose", "pay":"false"}),
                ):
                    rejected = session.act(principal, command)
                    self.assertFalse(rejected.ok)
                    self.assertEqual(before, authoritative_state_hash(session.state))
                self.replay(session)
                for viewer in "ACD":
                    self.assertIsNone(session.packet("pilot:"+viewer, full=True)["decision"])
                    public = json.dumps(StateProjector(self.db, session.state)._snapshot("pilot:"+viewer))
                    for object_id in session.state.players["B"].zones["hand"]:
                        self.assertNotIn(session.state.cards[object_id].ref, public)
                result = session.act("pilot:B", {"action_id":"choose", "pay":paid})
                self.assertTrue(result.ok, result.summary)
                self.assertEqual(0 if paid else 3, session.state.players["B"].mana_pool["C"])
                self.assertEqual("graveyard", self.current(session, source).zone)
                if not paid:
                    self.assertEqual("graveyard", self.current(session, victim).zone)
                    self.assertFalse([item for item in session.state.stack if item.ref==target])
                    counters = [event for event in session.state.events
                        if event.code=="stack.counter" and event.details.get("stack")==target]
                    self.assertEqual(1, len(counters))
                    self.assertEqual("A", counters[0].actor)
                self.resolve_all(session)
                self.assertEqual("battlefield" if paid else "graveyard", self.current(session, victim).zone)
                self.replay(session)

    def test_trusted_stack_payment_cast_x_zero_and_canonical_mana_sources(self):
        for x in (0, 3):
            with self.subTest(x=x):
                session, source, victim, target = self.setup(24200010+x,
                    source_name="Generic X Controller Payment", x=x, resources=x)
                self.until_choice(session)
                payload = session.packet("pilot:B", full=True)["decision"]
                self.assertEqual(x, payload["ctx"]["cost"]["GENERIC"])
                self.assertEqual([True, False], payload["legal_actions"][0]["choice_schema"]["legal_values"])
                result = session.act("pilot:B", {"action_id":"choose", "pay":True})
                self.assertTrue(result.ok, result.summary)
                self.assertEqual(0, session.state.players["B"].mana_pool["C"])
                self.resolve_all(session)
                self.assertEqual("battlefield", self.current(session, victim).zone)
                self.replay(session)
        session, source, victim, target = self.setup(24200014, resources=0, mana_lands=3)
        self.until_choice(session)
        payload = session.packet("pilot:B", full=True)["decision"]
        self.assertEqual([True, False], payload["legal_actions"][0]["choice_schema"]["legal_values"])
        result = session.act("pilot:B", {"action_id":"choose", "pay":True})
        self.assertTrue(result.ok, result.summary)
        self.assertTrue(all(session.state.cards[object_id].tapped
            for object_id in session.state.players["B"].zones["battlefield"]
            if session.state.cards[object_id].ref.startswith("PAYER-MANA-")))
        self.resolve_all(session)
        self.assertEqual("battlefield", self.current(session, victim).zone)
        self.replay(session)

    def test_trusted_stack_payment_stale_affordability_and_mandatory_sibling(self):
        from quorune.record import authoritative_state_hash
        session, source, victim, target = self.setup(24200020, source_name="Generic Controller Payment Draw")
        self.until_choice(session)
        before_hand = len(session.state.players["A"].zones["hand"])
        session.state.players["B"].mana_pool["C"] = 2
        before = authoritative_state_hash(session.state)
        rejected = session.act("pilot:B", {"action_id":"choose", "pay":True})
        self.assertFalse(rejected.ok)
        self.assertIn("payable", rejected.summary)
        self.assertEqual(before, authoritative_state_hash(session.state))
        session.state.players["B"].mana_pool["C"] = 3
        result = session.act("pilot:B", {"action_id":"choose", "pay":False})
        self.assertTrue(result.ok, result.summary)
        self.assertEqual(before_hand+1, len(session.state.players["A"].zones["hand"]))
        self.assertEqual("graveyard", self.current(session, victim).zone)
        self.assertFalse(session.state.stack)
        # These stale-affordability fixture edits are an owner diagnostic,
        # not recorded mana-changing commands. The primary cast/replay witness
        # independently covers both unchanged-world payment branches.

    def test_trusted_stack_payment_activation_and_entry_trigger_owners(self):
        session, source, victim, target = self.setup(24200030,
            source_name="Generic Activated Controller Payment", resources=1, activated=True)
        self.until_choice(session)
        self.assertTrue(self.current(session, source).tapped)
        result = session.act("pilot:B", {"action_id":"choose", "pay":False})
        self.assertTrue(result.ok, result.summary)
        self.assertEqual("battlefield", self.current(session, source).zone)
        self.assertEqual("graveyard", self.current(session, victim).zone)
        self.replay(session)
        session, source, victim, target = self.setup(24200031,
            source_name="Generic Entry Controller Payment", resources=1,
            victim_name="Generic Payment Victim Instant")
        self.until_choice(session, trigger_target=target)
        self.assertEqual("battlefield", self.current(session, source).zone)
        result = session.act("pilot:B", {"action_id":"choose", "pay":False})
        self.assertTrue(result.ok, result.summary)
        self.assertEqual("graveyard", self.current(session, victim).zone)
        self.replay(session)

    def test_trusted_stack_payment_uncounterable_target_retains_legal_decline(self):
        session, source, victim, target = self.setup(24200040,
            victim_name="Generic Uncounterable Payment Victim")
        self.until_choice(session)
        result = session.act("pilot:B", {"action_id":"choose", "pay":False})
        self.assertTrue(result.ok, result.summary)
        self.assertTrue([item for item in session.state.stack if item.ref==target])
        before_hand = len(session.state.players["B"].zones["hand"])
        self.resolve_all(session)
        self.assertEqual(before_hand+1, len(session.state.players["B"].zones["hand"]))
        self.assertEqual("graveyard", self.current(session, victim).zone)
        self.replay(session)

    def test_trusted_stack_payment_current_controller_and_vanished_target_boundaries(self):
        from quorune.record import checkpoint_envelope
        session, source, victim, target = self.setup(24200050, current_payer="C")
        self.until_choice(session)
        self.assertEqual(["pilot:C"], session.pending_principals())
        result = session.act("pilot:C", {"action_id":"choose", "pay":True})
        self.assertTrue(result.ok, result.summary)
        self.assertEqual(0, session.state.players["C"].mana_pool["C"])
        self.assertEqual(3, session.state.players["B"].mana_pool["C"])
        self.resolve_all(session)
        self.replay(session)
        session, source, victim, target = self.setup(24200051)
        session.engine._counter_stack_item(target, countered_by="D", reason="Bounded stale-target setup")
        session.initial_checkpoint = checkpoint_envelope(session.state)
        session.commands.clear()
        session.decisions.clear()
        self.resolve_all(session)
        self.assertEqual(3, session.state.players["B"].mana_pool["C"])
        self.assertEqual("graveyard", self.current(session, source).zone)
        self.assertFalse([event for event in session.state.events if event.code=="counter.unless.paid"])
        self.replay(session)

    def test_stack_payment_canonical_spell_copy_preserves_announced_x(self):
        from quorune.record import checkpoint_envelope
        session, source, victim, target = self.setup(24200060,
            source_name="Generic X Controller Payment", x=3, resources=6)
        original = next(item for item in session.state.stack if item.card_object_id==source.object_id)
        copied = session.engine._copy_stack_item(controller="A", target=original,
            targets=list(original.targets), target_groups={}, reason="Canonical copy owner diagnostic")
        self.assertEqual(3, copied.x_value)
        # Actual casting is covered above; this is the existing copy-owner
        # boundary, not new copy Oracle grammar. Commands replay from here.
        session.initial_checkpoint = checkpoint_envelope(session.state)
        session.commands.clear()
        session.decisions.clear()
        self.until_choice(session)
        self.assertEqual(3, session.packet("pilot:B", full=True)["decision"]["ctx"]["cost"]["GENERIC"])
        paid = session.act("pilot:B", {"action_id":"choose", "pay":True})
        self.assertTrue(paid.ok, paid.summary)
        self.until_choice(session)
        declined = session.act("pilot:B", {"action_id":"choose", "pay":False})
        self.assertTrue(declined.ok, declined.summary)
        self.assertEqual(3, session.state.players["B"].mana_pool["C"])
        self.assertEqual("graveyard", self.current(session, victim).zone)
        self.replay(session)

    def test_stack_payment_historical_record_rejects_silent_reinterpretation(self):
        import json
        from quorune.record import replay_record
        path = ROOT / "tests/fixtures/records/fixed-payment-v237-2756dd2b"
        provenance = json.loads((path/"provenance.json").read_text(encoding="utf-8"))
        self.assertEqual("explicit_runtime_trust_incompatibility", provenance["current_runtime_disposition"])
        with self.assertRaisesRegex(ValueError, "Runtime trust provenance mismatch in record manifest"):
            replay_record(path, self.db, verify=True)
        # Genuine archived payment commands cover the global provenance
        # boundary; the v1 counter payload control separately covers its
        # preserved isolated execution. Neither claims historical gameplay.


    def _arrange_quantity_cards(self, entries):
        def arrange(session):
            for index, (name, seat, zone) in enumerate(entries):
                self.add(session.engine, name, f"QUANTITY-{index}", seat=seat, zone=zone)
        return arrange

    def _expect_quantity_price(self, session, amount):
        decision = self.until_choice(session)
        self.assertEqual(["pilot:B"], session.pending_principals())
        self.assertEqual(2, decision.continuation["effect"]["schema_version"])
        offer = session.packet("pilot:B", full=True)["decision"]
        self.assertEqual(amount, offer["ctx"]["cost"]["GENERIC"])
        return offer

    def test_original_controller_quantity_counterspells_use_printed_prices(self):
        graveyards = (("Generic Payment Island", "A", "graveyard"),) * 2 + (
            ("Generic Payment Island", "B", "graveyard"),) * 4
        faeries = (("Faerie Seer", "A", "battlefield"), ("Faerie Seer", "B", "battlefield"))
        artifacts = (("Darksteel Ingot", "A", "battlefield"), ("Darksteel Ingot", "B", "battlefield"))
        cases = (
            ("Rakshasa's Disdain", 2, graveyards),
            ("Circular Logic", 2, graveyards),
            ("Oppressive Will", None, (("Generic Fixed Controller Payment", "A", "hand"),)),
            ("Override", 1, artifacts),
            ("Spell Syphon", 1, faeries + artifacts),
            ("Spell Stutter", 3, faeries),
            ("Spell Rupture", 3, (("Generic Payment Victim Creature", "A", "battlefield"),
                                   ("Glorious Anthem", "A", "battlefield"))),
            ("Evasive Action", 2, (("Hallowed Fountain", "A", "battlefield"),
                                   ("Generic Payment Island", "A", "battlefield"), ("Forest", "B", "battlefield"))),
            ("Thassa's Rebuff", 2, faeries + (("Wind Drake", "A", "battlefield"),
                                               ("Generic Payment Island", "A", "battlefield"))),
        )
        for index, (name, expected, entries) in enumerate(cases):
            with self.subTest(name=name):
                session, source, victim, target = self.setup(242_858_100 + index,
                    source_name=name, resources=12, controller_generic=2,
                    arrange=self._arrange_quantity_cards(entries))
                amount = len(session.state.players["A"].zones["hand"]) if expected is None else expected
                offer = self._expect_quantity_price(session, amount)
                if expected is None:
                    self.assertNotIn("QUANTITY-0", json.dumps(session.packet("pilot:B", full=True)))
                    self.assertIsNone(session.packet("pilot:C", full=True)["decision"])
                    self.replay(session)
                self.assertEqual([True, False], offer["legal_actions"][0]["choice_schema"]["legal_values"])
                result = session.act("pilot:B", {"action_id": "choose", "pay": False})
                self.assertTrue(result.ok, result.summary)
                self.assertEqual("graveyard", self.current(session, victim).zone)
                self.assertEqual("graveyard", self.current(session, source).zone)
                counter = next(event for event in session.state.events
                    if event.code == "stack.counter" and event.details.get("stack") == target)
                self.assertEqual("A", counter.actor)

    def test_original_ixidors_will_freezes_price_through_mana_ability(self):
        from quorune.record import authoritative_state_hash

        entries = (("Faerie Seer", "A", "battlefield"), ("Generic Payment Sacrifice Wizard", "B", "battlefield"))
        session, _, victim, target = self.setup(242_858_121, source_name="Ixidor's Will",
            resources=3, controller_generic=2, arrange=self._arrange_quantity_cards(entries))
        offer = self._expect_quantity_price(session, 4)
        self.assertEqual([True, False], offer["legal_actions"][0]["choice_schema"]["legal_values"])
        before = authoritative_state_hash(session.state)
        self.assertFalse(session.act("pilot:C", {"action_id": "choose", "pay": True}).ok)
        self.assertEqual(before, authoritative_state_hash(session.state))
        self.replay(session)
        result = session.act("pilot:B", {"action_id": "choose", "pay": True})
        self.assertTrue(result.ok, result.summary)
        self.assertEqual("graveyard", session.state.cards["stack-payment:QUANTITY-1"].zone)
        self.assertEqual(0, session.state.players["B"].mana_pool["C"])
        payment = next(event for event in session.state.events
            if event.code == "counter.unless.paid" and event.details.get("stack") == target)
        self.assertEqual(4, payment.details["cost"]["GENERIC"])
        self.resolve_all(session)
        self.assertEqual("battlefield", self.current(session, victim).zone)
        self.replay(session)

    def test_original_spell_rupture_offers_zero_payment_and_decline(self):
        for paid in (False, True):
            with self.subTest(paid=paid):
                session, _, victim, _ = self.setup(242_858_132 + paid, source_name="Spell Rupture",
                    resources=0, controller_generic=1)
                offer = self._expect_quantity_price(session, 0)
                self.assertEqual([True, False], offer["legal_actions"][0]["choice_schema"]["legal_values"])
                result = session.act("pilot:B", {"action_id": "choose", "pay": paid})
                self.assertTrue(result.ok, result.summary)
                self.resolve_all(session)
                self.assertEqual("battlefield" if paid else "graveyard", self.current(session, victim).zone)
                self.replay(session)

    def test_original_rethink_reads_target_spell_x_and_skips_a_lost_target(self):
        def arrange(session):
            self.add(session.engine, "Counterspell", "THIRD-COUNTER", seat="C")
            session.state.players["C"].mana_pool["U"] = 2

        for stale in (False, True):
            with self.subTest(stale=stale):
                session, source, victim, target = self.setup(242_858_140 + stale, source_name="Rethink",
                    resources=6, victim_name="Mind Spring", victim_x=3, victim_blue=2,
                    controller_generic=2, arrange=arrange)
                if stale:
                    while session.state.priority_player != "C":
                        self.assertTrue(session.act(session.pending_principals()[0], {"action_id": "pass"}).ok)
                    action = self.offer(session, "C", "cast:THIRD-COUNTER")
                    accepted = session.act("pilot:C", {"action_id": action["id"], "targets": [target], "pay": "auto"})
                    self.assertTrue(accepted.ok, accepted.summary)
                else:
                    self._expect_quantity_price(session, 5)
                    accepted = session.act("pilot:B", {"action_id": "choose", "pay": False})
                    self.assertTrue(accepted.ok, accepted.summary)
                self.resolve_all(session)
                self.assertEqual("graveyard", self.current(session, source).zone)
                self.assertEqual("graveyard", self.current(session, victim).zone)
                counter = next(event for event in session.state.events
                    if event.code == "stack.counter" and event.details.get("stack") == target)
                self.assertEqual("C" if stale else "A", counter.actor)
                self.replay(session)


    def test_original_countervailing_winds_uses_graveyard_price_and_cycles(self):
        from quorune.record import checkpoint_envelope

        graveyards = (("Generic Payment Island", "A", "graveyard"),) * 2 + (
            ("Generic Payment Island", "B", "graveyard"),) * 4
        session, source, victim, target = self.setup(242_858_161, source_name="Countervailing Winds",
            resources=8, controller_generic=2, arrange=self._arrange_quantity_cards(graveyards))
        self._expect_quantity_price(session, 2)
        result = session.act("pilot:B", {"action_id": "choose", "pay": False})
        self.assertTrue(result.ok, result.summary)
        self.assertEqual("graveyard", self.current(session, source).zone)
        self.assertEqual("graveyard", self.current(session, victim).zone)
        counter = next(event for event in session.state.events
            if event.code == "stack.counter" and event.details.get("stack") == target)
        self.assertEqual("A", counter.actor)
        self.replay(session)

        session = self.session(242_858_162)
        engine = session.engine
        source = self.add(engine, "Countervailing Winds", "CYCLER")
        self.assert_trusted(engine, source)
        session.state.active_player = "A"
        session.state.started = True
        session.state.phase = "precombat_main"
        session.state.step = "main"
        session.state.players["A"].mana_pool["C"] = 2
        engine._grant_priority("A")
        engine.pump()
        actions = session.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        offered = next(action for action in actions if action.get("source") == source.ref and action.get("from") == "hand")
        self.assertEqual(1, offered["cost_summary"]["discard_self"])
        session.initial_checkpoint = checkpoint_envelope(session.state)
        session.commands.clear()
        session.decisions.clear()
        before = len(session.state.players["A"].zones["hand"])
        result = session.act("pilot:A", {"action_id": offered["id"]})
        self.assertTrue(result.ok, result.summary)
        self.assertEqual("graveyard", self.current(session, source).zone)
        self.assertEqual(before - 1, len(session.state.players["A"].zones["hand"]))
        self.resolve_all(session)
        self.assertEqual(before, len(session.state.players["A"].zones["hand"]))
        self.replay(session)

    def test_public_controller_quantity_wrong_controller_mutant_is_killed(self):
        from quorune.semantic_runtime.query_effect_amounts import resolve_public_query_amount

        entries = (("Generic Payment Island", "A", "graveyard"),) * 2 + (
            ("Generic Payment Island", "B", "graveyard"),) * 4
        session, _, _, _ = self.setup(242_858_151, source_name="Countervailing Winds",
            controller_generic=2, resources=8, arrange=self._arrange_quantity_cards(entries))

        def read_payer(host, value, item):
            return resolve_public_query_amount(host, value, replace(item, controller="B"))

        with patch("quorune.semantic_runtime.values.resolve_public_query_amount", side_effect=read_payer):
            with self.assertRaisesRegex(AssertionError, "2 != 4"):
                self._expect_quantity_price(session, 2)


    def _destination_case(self, seed, source_name, mana, *, victim_name="Generic Payment Victim Creature",
                          resources=5, x=None, victim_zone="hand", modes=None):
        def arrange(session):
            session.state.players["A"].mana_pool.update(mana)
        return self.setup(seed, source_name=source_name, resources=resources, x=x, arrange=arrange,
                          victim_name=victim_name, victim_zone=victim_zone, modes=modes)

    def test_original_counter_destinations_commit_to_printed_zones(self):
        cases = (
            ("Deny the Divine", {"C":2}, "exile"), ("Assert Authority", {"C":5,"U":2}, "exile"),
            ("Lapse of Certainty", {"C":2,"W":1}, "library"), ("Void Shatter", {"C":1,"U":2}, "exile"),
            ("Dissipate", {"C":1,"U":2}, "exile"), ("No Escape", {"C":2}, "exile"),
            ("Horribly Awry", {"C":1}, "exile"), ("Deny Existence", {"C":2}, "exile"),
            ("Memory Lapse", {"C":1}, "library"), ("Remand", {"C":1}, "hand"),
            ("Liquify", {"C":2}, "exile"),
        )
        for index, (name, mana, destination) in enumerate(cases):
            with self.subTest(name=name):
                session, _, victim, target = self._destination_case(242_859_100+index,name,mana)
                before = len(session.state.players["A"].zones["hand"])
                if name == "No Escape":
                    decision = self.until_choice(session)
                    self.assertEqual(["pilot:A"], session.pending_principals())
                    payload = session.packet("pilot:A",full=True)["decision"]["ctx"]
                    self.assertEqual("scry",payload["operation"])
                    selected = [row["id"] for row in payload["objects"]]
                    accepted = session.act("pilot:A",{"action_id":"choose","cards":{"top":selected,"bottom":[]}})
                    self.assertTrue(accepted.ok,accepted.summary)
                self.resolve_all(session)
                self.assertEqual(destination,self.current(session,victim).zone)
                if destination == "library":
                    self.assertEqual(victim.object_id,session.state.players["B"].zones["library"][-1])
                if name == "Remand":
                    self.assertEqual(before+1,len(session.state.players["A"].zones["hand"]))
                counter = next(event for event in session.state.events
                    if event.code=="stack.counter" and event.details.get("stack")==target)
                self.assertEqual("A",counter.actor)
                self.assertEqual(destination,counter.details["destination"])
                if name in {"No Escape", "Remand"}:
                    self.replay(session)

    def test_original_paid_counter_destinations_apply_only_on_decline(self):
        cases = (
            ("Reject", {"C":1}, None, 3, False),
            ("Syncopate", {}, 3, 3, False),
            ("Spell Shrivel", {"C":2}, None, 4, False),
            ("No More Lies", {"W":1}, None, 3, False),
            ("No More Lies", {"W":1}, None, 3, True),
        )
        for index,(name,mana,x,price,paid) in enumerate(cases):
            with self.subTest(name=name,paid=paid):
                session, _, victim, target = self._destination_case(242_859_130+index,name,mana,resources=price,x=x)
                choice = self.until_choice(session)
                self.assertEqual(["pilot:B"],session.pending_principals())
                self.assertEqual(4,choice.continuation["effect"]["schema_version"])
                offer = session.packet("pilot:B",full=True)["decision"]
                self.assertEqual(price,offer["ctx"]["cost"]["GENERIC"])
                if name=="No More Lies":
                    self.replay(session)
                result = session.act("pilot:B",{"action_id":"choose","pay":paid})
                self.assertTrue(result.ok,result.summary)
                self.resolve_all(session)
                self.assertEqual("battlefield" if paid else "exile",self.current(session,victim).zone)
                if not paid:
                    counter = next(event for event in session.state.events
                        if event.code=="stack.counter" and event.details.get("stack")==target)
                    self.assertEqual("A",counter.actor)
                if name=="No More Lies":
                    self.replay(session)

    def test_original_memory_lapse_resumes_the_commander_replacement_and_exiles_flashback(self):
        from quorune.record import authoritative_state_hash, checkpoint_envelope

        for decline in (False, True):
            with self.subTest(decline=decline):
                session = self.session(242_859_147+decline)
                engine = session.engine
                source = self.add(engine,"Memory Lapse","LAPSE")
                victim = session.state.cards[session.state.players["B"].zones["command"][0]]
                engine.state.active_player = "B"
                engine.state.started = True
                engine.state.phase = "precombat_main"
                engine.state.step = "main"
                engine.state.players["B"].mana_pool["U"] = 1
                engine.state.players["A"].mana_pool.update(U=1,C=1)
                engine._grant_priority("B")
                offered = self.offer(session,"B","cast:"+victim.ref)
                session.initial_checkpoint = checkpoint_envelope(session.state)
                session.commands.clear()
                session.decisions.clear()
                self.assertTrue(session.act("pilot:B",{"action_id":offered["id"],"pay":"auto"}).ok)
                target = self.respond(session,source,victim)
                for _ in range(16):
                    decision = session.state.pending_decision
                    if decision.kind=="replacement.order":
                        break
                    self.assertEqual("priority",decision.kind)
                    result = session.act(session.pending_principals()[0],{"action_id":"pass"})
                    self.assertTrue(result.ok,result.summary)
                else:
                    self.fail("Expected the commander owner's replacement choice")
                self.assertEqual(["pilot:B"],session.pending_principals())
                self.assertIsNone(session.packet("pilot:C",full=True)["decision"])
                self.assertEqual("stack",self.current(session,victim).zone)
                self.assertTrue(any(item.ref==target for item in session.state.stack))
                options = session.packet("pilot:B",full=True)["decision"]["ctx"]["options"]
                chosen = next(option for option in options if bool(option.get("decline")) is decline)
                command = {"action_id":"choose","replacement":chosen["id"]}
                before = authoritative_state_hash(session.state)
                self.assertFalse(session.act("pilot:C",command).ok)
                self.assertEqual(before,authoritative_state_hash(session.state))
                self.replay(session)
                result = session.act("pilot:B",command)
                self.assertTrue(result.ok,result.summary)
                self.resolve_all(session)
                self.assertEqual("library" if decline else "command",self.current(session,victim).zone)
                if decline:
                    self.assertEqual(victim.object_id,session.state.players["B"].zones["library"][-1])
                self.replay(session)

        session, _, victim, _ = self._destination_case(242_859_150,"Memory Lapse",{"C":1},
            victim_name="Think Twice",victim_zone="graveyard")
        self.resolve_all(session)
        self.assertEqual("exile",self.current(session,victim).zone)
        self.replay(session)

    def test_original_remand_draws_without_moving_an_uncounterable_spell(self):
        session, _, _, target = self._destination_case(242_859_155,"Remand",{"C":1},
            victim_name="Generic Uncounterable Payment Victim")
        before = len(session.state.players["A"].zones["hand"])
        self.resolve_all(session)
        self.assertEqual(before+1,len(session.state.players["A"].zones["hand"]))
        self.assertTrue(any(event.code=="stack.counter.failed" and event.details.get("stack")==target
                            for event in session.state.events))
        self.assertFalse(any(event.code=="stack.counter" and event.details.get("stack")==target
                             for event in session.state.events))
        self.replay(session)

    def test_original_defabricate_keeps_its_spell_and_ability_modes_separate(self):
        session, _, artifact, _ = self._destination_case(242_859_161,"Defabricate",{"C":1},
            victim_name="Sol Ring",modes=("mode_1",))
        self.resolve_all(session)
        self.assertEqual("exile",self.current(session,artifact).zone)
        self.replay(session)

        from quorune.record import checkpoint_envelope
        session = self.session(242_859_162)
        engine = session.engine
        source = self.add(engine,"Defabricate","MODE-COUNTER")
        cycler = self.add(engine,"Countervailing Winds","MODE-CYCLER",seat="B")
        engine.state.active_player = "B"
        engine.state.started = True
        engine.state.phase = "precombat_main"
        engine.state.step = "main"
        engine.state.players["B"].mana_pool["C"] = 2
        engine.state.players["A"].mana_pool.update(U=1,C=1)
        engine._grant_priority("B")
        engine.pump()
        offered = next(action for action in session.packet("pilot:B",full=True)["decision"]["ctx"]["legal"]["actions"]
                       if action.get("source")==cycler.ref and action.get("from")=="hand")
        session.initial_checkpoint = checkpoint_envelope(session.state)
        session.commands.clear()
        session.decisions.clear()
        result = session.act("pilot:B",{"action_id":offered["id"]})
        self.assertTrue(result.ok,result.summary)
        ability = session.state.stack[-1]
        self.assertEqual("activated_ability",ability.kind)
        self.respond(session,source,cycler,modes=("mode_2",),target_stack=ability.ref)
        before = len(session.state.players["B"].zones["hand"])
        self.resolve_all(session)
        self.assertEqual(before,len(session.state.players["B"].zones["hand"]))
        self.assertEqual("graveyard",self.current(session,cycler).zone)
        self.replay(session)

    def test_original_spell_crumple_moves_both_owner_library_edges(self):
        session, source, victim, _ = self._destination_case(242_859_170,"Spell Crumple",{"U":2,"C":1})
        self.resolve_all(session)
        self.assertEqual(victim.object_id,session.state.players["B"].zones["library"][0])
        self.assertEqual(source.object_id,session.state.players["A"].zones["library"][0])
        public = session.packet("pilot:C",full=True)["state"]["players"]
        self.assertNotIn("library",public["A"])
        self.assertNotIn("library",public["B"])
        self.replay(session)

    def test_counter_destination_graveyard_mutant_is_killed(self):
        session, _, victim, _ = self._destination_case(242_859_176,"Dissipate",{"U":2,"C":1})
        with patch("quorune.stack_counter.counter_destination_fields",return_value=("graveyard","top")):
            with self.assertRaisesRegex(AssertionError,"exile.*graveyard"):
                self.resolve_all(session)
                self.assertEqual("exile",self.current(session,victim).zone)

    def test_resolving_spell_library_edge_mutant_is_killed(self):
        with patch("quorune.stack_resolution._printed_library_position",return_value="top"):
            with self.assertRaises(AssertionError):
                self.test_original_spell_crumple_moves_both_owner_library_edges()


    def _finish_counter_payment(self, session, *, paid):
        self.until_choice(session)
        self.assertEqual(["pilot:B"], session.pending_principals())
        result = session.act("pilot:B", {"action_id": "choose", "pay": paid})
        self.assertTrue(result.ok, result.summary)

    def test_original_counter_prices_use_current_controlled_birds_or_flying(self):
        for index, (name, bird, grant, expected) in enumerate((
            ("Dazzling Denial", False, False, 2),
            ("Dazzling Denial", True, False, 4),
            ("Lofty Denial", False, False, 1),
            ("Lofty Denial", False, True, 4),
        )):
            with self.subTest(name=name, bird=bird, grant=grant):
                def arrange(session):
                    self.add(session.engine, "Suntail Hawk", "OTHER-BIRD", seat="B", zone="battlefield")
                    if bird:
                        self.add(session.engine, "Suntail Hawk", "BIRD-1", zone="battlefield")
                        self.add(session.engine, "Suntail Hawk", "BIRD-2", zone="battlefield")
                    if grant:
                        self.add(session.engine, "Generic Payment Victim Creature", "GROUND", zone="battlefield")
                        self.add(session.engine, "Jump", "BOOST")
                        session.state.players["A"].mana_pool["U"] = 3

                session, _, victim, _ = self.setup(242_860_100+index, source_name=name,
                    controller_generic=1, resources=4, arrange=arrange)
                if grant:
                    action = self.offer(session, "A", "cast:BOOST")
                    self.assertIn("GROUND", action["target_schema"]["legal_refs"])
                    result = session.act("pilot:A", {"action_id":action["id"], "targets":["GROUND"], "pay":"auto"})
                    self.assertTrue(result.ok, result.summary)
                self.until_choice(session)
                self.assertEqual(["pilot:B"], session.pending_principals())
                offer = session.packet("pilot:B", full=True)["decision"]
                self.assertEqual(expected, offer["ctx"]["cost"]["GENERIC"])
                result = session.act("pilot:B", {"action_id":"choose", "pay":False})
                self.assertTrue(result.ok, result.summary)
                self.resolve_all(session)
                self.assertEqual("graveyard", self.current(session, victim).zone)
                if bird or grant:
                    self.replay(session)

    def test_original_faerie_trickery_filters_creatures_kindred_and_changelings(self):
        from quorune.record import authoritative_state_hash

        for index, name in enumerate(("Faerie Seer", "Bitterblossom", "Universal Automaton")):
            with self.subTest(name=name):
                def arrange(session):
                    session.state.players["A"].mana_pool["U"] = 2
                    session.state.players["B"].mana_pool["B"] = 1

                session, source, _, target = self.setup(242_860_110+index,
                    source_name="Faerie Trickery", victim_name=name, controller_generic=1,
                    resources=3, arrange=arrange, respond_to_victim=False)
                for _ in range(8):
                    if session.state.priority_player == "A":
                        break
                    self.assertTrue(session.act(session.pending_principals()[0], {"action_id":"pass"}).ok)
                self.assertEqual("A", session.state.priority_player)
                actions = session.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
                self.assertFalse(any(action["id"] == "cast:"+source.ref for action in actions))
                before = authoritative_state_hash(session.state)
                denied = session.act("pilot:A", {"action_id":"cast:"+source.ref, "targets":[target], "pay":"auto"})
                self.assertFalse(denied.ok)
                self.assertEqual(before, authoritative_state_hash(session.state))

        session, _, victim, _ = self._destination_case(242_860_114, "Faerie Trickery", {"U":2,"C":1})
        self.resolve_all(session)
        self.assertEqual("exile", self.current(session, victim).zone)
        self.replay(session)

    def test_original_counter_followups_resolve_after_either_payment_outcome(self):
        from quorune.record import authoritative_state_hash

        cases = ("Overrule", "Mindswipe", "Frightful Delusion", "Abstruse Interference")
        for index, name in enumerate(cases):
            for paid in (False, True):
                with self.subTest(name=name, paid=paid):
                    def arrange(session):
                        session.state.players["A"].mana_pool.update(W=1, R=1)
                        self.add(session.engine, "Generic Payment Island", "PRIVATE-DISCARD", seat="B")

                    announced_x = 3 if name in {"Overrule", "Mindswipe"} else None
                    session, _, victim, _ = self.setup(242_860_140+index*2+paid,
                        source_name=name, x=announced_x, controller_generic=announced_x or 2,
                        resources=3, arrange=arrange)
                    self.until_choice(session)
                    offer = session.packet("pilot:B", full=True)["decision"]
                    self.assertEqual(announced_x or 1, offer["ctx"]["cost"]["GENERIC"])
                    self.assertEqual(2 if name=="Abstruse Interference" else 5,
                                     session.state.pending_decision.continuation["effect"]["schema_version"])
                    life_a = session.state.players["A"].life
                    life_b = session.state.players["B"].life
                    result = session.act("pilot:B", {"action_id":"choose", "pay":paid})
                    self.assertTrue(result.ok,result.summary)
                    if name == "Frightful Delusion":
                        self.assertEqual("choice.apnap", session.state.pending_decision.kind)
                        self.assertEqual(["pilot:B"],session.pending_principals())
                        self.assertIsNone(session.packet("pilot:A",full=True)["decision"])
                        self.assertNotIn("PRIVATE-DISCARD",json.dumps(session.packet("pilot:A",full=True)))
                        before = authoritative_state_hash(session.state)
                        command = {"action_id":"choose","cards":["PRIVATE-DISCARD"]}
                        self.assertFalse(session.act("pilot:A",command).ok)
                        self.assertEqual(before,authoritative_state_hash(session.state))
                        result=session.act("pilot:B",command)
                        self.assertTrue(result.ok,result.summary)
                        self.assertEqual("graveyard",session.state.cards["stack-payment:PRIVATE-DISCARD"].zone)
                    self.resolve_all(session)
                    self.assertEqual("battlefield" if paid else "graveyard", self.current(session,victim).zone)
                    if name == "Overrule":
                        self.assertEqual(life_a+3, session.state.players["A"].life)
                    elif name == "Mindswipe":
                        self.assertEqual(life_b-3, session.state.players["B"].life)
                    elif name == "Abstruse Interference":
                        self.assertEqual(1,self._counter_followup_token_count(session,"eldrazi scion"))
                    if paid or name == "Mindswipe":
                        self.replay(session)

    def _counter_followup_token_count(self,session,subtype):
        return sum(card.zone=="battlefield" and card.controller=="A" and card.is_token
            and subtype in session.engine._effective_card_data(card)["type_line"].casefold()
            for card in session.state.cards.values())

    def _resolve_lander_payment(self, paid):
        session, _, victim, _ = self.setup(242_860_165+paid, source_name="Divert Disaster",
            controller_generic=1, resources=2)
        self._finish_counter_payment(session, paid=paid)
        self.resolve_all(session)
        self.assertEqual("battlefield" if paid else "graveyard",self.current(session,victim).zone)
        self.assertEqual(int(paid),self._counter_followup_token_count(session,"lander"))
        return session

    def test_original_counter_paid_followups_keep_surveil_and_lander_private_or_conditional(self):
        from quorune.record import authoritative_state_hash
        for paid in (False, True):
            with self.subTest(paid=paid):
                self.replay(self._resolve_lander_payment(paid))
                session, _, victim, _ = self.setup(242_860_172+paid,source_name="Don't Make a Sound",
                    controller_generic=1, resources=2)
                before_graveyard = len(session.state.players["A"].zones["graveyard"])
                self._finish_counter_payment(session,paid=paid)
                if paid:
                    self.until_choice(session)
                    self.assertEqual(["pilot:A"],session.pending_principals())
                    self.assertIsNone(session.packet("pilot:B",full=True)["decision"])
                    ctx=session.packet("pilot:A",full=True)["decision"]["ctx"]
                    self.assertEqual("surveil",ctx["operation"])
                    looked=[row["id"] for row in ctx["objects"]]
                    self.assertEqual(2,len(looked))
                    command={"action_id":"choose","cards":{"top":[looked[0]],"graveyard":[looked[1]]}}
                    before=authoritative_state_hash(session.state)
                    self.assertFalse(session.act("pilot:B",command).ok)
                    self.assertEqual(before,authoritative_state_hash(session.state))
                    self.replay(session)
                    result=session.act("pilot:A",command)
                    self.assertTrue(result.ok,result.summary)
                self.resolve_all(session)
                self.assertEqual(before_graveyard+1+int(paid),len(session.state.players["A"].zones["graveyard"]))
                self.assertEqual("battlefield" if paid else "graveyard", self.current(session,victim).zone)
                self.replay(session)

    def test_counter_payment_paid_followup_on_decline_mutant_is_killed(self):
        from quorune.rules.stack_controller_payment_cost import bound_counter_payment_followup_effects

        def always(effect, *, paid):
            return bound_counter_payment_followup_effects(effect,paid=True)

        with patch("quorune.semantic_choices.payments.bound_counter_payment_followup_effects",side_effect=always):
            with self.assertRaisesRegex(AssertionError,"0 != 1"):
                self._resolve_lander_payment(False)


class TargetedCounterTemplateTests(unittest.TestCase):
    def test_shared_direct_target_schema_rejects_mixed_or_malformed_predicates(self):
        self.assertEqual(
            {
                "zones": ["battlefield"],
                "categories": ["permanent"],
                "count": 1,
                "types_none": ["land"],
            },
            permanent_target_schema(types_none=("land",)),
        )
        for operation in (
            lambda: permanent_target_schema(
                types_any=("creature",),
                types_none=("land",),
            ),
            lambda: permanent_target_schema(types_any="creature"),
            lambda: stack_target_schema(categories=()),
            lambda: stack_target_schema(categories=("spell", "spell")),
            lambda: stack_target_schema(
                categories=("spell",),
                types_any=("creature",),
                colors_any=("U",),
            ),
            lambda: stack_target_schema(
                categories=("spell",),
                colorless="yes",
            ),
        ):
            with self.subTest(operation=operation):
                with self.assertRaises(ValueError):
                    operation()

    def test_targeted_counter_template_is_immutable_and_copy_isolated(self):
        template = TargetedCounterEffectTemplate(
            CounterTarget.CREATURE_SPELL
        )

        self.assertEqual(
            "counter-target-creature-spell-v2", template.template_id
        )
        self.assertEqual(
            ({"op": "counter_stack_target", "stack": "$target.0"},),
            template.effects,
        )
        schema = template.target_schema
        schema["types_any"].append("artifact")
        effects = template.effects
        effects[0]["op"] = "counter_stack"
        self.assertEqual(
            ["creature"], template.target_schema["types_any"]
        )
        self.assertEqual("counter_stack_target", template.effects[0]["op"])
        with self.assertRaisesRegex(ValueError, "target"):
            TargetedCounterEffectTemplate(  # type: ignore[arg-type]
                "creature spell"
            )

    def test_counter_whole_clause_parser_accepts_only_closed_direct_targets(self):
        for target in CounterTarget:
            with self.subTest(target=target):
                template = targeted_counter_effect_template(
                    f"Counter target {target.value}."
                )
                self.assertIsNotNone(template)
                assert template is not None
                self.assertEqual(target, template.target)
                self.assertTrue(template.target_schema["source_exclusion"])
        for text in (
            "Counter up to one target spell.",
            "You may counter target spell.",
            "Counter another target spell.",
            "Counter target spell unless its controller pays {2}.",
            "Counter target spell with mana value X or less.",
            "Counter all other spells.",
            "Counter target spell. Exile it instead of putting it into its owner's graveyard.",
        ):
            with self.subTest(text=text):
                self.assertIsNone(targeted_counter_effect_template(text))

    def test_intrinsic_counter_prohibition_requires_the_complete_sentence(self):
        self.assertTrue(
            is_intrinsically_uncounterable_spell(
                "This spell can't be countered."
            )
        )
        self.assertTrue(
            is_intrinsically_uncounterable_spell(
                "This spell cannot be countered."
            )
        )
        for text in (
            "This spell can't be countered by blue spells or abilities.",
            "If {G} was spent to cast this spell, it can't be countered.",
            "Creature spells you control can't be countered.",
            "Target spell can't be countered this turn.",
        ):
            with self.subTest(text=text):
                self.assertFalse(is_intrinsically_uncounterable_spell(text))


class TargetedCounterCompilerTests(unittest.TestCase):

    def test_counter_destination_descriptors_are_closed_to_one_spell(self):
        from quorune.compiler.counter_templates import targeted_controller_payment_template
        from quorune.rules.node_capability_shapes import targeted_counter_node_capabilities

        suffix = " If that spell is countered this way, exile it instead of putting it into its owner's graveyard."
        for parsed in (targeted_counter_effect_template("Counter target spell."+suffix),
                       targeted_controller_payment_template("Counter target spell unless its controller pays {X}."+suffix,
                           cast_x_available=True)):
            self.assertIsNotNone(parsed)
            _, effects, schema, mechanics = parsed.compiled()
            owned = targeted_counter_node_capabilities(effects=effects,target_schema=schema,mechanic_ids=mechanics)
            self.assertIn("stack.counter.destination_replacement",owned)
            self.assertEqual("exile",effects[0]["counter_destination"])
            for bad in ("battlefield",True,None):
                forged = {**effects[0],"counter_destination":bad}
                self.assertEqual((),targeted_counter_node_capabilities(
                    effects=(forged,),target_schema=schema,mechanic_ids=mechanics))
            ability_schema = {**schema,"categories":["ability"]}
            self.assertEqual((),targeted_counter_node_capabilities(
                effects=effects,target_schema=ability_schema,mechanic_ids=mechanics))
        self.assertIsNone(targeted_counter_effect_template("Counter target activated ability."+suffix))
        source = StackControllerPaymentCompilerTests.record(
            "Counter target spell."+suffix+" You may cast that card without paying its mana cost.")
        result = compile_oracle_card(source,capability_registry=load_default_capability_registry(),
                                     capability_profile="commander_review")
        self.assertTrue(any(row.kind=="replacement_effect" for row in result.material_residuals))


    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.db = focused_card_database(cls.temporary.name)
        cls.base = cls.db.lookup("Counterspell")
        cls.capabilities = load_default_capability_registry()

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temporary.cleanup()

    def compile(self, oracle_text: str, *, type_line: str = "Instant"):
        return compile_oracle_card(
            replace(
                self.base,
                name="Fixture",
                oracle_text=oracle_text,
                type_line=type_line,
                keywords=(),
                faces=(),
            ),
            capability_registry=self.capabilities,
            capability_profile="commander_review",
        )

    def test_spell_trigger_and_activated_contexts_share_targeted_counter_lowering(
        self,
    ):
        contexts = (
            (
                "Counter target spell.",
                "Instant",
                "spell_ability",
                "counter-target-spell-v2",
            ),
            (
                "When this creature enters, counter target activated ability.",
                "Creature — Test",
                "triggered_ability",
                "counter-target-activated-ability-v2",
            ),
            (
                "{U}, {T}: Counter target triggered ability.",
                "Creature — Test",
                "activated_ability",
                "counter-target-triggered-ability-v2",
            ),
        )
        for text, type_line, kind, template_id in contexts:
            with self.subTest(kind=kind, text=text):
                ir = self.compile(text, type_line=type_line)
                node = ir.faces[0].nodes[0]
                self.assertEqual("exact", ir.status)
                self.assertTrue(node.exact)
                self.assertEqual(kind, node.kind)
                self.assertEqual(template_id, node.template_id)
                self.assertEqual(
                    {
                        "stack.counter.effect",
                        "target.revalidate_resolution",
                    },
                    set(node.capability_dependencies)
                    - {
                        "trigger.event.normalized_zone_change",
                        "trigger.placement.apnap",
                    },
                )
                self.assertEqual(text, text[node.span.start : node.span.end])

    def test_all_closed_counter_target_domains_have_precise_source_spans(self):
        for target in CounterTarget:
            text = f"Counter target {target.value}."
            with self.subTest(target=target):
                ir = self.compile(text)
                node = ir.faces[0].nodes[0]
                self.assertEqual("exact", ir.status)
                self.assertEqual(text, node.text)
                self.assertEqual(text, text[node.span.start : node.span.end])
                self.assertTrue(node.target_schema["source_exclusion"])

    def test_unsupported_counter_variants_remain_material_residuals(self):
        for text in (
            "Counter up to one target spell.",
            "Counter target spell unless its controller discards a card.",
            "Counter target spell with mana value X or less.",
            "Counter all spells.",
            "Counter target spell. If that spell is countered this way, put it onto the battlefield under your control instead of putting it into its owner's graveyard.",
        ):
            with self.subTest(text=text):
                ir = self.compile(text)
                self.assertNotEqual("exact", ir.status)
                self.assertTrue(ir.material_residuals)

    def test_targeted_counter_shape_mutants_fail_closed(self):
        template = TargetedCounterEffectTemplate(CounterTarget.SPELL)
        expected = {
            "stack.counter.effect",
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
        malformed_effects = (
            ({"op": "counter_stack_target", "stack": "$target.1"},),
            ({"op": "counter_stack_target", "stack": "$source"},),
            (
                {
                    "op": "counter_stack_target",
                    "stack": "$target.0",
                    "destination": "exile",
                },
            ),
            ({"op": "counter_stack", "stack": "$target.0"},),
        )
        for effects in malformed_effects:
            with self.subTest(effects=effects):
                self.assertFalse(
                    capability_dependencies_for_node(
                        effects=effects,
                        target_schema=template.target_schema,
                        mechanic_ids=template.mechanics,
                    )
                )
        malformed_schemas = (
            {**template.target_schema, "zones": ["battlefield"]},
            {**template.target_schema, "count": 2},
            {**template.target_schema, "source_exclusion": False},
            {**template.target_schema, "controller": "opponent"},
            {**template.target_schema, "types_any": ["dragon"]},
        )
        for schema in malformed_schemas:
            with self.subTest(schema=schema):
                self.assertFalse(
                    capability_dependencies_for_node(
                        effects=template.effects,
                        target_schema=schema,
                        mechanic_ids=template.mechanics,
                    )
                )
        self.assertFalse(
            capability_dependencies_for_node(
                effects=template.effects,
                target_schema=template.target_schema,
                mechanic_ids=("cr-115-targets",),
            )
        )

    def test_intrinsic_uncounterable_compiles_as_stack_capability(self):
        text = "This spell can't be countered."
        ir = self.compile(text)
        node = ir.faces[0].nodes[0]

        self.assertEqual("exact", ir.status)
        self.assertEqual("static_ability", node.kind)
        self.assertEqual("stack", node.active_zone)
        self.assertEqual("continuous", node.event)
        self.assertEqual(
            "intrinsic-spell-counter-prohibition-v1", node.template_id
        )
        self.assertEqual(
            ("stack.counter.prohibition.intrinsic",),
            node.capability_dependencies,
        )
        self.assertEqual(text, text[node.span.start : node.span.end])

    def test_conditional_counter_prohibitions_remain_residuals(self):
        for text in (
            "This spell can't be countered by blue spells or abilities.",
            "If {G} was spent to cast this spell, it can't be countered.",
            "Target spell can't be countered this turn.",
        ):
            with self.subTest(text=text):
                ir = self.compile(text)
                self.assertNotEqual("exact", ir.status)
                self.assertTrue(ir.material_residuals)

    def test_generated_targeted_counter_program_is_capability_closed(self):
        registry = SemanticRegistry(include_builtin_packs=False)
        result = register_generated_programs(
            self.db,
            registry,
            (self.db.lookup("Counterspell"),),
            capability_registry=self.capabilities,
            capability_profile="commander_review",
            promote_exact_effect_programs=True,
        )
        programs = [program for program in registry.programs() if program.effects]
        self.assertEqual(1, result["exact_effect_programs_promoted"])
        self.assertEqual(1, len(programs))
        self.assertEqual("trusted", programs[0].trust_level)
        self.assertTrue(
            {
                "stack.counter.effect",
                "target.revalidate_resolution",
            }.issubset(programs[0].capability_dependencies)
        )

    def test_generated_intrinsic_prohibition_is_a_trusted_static_declaration(
        self,
    ):
        registry = SemanticRegistry(include_builtin_packs=False)
        result = register_generated_programs(
            self.db,
            registry,
            (self.db.lookup("Unanswerable Test Spell"),),
            capability_registry=self.capabilities,
            capability_profile="commander_review",
            promote_exact_capability_declarations=True,
        )
        programs = registry.programs_for_oracle(
            self.db.lookup("Unanswerable Test Spell").oracle_id
        )

        self.assertEqual(1, result["exact_programs_promoted"])
        self.assertEqual(1, len(programs))
        self.assertEqual("trusted", programs[0].trust_level)
        self.assertEqual("static:front:n1", programs[0].ability_id)
        self.assertEqual("stack", programs[0].active_zone)
        self.assertEqual("continuous", programs[0].event)
        self.assertEqual(
            ["stack.counter.prohibition.intrinsic"],
            programs[0].capability_dependencies,
        )


class FixedManaValueCounterCompilerTests(unittest.TestCase):
    # CR 202.3e and 608.2b: mana-value filters apply to the current stack
    # object, including announced X, and the same predicate is revalidated.
    @classmethod
    def setUpClass(cls):
        cls.registry = load_default_capability_registry()

    def compile(self, text, *, type_line="Instant"):
        return compile_oracle_card(StackControllerPaymentCompilerTests.record(text, type_line=type_line),
            capability_registry=self.registry, capability_profile="commander_review")

    def test_fixed_mana_value_counter_targets_keep_equal_lower_and_upper_bounds(self):
        for text, field, value in (
            ("Counter target spell with mana value 4 or greater.", "mana_value_min", 4),
            ("Counter target spell with mana value 1 or less.", "mana_value_max", 1),
            ("Counter target spell with mana value 2.", "mana_value", 2),
            ("Counter target creature spell with mana value 3 or less.", "mana_value_max", 3),
        ):
            with self.subTest(text=text):
                ir = self.compile(text)
                self.assertEqual("exact", ir.status, ir.material_residuals)
                node = ir.faces[0].nodes[0]
                self.assertEqual(value, node.target_schema[field])
                self.assertEqual(["spell"], node.target_schema["categories"])
                self.assertEqual(({"op": "counter_stack_target", "stack": "$target.0"},), node.effects)
                self.assertIn("stack.counter.effect", node.capability_dependencies)
                self.assertIn("target.revalidate_resolution", node.capability_dependencies)

    def test_fixed_stack_mana_value_shapes_costs_and_mutants_fail_closed(self):
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.node_capability_shapes import fixed_stack_counter_target_schema_is_closed
        text = "Counter target spell with mana value 4 or greater."
        node = self.compile(text).faces[0].nodes[0]
        for field, value in (("mana_value_min", True), ("mana_value_min", -1),
                             ("mana_value_min", 1.5), ("mana_value_max", 5), ("count", True)):
            with self.subTest(field=field, value=value):
                self.assertFalse(fixed_stack_counter_target_schema_is_closed({**node.target_schema, field: value}))
        for text in ("Counter target spell with mana value X or less.",
                     "Counter target activated ability with mana value 2.",
                     "Counter target spell with mana value 2, then draw that many cards."):
            self.assertNotEqual("exact", self.compile(text).status)
        raw = json.loads((ROOT / "quorune/rules/capability-registry.json").read_text(encoding="utf-8"))
        next(row for row in raw["capabilities"] if row["id"] == "stack.counter.effect").update(
            status="blocked", blockers=["stack counter authority unavailable"])
        blocked = compile_oracle_card(StackControllerPaymentCompilerTests.record("Counter target spell with mana value 4 or greater."),
            capability_registry=CapabilityRegistry(raw), capability_profile="commander_review")
        self.assertNotEqual("exact", blocked.status)
        from quorune.compiler import resolution_effect_templates
        compilers = tuple(compiler for compiler in resolution_effect_templates._DIRECT_RESOLUTION_EFFECT_COMPILERS
            if compiler is not resolution_effect_templates.targeted_counter_effect_template)
        with patch.object(resolution_effect_templates, "_DIRECT_RESOLUTION_EFFECT_COMPILERS", compilers):
            mutant = self.compile("Counter target spell with mana value 4 or greater.")
        self.assertNotEqual("exact", mutant.status)
        paid = self.compile("Counter target creature spell with mana value 3 or less unless its controller pays {2}.")
        self.assertEqual("exact", paid.status, paid.material_residuals)
        self.assertEqual(3, paid.faces[0].nodes[0].target_schema["mana_value_max"])


class FixedManaValueCounterRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from quorune.deck import DeckDefinition, DeckEntry
        cls.temporary = tempfile.TemporaryDirectory()
        path = Path(cls.temporary.name) / "fixed-stack-mana-value.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures/bound-effect-program-cards.json",
            ROOT / "tests/fixtures/fixed-stack-mana-value-cards.json"], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Fixed stack mana value", [DeckEntry("Generic Bound Commander", 1, "commander"),
            DeckEntry("Generic Bound Plains", 99)], ["Generic Bound Commander"])

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temporary.cleanup()

    import test_bound_effect_programs as _witnesses
    session = _witnesses.BoundEffectProgramRuntimeTests.session
    add = _witnesses.BoundEffectProgramRuntimeTests.add
    ready = _witnesses.BoundEffectProgramRuntimeTests.ready
    checkpoint = _witnesses.BoundEffectProgramRuntimeTests.checkpoint
    resolve = _witnesses.BoundEffectProgramRuntimeTests.resolve
    replay = _witnesses.BoundEffectProgramRuntimeTests.replay

    def test_stack_target_projection_preserves_cost_semantics_for_spell_copies(self):
        from types import SimpleNamespace
        from quorune.model import CardInstance, StackItem
        from quorune.selection.targeting import TargetSelectionOwnerMixin
        from quorune.targets import TargetGroup
        from quorune.characteristic_evaluation import type_parts
        group = TargetGroup.from_mapping({"zones": ["stack"], "categories": ["spell"], "count": 1})
        for cost, x, expected in (("{X}{X}{U}", 3, 7), ("{2/U}{U/P}", 0, 3), ("", 0, 0)):
            for kind in ("spell", "spell_copy"):
                with self.subTest(cost=cost, kind=kind):
                    card = CardInstance(object_id="projection-cost-card", ref="projection-cost-card", oracle_id="projection-cost",
                        printed_name="Projection Cost", owner="A", controller="A", zone="stack")
                    item = StackItem(stack_id="projection-stack", ref="projection-stack", kind=kind, controller="A",
                        label="Projection Cost", card_object_id=card.object_id, x_value=x)
                    data = {"mana_cost": cost, "cmc": 1, "type_line": "Instant", "colors": ["U"], "keywords": []}
                    host = SimpleNamespace(state=SimpleNamespace(stack=[item], cards={card.object_id: card}),
                        active_seats=[], _effective_card_data=lambda card: data, _type_parts=type_parts)
                    rows = TargetSelectionOwnerMixin._target_candidate_rows(host, "A", group)
                    self.assertEqual(expected, rows[0]["mana_value"])

    def test_original_stroke_uses_announced_x_in_real_response_offers_and_replays(self):
        from quorune.card_programs import bind_card_program_runtime
        from quorune.card_programs.adapters import compile_best_available_card_program
        from quorune.record import authoritative_state_hash
        for x in (0, 3):
            with self.subTest(x=x):
                session = self.session(340010 + x)
                spell = self.add(session.engine, "Generic Fixed Stack X Draw", zone="hand")
                counter = self.add(session.engine, "Disdainful Stroke", seat="B", zone="hand")
                for card in (spell, counter):
                    program = compile_best_available_card_program(self.db, self.db.lookup(card.printed_name),
                        semantic_registry=session.engine.semantics, capability_registry=self.registry, capability_profile="commander_review")
                    self.assertTrue(bind_card_program_runtime(program, capability_registry=self.registry, profile="commander_review")["strict_capability_ready"])
                session.state.players["B"].mana_pool.update({"U": 2})
                hand_before = len(session.state.players["A"].zones["hand"])
                action = self.ready(session, spell, {"G": x + 1})
                self.checkpoint(session)
                cast = session.act("pilot:A", {"action_id": action["id"], "x": x, "pay": "auto"})
                self.assertTrue(cast.ok, cast.summary)
                target_ref = session.state.stack[-1].ref
                self.assertTrue(session.act("pilot:A", {"action_id": "pass"}).ok)
                actions = session.packet("pilot:B", full=True)["decision"]["ctx"]["legal"]["actions"]
                offered = next((action for action in actions if action.get("card") == counter.ref), None)
                if x == 3:
                    self.assertIsNotNone(offered)
                    self.assertIn(target_ref, offered["target_schema"]["legal_refs"])
                    before = authoritative_state_hash(session.state)
                    denied = session.act("pilot:A", {"action_id": offered["id"], "targets": [target_ref], "pay": "auto"})
                    self.assertFalse(denied.ok)
                    self.assertEqual(before, authoritative_state_hash(session.state))
                    responded = session.act("pilot:B", {"action_id": offered["id"], "targets": [target_ref], "pay": "auto"})
                    self.assertTrue(responded.ok, responded.summary)
                else:
                    self.assertIsNone(offered)
                    before = authoritative_state_hash(session.state)
                    denied = session.act("pilot:B", {"action_id": "cast:" + counter.ref, "targets": [target_ref], "pay": "auto"})
                    self.assertFalse(denied.ok)
                    self.assertEqual(before, authoritative_state_hash(session.state))
                session = self.replay(session, load=True)
                self.resolve(session)
                self.assertEqual(hand_before - (1 if x == 3 else 0), len(session.state.players["A"].zones["hand"]))
                self.assertEqual("graveyard", session.state.cards[spell.object_id].zone)
                self.assertEqual("graveyard" if x == 3 else "hand", session.state.cards[counter.object_id].zone)
                self.assertEqual(0 if x == 3 else 2, session.state.players["B"].mana_pool.get("U", 0))
                self.replay(session, load=True)


if __name__ == "__main__":
    unittest.main()
