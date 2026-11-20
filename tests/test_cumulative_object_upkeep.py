"""Pinned CR702.24: whole repeated object costs after committed age counters."""
from dataclasses import replace
from copy import deepcopy
from types import SimpleNamespace
import unittest
from quorune.cumulative_object_upkeep import CumulativeObjectUpkeepSpec,compile_cumulative_object_upkeep
from quorune.rules.cumulative_object_upkeep import CONTROLLER_UPKEEP,cumulative_object_upkeep_runtime_dependencies

class CumulativeObjectUpkeepModelTests(unittest.TestCase):
    def test_closed_costs_roundtrip_and_keep_historical_wrapper_versions_rejected(self):
        for text,kind in (('Cumulative upkeep—Sacrifice a creature.','sacrifice'),('Cumulative upkeep—Sacrifice a land.','sacrifice'),('Cumulative upkeep—Discard a card.','discard')):
            spec=compile_cumulative_object_upkeep(text);self.assertEqual(kind,spec.payment.kind)
            self.assertEqual(spec,CumulativeObjectUpkeepSpec.from_dict(spec.to_dict()));self.assertEqual(spec,CumulativeObjectUpkeepSpec.from_effect(spec.effect()))
            for version in (True,1,2,3,4,5,6,8):
                with self.assertRaises(ValueError):CumulativeObjectUpkeepSpec.from_effect({**spec.effect(),'schema_version':version})
        for text in ('Cumulative upkeep {1}','Cumulative upkeep—Pay 1 life.','Cumulative upkeep—Sacrifice another creature.','Cumulative upkeep—Discard a card, then draw a card.','Cumulative upkeep—Sacrifice a creature or land.'):
            self.assertIsNone(compile_cumulative_object_upkeep(text))

    def test_repeat_count_is_current_committed_age_with_no_partial_descriptor(self):
        spec=compile_cumulative_object_upkeep('Cumulative upkeep—Sacrifice a creature.')
        self.assertIsNone(spec.repeated_payment(0));self.assertEqual(3,spec.repeated_payment(3).amount);self.assertEqual(1,spec.payment.amount)
        for age in (True,-1,1.0,'2'):
            with self.assertRaises(ValueError):spec.repeated_payment(age)
        for mutate in (lambda:replace(spec.payment,amount=2),lambda:replace(spec.payment,predicate=replace(spec.payment.predicate,owner='$controller')),lambda:replace(spec.payment,predicate=replace(spec.payment.predicate,exclude_ref='$source.zone_object'))):
            with self.assertRaises(ValueError):CumulativeObjectUpkeepSpec(mutate())

    def test_payload_minimum_requires_real_controller_upkeep_without_annotations(self):
        spec=compile_cumulative_object_upkeep('Cumulative upkeep—Discard a card.')
        base=dict(event='step.begin',active_zone='battlefield',target_schema=None,event_condition=CONTROLLER_UPKEEP,effects=[spec.effect()])
        self.assertIn('counter.producer.cumulative_upkeep_fixed_objects',cumulative_object_upkeep_runtime_dependencies(SimpleNamespace(**base)))
        for change in ({'event':'spell.cast'},{'active_zone':'graveyard'},{'event_condition':None},{'target_schema':{'kind':'any_target'}}):
            with self.assertRaises(ValueError):cumulative_object_upkeep_runtime_dependencies(SimpleNamespace(**{**base,**change}))
        for changes in ({'player':'B'},{'source':'different'},{'extra':True}):
            with self.assertRaises(ValueError):CumulativeObjectUpkeepSpec.from_effect({**spec.effect(),**changes})

    def test_age_is_placed_before_any_payment_offer_and_zero_can_pay_or_decline(self):
        from quorune.semantic_choices.cumulative_object_upkeep import prepare_cumulative_object_upkeep,complete_zero_age_upkeep
        spec=compile_cumulative_object_upkeep('Cumulative upkeep—Sacrifice a creature.')
        source=SimpleNamespace(ref='C1',logical_object_id='source@1',phased_out=False,controller='A',counters={})
        query=SimpleNamespace(active_seats=('A','B'),object=lambda *a,**k:source)
        context=SimpleNamespace(actor='A',stack_controller='A',stack_label='Upkeep',source_ref='C1',source_logical_object_id='source@1',query=query)
        effect=spec.effect();effect['player']='A';effect['source']='C1';effect['cumulative_payment']['payment_per_age']['predicate']['controller']='A'
        first=prepare_cumulative_object_upkeep(effect,context);self.assertIsNone(first.request);self.assertEqual('age',first.preparation_intents[0].counter_name);self.assertEqual(1,first.preparation_intents[0].amount)
        self.assertEqual({},source.counters);second=prepare_cumulative_object_upkeep(first.auto_continue.prepend_effects[0],context)
        self.assertEqual((True,False),second.request.choice.legal_values)
        continuation=SimpleNamespace(effect=second.continuation_effect,semantic_frame=SimpleNamespace(controller='A'))
        self.assertFalse(complete_zero_age_upkeep(continuation,{'pay':True},query).intents)
        self.assertFalse(complete_zero_age_upkeep(continuation,{'action_id':'choose','pay':True},query).intents)
        self.assertEqual(('C1',),complete_zero_age_upkeep(continuation,{'pay':False},query).intents[0].object_refs)
        for bad in ({'pay':1},{'pay':True,'cards':[]}):
            with self.assertRaises(ValueError):complete_zero_age_upkeep(continuation,bad,query)

    def test_fresh_age_multiplier_omission_is_killed(self):
        from unittest.mock import patch
        spec=compile_cumulative_object_upkeep('Cumulative upkeep—Sacrifice a land.')
        def witness():self.assertEqual(3,spec.repeated_payment(3).amount)
        witness()
        with patch.object(CumulativeObjectUpkeepSpec,'repeated_payment',return_value=spec.payment),self.assertRaises(AssertionError):witness()

    def test_repeated_cost_uses_explicit_twelve_and_preserves_legacy_sacrifice_one(self):
        from quorune.fixed_effect_payment import FixedEffectPaymentSpec
        from quorune.object_predicate import ObjectQuerySpec
        base=FixedEffectPaymentSpec('sacrifice',predicate=ObjectQuerySpec(zones=('battlefield',),controller='A',types_all=('creature',)))
        with self.assertRaises(ValueError):replace(base,amount=2)
        repeated=replace(base,schema_version=12,amount=3)
        self.assertEqual(repeated,FixedEffectPaymentSpec.from_dict(repeated.to_dict()))
        self.assertIn('cost.object.repeated_resolution',repeated.capabilities)
        for changes in ({'amount':True},{'amount':0},{'requirements':{}},{'schema_version':True},{'predicate':replace(repeated.predicate,owner='B')},{'predicate':replace(repeated.predicate,exclude_ref='C1')}):
            with self.assertRaises(ValueError):replace(repeated,**changes)


class CumulativeObjectUpkeepCompilerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import tempfile
        from pathlib import Path
        from common import ROOT
        from quorune.carddb import CardDatabase
        from quorune.rules.capabilities import load_default_capability_registry
        from scripts.build_test_database import build_fixture_database
        cls.temporary=tempfile.TemporaryDirectory();path=Path(cls.temporary.name)/'cumulative-objects-compiler.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/cumulative-object-upkeep-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
    @classmethod
    def tearDownClass(cls):cls.db.close();cls.temporary.cleanup()

    def test_all_three_full_originals_close_normally_with_unannotated_runtime_minimum(self):
        from quorune.oracle_ir import compile_oracle_card,register_generated_programs
        from quorune.semantics import SemanticRegistry
        from quorune.card_programs.adapters import compile_best_available_card_program
        from quorune.card_programs.binding import bind_card_program_runtime,bind_semantic_program_runtime
        semantics=SemanticRegistry();cards=[self.db.lookup(n,fuzzy=False) for n in ('Phyrexian Soulgorger','Polar Kraken','Vexing Sphinx')]
        register_generated_programs(self.db,semantics,cards,trust_level='provisional',capability_registry=self.registry,capability_profile='commander_review',promote_exact_runtime_handlers=True,promote_exact_trigger_programs=True,promote_exact_effect_programs=True,promote_exact_capability_declarations=True)
        for card in cards:
            with self.subTest(card=card.name):
                ir=compile_oracle_card(card,capability_registry=self.registry,capability_profile='commander_review');self.assertEqual('exact',ir.status,ir.material_residuals)
                p=compile_best_available_card_program(self.db,card,semantic_registry=semantics,capability_registry=self.registry,capability_profile='commander_review');b=bind_card_program_runtime(p,capability_registry=self.registry,profile='commander_review');self.assertTrue(b['strict_capability_ready'],b['blockers'])
                trigger=next(p for p in semantics.programs_for_oracle(card.oracle_id) if any(e.get('schema_version')==7 and e.get('op')=='offer_optional_mana_payment' for e in p.effects))
                for cap in ('counter.producer.cumulative_upkeep_fixed_objects','cost.object.repeated_resolution'):
                    deps=tuple(c for c in trigger.capability_dependencies if c!=cap)
                    changed=replace(trigger,coverage=(),handlers=(),capability_dependencies=deps,capability_closure=self.registry.closure(deps,profile='commander_review').to_dict())
                    result=bind_semantic_program_runtime(changed,capability_registry=self.registry,profile='commander_review');self.assertFalse(result['strict']);self.assertTrue(any(cap in x for x in result['blockers']),result['blockers'])

    def test_genuine_object_upkeep_or_repeated_payment_withdrawal_keeps_old_mana_life_and_draw(self):
        import json
        from common import ROOT
        from quorune.oracle_ir import compile_oracle_card
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index,discover_tests
        from test_cumulative_upkeep_counter_placement import cumulative_record
        raw=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding='utf-8'));declarations=read_evidence_document(ROOT/'platform/capability-evidence-declarations.json');known=set(discover_tests(ROOT).values())
        for cap in ('counter.producer.cumulative_upkeep_fixed_objects','cost.object.repeated_resolution'):
            changed=deepcopy(raw);next(r for r in changed['capabilities'] if r['id']==cap).update(status='blocked',blockers=['object upkeep owner withdrawn'])
            registry=CapabilityRegistry(changed);index=build_index(registry_value=changed,declaration_source=declarations,discovered_test_ids=known);registry.mark_evidence_verified(validate_capability_evidence_index(index,registry=registry))
            self.assertNotEqual('exact',compile_oracle_card(self.db.lookup('Phyrexian Soulgorger'),capability_registry=registry,capability_profile='commander_review').status)
            for text in ('Cumulative upkeep {1}','Cumulative upkeep—Pay 1 life.'):
                self.assertEqual('exact',compile_oracle_card(cumulative_record(text,41),capability_registry=registry,capability_profile='commander_review').status)
            self.assertEqual('exact',compile_oracle_card(self.db.lookup('Memnite'),capability_registry=registry,capability_profile='commander_review').status)


