from __future__ import annotations

"""CR 702.138b-c: Escape payment, rather than graveyard origin, gates counters."""

from pathlib import Path
from dataclasses import replace
from types import SimpleNamespace
from copy import deepcopy
import json
import tempfile
import unittest
from unittest import mock

from common import ROOT
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.entry_counter_model import DynamicEntryCounterAmountSpec, DynamicEntryCounterCalculation, DynamicEntryCounterValueSource
from quorune.entry_counters import dynamic_entry_counter_amount
from quorune.compiler.fixed_self_entry_counter_templates import dynamic_self_entry_counter_handler
from quorune.cast_lifecycles import compile_fixed_cast_lifecycle
from quorune.card_programs.adapters import compile_best_available_card_program
from quorune.card_programs import bind_card_program_runtime
from quorune.oracle_ir import compile_oracle_card
from quorune.semantics import SemanticRegistry
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import CapabilityRegistry, load_default_capability_registry
from scripts.build_test_database import build_fixture_database
import test_bound_effect_programs as actions
import test_self_public_zone_casting as zone_actions
from test_granted_skulk import record


FIXTURE = ROOT/'tests/fixtures/escaped-entry-counter-cards.json'
CLOSED = ('Phoenix of Ash','Ox of Agonas','Underworld Charger','Woe Strider',
    'Underworld Rage-Hound','Voracious Typhon','Loathsome Chimera','Chainweb Aracnir')


class EscapedEntryCounterCompilerTests(unittest.TestCase):
    def test_escaped_counter_grammar_and_boundaries_are_source_spanned(self):
        registry = load_default_capability_registry()
        text = 'This creature escapes with three +1/+1 counters on it.'
        source = record(text,'Creature')
        ir = compile_oracle_card(source,capability_registry=registry)
        self.assertEqual('exact',ir.status,ir.material_residuals)
        node, = ir.faces[0].nodes
        self.assertEqual(text,source.oracle_text[node.span.start:node.span.end])
        self.assertEqual('escaped',node.handlers[0]['amount_spec']['value_source'])
        self.assertEqual('702.138c',node.handlers[0]['rule_id'])
        for excluded in (
            'This creature escapes with twelve +1/+1 counters on it instead.',
            'This creature escapes with either a +1/+1 counter or a flying counter on it.',
            'This creature escapes with three +1/+1 counters on it. When it enters this way, draw a card.',
            'Another creature escapes with a +1/+1 counter on it.',
            'This creature escapes with X +1/+1 counters on it.',
            'This creature escapes with three +1/+1 counter on it.',
        ):
            with self.subTest(excluded=excluded):
                self.assertIsNone(dynamic_self_entry_counter_handler(excluded,source_name=source.name))
        raw = json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        for dependency in ('counter.producer.dynamic_self_entry','casting.lifecycle.fixed_zone_cast'):
            changed = deepcopy(raw)
            next(row for row in changed['capabilities'] if row['id']==dependency).update(status='blocked',blockers=['escape owner omitted'])
            self.assertNotEqual('exact',compile_oracle_card(source,capability_registry=CapabilityRegistry(changed)).status)

    def test_escaped_fact_distinguishes_actual_method_origin_and_spell_copies(self):
        line = 'Escape—{5}{G}{G}, Exile four other cards from your graveyard.'
        lifecycle = compile_fixed_cast_lifecycle(material_line=line,oracle_line=line,line_index=0)
        self.assertIsNotNone(lifecycle)
        spec = DynamicEntryCounterAmountSpec(DynamicEntryCounterValueSource.ESCAPED,
            DynamicEntryCounterCalculation.FIXED_IF_AT_LEAST,coefficient=3,minimum=1)
        self.assertEqual(spec,DynamicEntryCounterAmountSpec.from_dict(spec.to_dict()))
        card = SimpleNamespace(object_id='entry',zone='stack')
        for kind,origin,method,expected in (
            ('spell','graveyard',lifecycle.to_dict(),3),
            ('spell','hand',lifecycle.to_dict(),0),
            ('spell','graveyard',None,0),
            ('spell_copy','graveyard',lifecycle.to_dict(),0),
        ):
            with self.subTest(kind=kind,origin=origin,method=method):
                context = {'cast_origin':origin}
                if method is not None:context['fixed_cast_lifecycle']=method
                item = SimpleNamespace(kind=kind,card_object_id=card.object_id,context=context)
                host = SimpleNamespace(state=SimpleNamespace(stack=[item]))
                self.assertEqual(expected,dynamic_entry_counter_amount(host,card=card,destination_controller='B',amount_spec=spec))
        host = SimpleNamespace(state=SimpleNamespace(stack=[]))
        card.zone = 'exile'
        self.assertEqual(0,dynamic_entry_counter_amount(host,card=card,destination_controller='B',amount_spec=spec))
        for calculation,minimum in ((DynamicEntryCounterCalculation.MULTIPLY,None),
                (DynamicEntryCounterCalculation.FIXED_IF_AT_LEAST,2)):
            with self.assertRaises(ValueError):
                replace(spec,calculation=calculation,minimum=minimum)
        card.zone='stack'
        item.context={'cast_origin':'graveyard','fixed_cast_lifecycle':{'kind':'escape'}}
        item.kind='spell'
        with self.assertRaises(ValueError):
            dynamic_entry_counter_amount(SimpleNamespace(state=SimpleNamespace(stack=[item])),card=card,destination_controller='B',amount_spec=spec)


