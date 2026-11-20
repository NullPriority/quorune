"""Pinned CR601.2b/702.117/707.10: paid alternative is a distinct sealed fact."""
from dataclasses import FrozenInstanceError,replace
from unittest.mock import patch
import json,unittest
from quorune.paid_alternative_cast_model import PaidAlternativeCastFact as Fact,PaidAlternativeCastRequirement as Request


class PaidAlternativeCastModelTests(unittest.TestCase):
    def test_distinct_explicit_true_and_false_facts_round_trip_immutably(self):
        request=Request('surge');self.assertEqual(request,Request.from_dict(json.loads(json.dumps(request.to_dict()))))
        for paid in (False,True):
            fact=Fact('surge',paid);self.assertEqual(paid,fact.satisfies(request))
            self.assertEqual(fact,Fact.from_dict(json.loads(json.dumps(fact.to_dict()))))
            with self.assertRaises(FrozenInstanceError):fact.paid=not paid
            raw=fact.to_dict();raw['paid']=not paid;self.assertEqual(paid,fact.paid)

    def test_boolean_versions_unknown_methods_and_unmarked_kicker_payloads_fail_closed(self):
        raw=Fact('surge',True).to_dict()
        for change in ({'schema_version':True},{'schema_version':2},{'paid':1},{'paid':'true'},
                {'method':'prowl'},{'method':None},{'kind':'kicked'},{'unknown':True}):
            with self.assertRaises(ValueError):Fact.from_dict({**raw,**change})
        for value in (None,{},True,{'kicked':True},{'cost_option':'surge-alternative-ability'}):
            with self.assertRaises(ValueError):Fact.from_dict(value)
        for change in ({'schema_version':True},{'schema_version':2},{'method':'spectacle'},{'paid':True}):
            with self.assertRaises(ValueError):Request.from_dict({**Request('surge').to_dict(),**change})
        with self.assertRaises(ValueError):Fact('surge',True).satisfies({'method':'surge'})

    def test_entry_receipt_binds_current_source_to_original_resolving_stack_and_rejects_noncast_paid_facts(self):
        from quorune.continuous_effect_model import ContinuousObjectIdentity
        from quorune.paid_alternative_cast_model import PaidAlternativeEntryInformation as Entry
        from quorune.rules.paid_alternative_cast_facts import validated_paid_entry_occurrence
        source=ContinuousObjectIdentity('paid-object','paid-object@3')
        info=Entry(source,Fact('surge',True),'paid-object@2')
        self.assertEqual(info,Entry.from_dict(json.loads(json.dumps(info.to_dict()))))
        occurrence=Row(paid_alternative_entry_information=info.to_dict(),destination='battlefield',origin='stack',
            object_id='paid-object',current_logical_object_id='paid-object@3',previous_logical_object_id='paid-object@2')
        self.assertEqual(info.to_dict(),dict(validated_paid_entry_occurrence(occurrence)))
        for field,value in (('origin','exile'),('current_logical_object_id','paid-object@4'),('previous_logical_object_id','paid-object@1')):
            with self.assertRaises(ValueError):validated_paid_entry_occurrence(Row(**{**vars(occurrence),field:value}))
        with self.assertRaises(ValueError):Entry(source,Fact('surge',True))
        with self.assertRaises(ValueError):Entry(source,Fact('surge',True),'foreign-object@2')
        ordinary=Entry(source,Fact('surge',False));self.assertIsNone(ordinary.stack_logical_object_id)

    def test_paid_fact_comes_from_typed_authorized_option_and_never_option_name_or_kicker_boolean(self):
        from quorune.compiler.fixed_public_alternative_costs import compile_fixed_public_alternative_cost
        from quorune.rules.paid_alternative_cast_facts import paid_alternative_fact_from_option
        text='Surge {3}{U}{U}'
        spec=compile_fixed_public_alternative_cost(material_line=text,oracle_line=text,line_index=0)
        self.assertIsNotNone(spec)
        self.assertTrue(paid_alternative_fact_from_option(Request('surge'),spec.cast_cost_option()).paid)
        for option in ({'id':'normal'},{'id':'surge-alternative-forged'},{'id':'kicked','kicked':True}):
            self.assertFalse(paid_alternative_fact_from_option(Request('surge'),option).paid)
        with self.assertRaises(ValueError):paid_alternative_fact_from_option(Request('surge'),{'fixed_public_alternative_cost':{'kind':'surge'}})

    def test_legacy_and_incarnation_aware_query_references_keep_distinct_replay_meanings(self):
        from quorune.compiler.fixed_resolution_characteristic_queries import fixed_resolution_characteristic_query_is_closed
        from quorune.object_predicate import ObjectQuerySpec
        from quorune.semantic_runtime.values import resolve_semantic_value
        for reference in ('$source','$source.zone_object'):
            query=ObjectQuerySpec(zones=('battlefield',),controller='$controller',types_all=('creature',),exclude_ref=reference)
            self.assertTrue(fixed_resolution_characteristic_query_is_closed(query,target_schema=None))
        self.assertFalse(fixed_resolution_characteristic_query_is_closed(
            replace(query,exclude_ref='$source.controller'),target_schema=None))
        card=Row(zone='battlefield',logical_object_id='source@3',ref='source-ref',phased_out=False)
        host=Row(state=Row(cards={'source':card}),_stack_source_ref=lambda item:'source-ref')
        item=Row(source_object_id='source',context={'source_logical_object_id':'source@2'})
        self.assertEqual('source-ref',resolve_semantic_value(host,'$source',item))
        self.assertIsNone(resolve_semantic_value(host,'$source.zone_object',item))
        card.logical_object_id='source@2'
        self.assertEqual('source-ref',resolve_semantic_value(host,'$source.zone_object',item))


