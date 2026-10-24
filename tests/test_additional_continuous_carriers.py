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
import test_banding as bands


NAMES = ("Magistrate's Veto", 'Stormsurge Kraken', 'Skyhunter Strike Force',
    'Fortified Area', 'Dire Wolves', 'Cooperation')


class AdditionalContinuousCarrierTests(unittest.TestCase):
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
    binding = controls.LandSearchViewEquivalenceTests.binding
    activation_command = controls.LandSearchViewEquivalenceTests.activation_command
    activate = controls.LandSearchViewEquivalenceTests.activate
    resume = controls.LandSearchViewEquivalenceTests.resume
    advance = bands.BandingRuntimeTests.advance
    attack = bands.BandingRuntimeTests.attack
    blockers = bands.BandingRuntimeTests.blockers
    block = bands.BandingRuntimeTests.block
    damage = bands.BandingRuntimeTests.damage
    assign = bands.BandingRuntimeTests.assign
    finish = bands.BandingRuntimeTests.finish

    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        path = Path(cls.temp.name)/'additional-continuous-carriers.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',
            ROOT/'tests/fixtures/additional-continuous-carriers-cards.json'],path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition('Additional continuous carriers', [DeckEntry('Generic Bound Commander',1,'commander'),
            DeckEntry('Generic Bound Plains',30)], ['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temp.cleanup()

    def reject(self,s,command,seat='A'):
        return controls.LandSearchViewEquivalenceTests.reject(self,s,command,seat=seat)

    def keywords(self,s,card):
        return s.engine._combat_keywords(s.state.cards[card.object_id])

    def stat(self,s,card,kind='power'):
        return s.engine._numeric_stat(card.object_id,kind)

    def settle(self,s):
        for _ in range(100):
            decision = s.state.pending_decision
            if decision is None:
                s.engine.pump()
                continue
            principal = s.pending_principals()[0]
            if decision.kind == 'priority':
                if not s.state.stack:
                    return None
                command = {'action_id':'pass'}
            elif decision.kind == 'trigger.order':
                rows = s.packet(principal,full=True)['decision']['ctx']['triggers']
                command = {'action_id':'order','triggers':[r['id'] for r in rows]}
            elif decision.kind == 'replacement.order':
                rows = s.packet(principal,full=True)['decision']['ctx']['options']
                choices = [r for r in rows if r['id'].startswith('rule.commander-zone-replacement:')]
                self.assertEqual(1,len(choices),rows)
                command = {'action_id':'choose','replacement':choices[0]['id']}
            else:
                return decision
            result = s.act(principal,command)
            self.assertTrue(result.ok,result.summary)
        self.fail('Additional carrier stack did not settle')

    def test_all_six_normally_bind_complete_current_trusted_programs(self):
        for i,name in enumerate(NAMES):
            s = self.session(828000+i)
            source = self.add(s.engine,name,zone='hand')
            binding = self.binding(s,source)
            self.assertTrue(binding['strict_capability_ready'],binding['blockers'])
            programs = s.engine.semantics.programs_for_oracle(source.oracle_id)
            self.assertTrue(programs)
            self.assertTrue(all(p.trust_level == 'trusted' and not p.requires_arbiter for p in programs))
            self.assertTrue(all(str(p.provenance.get('authored_by','')).startswith('oracle-ir-v') for p in programs))

    def test_critical_runtime_dependencies_cannot_be_removed_from_carrier_programs(self):
        from quorune.card_programs.binding import bind_semantic_program_runtime
        required = ('combat.declaration.typed_components','state_query.commander.designation','combat.trigger.melee',
            'combat.banding','combat.banding','combat.banding')
        for i,(name,capability) in enumerate(zip(NAMES,required)):
            s = self.session(828010+i)
            source = self.add(s.engine,name)
            p = next(p for p in s.engine.semantics.programs_for_oracle(source.oracle_id) if capability in p.capability_dependencies)
            deps = tuple(c for c in p.capability_dependencies if c != capability)
            missing = replace(p,capability_dependencies=deps,
                capability_closure=self.registry.closure(deps,profile='commander_review').to_dict() if deps else None)
            binding = bind_semantic_program_runtime(missing,capability_registry=self.registry,profile='commander_review')
            self.assertFalse(binding['strict'],name)
            self.assertIn('capability:undeclared_runtime_dependency:'+capability,binding['blockers'])

    def test_nested_declaration_minima_decode_all_three_typed_forms_and_reject_bad_payloads(self):
        from quorune.ability_fragments import ability_fragment_to_dict
        from quorune.declaration_fragments import (DeclarationCostTemplate,DeclarationRequirementTemplate,
            DeclarationRestrictionTemplate,DECLARATION_COMPONENT_CAPABILITY_ID)
        from quorune.rules.keyword_runtime_requirements import _fragment_descriptor_requirements
        fragments = (
            DeclarationCostTemplate('carrier-cost-boundary',('attack',),'self',(('GENERIC',1),),'{1}'),
            DeclarationRequirementTemplate('intrinsic-attack-each-combat-if-able-v1','attack','attack_each_combat'),
            DeclarationRestrictionTemplate('carrier-restriction-boundary',('block',),'self'),
        )
        for fragment in fragments:
            value = ability_fragment_to_dict(fragment)
            self.assertEqual({DECLARATION_COMPONENT_CAPABILITY_ID},_fragment_descriptor_requirements({'nested':[value]}))
            wrong = deepcopy(value)
            wrong['value']['template_id'] = ''
            with self.assertRaises(ValueError):
                _fragment_descriptor_requirements({'nested':[wrong]})

    def test_fresh_nested_fragment_dependency_omission_is_detected(self):
        method = 'test_critical_runtime_dependencies_cannot_be_removed_from_carrier_programs'
        with patch('quorune.rules.keyword_runtime_requirements._fragment_descriptor_requirements',return_value=set()):
            with self.assertRaises(AssertionError):
                getattr(type(self)(method),method)()

    def test_real_veto_cast_rejects_white_and_blue_blockers_but_allows_colorless(self):
        s = self.session(828020)
        source = self.add(s.engine,NAMES[0],zone='hand')
        attacker = self.add(s.engine,'Grizzly Bears',ref='veto-attacker')
        attacker.acquired_control_turn_count = -1
        white = self.add(s.engine,'Silvercoat Lion',seat='B')
        blue = self.add(s.engine,'Fugitive Wizard',seat='B')
        green = self.add(s.engine,'Grizzly Bears',seat='B',ref='veto-green-blocker')
        colorless = self.add(s.engine,'Ornithopter',seat='B')
        private = self.add(s.engine,'Generic Bound Growth',seat='C',zone='hand')
        self.prepare(s,mana={'C':2,'R':1})
        self.checkpoint(s)
        self.cast(s,source,payment={'C':2,'R':1})
        self.assertIsNone(self.resolve(s))
        self.attack(s,{attacker.ref:'B'})
        self.blockers(s)
        self.block(s,{white.ref:attacker.ref},valid=False)
        self.block(s,{blue.ref:attacker.ref},valid=False)
        self.block(s,{green.ref:attacker.ref,colorless.ref:attacker.ref})
        actor,_ = self.damage(s)
        self.assertEqual('pilot:A',actor)
        self.assign(s,'A',[(attacker,colorless,2)])
        self.finish(s)
        self.assertEqual('graveyard',s.state.cards[colorless.object_id].zone)
        self.assertEqual('graveyard',s.state.cards[attacker.object_id].zone)
        self.assertEqual('battlefield',s.state.cards[green.object_id].zone)
        self.assertEqual(40,s.state.players['B'].life)
        self.private_replay(s,private)

    def band_setup(self,s,*,paid):
        area = self.add(s.engine,'Fortified Area',zone='hand' if paid else 'battlefield')
        wolf = self.add(s.engine,'Dire Wolves',zone='hand' if paid else 'battlefield')
        aura = self.add(s.engine,'Cooperation',zone='hand')
        bear = self.add(s.engine,'Grizzly Bears')
        other = self.add(s.engine,'Walking Corpse')
        wall = self.add(s.engine,'Wall of Wood',ref='carrier-own-wall')
        foreign_wall = self.add(s.engine,'Wall of Wood',seat='B',ref='carrier-foreign-wall')
        blocker = self.add(s.engine,'Hill Giant',seat='B')
        plains = self.add(s.engine,'Plains',zone='hand' if paid else 'battlefield')
        private = self.add(s.engine,'Generic Bound Growth',seat='C',zone='hand')
        for c in (bear,other):
            c.acquired_control_turn_count = -1
        if not paid:
            wolf.acquired_control_turn_count = -1
        return area,wolf,aura,bear,other,wall,foreign_wall,blocker,plains,private

    def test_real_three_banding_carriers_cast_and_assign_blocker_damage(self):
        s = self.session(828030)
        area,wolf,aura,bear,other,wall,foreign_wall,blocker,plains,private = self.band_setup(s,paid=True)
        haste = self.add(s.engine,'Expedite',zone='hand')
        self.prepare(s,mana={'C':5,'G':1,'W':3,'R':1})
        self.checkpoint(s)
        self.cast(s,wolf,payment={'C':2,'G':1}); self.assertIsNone(self.resolve(s))
        self.assertNotIn('banding',self.keywords(s,wolf))
        command = {'action_id':'play-land:'+plains.ref}
        self.reject(s,command,'D')
        result = s.act('pilot:A',command); self.assertTrue(result.ok,result.summary)
        self.assertIn('banding',self.keywords(s,wolf))
        self.cast(s,area,payment={'C':1,'W':2}); self.assertIsNone(self.resolve(s))
        self.assertEqual((1,3),(self.stat(s,wall),self.stat(s,wall,'toughness')))
        self.assertIn('banding',self.keywords(s,wall))
        self.assertNotIn('banding',self.keywords(s,foreign_wall))
        self.assertNotIn('banding',self.keywords(s,bear))
        self.cast(s,aura,[bear.ref],payment={'C':2,'W':1}); self.assertIsNone(self.resolve(s))
        self.assertIn('banding',self.keywords(s,bear))
        self.assertNotIn('banding',self.keywords(s,other))
        self.cast(s,haste,[wolf.ref],payment={'R':1}); self.assertIsNone(self.resolve(s))
        self.attack(s,{c.ref:'B' for c in (wolf,bear,other)},[[wolf.ref,bear.ref,other.ref]])
        self.blockers(s)
        self.block(s,{blocker.ref:other.ref})
        for c in (wolf,bear,other):
            self.assertEqual([blocker.object_id],s.state.combat.blockers[c.object_id])
        actor,_ = self.damage(s)
        self.assertEqual('pilot:A',actor)
        s = self.resume(s)
        self.assign(s,'A',[(wolf,blocker,2),(bear,blocker,2),(other,blocker,2),
            (blocker,wolf,1),(blocker,bear,1),(blocker,other,1)])
        self.finish(s)
        self.assertEqual('graveyard',s.state.cards[blocker.object_id].zone)
        self.assertTrue(all(s.state.cards[c.object_id].zone == 'battlefield' for c in (wolf,bear,other)))
        self.assertEqual(40,s.state.players['B'].life)
        self.private_replay(s,private)

    def test_real_banding_source_and_plains_departure_revoke_grants_without_disbanding(self):
        s = self.session(828040)
        area,wolf,aura,bear,other,wall,foreign_wall,blocker,plains,private = self.band_setup(s,paid=False)
        bounces = [self.add(s.engine,'Boomerang',zone='hand',ref='carrier-bounce-'+str(i)) for i in range(3)]
        lands = [self.add(s.engine,'Island',ref='carrier-blue-land-'+str(i)) for i in range(6)]
        self.prepare(s,mana={'C':2,'W':1})
        self.checkpoint(s)
        self.cast(s,aura,[bear.ref],payment={'C':2,'W':1}); self.assertIsNone(self.resolve(s))
        for c in lands[:2]:self.activate(s,c)
        self.cast(s,bounces[0],[area.ref],payment={'U':2}); self.assertIsNone(self.resolve(s))
        self.assertEqual(0,self.stat(s,wall))
        self.assertNotIn('banding',self.keywords(s,wall))
        self.attack(s,{c.ref:'B' for c in (wolf,bear,other)},[[wolf.ref,bear.ref,other.ref]])
        for index,target in enumerate((aura,plains),1):
            for c in lands[index*2:index*2+2]:self.activate(s,c)
            self.cast(s,bounces[index],[target.ref],payment={'U':2}); self.assertIsNone(self.resolve(s))
        self.assertEqual(1,len(s.state.combat.attack_bands))
        self.assertNotIn('banding',self.keywords(s,wolf))
        self.assertNotIn('banding',self.keywords(s,bear))
        self.blockers(s)
        self.block(s,{blocker.ref:other.ref})
        self.assertTrue(all(s.state.combat.blockers[c.object_id] == [blocker.object_id] for c in (wolf,bear,other)))
        actor,_ = self.damage(s)
        self.assertEqual('pilot:B',actor)
        self.assign(s,'B',[(blocker,other,3)])
        self.finish(s)
        self.assertEqual('graveyard',s.state.cards[other.object_id].zone)
        self.assertEqual('battlefield',s.state.cards[wolf.object_id].zone)
        self.private_replay(s,private)

    def test_real_commander_conditional_melee_and_independent_private_blocked_draw(self):
        s = self.session(828050)
        kraken = self.add(s.engine,'Stormsurge Kraken',zone='hand')
        sky = self.add(s.engine,'Skyhunter Strike Force',zone='hand')
        body = self.add(s.engine,'Grizzly Bears')
        body.acquired_control_turn_count = -1
        blocker = self.add(s.engine,'Hill Giant',seat='B')
        haste = [self.add(s.engine,'Expedite',zone='hand',ref='carrier-haste-'+str(i)) for i in range(2)]
        bounce = self.add(s.engine,'Unsummon',zone='hand',ref='carrier-commander-bounce')
        hostile = self.add(s.engine,'Unsummon',seat='B',zone='hand',ref='carrier-hostile-bounce')
        own_island = self.add(s.engine,'Island',ref='carrier-draw-blue-land')
        foreign_island = self.add(s.engine,'Island',seat='B',ref='carrier-hostile-blue-land')
        self.add(s.engine,'Generic Bound Commander',seat='D',zone='hand',ref='carrier-commander-registration')
        commander = next(s.state.cards[oid] for oid in s.state.players['A'].zones['command'])
        private = self.add(s.engine,'Generic Bound Growth',seat='C',zone='hand')
        self.prepare(s,mana={'C':5,'U':2,'W':2,'R':2})
        self.checkpoint(s)
        self.cast(s,kraken,payment={'C':3,'U':2}); self.assertIsNone(self.settle(s))
        self.cast(s,sky,payment={'C':2,'W':1}); self.assertIsNone(self.settle(s))
        self.assertEqual(5,self.stat(s,kraken))
        self.assertNotIn('melee',self.keywords(s,body))
        self.cast(s,commander,payment={'W':1}); self.assertIsNone(self.settle(s))
        self.assertEqual(7,self.stat(s,kraken))
        self.assertIn('melee',self.keywords(s,body))
        for c,h in zip((kraken,sky),haste):
            self.cast(s,h,[c.ref],payment={'R':1}); self.assertIsNone(self.settle(s))
        self.attack(s,{kraken.ref:'B',sky.ref:'C',body.ref:'C'})
        self.assertIsNone(self.settle(s))
        self.assertEqual((9,4,4),(self.stat(s,kraken),self.stat(s,sky),self.stat(s,body)))
        self.blockers(s)
        self.block(s,{blocker.ref:sky.ref},valid=False)
        self.block(s,{blocker.ref:kraken.ref})
        while s.state.pending_decision is not None and s.state.pending_decision.kind == 'combat.blockers':
            result = s.act(s.pending_principals()[0],{'action_id':'block','blocks':{}})
            self.assertTrue(result.ok,result.summary)
        self.assertTrue(s.state.stack)
        self.activate(s,foreign_island,seat='B')
        self.reject(s,{'action_id':'cast:'+hostile.ref,'targets':[kraken.ref],'pay':'auto'},'B')
        self.activate(s,own_island)
        self.cast(s,bounce,[commander.ref],payment={'U':1})
        decision = self.settle(s)
        self.assertIsNotNone(decision)
        self.assertEqual('semantic.choice',decision.kind)
        self.assertEqual('command',s.state.cards[commander.object_id].zone)
        self.assertEqual(7,self.stat(s,kraken))
        self.assertNotIn('melee',self.keywords(s,body))
        s = self.resume(s)
        self.assertEqual(['pilot:A'],s.pending_principals())
        packet = s.packet('pilot:A',full=True)
        values = packet['decision']['legal_actions'][0]['choice_schema']['legal_values']
        self.assertIn('decline',values)
        choice = 'draw' if 'draw' in values else 'apply'
        self.assertIn(choice,values)
        before = set(s.state.players['A'].zones['hand'])
        command = {'action_id':'choose','choice':choice}
        self.reject(s,command,'D')
        result = s.act('pilot:A',command); self.assertTrue(result.ok,result.summary)
        self.assertIsNone(self.settle(s))
        drawn = set(s.state.players['A'].zones['hand'])-before
        self.assertEqual(2,len(drawn))
        for oid in drawn:
            ref = s.state.cards[oid].ref
            self.assertIn(ref,json.dumps(s.packet('pilot:A',full=True)))
            for seat in 'BCD':self.assertNotIn(ref,json.dumps(s.packet('pilot:'+seat,full=True)))
        self.finish(s)
        self.assertEqual(32,s.state.players['C'].life)
        self.assertEqual(40,s.state.players['B'].life)
        self.assertEqual('graveyard',s.state.cards[blocker.object_id].zone)
        self.private_replay(s,private)


if __name__ == '__main__':
    unittest.main()
