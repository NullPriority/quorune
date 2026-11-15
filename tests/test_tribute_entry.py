"""Pinned CR702.104: Tribute is decided before entry, by the chosen opponent."""
from dataclasses import replace
import json,unittest
from unittest.mock import patch
from common import ROOT
import tribute_native_fixture as fixtures
from quorune.tribute_model import TributeSpec,TRIBUTE_CAPABILITY,TRIBUTE_HANDLER
from quorune.semantic_runtime.tribute import TributeEntryHandler
from quorune.oracle_ir import compile_oracle_card
from quorune.rules.capabilities import CapabilityRegistry
from quorune.card_programs.binding import bind_semantic_program_runtime


class TributeEntryCompilerTests(fixtures.TributeNativeFixture,unittest.TestCase):
    def test_intrinsic_and_unpaid_result_retain_separate_owned_programs(self):
        c=self.db.lookup('Snake of the Golden Grove',fuzzy=False)
        ir=compile_oracle_card(c,capability_registry=self.registry,capability_profile='commander_review')
        self.assertEqual('exact',ir.status,ir.material_residuals)
        intrinsic=next(n for f in ir.faces for n in f.nodes if n.handlers)
        spec=TributeEntryHandler().validate(intrinsic.handlers[0]);self.assertEqual(3,spec.amount)
        trigger=next(n for f in ir.faces for n in f.nodes if n.event=='permanent.enter.self')
        self.assertEqual({'field':'tribute_unpaid','op':'contains_any','value':['tribute']},trigger.event_condition)
        self.assertIn(TRIBUTE_CAPABILITY,intrinsic.capability_dependencies)
        self.assertIn(TRIBUTE_CAPABILITY,trigger.capability_dependencies)

    def test_descriptor_fields_types_and_unknown_mechanic_bodies_fail_closed(self):
        descriptor=TributeSpec(2,'tribute').descriptor()
        self.assertEqual(2,TributeEntryHandler().validate(descriptor).amount)
        for change in ({'schema_version':True},{'amount':True},{'amount':0},{'event':'damage'},
                {'handler_id':TRIBUTE_HANDLER+'x'},{'extra':1}):
            with self.subTest(change=change),self.assertRaises(ValueError):TributeEntryHandler().validate({**descriptor,**change})
        for name in ('Oracle of Bones','Flame-Wreathed Phoenix'):
            c=self.db.lookup(name,fuzzy=False)
            self.assertNotEqual('exact',compile_oracle_card(c,capability_registry=self.registry,capability_profile='commander_review').status)

    def test_verified_capability_withdrawal_preserves_an_independent_flying_source(self):
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index,discover_tests
        raw=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        next(r for r in raw['capabilities'] if r['id']==TRIBUTE_CAPABILITY).update(status='blocked',blockers=['Tribute withdrawn'])
        registry=CapabilityRegistry(raw)
        index=build_index(registry_value=raw,declaration_source=read_evidence_document(ROOT/'platform/capability-evidence-declarations.json'),discovered_test_ids=set(discover_tests(ROOT).values()))
        registry.mark_evidence_verified(validate_capability_evidence_index(index,registry=registry))
        self.assertNotEqual('exact',compile_oracle_card(self.db.lookup('Ornitharch'),capability_registry=registry).status)
        self.assertEqual('exact',compile_oracle_card(self.db.lookup('Vampire Nighthawk'),capability_registry=registry).status)


