from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from copy import deepcopy
import json,tempfile,unittest
from unittest.mock import patch
from common import ROOT
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition,DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
import test_token_article_composition as controls
from quorune.rules.evidence_storage import read_evidence_document

CAP='token.creation.post_creation_counter'
NAMES=('Wild Hypothesis','Paradox Zone','Fractal Anomaly','Berta, Wise Extrapolator','Recon Craft Theta',
    'Leyline Invocation','Additive Evolution','Fractal Summoning','Manifestation Sage')


class CreatedTokenCounterTests(unittest.TestCase):
    register_fixture_cards_on_add=True
    session=controls.TokenArticleCompositionTests.session
    add=controls.TokenArticleCompositionTests.add
    prepare=controls.TokenArticleCompositionTests.prepare
    checkpoint=controls.TokenArticleCompositionTests.checkpoint
    resolve=controls.TokenArticleCompositionTests.resolve
    replay=controls.TokenArticleCompositionTests.replay
    binding=controls.TokenArticleCompositionTests.binding
    actions=controls.TokenArticleCompositionTests.actions
    cast=controls.TokenArticleCompositionTests.cast
    reject=controls.TokenArticleCompositionTests.reject
    private_replay=controls.TokenArticleCompositionTests.private_replay
    priority=controls.TokenArticleCompositionTests.priority
    options=controls.TokenArticleCompositionTests.options
    use=controls.TokenArticleCompositionTests.use
    advance=controls.TokenArticleCompositionTests.advance
    top=controls.TokenArticleCompositionTests.top
    tokens=controls.TokenArticleCompositionTests.tokens
    settle=controls.TokenArticleCompositionTests.settle
    activate=controls.TokenArticleCompositionTests.activate

    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory();path=Path(cls.temp.name)/'created-counters.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',ROOT/'tests/fixtures/created-token-counter-cards.json',ROOT/'tests/fixtures/additional-token-status-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Creation followed by actual counters',[DeckEntry('Generic Bound Commander',1,'commander'),DeckEntry('Generic Bound Plains',30)],['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls):cls.db.close();cls.temp.cleanup()

    def fractals(self,s,count,size):
        rows=[c for c in self.tokens(s) if c.printed_name=='Fractal'];self.assertEqual(count,len(rows))
        for card in rows:
            data=s.engine._copyable_characteristics(card);self.assertEqual(('0','0'),(str(data['power']),str(data['toughness'])))
            self.assertEqual(size,card.counters.get('+1/+1',0))
        return rows

    def normal(self,s,card,payment,**choices):
        rows=self.options(s,card)
        option=next((o for o in rows if o['id']=='normal'),None)
        if option is None:
            option=next((o for o in rows if all(o.get('requirements',{}).get(color,0)==payment.get(color,0) for color in 'WUBRG')),None)
        self.assertIsNotNone(option,rows);self.use(s,card,option,payment,**choices)

    def test_real_fractal_summoning_pays_announced_x_and_puts_counters_after_entry(self):
        for size in (0,3):
            s=self.session(808000+size);spell=self.add(s.engine,'Fractal Summoning',zone='hand');private=self.add(s.engine,'Generic Bound Growth',seat='C',zone='hand')
            self.assertTrue(self.binding(s,spell)['strict_capability_ready'])
            self.prepare(s,mana={'C':size,'G':2});self.checkpoint(s);self.normal(s,spell,{'C':size,'G':2},x=size)
            s=self.replay(s,load=True);self.assertIsNone(self.settle(s))
            if size:self.fractals(s,1,size)
            else:self.assertEqual([],self.tokens(s))
            events=[e for e in s.state.events if e.code=='token.create'];self.assertEqual(1,len(events));self.assertEqual(1,events[0].details['base_quantity'])
            self.private_replay(s,private)

    def test_real_leyline_count_and_sage_hand_count_use_their_separate_producers(self):
        for i,name in enumerate(('Leyline Invocation','Manifestation Sage')):
            s=self.session(808010+i);spell=self.add(s.engine,name,zone='hand');self.add(s.engine,'Forest');self.add(s.engine,'Island')
            private=self.add(s.engine,'Generic Bound Growth',seat='C',zone='hand')
            self.assertTrue(self.binding(s,spell)['strict_capability_ready'])
            cost={'C':5,'G':1} if i==0 else {'G':4};self.prepare(s,mana=cost);self.checkpoint(s)
            count=2 if i==0 else len(s.state.players['A'].zones['hand'])-1
            self.normal(s,spell,cost);s=self.replay(s,load=True);self.assertIsNone(self.settle(s));self.fractals(s,1,count);self.private_replay(s,private)

    def test_created_object_result_binding_is_closed_and_preserves_amount_binding(self):
        from quorune.semantic_choices.model import AutoContinue,IntentResultObjects,IntentResultAmount,SemanticChoiceError
        from quorune.replacement.immutable import FrozenMap
        prepared=AutoContinue('Bind created results',(FrozenMap({'cards':[],'amount':0}),),
            amount_from_result=IntentResultAmount(1,0,'amount'),objects_from_result=IntentResultObjects(0,0))
        self.assertEqual(FrozenMap({'cards':['T1','T2'],'amount':3}),prepared.resolved_effects({0:('T1','T2'),1:3})[0])
        for value in (None,'T1',('T1','T1'),('',),('T1',False)):
            with self.assertRaises(SemanticChoiceError):prepared.resolved_effects({0:value,1:3})

    def test_nine_originals_have_complete_admission_and_independent_runtime_minimum(self):
        from quorune.card_programs.binding import bind_semantic_program_runtime
        s=self.session(808020)
        for i,name in enumerate(NAMES):
            card=self.add(s.engine,name,ref='created-source-'+str(i));bound=self.binding(s,card)
            self.assertTrue(bound['strict_capability_ready'],(name,bound['blockers']))
            ir=compile_oracle_card(self.db.lookup(name),capability_registry=self.registry,capability_profile='commander_review');self.assertEqual('exact',ir.status,(name,ir.material_residuals))
            program=next((p for p in s.engine.semantics.programs_for_oracle(card.oracle_id) if CAP in p.capability_dependencies),None);self.assertIsNotNone(program,name)
            deps=tuple(c for c in program.capability_dependencies if c!=CAP)
            changed=replace(program,capability_dependencies=deps,capability_closure=self.registry.closure(deps,profile='commander_review').to_dict())
            binding=bind_semantic_program_runtime(changed,capability_registry=self.registry,profile='commander_review')
            self.assertFalse(binding['strict']);self.assertIn('capability:undeclared_runtime_dependency:'+CAP,binding['blockers'])

    def test_entry_observer_sees_zero_base_before_separately_instructed_counters(self):
        s=self.session(808030);observer=self.add(s.engine,'Generic Small Entry Observer');spell=self.add(s.engine,'Fractal Summoning',zone='hand')
        drawn=self.top(s,'Sol Ring','entry-draw');private=self.add(s.engine,'Generic Bound Growth',seat='C',zone='hand')
        self.assertTrue(self.binding(s,observer)['strict_capability_ready']);self.prepare(s,mana={'C':3,'G':2});self.checkpoint(s)
        self.normal(s,spell,{'C':3,'G':2},x=3);s=self.replay(s,load=True);self.assertIsNone(self.settle(s))
        self.fractals(s,1,3);self.assertEqual('hand',s.state.cards[drawn.object_id].zone);self.private_replay(s,private)

    def test_public_count_is_read_after_creation_and_additional_creatures_receive_counters(self):
        s=self.session(808040);extra=self.add(s.engine,'Generic Additional Thopter Observer');self.add(s.engine,'Generic Bound Body')
        spell=self.add(s.engine,'Generic Counted Construct Subject',zone='hand');private=self.add(s.engine,'Generic Bound Growth',seat='C',zone='hand')
        for card in (extra,spell):self.assertTrue(self.binding(s,card)['strict_capability_ready'])
        self.prepare(s,mana={'G':1});self.checkpoint(s);self.normal(s,spell,{'G':1});s=self.replay(s,load=True);self.assertIsNone(self.settle(s))
        tokens={c.printed_name:c for c in self.tokens(s)};self.assertEqual({'Construct','Thopter'},set(tokens))
        for card in tokens.values():self.assertEqual(3,card.counters.get('+1/+1'))
        self.assertEqual(3,s.engine._numeric_stat(tokens['Construct'].object_id,'power'))
        self.assertEqual(4,s.engine._numeric_stat(tokens['Thopter'].object_id,'power'));self.private_replay(s,private)

    def test_additional_noncreature_token_is_also_a_counter_recipient(self):
        s=self.session(808050);self.add(s.engine,'Generic Token Status Observer');spell=self.add(s.engine,'Generic Fixed Construct Subject',zone='hand')
        private=self.add(s.engine,'Generic Bound Growth',seat='C',zone='hand');self.assertTrue(self.binding(s,spell)['strict_capability_ready'])
        self.prepare(s,mana={'G':1});self.checkpoint(s);self.normal(s,spell,{'G':1});s=self.replay(s,load=True);self.assertIsNone(self.settle(s))
        tokens={c.printed_name:c for c in self.tokens(s)};self.assertEqual({'Construct','Map'},set(tokens))
        for card in tokens.values():self.assertEqual(3,card.counters.get('+1/+1'))
        self.assertNotIn('Creature',s.engine._effective_card_data(tokens['Map'])['type_line']);self.private_replay(s,private)

    def test_token_and_counter_replacement_choices_resume_without_repeated_creation(self):
        s=self.session(808060)
        for i,name in enumerate(('Parallel Lives','Parallel Lives','Branching Evolution','Hardened Scales')):
            card=self.add(s.engine,name,ref='replacement-'+str(i));self.assertTrue(self.binding(s,card)['strict_capability_ready'],name)
        spell=self.add(s.engine,'Fractal Summoning',zone='hand');private=self.add(s.engine,'Generic Bound Growth',seat='C',zone='hand')
        self.prepare(s,mana={'C':3,'G':2});self.checkpoint(s);self.normal(s,spell,{'C':3,'G':2},x=3)
        choices=0
        for _ in range(16):
            pending=self.settle(s)
            if pending is None:break
            self.assertEqual('replacement.order',pending.kind)
            s=self.replay(s,load=True);principal=s.pending_principals()[0];options=s.packet(principal,full=True)['decision']['ctx']['options'];self.assertTrue(options)
            selected=next((o for o in options if 'replacement-2' in o['id']),None) if self.tokens(s) else options[0]
            self.assertIsNotNone(selected,options)
            command={'action_id':'choose','replacement':selected['id']};self.reject(s,command,'pilot:D')
            result=s.act(principal,command);self.assertTrue(result.ok,result.summary);choices+=1
        else:self.fail('Creation/counter replacements did not finish')
        self.assertGreaterEqual(choices,2);tokens=self.fractals(s,4,7)
        self.assertEqual(4,len(tokens));self.assertEqual(1,sum(e.code=='token.create' for e in s.state.events));self.private_replay(s,private)

    def test_real_wild_hypothesis_counters_precede_private_surveil(self):
        s=self.session(808070);spell=self.add(s.engine,'Wild Hypothesis',zone='hand')
        first=self.top(s,'Sol Ring','surveil-first');second=self.top(s,'Divination','surveil-second');private=self.add(s.engine,'Generic Bound Growth',seat='C',zone='hand')
        self.prepare(s,mana={'C':3,'G':1});self.checkpoint(s);self.normal(s,spell,{'C':3,'G':1},x=3)
        pending=self.settle(s);self.assertIsNotNone(pending);self.assertEqual('semantic.choice',pending.kind);self.fractals(s,1,3)
        command={'action_id':'choose','cards':{'top':[second.ref],'graveyard':[first.ref]}};self.reject(s,command,'pilot:D')
        s=self.replay(s,load=True);result=s.act('pilot:A',command);self.assertTrue(result.ok,result.summary);self.assertIsNone(self.settle(s))
        self.assertEqual('graveyard',s.state.cards[first.object_id].zone);self.private_replay(s,private)

    def test_real_history_and_source_counter_definitions_use_their_instruction_time(self):
        from quorune.turn_history import reset_turn_history
        s=self.session(808080);spell=self.add(s.engine,'Fractal Anomaly',zone='hand');draw=self.add(s.engine,'Divination',zone='hand')
        private=self.add(s.engine,'Generic Bound Growth',seat='C',zone='hand')
        s.state.turn_history=reset_turn_history(s.state.turn_history,s.state.turn_sequence)
        self.prepare(s,mana={'C':2,'U':2});self.checkpoint(s);self.normal(s,draw,{'C':2,'U':1});self.assertIsNone(self.settle(s))
        self.normal(s,spell,{'U':1});s=self.replay(s,load=True);self.assertIsNone(self.settle(s));self.fractals(s,1,2);self.private_replay(s,private)
        s=self.session(808081);source=self.add(s.engine,'Paradox Zone',zone='hand');private=self.add(s.engine,'Generic Bound Growth',seat='C',zone='hand')
        self.prepare(s,mana={'C':4,'G':1});self.checkpoint(s);self.normal(s,source,{'C':4,'G':1});self.assertIsNone(self.settle(s))
        self.assertEqual(1,s.state.cards[source.object_id].counters.get('growth'))
        self.advance(s,lambda q:q.state.phase=='ending' and bool(q.state.stack));self.assertIsNone(self.settle(s))
        self.assertEqual(2,s.state.cards[source.object_id].counters.get('growth'));self.fractals(s,1,2);self.private_replay(s,private)

    def test_real_fixed_entry_sources_keep_counter_base_and_existing_siblings(self):
        for i,(name,cost,size) in enumerate((('Additive Evolution',{'C':3,'G':2},3),('Recon Craft Theta',{'C':4},1))):
            s=self.session(808090+i);source=self.add(s.engine,name,zone='hand');private=self.add(s.engine,'Generic Bound Growth',seat='C',zone='hand')
            self.prepare(s,mana=cost);self.checkpoint(s);self.normal(s,source,cost);s=self.replay(s,load=True);self.assertIsNone(self.settle(s))
            tokens=self.tokens(s);self.assertEqual(1,len(tokens));self.assertEqual(size,tokens[0].counters.get('+1/+1'))
            self.assertEqual('0',str(s.engine._copyable_characteristics(tokens[0])['power']))
            if i:self.assertIn('flying',s.engine._combat_keywords(s.state.cards[source.object_id]))
            self.private_replay(s,private)

    def test_real_berta_activation_and_increment_mana_siblings_remain_exact(self):
        s=self.session(808100);source=self.add(s.engine,'Berta, Wise Extrapolator');spell=self.add(s.engine,'Fractal Summoning',zone='hand')
        private=self.add(s.engine,'Generic Bound Growth',seat='C',zone='hand')
        self.prepare(s,mana={'C':8,'G':2});self.checkpoint(s);self.activate(s,source,payment={'C':3},x=3)
        self.assertIsNone(self.settle(s));self.fractals(s,1,3);self.assertEqual(0,s.state.cards[source.object_id].counters.get('+1/+1',0))
        self.normal(s,spell,{'C':5,'G':2},x=5);pending=self.settle(s);self.assertIsNotNone(pending);self.assertEqual('semantic.choice',pending.kind)
        self.assertEqual(1,s.state.cards[source.object_id].counters.get('+1/+1'))
        s=self.replay(s,load=True);result=s.act('pilot:A',{'action_id':'choose','choice':'U'});self.assertTrue(result.ok,result.summary)
        self.assertIsNone(self.settle(s));self.assertEqual([3,5],sorted(c.counters.get('+1/+1',0) for c in self.tokens(s)))
        self.assertEqual(1,s.state.players['A'].mana_pool['U']);self.private_replay(s,private)

    def test_closed_schemas_and_hybrid_x_preserve_unowned_boundaries(self):
        from quorune.compiler.created_token_counters import created_token_counter_effect_template
        from quorune.created_token_counter_model import validate_created_token_counter,COUNTER_CREATED
        from quorune.semantic_choices.model import SemanticChoiceError
        from quorune.semantic_choices.created_token_counters import CREATED_TOKEN_COUNTER_HANDLERS
        text='Create a 0/0 green and blue Fractal creature token. Put three +1/+1 counters on it.'
        compiled=created_token_counter_effect_template(text,source_name='Fixture');self.assertIsNotNone(compiled)
        effect=compiled[1][0];validate_created_token_counter(effect)
        for change in ({'amount':True},{'amount':-1},{'schema_version':False},{'extra':True},
                {'token':{**effect['token'],'quantity':2}}, {'token':{**effect['token'],'controller':'B'}}):
            with self.assertRaises((ValueError,TypeError)):validate_created_token_counter({**effect,**change})
        child={'op':COUNTER_CREATED,'schema_version':1,'controller':'A','source':'source','amount':3,'cards':['T1','T1']}
        with self.assertRaises(ValueError):validate_created_token_counter(child,resolved=True)
        for bad in (text.replace('on it','on target creature'),text+' Attach this Equipment to it.',
                text.replace('three','any number of'),text.replace('+1/+1','-1/-1')):
            self.assertIsNone(created_token_counter_effect_template(bad,source_name='Fixture'),bad)
        for handler in CREATED_TOKEN_COUNTER_HANDLERS:
            with self.assertRaises(SemanticChoiceError):handler.complete(None,{'cards':['foreign']},None)
        from test_declared_effect_amounts import generic_spell
        for cost in ('{X}{G/U}{G/U}','{X}{X}{R/G}'):
            ir=compile_oracle_card(generic_spell('Create an X/X green Ooze creature token.',mana_cost=cost),capability_registry=self.registry,capability_profile='commander_review')
            self.assertEqual('exact',ir.status,ir.material_residuals)
        ir=compile_oracle_card(generic_spell('Create an X/X green Ooze creature token.',mana_cost='{X}{mystery}'),capability_registry=self.registry,capability_profile='commander_review')
        self.assertNotEqual('exact',ir.status)

    def test_verified_withdrawal_and_fresh_omitted_counter_step_reject(self):
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from scripts.update_capability_evidence import build_index,discover_tests
        baseline=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding='utf-8'));decl=read_evidence_document(ROOT/'platform/capability-evidence-declarations.json')
        for capability in (CAP,'token.creation.fixed_definition','counter.producer.fixed_effect'):
            raw=deepcopy(baseline);next(c for c in raw['capabilities'] if c['id']==capability).update(status='blocked',blockers=['withdrawn creation/counter binding'],dependency_fail_closed_status='not_run')
            registry=CapabilityRegistry(raw);index=build_index(registry_value=raw,declaration_source=decl,discovered_test_ids=set(discover_tests(ROOT).values()))
            registry.mark_evidence_verified(validate_capability_evidence_index(index,registry=registry))
            ir=compile_oracle_card(self.db.lookup('Fractal Summoning'),capability_registry=registry,capability_profile='commander_review');blockers=[b for r in ir.material_residuals for b in r.blockers]
            self.assertTrue(any('status:'+capability+':blocked' in b for b in blockers),blockers);self.assertFalse(any('evidence_index:unverified' in b for b in blockers))
        from quorune.semantic_choices.created_token_counters import CreatedTokenCounterHandler
        from quorune.created_token_counter_model import COUNTER_CREATED
        original=CreatedTokenCounterHandler.prepare
        def omit(handler,effect,context):
            prepared=original(handler,effect,context)
            return replace(prepared,preparation_intents=()) if handler.operation==COUNTER_CREATED else prepared
        method='test_real_fractal_summoning_pays_announced_x_and_puts_counters_after_entry'
        with patch.object(CreatedTokenCounterHandler,'prepare',omit):
            with self.assertRaises(AssertionError):getattr(type(self)(method),method)()
