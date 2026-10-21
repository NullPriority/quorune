from __future__ import annotations

"""CR 601.2h and 701.9b: payment is mandatory, random and server owned."""

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from common import ROOT
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition,DeckEntry
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
import test_attached_protection_exceptions as controls
import test_random_discard_activation_costs as random_controls


class SpellRandomDiscardCostTests(unittest.TestCase):
    register_fixture_cards_on_add=True
    session=controls.AttachedProtectionExceptionTests.session
    add=controls.AttachedProtectionExceptionTests.add
    prepare=controls.AttachedProtectionExceptionTests.prepare
    checkpoint=controls.AttachedProtectionExceptionTests.checkpoint
    resolve=controls.AttachedProtectionExceptionTests.resolve
    replay=controls.AttachedProtectionExceptionTests.replay
    binding=controls.AttachedProtectionExceptionTests.binding
    priority=controls.AttachedProtectionExceptionTests.priority
    cast=controls.AttachedProtectionExceptionTests.cast
    actions=controls.AttachedProtectionExceptionTests.actions
    private_replay=controls.AttachedProtectionExceptionTests.private_replay
    assert_hand_private=random_controls.RandomDiscardActivationRuntimeTests.assert_hand_private

    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory();path=Path(cls.temp.name)/'random-spell-cost.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',ROOT/'tests/fixtures/attached-protection-cards.json',
                                ROOT/'tests/fixtures/spell-random-discard-cost-cards.json',ROOT/'tests/fixtures/fixed-resolution-payment-cards.json',
                                ROOT/'tests/fixtures/announced-x-additional-cost-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Random spell cost',[DeckEntry('Generic Bound Commander',1,'commander'),DeckEntry('Generic Bound Plains',30)],['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls):cls.db.close();cls.temp.cleanup()

    def test_actual_sonic_burst_pays_one_random_other_hand_card_and_preserves_private_hands(self):
        session=self.session(750600);source=self.add(session.engine,'Sonic Burst',zone='hand')
        cards=[self.add(session.engine,name,zone='hand') for name in ('Grizzly Bears','Humble','Generic Bound Growth')]
        private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand',ref='PRIVATE-C-GROWTH')
        self.assertTrue(self.binding(session,source)['strict_capability_ready'],'Complete original Sonic Burst must admit')
        self.prepare(session,mana={'R':1,'C':1});self.checkpoint(session)
        original=set(session.state.players['A'].zones['hand'])-{source.object_id}
        before=authoritative_state_hash(session.state)
        command={'action_id':'cast:'+source.ref,'targets':['B'],'pay':'manual','payment':{'R':1,'C':1}}
        self.assertFalse(session.act('pilot:D',command).ok);self.assertEqual(before,authoritative_state_hash(session.state))
        self.assertFalse(session.act('pilot:A',{**command,'cost_cards':[cards[0].ref]}).ok)
        self.assertEqual(before,authoritative_state_hash(session.state))
        self.assertFalse(session.act('pilot:A',{**command,'_random_discard_cost_refs':[cards[0].ref]}).ok)
        self.assertEqual(before,authoritative_state_hash(session.state))
        self.assertFalse(session.act('pilot:A',{**command,'payment':{'R':1}}).ok)
        self.assertEqual(before,authoritative_state_hash(session.state))
        action=next(r for r in self.actions(session) if r['id']==command['action_id'])
        self.assertNotIn('cost_cards',action.get('choice_schema',{}))
        self.assertEqual(40,session.state.players['B'].life)
        result=session.act('pilot:A',command);self.assertTrue(result.ok,result.summary)
        self.assertEqual('stack',session.state.cards[source.object_id].zone)
        self.assertEqual(1,sum(session.state.cards[oid].zone=='graveyard' for oid in original))
        self.assertEqual(len(original)-1,sum(session.state.cards[oid].zone=='hand' for oid in original))
        self.assertEqual(0,sum(session.state.players['A'].mana_pool.values()))
        session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
        self.assertEqual(36,session.state.players['B'].life)
        self.assertEqual('graveyard',session.state.cards[source.object_id].zone)
        self.assert_hand_private(session,'A',set(session.state.players['A'].zones['hand']))
        self.private_replay(session,private)

    def test_other_original_spells_keep_their_complete_damage_and_target_restrictions(self):
        for index,(name,payment,damage,creature_only) in enumerate((('Sonic Seizure',{'R':1},3,False),('Acceptable Losses',{'R':1,'C':3},5,True))):
            with self.subTest(name=name):
                session=self.session(750610+index);source=self.add(session.engine,name,zone='hand')
                body=self.add(session.engine,'Grizzly Bears',seat='B')
                self.assertTrue(self.binding(session,source)['strict_capability_ready'])
                self.prepare(session,mana=payment);self.checkpoint(session)
                original=set(session.state.players['A'].zones['hand'])
                if creature_only:
                    before=authoritative_state_hash(session.state)
                    self.assertFalse(session.act('pilot:A',{'action_id':'cast:'+source.ref,'targets':['B'],'pay':'auto'}).ok)
                    self.assertEqual(before,authoritative_state_hash(session.state))
                self.cast(session,'A',source,[body.ref if creature_only else 'B'],payment)
                self.assertEqual(len(original)-2,len(session.state.players['A'].zones['hand']))
                session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
                if creature_only:self.assertEqual('graveyard',session.state.cards[body.object_id].zone)
                else:self.assertEqual(40-damage,session.state.players['B'].life)
                self.assert_hand_private(session,'A',set(session.state.players['A'].zones['hand']));self.replay(session)

    def test_source_alone_is_unpayable_and_one_other_card_pays_even_when_spell_is_countered(self):
        session=self.session(750620);source=self.add(session.engine,'Sonic Burst',zone='hand')
        for oid in tuple(session.state.players['A'].zones['hand']):
            if oid!=source.object_id:session.engine.move_card(oid,'library',reason='fixture hand boundary')
        counter=self.add(session.engine,'Counterspell',seat='B',zone='hand')
        self.prepare(session,mana={'R':1,'C':1});session.state.players['B'].mana_pool['U']=2;self.checkpoint(session)
        self.assertFalse(any(r['id']=='cast:'+source.ref for r in self.actions(session)))
        before=authoritative_state_hash(session.state)
        self.assertFalse(session.act('pilot:A',{'action_id':'cast:'+source.ref,'targets':['B'],'pay':'auto'}).ok)
        self.assertEqual(before,authoritative_state_hash(session.state))
        other=self.add(session.engine,'Humble',zone='hand');self.prepare(session,mana={'R':1,'C':1});self.checkpoint(session)
        self.cast(session,'A',source,['B'],{'R':1,'C':1})
        self.assertEqual('graveyard',session.state.cards[other.object_id].zone)
        self.assertEqual('stack',session.state.cards[source.object_id].zone)
        target=session.state.stack[-1].ref
        self.cast(session,'B',counter,[target],{'U':2});session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
        self.assertEqual(40,session.state.players['B'].life)
        self.assertEqual(('graveyard','graveyard'),(session.state.cards[source.object_id].zone,session.state.cards[other.object_id].zone))
        self.assertEqual(0,sum(session.state.players['A'].mana_pool.values()));self.replay(session)

    def test_actual_random_payment_retains_one_subset_across_competing_replacement_choices(self):
        session=self.session(750630);source=self.add(session.engine,'Sonic Burst',zone='hand')
        for seat,name in (('B','Generic Payment Destination Replacement'),('C','Generic Payment Competing Replacement')):
            replacement=self.add(session.engine,name,seat=seat)
            self.assertTrue(self.binding(session,replacement)['strict_capability_ready'])
        self.prepare(session,mana={'R':1,'C':1});self.checkpoint(session)
        original=set(session.state.players['A'].zones['hand']);refs={session.state.cards[oid].ref for oid in original-{source.object_id}}
        self.cast(session,'A',source,['D'],{'R':1,'C':1})
        self.assertEqual('replacement.order',session.state.pending_decision.kind)
        self.assertEqual(original,set(session.state.players['A'].zones['hand']))
        self.assertEqual(2,sum(session.state.players['A'].mana_pool.values()))
        pinned=None
        for _ in range(6):
            decision=session.state.pending_decision
            if decision is None or decision.kind!='replacement.order':break
            selected=decision.continuation['priority_response']['_random_discard_cost_refs']
            self.assertEqual(1,len(selected));self.assertTrue(set(selected)<=refs)
            if pinned is None:pinned=tuple(selected)
            else:self.assertEqual(pinned,tuple(selected))
            self.assert_hand_private(session,'A',original);session=self.replay(session,load=True)
            option=session.packet('pilot:A',full=True)['decision']['ctx']['options'][0]['id']
            before=authoritative_state_hash(session.state)
            self.assertFalse(session.act('pilot:B',{'action_id':'choose','replacement':option}).ok)
            self.assertEqual(before,authoritative_state_hash(session.state))
            result=session.act('pilot:A',{'action_id':'choose','replacement':option});self.assertTrue(result.ok,result.summary)
        else:self.fail('Random spell cost replacement did not finish')
        self.assertIsNotNone(pinned);paid=next(card for card in session.state.cards.values() if card.ref==pinned[0])
        self.assertEqual('exile',paid.zone);self.assertEqual(1,sum(paid.counters.values()))
        self.assertEqual('stack',session.state.cards[source.object_id].zone)
        self.assertEqual(len(original)-2,len(session.state.players['A'].zones['hand']))
        self.assertEqual(0,sum(session.state.players['A'].mana_pool.values()))
        self.assertEqual(40,session.state.players['D'].life)
        self.assertEqual('replacement.order',self.resolve(session).kind)
        self.assertEqual(36,session.state.players['D'].life)
        session=self.replay(session,load=True)
        option=session.packet('pilot:A',full=True)['decision']['ctx']['options'][0]['id']
        result=session.act('pilot:A',{'action_id':'choose','replacement':option});self.assertTrue(result.ok,result.summary)
        self.assertIsNone(self.resolve(session));self.assertEqual('exile',session.state.cards[source.object_id].zone)
        self.assertEqual('exile',session.state.cards[paid.object_id].zone);self.replay(session,load=True)

    def test_closed_random_cost_model_rejects_selected_qualified_and_compound_prices(self):
        from quorune.rules.random_discard_additional_cost import FixedRandomDiscardAdditionalCost,random_spell_cost_dependencies,RANDOM_SPELL_COST_CAPABILITY
        from quorune.compiler.random_discard_spell_costs import fixed_random_discard_spell_cost_template
        cost=FixedRandomDiscardAdditionalCost();raw=cost.to_descriptor()
        self.assertEqual(cost,FixedRandomDiscardAdditionalCost.from_descriptor(raw))
        self.assertIn(RANDOM_SPELL_COST_CAPABILITY,random_spell_cost_dependencies({'additional_costs':[raw]}))
        for change in ({'schema_version':True},{'count':True},{'count':2},{'kind':'discard'},{'choice_field':'cost_cards'}):
            with self.assertRaises(ValueError):FixedRandomDiscardAdditionalCost.from_descriptor({**raw,**change})
        with self.assertRaises(ValueError):random_spell_cost_dependencies({'additional_costs':[raw,raw]})
        text='As an additional cost to cast this spell, discard a card at random.'
        self.assertIsNotNone(fixed_random_discard_spell_cost_template(text))
        for bad in (text.replace('a card','two cards'),text.replace('a card','a creature card'),text.replace('discard','you may discard')):
            self.assertIsNone(fixed_random_discard_spell_cost_template(bad))
        from quorune.rules.random_discard_additional_cost import random_spell_cost_reference
        response={'card':'SPELL','_mana_payment_id':'server-payment','_random_discard_cost_refs':['PAID']}
        self.assertEqual('PAID',random_spell_cost_reference(response,origin='hand',destination='graveyard',object_ref='PAID'))
        for change in ({'_random_discard_cost_refs':['SPELL']},{'_random_discard_cost_refs':['PAID','OTHER']},
                       {'_mana_payment_id':None},{'cost_cards':['PAID']},{'discard_cards':['PAID']}):
            self.assertIsNone(random_spell_cost_reference({**response,**change},origin='hand',destination='graveyard',object_ref='PAID'))
        self.assertIsNone(random_spell_cost_reference(response,origin='hand',destination='graveyard',object_ref='OTHER'))
        self.assertIsNone(random_spell_cost_reference(response,origin='battlefield',destination='graveyard',object_ref='PAID'))
        self.assertIsNone(random_spell_cost_reference(response,origin='hand',destination='exile',object_ref='PAID'))
        from types import SimpleNamespace
        from quorune.random_discard import RandomDiscardIntent,resolve_random_discard
        cards={key:SimpleNamespace(object_id=key,logical_object_id=key+'@1',ref=key,zone='hand',owner='A') for key in ('a-source','b-other')}
        host=SimpleNamespace(active_seats=['A'],state=SimpleNamespace(config=SimpleNamespace(seed=7),cards=cards,
            players={'A':SimpleNamespace(zones={'hand':list(cards)})}))
        intent=RandomDiscardIntent('A',('A',),1,'payment','cost boundary')
        with patch('quorune.random_discard.random.Random') as randomizer:
            randomizer.return_value.sample.side_effect=lambda members,count:members[:count]
            self.assertEqual(('b-other',),resolve_random_discard(host,intent,commit_batch=lambda refs:refs,exclude_object_ids=('a-source',)))
        with self.assertRaises(ValueError):resolve_random_discard(host,intent,commit_batch=lambda refs:refs,exclude_object_ids=['a-source'])

    def test_canonically_verified_random_and_discard_owner_withdrawals_and_runtime_stripping(self):
        import json
        from dataclasses import replace
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from scripts.update_capability_evidence import build_index,discover_tests
        from quorune.oracle_ir import compile_oracle_card
        from quorune.card_programs.binding import bind_semantic_program_runtime
        from quorune.rules.random_discard_additional_cost import RANDOM_SPELL_COST_CAPABILITY as cap
        raw=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        declarations=json.loads((ROOT/'platform/capability-evidence-declarations.json').read_text(encoding='utf-8'));known=set(discover_tests(ROOT).values())
        def verified(value):
            registry=CapabilityRegistry(value);index=build_index(registry_value=value,declaration_source=declarations,discovered_test_ids=known)
            registry.mark_evidence_verified(validate_capability_evidence_index(index,registry=registry));return registry
        self.assertEqual('exact',compile_oracle_card(self.db.lookup('Sonic Burst'),capability_registry=verified(raw),capability_profile='commander_review').status)
        dependencies=(cap,'zone.discard.fixed_random','casting.additional_cost.zone_change.fixed_discard')
        for dependency in dependencies:
            changed=json.loads(json.dumps(raw));next(r for r in changed['capabilities'] if r['id']==dependency).update(status='blocked',blockers=['withdrawn random spell cost owner'])
            ir=compile_oracle_card(self.db.lookup('Sonic Burst'),capability_registry=verified(changed),capability_profile='commander_review')
            self.assertNotEqual('exact',ir.status);blockers=[b for residual in ir.material_residuals for b in residual.blockers]
            self.assertTrue(any('status:'+dependency+':blocked' in b for b in blockers));self.assertFalse(any('evidence_index:unverified' in b for b in blockers))
        session=self.session(750640);source=self.add(session.engine,'Sonic Burst',zone='hand')
        program=next(p for p in session.engine.semantics.programs_for_oracle(source.oracle_id) if cap in p.capability_dependencies)
        self.assertTrue(bind_semantic_program_runtime(program,capability_registry=self.registry,profile='commander_review')['strict'])
        from quorune.compiler.random_discard_spell_costs import closed_random_discard_spell_result
        from quorune.compiler.program_generation import _is_closed_fixed_damage_program
        recognizers=(_is_closed_fixed_damage_program,)
        self.assertTrue(closed_random_discard_spell_result(program,recognizers=recognizers))
        self.assertFalse(closed_random_discard_spell_result(replace(program,effects=[{**program.effects[0],'amount':0}]),recognizers=recognizers))
        deps=tuple(c for c in program.capability_dependencies if c!=cap)
        stripped=replace(program,capability_dependencies=deps,capability_closure=self.registry.closure(deps,profile='commander_review').to_dict() if deps else None)
        self.assertFalse(closed_random_discard_spell_result(stripped,recognizers=recognizers))
        result=bind_semantic_program_runtime(stripped,capability_registry=self.registry,profile='commander_review')
        self.assertFalse(result['strict']);self.assertIn('capability:undeclared_runtime_dependency:'+cap,result['blockers'])

    def test_fresh_actual_random_cost_producer_selector_and_discard_commit_omissions_are_killed(self):
        method='test_actual_sonic_burst_pays_one_random_other_hand_card_and_preserves_private_hands'
        for owner,result in (('quorune.compiler.spell_additional_cost_nodes.fixed_random_discard_spell_cost_template',None),
                             ('quorune.rules.random_discard_additional_cost.resolve_random_discard',()),
                             ('quorune.rules.casting.commit._fixed_zone_change_commit_entries',[])):
            with patch(owner,return_value=result):
                with self.assertRaises(AssertionError):getattr(type(self)(method),method)()
