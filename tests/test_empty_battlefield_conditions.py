"""Pinned CR603.4/400.7: empty public board and source sacrifice."""
from dataclasses import replace
import json,tempfile,unittest
from pathlib import Path
from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition,DeckEntry
from quorune.oracle_ir import compile_oracle_card,register_generated_programs
from quorune.rules.capabilities import load_default_capability_registry
from quorune.continuous_conditions import FixedPublicStateConditionSpec,FixedPublicStateConditionKind
from quorune.characteristic_fragments import CharacteristicQuantityScope
from scripts.build_test_database import build_fixture_database
import test_target_recipient_results as recipients
FIXTURE=ROOT/'tests/fixtures/empty-battlefield-condition-cards.json'

class EmptyBattlefieldConditionModelTests(unittest.TestCase):
    def test_closed_global_zero_count_preserves_controller_local_condition_and_old_codec(self):
        from quorune.compiler.public_state_queries import fixed_public_state_condition
        for noun in ('creatures','artifacts','enchantments','lands','planeswalkers','battles','permanents'):
            c=fixed_public_state_condition('no '+noun+' are on the battlefield',source_name='Boundary')
            self.assertEqual(FixedPublicStateConditionKind.QUERY_COUNT_AT_MOST,c.kind);self.assertEqual(0,c.amount)
            self.assertEqual(CharacteristicQuantityScope.ALL_ZONES,c.quantity.scope)
            self.assertEqual(('battlefield',),c.quantity.query.zones);self.assertFalse(c.quantity.exclude_source)
            self.assertEqual(() if noun=='permanents' else (noun[:-1],),c.quantity.query.types_all)
            self.assertEqual(c,FixedPublicStateConditionSpec.from_dict(c.to_dict()))
        local=fixed_public_state_condition('you control no creatures',source_name='Boundary')
        self.assertEqual(CharacteristicQuantityScope.CONTROLLER_ZONE,local.quantity.scope)
        for text in ('no other creatures are on the battlefield','no nontoken creatures are on the battlefield','no creatures are in a graveyard','no creatures are on the battlefield until end of turn'):
            self.assertIsNone(fixed_public_state_condition(text,source_name='Boundary'))

class EmptyBattlefieldConditionCompilerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary=tempfile.TemporaryDirectory();path=Path(cls.temporary.name)/'empty-board.sqlite3'
        build_fixture_database([FIXTURE],path);cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
    @classmethod
    def tearDownClass(cls):cls.db.close();cls.temporary.cleanup()
    def test_actual_original_has_complete_normal_admission_and_both_material_triggers(self):
        from quorune.semantics import SemanticRegistry
        from quorune.card_programs.adapters import compile_best_available_card_program
        from quorune.card_programs.binding import bind_card_program_runtime
        c=self.db.lookup('Call to the Grave',fuzzy=False);ir=compile_oracle_card(c,capability_registry=self.registry,capability_profile='commander_review')
        self.assertEqual('exact',ir.status,ir.material_residuals);self.assertEqual(2,len(ir.faces[0].nodes))
        semantics=SemanticRegistry();register_generated_programs(self.db,semantics,[c],trust_level='provisional',capability_registry=self.registry,
            capability_profile='commander_review',promote_exact_runtime_handlers=True,promote_exact_trigger_programs=True,
            promote_exact_effect_programs=True,promote_exact_capability_declarations=True)
        p=compile_best_available_card_program(self.db,c,semantic_registry=semantics,capability_registry=self.registry,capability_profile='commander_review')
        b=bind_card_program_runtime(p,capability_registry=self.registry,profile='commander_review');self.assertTrue(b['strict_capability_ready'],b['blockers'])

    def test_genuine_intervening_condition_withdrawal_keeps_unrelated_token_spell(self):
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index,discover_tests
        raw=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        next(r for r in raw['capabilities'] if r['id']=='trigger.condition.fixed_public_state').update(status='blocked',blockers=['condition owner withdrawn'])
        reg=CapabilityRegistry(raw);index=build_index(registry_value=raw,declaration_source=read_evidence_document(ROOT/'platform/capability-evidence-declarations.json'),discovered_test_ids=set(discover_tests(ROOT).values()))
        reg.mark_evidence_verified(validate_capability_evidence_index(index,registry=reg))
        self.assertNotEqual('exact',compile_oracle_card(self.db.lookup('Call to the Grave'),capability_registry=reg,capability_profile='commander_review').status)
        self.assertEqual('exact',compile_oracle_card(self.db.lookup('Raise the Alarm'),capability_registry=reg,capability_profile='commander_review').status)


