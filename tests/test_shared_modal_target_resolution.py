from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
import json
import tempfile
import unittest
from unittest import mock

from common import ROOT
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition,DeckEntry
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
import test_spree_mode_costs as spree


class BoundTargetMutationIdentityTests(unittest.TestCase):
    def test_bound_liveness_policy_contains_only_registered_scalar_mutations(self):
        from quorune.semantic_runtime import default_semantic_handler_registry
        from quorune.semantic_runtime.direct_target_fields import _MUTATING_TARGET_FIELDS,bound_mutation_target_overrides
        operations={row['operation'] for row in default_semantic_handler_registry().inventory()}
        self.assertTrue(set(_MUTATING_TARGET_FIELDS)<=operations)
        host=SimpleNamespace(state=SimpleNamespace(cards={},stack=[]))
        item=SimpleNamespace(context={'target_snapshots':{'R1':{'object_id':'object:1','zone_change_counter':1}}})
        for operation,field in (('destroy','card'),('bounce','card'),('exile_permanent','card'),
                ('tap','card'),('untap','card'),('damage','target'),('place_counters','card')):
            with self.subTest(operation=operation):
                self.assertEqual({field:None},bound_mutation_target_overrides(host,
                    {'op':operation,field:'$target_group.clause_0.0'},item,lambda value:'R1'))

    def test_departed_bound_target_never_rebinds_but_live_repeated_target_survives(self):
        from quorune.semantic_runtime.direct_target_fields import bound_mutation_target_overrides
        card=SimpleNamespace(ref='R1',zone_change_counter=1)
        host=SimpleNamespace(state=SimpleNamespace(cards={'object:1':card},stack=[]))
        snapshot={'ref':'R1','object_id':'object:1','zone_change_counter':1,'category':'permanent'}
        item=SimpleNamespace(context={'target_snapshots':{'R1':snapshot}})
        effect={'op':'destroy','card':'$target.0'}
        resolve=lambda value:'R1'
        self.assertEqual({},bound_mutation_target_overrides(host,effect,item,resolve))
        card.zone_change_counter=2
        self.assertEqual({'card':None},bound_mutation_target_overrides(host,effect,item,resolve))
        host.state.cards={'object:new':SimpleNamespace(ref='R1',zone_change_counter=1)}
        self.assertEqual({'card':None},bound_mutation_target_overrides(host,effect,item,resolve))
        host.state.cards={}
        self.assertEqual({'card':None},bound_mutation_target_overrides(host,effect,item,resolve))

    def test_literal_unknown_information_and_historical_references_keep_existing_boundaries(self):
        from quorune.semantic_runtime.direct_target_fields import bound_mutation_target_overrides
        host=SimpleNamespace(state=SimpleNamespace(cards={},stack=[]))
        item=SimpleNamespace(context={'target_snapshots':{'R1':{'object_id':'object:1','zone_change_counter':1}}})
        for effect in ({'op':'destroy','card':'R1'}, {'op':'create_token','card':'$target.0'},
            {'op':'draw','player':'$target.controller.0'}, {'op':'unknown','card':'$target.0'}):
            self.assertEqual({},bound_mutation_target_overrides(host,effect,item,lambda value:'R1'))
        for snapshots in ({},{'R1':{'ref':'R1'}},{'R1':{'category':'player','ref':'R1'}}):
            item.context={'target_snapshots':snapshots}
            self.assertEqual({},bound_mutation_target_overrides(host,{'op':'destroy','card':'$target.0'},item,lambda value:'R1'))


