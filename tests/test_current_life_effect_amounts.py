"""CR107.1b/608.2h and pinned Ajani rulings: life at resolution, frozen once."""
from dataclasses import FrozenInstanceError, replace
import json, unittest
from types import SimpleNamespace as Row
from unittest.mock import patch
from quorune.current_life_effect_amount_model import CurrentLifeEffectAmountSpec as Life
from quorune.query_effect_amount_model import CastXAmountSpec, PublicQueryAmountSpec
from quorune.rules.current_life_effect_amounts import resolve_current_life_effect_amount as resolve
from quorune.compiler.current_life_effect_amounts import current_life_effect_amount_template
from quorune.oracle_ir import _effect_template


class CurrentLifeAmountTests(unittest.TestCase):
    def test_explicit_closed_codec_is_immutable_and_old_amounts_remain_distinct(self):
        spec=Life('life:instruction');self.assertEqual(spec,Life.from_dict(json.loads(json.dumps(spec.to_dict()))))
        with self.assertRaises(FrozenInstanceError):spec.coefficient=-1
        for change in ({'schema_version':True},{'schema_version':2},{'binding_id':''},{'coefficient':True},{'coefficient':2},{'query_player':'B'}):
            with self.assertRaises(ValueError):Life.from_dict({**spec.to_dict(),**change})
        with self.assertRaises(ValueError):CastXAmountSpec.from_dict(spec.to_dict())
        with self.assertRaises(ValueError):PublicQueryAmountSpec.from_dict(spec.to_dict())
        with self.assertRaises(ValueError):Life.from_dict(CastXAmountSpec().to_dict())

    def test_locked_controller_current_life_freezes_once_across_signed_result_slots(self):
        host=Row(state=Row(players={'A':Row(life=7),'B':Row(life=13)}))
        item=Row(controller='B',ref='stack:B',context={});spec=Life('first')
        self.assertEqual(13,resolve(host,spec.to_dict(),item))
        host.state.players['B'].life=19
        self.assertEqual(-13,resolve(host,replace(spec,coefficient=-1).to_dict(),item))
        self.assertEqual(19,resolve(host,Life('next').to_dict(),item))
        saved=json.loads(json.dumps(item.context));resumed=Row(controller='B',ref='stack:B',context=saved)
        self.assertEqual(13,resolve(host,spec.to_dict(),resumed))
        self.assertEqual(7,host.state.players['A'].life)

    def test_negative_life_is_zero_but_unknown_boolean_and_changed_continuations_reject(self):
        host=Row(state=Row(players={'A':Row(life=-2)}));item=Row(controller='A',ref='stack',context={});spec=Life('one')
        self.assertEqual(0,resolve(host,spec.to_dict(),item))
        for value in (True,None,'3'):
            host.state.players['A'].life=value;item.context={}
            with self.assertRaises(ValueError):resolve(host,spec.to_dict(),item)
            self.assertEqual({},item.context)
        for previous in ({'schema_version':True,'controller':'A','value':3},
                {'schema_version':1,'controller':'B','value':3},{'schema_version':1,'controller':'A','value':True},
                {'schema_version':1,'controller':'A','value':-1},{'schema_version':1,'controller':'A','value':3,'hidden':'x'}):
            item.context={'declared_current_life_amounts':{'one':previous}}
            with self.assertRaises(ValueError):resolve(host,spec.to_dict(),item)

    def test_fresh_owner_life_mutant_is_killed_after_positive_locked_controller_value(self):
        host=Row(state=Row(players={'A':Row(life=7),'B':Row(life=13)}));spec=Life('one')
        self.assertEqual(13,resolve(host,spec.to_dict(),Row(controller='B',ref='stack',context={})))
        with patch('quorune.rules.current_life_effect_amounts.current_modifier_quantity',return_value=7):
            with self.assertRaises(AssertionError):self.assertEqual(13,resolve(host,spec.to_dict(),Row(controller='B',ref='stack',context={})))


