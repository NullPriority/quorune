"""CR601.2c/f,602.2b: one chosen creature supplies a locked current price."""
import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from common import ROOT
import test_recipient_control as recipients
import test_fixed_creature_power_damage as records
from inherited_composition_fixture import InheritedNativeFixture
from quorune.activation_target_values import (
    TargetActivationReduction, TARGET_ACTIVATION_VALUE_CAPABILITY as CAPABILITY,
)
from quorune.rules.activation.targeted_costs import single_creature_target_price_plan
from quorune.activation_cost_modifiers import ability_local_mana_reduction_from_dict
from quorune.activated_ability_descriptor import ABILITY_LOCAL_MANA_REDUCTION_FIELD
from quorune.carddb import CardDatabase
from quorune.card_programs.adapters import _compile_best_available_card_program_from_ir
from quorune.card_programs.binding import bind_card_program_runtime, bind_semantic_program_runtime
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.rules.capabilities import load_default_capability_registry
from quorune.semantics import SemanticRegistry
from scripts.build_test_database import build_fixture_database, compact_ci_fixture_paths

NAMES = ('Raft Security Officer', 'Belt of Giant Strength', 'Dragonfire Blade')
CREATURE = {'zones':['battlefield'],'categories':['permanent'],'count':1,'types_any':['creature']}


class LocalTargetPriceCompilerTests(unittest.TestCase):
    def test_typed_target_prices_use_independent_boundary_tables_and_closed_fields(self):
        for metric, values in (
            (TargetActivationReduction('power'), [(-2,0),(0,0),(3,3),(12,12)]),
            (TargetActivationReduction('power_at_most',1,3), [(-2,1),(3,1),(4,0)])):
            self.assertEqual(metric, ability_local_mana_reduction_from_dict(metric.to_dict()))
            for power, expected in values:self.assertEqual(expected, metric.amount(power=power,colors=()))
        colors = TargetActivationReduction('color_count')
        for given, expected in (((),0),(('C',),0),(('U','U'),1),(tuple('WUBRG'),5)):
            self.assertEqual(expected, colors.amount(power=0,colors=given))
        for changes in ({'schema_version':True},{'generic':True},{'generic':0},
                {'target_metric':'mana_value'},{'maximum_power':3},{'extra':1}):
            with self.subTest(changes=changes),self.assertRaises(ValueError):
                ability_local_mana_reduction_from_dict({**colors.to_dict(),**changes})
        self.assertEqual(1,len(single_creature_target_price_plan(CREATURE).groups))
        for schema in ({**CREATURE,'count':2},{**CREATURE,'up_to':1},
                {**CREATURE,'zones':['graveyard']},{**CREATURE,'types_any':['artifact']},
                {'groups':[dict(CREATURE,id='one'),dict(CREATURE,id='two')]},
                {'modes':{'first':CREATURE},'mode_count':1}):
            with self.subTest(schema=schema),self.assertRaises(ValueError):single_creature_target_price_plan(schema)

    def test_optional_multiple_noncreature_and_unknown_price_grammar_stay_residual(self):
        registry = load_default_capability_registry()
        for body in ('Tap up to one target creature.', 'Tap two target creatures.', 'Tap target artifact.'):
            record = records.card_record('{2}, {T}: '+body+' This ability costs {1} less to activate if it targets a creature with power 3 or less.',type_line='Creature — Human')
            self.assertNotEqual('exact',compile_oracle_card(record,capability_registry=registry).status,body)
        for tail in ('This ability costs {1} less to activate if it targets two creatures you control.',
                'This ability costs {X} less to activate, where X is the toughness of the creature it targets.',
                'This ability costs {1} less to activate for each color of the creature it targets. Invent an unknown result.'):
            record = records.card_record('{2}, {T}: Tap target creature. '+tail,type_line='Creature — Human')
            self.assertNotEqual('exact',compile_oracle_card(record,capability_registry=registry).status,tail)


