"""Pinned CR608.2h/120.4: independent resolution counts, simultaneous damage."""
from dataclasses import FrozenInstanceError,replace
import json,unittest
from types import SimpleNamespace as Row
from unittest.mock import patch
from quorune.announced_damage import AllocatedDamageRecipient
from quorune.characteristic_fragments import CharacteristicQuantitySpec,CharacteristicQuantityScope
from quorune.object_predicate import ObjectQuerySpec
from quorune.query_effect_amount_model import PublicQueryAmountSpec
from quorune.fixed_damage_set_model import PlayerDamageRelation as Relation
from quorune.per_player_query_damage_model import PerPlayerQueryDamageSpec as Spec,BoundPerPlayerQueryDamage as Bound
from quorune.semantic_runtime.per_player_query_damage import resolve_per_player_query_damage
from quorune.compiler.per_player_query_damage import per_player_query_damage_template
from quorune.oracle_ir import _effect_template


def spec():
    return Spec(PublicQueryAmountSpec(CharacteristicQuantitySpec(
        scope=CharacteristicQuantityScope.CONTROLLER_ZONE,
        query=ObjectQuerySpec(zones=('battlefield',),types_all=('land',)))),Relation.ALL)


class PerPlayerQueryDamageModelTests(unittest.TestCase):
    def test_explicit_new_quantity_and_sealed_batch_round_trip_without_reinterpreting_old_amount(self):
        original=spec();self.assertEqual(original,Spec.from_dict(json.loads(json.dumps(original.to_dict()))))
        with self.assertRaises(FrozenInstanceError):original.relation=Relation.OPPONENTS
        with self.assertRaises(ValueError):PublicQueryAmountSpec.from_dict(original.to_dict())
        bound=Bound('A',Relation.ALL,(AllocatedDamageRecipient('A',None,1),AllocatedDamageRecipient('B',None,4)))
        self.assertEqual(5,bound.total);self.assertEqual(bound,Bound.from_dict(json.loads(json.dumps(bound.to_dict()))))
        self.assertEqual(0,Bound('A',Relation.ALL,()).total)

    def test_hidden_global_declaration_and_unmarked_or_boolean_versions_fail_closed(self):
        original=spec()
        for amount in (replace(original.amount,quantity=replace(original.amount.quantity,scope=CharacteristicQuantityScope.ALL_ZONES)),
                replace(original.amount,quantity=replace(original.amount.quantity,query=ObjectQuerySpec(zones=('hand',)))),
                replace(original.amount,coefficient=-1),replace(original.amount,schema_version=2,binding_id='shared')):
            with self.assertRaises(ValueError):Spec(amount,Relation.ALL)
        for change in ({'schema_version':True},{'schema_version':2},{'target_index':0},{'relation':'each_player'}):
            with self.assertRaises(ValueError):Spec.from_dict({**original.to_dict(),**change})
        with self.assertRaises(ValueError):Spec.from_dict(original.amount.to_dict())

    def test_bound_rows_reject_duplicate_players_object_identity_zero_and_opponent_caster(self):
        for rows in ((AllocatedDamageRecipient('B',None,1),)*2,(AllocatedDamageRecipient('B','object:creature',1),)):
            with self.assertRaises(ValueError):Bound('A',Relation.ALL,rows)
        with self.assertRaises(ValueError):Bound('A',Relation.OPPONENTS,(AllocatedDamageRecipient('A',None,1),))
        raw=Bound('A',Relation.ALL,(AllocatedDamageRecipient('B',None,1),)).to_dict()
        for change in ({'schema_version':True},{'schema_version':2},{'total':1}):
            with self.assertRaises(ValueError):Bound.from_dict({**raw,**change})
        raw['recipients'][0]['amount']=0
        with self.assertRaises(ValueError):Bound.from_dict(raw)


