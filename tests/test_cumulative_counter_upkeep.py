"""Independent CR702.24 per-cost choices and noncollapsed counter placements."""
from dataclasses import replace
import unittest
from quorune.cumulative_counter_upkeep import CounterPlacementCostSpec,CumulativeCounterUpkeepSpec,compile_counter_cumulative_upkeep

class CumulativeCounterUpkeepModelTests(unittest.TestCase):
    def test_two_pinned_cost_scopes_roundtrip_in_explicit_new_wrapper(self):
        for text,scope in (('Cumulative upkeep—Put a -1/-1 counter on this creature.','source'),('Cumulative upkeep—Put a +1/+1 counter on a creature an opponent controls.','opponent_creature')):
            spec=compile_counter_cumulative_upkeep(text);self.assertEqual(scope,spec.cost.recipient_scope);self.assertEqual(spec,CumulativeCounterUpkeepSpec.from_effect(spec.effect()))
            self.assertEqual(spec.cost,CounterPlacementCostSpec.from_dict(spec.cost.to_dict()))
            for version in (True,1,2,3,4,5,6,7,9):
                with self.assertRaises(ValueError):CumulativeCounterUpkeepSpec.from_effect({**spec.effect(),'schema_version':version})

    def test_same_recipient_is_legal_per_cost_without_collapsing_replacement_events(self):
        spec=CounterPlacementCostSpec('+1/+1','opponent_creature')
        self.assertEqual((('C1','+1/+1',1),('C1','+1/+1',1),('C2','+1/+1',1)),spec.placements(['C1','C1','C2']))
        self.assertEqual((),spec.placements([]))
        for bad in ('C1',[None],['']):
            with self.assertRaises(ValueError):spec.placements(bad)

    def test_malformed_descriptor_and_unrepresented_costs_fail_closed(self):
        spec=CounterPlacementCostSpec('-1/-1','source')
        for change in ({'schema_version':True},{'schema_version':2},{'counter_name':'loyalty'},{'recipient_scope':'target_creature'}):
            with self.assertRaises(ValueError):replace(spec,**change)
        for text in ('Cumulative upkeep—Put two +1/+1 counters on a creature an opponent controls.','Cumulative upkeep—Put a +1/-1 counter on this creature.','Cumulative upkeep—Put a +1/+1 counter on target creature.','Cumulative upkeep—Put a +1/+1 counter on a creature an opponent controls. Draw a card.'):
            self.assertIsNone(compile_counter_cumulative_upkeep(text))


