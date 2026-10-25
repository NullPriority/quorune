from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from pathlib import Path
import json,tempfile,unittest
from unittest.mock import patch

from common import ROOT
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition,DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.optional_life_cost_model import (
    OPTIONAL_LIFE_COST_CAPABILITY as CAP,OPTIONAL_LIFE_COST_HANDLER as HANDLER,
    OPTIONAL_LIFE_COST_FIELD as FIELD,OptionalLifeColorReductionSpec,optional_life_cost_amount,
)
from scripts.build_test_database import build_fixture_database
import test_counted_permanent_costs as controls
import test_global_alternative_costs as grant_controls
from quorune.rules.evidence_storage import read_evidence_document

NAMES=('Defiler of Dreams','Defiler of Faith','Defiler of Flesh','Defiler of Instinct','Defiler of Vigor')


class OptionalLifeCostTests(unittest.TestCase):
    register_fixture_cards_on_add=True
    session=controls.CountedPermanentCostTests.session
    add=controls.CountedPermanentCostTests.add
    prepare=controls.CountedPermanentCostTests.prepare
    checkpoint=controls.CountedPermanentCostTests.checkpoint
    resolve=controls.CountedPermanentCostTests.resolve
    replay=controls.CountedPermanentCostTests.replay
    binding=controls.CountedPermanentCostTests.binding
    actions=controls.CountedPermanentCostTests.actions
    cast=controls.CountedPermanentCostTests.cast
    reject=controls.CountedPermanentCostTests.reject
    private_replay=controls.CountedPermanentCostTests.private_replay
    priority=grant_controls.GlobalAlternativeCostTests.priority

    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory();path=Path(cls.temp.name)/'optional-life.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',ROOT/'tests/fixtures/optional-life-cost-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Optional life colored reductions',[DeckEntry('Generic Bound Commander',1,'commander'),DeckEntry('Generic Bound Plains',30)],['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls):cls.db.close();cls.temp.cleanup()

    def options(self,s,spell):
        action=next((a for a in self.actions(s) if a['id']=='cast:'+spell.ref),None)
        return action.get('cost_options',[]) if action else []

    def paid_option(self,s,spell,count=1):
        options=self.options(s,spell)
        self.assertTrue(all(FIELD not in option for option in options))
        found=next((o for o in options if o.get('additional_life',0)==2*count),None)
        self.assertIsNotNone(found,options)
        return found

    def pay(self,s,spell,option,payment):
        command={'action_id':'cast:'+spell.ref,'cost_option':option['id'],'pay':'manual','payment':payment}
        self.reject(s,command,'pilot:D');result=s.act('pilot:A',command)
        self.assertTrue(result.ok,result.summary)

    def top(self,s,name,ref):
        card=self.add(s.engine,name,zone='library',ref=ref)
        library=s.state.players['A'].zones['library'];library.remove(card.object_id);library.append(card.object_id)
        return card

    def test_five_originals_have_complete_admission_and_independent_runtime_owner(self):
        from quorune.card_programs.binding import bind_semantic_program_runtime
        s=self.session(800000)
        for i,name in enumerate(NAMES):
            source=self.add(s.engine,name,ref='life-original-'+str(i));bound=self.binding(s,source)
            self.assertTrue(bound['strict_capability_ready'],(name,bound['blockers']))
            self.assertEqual('exact',compile_oracle_card(self.db.lookup(name),capability_registry=self.registry,capability_profile='commander_review').status)
            program=next((p for p in s.engine.semantics.programs_for_oracle(source.oracle_id)
                if any(d.get('handler_id')==HANDLER for d in p.handlers)),None)
            self.assertIsNotNone(program)
            deps=tuple(d for d in program.capability_dependencies if d!=CAP)
            mutant=replace(program,capability_dependencies=deps,capability_closure=self.registry.closure(deps,profile='commander_review').to_dict())
            value=bind_semantic_program_runtime(mutant,capability_registry=self.registry,profile='commander_review')
            self.assertFalse(value['strict']);self.assertIn('capability:undeclared_runtime_dependency:'+CAP,value['blockers'])

    def test_complete_declaration_and_descriptor_reject_unknown_or_mismatched_prices(self):
        from quorune.compiler.optional_life_costs import optional_life_color_reduction_template
        from quorune.semantic_runtime.optional_life_costs import OptionalLifeColorReductionHandler
        text='As an additional cost to cast blue permanent spells, you may pay 2 life. Those spells cost {U} less to cast if you paid life this way. This effect reduces only the amount of blue mana you pay.'
        template=optional_life_color_reduction_template(text);self.assertIsNotNone(template)
        self.assertEqual('U',OptionalLifeColorReductionHandler().validate(template[1]).color)
        for bad in (text.replace('2 life','3 life'),text.replace('{U}','{R}'),text.replace('permanent spells','spells'),
                text.split(' This effect')[0],text.replace('blue mana','generic mana')):
            self.assertIsNone(optional_life_color_reduction_template(bad),bad)
        for key,value in (('life',True),('life',3),('reduction',2),('color','C'),('schema_version',False)):
            raw=OptionalLifeColorReductionSpec('U').to_dict();raw[key]=value
            with self.assertRaises(ValueError):OptionalLifeColorReductionSpec.from_dict(raw)
        extra={**template[1],'unchecked':True}
        with self.assertRaises(ValueError):OptionalLifeColorReductionHandler().validate(extra)

    def test_actual_dreams_paid_and_declined_both_draw_and_keep_printed_flying(self):
        for paid in (False,True):
            s=self.session(800010+paid);self.add(s.engine,'Defiler of Dreams',ref='dreams-provider')
            spell=self.add(s.engine,'Defiler of Dreams',zone='hand',ref='dreams-cast')
            drawn=self.top(s,'Generic Bound Growth','dreams-top');private=self.add(s.engine,'Generic Bound Growth',seat='C',zone='hand')
            self.prepare(s,mana={'C':3,'U':2});self.checkpoint(s)
            option=self.paid_option(s,spell) if paid else next((o for o in self.options(s,spell) if o['id']=='normal'),None)
            self.assertIsNotNone(option);self.assertEqual(1 if paid else 2,option['requirements']['U'])
            self.pay(s,spell,option,{'C':3,'U':1 if paid else 2})
            self.assertEqual(38 if paid else 40,s.state.players['A'].life)
            s=self.replay(s,load=True);self.assertIsNone(self.resolve(s))
            self.assertEqual('battlefield',s.state.cards[spell.object_id].zone);self.assertEqual('hand',s.state.cards[drawn.object_id].zone)
            self.assertIn('flying',s.engine._combat_keywords(s.state.cards[spell.object_id]));self.private_replay(s,private)

    def test_actual_faith_paid_cast_creates_exactly_one_soldier(self):
        s=self.session(800020);self.add(s.engine,'Defiler of Faith',ref='faith-provider')
        spell=self.add(s.engine,'Defiler of Faith',zone='hand',ref='faith-cast');private=self.add(s.engine,'Generic Bound Growth',seat='C',zone='hand')
        self.prepare(s,mana={'C':3,'W':1});self.checkpoint(s);self.pay(s,spell,self.paid_option(s,spell),{'C':3,'W':1})
        s=self.replay(s,load=True);self.assertIsNone(self.resolve(s))
        tokens=[c for c in s.state.cards.values() if c.is_token and c.zone=='battlefield' and c.controller=='A']
        self.assertEqual(1,len(tokens));self.assertIn('Soldier',s.engine._effective_card_data(tokens[0])['type_line'])
        self.assertEqual(38,s.state.players['A'].life);self.assertEqual('battlefield',s.state.cards[spell.object_id].zone)
        self.private_replay(s,private)

    def test_actual_vigor_paid_cast_counters_only_current_controlled_creatures(self):
        s=self.session(800030);source=self.add(s.engine,'Defiler of Vigor',ref='vigor-provider')
        own=self.add(s.engine,'Generic Bound Body',ref='vigor-own');foreign=self.add(s.engine,'Generic Bound Body',seat='B',ref='vigor-foreign')
        spell=self.add(s.engine,'Defiler of Vigor',zone='hand',ref='vigor-cast');private=self.add(s.engine,'Generic Bound Growth',seat='C',zone='hand')
        self.prepare(s,mana={'C':3,'G':1});self.checkpoint(s);self.pay(s,spell,self.paid_option(s,spell),{'C':3,'G':1})
        s=self.replay(s,load=True);self.assertIsNone(self.resolve(s))
        for card in (source,own):self.assertEqual(1,s.state.cards[card.object_id].counters.get('+1/+1',0))
        for card in (spell,foreign):self.assertEqual(0,s.state.cards[card.object_id].counters.get('+1/+1',0))
        self.assertEqual(38,s.state.players['A'].life);self.private_replay(s,private)

    def test_multiple_providers_pay_once_each_and_total_life_limits_options(self):
        s=self.session(800040)
        for i in range(2):self.add(s.engine,'Defiler of Dreams',ref='multiple-dreams-'+str(i))
        spell=self.add(s.engine,'Defiler of Dreams',zone='hand',ref='multiple-cast')
        private=self.add(s.engine,'Generic Bound Growth',seat='C',zone='hand')
        s.state.players['A'].life=3;self.prepare(s,mana={'C':3,'U':2});self.checkpoint(s)
        self.assertEqual({0,2},{o.get('additional_life',0) for o in self.options(s,spell)})
        s.state.players['A'].life=40;self.prepare(s);self.checkpoint(s);option=self.paid_option(s,spell,2)
        self.assertEqual(0,option['requirements']['U']);self.assertEqual(3,option['requirements']['GENERIC'])
        provider={'source':'one','logical':'one:1','program':'one-cost','cost':OptionalLifeColorReductionSpec('U').to_dict()}
        duplicate={'id':'forged','optional_life_reduction':{'schema_version':1,'base_option':'normal','sources':[provider,deepcopy(provider)]}}
        with self.assertRaises(ValueError):optional_life_cost_amount(duplicate)
        initial_hand=len(s.state.players['A'].zones['hand'])
        self.pay(s,spell,option,{'C':3});self.assertEqual(36,s.state.players['A'].life)
        s=self.replay(s,load=True);pending=self.resolve(s)
        self.assertIsNotNone(pending);self.assertEqual('trigger.order',pending.kind)
        trigger_ids=[r['id'] for r in pending.payload_by_actor['A']['triggers']];self.assertEqual(2,len(trigger_ids))
        self.reject(s,{'action_id':'order','triggers':trigger_ids},'pilot:B')
        result=s.act('pilot:A',{'action_id':'order','triggers':trigger_ids});self.assertTrue(result.ok,result.summary)
        self.assertIsNone(self.resolve(s));self.assertEqual(initial_hand+1,len(s.state.players['A'].zones['hand']))
        self.private_replay(s,private)

    def test_actual_flesh_and_instinct_paid_casts_keep_their_targeted_cast_triggers(self):
        for index,(name,color) in enumerate((('Defiler of Flesh','B'),('Defiler of Instinct','R'))):
            s=self.session(800080+index);self.add(s.engine,name,ref='targeted-provider')
            spell=self.add(s.engine,name,zone='hand',ref='targeted-cast');body=self.add(s.engine,'Generic Bound Body',ref='targeted-body')
            private=self.add(s.engine,'Generic Bound Growth',seat='C',zone='hand')
            self.prepare(s,mana={'C':2,color:1});self.checkpoint(s);self.pay(s,spell,self.paid_option(s,spell),{'C':2,color:1})
            pending=self.resolve(s);self.assertIsNotNone(pending);self.assertEqual('semantic.target',pending.kind)
            target=body.ref if color=='B' else 'B'
            self.reject(s,{'action_id':'choose','targets':[target]},'pilot:C')
            s=self.replay(s,load=True);result=s.act('pilot:A',{'action_id':'choose','targets':[target]})
            self.assertTrue(result.ok,result.summary);self.assertIsNone(self.resolve(s))
            self.assertEqual(38,s.state.players['A'].life);self.assertEqual('battlefield',s.state.cards[spell.object_id].zone)
            if color=='B':
                self.assertIn('menace',s.engine._combat_keywords(s.state.cards[body.object_id]))
                effective=s.engine._effective_card_data(s.state.cards[body.object_id])
                self.assertEqual((3,7),(int(effective['power']),int(effective['toughness'])))
            else:
                self.assertEqual(39,s.state.players['B'].life)
                self.assertIn('first strike',s.engine._combat_keywords(s.state.cards[spell.object_id]))
            self.private_replay(s,private)

    def test_actual_countered_paid_spell_keeps_life_and_mana_spent(self):
        s=self.session(800090);self.add(s.engine,'Defiler of Dreams',ref='countered-provider')
        spell=self.add(s.engine,'Defiler of Dreams',zone='hand',ref='countered-cast')
        counter=self.add(s.engine,'Counterspell',seat='B',zone='hand');private=self.add(s.engine,'Generic Bound Growth',seat='C',zone='hand')
        s.state.players['B'].mana_pool['U']=2;self.prepare(s,mana={'C':3,'U':1});self.checkpoint(s)
        self.pay(s,spell,self.paid_option(s,spell),{'C':3,'U':1})
        self.assertTrue(s.state.stack);self.cast(s,'B',counter,[s.state.stack[0].ref])
        s=self.replay(s,load=True);self.assertIsNone(self.resolve(s))
        self.assertEqual('graveyard',s.state.cards[spell.object_id].zone);self.assertEqual(38,s.state.players['A'].life)
        self.assertEqual(0,sum(s.state.players['A'].mana_pool.values()));self.private_replay(s,private)

    def test_actual_blue_instant_has_no_life_option_and_pays_both_blue_symbols(self):
        s=self.session(800100);self.add(s.engine,'Defiler of Dreams',ref='instant-excluded-provider')
        counter=self.add(s.engine,'Counterspell',zone='hand');spell=self.add(s.engine,'Generic Bound Body',seat='B',zone='hand')
        private=self.add(s.engine,'Generic Bound Growth',seat='C',zone='hand')
        s.state.players['A'].mana_pool['U']=2;self.prepare(s,actor='B',active='B',mana={'W':1});self.checkpoint(s)
        self.cast(s,'B',spell);self.priority(s,'A')
        options=self.options(s,counter);self.assertTrue(options)
        self.assertTrue(all(not o.get('additional_life',0) for o in options))
        self.assertTrue(all(o['requirements']['U']==2 for o in options))
        self.cast(s,'A',counter,[s.state.stack[0].ref]);s=self.replay(s,load=True);self.assertIsNone(self.resolve(s))
        self.assertEqual('graveyard',s.state.cards[spell.object_id].zone);self.assertEqual(40,s.state.players['A'].life)
        self.private_replay(s,private)

    def test_actual_without_mana_alternative_still_allows_optional_life_payment(self):
        s=self.session(800110);self.add(s.engine,'Defiler of Vigor',ref='free-provider');self.add(s.engine,'Omniscience')
        spell=self.add(s.engine,'Llanowar Elves',zone='hand');private=self.add(s.engine,'Generic Bound Growth',seat='C',zone='hand')
        self.prepare(s);self.checkpoint(s);option=self.paid_option(s,spell)
        self.assertEqual(0,sum(option['requirements'].values()))
        self.pay(s,spell,option,{});s=self.replay(s,load=True);self.assertIsNone(self.resolve(s))
        self.assertEqual('battlefield',s.state.cards[spell.object_id].zone);self.assertEqual(38,s.state.players['A'].life)
        self.private_replay(s,private)

    def test_actual_kicker_additional_colored_cost_composes_with_normal_and_granted_bases(self):
        for grant in (False,True):
            s=self.session(800130+grant);self.add(s.engine,'Defiler of Vigor',ref='kicker-provider')
            if grant:self.add(s.engine,'Fist of Suns')
            spell=self.add(s.engine,'Gnarlid Colony',zone='hand');private=self.add(s.engine,'Generic Bound Growth',seat='C',zone='hand')
            payment={'C':2,'W':1,'U':1,'B':1,'R':1,'G':1} if grant else {'C':3,'G':1}
            self.prepare(s,mana=payment);self.checkpoint(s)
            options=[o for o in self.options(s,spell) if o.get('additional_life')==2 and 'kicked' in o['id']
                and ('battlefield-alternative:' in o['id'])==grant]
            self.assertEqual(1,len(options),self.options(s,spell))
            self.assertEqual(1,options[0]['requirements']['G'])
            self.pay(s,spell,options[0],payment);s=self.replay(s,load=True);self.assertIsNone(self.resolve(s))
            current=s.state.cards[spell.object_id]
            self.assertEqual('battlefield',current.zone);self.assertEqual(2,current.counters.get('+1/+1',0))
            self.assertIn('trample',s.engine._combat_keywords(current));self.assertEqual(38,s.state.players['A'].life)
            self.private_replay(s,private)

    def test_only_current_controlled_unphased_matching_battlefield_providers_apply(self):
        s=self.session(800120);self.add(s.engine,'Defiler of Dreams',ref='current-blue-provider')
        self.add(s.engine,'Defiler of Instinct',seat='B',ref='foreign-red-provider')
        self.add(s.engine,'Defiler of Faith',zone='graveyard',ref='grave-white-provider')
        phased=self.add(s.engine,'Defiler of Flesh',ref='phased-black-provider');phased.phased_out=True
        spells=[self.add(s.engine,name,zone='hand',ref='scope-cast-'+str(i)) for i,name in enumerate(NAMES)]
        self.prepare(s,mana={'C':3,'W':2,'U':2,'B':2,'R':2,'G':2});self.checkpoint(s)
        for name,spell in zip(NAMES,spells):
            options=self.options(s,spell);self.assertTrue(options,name)
            self.assertEqual(name=='Defiler of Dreams',any(o.get('additional_life',0) for o in options),name)
        self.assertEqual(40,s.state.players['A'].life);self.replay(s,load=True)

    def test_actual_source_ability_loss_removes_paid_offer_and_stale_command_rolls_back(self):
        s=self.session(800050);source=self.add(s.engine,'Defiler of Dreams',ref='lost-provider')
        spell=self.add(s.engine,'Defiler of Dreams',zone='hand',ref='lost-cast');gift=self.add(s.engine,'Gift of Tusks',zone='hand')
        self.prepare(s,mana={'C':3,'U':3});self.checkpoint(s);old=self.paid_option(s,spell)
        self.cast(s,'A',gift,[source.ref]);self.assertIsNone(self.resolve(s))
        self.assertFalse(any(o.get('additional_life',0) for o in self.options(s,spell)))
        self.reject(s,{'action_id':'cast:'+spell.ref,'cost_option':old['id'],'pay':'auto'})
        self.assertEqual(40,s.state.players['A'].life);self.replay(s,load=True)

    def test_actual_global_alternative_and_increase_keep_other_colors_and_generic_cost(self):
        s=self.session(800060);self.add(s.engine,'Defiler of Vigor',ref='alternative-provider')
        self.add(s.engine,'Fist of Suns');self.add(s.engine,'Sphere of Resistance')
        spell=self.add(s.engine,'Llanowar Elves',zone='hand');private=self.add(s.engine,'Generic Bound Growth',seat='C',zone='hand')
        self.prepare(s,mana={'C':1,'W':1,'U':1,'B':1,'R':1});self.checkpoint(s)
        options=[o for o in self.options(s,spell) if o.get('additional_life',0) and 'battlefield-alternative:' in o['id']]
        self.assertEqual(1,len(options),self.options(s,spell));option=options[0]
        self.assertEqual({'GENERIC':1,'W':1,'U':1,'B':1,'R':1,'G':0,'C':0},option['requirements'])
        self.pay(s,spell,option,{'C':1,'W':1,'U':1,'B':1,'R':1});s=self.replay(s,load=True)
        self.assertIsNone(self.resolve(s));self.assertEqual('battlefield',s.state.cards[spell.object_id].zone)
        self.assertEqual(38,s.state.players['A'].life);self.private_replay(s,private)

    def test_sacrificed_provider_keeps_locked_discount_without_a_late_cast_trigger(self):
        s=self.session(800070);source=self.add(s.engine,'Defiler of Flesh',ref='sacrificed-provider')
        spell=self.add(s.engine,'Demon of Catastrophes',zone='hand');private=self.add(s.engine,'Generic Bound Growth',seat='C',zone='hand')
        self.prepare(s,mana={'C':2,'B':1});self.checkpoint(s);option=self.paid_option(s,spell)
        command={'action_id':'cast:'+spell.ref,'cost_option':option['id'],'pay':'manual','payment':{'C':2,'B':1},'sacrifice_cards':[source.ref]}
        result=s.act('pilot:A',command);self.assertTrue(result.ok,result.summary)
        self.assertEqual('graveyard',s.state.cards[source.object_id].zone);self.assertEqual(38,s.state.players['A'].life)
        s=self.replay(s,load=True);self.assertIsNone(self.resolve(s));self.assertEqual('battlefield',s.state.cards[spell.object_id].zone)
        self.private_replay(s,private)

    def test_verified_owner_withdrawal_and_fresh_compiler_and_offer_omissions_reject(self):
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from scripts.update_capability_evidence import build_index,discover_tests
        raw=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        next(c for c in raw['capabilities'] if c['id']==CAP).update(status='blocked',blockers=['withdrawn optional life owner'],dependency_fail_closed_status='not_run')
        registry=CapabilityRegistry(raw);decl=read_evidence_document(ROOT/'platform/capability-evidence-declarations.json')
        index=build_index(registry_value=raw,declaration_source=decl,discovered_test_ids=set(discover_tests(ROOT).values()))
        registry.mark_evidence_verified(validate_capability_evidence_index(index,registry=registry))
        ir=compile_oracle_card(self.db.lookup('Defiler of Dreams'),capability_registry=registry,capability_profile='commander_review')
        blockers=[b for r in ir.material_residuals for b in r.blockers]
        self.assertTrue(any('status:'+CAP+':blocked' in b for b in blockers),blockers)
        self.assertFalse(any('evidence_index:unverified' in b for b in blockers))
        from quorune.compiler import optional_life_costs as compiler
        method='test_five_originals_have_complete_admission_and_independent_runtime_owner'
        with patch.object(compiler,'optional_life_color_reduction_template',return_value=None):
            with self.assertRaises(AssertionError):getattr(type(self)(method),method)()
        from quorune.rules.casting import optional_life_costs as owner
        method='test_actual_dreams_paid_and_declined_both_draw_and_keep_printed_flying'
        with patch.object(owner,'optional_life_providers',return_value=()):
            with self.assertRaises(AssertionError):getattr(type(self)(method),method)()
