"""Look/order pins a private permutation; it is not a Scry instruction."""
from dataclasses import replace
from types import SimpleNamespace
import json,unittest
from unittest.mock import patch
from common import ROOT
from quorune.library_order_model import FixedTopLibraryOrderSpec,TOP_ORDER_OPERATION,TOP_ORDER_CAPABILITY,SHUFFLE_CAPABILITY,fixed_shuffle_effect,validate_fixed_shuffle_effect
from quorune.semantic_choices.fixed_top_order import FIXED_TOP_ORDER_HANDLERS
from quorune.semantic_choices.ordering import LibraryOrderingHandler
from quorune.semantic_choices.model import SemanticChoiceError
from quorune.oracle_ir import compile_oracle_card
from quorune.rules.capabilities import load_default_capability_registry
from test_qualified_zone_event_queries import query_record


def ordering_query(refs=('first','second')):
    rows={ref:SimpleNamespace(ref=ref,printed_name='Private '+ref,logical_object_id=ref+'@0') for ref in refs}
    return SimpleNamespace(rows=rows,library_refs=lambda *_a,**_kw:refs,object=lambda ref,**_kw:rows.get(ref))


class FixedTopOrderModelTests(unittest.TestCase):
    def test_shuffle_lowering_has_one_owner_and_rejects_other_seat(self):
        from quorune.semantic_runtime.context import ReadOnlyHandlerContext, SemanticNodeError
        from quorune.semantic_runtime.intents import ShuffleLibraryIntent
        from quorune.semantic_runtime.library_shuffle import FixedLibraryShuffleHandler
        context=ReadOnlyHandlerContext.from_sequences(actor='A',default_reason='Explicit shuffle',seats=('A','B'),active_seats=('A','B'),apnap_order=('A','B'))
        handler=FixedLibraryShuffleHandler()
        plan=handler.lower(fixed_shuffle_effect('A'),context)
        self.assertEqual((ShuffleLibraryIntent(actor='A',player='A',reason='Explicit shuffle'),),plan.intents)
        for effect in (fixed_shuffle_effect('B'),fixed_shuffle_effect('foreign'),{**fixed_shuffle_effect('A'),'count':1}):
            with self.assertRaises(SemanticNodeError):handler.lower(effect,context)

    def test_new_order_and_shuffle_schemas_reject_open_or_malformed_fields(self):
        for count in (1,2,10):self.assertEqual(count,FixedTopLibraryOrderSpec.from_effect(FixedTopLibraryOrderSpec(count).effect()).count)
        for count in (True,0,-1,11,'4'):
            with self.assertRaises(ValueError):FixedTopLibraryOrderSpec(count)
        for change in ({'player':None},{'extra':1},{'count':False}):
            with self.assertRaises(ValueError):FixedTopLibraryOrderSpec.from_effect({**FixedTopLibraryOrderSpec(4).effect(),**change})
        self.assertEqual('$controller',validate_fixed_shuffle_effect(fixed_shuffle_effect()))
        for change in ({'player':True},{'extra':0},{'op':'scry'}):
            with self.assertRaises(ValueError):validate_fixed_shuffle_effect({**fixed_shuffle_effect(),**change})

    def test_exact_private_top_set_and_incarnation_survive_one_complete_order(self):
        handler=FIXED_TOP_ORDER_HANDLERS[0];query=ordering_query();context=SimpleNamespace(actor='A',stack_controller='A',stack_ref='S1',stack_label='Typed order',query=query)
        p=handler.prepare(FixedTopLibraryOrderSpec(4).effect('A'),context);self.assertEqual(('first','second'),tuple(p.continuation_effect['_looked_refs']));continuation=SimpleNamespace(effect=p.continuation_effect)
        for refs in ([],['first'],['first','first'],['first','foreign']):
            with self.assertRaises(SemanticChoiceError):handler.complete(continuation,{'cards':refs},query)
        result=handler.complete(continuation,{'cards':['second','first']},query);self.assertEqual(('second','first'),result.intents[0].refs_top_first)
        query.rows['first'].logical_object_id='first@1'
        with self.assertRaises(SemanticChoiceError):handler.complete(continuation,{'cards':['second','first']},query)

    def test_empty_order_does_not_invent_a_choice_and_foreign_actor_rejects(self):
        handler=FIXED_TOP_ORDER_HANDLERS[0];query=ordering_query(());context=SimpleNamespace(actor='A',stack_controller='A',stack_ref='S1',stack_label='Empty order',query=query)
        result=handler.prepare(FixedTopLibraryOrderSpec(4).effect('A'),context);self.assertIsNone(result.request);self.assertIsNotNone(result.auto_continue)
        with self.assertRaises(SemanticChoiceError):handler.prepare(FixedTopLibraryOrderSpec(4).effect('B'),context)

    def test_omitted_incarnation_guard_mutant_is_killed(self):
        handler=FIXED_TOP_ORDER_HANDLERS[0];query=ordering_query();context=SimpleNamespace(actor='A',stack_controller='A',stack_ref='S1',stack_label='Typed order',query=query);p=handler.prepare(FixedTopLibraryOrderSpec(2).effect('A'),context);continuation=SimpleNamespace(effect=p.continuation_effect);query.rows['first'].logical_object_id='first@1'
        def witness():
            with self.assertRaises(SemanticChoiceError):handler.complete(continuation,{'cards':['first','second']},query)
        witness()
        with patch.object(type(handler),'complete',LibraryOrderingHandler.complete),self.assertRaises(AssertionError):witness()


