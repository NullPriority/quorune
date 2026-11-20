"""Independent beneficial upkeep costs retain payer and recipient roles."""
from dataclasses import replace
import unittest
from quorune.cumulative_effect_cost_upkeep import UpkeepEffectCostSpec,compile_effect_cost_upkeep

class CumulativeEffectCostModelTests(unittest.TestCase):
    def test_both_original_costs_roundtrip_with_explicit_new_wrapper(self):
        for text,kind in (('Cumulative upkeep—Add {R}.','add_mana'),('Cumulative upkeep—An opponent gains 1 life.','opponent_life_gain')):
            spec=compile_effect_cost_upkeep(text);self.assertEqual(kind,spec.kind);self.assertEqual(spec,UpkeepEffectCostSpec.from_dict(spec.to_dict()));self.assertEqual(spec,UpkeepEffectCostSpec.from_effect(spec.effect()))
            for version in (True,1,2,3,4,5,6,7,8,9,11):
                with self.assertRaises(ValueError):UpkeepEffectCostSpec.from_effect({**spec.effect(),'schema_version':version})

    def test_mana_cost_benefits_only_its_payer_and_retains_each_unit(self):
        spec=UpkeepEffectCostSpec('add_mana',color='R')
        self.assertEqual(6,spec.total(6));self.assertEqual(0,spec.total(0))
        one=spec.occurrence('A',actor='A',active_seats=('A','B','C','D'))
        self.assertEqual(('add_mana','A',1,'R'),one);self.assertEqual((one,one),tuple(spec.occurrence('A',actor='A',active_seats=('A','B')) for _ in range(2)))
        self.assertFalse(spec.recipient_allowed('B',actor='A',active_seats=('A','B')))

    def test_opponent_life_cost_can_repeat_or_switch_eligible_opponents_without_targeting(self):
        spec=UpkeepEffectCostSpec('opponent_life_gain');seats=('A','B','C','D')
        self.assertFalse(spec.recipient_allowed('A',actor='A',active_seats=seats))
        self.assertTrue(spec.recipient_allowed('B',actor='A',active_seats=seats));self.assertTrue(spec.recipient_allowed('C',actor='A',active_seats=seats))
        planned=tuple(spec.occurrence(p,actor='A',active_seats=seats) for p in ('B','B','C'))
        self.assertEqual(('B','B','C'),tuple(p[1] for p in planned))
        self.assertFalse(spec.recipient_allowed('B',actor='A',active_seats=seats,denied_life_players=('B',)))
        self.assertFalse(spec.recipient_allowed('Z',actor='A',active_seats=seats))

    def test_malformed_costs_ages_and_unrepresented_results_are_rejected(self):
        spec=UpkeepEffectCostSpec('add_mana',color='R')
        for change in ({'unit_amount':True},{'unit_amount':0},{'kind':'damage'},{'color':'RR'},{'color':None},{'schema_version':True}):
            with self.assertRaises(ValueError):replace(spec,**change)
        for age in (True,-1,1.0):
            with self.assertRaises(ValueError):spec.total(age)
        for text in ('Cumulative upkeep—Add {R}{R}.','Cumulative upkeep—An opponent gains X life.','Cumulative upkeep—Target opponent gains 1 life.','Cumulative upkeep—An opponent gains 1 life. Draw a card.'):
            self.assertIsNone(compile_effect_cost_upkeep(text))
        with self.assertRaises(ValueError):UpkeepEffectCostSpec('opponent_life_gain',color='R')


