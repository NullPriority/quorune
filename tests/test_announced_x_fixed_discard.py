"""CR 107.3a, 602.2b, 701.9a: X does not change fixed discard costs."""
from copy import deepcopy
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
from quorune.activation_x import activation_x_symbols
from quorune.activation_x_discard import ANNOUNCED_X_DISCARD_CAPABILITY as CAP,activation_x_discard_count
from quorune.compiler.activation_x_costs import announced_x_activation_cost
from quorune.compiler.activated_zone_change_costs import fixed_activated_zone_change_cost
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition,DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.rules.capabilities import CapabilityRegistry,load_default_capability_registry
from scripts.build_test_database import build_fixture_database


class AnnouncedXDiscardModelTests(unittest.TestCase):
    def test_fixed_counts_keep_query_roundtrip_and_independent_repeated_x_price(self):
        for count,word in enumerate(('one','two','three','four','five','six','seven','eight','nine','ten'),1):
            for noun_count in (word,str(count),'a' if count==1 else word):
                cost='{X}{X}{B}, {T}, Discard '+noun_count+(' card' if count==1 else ' cards')
                raw,=parse_activated_abilities(card_name='Generic Source',oracle_text=cost+': You gain X life.')
                ability=announced_x_activation_cost(raw)
                self.assertEqual(2,ability.announced_x_symbols,cost)
                self.assertEqual(count,activation_x_discard_count(cost))
                self.assertEqual(count,ability.choices[0].count)
                self.assertEqual('discard_one',ability.choices[0].kind)
                self.assertEqual(ability,ActivatedAbility.from_dict(ability.to_dict()))
                self.assertIsNone(activation_x_symbols(cost))
                self.assertEqual(6,reduced_requirements(ability,announced_x=3)['GENERIC'])
                self.assertEqual(1,reduced_requirements(ability,announced_x=0)['B'])
        old,=parse_activated_abilities(card_name='Generic Source',oracle_text='{2}, Discard two cards: Draw a card.')
        old=fixed_activated_zone_change_cost(old)
        self.assertNotIn('announced_x_symbols',old.to_dict())
        self.assertEqual(old,ActivatedAbility.from_dict(old.to_dict()))

    def test_closed_discard_obligation_rejects_missing_or_changed_query_count_and_price(self):
        raw,=parse_activated_abilities(card_name='Generic Source',oracle_text='{X}{R}, {T}, Discard two cards: You gain X life.')
        ability=announced_x_activation_cost(raw);value=ability.to_dict()
        for mutate in ('missing','count','owner','zone','quality','tap','mana','raw'):
            bad=deepcopy(value)
            if mutate=='missing':bad['choices']=[]
            elif mutate=='count':bad['choices'][0]['count']=1
            elif mutate=='owner':bad['choices'][0]['predicate']['owner']=None
            elif mutate=='zone':bad['choices'][0]['predicate']['zones']=['graveyard']
            elif mutate=='quality':bad['choices'][0]['predicate']['types_all']=['creature']
            elif mutate=='tap':bad['tap_source']=False
            elif mutate=='mana':bad['mana']['R']=0
            else:bad['choices']=[raw.choices[0].to_dict()]
            with self.subTest(mutate=mutate),self.assertRaises(ValueError):ActivatedAbility.from_dict(bad)
        for cost in ('{X}, Discard eleven cards','{X}, Discard X cards','{X}, Discard a creature card',
                '{X}, Discard a card with mana value X','{X}, Discard a card at random',
                '{X}, Sacrifice this artifact, Discard a card','{X}{R/G}, Discard a card'):
            raw,=parse_activated_abilities(card_name='Generic Source',oracle_text=cost+': You gain X life.')
            self.assertIsNone(announced_x_activation_cost(raw).announced_x_symbols,cost)

    def test_verified_withdrawal_and_source_assurance_require_both_cost_owners(self):
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index,discover_tests
        from quorune.compiler.target_effect_corpus_assurance import TargetEffectCorpusCollector,TargetEffectAssuranceError
        full=load_default_capability_registry()
        card=record('{X}, Discard a card: Target creature gets +X/+X until end of turn.','Artifact')
        ir=compile_oracle_card(card,capability_registry=full,capability_profile='commander_review')
        self.assertEqual('exact',ir.status,ir.material_residuals);TargetEffectCorpusCollector().observe(card,ir)
        node,=ir.faces[0].nodes
        for cap in (CAP,'activation.selected_zone_change.fixed'):
            missing=replace(node,capability_dependencies=tuple(c for c in node.capability_dependencies if c!=cap))
            with self.assertRaisesRegex(TargetEffectAssuranceError,'lost required capabilities'):
                TargetEffectCorpusCollector().observe(card,replace(ir,faces=(replace(ir.faces[0],nodes=(missing,)),)))
        raw=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        next(r for r in raw['capabilities'] if r['id']==CAP).update(status='blocked',blockers=['X fixed discard withdrawn'])
        registry=CapabilityRegistry(raw)
        index=build_index(registry_value=raw,declaration_source=read_evidence_document(ROOT/'platform/capability-evidence-declarations.json'),discovered_test_ids=set(discover_tests(ROOT).values()))
        registry.mark_evidence_verified(validate_capability_evidence_index(index,registry=registry))
        self.assertNotEqual('exact',compile_oracle_card(card,capability_registry=registry).status)
        for text in ('{X}: You gain X life.','{1}, Discard a card: You gain 1 life.'):
            self.assertEqual('exact',compile_oracle_card(record(text,'Artifact'),capability_registry=registry).status)


