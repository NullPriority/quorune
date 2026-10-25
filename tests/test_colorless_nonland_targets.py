from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from pathlib import Path
import json,tempfile,unittest
from unittest.mock import patch

from common import ROOT
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition,DeckEntry
from quorune.compiler.direct_target import direct_permanent_target_spec
from quorune.oracle_ir import compile_oracle_card
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
import test_modal_activation_admission as controls
from quorune.rules.evidence_storage import read_evidence_document

CAP='target.permanent.characteristic_predicate'
NAMES=('Goblin Cratermaker',)


class ColorlessNonlandTargetTests(unittest.TestCase):
    register_fixture_cards_on_add=True
    session=controls.ModalActivationAdmissionTests.session
    add=controls.ModalActivationAdmissionTests.add
    prepare=controls.ModalActivationAdmissionTests.prepare
    checkpoint=controls.ModalActivationAdmissionTests.checkpoint
    resolve=controls.ModalActivationAdmissionTests.resolve
    replay=controls.ModalActivationAdmissionTests.replay
    binding=controls.ModalActivationAdmissionTests.binding
    actions=controls.ModalActivationAdmissionTests.actions
    activation=controls.ModalActivationAdmissionTests.activation
    cast=controls.ModalActivationAdmissionTests.cast
    reject=controls.ModalActivationAdmissionTests.reject
    private_replay=controls.ModalActivationAdmissionTests.private_replay

    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory();path=Path(cls.temp.name)/'colorless-nonland.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',ROOT/'tests/fixtures/colorless-nonland-target-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Current colors and nonland types',[DeckEntry('Generic Bound Commander',1,'commander'),DeckEntry('Generic Bound Plains',30)],['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls):cls.db.close();cls.temp.cleanup()

    def test_compiler_conjoins_existing_colorless_and_excluded_type_fields(self):
        parsed=direct_permanent_target_spec('target colorless nonland permanent')
        self.assertIsNotNone(parsed)
        self.assertEqual({'zones':['battlefield'],'categories':['permanent'],'count':1,'types_none':['land'],'colorless':True},parsed.to_target_schema())
        for text in ('target colorless nonartifact creature','target colorless noncreature artifact','target colorless nonenchantment permanent'):
            self.assertIsNotNone(direct_permanent_target_spec(text),text)
        for text in ('target colorless nonland land','target colorless nonartifact artifact','target colorless nonplanet permanent',
                'target colorless noncreature nonland permanent','target colorless colorless permanent'):
            self.assertIsNone(direct_permanent_target_spec(text),text)

    def test_original_full_modal_admission_has_independent_color_predicate_minimum(self):
        from quorune.card_programs.binding import bind_semantic_program_runtime
        s=self.session(798000);source=self.add(s.engine,'Goblin Cratermaker')
        b=self.binding(s,source);self.assertTrue(b['strict_capability_ready'],b['blockers'])
        self.assertEqual('exact',compile_oracle_card(self.db.lookup('Goblin Cratermaker'),capability_registry=self.registry,capability_profile='commander_review').status)
        p=next(p for p in s.engine.semantics.programs_for_oracle(source.oracle_id) if p.target_schema)
        deps=tuple(d for d in p.capability_dependencies if d!=CAP)
        changed=replace(p,capability_dependencies=deps,capability_closure=self.registry.closure(deps,profile='commander_review').to_dict())
        bound=bind_semantic_program_runtime(changed,capability_registry=self.registry,profile='commander_review')
        self.assertFalse(bound['strict']);self.assertIn('capability:undeclared_runtime_dependency:'+CAP,bound['blockers'])

    def test_original_destroy_mode_uses_game_colors_and_excludes_all_lands(self):
        for index,name in enumerate(('Sol Ring','Forerunner of Slaughter')):
            s=self.session(798010+index);source=self.add(s.engine,'Goblin Cratermaker')
            target=self.add(s.engine,name,seat='B');land=self.add(s.engine,'Great Furnace',seat='B')
            colored=self.add(s.engine,'Generic Bound Body',seat='B')
            private=self.add(s.engine,'Generic Bound Growth',seat='C',zone='hand')
            self.prepare(s,mana={'C':1});self.checkpoint(s);action=self.activation(s,source);self.assertIsNotNone(action)
            group=action['target_schema']['mode_schemas']['mode_2']['groups'][0]
            self.assertIn(target.ref,group['legal_refs']);self.assertNotIn(land.ref,group['legal_refs']);self.assertNotIn(colored.ref,group['legal_refs'])
            for bad in (land,colored):self.reject(s,{'action_id':action['id'],'modes':['mode_2'],'targets':[bad.ref],'pay':'auto'})
            command={'action_id':action['id'],'modes':['mode_2'],'targets':[target.ref],'pay':'auto'}
            self.reject(s,command,'pilot:D');result=s.act('pilot:A',command);self.assertTrue(result.ok,result.summary)
            self.assertEqual('graveyard',s.state.cards[source.object_id].zone);self.assertEqual(0,sum(s.state.players['A'].mana_pool.values()))
            s=self.replay(s,load=True);self.assertIsNone(self.resolve(s))
            self.assertEqual('graveyard',s.state.cards[target.object_id].zone)
            self.assertEqual('battlefield',s.state.cards[land.object_id].zone);self.assertEqual('battlefield',s.state.cards[colored.object_id].zone)
            self.private_replay(s,private)

    def test_original_damage_mode_remains_available_without_colorless_nonland_target(self):
        s=self.session(798020);source=self.add(s.engine,'Goblin Cratermaker');target=self.add(s.engine,'Generic Bound Body',seat='B')
        self.add(s.engine,'Great Furnace',seat='B');private=self.add(s.engine,'Generic Bound Growth',seat='C',zone='hand')
        self.prepare(s,mana={'C':1});self.checkpoint(s);action=self.activation(s,source);self.assertIsNotNone(action)
        self.assertEqual(['mode_1'],action['target_schema']['legal_modes'])
        result=s.act('pilot:A',{'action_id':action['id'],'modes':['mode_1'],'targets':[target.ref],'pay':'auto'})
        self.assertTrue(result.ok,result.summary);self.assertEqual('graveyard',s.state.cards[source.object_id].zone)
        s=self.replay(s,load=True);self.assertIsNone(self.resolve(s));self.assertEqual(2,s.state.cards[target.object_id].marked_damage)
        self.assertEqual('battlefield',s.state.cards[target.object_id].zone);self.private_replay(s,private)

    def test_current_color_change_revalidates_the_original_target_without_mode_switch_or_refund(self):
        s=self.session(798030);source=self.add(s.engine,'Goblin Cratermaker');target=self.add(s.engine,'Forerunner of Slaughter',seat='B')
        gift=self.add(s.engine,'Gift of Tusks',seat='C',zone='hand');private=self.add(s.engine,'Generic Bound Growth',seat='C',zone='hand')
        s.state.players['C'].mana_pool['U']=1;self.prepare(s,mana={'C':1});self.checkpoint(s)
        result=s.act('pilot:A',{'action_id':self.activation(s,source)['id'],'modes':['mode_2'],'targets':[target.ref],'pay':'auto'})
        self.assertTrue(result.ok,result.summary);logical=s.state.cards[target.object_id].logical_object_id
        self.cast(s,'C',gift,[target.ref]);s=self.replay(s,load=True);self.assertIsNone(self.resolve(s))
        current=s.state.cards[target.object_id];self.assertEqual(logical,current.logical_object_id);self.assertEqual('battlefield',current.zone)
        self.assertEqual(0,current.marked_damage);self.assertEqual('graveyard',s.state.cards[source.object_id].zone)
        self.assertEqual(0,sum(s.state.players['A'].mana_pool.values()));self.private_replay(s,private)

    def test_verified_withdrawal_and_fresh_conjunction_omission_reject(self):
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from scripts.update_capability_evidence import build_index,discover_tests
        raw=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        next(c for c in raw['capabilities'] if c['id']==CAP).update(status='blocked',blockers=['withdrawn current characteristics'],dependency_fail_closed_status='not_run')
        reg=CapabilityRegistry(raw);decl=read_evidence_document(ROOT/'platform/capability-evidence-declarations.json')
        index=build_index(registry_value=raw,declaration_source=decl,discovered_test_ids=set(discover_tests(ROOT).values()))
        reg.mark_evidence_verified(validate_capability_evidence_index(index,registry=reg))
        ir=compile_oracle_card(self.db.lookup('Goblin Cratermaker'),capability_registry=reg,capability_profile='commander_review')
        blocks=[b for r in ir.material_residuals for b in r.blockers]
        self.assertTrue(any('status:'+CAP+':blocked' in b for b in blocks),blocks);self.assertFalse(any('evidence_index:unverified' in b for b in blocks))
        from quorune.compiler import direct_target
        method='test_original_full_modal_admission_has_independent_color_predicate_minimum'
        with patch.object(direct_target,'_excluded_direct_type_fields',return_value=None):
            with self.assertRaises(AssertionError):getattr(type(self)(method),method)()
