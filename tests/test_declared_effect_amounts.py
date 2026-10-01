from __future__ import annotations

"""Declared-X compiler contracts, independently based on CR 107.3 and 608.2h.

An announced spell-cost X is retained on the stack; a closed public definition
uses the game facts when its instruction is applied. Neither permits changing
the target domain or silently discarding a definition's unsupported rider.
"""

import unittest
from types import SimpleNamespace
from unittest import mock
import inspect
from dataclasses import replace
from copy import deepcopy
import json
from pathlib import Path
import tempfile

from quorune.carddb import CardRecord
from quorune.oracle_ir import compile_oracle_card
from quorune.rules.capabilities import load_default_capability_registry
from quorune.compiler.query_characteristic_templates import query_characteristic_quantity
from quorune.query_effect_amount_model import (
    PublicQueryAmountError, PublicQueryAmountSpec, CastXAmountSpec,
    scope_declared_amount_bindings,
)
from quorune.semantic_runtime.query_effect_amounts import resolve_public_query_amount, resolve_cast_x_amount
from common import ROOT
import test_bound_effect_programs as helpers
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.record import authoritative_state_hash, replay_record
from quorune.card_programs import bind_card_program_runtime
from quorune.card_programs.adapters import compile_best_available_card_program
from quorune.semantics import SemanticRegistry
from quorune.permissions import CapabilityManager
from quorune.continuous_effect_state import expire_end_of_turn_continuous_effects
from scripts.build_test_database import build_fixture_database


def generic_spell(text: str, *, mana_cost: str = "{X}{G}") -> CardRecord:
    return CardRecord(
        oracle_id="00000000-0000-4000-8000-000000447001",
        name="Generic Declared Amount Fixture",
        mana_cost=mana_cost,
        mana_value=1.0 if "{X}" in mana_cost else 3.0,
        type_line="Sorcery",
        oracle_text=text,
        power=None,
        toughness=None,
        loyalty=None,
        defense=None,
        colors=("G",),
        color_identity=("G",),
        keywords=(),
        produced_mana=(),
        layout="normal",
        released_at="2026-01-01",
        legalities={"commander": "legal"},
        faces=(),
        raw={},
    )


class DeclaredEffectAmountCompilerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.capabilities = load_default_capability_registry()

    def compile(self, record):
        return compile_oracle_card(
            record,
            capability_registry=self.capabilities,
            capability_profile="commander_review",
        )

    def test_announced_spell_x_uses_existing_result_families(self):
        for text in (
            "Draw X cards.",
            "This spell deals X damage to any target.",
            "You gain X life and draw X cards.",
            "Create X tapped 2/2 green Bear creature tokens.",
        ):
            with self.subTest(text=text):
                compiled = self.compile(generic_spell(text))
                self.assertEqual("exact", compiled.status)
                self.assertFalse(compiled.material_residuals)
                self.assertTrue(compiled.faces[0].nodes[0].effects)

    def test_public_definition_can_bind_multiple_result_slots(self):
        for text in (
            "This spell deals X damage to each creature, where X is the number of creatures on the battlefield.",
            "Target player draws X cards and loses X life, where X is the number of artifacts you control.",
            "You draw X cards and you lose X life, where X is the number of cards in your hand.",
        ):
            with self.subTest(text=text):
                compiled = self.compile(generic_spell(text, mana_cost="{2}{G}"))
                self.assertEqual("exact", compiled.status)
                self.assertFalse(compiled.material_residuals)

    def test_source_pronoun_definition_uses_only_existing_entry_and_death_contexts(self):
        for event in ("enters", "dies"):
            with self.subTest(event=event):
                record = replace(generic_spell(
                    f"When this creature {event}, it deals X damage to target creature an opponent controls, where X is the number of Wizards you control.",
                    mana_cost="{2}{G}",
                ),type_line="Creature — Wizard",power="2",toughness="3")
                compiled = self.compile(record)
                self.assertEqual("exact",compiled.status)
                node = compiled.faces[0].nodes[0]
                self.assertEqual("$source",node.effects[0]["source"])
                self.assertEqual("public_query_effect_amount",node.effects[0]["amount"]["kind"])

    def test_undefined_x_dynamic_targets_and_open_definitions_remain_residual(self):
        for record in (
            generic_spell("Draw X cards.", mana_cost="{2}{G}"),
            generic_spell("Destroy target creature with mana value X."),
            generic_spell("Return target creature card with mana value X or less from your graveyard to the battlefield."),
            generic_spell("Draw X cards, where X is the number of creatures you control plus one.", mana_cost="{2}{G}"),
            generic_spell("Draw X cards, where X is the number of creatures with flying you control.", mana_cost="{2}{G}"),
            generic_spell("Draw X cards, where X is the number of cards in target player's hand.", mana_cost="{2}{G}"),
        ):
            with self.subTest(text=record.oracle_text):
                compiled = self.compile(record)
                self.assertNotEqual("exact", compiled.status)
                self.assertTrue(compiled.material_residuals)

    def test_nested_modal_declarations_remain_residual_until_instruction_scopes_are_owned(self):
        body = "You draw X cards and you lose X life, where X is the number of cards in your hand."
        compiled = self.compile(generic_spell(
            "Choose one or both —\n• " + body + "\n• " + body,
            mana_cost="{2}{G}",
        ))
        self.assertNotEqual("exact", compiled.status)
        self.assertTrue(compiled.material_residuals)

    def test_declared_amount_capability_and_result_dependencies_cannot_be_omitted(self):
        from quorune.rules.capabilities import capability_dependencies_for_node
        from quorune.compiler.program_generation import _is_closed_effect_program
        from quorune.oracle_ir import generated_programs

        row = generic_spell("Draw X cards.")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/"dependencies.sqlite3"
            build_fixture_database([helpers.FIXTURE],path)
            with CardDatabase(path) as db:
                program = generated_programs(db,row,trust_level="trusted",
                    capability_registry=self.capabilities,capability_profile="commander_review")[0]
        self.assertTrue(_is_closed_effect_program(program))
        for capability in ("quantity_expression.declared_effect_amount","zone.draw.library_to_hand"):
            with self.subTest(capability=capability):
                dependencies = [value for value in program.capability_dependencies if value != capability]
                closure = self.capabilities.closure(dependencies,profile="commander_review").to_dict()
                self.assertFalse(_is_closed_effect_program(replace(program,
                    capability_dependencies=dependencies,capability_closure=closure)))
        effect = program.effects[0]
        for malformed in (
            {**effect,"count":{**effect["count"],"coefficient":True}},
            {**effect,"count":{**effect["count"],"unknown":1}},
            {**effect,"count":1,"player":effect["count"]},
        ):
            with self.subTest(malformed=malformed):
                self.assertEqual((),capability_dependencies_for_node(
                    effects=(malformed,),target_schema=program.target_schema,
                    mechanic_ids=program.coverage))

    def test_registered_measurement_requires_actual_whole_card_runtime_closure(self):
        from quorune.compiler.unlock_frontier import analyze_card_unlocks
        from scripts.work_selection_cohort_measurements import _bound_effect_program_measurement

        records = [replace(generic_spell(text),oracle_id=f"fixture:declared-measure-{index}")
            for index,text in enumerate(("Draw X cards.","Draw X cards.\nDraw a card if you control a land."))]
        with mock.patch("quorune.compiler.effect_template_composition.declared_effect_amount_template",return_value=None):
            baseline = [analyze_card_unlocks(self.compile(row),program=None,program_error=None,
                capabilities=self.capabilities,profile="commander_review") for row in records]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/"measurement.sqlite3"
            build_fixture_database([helpers.FIXTURE],path)
            with CardDatabase(path) as db:
                result = _bound_effect_program_measurement(
                    frontier={"cards":baseline},bundle_id="bundle:declared-effect-amounts",
                    probe_id="declared-effect-amount-existing-owner-v1",
                    cards_by_oracle_id={row.oracle_id:row for row in records},
                    coverage={"minimum_complete_card_gain":50,"minimum_exact_ability_gain":100,"minimum_material_residual_reduction":100},
                    cohort_fingerprint="constructed-declared-cohort",database=db)
        self.assertEqual(1,result["complete_card_gain"])
        self.assertEqual(2,result["exact_ability_gain"])
        self.assertFalse(result["grants_gameplay_trust"])
        self.assertEqual("retired_below_harvest_floor",result["decision"])


