from __future__ import annotations

"""CR 305.6/613.1d/f: intrinsic mana is present before ability removal."""

from pathlib import Path
from types import SimpleNamespace
from dataclasses import replace
import copy
import json
import tempfile
import unittest
from unittest import mock

from common import ROOT
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition,DeckEntry
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.continuous_effects import CharacteristicState,ContinuousEffect,ContinuousOperation,Layer,evaluate_continuous_effects
from quorune.ability_fragments import GrantedActivatedAbilitySpec,ability_fragment_to_dict
from quorune.rules.activation.query import activated_abilities
from scripts.build_test_database import build_fixture_database
import test_bound_effect_programs as actions
import test_self_public_zone_casting as zone_actions


FIXTURE=ROOT/'tests/fixtures/intrinsic-mana-ability-loss-cards.json'


class IntrinsicManaLayerBoundaryTests(unittest.TestCase):
    def state(self):
        return CharacteristicState(name='Layer boundary',card_types={'land','creature'},subtypes={'forest'})

    def effect(self,identity,layer,operations,*,timestamp=1):
        return ContinuousEffect(identity,'source',layer,str(int(layer)),timestamp,tuple(operations))

    def query(self,values):
        host=SimpleNamespace(_effective_card_data=lambda _card:{'type_line':'Land Creature — Forest Island',
            'activated_abilities':values['activated_abilities'],'ability_fragments':values['ability_fragments']})
        return activated_abilities(host,SimpleNamespace())

    def test_type_changes_precede_intrinsic_mana_and_later_ability_loss(self):
        added=self.effect('type',Layer.TYPE,[ContinuousOperation('add_types',['island'],field='subtypes')])
        removed=self.effect('loss',Layer.ABILITY,[ContinuousOperation('remove_all_abilities')])
        expected={'intrinsic_forest','intrinsic_island'}
        before=evaluate_continuous_effects(self.state(),[added]).characteristics
        self.assertEqual(expected,{a.ability_id for a in self.query(before)})
        during=evaluate_continuous_effects(self.state(),[added,removed]).characteristics
        self.assertEqual({'forest','island'},set(during['subtypes']))
        self.assertEqual((),self.query(during))
        restored=evaluate_continuous_effects(self.state(),[added]).characteristics
        self.assertEqual(expected,{a.ability_id for a in self.query(restored)})
        no_land=self.effect('no-land',Layer.TYPE,[ContinuousOperation('remove_types',['land'],field='card_types')])
        absent=evaluate_continuous_effects(self.state(),[no_land]).characteristics
        self.assertEqual([],absent['activated_abilities'])

    def test_explicit_later_mana_grant_survives_without_recreating_intrinsic(self):
        grant=GrantedActivatedAbilitySpec(ability_id='granted:red',semantic_key='fixture:granted:red',
            cost_text='{T}',effect_text='Add {R}.',tap_source=True,mana_ability=True,
            fixed_mana_outputs=((('R',1),),))
        removed=self.effect('loss',Layer.ABILITY,[ContinuousOperation('remove_all_abilities')],timestamp=1)
        added=self.effect('grant',Layer.ABILITY,[ContinuousOperation('add_ability_fragment',ability_fragment_to_dict(grant))],timestamp=2)
        values=evaluate_continuous_effects(self.state(),[added,removed]).characteristics
        abilities=self.query(values)
        self.assertEqual({'granted:red'},{a.ability_id for a in abilities})
        self.assertEqual(1,abilities[0].fixed_mana_outputs[0].bundle['R'])

    def test_repeated_evaluation_rebuilds_only_evaluator_owned_intrinsic_mana(self):
        state=self.state()
        first=evaluate_continuous_effects(state,()).characteristics
        self.assertEqual({'intrinsic_forest'},{a['ability_id'] for a in first['activated_abilities']})
        self.assertFalse(any(key.startswith('_') for key in first))
        for current in (state,copy.deepcopy(state)):
            with self.subTest(deep_copy=current is not state):
                self.assertEqual(first,evaluate_continuous_effects(current,()).characteristics)
                current.subtypes={'island'}
                changed=evaluate_continuous_effects(current,()).characteristics
                self.assertEqual(['intrinsic_island'],[a['ability_id'] for a in changed['activated_abilities']])
                current.card_types={'creature'}
                self.assertEqual([],evaluate_continuous_effects(current,()).characteristics['activated_abilities'])

    def test_repeated_ability_removal_never_revives_intrinsic_mana(self):
        state=self.state()
        removed=self.effect('loss',Layer.ABILITY,[ContinuousOperation('remove_all_abilities')])
        for _ in range(3):
            values=evaluate_continuous_effects(state,(removed,)).characteristics
            self.assertEqual(['forest'],values['subtypes'])
            self.assertEqual([],values['activated_abilities'])
            self.assertEqual((),self.query(values))

    def test_repeated_evaluation_preserves_printed_abilities_and_rejects_reserved_spoof(self):
        from quorune.intrinsic_land_abilities import intrinsic_land_mana_abilities
        intrinsic=intrinsic_land_mana_abilities({'land'},{'forest'})[0]
        printed=replace(intrinsic,ability_id='printed:green')
        state=self.state();state.activated_abilities.append(printed)
        first=evaluate_continuous_effects(state,()).characteristics
        self.assertEqual(['printed:green','intrinsic_forest'],[a['ability_id'] for a in first['activated_abilities']])
        self.assertEqual(first,evaluate_continuous_effects(state,()).characteristics)
        self.assertIs(printed,state.activated_abilities[0])
        state.activated_abilities.append(replace(intrinsic))
        with self.assertRaisesRegex(ValueError,'reserved for CR 305.6'):
            evaluate_continuous_effects(state,())

    def test_intrinsic_provenance_omission_mutant_is_killed_by_repeated_evaluation(self):
        import quorune.continuous_effects as owner
        materialize=owner._materialize_intrinsic_mana
        visited=[]
        def untracked(state):
            materialize(state)
            visited.append(state.name)
            state._intrinsic_mana_abilities=()
        with mock.patch.object(owner,'_materialize_intrinsic_mana',side_effect=untracked):
            result=unittest.TestResult()
            witness=type(self)('test_repeated_evaluation_rebuilds_only_evaluator_owned_intrinsic_mana')
            witness.run(result)
        self.assertFalse(result.wasSuccessful())
        self.assertTrue(any('reserved for CR 305.6' in error for _,error in result.errors))
        self.assertTrue(visited)


