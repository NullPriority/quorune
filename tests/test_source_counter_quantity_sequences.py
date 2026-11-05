"""CR608.2c-h: source mutation before independent result-quantity evaluation."""
from dataclasses import replace
import unittest
from quorune.oracle_ir import compile_oracle_card
from quorune.rules.capabilities import load_default_capability_registry
from test_qualified_zone_event_queries import query_record


class SourceCounterQuantityCompilerTests(unittest.TestCase):
    def test_omitting_prefix_preservation_is_killed(self):
        from unittest.mock import patch
        from quorune.compiler import source_counter_quantity_sequences as owner
        text="Whenever this creature attacks, put a +1/+1 counter on it, then create a number of 1/1 red Goblin creature tokens equal to this creature's power."
        def witness():
            ir=compile_oracle_card(query_record(text),capability_registry=load_default_capability_registry(),capability_profile='commander_review')
            self.assertEqual('exact',ir.status,ir.material_residuals)
            self.assertEqual('place_counters',ir.faces[0].nodes[0].effects[0]['op'])
        witness()
        with patch.object(owner,'source_counter_quantity_sequence',return_value=None),self.assertRaises(AssertionError):witness()
    def test_source_counter_prefix_keeps_scalar_quantity_and_printed_order(self):
        for text in ("Whenever this creature attacks, put a +1/+1 counter on it, then create a number of 1/1 red Goblin creature tokens equal to this creature's power.",
                'At the beginning of your upkeep, put a muster counter on this enchantment. Then create a 1/1 red and white Soldier creature token with haste for each muster counter on this enchantment.',
                '{2}, {T}: Put a charge counter on this artifact, then draw a card for each charge counter on it.'):
            with self.subTest(text=text):
                ir=compile_oracle_card(replace(query_record(text),type_line='Artifact Creature — Goblin'),capability_registry=load_default_capability_registry(),capability_profile='commander_review')
                self.assertEqual('exact',ir.status,ir.material_residuals)
                effects=ir.faces[0].nodes[0].effects
                self.assertEqual(2,len(effects));self.assertEqual('place_counters',effects[0]['op'])
                self.assertEqual('$source.zone_object',effects[0]['card'])
                self.assertIn(effects[1]['op'],{'draw','create_token'})

    def test_open_context_and_linked_result_do_not_gain_source_reference(self):
        for text in ('Whenever another creature attacks, put a +1/+1 counter on it, then draw a card for each counter on it.',
                'Whenever this creature attacks, put a +1/+1 counter on it, then create the remembered token.',
                "Whenever this creature attacks, put a +1/+1 counter on it, then create a token with power equal to the counter's remembered value."):
            ir=compile_oracle_card(query_record(text),capability_registry=load_default_capability_registry(),capability_profile='commander_review')
            self.assertNotEqual('exact',ir.status)


import json,tempfile
from pathlib import Path
from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition,DeckEntry
from quorune.record import authoritative_state_hash
from scripts.build_test_database import build_fixture_database


