"""CR 111.3/201.5: a token's printed self name binds that token's program."""
from dataclasses import replace
import json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch

from common import ROOT,packet_without_decision_capability
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition,DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
import test_target_recipient_results as recipients
import test_qualified_zone_event_queries as records


class LiteralTokenSourceTests(InheritedNativeFixture,unittest.TestCase):
    reject=recipients.TargetRecipientNativeTests.reject
    finish=recipients.TargetRecipientNativeTests.finish
    tokens=recipients.TargetRecipientNativeTests.tokens

    @classmethod
    def setUpClass(cls):
        cls.temporary=tempfile.TemporaryDirectory();path=Path(cls.temporary.name)/'literal-token-sources.sqlite3'
        base=json.loads((ROOT/'tests/fixtures/bound-effect-program-cards.json').read_text(encoding='utf-8'))
        base['cards']=[row for row in base['cards'] if row['name']=='Generic Bound Plains'];base['rulings']=[]
        basic=Path(cls.temporary.name)/'basic.json';basic.write_text(json.dumps(base),encoding='utf-8')
        build_fixture_database([basic,ROOT/'tests/fixtures/literal-token-source-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Literal token sources',[DeckEntry('Sliver Hivelord',1,'commander'),
            DeckEntry('Generic Bound Plains',30)],['Sliver Hivelord'])

    def test_historical_child_route_keeps_its_program_and_incomplete_named_children_remain_residual(self):
        from quorune.compiler import typed_token_ability_nodes as owner
        record=replace(records.query_record('Create a 1/1 blue Bird creature token with "Whenever this token attacks, surveil 1."'),
            type_line='Sorcery',mana_cost='{U}',power=None,toughness=None,keywords=())
        with patch.object(owner,'_inner_node',wraps=owner._inner_node) as calls:
            ir=compile_oracle_card(record,capability_registry=self.registry)
        self.assertEqual('exact',ir.status,ir.material_residuals)
        self.assertTrue(calls.call_args_list)
        self.assertTrue(all(call.kwargs.get('token_source_name','Created Token')=='Created Token' for call in calls.call_args_list))
        original=self.db.lookup('Falcon, Winged Wonder',fuzzy=False)
        for text in (original.oracle_text.replace('Whenever Redwing attacks','Whenever Falcon attacks'),
                     original.oracle_text.replace('surveil 1.','surveil 1. Rewrite history.')):
            self.assertNotEqual('exact',compile_oracle_card(replace(original,oracle_text=text),capability_registry=self.registry).status)
        self.assertNotEqual('exact',compile_oracle_card(self.db.lookup('Ozox, the Clattering King',fuzzy=False),capability_registry=self.registry).status)

    def test_named_child_source_omission_mutant_loses_the_complete_original_composition(self):
        from quorune.compiler import typed_token_ability_nodes as owner
        original=owner._inner_node
        for name in ('Falcon, Winged Wonder','Ka-Zar of the Savage Land'):
            card=self.db.lookup(name,fuzzy=False)
            self.assertEqual('exact',compile_oracle_card(card,capability_registry=self.registry).status)
            def omit(**kwargs):return None if kwargs.get('token_source_name','Created Token')!='Created Token' else original(**kwargs)
            with patch.object(owner,'_inner_node',side_effect=omit):
                self.assertNotEqual('exact',compile_oracle_card(card,capability_registry=self.registry).status)

    def test_actual_redwing_attacks_surveils_for_its_controller_while_falcon_attacks_do_not(self):
        for attack_token in (False,True):
            with self.subTest(attack_token=attack_token):
                s=self.session(1001100+attack_token);source=self.add(s.engine,'Falcon, Winged Wonder',zone='hand')
                self.add(s.engine,'Fervor');top=self.add(s.engine,'Forest',zone='library',ref='redwing-private-top')
                private=self.add(s.engine,'Counterspell',seat='C',zone='hand',ref='literal-token-private-hand')
                library=s.state.players['A'].zones['library'];library[:]=[oid for oid in library if oid!=top.object_id]+[top.object_id]
                self.assert_closed(s,source);self.prepare(s,mana={'C':4,'W':2,'U':2});self.checkpoint(s)
                self.reject(s,'D',{'action_id':'cast:'+source.ref,'pay':'auto'});self.cast(s,'A',source)
                self.assertIsNone(self.resolve(s));token,=self.tokens(s,'A')
                data=s.engine._effective_card_data(token)
                self.assertEqual('Redwing',data['name']);self.assertEqual('A',token.owner);self.assertIn('Legendary',data['type_line'])
                self.assertIn('Scout',data['type_line']);self.assertEqual(['U'],list(data['colors']))
                self.assertEqual((1,1),tuple(s.engine._numeric_stat(token.object_id,stat) for stat in ('power','toughness')))
                self.assertIn('flying',s.engine._combat_keywords(token))
                self.attack(s,[(token if attack_token else source,'B')])
                self.assertEqual(int(attack_token),self.trigger_count(s,token));self.assertEqual(0,self.trigger_count(s,source))
                if attack_token:
                    decision=self.resolve(s);self.assertEqual('semantic.choice',decision.kind)
                    for seat in 'BCD':self.assertNotIn(top.ref,json.dumps(packet_without_decision_capability(s.packet('pilot:'+seat,full=True))))
                    self.assertIn(top.ref,json.dumps(packet_without_decision_capability(s.packet('pilot:A',full=True))))
                    command={'action_id':'choose','cards':{'top':[],'graveyard':[top.ref]}}
                    self.reject(s,'B',command);accepted=s.act('pilot:A',command);self.assertTrue(accepted.ok,accepted.summary)
                    self.assertIn(top.object_id,s.state.players['A'].zones['graveyard'])
                    self.assertEqual(1,sum(event.code=='library.surveil' for event in s.state.events))
                else:
                    self.assertIsNone(self.resolve(s));self.assertIn(top.object_id,s.state.players['A'].zones['library'])
                    self.assertFalse(any(event.code=='library.surveil' for event in s.state.events))
                self.finish(s,private)

    def test_actual_zabu_landfall_tracks_the_tokens_current_controller_after_a_real_control_change(self):
        s=self.session(1001120);source=self.add(s.engine,'Ka-Zar of the Savage Land',zone='hand')
        land=self.add(s.engine,'Forest',zone='hand',ref='zabu-A-land');other=self.add(s.engine,'Grizzly Bears')
        control=self.add(s.engine,'Act of Treason',seat='B',zone='hand')
        later=self.add(s.engine,'Forest',seat='B',zone='hand',ref='zabu-B-land')
        for index in range(3):self.add(s.engine,'Mountain',seat='B',ref='zabu-B-mountain-'+str(index))
        private=self.add(s.engine,'Counterspell',seat='C',zone='hand',ref='literal-zabu-private-hand')
        self.assert_closed(s,source);self.assert_closed(s,control);self.prepare(s,mana={'C':4,'G':2});self.checkpoint(s)
        self.cast(s,'A',source);self.assertIsNone(self.resolve(s));token,=self.tokens(s,'A')
        self.assertEqual('Zabu',s.engine._effective_card_data(token)['name']);self.assertEqual('A',token.owner)
        self.assertIn('Legendary',s.engine._effective_card_data(token)['type_line'])
        self.assertEqual((2,2),tuple(s.engine._numeric_stat(token.object_id,stat) for stat in ('power','toughness')))
        def play_land(seat,card):
            row=next(row for row in s.packet('pilot:'+seat,full=True)['decision']['ctx']['legal']['actions']
                if row.get('action')=='play_land' and row.get('card')==card.ref)
            accepted=s.act('pilot:'+seat,{'action_id':row['id']});self.assertTrue(accepted.ok,accepted.summary)
            self.assertIsNone(self.resolve(s))
        play_land('A',land);self.assertEqual(1,s.state.cards[token.object_id].counters.get('+1/+1',0))
        self.until(s,lambda current:current.state.active_player=='B' and current.state.phase=='precombat_main'
                   and current.state.pending_decision is not None and current.state.pending_decision.kind=='priority')
        self.cast(s,'B',control,[token.ref]);self.assertIsNone(self.resolve(s))
        self.assertEqual('B',s.state.cards[token.object_id].controller);self.assertEqual('A',s.state.cards[token.object_id].owner)
        play_land('B',later);self.assertEqual(2,s.state.cards[token.object_id].counters.get('+1/+1',0))
        self.assertEqual((4,4),tuple(s.engine._numeric_stat(token.object_id,stat) for stat in ('power','toughness')))
        self.assertFalse(s.state.cards[source.object_id].counters);self.assertFalse(s.state.cards[other.object_id].counters)
        self.finish(s,private)
