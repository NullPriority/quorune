"""CR122.1h is a counter rule over the original permanent incarnation."""
import unittest
from types import SimpleNamespace
from quorune.model import CardInstance
from quorune.semantic_runtime.zone_replacements import collect_zone_change_replacement_effects
from quorune.replacement_effects import (AffectedObject,ReplaceableEvent,ReplacementClass,
    ReplacementEffect,SetField,replacement_choice,apply_replacement)


class FinalityCounterOwnerTests(unittest.TestCase):
    def card(self,*,count=1,**changes):
        card=CardInstance(object_id='permanent',ref='FINALITY',oracle_id='original',printed_name='Original permanent',
            owner='A',controller='B',zone='battlefield',counters={'finality':count},known_to=list('ABCD'),revealed_to=list('ABCD'))
        for key,value in changes.items():setattr(card,key,value)
        return card

    def effects(self,card):
        host=SimpleNamespace(active_seats=tuple('ABCD'),card_record=lambda source:None,
            semantics=SimpleNamespace(runtime_handler_programs_for_oracle=lambda *args,**kwargs:()))
        return collect_zone_change_replacement_effects(host,sources=(card,))

    def event(self,card,*,origin='battlefield',destination='graveyard',logical_object_id=None):
        return ReplaceableEvent(event_id='departure',kind='zone.change',affected_player=None,
            affected_object=AffectedObject(object_id=card.object_id,owner=card.owner,controller=card.controller),
            payload={'origin':origin,'destination':destination,'object_ref':card.ref,
                'logical_object_id':logical_object_id or card.logical_object_id,'object_kind':'card',
                'object_types':['artifact'],'owner':card.owner,'destination_controller':None})

    def test_counter_rule_is_collected_without_any_printed_ability_and_exiles_only_its_original_departure(self):
        for count in (1,2,9):
            with self.subTest(count=count):
                card=self.card(count=count);effects=self.effects(card)
                self.assertEqual(1,len(effects));effect=effects[0]
                self.assertEqual(ReplacementClass.OTHER,effect.replacement_class)
                choice=replacement_choice(self.event(card),effects);self.assertIsNotNone(choice)
                self.assertEqual('B',choice.chooser)
                changed=apply_replacement(choice,effects,effect.effect_id)
                self.assertEqual('exile',changed.payload['destination'])
                self.assertEqual('battlefield',card.zone);self.assertEqual(count,card.counters['finality'])

    def test_removed_counter_other_origins_destinations_and_returned_incarnations_do_not_match(self):
        self.assertFalse(self.effects(self.card(count=0)))
        self.assertFalse(self.effects(self.card(phased_out=True)))
        card=self.card();effects=self.effects(card)
        for change in ({'origin':'hand'},{'origin':'graveyard'},{'destination':'hand'},
                {'destination':'exile'},{'logical_object_id':card.object_id+':old'}):
            with self.subTest(change=change):self.assertIsNone(replacement_choice(self.event(card,**change),effects))

    def test_finality_competes_with_other_replacements_and_does_not_gain_self_replacement_priority(self):
        card=self.card();finality,=self.effects(card)
        other=ReplacementEffect(effect_id='other-destination',source_id='OTHER',event_kind='zone.change',
            replacement_class=ReplacementClass.OTHER,conditions={'destination':{'eq':'graveyard'}},
            operations=(SetField('destination','hand'),))
        effects=(finality,other);choice=replacement_choice(self.event(card),effects)
        self.assertEqual({finality.effect_id,other.effect_id},set(choice.options))
        for selected,destination in ((finality.effect_id,'exile'),(other.effect_id,'hand')):
            event=apply_replacement(choice,effects,selected)
            self.assertEqual(destination,event.payload['destination']);self.assertIsNone(replacement_choice(event,effects))

    def test_existing_unearth_self_replacement_keeps_its_priority_over_finality(self):
        card=self.card(unearthed=True);effects=self.effects(card);choice=replacement_choice(self.event(card),effects)
        self.assertEqual(1,len(choice.options));self.assertTrue(choice.options[0].startswith('rule:unearth:'))
        changed=apply_replacement(choice,effects,choice.options[0]);self.assertEqual('exile',changed.payload['destination'])
        self.assertIsNone(replacement_choice(changed,effects))

    def test_finality_collection_omission_mutation_is_killed(self):
        from unittest.mock import patch
        with patch('quorune.semantic_runtime.zone_replacements.finality_counter_replacement',return_value=None):
            result=unittest.TestResult()
            FinalityCounterOwnerTests('test_counter_rule_is_collected_without_any_printed_ability_and_exiles_only_its_original_departure').run(result)
        self.assertFalse(result.errors,result.errors);self.assertTrue(result.failures)

    def test_actual_counter_payloads_require_finality_without_coverage_annotations(self):
        from quorune.rules.finality_counters import finality_counter_runtime_dependencies
        for effect in ({'op':'place_counters','counter':'Finality','count':1},
                {'op':'reanimate','entry_counter':{'counter_name':'finality','amount':1}},
                {'op':'counter','counters':{'FINALITY':1}}):
            self.assertEqual(('counter.rule.finality',),finality_counter_runtime_dependencies(SimpleNamespace(effects=(effect,),handlers=(),coverage=())))
        self.assertEqual((),finality_counter_runtime_dependencies(SimpleNamespace(effects=({'op':'draw','reason':'finality'},),handlers=(),coverage=())))


