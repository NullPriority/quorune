"""Resolution land choices retain non-target semantics and exact minima."""
from copy import deepcopy
from dataclasses import replace
import json
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from common import ROOT
from quorune.untargeted_untap_model import FixedLandUntapChoiceSpec, UNTARGETED_UNTAP_CAPABILITY
from quorune.semantic_choices.untargeted_untap import FixedLandUntapChoiceHandler
from quorune.semantic_choices.model import SemanticChoiceError
from quorune.semantic_runtime.intents import SetPermanentsTappedIntent
from quorune.object_query import ObjectQueryResult
from quorune.oracle_ir import compile_oracle_card
from quorune.rules.capabilities import load_default_capability_registry
from test_qualified_zone_event_queries import query_record


def land_query():
    rows={ref:ObjectQueryResult(object_id=ref,logical_object_id=ref+'@0',ref=ref,printed_name=ref,owner=seat,controller=seat,zone=zone,types=types,phased_out=phased,tapped=tapped) for ref,seat,zone,types,phased,tapped in (
        ('own','A','battlefield',('land',),False,True),('other','B','battlefield',('land',),False,False),
        ('phase','A','battlefield',('land',),True,True),('creature','A','battlefield',('creature',),False,True),('hand','A','hand',('land',),False,False))}
    return SimpleNamespace(rows=rows,objects=lambda **kwargs:tuple(rows.values()),object=lambda ref,**kwargs:rows.get(ref))


def prepared_choice(spec=None):
    query=land_query();handler=FixedLandUntapChoiceHandler();context=SimpleNamespace(actor='A',stack_controller='A',stack_ref='S1',stack_label='Land untap',query=query)
    prepared=handler.prepare((spec or FixedLandUntapChoiceSpec(2)).effect('A'),context)
    continuation=SimpleNamespace(effect=prepared.continuation_effect,stack_ref='S1')
    return query,handler,context,prepared,continuation


class UntargetedUntapModelTests(unittest.TestCase):
    def test_closed_schema_and_current_controller_scope(self):
        for maximum in (1,7,10):self.assertEqual(maximum,FixedLandUntapChoiceSpec.from_effect(FixedLandUntapChoiceSpec(maximum).effect()).maximum)
        for maximum in (True,'2',0,-1,11):
            with self.assertRaises(ValueError):FixedLandUntapChoiceSpec(maximum)
        for change in ({'player':False},{'extra':1},{'controller_scope':'opponent'}):
            with self.assertRaises(ValueError):FixedLandUntapChoiceSpec.from_effect({**FixedLandUntapChoiceSpec(3).effect(),**change})
        _,handler,context,p,_=prepared_choice(FixedLandUntapChoiceSpec(3,'controller'));self.assertEqual(('own',),p.request.choice.legal_refs)
        with self.assertRaises(SemanticChoiceError):handler.prepare(FixedLandUntapChoiceSpec(3).effect('B'),context)

    def test_zero_subset_and_distinct_current_incarnations(self):
        query,handler,_,p,c=prepared_choice();self.assertEqual(('other','own'),p.request.choice.legal_refs);self.assertEqual(0,p.request.choice.minimum)
        self.assertFalse(any(isinstance(i,SetPermanentsTappedIntent) for i in handler.complete(c,{'objects':[]},query).intents))
        result=handler.complete(c,{'objects':['other','own']},query);self.assertEqual(('other','own'),result.intents[0].object_refs);self.assertFalse(result.intents[0].tapped)
        for response in ({},{'objects':'own'},{'objects':[True]},{'objects':['own','own']},{'objects':['foreign']},{'objects':['own','other','phase']},{'objects':['creature']},{'objects':['phase']},{'objects':['own'],'extra':1}):
            with self.assertRaises(SemanticChoiceError):handler.complete(c,response,query)
        query.rows['own']=replace(query.rows['own'],logical_object_id='own@1')
        with self.assertRaises(SemanticChoiceError):handler.complete(c,{'objects':['own']},query)

    def test_empty_choice_and_changed_land_or_controller_fail_closed(self):
        query,handler,context,_,c=prepared_choice(FixedLandUntapChoiceSpec(2,'controller'))
        for change in ({'controller':'B'},{'types':('creature',)},{'phased_out':True},{'zone':'hand'}):
            original=query.rows['own'];query.rows['own']=replace(original,**change)
            with self.assertRaises(SemanticChoiceError):handler.complete(c,{'objects':['own']},query)
            query.rows['own']=original
        query.rows.clear();prepared=handler.prepare(FixedLandUntapChoiceSpec(2).effect('A'),context);self.assertIsNone(prepared.request);self.assertIsNotNone(prepared.auto_continue)

    def test_omitted_land_choice_incarnation_guard_mutant_is_killed(self):
        query,handler,_,_,c=prepared_choice();query.rows['own']=replace(query.rows['own'],logical_object_id='own@1')
        def witness():
            with self.assertRaises(SemanticChoiceError):handler.complete(c,{'objects':['own']},query)
        witness()
        with patch('quorune.semantic_choices.untargeted_untap._current_selected_land',return_value=True),self.assertRaises(AssertionError):witness()

    def test_untargeted_land_choice_candidates_have_canonical_replay_order(self):
        def witness():
            query,handler,context,first,_=prepared_choice();items=tuple(query.rows.items());query.rows.clear();query.rows.update(reversed(items));second=handler.prepare(FixedLandUntapChoiceSpec(2).effect('A'),context)
            self.assertEqual(first.request.choice,second.request.choice);self.assertEqual(first.request.public_context['objects'],second.request.public_context['objects'])
        witness()
        with patch('quorune.semantic_choices.untargeted_untap.sorted',side_effect=lambda rows,**kw:list(rows),create=True),self.assertRaises(AssertionError):witness()


