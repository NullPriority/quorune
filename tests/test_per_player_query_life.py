"""Pinned CR119.3/608.2h: simultaneous life loss reads independent live counts."""
from dataclasses import FrozenInstanceError,replace
import json,unittest
from types import SimpleNamespace as Row
from unittest.mock import patch
from quorune.characteristic_fragments import CharacteristicQuantitySpec,CharacteristicQuantityScope
from quorune.object_predicate import ObjectQuerySpec
from quorune.query_effect_amount_model import PublicQueryAmountSpec
from quorune.life_state import LifeChange
from quorune.per_player_query_life_model import PerPlayerQueryLifeLossSpec as Spec,BoundPerPlayerLifeLoss as Bound,PlayerLifeLossRelation as Relation
from quorune.semantic_runtime.per_player_query_life import resolve_per_player_query_life
from quorune.compiler.per_player_query_life import per_player_query_life_template
from quorune.oracle_ir import _effect_template


def spec(relation=Relation.ALL):
    return Spec(PublicQueryAmountSpec(CharacteristicQuantitySpec(scope=CharacteristicQuantityScope.CONTROLLER_ZONE,
        query=ObjectQuerySpec(zones=('battlefield',),types_all=('creature',)))),relation)


class PerPlayerQueryLifeModelTests(unittest.TestCase):
    def test_explicit_producer_and_sealed_canonical_changes_round_trip_immutably(self):
        original=spec();self.assertEqual(original,Spec.from_dict(json.loads(json.dumps(original.to_dict()))))
        with self.assertRaises(FrozenInstanceError):original.relation=Relation.OPPONENTS
        with self.assertRaises(ValueError):PublicQueryAmountSpec.from_dict(original.to_dict())
        bound=Bound('A',Relation.ALL,(LifeChange('A',-1),LifeChange('B',-4)))
        self.assertEqual(5,bound.total_loss);self.assertEqual(bound,Bound.from_dict(json.loads(json.dumps(bound.to_dict()))))
        self.assertEqual(0,Bound('A',Relation.ALL,()).total_loss)

    def test_global_hidden_declaration_and_boolean_or_unknown_versions_fail_closed(self):
        original=spec()
        for amount in (replace(original.amount,quantity=replace(original.amount.quantity,scope=CharacteristicQuantityScope.ALL_ZONES)),
                replace(original.amount,quantity=replace(original.amount.quantity,query=ObjectQuerySpec(zones=('hand',)))),
                replace(original.amount,coefficient=-1),replace(original.amount,schema_version=2,binding_id='shared')):
            with self.assertRaises(ValueError):Spec(amount,Relation.ALL)
        for change in ({'schema_version':True},{'schema_version':2},{'target_index':0},{'relation':'controller'}):
            with self.assertRaises(ValueError):Spec.from_dict({**original.to_dict(),**change})

    def test_sealed_batch_rejects_gains_zero_duplicate_players_and_opponent_self(self):
        for rows in ((LifeChange('B',1),),(LifeChange('B',0),),
                (LifeChange('B',-1),LifeChange('B',-2))):
            with self.assertRaises(ValueError):Bound('A',Relation.ALL,rows)
        with self.assertRaises(ValueError):Bound('A',Relation.OPPONENTS,(LifeChange('A',-1),))
        raw=Bound('A',Relation.ALL,(LifeChange('B',-1),)).to_dict()
        with self.assertRaises(ValueError):Bound.from_dict({**raw,'schema_version':True})
        with self.assertRaises(ValueError):Bound.from_dict({**raw,'changes':[{'player':'B','amount':True}]})
        raw['changes'][0]['owner']='A'
        with self.assertRaises(ValueError):Bound.from_dict(raw)


