from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from pathlib import Path
import json,tempfile,unittest
from unittest.mock import patch

from common import ROOT
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition,DeckEntry
from quorune.rules.capabilities import load_default_capability_registry
from quorune.oracle_ir import compile_oracle_card
from quorune.fractional_quantity_model import HalfQuantitySpec,HALF_LIFE_CAPABILITY
from quorune.rules.scheduled_player_shapes import scheduled_player_node_capabilities
from scripts.build_test_database import build_fixture_database
from test_qualified_zone_event_queries import query_record
import test_land_search_view_equivalence as controls
import test_ability_label_composition as journeys
from quorune.rules.evidence_storage import read_evidence_document


SCHEDULED_CAP='trigger.event.scheduled_player_result'
NAME='Havoc Festival'


class ScheduledHalfLifeTests(unittest.TestCase):
    register_fixture_cards_on_add=True
    session=controls.LandSearchViewEquivalenceTests.session
    add=controls.LandSearchViewEquivalenceTests.add
    prepare=controls.LandSearchViewEquivalenceTests.prepare
    checkpoint=controls.LandSearchViewEquivalenceTests.checkpoint
    resolve=controls.LandSearchViewEquivalenceTests.resolve
    replay=controls.LandSearchViewEquivalenceTests.replay
    private_replay=controls.LandSearchViewEquivalenceTests.private_replay
    priority=controls.LandSearchViewEquivalenceTests.priority
    actions=controls.LandSearchViewEquivalenceTests.actions
    cast=controls.LandSearchViewEquivalenceTests.cast
    reject=controls.LandSearchViewEquivalenceTests.reject
    binding=controls.LandSearchViewEquivalenceTests.binding
    activate=controls.LandSearchViewEquivalenceTests.activate
    activation_command=controls.LandSearchViewEquivalenceTests.activation_command
    resume=controls.LandSearchViewEquivalenceTests.resume
    advance=journeys.AbilityLabelCompositionTests.advance

    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory();path=Path(cls.temp.name)/'scheduled-half-life.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',ROOT/'tests/fixtures/scheduled-half-life-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Scheduled half life',[DeckEntry('Generic Bound Commander',1,'commander'),DeckEntry('Generic Bound Plains',30)],['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls):cls.db.close();cls.temp.cleanup()

    def test_bound_half_values_keep_original_event_player_and_full_trigger_minima(self):
        for i,(schedule,subject,rounding) in enumerate((("each player's upkeep",'that player','up'),
                ("each opponent's upkeep",'they','down'),("each player's draw step",'that player','up'))):
            verb='lose' if subject=='they' else 'loses'
            text=f'At the beginning of {schedule}, {subject} {verb} half their life, rounded {rounding}.'
            record=replace(query_record(text),oracle_id='fixture:scheduled-half-'+str(i))
            ir=compile_oracle_card(record,capability_registry=self.registry,capability_profile='commander_review')
            self.assertEqual('exact',ir.status,ir.material_residuals)
            node=ir.faces[0].nodes[0]
            self.assertEqual('step.begin',node.event);self.assertIsNone(node.target_schema)
            self.assertEqual('$context.player',node.effects[0]['player'])
            self.assertEqual(HalfQuantitySpec(rounding).to_dict(),node.effects[0]['amount'])
            self.assertTrue({SCHEDULED_CAP,HALF_LIFE_CAPABILITY,'life.change.effect','trigger.effect.fixed_event','trigger.placement.apnap'} <= set(node.capability_dependencies))
            self.assertEqual((0,len(text)),(node.span.start,node.span.end))

    def test_shape_and_wrong_subjects_keep_fraction_rounding_and_domain_boundaries(self):
        effect={'op':'lose_life','player':'$context.player','amount':HalfQuantitySpec('up').to_dict()}
        mechanics=('scheduled-player-event-result','half-current-player-life-loss','cr-119-life')
        args=dict(effects=(effect,),target_schema=None,mechanic_ids=mechanics)
        self.assertIn(HALF_LIFE_CAPABILITY,scheduled_player_node_capabilities(**args))
        for value in (True,0,-1,{'kind':'half_quantity','schema_version':1,'divisor':3,'rounding':'up'},
                {'kind':'half_quantity','schema_version':1,'divisor':True,'rounding':'up'},
                {'kind':'half_quantity','schema_version':1,'divisor':2}):
            self.assertEqual((),scheduled_player_node_capabilities(**{**args,'effects':({**effect,'amount':value},)}))
        for player in ('$target.0',None,[],True):
            self.assertEqual((),scheduled_player_node_capabilities(**{**args,'effects':({**effect,'player':player},)}))
        self.assertEqual((),scheduled_player_node_capabilities(**{**args,'mechanic_ids':('scheduled-player-event-result',)}))
        for op in ('draw','mill','damage'):
            self.assertEqual((),scheduled_player_node_capabilities(**{**args,'effects':({**effect,'op':op},)}))
        for body in ('that player loses half your life, rounded up.','that player gains half their life, rounded up.',
                'that player loses half their life.','that player loses a third of their life, rounded up.',
                'that player loses half their life, rounded up. You draw a card.'):
            ir=compile_oracle_card(query_record("At the beginning of each player's upkeep, "+body),capability_registry=self.registry)
            self.assertNotEqual('exact',ir.status,body)

    def test_original_normally_binds_both_static_and_scheduled_abilities(self):
        s=self.session(119720000);source=self.add(s.engine,NAME,zone='hand')
        b=self.binding(s,source);self.assertTrue(b['strict_capability_ready'],b['blockers'])
        programs=s.engine.semantics.programs_for_oracle(source.oracle_id)
        self.assertTrue(all(p.trust_level=='trusted' and not p.requires_arbiter for p in programs))
        p=next(p for p in programs if p.event=='step.begin')
        self.assertEqual('$context.player',p.effects[0]['player'])
        self.assertTrue({SCHEDULED_CAP,HALF_LIFE_CAPABILITY} <= set(p.capability_dependencies))

    def test_real_paid_festival_keeps_scheduled_player_and_reads_odd_life_after_source_departure(self):
        s=self.session(119720010);source=self.add(s.engine,NAME,zone='hand')
        remove=self.add(s.engine,'Disenchant',seat='B',zone='hand');heal=self.add(s.engine,'Revitalize',seat='B',zone='hand')
        lands=[self.add(s.engine,'Plains',seat='B',ref='half-white-land-'+str(i)) for i in range(4)]
        private=self.add(s.engine,'Generic Bound Growth',seat='C',zone='hand')
        self.prepare(s,mana={'C':4,'B':1,'R':1});self.checkpoint(s)
        self.cast(s,source,payment={'C':4,'B':1,'R':1});self.assertIsNone(self.resolve(s))
        self.advance(s,lambda x:x.state.active_player=='B' and x.state.step=='upkeep' and bool(x.state.stack))
        self.assertEqual('A',s.state.stack[-1].controller)
        for land in lands[:2]:self.activate(s,land,seat='B')
        self.cast(s,remove,[source.ref],seat='B',payment={'W':2})
        for _ in range(12):
            if s.state.cards[source.object_id].zone=='graveyard':break
            result=s.act(s.pending_principals()[0],{'action_id':'pass'});self.assertTrue(result.ok,result.summary)
        self.assertEqual('graveyard',s.state.cards[source.object_id].zone)
        self.assertEqual(40,s.state.players['B'].life)
        for land in lands[2:]:self.activate(s,land,seat='B')
        before=set(s.state.players['B'].zones['hand'])
        self.cast(s,heal,seat='B',payment={'W':2})
        for _ in range(12):
            if s.state.cards[heal.object_id].zone=='graveyard':break
            result=s.act(s.pending_principals()[0],{'action_id':'pass'});self.assertTrue(result.ok,result.summary)
        self.assertEqual(43,s.state.players['B'].life)
        drawn=set(s.state.players['B'].zones['hand'])-before;self.assertEqual(1,len(drawn))
        ref=s.state.cards[next(iter(drawn))].ref
        for seat in 'ACD':self.assertNotIn(ref,json.dumps(s.packet('pilot:'+seat,full=True)))
        s=self.resume(s);self.assertIsNone(self.resolve(s))
        self.assertEqual({'A':40,'B':21,'C':40,'D':40},{seat:s.state.players[seat].life for seat in 'ABCD'})
        self.private_replay(s,private)

    def test_real_each_player_upkeeps_lose_their_own_current_half_once(self):
        s=self.session(119720020);source=self.add(s.engine,NAME,zone='hand')
        private=self.add(s.engine,'Generic Bound Growth',seat='C',zone='hand')
        for seat,value in zip('ABCD',(41,43,17,40)):s.state.players[seat].life=value
        self.prepare(s,mana={'C':4,'B':1,'R':1});self.checkpoint(s)
        self.cast(s,source,payment={'C':4,'B':1,'R':1});self.assertIsNone(self.resolve(s))
        expected=dict(zip('ABCD',(41,43,17,40)))
        for seat in 'BCDA':
            self.advance(s,lambda x,seat=seat:x.state.active_player==seat and x.state.step=='upkeep' and bool(x.state.stack))
            self.assertEqual('A',s.state.stack[-1].controller)
            expected[seat]-=HalfQuantitySpec('up').amount(expected[seat])
            self.assertIsNone(self.resolve(s))
            self.assertEqual(expected,{p:s.state.players[p].life for p in 'ABCD'})
        self.assertEqual({'A':20,'B':21,'C':8,'D':20},expected)
        self.private_replay(s,private)

    def test_verified_each_owner_withdrawal_blocks_composition(self):
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from scripts.update_capability_evidence import build_index,discover_tests
        raw=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        for cap in (SCHEDULED_CAP,HALF_LIFE_CAPABILITY):
            changed=deepcopy(raw);next(r for r in changed['capabilities'] if r['id']==cap).update(status='blocked',blockers=['withdrawn scheduled fractional owner'],dependency_fail_closed_status='not_run')
            registry=CapabilityRegistry(changed)
            index=build_index(registry_value=changed,declaration_source=read_evidence_document(ROOT/'platform/capability-evidence-declarations.json'),discovered_test_ids=set(discover_tests(ROOT).values()))
            registry.mark_evidence_verified(validate_capability_evidence_index(index,registry=registry))
            ir=compile_oracle_card(self.db.lookup(NAME),capability_registry=registry,capability_profile='commander_review')
            blockers=[b for r in ir.material_residuals for b in r.blockers]
            self.assertTrue(any('status:'+cap+':blocked' in b for b in blockers),blockers)
            self.assertFalse(any('evidence_index:unverified' in b for b in blockers),blockers)

    def test_fresh_value_normalization_and_shape_omissions_are_detected(self):
        method='test_original_normally_binds_both_static_and_scheduled_abilities'
        with patch('quorune.compiler.scheduled_player_triggers.scheduled_fractional_life_instruction',return_value=None):
            with self.assertRaises(AssertionError):getattr(type(self)(method),method)()
        method='test_shape_and_wrong_subjects_keep_fraction_rounding_and_domain_boundaries'
        with patch('quorune.rules.scheduled_player_shapes.event_result_value_capabilities',return_value=()):
            with self.assertRaises(AssertionError):getattr(type(self)(method),method)()


if __name__=='__main__':unittest.main()