class SharedModalTargetRuntimeTests(unittest.TestCase):
    register_fixture_cards_on_add=True
    session=spree.SpreeModeCostRuntimeTests.session
    add=spree.SpreeModeCostRuntimeTests.add
    checkpoint=spree.SpreeModeCostRuntimeTests.checkpoint
    resolve=spree.SpreeModeCostRuntimeTests.resolve
    replay=spree.SpreeModeCostRuntimeTests.replay
    prepare=spree.SpreeModeCostRuntimeTests.prepare
    assert_closed=spree.SpreeModeCostRuntimeTests.assert_closed
    reject=spree.SpreeModeCostRuntimeTests.reject

    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory();path=Path(cls.temp.name)/'shared-modal-target.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',
            ROOT/'tests/fixtures/spree-mode-cost-cards.json',ROOT/'tests/fixtures/fixed-nonrepeating-modal-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Shared modal target',[DeckEntry('Generic Bound Commander',1,'commander'),
            DeckEntry('Generic Bound Plains',30)],['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls):cls.db.close();cls.temp.cleanup()

    def target(self,engine,*,creature=False,shield=0):
        values={'type_line':'Artifact Creature' if creature else 'Artifact Enchantment'}
        if creature:values.update(power='3',toughness='3')
        ref=engine.create_token('B',name='Shared modal target',characteristics=values)[0]
        target=engine._resolve_object('A',ref,zones={'battlefield'})
        if shield:target.counters['shield']=shield
        return target

    def test_original_raid_reuses_surviving_target_and_ignores_departed_target(self):
        for shield in (0,1):
            with self.subTest(shield=shield):
                session=self.session(60802410+shield);engine=session.engine
                source=self.add(engine,'Requisition Raid',zone='hand')
                target=self.target(engine,shield=shield)
                private=self.add(engine,'Generic Bound Growth',seat='C',zone='hand',ref='shared-private')
                self.assert_closed(session,source)
                self.prepare(session,mana={'C':2,'W':1});self.checkpoint(session)
                action=next(row for row in session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions'] if row['id']=='cast:'+source.ref)
                targets=[target.ref,target.ref]
                self.reject(session,action['id'],'C',modes=['mode_1','mode_2'],targets=targets,pay='auto')
                result=session.act('pilot:A',{'action_id':action['id'],'modes':['mode_1','mode_2'],'targets':targets,'pay':'auto'})
                self.assertTrue(result.ok,result.summary)
                session=self.replay(session,load=True);self.resolve(session)
                current=session.state.cards.get(target.object_id)
                self.assertTrue(current is None or current.zone!='battlefield')
                self.assertEqual('graveyard',session.state.cards[source.object_id].zone)
                self.assertEqual([],session.state.stack)
                for seat in 'ABD':self.assertNotIn(private.ref,json.dumps(session.packet('pilot:'+seat,full=True)))
                self.replay(session,load=True)

    def test_original_kolaghan_later_damage_does_not_affect_departed_artifact_creature(self):
        session=self.session(60802412);engine=session.engine
        source=self.add(engine,"Kolaghan's Command",zone='hand')
        target=self.target(engine,creature=True)
        self.assert_closed(session,source)
        self.prepare(session,mana={'C':1,'B':1,'R':1});self.checkpoint(session)
        life={seat:player.life for seat,player in session.state.players.items()}
        action=next(row for row in session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions'] if row['id']=='cast:'+source.ref)
        result=session.act('pilot:A',{'action_id':action['id'],'modes':['mode_3','mode_4'],'targets':[target.ref,target.ref],'pay':'auto'})
        self.assertTrue(result.ok,result.summary)
        session=self.replay(session,load=True);self.resolve(session)
        self.assertEqual('graveyard',session.state.cards[source.object_id].zone)
        current=session.state.cards.get(target.object_id)
        self.assertTrue(current is None or current.zone!='battlefield')
        self.assertEqual([],session.state.stack)
        self.assertEqual(life,{seat:player.life for seat,player in session.state.players.items()})
        self.replay(session,load=True)

    def test_initial_unknown_and_wrong_domain_targets_still_reject_atomically(self):
        session=self.session(60802413);engine=session.engine
        source=self.add(engine,'Requisition Raid',zone='hand')
        ref=engine.create_token('B',name='Artifact only',characteristics={'type_line':'Artifact'})[0]
        self.assert_closed(session,source)
        self.prepare(session,mana={'C':2,'W':1});self.checkpoint(session)
        action=next(row for row in session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions'] if row['id']=='cast:'+source.ref)
        for targets in ([ref,ref],['missing-shared-target','missing-shared-target']):
            self.reject(session,action['id'],modes=['mode_1','mode_2'],targets=targets,pay='auto')
        self.assertEqual('hand',session.state.cards[source.object_id].zone)
        self.assertEqual(3,sum(session.state.players['A'].mana_pool.values()))

    def test_departed_target_guard_omission_is_killed_by_original_raid(self):
        with mock.patch('quorune.semantic_runtime.values.bound_mutation_target_overrides',return_value={}) as omitted:
            result=unittest.TestResult()
            witness=type(self)('test_original_raid_reuses_surviving_target_and_ignores_departed_target')
            witness.run(result)
        self.assertFalse(result.wasSuccessful())
        self.assertTrue(any('Could not find' in error for _,error in [*result.failures,*result.errors]))
        self.assertGreater(omitted.call_count,0)
