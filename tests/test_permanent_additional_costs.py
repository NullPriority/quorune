from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from common import ROOT, keep_all
from quorune.carddb import CardDatabase
from quorune.card_programs import bind_card_program_runtime
from quorune.card_programs.adapters import compile_best_available_card_program
from quorune.deck import DeckDefinition, DeckEntry
from quorune.model import CardInstance, GameConfig
from quorune.oracle_ir import compile_oracle_card, register_generated_programs
from quorune.record import authoritative_state_hash, checkpoint_envelope, replay_record
from quorune.rules.capabilities import load_default_capability_registry
from quorune.semantics import SemanticRegistry
from quorune.session import CommanderSession
from scripts.build_test_database import build_fixture_database


class PermanentAdditionalCostCompilerTests(unittest.TestCase):
    def test_cost_only_carrier_has_no_resolution_effect_and_binds_existing_cost_owner(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'prices.sqlite3'
            build_fixture_database([ROOT / 'tests/fixtures/permanent-additional-costs.json'], path)
            with CardDatabase(path) as db:
                registry = load_default_capability_registry()
                for name in ('Makeshift Mauler', 'Lesser Masticore', 'Goremand', 'Fear of Isolation', 'Bayou Groff'):
                    record = db.lookup(name)
                    program = compile_best_available_card_program(db, record, semantic_registry=SemanticRegistry(),
                        capability_registry=registry, capability_profile='commander_review')
                    binding = bind_card_program_runtime(program, capability_registry=registry, profile='commander_review')
                    with self.subTest(name=name):
                        self.assertTrue(binding['strict_capability_ready'], binding['blockers'])
                        price = next(p for p in program.abilities if p.ability_id == 'spell:front')
                        self.assertEqual([], price.effects)
                        self.assertEqual('battlefield', price.destination)
                        self.assertTrue(price.cost_schema['additional_costs'])
                        self.assertTrue(price.provenance.get('card_program_admission'))

    def test_cost_carrier_runtime_shape_rejects_added_effects_missing_price_and_wrong_destination(self):
        from quorune.compiler.permanent_additional_cost_nodes import is_closed_permanent_additional_cost_program
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'prices.sqlite3'
            build_fixture_database([ROOT/'tests/fixtures/permanent-additional-costs.json'],path)
            with CardDatabase(path) as db:
                registry=load_default_capability_registry()
                program=compile_best_available_card_program(db,db.lookup('Makeshift Mauler'),semantic_registry=SemanticRegistry(),
                    capability_registry=registry,capability_profile='commander_review')
                price=next(p for p in program.abilities if p.ability_id=='spell:front')
                self.assertTrue(is_closed_permanent_additional_cost_program(price))
                for mutant in (replace(price,cost_schema=None), replace(price,destination='graveyard'),
                    replace(price,effects=[{'op':'draw','player':'$controller','count':1}]),
                    replace(price,cost_schema={'additional_costs':[]})):
                    self.assertFalse(is_closed_permanent_additional_cost_program(mutant))

    def test_price_capability_omission_keeps_whole_permanent_untrusted(self):
        from quorune.rules.capabilities import CapabilityRegistry
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'prices.sqlite3'
            build_fixture_database([ROOT/'tests/fixtures/permanent-additional-costs.json'],path)
            with CardDatabase(path) as db:
                raw=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
                for name,capability in (('Makeshift Mauler','casting.additional_cost.zone_change.fixed_exile'),
                    ('Goremand','casting.additional_cost.fixed_sacrifice'),('Bayou Groff','casting.additional_cost.fixed_alternative')):
                    value=deepcopy(raw);row=next(r for r in value['capabilities'] if r['id']==capability)
                    row.update(status='blocked',blockers=['Independent casting-price omission'])
                    ir=compile_oracle_card(db.lookup(name),capability_registry=CapabilityRegistry(value),capability_profile='commander_review')
                    self.assertNotEqual('exact',ir.status)

    def test_supported_price_does_not_admit_an_independently_partial_whole_card(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'prices.sqlite3'
            build_fixture_database([ROOT/'tests/fixtures/permanent-additional-costs.json'],path)
            with CardDatabase(path) as db:
                registry=load_default_capability_registry();record=db.lookup('Makeshift Mauler')
                record=replace(record,oracle_text=record.oracle_text+'\nWhenever a player sneezes, draw a card.')
                program=compile_best_available_card_program(db,record,semantic_registry=SemanticRegistry(),
                    capability_registry=registry,capability_profile='commander_review')
                self.assertTrue(program.residuals)
                price=next(p for p in program.abilities if p.ability_id=='spell:front')
                self.assertEqual('partial',price.provenance['card_program_admission']['oracle_ir_status'])
                self.assertFalse(bind_card_program_runtime(program,capability_registry=registry,profile='commander_review')['strict_capability_ready'])

    def test_registered_price_probe_measures_only_new_strict_whole_permanents(self):
        from scripts.work_selection_cohort_measurements import _measurement
        from quorune.compiler.unlock_frontier import analyze_card_unlocks
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'prices.sqlite3'
            build_fixture_database([ROOT/'tests/fixtures/permanent-additional-costs.json'],path)
            with CardDatabase(path) as db:
                registry=load_default_capability_registry();original=db.lookup('Makeshift Mauler')
                records=(original,replace(original,oracle_id='fixture:price-independent-residual',
                    oracle_text=original.oracle_text+'\nWhenever a player sneezes, draw a card.'))
                with mock.patch('quorune.compiler.closed_static_nodes.permanent_additional_cost_node',return_value=None):
                    baseline=[analyze_card_unlocks(compile_oracle_card(r,capability_registry=registry,capability_profile='commander_review'),
                        program=None,program_error=None,capabilities=registry,profile='commander_review') for r in records]
                measured=_measurement(frontier={'cards':baseline},bundle={
                    'bundle_id':'bundle:permanent-spell-additional-price','measurement_probe_id':'permanent-spell-additional-price-existing-owner-v1'},
                    cards_by_oracle_id={r.oracle_id:r for r in records},coverage={
                        'minimum_complete_card_gain':50,'minimum_exact_ability_gain':100,'minimum_material_residual_reduction':100},
                    cohort_fingerprint='original-price-regression',database=db)
                self.assertEqual(1,measured['complete_card_gain'])
                self.assertEqual(2,measured['exact_ability_gain'])
                self.assertFalse(measured['grants_gameplay_trust'])
                self.assertEqual('retired_below_harvest_floor',measured['decision'])

    def test_independent_partial_siblings_and_multiple_price_clauses_stay_residual(self):
        from test_fixed_optional_mana_payment_triggers import payment_record
        registry = load_default_capability_registry()
        for text in (
            "As an additional cost to cast this spell, sacrifice a creature.\nWhenever a player sneezes, draw a card.",
            "As an additional cost to cast this spell, sacrifice a creature.\nAs an additional cost to cast this spell, discard a card.",
            "As an additional cost to cast this spell, reveal a card.",
        ):
            ir = compile_oracle_card(payment_record(text), capability_registry=registry, capability_profile='commander_review')
            self.assertNotEqual('exact', ir.status)


class PermanentAdditionalCostActionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        path = Path(cls.temporary.name) / 'prices.sqlite3'
        build_fixture_database([ROOT / 'tests/fixtures/fixed-resolution-payment-cards.json', ROOT / 'tests/fixtures/permanent-additional-costs.json'], path)
        cls.db = CardDatabase(path); cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition('Permanent price deck', [DeckEntry('Generic Payment Commander', 1, 'commander'), DeckEntry('Generic Payment Plains', 30)], ['Generic Payment Commander'])

    @classmethod
    def tearDownClass(cls):
        cls.db.close(); cls.temporary.cleanup()

    def session(self, seed):
        session = CommanderSession.create(self.db, {s: deepcopy(self.deck) for s in 'ABCD'}, first_player='A', seed=seed,
            config=GameConfig(seed=seed, auto_pass_empty_priority=False))
        keep_all(session); e = session.engine
        e.permissions.invalidate_current(); e.state.pending_decision = None
        e.state.priority_player = None; e.state.priority_passes = []
        records = tuple(r for r in self.db.iter_cards() if r.name in {
            'Makeshift Mauler','Fear of Isolation','Goremand','Lesser Masticore','Bayou Groff','Mardu Outrider','Dauthi Voidwalker',
            'Generic Payment Commander','Generic Payment Plains'})
        register_generated_programs(self.db,e.semantics,records,trust_level='trusted',capability_registry=self.registry,
            capability_profile='commander_review',promote_exact_runtime_handlers=True,promote_exact_effect_programs=True,
            promote_exact_trigger_programs=True,promote_exact_capability_declarations=True)
        return session

    def add(self, e, name, ref, *, zone='battlefield', owner='A', controller=None):
        r = self.db.lookup(name); controller = controller or owner
        card = CardInstance(object_id='permanent-price:'+ref,ref=ref,oracle_id=r.oracle_id,printed_name=r.name,
            owner=owner,controller=controller,zone=zone,zone_timestamp=e._next_zone_timestamp(),
            known_to=[owner] if zone in {'hand','library'} else list(e.seats),revealed_to=[] if zone in {'hand','library'} else list(e.seats))
        e.state.cards[card.object_id]=card;e.state.players[controller if zone=='battlefield' else owner].zones[zone].append(card.object_id)
        return card

    def ready(self, s, card, mana):
        e=s.engine;e.state.started=True;e.state.active_player='A';e.state.phase='precombat_main';e.state.step='main'
        e.state.players['A'].mana_pool.update(mana);e.permissions.invalidate_current();e.state.pending_decision=None
        e._grant_priority('A');e.pump()
        return next(a for a in s.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions'] if a['id']=='cast:'+card.ref)

    def checkpoint(self,s):
        s.initial_checkpoint=checkpoint_envelope(s.state);s.commands.clear();s.decisions.clear()

    def resolve(self,s):
        for _ in range(24):
            if not s.state.stack:return
            if s.state.pending_decision and s.state.pending_decision.kind!='priority':return
            r=s.act(s.pending_principals()[0],{'action_id':'pass'});self.assertTrue(r.ok,r.summary)
        self.fail('Permanent price resolution did not finish')

    def replay(self,s):
        expected=authoritative_state_hash(s.state)
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'record';s.save(path);r=replay_record(path,self.db,verify=True)
        self.assertTrue(r['ok']);self.assertEqual(expected,r['final_state_hash'])

    def test_real_mauler_requires_own_creature_exile_price_and_resolves_as_permanent(self):
        s=self.session(1180801);e=s.engine
        spell=self.add(e,'Makeshift Mauler','MAULER',zone='hand')
        own=self.add(e,'Generic Payment Commander','OWN',zone='graveyard')
        other=self.add(e,'Generic Payment Commander','OTHER',zone='graveyard',owner='B')
        land=self.add(e,'Generic Payment Plains','LAND',zone='graveyard')
        action=self.ready(s,spell,{'U':1,'C':3});self.checkpoint(s)
        before=authoritative_state_hash(s.state)
        for refs in ([],[other.ref],[land.ref],[own.ref,land.ref]):
            r=s.act('pilot:A',{'action_id':action['id'],'pay':'auto','exile_cards':refs});self.assertFalse(r.ok)
            self.assertEqual(before,authoritative_state_hash(s.state))
        r=s.act('pilot:A',{'action_id':action['id'],'pay':'auto','exile_cards':[own.ref]});self.assertTrue(r.ok,r.summary)
        self.assertEqual('exile',s.state.cards[own.object_id].zone);self.assertEqual('stack',s.state.cards[spell.object_id].zone)
        self.resolve(s);self.assertEqual('battlefield',s.state.cards[spell.object_id].zone)
        self.assertEqual('graveyard',s.state.cards[other.object_id].zone);self.replay(s)

    def test_real_discard_and_owner_hand_return_prices_are_private_authoritative_and_replay(self):
        for seed,name,field,zone,mana in (
            (1180802,'Lesser Masticore','discard_cards','hand',{'C':2}),
            (1180803,'Fear of Isolation','return_cards','battlefield',{'U':1,'C':1}),
        ):
            with self.subTest(name=name):
                s=self.session(seed);e=s.engine;spell=self.add(e,name,'SPELL',zone='hand')
                price=self.add(e,'Generic Payment Commander','PRICE',zone=zone,owner='B' if zone=='battlefield' else 'A',controller='A')
                opponent=self.add(e,'Generic Payment Commander','OPPONENT',zone=zone,owner='C')
                action=self.ready(s,spell,mana);self.checkpoint(s);before=authoritative_state_hash(s.state)
                if zone=='hand':
                    for seat in 'BCD':self.assertNotIn(price.ref,str(s.packet('pilot:'+seat,full=True)))
                wrong=s.act('pilot:A',{'action_id':action['id'],'pay':'auto',field:[opponent.ref]})
                self.assertFalse(wrong.ok);self.assertEqual(before,authoritative_state_hash(s.state))
                accepted=s.act('pilot:A',{'action_id':action['id'],'pay':'auto',field:[price.ref]})
                self.assertTrue(accepted.ok,accepted.summary)
                self.assertEqual('graveyard' if zone=='hand' else 'hand',s.state.cards[price.object_id].zone)
                if zone=='battlefield':self.assertIn(price.object_id,s.state.players['B'].zones['hand'])
                self.resolve(s)
                if s.state.pending_decision and s.state.pending_decision.kind=='semantic.target':
                    # Fear's independent entry ability is optional; zero targets
                    # proves price payment does not depend on a result target.
                    accepted=s.act('pilot:A',{'action_id':'choose','targets':[]})
                    self.assertTrue(accepted.ok,accepted.summary);self.resolve(s)
                self.assertEqual('battlefield',s.state.cards[spell.object_id].zone);self.replay(s)

    def test_real_alternative_mana_or_sacrifice_price_is_paid_once_and_replays(self):
        for seed,branch in ((1180804,'mana'),(1180805,'sacrifice')):
            with self.subTest(branch=branch):
                s=self.session(seed);e=s.engine;spell=self.add(e,'Bayou Groff','GROFF',zone='hand')
                fodder=self.add(e,'Generic Payment Commander','FODDER')
                action=self.ready(s,spell,{'G':1,'C':4})
                options=action['cost_options']
                self.assertEqual(2,len(options))
                # Select by the owned public option kind, not fixture card identity.
                selected=next(o for o in options if (bool(o.get('choice_schema')) if branch=='sacrifice' else not bool(o.get('choice_schema'))))
                self.checkpoint(s)
                accepted=s.act('pilot:A',{'action_id':action['id'],'pay':'auto','cost_option':selected['id'],
                    **({'sacrifice_cards':[fodder.ref]} if branch=='sacrifice' else {})})
                self.assertTrue(accepted.ok,accepted.summary)
                self.resolve(s);self.assertEqual('battlefield',s.state.cards[spell.object_id].zone)
                self.assertEqual('graveyard' if branch=='sacrifice' else 'battlefield',s.state.cards[fodder.object_id].zone)
                self.assertEqual(3 if branch=='sacrifice' else 0,s.state.players['A'].mana_pool['C']);self.replay(s)

    def test_cast_price_omission_mutant_is_killed_by_actual_rejection(self):
        from quorune.rules.casting import costs as owner
        original=owner._cast_schema_and_mechanics
        def omit(host,seat,card,program,**kwargs):
            if program is not None and program.provenance.get('template_id')=='fixed-permanent-additional-cost-v1':
                program=replace(program,cost_schema=None)
            return original(host,seat,card,program,**kwargs)
        with mock.patch.object(owner,'_cast_schema_and_mechanics',omit):
            with self.assertRaises(AssertionError):
                self.test_real_mauler_requires_own_creature_exile_price_and_resolves_as_permanent()

    def test_permanent_sacrifice_price_replacement_is_seat_locked_pending_and_replays(self):
        s=self.session(1180806);e=s.engine;spell=self.add(e,'Bayou Groff','GROFF',zone='hand')
        fodder=self.add(e,'Generic Payment Commander','FODDER')
        self.add(e,'Dauthi Voidwalker','FIRST',owner='B')
        self.add(e,'Dauthi Voidwalker','SECOND',owner='C')
        action=self.ready(s,spell,{'G':1,'C':1})
        selected=next(o for o in action['cost_options'] if o.get('choice_schema'))
        self.checkpoint(s)
        accepted=s.act('pilot:A',{'action_id':action['id'],'pay':'auto','cost_option':selected['id'],'sacrifice_cards':[fodder.ref]})
        self.assertTrue(accepted.ok,accepted.summary)
        self.assertEqual('replacement.order',s.state.pending_decision.kind)
        before=authoritative_state_hash(s.state)
        wrong=s.act('pilot:B',{'action_id':'choose','replacement_effect_id':'wrong'})
        self.assertFalse(wrong.ok);self.assertEqual(before,authoritative_state_hash(s.state))
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'pending';s.save(path);s=CommanderSession.load(self.db,path)
        projected=s.packet('pilot:A',full=True)['decision']['ctx']
        options=projected['options']
        selected=options[0]['id']
        accepted=s.act('pilot:A',{'action_id':'choose','replacement':selected})
        self.assertTrue(accepted.ok,accepted.summary);self.resolve(s)
        self.assertEqual('exile',s.state.cards[fodder.object_id].zone)
        self.assertEqual('battlefield',s.state.cards[spell.object_id].zone)
        self.assertEqual(0,s.state.players['A'].mana_pool['C']);self.replay(s)

    def test_unpayable_permanent_price_withholds_cast_offer_and_rejects_command(self):
        s=self.session(1180807);e=s.engine;spell=self.add(e,'Makeshift Mauler','MAULER',zone='hand')
        with self.assertRaises(StopIteration):self.ready(s,spell,{'U':1,'C':3})
        before=authoritative_state_hash(s.state)
        rejected=s.act('pilot:A',{'action_id':'cast:'+spell.ref,'pay':'auto'})
        self.assertFalse(rejected.ok);self.assertEqual(before,authoritative_state_hash(s.state))
