"""Pinned CR608.2b/h/105.2/305.6: target's current public board, separate recipient."""
from dataclasses import FrozenInstanceError,replace
import json,unittest
from quorune.characteristic_fragments import CharacteristicQuantitySpec as Quantity,CharacteristicQuantityScope as Scope,CharacteristicQuantityReduction as Reduction
from quorune.object_predicate import ObjectQuerySpec
from quorune.query_effect_amount_model import PublicQueryAmountSpec as PublicAmount,PublicQueryAmountError
from quorune.target_player_query_amount_model import TargetPlayerQueryAmountSpec as TargetAmount


def amount_for(query,*,coefficient=1,reduction=Reduction.OBJECT_COUNT):
    quantity=Quantity(scope=Scope.CONTROLLER_ZONE,query=query,
        schema_version=2 if reduction is not Reduction.OBJECT_COUNT else 1,reduction=reduction)
    return TargetAmount(PublicAmount(quantity,coefficient))


class TargetPlayerPublicAmountModelTests(unittest.TestCase):
    def test_independent_target_wrapper_round_trips_without_reinterpreting_old_controller_amounts(self):
        spec=amount_for(ObjectQuerySpec(zones=('battlefield',),types_all=('creature',),tapped=True),coefficient=3)
        raw=spec.to_dict();self.assertEqual(spec,TargetAmount.from_dict(json.loads(json.dumps(raw))))
        self.assertEqual(Scope.CONTROLLER_ZONE,spec.amount.quantity.scope)
        self.assertEqual(spec.amount,PublicAmount.from_dict(raw['amount']))
        self.assertEqual(3,raw['amount']['coefficient'])
        with self.assertRaises(FrozenInstanceError):spec.target_index=1
        with self.assertRaises(PublicQueryAmountError):PublicAmount.from_dict(raw)
        with self.assertRaises(PublicQueryAmountError):TargetAmount.from_dict(raw['amount'])
        with self.assertRaises(PublicQueryAmountError):PublicAmount.from_dict({**raw['amount'],'target_index':0})

    def test_public_color_union_and_distinct_basic_type_counts_keep_existing_query_contract(self):
        union=amount_for(ObjectQuerySpec(zones=('battlefield',),colors_any=('B','R')),coefficient=2)
        domain=amount_for(ObjectQuerySpec(zones=('battlefield',),types_all=('land',)),reduction=Reduction.DISTINCT_BASIC_LAND_TYPES)
        self.assertEqual(('B','R'),union.amount.quantity.query.colors_any)
        self.assertEqual(Reduction.DISTINCT_BASIC_LAND_TYPES,domain.amount.quantity.reduction)
        self.assertEqual(domain,TargetAmount.from_dict(domain.to_dict()))

    def test_hidden_zones_unrepresented_reductions_binding_versions_and_other_target_indices_fail_closed(self):
        spec=amount_for(ObjectQuerySpec(zones=('battlefield',),types_all=('land',)))
        for change in ({'schema_version':True},{'schema_version':2},{'target_index':True},{'target_index':1},{'player':'B'}):
            with self.assertRaises(PublicQueryAmountError):TargetAmount.from_dict({**spec.to_dict(),**change})
        for quantity in (replace(spec.amount.quantity,query=ObjectQuerySpec(zones=('hand',))),
                replace(spec.amount.quantity,scope=Scope.ALL_ZONES),
                Quantity(scope=Scope.CONTROLLER_ZONE,query=ObjectQuerySpec(zones=('battlefield',)),schema_version=2,reduction=Reduction.TOTAL_MANA_VALUE)):
            with self.assertRaises(PublicQueryAmountError):TargetAmount(PublicAmount(quantity))
        for inner in (PublicAmount(spec.amount.quantity,-1),PublicAmount(spec.amount.quantity,1,2,'declared:test')):
            with self.assertRaises(PublicQueryAmountError):TargetAmount(inner)


from types import SimpleNamespace as Row
from unittest.mock import patch
from quorune.semantic_runtime.values import resolve_semantic_value
from quorune.semantic_runtime.query_effect_amounts import resolve_public_query_amount
from quorune.semantic_runtime.context import SemanticNodeError