class FixedTopOrderCompilerTests(unittest.TestCase):
    def test_standalone_spell_and_activated_order_have_complete_executable_admission(self):
        from quorune.card_programs import compile_card_program,bind_card_program_runtime
        from quorune.card_programs.adapters import compile_best_available_card_program
        from quorune.compiler.program_generation import register_generated_programs
        from quorune.semantics import SemanticRegistry
        database=type('NoRulings',(),{'rulings':lambda self,record:()})();registry=load_default_capability_registry()
        for text,kind in (('Look at the top five cards of your library, then put them back in any order.','Sorcery'),('{G}, {T}: Look at the top two cards of your library, then put them back in any order.','Creature — Elf')):
            record=replace(query_record(text),type_line=kind)
            p=compile_card_program(database,record,trust_level='trusted',capability_registry=registry,capability_profile='commander_review');self.assertEqual('capability_closed',p.trust_closure['trust_basis']);self.assertTrue(bind_card_program_runtime(p,capability_registry=registry,profile='commander_review')['strict_capability_ready'])
            semantics=SemanticRegistry()
            register_generated_programs(database,semantics,[record],trust_level='provisional',capability_registry=registry,capability_profile='commander_review',promote_exact_effect_programs=True)
            promoted=semantics.programs_for_oracle(record.oracle_id)
            self.assertTrue(promoted)
            self.assertTrue(all(program.trust_level=='trusted' for program in promoted))
            admitted=compile_best_available_card_program(database,record,semantic_registry=semantics,capability_registry=registry,capability_profile='commander_review')
            self.assertEqual('capability_closed',admitted.trust_closure['trust_basis'])
            self.assertTrue(bind_card_program_runtime(admitted,capability_registry=registry,profile='commander_review')['strict_capability_ready'])

    def test_fixed_controller_order_composes_in_spell_event_optional_and_counter_results(self):
        registry=load_default_capability_registry()
        for text,kind in (('Look at the top five cards of your library, then put them back in any order.','Sorcery'),('When this creature enters, look at the top four cards of your library, then put them back in any order.','Creature — Bird'),('At the beginning of your upkeep, you may look at the top three cards of your library, then put them back in any order.','Enchantment'),('Counter target spell. Look at the top four cards of your library, then put them back in any order.','Instant'),('Look at the top three cards of your library, then put them back in any order. You may shuffle. Draw a card.','Sorcery')):
            ir=compile_oracle_card(replace(query_record(text),type_line=kind),capability_registry=registry);self.assertEqual('exact',ir.status,ir.material_residuals);self.assertIn(TOP_ORDER_CAPABILITY,{c for n in ir.faces[0].nodes for c in n.capability_dependencies})
        for text in ('Look at the top X cards of your library, then put them back in any order.','Look at the top four cards of target opponent\'s library, then put them back in any order.','Look at the top four cards of your library, then put them on the bottom in any order.'):
            self.assertNotEqual('exact',compile_oracle_card(replace(query_record(text),type_line='Sorcery'),capability_registry=registry).status)

    def test_verified_order_or_shuffle_owner_withdrawal_keeps_ordinary_draw(self):
        from copy import deepcopy
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index,discover_tests
        raw=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding='utf-8'));decl=read_evidence_document(ROOT/'platform/capability-evidence-declarations.json');known=set(discover_tests(ROOT).values())
        for cap,text in ((TOP_ORDER_CAPABILITY,'Look at the top four cards of your library, then put them back in any order.'),(SHUFFLE_CAPABILITY,'You may shuffle.')):
            changed=deepcopy(raw);next(r for r in changed['capabilities'] if r['id']==cap).update(status='blocked',blockers=['library owner withdrawn']);registry=CapabilityRegistry(changed);index=build_index(registry_value=changed,declaration_source=decl,discovered_test_ids=known);registry.mark_evidence_verified(validate_capability_evidence_index(index,registry=registry))
            self.assertNotEqual('exact',compile_oracle_card(replace(query_record(text),type_line='Sorcery'),capability_registry=registry).status);self.assertEqual('exact',compile_oracle_card(replace(query_record('Draw a card.'),type_line='Sorcery'),capability_registry=registry).status)

    def test_omitted_top_order_producer_mutant_is_killed(self):
        for text in ('Look at the top four cards of your library, then put them back in any order.','Shuffle your library.'):
            def witness():self.assertEqual('exact',compile_oracle_card(replace(query_record(text),type_line='Sorcery'),capability_registry=load_default_capability_registry()).status)
            witness()
            with patch('quorune.compiler.fixed_top_order_templates.fixed_top_order_template',return_value=None),self.assertRaises(AssertionError):witness()