class PerPlayerQueryDamageRuntimeTests(unittest.TestCase):
    def test_all_counts_are_sealed_before_damage_and_bound_reload_never_queries_again(self):
        host=object();item=Row(controller='A',ref='stack',context={})
        snapshot=Row(recipients=tuple(Row(ref=p) for p in ('B','C','D','A')));observed=[]
        def count(host,value,item,*,query_player):observed.append(query_player);return {'A':1,'B':4,'C':0,'D':2}[query_player]
        with patch('quorune.semantic_runtime.per_player_query_damage.snapshot_fixed_damage_set',return_value=snapshot),patch('quorune.semantic_runtime.per_player_query_damage.resolve_public_query_amount',side_effect=count):
            raw=resolve_per_player_query_damage(host,spec().to_dict(),item)
        self.assertEqual(['B','C','D','A'],observed)
        bound=Bound.from_dict(raw);self.assertEqual([('B',4),('D',2),('A',1)],[(r.ref,r.amount) for r in bound.recipients])
        with patch('quorune.semantic_runtime.per_player_query_damage.resolve_public_query_amount',side_effect=AssertionError('Sealed amount must not query')):
            self.assertEqual(raw,resolve_per_player_query_damage(host,raw,item))
            item.controller='D'
            with self.assertRaises(ValueError):resolve_per_player_query_damage(host,raw,item)

    def test_actual_value_dispatch_passes_one_sealed_heterogeneous_intent_to_existing_handler(self):
        from quorune.semantic_runtime.values import resolve_semantic_value
        from quorune.semantic_runtime.fixed_damage_set_handlers import FixedDamageSetHandler
        from quorune.semantic_runtime.context import ReadOnlyHandlerContext,SemanticNodeError
        item=Row(controller='A',ref='stack',context={});snapshot=Row(recipients=(Row(ref='A'),Row(ref='B'),Row(ref='C')))
        with patch('quorune.semantic_runtime.per_player_query_damage.snapshot_fixed_damage_set',return_value=snapshot),patch('quorune.semantic_runtime.per_player_query_damage.resolve_public_query_amount',side_effect=(1,4,0)):
            amount=resolve_semantic_value(object(),spec().to_dict(),item)
        context=ReadOnlyHandlerContext.from_sequences(actor='A',default_reason='Query damage',seats='ABCD',active_seats='ABCD',apnap_order='ABCD')
        effect={'op':'damage_fixed_set','source':'spell-source','amount':amount,'groups':[{'kind':'players','relation':'all'}]}
        plan=FixedDamageSetHandler().lower(effect,context);self.assertEqual(1,len(plan.intents))
        intent=plan.intents[0];self.assertEqual((5,None),(intent.amount,intent.spec))
        self.assertEqual([('A',1),('B',4)],[(r.ref,r.amount) for r in intent.allocated_recipients])
        for change in ({'groups':[{'kind':'players','relation':'opponents'}]},
                {'announced_damage_recipients':[]},{'amount':spec().to_dict()},
                {'amount':Bound('A',Relation.ALL,(AllocatedDamageRecipient('creature',None,1),)).to_dict()}):
            with self.assertRaises(SemanticNodeError):FixedDamageSetHandler().lower({**effect,**change},context)

    def test_old_fixed_integer_amount_still_lowers_to_unchanged_uniform_spec(self):
        from quorune.semantic_runtime.fixed_damage_set_handlers import FixedDamageSetHandler
        from quorune.semantic_runtime.context import ReadOnlyHandlerContext
        context=ReadOnlyHandlerContext.from_sequences(actor='A',default_reason='Old fixed',seats='AB',active_seats='AB',apnap_order='AB')
        plan=FixedDamageSetHandler().lower({'op':'damage_fixed_set','source':'spell-source','amount':2,
            'groups':[{'kind':'players','relation':'all'}]},context)
        intent=plan.intents[0];self.assertEqual(2,intent.amount);self.assertIsNone(intent.allocated_recipients);self.assertIsNotNone(intent.spec)


