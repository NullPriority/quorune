"""CR702.140/730: actual alternatives merge without entry or fall back on lost targets.

The spell controller chooses position; the existing permanent keeps its controller,
incarnation and state. Its inherited mutation abilities see each legitimate merge.
An illegal target instead produces an ordinary creature entry under spell control.
All originals use ordinary generated registration and complete-program binding.
"""
import json,tempfile,unittest
from pathlib import Path

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition,DeckEntry
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.capabilities import CapabilityRegistry
from quorune.oracle_ir import compile_oracle_card
from scripts.build_test_database import build_fixture_database


class MutateCastNativeTests(InheritedNativeFixture,unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary=tempfile.TemporaryDirectory();path=Path(cls.temporary.name)/'mutate.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',
            ROOT/'tests/fixtures/mutate-casting-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Mutate native',[DeckEntry('Isamaru, Hound of Konda',1,'commander'),
            DeckEntry('Generic Bound Plains',14)],['Isamaru, Hound of Konda'])

    def test_verified_dependency_withdrawal_rejects_complete_mutate_source_but_retains_unrelated_creature(self):
        from copy import deepcopy
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index,discover_tests
        record=self.db.lookup('Dreamtail Heron');unrelated=self.db.lookup('Grizzly Bears')
        self.assertEqual('exact',compile_oracle_card(record,capability_registry=self.registry).status)
        raw=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding="utf-8"))
        declarations=read_evidence_document(ROOT/'platform/capability-evidence-declarations.json')
        known=set(discover_tests(ROOT).values())
        for capability in ('casting.mutate.fixed_mana','target.object.same_spell_owner',
                'continuous.characteristics.composition_copiable_values'):
            with self.subTest(capability=capability):
                changed=deepcopy(raw)
                next(row for row in changed['capabilities'] if row['id']==capability).update(
                    status='blocked',blockers=['Mutate dependency withdrawn'])
                registry=CapabilityRegistry(changed)
                index=build_index(registry_value=changed,declaration_source=declarations,discovered_test_ids=known)
                registry.mark_evidence_verified(validate_capability_evidence_index(index,registry=registry))
                self.assertNotEqual('exact',compile_oracle_card(record,capability_registry=registry).status)
                self.assertEqual('exact',compile_oracle_card(unrelated,capability_registry=registry).status)

    def scene(self,seed,*,source='Dreamtail Heron',actor='A',foreign_exile=False,copier=None):
        s=self.session(seed);engine=s.engine
        root=self.add(engine,'Grizzly Bears',ref='retained-mutate-creature')
        root.counters['+1/+1']=1
        first=self.add(engine,source,zone='exile' if foreign_exile else 'hand',ref='mutating-original-one')
        second=self.add(engine,'Dreamtail Heron',zone='hand',ref='mutating-original-two')
        human=self.add(engine,'Elite Vanguard',ref='human-mutate-boundary')
        changeling=self.add(engine,'Universal Automaton',ref='all-types-mutate-boundary')
        foreign=self.add(engine,'Grizzly Bears',seat='B',ref='other-owner-mutate-boundary')
        control=self.add(engine,'Act of Treason',seat='C',zone='hand',ref='actual-mutate-control')
        self.add(engine,'Vedalken Orrery',seat='C',ref='actual-control-timing')
        warden=self.add(engine,'Soul Warden',seat='D',ref='actual-entry-observer')
        self.add(engine,'Counterspell',seat='C',zone='hand',ref='private-mutate-control-hand')
        blink=self.add(engine,'Cloudshift',seat='C',zone='hand',ref='actual-mutate-blink')
        if copier is not None:
            copy_source=self.add(engine,'Thurid, Mare of Destiny',seat=copier,ref='actual-mutating-spell-copier')
            self.assert_closed(s,copy_source)
        self.add(engine,'Counterspell',seat='B',zone='hand',ref='actual-counter-mutate-original')
        self.prepare(s,actor=actor,active=actor,mana={'C':30,'U':10,'G':10,'W':10})
        s.state.players['C'].mana_pool.update({'C':20,'R':3,'W':3,'U':3})
        if actor!='B':s.state.players['B'].mana_pool.update({'U':3})
        if foreign_exile:
            from quorune.impulse_access import grant_temporary_cast_permission
            from quorune.impulse_access_model import TemporaryCastPermissionGrant,ImpulseAccessDuration
            grant_temporary_cast_permission(engine,card=first,
                grant=TemporaryCastPermissionGrant(actor,ImpulseAccessDuration.END_OF_TURN,None,False,
                    'Prepared existing foreign-card cast permission'),reason='Mutate permission fixture')
        self.assert_closed(s,first);self.assert_closed(s,second)
        self.assert_closed(s,control);self.assert_closed(s,warden)
        self.checkpoint(s)
        return s,root,first,second,(human,changeling,foreign),control,blink,warden

    def mutate(self,s,source,target,*,boundaries=(),actor='A',requirements=None,payment=None):
        self.priority(s,actor);source=s.state.cards[source.object_id]
        action=self.action(s,actor,source)
        options=[row for row in action['cost_options'] if row['id'].startswith('mutate')]
        if requirements is None:self.assertEqual(1,len(options),action)
        else:
            self.assertEqual(3,len(options),action)
            options=[row for row in options if all(row['requirements'][key]==value for key,value in requirements.items())]
            self.assertEqual(1,len(options),action)
        command={'action_id':action['id'],'cost_option':options[0]['id'],'targets':[target.ref],
            'pay':'manual' if payment is not None else 'auto'}
        if payment is not None:command['payment']=payment
        before=authoritative_state_hash(s.state)
        self.assertFalse(s.act('pilot:C',command).ok)
        self.assertEqual(before,authoritative_state_hash(s.state))
        for invalid in boundaries:
            self.assertFalse(s.act('pilot:'+actor,{**command,'targets':[invalid.ref]}).ok,invalid.printed_name)
            self.assertEqual(before,authoritative_state_hash(s.state))
        result=s.act('pilot:'+actor,command);self.assertTrue(result.ok,result.summary)
        item=next(item for item in s.state.stack if item.card_object_id==source.object_id)
        self.assertEqual(options[0]['id'],item.context['cost_option'])
        self.assertEqual([target.ref],item.targets)
        return item

    def choose_merge(self,s,position,*,load=False,actor='A'):
        self.until(s,lambda s:s.state.pending_decision is not None and s.state.pending_decision.kind=='semantic.choice')
        self.assertEqual([actor],s.state.pending_decision.actors)
        command={'action_id':'choose','choice':position};before=authoritative_state_hash(s.state)
        self.assertFalse(s.act('pilot:C',command).ok)
        self.assertEqual(before,authoritative_state_hash(s.state))
        self.assertFalse(s.act('pilot:'+actor,{'action_id':'choose','choice':'middle'}).ok)
        self.assertEqual(before,authoritative_state_hash(s.state))
        for seat in 'ABD':
            self.assertNotIn('private-mutate-control-hand',json.dumps(s.packet('pilot:'+seat,full=True)))
        if load:s=self.replay(s,load=True)
        result=s.act('pilot:'+actor,command);self.assertTrue(result.ok,result.summary)
        return s

    def test_actual_merge_retains_controller_and_duplicate_mutation_abilities_after_pending_load(self):
        s,root,first,second,boundaries,control,blink,warden=self.scene(702140001)
        self.cast(s,'C',control,[root.ref]);self.assertIsNone(self.resolve(s))
        root=s.state.cards[root.object_id]
        self.assertEqual(('A','C'),(root.owner,root.controller))
        incarnation=root.logical_object_id;entered=root.entered_battlefield_turn_sequence
        initial_hand=len(s.state.players['C'].zones['hand']);initial_life=s.state.players['D'].life
        self.mutate(s,first,root,boundaries=boundaries)
        s=self.choose_merge(s,'bottom',load=True)
        self.assertIsNone(self.resolve(s))
        retained=s.state.cards[root.object_id]
        self.assertEqual((incarnation,'C',entered),(retained.logical_object_id,retained.controller,retained.entered_battlefield_turn_sequence))
        self.assertEqual(1,retained.counters['+1/+1'])
        self.assertEqual(1,retained.permanent_composition.topology.mutation_count)
        self.assertEqual('Grizzly Bears',s.engine._effective_card_data(retained)['name'])
        self.assertIn('flying',s.engine._combat_keywords(retained))
        self.assertEqual(initial_hand+1,len(s.state.players['C'].zones['hand']))
        self.assertEqual(initial_life,s.state.players['D'].life,'A merge does not trigger an actual Soul Warden')
        self.assertNotIn(first.object_id,s.state.cards)
        self.mutate(s,second,retained)
        s=self.choose_merge(s,'top')
        self.assertEqual('trigger.order',s.state.pending_decision.kind)
        self.assertEqual(['C'],s.state.pending_decision.actors)
        refs=[row['id'] for row in s.packet('pilot:C',full=True)['decision']['ctx']['triggers']]
        self.assertEqual(2,len(refs));self.assertEqual(2,len(set(refs)))
        before=authoritative_state_hash(s.state)
        self.assertFalse(s.act('pilot:A',{'action_id':'order','triggers':refs}).ok)
        self.assertEqual(before,authoritative_state_hash(s.state))
        result=s.act('pilot:C',{'action_id':'order','triggers':refs});self.assertTrue(result.ok,result.summary)
        self.assertIsNone(self.resolve(s))
        retained=s.state.cards[root.object_id]
        self.assertEqual((incarnation,'C',entered),(retained.logical_object_id,retained.controller,retained.entered_battlefield_turn_sequence))
        self.assertEqual(2,retained.permanent_composition.topology.mutation_count)
        self.assertEqual('Dreamtail Heron',s.engine._effective_card_data(retained)['name'])
        self.assertEqual((4,5),(s.engine._numeric_stat(retained.object_id,'power'),s.engine._numeric_stat(retained.object_id,'toughness')))
        self.assertEqual(initial_hand+3,len(s.state.players['C'].zones['hand']),
            'Both inherited Heron occurrences trigger after the second legitimate mutation')
        self.assertEqual(initial_life,s.state.players['D'].life)
        self.assertEqual(3,len(retained.permanent_composition.parts))
        self.replay(s,load=True)

    def test_actual_blink_invalidates_incarnation_and_mutating_spell_enters_under_spell_control(self):
        s,root,source,_second,boundaries,control,blink,warden=self.scene(702140002,source='Sea-Dasher Octopus')
        self.cast(s,'C',control,[root.ref]);self.assertIsNone(self.resolve(s))
        incarnation=s.state.cards[root.object_id].logical_object_id
        life=s.state.players['D'].life
        self.mutate(s,source,root,boundaries=boundaries)
        self.cast(s,'C',blink,[root.ref]);self.resolve_top(s)
        self.assertNotEqual(incarnation,s.state.cards[root.object_id].logical_object_id)
        self.assertEqual('C',s.state.cards[root.object_id].controller)
        self.assertIsNone(self.resolve(s))
        original=s.state.cards[source.object_id]
        self.assertEqual(('battlefield','A'),(original.zone,original.controller))
        self.assertIsNone(original.permanent_composition)
        self.assertIsNone(s.state.cards[root.object_id].permanent_composition)
        self.assertEqual(life+2,s.state.players['D'].life,'Blink entry and ordinary fallback entry each trigger Soul Warden')
        self.replay(s,load=True)

    def test_actual_foreign_card_cast_keeps_card_owner_but_copy_uses_its_creator(self):
        s,root,source,_second,boundaries,control,_blink,_warden=self.scene(702140003,
            source='Majestic Auricorn',actor='B',foreign_exile=True,copier='B')
        self.cast(s,'C',control,[root.ref]);self.assertIsNone(self.resolve(s))
        life_c=s.state.players['C'].life;life_d=s.state.players['D'].life
        item=self.mutate(s,source,root,boundaries=boundaries,actor='B')
        self.assertEqual(('A','B'),(s.state.cards[source.object_id].owner,item.controller))
        self.until(s,lambda s:any(item.kind=='spell_copy' for item in s.state.stack))
        copied=next(item for item in s.state.stack if item.kind=='spell_copy')
        copied_id=copied.card_object_id
        self.assertEqual(('B','B'),(s.state.cards[copied_id].owner,copied.controller))
        self.assertEqual(item.context['mutate_payment'],copied.context['mutate_payment'])
        s=self.choose_merge(s,'bottom',actor='B')
        self.assertIsNone(self.resolve(s))
        copy=s.state.cards[copied_id];retained=s.state.cards[root.object_id]
        self.assertEqual(('battlefield','B',True),(copy.zone,copy.controller,copy.is_token))
        self.assertIsNone(copy.permanent_composition,'The B-owned copy cannot merge with the A-owned target')
        self.assertEqual(('A','C'),(retained.owner,retained.controller))
        self.assertEqual(1,retained.permanent_composition.topology.mutation_count)
        self.assertEqual(life_c+4,s.state.players['C'].life,'Only the original spell legitimately mutates')
        self.assertEqual(life_d+1,s.state.players['D'].life,'Only the illegal-target copy enters the battlefield')
        self.replay(s,load=True)

    def test_actual_countered_original_still_has_a_legal_mutating_copy_that_becomes_the_top_token(self):
        s,root,source,_second,boundaries,control,_blink,_warden=self.scene(702140004,
            source='Majestic Auricorn',copier='A')
        self.cast(s,'C',control,[root.ref]);self.assertIsNone(self.resolve(s))
        incarnation=s.state.cards[root.object_id].logical_object_id
        life_c=s.state.players['C'].life;life_d=s.state.players['D'].life
        original=self.mutate(s,source,root,boundaries=boundaries)
        counter=s.state.cards['fixture:actual-counter-mutate-original']
        self.assert_closed(s,counter);self.cast(s,'B',counter,[original.ref]);self.resolve_top(s)
        self.assertEqual('graveyard',s.state.cards[source.object_id].zone)
        s=self.choose_merge(s,'top');self.assertIsNone(self.resolve(s))
        retained=s.state.cards[root.object_id]
        self.assertEqual((incarnation,'C',True),(retained.logical_object_id,retained.controller,retained.is_token))
        self.assertEqual(1,retained.permanent_composition.topology.mutation_count)
        self.assertEqual(2,len(retained.permanent_composition.parts))
        self.assertTrue(retained.permanent_composition.topology.components[0].is_token)
        self.assertEqual('Majestic Auricorn',s.engine._effective_card_data(retained)['name'])
        self.assertEqual(life_c+4,s.state.players['C'].life)
        self.assertEqual(life_d,s.state.players['D'].life,'The copied mutating spell merges without an entry')
        self.replay(s,load=True)

    def test_actual_mixed_hybrid_mutate_cost_uses_selected_payment_and_only_created_beast_enters(self):
        s,root,source,_second,boundaries,control,_blink,_warden=self.scene(702140005,source='Trumpeting Gnarr')
        self.cast(s,'C',control,[root.ref]);self.assertIsNone(self.resolve(s))
        before=dict(s.state.players['A'].mana_pool);life_d=s.state.players['D'].life
        item=self.mutate(s,source,root,boundaries=boundaries,requirements={'GENERIC':3,'G':1,'U':1},
            payment={'C':3,'G':1,'U':1})
        for symbol,amount in {'C':3,'G':1,'U':1}.items():
            self.assertEqual(before[symbol]-amount,s.state.players['A'].mana_pool[symbol])
        self.assertEqual(('U','G'),item.mana_colors_spent)
        s=self.choose_merge(s,'bottom');self.assertIsNone(self.resolve(s))
        beasts=[card for card in s.state.cards.values() if card.zone=='battlefield' and card.is_token
            and card.controller=='C' and s.engine._effective_card_data(card)['name']=='Beast']
        self.assertEqual(1,len(beasts));self.assertEqual(life_d+1,s.state.players['D'].life)
        self.assertEqual(1,s.state.cards[root.object_id].permanent_composition.topology.mutation_count)
        self.replay(s,load=True)

    def test_actual_source_counter_mutations_affect_retained_object_and_never_its_blinked_incarnation(self):
        s,root,source,_second,boundaries,control,blink,_warden=self.scene(702140006,source='Glowstone Recluse')
        second=self.add(s.engine,'Vulpikeet',zone='hand',ref='actual-counter-second-mutation')
        self.assert_closed(s,second);self.checkpoint(s)
        self.cast(s,'C',control,[root.ref]);self.assertIsNone(self.resolve(s))
        original=s.state.cards[root.object_id].logical_object_id
        self.mutate(s,source,root,boundaries=boundaries)
        s=self.choose_merge(s,'bottom',load=True);self.assertIsNone(self.resolve(s))
        retained=s.state.cards[root.object_id]
        self.assertEqual((original,'C',3),(retained.logical_object_id,retained.controller,retained.counters['+1/+1']))
        self.assertEqual((5,5),(s.engine._numeric_stat(retained.object_id,'power'),s.engine._numeric_stat(retained.object_id,'toughness')))
        self.mutate(s,second,retained);s=self.choose_merge(s,'bottom')
        self.assertEqual('trigger.order',s.state.pending_decision.kind)
        self.assertEqual(['C'],s.state.pending_decision.actors)
        refs=[row['id'] for row in s.packet('pilot:C',full=True)['decision']['ctx']['triggers']]
        self.assertEqual(2,len(refs))
        result=s.act('pilot:C',{'action_id':'order','triggers':refs});self.assertTrue(result.ok,result.summary)
        self.cast(s,'C',blink,[root.ref]);decision=self.resolve(s)
        self.assertEqual('semantic.choice',decision.kind)
        self.assertEqual(['C'],decision.actors)
        context=s.packet('pilot:C',full=True)['decision']['ctx']
        self.assertEqual('composition_departure_order',context['operation'])
        order=[root.ref,source.ref,second.ref]
        self.assertEqual(set(order),{row['id'] for row in context['objects']})
        result=s.act('pilot:C',{'action_id':'choose','cards':order});self.assertTrue(result.ok,result.summary)
        self.assertNotEqual(original,s.state.cards[root.object_id].logical_object_id)
        self.settle(s)
        for object_id in (root.object_id,source.object_id,second.object_id):
            self.assertEqual(('battlefield','C'),(s.state.cards[object_id].zone,s.state.cards[object_id].controller))
            self.assertEqual(0,s.state.cards[object_id].counters.get('+1/+1',0))
        self.replay(s,load=True)
