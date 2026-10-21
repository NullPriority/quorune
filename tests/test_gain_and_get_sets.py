from __future__ import annotations

"""CR 611.2c: resolution determines and locks both halves of the grant."""

from pathlib import Path
from copy import deepcopy
from dataclasses import replace
import json
import tempfile
import unittest
from unittest.mock import patch
from common import ROOT
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition,DeckEntry
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
import test_cost_defined_x as controls


class GainAndGetSetTests(unittest.TestCase):
    register_fixture_cards_on_add=True
    session=controls.CostDefinedXTests.session
    add=controls.CostDefinedXTests.add
    prepare=controls.CostDefinedXTests.prepare
    checkpoint=controls.CostDefinedXTests.checkpoint
    resolve=controls.CostDefinedXTests.resolve
    replay=controls.CostDefinedXTests.replay
    binding=controls.CostDefinedXTests.binding
    priority=controls.CostDefinedXTests.priority
    cast=controls.CostDefinedXTests.cast
    actions=controls.CostDefinedXTests.actions
    private_replay=controls.CostDefinedXTests.private_replay
    top=controls.CostDefinedXTests.top
    advance=controls.CostDefinedXTests.advance
    attack=controls.combat_controls.AttachedProtectionExceptionTests.attack

    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory();path=Path(cls.temp.name)/'gain-and-get.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',ROOT/'tests/fixtures/attached-protection-cards.json',
            ROOT/'tests/fixtures/gain-and-get-cards.json',ROOT/'tests/fixtures/announced-x-additional-cost-cards.json',
            ROOT/'tests/fixtures/announced-x-activation-cards.json',ROOT/'tests/fixtures/activated-self-transform-cards.json',
            ROOT/'tests/fixtures/additional-conditional-modifier-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Gain and get',[DeckEntry('Generic Bound Commander',1,'commander'),DeckEntry('Generic Bound Plains',30)],['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls):cls.db.close();cls.temp.cleanup()

    def test_actual_craterhoof_enters_and_grants_one_counted_bonus_with_trample(self):
        session=self.session(750900);source=self.add(session.engine,'Craterhoof Behemoth',zone='hand')
        bodies=[self.add(session.engine,'Grizzly Bears',ref='hoof-body-'+str(i)) for i in range(2)]
        foreign=self.add(session.engine,'Grizzly Bears',seat='B',ref='foreign-hoof-body')
        private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand')
        self.assertTrue(self.binding(session,source)['strict_capability_ready'],'Complete original Craterhoof Behemoth must admit')
        self.prepare(session,mana={'G':3,'C':5});self.checkpoint(session)
        self.cast(session,'A',source,payment={'G':3,'C':5})
        session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
        self.assertEqual((8,8),(session.engine._numeric_stat(source.object_id,'power'),session.engine._numeric_stat(source.object_id,'toughness')))
        self.assertTrue({'haste','trample'}<=set(session.engine._combat_keywords(session.state.cards[source.object_id])))
        for body in bodies:
            self.assertEqual((5,5),(session.engine._numeric_stat(body.object_id,'power'),session.engine._numeric_stat(body.object_id,'toughness')))
            self.assertIn('trample',session.engine._combat_keywords(session.state.cards[body.object_id]))
        self.assertEqual((2,2),(session.engine._numeric_stat(foreign.object_id,'power'),session.engine._numeric_stat(foreign.object_id,'toughness')))
        self.assertNotIn('trample',session.engine._combat_keywords(session.state.cards[foreign.object_id]))
        self.private_replay(session,private)

    def test_actual_departed_craterhoof_keeps_trigger_controller_and_counts_at_resolution(self):
        session=self.session(750904);source=self.add(session.engine,'Craterhoof Behemoth',zone='hand')
        bodies=[self.add(session.engine,'Grizzly Bears',ref='departed-hoof-body-'+str(i)) for i in range(2)]
        foreign=self.add(session.engine,'Grizzly Bears',seat='B',ref='departed-hoof-foreign')
        bounce=self.add(session.engine,'Boomerang',seat='B',zone='hand')
        private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand')
        session.state.players['B'].mana_pool.update({'U':2})
        self.assertTrue(self.binding(session,source)['strict_capability_ready'])
        self.prepare(session,mana={'G':3,'C':5});self.checkpoint(session)
        self.cast(session,'A',source,payment={'G':3,'C':5})
        self.advance(session,lambda s:s.state.cards[source.object_id].zone=='battlefield' and len(s.state.stack)==1)
        self.assertEqual('A',session.state.stack[-1].controller)
        self.cast(session,'B',bounce,[source.ref],{'U':2});self.top(session)
        self.assertEqual('hand',session.state.cards[source.object_id].zone)
        session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
        for body in bodies:
            self.assertEqual((4,4),(session.engine._numeric_stat(body.object_id,'power'),session.engine._numeric_stat(body.object_id,'toughness')))
            self.assertIn('trample',session.engine._combat_keywords(session.state.cards[body.object_id]))
        self.assertEqual(2,session.engine._numeric_stat(foreign.object_id,'power'))
        self.assertNotIn('trample',session.engine._combat_keywords(session.state.cards[foreign.object_id]))
        self.private_replay(session,private)

    def test_actual_moonshaker_locks_recipients_through_control_blink_later_entry_and_cleanup(self):
        session=self.session(750901);source=self.add(session.engine,'Moonshaker Cavalry',zone='hand')
        bodies=[self.add(session.engine,'Grizzly Bears',ref='moon-body-'+str(i)) for i in range(2)]
        later=self.add(session.engine,'Grizzly Bears',zone='hand',ref='later-moon-body')
        blink=self.add(session.engine,'Cloudshift',zone='hand')
        theft=self.add(session.engine,'Word of Seizing',seat='B',zone='hand')
        foreign=self.add(session.engine,'Grizzly Bears',seat='D',ref='foreign-moon-body')
        private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand')
        self.assertTrue(self.binding(session,source)['strict_capability_ready'])
        for card in (blink,theft):self.assertTrue(self.binding(session,card)['strict_capability_ready'])
        session.state.players['B'].mana_pool.update({'R':2,'C':3})
        self.prepare(session,mana={'W':4,'G':1,'C':6});self.checkpoint(session)
        self.cast(session,'A',source,payment={'W':3,'C':5})
        session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
        self.assertEqual((9,9),(session.engine._numeric_stat(source.object_id,'power'),session.engine._numeric_stat(source.object_id,'toughness')))
        for body in bodies:
            self.assertEqual((5,5),(session.engine._numeric_stat(body.object_id,'power'),session.engine._numeric_stat(body.object_id,'toughness')))
            self.assertIn('flying',session.engine._combat_keywords(session.state.cards[body.object_id]))
        self.cast(session,'A',later,payment={'G':1,'C':1});self.assertIsNone(self.resolve(session))
        self.assertEqual(2,session.engine._numeric_stat(later.object_id,'power'))
        self.assertNotIn('flying',session.engine._combat_keywords(session.state.cards[later.object_id]))
        incarnation=session.state.cards[bodies[1].object_id].logical_object_id
        self.cast(session,'A',blink,[bodies[1].ref],{'W':1});self.assertIsNone(self.resolve(session))
        self.assertNotEqual(incarnation,session.state.cards[bodies[1].object_id].logical_object_id)
        self.assertEqual(2,session.engine._numeric_stat(bodies[1].object_id,'power'))
        self.assertNotIn('flying',session.engine._combat_keywords(session.state.cards[bodies[1].object_id]))
        self.cast(session,'B',theft,[bodies[0].ref],{'R':2,'C':3});self.assertIsNone(self.resolve(session))
        self.assertEqual('B',session.state.cards[bodies[0].object_id].controller)
        self.assertEqual(5,session.engine._numeric_stat(bodies[0].object_id,'power'))
        self.assertIn('flying',session.engine._combat_keywords(session.state.cards[bodies[0].object_id]))
        self.assertEqual(9,session.engine._numeric_stat(source.object_id,'power'))
        self.assertEqual(2,session.engine._numeric_stat(foreign.object_id,'power'))
        self.assertNotIn('flying',session.engine._combat_keywords(session.state.cards[foreign.object_id]))
        session=self.replay(session,load=True)
        self.advance(session,lambda s:s.state.active_player=='B' and s.state.phase=='precombat_main')
        self.assertEqual((6,6),(session.engine._numeric_stat(source.object_id,'power'),session.engine._numeric_stat(source.object_id,'toughness')))
        self.assertIn('flying',session.engine._combat_keywords(session.state.cards[source.object_id]))
        self.assertEqual('A',session.state.cards[bodies[0].object_id].controller)
        for body in (*bodies,later):
            self.assertEqual((2,2),(session.engine._numeric_stat(body.object_id,'power'),session.engine._numeric_stat(body.object_id,'toughness')))
            self.assertNotIn('flying',session.engine._combat_keywords(session.state.cards[body.object_id]))
        self.private_replay(session,private)

    def test_actual_throne_granted_activation_uses_departed_creatures_last_known_power(self):
        session=self.session(750905);throne=self.add(session.engine,'Dragon Throne of Tarkir',zone='hand')
        body=self.add(session.engine,'Grizzly Bears',ref='lki-throne-activator');body.acquired_control_turn_count=-1
        other=self.add(session.engine,'Grizzly Bears',ref='lki-throne-other')
        growth=self.add(session.engine,'Generic Bound Growth',zone='hand')
        bounce=self.add(session.engine,'Boomerang',seat='B',zone='hand')
        private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand',ref='lki-throne-private')
        self.assertTrue(self.binding(session,throne)['strict_capability_ready'])
        session.state.players['B'].mana_pool.update({'U':2})
        self.prepare(session,mana={'C':9,'G':1});self.checkpoint(session);self.equip_throne(session,throne,body)
        self.cast(session,'A',growth,[body.ref],{'G':1});self.assertIsNone(self.resolve(session))
        self.assertEqual(4,session.engine._numeric_stat(body.object_id,'power'))
        command=self.throne_activation(session,body);result=session.act('pilot:A',command);self.assertTrue(result.ok,result.summary)
        self.assertTrue(session.state.cards[body.object_id].tapped)
        self.cast(session,'B',bounce,[body.ref],{'U':2});self.top(session)
        self.assertEqual('hand',session.state.cards[body.object_id].zone)
        self.assertIsNone(session.state.cards[throne.object_id].attached_to)
        session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
        self.assertEqual((6,6),(session.engine._numeric_stat(other.object_id,'power'),session.engine._numeric_stat(other.object_id,'toughness')))
        self.assertIn('trample',session.engine._combat_keywords(session.state.cards[other.object_id]))
        self.private_replay(session,private)

    def test_keyword_first_matches_stat_first_without_losing_source_spans_or_boundaries(self):
        from test_bound_effect_programs import record
        from quorune.compiler.fixed_public_characteristic_sets import fixed_public_characteristic_set_effect_template as lower
        from quorune.oracle_ir import compile_oracle_card
        for subject in ('Creatures you control','Other creatures you control','Attacking creatures','Creatures target player controls'):
            first=subject+' gain flying and trample and get +2/-1 until end of turn.'
            second=subject+' get +2/-1 and gain flying and trample until end of turn.'
            self.assertIsNotNone(lower(first),first);self.assertEqual(lower(second),lower(first))
            ir=compile_oracle_card(record(first,'Sorcery'),capability_registry=self.registry,capability_profile='commander_review')
            self.assertEqual('exact',ir.status,ir.material_residuals)
            for node in ir.faces[0].nodes:self.assertEqual(node.text,first[node.span.start:node.span.end])
        for body in ('Creatures you control gain NotAKeyword and get +2/+2 until end of turn.',
                'Creatures you control gain protection from blue and get +2/+2 until end of turn.',
                'Creatures you control gain "{T}: Draw a card" and get +2/+2 until end of turn.',
                'Creatures you control gain trample and get +2/+2 until your next turn.',
                'Creatures you control gain trample and get +2/+2 until end of turn and draw a card.',
                'Creatures you control gain trample and get +X/+X until end of turn, where X is an unowned quantity.'):
            self.assertIsNone(lower(body),body)
            self.assertNotEqual('exact',compile_oracle_card(record(body,'Sorcery'),capability_registry=self.registry,capability_profile='commander_review').status,body)

    def test_verified_owner_withdrawals_reject_combined_group_results(self):
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from scripts.update_capability_evidence import build_index,discover_tests
        from quorune.oracle_ir import compile_oracle_card
        raw=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        declarations=json.loads((ROOT/'platform/capability-evidence-declarations.json').read_text(encoding='utf-8'))
        known=set(discover_tests(ROOT).values())
        def verified(value):
            registry=CapabilityRegistry(value);index=build_index(registry_value=value,declaration_source=declarations,discovered_test_ids=known)
            registry.mark_evidence_verified(validate_capability_evidence_index(index,registry=registry));return registry
        for name,dependency in (('Craterhoof Behemoth','continuous.resolution.fixed_characteristics_until_end_of_turn'),
                ('Moonshaker Cavalry','combat.block.flying'),('Craterhoof Behemoth','quantity_expression.public_query_effect_amount'),
                ('Dragon Throne of Tarkir','quantity_expression.scalar_effect_amount')):
            self.assertEqual('exact',compile_oracle_card(self.db.lookup(name),capability_registry=verified(raw),capability_profile='commander_review').status)
            changed=deepcopy(raw);next(r for r in changed['capabilities'] if r['id']==dependency).update(status='blocked',blockers=['withdrawn combined result owner'])
            blocked=verified(changed)
            closure=blocked.closure((dependency,),profile='commander_review')
            self.assertFalse(closure.trusted)
            self.assertTrue(any('status:'+dependency+':blocked' in b for b in closure.blockers))
            ir=compile_oracle_card(self.db.lookup(name),capability_registry=blocked,capability_profile='commander_review')
            self.assertNotEqual('exact',ir.status);blockers=[b for r in ir.material_residuals for b in r.blockers]
            if name=='Dragon Throne of Tarkir':
                # Its rejected quoted grant makes the complete attachment line residual.
                self.assertIn('continuous-effect layers and dependencies',blockers)
            else:self.assertTrue(any('status:'+dependency+':blocked' in b for b in blockers),(name,dependency,blockers))
            self.assertFalse(any('evidence_index:unverified' in b for b in blockers))

    def test_combined_runtime_requires_result_quantity_and_keyword_owners_and_closed_shapes(self):
        from quorune.card_programs.binding import bind_semantic_program_runtime
        from quorune.compiler.program_generation import _is_closed_effect_program
        for name in ('Craterhoof Behemoth','Dragon Throne of Tarkir'):
            session=self.session(750906);source=self.add(session.engine,name)
            programs=[p for p in session.engine.semantics.programs_for_oracle(source.oracle_id)
                if any(e.get('op')=='modify_all_matching_permanents_until_end_of_turn' for e in p.effects)]
            self.assertEqual(1,len(programs));program=programs[0]
            self.assertTrue(_is_closed_effect_program(program))
            self.assertTrue(bind_semantic_program_runtime(program,capability_registry=self.registry,profile='commander_review')['strict'])
            owners={'continuous.resolution.fixed_characteristics_until_end_of_turn','combat.damage.assignment.trample',
                'quantity_expression.declared_effect_amount',
                'quantity_expression.scalar_effect_amount' if name.startswith('Dragon') else 'quantity_expression.public_query_effect_amount'}
            for owner in sorted(owners):
                self.assertIn(owner,program.capability_dependencies)
                deps=tuple(c for c in program.capability_dependencies if c!=owner)
                stripped=replace(program,capability_dependencies=deps,capability_closure=self.registry.closure(deps,profile='commander_review').to_dict() if deps else None)
                bound=bind_semantic_program_runtime(stripped,capability_registry=self.registry,profile='commander_review')
                self.assertFalse(bound['strict'],(name,owner,bound['blockers']))
                self.assertIn('capability:undeclared_runtime_dependency:'+owner,bound['blockers'])
            for effect in ({**program.effects[0],'extra':True},
                    {**program.effects[0],'keywords':['Protection']},
                    {**program.effects[0],'power':{**program.effects[0]['power'],'coefficient':True}}):
                self.assertFalse(_is_closed_effect_program(replace(program,effects=[effect])))
                bound=bind_semantic_program_runtime(replace(program,effects=[effect]),capability_registry=self.registry,profile='commander_review')
                self.assertFalse(bound['strict']);self.assertTrue(any(b.startswith('runtime_resolution_characteristics:invalid:') for b in bound['blockers']))

    def test_fresh_native_combined_results_kill_normalizer_promotion_and_commit_omissions(self):
        cases=(('quorune.compiler.fixed_public_characteristic_sets._keyword_before_stat_grant',lambda text:text,
                'test_actual_craterhoof_enters_and_grants_one_counted_bonus_with_trample'),
            ('quorune.compiler.program_generation._is_closed_public_query_amount_program',lambda *a:False,
                'test_actual_throne_equips_and_granted_tap_uses_current_power_excluding_source'),
            ('quorune.effect_runtime.zones_and_attachments.create_resolution_continuous_effect',lambda *a,**k:None,
                'test_actual_craterhoof_enters_and_grants_one_counted_bonus_with_trample'),
            ('quorune.card_programs.runtime_requirements.fixed_resolution_characteristics_runtime_dependencies',lambda *a:(),
                'test_combined_runtime_requires_result_quantity_and_keyword_owners_and_closed_shapes'))
        for owner,mutation,method in cases:
            with patch(owner,mutation):
                with self.assertRaises(AssertionError):getattr(type(self)(method),method)()

    def test_actual_ibex_attack_reads_current_greatest_own_power_once_before_group_bonus(self):
        session=self.session(750902);source=self.add(session.engine,'Pathbreaker Ibex')
        source.acquired_control_turn_count=-1
        body=self.add(session.engine,'Grizzly Bears')
        foreign=self.add(session.engine,'Moonshaker Cavalry',seat='C')
        growth=self.add(session.engine,'Generic Bound Growth',zone='hand')
        self.add(session.engine,'Forest')
        private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand',ref='ibex-private-hand')
        self.assertTrue(self.binding(session,source)['strict_capability_ready'])
        self.prepare(session);self.checkpoint(session);self.attack(session,{source.ref:'B'})
        self.assertEqual(1,len(session.state.stack))
        self.cast(session,'A',growth,[body.ref]);self.top(session)
        self.assertEqual(1,len(session.state.stack));self.assertEqual(4,session.engine._numeric_stat(body.object_id,'power'))
        session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
        self.assertEqual((7,7),(session.engine._numeric_stat(source.object_id,'power'),session.engine._numeric_stat(source.object_id,'toughness')))
        self.assertEqual((8,8),(session.engine._numeric_stat(body.object_id,'power'),session.engine._numeric_stat(body.object_id,'toughness')))
        self.assertIn('trample',session.engine._combat_keywords(session.state.cards[source.object_id]))
        self.assertEqual(6,session.engine._numeric_stat(foreign.object_id,'power'))
        self.assertNotIn('trample',session.engine._combat_keywords(session.state.cards[foreign.object_id]))
        self.advance(session,lambda s:s.state.phase=='postcombat_main')
        self.assertEqual(33,session.state.players['B'].life)
        self.private_replay(session,private)

    def equip_throne(self,session,throne,body):
        self.cast(session,'A',throne,payment={'C':4});self.assertIsNone(self.resolve(session))
        self.priority(session,'A')
        offered=[r for r in self.actions(session) if r['id'].startswith('activate:'+throne.ref+':')]
        self.assertEqual(1,len(offered))
        command={'action_id':offered[0]['id'],'targets':[body.ref],'pay':'manual','payment':{'C':3}}
        before=authoritative_state_hash(session.state)
        self.assertFalse(session.act('pilot:D',command).ok);self.assertEqual(before,authoritative_state_hash(session.state))
        result=session.act('pilot:A',command);self.assertTrue(result.ok,result.summary);self.assertIsNone(self.resolve(session))
        self.assertEqual(body.object_id,session.state.cards[throne.object_id].attached_to)
        self.assertIn('defender',session.engine._combat_keywords(session.state.cards[body.object_id]))

    def throne_activation(self,session,body):
        self.priority(session,'A')
        offered=[r for r in self.actions(session) if r['id'].startswith('activate:'+body.ref+':') and not r.get('mana_ability')]
        self.assertEqual(1,len(offered))
        return {'action_id':offered[0]['id'],'pay':'manual','payment':{'C':2}}

    def test_actual_throne_equips_and_granted_tap_uses_current_power_excluding_source(self):
        session=self.session(750903);throne=self.add(session.engine,'Dragon Throne of Tarkir',zone='hand')
        body=self.add(session.engine,'Grizzly Bears',ref='throne-activator');body.acquired_control_turn_count=-1
        other=self.add(session.engine,'Grizzly Bears',ref='throne-other')
        foreign=self.add(session.engine,'Grizzly Bears',seat='D',ref='throne-foreign')
        growth=self.add(session.engine,'Generic Bound Growth',zone='hand')
        private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand',ref='throne-private')
        self.assertTrue(self.binding(session,throne)['strict_capability_ready'])
        self.prepare(session,mana={'C':9,'G':1});self.checkpoint(session);self.equip_throne(session,throne,body)
        command=self.throne_activation(session,body);before=authoritative_state_hash(session.state)
        self.assertFalse(session.act('pilot:B',command).ok);self.assertEqual(before,authoritative_state_hash(session.state))
        result=session.act('pilot:A',command);self.assertTrue(result.ok,result.summary)
        self.assertTrue(session.state.cards[body.object_id].tapped)
        self.assertEqual(0,session.state.players['A'].mana_pool.get('C',0))
        self.assertFalse(any(r['id']==command['action_id'] for r in self.actions(session)))
        self.cast(session,'A',growth,[body.ref],{'G':1});self.top(session)
        self.assertEqual(4,session.engine._numeric_stat(body.object_id,'power'))
        session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
        self.assertEqual((4,6,2),tuple(session.engine._numeric_stat(c.object_id,'power') for c in (body,other,foreign)))
        self.assertIn('trample',session.engine._combat_keywords(session.state.cards[other.object_id]))
        self.assertNotIn('trample',session.engine._combat_keywords(session.state.cards[foreign.object_id]))
        self.assertIn('defender',session.engine._combat_keywords(session.state.cards[body.object_id]))
        self.private_replay(session,private)