class FinalityCounterCompilerTests(unittest.TestCase):
    def test_replacement_words_in_reminders_do_not_replace_material_source_classification(self):
        from quorune.oracle_ir import compile_oracle_card
        from quorune.rules.capabilities import load_default_capability_registry
        from test_granted_skulk import record
        registry=load_default_capability_registry()
        text='Return target creature card from your graveyard to the battlefield with a finality counter on it. (If a creature with a finality counter on it would die, exile it instead.)'
        ir=compile_oracle_card(record(text,'Sorcery'),capability_registry=registry)
        self.assertEqual('exact',ir.status,ir.material_residuals)
        self.assertEqual(text,ir.faces[0].nodes[0].text)
        for body in ('Draw a card. (Draw a card instead.)','Draw a card. (You may skip this reminder.)'):
            self.assertEqual('exact',compile_oracle_card(record(body,'Sorcery'),capability_registry=registry).status)
        for body in ('Draw a card. If a card would be drawn, exile it instead.',
                'Draw a card. Skip your next turn.',
                'Draw a card. (If a card would be drawn, exile it instead.',
                'Draw a card. ) (Draw a card instead.)'):
            self.assertNotEqual('exact',compile_oracle_card(record(body,'Sorcery'),capability_registry=registry).status)

    def test_finality_entry_requires_both_the_entry_owner_and_the_intrinsic_counter_rule(self):
        from quorune.oracle_ir import compile_oracle_card
        from quorune.rules.capabilities import load_default_capability_registry
        from test_granted_skulk import record
        text='Return target creature card from your graveyard to the battlefield with a finality counter on it.'
        ir=compile_oracle_card(record(text,'Sorcery'),capability_registry=load_default_capability_registry())
        self.assertEqual('exact',ir.status,ir.material_residuals)
        self.assertIn('counter.rule.finality',ir.faces[0].nodes[0].capability_dependencies)
        self.assertIn('zone.reanimate.entry_counters',ir.faces[0].nodes[0].capability_dependencies)

    def test_verified_finality_withdrawal_keeps_other_entry_counters_available(self):
        from common import ROOT
        from quorune.oracle_ir import compile_oracle_card
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index,discover_tests
        from test_granted_skulk import record
        raw=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text())
        next(c for c in raw['capabilities'] if c['id']=='counter.rule.finality').update(status='blocked',blockers=['Finality authority withdrawn'])
        registry=CapabilityRegistry(raw);index=build_index(registry_value=raw,declaration_source=read_evidence_document(ROOT/'platform/capability-evidence-declarations.json'),discovered_test_ids=set(discover_tests(ROOT).values()))
        registry.mark_evidence_verified(validate_capability_evidence_index(index,registry=registry))
        prefix='Return target creature card from your graveyard to the battlefield with '
        self.assertNotEqual('exact',compile_oracle_card(record(prefix+'a finality counter on it.','Sorcery'),capability_registry=registry).status)
        self.assertEqual('exact',compile_oracle_card(record(prefix+'a lifelink counter on it.','Sorcery'),capability_registry=registry).status)


