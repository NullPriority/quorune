"""Independent CR702.144a stage/copy/actor boundaries; live integration remains pending."""
import unittest
from dataclasses import FrozenInstanceError

from quorune.demonstrate_model import DemonstrateState, DemonstrateStage
from quorune.event_spell_copy_model import EventSpellCopySource
from quorune.model import StackItem


class DemonstrateStateTests(unittest.TestCase):
    def source(self, kind='spell'):
        item=StackItem(stack_id='original-stack',ref='S1',kind=kind,controller='A',
            label='Original spell',card_object_id='original-card',semantic_key='test:spell',targets=['C'])
        return EventSpellCopySource(item.to_dict(),{'name':'Original','type_line':'Sorcery'},None,'original-card@1')

    def test_required_opponent_stage_follows_committed_copy_and_does_not_rewrite_original_cast_controller(self):
        source=self.source();state=DemonstrateState('B','TRIGGER',source)
        self.assertEqual(DemonstrateStage.SELF_COPY,state.stage)
        self.assertEqual(('A','C','D'),state.opponents(('A','B','C','D')))
        copied=state.after_committed_self_copy()
        self.assertEqual(DemonstrateStage.OPPONENT_COPY,copied.stage)
        self.assertEqual('A',copied.source.item().controller)
        self.assertEqual('B',copied.actor)
        with self.assertRaises(ValueError):copied.after_committed_self_copy()

    def test_snapshot_and_state_round_trip_are_frozen_without_aliasing_current_stack_data(self):
        state=DemonstrateState('A','TRIGGER',self.source())
        encoded=state.to_dict();restored=DemonstrateState.from_dict(encoded)
        encoded['source']['stack_item']['targets'].append('D')
        self.assertEqual(['C'],restored.source.item().targets)
        self.assertEqual(state,restored)
        with self.assertRaises(FrozenInstanceError):restored.actor='D'

    def test_open_versions_false_copy_states_uncast_sources_and_original_reference_reuse_reject(self):
        state=DemonstrateState('A','TRIGGER',self.source())
        for changes in ({'schema_version':True},{'schema_version':2},{'actor':''},
                {'stage':'unowned_stage'},
                {'self_copy_ref':'PRECREATED'},{'extra':True}):
            with self.subTest(changes=changes),self.assertRaises((ValueError,TypeError)):
                DemonstrateState.from_dict({**state.to_dict(),**changes})
        with self.assertRaises(ValueError):DemonstrateState('A','TRIGGER',self.source('spell_copy'))


class DemonstrateCompilerContractTests(unittest.TestCase):
    reminder='When you cast this spell, you may copy it. If you do, choose an opponent to also copy it.'

    def test_complete_keyword_reminders_are_closed_and_grants_or_unconsumed_riders_reject(self):
        from quorune.compiler.demonstrate_nodes import is_demonstrate_line
        for text in ('Demonstrate','Demonstrate ('+self.reminder+')',
                'Demonstrate ('+self.reminder+' Players may choose new targets for their copies.)'):
            with self.subTest(text=text):self.assertTrue(is_demonstrate_line(text))
        for text in ('Demonstrate 2','Creatures you cast have demonstrate.',
                'Demonstrate ('+self.reminder.replace('may copy','copy')+')',
                'Demonstrate ('+self.reminder+') Draw a card.',
                'Demonstrate ('+self.reminder+' Each copy becomes a token.)'):
            with self.subTest(text=text):self.assertFalse(is_demonstrate_line(text))

    def test_source_span_actual_cast_condition_and_missing_owner_fail_closed(self):
        from quorune.compiler.demonstrate_nodes import demonstrate_keyword_node
        from quorune.compiler.ir_model import SourceSpan
        line='Demonstrate ('+self.reminder+')';span=SourceSpan(8,8+len(line),2);residuals=[]
        node=demonstrate_keyword_node(node_id='n2',line=line,material_line='Demonstrate',span=span,
            capability_registry=None,capability_profile='commander_review',residuals=residuals,mechanics=('demonstrate',))
        self.assertEqual(span,node.span);self.assertEqual(line,node.text)
        self.assertEqual(('spell.cast','stack'),(node.event,node.active_zone))
        self.assertTrue(node.lowerable);self.assertFalse(node.exact);self.assertTrue(residuals)
        self.assertEqual('$context.event_spell_copy_source',node.effects[0]['copy_source'])

    def test_independent_payload_and_program_minima_keep_intrinsic_source_scope(self):
        from types import SimpleNamespace
        from quorune.rules.demonstrate import demonstrate_instruction,demonstrate_condition,demonstrate_payload_dependencies,demonstrate_program_dependencies
        effect=demonstrate_instruction()
        self.assertIn('keyword.demonstrate.intrinsic',demonstrate_payload_dependencies(effect))
        self.assertIn('stack.copy.cast_event_spell',demonstrate_payload_dependencies(effect))
        program=SimpleNamespace(effects=(effect,),event='spell.cast',active_zone='stack',target_schema=None,event_condition=demonstrate_condition())
        self.assertEqual(demonstrate_payload_dependencies(effect),demonstrate_program_dependencies(program))
        for key,value in (('event','spell.cast_or_copy'),('active_zone','battlefield'),('event_condition',None)):
            with self.subTest(key=key),self.assertRaises(ValueError):
                demonstrate_program_dependencies(SimpleNamespace(**{**vars(program),key:value}))
        for changes in ({'player':'$event.controller'},{'copy_source':{}},{'extra':True},
                {'demonstrate':{'schema_version':True}}):
            with self.subTest(changes=changes),self.assertRaises(ValueError):
                demonstrate_payload_dependencies({**effect,**changes})


