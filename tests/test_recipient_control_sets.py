"""Targeted recipients, original source exclusion and untargeted handoffs."""
from copy import deepcopy
from dataclasses import replace
import json,unittest
from types import SimpleNamespace
from unittest.mock import patch
import test_fixed_creature_power_damage as records
import test_recipient_control as recipients
from inherited_composition_fixture import InheritedNativeFixture
from quorune.oracle_ir import compile_oracle_card
from quorune.rules.capabilities import load_default_capability_registry,capability_dependencies_for_node
from quorune.rules.recipient_control_shapes import recipient_control_runtime_dependencies
from quorune.semantic_runtime.control_handlers import RecipientControlSetHandler


class RecipientControlSetModelCompilerTests(unittest.TestCase):
    def compile(self,text,*,permanent=False):
        return compile_oracle_card(records.card_record(text,type_line='Creature — Leviathan' if permanent else 'Sorcery'),
            capability_registry=load_default_capability_registry(),capability_profile='commander_review')

    def test_fixed_set_separates_actor_recipient_and_original_source(self):
        context=recipients.RecipientControlModelTests().context()
        effect={'op':'give_control_set','schema_version':1,'scope':'other_controlled_permanents','controller':'C','exclude_source':'original','source':'source-ref'}
        intent,=RecipientControlSetHandler().lower(effect,context).intents
        self.assertEqual(('A','C','original'),(intent.actor,intent.controller,intent.exclude_source_ref))
        for change in ({'schema_version':True},{'scope':'all_permanents'},{'controller':'Z'},{'duration':'until_end_of_turn'},{'exclude_source':True}):
            with self.subTest(change=change),self.assertRaises(ValueError):RecipientControlSetHandler().lower({**effect,**change},context)
        self.assertEqual((),RecipientControlSetHandler().lower({**effect,'controller':None},context).intents)
        self.assertIsNone(RecipientControlSetHandler().lower({**effect,'exclude_source':None},context).intents[0].exclude_source_ref)

    def test_closed_player_set_and_target_subject_choice_keep_their_distinct_schemas(self):
        ir=self.compile('When this creature enters, target opponent gains control of all other permanents you control.',permanent=True)
        self.assertEqual('exact',ir.status,ir.material_residuals)
        node,=ir.faces[0].nodes
        self.assertEqual(('give_control_set','$target.0','$source.zone_object'),tuple(node.effects[0][k] for k in ('op','controller','exclude_source')))
        self.assertEqual('opponent',node.target_schema['player_relation'])
        for change in ({'scope':'permanents_you_own'},{'controller':'$controller'},{'exclude_source':'$source'},{'schema_version':True}):
            with self.subTest(change=change):
                effects=({**node.effects[0],**change},)
                self.assertNotIn('continuous.control.fixed_recipient',capability_dependencies_for_node(effects=effects,target_schema=node.target_schema,mechanic_ids=node.mechanics))
                with self.assertRaises(ValueError):recipient_control_runtime_dependencies(SimpleNamespace(effects=effects,target_schema=node.target_schema))
        handoff=self.compile('Draw cards equal to the mana value of target artifact or creature you control. An opponent gains control of that permanent.')
        self.assertEqual('exact',handoff.status,handoff.material_residuals)
        draw,gift=handoff.faces[0].nodes[0].effects
        self.assertEqual(('target_characteristic','mana_value'),(draw['count']['origin'],draw['count']['characteristic']))
        self.assertEqual((2,'$target.0'),(gift['schema_version'],gift['card']))
        self.assertEqual(['permanent'],handoff.faces[0].nodes[0].target_schema['categories'])
        changed={**gift,'schema_version':1}
        with self.assertRaises(ValueError):recipient_control_runtime_dependencies(SimpleNamespace(effects=(changed,),target_schema=None))

    def test_unsupported_sets_and_mismatched_handoff_subjects_stay_residual(self):
        for text in ('Target opponent gains control of all permanents you own.',
                'Target opponent gains control of all other permanents you control until end of turn.',
                'Target opponent gains control of all other creatures you control.',
                'An opponent gains control of all other permanents you control.',
                'Draw cards equal to the mana value of target artifact. An opponent gains control of that creature.',
                'Draw cards equal to the mana value of up to one target artifact. An opponent gains control of that permanent.'):
            with self.subTest(text=text):self.assertNotEqual('exact',self.compile(text,permanent=True).status)


