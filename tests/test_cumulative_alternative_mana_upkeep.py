"""CR702.24a: per-age color choices and one complete payment vector."""
from dataclasses import replace
import unittest
from quorune.cumulative_alternative_mana_upkeep import CumulativeAlternativeManaSpec,compile_alternative_mana_upkeep

class CumulativeAlternativeManaModelTests(unittest.TestCase):
    def test_four_printed_pairs_roundtrip_and_preserve_old_wrapper_rejection(self):
        for a,b in (('W','U'),('U','B'),('G','W'),('R','G')):
            spec=compile_alternative_mana_upkeep('Cumulative upkeep {'+a+'} or {'+b+'}')
            self.assertEqual((a,b),spec.colors);self.assertEqual(spec,CumulativeAlternativeManaSpec.from_dict(spec.to_dict()));self.assertEqual(spec,CumulativeAlternativeManaSpec.from_effect(spec.effect()))
            for version in (True,1,2,3,4,5,6,7,8,10):
                with self.assertRaises(ValueError):CumulativeAlternativeManaSpec.from_effect({**spec.effect(),'schema_version':version})

    def test_per_age_choices_produce_all_mixed_three_counter_prices(self):
        spec=CumulativeAlternativeManaSpec(('W','U'));seen=set()
        for a in spec.colors:
            for b in spec.colors:
                for c in spec.colors:
                    counts={}
                    for color in (a,b,c):counts=spec.propose(3,counts,color)
                    cost=spec.requirements(3,counts);seen.add((cost['W'],cost['U']))
        self.assertEqual({(3,0),(2,1),(1,2),(0,3)},seen)

    def test_proposals_are_immutable_and_partial_payment_is_rejected(self):
        spec=CumulativeAlternativeManaSpec(('G','W'));counts={'G':1};next_counts=spec.propose(2,counts,'W')
        self.assertEqual({'G':1},counts);self.assertEqual({'G':1,'W':1},next_counts)
        with self.assertRaises(ValueError):spec.requirements(2,counts)
        self.assertEqual(2,sum(spec.requirements(2,next_counts).values()))
        with self.assertRaises(ValueError):spec.propose(2,next_counts,'G')

    def test_large_complete_price_has_no_option_enumeration_ceiling(self):
        spec=CumulativeAlternativeManaSpec(('R','G'));cost=spec.requirements(1000000,{'R':123456,'G':876544})
        self.assertEqual(1000000,sum(cost.values()));self.assertEqual(123456,cost['R']);self.assertEqual(876544,cost['G'])

    def test_zero_price_and_malformed_or_foreign_counts_are_closed(self):
        spec=CumulativeAlternativeManaSpec(('W','U'));self.assertEqual(0,sum(spec.requirements(0,{}).values()))
        for age,counts in ((True,{}),(-1,{}),(1,{'W':True}),(1,{'W':-1}),(1,{'B':1}),(1,{'W':2})):
            with self.assertRaises(ValueError):spec.requirements(age,counts)
        for colors in (('W','W'),('C','W'),('W','2'),('w','U'),('W',),('W','U','B')):
            with self.assertRaises(ValueError):replace(spec,colors=colors)

    def test_unrepresented_prices_recipients_and_result_siblings_remain_closed(self):
        for text in ('Cumulative upkeep {W/U}','Cumulative upkeep {W} or {W}','Cumulative upkeep {2} or {W}','Cumulative upkeep {W}{U} or {B}','Cumulative upkeep {W} or {U}. Draw a card.'):
            self.assertIsNone(compile_alternative_mana_upkeep(text))
        spec=CumulativeAlternativeManaSpec(('W','U'))
        for changes in ({'player':'B'},{'source':'other'},{'extra':True}):
            with self.assertRaises(ValueError):CumulativeAlternativeManaSpec.from_effect({**spec.effect(),**changes})