class DemonstrateCapturedSourceTests(unittest.TestCase):
    def fixture(self):
        from types import SimpleNamespace
        source=SimpleNamespace(zone='stack',ref='ORIGINAL',object_id='original-card',controller='A',
            logical_object_id='original-card@1')
        snapshot=DemonstrateStateTests().source()
        item=snapshot.item()
        host=SimpleNamespace(state=SimpleNamespace(stack=[item]))
        event={'card':'ORIGINAL','controller':'A','stack':'S1','logical_object_id':'original-card@1'}
        return source,snapshot,host,event

    def test_actual_source_capture_delegates_to_existing_owner_and_rejects_uncast_wrong_or_reincarnated_objects(self):
        from unittest.mock import patch
        from quorune.rules.demonstrate import capture_demonstrate_cast_source
        source,snapshot,host,event=self.fixture()
        with patch('quorune.rules.event_spell_copy.capture_event_spell_copy_source',return_value=snapshot) as capture:
            self.assertEqual(snapshot,capture_demonstrate_cast_source(host,source,event))
            capture.assert_called_once_with(host,host.state.stack[0])
        for changes in ({'controller':'B'},{'card':'OTHER'},{'stack':'MISSING'},
                {'logical_object_id':'original-card@2'}):
            with self.subTest(changes=changes),patch('quorune.rules.event_spell_copy.capture_event_spell_copy_source') as capture:
                with self.assertRaises(ValueError):capture_demonstrate_cast_source(host,source,{**event,**changes})
                capture.assert_not_called()
        host.state.stack[0].kind='spell_copy'
        with self.assertRaises(ValueError):capture_demonstrate_cast_source(host,source,event)

    def test_source_resolution_reuses_canonical_live_or_departure_snapshot_and_never_rewrites_copy_controller(self):
        from unittest.mock import patch
        from quorune.rules.demonstrate import resolve_demonstrate_source
        source,snapshot,host,event=self.fixture()
        state=DemonstrateState('B','TRIGGER',snapshot)
        with patch('quorune.rules.event_spell_copy.resolve_event_spell_copy_source',return_value=snapshot) as resolve:
            self.assertEqual(snapshot,resolve_demonstrate_source(host,state))
            resolve.assert_called_once_with(host,{'stack':'S1','copy_source':snapshot.to_dict()})
        self.assertEqual('B',state.actor);self.assertEqual('A',snapshot.item().controller)


class DemonstrateChoiceTests(unittest.TestCase):
    def fixture(self, actor='A', seats=('A','B','C','D')):
        from types import SimpleNamespace
        from quorune.rules.demonstrate import demonstrate_instruction
        source=DemonstrateStateTests().source()
        effect={**demonstrate_instruction(),'player':actor,'copy_source':source.to_dict(),'stack':source.item().ref}
        query=SimpleNamespace(seats=seats)
        context=SimpleNamespace(actor=actor,stack_ref='TRIGGER',query=query)
        return effect,context,query

    def test_decline_copies_nothing_and_accept_delegates_self_targets_before_mandatory_opponent(self):
        from types import SimpleNamespace
        from quorune.semantic_choices.demonstrate import prepare_demonstrate,complete_demonstrate
        effect,context,query=self.fixture()
        prepared=prepare_demonstrate(effect,context)
        self.assertEqual(['copy','decline'],prepared.request.choice.choice_schema()['legal_values'])
        continuation=SimpleNamespace(effect=prepared.continuation_effect,stack_ref='TRIGGER')
        declined=complete_demonstrate(continuation,{'choice':'decline'},query)
        self.assertEqual((),declined.intents);self.assertEqual((),declined.prepend_effects)
        accepted=complete_demonstrate(continuation,{'choice':'copy'},query)
        self.assertEqual((),accepted.intents)
        own,opponent=accepted.prepend_effects
        self.assertEqual(('copy_stack_item','A','S1'),(own['op'],own['player'],own['stack']))
        self.assertNotIn('targets',own)
        prepared_opponent=prepare_demonstrate(opponent,context)
        self.assertEqual(['B','C','D'],prepared_opponent.request.choice.choice_schema()['legal_values'])
        copied=complete_demonstrate(SimpleNamespace(effect=prepared_opponent.continuation_effect,
            stack_ref='TRIGGER'),{'choice':'C'},query)
        self.assertEqual(1,len(copied.prepend_effects))
        other,=copied.prepend_effects
        self.assertEqual(('copy_stack_item','C','S1'),(other['op'],other['player'],other['stack']))
        self.assertEqual(own['copy_source'],other['copy_source'])
        self.assertEqual('A',EventSpellCopySource.from_dict(other['copy_source']).item().controller)

    def test_copied_trigger_uses_current_controller_opponents_and_mandatory_current_choice(self):
        from types import SimpleNamespace
        from quorune.semantic_choices.demonstrate import prepare_demonstrate,complete_demonstrate
        from quorune.semantic_choices.model import SemanticChoiceError
        effect,context,query=self.fixture(actor='B')
        prepared=prepare_demonstrate(effect,context)
        accepted=complete_demonstrate(SimpleNamespace(effect=prepared.continuation_effect,stack_ref='TRIGGER'),{'choice':'copy'},query)
        own,opponent=accepted.prepend_effects
        self.assertEqual('B',own['player'])
        choice=prepare_demonstrate(opponent,context)
        self.assertEqual(['A','C','D'],choice.request.choice.choice_schema()['legal_values'])
        cont=SimpleNamespace(effect=choice.continuation_effect,stack_ref='TRIGGER')
        for value in ('B','decline','UNKNOWN',None):
            with self.subTest(value=value),self.assertRaises(SemanticChoiceError):
                complete_demonstrate(cont,{'choice':value},query)
        query.seats=('A','B','D')
        with self.assertRaises(SemanticChoiceError):complete_demonstrate(cont,{'choice':'C'},query)
        query.seats=('B',)
        self.assertIsNotNone(prepare_demonstrate(opponent,context).auto_continue)

    def test_source_actor_trigger_versions_and_unknown_resume_fields_reject(self):
        from quorune.semantic_choices.demonstrate import prepare_demonstrate
        from quorune.semantic_choices.model import SemanticChoiceError
        effect,context,query=self.fixture()
        for changes in ({'player':'B'},{'stack':'OTHER'},{'extra':True},
                {'demonstrate':{'schema_version':True}},{'demonstrate_state':{}},
                {'copy_source':None}):
            with self.subTest(changes=changes),self.assertRaises(SemanticChoiceError):
                prepare_demonstrate({**effect,**changes},context)
        self.assertEqual({'stack','operation'},set(prepare_demonstrate(effect,context).request.public_context))


