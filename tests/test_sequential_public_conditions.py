"""Printed-order current conditions reuse the normal effect cursor."""
from copy import deepcopy
from dataclasses import replace
import json,unittest
from types import SimpleNamespace
from unittest.mock import patch
import test_fixed_creature_power_damage as records
import test_recipient_control as recipients
from inherited_composition_fixture import InheritedNativeFixture
from quorune.oracle_ir import compile_oracle_card
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.sequential_condition_shapes import sequential_condition_node_capabilities,sequential_condition_runtime_dependencies
from quorune.compiler.sequential_public_conditions import SEQUENTIAL_CONDITION_CAPABILITY as CAP


TEXT=('Create a Treasure token if an opponent controls more lands than you. '
      'You gain 4 life if an opponent has more life than you. '
      'Create two 1/1 blue Fish creature tokens if an opponent controls more creatures than you. '
      'Draw a card if an opponent has more cards in hand than you.')


class SequentialConditionModelCompilerTests(unittest.TestCase):
    def compile(self,text):
        return compile_oracle_card(records.card_record(text),capability_registry=load_default_capability_registry(),capability_profile='commander_review')

    def test_four_complete_leaves_keep_conditions_results_and_printed_order(self):
        ir=self.compile(TEXT);self.assertEqual('exact',ir.status,ir.material_residuals)
        node,=ir.faces[0].nodes;self.assertIn(CAP,node.capability_dependencies)
        self.assertEqual(['create_token','life','create_token','draw'],[e['effects'][0]['op'] for e in node.effects])
        self.assertEqual(['land_count','life','creature_count','hand_count'],[e['condition']['fact_parameter'] for e in node.effects])
        for mutation in ('unknown','nested','target','prefix','five'):
            effects=deepcopy(list(node.effects));schema=None
            if mutation=='unknown':effects[0]['extra']=True
            elif mutation=='nested':effects[0]['effects']=[deepcopy(effects[1])]
            elif mutation=='target':schema={'zones':['player'],'categories':['player'],'count':1}
            elif mutation=='prefix':effects[0]['prefix_mechanic_ids']=['cr-121-drawing-a-card']
            else:effects.append(deepcopy(effects[0]))
            with self.subTest(mutation=mutation):
                self.assertEqual((),sequential_condition_node_capabilities(effects=effects,target_schema=schema,mechanic_ids=node.mechanics))
                with self.assertRaises(ValueError):sequential_condition_runtime_dependencies(SimpleNamespace(effects=effects,target_schema=schema,provenance={'template_id':'sequential-public-condition-program-v1'}))

    def test_unknown_siblings_and_alternative_branches_do_not_gain_sequence_closure(self):
        for text in (TEXT+' Invent a new rule.',TEXT+' Draw a card if you have an unknown designation.',
                'If an opponent controls more lands than you, draw a card. Otherwise, gain 2 life. '+TEXT,
                'Draw a card if an opponent controls more lands than you. Target creature gets +1/+1 if an opponent has more life than you.'):
            with self.subTest(text=text):self.assertNotEqual('exact',self.compile(text).status)


