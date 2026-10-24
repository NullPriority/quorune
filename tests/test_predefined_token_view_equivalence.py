from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from pathlib import Path
import json
import tempfile
import unittest
from unittest.mock import patch

from common import ROOT
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
import test_land_search_view_equivalence as controls


NAME = 'Warren Soultrader'


class PredefinedTokenViewEquivalenceTests(unittest.TestCase):
    register_fixture_cards_on_add = True
    session = controls.LandSearchViewEquivalenceTests.session
    add = controls.LandSearchViewEquivalenceTests.add
    prepare = controls.LandSearchViewEquivalenceTests.prepare
    checkpoint = controls.LandSearchViewEquivalenceTests.checkpoint
    resolve = controls.LandSearchViewEquivalenceTests.resolve
    replay = controls.LandSearchViewEquivalenceTests.replay
    private_replay = controls.LandSearchViewEquivalenceTests.private_replay
    priority = controls.LandSearchViewEquivalenceTests.priority
    actions = controls.LandSearchViewEquivalenceTests.actions
    cast = controls.LandSearchViewEquivalenceTests.cast
    reject = controls.LandSearchViewEquivalenceTests.reject
    binding = controls.LandSearchViewEquivalenceTests.binding
    activation_command = controls.LandSearchViewEquivalenceTests.activation_command
    activate = controls.LandSearchViewEquivalenceTests.activate
    resume = controls.LandSearchViewEquivalenceTests.resume
    views = controls.LandSearchViewEquivalenceTests.views

    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        path = Path(cls.temp.name)/'predefined-token-views.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',
            ROOT/'tests/fixtures/predefined-token-view-cards.json'], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition('Predefined token views', [DeckEntry('Generic Bound Commander', 1, 'commander'),
            DeckEntry('Generic Bound Plains', 30)], ['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temp.cleanup()

    def test_original_normally_binds_complete_predefined_result_and_full_price(self):
        from quorune.card_programs.activation_view_equivalence import shadowed_reviewed_activation_view_keys as shadow
        from quorune.compiler.token_templates import fixed_token_creation_effect_template
        s, source, record, current, old = self.views(NAME, 826000)
        bound = self.binding(s, source)
        self.assertTrue(bound['strict_capability_ready'], bound['blockers'])
        keys = shadow(record,current,old,capability_registry=self.registry,capability_profile='commander_review')
        self.assertEqual(1,len(keys))
        p = next(p for p in current if p.key in keys)
        self.assertEqual([fixed_token_creation_effect_template('Create a Treasure token.').effect],list(p.effects))
        self.assertEqual(1,p.cost_schema['life_payment'])
        self.assertFalse(p.cost_schema['tap_source'])
        self.assertFalse(any(p.cost_schema['mana'].values()))
        self.assertTrue(all(p.trust_level == 'trusted' and not p.requires_arbiter for p in current))
        self.assertTrue(str(p.provenance['authored_by']).startswith('oracle-ir-v'))

    def test_entire_definition_quantity_recipient_state_price_and_inputs_are_preserved(self):
        from quorune.card_programs.activation_view_equivalence import shadowed_reviewed_activation_view_keys as shadow
        _, _, record, current, old = self.views(NAME, 826010)
        def check(g=current,r=old):
            return shadow(record,g,r,capability_registry=self.registry,capability_profile='commander_review')
        keys = check()
        self.assertEqual(1,len(keys))
        key = next(iter(keys))
        p, previous = next(p for p in current if p.key == key), next(p for p in old if p.key == key)
        before = json.dumps([v.to_dict() for v in (*current,*old)],sort_keys=True)
        effect = p.effects[0]
        changed = [{**deepcopy(effect),'quantity':2}, {**deepcopy(effect),'quantity':True},
            {**deepcopy(effect),'controller':'B'}, {**deepcopy(effect),'name':'Gold'},
            {**deepcopy(effect),'tapped':True}, {**deepcopy(effect),'future':True}]
        for field, value in (('type_line','Token Artifact — Food'), ('colors',['G']), ('power','1'),
                ('display_text','Sacrifice this token: Add one mana of any color.'),
                ('activated_ability_profile','sac_any_color_mana_v1')):
            changed.append({**deepcopy(effect),'characteristics':{**deepcopy(effect['characteristics']),field:value}})
        for candidate in changed:
            self.assertNotIn(key,check(g=[replace(v,effects=[candidate]) if v.key == key else v for v in current]),candidate)
        for candidate in ({'op':'create_treasure'}, {'op':'create_treasure','controller':'B'},
                {'op':'create_treasure','controller':'$controller','quantity':2}):
            self.assertNotIn(key,check(r=[replace(previous,effects=[candidate])]))
        self.assertNotIn(key,check(r=[replace(previous,effects=[*previous.effects,{'op':'draw','player':'$controller','count':1}])]))
        price = {**deepcopy(p.cost_schema),'life_payment':0}
        self.assertNotIn(key,check(r=[replace(previous,cost_schema=price)]))
        self.assertFalse(check(r=[replace(previous,provenance={**previous.provenance,'source_rulings_hash':'0'*64})]))
        self.assertFalse(check(g=[replace(v,trust_level='provisional') for v in current]))
        self.assertFalse(check(g=[*current,*current]))
        self.assertEqual(before,json.dumps([v.to_dict() for v in (*current,*old)],sort_keys=True))

    def treasures(self,s,seat='A'):
        return [c for c in s.state.cards.values() if c.zone == 'battlefield' and c.is_token
            and c.printed_name == 'Treasure' and c.controller == seat]

    def test_real_paid_sick_source_creature_token_prices_and_all_five_treasure_colors(self):
        s = self.session(826020)
        source = self.add(s.engine,NAME,zone='hand')
        prices = [self.add(s.engine,'Fugitive Wizard',ref='warren-price-'+str(i)) for i in range(4)]
        foreign = self.add(s.engine,'Fugitive Wizard',seat='B',ref='warren-foreign-price')
        artifact = self.add(s.engine,'Sol Ring')
        alarm = self.add(s.engine,'Raise the Alarm',zone='hand')
        private = self.add(s.engine,'Generic Bound Growth',seat='C',zone='hand')
        self.prepare(s,mana={'C':3,'B':1,'W':1})
        self.checkpoint(s)
        self.cast(s,source,payment={'C':2,'B':1}); self.assertIsNone(self.resolve(s))
        self.cast(s,alarm,payment={'C':1,'W':1}); self.assertIsNone(self.resolve(s))
        soldiers = [c for c in s.state.cards.values() if c.zone == 'battlefield' and c.is_token and c.controller == 'A']
        self.assertEqual(2,len(soldiers))
        for bad in ([],[source],[foreign],[artifact],[prices[0],prices[0]]):
            self.reject(s,self.activation_command(s,source,costs=bad))
        for i,(price,color) in enumerate(zip([*prices,soldiers[0]],'WUBRG')):
            self.activate(s,source,costs=[price])
            self.assertEqual(39-i,s.state.players['A'].life)
            self.assertNotEqual('battlefield',s.state.cards[price.object_id].zone)
            self.assertFalse(s.state.cards[source.object_id].tapped)
            self.assertIsNone(self.resolve(s))
            tokens = self.treasures(s)
            self.assertEqual(1,len(tokens))
            token = tokens[0]
            data = s.engine._effective_card_data(token)
            self.assertEqual([],list(data['colors']))
            self.assertIn('Treasure',data['type_line'])
            self.assertIn('Artifact',data['type_line'])
            self.assertNotIn('Creature',data['type_line'])
            self.assertFalse(token.tapped)
            ability = next(a for a in s.engine._activated_abilities(token) if a.mana_ability)
            self.assertTrue(ability.tap_source)
            self.assertTrue(ability.sacrifice_source)
            command = self.activation_command(s,token)
            self.reject(s,{**command,'mana_choice':'C'})
            command['mana_choice'] = color
            self.reject(s,command,seat='D')
            before = len(s.state.stack)
            result = s.act('pilot:A',command)
            self.assertTrue(result.ok,result.summary)
            self.assertEqual(before,len(s.state.stack))
            self.assertEqual(1,s.state.players['A'].mana_pool[color])
            self.assertNotEqual('battlefield',s.state.cards[token.object_id].zone)
            self.reject(s,command)
        self.assertEqual({color:1 for color in 'WUBRG'}, {color:s.state.players['A'].mana_pool[color] for color in 'WUBRG'})
        self.assertEqual(35,s.state.players['A'].life)
        self.assertEqual('battlefield',s.state.cards[soldiers[1].object_id].zone)
        self.private_replay(s,private)

    def test_real_counter_keeps_prices_paid_and_source_departure_keeps_pending_result(self):
        s = self.session(826030)
        source = self.add(s.engine,NAME,zone='hand')
        prices = [self.add(s.engine,'Fugitive Wizard',ref='countered-warren-price-'+str(i)) for i in range(2)]
        stifle = self.add(s.engine,'Stifle',seat='B',zone='hand')
        bounce = self.add(s.engine,'Unsummon',seat='B',zone='hand')
        private = self.add(s.engine,'Generic Bound Growth',seat='C',zone='hand')
        s.state.players['B'].mana_pool['U'] = 2
        self.prepare(s,mana={'C':2,'B':1})
        self.checkpoint(s)
        self.cast(s,source,payment={'C':2,'B':1}); self.assertIsNone(self.resolve(s))
        self.activate(s,source,costs=[prices[0]])
        self.assertEqual(39,s.state.players['A'].life)
        self.cast(s,stifle,[s.state.stack[-1].ref],seat='B',payment={'U':1})
        self.assertIsNone(self.resolve(s))
        self.assertEqual([],self.treasures(s))
        self.assertEqual('graveyard',s.state.cards[prices[0].object_id].zone)
        self.activate(s,source,costs=[prices[1]])
        self.cast(s,bounce,[source.ref],seat='B',payment={'U':1})
        s = self.resume(s)
        self.assertIsNone(self.resolve(s))
        self.assertEqual('hand',s.state.cards[source.object_id].zone)
        self.assertEqual(38,s.state.players['A'].life)
        self.assertTrue(all(s.state.cards[c.object_id].zone == 'graveyard' for c in prices))
        self.assertEqual(1,len(self.treasures(s)))
        self.private_replay(s,private)

    def test_real_control_change_uses_activator_life_price_and_locked_token_recipient(self):
        s = self.session(826040)
        source = self.add(s.engine,NAME)
        price = self.add(s.engine,'Fugitive Wizard',seat='B',ref='stolen-warren-price')
        foreign = self.add(s.engine,'Fugitive Wizard',ref='stolen-warren-foreign-price')
        steal = self.add(s.engine,'Control Magic',seat='B',zone='hand')
        bounce = self.add(s.engine,'Unsummon',seat='B',zone='hand')
        private = self.add(s.engine,'Generic Bound Growth',seat='C',zone='hand')
        self.prepare(s,actor='B',active='B',mana={'C':2,'U':3})
        self.checkpoint(s)
        self.cast(s,steal,[source.ref],seat='B',payment={'C':2,'U':2}); self.assertIsNone(self.resolve(s))
        self.assertEqual(('A','B'),(s.state.cards[source.object_id].owner,s.state.cards[source.object_id].controller))
        self.reject(s,self.activation_command(s,source,costs=[foreign],seat='B'),seat='B')
        self.activate(s,source,costs=[price],seat='B')
        self.assertEqual((40,39),(s.state.players['A'].life,s.state.players['B'].life))
        self.cast(s,bounce,[source.ref],seat='B',payment={'U':1})
        s = self.resume(s)
        self.assertIsNone(self.resolve(s))
        self.assertEqual(('A','A','hand'),(s.state.cards[source.object_id].owner,s.state.cards[source.object_id].controller,s.state.cards[source.object_id].zone))
        self.assertEqual([],self.treasures(s,'A'))
        self.assertEqual(1,len(self.treasures(s,'B')))
        self.assertEqual('B',self.treasures(s,'B')[0].owner)
        self.private_replay(s,private)

    def test_fresh_omitted_or_widened_predefined_result_equivalence_is_detected(self):
        method = 'test_original_normally_binds_complete_predefined_result_and_full_price'
        with patch('quorune.card_programs.activation_view_equivalence._predefined_token_results_equal',return_value=False):
            with self.assertRaises(AssertionError):
                getattr(type(self)(method),method)()
        method = 'test_entire_definition_quantity_recipient_state_price_and_inputs_are_preserved'
        with patch('quorune.card_programs.activation_view_equivalence._predefined_token_results_equal',return_value=True):
            with self.assertRaises(AssertionError):
                getattr(type(self)(method),method)()


if __name__ == '__main__':
    unittest.main()