class UntargetedUntapCompilerTests(unittest.TestCase):
    def test_optional_draw_discard_retains_new_leaf_owner_and_legacy_shape(self):
        from quorune.compiler.affected_player_discard_templates import FIXED_CONTROLLER_DISCARD_CAPABILITY,FIXED_CONTROLLER_DISCARD_MECHANIC
        from quorune.rules.fixed_effect_clause_shapes import fixed_optional_effect_node_capabilities
        registry=load_default_capability_registry()
        for text in ('Whenever this creature enters or attacks, you may draw a card. If you do, discard a card.','Whenever this creature attacks or blocks, you may draw a card. If you do, discard a card.','At the beginning of your end step, if you attacked this turn, you may draw a card. If you do, discard a card.','When this creature enters, if you attacked this turn, you may draw a card. If you do, discard a card.','Whenever this artifact deals combat damage to a player, you may draw a card. If you do, discard a card.'):
            ir=compile_oracle_card(replace(query_record(text),type_line='Artifact Creature — Wizard'),capability_registry=registry);self.assertEqual('exact',ir.status,ir.material_residuals);node=ir.faces[0].nodes[0];self.assertIn(FIXED_CONTROLLER_DISCARD_CAPABILITY,node.capability_dependencies)
            legacy=fixed_optional_effect_node_capabilities(effects=node.effects,target_schema=None,mechanic_ids=tuple(m for m in node.mechanics if m!=FIXED_CONTROLLER_DISCARD_MECHANIC));self.assertTrue(legacy);self.assertNotIn(FIXED_CONTROLLER_DISCARD_CAPABILITY,legacy)

    def test_omitted_optional_controller_discard_owner_mutant_is_killed(self):
        def witness():self.assertEqual('exact',compile_oracle_card(replace(query_record('Whenever this creature attacks, you may draw a card. If you do, discard a card.'),type_line='Creature — Wizard'),capability_registry=load_default_capability_registry()).status)
        witness()
        with patch('quorune.rules.fixed_effect_clause_shapes.fixed_controller_discard_node_capabilities',return_value=()),self.assertRaises(AssertionError):witness()

    def test_plain_comma_untap_sequence_requires_complete_typed_siblings(self):
        registry=load_default_capability_registry()
        for text in ('Draw three cards, untap up to two lands, then discard a card.','Draw three cards, untap up to two lands, and discard a card.'):
            ir=compile_oracle_card(replace(query_record(text),type_line='Sorcery'),capability_registry=registry);self.assertEqual('exact',ir.status,ir.material_residuals);self.assertEqual(('draw','choose_untap_lands','choose_cards_apnap'),tuple(e['op'] for e in ir.faces[0].nodes[0].effects))
        for text in ('Draw three cards, untap up to two nonbasic lands, then discard a card.','Draw three cards, untap up to two lands, then secretly win the game.','Draw three cards, untap up to two lands if you control a creature, then discard a card.','Draw three cards, say "untap up to two lands, then discard a card."'):
            self.assertNotEqual('exact',compile_oracle_card(replace(query_record(text),type_line='Sorcery'),capability_registry=registry).status)

    def test_omitted_plain_comma_untap_partition_mutant_is_killed(self):
        def witness():self.assertEqual('exact',compile_oracle_card(replace(query_record('Draw three cards, untap up to two lands, then discard a card.'),type_line='Sorcery'),capability_registry=load_default_capability_registry()).status)
        witness()
        with patch('quorune.compiler.closed_effect_programs._comma_sequence_partition',return_value=None),self.assertRaises(AssertionError):witness()

    def test_controller_discard_carrier_survives_flattening_without_widening_legacy_leaf(self):
        from quorune.compiler.affected_player_discard_templates import FIXED_CONTROLLER_DISCARD_CAPABILITY,FIXED_CONTROLLER_DISCARD_MECHANIC
        from quorune.rules.affected_player_discard_capability_shapes import fixed_affected_player_discard_node_capabilities
        ir=compile_oracle_card(replace(query_record('Draw two cards, then discard two cards. Untap up to three lands.'),type_line='Instant'),capability_registry=load_default_capability_registry());self.assertEqual('exact',ir.status,ir.material_residuals);node=ir.faces[0].nodes[0];self.assertIn(FIXED_CONTROLLER_DISCARD_MECHANIC,node.mechanics);self.assertIn(FIXED_CONTROLLER_DISCARD_CAPABILITY,node.capability_dependencies)
        discard=next(e for e in node.effects if e['op']=='choose_cards_apnap');self.assertEqual((),fixed_affected_player_discard_node_capabilities(effects=(discard,),target_schema=None,mechanic_ids=('fixed-affected-player-discard','cr-402-hand')))

    def test_controller_discard_owner_withdrawal_closes_whole_ordered_program(self):
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index,discover_tests
        raw=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text());next(r for r in raw['capabilities'] if r['id']=='choice.affected_player.fixed_controller_discard').update(status='blocked',blockers=['controller discard withdrawn']);registry=CapabilityRegistry(raw);index=build_index(registry_value=raw,declaration_source=read_evidence_document(ROOT/'platform/capability-evidence-declarations.json'),discovered_test_ids=set(discover_tests(ROOT).values()));registry.mark_evidence_verified(validate_capability_evidence_index(index,registry=registry))
        for text,exact in (('Draw two cards, then discard two cards. Untap up to three lands.',False),('Whenever this creature attacks, you may draw a card. If you do, discard a card.',False),('Untap up to three lands.',True),('Draw a card.',True)):
            self.assertEqual(exact,compile_oracle_card(replace(query_record(text),type_line='Instant'),capability_registry=registry).status=='exact')

    def test_omitted_controller_discard_carrier_mutant_is_killed(self):
        from quorune.compiler.affected_player_discard_templates import standalone_controller_discard_effect_template,FIXED_CONTROLLER_DISCARD_MECHANIC
        def omitted(text):
            result=standalone_controller_discard_effect_template(text)
            return (*result[:3],tuple(m for m in result[3] if m!=FIXED_CONTROLLER_DISCARD_MECHANIC)) if result else None
        def witness():self.assertEqual('exact',compile_oracle_card(replace(query_record('Draw two cards, then discard two cards. Untap up to three lands.'),type_line='Instant'),capability_registry=load_default_capability_registry()).status)
        witness()
        with patch('quorune.compiler.fixed_controller_effect_sequences.standalone_controller_discard_effect_template',side_effect=omitted),self.assertRaises(AssertionError):witness()

    def test_fixed_spell_entry_activated_and_ordered_bodies_preserve_non_targets(self):
        registry=load_default_capability_registry()
        for text,kind in (('Untap up to three lands.','Instant'),('When this creature enters, untap up to five lands.','Creature — Drake'),('{2}: Untap up to two lands you control.','Artifact'),('Counter target spell. Untap up to four lands.','Instant'),("Return target creature to its owner's hand. Untap up to two lands.",'Instant'),('Draw two cards, then discard two cards. Untap up to three lands.','Instant'),('Draw three cards, untap up to two lands, then discard a card.','Sorcery')):
            ir=compile_oracle_card(replace(query_record(text),type_line=kind),capability_registry=registry);self.assertEqual('exact',ir.status,ir.material_residuals);self.assertIn(UNTARGETED_UNTAP_CAPABILITY,{c for n in ir.faces[0].nodes for c in n.capability_dependencies})
        for text in ('Untap up to X lands.','Untap up to three nonbasic lands.','Untap up to three creatures.','Untap up to three lands. Secretly win the game.'):
            self.assertNotEqual('exact',compile_oracle_card(replace(query_record(text),type_line='Instant'),capability_registry=registry).status)
        ir=compile_oracle_card(replace(query_record('Untap up to two target lands.'),type_line='Instant'),capability_registry=registry)
        self.assertNotIn(UNTARGETED_UNTAP_CAPABILITY,{c for n in ir.faces[0].nodes for c in n.capability_dependencies})

    def test_normal_provisional_registration_promotes_exact_leaf(self):
        from quorune.compiler.program_generation import register_generated_programs
        from quorune.card_programs.adapters import compile_best_available_card_program
        from quorune.card_programs import bind_card_program_runtime
        from quorune.semantics import SemanticRegistry
        database=type('NoRulings',(),{'rulings':lambda self,record:()})();registry=load_default_capability_registry()
        for text,kind in (('Untap up to three lands.','Instant'),('{2}: Untap up to two lands you control.','Artifact')):
            record=replace(query_record(text),type_line=kind);semantics=SemanticRegistry();register_generated_programs(database,semantics,[record],trust_level='provisional',capability_registry=registry,capability_profile='commander_review',promote_exact_effect_programs=True)
            self.assertTrue(semantics.programs_for_oracle(record.oracle_id));self.assertTrue(all(p.trust_level=='trusted' for p in semantics.programs_for_oracle(record.oracle_id)))
            p=compile_best_available_card_program(database,record,semantic_registry=semantics,capability_registry=registry,capability_profile='commander_review');self.assertEqual('capability_closed',p.trust_closure['trust_basis']);self.assertTrue(bind_card_program_runtime(p,capability_registry=registry,profile='commander_review')['strict_capability_ready'])

    def test_verified_owner_withdrawal_keeps_ordinary_target_untap(self):
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index,discover_tests
        original=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text());declarations=read_evidence_document(ROOT/'platform/capability-evidence-declarations.json');known=set(discover_tests(ROOT).values())
        for withdrawn in (UNTARGETED_UNTAP_CAPABILITY,'permanent.untap.effect'):
            raw=deepcopy(original);next(r for r in raw['capabilities'] if r['id']==withdrawn).update(status='blocked',blockers=['untap owner withdrawn']);registry=CapabilityRegistry(raw)
            index=build_index(registry_value=raw,declaration_source=declarations,discovered_test_ids=known);registry.mark_evidence_verified(validate_capability_evidence_index(index,registry=registry))
            for text,exact in (('Untap up to three lands.',False),('Untap target creature.',withdrawn==UNTARGETED_UNTAP_CAPABILITY),('Draw a card.',True)):
                ir=compile_oracle_card(replace(query_record(text),type_line='Instant'),capability_registry=registry);self.assertEqual(exact,ir.status=='exact')

    def test_omitted_land_choice_producer_mutant_is_killed(self):
        def witness():self.assertEqual('exact',compile_oracle_card(replace(query_record('Untap up to three lands.'),type_line='Instant'),capability_registry=load_default_capability_registry()).status)
        witness()
        with patch('quorune.compiler.untargeted_untap_templates.fixed_land_untap_choice_template',return_value=None),self.assertRaises(AssertionError):witness()