from inherited_composition_fixture import InheritedNativeFixture
import test_target_recipient_results as recipients

class CumulativeObjectUpkeepNativeTests(InheritedNativeFixture,unittest.TestCase):
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
        cls.temporary=tempfile.TemporaryDirectory();path=Path(cls.temporary.name)/'cumulative-objects-native.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/cumulative-object-upkeep-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Object cumulative upkeep',[DeckEntry('Sliver Hivelord',1,'commander'),DeckEntry('Generic Bound Plains',30)],['Sliver Hivelord'])

    def world(self,seed,name='Phyrexian Soulgorger',*,cast=False,age=0):
        from quorune.model import TurnEntry
        s=self.session(seed);source=self.add(s.engine,name,zone='hand' if cast else 'battlefield');source.counters['age']=age
        private=self.add(s.engine,'Counterspell',seat='C',zone='hand',ref='object-upkeep-private')
        s.state.current_turn=TurnEntry('object-upkeep-initial-D','D');s.state.last_normal_turn_player='D'
        self.assert_closed(s,source);return s,source,private

    def begin(self,s):
        self.prepare(s,actor='D',active='D');self.checkpoint(s)
        self.until(s,lambda ss:ss.state.active_player=='A' and ss.state.phase=='beginning' and ss.state.step=='upkeep')
        decision=self.resolve(s);self.assertEqual('semantic.choice',decision.kind);self.assertEqual(['A'],decision.actors)

    def choose(self,s,refs):
        command={'action_id':'choose','cards':list(refs)};self.reject(s,'B',command)
        result=s.act('pilot:A',command);self.assertTrue(result.ok,result.summary);self.assertIsNone(self.resolve(s))

    def test_actual_three_cast_originals_pay_own_creature_land_and_private_hand_costs(self):
        import json
        for offset,name in enumerate(('Phyrexian Soulgorger','Polar Kraken','Vexing Sphinx')):
            with self.subTest(card=name):
                s,source,private=self.world(70224100+offset,name,cast=True);self.add(s.engine,'Vedalken Orrery')
                card=self.add(s.engine,'Island' if name=='Polar Kraken' else 'Memnite',zone='hand' if name=='Vexing Sphinx' else 'battlefield',ref='object-upkeep-own-'+str(offset))
                foreign=self.add(s.engine,'Island' if name=='Polar Kraken' else 'Memnite',seat='B',zone='hand' if name=='Vexing Sphinx' else 'battlefield',ref='object-upkeep-foreign-'+str(offset))
                self.prepare(s,actor='D',active='D');s.state.players['A'].mana_pool.update({'C':8,'U':3});self.checkpoint(s)
                self.cast_for(s,'A',source);self.assertIsNone(self.resolve(s));self.assertEqual('battlefield',s.state.cards[source.object_id].zone)
                if name=='Polar Kraken':self.assertTrue(s.state.cards[source.object_id].tapped)
                self.until(s,lambda ss:ss.state.active_player=='A' and ss.state.phase=='beginning' and ss.state.step=='upkeep')
                self.assertEqual('semantic.choice',self.resolve(s).kind);self.assertEqual(1,s.state.cards[source.object_id].counters.get('age'))
                self.reject(s,'A',{'action_id':'choose','cards':[foreign.ref]})
                if name=='Vexing Sphinx':self.assertNotIn(card.ref,json.dumps(s.packet('pilot:B',full=True)))
                s=self.replay(s,load=True);self.choose(s,[card.ref]);self.assertEqual('graveyard',s.state.cards[card.object_id].zone)
                self.assertEqual('battlefield',s.state.cards[source.object_id].zone);self.finish(s,private)

    def test_actual_replaced_age_requires_two_distinct_creatures_and_rejects_partial_payment(self):
        s,source,private=self.world(70224110);self.add(s.engine,'Doubling Season')
        first=self.add(s.engine,'Memnite');second=self.add(s.engine,'Grizzly Bears');foreign=self.add(s.engine,'Memnite',seat='B',ref='upkeep-foreign-creature')
        self.begin(s);self.assertEqual(2,s.state.cards[source.object_id].counters.get('age'))
        for refs in ([first.ref],[first.ref,first.ref],[first.ref,foreign.ref]):self.reject(s,'A',{'action_id':'choose','cards':refs})
        s=self.replay(s,load=True);self.choose(s,[first.ref,second.ref]);self.assertEqual(['graveyard','graveyard'],[s.state.cards[c.object_id].zone for c in (first,second)])
        self.assertEqual('battlefield',s.state.cards[source.object_id].zone);self.assertEqual('battlefield',s.state.cards[foreign.object_id].zone);self.finish(s,private)

    def test_actual_age_prevention_still_offers_zero_payment_or_decline(self):
        for pay in (True,False):
            with self.subTest(pay=pay):
                s,source,private=self.world(70224120+int(pay));self.add(s.engine,'Solemnity');self.begin(s)
                self.assertEqual(0,s.state.cards[source.object_id].counters.get('age',0))
                command={'action_id':'choose','pay':pay};self.reject(s,'D',command);self.reject(s,'A',{'action_id':'choose','pay':1})
                s=self.replay(s,load=True);result=s.act('pilot:A',command);self.assertTrue(result.ok,result.summary);self.assertIsNone(self.resolve(s))
                self.assertEqual('battlefield' if pay else 'graveyard',s.state.cards[source.object_id].zone);self.finish(s,private)

    def test_actual_source_can_pay_its_own_entire_creature_cost(self):
        s,source,private=self.world(70224130);other=self.add(s.engine,'Grizzly Bears');self.begin(s);self.choose(s,[source.ref])
        self.assertEqual('graveyard',s.state.cards[source.object_id].zone);self.assertEqual('battlefield',s.state.cards[other.object_id].zone);self.finish(s,private)

    def test_actual_sphinx_decline_draws_for_its_committed_age_counters_on_death(self):
        s,source,private=self.world(70224140,'Vexing Sphinx',age=1);self.begin(s)
        hand=len(s.state.players['A'].zones['hand']);library=len(s.state.players['A'].zones['library'])
        self.assertEqual(2,s.state.cards[source.object_id].counters.get('age'));self.choose(s,[])
        self.assertEqual('graveyard',s.state.cards[source.object_id].zone);self.assertEqual(hand+2,len(s.state.players['A'].zones['hand']))
        self.assertEqual(library-2,len(s.state.players['A'].zones['library']));self.finish(s,private)

    def test_actual_whole_payment_fault_rolls_back_both_objects_and_retries_same_choice(self):
        from unittest.mock import patch
        from quorune.semantic_choices.intent_host import SemanticChoiceIntentHostMixin
        from quorune.errors import GameRuleError
        from quorune.record import authoritative_state_hash
        s,source,private=self.world(70224150);self.add(s.engine,'Doubling Season');first=self.add(s.engine,'Memnite');second=self.add(s.engine,'Grizzly Bears');self.begin(s)
        original=SemanticChoiceIntentHostMixin.move_objects_simultaneously_intent;calls=[]
        def fault(host,intent):
            result=original(host,intent);calls.append(True);raise GameRuleError('Repeated payment rollback witness')
        command={'action_id':'choose','cards':[first.ref,second.ref]};before=authoritative_state_hash(s.state)
        with patch.object(SemanticChoiceIntentHostMixin,'move_objects_simultaneously_intent',fault):
            result=s.act('pilot:A',command);self.assertFalse(result.ok);self.assertEqual(before,authoritative_state_hash(s.state))
        self.assertTrue(calls);self.assertEqual(['battlefield','battlefield'],[s.state.cards[c.object_id].zone for c in (first,second)])
        self.choose(s,[first.ref,second.ref]);self.finish(s,private)

    def test_actual_bounced_recast_source_is_not_charged_by_old_upkeep_occurrence(self):
        s,source,private=self.world(70224160);self.add(s.engine,'Vedalken Orrery')
        bounce=self.add(s.engine,'Unsummon',seat='B',zone='hand');self.add(s.engine,'Island',seat='B')
        for i in range(3):self.add(s.engine,'Island',ref='upkeep-recast-island-'+str(i))
        self.prepare(s,actor='D',active='D');self.checkpoint(s);old=source.logical_object_id
        self.until(s,lambda ss:ss.state.active_player=='A' and ss.state.phase=='beginning' and ss.state.step=='upkeep')
        self.cast_for(s,'B',bounce,[source.ref]);self.resolve_top(s);self.cast_for(s,'A',source);self.resolve_top(s)
        self.assertNotEqual(old,s.state.cards[source.object_id].logical_object_id);self.assertIsNone(self.resolve(s))
        self.assertEqual(0,s.state.cards[source.object_id].counters.get('age',0));self.assertEqual('battlefield',s.state.cards[source.object_id].zone);self.finish(s,private)

    def test_fresh_native_age_multiplier_omission_is_killed(self):
        from unittest.mock import patch
        with patch.object(CumulativeObjectUpkeepSpec,'repeated_payment',lambda spec,age:spec.payment if age else None):
            result=unittest.TestResult();type(self)('test_actual_replaced_age_requires_two_distinct_creatures_and_rejects_partial_payment').run(result)
            self.assertFalse(result.errors,result.errors);self.assertTrue(result.failures,'Age multiplier omission was not detected by actual cost choices')
