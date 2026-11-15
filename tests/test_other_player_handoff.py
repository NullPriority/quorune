"""CR608.2c/d: choose an untargeted other player, give, then resolver draws."""
import json,unittest
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import patch
import test_recipient_control as recipients
import test_player_maximum_control as maximum_fixtures
import test_fixed_creature_power_damage as records
from inherited_composition_fixture import InheritedNativeFixture
from quorune.oracle_ir import compile_oracle_card
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.recipient_control_shapes import recipient_control_node_capabilities,recipient_control_runtime_dependencies


class OtherPlayerHandoffModelCompilerTests(unittest.TestCase):
    def test_other_player_choice_is_versioned_and_old_opponent_choices_remain_strict(self):
        text='Choose another player. That player gains control of target Treasure you control. You draw a card.'
        ir=compile_oracle_card(records.card_record(text),capability_registry=load_default_capability_registry(),capability_profile='commander_review')
        self.assertEqual('exact',ir.status,ir.material_residuals)
        node,=ir.faces[0].nodes;gift,draw=node.effects
        self.assertEqual((3,False),(gift['schema_version'],gift['opponent_only']));self.assertEqual('draw',draw['op'])
        mechanics=('fixed-recipient-control','cr-115-targets','cr-611-continuous-effects')
        for change in ({'schema_version':2},{'schema_version':1},{'schema_version':True},{'opponent_only':True},{'opponent_only':0},{'card':'$source.zone_object'}):
            with self.subTest(change=change):
                effect={**gift,**change}
                self.assertEqual((),recipient_control_node_capabilities(effects=(effect,),target_schema=node.target_schema,mechanic_ids=mechanics))
                with self.assertRaises(ValueError):recipient_control_runtime_dependencies(SimpleNamespace(effects=(effect,),target_schema=node.target_schema))


class OtherPlayerHandoffNativeTests(InheritedNativeFixture,unittest.TestCase):
    setUpClass=classmethod(recipients.RecipientControlNativeTests.setUpClass.__func__)
    scene=recipients.RecipientControlNativeTests.scene
    prepare=maximum_fixtures.PlayerMaximumNativeTests.prepare
    finish_turn=recipients.RecipientControlNativeTests.finish_turn
    reject=recipients.RecipientControlNativeTests.reject
    finish=recipients.RecipientControlNativeTests.finish

    def board(self):
        extra={}
        def before(s,source,gift):
            self.add(s.engine,'Generic Bound Plains',ref='financier-white')
            for i in range(2):self.add(s.engine,'Island',ref='financier-island-A'+str(i))
            for i in range(4):self.add(s.engine,'Island',seat='B',ref='financier-island-B'+str(i))
            extra['remove']=self.add(s.engine,'Boomerang',seat='B',zone='hand')
        s,source,gift,private=self.scene('Discerning Financier',zone='battlefield',before=before)
        self.finish_turn(s);self.assertEqual(('A','upkeep'),(s.state.active_player,s.state.step))
        self.assertIsNone(self.resolve(s));token,=[c for c in s.state.cards.values() if c.is_token and c.zone=='battlefield']
        self.assertIn('Treasure',s.engine._effective_card_data(token)['type_line'])
        return s,source,token,private,extra

    def activate(self,s,source,target):
        self.priority(s,'A');row=next(r for r in s.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions'] if r['id'].startswith('activate:'+source.ref+':'))
        command={'action_id':row['id'],'targets':[target.ref],'pay':'auto'};self.reject(s,'B',command)
        result=s.act('pilot:A',command);self.assertTrue(result.ok,result.summary)

    def handoff(self):
        s,source,token,private,extra=self.board();self.activate(s,source,token)
        hand=set(s.state.players['A'].zones['hand']);self.assertEqual('semantic.choice',self.resolve(s).kind)
        self.assertEqual(hand,set(s.state.players['A'].zones['hand']));self.assertEqual('A',s.state.cards[token.object_id].controller)
        command={'action_id':'choose','recipient':'C'};self.reject(s,'B',command);self.reject(s,'A',{**command,'recipient':'A'})
        s=self.replay(s,load=True);result=s.act('pilot:A',command);self.assertTrue(result.ok,result.summary);self.assertIsNone(self.resolve(s))
        self.assertEqual(('C','A'),(s.state.cards[token.object_id].controller,s.state.cards[token.object_id].owner))
        drawn=set(s.state.players['A'].zones['hand'])-hand;self.assertEqual(1,len(drawn))
        for oid in drawn:
            for viewer in 'BCD':self.assertNotIn(s.state.cards[oid].ref,json.dumps(s.packet('pilot:'+viewer,full=True)))
        self.finish(s,private)

    def test_original_financier_upkeep_choice_transfer_and_private_draw_follow_printed_order(self):
        self.handoff()

    def test_illegal_original_treasure_prevents_choice_transfer_and_draw(self):
        s,source,token,private,extra=self.board();self.activate(s,source,token)
        hand=set(s.state.players['A'].zones['hand']);self.cast(s,'B',extra['remove'],[token.ref]);self.resolve_top(s)
        s=self.replay(s,load=True);self.assertIsNone(self.resolve(s));self.assertEqual(hand,set(s.state.players['A'].zones['hand']))
        self.assertNotIn(token.object_id,s.state.players['C'].zones['battlefield']);self.finish(s,private)

    def test_other_choice_still_requires_recipient_authority_and_target_schema_without_labels(self):
        from quorune.card_programs.binding import bind_semantic_program_runtime
        s,source,token,private,extra=self.board()
        p=next(p for p in s.engine.semantics.programs_for_oracle(source.oracle_id) if any(e.get('op')=='choose_control_recipient' for e in p.effects))
        deps=tuple(c for c in p.capability_dependencies if c!='continuous.control.fixed_recipient')
        changed=replace(p,coverage=(),capability_dependencies=deps,capability_closure=self.registry.closure(deps,profile='commander_review').to_dict())
        bound=bind_semantic_program_runtime(changed,capability_registry=self.registry,profile='commander_review')
        self.assertFalse(bound['strict']);self.assertIn('capability:undeclared_runtime_dependency:continuous.control.fixed_recipient',bound['blockers'])
        bound=bind_semantic_program_runtime(replace(p,coverage=(),target_schema=None),capability_registry=self.registry,profile='commander_review')
        self.assertFalse(bound['strict'])

    def test_fresh_financier_witness_kills_giving_control_to_the_resolving_player(self):
        from quorune.semantic_runtime.control_handlers import RecipientControlHandler
        original=RecipientControlHandler.lower;method='test_original_financier_upkeep_choice_transfer_and_private_draw_follow_printed_order'
        type(self)(method).handoff()
        def wrong(handler,effect,context):return original(handler,{**effect,'controller':context.actor},context)
        with patch.object(RecipientControlHandler,'lower',wrong):
            with self.assertRaises(AssertionError):type(self)(method).handoff()
