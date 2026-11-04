"""Actual one-per-opponent selection, partial resolution and copied choices."""
import json,tempfile,unittest
from pathlib import Path
from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition,DeckEntry
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database


class PerPlayerTargetNativeTests(InheritedNativeFixture,unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary=tempfile.TemporaryDirectory();path=Path(cls.temporary.name)/'per-player-target.sqlite3'
        build_fixture_database([
            ROOT/'tests/fixtures/bound-effect-program-cards.json',
            ROOT/'tests/fixtures/per-player-target-cards.json',
            ROOT/'tests/fixtures/fixed-disturb-cards.json',
            ROOT/'tests/fixtures/per-player-trigger-composition-cards.json',
        ],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Per player targets',[DeckEntry('Isamaru, Hound of Konda',1,'commander'),DeckEntry('Generic Bound Plains',14)],['Isamaru, Hound of Konda'])

    def reject(self,s,seat,command):
        before=authoritative_state_hash(s.state);result=s.act('pilot:'+seat,command)
        self.assertFalse(result.ok,result.summary);self.assertEqual(before,authoritative_state_hash(s.state))

    def board(self,seed):
        s=self.session(seed);wave=self.add(s.engine,'Dismantling Wave',zone='hand',ref='actual-per-player-wave')
        own=self.add(s.engine,'Phyrexian Walker',ref='per-player-own')
        b1=self.add(s.engine,'Phyrexian Walker',seat='B',ref='per-player-b-first')
        b2=self.add(s.engine,'Phyrexian Walker',seat='B',ref='per-player-b-second')
        c=self.add(s.engine,'Phyrexian Walker',seat='C',ref='per-player-c')
        d=self.add(s.engine,'Phyrexian Walker',seat='D',ref='per-player-d')
        private=self.add(s.engine,'Counterspell',seat='C',zone='hand',ref='per-player-private')
        self.assert_closed(s,wave)
        return s,wave,own,b1,b2,c,d,private

    def test_original_per_opponent_destroy_preserves_disturb_destination_replacement(self):
        s, spell, own, b1, b2, c, d, private = self.board(115760822)
        aura = self.add(s.engine, "Kindly Ancestor // Ancestor's Embrace",
            seat='B', zone='graveyard', ref='per-player-disturb-aura')
        orrery = self.add(s.engine, 'Vedalken Orrery', seat='B', ref='per-player-disturb-flash')
        for card in (aura, orrery):
            self.assert_closed(s, card)
        self.prepare(s, mana={'C': 2, 'W': 1})
        s.state.players['B'].mana_pool.update({'C': 1, 'W': 1})
        self.checkpoint(s)
        self.priority(s, 'B')
        back = "Ancestor's Embrace"
        action_id = 'cast:' + aura.ref + ':face:' + back
        actions = s.packet('pilot:B', full=True)['decision']['ctx']['legal']['actions']
        offered = next(row for row in actions if row['id'] == action_id)
        self.assertEqual(['disturb'], [row['id'] for row in offered['cost_options']])
        command = {'action_id': action_id, 'face': back, 'cost_option': 'disturb',
            'targets': [b1.ref], 'pay': 'auto'}
        self.reject(s, 'D', command)
        accepted = s.act('pilot:B', command)
        self.assertTrue(accepted.ok, accepted.summary)
        self.resolve_top(s)
        self.assertEqual(('battlefield', back, b1.object_id), (
            s.state.cards[aura.object_id].zone, s.state.cards[aura.object_id].active_face,
            s.state.cards[aura.object_id].attached_to))
        self.assertIn('lifelink', s.engine._combat_keywords(s.state.cards[b1.object_id]))
        s = self.replay(s, load=True)
        self.priority(s, 'A')
        self.reject(s, 'A', {'action_id': 'cast:' + spell.ref,
            'targets': [aura.ref, b1.ref], 'pay': 'auto'})
        self.cast(s, 'A', spell, [aura.ref, c.ref, d.ref])
        self.assertIsNone(self.resolve(s))
        self.assertEqual(('exile', None, None), (
            s.state.cards[aura.object_id].zone, s.state.cards[aura.object_id].active_face,
            s.state.cards[aura.object_id].attached_to))
        for card in (c, d, spell):
            self.assertEqual('graveyard', s.state.cards[card.object_id].zone)
        for card in (own, b1, b2, orrery):
            self.assertEqual('battlefield', s.state.cards[card.object_id].zone)
        self.assertNotIn('lifelink', s.engine._combat_keywords(s.state.cards[b1.object_id]))
        self.assertEqual(0, sum(s.state.players['A'].mana_pool.values()))
        self.assertEqual(0, sum(s.state.players['B'].mana_pool.values()))
        for viewer in 'ABD':
            self.assertNotIn(private.ref, json.dumps(s.packet('pilot:' + viewer, full=True)))
        self.replay(s, load=True)

    def test_closed_per_player_spell_does_not_constrain_its_separate_cycle_mill_trigger(self):
        # This is an explicitly constructed normal-admission control, not
        # whole-card support for the unresolved original Saga carrier.
        s, _original, own, b1, b2, c, d, private = self.board(115760823)
        spell = self.add(s.engine, 'Generic Per-Player Cycling Mill Control',
            zone='hand', ref='typed-per-player-mill-spell')
        cycled = self.add(s.engine, 'Generic Per-Player Cycling Mill Control',
            zone='hand', ref='typed-per-player-mill-cycle')
        for card in (spell, cycled):
            self.assert_closed(s, card)
        self.prepare(s, mana={'C': 8, 'W': 3})
        self.checkpoint(s)
        before_draws = len(s.state.players['A'].draw_history)
        libraries = {seat: tuple(s.state.players[seat].zones['library']) for seat in 'ABCD'}
        self.reject(s, 'A', {'action_id': 'cast:' + spell.ref,
            'targets': [b1.ref, b2.ref], 'pay': 'auto'})
        self.cast(s, 'A', spell, [b1.ref, c.ref, d.ref])
        self.resolve_top(s)
        for card in (b1, c, d):
            self.assertEqual('graveyard', s.state.cards[card.object_id].zone)
        for seat in 'ABCD':
            self.assertEqual(libraries[seat], tuple(s.state.players[seat].zones['library']))
        self.priority(s, 'A')
        actions = s.packet('pilot:A', full=True)['decision']['ctx']['legal']['actions']
        activation = next(row for row in actions if row['id'].startswith('activate:' + cycled.ref + ':'))
        self.assertFalse(activation.get('target_schema'))
        command = {'action_id': activation['id'], 'pay': 'auto'}
        self.reject(s, 'B', command)
        accepted = s.act('pilot:A', command)
        self.assertTrue(accepted.ok, accepted.summary)
        self.assertEqual('graveyard', s.state.cards[cycled.object_id].zone)
        self.assertEqual(2, len(s.state.stack))
        self.assertEqual('triggered_ability', s.state.stack[-1].kind)
        self.assertEqual(cycled.object_id, s.state.stack[-1].source_object_id)
        s = self.replay(s, load=True)
        self.resolve_top(s)
        for seat in 'BCD':
            self.assertEqual(len(libraries[seat]) - 3, len(s.state.players[seat].zones['library']))
            for uid in libraries[seat][-3:]:
                self.assertEqual('graveyard', s.state.cards[uid].zone)
        self.assertEqual(libraries['A'], tuple(s.state.players['A'].zones['library']))
        self.assertEqual(before_draws, len(s.state.players['A'].draw_history))
        for card in (own, b2):
            self.assertEqual('battlefield', s.state.cards[card.object_id].zone)
        self.assertEqual(1, len(s.state.stack))
        s = self.replay(s, load=True)
        self.assertIsNone(self.resolve(s))
        drawn = s.state.cards[libraries['A'][-1]]
        self.assertEqual('hand', drawn.zone)
        self.assertEqual(before_draws + 1, len(s.state.players['A'].draw_history))
        self.assertEqual(len(libraries['A']) - 1, len(s.state.players['A'].zones['library']))
        self.assertEqual((0, 0), tuple(s.state.players['A'].mana_pool[k] for k in ('C', 'W')))
        for viewer in 'BCD':
            self.assertNotIn(drawn.ref, json.dumps(s.packet('pilot:' + viewer, full=True)))
        for viewer in 'ABD':
            self.assertNotIn(private.ref, json.dumps(s.packet('pilot:' + viewer, full=True)))
        self.assertIn(drawn.ref, json.dumps(s.packet('pilot:A', full=True)))
        self.replay(s, load=True)

    def test_original_wave_keeps_targeted_spell_separate_from_cycling_trigger_and_draw(self):
        s, spell, own, b1, b2, c, d, private = self.board(115760821)
        cycled = self.add(s.engine, 'Dismantling Wave', zone='hand', ref='actual-cycling-wave')
        self.assert_closed(s, cycled)
        self.prepare(s, mana={'C': 8, 'W': 3})
        self.checkpoint(s)
        before_draws = len(s.state.players['A'].draw_history)
        before_library = len(s.state.players['A'].zones['library'])
        drawn_id = s.state.players['A'].zones['library'][-1]
        self.reject(s, 'A', {
            'action_id': 'cast:' + spell.ref, 'targets': [b1.ref, b2.ref], 'pay': 'auto',
        })
        self.cast(s, 'A', spell, [b1.ref])
        self.resolve_top(s)
        self.assertEqual('graveyard', s.state.cards[b1.object_id].zone)
        for card in (own, b2, c, d):
            self.assertEqual('battlefield', s.state.cards[card.object_id].zone)
        self.assertEqual(before_draws, len(s.state.players['A'].draw_history))
        self.priority(s, 'A')
        actions = s.packet('pilot:A', full=True)['decision']['ctx']['legal']['actions']
        activation = next(row for row in actions if row['id'].startswith('activate:' + cycled.ref + ':'))
        self.assertFalse(activation.get('target_schema'))
        command = {'action_id': activation['id'], 'pay': 'auto'}
        self.reject(s, 'B', command)
        result = s.act('pilot:A', command)
        self.assertTrue(result.ok, result.summary)
        self.assertEqual('graveyard', s.state.cards[cycled.object_id].zone)
        self.assertEqual(before_draws, len(s.state.players['A'].draw_history))
        self.assertEqual(2, len(s.state.stack))
        self.assertEqual('triggered_ability', s.state.stack[-1].kind)
        self.assertEqual(cycled.object_id, s.state.stack[-1].source_object_id)
        s = self.replay(s, load=True)
        self.resolve_top(s)
        for card in (own, b1, b2, c, d):
            self.assertEqual('graveyard', s.state.cards[card.object_id].zone)
        self.assertEqual(before_draws, len(s.state.players['A'].draw_history))
        self.assertEqual(before_library, len(s.state.players['A'].zones['library']))
        self.assertEqual('library', s.state.cards[drawn_id].zone)
        self.assertEqual(1, len(s.state.stack))
        s = self.replay(s, load=True)
        self.assertIsNone(self.resolve(s))
        self.assertEqual(before_draws + 1, len(s.state.players['A'].draw_history))
        self.assertEqual(before_library - 1, len(s.state.players['A'].zones['library']))
        self.assertEqual('hand', s.state.cards[drawn_id].zone)
        self.assertEqual('graveyard', s.state.cards[spell.object_id].zone)
        self.assertEqual('graveyard', s.state.cards[cycled.object_id].zone)
        self.assertEqual((0, 0), tuple(s.state.players['A'].mana_pool[k] for k in ('C', 'W')))
        for viewer in 'BCD':
            self.assertNotIn(s.state.cards[drawn_id].ref, json.dumps(s.packet('pilot:' + viewer, full=True)))
        for viewer in 'ABD':
            self.assertNotIn(private.ref, json.dumps(s.packet('pilot:' + viewer, full=True)))
        self.assertIn(s.state.cards[drawn_id].ref, json.dumps(s.packet('pilot:A', full=True)))
        self.assertIn(private.ref, json.dumps(s.packet('pilot:C', full=True)))
        self.replay(s, load=True)

    def test_actual_wave_rejects_same_player_then_keeps_partial_targets_after_control_and_departure(self):
        s,wave,own,b1,b2,c,d,private=self.board(115760820)
        theft=self.add(s.engine,'Act of Treason',seat='D',zone='hand',ref='per-player-actual-control-change')
        self.add(s.engine,'Vedalken Orrery',seat='D')
        for i in range(3):self.add(s.engine,'Mountain',seat='D',ref='per-player-red-'+str(i))
        self.prepare(s,mana={'W':1,'C':2});self.checkpoint(s)
        action=next(row for row in s.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions'] if row['id']=='cast:'+wave.ref)
        group=action['target_schema']['groups'][0]
        self.assertTrue(group['distinct_controllers']);self.assertTrue(group['retain_controller'])
        self.assertNotIn(own.ref,group['legal_refs'])
        self.reject(s,'A',{'action_id':action['id'],'targets':[b1.ref,b2.ref],'pay':'auto'})
        self.reject(s,'B',{'action_id':action['id'],'targets':[b1.ref,c.ref,d.ref],'pay':'auto'})
        self.cast(s,'A',wave,[b1.ref,c.ref,d.ref])
        self.cast(s,'D',theft,[b1.ref]);self.resolve_top(s)
        self.assertEqual('D',s.state.cards[b1.object_id].controller)
        for viewer in 'ABD':self.assertNotIn(private.ref,json.dumps(s.packet('pilot:'+viewer,full=True)))
        self.assertIn(private.ref,json.dumps(s.packet('pilot:C',full=True)))
        self.priority(s,'C')
        result=s.act('pilot:C',{'action_id':'concede','choices':{'confirm_concede':True}});self.assertTrue(result.ok,result.summary)
        self.assertFalse(s.state.players['C'].in_game)
        s=self.replay(s,load=True);self.assertIsNone(self.resolve(s))
        self.assertEqual('battlefield',s.state.cards[b1.object_id].zone)
        self.assertEqual('battlefield',s.state.cards[b2.object_id].zone)
        self.assertEqual('battlefield',s.state.cards[own.object_id].zone)
        self.assertEqual('graveyard',s.state.cards[d.object_id].zone)
        self.assertIn(d.object_id,s.state.players['D'].zones['graveyard'])
        self.replay(s,load=True)

    def test_actual_copy_keeps_illegal_original_and_count_while_retargeting_under_new_controller(self):
        s,wave,own,b1,b2,c,d,private=self.board(115770710)
        copy=self.add(s.engine,'Reverberate',seat='B',zone='hand',ref='per-player-real-copy')
        s.state.players['B'].mana_pool.update({'R':2})
        self.prepare(s,mana={'W':1,'C':2});self.checkpoint(s)
        self.cast(s,'A',wave,[b1.ref,c.ref,d.ref]);self.cast(s,'B',copy,[s.state.stack[-1].ref])
        decision=self.resolve(s);self.assertEqual('semantic.choice',decision.kind)
        self.reject(s,'B',{'action_id':'choose','targets':[own.ref,d.ref]})
        self.reject(s,'B',{'action_id':'choose','targets':[b2.ref,c.ref,d.ref]})
        self.reject(s,'A',{'action_id':'choose','targets':[{'retain':0},own.ref,{'retain':2}]})
        for viewer in 'ABD':self.assertNotIn(private.ref,json.dumps(s.packet('pilot:'+viewer,full=True)))
        s=self.replay(s,load=True)
        result=s.act('pilot:B',{'action_id':'choose','targets':[{'retain':0},own.ref,{'retain':2}]});self.assertTrue(result.ok,result.summary)
        self.assertEqual('spell_copy',s.state.stack[-1].kind)
        self.assertEqual(3,len(s.state.stack[-1].targets));self.resolve_top(s)
        self.assertEqual('graveyard',s.state.cards[own.object_id].zone)
        self.assertEqual('graveyard',s.state.cards[d.object_id].zone)
        self.assertEqual('battlefield',s.state.cards[b1.object_id].zone)
        self.assertEqual('battlefield',s.state.cards[c.object_id].zone)
        self.assertIsNone(self.resolve(s))
        for target in (b1,c):self.assertEqual('graveyard',s.state.cards[target.object_id].zone)
        self.assertEqual('battlefield',s.state.cards[b2.object_id].zone)
        self.replay(s,load=True)

    def test_actual_optional_zero_targets_resolves_without_target_or_effect_mutation(self):
        s,wave,own,b1,b2,c,d,_private=self.board(115760800)
        self.prepare(s,mana={'W':1,'C':2});self.checkpoint(s);self.cast(s,'A',wave,[])
        self.assertIsNone(self.resolve(s))
        for card in (own,b1,b2,c,d):self.assertEqual('battlefield',s.state.cards[card.object_id].zone)
        self.replay(s,load=True)