class CurrentLifeAmountCompilerTests(unittest.TestCase):
    def compile(self,text):
        return current_life_effect_amount_template(text,source_name='Current Life Source',
            compile_fixed=lambda body:_effect_template(body,card_name='Current Life Source'))

    def test_complete_token_counter_and_material_rider_instructions_keep_fixed_owners(self):
        cases=('Create X 1/1 white Pegasus creature tokens with flying, where X is your life total.',
            'Create X 2/2 white Cat creature tokens, where X is your life total.',
            'Put X +1/+1 counters on target creature, where X is your life total.',
            'Put X +1/+1 counters on target creature, where X is your life total. That creature gains trample until end of turn.')
        for text in cases:
            with self.subTest(text=text):
                result=self.compile(text);self.assertIsNotNone(result,text)
                self.assertIn('current-controller-life-effect-amount',result[3]);self.assertTrue(result[1])
                self.assertNotIn('cast_x_effect_amount',json.dumps(result[1]))

    def test_noncontroller_historical_arithmetic_target_counts_and_unknown_riders_stay_unrepresented(self):
        base='Create X 1/1 white Pegasus creature tokens with flying, where X is your life total.'
        for text in (base.replace('your life total',"target player's life total"),base.replace('your life total','your starting life total'),
                base.replace('your life total','twice your life total'),base.replace('your life total','the life you lost this turn'),
                base+' Invent a material unrepresented effect.',
                'Look at the top X cards of your library, where X is your life total. You may put any number of nonland permanent cards with mana value 3 or less from among them onto the battlefield. Then shuffle.',
                'Exile up to X target cards from graveyards, where X is your life total.'):
            self.assertIsNone(self.compile(text),text)

    def test_binding_is_real_source_specific_and_never_an_announced_cost_value(self):
        text='Put X +1/+1 counters on target creature, where X is your life total.'
        a=self.compile(text);b=self.compile(text.replace('is your','is equal to your'))
        self.assertNotEqual(a[1],b[1]);self.assertEqual(a[2],b[2])

    def test_raw_result_and_ordinary_frame_require_owners_without_coverage(self):
        from quorune.rules.current_life_effect_amounts import current_life_program_dependencies, is_closed_current_life_program
        from quorune.card_programs.descriptor_requirements import typed_result_amount_runtime_dependencies
        from quorune.current_life_effect_amount_model import CURRENT_LIFE_AMOUNT_CAPABILITY as CAP
        for body in ('Create X 1/1 white Pegasus creature tokens with flying, where X is your life total.',
                'Put X +1/+1 counters on target creature, where X is your life total. That creature gains trample until end of turn.'):
            result=self.compile(body);base={'effects':result[1],'target_schema':result[2],'handlers':(),
                'cost_schema':None,'coverage':(),'event':'activate','active_zone':'battlefield','ability_id':'activation:one'}
            program=Row(**base);required=current_life_program_dependencies(program)
            self.assertIn(CAP,required);self.assertIn('quantity_expression.current_player_value',required)
            self.assertIn(CAP,typed_result_amount_runtime_dependencies(program.effects))
            program.capability_dependencies=required;self.assertTrue(is_closed_current_life_program(program))
            for missing in (CAP,'quantity_expression.current_player_value','quantity_expression.declared_effect_amount',
                    'token.creation.fixed_definition' if 'Create X' in body else 'counter.producer.fixed_effect'):
                self.assertIn(missing,required)
                program.capability_dependencies=tuple(c for c in required if c!=missing);self.assertFalse(is_closed_current_life_program(program))
            for change in ({'event':'characteristics.evaluate'},{'active_zone':'hand'},
                    {'cost_schema':{'amount':Life('price').to_dict()}},
                    {'effects':({'op':'unknown_material_result','amount':Life('unknown').to_dict()},)}):
                with self.assertRaises(ValueError):current_life_program_dependencies(Row(**{**base,**change}))

    def test_real_value_dispatch_uses_current_life_binding_and_not_announced_x(self):
        from quorune.semantic_runtime.values import resolve_semantic_value
        host=Row(state=Row(players={'A':Row(life=8)}));item=Row(controller='A',ref='stack',x_value=3,context={})
        self.assertEqual(8,resolve_semantic_value(host,Life('one').to_dict(),item))
        host.state.players['A'].life=12
        self.assertEqual(8,resolve_semantic_value(host,Life('one').to_dict(),item))
        self.assertEqual(3,resolve_semantic_value(host,CastXAmountSpec().to_dict(),item))