class RecipientControlSetNativeTests(InheritedNativeFixture,unittest.TestCase):
    setUpClass=classmethod(recipients.RecipientControlNativeTests.setUpClass.__func__)
    scene=recipients.RecipientControlNativeTests.scene
    reject=recipients.RecipientControlNativeTests.reject
    finish=recipients.RecipientControlNativeTests.finish

    def sky_scene(self,*,response=None):
        extra={}
        def before(s,source,gift):
            extra['peer']=self.add(s.engine,'Grizzly Bears',seat='B',ref='recipient-set-foreign-controlled');extra['peer'].owner='A'
            extra['aura']=self.add(s.engine,'Control Magic',zone='hand')
            extra['tokens']=self.add(s.engine,'Raise the Alarm',zone='hand')
            if response=='blink':extra['response']=self.add(s.engine,'Cloudshift',zone='hand')
            elif response=='control':
                extra['response']=self.add(s.engine,'Turn Against',seat='B',zone='hand');s.state.players['B'].mana_pool.update({'C':4,'R':1})
        s,source,gift,private=self.scene('Sky Swallower',before=before)
        self.cast(s,'A',extra['aura'],[gift.ref]);self.assertIsNone(self.resolve(s))
        self.cast(s,'A',source);self.assertEqual('semantic.target',self.resolve(s).kind)
        self.reject(s,'A',{'action_id':'choose','targets':['A']});self.target(s,'A',['C'])
        return s,source,gift,private,extra

    def test_sky_uses_the_resolving_actor_current_set_including_response_tokens_and_keeps_attachments(self):
        for response in (None,'control'):
            with self.subTest(response=response):
                s,source,gift,private,extra=self.sky_scene(response=response)
                self.assertIn('flying',s.engine._combat_keywords(source));self.assertEqual(8,s.engine._numeric_stat(source.object_id,'power'))
                if response:
                    self.cast(s,'B',extra['response'],[source.ref]);self.resolve_top(s)
                self.cast(s,'A',extra['tokens']);self.resolve_top(s)
                tokens=[c for c in s.state.cards.values() if c.is_token and c.zone=='battlefield'];self.assertEqual(2,len(tokens))
                s=self.replay(s,load=True);self.assertIsNone(self.resolve(s))
                self.assertEqual('B' if response else 'A',s.state.cards[source.object_id].controller)
                self.assertEqual(('C','D'),(s.state.cards[gift.object_id].controller,s.state.cards[gift.object_id].owner))
                self.assertEqual('B',s.state.cards[extra['peer'].object_id].controller)
                for token in tokens:self.assertEqual(('C','A'),(s.state.cards[token.object_id].controller,s.state.cards[token.object_id].owner))
                aura=s.state.cards[extra['aura'].object_id];self.assertEqual(('C',gift.object_id),(aura.controller,aura.attached_to))
                self.finish(s,private)

    def sky_blink(self):
        s,source,gift,private,extra=self.sky_scene(response='blink');old=source.logical_object_id
        self.cast(s,'A',extra['response'],[source.ref]);self.resolve_top(s)
        self.assertEqual('semantic.target',s.state.pending_decision.kind);self.target(s,'A',['D'])
        s=self.replay(s,load=True);self.resolve_top(s)
        self.assertEqual('D',s.state.cards[gift.object_id].controller)
        self.assertEqual('A',s.state.cards[source.object_id].controller)
        self.assertIsNone(self.resolve(s))
        self.assertNotEqual(old,s.state.cards[source.object_id].logical_object_id)
        self.assertEqual('C',s.state.cards[source.object_id].controller)
        self.assertEqual('D',s.state.cards[gift.object_id].controller)
        self.finish(s,private)

    def test_sky_original_trigger_includes_the_returned_source_after_the_new_trigger_resolves(self):
        self.sky_blink()

    def test_sky_illegal_recipient_prevents_the_entire_set_transfer(self):
        s,source,gift,private,extra=self.sky_scene()
        self.priority(s,'C');result=s.act('pilot:C',{'action_id':'concede','choices':{'confirm_concede':True}});self.assertTrue(result.ok,result.summary)
        s=self.replay(s,load=True);self.assertIsNone(self.resolve(s))
        self.assertEqual(('A','A','B'),tuple(s.state.cards[c.object_id].controller for c in (source,gift,extra['peer'])))
        for viewer in 'ABD':self.assertNotIn(private.ref,json.dumps(s.packet('pilot:'+viewer,full=True)))
        self.replay(s,load=True)

    def handoff(self,mode):
        extra={}
        def before(s,source,gift):
            if mode in ('prototype','printed'):extra['subject']=self.add(s.engine,'Blitz Automaton',zone='hand')
            elif mode=='x':extra['subject']=self.add(s.engine,'Walking Ballista',zone='hand')
        s,source,gift,private=self.scene('Fateful Handoff',before=before)
        target=extra.get('subject',gift)
        if mode=='prototype':
            self.priority(s,'A');row=next(r for r in s.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions'] if r['id']=='cast-prototype:'+target.ref)
            result=s.act('pilot:A',{'action_id':row['id'],'pay':'auto'});self.assertTrue(result.ok,result.summary);self.assertIsNone(self.resolve(s))
        elif mode=='printed':self.cast(s,'A',target);self.assertIsNone(self.resolve(s))
        elif mode=='x':
            row=self.action(s,'A',target);command={'action_id':row['id'],'x':2,'pay':'auto'};self.reject(s,'B',command)
            result=s.act('pilot:A',command);self.assertTrue(result.ok,result.summary)
            self.assertEqual(('stack',2),(s.state.cards[target.object_id].zone,s.state.stack[-1].x_value))
            self.assertIsNone(self.resolve(s));self.assertEqual(2,s.state.cards[target.object_id].counters.get('+1/+1'))
        count={'ordinary':2,'prototype':3,'printed':7,'x':0}[mode]
        self.cast(s,'A',source,[target.ref]);hand=set(s.state.players['A'].zones['hand']);other=set(s.state.players['C'].zones['hand'])
        self.assertEqual('semantic.choice',self.resolve(s).kind)
        drawn=set(s.state.players['A'].zones['hand'])-hand;self.assertEqual(count,len(drawn))
        self.assertEqual(other,set(s.state.players['C'].zones['hand']))
        self.assertEqual('A',s.state.cards[target.object_id].controller)
        for oid in drawn:
            for viewer in 'BCD':self.assertNotIn(s.state.cards[oid].ref,json.dumps(s.packet('pilot:'+viewer,full=True)))
        command={'action_id':'choose','recipient':'C'};self.reject(s,'B',command);self.reject(s,'A',{**command,'recipient':'A'})
        s=self.replay(s,load=True);result=s.act('pilot:A',command);self.assertTrue(result.ok,result.summary);self.assertIsNone(self.resolve(s))
        self.assertEqual(('C','D' if mode=='ordinary' else 'A'),(s.state.cards[target.object_id].controller,s.state.cards[target.object_id].owner))
        self.finish(s,private)

    def test_handoff_draws_before_untargeted_choice_and_reads_prototype_and_battlefield_x_mana_value(self):
        for mode in ('ordinary','prototype','printed','x'):
            with self.subTest(mode=mode):self.handoff(mode)

    def test_handoff_illegal_control_relation_or_returned_target_stops_draw_and_gift(self):
        for blink in (False,True):
            with self.subTest(blink=blink):
                extra={}
                def before(s,source,gift):
                    extra['response']=self.add(s.engine,'Cloudshift' if blink else 'Turn Against',seat='A' if blink else 'B',zone='hand')
                    s.state.players['B'].mana_pool.update({'C':4,'R':1})
                s,source,gift,private=self.scene('Fateful Handoff',before=before)
                self.cast(s,'A',source,[gift.ref]);hand=set(s.state.players['A'].zones['hand'])
                old=gift.logical_object_id
                self.cast(s,'A' if blink else 'B',extra['response'],[gift.ref]);self.resolve_top(s)
                expected=hand-({extra['response'].object_id} if blink else set())
                s=self.replay(s,load=True);self.assertIsNone(self.resolve(s))
                self.assertEqual(expected,set(s.state.players['A'].zones['hand']))
                self.assertEqual('A' if blink else 'B',s.state.cards[gift.object_id].controller)
                if blink:self.assertNotEqual(old,s.state.cards[gift.object_id].logical_object_id)
                self.finish(s,private)

    def test_new_recipient_set_and_target_choice_require_authority_without_coverage(self):
        from quorune.card_programs.binding import bind_semantic_program_runtime
        for name in ('Sky Swallower','Fateful Handoff'):
            s,source,gift,private=self.scene(name)
            program=next(p for p in s.engine.semantics.programs_for_oracle(source.oracle_id) if any(e.get('op') in {'give_control_set','choose_control_recipient'} for e in p.effects))
            deps=tuple(c for c in program.capability_dependencies if c!='continuous.control.fixed_recipient')
            stripped=replace(program,coverage=(),capability_dependencies=deps,capability_closure=self.registry.closure(deps,profile='commander_review').to_dict())
            bound=bind_semantic_program_runtime(stripped,capability_registry=self.registry,profile='commander_review')
            self.assertFalse(bound['strict']);self.assertIn('capability:undeclared_runtime_dependency:continuous.control.fixed_recipient',bound['blockers'])
            for schema in (None,{'zones':['player'],'categories':['player'],'count':True},
                    {'zones':['hand'],'categories':['card'],'count':1}):
                stripped=replace(program,coverage=(),target_schema=schema)
                bound=bind_semantic_program_runtime(stripped,capability_registry=self.registry,profile='commander_review')
                self.assertFalse(bound['strict']);self.assertTrue(any('target schema' in reason for reason in bound['blockers']),bound['blockers'])
            if name=='Fateful Handoff':
                deps=tuple(c for c in program.capability_dependencies if c!='quantity_expression.scalar_effect_amount')
                stripped=replace(program,coverage=(),capability_dependencies=deps,capability_closure=self.registry.closure(deps,profile='commander_review').to_dict())
                bound=bind_semantic_program_runtime(stripped,capability_registry=self.registry,profile='commander_review')
                self.assertFalse(bound['strict']);self.assertIn('capability:undeclared_runtime_dependency:quantity_expression.scalar_effect_amount',bound['blockers'])

    def test_opponent_handoff_copy_rebinds_target_amount_drawer_and_resolution_chooser(self):
        extra={}
        def before(s,source,gift):
            extra['copy']=self.add(s.engine,'Reverberate',seat='B',zone='hand')
            extra['target']=self.add(s.engine,'Hill Giant',seat='B',ref='handoff-copy-target');extra['target'].owner='D'
            extra['illegal']=self.add(s.engine,'Grizzly Bears',ref='handoff-copy-new-illegal-target')
            s.state.players['B'].mana_pool['R']=2
        s,source,gift,private=self.scene('Fateful Handoff',before=before)
        self.cast(s,'A',source,[gift.ref]);self.cast(s,'B',extra['copy'],[s.state.stack[-1].ref])
        self.assertEqual('semantic.choice',self.resolve(s).kind)
        command={'action_id':'choose','targets':[extra['target'].ref]}
        self.reject(s,'A',command);self.reject(s,'B',{**command,'targets':[extra['illegal'].ref]})
        s=self.replay(s,load=True);result=s.act('pilot:B',command);self.assertTrue(result.ok,result.summary)
        hands={p:set(s.state.players[p].zones['hand']) for p in 'AB'}
        self.assertEqual('semantic.choice',self.resolve(s).kind)
        self.assertEqual(['pilot:B'],s.pending_principals());self.assertEqual(4,len(set(s.state.players['B'].zones['hand'])-hands['B']))
        choose={'action_id':'choose','recipient':'C'};self.reject(s,'A',choose)
        s=self.replay(s,load=True);result=s.act('pilot:B',choose);self.assertTrue(result.ok,result.summary)
        self.assertEqual('semantic.choice',self.resolve(s).kind)
        self.assertEqual(['pilot:A'],s.pending_principals());self.assertEqual(2,len(set(s.state.players['A'].zones['hand'])-hands['A']))
        choose={'action_id':'choose','recipient':'D'};self.reject(s,'B',choose)
        result=s.act('pilot:A',choose);self.assertTrue(result.ok,result.summary);self.assertIsNone(self.resolve(s))
        self.assertEqual(('C','D'),(s.state.cards[extra['target'].object_id].controller,s.state.cards[extra['target'].object_id].owner))
        self.assertEqual(('D','D'),(s.state.cards[gift.object_id].controller,s.state.cards[gift.object_id].owner))
        for player in 'AB':
            for oid in set(s.state.players[player].zones['hand'])-hands[player]:
                for viewer in set('ABCD')-{player}:self.assertNotIn(s.state.cards[oid].ref,json.dumps(s.packet('pilot:'+viewer,full=True)))
        self.finish(s,private)

    def test_fresh_sky_blink_witness_kills_excluding_the_returned_physical_source(self):
        from quorune.control_effects import execute_recipient_control_set
        method='test_sky_original_trigger_includes_the_returned_source_after_the_new_trigger_resolves'
        type(self)(method).sky_blink()
        def wrong(host,intent):
            card=host.state.cards.get(intent.source.object_id)
            return execute_recipient_control_set(host,replace(intent,exclude_source_ref=card.ref if card is not None else None))
        with patch('quorune.control_effects.execute_recipient_control_set',wrong):
            with self.assertRaises(AssertionError):type(self)(method).sky_blink()

    def test_fresh_handoff_witness_kills_giving_the_target_back_to_the_resolver(self):
        from quorune.semantic_runtime.control_handlers import RecipientControlHandler
        original=RecipientControlHandler.lower
        method='test_handoff_draws_before_untargeted_choice_and_reads_prototype_and_battlefield_x_mana_value'
        type(self)(method).handoff('ordinary')
        def wrong(handler,effect,context):return original(handler,{**effect,'controller':context.actor},context)
        with patch.object(RecipientControlHandler,'lower',wrong):
            with self.assertRaises(AssertionError):type(self)(method).handoff('ordinary')