class TargetPlayerPublicAmountRuntimeTests(unittest.TestCase):
    def test_targeted_query_binds_target_without_mutating_caster_or_changing_old_amounts(self):
        spec=amount_for(ObjectQuerySpec(zones=('battlefield',),types_all=('creature',),tapped=True),coefficient=2)
        host=Row(state=Row(active_seats=lambda:['A','B','C']))
        item=Row(controller='A',ref='STACK',targets=['B'],context={})
        observed=[]
        def query(_host,source,quantity):
            observed.append(source.controller);return {'A':1,'B':3,'C':0}[source.controller]
        with patch('quorune.semantic_runtime.query_effect_amounts.query_characteristic_count',side_effect=query):
            self.assertEqual(6,resolve_semantic_value(host,spec.to_dict(),item))
            self.assertEqual(2,resolve_public_query_amount(host,spec.amount.to_dict(),item))
            item.targets=['C'];self.assertEqual(0,resolve_semantic_value(host,spec.to_dict(),item))
        self.assertEqual(['B','A','C'],observed);self.assertEqual('A',item.controller);self.assertEqual({},item.context)

    def test_missing_foreign_object_and_departed_player_targets_reject_before_query(self):
        spec=amount_for(ObjectQuerySpec(zones=('battlefield',),types_all=('land',)))
        host=Row(state=Row(active_seats=lambda:['A','B']))
        for targets in ([],['creature-ref'],['C']):
            item=Row(controller='A',ref='STACK',targets=targets,context={})
            with patch('quorune.semantic_runtime.query_effect_amounts.query_characteristic_count') as query:
                with self.assertRaises(SemanticNodeError):resolve_semantic_value(host,spec.to_dict(),item)
                query.assert_not_called()


from quorune.oracle_ir import _effect_template
from quorune.compiler.target_player_query_amounts import target_player_query_amount_template
from quorune.rules.target_player_query_amounts import target_player_query_node_capabilities,target_player_query_program_dependencies,is_closed_target_player_query_program
from quorune.target_player_query_amount_model import TARGET_PLAYER_QUERY_AMOUNT_CAPABILITY as CAP

CLAUSES=(
    'You gain 2 life for each Mountain target opponent controls.',
    'Draw a card for each tapped creature target opponent controls.',
    'You gain 3 life for each black creature target opponent controls.',
    'Domain — Target player draws a card for each basic land type among lands they control.',
    'You gain 2 life for each black and/or red creature target opponent controls.',
    'You gain 1 life for each black and/or red permanent target opponent controls.')


def compile_target_query(text):
    return target_player_query_amount_template(text,compile_fixed=lambda body:_effect_template(body,card_name='Generic Target Query'),source_name='Generic Target Query')


