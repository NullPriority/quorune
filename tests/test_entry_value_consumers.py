from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import json,tempfile,unittest
from unittest.mock import patch
from common import ROOT
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition,DeckEntry
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
import test_public_zone_quantities as controls
from quorune.rules.evidence_storage import read_evidence_document

NAMES=('Realm Seekers','Inferno Project','Hulk, Strongest There Is','Big Bertha')
FIXED='counter.producer.fixed_self_entry'
DYNAMIC='counter.producer.dynamic_self_entry'
SIZE='quantity_expression.zone_cardinality'


class EntryValueConsumerTests(unittest.TestCase):
    register_fixture_cards_on_add=True
    session=controls.PublicZoneQuantityTests.session
    add=controls.PublicZoneQuantityTests.add
    prepare=controls.PublicZoneQuantityTests.prepare
    checkpoint=controls.PublicZoneQuantityTests.checkpoint
    resolve=controls.PublicZoneQuantityTests.resolve
    replay=controls.PublicZoneQuantityTests.replay
    binding=controls.PublicZoneQuantityTests.binding
    actions=controls.PublicZoneQuantityTests.actions
    reject=controls.PublicZoneQuantityTests.reject
    private_replay=controls.PublicZoneQuantityTests.private_replay
    priority=controls.PublicZoneQuantityTests.priority
    options=controls.PublicZoneQuantityTests.options
    use=controls.PublicZoneQuantityTests.use
    top=controls.PublicZoneQuantityTests.top
    settle=controls.PublicZoneQuantityTests.settle
    normal=controls.PublicZoneQuantityTests.normal
    advance=controls.PublicZoneQuantityTests.advance
    clear_hands=controls.PublicZoneQuantityTests.clear_hands
    hand=controls.PublicZoneQuantityTests.hand
    activate=controls.controls.CreatedTokenCounterTests.activate

    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory();path=Path(cls.temp.name)/'entry-values.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',ROOT/'tests/fixtures/entry-value-consumer-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Closed entry counter values',[DeckEntry('Generic Bound Commander',1,'commander'),DeckEntry('Generic Bound Plains',30)],['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls):cls.db.close();cls.temp.cleanup()

    def test_originals_admit_with_independent_entry_and_nested_quantity_minima(self):
        from quorune.oracle_ir import compile_oracle_card
        from quorune.card_programs.binding import bind_semantic_program_runtime
        s=self.session(810000)
        for i,name in enumerate(NAMES):
            card=self.add(s.engine,name,ref='entry-value-'+str(i));bound=self.binding(s,card)
            self.assertTrue(bound['strict_capability_ready'],(name,bound['blockers']))
            ir=compile_oracle_card(self.db.lookup(name),capability_registry=self.registry,capability_profile='commander_review')
            self.assertEqual('exact',ir.status,(name,ir.material_residuals))
            cap=FIXED if name.startswith('Hulk') else DYNAMIC
            program=next((p for p in s.engine.semantics.programs_for_oracle(card.oracle_id) if cap in p.capability_dependencies),None);self.assertIsNotNone(program,name)
            # Fixed entries share their unchanged runtime DTO with legacy
            # producers; their registered consumer minimum is placement.
            # Compiler-producer withdrawal is tested separately below.
            minimum='counter.placement.quantity_replacement' if cap==FIXED else cap
            for removed in ((minimum,SIZE) if name=='Realm Seekers' else (minimum,)):
                deps=tuple(d for d in program.capability_dependencies if d!=removed) or ('counter.placement.quantity_replacement',)
                changed=replace(program,capability_dependencies=deps,capability_closure=self.registry.closure(deps,profile='commander_review').to_dict())
                result=bind_semantic_program_runtime(changed,capability_registry=self.registry,profile='commander_review')
                self.assertFalse(result['strict'],(name,removed));self.assertIn('capability:undeclared_runtime_dependency:'+removed,result['blockers'])

    def test_closed_source_pronouns_values_and_old_hidden_query_boundary(self):
        from quorune.compiler.fixed_self_entry_counter_templates import fixed_self_entry_counter_handler,dynamic_self_entry_counter_handler
        from quorune.compiler.public_zone_quantities import public_zone_quantity
        from quorune.entry_counter_model import DynamicEntryCounterAmountSpec,DynamicEntryCounterValueSource as V,DynamicEntryCounterCalculation as C
        from quorune.characteristic_fragments import CharacteristicQuantitySpec,CharacteristicQuantityScope as S
        from quorune.object_predicate import ObjectQuerySpec
        for pronoun in ('it','him','her'):
            self.assertIsNotNone(fixed_self_entry_counter_handler('Generic Source enters with a +1/+1 counter on '+pronoun+'.',source_name='Generic Source'))
            self.assertIsNotNone(dynamic_self_entry_counter_handler('Generic Source enters with X +1/+1 counters on '+pronoun+'.',source_name='Generic Source'))
        for subject in ('Target creature','Another creature','Your opponent'):
            self.assertIsNone(dynamic_self_entry_counter_handler(subject+' enters with X +1/+1 counters on her.',source_name='Generic Source'))
        quantity=public_zone_quantity("the total number of cards in all players' hands")
        spec=DynamicEntryCounterAmountSpec(value_source=V.PUBLIC_QUERY,calculation=C.MULTIPLY,quantity=quantity)
        self.assertEqual(spec,DynamicEntryCounterAmountSpec.from_dict(json.loads(json.dumps(spec.to_dict()))))
        old_hidden=CharacteristicQuantitySpec(S.CONTROLLER_ZONE,ObjectQuerySpec(zones=('hand',)))
        with self.assertRaises(ValueError):replace(spec,quantity=old_hidden)
        with self.assertRaises(ValueError):replace(quantity,query=ObjectQuerySpec(zones=('hand',),types_all=('creature',)))

    def test_real_realm_cast_response_freezes_entry_count_then_pays_search_counter(self):
        s=self.session(810010);self.clear_hands(s);spell=self.add(s.engine,'Realm Seekers',zone='hand')
        self.hand(s,'A',2);response=self.hand(s,'B',3)[0];private=self.hand(s,'C',1)[0]
        target=self.add(s.engine,'Generic Bound Body',seat='B');forest=self.top(s,'Forest','realm-search-forest')
        s.state.players['B'].mana_pool.update({'G':1});self.prepare(s,mana={'C':6,'G':3});self.checkpoint(s)
        self.normal(s,spell,{'C':4,'G':2});self.priority(s,'B')
        option=next(o for o in self.options(s,response,seat='B') if o['id']=='normal')
        self.use(s,response,option,{'G':1},targets=[target.ref],seat='B');s=self.replay(s,load=True);self.assertIsNone(self.settle(s))
        self.assertEqual(5,s.state.cards[spell.object_id].counters.get('+1/+1'))
        self.activate(s,spell,payment={'C':2,'G':1});self.assertEqual(4,s.state.cards[spell.object_id].counters.get('+1/+1'))
        s=self.replay(s,load=True);pending=self.settle(s);self.assertEqual('semantic.search',pending.kind)
        refs={r['id'] for r in s.packet('pilot:A',full=True)['decision']['ctx']['search_cards']};self.assertIn(forest.ref,refs)
        command={'action_id':'choose','search_cards':[forest.ref]};self.reject(s,command,'pilot:D')
        result=s.act('pilot:A',command);self.assertTrue(result.ok,result.summary);self.assertIsNone(self.settle(s))
        self.assertEqual('hand',s.state.cards[forest.object_id].zone);self.assertEqual(4,s.state.cards[spell.object_id].counters.get('+1/+1'));self.private_replay(s,private)

    def test_real_realm_put_from_hand_counts_its_pre_entry_card(self):
        s=self.session(810020);self.clear_hands(s);spell=self.add(s.engine,'Realm Seekers',zone='hand')
        self.hand(s,'A',1);self.hand(s,'B',2);private=self.hand(s,'C',1)[0];piper=self.add(s.engine,'Elvish Piper')
        self.assertTrue(self.binding(s,piper)['strict_capability_ready']);self.prepare(s,mana={'G':1});self.checkpoint(s)
        self.activate(s,piper,payment={'G':1});pending=self.settle(s);self.assertIsNotNone(pending)
        command={'action_id':'choose','card':spell.ref};self.reject(s,command,'pilot:D');s=self.replay(s,load=True)
        result=s.act('pilot:A',command);self.assertTrue(result.ok,result.summary);self.assertIsNone(self.settle(s))
        self.assertEqual('battlefield',s.state.cards[spell.object_id].zone);self.assertEqual(5,s.state.cards[spell.object_id].counters.get('+1/+1'));self.private_replay(s,private)

    def test_real_inferno_sums_graveyard_mana_value_and_keeps_snapshot(self):
        s=self.session(810030);spell=self.add(s.engine,'Inferno Project',zone='hand')
        self.add(s.engine,'Divination',zone='graveyard');self.add(s.engine,'Counterspell',zone='graveyard');self.add(s.engine,'Sol Ring',zone='graveyard')
        self.add(s.engine,'Divination',seat='B',zone='graveyard',ref='foreign-inferno');draw=self.add(s.engine,'Divination',zone='hand',ref='inferno-later-draw')
        private=self.add(s.engine,'Generic Bound Growth',seat='C',zone='hand');self.prepare(s,mana={'C':8,'R':1,'U':1});self.checkpoint(s)
        self.normal(s,spell,{'C':6,'R':1});s=self.replay(s,load=True);self.assertIsNone(self.settle(s))
        self.assertEqual(5,s.state.cards[spell.object_id].counters.get('+1/+1'));self.assertIn('trample',s.engine._combat_keywords(s.state.cards[spell.object_id]))
        self.normal(s,draw,{'C':2,'U':1});self.assertIsNone(self.settle(s));self.assertEqual('graveyard',s.state.cards[draw.object_id].zone)
        self.assertEqual(5,s.state.cards[spell.object_id].counters.get('+1/+1'));self.private_replay(s,private)

    def test_real_hulk_fixed_him_entry_preserves_gamma_upkeep_doubling(self):
        s=self.session(810040);self.clear_hands(s);spell=self.add(s.engine,'Hulk, Strongest There Is',zone='hand')
        other=self.add(s.engine,'Generic Bound Body');other.counters['+1/+1']=3;private=self.hand(s,'C',1)[0]
        self.prepare(s,mana={'C':2,'G':2});self.checkpoint(s);self.normal(s,spell,{'C':2,'G':2})
        s=self.replay(s,load=True);self.assertIsNone(self.settle(s));self.assertEqual(1,s.state.cards[spell.object_id].counters.get('+1/+1'))
        self.assertIn('trample',s.engine._combat_keywords(s.state.cards[spell.object_id]))
        for _ in range(160):
            s.engine.pump()
            if s.state.active_player=='A' and s.state.step=='upkeep':break
            principal=s.pending_principals()[0];kind=s.state.pending_decision.kind
            if kind=='priority':command={'action_id':'pass'}
            elif kind=='combat.attackers':command={'action_id':'attack','attacks':{}}
            elif kind=='combat.blockers':command={'action_id':'block','blocks':{}}
            elif kind=='trigger.order':command={'action_id':'order','triggers':[r['id'] for r in s.packet(principal,full=True)['decision']['ctx']['triggers']]}
            else:self.fail('Unexpected Hulk upkeep decision '+kind)
            result=s.act(principal,command);self.assertTrue(result.ok,result.summary)
        else:self.fail('Four-player Hulk upkeep was not reached')
        self.assertIsNone(self.settle(s))
        self.assertEqual(2,s.state.cards[spell.object_id].counters.get('+1/+1'));self.assertEqual(3,s.state.cards[other.object_id].counters.get('+1/+1'));self.private_replay(s,private)

    def test_real_bertha_her_entry_uses_announced_x_and_paid_tap_sibling(self):
        for x in (0,3):
            s=self.session(810050+x);spell=self.add(s.engine,'Big Bertha',zone='hand');haste=self.add(s.engine,'Expedite',zone='hand')
            private=self.add(s.engine,'Generic Bound Growth',seat='C',zone='hand');self.prepare(s,mana={'C':4,'G':2,'R':1});self.checkpoint(s)
            self.normal(s,spell,{'C':x,'G':1} if x else {'G':1},x=x);s=self.replay(s,load=True);self.assertIsNone(self.settle(s))
            self.assertEqual(x,s.state.cards[spell.object_id].counters.get('+1/+1',0));self.assertIn('vigilance',s.engine._combat_keywords(s.state.cards[spell.object_id]))
            if x:
                self.normal(s,haste,{'R':1},targets=[spell.ref]);self.assertIsNone(self.settle(s));self.activate(s,spell,payment={'C':1,'G':1})
                s=self.replay(s,load=True);self.assertIsNone(self.settle(s));self.assertEqual(x+1,s.state.cards[spell.object_id].counters.get('+1/+1'));self.assertTrue(s.state.cards[spell.object_id].tapped)
            self.private_replay(s,private)

    def test_verified_withdrawal_and_fresh_entry_value_and_pronoun_omissions_reject(self):
        from copy import deepcopy
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from scripts.update_capability_evidence import build_index,discover_tests
        from quorune.oracle_ir import compile_oracle_card
        raw=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text());decl=read_evidence_document(ROOT/'platform/capability-evidence-declarations.json')
        for cap,name in ((FIXED,'Hulk, Strongest There Is'),(DYNAMIC,'Inferno Project'),(SIZE,'Realm Seekers')):
            changed=deepcopy(raw);next(c for c in changed['capabilities'] if c['id']==cap).update(status='blocked',blockers=['withdrawn entry value producer'],dependency_fail_closed_status='not_run')
            registry=CapabilityRegistry(changed);index=build_index(registry_value=changed,declaration_source=decl,discovered_test_ids=set(discover_tests(ROOT).values()));registry.mark_evidence_verified(validate_capability_evidence_index(index,registry=registry))
            ir=compile_oracle_card(self.db.lookup(name),capability_registry=registry,capability_profile='commander_review');blockers=[b for r in ir.material_residuals for b in r.blockers]
            self.assertTrue(any('status:'+cap+':blocked' in b for b in blockers),blockers);self.assertFalse(any('evidence_index:unverified' in b for b in blockers))
        from quorune import dynamic_characteristics as quantities
        method='test_real_realm_put_from_hand_counts_its_pre_entry_card'
        with patch.object(quantities,'query_characteristic_count',return_value=0):
            with self.assertRaises(AssertionError):getattr(type(self)(method),method)()
        from quorune.compiler import fixed_self_entry_counter_templates as compiler
        method='test_real_hulk_fixed_him_entry_preserves_gamma_upkeep_doubling'
        import re
        with patch.object(compiler,'_ENTRY',re.compile(r'never matches')):
            with self.assertRaises(AssertionError):getattr(type(self)(method),method)()
