from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import tempfile
import unittest

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
            trust_level="trusted", capability_registry=load_default_capability_registry(),
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
        programs = engine.semantics.programs_for_oracle(card.oracle_id)
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

    def respond(self, session, source, victim, *, x=None, activated=False):
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
        target = next(item.ref for item in session.engine.state.stack if item.card_object_id==victim.object_id)
        action = self.offer(session, "A", action_id)
        command = {"action_id":action_id, "pay":"auto"}
        if action.get("target_schema") is not None:
            self.assertIn(target, action["target_schema"]["legal_refs"])
            command["targets"] = [target]
        if x is not None:
            command["x"] = x
        result = session.act("pilot:A", command)
        self.assertTrue(result.ok, result.summary)
        return target

    def setup(self, seed, *, source_name="Generic Fixed Controller Payment", x=None,
              resources=3, victim_name="Generic Payment Victim Creature", activated=False, mana_lands=0,
              current_payer=None):
        from quorune.record import checkpoint_envelope
        session = self.session(seed)
        engine = session.engine
        source = self.add(engine, source_name, "COUNTER", zone="battlefield" if activated else "hand")
        victim = self.add(engine, victim_name, "VICTIM", seat="B")
        self.assert_trusted(engine, source)
        self.assert_trusted(engine, victim)
        for index in range(mana_lands):
            self.add(engine, "Generic Payment Island", f"PAYER-MANA-{index}", seat="B", zone="battlefield")
        engine.state.active_player = "B"
        engine.state.started = True
        engine.state.phase = "precombat_main"
        engine.state.step = "main"
        engine.state.players["B"].mana_pool.update(U=1, C=resources)
        engine.state.players["A"].mana_pool.update(U=1, C=x or 0)
        engine._grant_priority("B")
        action = self.offer(session, "B", "cast:"+victim.ref)
        session.initial_checkpoint = checkpoint_envelope(engine.state)
        session.commands.clear()
        session.decisions.clear()
        result = session.act("pilot:B", {"action_id":action["id"], "pay":"auto"})
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
        target = self.respond(session, source, victim, x=x, activated=activated)
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
            "Counter target spell with mana value 2 or less.",
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
            "Counter target spell with mana value 2 or less.",
            "Counter all spells.",
            "Counter target spell. If that spell is countered this way, exile it instead of putting it into its owner's graveyard.",
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


if __name__ == "__main__":
    unittest.main()
