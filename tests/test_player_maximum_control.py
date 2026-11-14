"""CR603.4: current unique maximum, rather than occurrence-time winner."""
import json,unittest
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import patch
import test_recipient_control as recipients
import test_fixed_creature_power_damage as records
from inherited_composition_fixture import InheritedNativeFixture
from quorune.player_maximum_model import PlayerMaximumSpec,PLAYER_MAXIMUM_CAPABILITY as CAP
from quorune.player_maximum import current_player_maximum
from quorune.continuous_conditions import FixedPublicStateConditionSpec
from quorune.compiler.player_maximum_templates import player_maximum_condition
from quorune.oracle_ir import compile_oracle_card
from quorune.rules.capabilities import load_default_capability_registry
from quorune.object_query import ObjectQueryResult

NAMES=('Ghazb\u00e1n Ogre','Wild Dogs','Wild Mammoth','Sokenzan Renegade','Thoughtbound Primoc')


class PlayerMaximumModelCompilerTests(unittest.TestCase):
    def world(self):
        players={p:SimpleNamespace(in_game=True,life=40,zones={'hand':['private-'+p]}) for p in 'ABCD'}
        return SimpleNamespace(players=players,turn_order=list('ABCD'),cards={})

    def test_unique_maximum_uses_all_active_public_values_and_rejects_unavailable_data(self):
        state=self.world();life=PlayerMaximumSpec('life');hand=PlayerMaximumSpec('hand_count')
        self.assertIsNone(current_player_maximum(state,life))
        state.players['B'].life=42;self.assertEqual('B',current_player_maximum(state,life))
        state.players['C'].life=43;self.assertEqual('C',current_player_maximum(state,life))
        state.players['C'].in_game=False;self.assertEqual('B',current_player_maximum(state,life))
        state.players['B'].life=True
        with self.assertRaises(ValueError):current_player_maximum(state,life)
        state.players['B'].zones['hand']=['secret-b1','secret-b2']
        self.assertEqual('B',current_player_maximum(state,hand))
        state.players['B'].zones['hand']=None
        with self.assertRaises(ValueError):current_player_maximum(state,hand)

    def test_permanent_metric_uses_current_controller_type_and_phasing(self):
        state=self.world()
        for index,controller in enumerate('BBC'):
            card=SimpleNamespace(object_id=str(index),controller=controller,owner='D',zone='battlefield',phased_out=False)
            state.cards[card.object_id]=card
        def row(card):return ObjectQueryResult(card.object_id,card.object_id,'Wizard','D',card.controller,'battlefield',types=('creature',),subtypes=('wizard',))
        for metric in ('creature_count','subtype:wizard'):
            spec=PlayerMaximumSpec(metric)
            self.assertEqual('B',current_player_maximum(state,spec,public_object_resolver=row))
            state.cards['1'].phased_out=True;self.assertIsNone(current_player_maximum(state,spec,public_object_resolver=row))
            state.cards['1'].phased_out=False
            with self.assertRaises(ValueError):current_player_maximum(state,spec)

    def test_closed_metric_condition_and_reference_codecs_and_matching_production(self):
        condition=player_maximum_condition('a player controls more Wizards than each other player')
        self.assertEqual('subtype:wizard',condition.fact_parameter)
        self.assertEqual(condition,FixedPublicStateConditionSpec.from_dict(condition.to_dict()))
        reference=PlayerMaximumSpec('life').to_reference()
        for change in ({'schema_version':True},{'metric':'unknown'},{'metric':True},{'extra':1}):
            with self.subTest(change=change),self.assertRaises(ValueError):PlayerMaximumSpec.from_reference({**reference,**change})
        for change in ({'amount':2},{'schema_version':3},{'fact_parameter':'unknown'}):
            with self.subTest(change=change),self.assertRaises(ValueError):FixedPublicStateConditionSpec.from_dict({**condition.to_dict(),**change})
        registry=load_default_capability_registry()
        text='At the beginning of your upkeep, if a player has more life than each other player, the player with the most life gains control of this creature.'
        ir=compile_oracle_card(records.card_record(text,type_line='Creature — Beast'),capability_registry=registry)
        self.assertEqual('exact',ir.status,ir.material_residuals)
        self.assertIn(CAP,ir.faces[0].nodes[0].capability_dependencies)
        self.assertEqual(3,ir.faces[0].nodes[0].effects[0]['schema_version'])
        for bad in (text.replace('most life','most cards in hand'),text.replace('gains control','gains temporary control'),
                'As long as a player has more life than each other player, this creature gets +1/+1.'):
            with self.subTest(text=bad):self.assertNotEqual('exact',compile_oracle_card(records.card_record(bad,type_line='Creature — Beast'),capability_registry=registry).status)

    def test_fresh_metric_witness_kills_selecting_a_tied_maximum(self):
        from quorune import player_maximum
        original=player_maximum.current_player_maximum
        method='test_unique_maximum_uses_all_active_public_values_and_rejects_unavailable_data'
        getattr(type(self)(method),method)()
        def wrong(state,spec,**kwargs):return original(state,spec,**kwargs) or state.turn_order[0]
        with patch(__name__+'.current_player_maximum',wrong):
            with self.assertRaises(AssertionError):getattr(type(self)(method),method)()