class EmptyBattlefieldConditionNativeTests(InheritedNativeFixture,unittest.TestCase):
    reject=recipients.TargetRecipientNativeTests.reject
    finish=recipients.TargetRecipientNativeTests.finish
    @classmethod
    def setUpClass(cls):
        cls.temporary=tempfile.TemporaryDirectory();path=Path(cls.temporary.name)/'empty-board-native.sqlite3'
        build_fixture_database([FIXTURE],path);cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Empty battlefield conditions',[DeckEntry('Sliver Hivelord',1,'commander'),DeckEntry('Generic Bound Plains',30)],['Sliver Hivelord'])
    def world(self,seed):
        s=self.session(seed);source=self.add(s.engine,'Call to the Grave')
        private=self.add(s.engine,'Counterspell',seat='C',zone='hand',ref='empty-condition-private')
        self.assert_closed(s,source);return s,source,private
    def end_step(self,s):
        self.until(s,lambda ss:ss.state.active_player=='A' and ss.state.step=='end_step')

    def test_actual_empty_battlefield_sacrifices_source_and_opponent_artifact_creature_prevents_trigger(self):
        for creature in (False,True):
            with self.subTest(creature=creature):
                s,source,private=self.world(6034001+creature)
                if creature:self.add(s.engine,'Memnite',seat='B')
                self.prepare(s);self.checkpoint(s);self.end_step(s)
                self.assertEqual(not creature,any(i.kind=='triggered_ability' and i.source_object_id==source.object_id for i in s.state.stack))
                self.assertIsNone(self.resolve(s));self.assertEqual('battlefield' if creature else 'graveyard',s.state.cards[source.object_id].zone)
                self.finish(s,private)

    def test_actual_intervening_condition_rechecks_after_flash_creature_enters(self):
        s,source,private=self.world(6034010);creature=self.add(s.engine,'Ashcoat Bear',zone='hand')
        self.add(s.engine,'Forest');self.add(s.engine,'Wastes')
        self.prepare(s);self.checkpoint(s);self.end_step(s);self.assertTrue(s.state.stack)
        self.cast_for(s,'A',creature);self.resolve_top(s);self.assertEqual('battlefield',s.state.cards[creature.object_id].zone)
        self.assertIsNone(self.resolve(s));self.assertEqual('battlefield',s.state.cards[source.object_id].zone);self.finish(s,private)

    def test_fresh_controller_only_count_mutant_is_killed_by_opponent_creature(self):
        from unittest.mock import patch
        import quorune.compiler.public_state_fact_queries as queries
        original=queries._query_condition
        def wrong_scope(**kwargs):
            value=original(**kwargs)
            if kwargs.get('at_most') is True and kwargs['amount']==0 and kwargs['scope'] is CharacteristicQuantityScope.ALL_ZONES:
                return replace(value,quantity=replace(value.quantity,scope=CharacteristicQuantityScope.CONTROLLER_ZONE))
            return value
        with patch.object(queries,'_query_condition',wrong_scope):
            result=unittest.TestResult()
            type(self)('test_actual_empty_battlefield_sacrifices_source_and_opponent_artifact_creature_prevents_trigger').run(result)
            self.assertFalse(result.errors,result.errors)
            self.assertTrue(result.failures,'A controller-only count passed the global opponent-creature witness')


    def test_actual_source_sacrifice_does_not_follow_bounced_and_recast_enchantment(self):
        s,source,private=self.world(6034020);bounce=self.add(s.engine,'Boomerang',zone='hand')
        self.add(s.engine,'Vedalken Orrery')
        for i in range(4):self.add(s.engine,'Wastes',ref='empty-recast-waste-'+str(i))
        for i in range(2):self.add(s.engine,'Island',ref='empty-recast-island-'+str(i))
        self.add(s.engine,'Swamp');old=source.logical_object_id
        self.prepare(s);self.checkpoint(s);self.end_step(s);self.assertTrue(s.state.stack)
        self.cast_for(s,'A',bounce,[source.ref]);self.resolve_top(s);self.assertEqual('hand',s.state.cards[source.object_id].zone)
        self.cast_for(s,'A',source);self.resolve_top(s);self.assertNotEqual(old,s.state.cards[source.object_id].logical_object_id)
        self.assertIsNone(self.resolve(s));self.assertEqual('battlefield',s.state.cards[source.object_id].zone);self.finish(s,private)