class CumulativeCounterUpkeepChoiceTests(unittest.TestCase):
    def context(self,*,scope='opponent_creature',age=0,allowed=True):
        from types import SimpleNamespace
        source=SimpleNamespace(ref='S1',logical_object_id='S1@1',zone='battlefield',phased_out=False,controller='A',types=('creature',),counters={'age':age})
        target=SimpleNamespace(ref='C1',logical_object_id='C1@1',zone='battlefield',phased_out=False,controller='B',types=('creature',),keywords=('Shroud','Hexproof'),counters={})
        other=SimpleNamespace(ref='C2',logical_object_id='C2@1',zone='battlefield',phased_out=False,controller='C',types=('creature',),counters={})
        rows=[source,target,other]
        query=SimpleNamespace(active_seats=('A','B','C','D'),objects=lambda **kw:tuple(rows),object=lambda ref,**kw:next((r for r in rows if r.ref==ref),None),counter_placement_allowed=lambda *a,**kw:allowed)
        context=SimpleNamespace(actor='A',stack_controller='A',source_ref='S1',source_logical_object_id='S1@1',stack_label='Counter upkeep',query=query)
        cost=CounterPlacementCostSpec('-1/-1' if scope=='source' else '+1/+1',scope)
        effect=CumulativeCounterUpkeepSpec(cost).effect();effect.update(player='A',source='S1')
        return context,effect,source,target,other

    def continuation(self,prepared):
        from types import SimpleNamespace
        return SimpleNamespace(effect=prepared.continuation_effect,semantic_frame=SimpleNamespace(controller='A'))

    def test_age_placement_precedes_sealed_price_and_source_cost_is_one_nongenerated_batch(self):
        from quorune.semantic_choices.cumulative_counter_upkeep import prepare_cumulative_counter_upkeep,complete_cumulative_counter_upkeep
        context,effect,source,*_=self.context(scope='source')
        first=prepare_cumulative_counter_upkeep(effect,context)
        self.assertEqual('age',first.preparation_intents[0].counter_name);self.assertTrue(first.preparation_intents[0].effect_generated)
        self.assertEqual(0,source.counters['age']);source.counters['age']=2
        offered=prepare_cumulative_counter_upkeep(first.auto_continue.prepend_effects[0],context)
        paid=complete_cumulative_counter_upkeep(self.continuation(offered),{'action_id':'choose','pay':True},context.query)
        self.assertEqual(1,len(paid.intents));self.assertEqual(('S1','S1'),paid.intents[0].object_refs)
        self.assertEqual(1,paid.intents[0].amount);self.assertFalse(paid.intents[0].effect_generated)

    def test_per_age_choices_repeat_same_or_different_opponent_without_partial_payment(self):
        from quorune.semantic_choices.cumulative_counter_upkeep import prepare_cumulative_counter_upkeep,complete_cumulative_counter_upkeep
        for second in ('C1','C2'):
            context,effect,source,target,other=self.context(age=2);effect['stage']='pay'
            first=prepare_cumulative_counter_upkeep(effect,context)
            self.assertIn('C1',first.request.choice.legal_refs);self.assertNotIn('S1',first.request.choice.legal_refs)
            self.assertEqual(1,first.request.choice.maximum)
            selected=complete_cumulative_counter_upkeep(self.continuation(first),{'cards':['C1']},context.query)
            self.assertFalse(selected.intents);self.assertIsNotNone(selected.repeat_effect);self.assertEqual({},target.counters)
            source.counters['age']=5
            final=prepare_cumulative_counter_upkeep(selected.repeat_effect,context)
            self.assertEqual(2,final.request.public_context['age_counters'])
            paid=complete_cumulative_counter_upkeep(self.continuation(final),{'cards':[second]},context.query)
            self.assertEqual(('C1',second),paid.intents[0].object_refs);self.assertEqual(1,paid.intents[0].amount)
            self.assertFalse(paid.intents[0].effect_generated)

    def test_declining_after_one_proposal_pays_no_counter_cost(self):
        from quorune.semantic_choices.cumulative_counter_upkeep import prepare_cumulative_counter_upkeep,complete_cumulative_counter_upkeep
        context,effect,source,target,_=self.context(age=2);effect['stage']='pay'
        first=prepare_cumulative_counter_upkeep(effect,context)
        selected=complete_cumulative_counter_upkeep(self.continuation(first),{'cards':['C1']},context.query)
        final=prepare_cumulative_counter_upkeep(selected.repeat_effect,context)
        declined=complete_cumulative_counter_upkeep(self.continuation(final),{'cards':[]},context.query)
        self.assertEqual(('S1',),declined.intents[0].object_refs);self.assertEqual('graveyard',declined.intents[0].destination)
        self.assertEqual({},target.counters)

    def test_counter_prohibition_rejects_payment_but_zero_price_still_can_be_paid(self):
        from quorune.semantic_choices.cumulative_counter_upkeep import prepare_cumulative_counter_upkeep,complete_cumulative_counter_upkeep
        context,effect,source,*_=self.context(scope='source',age=1,allowed=False);effect['stage']='pay'
        blocked=prepare_cumulative_counter_upkeep(effect,context);self.assertEqual((False,),blocked.request.choice.legal_values)
        with self.assertRaises(ValueError):complete_cumulative_counter_upkeep(self.continuation(blocked),{'pay':True},context.query)
        source.counters['age']=0;free=prepare_cumulative_counter_upkeep(effect,context)
        self.assertEqual((True,False),free.request.choice.legal_values)
        self.assertFalse(complete_cumulative_counter_upkeep(self.continuation(free),{'pay':True},context.query).intents)

    def test_stale_recipient_or_changed_original_source_seal_rejects_without_placement(self):
        from quorune.semantic_choices.cumulative_counter_upkeep import prepare_cumulative_counter_upkeep,complete_cumulative_counter_upkeep
        context,effect,source,target,_=self.context(age=1);effect['stage']='pay';offered=prepare_cumulative_counter_upkeep(effect,context)
        target.logical_object_id='C1@2'
        with self.assertRaises(ValueError):complete_cumulative_counter_upkeep(self.continuation(offered),{'cards':['C1']},context.query)
        source.logical_object_id='S1@2'
        with self.assertRaises(ValueError):prepare_cumulative_counter_upkeep(effect,context)

    def test_repeat_ref_does_not_replace_an_earlier_cost_recipient_incarnation(self):
        from quorune.semantic_choices.cumulative_counter_upkeep import prepare_cumulative_counter_upkeep,complete_cumulative_counter_upkeep
        context,effect,source,target,_=self.context(age=2);effect['stage']='pay'
        first=prepare_cumulative_counter_upkeep(effect,context)
        saved=complete_cumulative_counter_upkeep(self.continuation(first),{'cards':['C1']},context.query)
        target.logical_object_id='C1@2';last=prepare_cumulative_counter_upkeep(saved.repeat_effect,context)
        with self.assertRaises(ValueError):complete_cumulative_counter_upkeep(self.continuation(last),{'cards':['C1']},context.query)

    def test_ordered_counter_cost_identity_is_explicit_and_old_unique_ref_boundary_survives(self):
        from quorune.semantic_runtime.intents import PlaceCountersIntent
        from quorune.semantic_choices.intent_replacement import counter_intent_identity,validate_counter_intent_identity,with_replacement_selections
        old=PlaceCountersIntent('A',('C1',),'+1/+1',1,'Legacy',effect_generated=False)
        self.assertNotIn('ordered_cost_version',counter_intent_identity(old))
        self.assertEqual(counter_intent_identity(old),validate_counter_intent_identity(counter_intent_identity(old)))
        old_duplicate=replace(old,object_refs=('C1','C1'))
        with self.assertRaises(ValueError):validate_counter_intent_identity(counter_intent_identity(old_duplicate))
        ordered=replace(old_duplicate,ordered_cost_version=2);wire=counter_intent_identity(ordered)
        self.assertEqual(wire,validate_counter_intent_identity(wire));self.assertEqual(2,wire['ordered_cost_version'])
        self.assertEqual(2,with_replacement_selections(ordered,()).ordered_cost_version)
        for change in ({'ordered_cost_version':True},{'ordered_cost_version':1},{'amount':2},{'effect_generated':True}):
            with self.assertRaises(ValueError):replace(ordered,**change)
        for change in ({'ordered_cost_version':True},{'ordered_cost_version':1},{'amount':2},{'effect_generated':True}):
            with self.assertRaises(ValueError):validate_counter_intent_identity({**wire,**change})

    def test_actual_payload_requires_controller_upkeep_and_concrete_cost_owner_without_annotations(self):
        from types import SimpleNamespace
        from quorune.rules.cumulative_counter_upkeep import cumulative_counter_upkeep_runtime_dependencies
        from quorune.rules.cumulative_object_upkeep import CONTROLLER_UPKEEP
        spec=compile_counter_cumulative_upkeep('Cumulative upkeep—Put a -1/-1 counter on this creature.')
        base=dict(event='step.begin',active_zone='battlefield',target_schema=None,event_condition=CONTROLLER_UPKEEP,effects=[spec.effect()])
        self.assertIn('cost.counter.placement_resolution',cumulative_counter_upkeep_runtime_dependencies(SimpleNamespace(**base)))
        for change in ({'event':'spell.cast'},{'active_zone':'command'},{'target_schema':{'kind':'creature'}},{'event_condition':None}):
            with self.assertRaises(ValueError):cumulative_counter_upkeep_runtime_dependencies(SimpleNamespace(**{**base,**change}))

    def test_fresh_per_occurrence_cost_omission_mutant_is_killed(self):
        from unittest.mock import patch
        import quorune.semantic_choices.cumulative_counter_upkeep as helper
        from quorune.semantic_runtime.intents import PlaceCountersIntent
        context,effect,source,*_=self.context(scope='source',age=2);effect['stage']='pay'
        offered=helper.prepare_cumulative_counter_upkeep(effect,context)
        def witness():
            paid=helper.complete_cumulative_counter_upkeep(self.continuation(offered),{'pay':True},context.query)
            self.assertEqual(('S1','S1'),paid.intents[0].object_refs)
        witness()
        with patch.object(helper,'PlaceCountersIntent',return_value=PlaceCountersIntent('A',('S1',),'-1/-1',2,'Mutant',effect_generated=False)),self.assertRaises(AssertionError):witness()


class CumulativeCounterUpkeepCompilerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import tempfile
        from pathlib import Path
        from common import ROOT
        from quorune.carddb import CardDatabase
        from quorune.rules.capabilities import load_default_capability_registry
        from scripts.build_test_database import build_fixture_database
        cls.temporary=tempfile.TemporaryDirectory();path=Path(cls.temporary.name)/'counter-upkeep-compiler.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/cumulative-counter-upkeep-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
    @classmethod
    def tearDownClass(cls):cls.db.close();cls.temporary.cleanup()

    def test_full_originals_close_normally_and_payload_keeps_both_new_runtime_owners(self):
        from quorune.oracle_ir import compile_oracle_card,register_generated_programs
        from quorune.semantics import SemanticRegistry
        from quorune.card_programs.adapters import compile_best_available_card_program
        from quorune.card_programs.binding import bind_card_program_runtime,bind_semantic_program_runtime
        cards=[self.db.lookup(n,fuzzy=False) for n in ('Aboroth','Sheltering Ancient')];semantics=SemanticRegistry()
        register_generated_programs(self.db,semantics,cards,trust_level='provisional',capability_registry=self.registry,capability_profile='commander_review',promote_exact_runtime_handlers=True,promote_exact_trigger_programs=True,promote_exact_effect_programs=True,promote_exact_capability_declarations=True)
        for card in cards:
            with self.subTest(card=card.name):
                ir=compile_oracle_card(card,capability_registry=self.registry,capability_profile='commander_review');self.assertEqual('exact',ir.status,ir.material_residuals)
                program=compile_best_available_card_program(self.db,card,semantic_registry=semantics,capability_registry=self.registry,capability_profile='commander_review');bound=bind_card_program_runtime(program,capability_registry=self.registry,profile='commander_review');self.assertTrue(bound['strict_capability_ready'],bound['blockers'])
                trigger=next(p for p in semantics.programs_for_oracle(card.oracle_id) if any(e.get('schema_version')==8 for e in p.effects))
                for cap in ('cost.counter.placement_resolution','counter.producer.cumulative_upkeep_counter_costs'):
                    deps=tuple(c for c in trigger.capability_dependencies if c!=cap)
                    changed=replace(trigger,coverage=(),handlers=(),capability_dependencies=deps,capability_closure=self.registry.closure(deps,profile='commander_review').to_dict())
                    result=bind_semantic_program_runtime(changed,capability_registry=self.registry,profile='commander_review');self.assertFalse(result['strict']);self.assertTrue(any(cap in b for b in result['blockers']),result['blockers'])

    def test_genuine_counter_cost_owner_withdrawal_preserves_old_mana_object_and_unrelated_spell(self):
        import json
        from copy import deepcopy
        from common import ROOT
        from quorune.oracle_ir import compile_oracle_card
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index,discover_tests
        from test_cumulative_upkeep_counter_placement import cumulative_record
        raw=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding='utf-8'));declarations=read_evidence_document(ROOT/'platform/capability-evidence-declarations.json');known=set(discover_tests(ROOT).values())
        for cap in ('cost.counter.placement_resolution','counter.producer.cumulative_upkeep_counter_costs'):
            changed=deepcopy(raw);next(r for r in changed['capabilities'] if r['id']==cap).update(status='blocked',blockers=['counter cost owner withdrawn'])
            reg=CapabilityRegistry(changed);index=build_index(registry_value=changed,declaration_source=declarations,discovered_test_ids=known);reg.mark_evidence_verified(validate_capability_evidence_index(index,registry=reg))
            self.assertNotEqual('exact',compile_oracle_card(self.db.lookup('Aboroth'),capability_registry=reg,capability_profile='commander_review').status)
            for card in (self.db.lookup('Phyrexian Soulgorger'),self.db.lookup('Memnite'),cumulative_record('Cumulative upkeep {1}',52)):
                self.assertEqual('exact',compile_oracle_card(card,capability_registry=reg,capability_profile='commander_review').status)