class EscapedEntryCounterRuntimeTests(unittest.TestCase):
    register_fixture_cards_on_add = True
    session = actions.BoundEffectProgramRuntimeTests.session
    add = actions.BoundEffectProgramRuntimeTests.add
    checkpoint = actions.BoundEffectProgramRuntimeTests.checkpoint
    resolve = actions.BoundEffectProgramRuntimeTests.resolve
    replay = actions.BoundEffectProgramRuntimeTests.replay
    prepare = zone_actions.SelfPublicZoneRuntimeTests.prepare
    assert_closed = zone_actions.SelfPublicZoneRuntimeTests.assert_closed
    cast = zone_actions.SelfPublicZoneRuntimeTests.cast

    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        path = Path(cls.temp.name)/'escaped-entry.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',
            ROOT/'tests/fixtures/landwalk-as-though-cards.json',
            ROOT/'tests/fixtures/multikicker-entry-counter-cards.json',
            ROOT/'tests/fixtures/history-entry-counters.json',
            ROOT/'tests/fixtures/qualified-landwalk-cards.json', FIXTURE], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition('Escaped entry', [DeckEntry('Generic Bound Commander',1,'commander'),
            DeckEntry('Generic Bound Plains',30)], ['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temp.cleanup()

    def test_actual_typhon_escape_pays_costs_and_enters_with_three_counters(self):
        session = self.session(70213831)
        engine = session.engine
        source = self.add(engine, 'Voracious Typhon', zone='graveyard')
        fodder = [self.add(engine, 'Generic Bound Plains', zone='graveyard', ref=f'escape-fodder-{i}') for i in range(5)]
        self.assert_closed(session, source)
        private = self.add(engine,'Generic Bound Growth',seat='C',zone='hand',ref='escape-private-card')
        self.prepare(session, mana={'C':5,'G':2})
        self.checkpoint(session)
        action = next(row for row in session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
            if row.get('card') == source.ref)
        option = next(row for row in action['cost_options'] if row['id'] == 'escape')
        self.assertEqual(4, option['choice_schema']['exile_cards']['count'])
        self.assertNotIn(source.ref, option['choice_schema']['exile_cards']['legal_refs'])
        command = {'action_id':action['id'], 'cost_option':'escape', 'exile_cards':[c.ref for c in fodder[:4]], 'pay':'auto'}
        before = authoritative_state_hash(session.state)
        self.assertFalse(session.act('pilot:C',command).ok)
        self.assertEqual(before,authoritative_state_hash(session.state))
        for bad_refs in ([source.ref,*[c.ref for c in fodder[:3]]],
                [fodder[0].ref]*4,[c.ref for c in fodder[:3]]):
            self.assertFalse(session.act('pilot:A',{**command,'exile_cards':bad_refs}).ok)
            self.assertEqual(before,authoritative_state_hash(session.state))
        accepted = session.act('pilot:A',command)
        self.assertTrue(accepted.ok,accepted.summary)
        source = session.state.cards[source.object_id]
        fodder = [session.state.cards[card.object_id] for card in fodder]
        self.assertEqual('stack',source.zone)
        self.assertTrue(all(card.zone == 'exile' for card in fodder[:4]))
        self.assertEqual('graveyard',fodder[-1].zone)
        self.assertEqual(0,sum(session.state.players['A'].mana_pool.values()))
        session = self.replay(session,load=True)
        self.resolve(session)
        source = session.state.cards[source.object_id]
        self.assertEqual('battlefield',source.zone)
        self.assertEqual({'+1/+1':3},source.counters)
        self.assertEqual((7,7),tuple(session.engine._numeric_stat(source.object_id,stat) for stat in ('power','toughness')))
        for seat in 'ABD':
            self.assertNotIn(private.ref,json.dumps(session.packet('pilot:'+seat,full=True)))
        self.assertIn(private.ref,json.dumps(session.packet('pilot:C',full=True)))
        self.replay(session,load=True)

    def test_actual_originals_close_only_when_all_siblings_are_supported(self):
        for row in json.loads(FIXTURE.read_text(encoding='utf-8'))['cards']:
            if row['oracle_id'].startswith('fixture:'):
                continue
            with self.subTest(name=row['name']):
                card = self.db.lookup(row['name'])
                program = compile_best_available_card_program(self.db,card,semantic_registry=SemanticRegistry(),
                    capability_registry=self.registry,capability_profile='commander_review')
                bound = bind_card_program_runtime(program,capability_registry=self.registry,profile='commander_review')
                self.assertEqual(card.name in CLOSED,bound['strict_capability_ready'],bound['blockers'])

    def test_actual_normal_cast_and_blink_do_not_receive_escape_counters(self):
        session = self.session(70213832)
        source = self.add(session.engine,'Voracious Typhon',zone='hand')
        blink = self.add(session.engine,'Flicker of Fate',zone='hand')
        self.assert_closed(session,source)
        self.prepare(session,mana={'C':2,'G':2,'W':1})
        self.checkpoint(session)
        self.cast(session,'A',source)
        self.resolve(session)
        self.assertEqual({},source.counters)
        # A subsequent noncast entry is a fresh object, with no Escape payment.
        self.prepare(session,mana={'C':1,'W':1})
        self.checkpoint(session)
        self.cast(session,'A',blink,[source.ref])
        self.resolve(session)
        self.assertEqual('battlefield',source.zone)
        self.assertEqual({},source.counters)
        self.replay(session,load=True)

    def test_actual_escape_counter_order_precedes_entry_trigger_and_copy_blink_reset(self):
        session = self.session(70213833)
        engine = session.engine
        source = self.add(engine,'Woe Strider',zone='graveyard')
        self.add(engine,'Hardened Scales')
        self.add(engine,'Branching Evolution')
        fodder = [self.add(engine,'Generic Bound Plains',zone='graveyard',ref=f'woe-fodder-{i}') for i in range(4)]
        clone = self.add(engine,'Clone',zone='hand')
        blink = self.add(engine,'Flicker of Fate',zone='hand')
        self.assert_closed(session,source)
        self.prepare(session,mana={'C':3,'B':2})
        self.checkpoint(session)
        cast = next(row for row in session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions'] if row.get('card')==source.ref)
        accepted = session.act('pilot:A',{'action_id':cast['id'],'cost_option':'escape','exile_cards':[card.ref for card in fodder],'pay':'auto'})
        self.assertTrue(accepted.ok,accepted.summary)
        decision = self.resolve(session)
        self.assertEqual('replacement.order',decision.kind)
        self.assertEqual('stack',source.zone)
        self.assertEqual({},source.counters)
        session = self.replay(session,load=True)
        for seat in 'BCD':self.assertIsNone(session.packet('pilot:'+seat,full=True)['decision'])
        first = None
        while session.state.pending_decision is not None and session.state.pending_decision.kind=='replacement.order':
            option = session.packet('pilot:A',full=True)['decision']['ctx']['options'][0]['id']
            if first is None:first=option
            before = authoritative_state_hash(session.state)
            self.assertFalse(session.act('pilot:B',{'action_id':'choose','replacement':option}).ok)
            self.assertEqual(before,authoritative_state_hash(session.state))
            accepted = session.act('pilot:A',{'action_id':'choose','replacement':option})
            self.assertTrue(accepted.ok,accepted.summary)
        source = session.state.cards[source.object_id]
        self.assertEqual('battlefield',source.zone)
        self.assertEqual(6 if 'hardened' in first.casefold() else 5,source.counters.get('+1/+1'))
        # Counters have committed while the Goat-producing enter trigger waits.
        self.assertTrue(any(item.kind=='triggered_ability' for item in session.state.stack))
        self.assertFalse(any(card.is_token and card.printed_name=='Goat' for card in session.state.cards.values()))
        self.resolve(session)
        self.assertEqual(1,sum(card.is_token and card.printed_name=='Goat' and card.zone=='battlefield' for card in session.state.cards.values()))
        self.replay(session,load=True)

        self.prepare(session,mana={'C':3,'U':1})
        self.checkpoint(session)
        self.cast(session,'A',clone)
        decision = self.resolve(session)
        self.assertEqual('replacement.order',decision.kind)
        session = self.replay(session,load=True)
        option = next(row for row in session.packet('pilot:A',full=True)['decision']['ctx']['options']
            if source.ref in row['id'] and not row['id'].startswith('decline:'))
        accepted = session.act('pilot:A',{'action_id':'choose','replacement':option['id']})
        self.assertTrue(accepted.ok,accepted.summary)
        self.resolve(session)
        copied = session.state.cards[clone.object_id]
        self.assertEqual('battlefield',copied.zone)
        self.assertEqual({},copied.counters)
        self.assertEqual(3,session.engine._numeric_stat(copied.object_id,'power'))
        old_identity = source.logical_object_id
        self.prepare(session,mana={'C':1,'W':1})
        self.checkpoint(session)
        self.cast(session,'A',blink,[source.ref])
        self.resolve(session)
        source = session.state.cards[source.object_id]
        self.assertNotEqual(old_identity,source.logical_object_id)
        self.assertEqual('battlefield',source.zone)
        self.assertEqual({},source.counters)
        self.assertEqual(3,session.engine._numeric_stat(source.object_id,'power'))
        self.replay(session,load=True)

    def test_escape_provenance_omission_is_killed_by_native_witness(self):
        import quorune.semantic_runtime.self_entry_counters as owner
        original = owner.dynamic_entry_counter_amount
        def omitted(host,**kwargs):
            if kwargs['amount_spec'].value_source is DynamicEntryCounterValueSource.ESCAPED:return 0
            return original(host,**kwargs)
        with mock.patch.object(owner,'dynamic_entry_counter_amount',side_effect=omitted):
            with self.assertRaises(AssertionError):
                self.test_actual_typhon_escape_pays_costs_and_enters_with_three_counters()

    def test_actual_non_escape_graveyard_cast_offers_both_methods_without_free_counters(self):
        session = self.session(70213834)
        engine = session.engine
        source = self.add(engine,'Generic Escape Method Control',zone='graveyard')
        for i in range(4):self.add(engine,'Generic Bound Plains',zone='graveyard',ref=f'method-fodder-{i}')
        self.assert_closed(session,source)
        self.prepare(session,mana={'C':5,'G':2})
        self.checkpoint(session)
        action = next(row for row in session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions'] if row.get('card')==source.ref)
        self.assertIn('normal',[option['id'] for option in action['cost_options']])
        self.assertIn('escape',[option['id'] for option in action['cost_options']])
        result = session.act('pilot:A',{'action_id':action['id'],'cost_option':'normal','pay':'auto'})
        self.assertTrue(result.ok,result.summary)
        self.assertEqual('graveyard',session.state.stack[-1].context['cast_origin'])
        self.assertIsNone(session.state.stack[-1].context.get('fixed_cast_lifecycle'))
        self.assertEqual(4,len(session.state.players['A'].zones['graveyard']))
        self.assertEqual(0,len(session.state.players['A'].zones['exile']))
        session=self.replay(session,load=True)
        self.resolve(session)
        self.assertEqual({},session.state.cards[source.object_id].counters)
        self.replay(session,load=True)


if __name__ == '__main__':
    unittest.main()
