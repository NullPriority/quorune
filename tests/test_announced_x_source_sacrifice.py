"""CR 107.3a, 113.7a and 602.2b: X and fixed source-sacrifice costs."""
from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
import test_target_recipient_results as recipients
from test_granted_skulk import record
from quorune.abilities import ActivatedAbility, parse_activated_abilities, reduced_requirements
from quorune.activation_x import (activation_x_symbols,
    ANNOUNCED_ACTIVATION_X_SACRIFICE_CAPABILITY as CAP)
from quorune.compiler.activation_x_costs import announced_x_activation_cost
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.rules.capabilities import CapabilityRegistry, load_default_capability_registry
from scripts.build_test_database import build_fixture_database


class AnnouncedXSourceSacrificeModelTests(unittest.TestCase):
    def test_closed_cost_roundtrip_retains_every_x_and_exact_source_obligations(self):
        for kind in ('artifact','battle','creature','enchantment','land','permanent','planeswalker'):
            text='{X}{X}{R}, {T}, Sacrifice this '+kind
            raw,=parse_activated_abilities(card_name='Generic Source',oracle_text=text+': You gain X life.')
            ability=announced_x_activation_cost(raw)
            self.assertEqual(2,ability.announced_x_symbols)
            self.assertTrue(ability.sacrifice_source)
            self.assertEqual(4,reduced_requirements(ability,announced_x=2)['GENERIC'])
            self.assertEqual(1,reduced_requirements(ability,announced_x=0)['R'])
            self.assertEqual(ability,ActivatedAbility.from_dict(ability.to_dict()))
            self.assertIsNone(activation_x_symbols(text))
            for changes in ({'sacrifice_source':False},{'tap_source':False},
                    {'announced_x_symbols':1},{'announced_x_symbols':True},
                    {'cost_text':'{X}{X}{R}, {T}'},{'mana':{'R':0}}):
                with self.subTest(changes=changes),self.assertRaises(ValueError):
                    ActivatedAbility.from_dict({**ability.to_dict(),**changes})
        old,=parse_activated_abilities(card_name='Generic Source',oracle_text='{2}, Sacrifice this artifact: Draw a card.')
        self.assertNotIn('announced_x_symbols',old.to_dict())
        self.assertEqual(old,ActivatedAbility.from_dict(old.to_dict()))

    def test_selected_variable_and_unrepresented_costs_stay_residual(self):
        for cost in ('{X}, Sacrifice another creature','{X}, Sacrifice two artifacts',
                '{X}, Sacrifice this artifact, Sacrifice this artifact',
                '{X}, Discard a card','{X}, Pay X life',
                '{X}{G/U}, Sacrifice this creature','{X}, Exile this artifact'):
            raw,=parse_activated_abilities(card_name='Generic Source',oracle_text=cost+': You gain X life.')
            self.assertIsNone(announced_x_activation_cost(raw).announced_x_symbols,cost)

    def test_verified_withdrawal_and_source_assurance_require_the_combination_owner(self):
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index,discover_tests
        from quorune.compiler.target_effect_corpus_assurance import TargetEffectCorpusCollector,TargetEffectAssuranceError
        card=record('{X}, Sacrifice this artifact: Target creature gets +X/+X until end of turn.','Artifact')
        full=load_default_capability_registry()
        ir=compile_oracle_card(card,capability_registry=full,capability_profile='commander_review')
        self.assertEqual('exact',ir.status,ir.material_residuals)
        TargetEffectCorpusCollector().observe(card,ir)
        node,=ir.faces[0].nodes
        missing=replace(node,capability_dependencies=tuple(c for c in node.capability_dependencies if c!=CAP))
        with self.assertRaisesRegex(TargetEffectAssuranceError,'lost required capabilities'):
            TargetEffectCorpusCollector().observe(card,replace(ir,faces=(replace(ir.faces[0],nodes=(missing,)),)))
        raw=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        next(r for r in raw['capabilities'] if r['id']==CAP).update(status='blocked',blockers=['self-sacrifice X withdrawn'])
        registry=CapabilityRegistry(raw)
        index=build_index(registry_value=raw,declaration_source=read_evidence_document(ROOT/'platform/capability-evidence-declarations.json'),
            discovered_test_ids=set(discover_tests(ROOT).values()))
        registry.mark_evidence_verified(validate_capability_evidence_index(index,registry=registry))
        self.assertNotEqual('exact',compile_oracle_card(card,capability_registry=registry).status)
        self.assertEqual('exact',compile_oracle_card(record('{X}: You gain X life.','Artifact'),capability_registry=registry).status)


