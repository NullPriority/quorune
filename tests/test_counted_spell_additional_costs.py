from __future__ import annotations

from pathlib import Path
from dataclasses import replace
import json
import tempfile
import unittest
from unittest.mock import patch

from common import ROOT
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition,DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import CapabilityRegistry,load_default_capability_registry
from quorune.session import CommanderSession
from scripts.build_test_database import build_fixture_database
from test_qualified_zone_event_queries import query_record
import test_bound_effect_programs as witnesses
import test_counter_placement_events as counter_witnesses


class CountedSpellCostCompilerTests(unittest.TestCase):
    def test_exact_counted_spell_price_keeps_cost_and_result_separate(self):
        for body,operation in (('discard two cards','discard_one'),('sacrifice two creatures','sacrifice_one'),
                              ('exile three creature cards from your graveyard','exile_one_from_graveyard')):
            text='As an additional cost to cast this spell, '+body+'.\nDraw three cards.'
            ir=compile_oracle_card(replace(query_record(text),type_line='Sorcery'),capability_registry=load_default_capability_registry())
            self.assertEqual('exact',ir.status,ir.material_residuals)
            node=ir.faces[0].nodes[0];cost=node.cost['additional_costs'][0]
            self.assertEqual(operation,cost['operation']);self.assertEqual(3 if body.startswith('exile') else 2,cost['count'])
            self.assertEqual(['draw'],[e['op'] for e in node.effects]);self.assertEqual(3,node.effects[0]['count'])
            self.assertEqual((0,len(text)),(node.span.start,node.span.end))

    def test_random_variable_and_mixed_prices_remain_material(self):
        for price in ('discard a card at random','exile X cards from your graveyard','discard a card and sacrifice a creature',
                      'sacrifice two creatures with different powers','you may sacrifice one or more creatures'):
            text='As an additional cost to cast this spell, '+price+'.\nDraw three cards.'
            self.assertNotEqual('exact',compile_oracle_card(replace(query_record(text),type_line='Sorcery'),capability_registry=load_default_capability_registry()).status)

    def test_counted_price_requires_its_cost_and_result_dependencies(self):
        text='As an additional cost to cast this spell, discard two cards.\nDraw three cards.'
        for dependency in ('casting.additional_cost.zone_change.fixed_discard','zone.draw.library_to_hand','zone.change.destination_replacement'):
            value=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
            row=next(r for r in value['capabilities'] if r['id']==dependency);row.update(status='blocked',blockers=['required owner unavailable'])
            registry=CapabilityRegistry(value);registry.mark_evidence_verified('0'*64)
            self.assertNotEqual('exact',compile_oracle_card(replace(query_record(text),type_line='Sorcery'),capability_registry=registry).status)


class CountedSpellCostRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary=tempfile.TemporaryDirectory();path=Path(cls.temporary.name)/'counted-costs.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',ROOT/'tests/fixtures/counted-spell-additional-costs.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Counted spell costs',[DeckEntry('Generic Bound Commander',1,'commander'),DeckEntry('Generic Bound Plains',30)],['Generic Bound Commander'])
    @classmethod
    def tearDownClass(cls):cls.db.close();cls.temporary.cleanup()
    session=witnesses.BoundEffectProgramRuntimeTests.session
    add=witnesses.BoundEffectProgramRuntimeTests.add
    ready=witnesses.BoundEffectProgramRuntimeTests.ready
    checkpoint=witnesses.BoundEffectProgramRuntimeTests.checkpoint
    replay=witnesses.BoundEffectProgramRuntimeTests.replay
    resolve=counter_witnesses.CounterPlacementEventRuntimeTests.resolve
    order_triggers=counter_witnesses.CounterPlacementEventRuntimeTests.order_triggers

    def test_actual_reunion_pays_two_private_discards_before_drawing_and_replays(self):
        s=self.session(312001);e=s.engine
        spell=self.add(e,'Cathartic Reunion',zone='hand');cards=[self.add(e,'Generic Bound Body',zone='hand',ref='discard-'+str(i)) for i in range(2)]
        foreign=self.add(e,'Generic Bound Body',zone='hand',seat='B')
        self.add(e,'Dauthi Voidwalker',seat='B')
        action=self.ready(s,spell,{'R':1,'C':1});self.checkpoint(s)
        offered=action['cost_options'][0];self.assertEqual(2,offered['choice_schema']['discard_cards']['count'])
        for selection in ([cards[0].ref],[cards[0].ref,cards[0].ref],[cards[0].ref,foreign.ref],[spell.ref,cards[0].ref]):
            before=authoritative_state_hash(s.state)
            rejected=s.act('pilot:A',{'action_id':action['id'],'discard_cards':selection,'pay':'auto'})
            self.assertFalse(rejected.ok);self.assertEqual(before,authoritative_state_hash(s.state))
        for seat in 'BCD':
            packet=str(s.packet('pilot:'+seat,full=True));self.assertNotIn(cards[0].ref,packet);self.assertNotIn(cards[1].ref,packet)
        hand=len(s.state.players['A'].zones['hand'])
        accepted=s.act('pilot:A',{'action_id':action['id'],'discard_cards':[c.ref for c in cards],'pay':'auto'})
        self.assertTrue(accepted.ok,accepted.summary)
        self.assertEqual(hand-3,len(s.state.players['A'].zones['hand']))
        self.assertTrue(all(s.state.cards[c.object_id].zone=='exile' for c in cards));self.resolve(s)
        self.assertEqual(hand,len(s.state.players['A'].zones['hand']));self.replay(s)

    def test_actual_bankrupt_commits_two_sacrifices_as_one_event_batch_and_replays(self):
        s=self.session(312002);e=s.engine
        observer=self.add(e,'Vengeful Townsfolk');cards=[self.add(e,'Generic Bound Body',ref='sacrifice-'+str(i)) for i in range(2)]
        spell=self.add(e,'Bankrupt in Blood',zone='hand');action=self.ready(s,spell,{'B':1,'C':1});self.checkpoint(s)
        hand=len(s.state.players['A'].zones['hand'])
        accepted=s.act('pilot:A',{'action_id':action['id'],'sacrifice_cards':[c.ref for c in cards],'pay':'auto'})
        self.assertTrue(accepted.ok,accepted.summary);self.assertTrue(all(s.state.cards[c.object_id].zone=='graveyard' for c in cards))
        self.resolve(s)
        self.assertEqual(1,s.state.cards[observer.object_id].counters['+1/+1'])
        self.assertEqual(hand+2,len(s.state.players['A'].zones['hand']));self.replay(s)

    def test_counted_admission_omission_mutant_is_killed_by_original_reunion(self):
        from quorune.compiler.spell_additional_cost_templates import fixed_zone_change_additional_cost_template
        def legacy(text,**kwargs):return fixed_zone_change_additional_cost_template(text)
        with patch('quorune.compiler.spell_additional_cost_nodes.fixed_zone_change_additional_cost_template',side_effect=legacy):
            ir=compile_oracle_card(self.db.lookup('Cathartic Reunion'),capability_registry=self.registry)
            with self.assertRaises(AssertionError):self.assertEqual('exact',ir.status)

    def test_actual_counted_discard_replacement_suspends_saves_and_replays(self):
        s=self.session(312003);e=s.engine
        spell=self.add(e,'Cathartic Reunion',zone='hand');cards=[self.add(e,'Generic Bound Body',zone='hand',ref='pending-discard-'+str(i)) for i in range(2)]
        self.add(e,'Dauthi Voidwalker',seat='B',ref='replacement-B');self.add(e,'Dauthi Voidwalker',seat='C',ref='replacement-C')
        action=self.ready(s,spell,{'R':1,'C':1});self.checkpoint(s)
        accepted=s.act('pilot:A',{'action_id':action['id'],'discard_cards':[c.ref for c in cards],'pay':'auto'})
        self.assertTrue(accepted.ok,accepted.summary);self.assertEqual('replacement.order',s.state.pending_decision.kind)
        before=authoritative_state_hash(s.state)
        wrong=s.act('pilot:B',{'action_id':'choose','replacement':'wrong'})
        self.assertFalse(wrong.ok);self.assertEqual(before,authoritative_state_hash(s.state))
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'pending';s.save(path);s=CommanderSession.load(self.db,path)
        for _ in range(8):
            if s.state.pending_decision is None or s.state.pending_decision.kind!='replacement.order':break
            principal=s.pending_principals()[0];ctx=s.packet(principal,full=True)['decision']['ctx']
            accepted=s.act(principal,{'action_id':'choose','replacement':ctx['options'][0]['id']});self.assertTrue(accepted.ok,accepted.summary)
        self.resolve(s);self.assertTrue(all(s.state.cards[c.object_id].zone=='exile' for c in cards));self.replay(s)

    def test_prior_counted_cast_journal_rejects_foreign_future_and_duplicate_identities(self):
        from types import SimpleNamespace
        from quorune.replacement.replay import _prior_casting_zone_cost_event_ids
        from quorune.replacement.model import ReplacementEffectError
        event=SimpleNamespace(event_id='zone.change:6:81:second',kind='zone.change',
            payload={'origin':'hand','destination':'graveyard','object_ref':'second'},
            affected_object=SimpleNamespace(owner='A',controller='A'))
        batch=SimpleNamespace(events=(event,))
        response={'discard_cards':['first','second'],'_mana_replacement_selections':{'zone.change:5:76:first':['effect']}}
        self.assertEqual({'zone.change:5:76:first'},_prior_casting_zone_cost_event_ids(batch,response,seat='A'))
        for ids in (['zone.change:5:76:foreign'],['zone.change:6:82:first'],['zone.change:5:76:first','zone.change:5:77:first'],
                    ['zone.change:x:76:first'],['zone.change:5:76:second']):
            with self.subTest(ids=ids),self.assertRaises(ReplacementEffectError):
                _prior_casting_zone_cost_event_ids(batch,{**response,'_mana_replacement_selections':{key:['effect'] for key in ids}},seat='A')

    def test_prior_cast_selection_omission_mutant_is_killed_by_second_object_choice(self):
        with patch('quorune.replacement.replay._prior_casting_zone_cost_event_ids',return_value=set()):
            with self.assertRaisesRegex(AssertionError,'journal is malformed'):
                self.test_actual_counted_discard_replacement_suspends_saves_and_replays()


if __name__=='__main__':unittest.main()
