from __future__ import annotations

from pathlib import Path
from dataclasses import replace
import json
import tempfile
import unittest
from unittest.mock import patch

from common import ROOT
import test_compound_damage_recipients as controls
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.card_programs.binding import bind_semantic_program_runtime
from scripts.build_test_database import build_fixture_database


class AnnouncedTargetCardinalityRuntimeTests(unittest.TestCase):
    register_fixture_cards_on_add = True
    session = controls.CompoundDamageRuntimeTests.session
    add = controls.CompoundDamageRuntimeTests.add
    prepare = controls.CompoundDamageRuntimeTests.prepare
    checkpoint = controls.CompoundDamageRuntimeTests.checkpoint
    resolve = controls.CompoundDamageRuntimeTests.resolve
    replay = controls.CompoundDamageRuntimeTests.replay
    binding = controls.CompoundDamageRuntimeTests.binding
    actions = controls.CompoundDamageRuntimeTests.actions
    action = controls.CompoundDamageRuntimeTests.action
    cast = controls.CompoundDamageRuntimeTests.cast
    private_replay = controls.CompoundDamageRuntimeTests.private_replay

    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        path = Path(cls.temp.name) / 'announced-targets.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',
            ROOT/'tests/fixtures/announced-target-cardinality-cards.json',
            ROOT/'tests/fixtures/targeted-spell-cost-cards.json'], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition('Announced target witnesses', [
            DeckEntry('Generic Bound Commander', 1, 'commander'), DeckEntry('Generic Bound Plains', 30)],
            ['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls):
        cls.db.close(); cls.temp.cleanup()

    def test_original_gridlock_uses_one_announced_x_for_cost_and_distinct_targets(self):
        session = self.session(729201)
        source = self.add(session.engine, 'Gridlock', zone='hand')
        cards = [self.add(session.engine, 'Generic Bound Body', seat=s, ref='grid-target-'+s) for s in 'BD']
        private = self.add(session.engine, 'Generic Bound Growth', seat='C', zone='hand')
        self.prepare(session, mana={'U': 1, 'C': 2}); self.checkpoint(session)
        self.assertTrue(self.binding(session, source)['strict_capability_ready'])
        action = self.action(session, source)
        before = authoritative_state_hash(session.state)
        for x, refs in ((True,[cards[0].ref]), (2,[cards[0].ref]), (2,[cards[0].ref]*2)):
            rejected = session.act('pilot:A', {'action_id':action['id'], 'x':x, 'targets':refs, 'pay':'auto'})
            self.assertFalse(rejected.ok); self.assertEqual(before, authoritative_state_hash(session.state))
        result = session.act('pilot:A', {'action_id':action['id'], 'x':2,
            'targets':[c.ref for c in cards], 'pay':'auto'})
        self.assertTrue(result.ok, result.summary)
        self.assertEqual(2, session.state.stack[-1].x_value)
        self.assertEqual(0, sum(session.state.players['A'].mana_pool.values()))
        session = self.replay(session, load=True); self.assertIsNone(self.resolve(session))
        self.assertTrue(all(session.state.cards[c.object_id].tapped for c in cards))
        self.private_replay(session, private)

    def test_original_gridlock_advertises_and_pays_targeted_tax_with_the_same_x(self):
        session=self.session(729209)
        source=self.add(session.engine,'Gridlock',zone='hand')
        taxed=self.add(session.engine,'Boreal Elemental',seat='B')
        other=self.add(session.engine,'Generic Bound Body',seat='D')
        private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand')
        self.prepare(session,mana={'U':1,'C':4});self.checkpoint(session)
        for card in (source,taxed):self.assertTrue(self.binding(session,card)['strict_capability_ready'])
        action=self.action(session,source)
        self.assertIn(taxed.ref,action['target_schema']['legal_refs'])
        self.assertIn(other.ref,action['target_schema']['legal_refs'])
        self.assertEqual('x',action['target_schema']['groups'][0]['count_from'])
        result=session.act('pilot:A',{'action_id':action['id'],'x':2,
            'targets':[taxed.ref,other.ref],'pay':'auto'})
        self.assertTrue(result.ok,result.summary)
        self.assertEqual(2,session.state.stack[-1].x_value)
        self.assertEqual(0,sum(session.state.players['A'].mana_pool.values()))
        session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
        self.assertTrue(all(session.state.cards[c.object_id].tapped for c in (taxed,other)))
        self.private_replay(session,private)

    def test_wildest_self_exile_promotes_without_admitting_unrepresented_result_siblings(self):
        session=self.session(729212)
        source=self.add(session.engine,'Wildest Dreams',zone='hand')
        self.assertTrue(self.binding(session,source)['strict_capability_ready'])
        programs=[p for p in session.engine.semantics.programs_for_oracle(source.oracle_id) if p.event=='resolve']
        self.assertEqual(1,len(programs));self.assertEqual('exile',programs[0].destination)
        from quorune.compiler.announced_target_sets import announced_target_set_effect_template
        self.assertIsNone(announced_target_set_effect_template(self.db.lookup('Wildest Dreams').oracle_text))
        for name in ('Divergent Equation','Pest Infestation'):
            source=self.add(session.engine,name,zone='hand')
            self.assertFalse(self.binding(session,source)['strict_capability_ready'],name)

    def test_zero_selected_targets_resolves_independent_draw_but_all_illegal_targets_do_not(self):
        for x in (0,1):
            session=self.session(729213+x)
            source=self.add(session.engine,'Generic Drawing Grid',zone='hand')
            target=self.add(session.engine,'Generic Bound Body',seat='B')
            murder=self.add(session.engine,'Murder',seat='C',zone='hand')
            private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand')
            session.state.players['C'].mana_pool.update({'B':2,'C':1})
            self.prepare(session,mana={'U':1,'C':x});self.checkpoint(session)
            self.assertTrue(self.binding(session,source)['strict_capability_ready'])
            accepted=session.act('pilot:A',{'action_id':self.action(session,source)['id'],'x':x,
                'targets':[target.ref] if x else [],'pay':'auto'})
            self.assertTrue(accepted.ok,accepted.summary)
            hand=len(session.state.players['A'].zones['hand'])
            library=len(session.state.players['A'].zones['library'])
            if x:self.cast(session,'C',murder,[target.ref])
            session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
            self.assertEqual(hand+(0 if x else 1),len(session.state.players['A'].zones['hand']))
            self.assertEqual(library-(0 if x else 1),len(session.state.players['A'].zones['library']))
            self.assertEqual('graveyard',session.state.cards[source.object_id].zone)
            self.private_replay(session,private)

    def test_original_gridlock_tax_preview_excludes_a_target_when_x_and_tax_cannot_be_paid(self):
        session=self.session(729210)
        source=self.add(session.engine,'Gridlock',zone='hand')
        taxed=self.add(session.engine,'Boreal Elemental',seat='B')
        other=self.add(session.engine,'Generic Bound Body',seat='D')
        self.prepare(session,mana={'U':1,'C':2});self.checkpoint(session)
        action=self.action(session,source)
        self.assertNotIn(taxed.ref,action['target_schema']['legal_refs'])
        self.assertIn(other.ref,action['target_schema']['legal_refs'])
        before=authoritative_state_hash(session.state)
        rejected=session.act('pilot:A',{'action_id':action['id'],'x':1,'targets':[taxed.ref],'pay':'auto'})
        self.assertFalse(rejected.ok);self.assertEqual(before,authoritative_state_hash(session.state))
        accepted=session.act('pilot:A',{'action_id':action['id'],'x':1,'targets':[other.ref],'pay':'auto'})
        self.assertTrue(accepted.ok,accepted.summary)
        self.assertEqual(1,session.state.players['A'].mana_pool['C'])
        session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
        self.assertFalse(session.state.cards[taxed.object_id].tapped)
        self.assertTrue(session.state.cards[other.object_id].tapped)

    def test_zero_only_tax_preview_is_killed_by_a_fresh_original_gridlock(self):
        from quorune.rules.casting import target_cardinality
        with patch.object(target_cardinality,'target_count_cost_preview',side_effect=lambda host,seat,card,program,option:option):
            method='test_original_gridlock_advertises_and_pays_targeted_tax_with_the_same_x'
            with self.assertRaises(AssertionError):getattr(type(self)(method),method)()

    def test_up_to_tax_preview_keeps_zero_targets_and_paid_x_without_offering_an_unpayable_target(self):
        session=self.session(729226)
        source=self.add(session.engine,'Generic Flexible Grid',zone='hand')
        taxed=self.add(session.engine,'Boreal Elemental',seat='B')
        private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand')
        self.prepare(session,mana={'U':1,'C':2});self.checkpoint(session)
        action=self.action(session,source)
        self.assertNotIn(taxed.ref,action['target_schema']['legal_refs'])
        self.assertEqual(2,action['cost_options'][0]['choice_schema']['x']['maximum'])
        before=authoritative_state_hash(session.state)
        rejected=session.act('pilot:A',{'action_id':action['id'],'x':1,'targets':[taxed.ref],'pay':'auto'})
        self.assertFalse(rejected.ok);self.assertEqual(before,authoritative_state_hash(session.state))
        accepted=session.act('pilot:A',{'action_id':action['id'],'x':2,'targets':[],'pay':'auto'})
        self.assertTrue(accepted.ok,accepted.summary)
        session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
        self.assertFalse(session.state.cards[taxed.object_id].tapped)
        self.assertEqual(0,sum(session.state.players['A'].mana_pool.values()))
        self.private_replay(session,private)

    def test_countered_original_gridlock_does_not_tap_or_refund_the_announced_price(self):
        session=self.session(729219)
        source=self.add(session.engine,'Gridlock',zone='hand')
        target=self.add(session.engine,'Generic Bound Body',seat='D')
        counter=self.add(session.engine,'Counterspell',seat='B',zone='hand')
        private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand')
        session.state.players['B'].mana_pool.update({'U':2})
        self.prepare(session,mana={'U':1,'C':1});self.checkpoint(session)
        accepted=session.act('pilot:A',{'action_id':self.action(session,source)['id'],
            'x':1,'targets':[target.ref],'pay':'auto'})
        self.assertTrue(accepted.ok,accepted.summary)
        self.cast(session,'B',counter,[session.state.stack[-1].ref])
        session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
        self.assertFalse(session.state.cards[target.object_id].tapped)
        self.assertEqual(0,sum(session.state.players['A'].mana_pool.values()))
        self.assertEqual('graveyard',session.state.cards[source.object_id].zone)
        self.private_replay(session,private)

    def test_original_gridlock_all_chosen_targets_illegal_is_distinct_from_zero_target_cast(self):
        session=self.session(729211)
        source=self.add(session.engine,'Gridlock',zone='hand')
        target=self.add(session.engine,'Generic Bound Body',seat='B')
        murder=self.add(session.engine,'Murder',seat='C',zone='hand')
        private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand')
        session.state.players['C'].mana_pool.update({'B':2,'C':1})
        self.prepare(session,mana={'U':1,'C':1});self.checkpoint(session)
        accepted=session.act('pilot:A',{'action_id':self.action(session,source)['id'],
            'x':1,'targets':[target.ref],'pay':'auto'})
        self.assertTrue(accepted.ok,accepted.summary)
        self.cast(session,'C',murder,[target.ref])
        session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
        self.assertEqual('graveyard',session.state.cards[target.object_id].zone)
        self.assertEqual('graveyard',session.state.cards[source.object_id].zone)
        self.assertEqual(0,sum(session.state.players['A'].mana_pool.values()))
        self.private_replay(session,private)

    def test_original_by_force_can_destroy_nine_targets_without_a_fixed_six_cap(self):
        session = self.session(729202)
        source = self.add(session.engine, 'By Force', zone='hand')
        cards = [self.add(session.engine, 'Generic Bound Mentor', seat='ABCD'[i%4], ref='by-force-'+str(i)) for i in range(9)]
        private = self.add(session.engine, 'Generic Bound Growth', seat='C', zone='hand')
        self.prepare(session, mana={'R':1,'C':9}); self.checkpoint(session)
        self.assertTrue(self.binding(session, source)['strict_capability_ready'])
        result = session.act('pilot:A', {'action_id':self.action(session,source)['id'], 'x':9,
            'targets':[c.ref for c in cards], 'pay':'auto'})
        self.assertTrue(result.ok, result.summary)
        session = self.replay(session, load=True); self.assertIsNone(self.resolve(session))
        self.assertTrue(all(session.state.cards[c.object_id].zone=='graveyard' for c in cards))
        self.private_replay(session, private)

    def test_original_gridlock_zero_x_casts_without_any_targets_and_replays(self):
        session = self.session(729203)
        source = self.add(session.engine, 'Gridlock', zone='hand')
        private = self.add(session.engine, 'Generic Bound Growth', seat='C', zone='hand')
        self.prepare(session, mana={'U':1}); self.checkpoint(session)
        self.assertTrue(self.binding(session, source)['strict_capability_ready'])
        result = session.act('pilot:A', {'action_id':self.action(session,source)['id'], 'x':0,'pay':'auto'})
        self.assertTrue(result.ok,result.summary)
        self.assertEqual(0,session.state.stack[-1].x_value)
        self.assertEqual([],session.state.stack[-1].targets)
        session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
        self.assertEqual('graveyard',session.state.cards[source.object_id].zone)
        self.private_replay(session,private)

    def test_original_gridlock_partial_target_loss_taps_only_the_surviving_incarnation(self):
        session = self.session(729204)
        source=self.add(session.engine,'Gridlock',zone='hand')
        cards=[self.add(session.engine,'Generic Bound Body',seat=s,ref='partial-grid-'+s) for s in 'BD']
        murder=self.add(session.engine,'Murder',seat='C',zone='hand')
        private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand')
        session.state.players['C'].mana_pool.update({'B':2,'C':1})
        self.prepare(session,mana={'U':1,'C':2});self.checkpoint(session)
        self.assertTrue(self.binding(session,source)['strict_capability_ready'])
        result=session.act('pilot:A',{'action_id':self.action(session,source)['id'],'x':2,
            'targets':[c.ref for c in cards],'pay':'auto'})
        self.assertTrue(result.ok,result.summary)
        self.cast(session,'C',murder,[cards[1].ref])
        session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
        self.assertTrue(session.state.cards[cards[0].object_id].tapped)
        self.assertEqual('graveyard',session.state.cards[cards[1].object_id].zone)
        self.assertFalse(session.state.cards[cards[1].object_id].tapped)
        self.assertEqual(0,sum(session.state.players['A'].mana_pool.values()))
        self.private_replay(session,private)

    def test_up_to_count_keeps_paid_x_above_eligible_count_and_allows_zero_selected(self):
        for chosen in (False,True):
            session=self.session(729205)
            source=self.add(session.engine,'Generic Flexible Grid',zone='hand')
            body=self.add(session.engine,'Generic Bound Body',seat='B')
            private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand')
            self.prepare(session,mana={'U':1,'C':9});self.checkpoint(session)
            self.assertTrue(self.binding(session,source)['strict_capability_ready'])
            action=self.action(session,source)
            self.assertEqual(9,action['cost_options'][0]['choice_schema']['x']['maximum'])
            result=session.act('pilot:A',{'action_id':action['id'],'x':9,
                'targets':[body.ref] if chosen else [],'pay':'auto'})
            self.assertTrue(result.ok,result.summary)
            self.assertEqual(9,session.state.stack[-1].x_value)
            self.assertEqual(0,sum(session.state.players['A'].mana_pool.values()))
            session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
            self.assertEqual(chosen,session.state.cards[body.object_id].tapped)
            self.private_replay(session,private)

    def test_actual_cascade_forces_original_gridlock_to_zero_target_count(self):
        session=self.session(729206)
        party=self.add(session.engine,'Boarding Party',zone='hand')
        source=self.add(session.engine,'Gridlock',zone='library')
        library=session.state.players['A'].zones['library']
        library.remove(source.object_id);library.insert(0,source.object_id)
        body=self.add(session.engine,'Generic Bound Body',seat='B')
        private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand')
        self.prepare(session,mana={'R':1,'C':5});self.checkpoint(session)
        for card in (party,source):self.assertTrue(self.binding(session,card)['strict_capability_ready'])
        self.cast(session,'A',party);pending=self.resolve(session)
        self.assertEqual('selection.exile_cast',pending.kind)
        self.assertTrue(session.packet('pilot:A',full=True)['decision']['ctx']['cast_options'])
        before=authoritative_state_hash(session.state)
        rejected=session.act('pilot:A',{'action_id':'cast','x':1,'targets':[body.ref]})
        self.assertFalse(rejected.ok);self.assertEqual(before,authoritative_state_hash(session.state))
        result=session.act('pilot:A',{'action_id':'cast','x':0})
        self.assertTrue(result.ok,result.summary)
        self.assertEqual(0,next(item.x_value for item in session.state.stack if item.card_object_id==source.object_id))
        session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
        self.assertFalse(session.state.cards[body.object_id].tapped)
        self.private_replay(session,private)

    def test_executable_count_rejects_undeclared_and_malformed_parameter_authority(self):
        from quorune.target_cardinality import ANNOUNCED_TARGET_SET_CAPABILITY as cap
        session=self.session(729207);source=self.add(session.engine,'Gridlock',zone='hand')
        program=next(p for p in session.engine.semantics.programs_for_oracle(source.oracle_id)
            if cap in p.capability_dependencies)
        self.assertTrue(bind_semantic_program_runtime(program,capability_registry=self.registry,profile='commander_review')['strict'])
        dependencies=tuple(c for c in program.capability_dependencies if c!=cap)
        missing=replace(program,capability_dependencies=dependencies,
            capability_closure=self.registry.closure(dependencies,profile='commander_review').to_dict())
        bound=bind_semantic_program_runtime(missing,capability_registry=self.registry,profile='commander_review')
        self.assertFalse(bound['strict'])
        self.assertIn('capability:undeclared_runtime_dependency:'+cap,bound['blockers'])
        effects=json.loads(json.dumps(program.effects));effects[0]['announced_target_count']=False
        malformed=replace(program,effects=effects)
        self.assertFalse(bind_semantic_program_runtime(malformed,capability_registry=self.registry,profile='commander_review')['strict'])

    def test_wrong_announced_binding_is_killed_by_a_fresh_original_gridlock(self):
        from quorune.rules.casting import target_cardinality
        original=target_cardinality.bind_target_count_option
        def choose_one(program,option,response,*,has_mana_x,has_declared_cost_x=False,target_limit=None):
            return original(program,option,{**response,'x':1},has_mana_x=has_mana_x,
                has_declared_cost_x=has_declared_cost_x,target_limit=target_limit)
        with patch.object(target_cardinality,'bind_target_count_option',side_effect=choose_one):
            method='test_original_gridlock_uses_one_announced_x_for_cost_and_distinct_targets'
            with self.assertRaises(AssertionError):
                getattr(type(self)(method),method)()

    def test_restored_fixed_six_handler_bound_is_killed_by_a_fresh_original_by_force(self):
        from quorune.semantic_runtime import fixed_target_set_handlers
        original=fixed_target_set_handlers._fixed_target_fields
        def restore_fixed(effect,context,*,operation,replacements):
            fixed={key:value for key,value in effect.items() if key!='announced_target_count'}
            return original(fixed,context,operation=operation,replacements=replacements)
        with patch.object(fixed_target_set_handlers,'_fixed_target_fields',side_effect=restore_fixed):
            method='test_original_by_force_can_destroy_nine_targets_without_a_fixed_six_cap'
            with self.assertRaises(AssertionError):getattr(type(self)(method),method)()

    def test_original_candelabra_activation_binds_nine_targets_through_typed_x_cost(self):
        session=self.session(729208)
        source=self.add(session.engine,'Candelabra of Tawnos')
        lands=[self.add(session.engine,'Generic Bound Plains',seat='ABCD'[i%4],ref='candelabra-land-'+str(i)) for i in range(9)]
        for land in lands:land.tapped=True
        private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand')
        self.prepare(session,mana={'C':9});self.checkpoint(session)
        self.assertTrue(self.binding(session,source)['strict_capability_ready'])
        action=next(r for r in self.actions(session) if r['id'].startswith('activate:'+source.ref+':'))
        before=authoritative_state_hash(session.state)
        rejected=session.act('pilot:B',{'action_id':action['id'],'x':9,'targets':[c.ref for c in lands],'pay':'auto'})
        self.assertFalse(rejected.ok);self.assertEqual(before,authoritative_state_hash(session.state))
        result=session.act('pilot:A',{'action_id':action['id'],'x':9,'targets':[c.ref for c in lands],'pay':'auto'})
        self.assertTrue(result.ok,result.summary)
        self.assertEqual(9,session.state.stack[-1].x_value)
        self.assertTrue(session.state.cards[source.object_id].tapped)
        session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
        self.assertTrue(all(not session.state.cards[c.object_id].tapped for c in lands))
        self.assertTrue(session.state.cards[source.object_id].tapped)
        self.assertEqual(0,sum(session.state.players['A'].mana_pool.values()))
        self.private_replay(session,private)

    def test_original_death_denied_returns_only_chosen_owned_graveyard_creatures(self):
        session=self.session(729215)
        source=self.add(session.engine,'Death Denied',zone='hand')
        cards=[self.add(session.engine,'Generic Bound Body',zone='graveyard',ref='death-denied-'+str(i)) for i in range(2)]
        noncreature=self.add(session.engine,'Generic Bound Growth',zone='graveyard',ref='ineligible-graveyard-spell')
        foreign=self.add(session.engine,'Generic Bound Body',seat='B',zone='graveyard')
        private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand')
        self.prepare(session,mana={'B':2,'C':2});self.checkpoint(session)
        self.assertTrue(self.binding(session,source)['strict_capability_ready'])
        action=self.action(session,source)
        for illegal in (noncreature,foreign):
            self.assertNotIn(illegal.ref,action['target_schema']['legal_refs'])
            before=authoritative_state_hash(session.state)
            rejected=session.act('pilot:A',{'action_id':action['id'],'x':2,
                'targets':[cards[0].ref,illegal.ref],'pay':'auto'})
            self.assertFalse(rejected.ok);self.assertEqual(before,authoritative_state_hash(session.state))
        accepted=session.act('pilot:A',{'action_id':action['id'],'x':2,'targets':[c.ref for c in cards],'pay':'auto'})
        self.assertTrue(accepted.ok,accepted.summary)
        session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
        self.assertTrue(all(session.state.cards[c.object_id].zone=='hand' for c in cards))
        self.assertTrue(all(session.state.cards[c.object_id].zone=='graveyard' for c in (noncreature,foreign)))
        self.assertEqual(0,sum(session.state.players['A'].mana_pool.values()))
        self.private_replay(session,private)

    def test_original_candelabra_zero_and_partial_target_departure_keep_paid_x(self):
        for x in (0,2):
            session=self.session(729216+x)
            source=self.add(session.engine,'Candelabra of Tawnos')
            lands=[self.add(session.engine,'Generic Bound Plains',seat=s,ref='partial-untap-'+s) for s in 'BD']
            for land in lands:land.tapped=True
            bounce=self.add(session.engine,'Boomerang',seat='C',zone='hand')
            private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand')
            session.state.players['C'].mana_pool.update({'U':2})
            self.prepare(session,mana={'C':x});self.checkpoint(session)
            action=next(r for r in self.actions(session) if r['id'].startswith('activate:'+source.ref+':'))
            accepted=session.act('pilot:A',{'action_id':action['id'],'x':x,
                'targets':[c.ref for c in lands] if x else [],'pay':'auto'})
            self.assertTrue(accepted.ok,accepted.summary)
            self.assertEqual(x,session.state.stack[-1].x_value)
            if x:self.cast(session,'C',bounce,[lands[1].ref])
            session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
            self.assertEqual(not bool(x),session.state.cards[lands[0].object_id].tapped)
            self.assertEqual('hand' if x else 'battlefield',session.state.cards[lands[1].object_id].zone)
            self.assertTrue(session.state.cards[source.object_id].tapped)
            self.assertEqual(0,sum(session.state.players['A'].mana_pool.values()))
            self.private_replay(session,private)

    def test_original_rats_feast_keeps_single_graveyard_ownership_at_zero_and_positive_x(self):
        for x in (0,2):
            session=self.session(729220+x)
            source=self.add(session.engine,"Rats' Feast",zone='hand')
            cards=[self.add(session.engine,'Generic Bound Body',seat='B',zone='graveyard',ref='feast-b-'+str(i)) for i in range(2)]
            other=self.add(session.engine,'Generic Bound Body',seat='D',zone='graveyard',ref='feast-d')
            private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand')
            self.prepare(session,mana={'B':1,'C':x});self.checkpoint(session)
            self.assertTrue(self.binding(session,source)['strict_capability_ready'])
            action=self.action(session,source)
            if x:
                before=authoritative_state_hash(session.state)
                rejected=session.act('pilot:A',{'action_id':action['id'],'x':x,
                    'targets':[cards[0].ref,other.ref],'pay':'auto'})
                self.assertFalse(rejected.ok);self.assertEqual(before,authoritative_state_hash(session.state))
            accepted=session.act('pilot:A',{'action_id':action['id'],'x':x,
                'targets':[c.ref for c in cards] if x else [],'pay':'auto'})
            self.assertTrue(accepted.ok,accepted.summary)
            session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
            self.assertTrue(all(session.state.cards[c.object_id].zone==('exile' if x else 'graveyard') for c in cards))
            self.assertEqual('graveyard',session.state.cards[other.object_id].zone)
            self.assertEqual(0,sum(session.state.players['A'].mana_pool.values()))
            self.private_replay(session,private)

    def test_original_glimpse_zero_target_scry_keeps_private_pending_choice_and_replay(self):
        session=self.session(729223)
        source=self.add(session.engine,'Glimpse the Sun God',zone='hand')
        private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand')
        self.prepare(session,mana={'W':1});self.checkpoint(session)
        self.assertTrue(self.binding(session,source)['strict_capability_ready'])
        top=session.state.players['A'].zones['library'][-1]
        top_ref=session.state.cards[top].ref
        accepted=session.act('pilot:A',{'action_id':self.action(session,source)['id'],'x':0,'pay':'auto'})
        self.assertTrue(accepted.ok,accepted.summary)
        pending=self.resolve(session);self.assertEqual('semantic.choice',pending.kind)
        for viewer in 'BCD':self.assertNotIn(top_ref,json.dumps(session.packet('pilot:'+viewer,full=True)))
        session=self.replay(session,load=True)
        before=authoritative_state_hash(session.state)
        rejected=session.act('pilot:B',{'action_id':'choose','cards':{'top':[],'bottom':[top_ref]}})
        self.assertFalse(rejected.ok);self.assertEqual(before,authoritative_state_hash(session.state))
        accepted=session.act('pilot:A',{'action_id':'choose','cards':{'top':[],'bottom':[top_ref]}})
        self.assertTrue(accepted.ok,accepted.summary)
        self.assertIsNone(self.resolve(session))
        self.assertEqual(top,session.state.players['A'].zones['library'][0])
        self.assertEqual('graveyard',session.state.cards[source.object_id].zone)
        self.private_replay(session,private)

    def test_original_floodwater_dam_prices_two_mana_x_symbols_but_selects_only_x_lands(self):
        session=self.session(729224)
        source=self.add(session.engine,'Floodwater Dam')
        lands=[self.add(session.engine,'Generic Bound Plains',seat=s,ref='dam-land-'+s) for s in 'BD']
        private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand')
        self.prepare(session,mana={'C':5});self.checkpoint(session)
        self.assertTrue(self.binding(session,source)['strict_capability_ready'])
        action=next(r for r in self.actions(session) if r['id'].startswith('activate:'+source.ref+':'))
        accepted=session.act('pilot:A',{'action_id':action['id'],'x':2,'targets':[c.ref for c in lands],'pay':'auto'})
        self.assertTrue(accepted.ok,accepted.summary)
        self.assertEqual(2,session.state.stack[-1].x_value)
        self.assertEqual(0,sum(session.state.players['A'].mana_pool.values()))
        session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
        self.assertTrue(all(session.state.cards[c.object_id].tapped for c in lands))
        self.assertTrue(session.state.cards[source.object_id].tapped)
        self.private_replay(session,private)

    def test_original_gridlock_above_six_targets_adds_each_of_three_tax_sources_once(self):
        session=self.session(729225)
        source=self.add(session.engine,'Gridlock',zone='hand')
        taxed=[self.add(session.engine,'Boreal Elemental',seat=s,ref='many-tax-'+s) for s in 'BCD']
        other=[self.add(session.engine,'Generic Bound Body',seat='D',ref='many-other-'+str(i)) for i in range(6)]
        private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand')
        self.prepare(session,mana={'U':1,'C':15});self.checkpoint(session)
        action=self.action(session,source)
        self.assertTrue(all(c.ref in action['target_schema']['legal_refs'] for c in taxed+other))
        prices=action['cost_options'][0]['target_costs']['prices']
        self.assertEqual(set(range(4)),{len(row['components']) for row in prices})
        accepted=session.act('pilot:A',{'action_id':action['id'],'x':9,
            'targets':[c.ref for c in taxed+other],'pay':'auto'})
        self.assertTrue(accepted.ok,accepted.summary)
        self.assertEqual(9,session.state.stack[-1].x_value)
        self.assertEqual(0,sum(session.state.players['A'].mana_pool.values()))
        session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
        self.assertTrue(all(session.state.cards[c.object_id].tapped for c in taxed+other))
        self.private_replay(session,private)
