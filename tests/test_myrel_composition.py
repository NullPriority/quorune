from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import json,tempfile,unittest
from unittest.mock import patch
from common import ROOT
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition,DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
import test_static_player_restrictions as controls
import test_ability_label_composition as journeys
from quorune.rules.evidence_storage import read_evidence_document

CAP='actions.restriction.static_player'
QUERY='quantity_expression.public_query_effect_amount'
NAMES=('Myrel, Shield of Argive',)


class MyrelCompositionTests(unittest.TestCase):
    register_fixture_cards_on_add=True
    session=controls.StaticPlayerRestrictionRuntimeTests.session
    add=controls.StaticPlayerRestrictionRuntimeTests.add
    prepare=controls.StaticPlayerRestrictionRuntimeTests.prepare
    checkpoint=controls.StaticPlayerRestrictionRuntimeTests.checkpoint
    resolve=controls.StaticPlayerRestrictionRuntimeTests.resolve
    replay=controls.StaticPlayerRestrictionRuntimeTests.replay
    binding=controls.StaticPlayerRestrictionRuntimeTests.binding
    priority=controls.StaticPlayerRestrictionRuntimeTests.priority
    private_replay=controls.StaticPlayerRestrictionRuntimeTests.private_replay
    actions=controls.StaticPlayerRestrictionRuntimeTests.actions
    cast=controls.StaticPlayerRestrictionRuntimeTests.cast
    activate=controls.StaticPlayerRestrictionRuntimeTests.activate
    reject=controls.StaticPlayerRestrictionRuntimeTests.reject
    advance=journeys.AbilityLabelCompositionTests.advance

    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory();path=Path(cls.temp.name)/'myrel.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',ROOT/'tests/fixtures/myrel-composition-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Myrel composition',[DeckEntry('Generic Bound Commander',1,'commander'),DeckEntry('Generic Bound Plains',30)],['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls):cls.db.close();cls.temp.cleanup()

    def test_original_complete_admission_preserves_both_halves_and_live_locked_quantity(self):
        s=self.session(816000);source=self.add(s.engine,NAMES[0],zone='hand');bound=self.binding(s,source)
        self.assertTrue(bound['strict_capability_ready'],bound['blockers'])
        ir=compile_oracle_card(self.db.lookup(NAMES[0]),capability_registry=self.registry,capability_profile='commander_review')
        self.assertEqual('exact',ir.status,ir.material_residuals);nodes=ir.faces[0].nodes
        self.assertEqual(2,sum(n.template_id=='static-player-restriction-v1' for n in nodes))
        trigger=next(n for n in nodes if n.kind=='triggered_ability');self.assertIn(QUERY,trigger.capability_dependencies)
        effect=trigger.effects[0];self.assertEqual('$controller',effect['controller']);self.assertEqual([],effect['characteristics']['colors'])
        quantity=effect['quantity']['quantity'];self.assertEqual('controller_zone',quantity['scope']);self.assertFalse(quantity['exclude_source'])
        self.assertEqual(['soldier'],quantity['query']['subtypes_all']);self.assertEqual([],quantity['query']['types_all'])
        self.assertEqual(['battlefield'],quantity['query']['zones'])

    def test_real_myrel_cast_prohibits_battlefield_types_and_keeps_other_zones_and_land_mana(self):
        s=self.session(816010);source=self.add(s.engine,NAMES[0],zone='hand');bounce=self.add(s.engine,'Unsummon',zone='hand')
        enemy=self.add(s.engine,'Shock',seat='B',zone='hand');forest=self.add(s.engine,'Forest',seat='B')
        forbidden=[self.add(s.engine,name,seat='B') for name in ('Sol Ring','Darksteel Citadel','Cunning Sparkmage','Seal of Fire')]
        cycle=self.add(s.engine,'Striped Riverwinder',seat='B',zone='hand');skeleton=self.add(s.engine,'Reassembling Skeleton',seat='B',zone='graveyard')
        private=self.add(s.engine,'Generic Bound Growth',seat='C',zone='hand')
        s.state.players['A'].mana_pool.update({'C':3,'W':1,'U':1});s.state.players['B'].mana_pool.update({'R':1,'U':1,'B':1})
        self.prepare(s,actor='B');before=self.actions(s,'B')
        ids={card.ref:next(row['id'] for row in before if row['id'].startswith('activate:'+card.ref+':')) for card in forbidden}
        self.assertTrue(any(row['id']=='cast:'+enemy.ref for row in before));self.checkpoint(s)
        self.cast(s,source,payment={'C':3,'W':1});self.assertIsNone(self.resolve(s));self.priority(s,'B')
        offered={row['id'] for row in self.actions(s,'B')};self.assertNotIn('cast:'+enemy.ref,offered)
        self.reject(s,{'action_id':'cast:'+enemy.ref,'targets':['D'],'pay':'auto'},seat='B')
        for i,card in enumerate(forbidden):
            self.assertNotIn(ids[card.ref],offered)
            command={'action_id':ids[card.ref],'pay':'auto'}
            if i>=2:command['targets']=['D']
            self.reject(s,command,seat='B')
        self.activate(s,forest,seat='B');self.assertEqual(1,s.state.players['B'].mana_pool['G'])
        old_hand=set(s.state.players['B'].zones['hand']);self.activate(s,cycle,seat='B');self.assertIsNone(self.resolve(s))
        self.assertEqual('graveyard',s.state.cards[cycle.object_id].zone)
        drawn=set(s.state.players['B'].zones['hand'])-old_hand;self.assertEqual(1,len(drawn));draw_ref=s.state.cards[next(iter(drawn))].ref
        for seat in 'ABCD':
            packet=json.dumps(s.packet('pilot:'+seat,full=True))
            if seat=='B':self.assertIn(draw_ref,packet)
            else:self.assertNotIn(draw_ref,packet)
        self.priority(s,'B');row=next(r for r in self.actions(s,'B') if r['id'].startswith('activate:'+skeleton.ref+':'))
        command={'action_id':row['id'],'pay':'manual','payment':{'G':1,'B':1}};self.reject(s,command,seat='D')
        result=s.act('pilot:B',command);self.assertTrue(result.ok,result.summary)
        s=self.replay(s,load=True);self.assertIsNone(self.resolve(s));self.assertEqual(1,s.state.players['B'].mana_pool['R'])
        self.assertEqual('battlefield',s.state.cards[skeleton.object_id].zone);self.assertTrue(s.state.cards[skeleton.object_id].tapped)
        self.cast(s,bounce,[source.ref],payment={'U':1});self.assertIsNone(self.resolve(s));self.assertEqual('hand',s.state.cards[source.object_id].zone)
        self.activate(s,forbidden[0],seat='B');self.assertEqual(2,s.state.players['B'].mana_pool['C'])
        self.cast(s,enemy,['D'],seat='B');self.assertIsNone(self.resolve(s));self.assertEqual(38,s.state.players['D'].life)
        self.private_replay(s,private)

    def token_journey(self,*,depart):
        s=self.session(816020+depart);source=self.add(s.engine,NAMES[0],zone='hand');haste=self.add(s.engine,'Expedite',zone='hand')
        alarm=self.add(s.engine,'Raise the Alarm',zone='hand');bounce=self.add(s.engine,'Unsummon',zone='hand')
        self.add(s.engine,'Elite Vanguard');self.add(s.engine,'Generic Soldier Standard');self.add(s.engine,'Fugitive Wizard')
        self.add(s.engine,'Elite Vanguard',seat='B',ref='foreign_soldier')
        for i in range(2):self.add(s.engine,'Plains',ref='response_plains'+str(i))
        self.add(s.engine,'Island');private=self.add(s.engine,'Generic Bound Growth',seat='C',zone='hand')
        self.prepare(s,mana={'C':3,'W':1,'R':1});self.checkpoint(s)
        self.cast(s,source,payment={'C':3,'W':1});self.assertIsNone(self.resolve(s))
        self.cast(s,haste,[source.ref],payment={'R':1});self.assertIsNone(self.resolve(s))
        self.advance(s,lambda x:getattr(x.state.pending_decision,'kind',None)=='combat.attackers')
        command={'action_id':'attack','atk':{source.ref:'B'}};self.reject(s,command,seat='D')
        result=s.act('pilot:A',command);self.assertTrue(result.ok,result.summary);self.assertEqual(1,len(s.state.combat.attackers))
        self.assertTrue(s.state.stack or s.state.pending_trigger_batches);s=self.replay(s,load=True)
        if depart:
            self.cast(s,bounce,[source.ref]);self.assertIsNone(self.resolve_top(s))
            self.assertEqual('hand',s.state.cards[source.object_id].zone)
        self.cast(s,alarm);self.assertIsNone(self.resolve_top(s))
        expected=4 if depart else 5;before=set(s.state.cards)
        s=self.replay(s,load=True);self.assertIsNone(self.resolve(s))
        tokens=[card for oid,card in s.state.cards.items() if oid not in before and card.zone=='battlefield']
        self.assertEqual(expected,len(tokens))
        for token in tokens:
            data=s.engine._effective_card_data(token)
            self.assertTrue(token.is_token);self.assertEqual(('A','A'),(token.owner,token.controller));self.assertFalse(token.tapped)
            self.assertEqual([],data['colors']);self.assertIn('Artifact Creature',data['type_line']);self.assertIn('Soldier',data['type_line'])
            self.assertEqual((1,1),(s.engine._numeric_stat(token.object_id,'power'),s.engine._numeric_stat(token.object_id,'toughness')))
            self.assertNotIn(token.object_id,s.state.combat.attackers)
        events=[event for event in s.state.events if event.code=='token.create'];self.assertEqual(2,len(events));self.assertEqual(expected,events[-1].details['base_quantity'])
        if not depart:
            self.advance(s,lambda x:getattr(x.state.pending_decision,'kind',None)=='combat.blockers')
            result=s.act('pilot:B',{'action_id':'block','blocks':{}});self.assertTrue(result.ok,result.summary)
            self.advance(s,lambda x:x.state.players['B'].life<40);self.assertEqual(37,s.state.players['B'].life)
        self.private_replay(s,private)

    def resolve_top(self,s):
        initial=len(s.state.stack)
        self.assertGreater(initial,1)
        for _ in range(16):
            if len(s.state.stack)<initial:return None
            self.assertEqual('priority',s.state.pending_decision.kind)
            result=s.act(s.pending_principals()[0],{'action_id':'pass'});self.assertTrue(result.ok,result.summary)
        self.fail('Top response did not resolve above the attack trigger')

    def test_real_attack_reads_soldiers_after_response_and_creates_only_one_artifact_batch(self):
        self.token_journey(depart=False)

    def test_real_departed_myrel_trigger_resolves_and_excludes_its_departed_soldier(self):
        self.token_journey(depart=True)

    def test_verified_withdrawal_and_fresh_activation_omission_fail_closed(self):
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from scripts.update_capability_evidence import build_index,discover_tests
        raw=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding='utf-8'));declarations=read_evidence_document(ROOT/'platform/capability-evidence-declarations.json')
        for cap in (CAP,QUERY):
            changed=deepcopy(raw);next(c for c in changed['capabilities'] if c['id']==cap).update(status='blocked',blockers=['withdrawn Myrel composition owner'],dependency_fail_closed_status='not_run')
            registry=CapabilityRegistry(changed);index=build_index(registry_value=changed,declaration_source=declarations,discovered_test_ids=set(discover_tests(ROOT).values()))
            registry.mark_evidence_verified(validate_capability_evidence_index(index,registry=registry))
            ir=compile_oracle_card(self.db.lookup(NAMES[0]),capability_registry=registry,capability_profile='commander_review');blockers=[b for r in ir.material_residuals for b in r.blockers]
            self.assertTrue(any('status:'+cap+':blocked' in b for b in blockers),blockers);self.assertFalse(any('evidence_index:unverified' in b for b in blockers))
        method='test_real_myrel_cast_prohibits_battlefield_types_and_keeps_other_zones_and_land_mana'
        with patch('quorune.rules.activation.availability.current_activation_prohibitions',return_value=()):
            with self.assertRaises(AssertionError):getattr(type(self)(method),method)()
