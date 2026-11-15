"""Retain complete original-card admission and the actual instruction boundaries."""
import json,tempfile,unittest
from pathlib import Path
from dataclasses import replace
from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
import test_recipient_control as seat_actions
import test_reflexive_payments as reflexive
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition,DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.rules.capabilities import load_default_capability_registry
from quorune.semantics import SemanticRegistry
from quorune.card_programs.adapters import _compile_best_available_card_program_from_ir
from quorune.card_programs.binding import bind_card_program_runtime,bind_semantic_program_runtime
from scripts.build_test_database import build_fixture_database

ORIGINALS=('Irreverent Gremlin','Savai Thundermane','Drillworks Mole','Secrets of the Golden City',
    "Vona's Hunger",'Pride of Conquerors','Golden Demise')


class CensusAdmissionRetentionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory();path=Path(cls.temp.name)/'retention.sqlite3'
        build_fixture_database(ROOT/'tests/fixtures/census-admission-retention-cards.json',path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()

    @classmethod
    def tearDownClass(cls):cls.db.close();cls.temp.cleanup()

    def program(self,name):
        record=self.db.lookup(name);ir=compile_oracle_card(record,capability_registry=self.registry,capability_profile='commander_review')
        program=_compile_best_available_card_program_from_ir(self.db,record,ir,semantic_registry=SemanticRegistry(),
            capability_registry=self.registry,capability_profile='commander_review')
        return ir,program

    def test_complete_original_sources_keep_stored_and_runtime_capability_closure(self):
        for name in ORIGINALS:
            with self.subTest(name=name):
                ir,p=self.program(name);self.assertEqual('exact',ir.status,ir.material_residuals)
                self.assertEqual('capability_closed',p.trust_closure['trust_basis'],p.trust_closure['blockers'])
                b=bind_card_program_runtime(p,capability_registry=self.registry,profile='commander_review')
                self.assertTrue(b['strict_capability_ready'],b['blockers'])

    def test_payment_and_reflexive_life_results_stay_inside_their_owned_instruction(self):
        _,p=self.program('Savai Thundermane');trigger,=p.abilities;payment,=trigger.effects
        self.assertEqual('offer_optional_mana_payment',payment['op'])
        self.assertEqual(['damage','life'],[e['op'] for e in payment['reflexive_program']['effects']])
        _,p=self.program('Irreverent Gremlin');trigger=next(a for a in p.abilities if a.event=='creature.enter')
        optional,=trigger.effects;self.assertEqual('offer_optional_effect',optional['op'])
        cost,=optional['effects'];self.assertEqual('offer_optional_mana_payment',cost['op'])
        self.assertEqual(8,cost['payment']['schema_version']);self.assertEqual('discard',cost['payment']['kind'])
        self.assertEqual(['draw'],[e['op'] for e in cost['effects']]);self.assertEqual(3,trigger.trigger_limit['schema_version'])

    def test_ascend_prefix_and_commander_predicate_owners_cannot_be_forged_or_omitted(self):
        _,p=self.program('Secrets of the Golden City');spell,=p.abilities
        self.assertTrue(bind_semantic_program_runtime(spell,capability_registry=self.registry,profile='commander_review')['strict'])
        for prefix in ({'op':'ascend','player':'$target.0'}, {'op':'ascend','player':'$controller','extra':True},
                {'op':'life','player':'$controller','delta':1}):
            with self.subTest(prefix=prefix):
                bad=replace(spell,effects=[prefix,*spell.effects[1:]])
                self.assertFalse(bind_semantic_program_runtime(bad,capability_registry=self.registry,profile='commander_review')['strict'])
        _,p=self.program('Drillworks Mole');ability,=p.abilities;cap='state_query.commander.permanent_designation'
        self.assertIn(cap,ability.capability_dependencies)
        deps=tuple(x for x in ability.capability_dependencies if x!=cap)
        bad=replace(ability,capability_dependencies=deps,capability_closure=self.registry.closure(deps,profile='commander_review').to_dict())
        bound=bind_semantic_program_runtime(bad,capability_registry=self.registry,profile='commander_review')
        self.assertFalse(bound['strict']);self.assertIn('capability:undeclared_runtime_dependency:'+cap,bound['blockers'])


class CensusAdmissionNativeTests(InheritedNativeFixture,unittest.TestCase):
    reject=seat_actions.RecipientControlNativeTests.reject
    finish=seat_actions.RecipientControlNativeTests.finish
    choose=reflexive.ReflexivePaymentRuntimeTests.choose

    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory();path=Path(cls.temp.name)/'native.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',
            ROOT/'tests/fixtures/census-admission-retention-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Census retention',[DeckEntry('Generic Bound Commander',1,'commander'),
            DeckEntry('Generic Bound Plains',30)],['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls):cls.db.close();cls.temp.cleanup()

    def test_actual_savai_life_is_paid_reflexive_result_and_illegal_target_loses_both_results(self):
        for mode in ('decline','resolve','bounce'):
            with self.subTest(mode=mode):
                s=self.session(603240+len(mode));source=self.add(s.engine,'Savai Thundermane')
                cycler=self.add(s.engine,'Lonely Sandbar',zone='hand');target=self.add(s.engine,'Grizzly Bears',seat='B')
                bounce=self.add(s.engine,'Unsummon',seat='B',zone='hand');private=self.add(s.engine,'Counterspell',seat='C',zone='hand')
                self.assert_closed(s,source);self.assert_closed(s,cycler)
                self.prepare(s,mana={'C':4,'U':1});s.state.players['B'].mana_pool['U']=1;self.checkpoint(s)
                actions=s.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
                offer=next(a for a in actions if a['id'].startswith('activate:'+cycler.ref+':'))
                command={'action_id':offer['id'],'pay':'auto'};self.reject(s,'D',command)
                result=s.act('pilot:A',command);self.assertTrue(result.ok,result.summary)
                self.assertEqual('semantic.choice',self.resolve(s).kind);self.assertEqual(40,s.state.players['A'].life)
                s=self.replay(s,load=True);self.choose(s,pay=mode!='decline')
                if mode!='decline':
                    self.assertEqual('semantic.target',s.state.pending_decision.kind)
                    self.assertEqual(40,s.state.players['A'].life);self.choose(s,targets=[target.ref])
                    if mode=='bounce':self.cast(s,'B',bounce,[target.ref]);self.resolve_top(s)
                self.assertIsNone(self.resolve(s))
                self.assertEqual(42 if mode=='resolve' else 40,s.state.players['A'].life)
                self.assertEqual('graveyard' if mode=='resolve' else 'hand' if mode=='bounce' else 'battlefield',
                    s.state.cards[target.object_id].zone);self.finish(s,private)

    def test_actual_mole_accepts_only_the_designated_commander_and_preserves_omitted_target_source_counter(self):
        for select in (True,False):
            with self.subTest(select=select):
                s=self.session(122240+select);source=self.add(s.engine,'Drillworks Mole')
                source.acquired_control_turn_count=-1;ordinary=self.add(s.engine,'Grizzly Bears')
                private=self.add(s.engine,'Counterspell',seat='C',zone='hand')
                commander=next(c for c in s.state.cards.values() if c.owner=='A' and c.is_commander)
                self.assert_closed(s,source);self.prepare(s,mana={'C':8,'W':2});self.checkpoint(s)
                self.cast(s,'A',commander);self.assertIsNone(self.resolve(s));self.priority(s,'A')
                ability=next(a for a in s.engine._activated_abilities(s.state.cards[source.object_id]) if a.tap_source)
                action='activate:'+source.ref+':'+ability.ability_id
                self.reject(s,'A',{'action_id':action,'targets':[ordinary.ref],'pay':'auto'})
                command={'action_id':action,'targets':[s.state.cards[commander.object_id].ref] if select else [],'pay':'auto'}
                self.reject(s,'D',command);result=s.act('pilot:A',command);self.assertTrue(result.ok,result.summary)
                s=self.replay(s,load=True);self.assertIsNone(self.resolve(s))
                self.assertEqual(1,s.state.cards[source.object_id].counters.get('+1/+1',0))
                self.assertEqual(1 if select else 0,s.state.cards[commander.object_id].counters.get('+1/+1',0));self.finish(s,private)


if __name__=='__main__':unittest.main()
