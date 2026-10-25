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
import test_entry_value_consumers as controls
from quorune.rules.evidence_storage import read_evidence_document

CAP='damage.prevention.fixed_recipient_set'
BASE='damage.prevention.persistent_amount'
NAMES=('Kitsune Palliator',)


class FinitePreventionRecipientTests(unittest.TestCase):
    register_fixture_cards_on_add=True
    session=controls.EntryValueConsumerTests.session
    add=controls.EntryValueConsumerTests.add
    prepare=controls.EntryValueConsumerTests.prepare
    checkpoint=controls.EntryValueConsumerTests.checkpoint
    resolve=controls.EntryValueConsumerTests.resolve
    replay=controls.EntryValueConsumerTests.replay
    binding=controls.EntryValueConsumerTests.binding
    actions=controls.EntryValueConsumerTests.actions
    reject=controls.EntryValueConsumerTests.reject
    private_replay=controls.EntryValueConsumerTests.private_replay
    priority=controls.EntryValueConsumerTests.priority
    options=controls.EntryValueConsumerTests.options
    use=controls.EntryValueConsumerTests.use
    top=controls.EntryValueConsumerTests.top
    settle=controls.EntryValueConsumerTests.settle
    normal=controls.EntryValueConsumerTests.normal
    advance=controls.EntryValueConsumerTests.advance
    clear_hands=controls.EntryValueConsumerTests.clear_hands
    hand=controls.EntryValueConsumerTests.hand
    activate=controls.EntryValueConsumerTests.activate

    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory();path=Path(cls.temp.name)/'finite-prevention.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',ROOT/'tests/fixtures/finite-prevention-recipient-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Independent finite recipient shields',[DeckEntry('Generic Bound Commander',1,'commander'),DeckEntry('Generic Bound Plains',30)],['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls):cls.db.close();cls.temp.cleanup()

    def test_original_admission_and_payload_owned_minimum(self):
        from quorune.oracle_ir import compile_oracle_card
        from quorune.card_programs.binding import bind_semantic_program_runtime
        s=self.session(811000);card=self.add(s.engine,'Kitsune Palliator',zone='hand')
        bound=self.binding(s,card);self.assertTrue(bound['strict_capability_ready'],bound['blockers'])
        ir=compile_oracle_card(self.db.lookup(card.printed_name),capability_registry=self.registry,capability_profile='commander_review');self.assertEqual('exact',ir.status)
        program=next((p for p in s.engine.semantics.programs_for_oracle(card.oracle_id) if CAP in p.capability_dependencies and p.effects),None);self.assertIsNotNone(program)
        # The new producer owns its base dependency transitively; its own
        # payload minimum remains mandatory even with that base declared.
        for removed in (CAP,):
            deps=tuple(d for d in program.capability_dependencies if d!=removed)
            changed=replace(program,capability_dependencies=deps,capability_closure=self.registry.closure(deps,profile='commander_review').to_dict())
            result=bind_semantic_program_runtime(changed,capability_registry=self.registry,profile='commander_review');self.assertFalse(result['strict'],removed)
            self.assertIn('capability:undeclared_runtime_dependency:'+removed,result['blockers'])

    def test_closed_selector_shape_and_legacy_selector_remain_distinct(self):
        from quorune.prevention_recipient_sets import RECIPIENT_SET_KIND,validate_prevention_recipient_set,prevention_recipient_set_dependencies
        from quorune.compiler.prevention_templates import fixed_prevention_effect_template
        from quorune.rules.prevention_component_shapes import fixed_prevention_component_capabilities
        selector={'kind':RECIPIENT_SET_KIND,'schema_version':1};validate_prevention_recipient_set(json.loads(json.dumps(selector)))
        for bad in ({**selector,'schema_version':True},{**selector,'schema_version':2},{**selector,'query':{}},{'kind':'all_objects','schema_version':1}):
            with self.assertRaises(ValueError):validate_prevention_recipient_set(bad)
        row=fixed_prevention_effect_template('Prevent the next 1 damage that would be dealt to each creature and each player this turn.')
        self.assertIsNotNone(row);self.assertEqual({CAP,BASE},set(fixed_prevention_component_capabilities(effects=row[1],target_schema=None,mechanic_ids=row[3])))
        bad={**row[1][0],'amount':True};self.assertEqual((),fixed_prevention_component_capabilities(effects=(bad,),target_schema=None,mechanic_ids=row[3]))
        for bad in ({**row[1][0],'amount':True},{**row[1][0],'mode':'all'},{**row[1][0],'subjects':['A']}):
            with self.assertRaises(ValueError):prevention_recipient_set_dependencies(bad)
        self.assertIsNone(fixed_prevention_effect_template('Prevent the next 1 damage that would be dealt to each hidden card this turn.'))
        self.assertIsNone(fixed_prevention_effect_template('Prevent the next 0 damage that would be dealt to each creature and each player this turn.'))

    def test_real_cast_snapshots_each_player_creature_and_token_before_late_entry(self):
        s=self.session(811010);source=self.add(s.engine,'Kitsune Palliator',zone='hand');haste=self.add(s.engine,'Expedite',zone='hand')
        herd=self.add(s.engine,'Call of the Herd',zone='hand');early=self.add(s.engine,'Generic Bound Body',ref='early-shield-body')
        foreign=self.add(s.engine,'Generic Bound Body',seat='B',ref='foreign-shield-body');late=self.add(s.engine,'Generic Bound Body',zone='hand',ref='late-shield-body')
        phased=self.add(s.engine,'Generic Bound Body',seat='D',ref='phased-shield-body');phased.phased_out=True;self.add(s.engine,'Sol Ring')
        bolt=self.add(s.engine,'Lightning Bolt',zone='hand');fallout=self.add(s.engine,'Volcanic Fallout',zone='hand');private=self.add(s.engine,'Generic Bound Growth',seat='C',zone='hand')
        self.prepare(s,mana={'C':5,'W':2,'G':1,'R':4});self.checkpoint(s)
        self.normal(s,source,{'C':2,'W':1});self.assertIsNone(self.settle(s));self.normal(s,haste,{'R':1},targets=[source.ref]);self.assertIsNone(self.settle(s))
        self.normal(s,herd,{'C':2,'G':1});self.assertIsNone(self.settle(s));token=next(c for c in s.state.cards.values() if c.is_token and c.zone=='battlefield')
        self.activate(s,source);s=self.replay(s,load=True);self.assertIsNone(self.settle(s));self.assertEqual(8,len(s.state.damage_prevention_shields))
        self.normal(s,late,{'W':1});self.assertIsNone(self.settle(s));self.assertEqual(8,len(s.state.damage_prevention_shields))
        self.normal(s,bolt,{'R':1},targets=['B']);self.assertIsNone(self.settle(s));self.assertEqual(38,s.state.players['B'].life)
        self.normal(s,fallout,{'C':1,'R':2});s=self.replay(s,load=True);self.assertIsNone(self.settle(s))
        self.assertEqual([39,36,39,39],[s.state.players[p].life for p in 'ABCD'])
        for card in (source,early,foreign,token):self.assertEqual(1,s.state.cards[card.object_id].marked_damage,card.printed_name)
        self.assertEqual(2,s.state.cards[late.object_id].marked_damage);self.assertEqual(0,s.state.cards[phased.object_id].marked_damage)
        self.assertEqual([],s.state.damage_prevention_shields);self.private_replay(s,private)

    def test_real_pending_source_departure_and_player_departure_exclude_later_animation(self):
        s=self.session(811020);source=self.add(s.engine,'Kitsune Palliator');factory=self.add(s.engine,"Mishra's Factory")
        bounce=self.add(s.engine,'Unsummon',zone='hand');tremor=self.add(s.engine,'Tremor',zone='hand');private=self.add(s.engine,'Generic Bound Growth',seat='C',zone='hand')
        self.prepare(s,mana={'C':1,'U':1,'R':1});self.checkpoint(s);self.activate(s,source);self.priority(s,'B')
        result=s.act('pilot:B',{'action_id':'concede','choices':{'confirm_concede':True}});self.assertTrue(result.ok,result.summary)
        self.priority(s,'A');self.normal(s,bounce,{'U':1},targets=[source.ref]);s=self.replay(s,load=True);self.assertIsNone(self.settle(s))
        self.assertEqual('hand',s.state.cards[source.object_id].zone);self.assertEqual(3,len(s.state.damage_prevention_shields))
        self.activate(s,factory,needle='becomes',payment={'C':1});self.assertIsNone(self.settle(s));self.assertIn('creature',s.engine._type_parts(s.engine._effective_card_data(s.state.cards[factory.object_id])['type_line'])[0])
        self.normal(s,tremor,{'R':1});self.assertIsNone(self.settle(s));self.assertEqual(1,s.state.cards[factory.object_id].marked_damage)
        self.assertEqual(3,len(s.state.damage_prevention_shields));self.private_replay(s,private)

    def test_real_bounce_and_recast_does_not_reuse_old_incarnation_shield(self):
        s=self.session(811030);source=self.add(s.engine,'Kitsune Palliator');body=self.add(s.engine,'Generic Bound Body')
        bounce=self.add(s.engine,'Unsummon',zone='hand');bolt=self.add(s.engine,'Lightning Bolt',zone='hand');private=self.add(s.engine,'Generic Bound Growth',seat='C',zone='hand')
        self.prepare(s,mana={'U':1,'W':1,'R':1});self.checkpoint(s);self.activate(s,source);self.assertIsNone(self.settle(s))
        old=s.state.cards[body.object_id].logical_object_id;self.normal(s,bounce,{'U':1},targets=[body.ref]);self.assertIsNone(self.settle(s))
        self.normal(s,body,{'W':1});s=self.replay(s,load=True);self.assertIsNone(self.settle(s));self.assertNotEqual(old,s.state.cards[body.object_id].logical_object_id)
        self.normal(s,bolt,{'R':1},targets=[body.ref]);self.assertIsNone(self.settle(s));self.assertEqual(3,s.state.cards[body.object_id].marked_damage);self.private_replay(s,private)

    def test_real_two_resolutions_keep_independent_amount_for_each_recipient(self):
        s=self.session(811040);source=self.add(s.engine,'Kitsune Palliator');untap=self.add(s.engine,'Generic Bound Growth',zone='hand')
        rift=self.add(s.engine,'Flame Rift',zone='hand');private=self.add(s.engine,'Generic Bound Growth',seat='C',zone='hand',ref='private-rift-hand')
        self.prepare(s,mana={'G':1,'C':1,'R':1});self.checkpoint(s);self.activate(s,source);self.assertIsNone(self.settle(s))
        self.normal(s,untap,{'G':1},targets=[source.ref]);self.assertIsNone(self.settle(s));self.activate(s,source);s=self.replay(s,load=True);self.assertIsNone(self.settle(s))
        self.assertEqual(10,len(s.state.damage_prevention_shields));self.normal(s,rift,{'C':1,'R':1});pending=self.settle(s);choosers=[]
        for _ in range(16):
            if pending is None:break
            self.assertEqual('replacement.order',pending.kind)
            principal=s.pending_principals()[0];choosers.append(principal);ctx=s.packet(principal,full=True)['decision']['ctx']
            effect=ctx['options'][0]['id'];allocation=ctx['prevention_allocations'][effect];self.assertEqual(1,len(allocation['events']))
            command={'action_id':'choose','replacement':effect,'prevention_allocation':{allocation['events'][0]['event_id']:1}}
            self.reject(s,command,'pilot:D' if principal!='pilot:D' else 'pilot:A');s=self.private_replay(s,private)
            result=s.act(principal,command);self.assertTrue(result.ok,result.summary);pending=self.settle(s)
        else:self.fail('Finite recipient allocations did not settle')
        self.assertEqual(set('pilot:'+p for p in 'ABCD'),set(choosers))
        self.assertEqual([38]*4,[p.life for p in s.state.players.values()]);self.assertEqual(2,len(s.state.damage_prevention_shields));self.private_replay(s,private)

    def test_real_unused_shields_expire_before_next_turn_damage(self):
        s=self.session(811050);self.clear_hands(s);source=self.add(s.engine,'Kitsune Palliator');self.add(s.engine,'Mountain')
        bolt=self.add(s.engine,'Lightning Bolt',zone='hand');private=self.hand(s,'C',1)[0]
        self.prepare(s);self.checkpoint(s);self.activate(s,source);self.assertIsNone(self.settle(s));self.assertEqual(5,len(s.state.damage_prevention_shields))
        self.advance(s,lambda current:current.state.active_player=='B' and current.state.step=='upkeep');self.assertEqual([],s.state.damage_prevention_shields)
        self.priority(s,'A');option=next(o for o in self.options(s,bolt) if o['id']=='normal')
        command={'action_id':'cast:'+bolt.ref,'cost_option':option['id'],'pay':'auto','targets':['B']};self.reject(s,command,'pilot:D')
        result=s.act('pilot:A',command);self.assertTrue(result.ok,result.summary);self.assertIsNone(self.settle(s));self.assertEqual(37,s.state.players['B'].life);self.private_replay(s,private)

    def test_verified_withdrawal_and_fresh_missing_recipient_omission_reject(self):
        from copy import deepcopy
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from scripts.update_capability_evidence import build_index,discover_tests
        from quorune.oracle_ir import compile_oracle_card
        raw=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding='utf-8'));decl=read_evidence_document(ROOT/'platform/capability-evidence-declarations.json')
        for cap in (CAP,BASE):
            changed=deepcopy(raw);next(c for c in changed['capabilities'] if c['id']==cap).update(status='blocked',blockers=['withdrawn finite shield producer'],dependency_fail_closed_status='not_run')
            registry=CapabilityRegistry(changed);index=build_index(registry_value=changed,declaration_source=decl,discovered_test_ids=set(discover_tests(ROOT).values()));registry.mark_evidence_verified(validate_capability_evidence_index(index,registry=registry))
            ir=compile_oracle_card(self.db.lookup('Kitsune Palliator'),capability_registry=registry,capability_profile='commander_review');blockers=[b for r in ir.material_residuals for b in r.blockers]
            self.assertTrue(any('status:'+cap+':blocked' in b for b in blockers),blockers);self.assertFalse(any('evidence_index:unverified' in b for b in blockers))
        from quorune.effect_runtime import damage_modifiers as owner
        from quorune.damage_prevention_creation import PreventionSubjectAllocation
        method='test_real_cast_snapshots_each_player_creature_and_token_before_late_entry'
        original=owner._selected_prevention_subjects
        def omit_creatures(host,effect,**kwargs):
            if effect.get('selector',{}).get('kind')=='current_creatures_and_players':return tuple(PreventionSubjectAllocation(p,effect['amount']) for p,x in host.state.players.items() if x.in_game)
            return original(host,effect,**kwargs)
        with patch.object(owner,'_selected_prevention_subjects',side_effect=omit_creatures):
            with self.assertRaises(AssertionError):getattr(type(self)(method),method)()
