"""CR603.8: versioned global state subscriptions and own-cast payment."""
from copy import deepcopy
from dataclasses import replace
import unittest
from types import SimpleNamespace
from quorune.state_trigger_model import PublicStateTriggerSpec
from quorune.compiler.state_trigger_nodes import _state_condition
from quorune.characteristic_fragments import CharacteristicQuantityScope
from quorune.object_predicate import ObjectQuerySpec
from inherited_composition_fixture import InheritedNativeFixture
import test_target_recipient_results as recipients
from quorune.cast_source_sacrifice_payment import CastSourceSacrificePaymentSpec,cast_source_sacrifice_spec,cast_source_sacrifice_runtime_dependencies

class GlobalEmptyStateModelTests(unittest.TestCase):
    def test_global_codec_two_preserves_legacy_one_and_its_rejection_boundary(self):
        condition=_state_condition('there are no lands on the battlefield','Boundary')
        spec=PublicStateTriggerSpec(condition,schema_version=2)
        self.assertEqual(spec,PublicStateTriggerSpec.from_condition(spec.event_condition()))
        self.assertEqual(2,spec.to_dict()['schema_version'])
        with self.assertRaises(ValueError):PublicStateTriggerSpec(condition)
        old=_state_condition('you control no creatures','Boundary')
        self.assertEqual(1,PublicStateTriggerSpec(old).to_dict()['schema_version'])
        with self.assertRaises(ValueError):PublicStateTriggerSpec(old,schema_version=2)

    def test_global_query_rejects_changed_zone_owner_count_and_source_exclusion(self):
        condition=_state_condition('there are no lands on the battlefield','Boundary')
        mutations=[lambda:replace(condition,amount=1),lambda:replace(condition,quantity=replace(condition.quantity,exclude_source=True)),
            lambda:replace(condition,quantity=replace(condition.quantity,scope=CharacteristicQuantityScope.CONTROLLER_ZONE)),
            lambda:replace(condition,quantity=replace(condition.quantity,query=ObjectQuerySpec(zones=('battlefield',),types_all=('land',),owner='$controller')))]
        for mutate in mutations:
            with self.assertRaises(ValueError):PublicStateTriggerSpec(mutate(),schema_version=2)

    def test_cast_payment_is_explicit_wrapper_six_with_sealed_original_and_unchanged_cost_codec(self):
        spec=CastSourceSacrificePaymentSpec('land');effect=spec.effect()
        self.assertEqual(spec,cast_source_sacrifice_spec(effect));self.assertEqual(6,effect['schema_version'])
        self.assertEqual(2,effect['payment']['schema_version'])
        for changes in ({'schema_version':True},{'schema_version':2},{'player':'B'},{'source_stack':'other'},
                {'effects':[{'op':'draw','player':'$controller','count':1}]},
                {'declined_effects':[{'op':'counter_stack_target','stack':'other'}]}):
            with self.assertRaises(ValueError):cast_source_sacrifice_spec({**effect,**changes})

    def test_payload_keeps_payment_and_cast_authority_without_annotations(self):
        effect=CastSourceSacrificePaymentSpec('land').effect()
        base=dict(event='spell.cast',active_zone='stack',target_schema=None,event_condition={'field':'card','op':'eq','value':'$source.ref'})
        self.assertIn('choice.payment.cast_source_sacrifice',cast_source_sacrifice_runtime_dependencies(SimpleNamespace(**base),effect))
        for changes in ({'event':'resolve'},{'active_zone':'battlefield'},{'event_condition':None}):
            with self.assertRaises(ValueError):cast_source_sacrifice_runtime_dependencies(SimpleNamespace(**{**base,**changes}),effect)

    def test_global_subscription_uses_existing_pending_source_incarnation_suppression(self):
        from quorune.rules.state_triggers import state_trigger_available
        from quorune.state_trigger_model import STATE_TRIGGER_EVENT,STATE_TRIGGER_IDENTITY
        from quorune.semantics import SemanticProgram
        from quorune.source_maintenance import SourceMaintenanceSpec
        from quorune.model import StackItem
        spec=PublicStateTriggerSpec(_state_condition('there are no lands on the battlefield','Boundary'),schema_version=2)
        program=SemanticProgram(key='global-state',label='Global state',ability_id='trigger:front:n1',active_zone='battlefield',event=STATE_TRIGGER_EVENT,event_condition=spec.event_condition(),effects=[SourceMaintenanceSpec().effect()])
        source=SimpleNamespace(zone='battlefield',phased_out=False,object_id='source',logical_object_id='source@1')
        host=SimpleNamespace(state=SimpleNamespace(stack=[],pending_trigger_batches=[]),_fixed_public_state_condition_holds=lambda *_:True)
        self.assertTrue(state_trigger_available(host,source,program))
        item=StackItem('id:S1','S1','triggered_ability','A','State',source_object_id='source',semantic_key=program.key,context={STATE_TRIGGER_IDENTITY:'source@1'})
        host.state.stack.append(item);self.assertFalse(state_trigger_available(host,source,program))
        source.logical_object_id='source@2';self.assertTrue(state_trigger_available(host,source,program))


    def test_original_counter_reference_validation_omission_is_killed(self):
        from unittest.mock import patch
        import quorune.cast_source_sacrifice_payment as payment
        effect=payment.CastSourceSacrificePaymentSpec('land').effect()
        effect['declined_effects']=[{'op':'counter_stack_target','stack':'different'}]
        program=SimpleNamespace(event='spell.cast',active_zone='stack',target_schema=None,event_condition={'field':'card','op':'eq','value':'$source.ref'})
        def witness():
            with self.assertRaises(ValueError):payment.cast_source_sacrifice_runtime_dependencies(program,effect)
        witness()
        with patch.object(payment,'cast_source_sacrifice_spec',return_value=payment.CastSourceSacrificePaymentSpec('land')),self.assertRaises(AssertionError):witness()

class GlobalEmptyStateCompilerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import tempfile
        from pathlib import Path
        from common import ROOT
        from quorune.carddb import CardDatabase
        from quorune.rules.capabilities import load_default_capability_registry
        from scripts.build_test_database import build_fixture_database
        cls.temporary=tempfile.TemporaryDirectory();path=Path(cls.temporary.name)/'global-empty-state-compiler.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/global-empty-state-trigger-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
    @classmethod
    def tearDownClass(cls):cls.db.close();cls.temporary.cleanup()

    def test_actual_original_closes_all_three_material_abilities_with_data_derived_minima(self):
        from quorune.oracle_ir import compile_oracle_card,register_generated_programs
        from quorune.semantics import SemanticRegistry
        from quorune.card_programs.adapters import compile_best_available_card_program
        from quorune.card_programs.binding import bind_card_program_runtime,bind_semantic_program_runtime
        card=self.db.lookup('Mana Vortex',fuzzy=False)
        ir=compile_oracle_card(card,capability_registry=self.registry,capability_profile='commander_review')
        self.assertEqual('exact',ir.status,ir.material_residuals);self.assertEqual(3,len(ir.faces[0].nodes))
        semantics=SemanticRegistry();register_generated_programs(self.db,semantics,[card],trust_level='provisional',capability_registry=self.registry,capability_profile='commander_review',promote_exact_runtime_handlers=True,promote_exact_trigger_programs=True,promote_exact_effect_programs=True,promote_exact_capability_declarations=True)
        program=compile_best_available_card_program(self.db,card,semantic_registry=semantics,capability_registry=self.registry,capability_profile='commander_review')
        bound=bind_card_program_runtime(program,capability_registry=self.registry,profile='commander_review');self.assertTrue(bound['strict_capability_ready'],bound['blockers'])
        for event,cap in (('spell.cast','choice.payment.cast_source_sacrifice'),('state.condition','trigger.state.fixed_public_condition')):
            trigger=next(p for p in semantics.programs_for_oracle(card.oracle_id) if p.event==event)
            deps=tuple(c for c in trigger.capability_dependencies if c!=cap)
            altered=replace(trigger,coverage=(),handlers=(),capability_dependencies=deps,capability_closure=self.registry.closure(deps,profile='commander_review').to_dict())
            result=bind_semantic_program_runtime(altered,capability_registry=self.registry,profile='commander_review')
            self.assertFalse(result['strict']);self.assertTrue(any(cap in b for b in result['blockers']),result['blockers'])

    def test_genuine_payment_or_state_owner_withdrawal_rejects_original_and_preserves_controls(self):
        import json
        from common import ROOT
        from quorune.oracle_ir import compile_oracle_card
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index,discover_tests
        raw=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        declarations=read_evidence_document(ROOT/'platform/capability-evidence-declarations.json');known=set(discover_tests(ROOT).values())
        for cap in ('choice.payment.cast_source_sacrifice','trigger.state.fixed_public_condition'):
            changed=deepcopy(raw);next(r for r in changed['capabilities'] if r['id']==cap).update(status='blocked',blockers=['global state/cast-payment owner withdrawn'])
            registry=CapabilityRegistry(changed);index=build_index(registry_value=changed,declaration_source=declarations,discovered_test_ids=known)
            registry.mark_evidence_verified(validate_capability_evidence_index(index,registry=registry))
            self.assertNotEqual('exact',compile_oracle_card(self.db.lookup('Mana Vortex'),capability_registry=registry,capability_profile='commander_review').status)
            for name in ('Memnite','Counterspell'):
                self.assertEqual('exact',compile_oracle_card(self.db.lookup(name),capability_registry=registry,capability_profile='commander_review').status)

