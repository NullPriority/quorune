"""CR608.2h: exact token quantity syntax using existing public amount producers."""
from dataclasses import replace
import unittest
from quorune.oracle_ir import compile_oracle_card
from quorune.rules.capabilities import load_default_capability_registry
from test_qualified_zone_event_queries import query_record


class NumberOfTokenCompilerTests(unittest.TestCase):
    def test_omitting_number_of_normalization_is_killed(self):
        from unittest.mock import patch
        from quorune.compiler import scalar_effect_amounts as owner
        original=owner.re.fullmatch
        def omit(pattern,string,*args,**kwargs):
            return None if isinstance(pattern,str) and pattern.startswith('Create a number of ') else original(pattern,string,*args,**kwargs)
        text="Whenever this creature attacks, create a number of 1/1 white Rabbit creature tokens equal to this creature's power."
        def witness():
            ir=compile_oracle_card(query_record(text),capability_registry=load_default_capability_registry(),capability_profile='commander_review')
            self.assertEqual('exact',ir.status,ir.material_residuals)
        witness()
        with patch.object(owner.re,'fullmatch',side_effect=omit),self.assertRaises(AssertionError):witness()
    def test_source_power_counter_and_public_count_keep_fixed_token_definition(self):
        for text in ("Whenever this creature attacks, create a number of 1/1 white Rabbit creature tokens equal to this creature's power.",
                'When this creature dies, create a number of tapped Treasure tokens equal to its power.',
                'When this creature dies, create a number of 1/1 colorless Robot artifact creature tokens equal to the number of +1/+1 counters on this creature.',
                'Create a number of 1/1 green Saproling creature tokens equal to the number of lands you control.'):
            with self.subTest(text=text):
                ir=compile_oracle_card(replace(query_record(text),type_line='Creature — Rabbit' if text.startswith(('When','Whenever')) else 'Sorcery'),capability_registry=load_default_capability_registry(),capability_profile='commander_review')
                self.assertEqual('exact',ir.status,ir.material_residuals)
                effects=ir.faces[0].nodes[0].effects;self.assertEqual(1,len(effects))
                self.assertEqual('create_token',effects[0]['op']);self.assertIsInstance(effects[0]['quantity'],dict)

    def test_unsupported_linked_and_unknown_quantities_stay_residual(self):
        for text in ('Create a number of 1/1 green Saproling creature tokens equal to the remembered number.',
                'Create a number of Treasure tokens equal to the amount of excess damage dealt this way.',
                'Create a number of 1/1 green Saproling creature tokens equal to the result of the die roll.'):
            ir=compile_oracle_card(replace(query_record(text),type_line='Sorcery'),capability_registry=load_default_capability_registry(),capability_profile='commander_review')
            self.assertNotEqual('exact',ir.status)


import json,tempfile
from pathlib import Path
from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition,DeckEntry
from quorune.record import authoritative_state_hash
from scripts.build_test_database import build_fixture_database


class NumberOfTokenNativeTests(InheritedNativeFixture,unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary=tempfile.TemporaryDirectory();path=Path(cls.temporary.name)/'number-of-tokens.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',ROOT/'tests/fixtures/number-of-token-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Scalar token quantities',[DeckEntry('Isamaru, Hound of Konda',1,'commander'),DeckEntry('Generic Bound Plains',20)],['Isamaru, Hound of Konda'])

    def tokens(self,s,name):return [c for c in s.state.cards.values() if c.zone=='battlefield' and c.is_token and c.printed_name==name]

    def test_actual_hydra_death_uses_immediate_last_known_power_and_tapped_treasures(self):
        s=self.session(608200301)
        source=self.add(s.engine,'Goldvein Hydra');source.counters['+1/+1']=2
        growth=self.add(s.engine,'Giant Growth',zone='hand')
        kill=self.add(s.engine,'Murder',seat='B',zone='hand')
        self.add(s.engine,'Parallel Lives')
        self.assert_closed(s,source);self.assert_closed(s,growth);self.assert_closed(s,kill)
        s.state.players['B'].mana_pool.update({'B':2,'C':1});self.prepare(s,mana={'G':1});self.checkpoint(s)
        self.cast(s,'A',growth,[source.ref]);self.assertIsNone(self.resolve(s))
        self.assertEqual(5,s.engine._numeric_stat(source.object_id,'power'))
        self.cast(s,'B',kill,[source.ref]);self.resolve_top(s)
        self.assertEqual('graveyard',s.state.cards[source.object_id].zone)
        s=self.replay(s,load=True);self.assertIsNone(self.resolve(s))
        tokens=self.tokens(s,'Treasure');self.assertEqual(10,len(tokens))
        self.assertTrue(all(c.tapped for c in tokens));self.replay(s,load=True)

    def test_actual_rabbit_attack_reads_current_power_after_response_and_replays(self):
        s=self.session(608200302)
        source=self.add(s.engine,'Jacked Rabbit');source.counters['+1/+1']=2;source.acquired_control_turn_count=-1
        response=self.add(s.engine,'Giant Growth',zone='hand')
        self.add(s.engine,'Forest')
        self.assert_closed(s,source);self.assert_closed(s,response)
        self.prepare(s,mana={});self.checkpoint(s);self.attack(s,[(source,'B')])
        self.priority(s,'A');rows=s.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
        mana=next(row for row in rows if row.get('mana_ability') and 'forest' in row['id'])
        self.assertTrue(s.act('pilot:A',{'action_id':mana['id'],'pay':'auto'}).ok)
        self.cast(s,'A',response,[source.ref]);self.resolve_top(s)
        expected=s.engine._numeric_stat(source.object_id,'power')
        before=authoritative_state_hash(s.state);wrong=next(seat for seat in 'ABCD' if 'pilot:'+seat not in s.pending_principals())
        self.assertFalse(s.act('pilot:'+wrong,{'action_id':'pass'}).ok);self.assertEqual(before,authoritative_state_hash(s.state))
        s=self.replay(s,load=True);self.assertIsNone(self.resolve(s))
        self.assertEqual(expected,len(self.tokens(s,'Rabbit')));self.replay(s,load=True)

    def test_actual_mouser_death_retains_doubled_counters_before_zone_reset(self):
        s=self.session(608200303)
        source=self.add(s.engine,'Big Mother Mouser');source.counters['+1/+1']=2;source.acquired_control_turn_count=-1
        kill=self.add(s.engine,'Murder',seat='B',zone='hand')
        lands=[self.add(s.engine,'Swamp',seat='B',ref='mouser-kill-land-'+str(i)) for i in range(3)]
        self.assert_closed(s,source);self.assert_closed(s,kill)
        self.prepare(s,mana={});self.checkpoint(s);self.attack(s,[(source,'B')])
        self.assertIsNone(self.resolve(s));self.assertEqual(4,s.state.cards[source.object_id].counters['+1/+1'])
        self.priority(s,'B')
        for land in lands:
            actions=s.packet('pilot:B',full=True)['decision']['ctx']['legal']['actions']
            mana=next(row for row in actions if row.get('mana_ability') and land.ref in row['id'])
            self.assertTrue(s.act('pilot:B',{'action_id':mana['id'],'pay':'auto'}).ok)
        self.cast(s,'B',kill,[source.ref]);self.resolve_top(s)
        self.assertEqual({},s.state.cards[source.object_id].counters)
        s=self.replay(s,load=True);self.assertIsNone(self.resolve(s))
        self.assertEqual(4,len(self.tokens(s,'Robot')));self.replay(s,load=True)
