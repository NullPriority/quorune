from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import json
import tempfile
import unittest
from unittest.mock import patch

from common import ROOT
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
import test_attached_prevention as controls
from quorune.rules.evidence_storage import read_evidence_document

NAMES=('Ghost, Spectral Saboteur','Jumbo Cactuar','Akawalli, the Seething Tower')


class AbilityLabelCompositionTests(unittest.TestCase):
    register_fixture_cards_on_add=True
    session=controls.AttachedPreventionTests.session
    add=controls.AttachedPreventionTests.add
    prepare=controls.AttachedPreventionTests.prepare
    checkpoint=controls.AttachedPreventionTests.checkpoint
    binding=controls.AttachedPreventionTests.binding
    reject=controls.AttachedPreventionTests.reject
    replay=controls.AttachedPreventionTests.replay
    private_replay=controls.AttachedPreventionTests.private_replay
    priority=controls.AttachedPreventionTests.priority
    options=controls.AttachedPreventionTests.options
    actions=controls.AttachedPreventionTests.actions
    use=controls.AttachedPreventionTests.use
    normal=controls.AttachedPreventionTests.normal
    activate=controls.AttachedPreventionTests.activate
    settle=controls.AttachedPreventionTests.settle
    resolve=controls.AttachedPreventionTests.resolve
    top=controls.AttachedPreventionTests.top

    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory();path=Path(cls.temp.name)/'ability-labels.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',ROOT/'tests/fixtures/ability-label-composition-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Ability label composition',[DeckEntry('Generic Bound Commander',1,'commander'),DeckEntry('Generic Bound Plains',30)],['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls):
        cls.db.close();cls.temp.cleanup()

    def advance(self,s,predicate):
        for _ in range(180):
            s.engine.pump()
            if predicate(s):return
            principal=s.pending_principals()[0];packet=s.packet(principal,full=True);decision=packet['decision'];kind=s.state.pending_decision.kind;ctx=decision['ctx']
            if kind=='priority':command={'action_id':'pass'}
            elif kind=='combat.attackers':command={'action_id':'attack','atk':{}}
            elif kind=='combat.blockers':command={'action_id':'block','blocks':{}}
            elif kind=='trigger.order':command={'action_id':'order','triggers':[row['id'] for row in ctx['triggers']]}
            elif kind=='cleanup.discard':command={'a':'discard','cards':[row['id'] for row in ctx['hand'][:ctx['count']]]}
            else:self.fail('Unexpected label witness decision '+kind)
            result=s.act(principal,command);self.assertTrue(result.ok,result.summary)
        self.fail('Label witness did not reach its expected state')

    def cast_auto(self,s,card,targets):
        option=next(o for o in self.options(s,card) if o['id']=='normal')
        command={'action_id':'cast:'+card.ref,'cost_option':option['id'],'pay':'auto','targets':targets}
        self.reject(s,command,'pilot:D');result=s.act('pilot:A',command);self.assertTrue(result.ok,result.summary)

    def attack(self,s,refs):
        self.advance(s,lambda x:x.state.active_player=='A' and getattr(x.state.pending_decision,'kind',None)=='combat.attackers')
        command={'action_id':'attack','atk':{ref:'B' for ref in refs}}
        self.reject(s,command,'pilot:D');result=s.act('pilot:A',command);self.assertTrue(result.ok,result.summary)
        self.assertEqual(len(refs),len(s.state.combat.attackers))

    def test_originals_have_complete_admission_and_original_source_spans(self):
        s=self.session(815000)
        for name in NAMES:
            card=self.add(s.engine,name,zone='hand');bound=self.binding(s,card)
            self.assertTrue(bound['strict_capability_ready'],(name,bound['blockers']))
            record=self.db.lookup(name);ir=compile_oracle_card(record,capability_registry=self.registry,capability_profile='commander_review')
            self.assertEqual('exact',ir.status,ir.material_residuals)
            for face in ir.faces:
                for node in face.nodes:
                    self.assertEqual(node.text,record.oracle_text[node.span.start:node.span.end])
            self.assertTrue(any(' — ' in n.text for f in ir.faces for n in f.nodes))

    def test_static_labels_preserve_complete_bodies_and_functional_boundaries(self):
        from dataclasses import replace
        from quorune.compiler.oracle_source_text import without_ability_word_label
        from quorune.compiler.draw_templates import trigger_ability_word_material_line
        self.assertEqual("Ghost can't be blocked.",without_ability_word_label("Intangibility — Ghost can't be blocked."))
        self.assertEqual('As long as a condition holds, this creature gets +2/+2.',without_ability_word_label('Descend 8 — As long as a condition holds, this creature gets +2/+2.'))
        self.assertEqual('Whenever this creature attacks, it gets +9999/+0 until end of turn.',trigger_ability_word_material_line('10,000 Needles — Whenever this creature attacks, it gets +9999/+0 until end of turn.'))
        from quorune.compiler.closed_static_nodes import closed_static_or_replacement_node
        from quorune.compiler.ir_model import SourceSpan
        for separator in ('—','-'):
            text='Intangibility '+separator+" This creature can't be blocked."
            node=closed_static_or_replacement_node(node_id='token:n1',line=text,material_line=text,span=SourceSpan(0,len(text),1),source_name='Generic Token',card_types=('creature',),permanent_card_types={'creature'},source_is_class=False,capability_registry=self.registry,capability_profile='commander_review',residuals=[])
            self.assertIsNotNone(node);self.assertEqual(text,node.text)
        for text in ("Visit — This creature can't be blocked.","Visit - This creature can't be blocked.","Visit — Whenever this creature attacks, it gets +9999/+0 until end of turn.","Visit - Whenever this creature attacks, it gets +9999/+0 until end of turn.","If you paid — This creature can't be blocked.","Intangibility — This creature can't be blocked unless the top card of your library is blue."):
            card=replace(self.db.lookup(NAMES[0]),oracle_text=text,keywords=())
            ir=compile_oracle_card(card,capability_registry=self.registry,capability_profile='commander_review')
            self.assertNotEqual('exact',ir.status,(text,ir))

    def ghost_journey(self,*,remove):
        s=self.session(815010+remove);ghost=self.add(s.engine,NAMES[0],zone='hand');body=self.add(s.engine,'Generic Bound Body')
        blocker=self.add(s.engine,'Generic Bound Body',seat='B',ref='enemy_body');humble=self.add(s.engine,'Humble',zone='hand')
        self.add(s.engine,'Plains',ref='plains1');self.add(s.engine,'Plains',ref='plains2')
        private=self.add(s.engine,'Generic Bound Growth',seat='C',zone='hand')
        self.prepare(s,active='D',mana={'C':2,'U':1});self.checkpoint(s)
        self.normal(s,ghost,{'C':2,'U':1});self.assertIsNone(self.settle(s));self.assertEqual('D',s.state.active_player)
        self.attack(s,[ghost.ref,body.ref])
        if remove:
            self.priority(s,'A');self.cast_auto(s,humble,[ghost.ref]);self.assertIsNone(self.settle(s))
        self.advance(s,lambda x:getattr(x.state.pending_decision,'kind',None)=='combat.blockers')
        if remove:
            command={'action_id':'block','blocks':{blocker.ref:ghost.ref}}
        else:
            self.reject(s,{'action_id':'block','blocks':{blocker.ref:ghost.ref}},'pilot:B')
            command={'action_id':'block','blocks':{blocker.ref:body.ref}}
        self.reject(s,command,'pilot:D');result=s.act('pilot:B',command);self.assertTrue(result.ok,result.summary)
        s=self.replay(s,load=True)
        self.advance(s,lambda x:x.state.players['B'].life<40)
        self.assertEqual(38,s.state.players['B'].life)
        self.assertEqual('graveyard' if remove else 'battlefield',s.state.cards[ghost.object_id].zone)
        self.private_replay(s,private)

    def test_real_ghost_flash_cast_and_unblockable_combat(self):
        self.ghost_journey(remove=False)

    def test_real_ghost_ability_removal_restores_legal_blocking(self):
        self.ghost_journey(remove=True)

    def test_real_numeric_flavor_trigger_buffs_exactly_one_combat_and_expires(self):
        s=self.session(815030);source=self.add(s.engine,NAMES[1],zone='hand');haste=self.add(s.engine,'Expedite',zone='hand')
        self.add(s.engine,'Fugitive Wizard',seat='B');private=self.add(s.engine,'Generic Bound Growth',seat='C',zone='hand')
        s.state.players['B'].life=20000;self.prepare(s,mana={'C':5,'G':2,'R':1});self.checkpoint(s)
        self.normal(s,source,{'C':5,'G':2});self.assertIsNone(self.settle(s));self.assertEqual(1,s.engine._numeric_stat(source.object_id,'power'))
        self.normal(s,haste,{'R':1},targets=[source.ref]);self.assertIsNone(self.settle(s));self.attack(s,[source.ref])
        s=self.replay(s,load=True)
        self.advance(s,lambda x:getattr(x.state.pending_decision,'kind',None)=='combat.blockers')
        self.assertEqual(10000,s.engine._numeric_stat(source.object_id,'power'));self.assertEqual(7,s.engine._numeric_stat(source.object_id,'toughness'))
        result=s.act('pilot:B',{'action_id':'block','blocks':{}});self.assertTrue(result.ok,result.summary)
        self.advance(s,lambda x:x.state.players['B'].life<20000);self.assertEqual(10000,s.state.players['B'].life)
        self.advance(s,lambda x:x.state.active_player=='B');self.assertEqual(1,s.engine._numeric_stat(source.object_id,'power'))
        self.private_replay(s,private)

    def descend_journey(self,count,*,exile=False):
        certify=count==8 or exile
        s=self.session(815040+count+20*exile);source=self.add(s.engine,NAMES[2],zone='hand');haste=self.add(s.engine,'Expedite',zone='hand')
        blockers=[self.add(s.engine,'Fugitive Wizard',seat='B',ref='wizard'+str(i)) for i in range(2)]
        for i in range(count):self.add(s.engine,'Forest',zone='graveyard',ref='graveforest'+str(i))
        for i in range(8):self.add(s.engine,'Forest',seat='C',zone='graveyard',ref='foreignforest'+str(i))
        self.add(s.engine,'Humble',zone='graveyard',ref='nonpermanent_grave')
        crypt=self.add(s.engine,"Tormod's Crypt",zone='hand');private=self.add(s.engine,'Generic Bound Growth',seat='C',zone='hand')
        self.prepare(s,mana={'C':1,'B':1,'G':1,'R':1});self.checkpoint(s)
        self.normal(s,source,{'C':1,'B':1,'G':1});self.assertIsNone(self.settle(s))
        expected=3+2*(count>=4)+2*(count>=8);self.assertEqual(expected,s.engine._numeric_stat(source.object_id,'power'))
        self.normal(s,crypt,{});self.assertIsNone(self.settle(s));self.normal(s,haste,{'R':1},targets=[source.ref]);self.assertIsNone(self.settle(s))
        self.attack(s,[source.ref])
        if exile:
            self.priority(s,'A');self.activate(s,crypt,targets=['A']);self.assertIsNone(self.settle(s))
            self.assertEqual(3,s.engine._numeric_stat(source.object_id,'power'))
        self.advance(s,lambda x:getattr(x.state.pending_decision,'kind',None)=='combat.blockers')
        both={'action_id':'block','blocks':{card.ref:source.ref for card in blockers}}
        if count>=8 and not exile:
            self.reject(s,both,'pilot:B');command={'action_id':'block','blocks':{blockers[0].ref:source.ref}};damage=6
        else:
            command=both;damage=0 if count<4 or exile else 3
        self.reject(s,command,'pilot:D');result=s.act('pilot:B',command);self.assertTrue(result.ok,result.summary)
        if certify:s=self.replay(s,load=True)
        self.advance(s,lambda x:getattr(x.state.pending_decision,'kind',None)=='combat.damage')
        selected=blockers[:1] if count>=8 and not exile else blockers
        power=s.engine._numeric_stat(source.object_id,'power')
        assignments=[{'source':source.ref,'target':card.ref,'amount':1 if i else power-damage-len(selected)+1} for i,card in enumerate(selected)]
        if damage:assignments.append({'source':source.ref,'target':'B','amount':damage})
        command={'a':'dmg','assignments':assignments}
        self.reject(s,{'a':'dmg','assignments':[{'source':source.ref,'target':'B','amount':power}]},'pilot:A')
        self.reject(s,command,'pilot:D')
        if certify:s=self.replay(s,load=True)
        result=s.act('pilot:A',command);self.assertTrue(result.ok,result.summary)
        self.advance(s,lambda x:x.state.step=='end_combat')
        self.assertEqual(40-damage,s.state.players['B'].life);self.assertEqual('battlefield',s.state.cards[source.object_id].zone)
        if certify:self.private_replay(s,private)

    def test_real_descend_thresholds_accumulate_and_restrict_blocking(self):
        for count in (3,4,7,8):self.descend_journey(count)

    def test_real_descend_graveyard_exile_removes_both_bonuses_and_block_limit(self):
        self.descend_journey(8,exile=True)

    def test_verified_withdrawal_and_fresh_label_omission_mutants_fail_closed(self):
        import re
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from scripts.update_capability_evidence import build_index,discover_tests
        raw=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text());declarations=read_evidence_document(ROOT/'platform/capability-evidence-declarations.json')
        for cap,name in (('combat.declaration.typed_components',NAMES[0]),('trigger.event.normalized_self_attack',NAMES[1]),('continuous.characteristics.query_count_modifier',NAMES[2]),('continuous.characteristics.fixed_public_state',NAMES[2])):
            changed=deepcopy(raw);next(c for c in changed['capabilities'] if c['id']==cap).update(status='blocked',blockers=['withdrawn labeled-body owner'],dependency_fail_closed_status='not_run')
            registry=CapabilityRegistry(changed);index=build_index(registry_value=changed,declaration_source=declarations,discovered_test_ids=set(discover_tests(ROOT).values()))
            registry.mark_evidence_verified(validate_capability_evidence_index(index,registry=registry))
            ir=compile_oracle_card(self.db.lookup(name),capability_registry=registry,capability_profile='commander_review')
            blockers=[b for r in ir.material_residuals for b in r.blockers]
            self.assertTrue(any('status:'+cap+':blocked' in b for b in blockers),blockers)
            self.assertFalse(any('evidence_index:unverified' in b for b in blockers))
        from quorune.compiler import oracle_source_text,draw_templates
        method='test_originals_have_complete_admission_and_original_source_spans'
        with patch('quorune.compiler.closed_static_nodes.without_ability_word_label',side_effect=lambda text:text):
            with self.assertRaises(AssertionError):getattr(type(self)(method),method)()
        with patch.object(draw_templates,'_TRIGGER_ABILITY_WORD',re.compile(r'(?!)')):
            with self.assertRaises(AssertionError):getattr(type(self)(method),method)()
