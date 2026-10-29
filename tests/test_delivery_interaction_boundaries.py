from __future__ import annotations

"""Native conditional Aura composition and complete-card rejection contrasts."""

from pathlib import Path
import tempfile
import unittest
from common import ROOT
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition,DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.rules.capabilities import load_default_capability_registry
from quorune.record import authoritative_state_hash
from scripts.build_test_database import build_fixture_database
import test_provoke_rules as controls


class DeliveryInteractionBoundaryTests(unittest.TestCase):
    register_fixture_cards_on_add=True
    session=controls.ProvokeTests.session
    add=controls.ProvokeTests.add
    prepare=controls.ProvokeTests.prepare
    checkpoint=controls.ProvokeTests.checkpoint
    resolve=controls.ProvokeTests.resolve
    replay=controls.ProvokeTests.replay
    binding=controls.ProvokeTests.binding
    private_replay=controls.ProvokeTests.private_replay
    priority=controls.ProvokeTests.priority
    actions=controls.ProvokeTests.actions

    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory();path=Path(cls.temp.name)/'interaction-boundaries.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',
            ROOT/'tests/fixtures/delivery-interaction-boundary-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Interaction boundaries',[DeckEntry('Generic Bound Commander',1,'commander'),
            DeckEntry('Generic Bound Plains',30)],['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls):cls.db.close();cls.temp.cleanup()

    def reject(self,s,command,*,principal='pilot:A'):
        before=authoritative_state_hash(s.state);result=s.act(principal,command)
        self.assertFalse(result.ok,result.summary);self.assertEqual(before,authoritative_state_hash(s.state))

    def cast(self,s,c,payment,targets=(),*,seat='A',auto=False):
        self.priority(s,seat)
        rows=s.packet('pilot:'+seat,full=True)['decision']['ctx']['legal']['actions']
        row=next((r for r in rows if r.get('card')==c.ref and r.get('kind')=='cast'),None)
        self.assertIsNotNone(row,c.printed_name)
        command={'action_id':row['id'],'targets':list(targets),'pay':'auto' if auto else 'manual'}
        if not auto:command['payment']=payment
        self.reject(s,command,principal='pilot:D')
        result=s.act('pilot:'+seat,command);self.assertTrue(result.ok,result.summary)

    def test_actual_natures_embrace_creature_target_and_typed_restriction(self):
        s=self.session(764100);aura=self.add(s.engine,"Nature's Embrace",zone='hand')
        recipient=self.add(s.engine,'Generic Bound Body');artifact=self.add(s.engine,'Civic Saber')
        private=self.add(s.engine,'Generic Bound Growth',seat='C',zone='hand',ref='aura-boundary-private-c')
        self.assertTrue(self.binding(s,aura)['strict_capability_ready'])
        self.prepare(s,mana={'C':2,'G':1});self.checkpoint(s)
        self.reject(s,{'action_id':'cast:'+aura.ref,'targets':[artifact.ref],'pay':'manual','payment':{'C':2,'G':1}})
        self.cast(s,aura,{'C':2,'G':1},[recipient.ref]);s=self.replay(s,load=True)
        self.assertIsNone(self.resolve(s))
        current=s.state.cards[recipient.object_id]
        self.assertEqual((4,8),tuple(s.engine._numeric_stat(current.object_id,k) for k in ('power','toughness')))
        self.assertFalse(any(a.mana_ability for a in s.engine._activated_abilities(current)))
        self.assertEqual(recipient.object_id,s.state.cards[aura.object_id].attached_to)
        self.private_replay(s,private)

    def test_actual_natures_embrace_land_and_animated_land_keep_two_mana_grant(self):
        for index,animate in enumerate((False,True)):
            s=self.session(764101+index);aura=self.add(s.engine,"Nature's Embrace",zone='hand')
            land=self.add(s.engine,'Forest');land.acquired_control_turn_count=-1
            revolt=self.add(s.engine,"Nature's Revolt",zone='hand') if animate else None
            counter=self.add(s.engine,'Counterspell',zone='hand')
            bolt=self.add(s.engine,'Lightning Bolt',seat='B',zone='hand')
            s.state.players['B'].mana_pool['R']=1
            self.prepare(s,mana={'C':5 if animate else 2,'G':3 if animate else 1});self.checkpoint(s)
            self.cast(s,aura,{'C':2,'G':1},[land.ref]);self.assertIsNone(self.resolve(s))
            if animate:
                self.cast(s,revolt,{'C':3,'G':2});self.assertIsNone(self.resolve(s))
                self.assertEqual((4,4),tuple(s.engine._numeric_stat(land.object_id,k) for k in ('power','toughness')))
            else:self.assertNotIn('creature',s.engine._type_parts(s.engine._effective_card_data(land)['type_line'])[0])
            self.assertEqual(0,sum(s.state.players['A'].mana_pool.values()))
            self.cast(s,bolt,{'R':1},['A'],seat='B');target=s.state.stack[-1].ref
            self.cast(s,counter,{},[target],auto=True)
            self.assertTrue(s.state.cards[land.object_id].tapped)
            self.assertEqual(0,sum(s.state.players['A'].mana_pool.values()))
            s=self.replay(s,load=True);self.assertIsNone(self.resolve(s))
            self.assertEqual(40,s.state.players['A'].life)
            self.assertEqual(('graveyard','graveyard'),(s.state.cards[counter.object_id].zone,s.state.cards[bolt.object_id].zone))
            self.replay(s)

    def partial_source(self,name,seed):
        s=self.session(seed);s.state.config.semantic_policy='trusted_only'
        c=self.add(s.engine,name,zone='battlefield')
        ir=compile_oracle_card(self.db.lookup(name),capability_registry=self.registry,capability_profile='commander_review')
        self.assertNotEqual('exact',ir.status);self.assertTrue(ir.material_residuals)
        self.assertFalse(self.binding(s,c)['strict_capability_ready'])
        return s,c,ir

    def test_partial_pact_weapon_cannot_admit_or_activate_its_nonmana_equip(self):
        s,c,ir=self.partial_source('Pact Weapon',764110)
        target=self.add(s.engine,'Generic Bound Body');cost=self.add(s.engine,'Generic Bound Growth',zone='hand')
        self.assertTrue(any('attachment.equip.fixed_nonmana' in n.capability_dependencies for f in ir.faces for n in f.nodes))
        self.prepare(s,mana={'C':8,'B':2});self.checkpoint(s)
        self.assertFalse(any(r['id'].startswith('activate:'+c.ref+':') for r in self.actions(s)))
        self.assertFalse(any(a.builtin_semantic_key=='builtin:equip' for a in s.engine._activated_abilities(c)))
        self.reject(s,{'action_id':'activate:'+c.ref+':ab3','targets':[target.ref],'cost_cards':[cost.ref],'pay':'auto'})
        self.replay(s)

    def test_partial_reconfigure_companions_cannot_admit_or_attach(self):
        for i,name in enumerate(('The Reality Chip','Cloudsteel Kirin')):
            s,c,ir=self.partial_source(name,764111+i)
            target=self.add(s.engine,'Generic Bound Body')
            self.assertTrue(any('attachment.reconfigure.fixed_mana' in n.capability_dependencies for f in ir.faces for n in f.nodes))
            self.prepare(s,mana={'C':8,'U':2,'W':2});self.checkpoint(s)
            self.assertFalse(any(a.reconfigure for a in s.engine._activated_abilities(c)))
            self.assertFalse(any(r['id'].startswith('activate:'+c.ref+':') for r in self.actions(s)))
            self.reject(s,{'action_id':'activate:'+c.ref+':ab3-reconfigure-attach','targets':[target.ref],'pay':'auto'})
            self.replay(s)

    def test_exact_all_colors_fragment_does_not_admit_unowned_saga_and_replacement(self):
        s,c,ir=self.partial_source('The Kami War // O-Kagachi Made Manifest',764114)
        self.assertTrue(any('continuous.characteristics.all_colors_definition' in n.capability_dependencies for f in ir.faces for n in f.nodes))
        c.active_face='O-Kagachi Made Manifest'
        self.prepare(s,mana={'C':2,'W':1,'U':1,'B':1,'R':1,'G':1});self.checkpoint(s)
        self.assertFalse(self.binding(s,c)['strict_capability_ready'])
        self.assertTrue(any(r.material for f in ir.faces for r in f.residuals))
        self.reject(s,{'action_id':'cast:'+c.ref,'pay':'auto'})
        self.replay(s)

    def test_exact_resolution_mana_fragment_does_not_admit_unowned_saga_and_replacement(self):
        s=self.session(764115)
        s.state.config.semantic_policy='trusted_only'
        source=self.add(s.engine,'The Legend of Roku // Avatar Roku')
        source.active_face='Avatar Roku'
        control=self.add(s.engine,'Quirion Sentinel',zone='hand')
        private=self.add(s.engine,'Generic Bound Growth',seat='C',zone='hand')
        ir=compile_oracle_card(self.db.lookup(source.printed_name),
            capability_registry=self.registry,capability_profile='commander_review')
        capability='mana.production.fixed_resolution_choice'
        self.assertTrue(any(capability in node.capability_dependencies
            for face in ir.faces for node in face.nodes))
        self.assertNotEqual('exact',ir.status)
        blockers={blocker for residual in ir.material_residuals for blocker in residual.blockers}
        self.assertTrue({'replacement applicability','self-replacement and prevention ordering'} <= blockers)
        self.assertFalse(self.binding(s,source)['strict_capability_ready'])
        self.assertIn('trust_basis:unresolved',self.binding(s,source)['blockers'])
        self.assertTrue(self.binding(s,control)['strict_capability_ready'])

        self.prepare(s,mana={'C':12,'G':1})
        self.checkpoint(s)
        self.assertEqual('battlefield',s.state.cards[source.object_id].zone)
        self.assertIn('creature',s.engine._type_parts(s.engine._effective_card_data(source)['type_line'])[0])
        offers=self.actions(s)
        ability, = s.engine._activated_abilities(source)
        self.assertIsNone(ability.builtin_semantic_key)
        activation_id=f'activate:{source.ref}:{ability.ability_id}'
        self.assertFalse(any(row['id']==activation_id for row in offers))
        self.assertTrue(any(row.get('kind')=='cast' and row.get('card')==control.ref for row in offers))

        self.cast(s,control,{'C':1,'G':1})
        pending=self.resolve(s)
        self.assertIsNotNone(pending)
        self.assertEqual('semantic.choice',pending.kind)
        context=s.packet('pilot:A',full=True)['decision']['ctx']
        self.assertEqual(('choose_mana',1),(context['operation'],context['amount']))
        self.reject(s,{'action_id':'choose','choice':'G'},principal='pilot:D')
        result=s.act('pilot:A',{'action_id':'choose','choice':'G'})
        self.assertTrue(result.ok,result.summary)
        self.assertIsNone(self.resolve(s))
        self.assertEqual(1,s.state.players['A'].mana_pool['G'])
        self.assertEqual('battlefield',s.state.cards[control.object_id].zone)
        self.assertEqual('battlefield',s.state.cards[source.object_id].zone)
        self.assertFalse(self.binding(s,source)['strict_capability_ready'])
        self.private_replay(s,private)

        # The source catalogue discovers this printed price but has no
        # executable token body. Reject before payment or stack mutation.
        command={'action_id':activation_id,'pay':'manual','payment':{'C':8}}
        card_ids=set(s.state.cards)
        mana=dict(s.state.players['A'].mana_pool)
        self.reject(s,command,principal='pilot:D')
        self.reject(s,command)
        self.assertFalse(s.state.stack)
        self.assertEqual(card_ids,set(s.state.cards))
        self.assertEqual(mana,s.state.players['A'].mana_pool)
        self.assertFalse(self.binding(s,source)['strict_capability_ready'])
        s=self.replay(s,load=True)
        self.assertFalse(s.state.stack)
        self.assertFalse(any(row['id']==activation_id for row in self.actions(s)))
        self.private_replay(s,private)
