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
import test_finite_prevention_recipients as controls
from quorune.rules.evidence_storage import read_evidence_document

CAP='damage.prevention.attached_recipient'
INFO='object.reference.source_attachment_information'
BASE='damage.prevention.persistent_amount'
NAMES=('Fylgja','Kithkin Armor')


class AttachedPreventionTests(unittest.TestCase):
    register_fixture_cards_on_add=True
    session=controls.FinitePreventionRecipientTests.session
    add=controls.FinitePreventionRecipientTests.add
    prepare=controls.FinitePreventionRecipientTests.prepare
    checkpoint=controls.FinitePreventionRecipientTests.checkpoint
    resolve=controls.FinitePreventionRecipientTests.resolve
    replay=controls.FinitePreventionRecipientTests.replay
    binding=controls.FinitePreventionRecipientTests.binding
    actions=controls.FinitePreventionRecipientTests.actions
    reject=controls.FinitePreventionRecipientTests.reject
    private_replay=controls.FinitePreventionRecipientTests.private_replay
    priority=controls.FinitePreventionRecipientTests.priority
    options=controls.FinitePreventionRecipientTests.options
    use=controls.FinitePreventionRecipientTests.use
    top=controls.FinitePreventionRecipientTests.top
    settle=controls.FinitePreventionRecipientTests.settle
    normal=controls.FinitePreventionRecipientTests.normal
    advance=controls.FinitePreventionRecipientTests.advance
    clear_hands=controls.FinitePreventionRecipientTests.clear_hands
    hand=controls.FinitePreventionRecipientTests.hand
    activate=controls.FinitePreventionRecipientTests.activate

    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory();path=Path(cls.temp.name)/'attached-prevention.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',ROOT/'tests/fixtures/attached-prevention-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Bound attachment prevention',[DeckEntry('Generic Bound Commander',1,'commander'),DeckEntry('Generic Bound Plains',30)],['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls):cls.db.close();cls.temp.cleanup()

    def choose_source(self,s,name):
        pending=self.settle(s);self.assertIsNotNone(pending)
        ctx=s.packet('pilot:A',full=True)['decision']['ctx'];self.assertEqual('choose_damage_source',ctx['operation'])
        matches=[r for r in ctx['objects'] if r['name']==name];self.assertEqual(1,len(matches))
        command={'action_id':'choose','source':matches[0]['id']};self.reject(s,command,'pilot:D');s=self.replay(s,load=True)
        result=s.act('pilot:A',command);self.assertTrue(result.ok,result.summary);self.assertIsNone(self.settle(s));return s

    def test_originals_admit_with_independent_bound_recipient_and_information_minima(self):
        from quorune.oracle_ir import compile_oracle_card
        from quorune.card_programs.binding import bind_semantic_program_runtime
        s=self.session(812000)
        for name in NAMES:
            card=self.add(s.engine,name,zone='hand');b=self.binding(s,card);self.assertTrue(b['strict_capability_ready'],(name,b['blockers']))
            ir=compile_oracle_card(self.db.lookup(name),capability_registry=self.registry,capability_profile='commander_review');self.assertEqual('exact',ir.status)
            p=next((p for p in s.engine.semantics.programs_for_oracle(card.oracle_id) if CAP in p.capability_dependencies and p.effects),None);self.assertIsNotNone(p)
            for cap in (CAP,INFO):
                deps=tuple(c for c in p.capability_dependencies if c!=cap)
                changed=replace(p,capability_dependencies=deps,capability_closure=self.registry.closure(deps,profile='commander_review').to_dict())
                b=bind_semantic_program_runtime(changed,capability_registry=self.registry,profile='commander_review')
                self.assertFalse(b['strict'],(name,cap));self.assertIn('capability:undeclared_runtime_dependency:'+cap,b['blockers'])

    def test_closed_binding_versions_and_source_relation_proof_preserve_legacy(self):
        from quorune.attachment_references import AttachmentReferenceSpec,AttachmentReferenceKind as K,attachment_information_capabilities
        from quorune.attached_prevention_model import attached_prevention_dependencies,BOUND_SUBJECT_VERSION
        from quorune.compiler.prevention_templates import fixed_prevention_effect_template
        text='Prevent the next 1 damage that would be dealt to enchanted creature this turn.'
        self.assertIsNone(fixed_prevention_effect_template(text,card_name='Generic Source'))
        self.assertIsNone(fixed_prevention_effect_template(text,card_name='Generic Source',source_attachment_relation=K.EQUIPPED))
        row=fixed_prevention_effect_template(text,card_name='Generic Source',source_attachment_relation=K.ENCHANTED);self.assertIsNotNone(row)
        effect=row[1][0];self.assertEqual(2,effect['subject']['schema_version']);self.assertEqual({CAP,INFO},set(attached_prevention_dependencies(effect)))
        for bad in ({**effect,BOUND_SUBJECT_VERSION:True},{**effect,BOUND_SUBJECT_VERSION:2},{k:v for k,v in effect.items() if k!=BOUND_SUBJECT_VERSION},{**effect,'amount':False}):
            with self.assertRaises(ValueError):attached_prevention_dependencies(bad)
        old=AttachmentReferenceSpec(K.ENCHANTED,'creature').to_dict();self.assertEqual(1,old['schema_version'])
        self.assertEqual(old,AttachmentReferenceSpec.from_dict(json.loads(json.dumps(old))).to_dict());self.assertEqual((),attachment_information_capabilities(old))

    def test_real_fylgja_pays_healing_before_shield_and_keeps_counter_sibling(self):
        s=self.session(812010);aura=self.add(s.engine,'Fylgja',zone='hand');body=self.add(s.engine,'Generic Bound Body',seat='B')
        bolt=self.add(s.engine,'Lightning Bolt',zone='hand');private=self.add(s.engine,'Generic Bound Growth',seat='C',zone='hand')
        self.prepare(s,mana={'W':2,'C':2,'R':1});self.checkpoint(s);self.normal(s,aura,{'W':1},targets=[body.ref]);self.assertIsNone(self.settle(s))
        self.assertEqual(4,s.state.cards[aura.object_id].counters.get('healing'));self.normal(s,bolt,{'R':1},targets=[body.ref])
        self.activate(s,aura,needle='prevent');self.assertEqual(3,s.state.cards[aura.object_id].counters.get('healing'))
        s=self.replay(s,load=True);self.assertIsNone(self.settle(s));self.assertEqual(2,s.state.cards[body.object_id].marked_damage)
        self.activate(s,aura,needle='put',payment={'C':2,'W':1});self.assertIsNone(self.settle(s));self.assertEqual(4,s.state.cards[aura.object_id].counters.get('healing'));self.private_replay(s,private)

    def test_real_fylgja_healing_price_cannot_be_paid_a_fifth_time(self):
        s=self.session(812020);aura=self.add(s.engine,'Fylgja',zone='hand');body=self.add(s.engine,'Generic Bound Body')
        private=self.add(s.engine,'Generic Bound Growth',seat='C',zone='hand');self.prepare(s,mana={'W':1});self.checkpoint(s)
        self.normal(s,aura,{'W':1},targets=[body.ref]);self.assertIsNone(self.settle(s));past=None
        for remaining in (3,2,1,0):
            past=next(a['id'] for a in self.actions(s) if a['id'].startswith('activate:'+aura.ref+':') and 'prevent' in json.dumps(a).casefold())
            self.activate(s,aura,needle='prevent');self.assertEqual(remaining,s.state.cards[aura.object_id].counters.get('healing',0));self.assertIsNone(self.settle(s))
        self.assertFalse(any(a['id']==past for a in self.actions(s)));self.reject(s,{'action_id':past,'pay':'manual','payment':{}},'pilot:A')
        self.assertEqual(4,len(s.state.damage_prevention_shields));self.private_replay(s,private)

    def test_real_kithkin_block_rule_and_sacrificed_source_choice_use_old_attachment(self):
        s=self.session(812030);aura=self.add(s.engine,'Kithkin Armor',zone='hand');body=self.add(s.engine,'Generic Bound Body')
        blocker=self.add(s.engine,'Air Elemental',seat='B');mountain=self.add(s.engine,'Mountain');bolt=self.add(s.engine,'Lightning Bolt',zone='hand')
        private=self.add(s.engine,'Generic Bound Growth',seat='C',zone='hand');self.prepare(s,mana={'W':1});self.checkpoint(s)
        self.normal(s,aura,{'W':1},targets=[body.ref]);self.assertIsNone(self.settle(s))
        self.advance(s,lambda x:getattr(x.state.pending_decision,'kind',None)=='combat.attackers')
        command={'action_id':'attack','atk':{body.ref:'B'}};self.reject(s,command,'pilot:D');r=s.act('pilot:A',command);self.assertTrue(r.ok,r.summary)
        self.assertTrue(s.state.combat.attackers,'The actual attacker declaration was empty')
        def post_blocks(x):
            self.assertNotEqual('combat.blockers',getattr(x.state.pending_decision,'kind',None),
                'The prohibited four-power blocker was offered')
            return x.state.step=='declare_blockers' and x.state.combat.blockers_declared
        self.advance(s,post_blocks);self.priority(s,'A')
        option=next(o for o in self.options(s,bolt) if o['id']=='normal')
        command={'action_id':'cast:'+bolt.ref,'cost_option':option['id'],'pay':'auto','targets':[body.ref]}
        self.reject(s,command,'pilot:D');r=s.act('pilot:A',command);self.assertTrue(r.ok,r.summary);self.activate(s,aura,needle='prevent')
        self.assertEqual('graveyard',s.state.cards[aura.object_id].zone);s=self.choose_source(s,'Lightning Bolt')
        self.assertEqual(0,s.state.cards[body.object_id].marked_damage);self.advance(s,lambda x:x.state.players['B'].life<40)
        self.assertEqual(38,s.state.players['B'].life);self.private_replay(s,private)

    def test_real_unavailable_attached_recipient_never_shields_controller(self):
        for i,name in enumerate(NAMES):
            s=self.session(812040+i);aura=self.add(s.engine,name,zone='hand');body=self.add(s.engine,'Generic Bound Body')
            kill=self.add(s.engine,'Go for the Throat',zone='hand');bolt=self.add(s.engine,'Lightning Bolt',zone='hand')
            private=self.add(s.engine,'Generic Bound Growth',seat='C',zone='hand');self.prepare(s,mana={'W':1,'C':1,'B':1,'R':1});self.checkpoint(s)
            self.normal(s,aura,{'W':1},targets=[body.ref]);self.assertIsNone(self.settle(s));self.activate(s,aura,needle='prevent')
            if name=='Fylgja':self.assertEqual(3,s.state.cards[aura.object_id].counters.get('healing'))
            else:self.assertEqual('graveyard',s.state.cards[aura.object_id].zone)
            self.normal(s,kill,{'C':1,'B':1},targets=[body.ref]);s=self.replay(s,load=True);self.assertIsNone(self.settle(s))
            self.assertEqual('graveyard',s.state.cards[body.object_id].zone);self.assertEqual([],s.state.damage_prevention_shields)
            self.normal(s,bolt,{'R':1},targets=['A']);self.assertIsNone(self.settle(s));self.assertEqual(37,s.state.players['A'].life);self.private_replay(s,private)

    def test_real_source_destroyed_after_activation_retains_last_known_creature(self):
        s=self.session(812050);aura=self.add(s.engine,'Fylgja',zone='hand');body=self.add(s.engine,'Generic Bound Body',seat='B')
        destroy=self.add(s.engine,'Disenchant',zone='hand');bolt=self.add(s.engine,'Lightning Bolt',zone='hand');private=self.add(s.engine,'Generic Bound Growth',seat='C',zone='hand')
        self.prepare(s,mana={'W':2,'C':1,'R':1});self.checkpoint(s);self.normal(s,aura,{'W':1},targets=[body.ref]);self.assertIsNone(self.settle(s))
        self.normal(s,bolt,{'R':1},targets=[body.ref]);self.activate(s,aura,needle='prevent');self.normal(s,destroy,{'C':1,'W':1},targets=[aura.ref])
        s=self.replay(s,load=True);self.assertIsNone(self.settle(s));self.assertEqual('graveyard',s.state.cards[aura.object_id].zone)
        self.assertEqual(2,s.state.cards[body.object_id].marked_damage);self.private_replay(s,private)

    def test_departure_information_tracks_moved_attachment_and_keeps_legacy_snapshot(self):
        from quorune.attachments import attach_objects
        from quorune.attachment_references import AttachmentReferenceSpec,AttachmentReferenceKind as K,ATTACHMENT_INFORMATION_VERSION
        from quorune.semantic_runtime.values import resolve_semantic_value
        s=self.session(812060);aura=self.add(s.engine,'Fylgja');first=self.add(s.engine,'Generic Bound Body',ref='first-bound-body');second=self.add(s.engine,'Generic Bound Body',ref='second-bound-body')
        aura.counters['healing']=4;attach_objects(s.state.cards,aura,first,source_timestamp=1);self.prepare(s);self.activate(s,aura,needle='prevent')
        item=s.state.stack[-1];self.assertEqual(1,item.context[ATTACHMENT_INFORMATION_VERSION]);legacy=deepcopy(item.context);legacy.pop(ATTACHMENT_INFORMATION_VERSION)
        attach_objects(s.state.cards,s.state.cards[aura.object_id],second,source_timestamp=2)
        ref=AttachmentReferenceSpec(K.ENCHANTED,'creature',schema_version=2).to_dict()
        self.assertEqual(second.ref,resolve_semantic_value(s.engine,ref,item));s.engine.move_card(aura.object_id,'graveyard',reason='owner-level last-known test')
        self.assertEqual(second.ref,resolve_semantic_value(s.engine,ref,item))
        old=replace(item,context=legacy);oldref={**ref,'schema_version':1};self.assertEqual(first.ref,resolve_semantic_value(s.engine,oldref,old))
        with self.assertRaises(ValueError):resolve_semantic_value(s.engine,ref,old)
        s.engine.move_card(second.object_id,'hand',log=False);s.engine.move_card(second.object_id,'battlefield',controller='A',log=False)
        self.assertIsNone(resolve_semantic_value(s.engine,ref,item))

    def test_verified_withdrawal_and_fresh_information_and_missing_subject_mutants_reject(self):
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from scripts.update_capability_evidence import build_index,discover_tests
        from quorune.oracle_ir import compile_oracle_card
        raw=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text());decl=read_evidence_document(ROOT/'platform/capability-evidence-declarations.json')
        for cap in (CAP,INFO,BASE):
            changed=deepcopy(raw);next(c for c in changed['capabilities'] if c['id']==cap).update(status='blocked',blockers=['withdrawn attachment information'],dependency_fail_closed_status='not_run')
            registry=CapabilityRegistry(changed);index=build_index(registry_value=changed,declaration_source=decl,discovered_test_ids=set(discover_tests(ROOT).values()));registry.mark_evidence_verified(validate_capability_evidence_index(index,registry=registry))
            ir=compile_oracle_card(self.db.lookup('Kithkin Armor'),capability_registry=registry,capability_profile='commander_review');blockers=[b for r in ir.material_residuals for b in r.blockers]
            self.assertTrue(any('status:'+cap+':blocked' in b for b in blockers),blockers);self.assertFalse(any('evidence_index:unverified' in b for b in blockers))
        from quorune import attachment_information as info_owner
        method='test_departure_information_tracks_moved_attachment_and_keeps_legacy_snapshot'
        with patch.object(info_owner,'pin_attachment_information_departures',return_value=None):
            with self.assertRaises(AssertionError):getattr(type(self)(method),method)()
        from quorune.effect_runtime import damage_modifiers as owner
        method='test_real_unavailable_attached_recipient_never_shields_controller'
        with patch.object(owner,'_bound_prevention_subject_is_unavailable',return_value=False):
            with self.assertRaises(AssertionError):getattr(type(self)(method),method)()