from inherited_composition_fixture import InheritedNativeFixture

class DemonstrateNativeFixture(InheritedNativeFixture):
    @classmethod
    def setUpClass(cls):
        import json,tempfile
        from pathlib import Path
        from common import ROOT
        from quorune.carddb import CardDatabase
        from quorune.deck import DeckDefinition,DeckEntry
        from quorune.rules.capabilities import load_default_capability_registry
        from scripts.build_test_database import build_fixture_database
        cls.temporary=tempfile.TemporaryDirectory();root=Path(cls.temporary.name)
        base=json.loads((ROOT/'tests/fixtures/bound-effect-program-cards.json').read_text(encoding='utf-8'))
        base['cards']=[r for r in base['cards'] if r['name']=='Generic Bound Plains'];base['rulings']=[]
        path=root/'base.json';path.write_text(json.dumps(base),encoding='utf-8')
        build_fixture_database([path,ROOT/'tests/fixtures/demonstrate-cards.json'],root/'demonstrate.sqlite3')
        cls.db=CardDatabase(root/'demonstrate.sqlite3');cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Demonstrate',[DeckEntry('Isamaru, Hound of Konda',1,'commander'),
            DeckEntry('Generic Bound Plains',30)],['Isamaru, Hound of Konda'])


class DemonstrateNativeTests(DemonstrateNativeFixture,unittest.TestCase):
    def choose(self,s,seat,**response):
        from quorune.record import authoritative_state_hash
        before=authoritative_state_hash(s.state)
        foreign='D' if seat!='D' else 'A'
        wrong=s.act('pilot:'+foreign,{'action_id':'choose',**response})
        self.assertFalse(wrong.ok);self.assertEqual(before,authoritative_state_hash(s.state))
        result=s.act('pilot:'+seat,{'action_id':'choose',**response})
        self.assertTrue(result.ok,result.summary)

    def test_two_complete_originals_bind_normally_with_independent_fine_and_copy_minima(self):
        from dataclasses import replace
        from quorune.oracle_ir import compile_oracle_card
        from quorune.card_programs import bind_card_program_runtime
        from quorune.card_programs.binding import bind_semantic_program_runtime
        from quorune.card_programs.adapters import compile_best_available_card_program
        from quorune.semantics import SemanticRegistry
        from quorune.compiler.program_generation import register_generated_programs
        for name in ('Excavation Technique','Replication Technique'):
            card=self.db.lookup(name)
            ir=compile_oracle_card(card,capability_registry=self.registry,capability_profile='commander_review')
            self.assertEqual('exact',ir.status,(name,ir.material_residuals))
            semantics=SemanticRegistry()
            register_generated_programs(self.db,semantics,[card],capability_registry=self.registry,capability_profile='commander_review',
                promote_exact_runtime_handlers=True,promote_exact_trigger_programs=True,
                promote_exact_effect_programs=True,promote_exact_capability_declarations=True)
            program=compile_best_available_card_program(self.db,card,semantic_registry=semantics,
                capability_registry=self.registry,capability_profile='commander_review')
            result=bind_card_program_runtime(program,capability_registry=self.registry,profile='commander_review')
            self.assertTrue(result['strict_capability_ready'],(name,result['blockers']))
            trigger,=[p for p in semantics.programs_for_oracle(card.oracle_id)
                if any('demonstrate' in e for e in p.effects)]
            for removed in ('keyword.demonstrate.intrinsic','stack.copy.cast_event_spell'):
                dependencies=tuple(cap for cap in trigger.capability_dependencies if cap!=removed)
                changed=replace(trigger,capability_dependencies=dependencies,
                    capability_closure=self.registry.closure(dependencies,profile='commander_review').to_dict(),coverage=())
                binding=bind_semantic_program_runtime(changed,capability_registry=self.registry,profile='commander_review')
                self.assertFalse(binding['strict'],(removed,binding))

    def test_actual_paid_excavation_uses_principal_bound_targets_and_opponent_copy_resolves_first_privately_and_replays(self):
        import json
        s=self.session(702144101)
        source=self.add(s.engine,'Excavation Technique',zone='hand')
        victims=[self.add(s.engine,'Grizzly Bears',seat=seat,ref='demonstrate-victim-'+seat) for seat in 'BCD']
        secret=self.add(s.engine,'Counterspell',zone='hand',seat='D',ref='demonstrate-secret')
        self.assert_closed(s,source);self.prepare(s,mana={'C':3,'W':1});self.checkpoint(s)
        self.cast_for(s,'A',source,[victims[0].ref]);original=next(i for i in s.state.stack if i.kind=='spell')
        self.assertEqual(1,self.trigger_count(s,source))
        self.assertEqual('semantic.choice',self.resolve(s).kind)
        self.choose(s,'A',choice='copy');self.choose(s,'A',targets=[victims[1].ref])
        self.assertEqual(1,sum(i.kind=='spell_copy' for i in s.state.stack))
        self.choose(s,'A',choice='B');self.choose(s,'B',targets=[victims[2].ref])
        self.assertEqual([('A','spell'),('A','spell_copy'),('B','spell_copy')],[(i.controller,i.kind) for i in s.state.stack])
        self.assertEqual([victims[0].ref],original.targets)
        self.assertFalse(any(s.state.players['A'].mana_pool.values()))
        for viewer in 'ABC':self.assertNotIn(secret.ref,json.dumps(s.packet('pilot:'+viewer,full=True)))
        self.replay(s,load=True)
        self.resolve_top(s);self.assertEqual('graveyard',s.state.cards[victims[2].object_id].zone)
        self.assertEqual('battlefield',s.state.cards[victims[1].object_id].zone)
        self.resolve_top(s);self.assertEqual('graveyard',s.state.cards[victims[1].object_id].zone)
        self.assertIsNone(self.resolve(s));self.assertEqual('graveyard',s.state.cards[victims[0].object_id].zone)
        for seat in 'BCD':
            self.assertEqual(2,sum(c.controller==seat and c.zone=='battlefield' and c.is_token and c.printed_name=='Treasure Token' for c in s.state.cards.values()))
        self.assertFalse(s.state.stack);self.replay(s,load=True)

    def test_actual_paid_decline_produces_no_copy_or_opponent_choice_and_keeps_printed_effect(self):
        s=self.session(702144103);source=self.add(s.engine,'Replication Technique',zone='hand')
        target=self.add(s.engine,'Grizzly Bears',seat='A')
        self.assert_closed(s,source);self.prepare(s,mana={'C':4,'U':1});self.checkpoint(s)
        self.cast_for(s,'A',source,[target.ref]);self.assertEqual('semantic.choice',self.resolve(s).kind)
        self.choose(s,'A',choice='decline')
        self.assertEqual(['spell'],[i.kind for i in s.state.stack])
        self.assertIsNone(self.resolve(s));self.assertFalse(s.state.stack);self.replay(s,load=True)


    def test_actual_paid_replication_each_controller_chooses_current_targets_or_retains_original_and_replays(self):
        for retain in (False,True):
            with self.subTest(retain=retain):
                s=self.session(702144110+retain);source=self.add(s.engine,'Replication Technique',zone='hand')
                first=self.add(s.engine,'Grizzly Bears',ref='demonstrate-original-target')
                own=self.add(s.engine,'Forest',ref='demonstrate-own-target')
                other=self.add(s.engine,'Island',seat='B',ref='demonstrate-opponent-target')
                self.assert_closed(s,source);self.prepare(s,mana={'C':4,'U':1});self.checkpoint(s)
                self.cast_for(s,'A',source,[first.ref]);self.assertEqual('semantic.choice',self.resolve(s).kind)
                self.choose(s,'A',choice='copy');self.choose(s,'A',targets=[own.ref]);self.choose(s,'A',choice='B')
                if retain:self.choose(s,'B')
                else:self.choose(s,'B',targets=[other.ref])
                expected=first if retain else other
                self.assertEqual([expected.ref],s.state.stack[-1].targets)
                self.assertEqual('B',s.state.stack[-1].controller)
                self.assertEqual([own.ref],s.state.stack[-2].targets)
                self.replay(s,load=True);self.resolve_top(s)
                tokens=[c for c in s.state.cards.values() if c.is_token and c.zone=='battlefield']
                if retain:
                    # Keeping an original target is permitted while copying,
                    # but a permanent A controls is illegal for B at resolution.
                    self.assertEqual([],tokens)
                else:
                    self.assertEqual(1,len(tokens));self.assertEqual('B',tokens[0].controller)
                    self.assertEqual(expected.printed_name,s.engine._effective_card_data(tokens[0])['name'])
                self.assertIsNone(self.resolve(s));self.assertFalse(s.state.stack)
                self.assertEqual(2 if retain else 3,sum(c.is_token and c.zone=='battlefield' for c in s.state.cards.values()))
                self.replay(s,load=True)

    def test_actual_original_counter_keeps_demonstrate_copies_while_trigger_counter_keeps_only_original(self):
        for counter_trigger in (False,True):
            with self.subTest(counter_trigger=counter_trigger):
                s=self.session(702144120+counter_trigger);source=self.add(s.engine,'Replication Technique',zone='hand')
                target=self.add(s.engine,'Grizzly Bears')
                other=self.add(s.engine,'Forest',seat='B',ref='demonstrate-response-copy-target')
                counter=self.add(s.engine,'Stifle' if counter_trigger else 'Counterspell',seat='B',zone='hand')
                self.assert_closed(s,counter);self.prepare(s,mana={'C':4,'U':1})
                s.state.players['B'].mana_pool.update({'U':1 if counter_trigger else 2});self.checkpoint(s)
                self.cast_for(s,'A',source,[target.ref])
                item=next(i for i in s.state.stack if i.kind==('triggered_ability' if counter_trigger else 'spell')
                    and (i.source_object_id if counter_trigger else i.card_object_id)==source.object_id)
                self.cast_for(s,'B',counter,[item.ref]);self.resolve_top(s)
                if counter_trigger:
                    self.assertEqual(0,self.trigger_count(s,source));self.assertIsNone(self.resolve(s))
                    self.assertEqual(1,sum(c.is_token and c.zone=='battlefield' for c in s.state.cards.values()))
                else:
                    self.assertEqual('graveyard',s.state.cards[source.object_id].zone)
                    self.assertEqual('semantic.choice',self.resolve(s).kind)
                    self.choose(s,'A',choice='copy');self.choose(s,'A');self.choose(s,'A',choice='B');self.choose(s,'B',targets=[other.ref])
                    self.assertEqual(['spell_copy','spell_copy'],[i.kind for i in s.state.stack])
                    self.assertIsNone(self.resolve(s))
                    self.assertEqual(2,sum(c.is_token and c.zone=='battlefield' for c in s.state.cards.values()))
                self.assertFalse(s.state.stack);self.replay(s,load=True)


    def test_canonical_copied_demonstrate_trigger_uses_current_controller_and_keeps_original_snapshot(self):
        s=self.session(702144125);source=self.add(s.engine,'Replication Technique',zone='hand')
        original_target=self.add(s.engine,'Grizzly Bears')
        own=self.add(s.engine,'Forest',seat='B',ref='demonstrate-copied-trigger-own')
        other=self.add(s.engine,'Island',seat='C',ref='demonstrate-copied-trigger-opponent')
        self.prepare(s,mana={'C':4,'U':1});self.checkpoint(s);self.cast_for(s,'A',source,[original_target.ref])
        trigger=next(i for i in s.state.stack if i.kind=='triggered_ability')
        snapshot=trigger.context['event_spell_copy_source']
        # Exercise the canonical stack-copy boundary; no unsupported utility
        # card or forced support is introduced by this owner interaction.
        copied=s.engine._copy_stack_item(controller='B',target=trigger,targets=[],target_groups={},
            reason='Demonstrate copied-controller owner interaction')
        self.assertEqual(snapshot,copied.context['event_spell_copy_source']);self.checkpoint(s)
        self.assertEqual('semantic.choice',self.resolve(s).kind);self.assertEqual(['B'],s.state.pending_decision.actors)
        self.choose(s,'B',choice='copy');self.choose(s,'B',targets=[own.ref]);self.choose(s,'B',choice='C')
        self.choose(s,'C',targets=[other.ref])
        self.assertEqual([('B','spell_copy'),('C','spell_copy')],[(i.controller,i.kind) for i in s.state.stack[-2:]])
        self.assertEqual('semantic.choice',self.resolve(s).kind);self.choose(s,'A',choice='decline')
        self.assertIsNone(self.resolve(s));self.assertFalse(s.state.stack)
        self.assertEqual(3,sum(c.is_token and c.zone=='battlefield' for c in s.state.cards.values()))
        self.replay(s,load=True)


    def test_actual_post_self_copy_fault_rolls_back_before_opponent_choice_then_loads_and_retries(self):
        from unittest.mock import patch
        from quorune.errors import GameRuleError
        from quorune.record import authoritative_state_hash
        s=self.session(702144130);source=self.add(s.engine,'Replication Technique',zone='hand')
        target=self.add(s.engine,'Grizzly Bears');self.prepare(s,mana={'C':4,'U':1});self.checkpoint(s)
        self.cast_for(s,'A',source,[target.ref]);self.assertEqual('semantic.choice',self.resolve(s).kind)
        self.choose(s,'A',choice='copy');before=authoritative_state_hash(s.state)
        command={'action_id':'choose'};commit=s.engine._copy_stack_item;observed=[]
        def fault(*args,**kwargs):
            copied=commit(*args,**kwargs);observed.append(copied.ref)
            raise GameRuleError('Injected post Demonstrate self-copy fault')
        with patch.object(s.engine,'_copy_stack_item',side_effect=fault):
            result=s.act('pilot:A',command);self.assertFalse(result.ok,result.summary)
            self.assertIn('Injected post Demonstrate self-copy fault',result.summary)
        self.assertEqual(1,len(observed));self.assertEqual(before,authoritative_state_hash(s.state))
        self.assertFalse(any(i.kind=='spell_copy' for i in s.state.stack))
        s=self.replay(s,load=True);self.choose(s,'A');self.choose(s,'A',choice='B');self.choose(s,'B')
        self.assertIsNone(self.resolve(s));self.assertFalse(s.state.stack);self.replay(s,load=True)

    def test_fresh_native_wrong_opponent_copy_controller_mutant_is_killed_after_positive_original(self):
        from unittest.mock import patch
        import quorune.semantic_choices.demonstrate as owner
        method='test_actual_paid_excavation_uses_principal_bound_targets_and_opponent_copy_resolves_first_privately_and_replays'
        baseline=unittest.TestResult();type(self)(method).run(baseline)
        self.assertEqual((0,0),(len(baseline.failures),len(baseline.errors)))
        original=owner._copy_instruction
        def wrong(state,actor):return original(state,state.actor)
        result=unittest.TestResult()
        with patch.object(owner,'_copy_instruction',wrong):type(self)(method).run(result)
        self.assertEqual([],result.errors);self.assertEqual(1,len(result.failures))