class DeclaredEffectAmountValueTests(unittest.TestCase):
    def quantity(self):
        return query_characteristic_quantity("cards in your hand", source_name="Generic Source")

    def test_explicit_public_binding_is_frozen_and_independent_expressions_are_not(self):
        quantity = self.quantity()
        bound = PublicQueryAmountSpec(quantity=quantity, schema_version=2, binding_id="node:instruction:x")
        independent = PublicQueryAmountSpec(quantity=quantity)
        item = SimpleNamespace(ref="stack:test", controller="A", context={})
        with mock.patch("quorune.semantic_runtime.query_effect_amounts.query_characteristic_count", side_effect=[4,8,9]) as count:
            self.assertEqual(4,resolve_public_query_amount(None,bound.to_dict(),item))
            self.assertEqual(4,resolve_public_query_amount(None,bound.to_dict(),item))
            self.assertEqual(8,resolve_public_query_amount(None,independent.to_dict(),item))
            self.assertEqual(9,resolve_public_query_amount(None,independent.to_dict(),item))
            self.assertEqual(3,count.call_count)

    def test_declaration_source_scopes_and_cache_signatures_cannot_alias(self):
        value = PublicQueryAmountSpec(quantity=self.quantity(),schema_version=2,binding_id="x").to_dict()
        first = scope_declared_amount_bindings(value,"front:n1")
        second = scope_declared_amount_bindings(value,"front:n2")
        self.assertNotEqual(first["binding_id"],second["binding_id"])
        item = SimpleNamespace(ref="stack:test",controller="A",context={})
        with mock.patch("quorune.semantic_runtime.query_effect_amounts.query_characteristic_count",side_effect=[2,3]):
            self.assertEqual(2,resolve_public_query_amount(None,first,item))
            self.assertEqual(3,resolve_public_query_amount(None,second,item))
        item.context["declared_public_amounts"][first["binding_id"]]["value"] = True
        with self.assertRaises(PublicQueryAmountError):
            resolve_public_query_amount(None,first,item)

    def test_amount_versions_and_cast_values_reject_malformed_inputs(self):
        old = PublicQueryAmountSpec(quantity=self.quantity()).to_dict()
        self.assertNotIn("binding_id",old)
        self.assertEqual(old,PublicQueryAmountSpec.from_dict(old).to_dict())
        for bad in ({**old,"schema_version":2},{**old,"binding_id":"x"},{**old,"coefficient":True}):
            with self.subTest(bad=bad),self.assertRaises(PublicQueryAmountError):
                PublicQueryAmountSpec.from_dict(bad)
        with self.assertRaises(PublicQueryAmountError):
            PublicQueryAmountSpec(quantity=self.quantity(),schema_version=2,binding_id="x",coefficient=2)
        self.assertEqual(2,PublicQueryAmountSpec(quantity=self.quantity(),coefficient=2).coefficient)
        for amount in (0,3,None):
            self.assertEqual(amount or 0,resolve_cast_x_amount(CastXAmountSpec().to_dict(),SimpleNamespace(x_value=amount)))
        for amount in (True,-1,"3",1.5):
            with self.subTest(amount=amount),self.assertRaises(PublicQueryAmountError):
                resolve_cast_x_amount(CastXAmountSpec().to_dict(),SimpleNamespace(x_value=amount))

    def test_instruction_freezing_and_signed_cast_amount_mutants_are_killed(self):
        import quorune.semantic_runtime.query_effect_amounts as owner
        original = inspect.getsource(owner.resolve_public_query_amount)
        guard = "if spec.binding_id is not None:"
        self.assertEqual(1,original.count(guard))
        namespace = dict(vars(owner))
        exec(compile(original.replace(guard,"if False:"),"<omitted-declaration-cache>","exec"),namespace)
        mutant = namespace["resolve_public_query_amount"]
        amount = PublicQueryAmountSpec(quantity=self.quantity(),schema_version=2,binding_id="instruction:x").to_dict()
        item = SimpleNamespace(ref="stack:test",controller="A",context={})
        with mock.patch("quorune.semantic_runtime.query_effect_amounts.query_characteristic_count",side_effect=[4,8]):
            namespace["query_characteristic_count"] = owner.query_characteristic_count
            self.assertEqual(4,mutant(None,amount,item))
            with self.assertRaises(AssertionError):
                self.assertEqual(4,mutant(None,amount,item))
        original = inspect.getsource(owner.resolve_cast_x_amount)
        expression = "return spec.coefficient * amount"
        self.assertEqual(1,original.count(expression))
        namespace = dict(vars(owner))
        exec(compile(original.replace(expression,"return amount"),"<lost-cast-coefficient>","exec"),namespace)
        with self.assertRaises(AssertionError):
            self.assertEqual(-3,namespace["resolve_cast_x_amount"](CastXAmountSpec(coefficient=-1).to_dict(),SimpleNamespace(x_value=3)))

    def test_actual_v233_numeric_result_record_is_explicitly_incompatible_not_reinterpreted(self):
        path = ROOT/"tests/fixtures/records/declared-amounts-v233-ff7191fd"
        provenance = json.loads((path/"provenance.json").read_text(encoding="utf-8"))
        self.assertEqual("oracle-ir-v233",provenance["compiler_version"])
        self.assertEqual("explicit_runtime_trust_incompatibility",provenance["current_runtime_disposition"])
        programs = json.loads((path/"semantics.json").read_text(encoding="utf-8"))["programs"].values()
        result = next(p for p in programs if p["oracle_id"] == "00000000-0000-4000-8000-000000000653")
        self.assertEqual(["draw","lose_life"],[effect["op"] for effect in result["effects"]])
        self.assertEqual(5,len((path/"commands.jsonl").read_text(encoding="utf-8").splitlines()))
        with tempfile.TemporaryDirectory() as directory:
            db_path = Path(directory)/"historical.sqlite3"
            build_fixture_database([helpers.FIXTURE],db_path)
            with CardDatabase(db_path) as database:
                with self.assertRaisesRegex(ValueError,"Runtime trust provenance mismatch in record manifest"):
                    replay_record(path,database,verify=True)