from types import SimpleNamespace as Row
from quorune.oracle_ir import _effect_template
from quorune.compiler.paid_alternative_cast_results import paid_alternative_spell_result_template,paid_alternative_entry_trigger_node
from quorune.rules.paid_alternative_cast_facts import (
    validate_paid_alternative_instruction,paid_alternative_result_capabilities,paid_alternative_program_dependencies,
)
from quorune.paid_alternative_cast_model import PAID_ALTERNATIVE_CAST_CAPABILITY as CAP,PAID_ALTERNATIVE_CAST_MECHANIC as MECHANIC
from quorune.semantic_choices.paid_alternative_cast import prepare_paid_alternative_result
from quorune.semantic_choices.context import SnapshotSemanticChoiceQuery
from quorune.semantic_choices.model import SemanticChoiceError
from quorune.rules.capabilities import load_default_capability_registry
from quorune.compiler.ir_model import SourceSpan
from quorune.paid_alternative_cast_model import PAID_ALTERNATIVE_ENTRY_CONDITION

COMPLETE={'requires_complete_card_program':True,'card_program_admission':
    {'schema_version':1,'oracle_ir_status':'exact','material_residual_count':0}}

PREFIX="Return all nonland permanents to their owners' hands. "
BONUS="If this spell's surge cost was paid, create an 8/8 blue Octopus creature token."


def paid_spell(text):
    return paid_alternative_spell_result_template(text,compile_component=lambda body:_effect_template(body,card_name='Generic Paid Result'))