class PlayerMaximumNativeTests(InheritedNativeFixture,unittest.TestCase):
    setUpClass=classmethod(recipients.RecipientControlNativeTests.setUpClass.__func__)
    scene=recipients.RecipientControlNativeTests.scene
    reject=recipients.RecipientControlNativeTests.reject
    finish=recipients.RecipientControlNativeTests.finish
    finish_turn=recipients.RecipientControlNativeTests.finish_turn

    def prepare(self,s,*,mana):
        # Define the initial checkpoint before the one relevant turn boundary.
        InheritedNativeFixture.prepare(self,s,mana=mana)
        from quorune.engine import TURN_STEPS
        s.engine.permissions.invalidate_current();s.state.pending_decision=None
        s.state.active_player='D';s.state.phase='ending';s.state.step='end_step'
        s.state.last_normal_turn_player='D'
        s.state.current_turn=replace(s.state.current_turn,player='D')
        s.state.phase_index=TURN_STEPS.index(('ending','end_step'));s.state.priority_passes=[]
        s.engine._grant_priority('D');s.engine.pump()

    def board(self,name,*,tie_response=False,initial_tie=False,zone='battlefield'):
        extra={};metric=('hand' if name=='Sokenzan Renegade' else 'wizard' if name=='Thoughtbound Primoc' else 'creature' if name=='Wild Mammoth' else 'life')
        def before(s,source,gift):
            for i in range(6):self.add(s.engine,'Island',seat='C',ref='maximum-island-'+str(i))
            self.add(s.engine,'Generic Bound Plains',seat='C',ref='maximum-white-C')
            self.add(s.engine,'Mountain',seat='C',ref='maximum-red-C')
            self.add(s.engine,'Generic Bound Plains',ref='maximum-white-A')
            extra['blink']=self.add(s.engine,'Cloudshift',zone='hand')
            response={'life':'Revitalize','hand':'Opportunity','creature':'Raise the Alarm','wizard':'Turn Against'}[metric]
            extra['response']=self.add(s.engine,response,seat='C',zone='hand')
            if metric=='life':
                s.state.players['B'].life=43 if tie_response else 42
                if initial_tie:s.state.players['C'].life=s.state.players['B'].life
            elif metric=='hand':
                for seat,size in {'A':8,'B':12,'C':10,'D':9}.items():
                    while len(s.state.players[seat].zones['hand'])<size:
                        self.add(s.engine,'Generic Bound Plains',seat=seat,zone='hand',ref='maximum-hand-'+seat+'-'+str(len(s.state.players[seat].zones['hand'])))
            elif metric=='creature':
                for seat,count in (('B',4),('C',2 if tie_response else 3)):
                    for i in range(count):
                        c=self.add(s.engine,'Grizzly Bears',seat=seat,ref='maximum-creature-'+seat+str(i));c.owner='D'
            else:
                for seat in 'BC':
                    for i in range(2):self.add(s.engine,'Fugitive Wizard',seat=seat,ref='maximum-wizard-'+seat+str(i))
                extra['changeling']=self.add(s.engine,'Universal Automaton',seat='B',ref='maximum-changeling');extra['changeling'].owner='D'
        s,source,gift,private=self.scene(name,zone=zone,before=before)
        return s,source,gift,private,extra,metric

    def upkeep(self,s):
        self.finish_turn(s)
        self.assertEqual(('A','upkeep'),(s.state.active_player,s.state.step))

    def test_five_originals_transfer_to_the_current_unique_maximum_at_actual_upkeep(self):
        for name in NAMES:
            with self.subTest(name=name):
                s,source,gift,private,extra,metric=self.board(name);self.upkeep(s)
                self.assertEqual(1,self.trigger_count(s,source));s=self.replay(s,load=True);self.assertIsNone(self.resolve(s))
                self.assertEqual(('B','A'),(s.state.cards[source.object_id].controller,s.state.cards[source.object_id].owner))
                if name=='Thoughtbound Primoc':self.assertIn('flying',s.engine._combat_keywords(s.state.cards[source.object_id]))
                self.finish(s,private)

    def changed_winner(self,name='Wild Dogs',*,tie=False):
        s,source,gift,private,extra,metric=self.board(name,tie_response=tie);self.upkeep(s)
        self.assertEqual(1,self.trigger_count(s,source))
        targets=(['A' if tie else 'C'] if metric=='hand' else [extra['changeling'].ref] if metric=='wizard' else [])
        self.cast(s,'C',extra['response'],targets);self.resolve_top(s)
        s=self.replay(s,load=True);self.assertIsNone(self.resolve(s))
        self.assertEqual('A' if tie else 'C',s.state.cards[source.object_id].controller)
        if tie:self.assertFalse(any(e.source_id==source.object_id and e.effect_id.startswith('control-effect:') for e in s.state.continuous_effects))
        self.finish(s,private)

    def test_responses_change_the_winning_life_hand_creature_and_wizard_player(self):
        for name in ('Wild Dogs','Sokenzan Renegade','Wild Mammoth','Thoughtbound Primoc'):
            with self.subTest(name=name):self.changed_winner(name)

    def test_tied_life_hand_and_creature_maximum_on_resolution_prevents_control(self):
        for name in ('Wild Dogs','Sokenzan Renegade','Wild Mammoth'):
            with self.subTest(name=name):self.changed_winner(name,tie=True)

    def test_initial_life_tie_does_not_trigger_and_source_reentry_is_not_given_away(self):
        s,source,gift,private,extra,metric=self.board('Wild Dogs',initial_tie=True);self.upkeep(s)
        self.assertEqual(0,self.trigger_count(s,source));self.finish(s,private)
        s,source,gift,private,extra,metric=self.board('Wild Dogs');self.upkeep(s);old=source.logical_object_id
        self.cast(s,'A',extra['blink'],[source.ref]);self.resolve_top(s)
        s=self.replay(s,load=True);self.assertIsNone(self.resolve(s))
        self.assertNotEqual(old,s.state.cards[source.object_id].logical_object_id);self.assertEqual('A',s.state.cards[source.object_id].controller)
        self.finish(s,private)

    def test_metric_and_intervening_authority_and_matching_condition_survive_coverage_removal(self):
        from quorune.card_programs.binding import bind_semantic_program_runtime
        s,source,gift,private,extra,metric=self.board('Wild Dogs')
        program=next(p for p in s.engine.semantics.programs_for_oracle(source.oracle_id) if any(e.get('schema_version')==3 and e.get('op')=='give_control' for e in p.effects))
        for cap in (CAP,'continuous.control.fixed_recipient','trigger.condition.fixed_public_state'):
            deps=tuple(c for c in program.capability_dependencies if c!=cap)
            stripped=replace(program,coverage=(),capability_dependencies=deps,capability_closure=self.registry.closure(deps,profile='commander_review').to_dict())
            bound=bind_semantic_program_runtime(stripped,capability_registry=self.registry,profile='commander_review')
            self.assertFalse(bound['strict']);self.assertIn('capability:undeclared_runtime_dependency:'+cap,bound['blockers'])
        for condition in (None,{'field':'step','op':'eq','value':'upkeep'}):
            bound=bind_semantic_program_runtime(replace(program,coverage=(),event_condition=condition),capability_registry=self.registry,profile='commander_review')
            self.assertFalse(bound['strict']);self.assertTrue(any('intervening condition' in r for r in bound['blockers']))

    def test_fresh_changed_winner_witness_kills_using_the_occurrence_time_winner(self):
        method='test_responses_change_the_winning_life_hand_creature_and_wizard_player'
        type(self)(method).changed_winner()
        with patch('quorune.player_maximum.resolve_player_maximum',return_value='B'):
            with self.assertRaises(AssertionError):type(self)(method).changed_winner()

    def test_wild_dogs_cycling_and_new_controller_bushido_keep_their_original_owners(self):
        s,source,gift,private,extra,metric=self.board('Wild Dogs',zone='hand')
        self.priority(s,'A');row=next(r for r in s.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions'] if r['id'].startswith('activate:'+source.ref+':'))
        command={'action_id':row['id'],'pay':'auto'};self.reject(s,'B',command)
        before=set(s.state.players['A'].zones['hand'])
        result=s.act('pilot:A',command);self.assertTrue(result.ok,result.summary)
        self.assertEqual('graveyard',s.state.cards[source.object_id].zone)
        self.assertIsNone(self.resolve(s));self.assertEqual(1,len(set(s.state.players['A'].zones['hand'])-before))
        self.finish(s,private)
        s,source,gift,private,extra,metric=self.board('Sokenzan Renegade');self.upkeep(s);self.assertIsNone(self.resolve(s))
        self.assertEqual('B',s.state.cards[source.object_id].controller)
        self.attack(s,[(gift,'B')]);self.until(s,lambda s:s.state.pending_decision.kind=='combat.blockers')
        command={'action_id':'block','blocks':{source.ref:[gift.ref]}};self.reject(s,'C',command)
        result=s.act('pilot:B',command);self.assertTrue(result.ok,result.summary)
        self.assertEqual(1,self.trigger_count(s,source));self.assertIsNone(self.resolve(s))
        self.assertEqual((4,4),tuple(s.engine._numeric_stat(source.object_id,stat) for stat in ('power','toughness')))
        self.finish(s,private)