class PerPlayerQueryLifeRuntimeTests(unittest.TestCase):
    def test_all_quantities_seal_in_apnap_order_and_reload_never_requeries(self):
        host=Row(active_seats=['A','B','C','D'],apnap_order=lambda:['B','C','D','A'])
        item=Row(controller='A',ref='stack',context={});seen=[]
        def amount(host,value,item,*,query_player):seen.append(query_player);return {'A':1,'B':4,'C':0,'D':2}[query_player]
        with patch('quorune.semantic_runtime.per_player_query_life.resolve_public_query_amount',side_effect=amount):
            raw=resolve_per_player_query_life(host,spec().to_dict(),item)
        self.assertEqual(['B','C','D','A'],seen);bound=Bound.from_dict(raw)
        self.assertEqual((LifeChange('B',-4),LifeChange('D',-2),LifeChange('A',-1)),bound.changes)
        with patch('quorune.semantic_runtime.per_player_query_life.resolve_public_query_amount',side_effect=AssertionError('Sealed loss must not query')):
            self.assertEqual(raw,resolve_per_player_query_life(host,raw,item));item.controller='B'
            with self.assertRaises(ValueError):resolve_per_player_query_life(host,raw,item)

    def test_life_owner_prepares_one_heterogeneous_canonical_batch_and_zero_prepares_none(self):
        from unittest.mock import Mock
        import quorune.effect_runtime.life_effects as owner
        from quorune.effect_runtime.fractional_life import apply_life_loss
        host=Row(active_seats=['A','B','C','D'],_log=Mock())
        bound=Bound('A',Relation.ALL,(LifeChange('A',-1),LifeChange('B',-4)))
        effect={'op':'lose_life','scope':'all_active_players','amount':bound.to_dict()}
        prepared=Row(records=(Row(direction='loss',amount=1),Row(direction='loss',amount=4)))
        result=Row(to_dict=lambda:{},changed_players=('A','B'))
        with patch.object(owner,'_commit',return_value=prepared) as commit,patch.object(owner,'_result',return_value=result):
            self.assertEqual(5,apply_life_loss(host,effect,actor='A',operation='lose_life',reason='public count'))
        self.assertEqual(1,commit.call_count);self.assertEqual(bound.changes,commit.call_args.args[1])
        effect['amount']=Bound('A',Relation.ALL,()).to_dict()
        with patch.object(owner,'_commit') as commit:
            self.assertEqual(0,apply_life_loss(host,effect,actor='A',operation='lose_life',reason='zero'))
            commit.assert_not_called()

    def test_existing_half_quantity_keeps_its_rounding_and_uniform_scope_router(self):
        from unittest.mock import Mock
        from quorune.fractional_quantity_model import HalfQuantitySpec
        from quorune.effect_runtime.fractional_life import apply_life_loss
        import quorune.effect_runtime.life_effects as owner
        host=Row(active_seats=['A','B'],state=Row(players={'A':Row(life=41),'B':Row(life=40)}),_log=Mock())
        effect={'op':'lose_life','scope':'all_active_players','amount':HalfQuantitySpec('up').to_dict()}
        result=Row(to_dict=lambda:{},changed_players=('A','B'),
            for_player=lambda player:Row(delta=-21 if player=='A' else -20,resolved_loss=21 if player=='A' else 20))
        with patch.object(owner,'_commit',return_value=object()) as commit,patch.object(owner,'_result',return_value=result):
            self.assertEqual(41,apply_life_loss(host,effect,actor='A',operation='lose_life',reason='old half'))
        self.assertEqual((LifeChange('A',-21),LifeChange('B',-20)),commit.call_args.args[1])

    def test_life_owner_rejects_wrong_controller_scope_and_inactive_recipient_before_commit(self):
        from unittest.mock import Mock
        from quorune.errors import GameRuleError
        import quorune.effect_runtime.life_effects as owner
        host=Row(active_seats=['A','B'],_log=Mock())
        bound=Bound('A',Relation.ALL,(LifeChange('B',-4),)).to_dict()
        base={'op':'lose_life','scope':'all_active_players','amount':bound}
        for effect,actor in ((base,'B'),({**base,'player':'B'},'A'),
                ({'op':'lose_life_each_opponent','amount':bound},'A'),
                ({**base,'amount':Bound('A',Relation.ALL,(LifeChange('C',-1),)).to_dict()},'A')):
            with patch.object(owner,'_commit') as commit:
                with self.assertRaises(GameRuleError):owner._apply_lose_life(host,effect,actor=actor,operation=effect['op'],reason='invalid')
                commit.assert_not_called()