class TargetPlayerPublicAmountCompilerTests(unittest.TestCase):
    def test_complete_original_clauses_keep_query_target_and_result_recipient_separate(self):
        for index,text in enumerate(CLAUSES):
            with self.subTest(text=text):
                compiled=compile_target_query(text);self.assertIsNotNone(compiled)
                effect=compiled[1][0];slot='count' if effect['op']=='draw' else 'delta'
                spec=TargetAmount.from_dict(effect[slot]);self.assertEqual(0,spec.target_index)
                self.assertEqual('$target.0' if index==3 else '$controller',effect['player'])
                self.assertEqual('any' if index==3 else 'opponent',compiled[2]['player_relation'])
                required=target_player_query_node_capabilities(effects=compiled[1],target_schema=compiled[2])
                self.assertIn(CAP,required);self.assertIn('target.revalidate_resolution',required)
                self.assertIn('zone.draw.library_to_hand' if effect['op']=='draw' else 'life.change.effect',required)
                if index in (4,5):self.assertEqual(('B','R'),spec.amount.quantity.query.colors_any)

    def test_raw_frame_and_missing_own_dependency_cannot_close_an_unrepresented_program(self):
        compiled=compile_target_query(CLAUSES[0])
        program=Row(effects=compiled[1],target_schema=compiled[2],event='resolve',active_zone='stack',ability_id='spell:front',coverage=())
        required=target_player_query_program_dependencies(program);program.capability_dependencies=required
        self.assertTrue(is_closed_target_player_query_program(program))
        program.capability_dependencies=tuple(c for c in required if c!=CAP)
        self.assertFalse(is_closed_target_player_query_program(program))
        for schema in (None,{**compiled[2],'count':True},{**compiled[2],'count':2},{'zones':['battlefield'],'count':1}):
            self.assertEqual((),target_player_query_node_capabilities(effects=compiled[1],target_schema=schema))
        program.target_schema=None
        with self.assertRaises(ValueError):target_player_query_program_dependencies(program)

    def test_hidden_owners_keywords_wrong_quantities_and_extra_material_stay_residual(self):
        for text in (CLAUSES[0]+' Draw a card.',CLAUSES[1].replace('controls','owns'),
                CLAUSES[1].replace('tapped creature','creature with flying'),
                'Draw a card for each card in target opponent\u0027s hand.',
                CLAUSES[0].replace('2 life','X life'),
                'Each player draws a card for each creature they control.'):
            self.assertIsNone(compile_target_query(text),text)

    def test_fresh_caster_query_mutant_is_killed_after_positive_target_binding(self):
        from quorune.semantic_runtime.target_player_query_amounts import resolve_target_player_query_amount
        from quorune.semantic_runtime.query_effect_amounts import resolve_public_query_amount
        observed=[]
        def wrong_player(host,value,item):
            observed.append(item.controller)
            return resolve_public_query_amount(host,TargetAmount.from_dict(value).amount.to_dict(),item)
        with patch('quorune.semantic_runtime.target_player_query_amounts.resolve_target_player_query_amount',wrong_player):
            with self.assertRaises(AssertionError):
                TargetPlayerPublicAmountRuntimeTests('test_targeted_query_binds_target_without_mutating_caster_or_changing_old_amounts').test_targeted_query_binds_target_without_mutating_caster_or_changing_old_amounts()
        self.assertEqual(['A'],observed)


from pathlib import Path
import tempfile
from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition,DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.rules.capabilities import load_default_capability_registry
from quorune.card_programs.binding import bind_semantic_program_runtime
from scripts.build_test_database import build_fixture_database
import test_target_recipient_results as recipients

ORIGINALS=('Renewing Dawn','Theft of Dreams','Borrowing 100,000 Arrows','Starlight','Allied Strategies','Honorable Scout','Kithkin Zealot')
FIXTURE=ROOT/'tests/fixtures/target-player-query-amount-cards.json'


