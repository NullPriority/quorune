"""CR107.1a/b and pinned Dire Fleet Ravager 13-life example."""
from dataclasses import FrozenInstanceError,replace
from fractions import Fraction
from math import ceil,floor
import unittest
from unittest.mock import patch
from quorune.third_quantity_model import ThirdQuantitySpec,THIRD_LIFE_CAPABILITY,THIRD_LIFE_MECHANIC
from quorune.fractional_quantity_model import HalfQuantitySpec
from quorune.compiler.third_life_templates import third_life_effect_template
from quorune.rules.third_life_shapes import third_life_effect_dependencies,third_life_node_capabilities


class ThirdCurrentLifeModelTests(unittest.TestCase):
    def test_versioned_third_roundtrip_is_immutable_and_uses_pinned_rounding_example(self):
        spec=ThirdQuantitySpec('up');self.assertEqual(5,spec.amount(13))
        self.assertEqual(spec,ThirdQuantitySpec.from_dict(spec.to_dict()))
        with self.assertRaises(FrozenInstanceError):spec.divisor=2
        self.assertEqual([1,1,14],[spec.amount(n) for n in (2,3,40)])

    def test_bounded_integer_domain_agrees_with_independent_exact_fraction_rounding(self):
        for value in range(-9,201):
            for rounding,reference in (('up',ceil),('down',floor)):
                self.assertEqual(reference(Fraction(max(0,value),3)),ThirdQuantitySpec(rounding).amount(value))
        for value in (True,1.0,'13',None):
            with self.assertRaises(ValueError):ThirdQuantitySpec('up').amount(value)

    def test_unknown_fields_kind_versions_and_divisors_fail_closed(self):
        payload=ThirdQuantitySpec('up').to_dict()
        for changed in ({**payload,'unknown':1},{**payload,'kind':'half_quantity'},
                {**payload,'schema_version':True},{**payload,'schema_version':2},
                {**payload,'divisor':2},{**payload,'divisor':True},{**payload,'rounding':'nearest'}):
            with self.assertRaises(ValueError):ThirdQuantitySpec.from_dict(changed)
        for key in payload:
            with self.assertRaises(ValueError):ThirdQuantitySpec.from_dict({k:v for k,v in payload.items() if k!=key})

    def test_existing_half_v1_meaning_and_strict_divisor_are_unchanged(self):
        old={'kind':'half_quantity','schema_version':1,'divisor':2,'rounding':'up'}
        self.assertEqual(old,HalfQuantitySpec.from_dict(old).to_dict());self.assertEqual(7,HalfQuantitySpec.from_dict(old).amount(13))
        for changed in (ThirdQuantitySpec('up').to_dict(),{**old,'divisor':3},{**old,'schema_version':2}):
            with self.assertRaises(ValueError):HalfQuantitySpec.from_dict(changed)
        with self.assertRaises(ValueError):ThirdQuantitySpec.from_dict(old)

    def test_fresh_wrong_divisor_mutation_is_killed_after_positive_third_amount(self):
        self.assertEqual(5,ThirdQuantitySpec('up').amount(13))
        with patch.object(ThirdQuantitySpec,'amount',lambda self,value:HalfQuantitySpec(self.rounding).amount(value)):
            with self.assertRaises(AssertionError):self.assertEqual(5,ThirdQuantitySpec('up').amount(13))


