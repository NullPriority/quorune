"""CR201.5/400.7/603.2: self damage is bound to one source incarnation."""
from types import SimpleNamespace
import json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition,DeckEntry
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database


class DamageSourceIdentityOwnerTests(unittest.TestCase):
    def test_source_self_damage_rejects_reference_reuse_but_keeps_the_same_control_changed_object(self):
        from quorune.compiler.fixed_counter_trigger_nodes import fixed_counter_trigger_binding
        from quorune.trigger_discovery import semantic_event_matches
        host=SimpleNamespace(active_seats=('A','B','C','D'),state=SimpleNamespace(active_player='A'))
        for head in ('this creature deals combat damage to a player','this creature deals combat damage to an opponent',
                'this creature deals damage to an opponent','this creature deals damage to a player','Generic Source deals damage to a player'):
            with self.subTest(head=head):
                binding=fixed_counter_trigger_binding('Whenever '+head+', draw a card.',card_name='Generic Source')
                self.assertIsNotNone(binding)
                program=SimpleNamespace(event=binding.event.value,active_zone='battlefield',event_condition=binding.event_condition)
                source=SimpleNamespace(ref='source',object_id='source-id',logical_object_id='source@1',controller='A',owner='A',zone='battlefield')
                context={'card':'source','source':'source','source_logical_object_id':'source@0','target_kind':'player','target':'C','combat':True}
                self.assertFalse(semantic_event_matches(host,program,source,'damage.dealt',context))
                source.logical_object_id='source@0';source.controller='B'
                self.assertTrue(semantic_event_matches(host,program,source,'damage.dealt',context))
                self.assertFalse(semantic_event_matches(host,program,source,'damage.dealt',{**context,'source_logical_object_id':None}))

    def test_controlled_other_sources_and_receiving_events_keep_distinct_identity_roles(self):
        from quorune.compiler.fixed_counter_trigger_nodes import fixed_counter_trigger_binding
        from quorune.trigger_discovery import semantic_event_matches
        host=SimpleNamespace(active_seats=('A','B','C','D'),state=SimpleNamespace(active_player='A'))
        source=SimpleNamespace(ref='provider',object_id='provider-id',logical_object_id='provider@1',controller='A',owner='A',zone='battlefield')
        for text,event,context in (
                ('Whenever a creature you control deals combat damage to a player, draw a card.','damage.dealt',
                    {'source':'other','card':'other','source_logical_object_id':'other@7','source_controller':'A','source_types':['creature'],'target_kind':'player','target':'B','combat':True}),
                ('Whenever this creature is dealt damage, draw a card.','damage.received',
                    {'source':'other','card':'provider','source_logical_object_id':'other@7','target':'provider','target_logical_object_id':'provider@1','target_kind':'permanent','amount':1})):
            with self.subTest(event=event):
                binding=fixed_counter_trigger_binding(text,card_name='Generic Source');self.assertIsNotNone(binding)
                program=SimpleNamespace(event=binding.event.value,active_zone='battlefield',event_condition=binding.event_condition)
                self.assertTrue(semantic_event_matches(host,program,source,event,context))

    def test_event_source_identity_value_requires_a_real_logical_identity(self):
        from quorune.trigger_discovery import semantic_event_value
        from quorune.errors import GameRuleError
        host=SimpleNamespace(state=SimpleNamespace(active_player='A'))
        for identity in (None,'',True,1):
            with self.subTest(identity=identity),self.assertRaises(GameRuleError):
                semantic_event_value(host,'$source.logical_object_id',source=SimpleNamespace(logical_object_id=identity,controller='A',owner='A',ref='source',object_id='source-id'),context={})
        self.assertEqual('source@1',semantic_event_value(host,'$source.logical_object_id',
            source=SimpleNamespace(logical_object_id='source@1',controller='A',owner='A',ref='source',object_id='source-id'),context={}))

    def test_intervening_recheck_keeps_the_already_captured_event_source_identity(self):
        from quorune.compiler.fixed_counter_trigger_nodes import fixed_counter_trigger_binding
        from quorune.trigger_discovery import semantic_event_value,semantic_intervening_condition_matches
        from quorune.continuous_conditions import FIXED_PUBLIC_STATE_INTERVENING_COVERAGE
        from quorune.errors import GameRuleError
        host=SimpleNamespace(state=SimpleNamespace(active_player='A'))
        source=SimpleNamespace(ref='source',object_id='source-id',logical_object_id='source@2',controller='A',owner='A',zone='battlefield')
        binding=fixed_counter_trigger_binding('Whenever this creature deals combat damage to a player, draw a card.',card_name='Generic Source')
        program=SimpleNamespace(event_condition=binding.event_condition,coverage=(FIXED_PUBLIC_STATE_INTERVENING_COVERAGE,),active_zone='battlefield')
        context={'source':'source','source_logical_object_id':'source@0','target_kind':'player','target':'B','combat':True,
            'intervening_condition_controller':'A'}
        self.assertTrue(semantic_intervening_condition_matches(host,program,source=source,context=context))
        self.assertEqual('source@2',semantic_event_value(host,'$source.logical_object_id',source=source,
            context={k:v for k,v in context.items() if k!='intervening_condition_controller'}))
        with self.assertRaises(GameRuleError):semantic_event_value(host,'$source.logical_object_id',source=source,
            context={**context,'source_logical_object_id':None})

    def test_substituting_the_damage_identity_for_the_observing_source_identity_is_killed(self):
        import quorune.trigger_discovery as owner
        original=owner.semantic_event_value
        def omit(host,value,*,source,context):
            return context.get('source_logical_object_id') if value=='$source.logical_object_id' else original(host,value,source=source,context=context)
        baseline=unittest.TestResult()
        DamageSourceIdentityOwnerTests('test_source_self_damage_rejects_reference_reuse_but_keeps_the_same_control_changed_object').run(baseline)
        self.assertTrue(baseline.wasSuccessful(),baseline.errors+baseline.failures)
        with patch.object(owner,'semantic_event_value',side_effect=omit):
            result=unittest.TestResult()
            DamageSourceIdentityOwnerTests('test_source_self_damage_rejects_reference_reuse_but_keeps_the_same_control_changed_object').run(result)
        self.assertFalse(result.errors,result.errors);self.assertTrue(result.failures)


