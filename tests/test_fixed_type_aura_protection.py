from __future__ import annotations

"""CR 702.16n and 601.2c: exemption is narrower than target legality."""

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from common import ROOT
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition,DeckEntry
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
import test_attached_protection_exceptions as controls
from quorune.rules.evidence_storage import read_evidence_document


class FixedTypeAuraProtectionTests(unittest.TestCase):
    register_fixture_cards_on_add=True
    session=controls.AttachedProtectionExceptionTests.session
    add=controls.AttachedProtectionExceptionTests.add
    prepare=controls.AttachedProtectionExceptionTests.prepare
    checkpoint=controls.AttachedProtectionExceptionTests.checkpoint
    resolve=controls.AttachedProtectionExceptionTests.resolve
    replay=controls.AttachedProtectionExceptionTests.replay
    binding=controls.AttachedProtectionExceptionTests.binding
    priority=controls.AttachedProtectionExceptionTests.priority
    cast=controls.AttachedProtectionExceptionTests.cast
    actions=controls.AttachedProtectionExceptionTests.actions
    private_replay=controls.AttachedProtectionExceptionTests.private_replay

    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory();path=Path(cls.temp.name)/'fixed-type-aura.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',ROOT/'tests/fixtures/attached-protection-cards.json',
                                ROOT/'tests/fixtures/fixed-type-aura-protection-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Fixed-type Aura',[DeckEntry('Generic Bound Commander',1,'commander'),DeckEntry('Generic Bound Plains',30)],['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls):cls.db.close();cls.temp.cleanup()

    def test_actual_tattoo_retains_itself_and_rejects_illegal_target_before_sacrifice(self):
        session=self.session(750500);body=self.add(session.engine,'Nyxborn Colossus')
        aura=self.add(session.engine,'Tattoo Ward',zone='hand')
        seal=self.add(session.engine,'Seal of Fire',seat='B')
        private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand')
        self.assertTrue(self.binding(session,aura)['strict_capability_ready'],'Complete original Tattoo Ward must admit')
        self.prepare(session,mana={'W':1,'C':2});self.checkpoint(session)
        self.cast(session,'A',aura,[body.ref],{'W':1,'C':2});session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
        self.assertEqual(('battlefield',body.object_id),(session.state.cards[aura.object_id].zone,session.state.cards[aura.object_id].attached_to))
        self.assertEqual((7,8),(session.engine._numeric_stat(body.object_id,'power'),session.engine._numeric_stat(body.object_id,'toughness')))
        self.priority(session,'A');action=next(r for r in self.actions(session) if r['id'].startswith('activate:'+aura.ref+':'))
        before=authoritative_state_hash(session.state)
        self.assertFalse(session.act('pilot:A',{'action_id':action['id'],'targets':[body.ref],'pay':'auto'}).ok)
        self.assertEqual(before,authoritative_state_hash(session.state));self.assertEqual('battlefield',session.state.cards[aura.object_id].zone)
        self.assertNotIn(body.ref,action['target_schema']['legal_refs'])
        self.priority(session,'B')
        seal_action=next(r for r in session.packet('pilot:B',full=True)['decision']['ctx']['legal']['actions'] if r['id'].startswith('activate:'+seal.ref+':'))
        self.assertNotIn(body.ref,seal_action['target_schema']['legal_refs'])
        before=authoritative_state_hash(session.state)
        self.assertFalse(session.act('pilot:B',{'action_id':seal_action['id'],'targets':[body.ref],'pay':'auto'}).ok)
        self.assertEqual(before,authoritative_state_hash(session.state));self.assertEqual('battlefield',session.state.cards[seal.object_id].zone)
        self.priority(session,'A');before=authoritative_state_hash(session.state)
        self.assertFalse(session.act('pilot:D',{'action_id':action['id'],'targets':[seal.ref],'pay':'auto'}).ok)
        self.assertEqual(before,authoritative_state_hash(session.state))
        result=session.act('pilot:A',{'action_id':action['id'],'targets':[seal.ref],'pay':'auto'});self.assertTrue(result.ok,result.summary)
        self.assertEqual('graveyard',session.state.cards[aura.object_id].zone)
        self.assertEqual((6,7),(session.engine._numeric_stat(body.object_id,'power'),session.engine._numeric_stat(body.object_id,'toughness')))
        session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
        self.assertEqual('graveyard',session.state.cards[seal.object_id].zone)
        self.private_replay(session,private)

    def test_actual_other_aura_is_removed_and_its_retained_return_trigger_resolves(self):
        session=self.session(750510);body=self.add(session.engine,'Grizzly Bears')
        other=self.add(session.engine,'Rancor',zone='hand');aura=self.add(session.engine,'Tattoo Ward',zone='hand')
        self.assertTrue(self.binding(session,other)['strict_capability_ready'])
        self.assertTrue(self.binding(session,aura)['strict_capability_ready'])
        self.prepare(session,mana={'G':1,'W':1,'C':2});self.checkpoint(session)
        self.cast(session,'A',other,[body.ref],{'G':1});self.assertIsNone(self.resolve(session))
        self.assertEqual((4,2),(session.engine._numeric_stat(body.object_id,'power'),session.engine._numeric_stat(body.object_id,'toughness')))
        self.cast(session,'A',aura,[body.ref],{'W':1,'C':2});session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
        self.assertEqual(('battlefield',body.object_id),(session.state.cards[aura.object_id].zone,session.state.cards[aura.object_id].attached_to))
        self.assertEqual('hand',session.state.cards[other.object_id].zone)
        self.assertEqual((3,3),(session.engine._numeric_stat(body.object_id,'power'),session.engine._numeric_stat(body.object_id,'toughness')))
        self.assertNotIn('trample',session.engine._combat_keywords(session.state.cards[body.object_id]));self.replay(session)

    def test_fixed_type_codec_definition_authority_and_nonmatching_boundaries(self):
        from dataclasses import replace
        from types import SimpleNamespace
        from quorune.ability_fragments import ProtectionSpec,ProtectionQualityKind,ability_fragment_to_dict,ability_fragment_from_dict
        from quorune.protection import ProtectionSource,ProtectionVerdict,protection_verdict
        from quorune.protection_attachment_exceptions import CAPABILITY,FIXED_TYPE_AURA_PROTECTION_CAPABILITY,attached_protection_runtime_dependencies
        from quorune.compiler.continuous_templates import attached_fixed_characteristics_handler
        old=ProtectionSpec(ProtectionQualityKind.CARD_TYPE,'enchantment')
        self.assertEqual({'schema_version':1,'quality_kind':'card_type','quality':'enchantment'},old.to_dict())
        spec=replace(old,schema_version=3,exempt_aura_incarnation='aura@1')
        self.assertEqual(spec,ability_fragment_from_dict(ability_fragment_to_dict(spec)))
        value={'keywords':['Protection'],'ability_fragments':(ability_fragment_to_dict(spec),)}
        matching=ProtectionSource(card_types=frozenset({'enchantment'}))
        self.assertIs(ProtectionVerdict.ALLOWED,protection_verdict(value,matching,retaining_aura='aura@1'))
        for incarnation in (None,'other@1','aura@2'):
            self.assertIs(ProtectionVerdict.BLOCKED,protection_verdict(value,matching,retaining_aura=incarnation))
        self.assertIs(ProtectionVerdict.ALLOWED,protection_verdict(value,ProtectionSource(card_types=frozenset({'artifact'}))))
        self.assertIs(ProtectionVerdict.BLOCKED,protection_verdict({**value,'ability_fragments':tuple(ability_fragment_to_dict(v) for v in (spec,old))},matching,retaining_aura='aura@1'))
        raw=ability_fragment_to_dict(replace(spec,exempt_aura_incarnation='$source'))
        handler={'handler_id':'continuous.attached.fixed-characteristics.v1','modifier':{'add_ability_fragments':[raw]}}
        self.assertEqual({CAPABILITY,FIXED_TYPE_AURA_PROTECTION_CAPABILITY},attached_protection_runtime_dependencies(SimpleNamespace(handlers=(handler,))))
        for program in (SimpleNamespace(effects=({'nested':[raw]},)),SimpleNamespace(handlers=({**handler,'handler_id':'ability.static.protection.v1'},)),
                        SimpleNamespace(handlers=({**handler,'modifier':{'add_ability_fragments':[ability_fragment_to_dict(spec)]}},))):
            with self.assertRaises(ValueError):attached_protection_runtime_dependencies(program)
        text="Enchanted creature gets +1/+1 and has protection from enchantments. This effect doesn't remove this Aura."
        compiled=attached_fixed_characteristics_handler(text);self.assertIsNotNone(compiled)
        self.assertIn(FIXED_TYPE_AURA_PROTECTION_CAPABILITY,compiled[2])
        for bad in (text.replace('enchantments','the chosen card type'),text.replace('enchantments','everything'),
                    text.replace('this Aura','Auras'),text.replace('Enchanted creature','Equipped creature')):
            self.assertIsNone(attached_fixed_characteristics_handler(bad))
        for change in ({'quality_kind':'everything'},{'exempt_aura_incarnation':None},{'unknown':0}):
            with self.assertRaises((ValueError,TypeError)):ProtectionSpec.from_dict({**spec.to_dict(),**change})

    def test_canonically_verified_fixed_type_owner_withdrawals_and_runtime_minimum_stripping(self):
        import json
        from dataclasses import replace
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from scripts.update_capability_evidence import build_index,discover_tests
        from quorune.oracle_ir import compile_oracle_card
        from quorune.card_programs.binding import bind_semantic_program_runtime
        from quorune.protection_attachment_exceptions import FIXED_TYPE_AURA_PROTECTION_CAPABILITY as cap
        raw=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        declarations=read_evidence_document(ROOT/'platform/capability-evidence-declarations.json');known=set(discover_tests(ROOT).values())
        def verified(value):
            registry=CapabilityRegistry(value);index=build_index(registry_value=value,declaration_source=declarations,discovered_test_ids=known)
            registry.mark_evidence_verified(validate_capability_evidence_index(index,registry=registry));return registry
        self.assertEqual('exact',compile_oracle_card(self.db.lookup('Tattoo Ward'),capability_registry=verified(raw),capability_profile='commander_review').status)
        for dependency in (cap,'protection.attachment.source_aura_exception','protection.typed.debt','continuous.attached.fixed_characteristics'):
            changed=json.loads(json.dumps(raw));next(r for r in changed['capabilities'] if r['id']==dependency).update(status='blocked',blockers=['withdrawn fixed-type Aura owner'])
            ir=compile_oracle_card(self.db.lookup('Tattoo Ward'),capability_registry=verified(changed),capability_profile='commander_review')
            self.assertNotEqual('exact',ir.status);blockers=[b for residual in ir.material_residuals for b in residual.blockers]
            self.assertTrue(any('status:'+dependency+':blocked' in b for b in blockers));self.assertFalse(any('evidence_index:unverified' in b for b in blockers))
        session=self.session(750520);source=self.add(session.engine,'Tattoo Ward',zone='hand')
        program=next(p for p in session.engine.semantics.programs_for_oracle(source.oracle_id) if cap in p.capability_dependencies)
        self.assertTrue(bind_semantic_program_runtime(program,capability_registry=self.registry,profile='commander_review')['strict'])
        for dependency in (cap,'protection.attachment.source_aura_exception'):
            deps=tuple(c for c in program.capability_dependencies if c!=dependency)
            stripped=replace(program,capability_dependencies=deps,capability_closure=self.registry.closure(deps,profile='commander_review').to_dict() if deps else None)
            bound=bind_semantic_program_runtime(stripped,capability_registry=self.registry,profile='commander_review')
            self.assertFalse(bound['strict']);self.assertIn('capability:undeclared_runtime_dependency:'+dependency,bound['blockers'])

    def test_fresh_actual_fixed_type_producer_binding_and_retention_omissions_are_killed(self):
        from quorune.compiler.attached_protection_exceptions import attached_protection_exception_handler
        def omitted(line,**kwargs):return None if 'protection from enchantments' in line else attached_protection_exception_handler(line,**kwargs)
        method='test_actual_tattoo_retains_itself_and_rejects_illegal_target_before_sacrifice'
        for target,mutation in (('quorune.compiler.attached_protection_exceptions.attached_protection_exception_handler',omitted),
                                ('quorune.protection_attachment_exceptions.bind_attached_protection_exceptions',lambda node,*a:node),
                                ('quorune.protection_attachment_exceptions.protection_specs_for_retained_aura',lambda specs,*a:specs)):
            with patch(target,mutation):
                with self.assertRaises(AssertionError):getattr(type(self)(method),method)()
