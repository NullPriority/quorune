from __future__ import annotations

from pathlib import Path
import json
import tempfile
import unittest
from unittest.mock import patch

from common import ROOT
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import CapabilityRegistry, load_default_capability_registry
from scripts.build_test_database import build_fixture_database
from test_granted_skulk import record
import test_compound_damage_recipients as controls


class PublicNegativeKeywordQueryCompilerTests(unittest.TestCase):
    def test_withdrawn_power_toughness_owner_keeps_negative_keyword_clause_residual(self):
        raw=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text())
        next(row for row in raw['capabilities'] if row['id']=='continuous.power_toughness.fixed_anthem').update(
            status='blocked',blockers=['withdrawn P/T query authority'])
        ir=compile_oracle_card(record('Creatures without flying get -2/-0.','Enchantment'),
            capability_registry=CapabilityRegistry(raw),capability_profile='commander_review')
        self.assertNotEqual('exact',ir.status)
        self.assertTrue(ir.material_residuals)

    def test_negative_keyword_query_preserves_control_exclusion_and_positive_grammar(self):
        from quorune.compiler.public_state_queries import fixed_battlefield_query_subject
        relation,query,other=fixed_battlefield_query_subject('Other creatures you control without flying')
        self.assertEqual('source_controller',relation)
        self.assertTrue(other)
        self.assertEqual(('flying',),query.keywords_none)
        self.assertEqual((),query.keywords_all)
        _relation,positive,_other=fixed_battlefield_query_subject('Creatures with flying')
        self.assertEqual(('flying',),positive.keywords_all)
        self.assertEqual((),positive.keywords_none)
        for subject in ('Creatures without NotARealAbility','Creatures without flying or reach',
                'Artifacts without flying','Creatures without flying and with vigilance'):
            self.assertIsNone(fixed_battlefield_query_subject(subject))

    def test_keyword_rejecting_characteristic_consumers_remain_closed(self):
        from quorune.compiler.public_state_queries import fixed_characteristic_battlefield_query_subject, fixed_power_toughness_battlefield_query
        from quorune.compiler.fixed_resolution_characteristic_queries import fixed_resolution_characteristic_query_is_closed
        from quorune.object_predicate import ObjectQuerySpec
        self.assertIsNone(fixed_characteristic_battlefield_query_subject('Creatures without flying'))
        self.assertIsNotNone(fixed_power_toughness_battlefield_query('Creatures without flying','Creatures without flying get -2/-0.'))
        for field in ('keywords_all','keywords_none'):
            query=ObjectQuerySpec(types_all=('creature',),**{field:('flying',)})
            self.assertFalse(fixed_resolution_characteristic_query_is_closed(query,target_schema=None))
        registry=load_default_capability_registry()
        for text in ('Creatures without flying have flying.',
                'Creatures without flying get +1/+1 and have flying.'):
            ir=compile_oracle_card(record(text,'Enchantment'),capability_registry=registry,
                capability_profile='commander_review')
            self.assertNotEqual('exact',ir.status,text)
        positive=compile_oracle_card(record('Put a +1/+1 counter on each creature with flying.','Sorcery'),
            capability_registry=registry,capability_profile='commander_review')
        self.assertEqual('exact',positive.status,positive.material_residuals)
        for text,kind in (('Put a +1/+1 counter on each creature without flying.','Sorcery'),
                ('As long as you control two or more creatures without flying, this creature has flying.','Creature')):
            ir=compile_oracle_card(record(text,kind),capability_registry=registry,
                capability_profile='commander_review')
            self.assertNotEqual('exact',ir.status,text)