class PaidAlternativeCompilerChoiceTests(unittest.TestCase):
    def test_complete_spell_keeps_owned_return_prefix_fixed_token_and_raw_minimum(self):
        compiled=paid_spell(PREFIX+BONUS);self.assertIsNotNone(compiled)
        effects=compiled[1];self.assertEqual(['move_public_zone_set','apply_if_public_condition'],[e['op'] for e in effects])
        self.assertEqual(18,effects[-1]['schema_version'])
        req,children=validate_paid_alternative_instruction(effects[-1]);self.assertEqual('surge',req.method)
        self.assertEqual(('8','8'),(children[0]['characteristics']['power'],children[0]['characteristics']['toughness']))
        required=paid_alternative_result_capabilities(effects=effects,target_schema=None,mechanic_ids=compiled[3])
        self.assertIn(CAP,required);self.assertGreater(len(required),2)
        program=Row(effects=effects,target_schema=None,event='resolve',active_zone='stack',ability_id='spell:front',coverage=(),provenance=COMPLETE)
        self.assertEqual(required,paid_alternative_program_dependencies(program))
        program.active_zone='hand'
        with self.assertRaises(ValueError):paid_alternative_program_dependencies(program)

    def test_unknown_methods_riders_derived_counts_and_legacy_kicker_are_not_reinterpreted(self):
        for text in (BONUS.replace('surge','prowl'),BONUS.replace("this spell's surge cost was paid",'this spell was kicked'),
                BONUS+' Rewrite history.',BONUS.replace('an 8/8','X 8/8'),BONUS+' Otherwise, draw a card.'):
            self.assertIsNone(paid_spell(text),text)
        wrapper=paid_spell(BONUS)[1][-1]
        for change in ({'schema_version':True},{'schema_version':2},{'cast_fact':True},{'otherwise_effects':[]},
                {'condition':{'kind':'kicked','schema_version':1}}):
            with self.assertRaises(ValueError):validate_paid_alternative_instruction({**wrapper,**change})

    def test_entry_bodies_keep_other_creature_set_and_original_damage_target_without_forcing_trust(self):
        registry=load_default_capability_registry()
        cases=(('Reckless Bushwhacker','other creatures you control get +1/+0 and gain haste until end of turn.'),
            ('Tyrant of Valakut','it deals 3 damage to any target.'))
        for name,body in cases:
            with self.subTest(name=name):
                text='When this creature enters, if its surge cost was paid, '+body
                residuals=[];node=paid_alternative_entry_trigger_node(node_id='n1',line=text,material_line=text,
                    span=SourceSpan(0,len(text),1),card_name=name,effect_template=_effect_template,
                    trusted_mechanics=frozenset(),capability_registry=registry,capability_profile='commander_review',residuals=residuals)
                self.assertIsNotNone(node);self.assertTrue(node.lowerable)
                self.assertEqual(registry.closure((CAP,),profile='commander_review').trusted,node.exact)
                self.assertIn(CAP,node.capability_dependencies)
                self.assertEqual({'field':PAID_ALTERNATIVE_ENTRY_CONDITION,'op':'eq','value':'surge'},node.event_condition)
                if name.startswith('Reckless'):
                    effect=node.effects[0];self.assertEqual('modify_all_matching_permanents_until_end_of_turn',effect['op'])
                    self.assertEqual('$source.zone_object',effect['predicate']['exclude_ref']);self.assertEqual(['haste'],[k.casefold() for k in effect['keywords']])
                else:
                    self.assertEqual(3,node.effects[0]['amount']);self.assertEqual('$source',node.effects[0]['source'])
                    self.assertEqual(['player','battlefield'],node.target_schema['zones'])
                program=Row(effects=node.effects,event=node.event,active_zone=node.active_zone,event_condition=node.event_condition,coverage=(),provenance=COMPLETE)
                self.assertEqual((CAP,),paid_alternative_program_dependencies(program))
                program.provenance={}
                with self.assertRaises(ValueError):paid_alternative_program_dependencies(program)

    def test_condition_selects_only_its_typed_payment_and_correct_controller(self):
        wrapper=paid_spell(BONUS)[1][-1]
        query=SnapshotSemanticChoiceQuery(('A','B'),('B','A'))
        context=Row(actor='A',stack_controller='A',query=query)
        for paid in (True,False):
            bound={**wrapper,'player':'A','cast_fact':Fact('surge',paid).to_dict(),
                'effects':[{**wrapper['effects'][0],'controller':'A'}]}
            prepared=prepare_paid_alternative_result(bound,context)
            self.assertIsNone(prepared.request);self.assertEqual(int(paid),len(prepared.auto_continue.prepend_effects))
            with self.assertRaises(SemanticChoiceError):prepare_paid_alternative_result({**bound,'cast_fact':True},context)
            with self.assertRaises(SemanticChoiceError):prepare_paid_alternative_result(bound,Row(actor='B',stack_controller='A',query=query))

    def test_fresh_paid_bit_mutant_is_killed_after_true_and_false_choice_witness(self):
        original=Fact.satisfies;observed=[]
        def inverted(fact,request):
            observed.append(fact.paid);return not original(fact,request)
        with patch.object(Fact,'satisfies',inverted):
            with self.assertRaises(AssertionError):
                self.test_condition_selects_only_its_typed_payment_and_correct_controller()
        self.assertEqual([True],observed)

    def test_verified_paid_fact_and_cost_authority_withdrawal_keeps_unrelated_draw(self):
        from copy import deepcopy
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index,discover_tests
        from test_qualified_zone_event_queries import query_record
        raw=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        declarations=read_evidence_document(ROOT/'platform/capability-evidence-declarations.json')
        known=set(discover_tests(ROOT).values())
        control=replace(query_record('Draw a card.'),type_line='Sorcery',power=None,toughness=None)
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'withdrawal.sqlite3';build_fixture_database([FIXTURE],path)
            with CardDatabase(path) as db:
                for missing in (CAP,'casting.alternative_cost.fixed_public'):
                    changed=deepcopy(raw);next(c for c in changed['capabilities'] if c['id']==missing).update(status='blocked',blockers=['Paid-source authority withdrawn'])
                    registry=CapabilityRegistry(changed)
                    index=build_index(registry_value=changed,declaration_source=declarations,discovered_test_ids=known)
                    registry.mark_evidence_verified(validate_capability_evidence_index(index,registry=registry))
                    for name in ORIGINALS:
                        with self.subTest(missing=missing,name=name):
                            self.assertNotEqual('exact',compile_oracle_card(db.lookup(name),capability_registry=registry,capability_profile='commander_review').status)
                    self.assertEqual('exact',compile_oracle_card(control,capability_registry=registry,capability_profile='commander_review').status)


from pathlib import Path
import tempfile
from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition,DeckEntry
from quorune.semantics import SemanticRegistry
from quorune.oracle_ir import compile_oracle_card,register_generated_programs
from quorune.card_programs.adapters import compile_best_available_card_program
from quorune.card_programs.binding import bind_card_program_runtime,bind_semantic_program_runtime
from scripts.build_test_database import build_fixture_database
import test_target_recipient_results as recipients

