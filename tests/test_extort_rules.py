from __future__ import annotations
from dataclasses import replace
from pathlib import Path
import json
import tempfile
import unittest
from unittest.mock import patch
from common import ROOT
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition,DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import CapabilityRegistry,load_default_capability_registry
from quorune.rules.extort import EXTORT_CAPABILITY,extort_node_capabilities
from quorune.session import CommanderSession
from scripts.build_test_database import build_fixture_database
from test_qualified_zone_event_queries import query_record
import test_bound_effect_programs as witnesses
import test_counter_placement_events as counter_witnesses


class ExtortCompilerTests(unittest.TestCase):
    def test_each_keyword_instance_has_independent_cast_trigger_and_fixed_hybrid_choice(self):
        for text in ('Extort','Extort, extort'):
            ir=compile_oracle_card(replace(query_record(text),keywords=('Extort',)),capability_registry=load_default_capability_registry())
            self.assertEqual('exact',ir.status,ir.material_residuals)
            nodes=[n for f in ir.faces for n in f.nodes]
            self.assertEqual(2 if ',' in text else 1,len(nodes));self.assertEqual(len(nodes),len({n.node_id for n in nodes}))
            for node in nodes:
                self.assertEqual('spell.cast',node.event);self.assertEqual('battlefield',node.active_zone)
                self.assertEqual({'field':'controller','op':'eq','value':'$source.controller'},dict(node.event_condition))
                self.assertEqual('pay',node.effects[0]['stage']);self.assertIn(EXTORT_CAPABILITY,node.capability_dependencies)

    def test_extort_stage_shape_and_dependency_omissions_fail_closed(self):
        effect={'op':'extort','schema_version':1,'player':'$controller','source':'$source','stage':'pay'}
        args={'effects':(effect,),'target_schema':None,'mechanic_ids':('extort',)}
        self.assertTrue(extort_node_capabilities(**args))
        for changes in ({'stage':'gain'},{'schema_version':True},{'source':'B'},{'before':{'B':40}}):
            self.assertEqual((),extort_node_capabilities(**{**args,'effects':({**effect,**changes},)}))
        value=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        row=next(r for r in value['capabilities'] if r['id']=='life.change.effect');row.update(status='blocked',blockers=['life owner unavailable'])
        registry=CapabilityRegistry(value);registry.mark_evidence_verified('0'*64)
        self.assertNotEqual('exact',compile_oracle_card(replace(query_record('Extort'),keywords=('Extort',)),capability_registry=registry).status)

    def test_life_loss_batch_and_replacement_identity_reject_malformed_players_and_amounts(self):
        from quorune.semantic_runtime.life_batch_intent import LifeLossBatchIntent
        from quorune.semantic_choices.intent_replacement import validate_semantic_intent_identity
        from quorune.semantic_choices.model import SemanticChoiceError
        for players,amount in ((('B','B'),1),((),1),(('B',),0),(('B',),True),(('B',),-1)):
            with self.assertRaises(ValueError):LifeLossBatchIntent('A',players,amount,'Extort')
        valid=LifeLossBatchIntent('A',('B','C'),1,'Extort').identity()
        self.assertEqual(valid,validate_semantic_intent_identity('life_loss_batch',valid))
        for changes in ({'players':'BC'},{'players':['B','B']},{'amount':True},{'source_ref':''},{'extra':1}):
            with self.assertRaises(SemanticChoiceError):validate_semantic_intent_identity('life_loss_batch',{**valid,**changes})


    def test_actual_result_binding_rejects_malformed_values_and_preserves_unbound_continuations(self):
        from quorune.semantic_choices.model import AutoContinue,IntentResultAmount,IntentResultObjects,SemanticChoiceError
        from quorune.replacement.immutable import FrozenMap
        effect=FrozenMap({'op':'extort','stage':'gain','amount':0})
        unchanged=AutoContinue('ordinary continuation',(effect,))
        self.assertIs(unchanged.prepend_effects,unchanged.resolved_effects({}))
        objects=FrozenMap({'op':'place_fixed_counters','cards':[]})
        object_binding=AutoContinue('resolved objects',(objects,),objects_from_result=IntentResultObjects(0,0,'cards'))
        refs=['CREATED']
        resolved=object_binding.resolved_effects({0:refs})
        refs.append('LATE')
        self.assertEqual(('CREATED',),tuple(resolved[0]['cards']))
        self.assertEqual((),tuple(objects['cards']))
        bound=AutoContinue('resolved loss',(effect,),IntentResultAmount(0,0,'amount'))
        self.assertEqual(3,bound.resolved_effects({0:3})[0]['amount'])
        self.assertEqual(0,effect['amount'])
        for amount in (None,True,-1,'3',{},[],[('A',3)]):
            with self.subTest(amount=amount),self.assertRaises(SemanticChoiceError):bound.resolved_effects({0:amount})
        with self.assertRaises(SemanticChoiceError):bound.resolved_effects({1:3})
        for index,field in ((1,'amount'),(0,'count')):
            invalid=AutoContinue('invalid destination',(effect,),IntentResultAmount(0,index,field))
            with self.assertRaises(SemanticChoiceError):invalid.resolved_effects({0:3})
        for args in ((True,0,'amount'),(-1,0,'amount'),(0,-1,'amount'),(0,0,'player')):
            with self.assertRaises(SemanticChoiceError):IntentResultAmount(*args)


class ExtortRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary=tempfile.TemporaryDirectory();path=Path(cls.temporary.name)/'extort.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',ROOT/'tests/fixtures/extort-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Extort',[DeckEntry('Generic Bound Commander',1,'commander'),DeckEntry('Generic Bound Plains',30)],['Generic Bound Commander'])
    @classmethod
    def tearDownClass(cls):cls.db.close();cls.temporary.cleanup()
    session=witnesses.BoundEffectProgramRuntimeTests.session
    add=witnesses.BoundEffectProgramRuntimeTests.add
    ready=witnesses.BoundEffectProgramRuntimeTests.ready
    checkpoint=witnesses.BoundEffectProgramRuntimeTests.checkpoint
    replay=witnesses.BoundEffectProgramRuntimeTests.replay
    resolve=counter_witnesses.CounterPlacementEventRuntimeTests.resolve
    order_triggers=counter_witnesses.CounterPlacementEventRuntimeTests.order_triggers

    def begin(self,seed,mana):
        s=self.session(seed);e=s.engine;source=self.add(e,'Syndic of Tithes');target=self.add(e,'Generic Bound Body',seat='B')
        spell=self.add(e,'Unsummon',zone='hand');action=self.ready(s,spell,{'U':1,**mana});self.checkpoint(s)
        accepted=s.act('pilot:A',{'action_id':action['id'],'targets':[target.ref],'pay':'auto'});self.assertTrue(accepted.ok,accepted.summary)
        pending=self.resolve(s);self.assertEqual('semantic.choice',pending.kind)
        return s,source

    def test_actual_extort_white_and_black_payments_transfer_total_opponent_loss_and_replay(self):
        for color in ('W','B'):
            s,source=self.begin(316001 if color=='W' else 316002,{color:1})
            ctx=s.packet('pilot:A',full=True)['decision']['ctx'];values=ctx['legal_actions'][0]['choice_schema']['legal_values']
            self.assertIn(color,values);self.assertIn('decline',values)
            before=authoritative_state_hash(s.state)
            wrong=s.act('pilot:B',{'action_id':'choose','pay':color});self.assertFalse(wrong.ok);self.assertEqual(before,authoritative_state_hash(s.state))
            invalid=s.act('pilot:A',{'action_id':'choose','pay':'U'});self.assertFalse(invalid.ok);self.assertEqual(before,authoritative_state_hash(s.state))
            accepted=s.act('pilot:A',{'action_id':'choose','pay':color});self.assertTrue(accepted.ok,accepted.summary);self.resolve(s)
            self.assertEqual(43,s.state.players['A'].life);self.assertTrue(all(s.state.players[p].life==39 for p in 'BCD'))
            self.assertEqual(0,s.state.players['A'].mana_pool[color]);self.replay(s,load=True)

    def test_decline_or_unpayable_extort_does_not_lose_life(self):
        for mana in ({},{'W':1}):
            s,source=self.begin(316003 if mana else 316004,mana)
            accepted=s.act('pilot:A',{'action_id':'choose','pay':'decline'});self.assertTrue(accepted.ok,accepted.summary);self.resolve(s)
            self.assertTrue(all(s.state.players[p].life==40 for p in 'ABCD'));self.replay(s)

    def test_actual_gain_multiplier_applies_after_total_loss(self):
        s=self.session(316005);e=s.engine;self.add(e,'Syndic of Tithes');self.add(e,'Boon Reflection')
        target=self.add(e,'Generic Bound Body',seat='B');spell=self.add(e,'Unsummon',zone='hand')
        action=self.ready(s,spell,{'U':1,'W':1});self.checkpoint(s)
        accepted=s.act('pilot:A',{'action_id':action['id'],'targets':[target.ref],'pay':'auto'});self.assertTrue(accepted.ok,accepted.summary)
        self.assertEqual('semantic.choice',self.resolve(s).kind)
        accepted=s.act('pilot:A',{'action_id':'choose','pay':'W'});self.assertTrue(accepted.ok,accepted.summary);self.resolve(s)
        self.assertEqual(46,s.state.players['A'].life);self.assertTrue(all(s.state.players[p].life==39 for p in 'BCD'));self.replay(s)

    def test_omitting_actual_loss_gain_mutant_is_killed_by_four_player_transfer(self):
        from quorune.semantic_choices.model import AutoContinue
        original=AutoContinue.resolved_effects
        def fixed_gain(value,results):
            effects=original(value,results)
            return tuple({**dict(e),'amount':1} if e.get('op')=='extort' and e.get('stage')=='gain' else e for e in effects)
        with patch.object(AutoContinue,'resolved_effects',fixed_gain):
            with self.assertRaises(AssertionError):self.test_actual_extort_white_and_black_payments_transfer_total_opponent_loss_and_replay()

    def test_prevented_opponent_loss_contributes_zero_to_gain(self):
        from quorune.replacement import ReplacementEffect,ReplacementClass
        from quorune.effect_runtime.life_effects import collect_life_change_replacement_effects
        s,source=self.begin(316006,{'W':1})
        stopped=ReplacementEffect(effect_id='fixture:extort-prevented-loss',source_id='fixture:life-loss-boundary',event_kind='life.change',
            replacement_class=ReplacementClass.OTHER,conditions={'player':{'eq':'B'},'direction':{'eq':'loss'}},
            operations=({'op':'set','field':'amount','value':0},))
        original=collect_life_change_replacement_effects
        with patch('quorune.effect_runtime.life_effects.collect_life_change_replacement_effects',side_effect=lambda host:(*original(host),stopped)):
            accepted=s.act('pilot:A',{'action_id':'choose','pay':'W'});self.assertTrue(accepted.ok,accepted.summary);self.resolve(s)
        self.assertEqual(42,s.state.players['A'].life);self.assertEqual(40,s.state.players['B'].life)
        self.assertEqual((39,39),(s.state.players['C'].life,s.state.players['D'].life))

    def test_pending_payment_and_gain_replacement_preserve_committed_loss_after_reload(self):
        s=self.session(316007);e=s.engine;self.add(e,'Syndic of Tithes')
        self.add(e,'Boon Reflection',ref='gain-double-one');self.add(e,'Boon Reflection',ref='gain-double-two')
        target=self.add(e,'Generic Bound Body',seat='B');spell=self.add(e,'Unsummon',zone='hand')
        action=self.ready(s,spell,{'U':1,'W':1});self.checkpoint(s)
        accepted=s.act('pilot:A',{'action_id':action['id'],'targets':[target.ref],'pay':'auto'});self.assertTrue(accepted.ok,accepted.summary)
        self.assertEqual('semantic.choice',self.resolve(s).kind)
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'payment';s.save(path);s=CommanderSession.load(self.db,path)
        accepted=s.act('pilot:A',{'action_id':'choose','pay':'W'});self.assertTrue(accepted.ok,accepted.summary)
        self.assertEqual('replacement.order',s.state.pending_decision.kind)
        self.assertTrue(all(s.state.players[p].life==39 for p in 'BCD'));self.assertEqual(40,s.state.players['A'].life)
        before=authoritative_state_hash(s.state)
        rejected=s.act('pilot:B',{'action_id':'choose','replacement':'wrong'});self.assertFalse(rejected.ok);self.assertEqual(before,authoritative_state_hash(s.state))
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'gain';s.save(path);s=CommanderSession.load(self.db,path)
        for _ in range(8):
            if s.state.pending_decision is None or s.state.pending_decision.kind!='replacement.order':break
            principal=s.pending_principals()[0];ctx=s.packet(principal,full=True)['decision']['ctx']
            accepted=s.act(principal,{'action_id':'choose','replacement':ctx['options'][0]['id']});self.assertTrue(accepted.ok,accepted.summary)
        self.resolve(s);self.assertEqual(52,s.state.players['A'].life)
        self.assertTrue(all(s.state.players[p].life==39 for p in 'BCD'));self.replay(s)

    def test_two_actual_sources_choose_independent_payments_for_one_cast(self):
        s=self.session(316008);e=s.engine;self.add(e,'Syndic of Tithes');self.add(e,'Basilica Screecher')
        target=self.add(e,'Generic Bound Body',seat='B');spell=self.add(e,'Unsummon',zone='hand')
        action=self.ready(s,spell,{'U':1,'W':1});self.checkpoint(s)
        accepted=s.act('pilot:A',{'action_id':action['id'],'targets':[target.ref],'pay':'auto'});self.assertTrue(accepted.ok,accepted.summary)
        self.assertEqual('semantic.choice',self.resolve(s).kind)
        accepted=s.act('pilot:A',{'action_id':'choose','pay':'W'});self.assertTrue(accepted.ok,accepted.summary)
        if not s.state.pending_decision or s.state.pending_decision.kind!='semantic.choice':self.resolve(s)
        self.assertEqual('semantic.choice',s.state.pending_decision.kind)
        accepted=s.act('pilot:A',{'action_id':'choose','pay':'decline'});self.assertTrue(accepted.ok,accepted.summary);self.resolve(s)
        self.assertEqual(43,s.state.players['A'].life);self.assertTrue(all(s.state.players[p].life==39 for p in 'BCD'));self.replay(s)

    def test_loss_batch_replacement_suspends_before_any_loss_and_resumes_after_reload(self):
        from quorune.replacement import ReplacementEffect,ReplacementClass
        from quorune.effect_runtime.life_effects import collect_life_change_replacement_effects
        s,source=self.begin(316009,{'W':1})
        def replacement(name,multiplier):
            return ReplacementEffect(effect_id=name,source_id=name,event_kind='life.change',replacement_class=ReplacementClass.OTHER,
                conditions={'player':{'eq':'B'},'direction':{'eq':'loss'},'amount':{'gt':0}},
                operations=({'op':'multiply','field':'amount','factor':multiplier},))
        original=collect_life_change_replacement_effects
        effects=(replacement('fixture:extort-loss-double',2),replacement('fixture:extort-loss-triple',3))
        with patch('quorune.effect_runtime.life_effects.collect_life_change_replacement_effects',side_effect=lambda host:(*original(host),*effects)):
            accepted=s.act('pilot:A',{'action_id':'choose','pay':'W'});self.assertTrue(accepted.ok,accepted.summary)
            self.assertEqual('replacement.order',s.state.pending_decision.kind);self.assertTrue(all(s.state.players[p].life==40 for p in 'ABCD'))
            self.assertEqual(0,s.state.players['A'].mana_pool['W'])
            with tempfile.TemporaryDirectory() as directory:
                path=Path(directory)/'loss';s.save(path);s=CommanderSession.load(self.db,path)
            for _ in range(8):
                if s.state.pending_decision is None or s.state.pending_decision.kind!='replacement.order':break
                principal=s.pending_principals()[0];ctx=s.packet(principal,full=True)['decision']['ctx']
                accepted=s.act(principal,{'action_id':'choose','replacement':ctx['options'][0]['id']});self.assertTrue(accepted.ok,accepted.summary)
            self.resolve(s)
        self.assertEqual(48,s.state.players['A'].life);self.assertEqual(34,s.state.players['B'].life)
        self.assertEqual((39,39),(s.state.players['C'].life,s.state.players['D'].life))

    def test_queued_extort_retains_cast_controller_after_actual_source_bounce_or_control_change(self):
        for response_name in ('Unsummon','Word of Seizing'):
            s=self.session(316010 if response_name=='Unsummon' else 316011);e=s.engine
            source=self.add(e,'Syndic of Tithes');target=self.add(e,'Generic Bound Body',seat='B')
            spell=self.add(e,'Unsummon',zone='hand',ref='extort-original-cast')
            response=self.add(e,response_name,seat='B',zone='hand',ref='extort-source-response')
            action=self.ready(s,spell,{'U':1,'W':1});s.state.players['B'].mana_pool.update({'U':1,'R':2,'C':4});self.checkpoint(s)
            accepted=s.act('pilot:A',{'action_id':action['id'],'targets':[target.ref],'pay':'auto'});self.assertTrue(accepted.ok,accepted.summary)
            for _ in range(8):
                if s.pending_principals()[0]=='pilot:B':break
                accepted=s.act(s.pending_principals()[0],{'action_id':'pass'});self.assertTrue(accepted.ok,accepted.summary)
            action=next(a for a in s.packet('pilot:B',full=True)['decision']['ctx']['legal']['actions'] if a.get('card')==response.ref)
            accepted=s.act('pilot:B',{'action_id':action['id'],'targets':[source.ref],'pay':'auto'});self.assertTrue(accepted.ok,accepted.summary)
            self.assertEqual('semantic.choice',self.resolve(s).kind);self.assertEqual(['pilot:A'],list(s.pending_principals()))
            current=s.state.cards[source.object_id]
            self.assertEqual('hand' if response_name=='Unsummon' else 'battlefield',current.zone)
            if response_name=='Word of Seizing':self.assertEqual('B',current.controller)
            accepted=s.act('pilot:A',{'action_id':'choose','pay':'W'});self.assertTrue(accepted.ok,accepted.summary);self.resolve(s)
            self.assertEqual(43,s.state.players['A'].life);self.assertEqual(39,s.state.players['B'].life);self.replay(s,load=True)

    def test_source_order_omission_mutant_is_killed_by_two_source_replay(self):
        with patch('quorune.trigger_discovery._ordered_event_sources',side_effect=lambda host,sources:list(host._semantic_event_sources())):
            with self.assertRaisesRegex(ValueError,'Replay diverged'):
                self.test_two_actual_sources_choose_independent_payments_for_one_cast()


if __name__=='__main__':unittest.main()