from inherited_composition_fixture import InheritedNativeFixture
import test_target_recipient_results as recipients

class CumulativeCounterUpkeepNativeTests(InheritedNativeFixture,unittest.TestCase):
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
        cls.temporary=tempfile.TemporaryDirectory();path=Path(cls.temporary.name)/'counter-upkeep-native.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/cumulative-counter-upkeep-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Counter cumulative upkeep',[DeckEntry('Sliver Hivelord',1,'commander'),DeckEntry('Generic Bound Plains',30)],['Sliver Hivelord'])

    def world(self,seed,name='Sheltering Ancient',*,age=0,cast=False):
        from quorune.model import TurnEntry
        s=self.session(seed);source=self.add(s.engine,name,zone='hand' if cast else 'battlefield');source.counters['age']=age
        private=self.add(s.engine,'Counterspell',seat='C',zone='hand',ref='counter-upkeep-private')
        s.state.current_turn=TurnEntry('counter-upkeep-initial-D','D');s.state.last_normal_turn_player='D'
        self.assert_closed(s,source);return s,source,private

    def begin(self,s):
        self.prepare(s,actor='D',active='D');self.checkpoint(s)
        self.until(s,lambda ss:ss.state.active_player=='A' and ss.state.phase=='beginning' and ss.state.step=='upkeep')
        self.assertEqual('semantic.choice',self.resolve(s).kind);self.assertEqual(['A'],s.state.pending_decision.actors)

    def select(self,s,refs,*,complete=True):
        command={'action_id':'choose','cards':list(refs)};self.reject(s,'D',command)
        result=s.act('pilot:A',command);self.assertTrue(result.ok,result.summary)
        decision=self.resolve(s)
        if complete:self.assertIsNone(decision)
        else:self.assertEqual('semantic.choice',decision.kind)

    def test_actual_both_cast_originals_pay_fixed_source_or_nontarget_opponent_counter(self):
        for i,name in enumerate(('Aboroth','Sheltering Ancient')):
            with self.subTest(card=name):
                s,source,private=self.world(70224200+i,name,cast=True);self.add(s.engine,'Vedalken Orrery')
                target=self.add(s.engine,'Slippery Bogle',seat='B');self.prepare(s,actor='D',active='D');s.state.players['A'].mana_pool.update({'C':4,'G':2});self.checkpoint(s)
                self.cast_for(s,'A',source);self.assertIsNone(self.resolve(s));self.assertEqual('battlefield',s.state.cards[source.object_id].zone)
                self.until(s,lambda ss:ss.state.active_player=='A' and ss.state.phase=='beginning' and ss.state.step=='upkeep')
                self.assertEqual('semantic.choice',self.resolve(s).kind);self.assertFalse(s.state.stack[-1].targets)
                if name=='Aboroth':
                    command={'action_id':'choose','pay':True};self.reject(s,'B',command);self.assertTrue(s.act('pilot:A',command).ok);self.assertIsNone(self.resolve(s));recipient=source;counter='-1/-1'
                else:self.select(s,[target.ref]);recipient=target;counter='+1/+1'
                self.assertEqual(1,s.state.cards[recipient.object_id].counters.get(counter));self.assertEqual('battlefield',s.state.cards[source.object_id].zone);self.finish(s,private)

    def test_actual_nontarget_cost_uses_distinct_opponents_shroud_and_hexproof_without_partial_counters(self):
        s,source,private=self.world(70224210,age=1);first=self.add(s.engine,'Slippery Bogle',seat='B');second=self.add(s.engine,'Blastoderm',seat='C')
        self.begin(s);self.assertEqual(2,s.state.cards[source.object_id].counters.get('age'))
        self.reject(s,'A',{'action_id':'choose','cards':[source.ref]});self.select(s,[first.ref],complete=False)
        self.assertEqual(0,s.state.cards[first.object_id].counters.get('+1/+1',0));self.assertEqual(0,s.state.cards[second.object_id].counters.get('+1/+1',0))
        s=self.replay(s,load=True);self.select(s,[second.ref]);self.assertEqual(1,s.state.cards[first.object_id].counters.get('+1/+1'));self.assertEqual(1,s.state.cards[second.object_id].counters.get('+1/+1'));self.finish(s,private)

    def test_actual_repeated_recipient_keeps_per_cost_replacement_and_noneffect_provenance(self):
        s,source,private=self.world(70224220);self.add(s.engine,'Doubling Season');target=self.add(s.engine,'Grizzly Bears',seat='B')
        scales=self.add(s.engine,'Hardened Scales',seat='B');season=self.add(s.engine,'Doubling Season',seat='B',ref='counter-cost-opponent-season')
        self.assert_closed(s,scales);self.assert_closed(s,season)
        self.begin(s);self.assertEqual(2,s.state.cards[source.object_id].counters.get('age'))
        self.select(s,[target.ref],complete=False);self.assertEqual(0,s.state.cards[target.object_id].counters.get('+1/+1',0))
        self.select(s,[target.ref]);self.assertEqual(8,s.state.cards[target.object_id].counters.get('+1/+1'));self.assertEqual('battlefield',s.state.cards[source.object_id].zone);self.finish(s,private)

    def test_actual_source_counter_cost_is_not_doubled_and_lethal_cost_stabilizes_after_whole_payment(self):
        s,source,private=self.world(70224230,'Aboroth',age=8);self.add(s.engine,'Doubling Season');self.begin(s)
        self.assertEqual(10,s.state.cards[source.object_id].counters.get('age'))
        result=s.act('pilot:A',{'action_id':'choose','pay':True});self.assertTrue(result.ok,result.summary);self.assertIsNone(self.resolve(s))
        self.assertEqual('graveyard',s.state.cards[source.object_id].zone);self.finish(s,private)

    def test_actual_counter_prohibition_blocks_positive_price_but_zero_can_pay_or_decline(self):
        for age,pay in ((1,False),(0,True),(0,False)):
            with self.subTest(age=age,pay=pay):
                s,source,private=self.world(70224240+age+int(pay),'Aboroth',age=age);self.add(s.engine,'Solemnity');self.begin(s)
                self.assertEqual(age,s.state.cards[source.object_id].counters.get('age',0))
                if age:self.reject(s,'A',{'action_id':'choose','pay':True})
                s=self.replay(s,load=True);command={'action_id':'choose','pay':pay};self.reject(s,'B',command);self.assertTrue(s.act('pilot:A',command).ok);self.assertIsNone(self.resolve(s))
                self.assertEqual('battlefield' if pay else 'graveyard',s.state.cards[source.object_id].zone);self.finish(s,private)

    def test_actual_decline_after_one_cost_proposal_places_no_opponent_counters(self):
        s,source,private=self.world(70224250,age=1);target=self.add(s.engine,'Grizzly Bears',seat='B');self.begin(s)
        self.select(s,[target.ref],complete=False);self.select(s,[])
        self.assertEqual('graveyard',s.state.cards[source.object_id].zone);self.assertEqual(0,s.state.cards[target.object_id].counters.get('+1/+1',0));self.finish(s,private)

    def test_actual_cost_batch_fault_rolls_back_all_recipient_counters_and_retries(self):
        from unittest.mock import patch
        from quorune.semantic_choices.intent_host import SemanticChoiceIntentHostMixin
        from quorune.errors import GameRuleError
        from quorune.record import authoritative_state_hash
        s,source,private=self.world(70224260,age=1);first=self.add(s.engine,'Grizzly Bears',seat='B');second=self.add(s.engine,'Slippery Bogle',seat='C');self.begin(s);self.select(s,[first.ref],complete=False)
        original=SemanticChoiceIntentHostMixin.place_counters_intent;calls=[]
        def fault(host,intent):
            result=original(host,intent)
            if intent.ordered_cost_version==2:calls.append(True);raise GameRuleError('Counter cost batch rollback witness')
            return result
        before=authoritative_state_hash(s.state);command={'action_id':'choose','cards':[second.ref]}
        with patch.object(SemanticChoiceIntentHostMixin,'place_counters_intent',fault):
            result=s.act('pilot:A',command);self.assertFalse(result.ok);self.assertEqual(before,authoritative_state_hash(s.state))
        self.assertTrue(calls);self.assertEqual(0,s.state.cards[first.object_id].counters.get('+1/+1',0));self.assertEqual(0,s.state.cards[second.object_id].counters.get('+1/+1',0))
        self.select(s,[second.ref]);self.assertEqual(1,s.state.cards[first.object_id].counters.get('+1/+1'));self.assertEqual(1,s.state.cards[second.object_id].counters.get('+1/+1'));self.finish(s,private)

    def test_actual_old_upkeep_does_not_place_age_or_cost_counters_on_bounced_recast_source(self):
        s,source,private=self.world(70224270);self.add(s.engine,'Vedalken Orrery');self.add(s.engine,'Island');self.add(s.engine,'Forest')
        bounce=self.add(s.engine,'Unsummon',seat='B',zone='hand');self.add(s.engine,'Island',seat='B',ref='counter-cost-bounce-island')
        self.prepare(s,actor='D',active='D');self.checkpoint(s);old=source.logical_object_id
        self.until(s,lambda ss:ss.state.active_player=='A' and ss.state.phase=='beginning' and ss.state.step=='upkeep')
        self.cast_for(s,'B',bounce,[source.ref]);self.resolve_top(s);self.cast_for(s,'A',source);self.resolve_top(s)
        self.assertNotEqual(old,s.state.cards[source.object_id].logical_object_id);self.assertIsNone(self.resolve(s));self.assertEqual(0,s.state.cards[source.object_id].counters.get('age',0));self.finish(s,private)

    def test_actual_replacement_order_uses_recipient_and_resume_keeps_repeated_cost_occurrences(self):
        s,source,private=self.world(70224280,age=1);target=self.add(s.engine,'Grizzly Bears',seat='B');scales=self.add(s.engine,'Hardened Scales',seat='B');branch=self.add(s.engine,'Branching Evolution',seat='B')
        self.assert_closed(s,scales);self.assert_closed(s,branch)
        self.begin(s);self.assertEqual(2,s.state.cards[source.object_id].counters.get('age'))
        self.select(s,[target.ref],complete=False)
        result=s.act('pilot:A',{'action_id':'choose','cards':[target.ref]});self.assertTrue(result.ok,result.summary)
        self.assertEqual('replacement.order',s.state.pending_decision.kind);self.assertEqual(['B'],s.state.pending_decision.actors)
        self.assertEqual(0,s.state.cards[target.object_id].counters.get('+1/+1',0));s=self.replay(s,load=True)
        for _ in range(8):
            if s.state.pending_decision.kind!='replacement.order':break
            self.assertEqual(['B'],s.state.pending_decision.actors);packet=s.packet('pilot:B',full=True)
            options=packet['decision']['ctx']['options'];selected=next((o['id'] for o in options if o['source']==scales.ref),options[0]['id']);command={'action_id':'choose','choices':{'replacement':selected}};self.reject(s,'A',command)
            result=s.act('pilot:B',command);self.assertTrue(result.ok,result.summary)
        self.assertIsNone(self.resolve(s));self.assertEqual('battlefield',s.state.cards[source.object_id].zone)
        self.assertEqual(8,s.state.cards[target.object_id].counters.get('+1/+1',0));self.finish(s,private)

    def test_fresh_native_aggregated_counter_cost_mutant_is_killed(self):
        from unittest.mock import patch
        import quorune.semantic_choices.cumulative_counter_upkeep as helper
        from quorune.semantic_runtime.intents import PlaceCountersIntent
        def aggregate(**kwargs):
            if kwargs.get('ordered_cost_version')==2:
                kwargs=dict(kwargs);kwargs['amount']=len(kwargs['object_refs']);kwargs['object_refs']=(kwargs['object_refs'][0],);kwargs['ordered_cost_version']=None
            return PlaceCountersIntent(**kwargs)
        with patch.object(helper,'PlaceCountersIntent',aggregate):
            result=unittest.TestResult();type(self)('test_actual_repeated_recipient_keeps_per_cost_replacement_and_noneffect_provenance').run(result)
            self.assertFalse(result.errors,result.errors);self.assertTrue(result.failures,'Aggregated replacement granularity passed the fresh native witness')

    def test_actual_no_opponent_creature_has_no_payable_recipient_and_sacrifices_source(self):
        s,source,private=self.world(70224290);self.begin(s)
        self.reject(s,'A',{'action_id':'choose','cards':[source.ref]});self.select(s,[])
        self.assertEqual('graveyard',s.state.cards[source.object_id].zone);self.finish(s,private)