class ThirdCurrentLifeCompilerTests(unittest.TestCase):
    TEXT='Each player loses a third of their life, rounded up.'

    def test_complete_all_player_clause_uses_typed_value_and_rejects_nearest_unowned_grammar(self):
        value=third_life_effect_template(self.TEXT);self.assertIsNotNone(value)
        self.assertIsNone(value.target_schema);self.assertEqual('all_active_players',value.effects[0]['scope'])
        self.assertEqual(ThirdQuantitySpec('up').to_dict(),value.effects[0]['amount'])
        for bad in (self.TEXT+' Draw a card.',self.TEXT.replace('up','down'),self.TEXT.replace('Each player','Each opponent'),
                self.TEXT.replace('a third','half'),self.TEXT.replace('their life','your life'),self.TEXT.replace('a third','X'),self.TEXT[:-1]):
            self.assertIsNone(third_life_effect_template(bad),bad)

    def test_raw_result_and_node_proof_require_exact_scope_value_and_mechanic(self):
        value=third_life_effect_template(self.TEXT);effect=value.effects[0]
        required=(THIRD_LIFE_CAPABILITY,'life.change.effect')
        self.assertEqual(required,third_life_effect_dependencies(effect))
        self.assertEqual(required,third_life_node_capabilities(effects=value.effects,target_schema=None,mechanic_ids=value.mechanics))
        for bad in ({**effect,'player':'A'},{**effect,'scope':'opponents'},{**effect,'op':'life'},
                {**effect,'amount':ThirdQuantitySpec('down').to_dict()}):
            with self.assertRaises(ValueError):third_life_effect_dependencies(bad)
            self.assertEqual((),third_life_node_capabilities(effects=(bad,),target_schema=None,mechanic_ids=value.mechanics))
        for changes in ({'target_schema':{'count':1}},{'mechanic_ids':('cr-119-life',)},
                {'mechanic_ids':(THIRD_LIFE_MECHANIC,)},{'effects':(effect,effect)}):
            self.assertEqual((),third_life_node_capabilities(**{'effects':value.effects,'target_schema':None,'mechanic_ids':value.mechanics,**changes}))
        self.assertEqual((),third_life_effect_dependencies({'op':'lose_life','amount':1,'player':'A'}))

    def test_raw_runtime_frame_infers_authority_without_coverage_and_rejects_hidden_or_mixed_values(self):
        from types import SimpleNamespace as Row
        from quorune.rules.special_node_shapes import special_result_trigger_runtime_dependencies
        effect=third_life_effect_template(self.TEXT).effects[0]
        def frame(**changes):return Row(**{'effects':(effect,),'target_schema':None,'event':'permanent.enter.self',
            'active_zone':'battlefield','event_condition':None,'coverage':(),'capability_dependencies':(),**changes})
        required=set(special_result_trigger_runtime_dependencies(frame()))
        self.assertTrue({THIRD_LIFE_CAPABILITY,'life.change.effect','trigger.effect.fixed_event',
            'trigger.placement.apnap','trigger.event.normalized_zone_change'}<=required)
        for changes in ({'effects':(effect,{'op':'draw','player':'$controller','count':1})},
                {'effects':({'op':'offer_optional_effect','player':'$controller','effects':[effect]},)},
                {'event':'damage.dealt.self'},{'active_zone':'graveyard'},{'target_schema':{'count':1}}):
            with self.assertRaises(ValueError):special_result_trigger_runtime_dependencies(frame(**changes))

    def test_triggered_third_declares_its_actual_fixed_event_owner_beside_zone_transport(self):
        from quorune.rules.special_node_shapes import special_trigger_envelope_capabilities
        own=(THIRD_LIFE_CAPABILITY,'life.change.effect')
        mechanics=(THIRD_LIFE_MECHANIC,'cr-119-life','cr-603-handling-triggered-abilities','trigger-event-normalized-zone-change')
        required=special_trigger_envelope_capabilities(own,mechanics)
        self.assertTrue({'trigger.effect.fixed_event','trigger.placement.apnap','trigger.event.normalized_zone_change'}<=set(required))
        self.assertEqual(tuple(sorted(own)),special_trigger_envelope_capabilities(own,(THIRD_LIFE_MECHANIC,'cr-119-life')))

    def test_full_source_keeps_material_siblings_and_missing_authority_never_becomes_exact(self):
        from quorune.oracle_ir import compile_oracle_card
        from test_bound_effect_programs import record
        card=replace(record('Menace, deathtouch\nWhen this creature enters, each player loses a third of their life, rounded up.','Creature'),
            keywords=('Menace','Deathtouch'))
        ir=compile_oracle_card(card,capability_registry=None,capability_profile='commander_review')
        self.assertNotEqual('exact',ir.status)
        nodes=[n for f in ir.faces for n in f.nodes if n.event=='permanent.enter.self']
        self.assertEqual(1,len(nodes));self.assertEqual('third_quantity',nodes[0].effects[0]['amount']['kind'])
        self.assertFalse(nodes[0].exact);self.assertTrue(nodes[0].residual_ids)

    def test_third_runtime_passes_each_current_life_amount_to_one_canonical_loss_batch(self):
        from types import SimpleNamespace as Row
        from quorune.effect_runtime.fractional_life import apply_fractional_life_loss
        import quorune.effect_runtime.life_effects as owner
        lives={'A':13,'B':2,'C':3,'D':40};losses={'A':5,'B':1,'C':1,'D':14};seen=[]
        host=Row(active_seats=tuple(lives),state=Row(players={p:Row(life=n) for p,n in lives.items()}),_log=lambda *a,**kw:None)
        result=Row(for_player=lambda p:Row(delta=-losses[p],resolved_loss=losses[p]),
            to_dict=lambda:{},changed_players=tuple(lives))
        def commit(host,changes,**kwargs):seen.append(tuple((c.player,c.amount) for c in changes));return result
        with patch.object(owner,'_commit',side_effect=commit),patch.object(owner,'_result',side_effect=lambda value:value):
            total=apply_fractional_life_loss(host,third_life_effect_template(self.TEXT).effects[0],actor='B',operation='lose_life',reason='pinned third example')
        self.assertEqual(21,total);self.assertEqual([(('A',-5),('B',-1),('C',-1),('D',-14))],seen)
        self.assertEqual(lives,{p:host.state.players[p].life for p in lives})