class AnnouncedXDiscardNativeTests(InheritedNativeFixture,unittest.TestCase):
    reject=recipients.TargetRecipientNativeTests.reject
    finish=recipients.TargetRecipientNativeTests.finish

    @classmethod
    def setUpClass(cls):
        cls.temporary=tempfile.TemporaryDirectory();folder=Path(cls.temporary.name)
        basic=json.loads((ROOT/'tests/fixtures/bound-effect-program-cards.json').read_text(encoding='utf-8'))
        basic['cards']=[r for r in basic['cards'] if r['name']=='Generic Bound Plains'];basic['rulings']=[]
        (folder/'basic.json').write_text(json.dumps(basic),encoding='utf-8')
        path=folder/'discard-x.sqlite3'
        build_fixture_database([folder/'basic.json',ROOT/'tests/fixtures/announced-x-fixed-discard-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Fixed discard X',[DeckEntry('Sliver Hivelord',1,'commander'),DeckEntry('Generic Bound Plains',30)],['Sliver Hivelord'])

    def scene(self,name,*,mana=None,before=None):
        s=self.session(2021013+len(name));source=self.add(s.engine,name);source.acquired_control_turn_count=-1
        costs=[self.add(s.engine,'Forest',zone='hand',ref='discard-X-cost-'+str(i)) for i in range(2)]
        private=self.add(s.engine,'Counterspell',seat='C',zone='hand',ref='discard-X-private-C')
        if before is not None:before(s,source)
        s.state.config.semantic_policy='trusted_only';self.assert_closed(s,source)
        self.prepare(s,mana=mana or {'C':8,'R':1,'B':1,'U':1,'G':2});self.checkpoint(s)
        return s,source,costs,private

    def offered(self,s,source):
        self.priority(s,'A')
        return next(r for r in s.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
            if r['id'].startswith('activate:'+source.ref+':') and r.get('choice_schema',{}).get('x'))

    def activate(self,s,source,costs,*,x,targets=()):
        row=self.offered(s,source)
        expected=activation_x_discard_count(next(a for a in parse_activated_abilities(card_name=source.printed_name,oracle_text=self.db.lookup(source.printed_name).oracle_text) if '{X}' in a.cost_text).cost_text)
        self.assertEqual(expected,len(costs))
        for card in costs:
            self.assertIn(card.ref,json.dumps(s.packet('pilot:A',full=True)))
            for seat in 'BCD':self.assertNotIn(card.ref,json.dumps(s.packet('pilot:'+seat,full=True)))
        command={'action_id':row['id'],'x':x,'cost_cards':[c.ref for c in costs],'targets':list(targets),'pay':'auto'}
        self.reject(s,'B',command);self.reject(s,'A',{**command,'x':True})
        self.reject(s,'A',{**command,'cost_cards':[]})
        self.reject(s,'A',{**command,'cost_cards':[costs[0].ref]*2 if len(costs)==2 else [source.ref]})
        self.reject(s,'A',{**command,'cost_cards':['discard-X-private-C']+[c.ref for c in costs[1:]]})
        self.reject(s,'A',{**command,'x':100})
        result=s.act('pilot:A',command);self.assertTrue(result.ok,result.summary)
        self.assertEqual('battlefield',s.state.cards[source.object_id].zone)
        self.assertTrue(s.state.cards[source.object_id].tapped)
        self.assertEqual(x,s.state.stack[-1].x_value)
        for card in costs:
            self.assertEqual('graveyard',s.state.cards[card.object_id].zone)
            for seat in 'ABCD':self.assertIn(s.state.cards[card.object_id].ref,json.dumps(s.packet('pilot:'+seat,full=True)))
        return s.state.stack[-1]

    def test_actual_latulla_pays_two_cards_even_at_zero_and_keeps_colored_price(self):
        for x in (0,2):
            with self.subTest(x=x):
                s,source,costs,private=self.scene('Latulla, Keldon Overseer')
                self.activate(s,source,costs,x=x,targets=['B'])
                self.assertEqual((8-x,0),tuple(s.state.players['A'].mana_pool[c] for c in ('C','R')))
                s=self.replay(s,load=True);self.assertIsNone(self.resolve(s))
                self.assertEqual(40-x,s.state.players['B'].life)
                self.assertEqual([40,40],[s.state.players[k].life for k in 'CD'])
                self.finish(s,private)

    def test_actual_alexi_exact_targets_and_zero_retain_two_discarded_cards(self):
        for x in (0,2):
            with self.subTest(x=x):
                board={}
                def before(s,source):
                    board['B']=self.add(s.engine,'Grizzly Bears',seat='B',ref='alexi-b')
                    board['C']=self.add(s.engine,'Grizzly Bears',seat='C',ref='alexi-c')
                    board['C'].owner='D'
                s,source,costs,private=self.scene('Alexi, Zephyr Mage',before=before)
                self.activate(s,source,costs,x=x,targets=[c.ref for c in board.values()] if x else [])
                self.assertEqual((8-x,0),tuple(s.state.players['A'].mana_pool[c] for c in ('C','U')))
                s=self.replay(s,load=True);self.assertIsNone(self.resolve(s))
                for card in board.values():
                    self.assertEqual('hand' if x else 'battlefield',s.state.cards[card.object_id].zone)
                    if x:self.assertIn(card.object_id,s.state.players[card.owner].zones['hand'])
                self.finish(s,private)

    def test_actual_witch_fixed_discard_and_pump_keep_their_separate_counts(self):
        target={}
        def before(s,source):target['card']=self.add(s.engine,'Hill Giant',seat='D')
        s,source,costs,private=self.scene('Cackling Witch',before=before)
        self.activate(s,source,costs[:1],x=3,targets=[target['card'].ref])
        self.assertEqual((5,0),tuple(s.state.players['A'].mana_pool[c] for c in ('C','B')))
        s=self.replay(s,load=True);self.assertIsNone(self.resolve(s))
        self.assertEqual((6,3),tuple(s.engine._numeric_stat(target['card'].object_id,k) for k in ('power','toughness')))
        self.assertEqual('hand',s.state.cards[costs[1].object_id].zone)
        for viewer in 'BCD':self.assertNotIn(costs[1].ref,json.dumps(s.packet('pilot:'+viewer,full=True)))
        self.finish(s,private)

    def test_actual_hammer_mage_destroys_only_artifacts_within_its_paid_x_bound(self):
        board={}
        def before(s,source):
            for key,name,seat in (('zero','Ornithopter','B'),('two','Mind Stone','C'),('three','Darksteel Ingot','D'),('creature','Grizzly Bears','B')):
                board[key]=self.add(s.engine,name,seat=seat)
        s,source,costs,private=self.scene('Hammer Mage',before=before)
        self.activate(s,source,costs[:1],x=2)
        self.assertEqual((6,0),tuple(s.state.players['A'].mana_pool[c] for c in ('C','R')))
        s=self.replay(s,load=True);self.assertIsNone(self.resolve(s))
        for key,card in board.items():self.assertEqual('graveyard' if key in ('zero','two') else 'battlefield',s.state.cards[card.object_id].zone,key)
        self.finish(s,private)

    def test_catalog_infers_the_cost_dependency_without_coverage_annotations(self):
        s,source,costs,private=self.scene('Latulla, Keldon Overseer')
        program=next(p for p in s.engine.semantics.programs_for_oracle(source.oracle_id,event='activate') if p.effects and CAP in p.capability_dependencies)
        for cap in (CAP,'activation.selected_zone_change.fixed'):
            dependencies=tuple(c for c in program.capability_dependencies if c!=cap)
            missing=replace(program,coverage=(),capability_dependencies=dependencies,capability_closure=self.registry.closure(dependencies,profile='commander_review').to_dict())
            bound=bind_semantic_program_runtime(missing,capability_registry=self.registry,profile='commander_review')
            self.assertFalse(bound['strict']);self.assertIn('capability:undeclared_runtime_dependency:'+cap,bound['blockers'])

    def test_insufficient_owned_hand_hides_the_action_and_rejects_without_payment(self):
        def before(s,source):
            keep=next(c for c in s.state.cards.values() if c.ref=='discard-X-cost-0')
            for oid in tuple(s.state.players['A'].zones['hand']):
                if oid!=keep.object_id:s.engine.move_card(oid,'library')
        s,source,costs,private=self.scene('Alexi, Zephyr Mage',before=before)
        self.assertEqual(1,len(s.state.players['A'].zones['hand']))
        rows=s.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
        self.assertFalse(any(r['id'].startswith('activate:'+source.ref+':') for r in rows))
        self.reject(s,'A',{'action_id':'activate:'+source.ref+':ab1','x':0,'cost_cards':[costs[0].ref],'targets':[],'pay':'auto'})
        self.assertEqual('hand',s.state.cards[costs[0].object_id].zone)
        self.assertFalse(s.state.cards[source.object_id].tapped)
        self.assertEqual((8,1),tuple(s.state.players['A'].mana_pool[c] for c in ('C','U')))
        self.finish(s,private)

    def test_unrepresented_original_results_do_not_expose_a_new_paid_x_action(self):
        for name in ('Greel, Mind Raker','Knollspine Invocation',"Vivien's Arkbow",'Deepwood Elder'):
            with self.subTest(name=name):
                s=self.session(20210130+len(name));source=self.add(s.engine,name);source.acquired_control_turn_count=-1
                self.prepare(s,mana={'C':8,**{c:2 for c in 'WUBRG'}});self.checkpoint(s)
                rows=s.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
                self.assertFalse(any(r['id'].startswith('activate:'+source.ref+':') and r.get('choice_schema',{}).get('x') for r in rows))
                for ability in parse_activated_abilities(card_name=name,oracle_text=self.db.lookup(name).oracle_text):
                    if '{X}' in ability.cost_text:self.reject(s,'A',{'action_id':'activate:'+source.ref+':'+ability.ability_id,'x':0,'pay':'auto'})

    def test_live_omitted_x_price_and_discard_producer_mutants_are_killed(self):
        import quorune.rules.activation.x_costs as prices
        original=prices.activation_requirements
        def free_x(host,seat,source,ability,response=None,*,targets=None):
            requirements,value=original(host,seat,source,ability,response,targets=targets)
            if ability.announced_x_symbols is not None:requirements['GENERIC']=0
            return requirements,value
        with patch.object(prices,'activation_requirements',free_x):
            with self.assertRaises(AssertionError):self.test_actual_witch_fixed_discard_and_pump_keep_their_separate_counts()
        original_cost=announced_x_activation_cost
        def free_cards(ability):
            compiled=original_cost(ability)
            if compiled.announced_x_symbols is not None and compiled.choices:
                return replace(compiled,cost_text=compiled.cost_text.rpartition(', Discard ')[0],choices=())
            return compiled
        with patch('quorune.compiler.activation_x_costs.announced_x_activation_cost',free_cards):
            with self.assertRaises(AssertionError):self.test_actual_witch_fixed_discard_and_pump_keep_their_separate_counts()