class PerPlayerQueryDamageCompilerTests(unittest.TestCase):
    def compile(self,text):
        return per_player_query_damage_template(text,source_name='Generic Public Damage',
            compile_fixed=lambda body:_effect_template(body,card_name='Generic Public Damage'))

    def test_complete_land_nonbasic_and_island_clauses_keep_independent_player_relation(self):
        for recipient,quantity,coefficient,pronoun in (('player','lands',1,'they'),('player','nonbasic lands',2,'that player'),('opponent','Islands',1,'that player')):
            text=f'Generic Public Damage deals damage to each {recipient} equal to '+('twice ' if coefficient==2 else '')+f'the number of {quantity} {pronoun} control'+('s.' if pronoun=='that player' else '.')
            compiled=self.compile(text);self.assertIsNotNone(compiled,text)
            effect=compiled[1][0];amount=Spec.from_dict(effect['amount']);self.assertEqual(coefficient,amount.amount.coefficient)
            self.assertEqual(Relation.ALL if recipient=='player' else Relation.OPPONENTS,amount.relation)
            self.assertIsNone(compiled[2]);self.assertEqual('damage_fixed_set',effect['op'])

    def test_foreign_source_hidden_counts_wrong_antecedents_and_extra_instructions_stay_residual(self):
        text='Generic Public Damage deals damage to each player equal to the number of lands they control.'
        for bad in (text.replace('Generic Public Damage','Unrelated Source'),text.replace('they control','you control'),
                text.replace('lands','cards in hand'),text+' Draw a card.',text.replace('number of lands','total mana value of lands'),
                text.replace('they control','they controls'),text.replace('they control','that player control')):
            self.assertIsNone(self.compile(bad),bad)

    def test_raw_shape_frame_and_quantity_minima_are_independent_of_coverage(self):
        from quorune.rules.per_player_query_damage import per_player_query_damage_program_dependencies,is_closed_per_player_query_damage_program
        from quorune.card_programs.descriptor_requirements import typed_result_amount_runtime_dependencies
        from quorune.per_player_query_damage_model import PER_PLAYER_QUERY_DAMAGE_CAPABILITY as CAP
        compiled=self.compile('Generic Public Damage deals damage to each player equal to the number of lands they control.')
        program=Row(effects=compiled[1],event='resolve',active_zone='stack',ability_id='spell:front',coverage=(),
            target_schema=None,cost_schema=None)
        required=per_player_query_damage_program_dependencies(program);self.assertIn(CAP,required)
        self.assertIn(CAP,typed_result_amount_runtime_dependencies(program.effects))
        program.capability_dependencies=required;self.assertTrue(is_closed_per_player_query_damage_program(program))
        program.capability_dependencies=tuple(c for c in required if c!=CAP);self.assertFalse(is_closed_per_player_query_damage_program(program))
        for change in ({'target_schema':{'count':1}},{'event':'activate'},{'cost_schema':{'mana':{'C':1}}},
                {'effects':(*compiled[1],{'op':'draw','player':'$controller','count':1})}):
            invalid=Row(**{**vars(program),**change})
            with self.assertRaises(ValueError):per_player_query_damage_program_dependencies(invalid)

    def test_fresh_uniform_caster_amount_mutant_is_killed_after_distinct_share_witness(self):
        PerPlayerQueryDamageRuntimeTests('test_all_counts_are_sealed_before_damage_and_bound_reload_never_queries_again').test_all_counts_are_sealed_before_damage_and_bound_reload_never_queries_again()
        def caster(host,value,item,*,query_player):return 1
        with patch('quorune.semantic_runtime.per_player_query_damage.resolve_public_query_amount',side_effect=caster):
            # This independent witness passes distinct observed seats through the real binder.
            snapshot=Row(recipients=(Row(ref='A'),Row(ref='B')))
            with patch('quorune.semantic_runtime.per_player_query_damage.snapshot_fixed_damage_set',return_value=snapshot):
                bound=Bound.from_dict(resolve_per_player_query_damage(object(),spec().to_dict(),Row(controller='A',ref='stack',context={})))
            with self.assertRaises(AssertionError):self.assertEqual([1,4],[r.amount for r in bound.recipients])