class CumulativeEffectCostChoiceTests(unittest.TestCase):
    def world(self,kind='opponent_life_gain',age=0,denied=()):
        from types import SimpleNamespace
        source=SimpleNamespace(ref='S1',logical_object_id='S1@1',zone='battlefield',phased_out=False,controller='A',counters={'age':age})
        query=SimpleNamespace(active_seats=('A','B','C','D'),object=lambda *a,**k:source,life_gain_allowed=lambda p:p not in denied)
        context=SimpleNamespace(actor='A',stack_controller='A',stack_ref='STACK1',stack_label='Upkeep',source_ref='S1',source_logical_object_id='S1@1',query=query)
        spec=UpkeepEffectCostSpec(kind,color='R' if kind=='add_mana' else None);effect=spec.effect();effect.update(player='A',source='S1')
        return context,source,effect

    def continuation(self,prepared):
        from types import SimpleNamespace
        return SimpleNamespace(effect=prepared.continuation_effect,semantic_frame=SimpleNamespace(controller='A'))

    def test_age_placement_precedes_one_mana_cost_occurrence_per_age(self):
        from quorune.semantic_choices.cumulative_effect_cost_upkeep import prepare_cumulative_effect_cost_upkeep,complete_cumulative_effect_cost_upkeep
        context,source,effect=self.world('add_mana');first=prepare_cumulative_effect_cost_upkeep(effect,context)
        self.assertEqual('age',first.preparation_intents[0].counter_name);self.assertEqual(0,source.counters['age'])
        source.counters['age']=3;offered=prepare_cumulative_effect_cost_upkeep(first.auto_continue.prepend_effects[0],context)
        paid=complete_cumulative_effect_cost_upkeep(self.continuation(offered),{'action_id':'choose','pay':True},context.query)
        self.assertEqual(3,len(paid.intents));self.assertEqual([('A','R',1)]*3,[(i.player,i.color,i.amount) for i in paid.intents])

    def test_opponent_choices_repeat_without_committing_partial_life_and_lock_the_age(self):
        from quorune.semantic_choices.cumulative_effect_cost_upkeep import prepare_cumulative_effect_cost_upkeep,complete_cumulative_effect_cost_upkeep
        context,source,effect=self.world(age=2);effect['stage']='pay';first=prepare_cumulative_effect_cost_upkeep(effect,context)
        self.assertEqual(('B','C','D','decline'),first.request.choice.legal_values)
        proposed=complete_cumulative_effect_cost_upkeep(self.continuation(first),{'choice':'B'},context.query)
        self.assertFalse(proposed.intents);source.counters['age']=5
        second=prepare_cumulative_effect_cost_upkeep(proposed.repeat_effect,context);self.assertEqual(2,second.request.public_context['age_counters'])
        paid=complete_cumulative_effect_cost_upkeep(self.continuation(second),{'choice':'B'},context.query)
        self.assertEqual(1,len(paid.intents));self.assertEqual(('B','B'),paid.intents[0].players);self.assertEqual(1,paid.intents[0].amount)

    def test_prohibited_or_inactive_opponents_are_not_offered_and_zero_cost_still_payable(self):
        from quorune.semantic_choices.cumulative_effect_cost_upkeep import prepare_cumulative_effect_cost_upkeep,complete_cumulative_effect_cost_upkeep
        context,source,effect=self.world(age=1,denied=('B','C','D'));effect['stage']='pay';blocked=prepare_cumulative_effect_cost_upkeep(effect,context)
        self.assertEqual(('decline',),blocked.request.choice.legal_values)
        for bad in ('A','B','Z'):
            with self.assertRaises(ValueError):complete_cumulative_effect_cost_upkeep(self.continuation(blocked),{'choice':bad},context.query)
        source.counters['age']=0;free=prepare_cumulative_effect_cost_upkeep(effect,context)
        self.assertEqual((True,False),free.request.choice.legal_values);self.assertFalse(complete_cumulative_effect_cost_upkeep(self.continuation(free),{'pay':True},context.query).intents)

    def test_decline_after_one_proposal_has_only_original_source_sacrifice(self):
        from quorune.semantic_choices.cumulative_effect_cost_upkeep import prepare_cumulative_effect_cost_upkeep,complete_cumulative_effect_cost_upkeep
        context,source,effect=self.world(age=2);effect['stage']='pay';first=prepare_cumulative_effect_cost_upkeep(effect,context)
        selected=complete_cumulative_effect_cost_upkeep(self.continuation(first),{'choice':'B'},context.query)
        second=prepare_cumulative_effect_cost_upkeep(selected.repeat_effect,context)
        declined=complete_cumulative_effect_cost_upkeep(self.continuation(second),{'choice':'decline'},context.query)
        self.assertEqual(1,len(declined.intents));self.assertEqual(('S1',),declined.intents[0].object_refs);self.assertEqual('graveyard',declined.intents[0].destination)

    def test_new_prohibition_on_prior_opponent_rejects_entire_cost(self):
        from quorune.semantic_choices.cumulative_effect_cost_upkeep import prepare_cumulative_effect_cost_upkeep,complete_cumulative_effect_cost_upkeep
        context,source,effect=self.world(age=2);effect['stage']='pay';first=prepare_cumulative_effect_cost_upkeep(effect,context)
        selected=complete_cumulative_effect_cost_upkeep(self.continuation(first),{'choice':'B'},context.query)
        context.query.life_gain_allowed=lambda p:p!='B';second=prepare_cumulative_effect_cost_upkeep(selected.repeat_effect,context)
        with self.assertRaises(ValueError):complete_cumulative_effect_cost_upkeep(self.continuation(second),{'choice':'C'},context.query)

    def test_payer_source_phase_or_extra_fields_fail_before_benefit(self):
        from quorune.semantic_choices.cumulative_effect_cost_upkeep import prepare_cumulative_effect_cost_upkeep,complete_cumulative_effect_cost_upkeep
        context,source,effect=self.world('add_mana',1);effect['stage']='pay';offered=prepare_cumulative_effect_cost_upkeep(effect,context)
        for bad in ({'pay':1},{'pay':True,'player':'B'},{'choice':'B'}):
            with self.assertRaises(ValueError):complete_cumulative_effect_cost_upkeep(self.continuation(offered),bad,context.query)
        source.logical_object_id='S1@2'
        with self.assertRaises(ValueError):prepare_cumulative_effect_cost_upkeep(effect,context)

    def test_fresh_wrong_mana_beneficiary_mutant_is_killed(self):
        from unittest.mock import patch
        import quorune.semantic_choices.cumulative_effect_cost_upkeep as helper
        from quorune.semantic_runtime.intents import AddManaIntent
        context,source,effect=self.world('add_mana',1);effect['stage']='pay';offered=helper.prepare_cumulative_effect_cost_upkeep(effect,context)
        def witness():self.assertEqual('A',helper.complete_cumulative_effect_cost_upkeep(self.continuation(offered),{'pay':True},context.query).intents[0].player)
        witness()
        with patch.object(helper,'AddManaIntent',return_value=AddManaIntent('B','R',1,'A','Mutant')),self.assertRaises(AssertionError):witness()