class TargetPlayerPublicQueryNativeTests(InheritedNativeFixture,unittest.TestCase):
    reject=recipients.TargetRecipientNativeTests.reject
    finish=recipients.TargetRecipientNativeTests.finish

    @classmethod
    def setUpClass(cls):
        cls.temporary=tempfile.TemporaryDirectory();folder=Path(cls.temporary.name)
        basic=json.loads((ROOT/'tests/fixtures/bound-effect-program-cards.json').read_text(encoding='utf-8'))
        basic['cards']=[r for r in basic['cards'] if r['name']=='Generic Bound Plains'];basic['rulings']=[]
        path=folder/'basic.json';path.write_text(json.dumps(basic),encoding='utf-8')
        build_fixture_database([path,FIXTURE],folder/'target-public-query.sqlite3')
        cls.db=CardDatabase(folder/'target-public-query.sqlite3');cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Target public queries',[DeckEntry('Sliver Hivelord',1,'commander'),
            DeckEntry('Generic Bound Plains',30)],['Sliver Hivelord'])

    def start(self,seed):
        s=self.session(seed)
        while len(s.state.players['A'].zones['hand'])>2:s.engine.move_card(s.state.players['A'].zones['hand'][-1],'library',log=False)
        private=self.add(s.engine,'Counterspell',seat='C',zone='hand',ref='public-query-unrelated-private')
        return s,private

    def colored_board(self,s):
        black=self.add(s.engine,'Walking Corpse',seat='B',ref='target-black')
        gold=self.add(s.engine,'Rakdos Shred-Freak',seat='B',ref='target-black-red')
        red=self.add(s.engine,'Goblin Piker',seat='B',ref='target-red');red.owner='C'
        self.add(s.engine,'Memnite',seat='B',ref='target-colorless')
        self.add(s.engine,'Fervor',seat='B',ref='target-red-enchantment')
        self.add(s.engine,'Rakdos Signet',seat='B',ref='target-colorless-black-red-identity')
        self.add(s.engine,'Walking Corpse',seat='A',ref='caster-black')
        return black,gold,red

    def test_all_seven_originals_have_complete_normal_registration_and_independent_raw_amount_ownership(self):
        s,private=self.start(717000)
        for name in (*ORIGINALS,'Tribal Flames','Worldly Counsel','Exotic Curse'):
            with self.subTest(name=name):
                source=self.add(s.engine,name,zone='hand');self.assert_closed(s,source)
                self.assertEqual('exact',compile_oracle_card(self.db.lookup(name),capability_registry=self.registry,capability_profile='commander_review').status)
                for p in s.engine.semantics.programs_for_oracle(source.oracle_id):
                    if CAP not in p.capability_dependencies:continue
                    deps=tuple(c for c in p.capability_dependencies if c!=CAP)
                    raw=replace(p,coverage=(),capability_dependencies=deps,
                        capability_closure=self.registry.closure(deps,profile='commander_review').to_dict() if deps else None)
                    bound=bind_semantic_program_runtime(raw,capability_registry=self.registry,profile='commander_review')
                    self.assertFalse(bound['strict']);self.assertIn('capability:undeclared_runtime_dependency:'+CAP,bound['blockers'])

    def test_actual_life_spells_use_target_controlled_types_and_colors_with_caster_as_recipient(self):
        for index,(name,expected) in enumerate((('Renewing Dawn',6),('Starlight',6))):
            with self.subTest(name=name):
                s,private=self.start(717010+index);source=self.add(s.engine,name,zone='hand');self.assert_closed(s,source)
                if index==0:
                    for n in range(3):
                        land=self.add(s.engine,'Mountain',seat='B',ref=f'target-mountain-{n}')
                        if n==2:land.owner='C'
                    self.add(s.engine,'Mountain',seat='A',ref='caster-mountain')
                else:self.colored_board(s)
                self.prepare(s,mana={'C':1,'W':1});self.checkpoint(s);self.cast(s,'A',source,['B'])
                self.assertIsNone(self.resolve(s));self.assertEqual([40+expected,40,40,40],[s.state.players[p].life for p in 'ABCD'])
                self.finish(s,private)

    def test_actual_entry_life_union_counts_black_red_once_and_keeps_creature_permanent_scope(self):
        for index,(name,expected,cost) in enumerate((('Honorable Scout',6,{'W':1}),('Kithkin Zealot',4,{'C':1,'W':1}))):
            with self.subTest(name=name):
                s,private=self.start(717020+index);source=self.add(s.engine,name,zone='hand');self.assert_closed(s,source);self.colored_board(s)
                self.prepare(s,mana=cost);self.checkpoint(s);self.cast(s,'A',source)
                self.assertEqual('semantic.target',self.resolve(s).kind);self.target(s,'A',['B'])
                self.assertIsNone(self.resolve(s));self.assertEqual(40+expected,s.state.players['A'].life)
                self.assertEqual(40,s.state.players['B'].life);self.finish(s,private)

    def test_actual_both_draw_originals_count_target_tapped_creatures_and_draw_for_controller(self):
        for index,name in enumerate(('Theft of Dreams','Borrowing 100,000 Arrows')):
            with self.subTest(name=name):
                s,private=self.start(717030+index);source=self.add(s.engine,name,zone='hand');self.assert_closed(s,source)
                for n in range(3):
                    card=self.add(s.engine,'Grizzly Bears',seat='B',ref=f'target-creature-{n}');card.tapped=n!=2
                    if n==1:card.owner='C'
                self.add(s.engine,'Grizzly Bears',seat='A',ref='caster-tapped').tapped=True
                self.prepare(s,mana={'C':2,'U':1});self.checkpoint(s);self.cast(s,'A',source,['B'])
                hand=len(s.state.players['A'].zones['hand']);others={p:len(s.state.players[p].zones['hand']) for p in 'BCD'}
                self.assertIsNone(self.resolve(s));self.assertEqual(hand+2,len(s.state.players['A'].zones['hand']))
                self.assertEqual(others,{p:len(s.state.players[p].zones['hand']) for p in 'BCD'});self.finish(s,private)

    def test_actual_allied_strategies_uses_target_distinct_basic_types_and_target_draw_recipient(self):
        s,private=self.start(717040);source=self.add(s.engine,'Allied Strategies',zone='hand');self.assert_closed(s,source)
        for n,name in enumerate(('Steam Vents','Temple Garden','Watery Grave','Mountain','Wastes')):
            card=self.add(s.engine,name,seat='B',ref=f'target-domain-{n}')
            if n==2:card.owner='C'
        self.add(s.engine,'Forest',seat='A',ref='caster-domain')
        self.prepare(s,mana={'C':4,'U':1});self.checkpoint(s);self.cast(s,'A',source,['B'])
        hands={p:len(s.state.players[p].zones['hand']) for p in 'ABCD'}
        self.assertIsNone(self.resolve(s));self.assertEqual(hands['B']+5,len(s.state.players['B'].zones['hand']))
        self.assertEqual({p:hands[p] for p in 'ACD'},{p:len(s.state.players[p].zones['hand']) for p in 'ACD'});self.finish(s,private)

    def test_actual_tap_response_changes_the_quantity_before_resolution(self):
        s,private=self.start(717050);source=self.add(s.engine,'Theft of Dreams',zone='hand')
        target=self.add(s.engine,'Grizzly Bears',seat='B',ref='responding-tap-target')
        tapper=self.add(s.engine,'Icy Manipulator',seat='B');self.assert_closed(s,source);self.assert_closed(s,tapper)
        s.state.players['B'].mana_pool.update({'C':1})
        self.prepare(s,mana={'C':2,'U':1});self.checkpoint(s);self.cast(s,'A',source,['B'])
        self.priority(s,'B')
        actions=s.packet('pilot:B',full=True)['decision']['ctx']['legal']['actions']
        action=next(a for a in actions if a['id'].startswith('activate:'+tapper.ref+':'))
        command={'action_id':action['id'],'targets':[target.ref],'pay':'auto'}
        self.reject(s,'D',command);result=s.act('pilot:B',command);self.assertTrue(result.ok,result.summary)
        hand=len(s.state.players['A'].zones['hand']);s=self.replay(s,load=True)
        self.assertIsNone(self.resolve(s));self.assertTrue(s.state.cards[target.object_id].tapped)
        self.assertEqual(hand+1,len(s.state.players['A'].zones['hand']));self.finish(s,private)

    def test_actual_entry_source_and_counted_creature_leave_before_trigger_resolves(self):
        s,private=self.start(717051);source=self.add(s.engine,'Honorable Scout',zone='hand')
        counted=self.add(s.engine,'Walking Corpse',seat='B',ref='leaving-counted-black')
        self.add(s.engine,'Walking Corpse',seat='B',ref='remaining-counted-black')
        bounce1=self.add(s.engine,'Unsummon',seat='B',zone='hand',ref='bounce-scout')
        bounce2=self.add(s.engine,'Unsummon',seat='B',zone='hand',ref='bounce-counted')
        for card in (source,bounce1,bounce2):self.assert_closed(s,card)
        s.state.players['B'].mana_pool.update({'U':2})
        self.prepare(s,mana={'W':1});self.checkpoint(s);self.cast(s,'A',source)
        self.assertEqual('semantic.target',self.resolve(s).kind);self.target(s,'A',['B'])
        self.cast(s,'B',bounce1,[source.ref]);self.cast(s,'B',bounce2,[counted.ref])
        s=self.replay(s,load=True);self.assertIsNone(self.resolve(s))
        self.assertEqual(('hand','hand'),(s.state.cards[source.object_id].zone,s.state.cards[counted.object_id].zone))
        self.assertEqual([42,40,40,40],[s.state.players[p].life for p in 'ABCD']);self.finish(s,private)

    def test_actual_copy_uses_its_own_controller_and_new_target_without_changing_original(self):
        s,private=self.start(717052);source=self.add(s.engine,'Renewing Dawn',zone='hand')
        copier=self.add(s.engine,'Reverberate',seat='B',zone='hand')
        for p,n in (('A',1),('B',3),('D',2)):
            for i in range(n):self.add(s.engine,'Mountain',seat=p,ref=f'copy-mountain-{p}-{i}')
        self.assert_closed(s,source);self.assert_closed(s,copier);s.state.players['B'].mana_pool.update({'R':2})
        self.prepare(s,mana={'C':1,'W':1});self.checkpoint(s);self.cast(s,'A',source,['B']);original=s.state.stack[-1].ref
        self.cast(s,'B',copier,[original]);self.assertEqual('semantic.choice',self.resolve(s).kind)
        self.reject(s,'A',{'action_id':'choose','targets':['D']});s=self.replay(s,load=True)
        result=s.act('pilot:B',{'action_id':'choose','targets':['D']});self.assertTrue(result.ok,result.summary)
        copied=next(i for i in s.state.stack if i.kind=='spell_copy')
        self.assertEqual(('B',['D']),(copied.controller,copied.targets));self.assertEqual(['B'],next(i for i in s.state.stack if i.ref==original).targets)
        s=self.replay(s,load=True);self.assertIsNone(self.resolve(s))
        self.assertEqual([46,44,40,40],[s.state.players[p].life for p in 'ABCD'])
        self.assertEqual(2,len(s.engine._current_turn_history('spell_cast')));self.finish(s,private)

    def test_actual_departed_sole_target_fizzles_before_query_without_refunding_price(self):
        s,private=self.start(717053);source=self.add(s.engine,'Renewing Dawn',zone='hand');self.assert_closed(s,source)
        self.add(s.engine,'Mountain',seat='B');self.prepare(s,mana={'C':1,'W':1});self.checkpoint(s)
        self.cast(s,'A',source,['B']);self.priority(s,'B')
        result=s.act('pilot:B',{'action_id':'concede','choices':{'confirm_concede':True}});self.assertTrue(result.ok,result.summary)
        s=self.replay(s,load=True)
        with patch('quorune.semantic_runtime.target_player_query_amounts.resolve_target_player_query_amount',side_effect=AssertionError('Illegal sole target must not query')):
            self.assertIsNone(self.resolve(s))
        self.assertEqual([40]*3,[s.state.players[p].life for p in 'ACD']);self.assertEqual('graveyard',s.state.cards[source.object_id].zone)
        self.assertEqual(0,sum(s.state.players['A'].mana_pool.values()));self.finish(s,private)

    def test_actual_opponent_target_excludes_caster_while_any_player_zero_draw_can_target_self(self):
        s,private=self.start(717054);life=self.add(s.engine,'Renewing Dawn',zone='hand');draw=self.add(s.engine,'Allied Strategies',zone='hand')
        self.assert_closed(s,life);self.assert_closed(s,draw)
        self.prepare(s,mana={'C':5,'W':1,'U':1});self.checkpoint(s);self.priority(s,'A')
        action=self.action(s,'A',life);self.reject(s,'A',{'action_id':action['id'],'targets':['A'],'pay':'auto'})
        self.cast(s,'A',life,['B']);self.assertIsNone(self.resolve(s));self.assertEqual(40,s.state.players['A'].life)
        self.cast(s,'A',s.state.cards[draw.object_id],['A']);hand=len(s.state.players['A'].zones['hand'])
        self.assertIsNone(self.resolve(s));self.assertEqual(hand,len(s.state.players['A'].zones['hand']));self.finish(s,private)

    def test_actual_dredge_pause_reload_keeps_one_numeric_draw_amount(self):
        s,private=self.start(717055);source=self.add(s.engine,'Theft of Dreams',zone='hand')
        dredger=self.add(s.engine,'Dakmor Salvage',zone='graveyard')
        self.assert_closed(s,source);self.assert_closed(s,dredger)
        for i in range(2):self.add(s.engine,'Grizzly Bears',seat='B',ref=f'dredge-tapped-{i}').tapped=True
        self.prepare(s,mana={'C':2,'U':1});self.checkpoint(s);self.cast(s,'A',source,['B'])
        hand=len(s.state.players['A'].zones['hand'])
        import quorune.semantic_runtime.target_player_query_amounts as owner
        original=owner.resolve_target_player_query_amount;observed=[]
        def count(*args):observed.append(True);return original(*args)
        with patch.object(owner,'resolve_target_player_query_amount',side_effect=count):
            self.assertEqual('draw.replacement',self.resolve(s).kind)
        self.assertEqual([True],observed);s=self.replay(s,load=True)
        option=next(o for o in s.packet('pilot:A',full=True)['decision']['ctx']['options'] if 'Dredge' in o['label'])
        with patch.object(owner,'resolve_target_player_query_amount',side_effect=AssertionError('Draw continuation must retain its numeric amount')):
            result=s.act('pilot:A',{'action_id':'choose','choice':option['id']});self.assertTrue(result.ok,result.summary)
            self.assertIsNone(self.resolve(s))
        self.assertEqual(hand+2,len(s.state.players['A'].zones['hand']));self.assertEqual('hand',s.state.cards[dredger.object_id].zone)
        self.finish(s,private)

    def test_actual_committed_draw_fault_rolls_back_the_whole_result_then_retries(self):
        from quorune.drawing import transaction
        from quorune.record import authoritative_state_hash
        from quorune.errors import GameRuleError
        s,private=self.start(717056);source=self.add(s.engine,'Theft of Dreams',zone='hand');self.assert_closed(s,source)
        for i in range(2):self.add(s.engine,'Grizzly Bears',seat='B',ref=f'fault-tapped-{i}').tapped=True
        self.prepare(s,mana={'C':2,'U':1});self.checkpoint(s);self.cast(s,'A',source,['B'])
        hand=len(s.state.players['A'].zones['hand']);original=transaction._record_draw;observed=[];failed=None
        def fault(*args,**kwargs):
            value=original(*args,**kwargs);observed.append(len(s.state.players['A'].zones['hand']))
            raise GameRuleError('Injected committed query draw failure')
        with patch.object(transaction,'_record_draw',side_effect=fault):
            for _ in range(8):
                principal=s.pending_principals()[0];before=authoritative_state_hash(s.state);result=s.act(principal,{'action_id':'pass'})
                if not result.ok:
                    self.assertEqual(before,authoritative_state_hash(s.state));failed=principal;break
        self.assertEqual([hand+1],observed);self.assertIsNotNone(failed);self.assertEqual(hand,len(s.state.players['A'].zones['hand']))
        s=self.replay(s,load=True);result=s.act(failed,{'action_id':'pass'});self.assertTrue(result.ok,result.summary)
        self.assertIsNone(self.resolve(s));self.assertEqual(hand+2,len(s.state.players['A'].zones['hand']));self.finish(s,private)

    def life_witness(self,seed):
        s,private=self.start(seed);source=self.add(s.engine,'Renewing Dawn',zone='hand');self.assert_closed(s,source)
        for p,n in (('A',1),('B',3)):
            for i in range(n):self.add(s.engine,'Mountain',seat=p,ref=f'witness-mountain-{p}-{i}')
        self.prepare(s,mana={'C':1,'W':1});self.checkpoint(s);self.cast(s,'A',source,['B'])
        self.assertIsNone(self.resolve(s));self.assertEqual(46,s.state.players['A'].life);return s,private

    def test_fresh_native_wrong_player_mutant_is_killed_after_actual_paid_cast(self):
        observed=[]
        def wrong_player(host,value,item):
            observed.append((item.controller,tuple(item.targets)))
            return resolve_public_query_amount(host,TargetAmount.from_dict(value).amount.to_dict(),item)
        with patch('quorune.semantic_runtime.target_player_query_amounts.resolve_target_player_query_amount',side_effect=wrong_player):
            with self.assertRaises(AssertionError):self.life_witness(717057)
        self.assertEqual([('A',('B',))],observed)

    def test_genuinely_verified_query_authority_withdrawal_rejects_originals_and_keeps_unrelated_draw(self):
        from copy import deepcopy
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index,discover_tests
        from test_bound_effect_programs import record
        original=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        declarations=read_evidence_document(ROOT/'platform/capability-evidence-declarations.json')
        discovered=set(discover_tests(ROOT).values())
        for cap in (CAP,'quantity_expression.public_query_effect_amount'):
            raw=deepcopy(original);next(r for r in raw['capabilities'] if r['id']==cap).update(status='blocked',blockers=['Target query authority withdrawn'])
            registry=CapabilityRegistry(raw);index=build_index(registry_value=raw,declaration_source=declarations,discovered_test_ids=discovered)
            registry.mark_evidence_verified(validate_capability_evidence_index(index,registry=registry))
            for name in ORIGINALS:
                self.assertNotEqual('exact',compile_oracle_card(self.db.lookup(name),capability_registry=registry,capability_profile='commander_review').status,name)
            self.assertEqual('exact',compile_oracle_card(record('Draw a card.'),capability_registry=registry,capability_profile='commander_review').status)