FIXTURE=ROOT/'tests/fixtures/paid-alternative-cast-result-cards.json'
ORIGINALS=('Crush of Tentacles','Reckless Bushwhacker','Tyrant of Valakut')


class PaidAlternativeNativeTests(InheritedNativeFixture,unittest.TestCase):
    reject=recipients.TargetRecipientNativeTests.reject
    finish=recipients.TargetRecipientNativeTests.finish

    @classmethod
    def setUpClass(cls):
        cls.temporary=tempfile.TemporaryDirectory();folder=Path(cls.temporary.name)
        basic=json.loads((ROOT/'tests/fixtures/bound-effect-program-cards.json').read_text(encoding='utf-8'))
        basic['cards']=[r for r in basic['cards'] if r['name']=='Generic Bound Plains'];basic['rulings']=[]
        path=folder/'basic.json';path.write_text(json.dumps(basic),encoding='utf-8')
        build_fixture_database([path,FIXTURE],folder/'paid-alternative.sqlite3')
        cls.db=CardDatabase(folder/'paid-alternative.sqlite3');cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Paid alternative facts',[DeckEntry('Sliver Hivelord',1,'commander'),
            DeckEntry('Generic Bound Plains',30)],['Sliver Hivelord'])

    def trim(self,s):
        while len(s.state.players['A'].zones['hand'])>3:
            s.engine.move_card(s.state.players['A'].zones['hand'][-1],'library',log=False)

    def cast_price(self,s,card,*,surge,targets=(),seat='A',reserve=None):
        self.priority(s,seat);current=s.state.cards[card.object_id];action=self.action(s,seat,current)
        options={o['id'] for o in action['cost_options']}
        selected=next(o for o in options if o.startswith('surge-alternative-')) if surge else 'normal'
        self.assertIn(selected,options)
        command={'action_id':action['id'],'cost_option':selected,'targets':list(targets),'pay':'auto','reserve':reserve or {}}
        self.reject(s,'D' if seat!='D' else 'B',command)
        accepted=s.act('pilot:'+seat,command);self.assertTrue(accepted.ok,accepted.summary)
        fact=Fact.from_dict(s.state.stack[-1].context['paid_alternative_cast'])
        self.assertEqual(surge,fact.paid);self.assertEqual('surge',fact.method)

    def prime(self,s,primer):
        self.cast(s,'A',primer)
        self.assertNotIn('paid_alternative_cast',s.state.stack[-1].context)
        self.assertIsNone(self.resolve(s))

    def tokens(self,s):
        return [c for c in s.state.cards.values() if c.zone=='battlefield' and not c.is_card_object]

    def test_all_originals_close_normally_with_material_siblings_raw_owner_and_partial_neighbor(self):
        s=self.session(716000)
        for name in (*ORIGINALS,'Goblin Freerunner','Into the Roil','Goblin Ruinblaster'):
            with self.subTest(name=name):
                card=self.add(s.engine,name,zone='hand');self.assert_closed(s,card)
                self.assertEqual('exact',compile_oracle_card(self.db.lookup(name),capability_registry=self.registry,capability_profile='commander_review').status)
                for program in s.engine.semantics.programs_for_oracle(card.oracle_id):
                    if CAP not in program.capability_dependencies:continue
                    deps=tuple(c for c in program.capability_dependencies if c!=CAP)
                    changed=replace(program,coverage=(),capability_dependencies=deps,
                        capability_closure=self.registry.closure(deps,profile='commander_review').to_dict() if deps else None)
                    binding=bind_semantic_program_runtime(changed,capability_registry=self.registry,profile='commander_review')
                    self.assertFalse(binding['strict']);self.assertIn('capability:undeclared_runtime_dependency:'+CAP,binding['blockers'])
                    changed=replace(program,provenance={k:v for k,v in program.provenance.items()
                        if k not in {'requires_complete_card_program','card_program_admission'}})
                    self.assertFalse(bind_semantic_program_runtime(changed,capability_registry=self.registry,profile='commander_review')['strict'])
        card=self.db.lookup(ORIGINALS[0]);bad=replace(card,oracle_id='fixture:paid-alternative-unknown-sibling',oracle_text=card.oracle_text+'\nRewrite history.')
        semantics=SemanticRegistry();register_generated_programs(self.db,semantics,[bad],trust_level='provisional',
            capability_registry=self.registry,capability_profile='commander_review',promote_exact_runtime_handlers=True,
            promote_exact_trigger_programs=True,promote_exact_effect_programs=True,promote_exact_capability_declarations=True)
        program=compile_best_available_card_program(self.db,bad,semantic_registry=semantics,capability_registry=self.registry,capability_profile='commander_review')
        self.assertFalse(bind_card_program_runtime(program,capability_registry=self.registry,profile='commander_review')['strict_capability_ready'])

    def crush_witness(self,surge):
        s=self.session(716010+surge);self.trim(s)
        source=self.add(s.engine,ORIGINALS[0],zone='hand');primer=self.add(s.engine,'Ornithopter',zone='hand')
        bear=self.add(s.engine,'Grizzly Bears',seat='B');doubler=self.add(s.engine,'Parallel Lives')
        private=self.add(s.engine,'Counterspell',seat='C',zone='hand',ref='paid-crush-private')
        for card in (source,primer,doubler):self.assert_closed(s,card)
        self.prepare(s,mana={'C':3 if surge else 4,'U':2});self.checkpoint(s);self.prime(s,primer)
        self.cast_price(s,source,surge=surge);self.assertIsNone(self.resolve(s))
        for card in (primer,bear,doubler):self.assertEqual('hand',s.state.cards[card.object_id].zone)
        tokens=self.tokens(s);self.assertEqual(int(surge),len(tokens))
        if tokens:
            self.assertEqual('A',tokens[0].controller)
            self.assertEqual((8,8),tuple(s.engine._numeric_stat(tokens[0].object_id,k) for k in ('power','toughness')))
        self.assertEqual(0,sum(s.state.players['A'].mana_pool.values()));self.finish(s,private)

    def test_actual_crush_printed_and_surge_return_all_before_optional_octopus(self):
        for surge in (False,True):
            with self.subTest(surge=surge):self.crush_witness(surge)

    def test_actual_bushwhacker_both_prices_keep_haste_and_only_surge_boosts_other_controlled_creatures(self):
        for surge in (False,True):
            with self.subTest(surge=surge):
                s=self.session(716020+surge);self.trim(s)
                source=self.add(s.engine,ORIGINALS[1],zone='hand');primer=self.add(s.engine,'Ornithopter',zone='hand')
                own=self.add(s.engine,'Grizzly Bears');foreign=self.add(s.engine,'Grizzly Bears',seat='B',ref='paid-foreign-bear')
                private=self.add(s.engine,'Counterspell',seat='C',zone='hand',ref='paid-bush-private')
                self.assert_closed(s,source);self.prepare(s,mana={'C':1 if surge else 2,'R':1});self.checkpoint(s);self.prime(s,primer)
                self.cast_price(s,source,surge=surge);self.assertIsNone(self.resolve(s))
                current=s.state.cards[source.object_id];self.assertIn('haste',s.engine._combat_keywords(current))
                self.assertEqual(2,s.engine._numeric_stat(source.object_id,'power'))
                self.assertEqual(2+int(surge),s.engine._numeric_stat(own.object_id,'power'))
                self.assertEqual(int(surge),s.engine._numeric_stat(primer.object_id,'power'))
                self.assertEqual(2,s.engine._numeric_stat(foreign.object_id,'power'))
                self.assertEqual(surge,'haste' in s.engine._combat_keywords(s.state.cards[own.object_id]));self.finish(s,private)

    def test_actual_tyrant_both_prices_keep_flying_and_only_surge_announces_target_and_deals_damage(self):
        for surge in (False,True):
            with self.subTest(surge=surge):
                s=self.session(716030+surge);self.trim(s)
                source=self.add(s.engine,ORIGINALS[2],zone='hand');primer=self.add(s.engine,'Ornithopter',zone='hand')
                private=self.add(s.engine,'Counterspell',seat='C',zone='hand',ref='paid-tyrant-private')
                self.assert_closed(s,source);self.prepare(s,mana={'C':3 if surge else 5,'R':2});self.checkpoint(s);self.prime(s,primer)
                self.cast_price(s,source,surge=surge);decision=self.resolve(s)
                if surge:
                    self.assertEqual('semantic.target',decision.kind);self.target(s,'A',['D']);self.assertIsNone(self.resolve(s))
                else:self.assertIsNone(decision)
                self.assertIn('flying',s.engine._combat_keywords(s.state.cards[source.object_id]))
                self.assertEqual(37 if surge else 40,s.state.players['D'].life);self.finish(s,private)

    def test_actual_opponent_spell_does_not_unlock_surge_but_countered_own_primer_does(self):
        for own in (False,True):
            with self.subTest(own=own):
                s=self.session(716040+own);self.trim(s);source=self.add(s.engine,ORIGINALS[0],zone='hand')
                private=self.add(s.engine,'Counterspell',seat='C',zone='hand',ref='paid-history-private')
                if own:
                    primer=self.add(s.engine,'Ornithopter',zone='hand');counter=self.add(s.engine,'Counterspell',seat='B',zone='hand')
                    self.add(s.engine,'Island',seat='B',ref='paid-counter-island-1');self.add(s.engine,'Island',seat='B',ref='paid-counter-island-2')
                else:
                    shock=self.add(s.engine,'Shock',seat='B',zone='hand');self.add(s.engine,'Mountain',seat='B')
                self.prepare(s,mana={'C':3 if own else 4,'U':2});self.checkpoint(s)
                if own:
                    self.cast(s,'A',primer);self.cast(s,'B',counter,[s.state.stack[-1].ref]);self.assertIsNone(self.resolve(s))
                    self.assertEqual('graveyard',s.state.cards[primer.object_id].zone)
                else:
                    self.cast(s,'B',shock,['D']);self.assertIsNone(self.resolve(s));self.priority(s,'A')
                    action=self.action(s,'A',s.state.cards[source.object_id])
                    self.assertEqual({'normal'},{o['id'] for o in action['cost_options']})
                    from quorune.compiled_public_alternative_costs import compiled_fixed_public_alternative_cost_specs
                    spec=next(p for p in compiled_fixed_public_alternative_cost_specs(s.engine,s.state.cards[source.object_id]) if p.kind.value=='surge')
                    self.reject(s,'A',{'action_id':action['id'],'cost_option':spec.option_id,'pay':'auto'})
                self.cast_price(s,source,surge=own);self.assertIsNone(self.resolve(s))
                self.assertEqual(int(own),len(self.tokens(s)));self.finish(s,private)

    def test_actual_crush_copy_retains_paid_decision_and_uses_its_own_controller(self):
        for surge in (False,True):
            with self.subTest(surge=surge):
                s=self.session(716050+surge);self.trim(s);source=self.add(s.engine,ORIGINALS[0],zone='hand')
                primer=self.add(s.engine,'Ornithopter',zone='hand');copy=self.add(s.engine,'Reverberate',seat='B',zone='hand')
                self.add(s.engine,'Mountain',seat='B',ref='paid-copy-mountain-1');self.add(s.engine,'Mountain',seat='B',ref='paid-copy-mountain-2')
                private=self.add(s.engine,'Counterspell',seat='C',zone='hand',ref='paid-copy-private')
                self.assert_closed(s,copy);self.prepare(s,mana={'C':3 if surge else 4,'U':2});self.checkpoint(s);self.prime(s,primer)
                self.cast_price(s,source,surge=surge);original=s.state.stack[-1].ref
                self.cast(s,'B',copy,[original]);self.resolve_top(s)
                copied=next(i for i in s.state.stack if i.kind=='spell_copy')
                self.assertEqual('B',copied.controller);self.assertEqual(surge,Fact.from_dict(copied.context['paid_alternative_cast']).paid)
                s=self.replay(s,load=True);self.resolve_top(s)
                first=self.tokens(s);self.assertEqual(int(surge),len(first))
                if first:self.assertEqual('B',first[0].controller)
                self.resolve_top(s);self.assertIsNone(self.resolve(s))
                final=self.tokens(s);self.assertEqual(int(surge),len(final))
                if final:
                    self.assertEqual('A',final[0].controller);self.assertNotEqual(first[0].object_id,final[0].object_id)
                self.assertEqual(3,len(s.engine._current_turn_history('spell_cast')));self.finish(s,private)

    def test_actual_paid_tyrant_trigger_survives_blink_and_new_noncast_entry_does_not_retrigger(self):
        s=self.session(716060);self.trim(s);source=self.add(s.engine,ORIGINALS[2],zone='hand')
        primer=self.add(s.engine,'Ornithopter',zone='hand');blink=self.add(s.engine,'Cloudshift',zone='hand')
        private=self.add(s.engine,'Counterspell',seat='C',zone='hand',ref='paid-blink-private')
        self.assert_closed(s,blink);self.prepare(s,mana={'C':3,'R':2,'W':1});self.checkpoint(s);self.prime(s,primer)
        self.cast_price(s,source,surge=True,reserve={'W':1})
        self.assertEqual('semantic.target',self.resolve(s).kind);self.target(s,'A',['D'])
        old=s.state.cards[source.object_id].logical_object_id
        self.cast(s,'A',blink,[s.state.cards[source.object_id].ref]);self.resolve_top(s)
        self.assertNotEqual(old,s.state.cards[source.object_id].logical_object_id)
        self.assertEqual(1,len([i for i in s.state.stack if i.kind=='triggered_ability' and i.source_object_id==source.object_id]))
        s=self.replay(s,load=True);self.assertIsNone(self.resolve(s));self.assertEqual(37,s.state.players['D'].life)
        self.finish(s,private)

    def test_actual_bushwhacker_recast_is_a_new_other_recipient_and_temporary_result_expires(self):
        s=self.session(716070);self.trim(s);source=self.add(s.engine,ORIGINALS[1],zone='hand')
        primer=self.add(s.engine,'Ornithopter',zone='hand');own=self.add(s.engine,'Grizzly Bears')
        orrery=self.add(s.engine,'Vedalken Orrery');bounce=self.add(s.engine,'Unsummon',seat='B',zone='hand')
        self.add(s.engine,'Island',seat='B');private=self.add(s.engine,'Counterspell',seat='C',zone='hand',ref='paid-recast-private')
        self.assert_closed(s,orrery);self.prepare(s,mana={'C':3,'R':2});self.checkpoint(s);self.prime(s,primer)
        self.cast_price(s,source,surge=True,reserve={'C':2,'R':1});self.resolve_top(s)
        old=s.state.cards[source.object_id].logical_object_id
        self.cast(s,'B',bounce,[s.state.cards[source.object_id].ref]);self.resolve_top(s)
        self.cast_price(s,s.state.cards[source.object_id],surge=False);self.resolve_top(s)
        self.assertNotEqual(old,s.state.cards[source.object_id].logical_object_id)
        self.assertEqual(1,len([i for i in s.state.stack if i.kind=='triggered_ability' and i.source_object_id==source.object_id]))
        self.assertIsNone(self.resolve(s));self.assertEqual(3,s.engine._numeric_stat(source.object_id,'power'))
        self.assertEqual(3,s.engine._numeric_stat(own.object_id,'power'))
        self.until(s,lambda ss:ss.state.active_player=='B' and ss.state.step=='upkeep')
        self.assertEqual(2,s.engine._numeric_stat(source.object_id,'power'));self.assertEqual(2,s.engine._numeric_stat(own.object_id,'power'))
        self.finish(s,private)

    def test_actual_paid_permanent_spell_copy_keeps_entry_receipt_without_a_new_cast(self):
        s=self.session(716080);self.trim(s);source=self.add(s.engine,ORIGINALS[2],zone='hand')
        primer=self.add(s.engine,'Ornithopter',zone='hand');copier=self.add(s.engine,'Lithoform Engine')
        private=self.add(s.engine,'Counterspell',seat='C',zone='hand',ref='paid-permanent-copy-private')
        self.assert_closed(s,copier);self.prepare(s,mana={'C':7,'R':2});self.checkpoint(s);self.prime(s,primer)
        self.cast_price(s,source,surge=True,reserve={'C':4});original=s.state.stack[-1].ref;self.priority(s,'A')
        action=next(a for a in s.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
            if a['id'].startswith('activate:'+copier.ref+':') and original in json.dumps(a.get('target_schema') or {}))
        command={'action_id':action['id'],'targets':[original],'pay':'auto'};self.reject(s,'B',command)
        accepted=s.act('pilot:A',command);self.assertTrue(accepted.ok,accepted.summary);self.resolve_top(s)
        copied=next(i for i in s.state.stack if i.kind=='spell_copy')
        self.assertTrue(Fact.from_dict(copied.context['paid_alternative_cast']).paid)
        s=self.replay(s,load=True);self.resolve_top(s)
        self.assertEqual('semantic.target',s.state.pending_decision.kind);self.target(s,'A',['D']);self.resolve_top(s)
        self.assertEqual(37,s.state.players['D'].life)
        self.resolve_top(s);self.assertEqual('semantic.target',s.state.pending_decision.kind)
        self.target(s,'A',['C']);self.assertIsNone(self.resolve(s))
        self.assertEqual((37,37),(s.state.players['C'].life,s.state.players['D'].life))
        bodies=[c for c in s.state.cards.values() if c.zone=='battlefield' and 'Tyrant of Valakut' in c.printed_name]
        self.assertEqual(2,len(bodies));self.assertEqual(1,sum(not c.is_card_object for c in bodies))
        self.assertEqual(2,len(s.engine._current_turn_history('spell_cast')));self.finish(s,private)

    def test_actual_prefix_token_and_paid_entry_faults_roll_back_then_retry_without_stale_scope(self):
        from quorune.errors import GameRuleError
        from quorune.record import authoritative_state_hash
        import quorune.rules.paid_alternative_cast_facts as owner
        for index,entry in enumerate((False,True)):
            with self.subTest(entry=entry):
                s=self.session(716090+index);self.trim(s);source=self.add(s.engine,ORIGINALS[2] if entry else ORIGINALS[0],zone='hand')
                primer=self.add(s.engine,'Ornithopter',zone='hand');bear=self.add(s.engine,'Grizzly Bears',seat='B')
                private=self.add(s.engine,'Counterspell',seat='C',zone='hand',ref='paid-fault-private')
                self.prepare(s,mana={'C':3,**({'R':2} if entry else {'U':2})});self.checkpoint(s);self.prime(s,primer)
                self.cast_price(s,source,surge=True);observed=[];failed=None
                if entry:
                    original=owner.information_for_paid_entry
                    def fault(*args,**kwargs):
                        result=original(*args,**kwargs)
                        if result is not None and result['fact']['paid']:
                            observed.append(s.state.cards[source.object_id].zone)
                            raise GameRuleError('Injected committed paid entry receipt failure')
                        return result
                    target,name=owner,'information_for_paid_entry'
                else:
                    original=s.engine._log
                    def fault(actor,code,*args,**kwargs):
                        result=original(actor,code,*args,**kwargs)
                        if code=='token.create':
                            observed.append((s.state.cards[primer.object_id].zone,s.state.cards[bear.object_id].zone,len(self.tokens(s))))
                            raise GameRuleError('Injected committed paid token result failure')
                        return result
                    target,name=s.engine,'_log'
                with patch.object(target,name,side_effect=fault):
                    for _ in range(8):
                        principal=s.pending_principals()[0];before=authoritative_state_hash(s.state)
                        result=s.act(principal,{'action_id':'pass'})
                        if not result.ok:
                            self.assertEqual(before,authoritative_state_hash(s.state));failed=principal;break
                self.assertIsNotNone(failed);self.assertEqual(['battlefield'] if entry else [('hand','hand',1)],observed)
                self.assertIsNone(owner._RESOLVING_ENTRY.get())
                for card in (primer,bear):self.assertEqual('battlefield',s.state.cards[card.object_id].zone)
                self.assertEqual('stack',s.state.cards[source.object_id].zone)
                self.assertEqual(0,sum(s.state.players['A'].mana_pool.values()))
                s=self.replay(s,load=True);result=s.act(failed,{'action_id':'pass'});self.assertTrue(result.ok,result.summary)
                if entry:
                    self.assertEqual('semantic.target',self.resolve(s).kind);self.target(s,'A',['D'])
                self.assertIsNone(self.resolve(s));self.assertIsNone(owner._RESOLVING_ENTRY.get())
                self.assertEqual(37 if entry else 40,s.state.players['D'].life)
                if not entry:self.assertEqual(1,len(self.tokens(s)))
                self.finish(s,private)

    def test_fresh_native_paid_bit_mutant_is_killed_after_actual_normal_and_surge_witness(self):
        original=Fact.satisfies;observed=[]
        def inverted(fact,requirement):
            observed.append(fact.paid);return not original(fact,requirement)
        with patch.object(Fact,'satisfies',inverted):
            with self.assertRaises(AssertionError):
                self.crush_witness(False)
        self.assertEqual([False],observed)

    def test_actual_crush_commander_return_pause_reloads_before_paid_octopus_result(self):
        s=self.session(716100);self.trim(s);source=self.add(s.engine,ORIGINALS[0],zone='hand')
        primer=self.add(s.engine,'Ornithopter',zone='hand')
        commander=next(c for c in s.state.cards.values() if c.owner=='B' and c.is_commander)
        register_generated_programs(self.db,s.engine.semantics,(self.db.lookup('Sliver Hivelord'),),trust_level='provisional',
            capability_registry=self.registry,capability_profile='commander_review',promote_exact_runtime_handlers=True,
            promote_exact_trigger_programs=True,promote_exact_effect_programs=True,promote_exact_capability_declarations=True)
        self.assert_closed(s,commander);s.engine.move_card(commander.object_id,'battlefield',controller='B',log=False)
        private=self.add(s.engine,'Counterspell',seat='C',zone='hand',ref='paid-prefix-private')
        self.prepare(s,mana={'C':3,'U':2});self.checkpoint(s);self.prime(s,primer)
        self.cast_price(s,source,surge=True);self.assertEqual('replacement.order',self.resolve(s).kind)
        self.assertEqual(['pilot:B'],s.pending_principals());self.assertFalse(self.tokens(s))
        self.assertTrue(Fact.from_dict(next(i for i in s.state.stack if i.card_object_id==source.object_id).context['paid_alternative_cast']).paid)
        s=self.replay(s,load=True);options=s.packet('pilot:B',full=True)['decision']['ctx']['options']
        chosen=next(o for o in options if not o.get('decline'))
        command={'action_id':'choose','replacement':chosen['id']};self.reject(s,'A',command)
        result=s.act('pilot:B',command);self.assertTrue(result.ok,result.summary);self.assertIsNone(self.resolve(s))
        self.assertEqual('command',s.state.cards[commander.object_id].zone)
        self.assertEqual('hand',s.state.cards[primer.object_id].zone);self.assertEqual(1,len(self.tokens(s)))
        self.assertEqual('A',self.tokens(s)[0].controller);self.finish(s,private)
