from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from common import ROOT
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.conditional_free_cast_model import CONDITIONAL_FREE_CAST_CAPABILITY as CAPABILITY
from quorune.rules.conditional_free_cast_shapes import conditional_free_cast_capabilities
from quorune.public_alternative_costs import FixedPublicAlternativeCondition, FixedPublicAlternativeConditionKind as Kind, FixedPublicAlternativeCostSpec, fixed_public_alternative_condition_met
from quorune.compiler.fixed_public_alternative_costs import compile_fixed_public_alternative_cost
from scripts.build_test_database import build_fixture_database
import test_receiver_query_unions as controls
from quorune.rules.evidence_storage import read_evidence_document


LEGATES = ('Cho-Arrim Legate', 'Deepwood Legate', 'Kyren Legate', 'Rushwood Legate', 'Saprazzan Legate')
LAND_PAIRS = {'Cho-Arrim Legate': ('plains', 'swamp'), 'Deepwood Legate': ('swamp', 'forest'),
    'Kyren Legate': ('mountain', 'plains'), 'Rushwood Legate': ('forest', 'island'),
    'Saprazzan Legate': ('island', 'mountain'), 'Massacre': ('swamp', 'plains'),
    'Mogg Salvage': ('mountain', 'island'), 'Refreshing Rain': ('forest', 'swamp'),
    'Submerge': ('island', 'forest'), "Sivvi's Ruse": ('plains', 'mountain')}
NAMES = (*LEGATES, 'Massacre', 'Mogg Salvage', 'Refreshing Rain', 'Submerge', "Sivvi's Ruse", 'Deadly Rollick', 'Fierce Guardianship', 'Flawless Maneuver', 'Obscuring Haze')


def compile_line(text):
    return compile_fixed_public_alternative_cost(material_line=text, oracle_line=text, line_index=0)