class LocalTargetPriceNativeTests(InheritedNativeFixture,unittest.TestCase):
    reject = recipients.RecipientControlNativeTests.reject
    finish = recipients.RecipientControlNativeTests.finish

    @classmethod
    def setUpClass(cls):
        names = {*NAMES,'Sliver Hivelord','Generic Bound Plains','Grizzly Bears','Counterspell','Sol Ring',
            'Scion of the Wild','Wild Cantor','Fusion Elemental','Ornithopter','Thoughtlace','Giant Growth','Fervor',
            'Hill Giant','Cloudshift','Plains','Wastes','Mountain','Lightning Bolt','Spatial Contortion'}
        paths = (*compact_ci_fixture_paths(root=ROOT),ROOT/'tests/fixtures/bound-effect-program-cards.json')
        cards = {r['name']:r for p in paths for r in json.loads(p.read_text(encoding='utf-8'))['cards'] if r['name'] in names}
        assert set(cards)==names,names-set(cards)
        cls.temporary=tempfile.TemporaryDirectory();folder=Path(cls.temporary.name)
        value=json.loads((ROOT/'tests/fixtures/recipient-control-cards.json').read_text(encoding='utf-8'))
        value.update(cards=list(cards.values()),rulings=[])
        fixture=folder/'target-prices.json';fixture.write_text(json.dumps(value),encoding='utf-8')
        path=folder/'target-prices.sqlite3';build_fixture_database([fixture],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Local target prices',[DeckEntry('Sliver Hivelord',1,'commander'),
            DeckEntry('Generic Bound Plains',30)],['Sliver Hivelord'])

    def scene(self,name,*,mana,before):
        s=self.session(2021034+len(name));source=self.add(s.engine,name)
        body=self.add(s.engine,'Grizzly Bears')
        private=self.add(s.engine,'Counterspell',seat='C',zone='hand')
        extra=before(s,source,body)
        self.assert_closed(s,source);s.state.config.semantic_policy='trusted_only'
        self.prepare(s,mana=mana);self.checkpoint(s)
        return s,source,body,private,extra

    def offer(self,s,source):
        self.priority(s,'A')
        return next((r for r in s.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
            if r['id'].startswith('activate:'+source.ref+':') and not r.get('mana_ability')),None)

    def quoted(self,row,ref):
        if 'target_costs' not in row:return {row['cost_summary']['m'].get('GENERIC',0)}
        return {p['requirements'].get('GENERIC',0) for p in row['target_costs']['prices']
            if any(ref in refs for refs in p['targets'].values())}

    def activate(self,s,source,target,*,payment=None):
        row=self.offer(s,source);self.assertIsNotNone(row)
        command={'action_id':row['id'],'targets':[target.ref],'pay':'auto'}
        if payment is not None:command.update(pay='manual',payment=payment)
        self.reject(s,'B',command)
        result=s.act('pilot:A',command);self.assertTrue(result.ok,result.summary)

    def test_originals_bind_normally_and_target_price_minima_survive_missing_labels(self):
        for name in NAMES:
            with self.subTest(name=name):
                c=self.db.lookup(name,fuzzy=False);ir=compile_oracle_card(c,capability_registry=self.registry,capability_profile='commander_review')
                self.assertEqual('exact',ir.status,ir.material_residuals)
                p=_compile_best_available_card_program_from_ir(self.db,c,ir,semantic_registry=SemanticRegistry(),
                    capability_registry=self.registry,capability_profile='commander_review')
                bound=bind_card_program_runtime(p,capability_registry=self.registry,profile='commander_review')
                self.assertTrue(bound['strict_capability_ready'],bound['blockers']);self.assertEqual('capability_closed',bound['trust_basis'])
                s=self.session(202103401);source=self.add(s.engine,name,zone='hand')
                programs=[p for p in s.engine.semantics.programs_for_oracle(source.oracle_id)
                    if p.event=='activate' and (p.cost_schema or {}).get(ABILITY_LOCAL_MANA_REDUCTION_FIELD) is not None]
                self.assertEqual(1,len(programs));program,=programs
                unlabelled=replace(program,coverage=[])
                self.assertTrue(bind_semantic_program_runtime(unlabelled,capability_registry=self.registry,profile='commander_review')['strict'])
                deps=tuple(c for c in program.capability_dependencies if c!=CAPABILITY)
                changed=replace(unlabelled,capability_dependencies=deps,
                    capability_closure=self.registry.closure(deps,profile='commander_review').to_dict())
                result=bind_semantic_program_runtime(changed,capability_registry=self.registry,profile='commander_review')
                self.assertFalse(result['strict']);self.assertIn('capability:undeclared_runtime_dependency:'+CAPABILITY,result['blockers'])
                malformed=replace(unlabelled,target_schema={**CREATURE,'count':2})
                self.assertFalse(bind_semantic_program_runtime(malformed,capability_registry=self.registry,profile='commander_review')['strict'])
        self.verified_owner_withdrawal()

    def verified_owner_withdrawal(self):
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index, discover_tests
        raw=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        next(r for r in raw['capabilities'] if r['id']==CAPABILITY).update(status='blocked',blockers=['target-value owner withdrawn'])
        registry=CapabilityRegistry(raw)
        index=build_index(registry_value=raw,declaration_source=read_evidence_document(ROOT/'platform/capability-evidence-declarations.json'),
            discovered_test_ids=set(discover_tests(ROOT).values()))
        registry.mark_evidence_verified(validate_capability_evidence_index(index,registry=registry))
        for name in NAMES:
            ir=compile_oracle_card(self.db.lookup(name,fuzzy=False),capability_registry=registry,capability_profile='commander_review')
            self.assertNotEqual('exact',ir.status)
            blockers=[b for r in ir.material_residuals for b in r.blockers]
            self.assertTrue(any('status:'+CAPABILITY+':blocked' in b for b in blockers),blockers)
            self.assertFalse(any('evidence_index:unverified' in b for b in blockers))
        self.assertEqual('exact',compile_oracle_card(self.db.lookup('Grizzly Bears',fuzzy=False),capability_registry=registry).status)

    def test_actual_raft_reads_layer_seven_power_and_does_not_tap_a_blinked_copy(self):
        def before(s,source,body):
            target=self.add(s.engine,'Hill Giant',seat='B');target.owner='D'
            self.add(s.engine,'Fervor');self.add(s.engine,'Plains',seat='B')
            return {'target':target,'growth':self.add(s.engine,'Giant Growth',zone='hand'),
                'blink':self.add(s.engine,'Cloudshift',seat='B',zone='hand')}
        s,source,body,private,extra=self.scene('Raft Security Officer',mana={'C':2,'G':1},before=before)
        target=extra['target'];row=self.offer(s,source)
        self.assertEqual({1},self.quoted(row,target.ref))
        self.cast(s,'A',extra['growth'],[target.ref]);self.assertIsNone(self.resolve(s))
        self.assertEqual(6,s.engine._numeric_stat(target.object_id,'power'))
        row=self.offer(s,source);self.assertEqual({2},self.quoted(row,target.ref))
        self.reject(s,'A',{'action_id':row['id'],'targets':[target.ref],'pay':'manual','payment':{'C':1}})
        logical=s.state.cards[target.object_id].logical_object_id
        self.activate(s,source,target,payment={'C':2})
        self.assertTrue(s.state.cards[source.object_id].tapped);self.assertFalse(s.state.cards[target.object_id].tapped)
        self.cast(s,'B',extra['blink'],[target.ref]);self.resolve_top(s)
        self.assertNotEqual(logical,s.state.cards[target.object_id].logical_object_id)
        s=self.replay(s,load=True);self.assertIsNone(self.resolve(s))
        self.assertFalse(s.state.cards[target.object_id].tapped);self.assertEqual(0,s.state.players['A'].mana_pool['C'])
        self.finish(s,private)

    def test_actual_belt_keeps_affordable_targets_and_reads_growth_before_its_own_setting(self):
        def before(s,source,body):
            return {'large':self.add(s.engine,'Fusion Elemental'),'zero':self.add(s.engine,'Ornithopter'),
                'growth':self.add(s.engine,'Giant Growth',zone='hand')}
        s,source,body,private,extra=self.scene('Belt of Giant Strength',mana={'C':2,'G':1},before=before)
        target=extra['large'];row=self.offer(s,source)
        self.assertEqual({2},self.quoted(row,target.ref))
        self.assertNotIn(body.ref,json.dumps(row['target_schema']));self.assertNotIn(extra['zero'].ref,json.dumps(row['target_schema']))
        self.reject(s,'A',{'action_id':row['id'],'targets':[body.ref],'pay':'auto'})
        self.cast(s,'A',extra['growth'],[target.ref]);self.assertIsNone(self.resolve(s))
        row=self.offer(s,source);self.assertEqual({0},self.quoted(row,target.ref))
        self.activate(s,source,target,payment={});self.assertIsNone(s.state.cards[source.object_id].attached_to)
        s=self.replay(s,load=True);self.assertIsNone(self.resolve(s))
        self.assertEqual(target.object_id,s.state.cards[source.object_id].attached_to)
        self.assertEqual((13,13),tuple(s.engine._numeric_stat(target.object_id,stat) for stat in ('power','toughness')))
        self.assertEqual(2,s.state.players['A'].mana_pool['C']);self.finish(s,private)

    def test_actual_belt_locks_power_before_mana_sacrifice_changes_the_target(self):
        def before(s,source,body):
            return {'target':self.add(s.engine,'Scion of the Wild'),'donor':self.add(s.engine,'Wild Cantor')}
        s,source,body,private,extra=self.scene('Belt of Giant Strength',mana={'C':6},before=before)
        target=extra['target'];self.assert_closed(s,target);self.assert_closed(s,extra['donor'])
        row=self.offer(s,source);self.assertIsNotNone(row);self.assertEqual({7},self.quoted(row,target.ref))
        self.assertNotIn(body.ref,json.dumps(row['target_schema']))
        self.activate(s,source,target)
        self.assertEqual(0,sum(s.state.players['A'].mana_pool.values()))
        self.assertEqual('graveyard',s.state.cards[extra['donor'].object_id].zone)
        self.assertEqual(2,s.engine._numeric_stat(target.object_id,'power'))
        s=self.replay(s,load=True);self.assertIsNone(self.resolve(s))
        self.assertEqual(target.object_id,s.state.cards[source.object_id].attached_to)
        self.assertEqual((10,10),tuple(s.engine._numeric_stat(target.object_id,stat) for stat in ('power','toughness')))
        self.finish(s,private)

    def test_actual_dragonfire_uses_current_colors_and_keeps_monocolored_hexproof(self):
        def before(s,source,body):
            self.add(s.engine,'Mountain',seat='B')
            for i in range(2):self.add(s.engine,'Wastes',seat='B',ref='dragonfire-void-'+str(i))
            return {'all_colors':self.add(s.engine,'Fusion Elemental'),'zero':self.add(s.engine,'Ornithopter'),
                'lace':self.add(s.engine,'Thoughtlace',zone='hand'),'bolt':self.add(s.engine,'Lightning Bolt',seat='B',zone='hand'),
                'void':self.add(s.engine,'Spatial Contortion',seat='B',zone='hand')}
        s,source,body,private,extra=self.scene('Dragonfire Blade',mana={'C':3,'U':1},before=before)
        target=extra['all_colors'];row=self.offer(s,source)
        for ref,price in ((target.ref,0),(body.ref,3),(extra['zero'].ref,4)):
            self.assertEqual({price},self.quoted(row,ref))
        self.cast(s,'A',extra['lace'],[target.ref]);self.assertIsNone(self.resolve(s))
        row=self.offer(s,source);self.assertEqual({3},self.quoted(row,target.ref))
        self.assertNotIn(extra['zero'].ref,json.dumps(row['target_schema']))
        self.reject(s,'A',{'action_id':row['id'],'targets':[target.ref],'pay':'manual','payment':{}})
        self.activate(s,source,target,payment={'C':3});self.assertIsNone(self.resolve(s))
        self.assertEqual((10,10),tuple(s.engine._numeric_stat(target.object_id,stat) for stat in ('power','toughness')))
        self.priority(s,'B');bolt=self.action(s,'B',extra['bolt'])
        self.reject(s,'B',{'action_id':bolt['id'],'targets':[target.ref],'pay':'auto'})
        self.cast(s,'B',extra['void'],[target.ref]);s=self.replay(s,load=True);self.assertIsNone(self.resolve(s))
        self.assertEqual((13,7),tuple(s.engine._numeric_stat(target.object_id,stat) for stat in ('power','toughness')))
        self.finish(s,private)

    def test_fresh_target_scalar_and_threshold_omissions_are_killed(self):
        original=TargetActivationReduction.amount
        for metric,method in (
            ('power_at_most','test_actual_raft_reads_layer_seven_power_and_does_not_tap_a_blinked_copy'),
            ('color_count','test_actual_dragonfire_uses_current_colors_and_keeps_monocolored_hexproof')):
            def omitted(spec,*,power,colors):return 0 if spec.target_metric==metric else original(spec,power=power,colors=colors)
            with patch.object(TargetActivationReduction,'amount',omitted),self.assertRaises(AssertionError):
                getattr(type(self)(method),method)()


if __name__=='__main__':unittest.main()