class DemonstrateMutationTests(unittest.TestCase):
    def test_fresh_original_controller_copy_mutant_is_killed_by_copied_trigger_actor(self):
        from unittest.mock import patch
        import quorune.semantic_choices.demonstrate as owner
        witness=DemonstrateChoiceTests('test_copied_trigger_uses_current_controller_opponents_and_mandatory_current_choice')
        baseline=unittest.TestResult();witness.run(baseline)
        self.assertEqual((0,0),(len(baseline.failures),len(baseline.errors)))
        original=owner._copy_instruction
        def wrong(state,actor):return original(state,state.source.item().controller)
        result=unittest.TestResult()
        with patch.object(owner,'_copy_instruction',wrong):
            DemonstrateChoiceTests('test_copied_trigger_uses_current_controller_opponents_and_mandatory_current_choice').run(result)
        self.assertEqual([],result.errors);self.assertEqual(1,len(result.failures))


class DemonstrateRegistrationTests(DemonstrateNativeFixture,unittest.TestCase):
    def test_genuinely_verified_fine_and_cast_copy_withdrawals_block_both_originals_and_keep_vanilla(self):
        import json
        from copy import deepcopy
        from common import ROOT
        from quorune.oracle_ir import compile_oracle_card
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index,discover_tests
        raw=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        document=read_evidence_document(ROOT/'platform/capability-evidence-declarations.json')
        known=set(discover_tests(ROOT).values())
        for cap in ('keyword.demonstrate.intrinsic','stack.copy.cast_event_spell'):
            value=deepcopy(raw)
            next(row for row in value['capabilities'] if row['id']==cap).update(status='blocked',
                blockers=['Withdrawn Demonstrate or canonical cast-copy owner'])
            registry=CapabilityRegistry(value)
            index=build_index(registry_value=value,declaration_source=document,discovered_test_ids=known)
            registry.mark_evidence_verified(validate_capability_evidence_index(index,registry=registry))
            self.assertTrue(registry.evidence_fingerprint)
            for name in ('Excavation Technique','Replication Technique'):
                ir=compile_oracle_card(self.db.lookup(name),capability_registry=registry,capability_profile='commander_review')
                self.assertNotEqual('exact',ir.status,(cap,name))
                blockers={b for residual in ir.material_residuals for b in residual.blockers}
                self.assertIn('capability:status:'+cap+':blocked',blockers)
                self.assertFalse(any('unverified' in b for b in blockers))
            self.assertEqual('exact',compile_oracle_card(self.db.lookup('Grizzly Bears'),
                capability_registry=registry,capability_profile='commander_review').status)