class GlobalEmptyStateNativeTests(InheritedNativeFixture,unittest.TestCase):
    reject=recipients.TargetRecipientNativeTests.reject
    finish=recipients.TargetRecipientNativeTests.finish
    @classmethod
    def setUpClass(cls):
        import tempfile
        from pathlib import Path
        from common import ROOT
        from quorune.carddb import CardDatabase
        from quorune.deck import DeckDefinition,DeckEntry
        from quorune.rules.capabilities import load_default_capability_registry
        from scripts.build_test_database import build_fixture_database
        cls.temporary=tempfile.TemporaryDirectory();path=Path(cls.temporary.name)/'global-empty-state.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/global-empty-state-trigger-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Global empty-land state',[DeckEntry('Sliver Hivelord',1,'commander'),DeckEntry('Generic Bound Plains',30)],['Sliver Hivelord'])

    def start_cast(self,s,source):
        self.assert_closed(s,source);self.cast_for(s,'A',source)
        original=next(i.ref for i in s.state.stack if i.kind=='spell' and i.card_object_id==source.object_id)
        decision=self.resolve(s);self.assertEqual('semantic.choice',decision.kind)
        self.assertEqual(['A'],decision.actors);return original

    def payment(self,s,cards):
        command={'action_id':'choose','cards':[card.ref for card in cards]};self.reject(s,'B',command)
        result=s.act('pilot:A',command);self.assertTrue(result.ok,result.summary)

    def state_refs(self,s,source):
        return [i.ref for i in s.state.stack if i.source_object_id==source.object_id and i.context.get('event')=='state.condition']

    def test_actual_cast_payment_sacrifices_only_one_land_or_counters_original(self):
        for pay in (False,True):
            with self.subTest(pay=pay):
                s=self.session(6038100+pay);source=self.add(s.engine,'Mana Vortex',zone='hand')
                lands=[self.add(s.engine,'Island',ref='vortex-cast-island-'+str(i)) for i in range(3)]
                wrong=self.add(s.engine,'Memnite');private=self.add(s.engine,'Counterspell',seat='C',zone='hand',ref='vortex-cast-private')
                self.prepare(s);self.checkpoint(s);self.start_cast(s,source)
                self.reject(s,'A',{'action_id':'choose','cards':[wrong.ref]})
                self.reject(s,'A',{'action_id':'choose','cards':[lands[0].ref,lands[1].ref]})
                self.payment(s,[lands[0]] if pay else []);self.assertIsNone(self.resolve(s))
                self.assertEqual('battlefield' if pay else 'graveyard',s.state.cards[source.object_id].zone)
                self.assertEqual('graveyard' if pay else 'battlefield',s.state.cards[lands[0].object_id].zone)
                self.assertEqual('battlefield',s.state.cards[wrong.object_id].zone);self.finish(s,private)

    def empty_world(self,seed):
        s=self.session(seed);source=self.add(s.engine,'Mana Vortex',zone='hand');land=self.add(s.engine,'Island')
        private=self.add(s.engine,'Counterspell',seat='C',zone='hand',ref='vortex-state-private')
        self.prepare(s,mana={'U':1,'C':1});self.checkpoint(s);original=self.start_cast(s,source);self.payment(s,[land])
        self.until(s,lambda ss:not any(i.ref==original for i in ss.state.stack) and ss.state.cards[source.object_id].zone=='battlefield')
        self.assertEqual(1,len(self.state_refs(s,source)));return s,source,private

    def test_actual_zero_land_state_queues_once_then_sacrifices_source(self):
        s,source,private=self.empty_world(6038110);ref=self.state_refs(s,source)[0]
        for _ in range(2):
            result=s.act(s.pending_principals()[0],{'action_id':'pass'});self.assertTrue(result.ok,result.summary)
            self.assertEqual([ref],self.state_refs(s,source))
        self.assertIsNone(self.resolve(s));self.assertEqual('graveyard',s.state.cards[source.object_id].zone);self.finish(s,private)

    def test_actual_countered_state_occurrence_retriggers_without_duplicate_pending_instance(self):
        s=self.session(6038120);source=self.add(s.engine,'Mana Vortex',zone='hand');land=self.add(s.engine,'Island')
        counter=self.add(s.engine,'Stifle',seat='B',zone='hand');private=self.add(s.engine,'Counterspell',seat='C',zone='hand',ref='vortex-retrigger-private')
        self.prepare(s,mana={'U':1,'C':1});s.state.players['B'].mana_pool['U']=1;self.checkpoint(s)
        original=self.start_cast(s,source);self.payment(s,[land]);self.until(s,lambda ss:not any(i.ref==original for i in ss.state.stack) and ss.state.cards[source.object_id].zone=='battlefield')
        old=self.state_refs(s,source)[0];self.cast_for(s,'B',counter,[old]);self.resolve_top(s)
        refs=self.state_refs(s,source);self.assertEqual(1,len(refs));self.assertNotEqual(old,refs[0])
        self.assertIsNone(self.resolve(s));self.assertEqual('graveyard',s.state.cards[source.object_id].zone);self.finish(s,private)

    def test_actual_late_land_does_not_recheck_already_triggered_state_condition(self):
        s=self.session(6038130);source=self.add(s.engine,'Mana Vortex',zone='hand');land=self.add(s.engine,'Island')
        atlas=self.add(s.engine,'Walking Atlas',seat='B');self.add(s.engine,'Fervor',seat='B');arriving=self.add(s.engine,'Forest',seat='B',zone='hand')
        private=self.add(s.engine,'Counterspell',seat='C',zone='hand',ref='vortex-late-land-private')
        self.assert_closed(s,atlas);self.prepare(s,mana={'U':1,'C':1});self.checkpoint(s)
        original=self.start_cast(s,source);self.payment(s,[land]);self.until(s,lambda ss:not any(i.ref==original for i in ss.state.stack) and ss.state.cards[source.object_id].zone=='battlefield')
        old=self.state_refs(s,source)[0];self.priority(s,'B')
        action=next(a for a in s.packet('pilot:B',full=True)['decision']['ctx']['legal']['actions'] if a['id']=='activate:'+atlas.ref+':ab1')
        result=s.act('pilot:B',{'action_id':action['id'],'pay':'auto'});self.assertTrue(result.ok,result.summary)
        self.assertEqual('semantic.choice',self.resolve(s).kind);command={'action_id':'choose','card':arriving.ref};self.reject(s,'A',command)
        result=s.act('pilot:B',command);self.assertTrue(result.ok,result.summary)
        self.until(s,lambda ss:ss.state.cards[arriving.object_id].zone=='battlefield');self.assertIn(old,self.state_refs(s,source))
        self.assertIsNone(self.resolve(s));self.assertEqual('graveyard',s.state.cards[source.object_id].zone)
        self.assertEqual('battlefield',s.state.cards[arriving.object_id].zone);self.finish(s,private)

    def test_actual_cast_trigger_survives_original_countered_then_declines_without_extra_sacrifice(self):
        s=self.session(6038140);source=self.add(s.engine,'Mana Vortex',zone='hand')
        lands=[self.add(s.engine,'Island',ref='vortex-gone-island-'+str(i)) for i in range(3)]
        counter=self.add(s.engine,'Counterspell',seat='B',zone='hand',ref='vortex-opponent-counter')
        private=self.add(s.engine,'Counterspell',seat='C',zone='hand',ref='vortex-original-gone-private')
        self.prepare(s);s.state.players['B'].mana_pool['U']=2;self.checkpoint(s);self.assert_closed(s,source);self.cast_for(s,'A',source)
        original=next(i.ref for i in s.state.stack if i.kind=='spell' and i.card_object_id==source.object_id)
        self.cast_for(s,'B',counter,[original]);self.resolve_top(s);self.assertEqual('graveyard',s.state.cards[source.object_id].zone)
        self.assertEqual('semantic.choice',self.resolve(s).kind);self.payment(s,[]);self.assertIsNone(self.resolve(s))
        self.assertEqual(['battlefield']*3,[s.state.cards[c.object_id].zone for c in lands]);self.finish(s,private)

    def test_actual_selected_payment_fault_rolls_back_land_stack_priority_and_retries(self):
        from unittest.mock import patch
        from quorune.semantic_choices.intent_host import SemanticChoiceIntentHostMixin
        from quorune.errors import GameRuleError
        from quorune.record import authoritative_state_hash
        s=self.session(6038150);source=self.add(s.engine,'Mana Vortex',zone='hand')
        lands=[self.add(s.engine,'Island',ref='vortex-fault-island-'+str(i)) for i in range(3)]
        private=self.add(s.engine,'Counterspell',seat='C',zone='hand',ref='vortex-fault-private')
        self.prepare(s);self.checkpoint(s);self.start_cast(s,source);original=SemanticChoiceIntentHostMixin.move_objects_simultaneously_intent;calls=[]
        def fault(host,intent):
            result=original(host,intent);calls.append(True);raise GameRuleError('cast payment rollback witness')
        command={'action_id':'choose','cards':[lands[0].ref]};before=authoritative_state_hash(s.state)
        with patch.object(SemanticChoiceIntentHostMixin,'move_objects_simultaneously_intent',fault):
            result=s.act('pilot:A',command);self.assertFalse(result.ok);self.assertEqual(before,authoritative_state_hash(s.state))
        self.assertTrue(calls);self.assertEqual('battlefield',s.state.cards[lands[0].object_id].zone)
        self.assertTrue(s.act('pilot:A',command).ok);self.assertIsNone(self.resolve(s));self.assertEqual('battlefield',s.state.cards[source.object_id].zone);self.finish(s,private)

    def test_fresh_counter_consequence_omission_is_killed_by_actual_decline(self):
        from unittest.mock import patch
        from quorune.semantic_runtime.stack_counter_handlers import CounterStackTargetHandler
        from quorune.semantic_runtime.intents import IntentPlan
        with patch.object(CounterStackTargetHandler,'lower',return_value=IntentPlan(operation='counter_stack_target',handler_id='generic.counter-stack-target.v1',intents=(),result_shape='by_player')):
            result=unittest.TestResult();type(self)('test_actual_cast_payment_sacrifices_only_one_land_or_counters_original').run(result)
            self.assertFalse(result.errors,result.errors);self.assertTrue(result.failures,'Declining paid no cost and failed to counter the original without detection')

    def test_actual_opponent_last_land_prevents_state_until_its_upkeep_sacrifice(self):
        s=self.session(6038160);source=self.add(s.engine,'Mana Vortex',zone='hand');own=self.add(s.engine,'Island')
        foreign=self.add(s.engine,'Island',seat='B',ref='vortex-opponent-last-land')
        private=self.add(s.engine,'Counterspell',seat='C',zone='hand',ref='vortex-upkeep-private')
        self.prepare(s,mana={'U':1,'C':1});self.checkpoint(s);self.start_cast(s,source);self.payment(s,[own])
        self.assertIsNone(self.resolve(s));self.assertEqual('battlefield',s.state.cards[source.object_id].zone)
        self.assertEqual([],self.state_refs(s,source));self.assertEqual('battlefield',s.state.cards[foreign.object_id].zone)
        self.until(s,lambda ss:ss.state.active_player=='B' and ss.state.phase=='beginning' and ss.state.step=='upkeep')
        decision=self.resolve(s);self.assertEqual('choice.apnap',decision.kind);self.assertEqual(['B'],decision.actors)
        command={'action_id':'choose','cards':[foreign.ref]};self.reject(s,'A',command)
        result=s.act('pilot:B',command);self.assertTrue(result.ok,result.summary)
        self.assertIsNone(self.resolve(s));self.assertEqual('graveyard',s.state.cards[foreign.object_id].zone)
        self.assertEqual('graveyard',s.state.cards[source.object_id].zone);self.finish(s,private)
