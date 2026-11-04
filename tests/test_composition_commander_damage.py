"""Combat damage follows a commander's archived physical identity."""
import unittest

import test_composition_commander_departure as departures
import test_ability_label_composition as combat_journeys
from quorune.permanent_composition import MergePosition
from quorune.record import authoritative_state_hash


class PhysicalCommanderDamageModelTests(unittest.TestCase):
    def identities(self):
        from quorune.commander_damage_identity_model import PhysicalCommanderDamageIdentity,PhysicalCommanderDamageIdentities
        return PhysicalCommanderDamageIdentities((PhysicalCommanderDamageIdentity('commander:A:2','second-oracle'),
            PhysicalCommanderDamageIdentity('commander:A:1','first-oracle')))

    def test_closed_identity_codec_rejects_duplicate_unknown_boolean_and_contradictory_sources(self):
        from copy import deepcopy
        from quorune.commander_damage_identity_model import PhysicalCommanderDamageIdentities
        from quorune.damage_source import DamageSourceSnapshot,DamageError
        identities=self.identities();wire=identities.to_dict()
        self.assertEqual(identities,PhysicalCommanderDamageIdentities.from_dict(wire))
        for value in ({**wire,'schema_version':True},{**wire,'commanders':[]},
                {**wire,'unknown':1},{**wire,'commanders':[wire['commanders'][0],wire['commanders'][0]]},
                {**wire,'commanders':[{'designation_id':'X','oracle_id':False}]}):
            with self.subTest(value=value),self.assertRaises(ValueError):PhysicalCommanderDamageIdentities.from_dict(value)
        source=DamageSourceSnapshot('ROOT','root','root@1','B','A',zone='battlefield',oracle_id='root-oracle',
            is_commander=True,physical_commander_identities=identities)
        self.assertEqual(source,DamageSourceSnapshot.from_dict(source.to_dict()))
        from dataclasses import replace
        for changes in ({'is_commander':False},{'commander_designation_id':'unknown'},
                {'commander_designation_id':'commander:A:1','oracle_id':'wrong-oracle'}):
            with self.subTest(changes=changes),self.assertRaises(DamageError):replace(source,**changes)
        malformed=deepcopy(source.to_dict());malformed['physical_commander_identities']=None
        with self.assertRaises(DamageError):DamageSourceSnapshot.from_dict(malformed)
        from quorune.damage import _final_event
        from quorune.damage_values import DamageProposal,DamageRecipientSnapshot
        event=DamageProposal('bad-identity:event',source,DamageRecipientSnapshot('D','player','D'),
            2,True,'Closed source decoder control').event()
        event=replace(event,payload={**dict(event.payload),'source_physical_commander_identities':None})
        with self.assertRaises(DamageError):_final_event(event,proposed=2,dealt=2,prevented=0)

    def test_absence_retains_ordinary_snapshot_shape_and_legacy_single_ledger_keys(self):
        from quorune.damage_source import DamageSourceSnapshot
        from quorune.commander import commander_damage_keys
        source=DamageSourceSnapshot('C','card','card@1','A','A',zone='battlefield',oracle_id='ordinary-oracle',
            commander_designation_id='commander:A:1',is_commander=True)
        wire=source.to_dict();self.assertNotIn('physical_commander_identities',wire)
        self.assertEqual(wire,DamageSourceSnapshot.from_dict(wire).to_dict())
        for version,expected in ((None,('ordinary-oracle',)),(2,('commander:A:1',))):
            self.assertEqual(expected,commander_damage_keys(source_is_commander=True,
                designation_id=source.commander_designation_id,oracle_id=source.oracle_id,identity_version=version))
        self.assertEqual(('commander:A:1','commander:A:2'),commander_damage_keys(source_is_commander=True,
            designation_id=None,oracle_id='root-oracle',identity_version=2,physical_identities=self.identities()))
        self.assertEqual(('first-oracle','second-oracle'),commander_damage_keys(source_is_commander=True,
            designation_id=None,oracle_id='root-oracle',identity_version=None,physical_identities=self.identities()))