class DemonstrateContinuationTests(unittest.TestCase):
    def test_typed_partial_continuation_round_trip_preserves_original_source_and_current_actor(self):
        import json
        from quorune.semantic_choices.defaults import default_semantic_choice_registry
        from quorune.semantic_choices.model import SemanticChoiceContinuation,SemanticChoiceFrame
        from quorune.semantic_choices.demonstrate import prepare_demonstrate,complete_demonstrate
        from quorune.replacement.immutable import FrozenMap
        from types import SimpleNamespace
        effect,context,query=DemonstrateChoiceTests().fixture(actor='B')
        prepared=prepare_demonstrate(effect,context)
        accepted=complete_demonstrate(SimpleNamespace(effect=prepared.continuation_effect,stack_ref='TRIGGER'),{'choice':'copy'},query)
        saved=prepare_demonstrate(accepted.prepend_effects[1],context)
        continuation=SemanticChoiceContinuation(handler_id='choice.public.objects-then.v1',handler_version=1,
            stack_ref='TRIGGER',effect=saved.continuation_effect,remaining=(),destination=None,note='Demonstrate',
            semantic_frame=SemanticChoiceFrame('fixture:demonstrate',1,'TRIGGER',2,'B'))
        registry=default_semantic_choice_registry()
        handler,restored=registry.decode_continuation(json.loads(json.dumps(continuation.to_dict())))
        result=handler.complete(restored,{'choice':'A'},query)
        copy,=result.prepend_effects
        self.assertEqual(('copy_stack_item','A','S1'),(copy['op'],copy['player'],copy['stack']))
        self.assertEqual('A',EventSpellCopySource.from_dict(copy['copy_source']).item().controller)
        self.assertEqual('B',restored.effect['demonstrate_state']['actor'])


