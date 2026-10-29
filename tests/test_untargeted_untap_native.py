"""Actual spell/entry compositions choose non-target lands at resolution."""
from dataclasses import replace
import unittest
from unittest.mock import patch

import untargeted_untap_fixture as fixtures


class UntargetedUntapNativeTests(fixtures.UntargetedUntapFixture,unittest.TestCase):
    def test_actual_drake_chooses_opponent_hexproof_land_after_source_bounce(self):
        s=self.session(613653001);source=self.add(s.engine,'Peregrine Drake',zone='hand');own=self.land(s,'own-land');other=self.land(s,'foreign-hexproof-land',seat='B',name='Lotus Field');self.land(s,'bounce-mana',seat='B',tapped=False);bounce=self.add(s.engine,'Unsummon',seat='B',zone='hand');self.add(s.engine,'Memnite',zone='hand',ref='private-untap-hand');self.assert_closed(s,source);self.prepare(s,mana={'C':4,'U':1});self.checkpoint(s);self.cast(s,'A',source);self.resolve_top(s);self.cast(s,'B',bounce,[source.ref]);self.resolve_top(s);self.assertEqual('hand',s.state.cards[source.object_id].zone);self.assertEqual('semantic.choice',self.resolve(s).kind)
        s=self.choose_lands(s,[own.ref,other.ref]);self.settle(s);self.assertFalse(s.state.cards[own.object_id].tapped);self.assertFalse(s.state.cards[other.object_id].tapped);self.replay(s,load=True)

    def test_actual_palinchron_seven_distinct_lands_then_paid_return(self):
        s=self.session(613653010);source=self.add(s.engine,'Palinchron',zone='hand');lands=[self.land(s,'seven-land-'+str(i),seat='A' if i%2==0 else 'B') for i in range(7)];self.assert_closed(s,source);self.prepare(s,mana={'C':7,'U':4});self.checkpoint(s);self.cast_for(s,'A',source,reserve={'C':2,'U':2});self.resolve_top(s);self.assertEqual('semantic.choice',self.resolve(s).kind);s=self.choose_lands(s,[c.ref for c in lands]);self.settle(s)
        self.assertTrue(all(not s.state.cards[c.object_id].tapped for c in lands));self.priority(s,'A');a=next(a for a in s.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions'] if a['id'].startswith('activate:'+source.ref+':'));r=s.act('pilot:A',{'action_id':a['id'],'pay':'auto'});self.assertTrue(r.ok,r.summary);self.resolve_top(s);self.assertEqual('hand',s.state.cards[source.object_id].zone);self.replay(s,load=True)

    def test_actual_rewind_and_snap_keep_real_target_loss_over_the_entire_effect(self):
        for name in ('Rewind','Snap'):
            for lost in (False,True):
                with self.subTest(name=name,lost=lost):
                    s=self.session(613653020+len(name)+lost);source=self.add(s.engine,name,zone='hand');land=self.land(s,'effect-land');self.land(s,'response-island-1',seat='B',tapped=False);self.land(s,'response-island-2',seat='B',tapped=False);self.assert_closed(s,source)
                    if name=='Rewind':
                        target=self.add(s.engine,'Shock',seat='B',zone='hand');response=self.add(s.engine,'Counterspell',seat='B',zone='hand');s.state.players['B'].mana_pool['R']=1
                    else:
                        target=self.add(s.engine,'Memnite',seat='B');response=self.add(s.engine,'Unsummon',seat='B',zone='hand')
                    self.prepare(s,mana={'C':2,'U':2});self.checkpoint(s)
                    if name=='Rewind':self.cast(s,'B',target,['C']);target_ref=next(i.ref for i in s.state.stack if i.card_object_id==target.object_id)
                    else:target_ref=target.ref
                    self.cast(s,'A',source,[target_ref])
                    if lost:
                        self.cast(s,'B',response,[target_ref]);self.resolve_top(s);self.settle(s);self.assertTrue(s.state.cards[land.object_id].tapped)
                    else:
                        self.assertEqual('semantic.choice',self.resolve(s).kind);s=self.choose_lands(s,[land.ref]);self.settle(s);self.assertFalse(s.state.cards[land.object_id].tapped)
                    self.assertEqual(40,s.state.players['C'].life);self.replay(s,load=True)

    def test_actual_rewind_uncounterable_spell_still_reaches_untap(self):
        s=self.session(613653040);source=self.add(s.engine,'Rewind',zone='hand');decay=self.add(s.engine,'Abrupt Decay',seat='B',zone='hand');permanent=self.add(s.engine,'Fervor');land=self.land(s,'uncounterable-land');self.assert_closed(s,source);self.prepare(s,mana={'C':2,'U':2});s.state.players['B'].mana_pool.update({'B':1,'G':1});self.checkpoint(s);self.cast(s,'B',decay,[permanent.ref]);ref=next(i.ref for i in s.state.stack if i.card_object_id==decay.object_id);self.cast(s,'A',source,[ref]);self.assertEqual('semantic.choice',self.resolve(s).kind);s=self.choose_lands(s,[land.ref]);self.assertFalse(s.state.cards[land.object_id].tapped);self.assertTrue(any(i.ref==ref for i in s.state.stack));self.settle(s);self.assertEqual('graveyard',s.state.cards[permanent.object_id].zone);self.replay(s,load=True)

    def test_actual_draw_discard_and_untap_obey_both_printed_orders(self):
        for name in ('Frantic Search','Pore Over the Pages'):
            with self.subTest(name=name):
                s=self.session(613653050+len(name));source=self.add(s.engine,name,zone='hand');lands=[self.land(s,'ordered-land-'+str(i)) for i in range(2)];self.add(s.engine,'Memnite',zone='hand',ref='private-untap-hand');self.assert_closed(s,source);self.prepare(s,mana={'C':3,'U':2});self.checkpoint(s);self.cast(s,'A',source);before=len(s.state.players['A'].zones['hand']);self.assertEqual('choice.apnap' if name=='Frantic Search' else 'semantic.choice',self.resolve(s).kind)
                if name=='Frantic Search':
                    self.assertEqual(before+2,len(s.state.players['A'].zones['hand']));self.assertTrue(all(s.state.cards[c.object_id].tapped for c in lands));s=self.replay(s,load=True);self.discard(s,2)
                else:self.assertEqual(before+3,len(s.state.players['A'].zones['hand']))
                s=self.choose_lands(s,[c.ref for c in lands]);self.assertTrue(all(not s.state.cards[c.object_id].tapped for c in lands))
                if name=='Pore Over the Pages':s=self.replay(s,load=True);self.discard(s,1)
                self.settle(s);self.assertEqual(before+(0 if name=='Frantic Search' else 2),len(s.state.players['A'].zones['hand']));self.replay(s,load=True)

    def test_actual_empty_and_zero_or_unchanged_land_choices(self):
        for scene in ('empty','zero','unchanged'):
            with self.subTest(scene=scene):
                s=self.session(613653070+len(scene));source=self.add(s.engine,'Peregrine Drake' if scene=='empty' else 'Great Whale',zone='hand');land=None if scene=='empty' else self.land(s,'unchanged-land',tapped=False);self.assert_closed(s,source);self.prepare(s,mana={'C':5,'U':2});self.checkpoint(s);self.cast(s,'A',source)
                if scene=='empty':self.settle(s)
                else:self.resolve_top(s);self.assertEqual('semantic.choice',self.resolve(s).kind);s=self.choose_lands(s,[] if scene=='zero' else [land.ref]);self.settle(s);self.assertFalse(s.state.cards[land.object_id].tapped)
                self.assertFalse(any(e.code=='permanent.untap' for e in s.state.events));self.replay(s,load=True)

    def test_actual_simultaneous_untap_keeps_stun_and_step_restraint_owners(self):
        from quorune import tap_state
        s=self.session(613653080);source=self.add(s.engine,'Peregrine Drake',zone='hand');orb=self.add(s.engine,'Mesmeric Orb');choke=self.add(s.engine,'Choke');first=self.land(s,'batch-island');second=self.land(s,'batch-forest',name='Forest');stunned=self.land(s,'batch-stunned',seat='B');stunned.counters['stun']=1;self.assert_closed(s,source);self.assert_closed(s,orb);self.assert_closed(s,choke);self.prepare(s,mana={'C':4,'U':1});self.checkpoint(s);self.cast(s,'A',source);self.resolve_top(s);self.assertEqual('semantic.choice',self.resolve(s).kind);sizes={seat:len(s.state.players[seat].zones['library']) for seat in 'AB'};observed=[];real=tap_state.dispatch_tap_state_group
        def observe(host,cards,**kwargs):
            cards=tuple(cards)
            if kwargs.get('tapped') is False:observed.append(tuple(host.state.cards[c.object_id].tapped for c in (first,second,stunned)))
            return real(host,cards,**kwargs)
        with patch('quorune.tap_state.dispatch_tap_state_group',side_effect=observe):s=self.choose_lands(s,[first.ref,second.ref,stunned.ref])
        self.assertEqual([(False,False,True)],observed);self.assertEqual(0,s.state.cards[stunned.object_id].counters.get('stun',0));self.settle(s);self.assertEqual(sizes['A']-2,len(s.state.players['A'].zones['library']));self.assertEqual(sizes['B'],len(s.state.players['B'].zones['library']));self.replay(s,load=True)

    def test_runtime_untargeted_choice_owner_is_inferred_without_coverage(self):
        from quorune.card_programs.binding import bind_semantic_program_runtime
        from quorune.untargeted_untap_model import UNTARGETED_UNTAP_CAPABILITY
        s=self.session(613653090);checked=0
        for name in ('Peregrine Drake','Rewind','Frantic Search'):
            source=self.add(s.engine,name)
            for p in s.engine.semantics.programs_for_oracle(source.oracle_id):
                if UNTARGETED_UNTAP_CAPABILITY not in p.capability_dependencies:continue
                deps=tuple(c for c in p.capability_dependencies if c!=UNTARGETED_UNTAP_CAPABILITY);q=replace(p,coverage=[],capability_dependencies=deps,capability_closure=self.registry.closure(deps,profile='commander_review').to_dict() if deps else None);binding=bind_semantic_program_runtime(q,capability_registry=self.registry,profile='commander_review');self.assertFalse(binding['strict']);self.assertIn('capability:undeclared_runtime_dependency:'+UNTARGETED_UNTAP_CAPABILITY,binding['blockers']);checked+=1
        self.assertEqual(3,checked)