class ConditionalFreeCastModelTests(unittest.TestCase):
    def test_new_condition_wire_is_closed_and_old_condition_and_price_stay_exact(self):
        for kind in (Kind.ALWAYS, Kind.YOUR_TURN, Kind.NOT_YOUR_TURN):
            old = FixedPublicAlternativeCondition(kind).to_dict()
            self.assertEqual({'schema_version': 1, 'kind': kind.value, 'basic_land_type': None}, old)
            self.assertEqual(old, FixedPublicAlternativeCondition.from_dict(old).to_dict())
        for condition in (FixedPublicAlternativeCondition(Kind.LAND_PAIR, 'forest', 2, 'island'),
                FixedPublicAlternativeCondition(Kind.CONTROLLED_COMMANDER, schema_version=2)):
            self.assertEqual(condition, FixedPublicAlternativeCondition.from_dict(json.loads(json.dumps(condition.to_dict()))))
            for changes in ({'schema_version': 1}, {'schema_version': True}, {'kind': 'future'}, {'future': 1}):
                with self.assertRaises(ValueError):
                    FixedPublicAlternativeCondition.from_dict({**condition.to_dict(), **changes})
        text = 'If an opponent controls an Island and you control a Forest, you may cast this spell without paying its mana cost.'
        spec = compile_line(text)
        self.assertEqual('{0}', spec.cost_text)
        self.assertFalse(any(dict(spec.mana_requirements).values()))
        self.assertEqual(spec, FixedPublicAlternativeCostSpec.from_dict(spec.to_dict()))
        self.assertEqual((CAPABILITY,), conditional_free_cast_capabilities(spec.condition.to_dict()))
        with self.assertRaises(ValueError):
            replace(spec, cost_text=None)
        for field, value in (('basic_land_type', 'Forest'), ('opponent_basic_land_type', 'wastes')):
            with self.assertRaises(ValueError):
                replace(spec.condition, **{field: value})

    def test_pinned_public_surfaces_reject_unowned_timing_prices_and_conditions(self):
        data = json.loads((ROOT/'tests/fixtures/conditional-free-cast-cards.json').read_text(encoding='utf-8'))
        for card in data['cards'][:15]:
            line = card['oracle_text'].splitlines()[0]
            spec = compile_line(line)
            self.assertIsNotNone(spec, card['name'])
            self.assertEqual('{0}', spec.cost_text)
            self.assertFalse(spec.additional_costs)
        for text in ('If you own a commander, you may cast this spell without paying its mana cost.',
                'If you control your commander, you may cast this spell without paying its mana cost.',
                'If an opponent controls two Islands, you may cast this spell without paying its mana cost.',
                'If an opponent controls an Island and you control a Forest, you may cast this spell without paying its mana cost and as though it had flash.',
                'If you control a commander, you may cast this spell for {X}.'):
            self.assertIsNone(compile_line(text))

    def test_condition_reads_caster_current_land_types_and_any_owned_commander(self):
        cards = {'own': SimpleNamespace(object_id='own', zone='battlefield', controller='B', phased_out=False, is_commander=False, owner='A', kind='Land — Forest'),
            'other': SimpleNamespace(object_id='other', zone='battlefield', controller='C', phased_out=False, is_commander=False, owner='B', kind='Land — Island')}
        host = SimpleNamespace(state=SimpleNamespace(cards=cards, players={s:SimpleNamespace(zones={'battlefield':[k for k,c in cards.items() if c.controller==s]}) for s in 'ABCD'}),
            active_seats=list('ABCD'), _effective_card_data=lambda card:{'type_line':card.kind},
            _type_parts=lambda text:({'land'},set(text.casefold().split())&{'forest','island'},set()))
        condition=FixedPublicAlternativeCondition(Kind.LAND_PAIR, 'forest', 2, 'island')
        self.assertTrue(fixed_public_alternative_condition_met(host,'B',None,condition.to_dict()))
        self.assertFalse(fixed_public_alternative_condition_met(host,'A',None,condition.to_dict()))
        cards['other'].phased_out=True
        self.assertFalse(fixed_public_alternative_condition_met(host,'B',None,condition.to_dict()))
        commander=FixedPublicAlternativeCondition(Kind.CONTROLLED_COMMANDER,schema_version=2)
        cards['own'].is_commander=True
        self.assertTrue(fixed_public_alternative_condition_met(host,'B',None,commander.to_dict()))
        cards['own'].phased_out=True
        self.assertFalse(fixed_public_alternative_condition_met(host,'B',None,commander.to_dict()))