class HealingTechniqueNativeTests(DemonstrateNativeFixture,unittest.TestCase):
    choose=DemonstrateNativeTests.choose

    def test_full_healing_original_and_existing_techniques_bind_normally_and_body_fine_owner_cannot_be_stripped(self):
        from dataclasses import replace
        from quorune.oracle_ir import compile_oracle_card
        from quorune.card_programs.binding import bind_semantic_program_runtime
        cap='quantity_expression.graveyard_target_mana_value'
        s=self.session(608202600)
        for name in ('Healing Technique','Excavation Technique','Replication Technique','Cremate'):
            record=self.db.lookup(name);ir=compile_oracle_card(record,capability_registry=self.registry,capability_profile='commander_review')
            self.assertEqual('exact',ir.status,(name,ir.material_residuals))
            card=self.add(s.engine,name,zone='hand');self.assert_closed(s,card)
            if name!='Healing Technique':continue
            body,=[p for p in s.engine.semantics.programs_for_oracle(card.oracle_id) if cap in p.capability_dependencies]
            deps=tuple(d for d in body.capability_dependencies if d!=cap)
            changed=replace(body,capability_dependencies=deps,coverage=(),capability_closure=self.registry.closure(deps,profile='commander_review').to_dict())
            bound=bind_semantic_program_runtime(changed,capability_registry=self.registry,profile='commander_review')
            self.assertFalse(bound['strict']);self.assertIn('capability:undeclared_runtime_dependency:'+cap,bound['blockers'])

    def test_actual_paid_healing_decline_returns_card_gains_intrinsic_mana_value_and_self_exiles_with_private_load_replay(self):
        import json
        for name,amount in (('Island',0),('Grizzly Bears',2)):
            with self.subTest(name=name):
                s=self.session(608202610+amount);source=self.add(s.engine,'Healing Technique',zone='hand')
                returned=self.add(s.engine,name,zone='graveyard',ref='healing-returned-card')
                secret=self.add(s.engine,'Counterspell',seat='D',zone='hand',ref='healing-unrelated-secret')
                self.assert_closed(s,source);self.prepare(s,mana={'C':3,'G':1});self.checkpoint(s)
                before=s.state.players['A'].life;self.cast_for(s,'A',source,[returned.ref])
                self.assertEqual('semantic.choice',self.resolve(s).kind);self.choose(s,'A',choice='decline')
                s=self.replay(s,load=True);self.assertIsNone(self.resolve(s))
                self.assertEqual('hand',s.state.cards[returned.object_id].zone)
                self.assertEqual(before+amount,s.state.players['A'].life)
                self.assertEqual('exile',s.state.cards[source.object_id].zone)
                self.assertFalse(any(s.state.players['A'].mana_pool.values()))
                for viewer in 'ABC':self.assertNotIn(secret.ref,json.dumps(s.packet('pilot:'+viewer,full=True)))
                self.replay(s,load=True)

    def test_actual_paid_healing_demonstrate_copies_use_each_controllers_graveyard_and_life_then_exile_only_original_with_replay(self):
        import json
        s=self.session(608202620);source=self.add(s.engine,'Healing Technique',zone='hand')
        original=self.add(s.engine,'Island',zone='graveyard',ref='healing-original-target')
        own=self.add(s.engine,'Grizzly Bears',zone='graveyard',ref='healing-own-copy-target')
        other=self.add(s.engine,'Sliver Hivelord',seat='B',zone='graveyard',ref='healing-opponent-copy-target')
        secret=self.add(s.engine,'Counterspell',seat='D',zone='hand',ref='healing-copy-private')
        self.assert_closed(s,source);self.prepare(s,mana={'C':3,'G':1});self.checkpoint(s)
        self.cast_for(s,'A',source,[original.ref]);self.assertEqual('semantic.choice',self.resolve(s).kind)
        self.choose(s,'A',choice='copy');self.choose(s,'A',targets=[own.ref]);self.choose(s,'A',choice='B')
        self.choose(s,'B',targets=[other.ref]);self.assertEqual(2,sum(i.kind=='spell_copy' for i in s.state.stack))
        s=self.replay(s,load=True);self.resolve_top(s)
        self.assertEqual((40,45),(s.state.players['A'].life,s.state.players['B'].life))
        self.assertEqual('hand',s.state.cards[other.object_id].zone)
        self.assertEqual('stack',s.state.cards[source.object_id].zone)
        self.assertIsNone(self.resolve(s));self.assertEqual((42,45),(s.state.players['A'].life,s.state.players['B'].life))
        self.assertTrue(all(s.state.cards[c.object_id].zone=='hand' for c in (original,own,other)))
        self.assertEqual('exile',s.state.cards[source.object_id].zone)
        for viewer in 'ABC':self.assertNotIn(secret.ref,json.dumps(s.packet('pilot:'+viewer,full=True)))
        self.replay(s,load=True)


    def test_actual_countered_healing_or_cremated_target_keeps_paid_price_and_skips_all_life_and_self_exile_with_replay(self):
        for mode in ('counter','target'):
            with self.subTest(mode=mode):
                s=self.session(608202700+len(mode));source=self.add(s.engine,'Healing Technique',zone='hand')
                target=self.add(s.engine,'Grizzly Bears',zone='graveyard',ref='healing-lost-target')
                response=self.add(s.engine,'Counterspell' if mode=='counter' else 'Cremate',seat='B',zone='hand')
                s.state.players['B'].mana_pool.update({'U':2,'B':1})
                for card in (source,response):self.assert_closed(s,card)
                self.prepare(s,mana={'C':3,'G':1});self.checkpoint(s);self.cast_for(s,'A',source,[target.ref])
                self.assertEqual('semantic.choice',self.resolve(s).kind);self.choose(s,'A',choice='decline')
                target_ref=next(i.ref for i in s.state.stack if i.kind=='spell' and i.card_object_id==source.object_id) if mode=='counter' else target.ref
                self.cast_for(s,'B',response,[target_ref]);s=self.replay(s,load=True);self.assertIsNone(self.resolve(s))
                self.assertEqual(40,s.state.players['A'].life);self.assertEqual('graveyard',s.state.cards[source.object_id].zone)
                self.assertEqual('graveyard' if mode=='counter' else 'exile',s.state.cards[target.object_id].zone)
                self.assertFalse(any(s.state.players['A'].mana_pool.values()));self.replay(s,load=True)

    def test_actual_retained_copy_targets_revalidate_against_each_controller_and_repeated_target_before_original_with_replay(self):
        for retained in ('self','opponent'):
            with self.subTest(retained=retained):
                s=self.session(608202710+len(retained));source=self.add(s.engine,'Healing Technique',zone='hand')
                original=self.add(s.engine,'Grizzly Bears',zone='graveyard',ref='healing-shared-target')
                own=self.add(s.engine,'Sliver Hivelord',zone='graveyard',ref='healing-fresh-own-target')
                other=self.add(s.engine,'Sliver Hivelord',seat='B',zone='graveyard',ref='healing-fresh-opponent-target')
                self.prepare(s,mana={'C':3,'G':1});self.checkpoint(s);self.cast_for(s,'A',source,[original.ref])
                self.assertEqual('semantic.choice',self.resolve(s).kind);self.choose(s,'A',choice='copy')
                if retained=='self':self.choose(s,'A')
                else:self.choose(s,'A',targets=[own.ref])
                self.choose(s,'A',choice='B')
                if retained=='opponent':self.choose(s,'B')
                else:self.choose(s,'B',targets=[other.ref])
                s=self.replay(s,load=True);self.assertIsNone(self.resolve(s))
                self.assertEqual((42,45) if retained=='self' else (47,40),(s.state.players['A'].life,s.state.players['B'].life))
                self.assertEqual('graveyard' if retained=='self' else 'exile',s.state.cards[source.object_id].zone)
                self.assertEqual('hand',s.state.cards[original.object_id].zone)
                self.assertEqual('graveyard' if retained=='self' else 'hand',s.state.cards[own.object_id].zone)
                self.assertEqual('graveyard' if retained=='opponent' else 'hand',s.state.cards[other.object_id].zone)
                self.replay(s,load=True)

    def test_actual_post_return_life_and_self_exile_fault_rolls_back_full_state_then_loads_and_retries_once(self):
        from unittest.mock import patch
        from quorune.errors import GameRuleError
        from quorune.record import authoritative_state_hash
        from quorune.zone_transitions import ZoneTransitionOwner
        s=self.session(608202730);source=self.add(s.engine,'Healing Technique',zone='hand')
        target=self.add(s.engine,'Grizzly Bears',zone='graveyard',ref='healing-rollback-target')
        self.prepare(s,mana={'C':3,'G':1});self.checkpoint(s);self.cast_for(s,'A',source,[target.ref])
        self.assertEqual('semantic.choice',self.resolve(s).kind);self.choose(s,'A',choice='decline')
        original=ZoneTransitionOwner.move_card;seen=[];failed=None;before=None
        def fault(owner,object_id,destination,**kwargs):
            moved=original(owner,object_id,destination,**kwargs)
            if object_id==source.object_id and destination=='exile':
                seen.append((owner.state.cards[target.object_id].zone,owner.state.players['A'].life,moved.zone))
                raise GameRuleError('Injected post-Healing result fault')
            return moved
        with patch.object(ZoneTransitionOwner,'move_card',fault):
            for _ in range(16):
                actor=s.pending_principals()[0];before=authoritative_state_hash(s.state);result=s.act(actor,{'action_id':'pass'})
                if not result.ok:
                    self.assertIn('Injected post-Healing',result.summary);failed=actor;break
            else:self.fail('Printed terminal exile fault was not reached')
        self.assertEqual([('hand',42,'exile')],seen);self.assertEqual(before,authoritative_state_hash(s.state))
        self.assertEqual('graveyard',s.state.cards[target.object_id].zone);self.assertEqual(40,s.state.players['A'].life)
        self.assertEqual('stack',s.state.cards[source.object_id].zone)
        s=self.replay(s,load=True);result=s.act(failed,{'action_id':'pass'});self.assertTrue(result.ok,result.summary)
        self.assertIsNone(self.resolve(s));self.assertEqual(42,s.state.players['A'].life)
        self.assertEqual('hand',s.state.cards[target.object_id].zone);self.assertEqual('exile',s.state.cards[source.object_id].zone)
        self.replay(s,load=True)

    def test_fresh_native_zero_quantity_mutant_is_killed_after_positive_actual_paid_original(self):
        from unittest.mock import patch
        import quorune.semantic_runtime.values as owner
        method='test_actual_paid_healing_decline_returns_card_gains_intrinsic_mana_value_and_self_exiles_with_private_load_replay'
        baseline=unittest.TestResult();type(self)(method).run(baseline)
        self.assertEqual((0,0),(len(baseline.failures),len(baseline.errors)))
        original=owner.resolve_scalar_effect_amount
        def zero(host,value,item):
            return 0 if value.get('origin')=='target_graveyard_mana_value' else original(host,value,item)
        mutated=unittest.TestResult()
        with patch.object(owner,'resolve_scalar_effect_amount',zero):type(self)(method).run(mutated)
        self.assertEqual([],mutated.errors);self.assertEqual(1,len(mutated.failures))


    def test_actual_life_gain_doubling_uses_canonical_replacement_after_return_and_keeps_terminal_exile_and_replay(self):
        s=self.session(608202750);source=self.add(s.engine,'Healing Technique',zone='hand')
        target=self.add(s.engine,'Grizzly Bears',zone='graveyard',ref='healing-doubled-target')
        replacement=self.add(s.engine,'Boon Reflection')
        for card in (source,replacement):self.assert_closed(s,card)
        self.prepare(s,mana={'C':3,'G':1});self.checkpoint(s);self.cast_for(s,'A',source,[target.ref])
        self.assertEqual('semantic.choice',self.resolve(s).kind);self.choose(s,'A',choice='decline')
        s=self.replay(s,load=True);self.assertIsNone(self.resolve(s))
        self.assertEqual(44,s.state.players['A'].life);self.assertEqual('hand',s.state.cards[target.object_id].zone)
        self.assertEqual('exile',s.state.cards[source.object_id].zone);self.replay(s,load=True)

    def test_actual_commander_hand_destination_replacement_loads_then_gains_original_graveyard_mana_value_and_self_exiles(self):
        from quorune.record import authoritative_state_hash
        s=self.session(608202760);source=self.add(s.engine,'Healing Technique',zone='hand')
        commander_id=s.state.players['A'].zones['command'][0];commander=s.state.cards[commander_id]
        s.engine.move_card(commander_id,'graveyard',reason='Prepared actual commander return boundary')
        s.engine.permissions.invalidate_current();s.state.pending_decision=None
        self.assertTrue(s.engine._stabilize());self.assertEqual('state.commander_zone',s.state.pending_decision.kind)
        kept=s.act('pilot:A',{'action_id':'remain'});self.assertTrue(kept.ok,kept.summary)
        self.prepare(s,mana={'C':3,'G':1});self.checkpoint(s);self.cast_for(s,'A',source,[commander.ref])
        self.assertEqual('semantic.choice',self.resolve(s).kind);self.choose(s,'A',choice='decline')
        self.assertEqual('replacement.order',self.resolve(s).kind);self.assertEqual(40,s.state.players['A'].life)
        self.assertEqual('graveyard',s.state.cards[commander_id].zone);s=self.replay(s,load=True)
        options=s.packet('pilot:A',full=True)['decision']['ctx']['options'];offered,=[r for r in options if not r.get('decline')]
        before=authoritative_state_hash(s.state)
        wrong=s.act('pilot:B',{'action_id':'choose','replacement':offered['id']});self.assertFalse(wrong.ok)
        self.assertEqual(before,authoritative_state_hash(s.state))
        chosen=s.act('pilot:A',{'action_id':'choose','replacement':offered['id']});self.assertTrue(chosen.ok,chosen.summary)
        self.assertIsNone(self.resolve(s));self.assertEqual('command',s.state.cards[commander_id].zone)
        self.assertEqual(41,s.state.players['A'].life);self.assertEqual('exile',s.state.cards[source.object_id].zone)
        self.replay(s,load=True)