class DeclaredEffectAmountActionTests(unittest.TestCase):
    session = helpers.BoundEffectProgramRuntimeTests.session
    add = helpers.BoundEffectProgramRuntimeTests.add
    ready = helpers.BoundEffectProgramRuntimeTests.ready
    checkpoint = helpers.BoundEffectProgramRuntimeTests.checkpoint
    resolve = helpers.BoundEffectProgramRuntimeTests.resolve
    replay = helpers.BoundEffectProgramRuntimeTests.replay

    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        path = Path(cls.temporary.name)/"declared-amounts.sqlite3"
        build_fixture_database([helpers.FIXTURE,ROOT/"tests/fixtures/declared-amount-cards.json"],path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Generic Declared Amount Deck",[
            DeckEntry("Generic Bound Commander",1,"commander"),
            DeckEntry("Generic Bound Plains",99),
        ],["Generic Bound Commander"])

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temporary.cleanup()

    def test_real_announced_x_offer_payment_rollback_privacy_and_replay(self):
        session = self.session(234001)
        engine = session.engine
        source = self.add(engine,"Generic Double Announced Draw",zone="hand")
        action = self.ready(session,source,{"W":1,"C":6})
        before = authoritative_state_hash(session.state)
        rejected = session.act("pilot:A",{"action_id":action["id"],"x":4,"pay":"auto"})
        self.assertFalse(rejected.ok)
        self.assertEqual(before,authoritative_state_hash(session.state))
        source = engine.state.cards[source.object_id]
        action = self.ready(session,source,{"W":1,"C":6})
        self.checkpoint(session)
        old_hand = set(engine.state.players["A"].zones["hand"])
        accepted = session.act("pilot:A",{"action_id":action["id"],"x":3,"pay":"auto"})
        self.assertTrue(accepted.ok,accepted.summary)
        self.assertEqual(3,engine.state.stack[-1].x_value)
        self.assertEqual(0,engine.state.players["A"].mana_pool.get("C",0))
        self.assertEqual(0,engine.state.players["A"].mana_pool.get("W",0))
        self.assertIsNone(self.resolve(session))
        drawn = set(engine.state.players["A"].zones["hand"])-old_hand
        self.assertEqual(3,len(drawn))
        for object_id in drawn:
            ref = engine.state.cards[object_id].ref
            self.assertIn(ref,json.dumps(session.packet("pilot:A",full=True)))
            for seat in "BCD":
                self.assertNotIn(ref,json.dumps(session.packet("pilot:"+seat,full=True)))
        self.replay(session,load=True)

    def test_cycling_sibling_declares_the_existing_draw_runtime_dependency(self):
        record = replace(generic_spell("Cycling {2}\nThis spell deals X damage to each creature."),keywords=("Cycling",))
        program = compile_best_available_card_program(self.db,record,semantic_registry=SemanticRegistry(),capability_registry=self.registry,capability_profile="commander_review")
        binding = bind_card_program_runtime(program,capability_registry=self.registry,profile="commander_review")
        self.assertTrue(binding["strict_capability_ready"],binding["blockers"])

    def test_real_public_definition_is_frozen_before_draw_and_replays(self):
        session = self.session(234002)
        engine = session.engine
        source = self.add(engine,"Generic Defined Hand Result",zone="hand")
        action = self.ready(session,source,{"W":1})
        initial_hand = len(engine.state.players["A"].zones["hand"])
        initial_life = engine.state.players["A"].life
        expected_x = initial_hand-1
        self.checkpoint(session)
        accepted = session.act("pilot:A",{"action_id":action["id"],"pay":"auto"})
        self.assertTrue(accepted.ok,accepted.summary)
        self.assertIsNone(self.resolve(session))
        self.assertEqual(initial_hand-1+expected_x,len(engine.state.players["A"].zones["hand"]))
        self.assertEqual(initial_life-expected_x,engine.state.players["A"].life)
        self.replay(session,load=True)

    def test_real_zero_cast_amounts_are_paid_no_ops_across_result_owners(self):
        session = self.session(234003)
        engine = session.engine
        target = self.add(engine,"Generic Bound Body",seat="B",ref="zero-body")
        pristine = deepcopy(engine.state)
        for name in ("Generic Announced Draw","Generic Announced Group Damage","Generic Announced Token","Generic Announced Swell"):
            with self.subTest(name=name):
                engine.state = deepcopy(pristine)
                engine.permissions = CapabilityManager(engine.state)
                source = self.add(engine,name,zone="hand")
                action = self.ready(session,source,{"W":1})
                before_hand = len(engine.state.players["A"].zones["hand"])
                before_cards = set(engine.state.cards)
                before_effects = deepcopy(engine.state.continuous_effects)
                self.checkpoint(session)
                accepted = session.act("pilot:A",{"action_id":action["id"],"x":0,"pay":"auto"})
                self.assertTrue(accepted.ok,accepted.summary)
                self.assertEqual(0,engine.state.stack[-1].x_value)
                self.assertIsNone(self.resolve(session))
                self.assertFalse(engine.state.stack)
                self.assertEqual("graveyard",engine.state.cards[source.object_id].zone)
                self.assertEqual(before_hand-1,len(engine.state.players["A"].zones["hand"]))
                self.assertEqual(before_cards,set(engine.state.cards))
                self.assertEqual(0,engine.state.cards[target.object_id].marked_damage)
                if name != "Generic Announced Swell":
                    self.assertEqual(before_effects,engine.state.continuous_effects)
                else:
                    # A zero modifier may still have a normal duration record;
                    # its characteristics, not absence of that record, are the rule.
                    self.assertEqual(2,engine._numeric_stat(target.object_id,"power"))
                    self.assertEqual(6,engine._numeric_stat(target.object_id,"toughness"))
                self.assertEqual(0,engine.state.players["A"].mana_pool.get("W",0))
                self.replay(session,load=True)

    def test_real_declared_amount_survives_draw_choice_checkpoint_without_repeating_effects(self):
        session = self.session(234004)
        engine = session.engine
        source = self.add(engine,"Generic Defined Hand Result",zone="hand")
        dredge = self.add(engine,"Generic Declared Dredge",zone="graveyard")
        action = self.ready(session,source,{"W":1})
        hand = len(engine.state.players["A"].zones["hand"])
        life = engine.state.players["A"].life
        library = len(engine.state.players["A"].zones["library"])
        amount = hand-1
        self.checkpoint(session)
        accepted = session.act("pilot:A",{"action_id":action["id"],"pay":"auto"})
        self.assertTrue(accepted.ok,accepted.summary)
        pending = self.resolve(session)
        self.assertIsNotNone(pending)
        self.assertEqual("draw.replacement",pending.kind)
        item = engine.state.stack[-1]
        values = item.context["declared_public_amounts"]
        self.assertEqual([amount],[value["value"] for value in values.values()])
        self.assertEqual(life,engine.state.players["A"].life)
        for seat in "BCD":
            self.assertIsNone(session.packet("pilot:"+seat,full=True)["decision"])
        resumed = self.replay(session,load=True)
        chosen = resumed.act("pilot:A",{"action_id":"choose","choice":dredge.ref})
        self.assertTrue(chosen.ok,chosen.summary)
        self.assertIsNone(self.resolve(resumed))
        self.assertEqual(hand-1+amount,len(resumed.state.players["A"].zones["hand"]))
        self.assertEqual(life-amount,resumed.state.players["A"].life)
        self.assertEqual(library-amount,len(resumed.state.players["A"].zones["library"]))
        self.assertEqual("hand",resumed.state.cards[dredge.object_id].zone)
        self.assertFalse(resumed.state.stack)
        self.replay(resumed,load=True)

    def test_real_zero_mill_and_scry_consume_price_without_library_mutation(self):
        session = self.session(234005)
        engine = session.engine
        pristine = deepcopy(engine.state)
        for name in ("Generic Announced Mill","Generic Announced Scry"):
            with self.subTest(name=name):
                engine.state = deepcopy(pristine)
                engine.permissions = CapabilityManager(engine.state)
                source = self.add(engine,name,zone="hand")
                action = self.ready(session,source,{"W":1})
                libraries = {seat:list(engine.state.players[seat].zones["library"]) for seat in "ABCD"}
                self.checkpoint(session)
                response = {"action_id":action["id"],"x":0,"pay":"auto"}
                if name == "Generic Announced Mill":
                    response["targets"] = ["B"]
                accepted = session.act("pilot:A",response)
                self.assertTrue(accepted.ok,accepted.summary)
                self.assertIsNone(self.resolve(session))
                self.assertFalse(engine.state.stack)
                for seat in "ABCD":
                    self.assertEqual(libraries[seat],engine.state.players[seat].zones["library"])
                self.assertEqual(0,engine.state.players["A"].mana_pool.get("W",0))
                self.assertEqual("graveyard",engine.state.cards[source.object_id].zone)
                self.replay(session,load=True)

    def test_real_positive_cast_results_use_existing_damage_token_and_characteristic_owners(self):
        session = self.session(234006)
        engine = session.engine
        target = self.add(engine,"Generic Bound Body",seat="B",ref="positive-body")
        pristine = deepcopy(engine.state)
        for name in ("Generic Announced Damage","Generic Announced Group Damage","Generic Announced Token","Generic Announced Swell"):
            with self.subTest(name=name):
                engine.state = deepcopy(pristine)
                engine.permissions = CapabilityManager(engine.state)
                source = self.add(engine,name,zone="hand")
                action = self.ready(session,source,{"W":1,"C":2})
                cards = set(engine.state.cards)
                self.checkpoint(session)
                response = {"action_id":action["id"],"x":2,"pay":"auto"}
                if name == "Generic Announced Damage":
                    self.assertIn(target.ref,action["target_schema"]["legal_refs"])
                    response["targets"] = [target.ref]
                accepted = session.act("pilot:A",response)
                self.assertTrue(accepted.ok,accepted.summary)
                self.assertIsNone(self.resolve(session))
                self.assertEqual(0,engine.state.players["A"].mana_pool.get("W",0))
                self.assertEqual(0,engine.state.players["A"].mana_pool.get("C",0))
                current = engine.state.cards[target.object_id]
                if "Damage" in name:
                    self.assertEqual(2,current.marked_damage)
                elif name == "Generic Announced Token":
                    created = set(engine.state.cards)-cards
                    self.assertEqual(2,len(created))
                    for object_id in created:
                        token = engine.state.cards[object_id]
                        self.assertTrue(token.is_token)
                        self.assertEqual("A",token.controller)
                        self.assertEqual(2,engine._numeric_stat(object_id,"power"))
                        self.assertEqual(2,engine._numeric_stat(object_id,"toughness"))
                else:
                    self.assertEqual(0,engine._numeric_stat(target.object_id,"power"))
                    self.assertEqual(4,engine._numeric_stat(target.object_id,"toughness"))
                self.replay(session,load=True)
                if name == "Generic Announced Swell":
                    expire_end_of_turn_continuous_effects(engine.state)
                    self.assertEqual(2,engine._numeric_stat(target.object_id,"power"))
                    self.assertEqual(6,engine._numeric_stat(target.object_id,"toughness"))

    def test_real_public_activation_uses_resolution_count_locked_controller_and_stale_target_owner(self):
        session = self.session(234007)
        engine = session.engine
        source = self.add(engine,"Generic Defined Shot")
        source.acquired_control_turn_count = -1
        friend = self.add(engine,"Generic Bound Body",ref="defined-friend")
        target = self.add(engine,"Generic Bound Body",seat="B",ref="defined-enemy")
        action = self.ready(session,source,{"C":1})
        legal = action["target_schema"]["legal_refs"]
        self.assertIn(target.ref,legal)
        self.assertNotIn(friend.ref,legal)
        before = authoritative_state_hash(session.state)
        rejected = session.act("pilot:A",{"action_id":action["id"],"targets":[friend.ref],"pay":"auto"})
        self.assertFalse(rejected.ok)
        self.assertEqual(before,authoritative_state_hash(session.state))
        source = engine.state.cards[source.object_id]
        action = self.ready(session,source,{"C":1})
        accepted = session.act("pilot:A",{"action_id":action["id"],"targets":[target.ref],"pay":"auto"})
        self.assertTrue(accepted.ok,accepted.summary)
        self.assertTrue(engine.state.cards[source.object_id].tapped)
        # These changes precede the replay checkpoint: the rules expectation is
        # current A-controlled creatures, not activation-time membership or
        # the source's new controller. The stack ability remains A's.
        engine.change_control(source.object_id,"B")
        self.add(engine,"Generic Bound Body",ref="defined-late-friend")
        self.assertEqual("A",engine.state.stack[-1].controller)
        self.checkpoint(session)
        self.assertIsNone(self.resolve(session))
        self.assertEqual(2,engine.state.cards[target.object_id].marked_damage)
        self.replay(session,load=True)

        engine.change_control(source.object_id,"A")
        source = engine.state.cards[source.object_id]
        source.tapped = False
        source.acquired_control_turn_count = -1
        action = self.ready(session,source,{"C":1})
        accepted = session.act("pilot:A",{"action_id":action["id"],"targets":[target.ref],"pay":"auto"})
        self.assertTrue(accepted.ok,accepted.summary)
        old_identity = engine.state.cards[target.object_id].logical_object_id
        engine.move_card(target.object_id,"exile",log=False)
        engine.move_card(target.object_id,"battlefield",controller="B",log=False)
        self.assertNotEqual(old_identity,engine.state.cards[target.object_id].logical_object_id)
        self.checkpoint(session)
        self.assertIsNone(self.resolve(session))
        self.assertEqual(0,engine.state.cards[target.object_id].marked_damage)
        self.replay(session,load=True)


if __name__ == "__main__":
    unittest.main()