from pathlib import Path
import json,tempfile
from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition,DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
import test_turn_life_loss_recovery as recovery_helpers


class ThirdCurrentLifeNativeTests(InheritedNativeFixture,unittest.TestCase):
    start=recovery_helpers.TurnLifeLossRecoveryNativeTests.start
    reject=recovery_helpers.TurnLifeLossRecoveryNativeTests.reject
    finish=recovery_helpers.TurnLifeLossRecoveryNativeTests.finish

    @classmethod
    def setUpClass(cls):
        cls.temporary=tempfile.TemporaryDirectory();folder=Path(cls.temporary.name)
        base=json.loads((ROOT/'tests/fixtures/bound-effect-program-cards.json').read_text(encoding='utf-8'))
        base['cards']=[r for r in base['cards'] if r['name']=='Generic Bound Plains'];base['rulings']=[]
        basic=folder/'basic.json';basic.write_text(json.dumps(base),encoding='utf-8')
        build_fixture_database([basic,ROOT/'tests/fixtures/third-current-life-cards.json'],folder/'third-life.sqlite3')
        cls.db=CardDatabase(folder/'third-life.sqlite3');cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Actual third-current-life',[DeckEntry('Sliver Hivelord',1,'commander'),
            DeckEntry('Generic Bound Plains',30)],['Sliver Hivelord'])

    def test_complete_original_normally_binds_both_keywords_and_independent_etb_owner(self):
        from quorune.card_programs.binding import bind_semantic_program_runtime
        s,_=self.start(724000);source=self.add(s.engine,'Dire Fleet Ravager',zone='hand');self.assert_closed(s,source)
        ir=compile_oracle_card(self.db.lookup('Dire Fleet Ravager'),capability_registry=self.registry,capability_profile='commander_review')
        self.assertEqual('exact',ir.status,ir.material_residuals)
        programs=[p for p in s.engine.semantics.programs_for_oracle(source.oracle_id) if p.event=='permanent.enter.self']
        self.assertEqual(1,len(programs));program=programs[0]
        for cap in (THIRD_LIFE_CAPABILITY,'life.change.effect','trigger.event.normalized_zone_change'):
            deps=tuple(d for d in program.capability_dependencies if d!=cap)
            raw=replace(program,coverage=(),capability_dependencies=deps,capability_closure=self.registry.closure(deps,profile='commander_review').to_dict())
            bound=bind_semantic_program_runtime(raw,capability_registry=self.registry,profile='commander_review')
            self.assertFalse(bound['strict']);self.assertIn('capability:undeclared_runtime_dependency:'+cap,bound['blockers'])

    def cast_world(self,seed,*,response=None,life=None,extra_mana=0,resonator=False):
        s,private=self.start(seed);source=self.add(s.engine,'Dire Fleet Ravager',zone='hand');self.assert_closed(s,source)
        if response is not None:
            self.add(s.engine,response,seat='B',zone='hand',ref='third-response');s.state.players['B'].mana_pool.update({'U':1,'W':1,'C':1})
        for seat,total in (life or {'A':13,'B':2,'C':3,'D':40}).items():s.state.players[seat].life=total
        if resonator:self.add(s.engine,'Strionic Resonator',ref='third-resonator')
        self.prepare(s,mana={'C':3+extra_mana,'B':2});self.checkpoint(s)
        before=sum(s.state.players['A'].mana_pool.values());self.cast(s,'A',source)
        self.assertEqual(before-5,sum(s.state.players['A'].mana_pool.values()))
        self.resolve_top(s);self.assertEqual('battlefield',s.state.cards[source.object_id].zone)
        self.assertTrue({'menace','deathtouch'}<=set(s.engine._combat_keywords(s.state.cards[source.object_id])))
        self.assertTrue(any(i.source_object_id==source.object_id for i in s.state.stack))
        return s,private,source

    def actual_printed_result(self,seed):
        s,private,source=self.cast_world(seed);self.assertIsNone(self.resolve(s))
        self.assertEqual((8,1,2,26),tuple(s.state.players[p].life for p in 'ABCD'))
        self.assertEqual('battlefield',s.state.cards[source.object_id].zone);self.finish(s,private)

    def test_actual_printed_five_mana_cast_and_four_independent_third_life_losses_replay_privately(self):
        self.actual_printed_result(724001)

    def test_actual_life_gain_response_changes_only_that_players_resolution_amount(self):
        s,private,source=self.cast_world(724010,response='Revitalize')
        response=next(c for c in s.state.cards.values() if c.ref=='third-response');self.assert_closed(s,response)
        self.cast(s,'B',response);self.resolve_top(s)
        self.assertEqual((13,5,3,40),tuple(s.state.players[p].life for p in 'ABCD'))
        self.assertIsNone(self.resolve(s));self.assertEqual((8,3,2,26),tuple(s.state.players[p].life for p in 'ABCD'));self.finish(s,private)

    def test_actual_source_bounce_does_not_remove_its_independent_life_loss_trigger(self):
        s,private,source=self.cast_world(724011,response='Unsummon')
        response=next(c for c in s.state.cards.values() if c.ref=='third-response');self.assert_closed(s,response)
        self.cast(s,'B',response,[source.ref]);self.resolve_top(s);self.assertEqual('hand',s.state.cards[source.object_id].zone)
        self.assertIsNone(self.resolve(s));self.assertEqual((8,1,2,26),tuple(s.state.players[p].life for p in 'ABCD'));self.finish(s,private)

    def test_actual_countered_etb_keeps_printed_keywords_and_paid_cast_without_life_loss(self):
        s,private,source=self.cast_world(724012,response='Stifle')
        response=next(c for c in s.state.cards.values() if c.ref=='third-response');self.assert_closed(s,response)
        self.cast(s,'B',response,[s.state.stack[-1].ref]);self.assertIsNone(self.resolve(s))
        self.assertEqual((13,2,3,40),tuple(s.state.players[p].life for p in 'ABCD'))
        self.assertEqual(0,sum(s.state.players['A'].mana_pool.values()));self.assertEqual('battlefield',s.state.cards[source.object_id].zone)
        self.assertTrue({'menace','deathtouch'}<=set(s.engine._combat_keywords(s.state.cards[source.object_id])));self.finish(s,private)

    def test_actual_copied_trigger_recomputes_each_new_life_total_and_eliminates_only_zero_player(self):
        s,private,source=self.cast_world(724013,extra_mana=2,resonator=True)
        resonator=next(c for c in s.state.cards.values() if c.ref=='third-resonator');self.assert_closed(s,resonator)
        self.priority(s,'A');action=next(a for a in s.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions'] if a['id'].startswith('activate:'+resonator.ref+':'))
        command={'action_id':action['id'],'pay':'auto','targets':[s.state.stack[-1].ref]};self.reject(s,'D',command)
        result=s.act('pilot:A',command);self.assertTrue(result.ok,result.summary);self.resolve_top(s)
        self.assertEqual(2,len([i for i in s.state.stack if i.source_object_id==source.object_id]))
        self.resolve_top(s);self.assertEqual((8,1,2,26),tuple(s.state.players[p].life for p in 'ABCD'))
        self.assertIsNone(self.resolve(s));self.assertEqual((5,0,1,17),tuple(s.state.players[p].life for p in 'ABCD'))
        self.assertNotIn('B',s.engine.active_seats);self.assertIn('C',s.engine.active_seats);self.finish(s,private)

    def test_fresh_native_half_divisor_substitution_is_killed_after_actual_positive_result(self):
        self.actual_printed_result(724020);observed=[]
        def half(spec,value):observed.append(value);return HalfQuantitySpec(spec.rounding).amount(value)
        with patch.object(ThirdQuantitySpec,'amount',half):
            with self.assertRaises(AssertionError):self.actual_printed_result(724021)
        self.assertTrue(observed);self.assertIn(13,observed)

    def test_actual_post_batch_fault_restores_all_four_lives_events_and_pending_trigger_then_retries(self):
        from quorune.record import authoritative_state_hash
        from quorune.errors import GameRuleError
        import quorune.effect_runtime.life_effects as owner
        s,private,source=self.cast_world(724030);original=owner._commit;observed=[];failed=False
        def fault(*args,**kwargs):
            result=original(*args,**kwargs);observed.append(tuple(s.state.players[p].life for p in 'ABCD'))
            raise GameRuleError('Injected post-third-batch failure')
        with patch.object(owner,'_commit',side_effect=fault):
            for _ in range(8):
                before=authoritative_state_hash(s.state)
                result=s.act(s.pending_principals()[0],{'action_id':'pass'})
                if not result.ok:
                    self.assertIn('Injected post-third',result.summary);self.assertEqual(before,authoritative_state_hash(s.state));failed=True;break
        self.assertTrue(failed);self.assertEqual([(8,1,2,26)],observed)
        self.assertEqual((13,2,3,40),tuple(s.state.players[p].life for p in 'ABCD'))
        self.assertTrue(any(i.source_object_id==source.object_id for i in s.state.stack))
        s=self.replay(s,load=True);self.assertIsNone(self.resolve(s))
        self.assertEqual((8,1,2,26),tuple(s.state.players[p].life for p in 'ABCD'));self.finish(s,private)

    def test_actual_all_one_life_players_lose_simultaneously_and_game_ends_as_draw(self):
        s,private,source=self.cast_world(724031,life=dict.fromkeys('ABCD',1))
        for viewer in 'ABD':self.assertNotIn(private.ref,str(s.packet('pilot:'+viewer,full=True)))
        self.assertIsNone(self.resolve(s));self.assertEqual((0,0,0,0),tuple(s.state.players[p].life for p in 'ABCD'))
        self.assertTrue(s.state.game_over);self.assertIsNone(s.state.winner);self.replay(s,load=True)

    def test_genuinely_verified_third_owner_withdrawal_blocks_full_original_preserving_existing_half(self):
        import copy
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index,discover_tests
        raw=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        doc=read_evidence_document(ROOT/'platform/capability-evidence-declarations.json')
        known=set(discover_tests(ROOT).values());changed=copy.deepcopy(raw)
        next(r for r in changed['capabilities'] if r['id']==THIRD_LIFE_CAPABILITY).update(status='blocked',blockers=['Third owner withdrawn'])
        registry=CapabilityRegistry(changed);index=build_index(registry_value=changed,declaration_source=doc,discovered_test_ids=known)
        registry.mark_evidence_verified(validate_capability_evidence_index(index,registry=registry))
        self.assertNotEqual('exact',compile_oracle_card(self.db.lookup('Dire Fleet Ravager'),capability_registry=registry,capability_profile='commander_review').status)
        self.assertEqual('exact',compile_oracle_card(self.db.lookup('Fraying Omnipotence'),capability_registry=registry,capability_profile='commander_review').status)
        from quorune.oracle_ir import register_generated_programs
        from quorune.semantics import SemanticRegistry
        from quorune.card_programs.adapters import compile_best_available_card_program
        from quorune.card_programs import bind_card_program_runtime
        for name,expected in (('Dire Fleet Ravager',False),('Fraying Omnipotence',True)):
            card=self.db.lookup(name);semantics=SemanticRegistry()
            register_generated_programs(self.db,semantics,(card,),capability_registry=registry,capability_profile='commander_review',
                promote_exact_runtime_handlers=True,promote_exact_trigger_programs=True,
                promote_exact_effect_programs=True,promote_exact_capability_declarations=True)
            program=compile_best_available_card_program(self.db,card,semantic_registry=semantics,capability_registry=registry,capability_profile='commander_review')
            bound=bind_card_program_runtime(program,capability_registry=registry,profile='commander_review')
            self.assertEqual(expected,bound['strict_capability_ready'],(name,bound['blockers']))


if __name__=='__main__':unittest.main()