class IntrinsicManaAbilityLossRuntimeTests(unittest.TestCase):
    register_fixture_cards_on_add=True
    session=actions.BoundEffectProgramRuntimeTests.session
    add=actions.BoundEffectProgramRuntimeTests.add
    checkpoint=actions.BoundEffectProgramRuntimeTests.checkpoint
    resolve=actions.BoundEffectProgramRuntimeTests.resolve
    replay=actions.BoundEffectProgramRuntimeTests.replay
    prepare=zone_actions.SelfPublicZoneRuntimeTests.prepare
    cast=zone_actions.SelfPublicZoneRuntimeTests.cast
    assert_closed=zone_actions.SelfPublicZoneRuntimeTests.assert_closed

    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory();path=Path(cls.temp.name)/'intrinsic-loss.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',FIXTURE],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Intrinsic loss',[DeckEntry('Generic Bound Commander',1,'commander'),
            DeckEntry('Generic Bound Plains',30)],['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls):cls.db.close();cls.temp.cleanup()

    def test_actual_humble_removes_dryad_intrinsic_mana_and_rejects_activation(self):
        session=self.session(30506101);engine=session.engine
        arbor=self.add(engine,'Dryad Arbor')
        spell=self.add(engine,'Humble',zone='hand')
        growth=self.add(engine,'Generic Bound Growth',zone='hand',ref='intrinsic-unpayable-growth')
        private=self.add(engine,'Generic Bound Growth',seat='C',zone='hand',ref='intrinsic-private')
        self.assert_closed(session,arbor);self.assert_closed(session,spell)
        self.prepare(session,mana={'C':1,'W':1});self.checkpoint(session)
        offered=session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
        action=next(a for a in offered if a['id'].startswith('activate:'+arbor.ref+':') and 'intrinsic_forest' in a['id'])
        before=authoritative_state_hash(session.state)
        self.assertFalse(session.act('pilot:C',{'action_id':action['id']}).ok)
        self.assertEqual(before,authoritative_state_hash(session.state))
        self.cast(session,'A',spell,[arbor.ref])
        session=self.replay(session,load=True);self.resolve(session)
        arbor=session.state.cards[arbor.object_id]
        self.assertEqual((0,1),tuple(session.engine._numeric_stat(arbor.object_id,s) for s in ['power','toughness']))
        self.assertIn('Forest',session.engine._effective_card_data(arbor)['type_line'])
        offered=session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
        self.assertFalse(any(a['id'].startswith('activate:'+arbor.ref+':') and 'intrinsic_forest' in a['id'] for a in offered))
        before=authoritative_state_hash(session.state)
        result=session.act('pilot:A',{'action_id':action['id']})
        self.assertFalse(result.ok,result.summary)
        self.assertEqual(before,authoritative_state_hash(session.state))
        result=session.act('pilot:A',{'action_id':'cast:'+growth.ref,'targets':[arbor.ref],'pay':'auto'})
        self.assertFalse(result.ok,result.summary)
        self.assertEqual(before,authoritative_state_hash(session.state))
        self.assertEqual('hand',session.state.cards[growth.object_id].zone)
        self.assertFalse(session.state.cards[arbor.object_id].tapped)
        for viewer in 'ABD':self.assertNotIn(private.ref,json.dumps(session.packet('pilot:'+viewer,full=True)))
        self.replay(session,load=True)

    def test_actual_cleanup_restores_current_forest_and_added_swamp_mana(self):
        session=self.session(30506102);engine=session.engine
        arbor=self.add(engine,'Dryad Arbor')
        urborg=self.add(engine,'Urborg, Tomb of Yawgmoth')
        spell=self.add(engine,'Humble',zone='hand')
        self.assert_closed(session,arbor);self.assert_closed(session,urborg)
        self.prepare(session,mana={'C':1,'W':1});self.checkpoint(session)
        self.cast(session,'A',spell,[arbor.ref]);self.resolve(session)
        actions=session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
        self.assertFalse(any(a['id'].startswith('activate:'+arbor.ref+':') for a in actions))
        start=session.state.turn_sequence
        for _ in range(128):
            if session.state.turn_sequence>start and 'pilot:A' in session.pending_principals():break
            principal=session.pending_principals()[0]
            decision=session.packet(principal,full=True)['decision']
            kind=session.state.pending_decision.kind
            if kind=='priority':command={'action_id':'pass'}
            elif kind=='combat.attackers':command={'action_id':'attack','attackers':{}}
            elif kind=='cleanup.discard':command={'action_id':'discard',
                'cards':[row['id'] for row in decision['ctx']['hand'][:decision['ctx']['count']]]}
            else:self.fail('Unexpected turn decision: '+kind)
            result=session.act(principal,command)
            self.assertTrue(result.ok,result.summary)
        else:self.fail('Ordinary cleanup did not return the next A priority window')
        actions=session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
        own=[a for a in actions if a['id'].startswith('activate:'+arbor.ref+':')]
        self.assertTrue(any('intrinsic_forest' in a['id'] for a in own))
        self.assertTrue(any('intrinsic_swamp' in a['id'] for a in own))
        action=next(a for a in own if 'intrinsic_forest' in a['id'])
        result=session.act('pilot:A',{'action_id':action['id']});self.assertTrue(result.ok,result.summary)
        self.assertTrue(session.state.cards[arbor.object_id].tapped)
        self.assertEqual(1,session.state.players['A'].mana_pool['G'])
        self.replay(session,load=True)

    def test_late_intrinsic_recreation_mutant_is_killed_by_native_humble(self):
        import quorune.continuous_effects as owner
        original=owner._apply_ability_operation
        revived=[]
        def reintroduced(state,operation,**options):
            result=original(state,operation,**options)
            if operation.op=='remove_all_abilities':
                revived.append(state.name)
                state.activated_abilities.extend(owner.intrinsic_land_mana_abilities(state.card_types,
                    state.subtypes,existing_abilities=state.activated_abilities))
            return result
        with mock.patch.object(owner,'_apply_ability_operation',side_effect=reintroduced):
            with self.assertRaises(AssertionError):
                self.test_actual_humble_removes_dryad_intrinsic_mana_and_rejects_activation()
        self.assertTrue(revived)

    def test_actual_clone_copies_land_type_but_not_the_source_ability_removal(self):
        session=self.session(30506103);engine=session.engine
        arbor=self.add(engine,'Dryad Arbor')
        spell=self.add(engine,'Humble',zone='hand')
        clone=self.add(engine,'Clone',zone='hand')
        self.assert_closed(session,arbor);self.assert_closed(session,clone)
        self.prepare(session,mana={'C':1,'W':1});self.checkpoint(session)
        self.cast(session,'A',spell,[arbor.ref]);self.resolve(session)
        self.assertFalse(any(a.ability_id=='intrinsic_forest' for a in engine._activated_abilities(arbor)))
        self.replay(session,load=True)
        self.prepare(session,mana={'C':3,'U':1});self.checkpoint(session)
        self.cast(session,'A',clone);decision=self.resolve(session)
        self.assertEqual('replacement.order',decision.kind)
        session=self.replay(session,load=True)
        option=next(row for row in session.packet('pilot:A',full=True)['decision']['ctx']['options']
            if arbor.ref in row['id'] and not row['id'].startswith('decline:'))
        accepted=session.act('pilot:A',{'action_id':'choose','replacement':option['id']})
        self.assertTrue(accepted.ok,accepted.summary);self.resolve(session)
        copied=session.state.cards[clone.object_id]
        self.assertEqual((1,1),tuple(session.engine._numeric_stat(copied.object_id,s) for s in ['power','toughness']))
        self.assertEqual({'intrinsic_forest'},{a.ability_id for a in session.engine._activated_abilities(copied) if a.ability_id.startswith('intrinsic_')})
        actions=session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
        self.assertFalse(any(a['id'].startswith('activate:'+copied.ref+':') and 'intrinsic_forest' in a['id'] for a in actions))
        self.replay(session,load=True)