class PerPlayerQueryLifeCompilerTests(unittest.TestCase):
    def compile(self,text):
        return per_player_query_life_template(text,source_name='Public Loss Source',
            compile_fixed=lambda body:_effect_template(body,card_name='Public Loss Source'))

    def test_all_player_and_opponent_clauses_keep_existing_life_owner_and_positive_coefficient(self):
        for players,coefficient in (('player',1),('opponent',2)):
            compiled=self.compile(f'Each {players} loses {coefficient} life for each creature they control.')
            self.assertIsNotNone(compiled);effect=compiled[1][0];value=Spec.from_dict(effect['amount'])
            self.assertEqual(coefficient,value.amount.coefficient);self.assertIsNone(compiled[2])
            self.assertEqual('lose_life' if players=='player' else 'lose_life_each_opponent',effect['op'])
            self.assertEqual(Relation.ALL if players=='player' else Relation.OPPONENTS,value.relation)

    def test_unknown_antecedents_private_counts_open_arithmetic_and_material_riders_stay_residual(self):
        text='Each player loses 1 life for each creature they control.'
        for bad in (text.replace('they control','you control'),text.replace('creature','card in hand'),
                text.replace('1 life','X life'),text.replace('1 life','0 life'),text+' Draw a card.'):
            self.assertIsNone(self.compile(bad),bad)

    def test_raw_quantity_result_and_source_frame_minima_do_not_depend_on_coverage(self):
        from quorune.rules.per_player_query_life import per_player_query_life_program_dependencies,is_closed_per_player_query_life_program
        from quorune.card_programs.descriptor_requirements import typed_result_amount_runtime_dependencies
        from quorune.per_player_query_life_model import PER_PLAYER_QUERY_LIFE_CAPABILITY as CAP
        compiled=self.compile('Each opponent loses 1 life for each creature they control.')
        base={'effects':compiled[1],'event':'resolve','active_zone':'stack','ability_id':'spell:front',
            'coverage':(),'target_schema':None,'cost_schema':None,'handlers':()}
        program=Row(**base);required=per_player_query_life_program_dependencies(program)
        self.assertIn(CAP,required);self.assertIn(CAP,typed_result_amount_runtime_dependencies(program.effects))
        program.capability_dependencies=required;self.assertTrue(is_closed_per_player_query_life_program(program))
        program.capability_dependencies=tuple(c for c in required if c!=CAP);self.assertFalse(is_closed_per_player_query_life_program(program))
        entry=Row(**{**base,'event':'permanent.enter.self','active_zone':'battlefield','ability_id':'trigger:front:n1'})
        self.assertEqual(required,per_player_query_life_program_dependencies(entry))
        for change in ({'event':'activate'},{'target_schema':{'count':1}},
                {'handlers':({'amount':compiled[1][0]['amount']},)},
                {'effects':(*compiled[1],{'op':'draw','count':1,'player':'$controller'})}):
            with self.assertRaises(ValueError):per_player_query_life_program_dependencies(Row(**{**base,**change}))

    def test_fresh_caster_count_mutant_is_killed_after_positive_independent_count_witness(self):
        import quorune.semantic_runtime.per_player_query_life as owner
        from quorune.semantic_runtime.query_effect_amounts import resolve_public_query_amount
        host=Row(active_seats=['A','B','C','D'],apnap_order=lambda:['B','C','D','A'])
        item=Row(controller='A',ref='stack',context={});seen=[]
        def count(host,source,quantity):return {'A':1,'B':4,'C':0,'D':2}[source.controller]
        def witness():
            bound=Bound.from_dict(resolve_per_player_query_life(host,spec().to_dict(),item))
            self.assertEqual((LifeChange('B',-4),LifeChange('D',-2),LifeChange('A',-1)),bound.changes)
        def caster(host,value,item,*,query_player):
            seen.append(query_player);return resolve_public_query_amount(host,value,item,query_player=item.controller)
        with patch('quorune.semantic_runtime.query_effect_amounts.query_characteristic_count',side_effect=count):
            witness()
            with patch.object(owner,'resolve_public_query_amount',side_effect=caster):
                with self.assertRaises(AssertionError):witness()
        self.assertEqual(['B','C','D','A'],seen)