class CumulativeAlternativeManaChoiceTests(unittest.TestCase):
    def world(self,age=0):
        from types import SimpleNamespace
        source=SimpleNamespace(ref='S1',logical_object_id='S1@1',zone='battlefield',phased_out=False,controller='A',counters={'age':age})
        query=SimpleNamespace(active_seats=('A','B'),object=lambda *a,**k:source,cost_is_affordable=lambda actor,cost:cost.get('W',0)<=2 and cost.get('U',0)<=1)
        context=SimpleNamespace(actor='A',stack_controller='A',stack_ref='STACK1',stack_label='Upkeep',source_ref='S1',source_logical_object_id='S1@1',query=query)
        spec=CumulativeAlternativeManaSpec(('W','U'));effect=spec.effect();effect.update(player='A',source='S1')
        return context,source,effect

    def continuation(self,prepared):
        from types import SimpleNamespace
        return SimpleNamespace(effect=prepared.continuation_effect,semantic_frame=SimpleNamespace(controller='A'))

    def test_age_is_committed_before_proposals_and_no_mana_intent_is_emitted_during_them(self):
        from quorune.semantic_choices.cumulative_alternative_mana_upkeep import prepare_cumulative_alternative_mana_upkeep,complete_cumulative_alternative_mana_upkeep
        context,source,effect=self.world();first=prepare_cumulative_alternative_mana_upkeep(effect,context)
        self.assertEqual('age',first.preparation_intents[0].counter_name);self.assertEqual(0,source.counters['age'])
        source.counters['age']=3;price=prepare_cumulative_alternative_mana_upkeep(first.auto_continue.prepend_effects[0],context)
        self.assertEqual(('W','U','decline'),price.request.choice.legal_values)
        proposed=complete_cumulative_alternative_mana_upkeep(self.continuation(price),{'choice':'W'},context.query)
        self.assertFalse(proposed.intents);self.assertEqual(3,proposed.repeat_effect['upkeep_age'])
        self.assertEqual({'W':1,'U':0},dict(proposed.repeat_effect['color_counts']))

    def test_full_mixed_price_uses_existing_source_maintenance_and_ordinary_mana_owner(self):
        from quorune.semantic_choices.cumulative_alternative_mana_upkeep import prepare_cumulative_alternative_mana_upkeep,complete_cumulative_alternative_mana_upkeep
        from quorune.semantic_choices.source_maintenance import prepare_source_maintenance,complete_source_maintenance
        from quorune.source_maintenance import SourceMaintenanceSpec
        context,source,effect=self.world(3);effect['stage']='price'
        for color in ('W','U','W'):
            offered=prepare_cumulative_alternative_mana_upkeep(effect,context)
            result=complete_cumulative_alternative_mana_upkeep(self.continuation(offered),{'action_id':'choose','choice':color},context.query)
            self.assertFalse(result.intents);effect=result.repeat_effect;source.counters['age']=9
        delegated=prepare_cumulative_alternative_mana_upkeep(effect,context)
        inner=delegated.auto_continue.prepend_effects[0];self.assertEqual(3,inner['schema_version'])
        price=SourceMaintenanceSpec.from_effect(inner).payment
        self.assertEqual(2,price.requirements['W']);self.assertEqual(1,price.requirements['U']);self.assertEqual(3,sum(price.requirements.values()))
        old=prepare_source_maintenance(inner,context);self.assertIn(True,old.request.choice.legal_values)
        paid=complete_source_maintenance(self.continuation(old),{'pay':True},context.query)
        self.assertEqual(1,len(paid.intents));self.assertEqual(price.requirements,paid.intents[0].requirements)

    def test_unaffordable_declared_vector_does_not_charge_partial_mana(self):
        from quorune.semantic_choices.cumulative_alternative_mana_upkeep import prepare_cumulative_alternative_mana_upkeep
        from quorune.semantic_choices.source_maintenance import prepare_source_maintenance,complete_source_maintenance
        context,source,effect=self.world(3);effect.update(stage='declare',upkeep_age=3,color_counts={'W':3,'U':0})
        inner=prepare_cumulative_alternative_mana_upkeep(effect,context).auto_continue.prepend_effects[0]
        offered=prepare_source_maintenance(inner,context);self.assertEqual((False,),offered.request.choice.legal_values)
        with self.assertRaises(ValueError):complete_source_maintenance(self.continuation(offered),{'pay':True},context.query)
        declined=complete_source_maintenance(self.continuation(offered),{'pay':False},context.query)
        self.assertEqual(('S1',),declined.intents[0].object_refs);self.assertEqual('graveyard',declined.intents[0].destination)

    def test_zero_price_and_early_decline_use_original_source_sacrifice(self):
        from quorune.semantic_choices.cumulative_alternative_mana_upkeep import prepare_cumulative_alternative_mana_upkeep,complete_cumulative_alternative_mana_upkeep
        context,source,effect=self.world(0);effect['stage']='price';offered=prepare_cumulative_alternative_mana_upkeep(effect,context)
        self.assertFalse(complete_cumulative_alternative_mana_upkeep(self.continuation(offered),{'pay':True},context.query).intents)
        source.counters['age']=2;offered=prepare_cumulative_alternative_mana_upkeep(effect,context)
        first=complete_cumulative_alternative_mana_upkeep(self.continuation(offered),{'choice':'W'},context.query)
        second=prepare_cumulative_alternative_mana_upkeep(first.repeat_effect,context)
        declined=complete_cumulative_alternative_mana_upkeep(self.continuation(second),{'choice':'decline'},context.query)
        self.assertEqual(('S1',),declined.intents[0].object_refs)

    def test_changed_source_phase_payer_or_color_fails_before_payment(self):
        from quorune.semantic_choices.cumulative_alternative_mana_upkeep import prepare_cumulative_alternative_mana_upkeep,complete_cumulative_alternative_mana_upkeep
        context,source,effect=self.world(2);effect['stage']='price';offered=prepare_cumulative_alternative_mana_upkeep(effect,context)
        for response in ({'choice':'B'},{'choice':'W','pay':True},{'choice':True}):
            with self.assertRaises(ValueError):complete_cumulative_alternative_mana_upkeep(self.continuation(offered),response,context.query)
        for changed in ({**effect,'player':'B'},{**effect,'stage':'unknown'},{**effect,'upkeep_age':2}):
            with self.assertRaises(ValueError):prepare_cumulative_alternative_mana_upkeep(changed,context)
        source.logical_object_id='S1@2'
        with self.assertRaises(ValueError):prepare_cumulative_alternative_mana_upkeep(effect,context)

    def test_fresh_mixed_price_color_omission_is_killed(self):
        from unittest.mock import patch
        spec=CumulativeAlternativeManaSpec(('W','U'))
        def witness():self.assertEqual(1,spec.requirements(3,{'W':2,'U':1})['U'])
        witness()
        with patch.object(CumulativeAlternativeManaSpec,'requirements',return_value={'W':3,'U':0}),self.assertRaises(AssertionError):witness()


    def test_payload_owns_controller_upkeep_authority_without_annotations(self):
        from types import SimpleNamespace
        from quorune.rules.cumulative_alternative_mana_upkeep import cumulative_alternative_mana_upkeep_runtime_dependencies
        from quorune.rules.cumulative_object_upkeep import CONTROLLER_UPKEEP
        spec=CumulativeAlternativeManaSpec(('W','U'));base=dict(event='step.begin',active_zone='battlefield',target_schema=None,event_condition=CONTROLLER_UPKEEP,effects=[spec.effect()])
        self.assertIn('counter.producer.cumulative_upkeep_alternative_mana',cumulative_alternative_mana_upkeep_runtime_dependencies(SimpleNamespace(**base)))
        for change in ({'event':'spell.cast'},{'active_zone':'hand'},{'target_schema':{'kind':'player'}},{'event_condition':None}):
            with self.assertRaises(ValueError):cumulative_alternative_mana_upkeep_runtime_dependencies(SimpleNamespace(**{**base,**change}))

    def test_large_age_final_proposal_delegates_one_vector_without_option_expansion(self):
        from quorune.semantic_choices.cumulative_alternative_mana_upkeep import prepare_cumulative_alternative_mana_upkeep,complete_cumulative_alternative_mana_upkeep
        from quorune.source_maintenance import SourceMaintenanceSpec
        context,source,effect=self.world(1000000);effect.update(stage='declare',upkeep_age=1000000,color_counts={'W':999998,'U':1})
        offered=prepare_cumulative_alternative_mana_upkeep(effect,context)
        last=complete_cumulative_alternative_mana_upkeep(self.continuation(offered),{'choice':'W'},context.query)
        final=prepare_cumulative_alternative_mana_upkeep(last.repeat_effect,context)
        payment=SourceMaintenanceSpec.from_effect(final.auto_continue.prepend_effects[0]).payment
        self.assertEqual(999999,payment.requirements['W']);self.assertEqual(1,payment.requirements['U'])


