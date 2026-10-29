"""Pinned605.1a: a library-moving activated result uses the ordinary stack."""
import json,unittest
import mana_composition_fixture as fixtures
from quorune.record import authoritative_state_hash

EGGS=(('Skycloud Egg',('W','U')),('Mossfire Egg',('R','G')),('Sungrass Egg',('G','W')),('Shadowblood Egg',('B','R')),('Darkwater Egg',('U','B')))


class LibraryMovingManaActivationTests(fixtures.ManaCompositionFixture,unittest.TestCase):
    def test_actual_eggs_pay_sacrifice_then_add_both_colors_and_private_draw_on_resolution(self):
        for index,(name,colors) in enumerate(EGGS):
            with self.subTest(name=name):
                session=self.session(605648100+index);source=self.add(session.engine,name,zone='hand');private=self.add(session.engine,'Shock',seat='D',zone='hand',ref='egg-private');self.assert_closed(session,source)
                self.prepare(session,mana={'C':3});self.checkpoint(session);self.cast_for(session,'A',source,reserve={'C':2});self.settle(session);self.priority(session,'A');ability=next(a for a in session.engine._activated_abilities(session.state.cards[source.object_id]) if not a.mana_ability)
                offer=next(a for a in session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions'] if a['id']=='activate:'+session.state.cards[source.object_id].ref+':'+ability.ability_id);hand=len(session.state.players['A'].zones['hand']);top=session.state.cards[session.state.players['A'].zones['library'][-1]].ref
                before=authoritative_state_hash(session.state);command={'action_id':offer['id'],'pay':'auto'};self.assertFalse(session.act('pilot:C',command).ok);self.assertEqual(before,authoritative_state_hash(session.state));result=session.act('pilot:A',command);self.assertTrue(result.ok,result.summary)
                self.assertEqual('graveyard',session.state.cards[source.object_id].zone);self.assertEqual('activated_ability',session.state.stack[-1].kind);self.assertEqual(hand,len(session.state.players['A'].zones['hand']));self.assertEqual(0,sum(session.state.players['A'].mana_pool.values()))
                session=self.replay(session,load=True);self.settle(session);self.assertEqual(hand+1,len(session.state.players['A'].zones['hand']))
                for color in colors:self.assertEqual(1,session.state.players['A'].mana_pool[color])
                self.assertEqual(2,sum(session.state.players['A'].mana_pool.values()))
                for seat in 'BCD':self.assertNotIn(top,json.dumps(session.packet('pilot:'+seat,full=True)))
                for seat in 'ABC':self.assertNotIn(private.ref,json.dumps(session.packet('pilot:'+seat,full=True)))
                self.replay(session,load=True)

    def test_actual_egg_activation_is_counterable_and_is_not_an_automatic_mana_source(self):
        session=self.session(605648110);source=self.add(session.engine,'Skycloud Egg');counter=self.add(session.engine,'Stifle',seat='B',zone='hand');self.add(session.engine,'Island',seat='B');self.assert_closed(session,source)
        self.prepare(session,mana={'C':2});self.checkpoint(session);self.priority(session,'A');offer=next(a for a in session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions'] if a['id'].startswith('activate:'+source.ref+':'));hand=len(session.state.players['A'].zones['hand']);result=session.act('pilot:A',{'action_id':offer['id'],'pay':'auto'});self.assertTrue(result.ok,result.summary)
        self.cast(session,'B',counter,[session.state.stack[-1].ref]);session=self.replay(session,load=True);self.settle(session);self.assertEqual(hand,len(session.state.players['A'].zones['hand']));self.assertEqual(0,sum(session.state.players['A'].mana_pool.values()));self.assertEqual('graveyard',session.state.cards[source.object_id].zone);self.replay(session,load=True)
        session=self.session(605648111);source=self.add(session.engine,'Mossfire Egg');spell=self.add(session.engine,'Grizzly Bears',zone='hand');self.prepare(session,mana={'C':2});self.checkpoint(session);self.priority(session,'A')
        self.assertFalse(any(a['id']=='cast:'+spell.ref for a in session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']));offer=next(a for a in session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions'] if a['id'].startswith('activate:'+source.ref+':'));result=session.act('pilot:A',{'action_id':offer['id'],'pay':'auto'});self.assertTrue(result.ok,result.summary);self.settle(session)
        self.cast(session,'A',spell);self.settle(session);self.assertEqual('battlefield',session.state.cards[spell.object_id].zone);self.assertEqual(0,sum(session.state.players['A'].mana_pool.values()));self.replay(session,load=True)