class ConditionalFreeCastRuntimeTests(unittest.TestCase):
    register_fixture_cards_on_add = True
    session = controls.ReceiverQueryUnionRuntimeTests.session
    add = controls.ReceiverQueryUnionRuntimeTests.add
    prepare = controls.ReceiverQueryUnionRuntimeTests.prepare
    checkpoint = controls.ReceiverQueryUnionRuntimeTests.checkpoint
    resolve = controls.ReceiverQueryUnionRuntimeTests.resolve
    replay = controls.ReceiverQueryUnionRuntimeTests.replay
    binding = controls.ReceiverQueryUnionRuntimeTests.binding
    priority = controls.ReceiverQueryUnionRuntimeTests.priority
    private_replay = controls.ReceiverQueryUnionRuntimeTests.private_replay
    stats = controls.ReceiverQueryUnionRuntimeTests.stats

    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        path = Path(cls.temp.name)/'conditional-free-casts.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',
            ROOT/'tests/fixtures/conditional-free-cast-cards.json'], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition('Conditional zero mana', [DeckEntry('Generic Bound Commander', 1, 'commander'),
            DeckEntry('Generic Bound Plains', 30)], ['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temp.cleanup()

    def actions(self,s,seat='A'):
        return s.packet('pilot:'+seat,full=True)['decision']['ctx']['legal']['actions']

    def offer(self,s,card,seat='A'):
        row=next((r for r in self.actions(s,seat) if r['id']=='cast:'+card.ref),None)
        self.assertIsNotNone(row,card.printed_name)
        return row

    def reject(self,s,command,seat='A'):
        before=authoritative_state_hash(s.state)
        result=s.act('pilot:'+seat,command)
        self.assertFalse(result.ok,result.summary)
        self.assertEqual(before,authoritative_state_hash(s.state))

    def cast(self,s,card,targets=(),*,seat='A',payment=None,cost_option=None):
        self.priority(s,seat);row=self.offer(s,card,seat)
        command={'action_id':row['id'],'targets':list(targets),'pay':'auto'}
        if payment is not None:command.update(pay='manual',payment=payment)
        if cost_option is not None:command['cost_option']=cost_option
        self.reject(s,command,'D' if seat!='D' else 'C')
        result=s.act('pilot:'+seat,command);self.assertTrue(result.ok,result.summary)

    def free_cast(self,s,source,targets=(),seat='A'):
        self.priority(s,seat)
        row=self.offer(s,source,seat)
        option=next((r for r in row['cost_options'] if r['id'].startswith('plain-alternative-')),None)
        self.assertIsNotNone(option,source.printed_name)
        command={'action_id':row['id'],'cost_option':option['id'],'targets':list(targets),'pay':'auto'}
        self.reject(s,command,'D' if seat!='D' else 'C')
        accepted=s.act('pilot:'+seat,command)
        self.assertTrue(accepted.ok,accepted.summary)

    def lands(self,s,name,seat='A'):
        own,opponent=LAND_PAIRS[name]
        return (self.add(s.engine,own.title(),seat=seat,ref='own-price-land-'+seat),
            self.add(s.engine,opponent.title(),seat='B' if seat!='B' else 'C',ref='opponent-price-land-'+seat))

    def commander(self,s,seat='A',owner='B'):
        self.add(s.engine,'Generic Bound Commander',seat='D',zone='hand',ref='free-commander-registration')
        card=next(s.state.cards[oid] for oid in s.state.players[owner].zones['command'])
        s.engine.move_card(card.object_id,'battlefield',controller=owner,reason='conditional zero-mana Commander boundary')
        if seat!=owner:s.engine.change_control(card.object_id,seat,reason='borrowed designated Commander boundary')
        return s.state.cards[card.object_id]

    def test_fourteen_originals_have_complete_admission_and_independent_zero_price_minimum(self):
        from quorune.card_programs.binding import bind_semantic_program_runtime
        from quorune.oracle_ir import compile_oracle_card
        for i,name in enumerate(NAMES):
            s=self.session(755100+i);source=self.add(s.engine,name,zone='hand')
            self.assertTrue(self.binding(s,source)['strict_capability_ready'],name)
            ir=compile_oracle_card(self.db.lookup(name),capability_registry=self.registry,capability_profile='commander_review')
            self.assertEqual('exact',ir.status,(name,ir.material_residuals))
            program=next(p for p in s.engine.semantics.programs_for_oracle(source.oracle_id) if CAPABILITY in p.capability_dependencies)
            deps=tuple(c for c in program.capability_dependencies if c!=CAPABILITY)
            missing=replace(program,capability_dependencies=deps,
                capability_closure=self.registry.closure(deps,profile='commander_review').to_dict())
            bound=bind_semantic_program_runtime(missing,capability_registry=self.registry,profile='commander_review')
            self.assertFalse(bound['strict'])
            self.assertIn('capability:undeclared_runtime_dependency:'+CAPABILITY,bound['blockers'])

    def test_five_actual_legates_offer_both_prices_and_complete_free_creature_casts(self):
        for i,name in enumerate(LEGATES):
            s=self.session(755120+i);source=self.add(s.engine,name,zone='hand');self.lands(s,name)
            private=self.add(s.engine,'Generic Bound Growth',seat='C',zone='hand')
            mana={'W':8,'U':8,'B':8,'R':8,'G':8,'C':8}
            self.prepare(s,mana=mana);self.checkpoint(s)
            row=self.offer(s,source);self.assertIn('normal',[r['id'] for r in row['cost_options']])
            self.free_cast(s,source)
            self.assertEqual(mana,{k:s.state.players['A'].mana_pool[k] for k in mana})
            s=self.replay(s,load=True);self.assertIsNone(self.resolve(s))
            self.assertEqual('battlefield',s.state.cards[source.object_id].zone)
            card=self.db.lookup(name);self.assertEqual((int(card.power),int(card.toughness)),self.stats(s,source))
            expected={'Kyren Legate':'haste','Saprazzan Legate':'flying','Cho-Arrim Legate':'protection'}.get(name)
            if expected:self.assertIn(expected,s.engine._combat_keywords(s.state.cards[source.object_id]))
            self.private_replay(s,private)

    def test_land_pair_instants_and_massacre_execute_complete_fixed_results(self):
        for i,name in enumerate(('Massacre','Mogg Salvage','Refreshing Rain','Submerge')):
            s=self.session(755140+i);source=self.add(s.engine,name,zone='hand');self.lands(s,name)
            target=self.add(s.engine,'Grizzly Bears',seat='B')
            artifact=self.add(s.engine,'Sol Ring',seat='B') if name=='Mogg Salvage' else None
            private=self.add(s.engine,'Generic Bound Growth',seat='C',zone='hand')
            self.prepare(s);self.checkpoint(s)
            targets=[] if name=='Massacre' else [artifact.ref] if artifact is not None else ['C'] if name=='Refreshing Rain' else [target.ref]
            self.free_cast(s,source,targets)
            s=self.replay(s,load=True);self.assertIsNone(self.resolve(s))
            self.assertEqual('graveyard',s.state.cards[source.object_id].zone)
            if name=='Massacre':self.assertEqual('graveyard',s.state.cards[target.object_id].zone)
            elif name=='Mogg Salvage':self.assertEqual('graveyard',s.state.cards[artifact.object_id].zone)
            elif name=='Refreshing Rain':self.assertEqual(46,s.state.players['C'].life)
            else:
                self.assertEqual('library',s.state.cards[target.object_id].zone)
                self.assertEqual(target.object_id,s.state.players['B'].zones['library'][-1])
            self.private_replay(s,private)

    def test_borrowed_commander_enables_actual_rollick_guardianship_and_maneuver(self):
        for i,name in enumerate(('Deadly Rollick','Fierce Guardianship','Flawless Maneuver')):
            s=self.session(755160+i);source=self.add(s.engine,name,zone='hand');commander=self.commander(s)
            victim=self.add(s.engine,'Grizzly Bears',seat='B')
            spell=self.add(s.engine,'Shock',seat='B',zone='hand')
            removal=self.add(s.engine,'Go for the Throat',seat='B',zone='hand')
            private=self.add(s.engine,'Generic Bound Growth',seat='C',zone='hand')
            self.prepare(s);s.state.players['B'].mana_pool.update({'R':1,'B':1,'C':1});self.checkpoint(s)
            targets=[victim.ref] if name=='Deadly Rollick' else []
            if name=='Fierce Guardianship':
                self.priority(s,'B');self.cast(s,spell,['D'],seat='B');targets=[s.state.stack[-1].ref]
            self.free_cast(s,source,targets)
            s=self.replay(s,load=True);self.assertIsNone(self.resolve(s))
            if name=='Deadly Rollick':self.assertEqual('exile',s.state.cards[victim.object_id].zone)
            elif name=='Fierce Guardianship':
                self.assertEqual('graveyard',s.state.cards[spell.object_id].zone);self.assertEqual(40,s.state.players['D'].life)
            else:
                self.cast(s,removal,[commander.ref],seat='B');self.assertIsNone(self.resolve(s))
                self.assertEqual('battlefield',s.state.cards[commander.object_id].zone)
            self.private_replay(s,private)

    def test_each_legates_normal_printed_price_remains_available_without_the_condition(self):
        for i,name in enumerate(LEGATES):
            s=self.session(755180+i);source=self.add(s.engine,name,zone='hand')
            card=self.db.lookup(name)
            from quorune.util import mana_cost_to_vector
            requirements,_=mana_cost_to_vector(card.mana_cost)
            mana={('C' if k=='GENERIC' else k):v for k,v in requirements.items() if v}
            private=self.add(s.engine,'Generic Bound Growth',seat='C',zone='hand')
            self.prepare(s,mana=mana);self.checkpoint(s)
            row=self.offer(s,source)
            self.assertEqual(['normal'],[r['id'] for r in row['cost_options']])
            self.cast(s,source,payment=mana,cost_option='normal');self.assertIsNone(self.resolve(s))
            self.assertEqual('battlefield',s.state.cards[source.object_id].zone)
            self.assertEqual(0,sum(s.state.players['A'].mana_pool.values()))
            self.private_replay(s,private)

    def test_land_pair_stale_offer_type_control_and_phasing_require_both_current_relations(self):
        s=self.session(755190);source=self.add(s.engine,'Rushwood Legate',zone='hand');own,enemy=self.lands(s,'Rushwood Legate')
        private=self.add(s.engine,'Generic Bound Growth',seat='C',zone='hand')
        self.prepare(s);self.checkpoint(s)
        row=self.offer(s,source);option=next(r for r in row['cost_options'] if r['id'].startswith('plain-alternative-'))
        enemy.phased_out=True
        self.reject(s,{'action_id':row['id'],'cost_option':option['id'],'pay':'auto'})
        enemy=s.state.cards[enemy.object_id];enemy.phased_out=False
        s.engine.change_control(enemy.object_id,'A',reason='opponent land is no longer opponent controlled')
        self.prepare(s);self.checkpoint(s)
        self.assertFalse(any(r['id']=='cast:'+source.ref for r in self.actions(s)))
        s.engine.change_control(enemy.object_id,'B',reason='opponent land restored')
        own=s.state.cards[own.object_id];own.phased_out=True
        self.prepare(s);self.checkpoint(s)
        self.assertFalse(any(r['id']=='cast:'+source.ref for r in self.actions(s)))
        own.phased_out=False
        self.prepare(s);self.checkpoint(s)
        self.free_cast(s,source);self.assertIsNone(self.resolve(s))
        self.assertEqual('battlefield',s.state.cards[source.object_id].zone)
        self.private_replay(s,private)

    def test_zero_price_pays_increases_and_keeps_sorcery_timing_and_post_cast_cost_lock(self):
        s=self.session(755200);source=self.add(s.engine,'Rushwood Legate',zone='hand');own,enemy=self.lands(s,'Rushwood Legate')
        sphere=self.add(s.engine,'Sphere of Resistance')
        response=self.add(s.engine,'Boomerang',seat='B',zone='hand')
        other=self.add(s.engine,'Rushwood Legate',zone='hand',ref='offturn-free-legate')
        private=self.add(s.engine,'Generic Bound Growth',seat='C',zone='hand')
        self.prepare(s,actor='A',active='B',mana={'C':1})
        self.checkpoint(s)
        self.assertFalse(any(r['id']=='cast:'+other.ref for r in self.actions(s)))
        self.prepare(s,mana={'C':1});s.state.players['B'].mana_pool.update({'U':2,'C':1});self.checkpoint(s)
        option=next(r for r in self.offer(s,source)['cost_options'] if r['id'].startswith('plain-alternative-'))
        self.assertEqual(1,option['requirements']['GENERIC'])
        self.free_cast(s,source)
        self.assertEqual(0,sum(s.state.players['A'].mana_pool.values()))
        # An actual response changes future eligibility after the price was paid.
        self.cast(s,response,[enemy.ref],seat='B',payment={'U':2,'C':1})
        s=self.replay(s,load=True)
        self.assertIsNone(self.resolve(s));self.assertEqual('battlefield',s.state.cards[source.object_id].zone)
        self.assertEqual('hand',s.state.cards[enemy.object_id].zone)
        self.assertEqual(0,sum(s.state.players['A'].mana_pool.values()))
        self.private_replay(s,private)

    def test_canonically_verified_conditional_zero_price_withdrawal_blocks_originals(self):
        from copy import deepcopy
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from scripts.update_capability_evidence import build_index,discover_tests
        from quorune.oracle_ir import compile_oracle_card
        raw=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        declarations=read_evidence_document(ROOT/'platform/capability-evidence-declarations.json')
        value=deepcopy(raw);next(c for c in value['capabilities'] if c['id']==CAPABILITY).update(status='blocked',blockers=['withdrawn conditional zero mana'])
        registry=CapabilityRegistry(value)
        index=build_index(registry_value=value,declaration_source=declarations,discovered_test_ids=set(discover_tests(ROOT).values()))
        registry.mark_evidence_verified(validate_capability_evidence_index(index,registry=registry))
        for name in NAMES:
            ir=compile_oracle_card(self.db.lookup(name),capability_registry=registry,capability_profile='commander_review')
            self.assertNotEqual('exact',ir.status,name)
            blockers=[b for r in ir.material_residuals for b in r.blockers]
            self.assertTrue(any('status:'+CAPABILITY+':blocked' in b for b in blockers),blockers)
            self.assertFalse(any('evidence_index:unverified' in b for b in blockers),blockers)

    def test_fresh_originals_kill_zero_price_producer_and_condition_verdict_omissions(self):
        method='test_fourteen_originals_have_complete_admission_and_independent_zero_price_minimum'
        with patch('quorune.compiler.conditional_free_casts.conditional_zero_mana_condition',return_value=None):
            with self.assertRaises(AssertionError):getattr(type(self)(method),method)()
        method='test_five_actual_legates_offer_both_prices_and_complete_free_creature_casts'
        with patch('quorune.rules.casting.alternative_costs.fixed_public_alternative_condition_met',return_value=False):
            with self.assertRaises(AssertionError):getattr(type(self)(method),method)()

    def test_actual_deepwood_pump_retains_source_incarnation_and_paid_cost(self):
        s=self.session(755210);source=self.add(s.engine,'Deepwood Legate',zone='hand');self.lands(s,'Deepwood Legate')
        bounce=self.add(s.engine,'Unsummon',seat='B',zone='hand')
        private=self.add(s.engine,'Generic Bound Growth',seat='C',zone='hand')
        self.prepare(s,mana={'B':2});s.state.players['B'].mana_pool['U']=1;self.checkpoint(s)
        self.free_cast(s,source);self.assertIsNone(self.resolve(s))
        row=next(r for r in self.actions(s) if r['id'].startswith('activate:'+source.ref+':'))
        result=s.act('pilot:A',{'action_id':row['id'],'pay':'manual','payment':{'B':1}});self.assertTrue(result.ok,result.summary)
        self.assertIsNone(self.resolve(s));self.assertEqual((2,2),self.stats(s,source))
        self.priority(s,'A');row=next(r for r in self.actions(s) if r['id'].startswith('activate:'+source.ref+':'))
        result=s.act('pilot:A',{'action_id':row['id'],'pay':'manual','payment':{'B':1}});self.assertTrue(result.ok,result.summary)
        self.cast(s,bounce,[source.ref],seat='B');s=self.replay(s,load=True);self.assertIsNone(self.resolve(s))
        self.assertEqual('hand',s.state.cards[source.object_id].zone)
        self.assertEqual(0,s.state.players['A'].mana_pool['B'])
        self.private_replay(s,private)

    def test_actual_ruse_and_haze_preserve_their_distinct_damage_prevention_scopes(self):
        for i,name in enumerate(("Sivvi's Ruse",'Obscuring Haze')):
            s=self.session(755220+i);source=self.add(s.engine,name,zone='hand')
            if name in LAND_PAIRS:self.lands(s,name)
            else:self.commander(s)
            own=self.add(s.engine,'Grizzly Bears')
            foreign=self.add(s.engine,'Grizzly Bears',seat='B',ref='prevention-opponent')
            strike=self.add(s.engine,'Generic Free Cast Fight',zone='hand')
            private=self.add(s.engine,'Generic Bound Growth',seat='C',zone='hand')
            self.prepare(s,mana={'G':1});self.checkpoint(s)
            self.free_cast(s,source);self.assertIsNone(self.resolve(s))
            self.cast(s,strike,[own.ref,foreign.ref],payment={'G':1})
            s=self.replay(s,load=True);self.assertIsNone(self.resolve(s))
            self.assertEqual('battlefield',s.state.cards[own.object_id].zone)
            self.assertEqual(0,s.state.cards[own.object_id].marked_damage)
            self.assertEqual('graveyard',s.state.cards[foreign.object_id].zone)
            self.private_replay(s,private)

    def test_reviewed_price_overlay_requires_same_source_target_result_and_condition(self):
        from quorune.card_programs.alternative_price_overlay import shadowed_reviewed_zero_price_keys
        from quorune.compiler.program_generation import generated_programs
        s=self.session(755230);record=self.db.lookup('Deadly Rollick')
        old=next(p for p in s.engine.semantics.programs_for_oracle(record.oracle_id) if p.event=='resolve')
        current=generated_programs(self.db,record,trust_level='trusted',capability_registry=self.registry,capability_profile='commander_review')
        self.assertEqual({old.key},shadowed_reviewed_zero_price_keys(record,current,(old,)))
        for changed in (replace(old,provenance={**old.provenance,'source_rulings_hash':'different'}),
                replace(old,effects=[{'op':'destroy','card':'$target.0'}]),
                replace(old,target_schema={**old.target_schema,'count':2}),
                replace(old,cost_schema={'alternate_costs':[{**old.cost_schema['alternate_costs'][0],'condition':{'control_commander':False}}]})):
            self.assertEqual(set(),shadowed_reviewed_zero_price_keys(record,current,(changed,)))
        self.assertEqual(set(),shadowed_reviewed_zero_price_keys(record,[p for p in current if p.event=='resolve'],(old,)))
        broadened=[replace(p,target_schema={**p.target_schema,'types_any':['creature','artifact']}) if p.event=='resolve' else p for p in current]
        self.assertEqual(set(),shadowed_reviewed_zero_price_keys(record,broadened,(old,)))

    def test_unowned_new_target_companion_stays_residual(self):
        from quorune.oracle_ir import compile_oracle_card
        ir=compile_oracle_card(self.db.lookup('Deflecting Swat'),capability_registry=self.registry,capability_profile='commander_review')
        self.assertNotEqual('exact',ir.status)
        self.assertTrue(any('choose new targets' in r.text for r in ir.material_residuals))

    def test_actual_basic_type_change_updates_zero_price_and_commander_copies_do_not_qualify(self):
        s=self.session(755240);source=self.add(s.engine,'Rushwood Legate',zone='hand')
        own=self.add(s.engine,'Forest');enemy=self.add(s.engine,'Mountain',seat='B')
        seas=self.add(s.engine,'Spreading Seas',zone='hand')
        private=self.add(s.engine,'Generic Bound Growth',seat='C',zone='hand')
        self.prepare(s,mana={'C':1,'U':1});self.checkpoint(s)
        self.assertFalse(any(r['id'].startswith('plain-alternative-') for r in self.offer(s,source)['cost_options']))
        self.cast(s,seas,[enemy.ref],payment={'C':1,'U':1});self.assertIsNone(self.resolve(s))
        self.assertIn('island',s.engine._type_parts(s.engine._effective_card_data(s.state.cards[enemy.object_id])['type_line'])[1])
        self.free_cast(s,source);self.assertIsNone(self.resolve(s));self.assertEqual('battlefield',s.state.cards[source.object_id].zone)
        self.private_replay(s,private)
        s=self.session(755241);free=self.add(s.engine,'Fierce Guardianship',zone='hand');commander=self.commander(s)
        token=s.engine.create_token('A',name='',copy_of=commander.ref,reason='free price copy designation boundary')[0]
        copied=next(c for c in s.state.cards.values() if c.ref==token)
        self.assertFalse(copied.is_commander)
        commander.phased_out=True
        condition=FixedPublicAlternativeCondition(Kind.CONTROLLED_COMMANDER,schema_version=2)
        self.assertFalse(fixed_public_alternative_condition_met(s.engine,'A',free,condition.to_dict()))
        commander.phased_out=False
        self.assertTrue(fixed_public_alternative_condition_met(s.engine,'A',free,condition.to_dict()))
