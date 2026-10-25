from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
import json,tempfile,unittest
from unittest.mock import patch
from common import ROOT
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition,DeckEntry
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
import test_attached_prevention as controls
from quorune.rules.evidence_storage import read_evidence_document

CAP='damage.prevention.source_entry_designation'
BASE='damage.prevention.persistent_amount'
ENTRY='zone.entry.public_designation'
NAMES=('Story Circle','Prismatic Circle','Circle of Solace')


class DesignationPreventionTests(unittest.TestCase):
    register_fixture_cards_on_add=True
    session=controls.AttachedPreventionTests.session
    add=controls.AttachedPreventionTests.add
    prepare=controls.AttachedPreventionTests.prepare
    checkpoint=controls.AttachedPreventionTests.checkpoint
    resolve=controls.AttachedPreventionTests.resolve
    replay=controls.AttachedPreventionTests.replay
    binding=controls.AttachedPreventionTests.binding
    actions=controls.AttachedPreventionTests.actions
    reject=controls.AttachedPreventionTests.reject
    private_replay=controls.AttachedPreventionTests.private_replay
    priority=controls.AttachedPreventionTests.priority
    options=controls.AttachedPreventionTests.options
    use=controls.AttachedPreventionTests.use
    top=controls.AttachedPreventionTests.top
    settle=controls.AttachedPreventionTests.settle
    normal=controls.AttachedPreventionTests.normal
    clear_hands=controls.AttachedPreventionTests.clear_hands
    hand=controls.AttachedPreventionTests.hand
    activate=controls.AttachedPreventionTests.activate

    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory();path=Path(cls.temp.name)/'designation-prevention.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',ROOT/'tests/fixtures/designation-prevention-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Bound entry-designation prevention',[DeckEntry('Generic Bound Commander',1,'commander'),DeckEntry('Generic Bound Plains',30)],['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls):cls.db.close();cls.temp.cleanup()

    def choose_entry(self,s,value):
        pending=self.settle(s);self.assertIsNotNone(pending);self.assertEqual('replacement.order',pending.kind)
        rows=s.packet('pilot:A',full=True)['decision']['ctx']['options'];row=next(r for r in rows if r['id'].endswith(':'+value))
        command={'action_id':'choose','replacement':row['id']};self.reject(s,command,'pilot:D');s=self.replay(s,load=True)
        r=s.act('pilot:A',command);self.assertTrue(r.ok,r.summary);return s

    def choose_source(self,s,name,*,excluded=()):
        pending=self.settle(s);self.assertIsNotNone(pending);ctx=s.packet('pilot:A',full=True)['decision']['ctx']
        self.assertEqual('choose_damage_source',ctx['operation']);rows=ctx['objects']
        for ref in excluded:self.assertNotIn(ref,{r['id'] for r in rows})
        found=[r for r in rows if r['name']==name];self.assertEqual(1,len(found),(name,rows))
        command={'action_id':'choose','source':found[0]['id']};self.reject(s,command,'pilot:D');s=self.replay(s,load=True)
        r=s.act('pilot:A',command);self.assertTrue(r.ok,r.summary);self.assertIsNone(self.settle(s));return s

    def test_originals_admit_and_require_actual_designation_predicate_producer(self):
        from quorune.oracle_ir import compile_oracle_card
        from quorune.card_programs.binding import bind_semantic_program_runtime
        s=self.session(813000)
        for name in NAMES:
            card=self.add(s.engine,name,zone='hand');b=self.binding(s,card);self.assertTrue(b['strict_capability_ready'],(name,b['blockers']))
            ir=compile_oracle_card(self.db.lookup(name),capability_registry=self.registry,capability_profile='commander_review');self.assertEqual('exact',ir.status)
            p=next((p for p in s.engine.semantics.programs_for_oracle(card.oracle_id) if CAP in p.capability_dependencies and p.effects),None);self.assertIsNotNone(p)
            deps=tuple(c for c in p.capability_dependencies if c!=CAP)
            changed=replace(p,capability_dependencies=deps,capability_closure=self.registry.closure(deps,profile='commander_review').to_dict())
            b=bind_semantic_program_runtime(changed,capability_registry=self.registry,profile='commander_review');self.assertFalse(b['strict']);self.assertIn('capability:undeclared_runtime_dependency:'+CAP,b['blockers'])

    def test_closed_information_distinguishes_missing_choice_from_missing_or_foreign_capture(self):
        from quorune.designation_prevention import DESIGNATION_PREDICATE_KIND as KIND,DESIGNATION_INFORMATION_FIELD as FIELD,capture_designation_information,resolve_designation_predicate,designation_predicate_kind,unavailable_designation
        spec={'kind':KIND,'schema_version':1,'designation':'color'}
        source=SimpleNamespace(object_id='source',logical_object_id='source@3',annotations={'chosen_color':'R'})
        info=capture_designation_information(source,(spec,));item=SimpleNamespace(source_object_id='source',card_object_id=None,context={'source_logical_object_id':'source@3',FIELD:info})
        self.assertEqual(['R'],resolve_designation_predicate(spec,item)['colors_any'])
        for bad in ({**spec,'schema_version':True},{**spec,'schema_version':2},{**spec,'designation':'card_name'},{**spec,'extra':True}):
            with self.assertRaises(ValueError):designation_predicate_kind(bad)
        for changed in ({},{**info,'source_object_id':'foreign'},{**info,'source_logical_object_id':'source@4'},{**info,'values':{}}):
            with self.assertRaises(ValueError):resolve_designation_predicate(spec,replace_namespace(item,FIELD,changed))
        source.annotations={};item.context[FIELD]=capture_designation_information(source,(spec,));self.assertTrue(unavailable_designation(resolve_designation_predicate(spec,item)))
        source.annotations={'chosen_color':'C'}
        with self.assertRaises(ValueError):capture_designation_information(source,(spec,))

    def test_real_story_circle_entry_choice_and_paid_source_choice_prevent_only_matching_source(self):
        s=self.session(813010);circle=self.add(s.engine,'Story Circle',zone='hand');bolt=self.add(s.engine,'Lightning Bolt',zone='hand')
        blue=self.add(s.engine,'Air Elemental',seat='B');private=self.add(s.engine,'Generic Bound Growth',seat='C',zone='hand')
        self.prepare(s,mana={'C':1,'W':3,'R':1});self.checkpoint(s);self.normal(s,circle,{'C':1,'W':2});s=self.choose_entry(s,'R');self.assertIsNone(self.settle(s))
        self.assertEqual('R',s.state.cards[circle.object_id].annotations['chosen_color']);self.normal(s,bolt,{'R':1},targets=['A']);self.activate(s,circle,payment={'W':1})
        s=self.choose_source(s,'Lightning Bolt',excluded=(blue.ref,));self.assertEqual(40,s.state.players['A'].life);self.assertEqual([],s.state.damage_prevention_shields);self.private_replay(s,private)

    def test_real_solace_chosen_type_uses_creature_source_and_foreign_principal_correctly(self):
        s=self.session(813020);circle=self.add(s.engine,'Circle of Solace',zone='hand');goblin=self.add(s.engine,'Goblin Fireslinger',seat='B')
        human=self.add(s.engine,'Prodigal Sorcerer',seat='B');private=self.add(s.engine,'Generic Bound Growth',seat='C',zone='hand')
        haste=[self.add(s.engine,'Expedite',zone='hand',ref='solace-haste-'+str(i)) for i in range(2)]
        self.prepare(s,mana={'C':4,'W':2,'R':2});self.checkpoint(s);self.normal(s,circle,{'C':3,'W':1});s=self.choose_entry(s,'goblin');self.assertIsNone(self.settle(s))
        for spell,source in zip(haste,(human,goblin)):
            self.normal(s,spell,{'R':1},targets=[source.ref]);self.assertIsNone(self.settle(s))
        self.activate(s,circle,payment={'C':1,'W':1});self.assertIsNone(self.settle(s));self.assertEqual(1,len(s.state.damage_prevention_shields))
        for source in (human,goblin):
            self.priority(s,'B');rows=s.packet('pilot:B',full=True)['decision']['ctx']['legal']['actions']
            a=next(a for a in rows if a['id'].startswith('activate:'+source.ref+':'))
            command={'action_id':a['id'],'targets':['A'],'pay':'manual','payment':{}};self.reject(s,command,'pilot:D');r=s.act('pilot:B',command);self.assertTrue(r.ok,r.summary)
            s=self.replay(s,load=True);self.assertIsNone(self.settle(s));self.assertEqual(39,s.state.players['A'].life)
            self.assertEqual(1 if source.object_id==human.object_id else 0,len(s.state.damage_prevention_shields))
        self.private_replay(s,private)

    def test_real_story_circle_reentry_choice_cannot_replace_old_activation_designation(self):
        s=self.session(813030);circle=self.add(s.engine,'Story Circle',zone='hand');bolt=self.add(s.engine,'Lightning Bolt',zone='hand')
        blink=self.add(s.engine,'Flicker of Fate',zone='hand');blue=self.add(s.engine,'Air Elemental',seat='B');private=self.add(s.engine,'Generic Bound Growth',seat='C',zone='hand')
        self.prepare(s,mana={'C':2,'W':4,'R':1});self.checkpoint(s);self.normal(s,circle,{'C':1,'W':2});s=self.choose_entry(s,'R');self.assertIsNone(self.settle(s))
        old=s.state.cards[circle.object_id].logical_object_id;self.normal(s,bolt,{'R':1},targets=['A']);self.activate(s,circle,payment={'W':1})
        self.normal(s,blink,{'C':1,'W':1},targets=[circle.ref]);s=self.choose_entry(s,'U')
        self.assertNotEqual(old,s.state.cards[circle.object_id].logical_object_id);self.assertEqual('U',s.state.cards[circle.object_id].annotations['chosen_color'])
        s=self.choose_source(s,'Lightning Bolt',excluded=(blue.ref,));self.assertEqual(40,s.state.players['A'].life);self.private_replay(s,private)

    def test_real_no_entry_choice_pays_cost_without_creating_wildcard_or_source_prompt(self):
        s=self.session(813040);circle=self.add(s.engine,'Story Circle');bolt=self.add(s.engine,'Lightning Bolt',zone='hand')
        private=self.add(s.engine,'Generic Bound Growth',seat='C',zone='hand');self.prepare(s,mana={'W':1,'R':1});self.checkpoint(s)
        self.normal(s,bolt,{'R':1},targets=['A']);self.activate(s,circle,payment={'W':1});s=self.replay(s,load=True);self.assertIsNone(self.settle(s))
        self.assertEqual(37,s.state.players['A'].life);self.assertEqual([],s.state.damage_prevention_shields);self.assertEqual(0,s.state.players['A'].mana_pool['W']);self.private_replay(s,private)

    def test_real_prismatic_cumulative_upkeep_and_retained_color_remain_separate(self):
        s=self.session(813050);self.clear_hands(s);circle=self.add(s.engine,'Prismatic Circle',zone='hand');bolt=self.add(s.engine,'Lightning Bolt',zone='hand')
        ring=self.add(s.engine,'Sol Ring');self.add(s.engine,'Mountain');private=self.hand(s,'C',1)[0]
        self.prepare(s,mana={'C':2,'W':1});self.checkpoint(s);self.normal(s,circle,{'C':2,'W':1});s=self.choose_entry(s,'R');self.assertIsNone(self.settle(s));initial=s.state.turn_sequence
        for _ in range(160):
            s.engine.pump()
            if s.state.active_player=='A' and s.state.step=='upkeep' and s.state.turn_sequence>initial:break
            principal=s.pending_principals()[0];kind=s.state.pending_decision.kind
            if kind=='priority':command={'action_id':'pass'}
            elif kind=='combat.attackers':command={'action_id':'attack','atk':{}}
            elif kind=='combat.blockers':command={'action_id':'block','blocks':{}}
            elif kind=='trigger.order':command={'action_id':'order','triggers':[r['id'] for r in s.packet(principal,full=True)['decision']['ctx']['triggers']]}
            else:self.fail('Unexpected Prismatic journey decision '+kind)
            r=s.act(principal,command);self.assertTrue(r.ok,r.summary)
        else:self.fail('The actual four-player Prismatic upkeep was not reached')
        self.activate(s,ring,needle='add');pending=self.settle(s);self.assertIsNotNone(pending);self.assertEqual(1,s.state.cards[circle.object_id].counters.get('age'))
        command={'action_id':'choose','pay':True,'plan':'KEEP_PERMANENT'};self.reject(s,command,'pilot:D');s=self.replay(s,load=True);r=s.act('pilot:A',command);self.assertTrue(r.ok,r.summary);self.assertIsNone(self.settle(s))
        self.assertEqual(1,s.state.players['A'].mana_pool['C']);option=next(o for o in self.options(s,bolt) if o['id']=='normal')
        command={'action_id':'cast:'+bolt.ref,'cost_option':option['id'],'pay':'auto','targets':['A']};self.reject(s,command,'pilot:D');r=s.act('pilot:A',command);self.assertTrue(r.ok,r.summary)
        self.activate(s,circle,payment={'C':1});s=self.choose_source(s,'Lightning Bolt');self.assertEqual(40,s.state.players['A'].life);self.private_replay(s,private)

    def test_verified_withdrawal_and_fresh_wrong_choice_and_wildcard_mutants_reject(self):
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from scripts.update_capability_evidence import build_index,discover_tests
        from quorune.oracle_ir import compile_oracle_card
        raw=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding='utf-8'));decl=read_evidence_document(ROOT/'platform/capability-evidence-declarations.json')
        for cap in (CAP,BASE,ENTRY):
            changed=deepcopy(raw);next(c for c in changed['capabilities'] if c['id']==cap).update(status='blocked',blockers=['withdrawn source designation producer'],dependency_fail_closed_status='not_run')
            registry=CapabilityRegistry(changed);index=build_index(registry_value=changed,declaration_source=decl,discovered_test_ids=set(discover_tests(ROOT).values()));registry.mark_evidence_verified(validate_capability_evidence_index(index,registry=registry))
            ir=compile_oracle_card(self.db.lookup('Story Circle'),capability_registry=registry,capability_profile='commander_review');blockers=[b for r in ir.material_residuals for b in r.blockers]
            self.assertTrue(any('status:'+cap+':blocked' in b for b in blockers),blockers);self.assertFalse(any('evidence_index:unverified' in b for b in blockers))
        from quorune import designation_prevention as owner
        from quorune.object_query import ObjectQuerySpec
        from quorune.damage_source import REPRESENTED_DAMAGE_SOURCE_ZONES
        method='test_real_story_circle_entry_choice_and_paid_source_choice_prevent_only_matching_source'
        wildcard=ObjectQuerySpec(zones=REPRESENTED_DAMAGE_SOURCE_ZONES,known_to_actor=True).to_dict()
        with patch.object(owner,'resolve_designation_predicate',return_value=wildcard):
            with self.assertRaises(AssertionError):getattr(type(self)(method),method)()
        original=owner.capture_designation_information
        def wrong(source,effects):
            info=original(source,effects)
            if info is not None and 'color' in info['values']:info['values']['color']='U'
            return info
        with patch.object(owner,'capture_designation_information',side_effect=wrong):
            with self.assertRaises(AssertionError):getattr(type(self)(method),method)()


def replace_namespace(item,field,info):
    return SimpleNamespace(source_object_id=item.source_object_id,card_object_id=item.card_object_id,
        context={**item.context,field:info})