class SourceCounterQuantityNativeTests(InheritedNativeFixture,unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary=tempfile.TemporaryDirectory();path=Path(cls.temporary.name)/'source-counter-quantity.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',ROOT/'tests/fixtures/source-counter-quantity-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Source counter quantity',[DeckEntry('Isamaru, Hound of Konda',1,'commander'),DeckEntry('Generic Bound Plains',20)],['Isamaru, Hound of Konda'])

    def test_actual_krenko_reads_after_replaced_counter_and_keeps_old_source_after_blink(self):
        for blinked in (False,True):
            with self.subTest(blinked=blinked):
                s=self.session(608200401+int(blinked))
                source=self.add(s.engine,'Krenko, Tin Street Kingpin');source.acquired_control_turn_count=-1
                self.add(s.engine,'Hardened Scales')
                blink=self.add(s.engine,'Cloudshift',zone='hand') if blinked else None
                land=self.add(s.engine,'Plains') if blinked else None
                self.assert_closed(s,source);self.prepare(s,mana={});self.checkpoint(s)
                original=source.logical_object_id;self.attack(s,[(source,'B')])
                if blinked:
                    self.priority(s,'A');rows=s.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
                    mana=next(row for row in rows if row.get('mana_ability') and land.ref in row['id'])
                    self.assertTrue(s.act('pilot:A',{'action_id':mana['id'],'pay':'auto'}).ok)
                    self.cast(s,'A',blink,[source.ref]);self.resolve_top(s)
                    self.assertNotEqual(original,s.state.cards[source.object_id].logical_object_id)
                s=self.replay(s,load=True);self.assertIsNone(self.resolve(s))
                current=s.state.cards[source.object_id]
                self.assertEqual(0 if blinked else 2,current.counters.get('+1/+1',0))
                goblins=[c for c in s.state.cards.values() if c.zone=='battlefield' and c.is_token and c.printed_name=='Goblin Token']
                self.assertEqual(1 if blinked else 3,len(goblins));self.replay(s,load=True)

    def test_actual_insight_engine_draws_from_postcounter_amount_and_keeps_cards_private(self):
        s=self.session(608200403);source=self.add(s.engine,'Insight Engine');source.counters['charge']=2
        self.assert_closed(s,source);self.prepare(s,mana={'C':2});self.checkpoint(s)
        hand=set(s.state.players['A'].zones['hand']);rows=s.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
        offered=next(row for row in rows if row['id'].startswith('activate:'+source.ref+':') and not row.get('mana_ability'))
        command={'action_id':offered['id'],'pay':'auto'};before=authoritative_state_hash(s.state)
        self.assertFalse(s.act('pilot:B',command).ok);self.assertEqual(before,authoritative_state_hash(s.state))
        self.assertTrue(s.act('pilot:A',command).ok);s=self.replay(s,load=True);self.assertIsNone(self.resolve(s))
        self.assertEqual(3,s.state.cards[source.object_id].counters['charge'])
        self.assertEqual(len(hand)+3,len(s.state.players['A'].zones['hand']))
        drawn=set(s.state.players['A'].zones['hand'])-hand
        for seat in 'BCD':
            packet=json.dumps(s.packet('pilot:'+seat,full=True))
            for uid in drawn:self.assertNotIn(s.state.cards[uid].ref,packet)
        self.replay(s,load=True)

    def test_actual_aligned_heart_second_spell_creates_postcounter_tokens_with_prowess(self):
        s=self.session(608200404)
        source=self.add(s.engine,'Aligned Heart');source.counters['rally']=1
        spells=[self.add(s.engine,'Memnite',zone='hand',ref='aligned-cast-'+str(i)) for i in range(2)]
        draw=self.add(s.engine,"Chaplain's Blessing",zone='hand');land=self.add(s.engine,'Plains')
        self.assert_closed(s,source);self.assert_closed(s,draw)
        self.prepare(s,mana={});self.checkpoint(s)
        self.cast(s,'A',spells[0],[]);self.assertIsNone(self.resolve(s))
        self.assertEqual(1,s.state.cards[source.object_id].counters['rally'])
        self.cast(s,'A',spells[1],[]);s=self.replay(s,load=True);self.assertIsNone(self.resolve(s))
        tokens=[c for c in s.state.cards.values() if c.zone=='battlefield' and c.is_token and c.printed_name=='Monk']
        self.assertEqual(2,len(tokens));self.assertEqual(2,s.state.cards[source.object_id].counters['rally'])
        self.priority(s,'A');rows=s.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
        mana=next(row for row in rows if row.get('mana_ability') and land.ref in row['id'])
        self.assertTrue(s.act('pilot:A',{'action_id':mana['id'],'pay':'auto'}).ok)
        self.cast(s,'A',draw,[])
        self.assertEqual('trigger.order',s.state.pending_decision.kind)
        ctx=s.packet('pilot:A',full=True)['decision']['ctx']
        self.assertEqual(2,len(ctx['triggers']))
        self.assertTrue(s.act('pilot:A',{'action_id':'order','triggers':[row['id'] for row in ctx['triggers']]}).ok)
        self.assertIsNone(self.resolve(s))
        self.assertTrue(all(s.engine._numeric_stat(token.object_id,'power')==2 for token in tokens))
        self.assertEqual(2,s.state.cards[source.object_id].counters['rally']);self.replay(s,load=True)