class AnnouncedXSourceSacrificeNativeTests(InheritedNativeFixture,unittest.TestCase):
    reject=recipients.TargetRecipientNativeTests.reject
    finish=recipients.TargetRecipientNativeTests.finish
    tokens=recipients.TargetRecipientNativeTests.tokens

    @classmethod
    def setUpClass(cls):
        cls.temporary=tempfile.TemporaryDirectory();folder=Path(cls.temporary.name)
        basic=json.loads((ROOT/'tests/fixtures/bound-effect-program-cards.json').read_text(encoding='utf-8'))
        basic['cards']=[r for r in basic['cards'] if r['name']=='Generic Bound Plains'];basic['rulings']=[]
        (folder/'basic.json').write_text(json.dumps(basic),encoding='utf-8')
        path=folder/'source-sacrifice-x.sqlite3'
        build_fixture_database([folder/'basic.json',ROOT/'tests/fixtures/announced-x-source-sacrifice-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Source sacrifice X',[DeckEntry('Sliver Hivelord',1,'commander'),
            DeckEntry('Generic Bound Plains',30)],['Sliver Hivelord'])

    def scene(self,name,*,mana=None,before=None):
        s=self.session(2021012+len(name))
        source=self.add(s.engine,name);source.acquired_control_turn_count=-1
        private=self.add(s.engine,'Counterspell',seat='C',zone='hand',ref='sacrifice-x-private-C')
        if before is not None:before(s,source)
        s.state.config.semantic_policy='trusted_only';self.assert_closed(s,source)
        self.prepare(s,mana=mana or {'C':8,'R':2,'W':1});self.checkpoint(s)
        return s,source,private

    def offered(self,s,source):
        self.priority(s,'A')
        return next(r for r in s.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
            if r['id'].startswith('activate:'+source.ref+':') and r.get('choice_schema',{}).get('x'))

    def activate(self,s,source,*,x,targets=()):
        row=self.offered(s,source)
        command={'action_id':row['id'],'x':x,'targets':list(targets),'pay':'auto'}
        self.reject(s,'B',command)
        self.reject(s,'A',{**command,'x':True})
        result=s.act('pilot:A',command);self.assertTrue(result.ok,result.summary)
        self.assertEqual('graveyard',s.state.cards[source.object_id].zone)
        self.assertIn(source.object_id,s.state.players[source.owner].zones['graveyard'])
        self.assertEqual(x,s.state.stack[-1].x_value)
        return s.state.stack[-1]

    def test_actual_elementals_keep_paid_x_and_damage_source_after_sacrifice(self):
        for name in ('Cinder Elemental','Goblin Dynamo','Pain Kami'):
            with self.subTest(name=name):
                target={}
                def before(s,source):target['card']=self.add(s.engine,'Hill Giant',seat='D')
                s,source,private=self.scene(name,before=before)
                self.activate(s,source,x=2,targets=[target['card'].ref])
                self.assertEqual((6,1),tuple(s.state.players['A'].mana_pool[c] for c in ('C','R')))
                s=self.replay(s,load=True);self.assertIsNone(self.resolve(s))
                self.assertEqual(2,s.state.cards[target['card'].object_id].marked_damage)
                self.assertEqual('battlefield',s.state.cards[target['card'].object_id].zone)
                self.finish(s,private)

    def test_actual_basket_counts_snakes_and_keeps_sorcery_timing(self):
        response={}
        def before(s,source):
            response['card']=self.add(s.engine,'Shock',seat='B',zone='hand')
            s.state.players['B'].mana_pool['R']=1
        s,source,private=self.scene('Snake Basket',before=before)
        row=self.offered(s,source)
        self.cast(s,'B',response['card'],targets=['D'])
        self.priority(s,'A')
        self.assertFalse(any(r['id']==row['id'] for r in s.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']))
        self.reject(s,'A',{'action_id':row['id'],'x':3,'pay':'auto'})
        self.assertIsNone(self.resolve(s))
        self.assertEqual(38,s.state.players['D'].life)
        self.activate(s,source,x=3)
        self.assertEqual(5,s.state.players['A'].mana_pool['C'])
        s=self.replay(s,load=True);self.assertIsNone(self.resolve(s))
        tokens=self.tokens(s,'A');self.assertEqual(3,len(tokens))
        for token in tokens:
            self.assertEqual((1,1),tuple(s.engine._numeric_stat(token.object_id,k) for k in ('power','toughness')))
            self.assertIn('Snake',s.engine._effective_card_data(token)['type_line'])
            self.assertEqual(['G'],s.engine._effective_card_data(token)['colors'])
        self.finish(s,private)

    def test_actual_vault_double_x_and_zero_cannot_reuse_its_tap_for_mana(self):
        for x in (0,2):
            with self.subTest(x=x):
                s,source,private=self.scene('Treasure Vault',mana={'C':4})
                self.activate(s,source,x=x)
                self.assertEqual(4-2*x,s.state.players['A'].mana_pool['C'])
                s=self.replay(s,load=True);self.assertIsNone(self.resolve(s))
                tokens=self.tokens(s,'A');self.assertEqual(x,len(tokens))
                for token in tokens:self.assertIn('Treasure',s.engine._effective_card_data(token)['type_line'])
                self.finish(s,private)
        s,source,private=self.scene('Treasure Vault',mana={'C':3})
        row=self.offered(s,source)
        self.reject(s,'A',{'action_id':row['id'],'x':2,'pay':'auto'})
        self.assertEqual('battlefield',s.state.cards[source.object_id].zone)
        self.assertFalse(s.state.cards[source.object_id].tapped)
        self.assertEqual(3,s.state.players['A'].mana_pool['C'])
        self.finish(s,private)

    def test_actual_deed_and_dismantler_resolve_the_chosen_bound_after_source_payment(self):
        for name in ('Pernicious Deed','Dauntless Dismantler'):
            with self.subTest(name=name):
                board={}
                def before(s,source):
                    for key,other,seat in (('zero','Ornithopter','B'),('two','Mind Stone','C'),
                            ('creature','Grizzly Bears','D'),('three','Darksteel Ingot','B'),('land','Forest','C')):
                        board[key]=self.add(s.engine,other,seat=seat)
                s,source,private=self.scene(name,before=before)
                self.activate(s,source,x=2)
                self.assertEqual(6 if name=='Pernicious Deed' else 4,s.state.players['A'].mana_pool['C'])
                s=self.replay(s,load=True);self.assertIsNone(self.resolve(s))
                destroyed={'zero','two','creature'} if name=='Pernicious Deed' else {'two'}
                for key,card in board.items():self.assertEqual('graveyard' if key in destroyed else 'battlefield',s.state.cards[card.object_id].zone,key)
                self.finish(s,private)

    def test_actual_settlers_prices_double_x_and_requires_exact_distinct_land_targets(self):
        for x in (0,2):
            with self.subTest(x=x):
                board={}
                def before(s,source):
                    for seat in 'BCD':board[seat]=self.add(s.engine,'Forest',seat=seat,ref='settlers-land-'+seat)
                    board['creature']=self.add(s.engine,'Grizzly Bears',seat='D')
                s,source,private=self.scene('Orcish Settlers',before=before)
                row=self.offered(s,source)
                command={'action_id':row['id'],'x':x,'targets':[board[k].ref for k in 'BC'] if x else [],'pay':'auto'}
                if x:
                    for refs in ([board['B'].ref],[board['B'].ref]*2,[board['B'].ref,board['creature'].ref]):
                        self.reject(s,'A',{**command,'targets':refs})
                self.activate(s,source,x=x,targets=command['targets'])
                self.assertEqual((8-2*x,1),tuple(s.state.players['A'].mana_pool[c] for c in ('C','R')))
                s=self.replay(s,load=True);self.assertIsNone(self.resolve(s))
                for key,card in board.items():
                    self.assertEqual('graveyard' if x and key in 'BC' else 'battlefield',s.state.cards[card.object_id].zone,key)
                self.finish(s,private)

    def test_actual_mask_sizes_one_golem_and_unearth_replaces_the_second_sacrifice(self):
        s,source,private=self.scene('Mask of the Jadecrafter',mana={'C':8,'G':1})
        self.activate(s,source,x=2)
        s=self.replay(s,load=True);self.assertIsNone(self.resolve(s))
        tokens=self.tokens(s,'A');self.assertEqual(1,len(tokens))
        token=tokens[0];data=s.engine._effective_card_data(token)
        self.assertEqual((2,2),tuple(s.engine._numeric_stat(token.object_id,k) for k in ('power','toughness')))
        self.assertIn('Artifact Creature',data['type_line']);self.assertIn('Golem',data['type_line'])
        self.assertEqual([],data['colors'])
        self.priority(s,'A')
        source=s.state.cards[source.object_id]
        row=next(r for r in s.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
            if r['id'].startswith('activate:'+source.ref+':'))
        command={'action_id':row['id'],'pay':'auto'};self.reject(s,'D',command)
        result=s.act('pilot:A',command);self.assertTrue(result.ok,result.summary)
        s=self.replay(s,load=True);self.assertIsNone(self.resolve(s))
        source=s.state.cards[source.object_id];self.assertEqual(('battlefield',True),(source.zone,source.unearthed))
        row=self.offered(s,source)
        result=s.act('pilot:A',{'action_id':row['id'],'x':1,'pay':'auto'})
        self.assertTrue(result.ok,result.summary)
        self.assertEqual(('exile',False),(s.state.cards[source.object_id].zone,s.state.cards[source.object_id].unearthed))
        self.assertEqual(1,s.state.stack[-1].x_value)
        s=self.replay(s,load=True);self.assertIsNone(self.resolve(s))
        self.assertEqual([1,2],sorted(s.engine._numeric_stat(t.object_id,'power') for t in self.tokens(s,'A')))
        self.assertEqual((3,0),tuple(s.state.players['A'].mana_pool[c] for c in ('C','G')))
        self.finish(s,private)

    def test_partial_originals_cannot_expose_a_paid_x_action_or_lose_a_source(self):
        for name in ('Hearth Kami','Runed Arch',"Wizard's Rockets",'Rod of Absorption'):
            with self.subTest(name=name):
                s=self.session(20210120+len(name));source=self.add(s.engine,name)
                source.acquired_control_turn_count=-1
                self.prepare(s,mana={'C':8});self.checkpoint(s)
                rows=s.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
                self.assertFalse(any(r['id'].startswith('activate:'+source.ref+':') and r.get('choice_schema',{}).get('x') for r in rows))
                for ability in parse_activated_abilities(card_name=name,oracle_text=self.db.lookup(name).oracle_text):
                    if '{X}' in ability.cost_text:
                        self.reject(s,'A',{'action_id':'activate:'+source.ref+':'+ability.ability_id,'x':2,'pay':'auto'})
                self.assertEqual('battlefield',s.state.cards[source.object_id].zone)

    def test_catalog_requires_the_combination_dependency_without_coverage_annotations(self):
        s,source,private=self.scene('Treasure Vault')
        program=next(p for p in s.engine.semantics.programs_for_oracle(source.oracle_id,event='activate')
            if p.effects and CAP in p.capability_dependencies)
        dependencies=tuple(c for c in program.capability_dependencies if c!=CAP)
        missing=replace(program,coverage=(),capability_dependencies=dependencies,
            capability_closure=self.registry.closure(dependencies,profile='commander_review').to_dict())
        bound=bind_semantic_program_runtime(missing,capability_registry=self.registry,profile='commander_review')
        self.assertFalse(bound['strict'])
        self.assertIn('capability:undeclared_runtime_dependency:'+CAP,bound['blockers'])

    def test_live_omitted_x_price_and_omitted_sacrifice_mutants_are_killed(self):
        import quorune.rules.activation.x_costs as prices
        original=prices.activation_requirements
        def omitted(host,seat,source,ability,response=None,*,targets=None):
            requirements,value=original(host,seat,source,ability,response,targets=targets)
            if ability.announced_x_symbols is not None:requirements['GENERIC']=0
            return requirements,value
        with patch.object(prices,'activation_requirements',omitted):
            with self.assertRaises(AssertionError):self.test_actual_basket_counts_snakes_and_keeps_sorcery_timing()
        original_cost=announced_x_activation_cost
        def omitted_sacrifice(ability):
            compiled=original_cost(ability)
            if compiled.announced_x_symbols is not None and compiled.sacrifice_source:
                return replace(compiled,cost_text=compiled.cost_text.split(', Sacrifice')[0],sacrifice_source=False)
            return compiled
        with patch('quorune.compiler.activation_x_costs.announced_x_activation_cost',omitted_sacrifice):
            with self.assertRaises(AssertionError):self.test_actual_basket_counts_snakes_and_keeps_sorcery_timing()
