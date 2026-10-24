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
import test_ability_label_composition as journeys


NAMES = ('Sylvan Safekeeper', 'Scryb Ranger')


class ActivationTargetViewEquivalenceTests(unittest.TestCase):
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
    advance = journeys.AbilityLabelCompositionTests.advance

    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        path = Path(cls.temp.name)/'activation-target-views.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',
            ROOT/'tests/fixtures/activation-target-view-cards.json'], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition('Activation target views', [DeckEntry('Generic Bound Commander', 1, 'commander'),
            DeckEntry('Generic Bound Plains', 30)], ['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temp.cleanup()

    def keywords(self,s,card):
        return s.engine._combat_keywords(s.state.cards[card.object_id])

    def test_originals_normally_bind_complete_prices_and_exact_single_creature_targets(self):
        from quorune.card_programs.activation_view_equivalence import shadowed_reviewed_activation_view_keys as shadow
        from quorune.card_programs.activation_target_equivalence import reviewed_activation_with_equivalent_target as compare
        from quorune.card_programs.current_view_equivalence import _catalog_preserves_cost
        for i,name in enumerate(NAMES):
            s,source,record,current,old = self.views(name,827000+i)
            bound = self.binding(s,source)
            self.assertTrue(bound['strict_capability_ready'],bound['blockers'])
            keys = shadow(record,current,old,capability_registry=self.registry,capability_profile='commander_review')
            self.assertEqual(1,len(keys))
            key = next(iter(keys))
            p,previous = next(p for p in current if p.key == key), next(p for p in old if p.key == key)
            self.assertFalse(_catalog_preserves_cost(p,previous))
            comparison = compare(p,previous)
            self.assertIsNotNone(comparison)
            self.assertTrue(_catalog_preserves_cost(p,comparison))
            self.assertNotEqual(previous.target_schema,p.target_schema)
            self.assertEqual(p.target_schema,comparison.target_schema)
            self.assertTrue(all(v.trust_level == 'trusted' and not v.requires_arbiter for v in current))
            self.assertFalse(p.cost_schema['tap_source'])
            self.assertFalse(any(p.cost_schema['mana'].values()))
            self.assertEqual('once_per_turn' if name == NAMES[1] else None,p.cost_schema.get('activation_limit'))

    def test_target_aliases_preserve_all_price_limit_domain_and_input_fields(self):
        from quorune.card_programs.activation_view_equivalence import shadowed_reviewed_activation_view_keys as shadow
        for i,name in enumerate(NAMES):
            _,_,record,current,old = self.views(name,827010+i)
            def check(g=current,r=old):
                return shadow(record,g,r,capability_registry=self.registry,capability_profile='commander_review')
            keys = check()
            self.assertEqual(1,len(keys))
            key = next(iter(keys))
            previous = next(p for p in old if p.key == key)
            before = json.dumps([v.to_dict() for v in (*current,*old)],sort_keys=True)
            base = previous.target_schema
            variants = [{**deepcopy(base),'creature':False}, {**deepcopy(base),'count':0},
                {**deepcopy(base),'count':2}, {**deepcopy(base),'count':True},
                {**deepcopy(base),'zones':['graveyard']}, {**deepcopy(base),'categories':['card']},
                {**deepcopy(base),'controller':'opponent'}, {**deepcopy(base),'tapped':True},
                {**deepcopy(base),'min':0}, {**deepcopy(base),'future':True},
                {**deepcopy(base),'types_any':['creature']}, {'groups':[deepcopy(base)]}]
            for target in variants:
                handlers = deepcopy(previous.handlers)
                handlers[0]['ability']['target_schema'] = deepcopy(target)
                self.assertNotIn(key,check(r=[replace(previous,target_schema=target,handlers=handlers)]),target)
            # Top-level and embedded target domains cannot disagree.
            self.assertNotIn(key,check(r=[replace(previous,target_schema={**deepcopy(base),'controller':'opponent'})]))
            for field,value in (('life_payment',1),('tap_source',True),('activation_limit','once_per_turn' if name == NAMES[0] else None)):
                handlers = deepcopy(previous.handlers)
                handlers[0]['ability'][field] = value
                self.assertNotIn(key,check(r=[replace(previous,handlers=handlers)]),field)
            self.assertNotIn(key,check(r=[replace(previous,effects=[{'op':'draw','player':'$controller','count':1}])]))
            self.assertFalse(check(r=[replace(previous,provenance={**previous.provenance,'source_oracle_hash':'0'*64})]))
            self.assertFalse(check(g=[replace(v,trust_level='provisional') for v in current]))
            self.assertFalse(check(g=[*current,*current]))
            self.assertEqual(before,json.dumps([v.to_dict() for v in (*current,*old)],sort_keys=True))

    def test_real_safekeeper_land_prices_self_target_shroud_and_cleanup_expiry(self):
        s = self.session(827020)
        source = self.add(s.engine,NAMES[0],zone='hand')
        body = self.add(s.engine,'Fugitive Wizard',ref='safekeeper-own-target')
        foreign = self.add(s.engine,'Fugitive Wizard',seat='B',ref='safekeeper-opponent-target')
        artifact = self.add(s.engine,'Sol Ring')
        lands = [self.add(s.engine,name,ref='safekeeper-price-'+str(i)) for i,name in enumerate(('Forest','Island','Forest'))]
        lands[0].tapped = True
        wrong_land = self.add(s.engine,'Forest',seat='B',ref='safekeeper-opponent-price')
        own_spell = self.add(s.engine,'Expedite',zone='hand')
        foreign_spell = self.add(s.engine,'Unsummon',seat='B',zone='hand')
        private = self.add(s.engine,'Generic Bound Growth',seat='C',zone='hand')
        s.state.players['B'].mana_pool['U'] = 1
        self.prepare(s,mana={'G':1,'R':1})
        self.checkpoint(s)
        self.cast(s,source,payment={'G':1}); self.assertIsNone(self.resolve(s))
        for target in (foreign,artifact):
            self.reject(s,self.activation_command(s,source,costs=[lands[0]],targets=[target.ref]))
        for price in (wrong_land,artifact):
            self.reject(s,self.activation_command(s,source,costs=[price],targets=[body.ref]))
        self.activate(s,source,costs=[lands[0]],targets=[body.ref])
        self.assertEqual('graveyard',s.state.cards[lands[0].object_id].zone)
        self.assertFalse(s.state.cards[source.object_id].tapped)
        s = self.resume(s)
        self.assertIsNone(self.resolve(s))
        self.assertIn('shroud',self.keywords(s,body))
        self.reject(s,self.activation_command(s,source,costs=[lands[1]],targets=[body.ref]))
        self.reject(s,{'action_id':'cast:'+own_spell.ref,'pay':'auto','targets':[body.ref]})
        self.priority(s,'B')
        self.reject(s,{'action_id':'cast:'+foreign_spell.ref,'pay':'auto','targets':[body.ref]},seat='B')
        self.activate(s,source,costs=[lands[1]],targets=[source.ref])
        self.assertIsNone(self.resolve(s))
        self.assertIn('shroud',self.keywords(s,source))
        self.advance(s,lambda x:x.state.active_player == 'B' and x.state.phase == 'precombat_main' and x.state.priority_player == 'B')
        self.assertNotIn('shroud',self.keywords(s,source))
        self.assertNotIn('shroud',self.keywords(s,body))
        self.activate(s,source,costs=[lands[2]],targets=[body.ref])
        self.assertIsNone(self.resolve(s))
        self.assertIn('shroud',self.keywords(s,body))
        self.private_replay(s,private)

    def test_real_safekeeper_target_control_revalidation_and_source_departure(self):
        s = self.session(827030)
        source = self.add(s.engine,NAMES[0],zone='hand')
        bodies = [self.add(s.engine,'Fugitive Wizard',ref='safekeeper-drift-target-'+str(i)) for i in range(2)]
        prices = [self.add(s.engine,'Forest',ref='safekeeper-drift-price-'+str(i)) for i in range(2)]
        steal = self.add(s.engine,'Control Magic',seat='B',zone='hand')
        self.add(s.engine,'Vedalken Orrery',seat='B')
        bounce = self.add(s.engine,'Unsummon',seat='B',zone='hand')
        private = self.add(s.engine,'Generic Bound Growth',seat='C',zone='hand')
        s.state.players['B'].mana_pool.update({'C':2,'U':3})
        self.prepare(s,mana={'G':1})
        self.checkpoint(s)
        self.cast(s,source,payment={'G':1}); self.assertIsNone(self.resolve(s))
        self.activate(s,source,costs=[prices[0]],targets=[bodies[0].ref])
        self.cast(s,steal,[bodies[0].ref],seat='B',payment={'C':2,'U':2})
        s = self.resume(s)
        self.assertIsNone(self.resolve(s))
        self.assertEqual('B',s.state.cards[bodies[0].object_id].controller)
        self.assertNotIn('shroud',self.keywords(s,bodies[0]))
        self.assertEqual('graveyard',s.state.cards[prices[0].object_id].zone)
        self.activate(s,source,costs=[prices[1]],targets=[bodies[1].ref])
        self.cast(s,bounce,[source.ref],seat='B',payment={'U':1})
        s = self.resume(s)
        self.assertIsNone(self.resolve(s))
        self.assertEqual('hand',s.state.cards[source.object_id].zone)
        self.assertIn('shroud',self.keywords(s,bodies[1]))
        self.assertEqual('graveyard',s.state.cards[prices[1].object_id].zone)
        self.private_replay(s,private)

    def test_real_ranger_flash_forest_owner_return_protection_and_limit_across_control(self):
        s = self.session(827040)
        source = self.add(s.engine,NAMES[1],zone='hand')
        land = self.add(s.engine,'Taiga',seat='B',ref='ranger-foreign-owned-forest')
        s.state.players['B'].zones['battlefield'].remove(land.object_id)
        s.state.players['A'].zones['battlefield'].append(land.object_id)
        land.controller = 'A'
        reserve = self.add(s.engine,'Forest',ref='ranger-reserve-forest')
        mana_land = self.add(s.engine,'Forest',ref='ranger-green-mana')
        island = self.add(s.engine,'Island',ref='ranger-blue-mana')
        foreign = self.add(s.engine,'Forest',seat='B',ref='ranger-opponent-forest')
        elf = self.add(s.engine,'Llanowar Elves',seat='B')
        b_mana = [self.add(s.engine,name,seat='B',ref='ranger-opponent-mana-'+str(i)) for i,name in enumerate(('Mountain','Mountain','Island'))]
        own_target = self.add(s.engine,'Fugitive Wizard',ref='ranger-next-turn-target')
        own_target.tapped = True
        steal = self.add(s.engine,'Act of Treason',seat='B',zone='hand')
        blue = self.add(s.engine,'Unsummon',seat='B',zone='hand')
        private = self.add(s.engine,'Generic Bound Growth',seat='C',zone='hand')
        self.prepare(s)
        self.checkpoint(s)
        self.advance(s,lambda x:x.state.active_player == 'B' and x.state.phase == 'precombat_main' and x.state.priority_player == 'B')
        for permanent in [elf,*b_mana]:
            self.activate(s,permanent,seat='B')
        self.assertTrue(s.state.cards[elf.object_id].tapped)
        self.activate(s,island)
        self.activate(s,mana_land)
        self.cast(s,source,payment={'U':1,'G':1})
        self.assertIsNone(self.resolve(s))
        self.assertEqual('B',s.state.active_player)
        self.assertIn('flying',self.keywords(s,source))
        self.priority(s,'B')
        self.reject(s,{'action_id':'cast:'+blue.ref,'pay':'auto','targets':[source.ref]},seat='B')
        for price in (island,foreign):
            self.reject(s,self.activation_command(s,source,costs=[price],targets=[elf.ref]))
        command = self.activation_command(s,source,costs=[land],targets=[elf.ref])
        self.activate(s,source,costs=[land],targets=[elf.ref])
        self.assertEqual(('B','hand'),(s.state.cards[land.object_id].owner,s.state.cards[land.object_id].zone))
        self.assertIn(land.object_id,s.state.players['B'].zones['hand'])
        s = self.resume(s)
        self.assertIsNone(self.resolve(s))
        self.assertFalse(s.state.cards[elf.object_id].tapped)
        self.cast(s,steal,[source.ref],seat='B',payment={'R':2,'G':1})
        self.assertIsNone(self.resolve(s))
        self.assertEqual(('A','B'),(s.state.cards[source.object_id].owner,s.state.cards[source.object_id].controller))
        self.priority(s,'B')
        self.assertFalse(any(r['id'] == command['action_id'] for r in self.actions(s,'B')))
        self.reject(s,{**command,'cost_cards':[foreign.ref]},seat='B')
        self.advance(s,lambda x:x.state.active_player == 'C' and x.state.phase == 'precombat_main' and x.state.priority_player == 'C')
        self.assertEqual('A',s.state.cards[source.object_id].controller)
        self.assertTrue(s.state.cards[own_target.object_id].tapped)
        self.activate(s,source,costs=[reserve],targets=[own_target.ref])
        self.assertIsNone(self.resolve(s))
        self.assertEqual('hand',s.state.cards[reserve.object_id].zone)
        self.assertFalse(s.state.cards[own_target.object_id].tapped)
        self.private_replay(s,private)

    def test_fresh_missing_or_widened_target_comparison_is_detected(self):
        method = 'test_originals_normally_bind_complete_prices_and_exact_single_creature_targets'
        with patch('quorune.card_programs.activation_view_equivalence.reviewed_activation_with_equivalent_target',side_effect=lambda current,old:old):
            with self.assertRaises(AssertionError):
                getattr(type(self)(method),method)()
        def unchecked(current,old):
            handlers = deepcopy(old.handlers)
            if handlers:
                handlers[0]['ability']['target_schema'] = deepcopy(current.target_schema)
            return replace(old,target_schema=deepcopy(current.target_schema),handlers=handlers)
        method = 'test_target_aliases_preserve_all_price_limit_domain_and_input_fields'
        with patch('quorune.card_programs.activation_view_equivalence.reviewed_activation_with_equivalent_target',side_effect=unchecked):
            with self.assertRaises(AssertionError):
                getattr(type(self)(method),method)()


if __name__ == '__main__':
    unittest.main()