from pathlib import Path
import tempfile
from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.rules.capabilities import load_default_capability_registry
from quorune.oracle_ir import compile_oracle_card
from quorune.card_programs.binding import bind_semantic_program_runtime
from scripts.build_test_database import build_fixture_database
import test_turn_life_loss_recovery as recovery

ORIGINALS=('Storm Herd','Ajani, Caller of the Pride','Ajani, Wise Counselor','Ajani, Valiant Protector')
ULTIMATES={ORIGINALS[1]:-8,ORIGINALS[2]:-9,ORIGINALS[3]:-11}


class CurrentLifeAmountNativeTests(InheritedNativeFixture,unittest.TestCase):
    start=recovery.TurnLifeLossRecoveryNativeTests.start
    reject=recovery.TurnLifeLossRecoveryNativeTests.reject
    finish=recovery.TurnLifeLossRecoveryNativeTests.finish

    @classmethod
    def setUpClass(cls):
        cls.temporary=tempfile.TemporaryDirectory();folder=Path(cls.temporary.name)
        base=json.loads((ROOT/'tests/fixtures/bound-effect-program-cards.json').read_text(encoding='utf-8'))
        base['cards']=[r for r in base['cards'] if r['name']=='Generic Bound Plains'];base['rulings']=[]
        path=folder/'basic.json';path.write_text(json.dumps(base),encoding='utf-8')
        build_fixture_database([path,ROOT/'tests/fixtures/current-life-effect-amount-cards.json'],folder/'life.sqlite3')
        cls.db=CardDatabase(folder/'life.sqlite3');cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Actual current life',[DeckEntry('Sliver Hivelord',1,'commander'),
            DeckEntry('Generic Bound Plains',30)],['Sliver Hivelord'])

    def tokens(self,s,seat,name):
        return [c for c in s.state.cards.values() if not c.is_card_object and c.zone=='battlefield'
            and c.controller==seat and c.printed_name==name+' Token']

    def activate_loyalty(self,s,source,delta,targets=()):
        self.priority(s,'A');current=s.state.cards[source.object_id]
        ability=next(a for a in s.engine._activated_abilities(current) if a.loyalty_delta==delta)
        offered=next(a for a in s.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
            if a.get('source')==current.ref and a.get('ability')==ability.ability_id)
        command={'action_id':offered['id'],'pay':'auto','targets':list(targets)}
        self.reject(s,'D',command);result=s.act('pilot:A',command);self.assertTrue(result.ok,result.summary)

    def test_all_complete_originals_admit_normally_with_every_loyalty_sibling(self):
        from quorune.oracle_ir import register_generated_programs
        from quorune.card_programs.adapters import compile_best_available_card_program
        from quorune.card_programs import bind_card_program_runtime
        from quorune.semantics import SemanticRegistry
        for name in ORIGINALS:
            with self.subTest(name=name):
                card=self.db.lookup(name);registry=SemanticRegistry()
                register_generated_programs(self.db,registry,(card,),capability_registry=self.registry,
                    capability_profile='commander_review',promote_exact_runtime_handlers=True,
                    promote_exact_trigger_programs=True,promote_exact_effect_programs=True,promote_exact_capability_declarations=True)
                program=compile_best_available_card_program(self.db,card,semantic_registry=registry,
                    capability_registry=self.registry,capability_profile='commander_review')
                bound=bind_card_program_runtime(program,capability_registry=self.registry,profile='commander_review')
                self.assertTrue(bound['strict_capability_ready'],bound)
                ir=compile_oracle_card(card,capability_registry=self.registry,capability_profile='commander_review')
                self.assertEqual('exact',ir.status,ir.material_residuals)

    def test_actual_storm_herd_printed_cast_uses_life_after_response_and_keeps_flying_definition(self):
        s,private=self.start(721001);spell=self.add(s.engine,'Storm Herd',zone='hand')
        shock=self.add(s.engine,'Shock',zone='hand');nectar=self.add(s.engine,'Sacred Nectar',zone='hand')
        for c in (spell,shock,nectar):self.assert_closed(s,c)
        s.state.players['A'].life=4;self.prepare(s,mana={'C':9,'W':3,'R':1});self.checkpoint(s)
        self.cast(s,'A',spell);self.cast(s,'A',shock,['A']);s=self.replay(s,load=True);self.assertIsNone(self.resolve(s))
        self.assertEqual(2,s.state.players['A'].life);tokens=self.tokens(s,'A','Pegasus');self.assertEqual(2,len(tokens))
        for token in tokens:
            data=s.engine._effective_card_data(token);self.assertEqual(('1','1'),(str(data['power']),str(data['toughness'])))
            self.assertIn('Flying',data['keywords'])
        self.cast(s,'A',nectar);self.assertIsNone(self.resolve(s));self.assertEqual(6,s.state.players['A'].life)
        self.assertEqual(2,len(self.tokens(s,'A','Pegasus')));self.finish(s,private)

    def test_actual_all_three_ultimate_prices_and_material_trample_rider_survive_source_departure(self):
        for index,(name,delta) in enumerate(ULTIMATES.items()):
            with self.subTest(name=name):
                s,private=self.start(721010+index);source=self.add(s.engine,name)
                target=self.add(s.engine,'Grizzly Bears');shock=self.add(s.engine,'Shock',zone='hand')
                for c in (source,target,shock):self.assert_closed(s,c)
                source.counters['loyalty']=-delta;s.state.players['A'].life=4
                self.prepare(s,mana={'R':1});self.checkpoint(s)
                self.activate_loyalty(s,source,delta,() if index==0 else (target.ref,))
                self.assertEqual('graveyard',s.state.cards[source.object_id].zone)
                self.cast(s,'A',shock,['A']);s=self.replay(s,load=True);self.assertIsNone(self.resolve(s))
                self.assertEqual(2,s.state.players['A'].life)
                if index==0:self.assertEqual(2,len(self.tokens(s,'A','Cat')))
                else:
                    self.assertEqual(2,s.state.cards[target.object_id].counters.get('+1/+1'))
                    data=s.engine._effective_card_data(s.state.cards[target.object_id]);self.assertEqual(index==2,'Trample' in data['keywords'])
                self.finish(s,private)

    def test_actual_copied_storm_herd_uses_copy_controller_current_life(self):
        s,private=self.start(721020);source=self.add(s.engine,'Storm Herd',zone='hand')
        copy=self.add(s.engine,'Reverberate',seat='B',zone='hand')
        self.assert_closed(s,source);self.assert_closed(s,copy)
        s.state.players['A'].life=2;s.state.players['B'].life=3;s.state.players['B'].mana_pool.update({'R':2})
        self.prepare(s,mana={'C':8,'W':2});self.checkpoint(s)
        self.cast(s,'A',source);self.cast(s,'B',copy,[s.state.stack[-1].ref])
        s=self.replay(s,load=True);self.assertIsNone(self.resolve(s))
        self.assertEqual(2,len(self.tokens(s,'A','Pegasus')));self.assertEqual(3,len(self.tokens(s,'B','Pegasus')))
        self.finish(s,private)

    def test_actual_one_life_keeps_single_token_and_counter_trample_followup(self):
        for index,name in enumerate(('Storm Herd','Ajani, Valiant Protector')):
            with self.subTest(name=name):
                s,private=self.start(721021+index)
                source=self.add(s.engine,name,zone='hand' if index==0 else 'battlefield')
                target=self.add(s.engine,'Grizzly Bears')
                for c in (source,target):self.assert_closed(s,c)
                s.state.players['A'].life=1
                if index:source.counters['loyalty']=11
                self.prepare(s,mana={'C':8,'W':2});self.checkpoint(s)
                if index:self.activate_loyalty(s,source,-11,(target.ref,))
                else:self.cast(s,'A',source)
                self.assertIsNone(self.resolve(s));self.assertTrue(s.state.players['A'].in_game)
                self.assertEqual(1 if index==0 else 0,len(self.tokens(s,'A','Pegasus')))
                if index:
                    self.assertEqual(1,s.state.cards[target.object_id].counters.get('+1/+1',0))
                    self.assertIn('Trample',s.engine._effective_card_data(s.state.cards[target.object_id])['keywords'])
                self.finish(s,private)

    def test_actual_lethal_response_removes_unresolved_storm_instead_of_creating_zero_life_tokens(self):
        s,private=self.start(721026);source=self.add(s.engine,'Storm Herd',zone='hand')
        shock=self.add(s.engine,'Shock',seat='B',zone='hand')
        self.assert_closed(s,source);self.assert_closed(s,shock)
        s.state.players['A'].life=2;s.state.players['B'].mana_pool.update({'R':1})
        self.prepare(s,mana={'C':8,'W':2});self.checkpoint(s)
        self.cast(s,'A',source);self.cast(s,'B',shock,['A']);self.assertIsNone(self.resolve(s))
        self.assertFalse(s.state.players['A'].in_game);self.assertEqual([],self.tokens(s,'A','Pegasus'))
        self.finish(s,private)

    def test_actual_counter_replacement_order_reloads_without_requerying_current_life(self):
        s,private=self.start(721023);source=self.add(s.engine,'Ajani, Wise Counselor')
        target=self.add(s.engine,'Grizzly Bears');season=self.add(s.engine,'Doubling Season');scales=self.add(s.engine,'Hardened Scales')
        for c in (source,target,season,scales):self.assert_closed(s,c)
        source.counters['loyalty']=9;s.state.players['A'].life=2;self.prepare(s);self.checkpoint(s)
        self.activate_loyalty(s,source,-9,(target.ref,));self.assertEqual('replacement.order',self.resolve(s).kind)
        s=self.replay(s,load=True)
        with patch('quorune.rules.current_life_effect_amounts.current_modifier_quantity',side_effect=AssertionError('A sealed replacement cannot reread life')):
            for _ in range(4):
                if s.state.pending_decision is None or s.state.pending_decision.kind!='replacement.order':break
                option=s.packet('pilot:A',full=True)['decision']['ctx']['options'][0]['id']
                self.reject(s,'D',{'action_id':'choose','replacement':option})
                accepted=s.act('pilot:A',{'action_id':'choose','replacement':option});self.assertTrue(accepted.ok,accepted.summary)
                self.resolve(s)
        self.assertIn(s.state.cards[target.object_id].counters.get('+1/+1'),(5,6));self.finish(s,private)

    def test_actual_countered_and_target_fizzled_ultimates_keep_paid_loyalty_without_result(self):
        for index,name in enumerate(('Ajani, Caller of the Pride','Ajani, Valiant Protector')):
            with self.subTest(name=name):
                s,private=self.start(721024+index);source=self.add(s.engine,name);target=self.add(s.engine,'Grizzly Bears')
                response=self.add(s.engine,'Stifle' if index==0 else 'Boomerang',seat='B',zone='hand')
                for c in (source,target,response):self.assert_closed(s,c)
                source.counters['loyalty']=-ULTIMATES[name];s.state.players['B'].mana_pool.update({'U':2})
                self.prepare(s);self.checkpoint(s);self.activate_loyalty(s,source,ULTIMATES[name],() if index==0 else (target.ref,))
                response_target=s.state.stack[-1].ref if index==0 else target.ref
                self.cast(s,'B',response,[response_target]);self.assertIsNone(self.resolve(s))
                self.assertEqual('graveyard',s.state.cards[source.object_id].zone);self.assertEqual([],self.tokens(s,'A','Cat'))
                self.assertEqual(0,s.state.cards[target.object_id].counters.get('+1/+1',0));self.finish(s,private)

    def test_actual_post_counter_commit_fault_rolls_back_and_retry_keeps_paid_loyalty(self):
        from quorune.errors import GameRuleError
        from quorune.record import authoritative_state_hash
        import quorune.counter_placement as owner
        s,private=self.start(721030);source=self.add(s.engine,'Ajani, Wise Counselor');target=self.add(s.engine,'Grizzly Bears')
        self.assert_closed(s,source);self.assert_closed(s,target);source.counters['loyalty']=9;s.state.players['A'].life=2
        self.prepare(s);self.checkpoint(s);self.activate_loyalty(s,source,-9,(target.ref,))
        original=owner.commit_counter_placement_plan;observed=[];failed=None
        def fault(*args,**kwargs):
            result=original(*args,**kwargs);observed.append(s.state.cards[target.object_id].counters.get('+1/+1'))
            raise GameRuleError('Injected actual current-life counter commit failure')
        with patch.object(owner,'commit_counter_placement_plan',side_effect=fault):
            for _ in range(8):
                principal=s.pending_principals()[0];before=authoritative_state_hash(s.state);result=s.act(principal,{'action_id':'pass'})
                if not result.ok:self.assertEqual(before,authoritative_state_hash(s.state));failed=principal;break
        self.assertEqual([2],observed);self.assertIsNotNone(failed)
        self.assertEqual(0,s.state.cards[target.object_id].counters.get('+1/+1',0));self.assertEqual('graveyard',s.state.cards[source.object_id].zone)
        s=self.replay(s,load=True);result=s.act(failed,{'action_id':'pass'});self.assertTrue(result.ok,result.summary)
        self.assertIsNone(self.resolve(s));self.assertEqual(2,s.state.cards[target.object_id].counters.get('+1/+1'));self.finish(s,private)

    def test_actual_printed_planeswalker_casts_keep_positive_loyalty_siblings(self):
        cases=(('Ajani, Caller of the Pride',{'C':1,'W':2},1,5,1),
            ('Ajani, Wise Counselor',{'C':3,'W':2},2,7,0),
            ('Ajani, Valiant Protector',{'C':4,'G':1,'W':1},2,6,2))
        for index,(name,mana,delta,loyalty,counters) in enumerate(cases):
            with self.subTest(name=name):
                s,private=self.start(721040+index);source=self.add(s.engine,name,zone='hand');target=self.add(s.engine,'Grizzly Bears')
                self.assert_closed(s,source);self.assert_closed(s,target);s.state.players['A'].life=2
                self.prepare(s,mana=mana);self.checkpoint(s);self.cast(s,'A',source);self.assertIsNone(self.resolve(s))
                self.activate_loyalty(s,source,delta,() if index==1 else (target.ref,));self.assertIsNone(self.resolve(s))
                self.assertEqual(loyalty,s.state.cards[source.object_id].counters.get('loyalty'))
                self.assertEqual(counters,s.state.cards[target.object_id].counters.get('+1/+1',0))
                self.assertEqual(3 if index==1 else 2,s.state.players['A'].life);self.finish(s,private)

    def test_fresh_native_original_controller_life_mutant_is_killed_by_copied_storm(self):
        import quorune.rules.current_life_effect_amounts as owner
        original=owner.current_modifier_quantity;observed=[]
        def wrong_controller(host,item,quantity):
            observed.append(item.controller)
            return original(host,Row(controller='A'),quantity)
        with patch.object(owner,'current_modifier_quantity',side_effect=wrong_controller):
            with self.assertRaises(AssertionError):self.test_actual_copied_storm_herd_uses_copy_controller_current_life()
        self.assertIn('B',observed)

    def test_genuinely_verified_current_life_withdrawal_blocks_all_originals_but_keeps_fixed_tokens(self):
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index,discover_tests
        from test_bound_effect_programs import record
        from quorune.current_life_effect_amount_model import CURRENT_LIFE_AMOUNT_CAPABILITY as CAP
        raw=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        next(r for r in raw['capabilities'] if r['id']==CAP).update(status='blocked',blockers=['Current controller life amount withdrawn'])
        declarations=read_evidence_document(ROOT/'platform/capability-evidence-declarations.json')
        registry=CapabilityRegistry(raw);index=build_index(registry_value=raw,declaration_source=declarations,discovered_test_ids=set(discover_tests(ROOT).values()))
        registry.mark_evidence_verified(validate_capability_evidence_index(index,registry=registry))
        for name in ORIGINALS:self.assertNotEqual('exact',compile_oracle_card(self.db.lookup(name),capability_registry=registry,capability_profile='commander_review').status)
        for text in ('Create two 1/1 white Pegasus creature tokens with flying.','You gain 3 life.'):
            self.assertEqual('exact',compile_oracle_card(record(text,'Sorcery'),capability_registry=registry,capability_profile='commander_review').status)


if __name__=='__main__':unittest.main()