class HealingTechniqueWithdrawalTests(DemonstrateNativeFixture,unittest.TestCase):
    def test_genuine_verified_six_owner_withdrawals_block_complete_healing_and_keep_old_techniques_and_unrelated_controls(self):
        import json
        from copy import deepcopy
        from common import ROOT
        from quorune.oracle_ir import compile_oracle_card
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index,discover_tests
        raw=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        doc=read_evidence_document(ROOT/'platform/capability-evidence-declarations.json');known=set(discover_tests(ROOT).values())
        for cap in ('quantity_expression.graveyard_target_mana_value','quantity_expression.scalar_effect_amount',
                'card.return.own_graveyard_to_owner_hand','target.revalidate_resolution','zone.exile.resolving_spell','life.change.effect'):
            value=deepcopy(raw);next(r for r in value['capabilities'] if r['id']==cap).update(status='blocked',blockers=['Healing quantity or independent canonical owner withdrawn'])
            registry=CapabilityRegistry(value);index=build_index(registry_value=value,declaration_source=doc,discovered_test_ids=known)
            registry.mark_evidence_verified(validate_capability_evidence_index(index,registry=registry));self.assertTrue(registry.evidence_fingerprint)
            ir=compile_oracle_card(self.db.lookup('Healing Technique'),capability_registry=registry,capability_profile='commander_review')
            self.assertNotEqual('exact',ir.status,cap)
            blockers={b for r in ir.material_residuals for b in r.blockers};self.assertIn('capability:status:'+cap+':blocked',blockers)
            self.assertFalse(any('unverified' in b for b in blockers))
            for name in ('Grizzly Bears','Island'):
                self.assertEqual('exact',compile_oracle_card(self.db.lookup(name),capability_registry=registry,capability_profile='commander_review').status,(cap,name))
            if cap!='target.revalidate_resolution':
                for name in ('Excavation Technique','Replication Technique','Cremate'):
                    self.assertEqual('exact',compile_oracle_card(self.db.lookup(name),capability_registry=registry,capability_profile='commander_review').status,(cap,name))
