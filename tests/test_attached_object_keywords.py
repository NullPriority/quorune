from __future__ import annotations

"""CR611.3/702: an attached keyword applies to its actual permanent subject."""

from pathlib import Path
from dataclasses import replace
from copy import deepcopy
import json
import re
import tempfile
import unittest
from unittest.mock import patch
from common import ROOT
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition,DeckEntry
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
import test_classic_flash_casts as controls
from quorune.rules.evidence_storage import read_evidence_document


class AttachedObjectKeywordTests(unittest.TestCase):
    register_fixture_cards_on_add=True
    session=controls.ClassicFlashCastTests.session
    add=controls.ClassicFlashCastTests.add
    prepare=controls.ClassicFlashCastTests.prepare
    checkpoint=controls.ClassicFlashCastTests.checkpoint
    resolve=controls.ClassicFlashCastTests.resolve
    replay=controls.ClassicFlashCastTests.replay
    binding=controls.ClassicFlashCastTests.binding
    priority=controls.ClassicFlashCastTests.priority
    cast=controls.ClassicFlashCastTests.cast
    offered_cast=controls.ClassicFlashCastTests.offered_cast
    top=controls.ClassicFlashCastTests.top
    private_replay=controls.ClassicFlashCastTests.private_replay
    advance=controls.ClassicFlashCastTests.advance
    offered=controls.ClassicFlashCastTests.offered
    next_cleanup_trigger=controls.ClassicFlashCastTests.next_cleanup_trigger

    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory();path=Path(cls.temp.name)/'attached-keywords.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',ROOT/'tests/fixtures/attached-protection-cards.json',
            ROOT/'tests/fixtures/classic-flash-cards.json',ROOT/'tests/fixtures/attached-object-keyword-cards.json',
            ROOT/'tests/fixtures/fixed-mana-result-cards.json',ROOT/'tests/fixtures/attached-pt-keyword-removal-cards.json',
            ROOT/'tests/fixtures/regeneration-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Attached object keywords',[DeckEntry('Generic Bound Commander',1,'commander'),DeckEntry('Generic Bound Plains',30)],['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls):cls.db.close();cls.temp.cleanup()

    def keywords(self,session,card):
        return {str(k).casefold() for k in session.engine._effective_card_data(session.state.cards[card.object_id]).get('keywords',())}

    def test_granted_keyword_owners_are_independent_runtime_requirements(self):
        from quorune.card_programs.binding import bind_semantic_program_runtime
        for name,owner in (('Relic Ward','target.protection.shroud_permanent'),
                ('Indestructibility','permanent.indestructible.ordinary'),('Hardlight Containment','trigger.keyword.ward.fixed_generic')):
            session=self.session(751603);source=self.add(session.engine,name,zone='hand')
            programs=[p for p in session.engine.semantics.programs_for_oracle(source.oracle_id)
                if any(d.get('handler_id')=='continuous.attached.fixed-characteristics.v1' for d in p.handlers)]
            self.assertEqual(1,len(programs));program=programs[0]
            self.assertTrue(bind_semantic_program_runtime(program,capability_registry=self.registry,profile='commander_review')['strict'])
            for dependency in ('continuous.attached.fixed_characteristics',owner):
                self.assertIn(dependency,program.capability_dependencies)
                deps=tuple(c for c in program.capability_dependencies if c!=dependency)
                stripped=replace(program,capability_dependencies=deps,capability_closure=self.registry.closure(deps,profile='commander_review').to_dict() if deps else None)
                bound=bind_semantic_program_runtime(stripped,capability_registry=self.registry,profile='commander_review')
                self.assertFalse(bound['strict'],(name,dependency));self.assertIn('capability:undeclared_runtime_dependency:'+dependency,bound['blockers'])

    def test_actual_relic_ward_protects_noncreature_artifact_but_keeps_mana_ability(self):
        session=self.session(751600);source=self.add(session.engine,'Relic Ward',zone='hand')
        ring=self.add(session.engine,'Sol Ring',seat='B')
        remove=self.add(session.engine,'Boomerang',seat='B',zone='hand',ref='remove-relic-ward')
        probe=self.add(session.engine,'Boomerang',seat='B',zone='hand',ref='probe-relic-target')
        private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand')
        self.assertTrue(self.binding(session,source)['strict_capability_ready'],'Complete original Relic Ward must admit')
        self.prepare(session,mana={'W':1,'C':1});session.state.players['B'].mana_pool.update({'U':4});self.checkpoint(session)
        self.offered_cast(session,source,{'W':1,'C':1},[ring.ref]);session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
        self.assertIn('shroud',self.keywords(session,ring));self.priority(session,'B')
        actions=self.offered(session,'B');matches=[a for a in actions if a['id']=='cast:'+probe.ref];self.assertEqual(1,len(matches))
        self.assertNotIn(ring.ref,matches[0]['target_schema']['legal_refs'])
        before=authoritative_state_hash(session.state)
        self.assertFalse(session.act('pilot:B',{'action_id':'cast:'+probe.ref,'targets':[ring.ref],'pay':'manual','payment':{'U':2}}).ok)
        self.assertEqual(before,authoritative_state_hash(session.state))
        mana=[a for a in actions if a['id'].startswith('activate:'+ring.ref+':')];self.assertEqual(1,len(mana))
        result=session.act('pilot:B',{'action_id':mana[0]['id']});self.assertTrue(result.ok,result.summary)
        self.assertTrue(session.state.cards[ring.object_id].tapped)
        self.cast(session,'B',remove,[source.ref],{'U':2});self.assertIsNone(self.resolve(session))
        self.assertNotIn('shroud',self.keywords(session,ring));self.assertEqual('hand',session.state.cards[source.object_id].zone)
        self.cast(session,'B',probe,[ring.ref],{'U':2});self.assertIsNone(self.resolve(session))
        self.assertEqual('hand',session.state.cards[ring.object_id].zone);self.private_replay(session,private)

    def test_actual_indestructibility_keeps_destroyed_artifact_and_exile_remains_legal(self):
        session=self.session(751601);source=self.add(session.engine,'Indestructibility',zone='hand')
        ring=self.add(session.engine,'Sol Ring');destroy=self.add(session.engine,'Naturalize',seat='B',zone='hand')
        exile=self.add(session.engine,'Revoke Existence',zone='hand');private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand')
        self.assertTrue(self.binding(session,source)['strict_capability_ready'],'Complete original Indestructibility must admit')
        self.prepare(session,mana={'W':2,'C':4});session.state.players['B'].mana_pool.update({'G':1,'C':1});self.checkpoint(session)
        self.offered_cast(session,source,{'W':1,'C':3},[ring.ref]);session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
        self.assertIn('indestructible',self.keywords(session,ring))
        self.cast(session,'B',destroy,[ring.ref],{'G':1,'C':1});self.assertIsNone(self.resolve(session))
        self.assertEqual('battlefield',session.state.cards[ring.object_id].zone);self.assertEqual('battlefield',session.state.cards[source.object_id].zone)
        self.assertEqual('graveyard',session.state.cards[destroy.object_id].zone)
        self.offered_cast(session,exile,{'W':1,'C':1},[ring.ref]);session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
        self.assertEqual('exile',session.state.cards[ring.object_id].zone);self.assertEqual('graveyard',session.state.cards[source.object_id].zone)
        self.private_replay(session,private)

    def test_actual_hardlight_keeps_entry_exile_and_ward_on_its_noncreature_artifact(self):
        session=self.session(751602);source=self.add(session.engine,'Hardlight Containment',zone='hand')
        ring=self.add(session.engine,'Sol Ring');foreign=self.add(session.engine,'Sol Ring',seat='B',ref='foreign-hardlight-artifact')
        captive=self.add(session.engine,'Grizzly Bears',seat='B')
        targeter=self.add(session.engine,'Boomerang',seat='B',zone='hand',ref='target-hardlight-artifact')
        remove=self.add(session.engine,'Boomerang',seat='B',zone='hand',ref='remove-hardlight-aura')
        private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand')
        self.assertTrue(self.binding(session,source)['strict_capability_ready'],'Complete original Hardlight Containment must admit')
        self.prepare(session,mana={'W':1});session.state.players['B'].mana_pool.update({'U':4});self.checkpoint(session)
        before=authoritative_state_hash(session.state)
        self.assertFalse(session.act('pilot:A',{'action_id':'cast:'+source.ref,'targets':[foreign.ref],'pay':'manual','payment':{'W':1}}).ok)
        self.assertEqual(before,authoritative_state_hash(session.state))
        self.offered_cast(session,source,{'W':1},[ring.ref]);decision=self.resolve(session)
        self.assertIsNotNone(decision);self.assertEqual('semantic.target',decision.kind)
        session=self.replay(session,load=True);result=session.act('pilot:A',{'action_id':'choose','targets':[captive.ref]});self.assertTrue(result.ok,result.summary)
        self.assertIsNone(self.resolve(session));self.assertEqual('exile',session.state.cards[captive.object_id].zone)
        self.cast(session,'B',targeter,[ring.ref],{'U':2});decision=self.resolve(session)
        self.assertIsNotNone(decision);self.assertEqual('semantic.choice',decision.kind);self.assertEqual(['pilot:B'],session.pending_principals())
        session=self.replay(session,load=True);result=session.act('pilot:B',{'action_id':'choose','pay':False});self.assertTrue(result.ok,result.summary)
        self.assertIsNone(self.resolve(session));self.assertEqual('battlefield',session.state.cards[ring.object_id].zone)
        self.assertEqual('graveyard',session.state.cards[targeter.object_id].zone)
        self.cast(session,'B',remove,[source.ref],{'U':2});self.assertIsNone(self.resolve(session))
        self.assertEqual('battlefield',session.state.cards[captive.object_id].zone);self.assertEqual('hand',session.state.cards[source.object_id].zone)
        self.assertFalse(any(r.get('kind')=='ward' for r in session.engine._effective_card_data(session.state.cards[ring.object_id]).get('ability_fragments',())))
        self.private_replay(session,private)

    def test_relic_intrinsic_cleanup_sacrifices_aura_and_ends_artifact_shroud(self):
        session=self.session(751604);source=self.add(session.engine,'Relic Ward',zone='hand')
        ring=self.add(session.engine,'Sol Ring',seat='B');private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand')
        self.prepare(session,actor='B',active='B');session.state.players['A'].mana_pool.update({'W':1,'C':1});self.checkpoint(session)
        self.offered_cast(session,source,{'W':1,'C':1},[ring.ref],timing_permission='intrinsic')
        session=self.replay(session,load=True);self.assertIsNone(self.resolve(session));self.assertIn('shroud',self.keywords(session,ring))
        self.next_cleanup_trigger(session,source);session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
        self.assertEqual('graveyard',session.state.cards[source.object_id].zone);self.assertEqual('battlefield',session.state.cards[ring.object_id].zone)
        self.assertNotIn('shroud',self.keywords(session,ring));self.private_replay(session,private)

    def test_indestructibility_accepts_land_and_creature_but_zero_toughness_still_dies(self):
        session=self.session(751605);source=self.add(session.engine,'Indestructibility',zone='hand')
        other=self.add(session.engine,'Indestructibility',zone='hand',ref='land-indestructibility')
        body=self.add(session.engine,'Grizzly Bears');land=self.add(session.engine,'Generic Bound Plains',ref='indestructible-land')
        shrink=self.add(session.engine,'Disfigure',seat='B',zone='hand');private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand')
        self.prepare(session,mana={'W':2,'C':6});session.state.players['B'].mana_pool.update({'B':1});self.checkpoint(session)
        self.offered_cast(session,source,{'W':1,'C':3},[body.ref]);self.assertIsNone(self.resolve(session))
        self.offered_cast(session,other,{'W':1,'C':3},[land.ref]);self.assertIsNone(self.resolve(session))
        self.assertIn('indestructible',self.keywords(session,body));self.assertIn('indestructible',self.keywords(session,land))
        self.cast(session,'B',shrink,[body.ref],{'B':1});session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
        self.assertEqual('graveyard',session.state.cards[body.object_id].zone);self.assertEqual('graveyard',session.state.cards[source.object_id].zone)
        self.assertEqual('battlefield',session.state.cards[land.object_id].zone);self.assertEqual('battlefield',session.state.cards[other.object_id].zone)
        self.private_replay(session,private)

    def test_subject_descriptors_are_closed_and_legacy_creature_shape_is_retained(self):
        from quorune.compiler.continuous_templates import attached_fixed_characteristics_handler
        from quorune.semantic_runtime.attached_continuous import AttachedFixedCharacteristicsHandler
        handler=AttachedFixedCharacteristicsHandler()
        for text,types in (('Enchanted artifact has shroud.',['artifact']),('Enchanted permanent has indestructible.',[]),
                ('Enchanted land has hexproof.',['land']),('Enchanted battle has ward {2}.',['battle']),
                ('Enchanted enchantment has vigilance.',['enchantment']),('Enchanted planeswalker has ward {1}.',['planeswalker']),
                ('Enchanted creature has flying.',['creature'])):
            compiled=attached_fixed_characteristics_handler(text);self.assertIsNotNone(compiled,text)
            descriptor=compiled[1];self.assertEqual(types,descriptor['condition']['types_all']);handler.validate(descriptor)
        for text in ('Enchanted player has shroud.','Enchanted artifact has an unrepresented ability.',
                'Enchanted artifact has "At the beginning of your upkeep, invent a rule."',
                'Enchanted artifact has shroud until end of turn.'):
            self.assertIsNone(attached_fixed_characteristics_handler(text),text)
        descriptor=attached_fixed_characteristics_handler('Enchanted artifact has shroud.')[1]
        for changed in ({**descriptor,'schema_version':True},{**descriptor,'condition':{**descriptor['condition'],'types_all':['player']}},
                {**descriptor,'condition':{**descriptor['condition'],'types_all':['artifact','artifact']}},
                {**descriptor,'condition':{**descriptor['condition'],'types_all':[True]}}):
            with self.assertRaises(ValueError):handler.validate(changed)

    def test_canonically_verified_grant_and_keyword_withdrawals_keep_originals_residual(self):
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from scripts.update_capability_evidence import build_index,discover_tests
        from quorune.oracle_ir import compile_oracle_card
        raw=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        declarations=read_evidence_document(ROOT/'platform/capability-evidence-declarations.json');known=set(discover_tests(ROOT).values())
        for cap,names in (('continuous.attached.fixed_characteristics',('Relic Ward','Indestructibility','Hardlight Containment')),
                ('target.protection.shroud_permanent',('Relic Ward',)),('permanent.indestructible.ordinary',('Indestructibility',)),
                ('trigger.keyword.ward.fixed_generic',('Hardlight Containment',))):
            changed=deepcopy(raw);next(r for r in changed['capabilities'] if r['id']==cap).update(status='blocked',blockers=['withdrawn attached keyword authority'])
            registry=CapabilityRegistry(changed);index=build_index(registry_value=changed,declaration_source=declarations,discovered_test_ids=known)
            registry.mark_evidence_verified(validate_capability_evidence_index(index,registry=registry))
            for name in names:
                ir=compile_oracle_card(self.db.lookup(name),capability_registry=registry,capability_profile='commander_review')
                self.assertNotEqual('exact',ir.status,name);blockers=[b for r in ir.material_residuals for b in r.blockers]
                self.assertTrue(any('status:'+cap+':blocked' in b for b in blockers),(name,blockers));self.assertFalse(any('evidence_index:unverified' in b for b in blockers))

    def test_full_original_producer_lowerer_and_keyword_requirement_omissions_are_killed(self):
        native='test_actual_relic_ward_protects_noncreature_artifact_but_keeps_mana_ability'
        cases=(('quorune.compiler.continuous_templates._ATTACHED_OBJECT_KEYWORD_GRANT',re.compile(r'(?!)'),native),
            ('quorune.semantic_runtime.attached_continuous.AttachedFixedCharacteristicsHandler.lower',lambda *a:(),native),
            ('quorune.rules.keyword_runtime_requirements.attached_keyword_runtime_dependencies',lambda *a:(),
             'test_granted_keyword_owners_are_independent_runtime_requirements'))
        for owner,omission,method in cases:
            with patch(owner,omission):
                with self.assertRaises(AssertionError):getattr(type(self)(method),method)()
