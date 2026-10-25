from __future__ import annotations

"""CR118.8/601.2: a paid self-zone method retains its own additional discard."""

import json
from dataclasses import replace
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from common import ROOT
from quorune.carddb import CardDatabase
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.deck import DeckDefinition,DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.self_graveyard_discard import (
    CAPABILITY,CAPABILITIES,HANDLER_ID,CONTEXT_FIELD,FINGERPRINT_FIELD,SelfGraveyardDiscardSpec,
    compiled_self_graveyard_discard_specs,validate_self_graveyard_discard_option,
)
from quorune.rules.capabilities import CapabilityRegistry,load_default_capability_registry
from scripts.build_test_database import build_fixture_database
import test_turn_count_self_costs as controls


class SelfZoneDiscardCastTests(unittest.TestCase):
    register_fixture_cards_on_add=True
    session=controls.TurnCountSelfCostTests.session
    add=controls.TurnCountSelfCostTests.add
    prepare=controls.TurnCountSelfCostTests.prepare
    checkpoint=controls.TurnCountSelfCostTests.checkpoint
    cast=controls.TurnCountSelfCostTests.cast
    resolve=controls.TurnCountSelfCostTests.resolve
    replay=controls.TurnCountSelfCostTests.replay
    binding=controls.TurnCountSelfCostTests.binding
    actions=controls.TurnCountSelfCostTests.actions
    priority=controls.TurnCountSelfCostTests.priority
    private_replay=controls.TurnCountSelfCostTests.private_replay

    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory();path=Path(cls.temp.name)/'self-zone-discard-casts.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',ROOT/'tests/fixtures/self-zone-discard-cast-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Printed paid graveyard permission',[DeckEntry('Generic Bound Commander',1,'commander'),
            DeckEntry('Generic Bound Plains',30)],['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls):
        cls.db.close();cls.temp.cleanup()

    def cast_action(self,session,source):
        return next((row for row in self.actions(session) if row['id']=='cast:'+source.ref),None)

    def paid_cast(self,session,source,cards,targets=()):
        self.priority(session,'A')
        action=self.cast_action(session,source);self.assertIsNotNone(action)
        option=next(row for row in action['cost_options'] if row['id'].startswith('graveyard-discard:'))
        result=session.act('pilot:A',{'action_id':action['id'],'cost_option':option['id'],
            'discard_cards':[card.ref for card in cards],'targets':list(targets),'pay':'auto'})
        self.assertTrue(result.ok,result.summary)

    def empty_initial_hand(self,session):
        for object_id in list(session.state.players['A'].zones['hand']):
            session.engine.move_card(object_id,'library',controller='A',log=False)

    def test_actual_rona_graveyard_method_pays_two_discards_and_preserves_its_cast_trigger(self):
        session=self.session(745100);source=self.add(session.engine,"Rona, Sheoldred's Faithful",zone='graveyard')
        first=self.add(session.engine,'Grizzly Bears',zone='hand',ref='rona-first-discard')
        second=self.add(session.engine,'Plains',zone='hand',ref='rona-second-discard')
        bolt=self.add(session.engine,'Lightning Bolt',zone='hand')
        private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand')
        self.prepare(session,mana={'U':1,'B':2,'C':1,'R':1});self.checkpoint(session)
        action=self.cast_action(session,source);self.assertIsNotNone(action)
        option=next(row for row in action['cost_options'] if row['id'].startswith('graveyard-discard:'))
        self.assertEqual({'GENERIC':1,'U':1,'B':2},{k:v for k,v in option['requirements'].items() if v})
        self.assertNotIn('normal',[row['id'] for row in action['cost_options']])
        choices=option['choice_schema']['discard_cards']
        self.assertEqual(2,choices['count'])
        self.assertIn(first.ref,choices['legal_refs']);self.assertIn(second.ref,choices['legal_refs'])
        for viewer in 'BCD':
            packet=json.dumps(session.packet('pilot:'+viewer,full=True))
            self.assertNotIn(first.ref,packet);self.assertNotIn(second.ref,packet)
        command={'action_id':action['id'],'cost_option':option['id'],'discard_cards':[first.ref,second.ref],
            'pay':'manual','payment':{'U':1,'B':2,'C':1}}
        before=authoritative_state_hash(session.state)
        self.assertFalse(session.act('pilot:D',command).ok);self.assertEqual(before,authoritative_state_hash(session.state))
        result=session.act('pilot:A',command);self.assertTrue(result.ok,result.summary)
        self.assertEqual('stack',session.state.cards[source.object_id].zone)
        self.assertEqual(['graveyard','graveyard'],[session.state.cards[row.object_id].zone for row in (first,second)])
        self.assertEqual([40]*4,[session.state.players[seat].life for seat in 'ABCD'])
        self.assertEqual({'R':1},{k:v for k,v in session.state.players['A'].mana_pool.items() if v})
        session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
        self.assertEqual('battlefield',session.state.cards[source.object_id].zone)
        self.assertTrue(self.binding(session,source)['strict_capability_ready'])
        self.cast(session,'A',bolt,['B']);self.assertIsNone(self.resolve(session))
        self.assertEqual([40,36,39,39],[session.state.players[seat].life for seat in 'ABCD'])
        self.assertEqual('battlefield',session.state.cards[source.object_id].zone)
        for object_id in session.state.players['A'].zones['hand']:
            for viewer in 'BCD':self.assertNotIn(session.state.cards[object_id].ref,json.dumps(session.packet('pilot:'+viewer,full=True)))
        self.private_replay(session,private)

    def test_two_originals_have_complete_nonempty_paid_zone_programs(self):
        for name,count in (('Alien Symbiosis',1),("Rona, Sheoldred's Faithful",2)):
            session=self.session(745110+count);source=self.add(session.engine,name,zone='graveyard')
            ir=compile_oracle_card(self.db.lookup(name),capability_registry=self.registry,capability_profile='commander_review')
            self.assertEqual('exact',ir.status,ir.material_residuals)
            self.assertTrue(self.binding(session,source)['strict_capability_ready'])
            program,=[p for p in session.engine.semantics.programs_for_oracle(source.oracle_id)
                if p.event=='cast.zone.permission' and any(h.get('handler_id')==HANDLER_ID for h in p.handlers)]
            self.assertIn(CAPABILITY,program.capability_dependencies)
            self.assertEqual(count,program.handlers[0]['payment']['discard_count'])

    def test_paid_zone_value_and_handler_reject_malformed_payment_or_scope(self):
        from quorune.semantic_runtime.self_graveyard_discard import SelfGraveyardDiscardPermissionHandler
        from quorune.compiler.self_graveyard_discard_templates import self_graveyard_discard_template
        value=SelfGraveyardDiscardSpec(2)
        self.assertEqual(value,SelfGraveyardDiscardSpec.from_dict(value.to_dict()))
        self.assertEqual(2,value.discard_descriptor()['count'])
        for change in ({'schema_version':True},{'schema_version':2},{'discard_count':0},
                {'discard_count':True},{'discard_count':-1},{'discard_count':'2'},{'future':True}):
            with self.assertRaises(ValueError):SelfGraveyardDiscardSpec.from_dict({**value.to_dict(),**change})
        descriptor=self_graveyard_discard_template(self.db.lookup("Rona, Sheoldred's Faithful").oracle_text.splitlines()[-1])[1]
        handler=SelfGraveyardDiscardPermissionHandler()
        self.assertEqual(value,handler.validate(descriptor))
        for change in ({'schema_version':True},{'source_zone':'exile'},{'requires_complete_card_program':False},
                {'event':'spell.cast'},{'payment':{'schema_version':1,'discard_count':False}}):
            with self.assertRaises(ValueError):handler.validate({**descriptor,**change})
        for text in ('You may cast this card from your graveyard by discarding X cards in addition to paying its other costs.',
                'You may cast this card from exile by discarding a card in addition to paying its other costs.',
                'You may cast this card from your graveyard by discarding a creature card in addition to paying its other costs.',
                'You may cast this card from your graveyard by paying 3 life and discarding a card in addition to paying its other costs.'):
            self.assertIsNone(self_graveyard_discard_template(text))

    def test_actual_alien_pays_one_discard_and_repeats_after_real_aura_destruction(self):
        session=self.session(745120);source=self.add(session.engine,'Alien Symbiosis',zone='graveyard')
        target=self.add(session.engine,'Grizzly Bears',seat='B')
        first=self.add(session.engine,'Plains',zone='hand',ref='alien-discard1')
        second=self.add(session.engine,'Plains',zone='hand',ref='alien-discard2')
        destroy=self.add(session.engine,'Naturalize',zone='hand')
        private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand')
        self.prepare(session,mana={'B':2,'G':1,'C':3});self.checkpoint(session)
        before=authoritative_state_hash(session.state)
        action=self.cast_action(session,source);self.assertIsNotNone(action)
        option=next(row for row in action['cost_options'] if row['id'].startswith('graveyard-discard:'))
        denied=session.act('pilot:A',{'action_id':action['id'],'cost_option':option['id'],
            'discard_cards':[first.ref],'targets':[second.ref],'pay':'auto'})
        self.assertFalse(denied.ok);self.assertEqual(before,authoritative_state_hash(session.state))
        self.paid_cast(session,source,[first],[target.ref])
        session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
        aura=session.state.cards[source.object_id];body=session.state.cards[target.object_id]
        self.assertEqual('battlefield',aura.zone);self.assertEqual(body.object_id,aura.attached_to)
        self.assertEqual((3,3),(session.engine._numeric_stat(body.object_id,'power'),session.engine._numeric_stat(body.object_id,'toughness')))
        self.assertIn('menace',session.engine._combat_keywords(body))
        self.assertIn('symbiote',session.engine._type_parts(session.engine._effective_card_data(body)['type_line'])[1])
        old_identity=aura.logical_object_id
        self.cast(session,'A',destroy,[aura.ref]);self.assertIsNone(self.resolve(session))
        self.assertEqual('graveyard',session.state.cards[source.object_id].zone)
        self.assertNotEqual(old_identity,session.state.cards[source.object_id].logical_object_id)
        self.assertEqual(2,session.engine._numeric_stat(body.object_id,'power'))
        self.paid_cast(session,session.state.cards[source.object_id],[second],[body.ref])
        session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
        self.assertEqual('battlefield',session.state.cards[source.object_id].zone)
        self.assertEqual(3,session.engine._numeric_stat(body.object_id,'power'))
        self.assertEqual('graveyard',session.state.cards[second.object_id].zone)
        self.private_replay(session,private)

    def test_duplicate_foreign_missing_and_unpaid_method_choices_roll_back(self):
        session=self.session(745121);source=self.add(session.engine,"Rona, Sheoldred's Faithful",zone='graveyard')
        first=self.add(session.engine,'Plains',zone='hand',ref='valid-discard1')
        second=self.add(session.engine,'Plains',zone='hand',ref='valid-discard2')
        foreign=self.add(session.engine,'Plains',seat='B',zone='hand',ref='foreign-discard')
        self.prepare(session,mana={'U':1,'B':2,'C':1});self.checkpoint(session)
        action=self.cast_action(session,source);self.assertIsNotNone(action)
        option=next(row for row in action['cost_options'] if row['id'].startswith('graveyard-discard:'))
        base={'action_id':action['id'],'cost_option':option['id'],'discard_cards':[first.ref,second.ref],'pay':'auto'}
        for changes in ({'discard_cards':[first.ref,first.ref]},{'discard_cards':[first.ref,foreign.ref]},
                {'discard_cards':[first.ref]},{'discard_cards':[]},{'cost_option':'normal'},
                {'cost_option':'graveyard-discard:1'},{'pay':'manual','payment':{}}):
            before=authoritative_state_hash(session.state)
            result=session.act('pilot:A',{**base,**changes})
            self.assertFalse(result.ok,(changes,result.summary));self.assertEqual(before,authoritative_state_hash(session.state))
        self.paid_cast(session,source,[first,second]);session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
        self.assertEqual('battlefield',session.state.cards[source.object_id].zone)

    def test_insufficient_hand_withholds_paid_route_and_preserves_source(self):
        session=self.session(745122);self.empty_initial_hand(session)
        source=self.add(session.engine,"Rona, Sheoldred's Faithful",zone='graveyard')
        only=self.add(session.engine,'Plains',zone='hand')
        self.prepare(session,mana={'U':1,'B':2,'C':1});self.checkpoint(session)
        self.assertIsNone(self.cast_action(session,source))
        before=authoritative_state_hash(session.state)
        result=session.act('pilot:A',{'action_id':'cast:'+source.ref,'cost_option':'graveyard-discard:2',
            'discard_cards':[only.ref,only.ref],'pay':'auto'})
        self.assertFalse(result.ok);self.assertEqual(before,authoritative_state_hash(session.state))
        self.assertEqual('graveyard',session.state.cards[source.object_id].zone)
        self.assertEqual('hand',session.state.cards[only.object_id].zone)
        self.replay(session,load=True)

    def test_normal_hand_and_independent_permission_keep_ordinary_unpaid_route(self):
        session=self.session(745123);self.empty_initial_hand(session)
        source=self.add(session.engine,"Rona, Sheoldred's Faithful",zone='hand')
        self.prepare(session,mana={'U':1,'B':2,'C':1});self.checkpoint(session)
        action=self.cast_action(session,source);self.assertIsNotNone(action)
        self.assertEqual(['normal'],[row['id'] for row in action['cost_options']])
        self.cast(session,'A',source);session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
        self.assertEqual('battlefield',session.state.cards[source.object_id].zone)
        session=self.session(745124);self.empty_initial_hand(session)
        source=self.add(session.engine,'Generic Independent Graveyard Permission',zone='graveyard')
        self.prepare(session,mana={'U':1,'B':2,'C':1});self.checkpoint(session)
        action=self.cast_action(session,source);self.assertIsNotNone(action)
        self.assertEqual(['normal'],[row['id'] for row in action['cost_options']])
        self.cast(session,'A',source);session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
        self.assertEqual('battlefield',session.state.cards[source.object_id].zone)
        self.assertEqual([],session.state.players['A'].zones['hand'])

    def test_countered_paid_cast_returns_to_graveyard_then_pays_a_fresh_cost(self):
        session=self.session(745125);source=self.add(session.engine,"Rona, Sheoldred's Faithful",zone='graveyard')
        cards=[self.add(session.engine,'Plains',zone='hand',ref='repeat-discard'+str(i)) for i in range(4)]
        counter=self.add(session.engine,'Counterspell',seat='B',zone='hand')
        self.prepare(session,mana={'U':2,'B':4,'C':2});session.state.players['B'].mana_pool['U']=2
        self.checkpoint(session)
        self.paid_cast(session,source,cards[:2]);target=session.state.stack[-1].ref
        self.cast(session,'B',counter,[target]);self.assertIsNone(self.resolve(session))
        self.assertEqual('graveyard',session.state.cards[source.object_id].zone)
        self.paid_cast(session,session.state.cards[source.object_id],cards[2:])
        session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
        self.assertEqual('battlefield',session.state.cards[source.object_id].zone)
        self.assertTrue(all(session.state.cards[row.object_id].zone=='graveyard' for row in cards))
        self.assertEqual({}, {k:v for k,v in session.state.players['A'].mana_pool.items() if v})
        self.replay(session,load=True)

    def test_paid_permission_dependencies_cannot_be_stripped_or_withdrawn(self):
        session=self.session(745126);source=self.add(session.engine,"Rona, Sheoldred's Faithful",zone='graveyard')
        program,=[p for p in session.engine.semantics.programs_for_oracle(source.oracle_id)
            if p.event=='cast.zone.permission' and any(h.get('handler_id')==HANDLER_ID for h in p.handlers)]
        for cap in CAPABILITIES:
            deps=tuple(value for value in program.capability_dependencies if value!=cap)
            missing=replace(program,capability_dependencies=deps,capability_closure=self.registry.closure(deps,profile='commander_review').to_dict())
            bound=bind_semantic_program_runtime(missing,capability_registry=self.registry,profile='commander_review')
            self.assertFalse(bound['strict']);self.assertIn('capability:undeclared_runtime_dependency:'+cap,bound['blockers'])
            raw=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
            next(row for row in raw['capabilities'] if row['id']==cap).update(status='blocked',blockers=['withdrawn paid self-zone authority'])
            for name in ('Alien Symbiosis',"Rona, Sheoldred's Faithful"):
                self.assertNotEqual('exact',compile_oracle_card(self.db.lookup(name),capability_registry=CapabilityRegistry(raw),capability_profile='commander_review').status)

    def test_current_face_whole_admission_component_and_owner_are_required(self):
        session=self.session(745127);source=self.add(session.engine,"Rona, Sheoldred's Faithful",zone='graveyard')
        self.prepare(session,mana={'U':1,'B':2,'C':1});self.checkpoint(session)
        self.assertTrue(compiled_self_graveyard_discard_specs(session.engine,'A',source))
        action=self.cast_action(session,source)
        selected=list(session.state.players['A'].zones['hand'])[:2]
        command={'action_id':action['id'],'cost_option':'graveyard-discard:2',
            'discard_cards':[session.state.cards[oid].ref for oid in selected],'pay':'auto'}
        self.assertFalse(compiled_self_graveyard_discard_specs(session.engine,'B',source))
        for changed in (replace(source,zone='hand'),replace(source,face_down=True),replace(source,object_kind='token'),
                replace(source,annotations={**source.annotations,'copy_overrides':{'type_line':'Creature — Human'}})):
            self.assertFalse(compiled_self_graveyard_discard_specs(session.engine,'A',changed))
        for target in ('program_has_complete_card_program_admission','program_matches_face'):
            with patch('quorune.rules.graveyard_payment_sources.'+target,return_value=False):
                self.assertFalse(compiled_self_graveyard_discard_specs(session.engine,'A',source))
                before=authoritative_state_hash(session.state)
                self.assertFalse(session.act('pilot:A',command).ok)
                self.assertEqual(before,authoritative_state_hash(session.state))
        with patch.object(session.engine,'_effective_static_component_keys',return_value=()):
            self.assertFalse(compiled_self_graveyard_discard_specs(session.engine,'A',source))
            before=authoritative_state_hash(session.state)
            self.assertFalse(session.act('pilot:A',command).ok)
            self.assertEqual(before,authoritative_state_hash(session.state))
        self.assertIsNotNone(self.cast_action(session,source))

    def test_resolved_cost_contract_cannot_lose_payment_or_change_method(self):
        session=self.session(745128);source=self.add(session.engine,"Rona, Sheoldred's Faithful",zone='graveyard')
        self.prepare(session,mana={'U':1,'B':2,'C':1});self.checkpoint(session)
        spec=compiled_self_graveyard_discard_specs(session.engine,'A',source)[0]
        option=spec.cost_option({'id':'normal','kind':'normal','requirements':{'GENERIC':1,'U':1,'B':2}})
        option['additional_costs']=option.pop('_additional_option_costs')
        validate_self_graveyard_discard_option(session.engine,'A',source,option,origin='graveyard')
        for changed in ({'id':'normal'},{'source_zone':'hand'},{FINGERPRINT_FIELD:'0'*64},
                {CONTEXT_FIELD:{'schema_version':1,'discard_count':1}},{'additional_costs':[]}):
            with self.assertRaises(ValueError):
                validate_self_graveyard_discard_option(session.engine,'A',source,{**option,**changed},origin='graveyard')
        missing={key:value for key,value in option.items() if key!=CONTEXT_FIELD}
        with self.assertRaises(ValueError):validate_self_graveyard_discard_option(session.engine,'A',source,missing,origin='graveyard')
        with self.assertRaises(ValueError):validate_self_graveyard_discard_option(session.engine,'A',source,option,origin='hand')

    def test_fresh_original_kills_omitted_actual_discard_cost(self):
        method='test_actual_rona_graveyard_method_pays_two_discards_and_preserves_its_cast_trigger'
        with patch('quorune.rules.casting.commit._fixed_zone_change_commit_entries',return_value=[]):
            with self.assertRaises(AssertionError):getattr(type(self)(method),method)()

    def test_fresh_original_kills_omitted_paid_permission_production(self):
        method='test_two_originals_have_complete_nonempty_paid_zone_programs'
        with patch('quorune.compiler.self_graveyard_discard_templates.self_graveyard_discard_template',return_value=None):
            with self.assertRaises(AssertionError):getattr(type(self)(method),method)()