class SourceCounterLifeGrammarTests(unittest.TestCase):
    def test_both_scaled_source_death_life_results_use_existing_source_counter_scalar(self):
        from test_qualified_zone_event_queries import query_record
        from quorune.oracle_ir import compile_oracle_card
        from quorune.rules.capabilities import load_default_capability_registry
        reg=load_default_capability_registry()
        for verb,op,field in (('gain','life','delta'),('lose','lose_life','amount')):
            card=query_record('When this creature dies, you '+verb+' 2 life for each age counter on it.')
            ir=compile_oracle_card(card,capability_registry=reg,capability_profile='commander_review');self.assertEqual('exact',ir.status,ir.material_residuals)
            effect=ir.faces[0].nodes[0].effects[0];self.assertEqual(op,effect['op']);self.assertEqual(2,effect[field]['coefficient']);self.assertEqual('age',effect[field]['counter_name'])

    def test_ambiguous_counter_reference_and_unsupported_rider_stay_material(self):
        from test_qualified_zone_event_queries import query_record
        from quorune.oracle_ir import compile_oracle_card
        from quorune.rules.capabilities import load_default_capability_registry
        reg=load_default_capability_registry()
        for text in ('When this creature dies, you gain 2 life for each age counter on another permanent.','When this creature dies, you lose 2 life for each age counter on it unless an opponent pays life.'):
            ir=compile_oracle_card(query_record(text),capability_registry=reg,capability_profile='commander_review');self.assertNotEqual('exact',ir.status)