import json,tempfile
from pathlib import Path
from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition,DeckEntry
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database


class FinalityCounterNativeTests(InheritedNativeFixture,unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary=tempfile.TemporaryDirectory();path=Path(cls.temporary.name)/'finality.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',ROOT/'tests/fixtures/finality-counter-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Finality counters',[DeckEntry('Isamaru, Hound of Konda',1,'commander'),DeckEntry('Generic Bound Plains',14)],['Isamaru, Hound of Konda'])

    def reject(self,s,seat,command):
        before=authoritative_state_hash(s.state);result=s.act('pilot:'+seat,command)
        self.assertFalse(result.ok,result.summary);self.assertEqual(before,authoritative_state_hash(s.state))

    def board(self,seed,*,commander=False):
        s=self.session(seed);spell=self.add(s.engine,'Rite of the Moth',zone='hand')
        if commander:
            target=s.state.cards[s.state.players['A'].zones['command'][0]]
            s.engine.move_card(target.object_id,'graveyard',semantic_events=False,log=False)
            target.commander_zone_choice_logical_id=target.logical_object_id
        else:target=self.add(s.engine,'Memnite',zone='graveyard',ref='actual-finality-return')
        shock=self.add(s.engine,'Shock',seat='B',zone='hand');self.add(s.engine,'Mountain',seat='B')
        private=self.add(s.engine,'Counterspell',seat='C',zone='hand',ref='finality-other-private')
        self.assert_closed(s,spell);self.assert_closed(s,shock)
        return s,spell,target,shock,private

    def test_actual_finality_reanimation_exiles_on_death_and_real_counter_removal_restores_graveyard_death(self):
        for removed in (False,True):
            with self.subTest(removed=removed):
                s,spell,target,shock,private=self.board(122100170+int(removed))
                hexmage=self.add(s.engine,'Vampire Hexmage') if removed else None
                if hexmage:self.assert_closed(s,hexmage)
                self.prepare(s,mana={'W':1,'B':2,'C':1});self.checkpoint(s)
                self.cast(s,'A',spell,[target.ref]);s=self.replay(s,load=True);self.assertIsNone(self.resolve(s))
                self.assertEqual(1,s.state.cards[target.object_id].counters.get('finality',0))
                if removed:
                    self.priority(s,'A');actions=s.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
                    offered=next(row for row in actions if row['id'].startswith('activate:'+hexmage.ref+':'))
                    command={'action_id':offered['id'],'targets':[target.ref],'pay':'auto'}
                    self.reject(s,'D',command);result=s.act('pilot:A',command);self.assertTrue(result.ok,result.summary)
                    s=self.replay(s,load=True);self.assertIsNone(self.resolve(s))
                    self.assertEqual(0,s.state.cards[target.object_id].counters.get('finality',0))
                    self.assertEqual('graveyard',s.state.cards[hexmage.object_id].zone)
                self.cast(s,'B',shock,[target.ref]);s=self.replay(s,load=True);self.assertIsNone(self.resolve(s))
                self.assertEqual('graveyard' if removed else 'exile',s.state.cards[target.object_id].zone)
                self.assertFalse(s.state.cards[target.object_id].counters)
                for viewer in 'ABD':self.assertNotIn(private.ref,json.dumps(s.packet('pilot:'+viewer,full=True)))
                self.replay(s,load=True)

    def test_actual_finality_commander_departure_exiles_before_its_owner_command_zone_choice(self):
        s,spell,target,shock,private=self.board(122100180,commander=True)
        self.prepare(s,mana={'W':1,'B':2,'C':1});self.checkpoint(s)
        self.cast(s,'A',spell,[target.ref]);self.assertIsNone(self.resolve(s))
        self.assertEqual('battlefield',s.state.cards[target.object_id].zone)
        self.cast(s,'B',shock,[target.ref]);decision=self.resolve(s)
        self.assertIsNotNone(decision);self.assertEqual('state.commander_zone',decision.kind)
        self.assertEqual('exile',s.state.cards[target.object_id].zone)
        self.assertEqual(['pilot:A'],list(s.pending_principals()));self.assertFalse(s.state.cards[target.object_id].counters)
        self.reject(s,'B',{'action_id':'command','choice':'command'});s=self.replay(s,load=True)
        result=s.act('pilot:A',{'action_id':'command','choice':'command'});self.assertTrue(result.ok,result.summary)
        self.assertEqual('command',s.state.cards[target.object_id].zone)
        for viewer in 'ABD':self.assertNotIn(private.ref,json.dumps(s.packet('pilot:'+viewer,full=True)))
        self.replay(s,load=True)

    def test_actual_finality_on_a_merged_commander_exiles_all_physical_cards_before_owner_choice(self):
        s,spell,target,shock,private=self.board(122100181,commander=True)
        mutate=self.add(s.engine,'Sea-Dasher Octopus',zone='hand');self.assert_closed(s,mutate)
        self.prepare(s,mana={'W':1,'B':2,'C':2,'U':1});self.checkpoint(s)
        self.cast(s,'A',spell,[target.ref]);self.assertIsNone(self.resolve(s));self.priority(s,'A')
        action=next(row for row in s.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions'] if row['id']=='cast:'+mutate.ref)
        option=next(row for row in action['cost_options'] if row['id'].startswith('mutate'))
        result=s.act('pilot:A',{'action_id':action['id'],'cost_option':option['id'],'targets':[target.ref],'pay':'auto'})
        self.assertTrue(result.ok,result.summary);decision=self.resolve(s);self.assertEqual('semantic.choice',decision.kind)
        s=self.replay(s,load=True);result=s.act('pilot:A',{'action_id':'choose','choice':'top'});self.assertTrue(result.ok,result.summary)
        self.assertIsNone(self.resolve(s));root=s.state.cards[target.object_id]
        self.assertEqual(1,root.counters.get('finality',0));self.assertEqual(2,len(root.permanent_composition.topology.components))
        self.cast(s,'B',shock,[root.ref]);decision=self.resolve(s)
        self.assertEqual('permanent.composition_order',decision.kind)
        from quorune.composition_batch_order_model import CompositionStateBasedOrderContinuation
        frame=CompositionStateBasedOrderContinuation.from_dict(decision.continuation['composition_state_based_order'])
        self.assertEqual('exile',frame.request.subjects[0].destination)
        self.assertEqual('A',frame.request.subjects[0].ordering_actor)
        self.assertEqual('battlefield',s.state.cards[root.object_id].zone)
        self.reject(s,'B',{'action_id':'order','cards':[root.ref,mutate.ref]})
        s=self.replay(s,load=True)
        result=s.act('pilot:A',{'action_id':'order','cards':[root.ref,mutate.ref]});self.assertTrue(result.ok,result.summary)
        decision=self.resolve(s);self.assertEqual('state.commander_zone',decision.kind)
        self.assertEqual('exile',s.state.cards[target.object_id].zone)
        self.assertEqual('exile',s.state.cards[mutate.object_id].zone)
        self.reject(s,'B',{'action_id':'command','choice':'command'});s=self.replay(s,load=True)
        result=s.act('pilot:A',{'action_id':'command','choice':'command'});self.assertTrue(result.ok,result.summary)
        self.assertEqual('command',s.state.cards[target.object_id].zone);self.assertEqual('exile',s.state.cards[mutate.object_id].zone)
        self.replay(s,load=True)