class EffectCostTransactionTests(unittest.TestCase):
    def test_life_permission_snapshot_is_immutable_and_existing_default_is_permissive(self):
        from quorune.semantic_choices.context import SnapshotSemanticChoiceQuery
        from quorune.life_gain_permission import LifeGainPermissionSnapshot
        q=SnapshotSemanticChoiceQuery(seat_order=('A','B'),active_order=('A','B'))
        self.assertTrue(q.life_gain_allowed('A'));self.assertTrue(q.life_gain_allowed('B'));self.assertFalse(q.life_gain_allowed('Z'))
        denied=replace(q,life_gain_permissions=LifeGainPermissionSnapshot(('B',)))
        self.assertFalse(denied.life_gain_allowed('B'));self.assertTrue(q.life_gain_allowed('B'))
        with self.assertRaises(ValueError):replace(q,life_gain_permissions={})

    def test_gain_cost_identity_retains_repeated_recipients_and_rejects_malformed_data(self):
        from quorune.semantic_runtime.life_gain_cost_intent import LifeGainCostBatchIntent
        from quorune.semantic_choices.intent_replacement import semantic_intent_identity,validate_semantic_intent_identity,with_replacement_selections
        cost=LifeGainCostBatchIntent('A',('B','B','C'),1,'Wall upkeep','S1');kind,wire=semantic_intent_identity(cost)
        self.assertEqual('life_gain_cost_batch',kind);self.assertEqual(wire,validate_semantic_intent_identity(kind,wire));self.assertEqual(('B','B','C'),with_replacement_selections(cost,()).players)
        for change in ({'amount':True},{'amount':0},{'players':('A',)},{'players':()},{'actor':''}):
            with self.assertRaises(ValueError):replace(cost,**change)
        with self.assertRaises(ValueError):validate_semantic_intent_identity(kind,{**wire,'extra':True})

    def test_benefit_batch_uses_existing_life_commit_once_and_prechecks_all_prohibitions(self):
        from types import SimpleNamespace
        from unittest.mock import patch
        from quorune.semantic_runtime.life_gain_cost_intent import LifeGainCostBatchIntent
        from quorune.semantic_choices.life_intent_commit import apply_life_gain_cost_batch_intent
        from quorune.errors import GameRuleError
        from quorune.life_gain_permission import LifeGainPermissionSnapshot
        cost=LifeGainCostBatchIntent('A',('B','B','C'),1,'Wall upkeep','S1');host=SimpleNamespace(active_seats=('A','B','C'),_log=lambda *a,**k:None)
        committed=SimpleNamespace(records=(SimpleNamespace(direction='gain',amount=1),)*3)
        with patch('quorune.semantic_choices.life_intent_commit.collect_life_gain_permissions',return_value=LifeGainPermissionSnapshot()),patch('quorune.effect_runtime.life_effects._commit',return_value=committed) as commit:
            self.assertEqual(3,apply_life_gain_cost_batch_intent(host,cost));self.assertEqual(1,commit.call_count)
            self.assertEqual(('B','B','C'),tuple(c.player for c in commit.call_args.args[1]));self.assertEqual('cost',commit.call_args.kwargs['effect']['cause'])
        with patch('quorune.semantic_choices.life_intent_commit.collect_life_gain_permissions',return_value=LifeGainPermissionSnapshot(('C',))),patch('quorune.effect_runtime.life_effects._commit') as commit:
            with self.assertRaises(GameRuleError):apply_life_gain_cost_batch_intent(host,cost)
            commit.assert_not_called()

    def test_effect_cost_payload_owns_original_upkeep_scope_without_annotations(self):
        from types import SimpleNamespace
        from quorune.rules.cumulative_effect_cost_upkeep import cumulative_effect_cost_upkeep_runtime_dependencies
        from quorune.rules.cumulative_object_upkeep import CONTROLLER_UPKEEP
        spec=UpkeepEffectCostSpec('add_mana',color='R');base=dict(event='step.begin',active_zone='battlefield',target_schema=None,event_condition=CONTROLLER_UPKEEP,effects=[spec.effect()])
        self.assertIn('counter.producer.cumulative_upkeep_effect_costs',cumulative_effect_cost_upkeep_runtime_dependencies(SimpleNamespace(**base)))
        for change in ({'event':'mana.added'},{'active_zone':'hand'},{'target_schema':{'kind':'player'}},{'event_condition':None}):
            with self.assertRaises(ValueError):cumulative_effect_cost_upkeep_runtime_dependencies(SimpleNamespace(**{**base,**change}))


class CumulativeEffectCostCompilerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import tempfile
        from pathlib import Path
        from common import ROOT
        from quorune.carddb import CardDatabase
        from quorune.rules.capabilities import load_default_capability_registry
        from scripts.build_test_database import build_fixture_database
        cls.temporary=tempfile.TemporaryDirectory();path=Path(cls.temporary.name)/'upkeep-effects-compiler.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/cumulative-effect-cost-upkeep-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
    @classmethod
    def tearDownClass(cls):cls.db.close();cls.temporary.cleanup()

    def test_both_full_originals_close_normally_and_cost_data_preserves_concrete_authority(self):
        from quorune.oracle_ir import compile_oracle_card,register_generated_programs
        from quorune.semantics import SemanticRegistry
        from quorune.card_programs.adapters import compile_best_available_card_program
        from quorune.card_programs.binding import bind_card_program_runtime,bind_semantic_program_runtime
        cards=[self.db.lookup(n,fuzzy=False) for n in ('Braid of Fire','Wall of Shards')];semantics=SemanticRegistry();cap='counter.producer.cumulative_upkeep_effect_costs'
        register_generated_programs(self.db,semantics,cards,trust_level='provisional',capability_registry=self.registry,capability_profile='commander_review',promote_exact_runtime_handlers=True,promote_exact_trigger_programs=True,promote_exact_effect_programs=True,promote_exact_capability_declarations=True)
        for card in cards:
            with self.subTest(card=card.name):
                ir=compile_oracle_card(card,capability_registry=self.registry,capability_profile='commander_review');self.assertEqual('exact',ir.status,ir.material_residuals)
                program=compile_best_available_card_program(self.db,card,semantic_registry=semantics,capability_registry=self.registry,capability_profile='commander_review');bound=bind_card_program_runtime(program,capability_registry=self.registry,profile='commander_review');self.assertTrue(bound['strict_capability_ready'],bound['blockers'])
                trigger=next(p for p in semantics.programs_for_oracle(card.oracle_id) if any(e.get('schema_version')==10 for e in p.effects));deps=tuple(c for c in trigger.capability_dependencies if c!=cap)
                changed=replace(trigger,coverage=(),handlers=(),capability_dependencies=deps,capability_closure=self.registry.closure(deps,profile='commander_review').to_dict())
                result=bind_semantic_program_runtime(changed,capability_registry=self.registry,profile='commander_review');self.assertFalse(result['strict']);self.assertTrue(any(cap in b for b in result['blockers']),result['blockers'])

    def test_genuine_beneficial_cost_withdrawal_preserves_old_upkeeps_and_unrelated_spell(self):
        import json
        from common import ROOT
        from quorune.oracle_ir import compile_oracle_card
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index,discover_tests
        raw=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding='utf-8'));next(r for r in raw['capabilities'] if r['id']=='counter.producer.cumulative_upkeep_effect_costs').update(status='blocked',blockers=['effect cost owner withdrawn'])
        reg=CapabilityRegistry(raw);idx=build_index(registry_value=raw,declaration_source=read_evidence_document(ROOT/'platform/capability-evidence-declarations.json'),discovered_test_ids=set(discover_tests(ROOT).values()));reg.mark_evidence_verified(validate_capability_evidence_index(idx,registry=reg))
        self.assertNotEqual('exact',compile_oracle_card(self.db.lookup('Braid of Fire'),capability_registry=reg,capability_profile='commander_review').status)
        for name in ('Memnite','Phyrexian Soulgorger','Aboroth','Earthen Goo'):
            self.assertEqual('exact',compile_oracle_card(self.db.lookup(name),capability_registry=reg,capability_profile='commander_review').status)