from pathlib import Path
import tempfile
from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition,DeckEntry
from quorune.rules.capabilities import load_default_capability_registry
from quorune.oracle_ir import compile_oracle_card
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.per_player_query_damage_model import PER_PLAYER_QUERY_DAMAGE_CAPABILITY as CAP
from scripts.build_test_database import build_fixture_database
import test_target_recipient_results as recipients

ORIGINALS=('Acidic Soil','Price of Progress','Typhoon')
FIXTURE=ROOT/'tests/fixtures/per-player-query-damage-cards.json'


class PerPlayerQueryDamageNativeTests(InheritedNativeFixture,unittest.TestCase):
    reject=recipients.TargetRecipientNativeTests.reject
    finish=recipients.TargetRecipientNativeTests.finish

    @classmethod
    def setUpClass(cls):
        cls.temporary=tempfile.TemporaryDirectory();folder=Path(cls.temporary.name)
        base=json.loads((ROOT/'tests/fixtures/bound-effect-program-cards.json').read_text(encoding='utf-8'))
        base['cards']=[r for r in base['cards'] if r['name']=='Generic Bound Plains'];base['rulings']=[]
        path=folder/'basic.json';path.write_text(json.dumps(base),encoding='utf-8')
        build_fixture_database([path,FIXTURE],folder/'per-player-damage.sqlite3')
        cls.db=CardDatabase(folder/'per-player-damage.sqlite3');cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Per-player public damage',[DeckEntry('Sliver Hivelord',1,'commander'),
            DeckEntry('Generic Bound Plains',30)],['Sliver Hivelord'])

    def start(self,seed):
        s=self.session(seed)
        while len(s.state.players['A'].zones['hand'])>2:s.engine.move_card(s.state.players['A'].zones['hand'][-1],'library',log=False)
        private=self.add(s.engine,'Counterspell',seat='C',zone='hand',ref='unrelated-private-damage')
        return s,private

    def land(self,s,seat,name,index):return self.add(s.engine,name,seat=seat,ref=f'quantity-land-{seat}-{index}')

    def lives(self,s):return [s.state.players[p].life for p in 'ABCD']

    def soil_board(self,s):
        for seat,count in (('A',2),('B',4),('C',1)):
            for i in range(count):
                land=self.land(s,seat,'Mountain' if seat=='B' else 'Forest',i)
                if seat=='B' and i==2:land.owner='C'

    def soil_witness(self,seed,*,finish=True):
        s,private=self.start(seed);source=self.add(s.engine,'Acidic Soil',zone='hand');self.assert_closed(s,source)
        self.soil_board(s);self.prepare(s,mana={'C':2,'R':1});self.checkpoint(s);self.cast(s,'A',source)
        self.assertIsNone(self.resolve(s));self.assertEqual([38,36,39,40],self.lives(s))
        self.assertEqual('graveyard',s.state.cards[source.object_id].zone);self.assertEqual(0,sum(s.state.players['A'].mana_pool.values()))
        if finish:self.finish(s,private)
        return s,private

    def test_complete_three_originals_close_normally_and_raw_quantity_or_frame_cannot_be_omitted(self):
        s,private=self.start(718000)
        for name in ORIGINALS:
            source=self.add(s.engine,name,zone='hand');self.assert_closed(s,source)
            self.assertEqual('exact',compile_oracle_card(self.db.lookup(name),capability_registry=self.registry,capability_profile='commander_review').status)
            program=next(p for p in s.engine.semantics.programs_for_oracle(source.oracle_id) if CAP in p.capability_dependencies)
            for cap in (CAP,'quantity_expression.public_query_effect_amount','damage.batch.fixed_set'):
                deps=tuple(c for c in program.capability_dependencies if c!=cap)
                changed=replace(program,coverage=(),capability_dependencies=deps,
                    capability_closure=self.registry.closure(deps,profile='commander_review').to_dict() if deps else None)
                bound=bind_semantic_program_runtime(changed,capability_registry=self.registry,profile='commander_review')
                self.assertFalse(bound['strict']);self.assertIn('capability:undeclared_runtime_dependency:'+cap,bound['blockers'])
            changed=replace(program,event='activate',coverage=())
            self.assertFalse(bind_semantic_program_runtime(changed,capability_registry=self.registry,profile='commander_review')['strict'])

    def test_actual_acidic_soil_uses_each_current_controller_count_in_one_simultaneous_batch(self):
        import quorune.fixed_damage_set as owner
        original=owner.resolve_damage_batch;observed=[]
        def batch(host,proposals,**kwargs):
            proposals=tuple(proposals);observed.append([(p.recipient.ref,p.amount) for p in proposals])
            self.assertEqual([40]*4,[host.state.players[p].life for p in 'ABCD'])
            return original(host,proposals,**kwargs)
        with patch.object(owner,'resolve_damage_batch',side_effect=batch):s,private=self.soil_witness(718010,finish=False)
        self.assertEqual([[('A',2),('B',4),('C',1)]],observed)
        self.finish(s,private)

    def test_actual_price_of_progress_counts_nonbasic_supertype_separately_from_basic_land_types(self):
        s,private=self.start(718011);source=self.add(s.engine,'Price of Progress',zone='hand');self.assert_closed(s,source)
        self.land(s,'A','Forest',0);self.land(s,'A','Steam Vents',1)
        for i,name in enumerate(('Darksteel Citadel','Temple Garden','Watery Grave','Wastes')):self.land(s,'B',name,i)
        for i,name in enumerate(('Plains','Island','Swamp')):self.land(s,'C',name,i)
        self.land(s,'D','Island',0);self.add(s.engine,'Sol Ring',seat='D')
        self.prepare(s,mana={'C':1,'R':1});self.checkpoint(s);self.cast(s,'A',source)
        self.assertIsNone(self.resolve(s));self.assertEqual([38,34,40,40],self.lives(s));self.finish(s,private)

    def test_actual_typhoon_only_opponents_count_basic_and_nonbasic_islands(self):
        s,private=self.start(718012);source=self.add(s.engine,'Typhoon',zone='hand');self.assert_closed(s,source)
        for i,name in enumerate(('Island','Steam Vents','Watery Grave')):self.land(s,'A',name,i)
        self.land(s,'B','Island',0);self.land(s,'B','Steam Vents',1)
        self.land(s,'C','Watery Grave',0).owner='D';self.land(s,'D','Mountain',0)
        self.prepare(s,mana={'C':5,'G':1});self.checkpoint(s);self.cast(s,'A',source)
        self.assertIsNone(self.resolve(s));self.assertEqual([40,38,39,40],self.lives(s));self.finish(s,private)

    def test_actual_zero_counts_still_pay_full_price_without_damage_events(self):
        s,private=self.start(718013);source=self.add(s.engine,'Acidic Soil',zone='hand');self.assert_closed(s,source)
        self.prepare(s,mana={'C':2,'R':1});self.checkpoint(s);self.cast(s,'A',source)
        self.assertIsNone(self.resolve(s));self.assertEqual([40]*4,self.lives(s));self.assertEqual((),tuple(s.engine._current_turn_history('damage')))
        self.assertEqual('graveyard',s.state.cards[source.object_id].zone);self.assertEqual(0,sum(s.state.players['A'].mana_pool.values()))
        self.finish(s,private)

    def test_actual_land_return_response_changes_only_that_controllers_resolution_quantity(self):
        s,private=self.start(718020);source=self.add(s.engine,'Acidic Soil',zone='hand');self.assert_closed(s,source);self.soil_board(s)
        bounce=self.add(s.engine,'Boomerang',seat='B',zone='hand');self.assert_closed(s,bounce)
        target=next(s.state.cards[oid] for oid in s.state.players['B'].zones['battlefield'] if s.state.cards[oid].printed_name=='Mountain')
        s.state.players['B'].mana_pool.update({'U':2})
        self.prepare(s,mana={'C':2,'R':1});self.checkpoint(s);self.cast(s,'A',source);self.cast(s,'B',bounce,[target.ref])
        s=self.replay(s,load=True);self.assertIsNone(self.resolve(s));self.assertEqual([38,37,39,40],self.lives(s))
        self.assertEqual('hand',s.state.cards[target.object_id].zone);self.finish(s,private)

    def test_actual_typhoon_copy_recomputes_opponents_for_its_own_controller(self):
        s,private=self.start(718021);source=self.add(s.engine,'Typhoon',zone='hand');copy=self.add(s.engine,'Reverberate',seat='B',zone='hand')
        self.assert_closed(s,source);self.assert_closed(s,copy)
        for seat,count in (('A',5),('B',1),('C',2)):
            for i in range(count):self.land(s,seat,'Island',i)
        s.state.players['B'].mana_pool.update({'R':2});self.prepare(s,mana={'C':5,'G':1});self.checkpoint(s)
        self.cast(s,'A',source);self.cast(s,'B',copy,[s.state.stack[-1].ref])
        s=self.replay(s,load=True);self.assertIsNone(self.resolve(s));self.assertEqual([35,39,36,40],self.lives(s))
        self.assertEqual(2,len(s.engine._current_turn_history('spell_cast')));self.finish(s,private)

    def test_actual_replacement_choices_reload_sealed_independent_amounts_before_one_batch_commit(self):
        import quorune.semantic_runtime.per_player_query_damage as owner
        s,private=self.start(718022);source=self.add(s.engine,'Acidic Soil',zone='hand');self.assert_closed(s,source);self.soil_board(s)
        first=self.add(s.engine,'Furnace of Rath',ref='first-count-furnace');second=self.add(s.engine,'Furnace of Rath',seat='D',ref='second-count-furnace')
        self.assert_closed(s,first);self.assert_closed(s,second)
        self.prepare(s,mana={'C':2,'R':1});self.checkpoint(s);self.cast(s,'A',source)
        original=owner.resolve_public_query_amount;observed=[]
        def count(*args,**kwargs):observed.append(kwargs['query_player']);return original(*args,**kwargs)
        with patch.object(owner,'resolve_public_query_amount',side_effect=count):self.assertEqual('replacement.order',self.resolve(s).kind)
        self.assertEqual(['A','B','C','D'],observed)
        for seat in 'ABC':
            self.assertEqual([40]*4,self.lives(s));s=self.replay(s,load=True);self.assertEqual(['pilot:'+seat],s.pending_principals())
            option=s.packet('pilot:'+seat,full=True)['decision']['ctx']['options'][0]['id']
            command={'action_id':'choose','choices':{'replacement':option}};self.reject(s,'D',command)
            with patch.object(owner,'resolve_public_query_amount',side_effect=AssertionError('Pending batch must retain numeric player shares')):
                result=s.act('pilot:'+seat,command);self.assertTrue(result.ok,result.summary)
        self.assertIsNone(self.resolve(s));self.assertEqual([32,24,36,40],self.lives(s));self.finish(s,private)

    def test_actual_prevention_response_uses_affected_player_order_and_preserves_other_shares(self):
        s,private=self.start(718023);source=self.add(s.engine,'Acidic Soil',zone='hand');self.assert_closed(s,source);self.soil_board(s)
        furnace=self.add(s.engine,'Furnace of Rath');shield=self.add(s.engine,'Mending Hands',seat='B',zone='hand')
        self.assert_closed(s,furnace);self.assert_closed(s,shield);s.state.players['B'].mana_pool.update({'W':1})
        self.prepare(s,mana={'C':2,'R':1});self.checkpoint(s);self.cast(s,'A',source);self.cast(s,'B',shield,['B'])
        self.assertEqual('replacement.order',self.resolve(s).kind);self.assertEqual([40]*4,self.lives(s));s=self.replay(s,load=True)
        option=next(o['id'] for o in s.packet('pilot:B',full=True)['decision']['ctx']['options'] if ':'+furnace.ref+':' in o['id'])
        command={'action_id':'choose','choices':{'replacement':option}};self.reject(s,'C',command)
        result=s.act('pilot:B',command);self.assertTrue(result.ok,result.summary);self.assertIsNone(self.resolve(s))
        self.assertEqual([36,36,38,40],self.lives(s));self.finish(s,private)

    def test_actual_committed_heterogeneous_batch_fault_rolls_back_all_players_then_retries(self):
        import quorune.fixed_damage_set as owner
        from quorune.errors import GameRuleError
        from quorune.record import authoritative_state_hash
        s,private=self.start(718024);source=self.add(s.engine,'Acidic Soil',zone='hand');self.assert_closed(s,source);self.soil_board(s)
        self.prepare(s,mana={'C':2,'R':1});self.checkpoint(s);self.cast(s,'A',source)
        original=owner.resolve_damage_batch;observed=[];failed=None
        def fault(*args,**kwargs):
            result=original(*args,**kwargs);observed.append(self.lives(s));raise GameRuleError('Injected committed per-player batch failure')
        with patch.object(owner,'resolve_damage_batch',side_effect=fault):
            for _ in range(8):
                principal=s.pending_principals()[0];before=authoritative_state_hash(s.state);result=s.act(principal,{'action_id':'pass'})
                if not result.ok:
                    self.assertEqual(before,authoritative_state_hash(s.state));failed=principal;break
        self.assertEqual([[38,36,39,40]],observed);self.assertIsNotNone(failed);self.assertEqual([40]*4,self.lives(s))
        s=self.replay(s,load=True);result=s.act(failed,{'action_id':'pass'});self.assertTrue(result.ok,result.summary)
        self.assertIsNone(self.resolve(s));self.assertEqual([38,36,39,40],self.lives(s));self.finish(s,private)

    def test_fresh_native_uniform_caster_count_mutant_is_killed_by_actual_original_action(self):
        import quorune.semantic_runtime.per_player_query_damage as owner
        original=owner.resolve_public_query_amount;observed=[]
        def caster(host,value,item,*,query_player):
            observed.append(query_player);return original(host,value,item,query_player=item.controller)
        with patch.object(owner,'resolve_public_query_amount',side_effect=caster):
            with self.assertRaises(AssertionError):self.soil_witness(718025,finish=False)
        self.assertEqual(['A','B','C','D'],observed)

    def test_genuinely_verified_per_player_quantity_or_batch_withdrawal_preserves_unrelated_draw(self):
        from copy import deepcopy
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index,discover_tests
        from test_bound_effect_programs import record
        original=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        declarations=read_evidence_document(ROOT/'platform/capability-evidence-declarations.json');known=set(discover_tests(ROOT).values())
        for cap in (CAP,'quantity_expression.public_query_effect_amount','damage.batch.fixed_set'):
            raw=deepcopy(original);next(r for r in raw['capabilities'] if r['id']==cap).update(status='blocked',blockers=['Per-player batch authority withdrawn'])
            registry=CapabilityRegistry(raw);index=build_index(registry_value=raw,declaration_source=declarations,discovered_test_ids=known)
            registry.mark_evidence_verified(validate_capability_evidence_index(index,registry=registry))
            for name in ORIGINALS:self.assertNotEqual('exact',compile_oracle_card(self.db.lookup(name),capability_registry=registry,capability_profile='commander_review').status,name)
            self.assertEqual('exact',compile_oracle_card(record('Draw a card.'),capability_registry=registry,capability_profile='commander_review').status)
