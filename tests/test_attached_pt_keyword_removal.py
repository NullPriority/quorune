from __future__ import annotations

"""CR 613.1f/613.7: fixed attached P/T and removal retain independent layers."""

from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from common import ROOT
from quorune.carddb import CardDatabase
from quorune.compiler.continuous_templates import attached_fixed_characteristics_handler
from quorune.deck import DeckDefinition,DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import CapabilityRegistry,load_default_capability_registry
from scripts.build_test_database import build_fixture_database
import test_compound_damage_recipients as controls
from test_attached_static_characteristics import attached_grant_record


class AttachedPtRemovalCompilerTests(unittest.TestCase):
    def test_one_attached_descriptor_retains_both_stat_changes_and_exact_removals(self):
        for line,p,t,abilities in (
            ('Equipped creature gets +10/+10 and loses flying.',10,10,['Flying']),
            ('Enchanted creature gets -6/-0 and loses flying.',-6,0,['Flying']),
            ('Enchanted creature gets +1/+2 and loses flying and first strike.',1,2,['Flying','First Strike'])):
            compiled=attached_fixed_characteristics_handler(line)
            self.assertIsNotNone(compiled,line)
            modifier=compiled[1]['modifier']
            self.assertEqual((p,t),(modifier['power'],modifier['toughness']))
            self.assertEqual(abilities,modifier['remove_abilities'])
            self.assertEqual([],modifier['add_abilities']);self.assertFalse(modifier['remove_all_abilities'])
        legacy=attached_fixed_characteristics_handler('Enchanted creature gets -2/-0 and loses all abilities.')
        self.assertTrue(legacy[1]['modifier']['remove_all_abilities']);self.assertEqual([],legacy[1]['modifier']['remove_abilities'])
        for bad in ('Equipped creature gets +1/+1 and loses flying unless you pay {1}.',
                'Equipped creature gets +1/+1 and loses flying of your choice.',
                'Equipped creature gets +1/+1 and loses toxic 2.',
                'Equipped creature gets +1/+1 and loses an unrepresented keyword.'):
            self.assertIsNone(attached_fixed_characteristics_handler(bad),bad)

    def test_withdrawn_attached_owner_keeps_combined_original_source_residual(self):
        raw=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        next(row for row in raw['capabilities'] if row['id']=='continuous.attached.fixed_characteristics').update(status='blocked',blockers=['withdrawn'])
        source=attached_grant_record('Enchant creature\nEnchanted creature gets -6/-0 and loses flying.')
        self.assertNotEqual('exact',compile_oracle_card(source,capability_registry=CapabilityRegistry(raw),capability_profile='commander_review').status)

    def _assert_equip_preflight_requires_exact_trusted_keyword_line_cost_and_target_contract(self):
        from dataclasses import replace
        from quorune.abilities import parse_activated_abilities
        from quorune.rules.equip_preflight_coverage import trusted_equip_keyword_covers
        session=self.session(730006);source=self.add(session.engine,'Colossus Hammer',zone='hand')
        source=self.db.lookup(source.printed_name)
        ability=parse_activated_abilities(card_name=source.name,oracle_text='Equipped creature gets +10/+10 and loses flying.\nEquip {8}',keywords=('Equip',))[0]
        owner=next(program for program in session.engine.semantics.programs_for_oracle(source.oracle_id) if 'equip' in program.coverage)
        self.assertTrue(trusted_equip_keyword_covers(ability,[owner]))
        for changed in (replace(owner,trust_level='provisional'),replace(owner,coverage=()),
                replace(owner,capability_dependencies=(),capability_closure=None),replace(owner,provenance={**owner.provenance,'source_span':{'line':1}})):
            self.assertFalse(trusted_equip_keyword_covers(ability,[changed]))
        for changed in (replace(ability,cost_text='{3}'),replace(ability,sorcery_speed=False),
                replace(ability,builtin_semantic_key='unowned:equip')):
            self.assertFalse(trusted_equip_keyword_covers(changed,[owner]))
        from quorune.preflight import card_semantic_status
        session.engine.semantics.put(replace(owner,provenance={**owner.provenance,'source_oracle_hash':'0'*64}))
        status=card_semantic_status(source,session.engine.semantics,db=self.db,capability_registry=self.registry,capability_profile='commander_review')
        self.assertNotEqual('fully_playable',status['status'])
        self.assertIn('semantic_source_hash_drift',status['unresolved'])


