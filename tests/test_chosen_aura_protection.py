from __future__ import annotations

"""CR 614.12a/702.16n: source designation and protection belong to the Aura."""

from pathlib import Path
import json
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


class ChosenAuraProtectionTests(unittest.TestCase):
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
    private_replay=controls.AttachedProtectionExceptionTests.private_replay
    actions=controls.AttachedProtectionExceptionTests.actions

    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory();path=Path(cls.temp.name)/'chosen-protection.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',ROOT/'tests/fixtures/attached-protection-cards.json',
                                ROOT/'tests/fixtures/chosen-aura-protection-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Chosen Aura protection',[DeckEntry('Generic Bound Commander',1,'commander'),DeckEntry('Generic Bound Plains',30)],['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls):cls.db.close();cls.temp.cleanup()

    def choose(self,session,color):
        self.assertEqual('replacement.order',self.resolve(session).kind)
        option=next(row['id'] for row in session.packet('pilot:A',full=True)['decision']['ctx']['options'] if row['id'].endswith(':'+color))
        result=session.act('pilot:A',{'action_id':'choose','choices':{'replacement':option}});self.assertTrue(result.ok,result.summary)
        self.assertIsNone(self.resolve(session))

    def test_actual_pentarch_entry_choice_belongs_to_aura_controller_and_draw_remains_private(self):
        from quorune.ability_fragments import protection_specs,ability_fragment_from_dict
        session=self.session(750400);body=self.add(session.engine,'Grizzly Bears',seat='B')
        aura=self.add(session.engine,'Pentarch Ward',zone='hand')
        matching=self.add(session.engine,'Humble',zone='hand')
        nonmatching=self.add(session.engine,'Boomerang',seat='B',zone='hand')
        private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand')
        self.assertTrue(self.binding(session,aura)['strict_capability_ready'],'Complete original Pentarch Ward must admit')
        self.prepare(session,mana={'W':2,'C':3});session.state.players['B'].mana_pool['U']=2;self.checkpoint(session)
        hands={seat:len(session.state.players[seat].zones['hand']) for seat in 'ABCD'}
        before_hand=set(session.state.players['A'].zones['hand'])
        self.cast(session,'A',aura,[body.ref],{'W':1,'C':2})
        self.assertEqual('replacement.order',self.resolve(session).kind)
        self.assertEqual(['pilot:A'],list(session.pending_principals()))
        self.assertEqual('stack',session.state.cards[aura.object_id].zone)
        self.assertEqual(hands['A']-1,len(session.state.players['A'].zones['hand']))
        session=self.replay(session,load=True)
        option=next(r['id'] for r in session.packet('pilot:A',full=True)['decision']['ctx']['options'] if r['id'].endswith(':W'))
        before=authoritative_state_hash(session.state)
        self.assertFalse(session.act('pilot:B',{'action_id':'choose','choices':{'replacement':option}}).ok)
        self.assertEqual(before,authoritative_state_hash(session.state))
        result=session.act('pilot:A',{'action_id':'choose','choices':{'replacement':option}});self.assertTrue(result.ok,result.summary)
        self.assertIsNone(self.resolve(session))
        current=session.state.cards[aura.object_id]
        self.assertEqual(('battlefield',body.object_id,'W'),(current.zone,current.attached_to,current.annotations.get('chosen_color')))
        specs=protection_specs(ability_fragment_from_dict(r) for r in session.engine._effective_card_data(session.state.cards[body.object_id]).get('ability_fragments',()))
        self.assertEqual(1,len(specs),'The actual registered layer-six grant must produce one protection instance')
        spec,=specs;self.assertEqual(('W',current.logical_object_id),(spec.quality,spec.exempt_aura_incarnation))
        self.assertEqual(hands['A'],len(session.state.players['A'].zones['hand']))
        for seat in 'BCD':self.assertEqual(hands[seat],len(session.state.players[seat].zones['hand']))
        drawn=set(session.state.players['A'].zones['hand'])-before_hand;self.assertEqual(1,len(drawn))
        revealed=session.state.cards[next(iter(drawn))].ref
        self.assertIn(revealed,json.dumps(session.packet('pilot:A',full=True)))
        for seat in 'BCD':self.assertNotIn(revealed,json.dumps(session.packet('pilot:'+seat,full=True)))
        self.priority(session,'A');before=authoritative_state_hash(session.state)
        self.assertFalse(session.act('pilot:A',{'action_id':'cast:'+matching.ref,'targets':[body.ref],'pay':'auto'}).ok)
        self.assertEqual(before,authoritative_state_hash(session.state))
        self.cast(session,'B',nonmatching,[body.ref],{'U':2});session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
        self.assertEqual(('hand','graveyard'),(session.state.cards[body.object_id].zone,session.state.cards[aura.object_id].zone))
        self.private_replay(session,private)

    def test_closed_handler_uses_only_the_retained_source_choice_and_current_aura(self):
        from dataclasses import replace
        from quorune.semantic_runtime.chosen_aura_protection import SourceChosenAuraProtectionHandler
        from quorune.semantic_runtime.continuous_components import ContinuousEffectSourceContext
        from quorune.continuous_effect_model import ContinuousObjectIdentity
        from quorune.replacement.immutable import FrozenMap
        handler=SourceChosenAuraProtectionHandler()
        descriptor={'handler_id':handler.handler_id,'schema_version':1,'event':handler.event,'subject':'source_attached_creature'}
        context=ContinuousEffectSourceContext('aura','AURA','A',1,'component',
            attached_object=ContinuousObjectIdentity('body','body@1'),source_logical_object_id='aura@1',
            source_designations=FrozenMap({'chosen_color':'R'}))
        effects=handler.lower(descriptor,context)
        self.assertEqual(1,len(effects));fragment=effects[0].operations[1].value
        self.assertEqual(('R','aura@1'),(fragment['value']['quality'],fragment['value']['exempt_aura_incarnation']))
        self.assertEqual((),handler.lower(descriptor,replace(context,attached_object=None)))
        for change in ({'schema_version':True},{'subject':'source_controller_creatures'},{'extra':0}):
            with self.assertRaises(ValueError):handler.validate({**descriptor,**change})
        for color in (None,'','colorless','WU','purple'):
            with self.assertRaises(ValueError):handler.lower(descriptor,replace(context,source_designations=FrozenMap({'chosen_color':color})))
        with self.assertRaises(ValueError):handler.lower(descriptor,replace(context,source_logical_object_id=None))
        other=handler.lower(descriptor,replace(context,source_controller='B',source_logical_object_id='aura@2'))[0].operations[1].value
        self.assertEqual(('R','aura@2'),(other['value']['quality'],other['value']['exempt_aura_incarnation']))

    def test_fresh_actual_chosen_compiler_and_registered_grant_omissions_are_killed(self):
        from quorune.compiler.attached_protection_exceptions import attached_protection_exception_handler
        method='test_actual_pentarch_entry_choice_belongs_to_aura_controller_and_draw_remains_private'
        def omitted(line,**kwargs):
            return None if 'the chosen color' in line else attached_protection_exception_handler(line,**kwargs)
        with patch('quorune.compiler.attached_protection_exceptions.attached_protection_exception_handler',omitted):
            with self.assertRaises(AssertionError):getattr(type(self)(method),method)()
        with patch('quorune.semantic_runtime.chosen_aura_protection.SourceChosenAuraProtectionHandler.lower',return_value=()):
            with self.assertRaises(AssertionError):getattr(type(self)(method),method)()

    def test_canonically_verified_chosen_and_entry_owner_withdrawals_and_minimum_strip_reject(self):
        import json
        from dataclasses import replace
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from scripts.update_capability_evidence import build_index,discover_tests
        from quorune.oracle_ir import compile_oracle_card
        from quorune.card_programs.binding import bind_semantic_program_runtime
        cap='protection.attachment.source_chosen_color'
        raw=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        declarations=read_evidence_document(ROOT/'platform/capability-evidence-declarations.json')
        known=set(discover_tests(ROOT).values())
        def verified(value):
            registry=CapabilityRegistry(value);index=build_index(registry_value=value,declaration_source=declarations,discovered_test_ids=known)
            registry.mark_evidence_verified(validate_capability_evidence_index(index,registry=registry));return registry
        self.assertEqual('exact',compile_oracle_card(self.db.lookup('Pentarch Ward'),capability_registry=verified(raw),capability_profile='commander_review').status)
        for dependency in (cap,'protection.attachment.source_aura_exception','zone.entry.public_designation'):
            data=json.loads(json.dumps(raw));next(r for r in data['capabilities'] if r['id']==dependency).update(status='blocked',blockers=['withdrawn chosen Aura owner'])
            ir=compile_oracle_card(self.db.lookup('Pentarch Ward'),capability_registry=verified(data),capability_profile='commander_review')
            self.assertNotEqual('exact',ir.status)
            blockers=[b for residual in ir.material_residuals for b in residual.blockers]
            self.assertTrue(any('status:'+dependency+':blocked' in b for b in blockers));self.assertFalse(any('evidence_index:unverified' in b for b in blockers))
        session=self.session(750401);source=self.add(session.engine,'Pentarch Ward',zone='hand')
        program=next(p for p in session.engine.semantics.programs_for_oracle(source.oracle_id) if cap in p.capability_dependencies)
        deps=tuple(c for c in program.capability_dependencies if c!=cap)
        changed=replace(program,capability_dependencies=deps,capability_closure=self.registry.closure(deps,profile='commander_review').to_dict() if deps else None)
        bound=bind_semantic_program_runtime(changed,capability_registry=self.registry,profile='commander_review')
        self.assertFalse(bound['strict']);self.assertIn('capability:undeclared_runtime_dependency:'+cap,bound['blockers'])

    def test_actual_flickering_return_ends_the_grant_and_recast_selects_for_a_new_incarnation(self):
        session=self.session(750410);body=self.add(session.engine,'Grizzly Bears')
        aura=self.add(session.engine,'Flickering Ward',zone='hand')
        self.assertTrue(self.binding(session,aura)['strict_capability_ready'])
        self.prepare(session,mana={'W':3});self.checkpoint(session)
        self.cast(session,'A',aura,[body.ref],{'W':1});self.choose(session,'U')
        from quorune.ability_fragments import protection_specs,ability_fragment_from_dict
        spec,=protection_specs(ability_fragment_from_dict(r) for r in session.engine._effective_card_data(session.state.cards[body.object_id]).get('ability_fragments',()))
        self.assertEqual('U',spec.quality)
        old=session.state.cards[aura.object_id].logical_object_id
        self.priority(session,'A');action=next(row for row in self.actions(session) if row['id'].startswith('activate:'+aura.ref+':'))
        before=authoritative_state_hash(session.state)
        self.assertFalse(session.act('pilot:B',{'action_id':action['id'],'pay':'auto'}).ok)
        self.assertEqual(before,authoritative_state_hash(session.state))
        result=session.act('pilot:A',{'action_id':action['id'],'pay':'manual','payment':{'W':1}});self.assertTrue(result.ok,result.summary)
        session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
        self.assertEqual('hand',session.state.cards[aura.object_id].zone)
        self.assertFalse(any(r.get('kind')=='protection' for r in session.engine._effective_card_data(session.state.cards[body.object_id]).get('ability_fragments',())))
        self.cast(session,'A',session.state.cards[aura.object_id],[body.ref],{'W':1})
        self.assertEqual('replacement.order',self.resolve(session).kind)
        session=self.replay(session,load=True);self.choose(session,'W')
        current=session.state.cards[aura.object_id]
        self.assertNotEqual(old,current.logical_object_id)
        self.assertEqual(('battlefield',body.object_id,'W'),(current.zone,current.attached_to,current.annotations.get('chosen_color')))
        self.assertEqual(0,session.state.players['A'].mana_pool.get('W',0));self.replay(session)

    def test_actual_cho_manno_flash_enters_on_another_turn_and_keeps_the_source_choice(self):
        session=self.session(750411);body=self.add(session.engine,'Grizzly Bears',seat='B')
        aura=self.add(session.engine,"Cho-Manno's Blessing",zone='hand')
        self.assertTrue(self.binding(session,aura)['strict_capability_ready'])
        self.prepare(session,actor='B',active='B');session.state.players['A'].mana_pool['W']=2;self.checkpoint(session)
        self.priority(session,'A')
        self.assertTrue(any(row['id']=='cast:'+aura.ref for row in self.actions(session)))
        self.cast(session,'A',aura,[body.ref],{'W':2});session=self.replay(session,load=True);self.choose(session,'B')
        current=session.state.cards[aura.object_id]
        self.assertEqual(('B','battlefield',body.object_id,'B'),(session.state.active_player,current.zone,current.attached_to,current.annotations.get('chosen_color')))
        self.assertEqual(0,session.state.players['A'].mana_pool.get('W',0));self.replay(session)