class SequentialConditionNativeTests(InheritedNativeFixture,unittest.TestCase):
    setUpClass=classmethod(recipients.RecipientControlNativeTests.setUpClass.__func__)
    scene=recipients.RecipientControlNativeTests.scene
    reject=recipients.RecipientControlNativeTests.reject
    finish=recipients.RecipientControlNativeTests.finish

    def board(self,flags,*,name='Beza, the Bounding Spring',theft=False):
        extra={}
        def before(s,source,gift):
            for seat,count in (('A',3),('B',4 if flags[0] else 2),('C',2)):
                for i in range(count):self.add(s.engine,'Island',seat=seat,ref='sequential-land-'+seat+str(i))
            s.state.players['A'].life=30 if flags[1] else 50
            for seat,count in (('B',3 if flags[2] else 2),('C',2)):
                for i in range(count):self.add(s.engine,'Grizzly Bears',seat=seat,ref='sequential-creature-'+seat+str(i))
            if not flags[3]:
                while len(s.state.players['A'].zones['hand'])<12:self.add(s.engine,'Generic Bound Plains',zone='hand',ref='sequential-hand-'+str(len(s.state.players['A'].zones['hand'])))
            if theft:
                extra['theft']=self.add(s.engine,'Turn Against',seat='B',zone='hand');s.state.players['B'].mana_pool.update({'C':4,'R':1})
        s,source,gift,private=self.scene(name,before=before)
        self.cast(s,'A',source);self.resolve_top(s)
        return s,source,gift,private,extra

    def beza(self,flags,*,theft=False):
        s,source,gift,private,extra=self.board(flags,theft=theft)
        if theft:self.cast(s,'B',extra['theft'],[source.ref]);self.resolve_top(s)
        hands=set(s.state.players['A'].zones['hand']);life=s.state.players['A'].life
        s=self.replay(s,load=True);self.assertIsNone(self.resolve(s))
        tokens=[c for c in s.state.cards.values() if c.is_token and c.zone=='battlefield']
        treasure=[c for c in tokens if 'Treasure' in s.engine._effective_card_data(c)['type_line']]
        fish=[c for c in tokens if 'Fish' in s.engine._effective_card_data(c)['type_line']]
        self.assertEqual(int(flags[0]),len(treasure));self.assertEqual(2*int(flags[2]),len(fish))
        self.assertEqual(life+4*int(flags[1]),s.state.players['A'].life)
        drawn=set(s.state.players['A'].zones['hand'])-hands;self.assertEqual(int(flags[3]),len(drawn))
        for token in tokens:self.assertEqual(('A','A'),(token.owner,token.controller))
        for token in fish:
            data=s.engine._effective_card_data(token);self.assertEqual(['U'],data['colors'])
            self.assertEqual((1,1),tuple(s.engine._numeric_stat(token.object_id,k) for k in ('power','toughness')))
        for oid in drawn:
            for viewer in 'BCD':self.assertNotIn(s.state.cards[oid].ref,json.dumps(s.packet('pilot:'+viewer,full=True)))
        self.finish(s,private)

    def test_original_beza_true_false_and_each_independent_condition_result(self):
        for flags in ((True,True,True,True),(False,False,False,False),
                (True,False,False,False),(False,True,False,False),(False,False,True,False),(False,False,False,True)):
            with self.subTest(flags=flags):self.beza(flags)

    def test_beza_source_control_change_does_not_change_comparator_or_result_actor(self):
        self.beza((True,True,True,True),theft=True)

    def changed_state(self):
        s,source,gift,private,extra=self.board((False,False,True,False),name='Sequential Condition Witness')
        # A has source+gift=2 creatures; B3. One token makes a tie before draw.
        hand=set(s.state.players['A'].zones['hand']);self.assertIsNone(self.resolve(s))
        self.assertEqual(hand,set(s.state.players['A'].zones['hand']))
        tokens=[c for c in s.state.cards.values() if c.is_token and c.zone=='battlefield'];self.assertEqual(1,len(tokens))
        self.finish(s,private)

    def test_later_condition_observes_the_earlier_created_creature(self):
        self.changed_state()

    def test_sequence_leaf_and_comparator_authority_are_required_without_labels(self):
        from quorune.card_programs.binding import bind_semantic_program_runtime
        s,source,gift,private,extra=self.board((True,True,True,True))
        p=next(p for p in s.engine.semantics.programs_for_oracle(source.oracle_id) if len(p.effects)==4)
        for cap in (CAP,'resolution.effect.public_condition','condition.player.opponent_greater_metric','resolution.effect_program.closed_components'):
            deps=tuple(c for c in p.capability_dependencies if c!=cap)
            changed=replace(p,coverage=(),capability_dependencies=deps,capability_closure=self.registry.closure(deps,profile='commander_review').to_dict())
            bound=bind_semantic_program_runtime(changed,capability_registry=self.registry,profile='commander_review')
            self.assertFalse(bound['strict']);self.assertIn('capability:undeclared_runtime_dependency:'+cap,bound['blockers'])

    def test_fresh_temporal_witness_kills_freezing_the_initial_condition_result(self):
        from quorune import player_maximum
        method='test_later_condition_observes_the_earlier_created_creature';type(self)(method).changed_state()
        original=player_maximum.opponent_has_greater_metric;frozen={}
        def wrong(state,controller,spec,**kwargs):
            key=(id(state),controller,spec)
            if key not in frozen:frozen[key]=original(state,controller,spec,**kwargs)
            return frozen[key]
        with patch.object(player_maximum,'opponent_has_greater_metric',wrong):
            with self.assertRaisesRegex(AssertionError,'Items in the second set but not the first'):type(self)(method).changed_state()