class AttachedPtRemovalRuntimeTests(unittest.TestCase):
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
        cls.temporary=tempfile.TemporaryDirectory();path=Path(cls.temporary.name)/'attached-removal.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',
            ROOT/'tests/fixtures/attached-pt-keyword-removal-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Attached removal',[DeckEntry('Generic Bound Commander',1,'commander'),
            DeckEntry('Generic Bound Plains',30)],['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls):cls.db.close();cls.temporary.cleanup()

    def test_equip_preflight_requires_exact_trusted_keyword_line_cost_and_target_contract(self):
        AttachedPtRemovalCompilerTests._assert_equip_preflight_requires_exact_trusted_keyword_line_cost_and_target_contract(self)

    def equip(self,session,source,target):
        for _ in range(16):
            if session.state.priority_player=='A':break
            self.assertTrue(session.act(session.pending_principals()[0],{'action_id':'pass'}).ok)
        else:self.fail('Equipment controller did not receive priority')
        action=next(row for row in self.actions(session) if row['id'].startswith('activate:'+source.ref+':'))
        before=authoritative_state_hash(session.state)
        denied=session.act('pilot:D',{'action_id':action['id'],'targets':[target.ref],'pay':'auto'})
        self.assertFalse(denied.ok);self.assertEqual(before,authoritative_state_hash(session.state))
        accepted=session.act('pilot:A',{'action_id':action['id'],'targets':[target.ref],'pay':'auto'})
        self.assertTrue(accepted.ok,accepted.summary)
        session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
        return session

    def test_original_hammer_cast_equip_reattach_and_departure_keep_both_layers(self):
        session=self.session(730001)
        source=self.add(session.engine,'Colossus Hammer',zone='hand')
        first=self.add(session.engine,'Generic Bound Body',ref='hammer-first');first.counters['flying']=1
        second=self.add(session.engine,'Generic Bound Body',ref='hammer-second');second.counters['flying']=1
        removal=self.add(session.engine,'Naturalize',seat='B',zone='hand')
        private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand')
        session.state.players['B'].mana_pool.update({'G':1,'C':1})
        self.prepare(session,mana={'C':17});self.checkpoint(session)
        self.assertTrue(self.binding(session,source)['strict_capability_ready'])
        self.cast(session,'A',source);self.assertIsNone(self.resolve(session))
        session=self.equip(session,source,first)
        self.assertEqual((12,16),(session.engine._numeric_stat(first.object_id,'power'),session.engine._numeric_stat(first.object_id,'toughness')))
        self.assertNotIn('flying',session.engine._combat_keywords(session.state.cards[first.object_id]))
        self.assertIn('flying',session.engine._combat_keywords(session.state.cards[second.object_id]))
        session=self.equip(session,source,second)
        self.assertEqual(2,session.engine._numeric_stat(first.object_id,'power'));self.assertIn('flying',session.engine._combat_keywords(session.state.cards[first.object_id]))
        self.assertEqual(12,session.engine._numeric_stat(second.object_id,'power'));self.assertNotIn('flying',session.engine._combat_keywords(session.state.cards[second.object_id]))
        self.cast(session,'B',removal,[session.state.cards[source.object_id].ref])
        session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
        self.assertEqual(2,session.engine._numeric_stat(second.object_id,'power'));self.assertIn('flying',session.engine._combat_keywords(session.state.cards[second.object_id]))
        self.private_replay(session,private)

    def test_original_coils_applies_negative_stats_and_removal_but_later_jump_restores_flying(self):
        session=self.session(730002)
        source=self.add(session.engine,'Tightening Coils',zone='hand')
        target=self.add(session.engine,'Generic Bound Body',seat='B',ref='coils-target');target.counters['flying']=1
        jump=self.add(session.engine,'Jump',seat='C',zone='hand')
        removal=self.add(session.engine,'Naturalize',seat='B',zone='hand')
        private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand')
        session.state.players['B'].mana_pool.update({'G':1,'C':1});session.state.players['C'].mana_pool.update({'U':1})
        self.prepare(session,mana={'U':1,'C':1});self.checkpoint(session)
        self.assertTrue(self.binding(session,source)['strict_capability_ready'])
        self.cast(session,'A',source,[target.ref]);session=self.replay(session,load=True)
        self.assertIsNone(self.resolve(session));self.assertEqual(-4,session.engine._numeric_stat(target.object_id,'power'))
        self.assertNotIn('flying',session.engine._combat_keywords(session.state.cards[target.object_id]))
        self.cast(session,'C',jump,[target.ref]);session=self.replay(session,load=True)
        self.assertIsNone(self.resolve(session));self.assertIn('flying',session.engine._combat_keywords(session.state.cards[target.object_id]))
        self.assertEqual(-4,session.engine._numeric_stat(target.object_id,'power'))
        self.cast(session,'B',removal,[session.state.cards[source.object_id].ref]);self.assertIsNone(self.resolve(session))
        self.assertEqual(2,session.engine._numeric_stat(target.object_id,'power'))
        self.private_replay(session,private)

    def test_losing_combined_modifier_producer_is_killed_by_fresh_original_coils(self):
        from quorune.compiler.continuous_templates import _attached_compound_modifier
        method='test_original_coils_applies_negative_stats_and_removal_but_later_jump_restores_flying'
        # Returning None permits a complete alternate lowering. Omit one real
        # contribution while retaining this producer, so fallback cannot mask it.
        for field,value in (('power',0),('remove_abilities',[])):
            def omitted(body):
                modifier=_attached_compound_modifier(body)
                return None if modifier is None else {**modifier,field:value}
            with self.subTest(field=field),patch(
                    'quorune.compiler.continuous_templates._attached_compound_modifier',side_effect=omitted):
                with self.assertRaises(AssertionError):getattr(type(self)(method),method)()

    def test_original_magebane_retains_noncombat_prevention_and_complete_modifier(self):
        session=self.session(730003)
        source=self.add(session.engine,'Magebane Armor',zone='hand')
        target=self.add(session.engine,'Generic Bound Body',ref='magebane-equipped');target.counters['flying']=1
        other=self.add(session.engine,'Generic Bound Body',seat='D',ref='magebane-other')
        first=self.add(session.engine,'Shock',seat='B',zone='hand',ref='magebane-first-shock')
        second=self.add(session.engine,'Shock',seat='B',zone='hand',ref='magebane-second-shock')
        private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand')
        session.state.players['B'].mana_pool.update({'R':2})
        self.prepare(session,mana={'C':5});self.checkpoint(session)
        self.assertTrue(self.binding(session,source)['strict_capability_ready'])
        self.cast(session,'A',source);self.assertIsNone(self.resolve(session))
        session=self.equip(session,source,target)
        self.assertEqual((4,10),(session.engine._numeric_stat(target.object_id,'power'),session.engine._numeric_stat(target.object_id,'toughness')))
        self.assertNotIn('flying',session.engine._combat_keywords(session.state.cards[target.object_id]))
        self.cast(session,'B',first,[target.ref]);session=self.replay(session,load=True)
        self.assertIsNone(self.resolve(session));self.assertEqual(0,session.state.cards[target.object_id].marked_damage)
        self.cast(session,'B',second,[other.ref]);self.assertIsNone(self.resolve(session))
        self.assertEqual(2,session.state.cards[other.object_id].marked_damage)
        self.private_replay(session,private)

    def test_three_originals_have_complete_preflight_and_bound_attached_authority(self):
        from dataclasses import replace
        from quorune.card_programs.binding import bind_semantic_program_runtime
        from quorune.preflight import card_semantic_status
        session=self.session(730004)
        for name in ('Colossus Hammer','Tightening Coils','Magebane Armor'):
            source=self.add(session.engine,name,zone='hand')
            self.assertTrue(self.binding(session,source)['strict_capability_ready'],name)
            status=card_semantic_status(self.db.lookup(name),session.engine.semantics,db=self.db,
                capability_registry=self.registry,capability_profile='commander_review')
            self.assertEqual('fully_playable',status['status'],(name,status['unresolved']))
            cap='continuous.attached.fixed_characteristics'
            program=next(p for p in session.engine.semantics.programs_for_oracle(source.oracle_id) if cap in p.capability_dependencies)
            dependencies=tuple(c for c in program.capability_dependencies if c!=cap)
            missing=replace(program,capability_dependencies=dependencies,
                capability_closure=self.registry.closure(dependencies,profile='commander_review').to_dict() if dependencies else None)
            bound=bind_semantic_program_runtime(missing,capability_registry=self.registry,profile='commander_review')
            self.assertFalse(bound['strict']);self.assertIn('capability:undeclared_runtime_dependency:'+cap,bound['blockers'])
