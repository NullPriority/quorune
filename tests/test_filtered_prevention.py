from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from pathlib import Path
import json,tempfile,unittest
from unittest.mock import patch
from common import ROOT
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition,DeckEntry
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
import test_designation_prevention as controls
from quorune.rules.evidence_storage import read_evidence_document

BASE='damage.prevention.persistent_amount'
PUBLIC_STATE='state_query.permanent.public_state_predicate'
NAMES=('Circle of Protection: Shadow','Wellgabber Apothecary','Shieldmage Elder')


class FilteredPreventionTests(unittest.TestCase):
    register_fixture_cards_on_add=True
    session=controls.DesignationPreventionTests.session
    add=controls.DesignationPreventionTests.add
    prepare=controls.DesignationPreventionTests.prepare
    checkpoint=controls.DesignationPreventionTests.checkpoint
    resolve=controls.DesignationPreventionTests.resolve
    replay=controls.DesignationPreventionTests.replay
    binding=controls.DesignationPreventionTests.binding
    actions=controls.DesignationPreventionTests.actions
    reject=controls.DesignationPreventionTests.reject
    private_replay=controls.DesignationPreventionTests.private_replay
    priority=controls.DesignationPreventionTests.priority
    options=controls.DesignationPreventionTests.options
    use=controls.DesignationPreventionTests.use
    top=controls.DesignationPreventionTests.top
    settle=controls.DesignationPreventionTests.settle
    normal=controls.DesignationPreventionTests.normal
    activate=controls.DesignationPreventionTests.activate

    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory();path=Path(cls.temp.name)/'filtered-prevention.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',ROOT/'tests/fixtures/filtered-prevention-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Filtered prevention scopes',[DeckEntry('Generic Bound Commander',1,'commander'),DeckEntry('Generic Bound Plains',30)],['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls):cls.db.close();cls.temp.cleanup()

    def ping(self,s,source):
        self.priority(s,'B');rows=s.packet('pilot:B',full=True)['decision']['ctx']['legal']['actions']
        a=next((a for a in rows if a['id'].startswith('activate:'+source.ref+':')),None);self.assertIsNotNone(a)
        command={'action_id':a['id'],'targets':['A'],'pay':'manual','payment':{}};self.reject(s,command,'pilot:D')
        r=s.act('pilot:B',command);self.assertTrue(r.ok,r.summary)

    def choose_shadow(self,s,source,excluded=()):
        pending=self.settle(s);self.assertIsNotNone(pending);ctx=s.packet('pilot:A',full=True)['decision']['ctx']
        self.assertEqual('choose_damage_source',ctx['operation']);ids={r['id'] for r in ctx['objects']};self.assertIn(source.ref,ids)
        for ref in excluded:self.assertNotIn(ref,ids)
        command={'action_id':'choose','source':source.ref};self.reject(s,command,'pilot:D');s=self.replay(s,load=True)
        r=s.act('pilot:A',command);self.assertTrue(r.ok,r.summary);return s

    def test_originals_have_complete_normal_admission(self):
        from quorune.oracle_ir import compile_oracle_card
        s=self.session(814000)
        for name in NAMES:
            card=self.add(s.engine,name,zone='hand');b=self.binding(s,card);self.assertTrue(b['strict_capability_ready'],(name,b['blockers']))
            ir=compile_oracle_card(self.db.lookup(name),capability_registry=self.registry,capability_profile='commander_review')
            self.assertEqual('exact',ir.status,(name,ir.material_residuals));self.assertIn(BASE,{c for p in s.engine.semantics.programs_for_oracle(card.oracle_id) for c in p.capability_dependencies})

    def test_shared_target_codec_keeps_tapped_subtype_union_and_source_identity_effects(self):
        from quorune.compiler.direct_target import direct_permanent_target_spec,DirectPermanentTargetSpec
        from quorune.compiler.prevention_templates import fixed_prevention_effect_template
        target=direct_permanent_target_spec('target tapped Merfolk or Kithkin creature');self.assertIsNotNone(target)
        schema=target.to_target_schema();self.assertEqual(['kithkin','merfolk'],schema['subtypes_any']);self.assertTrue(schema['state_predicate']['tapped'])
        self.assertEqual(target,DirectPermanentTargetSpec.from_target_schema(json.loads(json.dumps(schema))))
        self.assertIsNone(direct_permanent_target_spec('target tapped invented-kind creature'))
        for phrase,category in (('target creature','permanent'),('target spell','spell')):
            row=fixed_prevention_effect_template('Prevent all damage '+phrase+' would deal this turn.');self.assertIsNotNone(row)
            self.assertEqual('$target.0',row[1][0]['chosen_source']);self.assertEqual('*',row[1][0]['subject']);self.assertEqual([category],row[2]['categories'])
        row=fixed_prevention_effect_template('Prevent all damage that would be dealt to target tapped Merfolk or Kithkin creature this turn.')
        self.assertEqual('$target.0',row[1][0]['subject']);self.assertNotIn('scope',row[1][0])

    def test_real_shadow_circle_prevents_noncombat_matching_source(self):
        s=self.session(814010);circle=self.add(s.engine,'Circle of Protection: Shadow',zone='hand');spark=self.add(s.engine,'Cunning Sparkmage',seat='B')
        plain=self.add(s.engine,'Cunning Sparkmage',seat='B',ref='plain-sparkmage');rift=self.add(s.engine,'Shadow Rift',zone='hand')
        private=self.add(s.engine,'Generic Bound Growth',seat='C',zone='hand');self.prepare(s,mana={'C':2,'W':1,'U':1});self.checkpoint(s)
        self.normal(s,circle,{'C':1,'W':1});self.assertIsNone(self.settle(s));self.normal(s,rift,{'U':1},targets=[spark.ref]);self.assertIsNone(self.settle(s))
        self.ping(s,spark);self.activate(s,circle,payment={'C':1});s=self.choose_shadow(s,spark,(plain.ref,))
        self.assertIsNone(self.settle(s));self.assertEqual(40,s.state.players['A'].life);self.assertEqual([],s.state.damage_prevention_shields);self.private_replay(s,private)

    def test_real_shadow_property_loss_keeps_shield_unused(self):
        s=self.session(814020);circle=self.add(s.engine,'Circle of Protection: Shadow',zone='hand');spark=self.add(s.engine,'Cunning Sparkmage',seat='B')
        rift=self.add(s.engine,'Shadow Rift',zone='hand');humble=self.add(s.engine,'Humble',zone='hand');private=self.add(s.engine,'Generic Bound Growth',seat='C',zone='hand')
        self.prepare(s,mana={'C':3,'W':2,'U':1});self.checkpoint(s);self.normal(s,circle,{'C':1,'W':1});self.assertIsNone(self.settle(s))
        self.normal(s,rift,{'U':1},targets=[spark.ref]);self.assertIsNone(self.settle(s));self.ping(s,spark);self.activate(s,circle,payment={'C':1});s=self.choose_shadow(s,spark)
        self.priority(s,'A');self.normal(s,humble,{'C':1,'W':1},targets=[spark.ref]);s=self.replay(s,load=True);self.assertIsNone(self.settle(s))
        self.assertEqual(39,s.state.players['A'].life);self.assertEqual(1,len(s.state.damage_prevention_shields));self.private_replay(s,private)

    def test_real_wellgabber_accepts_each_tapped_subtype_and_rejects_other_candidates(self):
        for i,name in enumerate(('Merfolk of the Pearl Trident','Mosquito Guard')):
            s=self.session(814030+i);source=self.add(s.engine,'Wellgabber Apothecary',zone='hand');target=self.add(s.engine,name,seat='B')
            target.tapped=True;untapped=self.add(s.engine,name,seat='B',ref='untapped-tribal');wrong=self.add(s.engine,'Generic Bound Body',seat='B');wrong.tapped=True
            bolt=self.add(s.engine,'Lightning Bolt',zone='hand');private=self.add(s.engine,'Generic Bound Growth',seat='C',zone='hand')
            self.prepare(s,mana={'C':5,'W':2,'R':1});self.checkpoint(s);self.normal(s,source,{'C':4,'W':1});self.assertIsNone(self.settle(s))
            self.normal(s,bolt,{'R':1},targets=[target.ref]);self.priority(s,'A');a=next(a for a in self.actions(s) if a['id'].startswith('activate:'+source.ref+':'))
            refs=set(a['target_schema']['legal_refs']);self.assertIn(target.ref,refs);self.assertNotIn(untapped.ref,refs);self.assertNotIn(wrong.ref,refs)
            self.activate(s,source,payment={'C':1,'W':1},targets=[target.ref]);s=self.replay(s,load=True);self.assertIsNone(self.settle(s));self.assertEqual(0,s.state.cards[target.object_id].marked_damage);self.private_replay(s,private)

    def test_real_wellgabber_revalidates_untap_and_does_not_follow_reentry(self):
        for returned in (False,True):
            s=self.session(814040+returned);source=self.add(s.engine,'Wellgabber Apothecary');target=self.add(s.engine,'Merfolk of the Pearl Trident',seat='A');target.tapped=True
            change=self.add(s.engine,'Unsummon' if returned else 'Generic Bound Growth',zone='hand');bolt=self.add(s.engine,'Lightning Bolt',zone='hand')
            private=self.add(s.engine,'Generic Bound Growth',seat='C',zone='hand',ref='private-wellgabber-change')
            self.prepare(s,mana={'C':1,'W':1,'U':2,'G':1,'R':1});self.checkpoint(s);self.activate(s,source,payment={'C':1,'W':1},targets=[target.ref])
            if returned:self.assertIsNone(self.settle(s))
            self.normal(s,change,{'U':1} if returned else {'G':1},targets=[target.ref]);self.assertIsNone(self.settle(s))
            if returned:self.normal(s,target,{'U':1});self.assertIsNone(self.settle(s))
            else:self.assertEqual([],s.state.damage_prevention_shields)
            self.normal(s,bolt,{'R':1},targets=[target.ref]);s=self.replay(s,load=True);self.assertIsNone(self.settle(s));self.assertEqual('graveyard',s.state.cards[target.object_id].zone);self.private_replay(s,private)

    def test_real_elder_pays_tribal_taps_and_pins_spell_and_creature_sources(self):
        s=self.session(814050);elder=self.add(s.engine,'Shieldmage Elder',zone='hand');cleric=self.add(s.engine,'Generic Prevention Cleric')
        first=self.add(s.engine,'Fugitive Wizard',ref='first-prevention-wizard');second=self.add(s.engine,'Fugitive Wizard',ref='second-prevention-wizard')
        enemy=self.add(s.engine,'Cunning Sparkmage',seat='B');bolt=self.add(s.engine,'Lightning Bolt',zone='hand');blink=self.add(s.engine,'Flicker of Fate',zone='hand')
        private=self.add(s.engine,'Generic Bound Growth',seat='C',zone='hand');self.prepare(s,mana={'C':6,'W':2,'R':1});self.checkpoint(s)
        self.normal(s,elder,{'C':5,'W':1});self.assertIsNone(self.settle(s));self.normal(s,bolt,{'R':1},targets=['A']);self.priority(s,'A')
        a=next(a for a in self.actions(s) if a['id'].startswith('activate:'+elder.ref+':') and 'target spell' in json.dumps(a).casefold())
        targets=a['target_schema']['legal_refs'];self.assertEqual(1,len(targets))
        bad={'action_id':a['id'],'targets':[targets[0]],'cost_cards':[first.ref,first.ref],'pay':'manual','payment':{}};self.reject(s,bad,'pilot:A')
        self.activate(s,elder,needle='target spell',targets=[targets[0]],cost_cards=[first.ref,second.ref]);self.assertTrue(s.state.cards[first.object_id].tapped);self.assertTrue(s.state.cards[second.object_id].tapped)
        s=self.replay(s,load=True);self.assertIsNone(self.settle(s));self.assertEqual(40,s.state.players['A'].life)
        self.ping(s,enemy);self.activate(s,elder,needle='target creature',targets=[enemy.ref],cost_cards=[elder.ref,cleric.ref])
        self.assertTrue(s.state.cards[elder.object_id].tapped);self.assertTrue(s.state.cards[cleric.object_id].tapped);self.assertIsNone(self.settle(s));self.assertEqual(40,s.state.players['A'].life)
        old=s.state.cards[enemy.object_id].logical_object_id;self.normal(s,blink,{'C':1,'W':1},targets=[enemy.ref]);self.assertIsNone(self.settle(s));self.assertNotEqual(old,s.state.cards[enemy.object_id].logical_object_id)
        self.ping(s,enemy);s=self.replay(s,load=True);self.assertIsNone(self.settle(s));self.assertEqual(39,s.state.players['A'].life);self.private_replay(s,private)

    def test_verified_withdrawal_and_fresh_predicate_and_source_pin_omissions_reject(self):
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from scripts.update_capability_evidence import build_index,discover_tests
        from quorune.oracle_ir import compile_oracle_card
        raw=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding='utf-8'));decl=read_evidence_document(ROOT/'platform/capability-evidence-declarations.json')
        for cap,name in ((BASE,'Circle of Protection: Shadow'),(PUBLIC_STATE,'Wellgabber Apothecary')):
            changed=deepcopy(raw);next(c for c in changed['capabilities'] if c['id']==cap).update(status='blocked',blockers=['withdrawn prevention predicate owner'],dependency_fail_closed_status='not_run')
            registry=CapabilityRegistry(changed);index=build_index(registry_value=changed,declaration_source=decl,discovered_test_ids=set(discover_tests(ROOT).values()));registry.mark_evidence_verified(validate_capability_evidence_index(index,registry=registry))
            ir=compile_oracle_card(self.db.lookup(name),capability_registry=registry,capability_profile='commander_review');blockers=[b for r in ir.material_residuals for b in r.blockers]
            self.assertTrue(any('status:'+cap+':blocked' in b for b in blockers),blockers);self.assertFalse(any('evidence_index:unverified' in b for b in blockers))
        from quorune.semantic_choices import damage_prevention as chooser
        method='test_real_shadow_circle_prevents_noncombat_matching_source'
        original=chooser._candidates
        def omit_shadow(query,*,predicate):
            return original(query,predicate=replace(predicate,keywords_all=()) if predicate.keywords_all==('shadow',) else predicate)
        with patch.object(chooser,'_candidates',side_effect=omit_shadow):
            with self.assertRaises(AssertionError):getattr(type(self)(method),method)()
        from quorune import damage_prevention_creation as owner
        method='test_real_elder_pays_tribal_taps_and_pins_spell_and_creature_sources'
        with patch.object(owner,'pin_chosen_damage_source',return_value=None):
            with self.assertRaises(AssertionError):getattr(type(self)(method),method)()