NAMES=('Jötun Owl Keeper','Krovikan Whispers','Arctic Nishoba','Earthen Goo')

class CumulativeAlternativeManaCompilerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import tempfile
        from pathlib import Path
        from common import ROOT
        from quorune.carddb import CardDatabase
        from quorune.rules.capabilities import load_default_capability_registry
        from scripts.build_test_database import build_fixture_database
        cls.temporary=tempfile.TemporaryDirectory();path=Path(cls.temporary.name)/'alternative-upkeep-compiler.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/cumulative-alternative-mana-upkeep-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
    @classmethod
    def tearDownClass(cls):cls.db.close();cls.temporary.cleanup()

    def test_all_four_whole_parents_close_normally_with_both_life_siblings_and_data_owned_price(self):
        from dataclasses import replace
        from quorune.oracle_ir import compile_oracle_card,register_generated_programs
        from quorune.semantics import SemanticRegistry
        from quorune.card_programs.adapters import compile_best_available_card_program
        from quorune.card_programs.binding import bind_card_program_runtime,bind_semantic_program_runtime
        cards=[self.db.lookup(n,fuzzy=False) for n in NAMES];semantics=SemanticRegistry()
        register_generated_programs(self.db,semantics,cards,trust_level='provisional',capability_registry=self.registry,capability_profile='commander_review',promote_exact_runtime_handlers=True,promote_exact_trigger_programs=True,promote_exact_effect_programs=True,promote_exact_capability_declarations=True)
        cap='counter.producer.cumulative_upkeep_alternative_mana'
        for card in cards:
            with self.subTest(card=card.name):
                ir=compile_oracle_card(card,capability_registry=self.registry,capability_profile='commander_review');self.assertEqual('exact',ir.status,ir.material_residuals)
                program=compile_best_available_card_program(self.db,card,semantic_registry=semantics,capability_registry=self.registry,capability_profile='commander_review');bound=bind_card_program_runtime(program,capability_registry=self.registry,profile='commander_review');self.assertTrue(bound['strict_capability_ready'],bound['blockers'])
                upkeep=next(p for p in semantics.programs_for_oracle(card.oracle_id) if any(e.get('schema_version')==9 for e in p.effects))
                deps=tuple(c for c in upkeep.capability_dependencies if c!=cap);changed=replace(upkeep,coverage=(),handlers=(),capability_dependencies=deps,capability_closure=self.registry.closure(deps,profile='commander_review').to_dict())
                result=bind_semantic_program_runtime(changed,capability_registry=self.registry,profile='commander_review');self.assertFalse(result['strict']);self.assertTrue(any(cap in b for b in result['blockers']),result['blockers'])

    def test_genuine_alternative_price_withdrawal_retains_old_mana_life_object_counter_and_spell(self):
        import json
        from common import ROOT
        from quorune.oracle_ir import compile_oracle_card
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index,discover_tests
        from test_cumulative_upkeep_counter_placement import cumulative_record
        raw=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding='utf-8'));next(r for r in raw['capabilities'] if r['id']=='counter.producer.cumulative_upkeep_alternative_mana').update(status='blocked',blockers=['alternative price owner withdrawn'])
        reg=CapabilityRegistry(raw);index=build_index(registry_value=raw,declaration_source=read_evidence_document(ROOT/'platform/capability-evidence-declarations.json'),discovered_test_ids=set(discover_tests(ROOT).values()));reg.mark_evidence_verified(validate_capability_evidence_index(index,registry=reg))
        self.assertNotEqual('exact',compile_oracle_card(self.db.lookup('Earthen Goo'),capability_registry=reg,capability_profile='commander_review').status)
        for card in (self.db.lookup('Phyrexian Soulgorger'),self.db.lookup('Aboroth'),self.db.lookup('Memnite'),cumulative_record('Cumulative upkeep {1}',61),cumulative_record('Cumulative upkeep—Pay 1 life.',62)):
            self.assertEqual('exact',compile_oracle_card(card,capability_registry=reg,capability_profile='commander_review').status)