class CompositionCommanderDamageTests(unittest.TestCase):
    register_fixture_cards_on_add=True
    setUpClass=classmethod(departures.CompositionCommanderDepartureTests.setUpClass.__func__)
    tearDownClass=classmethod(departures.CompositionCommanderDepartureTests.tearDownClass.__func__)
    assert_closed=departures.CompositionCommanderDepartureTests.assert_closed
    priority=departures.CompositionCommanderDepartureTests.priority
    cast=departures.CompositionCommanderDepartureTests.cast
    fixture=departures.CompositionCommanderDepartureTests.fixture
    advance=combat_journeys.AbilityLabelCompositionTests.advance

    def test_plural_source_updates_each_ledger_without_duplicating_life_damage(self):
        from test_bound_effect_programs import BoundEffectProgramRuntimeTests as H
        from quorune.damage import resolve_damage_batch
        from quorune.damage_source import DamageSourceSnapshot
        from quorune.damage_values import DamageProposal,DamageRecipientSnapshot
        from quorune.commander import commander_damage_losers
        session=H.session(self,959341);engine=session.engine
        identities=PhysicalCommanderDamageModelTests.identities(self)
        source=DamageSourceSnapshot('MODEL_SOURCE','model-source','model-source@1','B','A',zone='battlefield',
            oracle_id='noncommander-root',is_commander=True,physical_commander_identities=identities)
        engine.state.players['D'].commander_damage_received={'commander:A:1':19}
        proposal=DamageProposal('physical-commanders:model',source,DamageRecipientSnapshot('D','player','D'),
            2,True,'Typed multiple-physical-commander source model; no native Mutate producer claim')
        result=resolve_damage_batch(engine,(proposal,))
        self.assertEqual(38,engine.state.players['D'].life);self.assertEqual(2,result.dealt_amount)
        self.assertEqual(1,len(result.events))
        self.assertEqual({'commander:A:1':21,'commander:A:2':2},engine.state.players['D'].commander_damage_received)
        self.assertEqual(('D',),commander_damage_losers(engine.state))
        before=dict(engine.state.players['D'].commander_damage_received)
        noncombat=DamageProposal('physical-commanders:noncombat',source,DamageRecipientSnapshot('D','player','D'),
            1,False,'Noncombat damage never contributes to commander combat damage')
        resolve_damage_batch(engine,(noncombat,))
        self.assertEqual(37,engine.state.players['D'].life)
        self.assertEqual(before,engine.state.players['D'].commander_damage_received)

    def test_prepared_merged_source_keeps_its_physical_commander_after_departure(self):
        from quorune.damage import damage_proposal,prepare_damage_batch,commit_prepared_damage_batch
        from quorune.zone_transitions import ZoneTransitionOwner
        session,root,commander,designation,_control,_shock,_exile=self.fixture(MergePosition.TOP,959342)
        engine=session.engine
        proposal=damage_proposal(engine,proposal_id='merged-commander:lki',actor='B',source_ref=root.ref,
            target='D',amount=2,combat=True,reason='Physical commander source pinned before departure')
        prepared=prepare_damage_batch(engine,(proposal,))
        ZoneTransitionOwner(engine).depart_composition(root.object_id,'graveyard',
            physical_order=[root.object_id,commander.object_id],chooser='A',actor='C',
            reason='Explicit kernel departure after source preparation')
        result=commit_prepared_damage_batch(engine,prepared)
        self.assertEqual(38,engine.state.players['D'].life)
        self.assertEqual({designation:2},engine.state.players['D'].commander_damage_received)
        self.assertEqual('B',result.events[0].source_controller)
        self.assertEqual('graveyard',engine.state.cards[root.object_id].zone)
        self.assertEqual('graveyard',engine.state.cards[commander.object_id].zone)

    def test_actual_combat_uses_the_buried_physical_commander_when_the_live_root_is_not_that_card(self):
        from test_bound_effect_programs import BoundEffectProgramRuntimeTests as H
        from quorune.turn_step_owner import TurnEntry
        from quorune.commander import commander_damage_source
        session,root,commander,designation,_control,_shock,_exile=self.fixture(MergePosition.TOP,959340)
        engine=session.engine
        self.assertNotEqual(root.object_id,commander.object_id)
        self.assertTrue(root.is_commander);self.assertIsNone(root.commander_designation_id)
        engine.permissions.invalidate_current();engine.state.pending_decision=None
        engine._begin_turn(TurnEntry(turn_id=engine._next_ref('N'),player='B',extra=False,
            created_sequence=engine.state.turn_sequence))
        engine.pump()
        H.checkpoint(self,session)
        self.advance(session,lambda s:s.state.active_player=='B' and
            getattr(s.state.pending_decision,'kind',None)=='combat.attackers')
        self.assertIn(root.ref,[row['id'] for row in session.state.pending_decision.payload_by_actor['B']['candidates']])
        before=authoritative_state_hash(session.state)
        command={'action_id':'attack','atk':{root.ref:'D'}}
        self.assertFalse(session.act('pilot:A',command).ok)
        self.assertEqual(before,authoritative_state_hash(session.state))
        accepted=session.act('pilot:B',command);self.assertTrue(accepted.ok,accepted.summary)
        session=H.replay(self,session,load=True);engine=session.engine
        self.advance(session,lambda s:s.state.players['D'].life<40)
        self.assertEqual(38,engine.state.players['D'].life)
        self.assertEqual({designation:2},engine.state.players['D'].commander_damage_received)
        self.assertNotIn(root.oracle_id,engine.state.players['D'].commander_damage_received)
        identity=commander_damage_source(engine.state,designation)
        self.assertIsNotNone(identity)
        self.assertEqual((commander.oracle_id,'A'),(identity.oracle_id,identity.owner))
        self.assertNotIn(commander.object_id,engine.state.cards)
        self.assertEqual('battlefield',engine.state.cards[root.object_id].zone)
        import json
        for seat in 'ABCD':
            public=json.dumps(session.packet('pilot:'+seat,full=True)['state'])
            self.assertNotIn('permanent_composition',public)
            self.assertNotIn(commander.object_id,public)
        self.assertFalse(hasattr(identity,'zone'))
        H.replay(self,session,load=True)
