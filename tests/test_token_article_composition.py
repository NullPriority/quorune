from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from pathlib import Path
import json,re,tempfile,unittest
from unittest.mock import patch

from common import ROOT
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition,DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
import test_turn_cast_grants as controls
from quorune.rules.evidence_storage import read_evidence_document

NAMES=('Slime Molding','Spoils of Blood','Miming Slime','Kin-Tree Invocation','Seed Guardian','Wurmcalling',
    'Oviya Pashiri, Sage Lifecrafter','Ominous Seas','Tumbleweed Rising','Flesh Carver','Formless Genesis','Abzan Monument')
CAP='token.creation.fixed_definition'


class TokenArticleCompositionTests(unittest.TestCase):
    register_fixture_cards_on_add=True
    session=controls.TurnCastGrantTests.session
    add=controls.TurnCastGrantTests.add
    prepare=controls.TurnCastGrantTests.prepare
    checkpoint=controls.TurnCastGrantTests.checkpoint
    resolve=controls.TurnCastGrantTests.resolve
    replay=controls.TurnCastGrantTests.replay
    binding=controls.TurnCastGrantTests.binding
    actions=controls.TurnCastGrantTests.actions
    cast=controls.TurnCastGrantTests.cast
    reject=controls.TurnCastGrantTests.reject
    private_replay=controls.TurnCastGrantTests.private_replay
    priority=controls.TurnCastGrantTests.priority
    advance=controls.TurnCastGrantTests.advance
    options=controls.TurnCastGrantTests.options
    use=controls.TurnCastGrantTests.use
    top=controls.TurnCastGrantTests.top

    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory();path=Path(cls.temp.name)/'token-article.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',ROOT/'tests/fixtures/token-article-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Exact singular token amounts',[DeckEntry('Generic Bound Commander',1,'commander'),DeckEntry('Generic Bound Plains',30)],['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls):cls.db.close();cls.temp.cleanup()

    def tokens(self,s,seat='A'):
        return [c for c in s.state.cards.values() if c.is_token and c.zone=='battlefield' and c.controller==seat]

    def token(self,s,size,*,seat='A',count=1):
        cards=self.tokens(s,seat);self.assertEqual(count,len(cards),[(c.printed_name,c.zone) for c in cards])
        for card in cards:
            data=s.engine._copyable_characteristics(card)
            self.assertEqual((str(size),str(size)),(str(data['power']),str(data['toughness'])))
            self.assertEqual({},dict(card.counters))
        return cards[0]

    def settle(self,s):
        for _ in range(16):
            pending=self.resolve(s)
            if pending is None or pending.kind!='trigger.order':return pending
            principal=s.pending_principals()[0];rows=s.packet(principal,full=True)['decision']['ctx']['triggers']
            result=s.act(principal,{'action_id':'order','triggers':[row['id'] for row in rows]});self.assertTrue(result.ok,result.summary)
        self.fail('Token siblings did not settle')

    def normal(self,s,card,payment,**choices):
        option=next((o for o in self.options(s,card) if o['id']=='normal'),None);self.assertIsNotNone(option)
        self.use(s,card,option,payment,**choices)

    def activate(self,s,source,needle='',payment=None,**choices):
        self.priority(s,'A');rows=[a for a in self.actions(s) if a['id'].startswith('activate:'+source.ref+':')]
        row=next((a for a in rows if needle.casefold() in json.dumps(a).casefold()),None);self.assertIsNotNone(row,rows)
        command={'action_id':row['id'],'pay':'manual','payment':payment or {},**choices};self.reject(s,command,'pilot:D')
        result=s.act('pilot:A',command);self.assertTrue(result.ok,result.summary)

    def test_originals_admit_normally_and_token_runtime_minimum_is_independent(self):
        from quorune.card_programs.binding import bind_semantic_program_runtime
        s=self.session(806000)
        for i,name in enumerate(NAMES):
            source=self.add(s.engine,name,ref='article-original-'+str(i));binding=self.binding(s,source)
            self.assertTrue(binding['strict_capability_ready'],(name,binding['blockers']))
            ir=compile_oracle_card(self.db.lookup(name),capability_registry=self.registry,capability_profile='commander_review')
            self.assertEqual('exact',ir.status,(name,ir.material_residuals))
            program=next((p for p in s.engine.semantics.programs_for_oracle(source.oracle_id) if CAP in p.capability_dependencies),None);self.assertIsNotNone(program,name)
            deps=tuple(c for c in program.capability_dependencies if c!=CAP)
            changed=replace(program,capability_dependencies=deps,capability_closure=self.registry.closure(deps,profile='commander_review').to_dict() if deps else None)
            bound=bind_semantic_program_runtime(changed,capability_registry=self.registry,profile='commander_review')
            self.assertFalse(bound['strict'],(name,bound));self.assertIn('capability:undeclared_runtime_dependency:'+CAP,bound['blockers'])

    def test_singular_articles_share_one_closed_definition_and_original_source_span(self):
        from quorune.compiler.token_templates import fixed_token_creation_effect_template as compile_token
        for stats in ('0/0','2/2','8/8'):
            self.assertEqual(compile_token('Create a '+stats+' blue Kraken creature token.'),compile_token('Create an '+stats+' blue Kraken creature token.'))
        for bad in ('Create any 8/8 blue Kraken creature token.','Create another 8/8 blue Kraken creature token.',
                'Create an X/X blue Kraken creature token.','Create an 8/8 blue Kraken creature token with unknown prowess.'):
            self.assertIsNone(compile_token(bad),bad)
        card=self.db.lookup('Slime Molding');ir=compile_oracle_card(card,capability_registry=self.registry,capability_profile='commander_review')
        self.assertEqual('exact',ir.status);self.assertEqual(card.oracle_text,ir.faces[0].nodes[0].text)
        effect=ir.faces[0].nodes[0].effects[0]
        from quorune.query_effect_amount_model import CAST_X_AMOUNT_KIND
        self.assertEqual(1,effect['quantity']);self.assertEqual(CAST_X_AMOUNT_KIND,effect['characteristics']['power']['kind'])
        from quorune.rules.token_creation_capability_shapes import token_definition_payload_dependencies
        from quorune.fixed_token_production import afterlife_token_effect,AFTERLIFE_CAPABILITY_ID
        from quorune.replacement.immutable import FrozenMap
        self.assertEqual((CAP,),token_definition_payload_dependencies(effect))
        afterlife=FrozenMap(afterlife_token_effect(1))
        self.assertEqual((AFTERLIFE_CAPABILITY_ID,),token_definition_payload_dependencies(afterlife,('afterlife',)))
        self.assertEqual((CAP,),token_definition_payload_dependencies(afterlife))
        self.assertEqual((),token_definition_payload_dependencies({'op':'create_token','name':'legacy','quantity':1}))
        self.assertEqual((),token_definition_payload_dependencies({**effect,'copy_of':'$source'}))
        promoted=compile_oracle_card(self.db.lookup('Fractal Summoning'),capability_registry=self.registry,capability_profile='commander_review')
        self.assertEqual('exact',promoted.status,promoted.material_residuals)
        for name in ('Mystic Genesis','Grove of the Guardian'):
            other=compile_oracle_card(self.db.lookup(name),capability_registry=self.registry,capability_profile='commander_review')
            self.assertNotEqual('exact',other.status,name);self.assertTrue(other.material_residuals)

    def test_actual_announced_x_creates_fixed_base_body_including_real_zero_token(self):
        for size in (0,1,3):
            s=self.session(806010+size);spell=self.add(s.engine,'Slime Molding',zone='hand');private=self.add(s.engine,'Generic Bound Growth',seat='C',zone='hand')
            self.prepare(s,mana={'C':size,'G':1});self.checkpoint(s)
            self.reject(s,{'action_id':'cast:'+spell.ref,'x':-1,'pay':'auto'});self.normal(s,spell,{'C':size,'G':1},x=size)
            s=self.replay(s,load=True);self.assertIsNone(self.settle(s))
            events=[e for e in s.state.events if e.code=='token.create'];self.assertEqual(1,len(events));self.assertEqual(1,events[0].details['base_quantity'])
            if size:self.token(s,size)
            else:self.assertEqual([],self.tokens(s))
            self.assertEqual('graveyard',s.state.cards[spell.object_id].zone);self.private_replay(s,private)

    def test_countered_announced_x_spends_cost_without_creating_a_token(self):
        s=self.session(806020);spell=self.add(s.engine,'Slime Molding',zone='hand');counter=self.add(s.engine,'Counterspell',seat='B',zone='hand')
        private=self.add(s.engine,'Generic Bound Growth',seat='C',zone='hand');s.state.players['B'].mana_pool['U']=2
        self.prepare(s,mana={'C':3,'G':1});self.checkpoint(s);self.normal(s,spell,{'C':3,'G':1},x=3)
        self.cast(s,'B',counter,[s.state.stack[-1].ref]);self.assertIsNone(self.settle(s));self.assertEqual([],self.tokens(s))
        self.assertFalse(any(e.code=='token.create' for e in s.state.events));self.assertEqual(0,sum(s.state.players['A'].mana_pool.values()));self.private_replay(s,private)

    def test_current_power_and_toughness_queries_snapshot_the_new_body(self):
        for i,(name,size,cost) in enumerate((('Miming Slime',4,{'C':2,'G':1}),('Kin-Tree Invocation',8,{'B':1,'G':1}),('Tumbleweed Rising',4,{'C':1,'G':1}))):
            s=self.session(806030+i);body=self.add(s.engine,'Generic Bound Body');foreign=self.add(s.engine,'Generic Bound Body',seat='B',ref='large-foreign')
            foreign.counters['+1/+1']=10;growth=self.add(s.engine,'Generic Bound Growth',zone='hand');gift=self.add(s.engine,'Gift of Tusks',zone='hand')
            spell=self.add(s.engine,name,zone='hand');private=self.add(s.engine,'Generic Bound Growth',seat='C',zone='hand',ref='query-private')
            pool={'C':2,'G':2,'B':1,'U':1};self.prepare(s,mana=pool);self.checkpoint(s)
            self.normal(s,growth,{'G':1},targets=[body.ref]);self.assertIsNone(self.settle(s));self.normal(s,spell,cost)
            s=self.replay(s,load=True);self.assertIsNone(self.settle(s));token=self.token(s,size)
            self.normal(s,gift,{'U':1},targets=[body.ref]);self.assertIsNone(self.settle(s))
            self.assertEqual((str(size),str(size)),tuple(str(s.engine._effective_card_data(s.state.cards[token.object_id])[key]) for key in ('power','toughness')));self.private_replay(s,private)

    def test_actual_spoils_counts_real_creature_deaths_and_freezes_the_result(self):
        s=self.session(806040);self.add(s.engine,'Generic Bound Body',ref='dies-A');self.add(s.engine,'Generic Bound Body',seat='B',ref='dies-B')
        wipe=self.add(s.engine,'Day of Judgment',zone='hand');spell=self.add(s.engine,'Spoils of Blood',zone='hand')
        later=self.add(s.engine,'Generic Bound Body',zone='hand',ref='later-body');kill=self.add(s.engine,'Go for the Throat',zone='hand')
        private=self.add(s.engine,'Generic Bound Growth',seat='C',zone='hand')
        self.prepare(s,mana={'C':3,'W':3,'B':2});self.checkpoint(s);self.normal(s,wipe,{'C':2,'W':2});self.assertIsNone(self.settle(s))
        self.normal(s,spell,{'B':1});s=self.replay(s,load=True);self.assertIsNone(self.settle(s));token=self.token(s,2)
        self.normal(s,later,{'W':1});self.assertIsNone(self.settle(s));self.normal(s,kill,{'C':1,'B':1},targets=[later.ref]);self.assertIsNone(self.settle(s))
        self.assertEqual('2',str(s.engine._effective_card_data(s.state.cards[token.object_id])['power']));self.private_replay(s,private)

    def test_actual_seed_death_counts_current_owned_creature_cards_including_itself(self):
        s=self.session(806050);source=self.add(s.engine,'Seed Guardian')
        self.add(s.engine,'Generic Bound Body',zone='graveyard',ref='prior-body');self.add(s.engine,'Sol Ring',zone='graveyard')
        self.add(s.engine,'Generic Bound Body',seat='B',zone='graveyard',ref='foreign-corpse')
        kill=self.add(s.engine,'Go for the Throat',zone='hand');private=self.add(s.engine,'Generic Bound Growth',seat='C',zone='hand')
        self.prepare(s,mana={'C':1,'B':1});self.checkpoint(s);self.assertIn('reach',s.engine._combat_keywords(source))
        self.normal(s,kill,{'C':1,'B':1},targets=[source.ref]);s=self.replay(s,load=True);self.assertIsNone(self.settle(s))
        self.assertEqual('graveyard',s.state.cards[source.object_id].zone);self.token(s,2);self.private_replay(s,private)

    def test_actual_buyback_retains_announced_x_and_returns_the_spell(self):
        s=self.session(806060);spell=self.add(s.engine,'Wurmcalling',zone='hand');private=self.add(s.engine,'Generic Bound Growth',seat='C',zone='hand')
        self.prepare(s,mana={'C':4,'G':2});self.checkpoint(s)
        option=next((o for o in self.options(s,spell) if 'Buyback' in o.get('label','')),None);self.assertIsNotNone(option)
        self.use(s,spell,option,{'C':4,'G':2},x=2);s=self.replay(s,load=True);self.assertIsNone(self.settle(s));self.token(s,2)
        self.assertEqual('hand',s.state.cards[spell.object_id].zone);self.private_replay(s,private)

    def test_actual_oviya_and_replacement_snapshot_count_before_each_creation_batch(self):
        s=self.session(806070);source=self.add(s.engine,'Oviya Pashiri, Sage Lifecrafter');self.add(s.engine,'Generic Bound Body')
        replacement=self.add(s.engine,'Parallel Lives');self.add(s.engine,'Generic Bound Body',seat='B',ref='foreign-body')
        growth=self.add(s.engine,'Generic Bound Growth',zone='hand');private=self.add(s.engine,'Generic Bound Growth',seat='C',zone='hand',ref='oviya-private')
        self.prepare(s,mana={'C':6,'G':3});self.checkpoint(s);self.assertTrue(self.binding(s,replacement)['strict_capability_ready'])
        self.activate(s,source,'Servo',{'C':2,'G':1});self.assertIsNone(self.settle(s));self.token(s,1,count=2)
        self.normal(s,growth,{'G':1},targets=[source.ref]);self.assertIsNone(self.settle(s));self.activate(s,source,'Construct',{'C':4,'G':1})
        s=self.replay(s,load=True);self.assertIsNone(self.settle(s))
        constructs=[c for c in self.tokens(s) if c.printed_name=='Construct'];self.assertEqual(2,len(constructs))
        for card in constructs:self.assertEqual(('4','4'),tuple(str(s.engine._copyable_characteristics(card)[key]) for key in ('power','toughness')))
        self.assertTrue(s.state.cards[source.object_id].tapped);self.private_replay(s,private)

    def test_actual_flesh_carver_uses_death_lki_after_counter_reset(self):
        s=self.session(806080);source=self.add(s.engine,'Flesh Carver');victim=self.add(s.engine,'Generic Bound Body')
        kill=self.add(s.engine,'Go for the Throat',zone='hand');private=self.add(s.engine,'Generic Bound Growth',seat='C',zone='hand')
        self.prepare(s,mana={'C':2,'B':2});self.checkpoint(s);self.assertIn('intimidate',s.engine._combat_keywords(source))
        self.activate(s,source,payment={'C':1,'B':1},cost_cards=[victim.ref]);self.assertIsNone(self.settle(s))
        self.assertEqual(2,s.state.cards[source.object_id].counters.get('+1/+1'));self.normal(s,kill,{'C':1,'B':1},targets=[source.ref])
        s=self.replay(s,load=True);self.assertIsNone(self.settle(s));self.assertEqual({},dict(s.state.cards[source.object_id].counters));self.token(s,4);self.private_replay(s,private)

    def test_actual_retrace_discards_a_real_land_before_resolving_land_card_count(self):
        from quorune.object_predicate import ObjectQuerySpec
        from quorune.object_query import object_query_result,object_matches_query
        s=self.session(806090);spell=self.add(s.engine,'Formless Genesis',zone='graveyard');land=self.add(s.engine,'Generic Bound Plains',zone='hand',ref='retrace-payment')
        self.add(s.engine,'Forest',zone='graveyard');private=self.add(s.engine,'Generic Bound Growth',seat='C',zone='hand')
        self.prepare(s,mana={'C':2,'G':1});self.checkpoint(s)
        option=next((o for o in self.options(s,spell) if o['id']=='retrace'),None);self.assertIsNotNone(option)
        self.use(s,spell,option,{'C':2,'G':1},discard_cards=[land.ref]);s=self.replay(s,load=True);self.assertIsNone(self.settle(s));token=self.token(s,2)
        self.assertEqual('graveyard',s.state.cards[land.object_id].zone);self.assertIn('deathtouch',s.engine._combat_keywords(token))
        data=s.engine._effective_card_data(token);row=object_query_result(token,data,type_parts=s.engine._type_parts(data['type_line']),known_to_actor=True,attached_to_ref=None)
        self.assertTrue(object_matches_query(row,ObjectQuerySpec(zones=('battlefield',),subtypes_any=('zombie',))));self.private_replay(s,private)

    def test_actual_monument_search_and_paid_sacrifice_keep_greatest_toughness_result(self):
        s=self.session(806100);source=self.add(s.engine,'Abzan Monument',zone='hand');self.add(s.engine,'Generic Bound Body')
        forest=self.top(s,'Forest','monument-forest');island=self.add(s.engine,'Island',zone='library');mountain=self.add(s.engine,'Mountain',zone='library')
        private=self.add(s.engine,'Generic Bound Growth',seat='C',zone='hand')
        self.prepare(s,mana={'C':3,'W':1,'B':1,'G':1});self.checkpoint(s);self.normal(s,source,{'C':2});pending=self.settle(s)
        self.assertIsNotNone(pending);self.assertEqual('semantic.search',pending.kind)
        packet=s.packet('pilot:A',full=True)['decision']['ctx'];refs={row['id'] for row in packet['search_cards']}
        self.assertIn(forest.ref,refs);self.assertNotIn(island.ref,refs);self.assertNotIn(mountain.ref,refs)
        command={'action_id':'choose','search_cards':[forest.ref]};self.reject(s,command,'pilot:D');result=s.act('pilot:A',command);self.assertTrue(result.ok,result.summary)
        self.assertIsNone(self.settle(s));self.assertEqual('hand',s.state.cards[forest.object_id].zone)
        self.activate(s,source,payment={'C':1,'W':1,'B':1,'G':1});s=self.replay(s,load=True);self.assertIsNone(self.settle(s))
        self.assertEqual('graveyard',s.state.cards[source.object_id].zone);self.token(s,6);self.private_replay(s,private)

    def test_actual_ominous_draws_pay_named_counters_for_one_literal_token(self):
        s=self.session(806110);source=self.add(s.engine,'Ominous Seas');spells=[self.add(s.engine,'Divination',zone='hand',ref='draw-'+str(i)) for i in range(4)]
        private=self.add(s.engine,'Generic Bound Growth',seat='C',zone='hand')
        self.prepare(s,mana={'C':8,'U':4});self.checkpoint(s)
        for spell in spells:self.normal(s,spell,{'C':2,'U':1});self.assertIsNone(self.settle(s))
        self.assertEqual(8,s.state.cards[source.object_id].counters.get('foreshadow'));self.activate(s,source)
        s=self.replay(s,load=True);self.assertIsNone(self.settle(s));self.assertEqual(0,s.state.cards[source.object_id].counters.get('foreshadow',0));self.token(s,8);self.private_replay(s,private)

    def test_verified_token_withdrawal_and_fresh_article_and_numeric_omissions_reject(self):
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from scripts.update_capability_evidence import build_index,discover_tests
        raw=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding='utf-8'));next(c for c in raw['capabilities'] if c['id']==CAP).update(status='blocked',blockers=['withdrawn token definition'],dependency_fail_closed_status='not_run')
        registry=CapabilityRegistry(raw);decl=read_evidence_document(ROOT/'platform/capability-evidence-declarations.json')
        index=build_index(registry_value=raw,declaration_source=decl,discovered_test_ids=set(discover_tests(ROOT).values()));registry.mark_evidence_verified(validate_capability_evidence_index(index,registry=registry))
        ir=compile_oracle_card(self.db.lookup('Slime Molding'),capability_registry=registry,capability_profile='commander_review');blockers=[b for r in ir.material_residuals for b in r.blockers]
        self.assertTrue(any('status:'+CAP+':blocked' in b for b in blockers),blockers);self.assertFalse(any('evidence_index:unverified' in b for b in blockers))
        from quorune.compiler import token_templates as owner
        method='test_actual_announced_x_creates_fixed_base_body_including_real_zero_token'
        old=re.compile(owner._FIXED_CREATURE_TOKEN.pattern.replace('|an|','|'),owner._FIXED_CREATURE_TOKEN.flags)
        with patch.object(owner,'_FIXED_CREATURE_TOKEN',old):
            with self.assertRaises(AssertionError):getattr(type(self)(method),method)()
        from quorune.semantic_runtime import values
        original=values.resolve_cast_x_amount
        with patch.object(values,'resolve_cast_x_amount',side_effect=lambda value,item:original(value,item)+1):
            with self.assertRaises(AssertionError):getattr(type(self)(method),method)()