class PublicNegativeKeywordQueryRuntimeTests(unittest.TestCase):
    register_fixture_cards_on_add=True
    session=controls.CompoundDamageRuntimeTests.session
    add=controls.CompoundDamageRuntimeTests.add
    prepare=controls.CompoundDamageRuntimeTests.prepare
    checkpoint=controls.CompoundDamageRuntimeTests.checkpoint
    cast=controls.CompoundDamageRuntimeTests.cast
    resolve=controls.CompoundDamageRuntimeTests.resolve
    replay=controls.CompoundDamageRuntimeTests.replay
    binding=controls.CompoundDamageRuntimeTests.binding
    actions=controls.CompoundDamageRuntimeTests.actions
    action=controls.CompoundDamageRuntimeTests.action
    private_replay=controls.CompoundDamageRuntimeTests.private_replay

    @classmethod
    def setUpClass(cls):
        cls.temporary=tempfile.TemporaryDirectory()
        path=Path(cls.temporary.name)/'negative-keyword.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',
            ROOT/'tests/fixtures/public-negative-keyword-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Negative keyword witnesses',[DeckEntry('Generic Bound Commander',1,'commander'),
            DeckEntry('Generic Bound Plains',30)],['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls):
        cls.db.close();cls.temporary.cleanup()

    def power(self,session,card):
        return int(session.engine._effective_card_data(session.state.cards[card.object_id])['power'])

    def test_original_gravitational_shift_follows_counter_and_actual_flying_grants_and_source_departure(self):
        session=self.session(729501)
        source=self.add(session.engine,'Gravitational Shift',zone='hand')
        own=self.add(session.engine,'Generic Bound Body',ref='ground-own')
        ground=self.add(session.engine,'Generic Bound Body',seat='B',ref='ground-b')
        flying=self.add(session.engine,'Generic Bound Body',seat='D',ref='flying-d')
        flying.counters['flying']=1
        jump=self.add(session.engine,'Jump',seat='C',zone='hand')
        naturalize=self.add(session.engine,'Naturalize',seat='B',zone='hand')
        private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand')
        session.state.players['C'].mana_pool.update({'U':1})
        session.state.players['B'].mana_pool.update({'G':1,'C':1})
        self.prepare(session,mana={'U':2,'C':3});self.checkpoint(session)
        for card in (source,jump,naturalize):self.assertTrue(self.binding(session,card)['strict_capability_ready'])
        from quorune.preflight import card_semantic_status
        status=card_semantic_status(self.db.lookup('Gravitational Shift'),session.engine.semantics,
            db=self.db,capability_registry=self.registry,capability_profile='commander_review')
        self.assertEqual('fully_playable',status['status'],status['unresolved'])
        action=self.action(session,source);before=authoritative_state_hash(session.state)
        denied=session.act('pilot:D',{'action_id':action['id'],'pay':'auto'})
        self.assertFalse(denied.ok);self.assertEqual(before,authoritative_state_hash(session.state))
        accepted=session.act('pilot:A',{'action_id':action['id'],'pay':'auto'})
        self.assertTrue(accepted.ok,accepted.summary)
        session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
        self.assertEqual([0,0,4],[self.power(session,c) for c in (own,ground,flying)])
        self.cast(session,'C',jump,[ground.ref]);self.assertIsNone(self.resolve(session))
        self.assertEqual([0,4,4],[self.power(session,c) for c in (own,ground,flying)])
        session=self.replay(session,load=True)
        self.cast(session,'B',naturalize,[source.ref]);self.assertIsNone(self.resolve(session))
        self.assertEqual([2,2,2],[self.power(session,c) for c in (own,ground,flying)])
        self.assertEqual('graveyard',session.state.cards[source.object_id].zone)
        self.private_replay(session,private)

    def test_negative_keyword_lord_uses_current_controller_and_excludes_its_source(self):
        session=self.session(729502)
        source=self.add(session.engine,'Generic Grounded Lord')
        own=self.add(session.engine,'Generic Bound Body',ref='lord-own')
        other=self.add(session.engine,'Generic Bound Body',seat='B',ref='lord-other')
        flyer=self.add(session.engine,'Generic Bound Body',seat='B',ref='lord-flyer')
        flyer.counters['flying']=1
        private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand')
        session.engine.change_control(source.object_id,'B',reason='Pre-action controller contrast')
        self.prepare(session,mana={});self.checkpoint(session)
        self.assertTrue(self.binding(session,source)['strict_capability_ready'])
        self.assertEqual([2,2,3,2],[self.power(session,c) for c in (source,own,other,flyer)])
        session=self.replay(session,load=True)
        self.assertEqual([2,2,3,2],[self.power(session,c) for c in (source,own,other,flyer)])
        self.private_replay(session,private)

    def test_losing_negative_keyword_filter_is_killed_by_fresh_original_gravitational_shift(self):
        from quorune.compiler import public_state_queries
        original=public_state_queries._extended_public_battlefield_query
        def omit_negative(subject):
            parsed=original(subject)
            if parsed is None or not parsed[1].keywords_none:return parsed
            from dataclasses import replace
            return parsed[0],replace(parsed[1],keywords_none=()),parsed[2]
        with patch.object(public_state_queries,'_extended_public_battlefield_query',side_effect=omit_negative):
            method='test_original_gravitational_shift_follows_counter_and_actual_flying_grants_and_source_departure'
            with self.assertRaises(AssertionError):getattr(type(self)(method),method)()

    def test_runtime_query_cannot_omit_its_registered_power_toughness_authority(self):
        from dataclasses import replace
        from quorune.card_programs.binding import bind_semantic_program_runtime
        session=self.session(729503)
        source=self.add(session.engine,'Gravitational Shift')
        cap='continuous.power_toughness.fixed_anthem'
        program=next(p for p in session.engine.semantics.programs_for_oracle(source.oracle_id)
            if cap in p.capability_dependencies)
        dependencies=tuple(value for value in program.capability_dependencies if value!=cap)
        malformed=replace(program,capability_dependencies=dependencies,
            capability_closure=self.registry.closure(dependencies,profile='commander_review').to_dict() if dependencies else None)
        bound=bind_semantic_program_runtime(malformed,capability_registry=self.registry,profile='commander_review')
        self.assertFalse(bound['strict'])
        self.assertIn('capability:undeclared_runtime_dependency:'+cap,bound['blockers'])