from pathlib import Path
import tempfile
from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition,DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.rules.capabilities import load_default_capability_registry
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.per_player_query_life_model import PER_PLAYER_QUERY_LIFE_CAPABILITY as CAP
from scripts.build_test_database import build_fixture_database
import test_target_recipient_results as recipients

ORIGINALS=('Stronghold Discipline','Netherborn Phalanx')
FIXTURE=ROOT/'tests/fixtures/per-player-query-life-cards.json'


class PerPlayerQueryLifeNativeTests(InheritedNativeFixture,unittest.TestCase):
    reject=recipients.TargetRecipientNativeTests.reject
    finish=recipients.TargetRecipientNativeTests.finish

    @classmethod
    def setUpClass(cls):
        cls.temporary=tempfile.TemporaryDirectory();folder=Path(cls.temporary.name)
        base=json.loads((ROOT/'tests/fixtures/bound-effect-program-cards.json').read_text(encoding='utf-8'))
        base['cards']=[r for r in base['cards'] if r['name']=='Generic Bound Plains'];base['rulings']=[]
        path=folder/'basic.json';path.write_text(json.dumps(base),encoding='utf-8')
        build_fixture_database([path,FIXTURE],folder/'per-player-life.sqlite3')
        cls.db=CardDatabase(folder/'per-player-life.sqlite3');cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Per-player public life loss',[DeckEntry('Sliver Hivelord',1,'commander'),
            DeckEntry('Generic Bound Plains',30)],['Sliver Hivelord'])

    def start(self,seed):
        s=self.session(seed)
        while len(s.state.players['A'].zones['hand'])>2:s.engine.move_card(s.state.players['A'].zones['hand'][-1],'library',log=False)
        private=self.add(s.engine,'Counterspell',seat='C',zone='hand',ref='unrelated-private-life-count')
        return s,private

    def lives(self,s):return [s.state.players[p].life for p in 'ABCD']

    def board(self,s):
        rows=[]
        for seat,count in (('A',2),('B',3),('C',1)):
            for i in range(count):
                name='Memnite' if seat=='B' and i==0 else 'Grizzly Bears'
                card=self.add(s.engine,name,seat=seat,ref=f'life-count-creature-{seat}-{i}');rows.append(card)
                if seat=='B' and i==2:card.owner='A'
        self.add(s.engine,'Sol Ring',seat='B');self.add(s.engine,'Swamp',seat='B')
        return rows

    def discipline_witness(self,seed,*,finish=True):
        s,private=self.start(seed);source=self.add(s.engine,'Stronghold Discipline',zone='hand');self.assert_closed(s,source)
        self.board(s);self.prepare(s,mana={'C':2,'B':2});self.checkpoint(s);self.cast(s,'A',source)
        self.assertIsNone(self.resolve(s));self.assertEqual([38,37,39,40],self.lives(s))
        self.assertEqual('graveyard',s.state.cards[source.object_id].zone);self.assertEqual(0,sum(s.state.players['A'].mana_pool.values()))
        if finish:self.finish(s,private)
        return s,private

    def test_both_complete_originals_close_normally_with_raw_quantity_and_frame_minima(self):
        s,private=self.start(719000)
        for name in ORIGINALS:
            source=self.add(s.engine,name,zone='hand');self.assert_closed(s,source)
            self.assertEqual('exact',compile_oracle_card(self.db.lookup(name),capability_registry=self.registry,capability_profile='commander_review').status)
            program=next(p for p in s.engine.semantics.programs_for_oracle(source.oracle_id) if CAP in p.capability_dependencies)
            for cap in (CAP,'quantity_expression.public_query_effect_amount','life.change.effect'):
                deps=tuple(c for c in program.capability_dependencies if c!=cap)
                changed=replace(program,coverage=(),capability_dependencies=deps,
                    capability_closure=self.registry.closure(deps,profile='commander_review').to_dict() if deps else None)
                bound=bind_semantic_program_runtime(changed,capability_registry=self.registry,profile='commander_review')
                self.assertFalse(bound['strict']);self.assertIn('capability:undeclared_runtime_dependency:'+cap,bound['blockers'])
            self.assertFalse(bind_semantic_program_runtime(replace(program,event='activate',coverage=()),
                capability_registry=self.registry,profile='commander_review')['strict'])

    def test_actual_discipline_commits_independent_current_control_counts_in_one_life_batch(self):
        import quorune.effect_runtime.life_effects as owner
        original=owner._commit;observed=[]
        def commit(host,changes,**kwargs):
            changes=tuple(changes);observed.append(changes);self.assertEqual([40]*4,[host.state.players[p].life for p in 'ABCD'])
            return original(host,changes,**kwargs)
        with patch.object(owner,'_commit',side_effect=commit):s,private=self.discipline_witness(719010,finish=False)
        self.assertEqual([(LifeChange('A',-2),LifeChange('B',-3),LifeChange('C',-1))],observed)
        self.finish(s,private)

    def test_actual_netherborn_cast_keeps_transmute_sibling_and_entry_loss_only_for_opponents(self):
        s,private=self.start(719011);source=self.add(s.engine,'Netherborn Phalanx',zone='hand');self.assert_closed(s,source)
        self.board(s);self.prepare(s,mana={'C':5,'B':1});self.checkpoint(s);self.cast(s,'A',source)
        self.assertIsNone(self.resolve(s));self.assertEqual([40,37,39,40],self.lives(s))
        self.assertEqual('battlefield',s.state.cards[source.object_id].zone);self.assertEqual(0,sum(s.state.players['A'].mana_pool.values()))
        self.finish(s,private)

    def test_actual_netherborn_transmute_pays_discards_then_privately_searches_reveals_and_shuffles(self):
        s,private=self.start(719012);source=self.add(s.engine,'Netherborn Phalanx',zone='hand');self.assert_closed(s,source)
        match=self.add(s.engine,'Craw Wurm',zone='library',ref='correct-six-mana-value')
        wrong=self.add(s.engine,'Rumbling Baloth',zone='library',ref='incorrect-four-mana-value')
        self.prepare(s,mana={'C':1,'B':2});self.checkpoint(s);self.priority(s,'A')
        action=next(a for a in s.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
            if a['id'].startswith('activate:'+source.ref+':'))
        command={'action_id':action['id'],'pay':'auto'};self.reject(s,'D',command)
        result=s.act('pilot:A',command);self.assertTrue(result.ok,result.summary)
        self.assertEqual('graveyard',s.state.cards[source.object_id].zone);self.assertEqual(0,sum(s.state.players['A'].mana_pool.values()))
        s=self.replay(s,load=True);self.assertEqual('semantic.search',self.resolve(s).kind)
        options={r['id'] for r in s.packet('pilot:A',full=True)['decision']['ctx']['search_cards']}
        self.assertIn(match.ref,options);self.assertNotIn(wrong.ref,options)
        for viewer in 'BCD':self.assertNotIn(match.ref,json.dumps(s.packet('pilot:'+viewer,full=True)))
        self.reject(s,'D',{'action_id':'choose','cards':[match.ref]});self.reject(s,'A',{'action_id':'choose','cards':[wrong.ref]})
        s=self.replay(s,load=True);result=s.act('pilot:A',{'action_id':'choose','cards':[match.ref]});self.assertTrue(result.ok,result.summary)
        self.assertEqual('hand',s.state.cards[match.object_id].zone);self.assertEqual([40]*4,self.lives(s))
        for viewer in 'BCD':self.assertIn(match.ref,json.dumps(s.packet('pilot:'+viewer,full=True)))
        self.assertEqual(1,sum(e.code=='library.shuffle' for e in s.state.events));self.finish(s,private)

    def test_actual_zero_life_counts_still_pay_full_spell_price(self):
        s,private=self.start(719013);source=self.add(s.engine,'Stronghold Discipline',zone='hand');self.assert_closed(s,source)
        self.add(s.engine,'Sol Ring',seat='B');self.add(s.engine,'Swamp',seat='C')
        self.prepare(s,mana={'C':2,'B':2});self.checkpoint(s);self.cast(s,'A',source)
        self.assertIsNone(self.resolve(s));self.assertEqual([40]*4,self.lives(s));self.assertEqual(0,sum(s.state.players['A'].mana_pool.values()))
        self.assertEqual('graveyard',s.state.cards[source.object_id].zone);self.finish(s,private)

    def test_actual_bounce_response_changes_only_the_current_controlled_creature_count(self):
        s,private=self.start(719020);source=self.add(s.engine,'Stronghold Discipline',zone='hand');self.assert_closed(s,source)
        rows=self.board(s);target=next(c for c in rows if c.controller=='B')
        bounce=self.add(s.engine,'Boomerang',seat='B',zone='hand');self.assert_closed(s,bounce)
        s.state.players['B'].mana_pool.update({'U':2})
        self.prepare(s,mana={'C':2,'B':2});self.checkpoint(s);self.cast(s,'A',source);self.cast(s,'B',bounce,[target.ref])
        s=self.replay(s,load=True);self.assertIsNone(self.resolve(s));self.assertEqual([38,38,39,40],self.lives(s))
        self.assertEqual('hand',s.state.cards[target.object_id].zone);self.finish(s,private)

    def test_actual_netherborn_trigger_survives_departed_source_with_locked_controller(self):
        s,private=self.start(719021);source=self.add(s.engine,'Netherborn Phalanx',zone='hand');self.assert_closed(s,source);self.board(s)
        bounce=self.add(s.engine,'Boomerang',seat='B',zone='hand');self.assert_closed(s,bounce)
        s.state.players['B'].mana_pool.update({'U':2})
        self.prepare(s,mana={'C':5,'B':1});self.checkpoint(s);self.cast(s,'A',source)
        self.until(s,lambda game:game.state.cards[source.object_id].zone=='battlefield' and bool(game.state.stack))
        self.cast(s,'B',bounce,[s.state.cards[source.object_id].ref]);s=self.replay(s,load=True)
        self.assertIsNone(self.resolve(s));self.assertEqual('hand',s.state.cards[source.object_id].zone)
        self.assertEqual([40,37,39,40],self.lives(s));self.finish(s,private)

    def test_actual_clone_entry_uses_copy_controller_and_copied_netherborn_ability(self):
        s,private=self.start(719022);self.board(s)
        original=self.add(s.engine,'Netherborn Phalanx',seat='A',ref='public-original-phalanx')
        source=self.add(s.engine,'Clone',seat='B',zone='hand');self.assert_closed(s,original);self.assert_closed(s,source)
        self.prepare(s,actor='B',active='B',mana={'C':3,'U':1});self.checkpoint(s);self.cast(s,'B',source)
        self.assertEqual('replacement.order',self.resolve(s).kind);s=self.replay(s,load=True)
        option=next(o['id'] for o in s.packet('pilot:B',full=True)['decision']['ctx']['options']
            if original.ref in o['id'] and not o['id'].startswith('decline:'))
        command={'action_id':'choose','replacement':option};self.reject(s,'D',command)
        result=s.act('pilot:B',command);self.assertTrue(result.ok,result.summary);self.assertIsNone(self.resolve(s))
        self.assertEqual('Netherborn Phalanx',s.engine.display_name(source.object_id))
        self.assertEqual([37,40,39,40],self.lives(s));self.finish(s,private)

    def test_actual_life_loss_ignores_damage_shield_which_still_prevents_a_later_bolt(self):
        s,private=self.start(719023);source=self.add(s.engine,'Stronghold Discipline',zone='hand');self.board(s)
        shield=self.add(s.engine,'Mending Hands',seat='B',zone='hand');bolt=self.add(s.engine,'Lightning Bolt',zone='hand')
        self.add(s.engine,'Mountain',seat='A')
        for card in (source,shield,bolt):self.assert_closed(s,card)
        s.state.players['B'].mana_pool.update({'W':1})
        self.prepare(s,mana={'C':2,'B':2});self.checkpoint(s);self.cast(s,'A',source);self.cast(s,'B',shield,['B'])
        self.assertIsNone(self.resolve(s));self.assertEqual([38,37,39,40],self.lives(s))
        self.cast(s,'A',s.state.cards[bolt.object_id],['B']);self.assertIsNone(self.resolve(s))
        self.assertEqual([38,37,39,40],self.lives(s));self.finish(s,private)

    def test_actual_all_losses_precede_elimination_and_removal_of_owned_borrowed_creatures(self):
        s,private=self.start(719024);source=self.add(s.engine,'Stronghold Discipline',zone='hand');self.assert_closed(s,source)
        rows=self.board(s);borrowed=next(c for c in rows if c.controller=='B' and c.owner=='A')
        s.state.players['A'].life=2;self.prepare(s,mana={'C':2,'B':2});self.checkpoint(s);self.cast(s,'A',source)
        self.assertIsNone(self.resolve(s));self.assertFalse(s.state.players['A'].in_game)
        self.assertEqual([37,39,40],[s.state.players[p].life for p in 'BCD'])
        self.assertNotEqual('battlefield',s.state.cards[borrowed.object_id].zone);self.finish(s,private)

    def test_actual_committed_life_batch_fault_rolls_back_every_player_then_retries(self):
        from quorune.errors import GameRuleError
        from quorune.record import authoritative_state_hash
        import quorune.effect_runtime.life_effects as owner
        s,private=self.start(719025);source=self.add(s.engine,'Stronghold Discipline',zone='hand');self.assert_closed(s,source);self.board(s)
        self.prepare(s,mana={'C':2,'B':2});self.checkpoint(s);self.cast(s,'A',source)
        original=owner.commit_life_change_batch;observed=[];failed=None
        def fault(*args,**kwargs):
            result=original(*args,**kwargs);observed.append(self.lives(s));raise GameRuleError('Injected committed per-player life failure')
        with patch.object(owner,'commit_life_change_batch',side_effect=fault):
            for _ in range(8):
                principal=s.pending_principals()[0];before=authoritative_state_hash(s.state);result=s.act(principal,{'action_id':'pass'})
                if not result.ok:
                    self.assertEqual(before,authoritative_state_hash(s.state));failed=principal;break
        self.assertEqual([[38,37,39,40]],observed);self.assertIsNotNone(failed);self.assertEqual([40]*4,self.lives(s))
        s=self.replay(s,load=True);result=s.act(failed,{'action_id':'pass'});self.assertTrue(result.ok,result.summary)
        self.assertIsNone(self.resolve(s));self.assertEqual([38,37,39,40],self.lives(s));self.finish(s,private)

    def test_fresh_native_caster_count_mutant_is_killed_after_actual_complete_paid_spell(self):
        import quorune.semantic_runtime.per_player_query_life as owner
        original=owner.resolve_public_query_amount;observed=[]
        def caster(host,value,item,*,query_player):
            observed.append(query_player);return original(host,value,item,query_player=item.controller)
        with patch.object(owner,'resolve_public_query_amount',side_effect=caster):
            with self.assertRaises(AssertionError):self.discipline_witness(719026,finish=False)
        self.assertEqual(['A','B','C','D'],observed)

    def test_genuinely_verified_quantity_or_life_owner_withdrawal_preserves_unrelated_draw(self):
        from copy import deepcopy
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index,discover_tests
        from test_bound_effect_programs import record
        original=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        declarations=read_evidence_document(ROOT/'platform/capability-evidence-declarations.json');known=set(discover_tests(ROOT).values())
        for cap in (CAP,'quantity_expression.public_query_effect_amount','life.change.effect'):
            raw=deepcopy(original);next(r for r in raw['capabilities'] if r['id']==cap).update(status='blocked',blockers=['Public life quantity authority withdrawn'])
            registry=CapabilityRegistry(raw);index=build_index(registry_value=raw,declaration_source=declarations,discovered_test_ids=known)
            registry.mark_evidence_verified(validate_capability_evidence_index(index,registry=registry))
            for name in ORIGINALS:self.assertNotEqual('exact',compile_oracle_card(self.db.lookup(name),capability_registry=registry,capability_profile='commander_review').status,name)
            self.assertEqual('exact',compile_oracle_card(record('Draw a card.'),capability_registry=registry,capability_profile='commander_review').status)
            if cap!='life.change.effect':
                self.assertEqual('exact',compile_oracle_card(record('Each player loses 1 life.'),capability_registry=registry,capability_profile='commander_review').status)