class TributeEntryNativeTests(fixtures.TributeNativeFixture,unittest.TestCase):
    def test_actual_untargeted_original_results_and_paid_alternatives(self):
        from quorune.haste import has_effective_haste
        cases=(('Ornitharch',2),('Pharagax Giant',2),('Thunder Brute',3),('Fanatic of Xenagos',1))
        for name,count in cases:
            for paid in (False,True):
                with self.subTest(name=name,paid=paid):
                    s,source,private=self.scene(name)
                    self.begin_entry(s,source)
                    self.choose(s,'B',self.option(s,'B',decline=not paid))
                    self.assertIsNone(self.resolve(s))
                    current=s.state.cards[source.object_id]
                    self.assertEqual('battlefield',current.zone)
                    self.assertEqual(count if paid else 0,current.counters.get('+1/+1',0))
                    if name=='Ornitharch':
                        birds=[c for c in s.state.cards.values() if c.is_token and c.zone=='battlefield']
                        self.assertEqual(0 if paid else 2,len(birds))
                        for bird in birds:
                            self.assertEqual('A',bird.controller)
                            self.assertEqual(1,s.engine._numeric_stat(bird.object_id,'power'))
                            self.assertIn('Flying',s.engine._effective_card_data(bird)['keywords'])
                    elif name=='Pharagax Giant':
                        self.assertEqual((40,)+(40 if paid else 35,)*3,
                            tuple(s.state.players[p].life for p in 'ABCD'))
                    else:
                        self.assertEqual(not paid,has_effective_haste(s.engine,current))
                        self.assertEqual((5 if name=='Thunder Brute' else 3)+(count if paid else name=='Fanatic of Xenagos'),
                            s.engine._numeric_stat(current.object_id,'power'))
                    self.finish(s,private)

    def test_actual_targeted_results_select_after_entry_and_paid_entries_never_offer_a_target(self):
        for name,count in (('Nessian Demolok',3),('Siren of the Fanged Coast',3),('Shrike Harpy',2)):
            for paid in (False,True):
                with self.subTest(name=name,paid=paid):
                    bodies={}
                    def before(s,source):
                        bodies['target']=self.add(s.engine,'Mind Stone' if name=='Nessian Demolok' else 'Hill Giant',seat='C')
                    s,source,private=self.scene(name,before=before)
                    self.begin_entry(s,source)
                    self.choose(s,'B',self.option(s,'B',decline=not paid))
                    if not paid:
                        self.assertEqual('semantic.target',s.state.pending_decision.kind)
                        self.assertEqual('battlefield',s.state.cards[source.object_id].zone)
                        self.target(s,'A',['C' if name=='Shrike Harpy' else bodies['target'].ref])
                    if name=='Shrike Harpy' and not paid:
                        self.assertIsNotNone(self.resolve(s))
                        command={'action_id':'choose','cards':[bodies['target'].ref]}
                        self.reject(s,'B',command)
                        result=s.act('pilot:C',command);self.assertTrue(result.ok,result.summary)
                    self.assertIsNone(self.resolve(s))
                    current=s.state.cards[source.object_id];target=s.state.cards[bodies['target'].object_id]
                    self.assertEqual(count if paid else 0,current.counters.get('+1/+1',0))
                    if name=='Siren of the Fanged Coast':
                        self.assertEqual('battlefield',target.zone);self.assertEqual('C' if paid else 'A',target.controller)
                    else:self.assertEqual('battlefield' if paid else 'graveyard',target.zone)
                    self.finish(s,private)

    def test_actual_ravager_fight_remains_optional_after_unpaid_target_selection(self):
        for paid,fight in ((True,False),(False,False),(False,True)):
            with self.subTest(paid=paid,fight=fight):
                bodies={}
                def before(s,source):bodies['target']=self.add(s.engine,'Hill Giant',seat='C')
                s,source,private=self.scene('Nessian Wilds Ravager',before=before)
                self.begin_entry(s,source)
                self.choose(s,'B',self.option(s,'B',decline=not paid))
                if not paid:
                    self.target(s,'A',[bodies['target'].ref])
                    self.assertEqual('semantic.choice',self.resolve(s).kind)
                    command={'action_id':'choose','choice':'apply' if fight else 'decline'}
                    self.reject(s,'B',command)
                    result=s.act('pilot:A',command);self.assertTrue(result.ok,result.summary)
                self.assertIsNone(self.resolve(s))
                self.assertEqual('graveyard' if fight else 'battlefield',s.state.cards[bodies['target'].object_id].zone)
                current=s.state.cards[source.object_id]
                self.assertEqual(6 if paid else 0,current.counters.get('+1/+1',0))
                self.assertEqual(3 if fight else 0,current.marked_damage)
                self.finish(s,private)

    def test_biomancer_and_tribute_both_entry_orders_preserve_distinct_counter_instructions(self):
        for biomancer_first in (False,True):
            for paid in (False,True):
                with self.subTest(biomancer_first=biomancer_first,paid=paid):
                    def before(s,source):
                        control=self.add(s.engine,'Master Biomancer')
                        self.assert_closed(s,control)
                    s,source,private=self.scene('Snake of the Golden Grove',before=before)
                    self.cast(s,'A',source)
                    self.assertEqual('replacement.order',self.resolve(s).kind)
                    if biomancer_first:
                        rows=s.packet('pilot:A',full=True)['decision']['ctx']['options']
                        self.choose(s,'A',next(r for r in rows if 'external-entry-counter' in r['id']))
                    self.choose(s,'A',self.option(s,'A',ends='opponent:B'))
                    self.assertEqual('stack',s.state.cards[source.object_id].zone)
                    self.choose(s,'B',self.option(s,'B',decline=not paid))
                    self.assertIsNone(self.resolve(s))
                    current=s.state.cards[source.object_id]
                    self.assertEqual(5 if paid else 2,current.counters.get('+1/+1',0))
                    self.assertIn('Mutant',s.engine._effective_card_data(current)['type_line'])
                    self.assertEqual(40 if paid else 44,s.state.players['A'].life)
                    self.finish(s,private)

    def test_blink_gets_a_new_opponent_decision_and_old_unpaid_trigger_keeps_its_entry_fact(self):
        for first_paid in (False,True):
            with self.subTest(first_paid=first_paid):
                spells={}
                def before(s,source):
                    spells['blink']=self.add(s.engine,'Ephemerate',zone='hand')
                    self.assert_closed(s,spells['blink'])
                s,source,private=self.scene('Snake of the Golden Grove',before=before)
                self.begin_entry(s,source)
                self.choose(s,'B',self.option(s,'B',decline=not first_paid))
                first_identity=s.state.cards[source.object_id].logical_object_id
                self.assertEqual(40,s.state.players['A'].life)
                self.cast(s,'A',spells['blink'],[source.ref])
                self.assertEqual('replacement.order',self.resolve(s).kind)
                self.choose(s,'A',self.option(s,'A',ends='opponent:C'))
                self.assertEqual(['C'],s.state.pending_decision.actors)
                s=self.replay(s,load=True)
                self.choose(s,'C',self.option(s,'C',decline=first_paid))
                self.assertIsNone(self.resolve(s))
                current=s.state.cards[source.object_id]
                self.assertNotEqual(first_identity,current.logical_object_id)
                self.assertEqual(0 if first_paid else 3,current.counters.get('+1/+1',0))
                self.assertEqual(44,s.state.players['A'].life)
                self.finish(s,private)

    def test_actual_clone_copies_tribute_and_makes_its_own_decision(self):
        for paid in (False,True):
            with self.subTest(paid=paid):
                originals={}
                def before(s,source):
                    originals['snake']=self.add(s.engine,'Snake of the Golden Grove',seat='B')
                    self.assert_closed(s,originals['snake'])
                s,source,private=self.scene('Clone',before=before)
                self.cast(s,'A',source)
                self.assertEqual('replacement.order',self.resolve(s).kind)
                rows=s.packet('pilot:A',full=True)['decision']['ctx']['options']
                self.choose(s,'A',next(r for r in rows if originals['snake'].ref in r['id'] and not r['id'].startswith('decline:')))
                self.choose(s,'A',self.option(s,'A',ends='opponent:C'))
                self.choose(s,'C',self.option(s,'C',decline=not paid))
                self.assertIsNone(self.resolve(s))
                current=s.state.cards[source.object_id]
                self.assertEqual('battlefield',current.zone)
                self.assertEqual(3 if paid else 0,current.counters.get('+1/+1',0))
                self.assertEqual('B',s.state.cards[originals['snake'].object_id].controller)
                self.assertEqual(40 if paid else 44,s.state.players['A'].life)
                self.finish(s,private)

    def test_foreign_owned_reanimated_original_uses_its_entering_controller_and_chosen_opponent(self):
        # Public Reanimate is an existing generic closed-owner control, not a
        # newly claimed original card. Snake itself uses normal registration.
        for paid in (False,True):
            with self.subTest(paid=paid):
                bodies={}
                def before(s,source):
                    bodies['snake']=self.add(s.engine,'Snake of the Golden Grove',seat='D',zone='graveyard')
                    self.assert_closed(s,bodies['snake'])
                s,spell,private=self.scene('Public Reanimate',before=before)
                self.cast(s,'A',spell,[bodies['snake'].ref])
                self.assertEqual('replacement.order',self.resolve(s).kind)
                self.choose(s,'A',self.option(s,'A',ends='opponent:B'))
                self.choose(s,'B',self.option(s,'B',decline=not paid))
                self.assertIsNone(self.resolve(s))
                current=s.state.cards[bodies['snake'].object_id]
                self.assertEqual(('battlefield','A','D'),(current.zone,current.controller,current.owner))
                self.assertEqual(3 if paid else 0,current.counters.get('+1/+1',0))
                self.assertEqual((40 if paid else 44,40,40,40),tuple(s.state.players[p].life for p in 'ABCD'))
                self.finish(s,private)

    def test_simultaneous_copied_entries_finish_each_opponent_decision_before_atomic_entry(self):
        bodies={}
        def before(s,source):
            bodies['snake']=self.add(s.engine,'Snake of the Golden Grove')
            self.assert_closed(s,bodies['snake'])
        s,spell,private=self.scene('Generic Entry Two Copies',before=before)
        self.cast(s,'A',spell,[bodies['snake'].ref])
        self.assertEqual('replacement.order',self.resolve(s).kind)
        self.choose(s,'A',self.option(s,'A',ends='opponent:C'))
        self.assertEqual(['C'],s.state.pending_decision.actors)
        self.assertFalse(any(c.is_token and c.zone=='battlefield' for c in s.state.cards.values()))
        s=self.replay(s,load=True)
        self.choose(s,'C',self.option(s,'C'))
        self.assertEqual(['A'],s.state.pending_decision.actors)
        self.assertFalse(any(c.is_token and c.zone=='battlefield' for c in s.state.cards.values()))
        self.choose(s,'A',self.option(s,'A',ends='opponent:B'))
        self.assertEqual(['B'],s.state.pending_decision.actors)
        self.assertFalse(any(c.is_token and c.zone=='battlefield' for c in s.state.cards.values()))
        s=self.replay(s,load=True)
        self.choose(s,'B',self.option(s,'B',decline=True))
        self.assertIsNone(self.resolve(s))
        tokens=[c for c in s.state.cards.values() if c.is_token and c.zone=='battlefield']
        self.assertEqual(2,len(tokens))
        self.assertEqual([0,3],sorted(c.counters.get('+1/+1',0) for c in tokens))
        self.assertEqual([('A','A')]*2,[(c.owner,c.controller) for c in tokens])
        self.assertEqual(44,s.state.players['A'].life)
        self.finish(s,private)

    def test_departed_opponent_is_not_offered_and_cannot_submit_an_entry_choice(self):
        s,source,private=self.scene('Snake of the Golden Grove')
        self.priority(s,'B')
        result=s.act('pilot:B',{'action_id':'concede','choices':{'confirm_concede':True}})
        self.assertTrue(result.ok,result.summary)
        self.assertFalse(s.state.players['B'].in_game)
        self.cast(s,'A',source)
        self.assertEqual('replacement.order',self.resolve(s).kind)
        rows=s.packet('pilot:A',full=True)['decision']['ctx']['options']
        self.assertEqual(2,len(rows))
        self.assertFalse(any(r['id'].endswith('opponent:B') for r in rows))
        selected=self.option(s,'A',ends='opponent:C')
        self.reject(s,'B',{'action_id':'choose','replacement':selected['id']})
        self.choose(s,'A',selected)
        s=self.replay(s,load=True)
        self.choose(s,'C',self.option(s,'C',decline=True))
        self.assertIsNone(self.resolve(s))
        self.assertEqual(44,s.state.players['A'].life)
        self.finish(s,private)

    def test_fresh_simultaneous_witness_kills_missing_token_entry_outcome_commit(self):
        method='test_simultaneous_copied_entries_finish_each_opponent_decision_before_atomic_entry'
        getattr(type(self)(method),method)()
        with patch('quorune.tribute_entry_results.commit_tribute_entry_outcomes',return_value=None):
            with self.assertRaises(AssertionError):getattr(type(self)(method),method)()

    def test_counter_replacements_use_the_chosen_placing_player_and_receiving_controller(self):
        # CR702.104a and the pinned 2017-04-18 ruling: B places counters
        # on A's creature. Scales checks the receiving controller.
        for name,seat,amount in (('Hardened Scales','A',4),('Hardened Scales','B',3)):
            with self.subTest(name=name,seat=seat):
                def before(s,source):
                    control=self.add(s.engine,name,seat=seat)
                    self.assert_closed(s,control)
                s,source,private=self.scene('Snake of the Golden Grove',before=before)
                self.begin_entry(s,source)
                self.choose(s,'B',self.option(s,'B'))
                self.assertIsNone(self.resolve(s))
                self.assertEqual(amount,s.state.cards[source.object_id].counters.get('+1/+1',0))
                self.assertEqual(40,s.state.players['A'].life)
                self.finish(s,private)

    def test_solemnity_makes_tribute_unpayable_and_preserves_the_unpaid_trigger(self):
        # Pinned Solemnity ruling: the chosen opponent cannot pay tribute.
        def before(s,source):
            control=self.add(s.engine,'Solemnity',seat='C')
            self.assert_closed(s,control)
        s,source,private=self.scene('Snake of the Golden Grove',before=before)
        self.cast(s,'A',source)
        self.assertEqual('replacement.order',self.resolve(s).kind)
        self.choose(s,'A',self.option(s,'A',ends='opponent:B'))
        self.assertIsNone(self.resolve(s))
        self.assertEqual('battlefield',s.state.cards[source.object_id].zone)
        self.assertFalse(s.state.cards[source.object_id].counters)
        self.assertEqual(44,s.state.players['A'].life)
        self.finish(s,private)

    def test_actual_snake_chosen_opponent_pays_or_declines_before_entry_and_life_trigger(self):
        for paid in (False,True):
            with self.subTest(paid=paid):
                s,source,private=self.scene('Snake of the Golden Grove')
                self.begin_entry(s,source)
                s=self.replay(s,load=True)
                self.choose(s,'B',self.option(s,'B',decline=not paid))
                self.assertIsNone(self.resolve(s))
                self.assertEqual('battlefield',s.state.cards[source.object_id].zone)
                self.assertEqual(3 if paid else 0,s.state.cards[source.object_id].counters.get('+1/+1',0))
                self.assertEqual((40 if paid else 44,40,40,40),tuple(s.state.players[p].life for p in 'ABCD'))
                self.finish(s,private)

    def test_unpaid_condition_dependency_is_required_without_coverage_annotations(self):
        s,source,private=self.scene('Snake of the Golden Grove')
        program=next(p for p in s.engine.semantics.programs_for_oracle(source.oracle_id) if p.event=='permanent.enter.self')
        deps=tuple(c for c in program.capability_dependencies if c!=TRIBUTE_CAPABILITY)
        missing=replace(program,coverage=(),capability_dependencies=deps,capability_closure=self.registry.closure(deps,profile='commander_review').to_dict())
        bound=bind_semantic_program_runtime(missing,capability_registry=self.registry,profile='commander_review')
        self.assertFalse(bound['strict']);self.assertIn('capability:undeclared_runtime_dependency:'+TRIBUTE_CAPABILITY,bound['blockers'])

    def test_fresh_paid_entry_witness_kills_an_unconditionally_unpaid_event_provider(self):
        baseline=type(self)('test_actual_snake_chosen_opponent_pays_or_declines_before_entry_and_life_trigger')
        baseline.test_actual_snake_chosen_opponent_pays_or_declines_before_entry_and_life_trigger()
        with patch('quorune.tribute_entry_results.tribute_entry_context',return_value={'tribute_unpaid':['tribute']}):
            witness=type(self)('test_actual_snake_chosen_opponent_pays_or_declines_before_entry_and_life_trigger')
            with self.assertRaises(AssertionError):witness.test_actual_snake_chosen_opponent_pays_or_declines_before_entry_and_life_trigger()