from inherited_composition_fixture import InheritedNativeFixture
import test_target_recipient_results as recipients

class CumulativeEffectCostNativeTests(InheritedNativeFixture,unittest.TestCase):
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
        cls.temporary=tempfile.TemporaryDirectory();path=Path(cls.temporary.name)/'upkeep-effects-native.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/cumulative-effect-cost-upkeep-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Beneficial upkeep costs',[DeckEntry('Sliver Hivelord',1,'commander'),DeckEntry('Generic Bound Plains',30)],['Sliver Hivelord'])

    def world(self,seed,name='Wall of Shards',*,age=0,cast=False):
        from quorune.model import TurnEntry
        s=self.session(seed);source=self.add(s.engine,name,zone='hand' if cast else 'battlefield');source.counters['age']=age
        private=self.add(s.engine,'Counterspell',seat='C',zone='hand',ref='effect-upkeep-private')
        s.state.current_turn=TurnEntry('effect-upkeep-initial-D','D');s.state.last_normal_turn_player='D';self.assert_closed(s,source)
        return s,source,private

    def start(self,s):
        self.prepare(s,actor='D',active='D');self.checkpoint(s)
        self.until(s,lambda ss:ss.state.active_player=='A' and ss.state.phase=='beginning' and ss.state.step=='upkeep')

    def begin(self,s):
        self.start(s);self.assertEqual('semantic.choice',self.resolve(s).kind);self.assertEqual(['A'],s.state.pending_decision.actors)

    def select(self,s,player,*,complete=True):
        command={'action_id':'choose','choice':player};self.reject(s,'D',command);result=s.act('pilot:A',command);self.assertTrue(result.ok,result.summary);decision=self.resolve(s)
        if complete:self.assertIsNone(decision)
        else:self.assertEqual('semantic.choice',decision.kind)

    def pay(self,s,value):
        command={'action_id':'choose','pay':value};self.reject(s,'B',command);result=s.act('pilot:A',command);self.assertTrue(result.ok,result.summary);self.assertIsNone(self.resolve(s))

    def test_actual_both_cast_originals_pay_the_beneficial_cost(self):
        for i,name in enumerate(('Braid of Fire','Wall of Shards')):
            with self.subTest(card=name):
                s,source,private=self.world(70224500+i,name,cast=True);self.add(s.engine,'Vedalken Orrery');self.prepare(s,actor='D',active='D');s.state.players['A'].mana_pool.update({'C':1,'R':1,'W':1});self.checkpoint(s)
                self.cast_for(s,'A',source);self.assertIsNone(self.resolve(s));self.until(s,lambda ss:ss.state.active_player=='A' and ss.state.phase=='beginning' and ss.state.step=='upkeep');self.assertEqual('semantic.choice',self.resolve(s).kind)
                if name=='Braid of Fire':self.pay(s,True);self.assertEqual(1,s.state.players['A'].mana_pool.get('R',0))
                else:
                    before=s.state.players['B'].life;self.select(s,'B');self.assertEqual(before+1,s.state.players['B'].life)
                self.assertEqual('battlefield',s.state.cards[source.object_id].zone);self.assertEqual(1,s.state.cards[source.object_id].counters.get('age'));self.finish(s,private)

    def test_actual_braid_uses_respondable_stack_and_countered_upkeep_adds_no_age_or_mana(self):
        s,source,private=self.world(70224510,'Braid of Fire');counter=self.add(s.engine,'Stifle',seat='B',zone='hand');self.add(s.engine,'Island',seat='B');self.start(s)
        trigger=next(i.ref for i in s.state.stack if i.kind=='triggered_ability' and i.source_object_id==source.object_id)
        self.cast_for(s,'B',counter,[trigger]);self.assertIsNone(self.resolve(s));self.assertEqual(0,s.state.cards[source.object_id].counters.get('age',0));self.assertEqual(0,s.state.players['A'].mana_pool.get('R',0));self.finish(s,private)

    def test_actual_braid_replaced_age_adds_full_mana_and_upkeep_mana_empties_before_draw(self):
        s,source,private=self.world(70224520,'Braid of Fire');double=self.add(s.engine,'Doubling Season');self.assert_closed(s,double);self.begin(s)
        self.assertEqual(2,s.state.cards[source.object_id].counters.get('age'));self.pay(s,True);self.assertEqual(2,s.state.players['A'].mana_pool.get('R',0))
        self.until(s,lambda ss:ss.state.active_player=='A' and ss.state.phase=='beginning' and ss.state.step=='draw')
        self.assertEqual(0,s.state.players['A'].mana_pool.get('R',0));self.finish(s,private)

    def test_actual_wall_can_repeat_or_switch_nontarget_hexproof_opponents_without_partial_gain(self):
        s,source,private=self.world(70224530,age=2);hexproof=self.add(s.engine,'Leyline of Sanctity',seat='B');self.assert_closed(s,hexproof);self.begin(s);before={p:s.state.players[p].life for p in 'ABCD'}
        self.assertEqual(3,s.state.cards[source.object_id].counters.get('age'));self.reject(s,'A',{'action_id':'choose','choice':'A'})
        self.select(s,'B',complete=False);self.select(s,'B',complete=False);self.assertEqual(before,{p:s.state.players[p].life for p in 'ABCD'})
        s=self.replay(s,load=True);self.select(s,'C');self.assertEqual(before['B']+2,s.state.players['B'].life);self.assertEqual(before['C']+1,s.state.players['C'].life);self.assertEqual(before['A'],s.state.players['A'].life);self.finish(s,private)

    def test_actual_wall_decline_after_proposal_grants_no_life(self):
        s,source,private=self.world(70224540,age=1);self.begin(s);before=s.state.players['B'].life;self.select(s,'B',complete=False);self.select(s,'decline')
        self.assertEqual(before,s.state.players['B'].life);self.assertEqual('graveyard',s.state.cards[source.object_id].zone);self.finish(s,private)

    def test_actual_life_gain_prohibition_removes_all_cost_opponents_and_zero_age_is_free(self):
        for age,value in ((1,False),(0,True),(0,False)):
            with self.subTest(age=age,pay=value):
                s,source,private=self.world(70224550+age+int(value),age=age);ban=self.add(s.engine,'Rampaging Ferocidon',seat='B');self.assert_closed(s,ban)
                if age==0:self.add(s.engine,'Solemnity')
                self.begin(s)
                if age:
                    for opponent in 'BCD':self.reject(s,'A',{'action_id':'choose','choice':opponent})
                    self.select(s,'decline');self.assertEqual('graveyard',s.state.cards[source.object_id].zone)
                else:self.pay(s,value);self.assertEqual('battlefield' if value else 'graveyard',s.state.cards[source.object_id].zone)
                self.finish(s,private)

    def test_actual_repeated_life_gains_trigger_each_occurrence(self):
        s,source,private=self.world(70224560,age=1);pridemate=self.add(s.engine,"Ajani's Pridemate",seat='B');double=self.add(s.engine,'Boon Reflection',seat='B');self.assert_closed(s,pridemate);self.assert_closed(s,double);self.begin(s);before=s.state.players['B'].life
        self.select(s,'B',complete=False)
        result=s.act('pilot:A',{'action_id':'choose','choice':'B'});self.assertTrue(result.ok,result.summary)
        decision=self.resolve(s)
        if decision is not None:
            self.assertEqual('trigger.order',decision.kind);self.assertEqual(['B'],decision.actors)
            ctx=s.packet('pilot:B',full=True)['decision']['ctx'];triggers=[row['id'] for row in ctx['triggers']]
            result=s.act('pilot:B',{'action_id':'order','triggers':triggers});self.assertTrue(result.ok,result.summary);self.assertIsNone(self.resolve(s))
        self.assertEqual(before+4,s.state.players['B'].life);self.assertEqual(2,s.state.cards[pridemate.object_id].counters.get('+1/+1',0));self.finish(s,private)

    def test_actual_life_cost_batch_replacement_order_is_recipient_owned_and_precommit_replays(self):
        s,source,private=self.world(70224570,age=1);first=self.add(s.engine,'Boon Reflection',seat='B');second=self.add(s.engine,'Rhox Faithmender',seat='B');self.assert_closed(s,first);self.assert_closed(s,second);self.begin(s);before=s.state.players['B'].life
        self.select(s,'B',complete=False);result=s.act('pilot:A',{'action_id':'choose','choice':'B'});self.assertTrue(result.ok,result.summary)
        self.assertEqual('replacement.order',s.state.pending_decision.kind);self.assertEqual(['B'],s.state.pending_decision.actors);self.assertEqual(before,s.state.players['B'].life);s=self.replay(s,load=True)
        for _ in range(8):
            if s.state.pending_decision.kind!='replacement.order':break
            self.assertEqual(['B'],s.state.pending_decision.actors);selected=s.packet('pilot:B',full=True)['decision']['ctx']['options'][0]['id'];command={'action_id':'choose','choices':{'replacement':selected}};self.reject(s,'A',command);result=s.act('pilot:B',command);self.assertTrue(result.ok,result.summary)
        self.assertIsNone(self.resolve(s));self.assertEqual(before+8,s.state.players['B'].life);self.assertEqual('battlefield',s.state.cards[source.object_id].zone);self.finish(s,private)

    def test_actual_full_life_cost_fault_rolls_back_all_players_and_retries(self):
        from unittest.mock import patch
        from quorune.semantic_choices.intent_host import SemanticChoiceIntentHostMixin
        from quorune.errors import GameRuleError
        from quorune.record import authoritative_state_hash
        s,source,private=self.world(70224580,age=1);self.begin(s);self.select(s,'B',complete=False);before=authoritative_state_hash(s.state);original=SemanticChoiceIntentHostMixin.apply_life_gain_cost_batch_intent;calls=[]
        def fault(host,intent):
            result=original(host,intent);calls.append(True);raise GameRuleError('Life benefit cost rollback witness')
        command={'action_id':'choose','choice':'C'}
        with patch.object(SemanticChoiceIntentHostMixin,'apply_life_gain_cost_batch_intent',fault):
            result=s.act('pilot:A',command);self.assertFalse(result.ok);self.assertEqual(before,authoritative_state_hash(s.state))
        self.assertTrue(calls);self.select(s,'C');self.finish(s,private)

    def test_actual_mana_cost_fault_rolls_back_earlier_units_and_retries(self):
        from unittest.mock import patch
        from quorune.semantic_choices.intent_host import SemanticChoiceIntentHostMixin
        from quorune.errors import GameRuleError
        from quorune.record import authoritative_state_hash
        s,source,private=self.world(70224590,'Braid of Fire',age=1);self.begin(s);before=authoritative_state_hash(s.state);original=SemanticChoiceIntentHostMixin.apply_mana_intent;calls=[]
        def fault(host,intent):
            result=original(host,intent);calls.append(True)
            if len(calls)==2:raise GameRuleError('Second mana benefit cost unit rollback')
            return result
        with patch.object(SemanticChoiceIntentHostMixin,'apply_mana_intent',fault):
            result=s.act('pilot:A',{'action_id':'choose','pay':True});self.assertFalse(result.ok);self.assertEqual(before,authoritative_state_hash(s.state))
        self.assertEqual(2,len(calls));self.assertEqual(0,s.state.players['A'].mana_pool.get('R',0));self.pay(s,True);self.assertEqual(2,s.state.players['A'].mana_pool.get('R',0));self.finish(s,private)

    def test_actual_bounced_recast_braid_is_not_charged_by_old_upkeep(self):
        s,source,private=self.world(70224600,'Braid of Fire');self.add(s.engine,'Vedalken Orrery');self.add(s.engine,'Island');self.add(s.engine,'Mountain')
        bounce=self.add(s.engine,'Boomerang',seat='B',zone='hand');self.assert_closed(s,bounce)
        for i in range(2):self.add(s.engine,'Island',seat='B',ref='benefit-opponent-island-'+str(i))
        self.start(s);old=source.logical_object_id
        self.cast_for(s,'B',bounce,[source.ref]);self.resolve_top(s);self.cast_for(s,'A',source);self.resolve_top(s)
        self.assertNotEqual(old,s.state.cards[source.object_id].logical_object_id);self.assertIsNone(self.resolve(s));self.assertEqual(0,s.state.cards[source.object_id].counters.get('age',0));self.assertEqual(0,s.state.players['A'].mana_pool.get('R',0));self.finish(s,private)

    def test_fresh_native_aggregated_life_cost_mutant_is_killed(self):
        from unittest.mock import patch
        import quorune.semantic_choices.cumulative_effect_cost_upkeep as helper
        from quorune.semantic_runtime.life_gain_cost_intent import LifeGainCostBatchIntent
        def aggregate(**kwargs):
            if len(set(kwargs['players']))==1:kwargs={**kwargs,'players':(kwargs['players'][0],),'amount':kwargs['amount']*len(kwargs['players'])}
            return LifeGainCostBatchIntent(**kwargs)
        with patch.object(helper,'LifeGainCostBatchIntent',aggregate):
            result=unittest.TestResult();type(self)('test_actual_repeated_life_gains_trigger_each_occurrence').run(result)
            self.assertFalse(result.errors,result.errors);self.assertTrue(result.failures,'Aggregated life costs passed the actual event multiplicity witness')