class DamageSourceIdentityNativeTests(InheritedNativeFixture,unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary=tempfile.TemporaryDirectory();path=Path(cls.temporary.name)/'damage-source-identity.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',
            ROOT/'tests/fixtures/damage-source-identity-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Damage source identity',[DeckEntry('Isamaru, Hound of Konda',1,'commander'),
            DeckEntry('Generic Bound Plains',14)],['Isamaru, Hound of Konda'])

    def test_actual_old_damage_does_not_trigger_a_returned_niv_and_keeps_created_triggers_and_control_changes(self):
        for mode in ('same','blink_before_damage','blink_after_trigger','control_before_damage'):
            with self.subTest(mode=mode):
                s=self.session(201400700+len(mode))
                source=self.add(s.engine,'Niv-Mizzet, Dracogenius',ref='actual-identity-niv')
                blink=self.add(s.engine,'Cloudshift',zone='hand',ref='actual-identity-blink')
                self.add(s.engine,'Plains',ref='identity-blink-mana')
                self.add(s.engine,'Counterspell',seat='D',zone='hand',ref='source-identity-private-hand')
                if mode=='control_before_damage':
                    giver=self.add(s.engine,'Vedalken Orrery',seat='B',ref='actual-control-timing')
                    theft=self.add(s.engine,'Act of Treason',seat='B',zone='hand',ref='actual-identity-control')
                    self.assert_closed(s,giver);self.assert_closed(s,theft)
                self.assert_closed(s,source);self.assert_closed(s,blink)
                self.prepare(s,mana={'U':1,'R':1})
                if mode=='control_before_damage':s.state.players['B'].mana_pool.update({'C':2,'R':1})
                self.checkpoint(s);original=s.state.cards[source.object_id].logical_object_id
                hands={seat:len(s.state.players[seat].zones['hand']) for seat in 'AB'}
                offered=[row for row in s.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
                    if row['id'].startswith('activate:'+source.ref+':')]
                self.assertEqual(1,len(offered));command={'action_id':offered[0]['id'],'targets':['B'],'pay':'auto'}
                before=authoritative_state_hash(s.state);self.assertFalse(s.act('pilot:D',command).ok);self.assertEqual(before,authoritative_state_hash(s.state))
                with patch.object(s.engine,'_dispatch_semantic_event',wraps=s.engine._dispatch_semantic_event) as spy:
                    activated=s.act('pilot:A',command);self.assertTrue(activated.ok,activated.summary)
                    if mode=='blink_after_trigger':
                        self.resolve_top(s);self.assertTrue(s.state.stack,'Damage has already created its draw trigger')
                    if mode.startswith('blink_'):
                        self.cast(s,'A',blink,[source.ref]);self.resolve_top(s)
                        self.assertNotEqual(original,s.state.cards[source.object_id].logical_object_id)
                    elif mode=='control_before_damage':
                        self.cast(s,'B',theft,[source.ref]);self.resolve_top(s)
                        self.assertEqual(original,s.state.cards[source.object_id].logical_object_id)
                        self.assertEqual('B',s.state.cards[source.object_id].controller)
                    decision=self.resolve(s);chooser=None
                    if decision is not None and decision.kind=='semantic.choice':
                        chooser=decision.actors[0];prior=set(s.state.players[chooser].zones['hand'])
                        before=authoritative_state_hash(s.state)
                        self.assertFalse(s.act('pilot:D',{'action_id':'choose','choice':'apply'}).ok);self.assertEqual(before,authoritative_state_hash(s.state))
                        accepted=s.act('pilot:'+chooser,{'action_id':'choose','choice':'apply'});self.assertTrue(accepted.ok,accepted.summary)
                        self.assertIsNone(self.resolve(s));drawn=set(s.state.players[chooser].zones['hand'])-prior;self.assertEqual(1,len(drawn))
                        drawn_ref=s.state.cards[drawn.pop()].ref
                        for viewer in set('ABCD')-{chooser}:self.assertNotIn(drawn_ref,json.dumps(s.packet('pilot:'+viewer,full=True)))
                    events=[call.args[1] for call in spy.call_args_list if len(call.args)>=2 and call.args[0]=='damage.dealt']
                self.assertEqual(1,len(events));self.assertEqual(original,events[0]['source_logical_object_id'])
                self.assertEqual(39,s.state.players['B'].life)
                self.replay(s,load=True)
                expected=None if mode=='blink_before_damage' else 'B' if mode=='control_before_damage' else 'A'
                self.assertEqual(expected,chooser)
                self.assertEqual(-1 if mode=='blink_before_damage' else 1 if mode=='same' else 0,
                    len(s.state.players['A'].zones['hand'])-hands['A'])
                self.assertEqual(0,len(s.state.players['B'].zones['hand'])-hands['B'])
                for viewer in 'ABC':self.assertNotIn('source-identity-private-hand',json.dumps(s.packet('pilot:'+viewer,full=True)))