from inherited_composition_fixture import InheritedNativeFixture
import test_target_recipient_results as recipients

class CumulativeAlternativeManaNativeTests(InheritedNativeFixture,unittest.TestCase):
    reject=recipients.TargetRecipientNativeTests.reject
    finish=recipients.TargetRecipientNativeTests.finish
    @classmethod
    def setUpClass(cls):
        import tempfile
        from pathlib import Path
        from common import ROOT
        from quorune.carddb import CardDatabase
        from quorune.deck import DeckDefinition,DeckEntry
        from quorune.rules.capabilities import load_default_capability_registry
        from scripts.build_test_database import build_fixture_database
        cls.temporary=tempfile.TemporaryDirectory();path=Path(cls.temporary.name)/'alternative-upkeep-native.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/cumulative-alternative-mana-upkeep-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Alternative colored upkeep',[DeckEntry('Sliver Hivelord',1,'commander'),DeckEntry('Generic Bound Plains',30)],['Sliver Hivelord'])

    def world(self,seed,name='Jötun Owl Keeper',*,age=0,cast=False):
        from quorune.model import TurnEntry
        s=self.session(seed);source=self.add(s.engine,name,zone='hand' if cast else 'battlefield');source.counters['age']=age
        private=self.add(s.engine,'Counterspell',seat='C',zone='hand',ref='alternative-upkeep-private')
        s.state.current_turn=TurnEntry('alternative-upkeep-initial-D','D');s.state.last_normal_turn_player='D';self.assert_closed(s,source)
        return s,source,private

    def begin(self,s):
        self.prepare(s,actor='D',active='D');self.checkpoint(s)
        self.until(s,lambda ss:ss.state.active_player=='A' and ss.state.phase=='beginning' and ss.state.step=='upkeep')
        self.assertEqual('semantic.choice',self.resolve(s).kind);self.assertEqual(['A'],s.state.pending_decision.actors)

    def declare(self,s,color):
        command={'action_id':'choose','choice':color};self.reject(s,'B',command)
        result=s.act('pilot:A',command);self.assertTrue(result.ok,result.summary);decision=self.resolve(s);self.assertEqual('semantic.choice',decision.kind)
        return decision

    def pay(self,s,value,*,complete=True):
        command={'action_id':'choose','pay':value};self.reject(s,'D',command);result=s.act('pilot:A',command);self.assertTrue(result.ok,result.summary)
        decision=self.resolve(s)
        if complete:self.assertIsNone(decision)
        else:self.assertEqual('semantic.choice',decision.kind)

    def test_actual_all_four_cast_originals_declare_and_autopay_a_printed_color(self):
        import json
        from quorune.cumulative_alternative_mana_upkeep import compile_alternative_mana_upkeep
        lands={'W':'Plains','U':'Island','B':'Swamp','R':'Mountain','G':'Forest'}
        for i,name in enumerate(NAMES):
            with self.subTest(card=name):
                s,source,private=self.world(70224300+i,name,cast=True);self.add(s.engine,'Vedalken Orrery')
                spec=compile_alternative_mana_upkeep(self.db.lookup(name).oracle_text.splitlines()[0 if name=='Jötun Owl Keeper' else 1].partition(' (')[0])
                chosen=spec.colors[0];land=self.add(s.engine,lands[chosen]);victim=self.add(s.engine,'Grizzly Bears',seat='B')
                self.prepare(s,actor='D',active='D');s.state.players['A'].mana_pool.update({'C':6,'W':2,'U':2,'B':2,'R':2,'G':2});self.checkpoint(s)
                self.cast_for(s,'A',source,[victim.ref] if name=='Krovikan Whispers' else []);self.assertIsNone(self.resolve(s))
                if name=='Krovikan Whispers':self.assertEqual('A',s.state.cards[victim.object_id].controller)
                self.until(s,lambda ss:ss.state.active_player=='A' and ss.state.phase=='beginning' and ss.state.step=='upkeep')
                self.assertEqual('semantic.choice',self.resolve(s).kind);self.assertFalse(s.state.cards[land.object_id].tapped)
                self.reject(s,'A',{'action_id':'choose','choice':'C'});s=self.replay(s,load=True);self.declare(s,chosen)
                self.assertFalse(s.state.cards[land.object_id].tapped);self.pay(s,True);self.assertTrue(s.state.cards[land.object_id].tapped)
                self.assertEqual('battlefield',s.state.cards[source.object_id].zone);self.assertEqual(1,s.state.cards[source.object_id].counters.get('age'));self.finish(s,private)

    def test_actual_three_age_mixed_price_is_paid_once_after_all_proposals(self):
        s,source,private=self.world(70224310,age=2);lands=[self.add(s.engine,'Plains',ref='alternative-white-'+str(i)) for i in range(2)];lands.append(self.add(s.engine,'Island'))
        self.begin(s);self.assertEqual(3,s.state.cards[source.object_id].counters.get('age'))
        for color in ('W','U','W'):
            self.declare(s,color);self.assertTrue(all(not s.state.cards[c.object_id].tapped for c in lands));self.assertEqual(0,sum(s.state.players['A'].mana_pool.values()))
        s=self.replay(s,load=True);self.pay(s,True);self.assertTrue(all(s.state.cards[c.object_id].tapped for c in lands));self.assertEqual(0,sum(s.state.players['A'].mana_pool.values()));self.finish(s,private)

    def test_actual_unpayable_final_vector_or_early_decline_charges_no_mana(self):
        for early in (True,False):
            with self.subTest(early=early):
                s,source,private=self.world(70224320+int(early),'Earthen Goo',age=1);self.begin(s);self.declare(s,'R')
                if early:
                    result=s.act('pilot:A',{'action_id':'choose','choice':'decline'});self.assertTrue(result.ok,result.summary);self.assertIsNone(self.resolve(s))
                else:
                    self.declare(s,'G');self.reject(s,'A',{'action_id':'choose','pay':True});self.pay(s,False)
                self.assertEqual('graveyard',s.state.cards[source.object_id].zone);self.assertEqual(0,sum(s.state.players['A'].mana_pool.values()));self.finish(s,private)

    def test_actual_age_replacement_prices_both_colors_and_earthen_stats_follow_age(self):
        s,source,private=self.world(70224330,'Earthen Goo');doubler=self.add(s.engine,'Doubling Season');self.assert_closed(s,doubler)
        self.add(s.engine,'Mountain');self.add(s.engine,'Forest');self.begin(s);self.assertEqual(2,s.state.cards[source.object_id].counters.get('age'))
        self.declare(s,'R');self.declare(s,'G');self.pay(s,True)
        base=self.db.lookup('Earthen Goo');self.assertEqual((int(base.power)+2,int(base.toughness)+2),tuple(s.engine._numeric_stat(source.object_id,k) for k in ('power','toughness')));self.finish(s,private)

    def test_actual_zero_age_prohibition_allows_pay_zero_or_decline_without_token_gain(self):
        for value in (True,False):
            with self.subTest(pay=value):
                s,source,private=self.world(70224340+int(value));ban=self.add(s.engine,'Solemnity');self.assert_closed(s,ban);self.begin(s)
                self.assertEqual(0,s.state.cards[source.object_id].counters.get('age',0));self.pay(s,value)
                self.assertEqual('battlefield' if value else 'graveyard',s.state.cards[source.object_id].zone)
                self.assertFalse([c for c in s.state.cards.values() if c.zone=='battlefield' and not c.is_card_object]);self.finish(s,private)

    def test_actual_jotun_death_reads_last_known_age_for_flying_bird_tokens(self):
        s,source,private=self.world(70224350,age=1);self.begin(s)
        result=s.act('pilot:A',{'action_id':'choose','choice':'decline'});self.assertTrue(result.ok,result.summary);self.assertIsNone(self.resolve(s))
        tokens=[c for c in s.state.cards.values() if c.zone=='battlefield' and not c.is_card_object]
        self.assertEqual(2,len(tokens));self.assertEqual('graveyard',s.state.cards[source.object_id].zone)
        for token in tokens:self.assertIn('Flying',s.engine._effective_card_data(token)['keywords'])
        self.finish(s,private)

    def test_actual_arctic_death_uses_twice_last_known_age_for_controller_life(self):
        s,source,private=self.world(70224360,'Arctic Nishoba',age=1);self.begin(s);before=s.state.players['A'].life
        self.assertEqual(2,s.state.cards[source.object_id].counters.get('age'));self.assertTrue(s.act('pilot:A',{'action_id':'choose','choice':'decline'}).ok);self.assertIsNone(self.resolve(s))
        self.assertEqual(before+4,s.state.players['A'].life);self.assertEqual('graveyard',s.state.cards[source.object_id].zone);self.finish(s,private)

    def test_actual_krovikan_cast_control_then_upkeep_decline_reverts_control_and_loses_life(self):
        s,source,private=self.world(70224370,'Krovikan Whispers',cast=True);self.add(s.engine,'Vedalken Orrery');victim=self.add(s.engine,'Grizzly Bears',seat='B')
        self.prepare(s,actor='D',active='D');s.state.players['A'].mana_pool.update({'C':3,'U':1});self.checkpoint(s);self.cast_for(s,'A',source,[victim.ref]);self.assertIsNone(self.resolve(s));self.assertEqual('A',s.state.cards[victim.object_id].controller)
        self.until(s,lambda ss:ss.state.active_player=='A' and ss.state.phase=='beginning' and ss.state.step=='upkeep');self.assertEqual('semantic.choice',self.resolve(s).kind);before=s.state.players['A'].life
        self.assertTrue(s.act('pilot:A',{'action_id':'choose','choice':'decline'}).ok);self.assertIsNone(self.resolve(s));self.assertEqual(before-2,s.state.players['A'].life)
        self.assertEqual('B',s.state.cards[victim.object_id].controller);self.assertEqual('graveyard',s.state.cards[source.object_id].zone);self.finish(s,private)

    def test_actual_final_mana_payment_fault_rolls_back_land_mana_stack_and_retries(self):
        from unittest.mock import patch
        from quorune.semantic_choices.intent_host import SemanticChoiceIntentHostMixin
        from quorune.errors import GameRuleError
        from quorune.record import authoritative_state_hash
        s,source,private=self.world(70224380,age=1);first=self.add(s.engine,'Plains');second=self.add(s.engine,'Island');self.begin(s);self.declare(s,'W');self.declare(s,'U')
        original=SemanticChoiceIntentHostMixin.pay_mana_cost_intent;calls=[]
        def fault(host,intent):
            original(host,intent);calls.append(True);raise GameRuleError('Alternative mana rollback witness')
        command={'action_id':'choose','pay':True};before=authoritative_state_hash(s.state)
        with patch.object(SemanticChoiceIntentHostMixin,'pay_mana_cost_intent',fault):
            result=s.act('pilot:A',command);self.assertFalse(result.ok);self.assertEqual(before,authoritative_state_hash(s.state))
        self.assertTrue(calls);self.assertFalse(s.state.cards[first.object_id].tapped);self.assertFalse(s.state.cards[second.object_id].tapped);self.pay(s,True);self.finish(s,private)

    def test_actual_old_upkeep_does_not_price_bounced_recast_source(self):
        s,source,private=self.world(70224390,'Earthen Goo');self.add(s.engine,'Vedalken Orrery');self.add(s.engine,'Mountain')
        for i in range(2):self.add(s.engine,'Island',ref='alternative-recast-island-'+str(i))
        bounce=self.add(s.engine,'Unsummon',seat='B',zone='hand');self.add(s.engine,'Island',seat='B',ref='alternative-opponent-island')
        self.prepare(s,actor='D',active='D');self.checkpoint(s);old=source.logical_object_id;self.until(s,lambda ss:ss.state.active_player=='A' and ss.state.phase=='beginning' and ss.state.step=='upkeep')
        self.cast_for(s,'B',bounce,[source.ref]);self.resolve_top(s);self.cast_for(s,'A',source);self.resolve_top(s)
        self.assertNotEqual(old,s.state.cards[source.object_id].logical_object_id);self.assertIsNone(self.resolve(s));self.assertEqual(0,s.state.cards[source.object_id].counters.get('age',0));self.finish(s,private)

    def test_actual_copied_upkeep_counts_shared_age_separately_for_each_resolution(self):
        s,source,private=self.world(70224400,'Earthen Goo');copy=self.add(s.engine,'Strionic Resonator');self.assert_closed(s,copy)
        for i in range(5):self.add(s.engine,'Mountain',ref='alternative-copy-mountain-'+str(i));self.add(s.engine,'Forest',ref='alternative-copy-forest-'+str(i))
        self.prepare(s,actor='D',active='D');self.checkpoint(s);self.until(s,lambda ss:ss.state.active_player=='A' and ss.state.phase=='beginning' and ss.state.step=='upkeep');self.priority(s,'A')
        original=next(item.ref for item in s.state.stack if item.kind=='triggered_ability' and item.source_object_id==source.object_id)
        action=next(a for a in s.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions'] if a['id']=='activate:'+copy.ref+':ab1')
        result=s.act('pilot:A',{'action_id':action['id'],'targets':[original],'pay':'auto'});self.assertTrue(result.ok,result.summary);self.assertEqual('semantic.choice',self.resolve(s).kind)
        self.assertEqual(1,s.state.cards[source.object_id].counters.get('age'));self.declare(s,'R');self.pay(s,True,complete=False)
        self.assertEqual(2,s.state.cards[source.object_id].counters.get('age'));self.declare(s,'R');self.declare(s,'G');self.pay(s,True);self.assertEqual('battlefield',s.state.cards[source.object_id].zone);self.finish(s,private)

    def test_fresh_native_declared_color_price_omission_is_killed(self):
        from unittest.mock import patch
        def omit(spec,age,counts):
            cost={k:0 for k in ('GENERIC','W','U','B','R','G','C')};cost['W']=sum(counts.values());return cost
        with patch.object(CumulativeAlternativeManaSpec,'requirements',omit):
            result=unittest.TestResult();type(self)('test_actual_three_age_mixed_price_is_paid_once_after_all_proposals').run(result)
            self.assertFalse(result.errors,result.errors);self.assertTrue(result.failures,'Wrong declared colors passed the actual mixed-price witness')
