"""Pinned CR608.2/701.20: private current color subset, then public fixed result."""
from copy import deepcopy
from dataclasses import replace,FrozenInstanceError
from pathlib import Path
from types import SimpleNamespace as Row
import json,tempfile,unittest
from unittest.mock import patch
from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.colored_hand_reveal_model import (
    COLORED_REVEAL_OPERATION as OP,COLORED_REVEAL_CAPABILITY as CAP,
    ColoredRevealResult,ColoredHandRevealSpec,reveal_count,decode_colored_hand_reveal,
)
from quorune.compiler.colored_hand_reveal_templates import colored_hand_reveal_template
from quorune.rules.colored_hand_reveals import colored_reveal_result_dependencies
from quorune.semantic_choices.colored_hand_reveals import ColoredHandRevealChoiceHandler
from quorune.semantic_choices.context import SemanticChoiceContext,SnapshotSemanticChoiceQuery
from quorune.object_query import ObjectQueryResult
from quorune.oracle_ir import _effect_template
from quorune.replacement.immutable import FrozenMap,thaw_value

NAMES=('Scent of Cinder','Scent of Jasmine','Scent of Ivy','Scent of Nightshade','Scent of Brine')
TEXTS=(
 'Reveal any number of red cards in your hand. This spell deals X damage to any target, where X is the number of cards revealed this way.',
 'Reveal any number of white cards in your hand. You gain 2 life for each card revealed this way.',
 'Reveal any number of green cards in your hand. Target creature gets +X/+X until end of turn, where X is the number of cards revealed this way.',
 'Reveal any number of black cards in your hand. Target creature gets -X/-X until end of turn, where X is the number of cards revealed this way.',
 'Reveal any number of blue cards in your hand. Counter target spell unless its controller pays {1} for each card revealed this way.')
FIXTURE=ROOT/'tests/fixtures/colored-hand-reveal-cards.json'


def compile_reveal(text):
    return colored_hand_reveal_template(text,compile_fixed=lambda body:_effect_template(body,card_name='Generic Reveal'))


class ColoredRevealModelCompilerTests(unittest.TestCase):
    def test_all_five_complete_clauses_parameterize_only_owned_numeric_result_slots(self):
        expected=(('damage','amount',3),('life','delta',6),
            ('modify_stats_until_end_of_turn','power',3),('modify_stats_until_end_of_turn','toughness',-3),
            ('counter_unless_pay','cost',None))
        for text,(op,field,value) in zip(TEXTS,expected,strict=True):
            with self.subTest(text=text):
                compiled=compile_reveal(text);self.assertIsNotNone(compiled)
                spec=decode_colored_hand_reveal(compiled[1][0]);effect=spec.result.materialize(3)
                self.assertEqual(op,effect['op'])
                self.assertEqual(3,effect['cost']['GENERIC']) if value is None else self.assertEqual(value,effect[field])
                self.assertIn(CAP,colored_reveal_result_dependencies(spec))
                self.assertEqual(compiled[2],thaw_value(spec.result.target_schema) if spec.result.target_schema is not None else None)

    def test_new_version_round_trips_immutably_and_old_amounts_are_not_reinterpreted(self):
        spec=decode_colored_hand_reveal(compile_reveal(TEXTS[0])[1][0]);raw=spec.to_dict()
        self.assertEqual(raw,ColoredHandRevealSpec.from_dict(json.loads(json.dumps(raw))).to_dict())
        with self.assertRaises(FrozenInstanceError):spec.color='G'
        self.assertEqual(0,spec.result.materialize(0)['amount'])
        for count in (-1,True,1.0,None):
            with self.assertRaises(ValueError):spec.result.materialize(count)
        for change in ({'schema_version':2},{'schema_version':True},{'color':'RG'},{'unknown':1}):
            with self.assertRaises(ValueError):ColoredHandRevealSpec.from_dict({**raw,**change})
        old={'kind':'cast_x_effect_amount','schema_version':1,'coefficient':1}
        from quorune.query_effect_amount_model import CastXAmountSpec
        self.assertEqual(old,CastXAmountSpec.from_dict(old).to_dict())

    def test_unknown_result_slots_targets_riders_and_other_reveal_producers_stay_closed(self):
        for text in (TEXTS[0]+' Draw a card.',TEXTS[0].replace('red','artifact'),
                TEXTS[0].replace('your hand','target player\'s hand'),
                TEXTS[0].replace('any number','two'),TEXTS[0].replace('the number of cards revealed this way','the number of cards in your hand'),
                TEXTS[2].replace('Target creature','X target creatures')):
            self.assertIsNone(compile_reveal(text))
        effect={'op':'damage','source':reveal_count(),'target':'$target.0','amount':1}
        with self.assertRaises(ValueError):ColoredRevealResult(FrozenMap(effect),None)

    def test_owning_result_dependencies_are_derived_from_typed_instruction(self):
        from quorune.rules.colored_hand_reveals import colored_hand_reveal_program_dependencies
        spec=decode_colored_hand_reveal(compile_reveal(TEXTS[0])[1][0])
        program=Row(effects=(spec.effect(),),event='resolve',active_zone='stack',ability_id='spell:front',
            target_schema=thaw_value(spec.result.target_schema),coverage=())
        required=colored_hand_reveal_program_dependencies(program)
        self.assertIn(CAP,required);self.assertIn('damage.amount.positive',required)
        from quorune.rules.colored_hand_reveals import is_closed_colored_hand_reveal_program
        program.capability_dependencies=required;self.assertTrue(is_closed_colored_hand_reveal_program(program))
        program.capability_dependencies=tuple(c for c in required if c!=CAP)
        self.assertFalse(is_closed_colored_hand_reveal_program(program))
        program.target_schema=None
        with self.assertRaises(ValueError):colored_hand_reveal_program_dependencies(program)


def hand_row(ref,color,*,owner='A',logical=None):
    return ObjectQueryResult(object_id='object:'+ref,logical_object_id=logical or 'logical:'+ref,
        ref=ref,printed_name=ref,owner=owner,controller=owner,zone='hand',colors=tuple(color))


class ColoredRevealChoiceTests(unittest.TestCase):
    def frame(self,rows):
        query=SnapshotSemanticChoiceQuery(('A','B','C'),('A','B','C'),object_rows=tuple(rows))
        context=SemanticChoiceContext('A','S1','A','Reveal spell','P1','P1','fixture:reveal',1,query)
        spec=decode_colored_hand_reveal(compile_reveal(TEXTS[0])[1][0])
        effect={**spec.effect(),'player':'A'}
        return ColoredHandRevealChoiceHandler(),spec,effect,context,query

    def test_private_subset_uses_current_color_membership_including_multicolor_and_zero(self):
        handler,spec,effect,context,query=self.frame((hand_row('red','R'),hand_row('gold','RW'),
            hand_row('white','W'),hand_row('foreign','R',owner='B')))
        prepared=handler.prepare(effect,context)
        self.assertEqual('actor_private',prepared.request.choice.visibility)
        self.assertEqual(('gold','red'),prepared.request.choice.legal_refs)
        continuation=Row(effect=prepared.continuation_effect,semantic_frame=Row(controller='A'))
        result=handler.complete(continuation,{'cards':['gold','red']},query)
        self.assertEqual(2,result.prepend_effects[0]['amount']);self.assertEqual(2,len(result.intents[0].snapshots))
        empty=handler.complete(continuation,{'cards':[]},query)
        self.assertEqual((),empty.intents);self.assertEqual(0,empty.prepend_effects[0]['amount'])

    def test_wrong_color_foreign_duplicate_stale_and_wrong_controller_fail_before_intents(self):
        handler,spec,effect,context,query=self.frame((hand_row('red','R'),hand_row('white','W'),hand_row('foreign','R',owner='B')))
        continuation=Row(effect=handler.prepare(effect,context).continuation_effect,semantic_frame=Row(controller='A'))
        for refs in (['white'],['foreign'],['red','red'],[True]):
            with self.assertRaises(ValueError):handler.complete(continuation,{'cards':refs},query)
        changed=SnapshotSemanticChoiceQuery(('A','B','C'),('A','B','C'),object_rows=(hand_row('red','R',logical='new'),hand_row('white','W')))
        with self.assertRaises(ValueError):handler.complete(continuation,{'cards':['red']},changed)
        continuation.semantic_frame.controller='B'
        with self.assertRaises(ValueError):handler.complete(continuation,{'cards':['red']},query)

    def test_no_eligible_card_auto_continues_with_bound_zero_and_no_reveal(self):
        handler,spec,effect,context,query=self.frame((hand_row('white','W'),))
        prepared=handler.prepare(effect,context)
        self.assertIsNone(prepared.request);self.assertEqual(0,prepared.auto_continue.prepend_effects[0]['amount'])

    def test_count_offset_mutant_is_killed_after_positive_private_choice_witness(self):
        original=ColoredRevealResult.materialize
        def mutant(spec,count):return original(spec,count+1)
        with patch.object(ColoredRevealResult,'materialize',mutant):
            with self.assertRaises(AssertionError):
                self.test_private_subset_uses_current_color_membership_including_multicolor_and_zero()


from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition,DeckEntry
from quorune.rules.capabilities import load_default_capability_registry
from quorune.oracle_ir import register_generated_programs,compile_oracle_card
from quorune.semantics import SemanticRegistry
from quorune.card_programs.adapters import compile_best_available_card_program
from quorune.card_programs.binding import bind_card_program_runtime,bind_semantic_program_runtime
from scripts.build_test_database import build_fixture_database
import test_target_recipient_results as recipients


class ColoredRevealNativeTests(InheritedNativeFixture,unittest.TestCase):
    reject=recipients.TargetRecipientNativeTests.reject
    finish=recipients.TargetRecipientNativeTests.finish

    @classmethod
    def setUpClass(cls):
        cls.temporary=tempfile.TemporaryDirectory();folder=Path(cls.temporary.name)
        base=json.loads((ROOT/'tests/fixtures/bound-effect-program-cards.json').read_text(encoding='utf-8'))
        base['cards']=[c for c in base['cards'] if c['name']=='Generic Bound Plains'];base['rulings']=[]
        basic=folder/'basic.json';basic.write_text(json.dumps(base),encoding='utf-8')
        build_fixture_database([basic,FIXTURE],folder/'colored-reveal.sqlite3')
        cls.db=CardDatabase(folder/'colored-reveal.sqlite3');cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Colored reveal',[DeckEntry('Sliver Hivelord',1,'commander'),DeckEntry('Generic Bound Plains',30)],['Sliver Hivelord'])

    def trim(self,s):
        while len(s.state.players['A'].zones['hand'])>3:
            s.engine.move_card(s.state.players['A'].zones['hand'][-1],'library',log=False)

    def register(self,name):
        card=self.db.lookup(name,fuzzy=False);semantics=SemanticRegistry()
        register_generated_programs(self.db,semantics,[card],trust_level='provisional',capability_registry=self.registry,
            capability_profile='commander_review',promote_exact_runtime_handlers=True,promote_exact_trigger_programs=True,
            promote_exact_effect_programs=True,promote_exact_capability_declarations=True)
        program=compile_best_available_card_program(self.db,card,semantic_registry=semantics,capability_registry=self.registry,capability_profile='commander_review')
        bound=bind_card_program_runtime(program,capability_registry=self.registry,profile='commander_review')
        self.assertTrue(bound['strict_capability_ready'],bound['blockers']);return card,semantics

    def choose_reveal(self,s,refs,*,actor='A'):
        self.assertIsNotNone(s.state.pending_decision)
        command={'action_id':'choose','choices':{'cards':list(refs)}}
        self.reject(s,'D' if actor!='D' else 'A',command)
        result=s.act('pilot:'+actor,command);self.assertTrue(result.ok,result.summary)

    def test_all_five_originals_have_complete_normal_registration_and_raw_owners(self):
        for name in NAMES:
            with self.subTest(name=name):
                card,semantics=self.register(name)
                self.assertEqual('exact',compile_oracle_card(card,capability_registry=self.registry,capability_profile='commander_review').status)
                spell=next(p for p in semantics.programs_for_oracle(card.oracle_id) if p.event=='resolve')
                for missing in colored_reveal_result_dependencies(decode_colored_hand_reveal(spell.effects[0])):
                    with self.subTest(missing=missing):
                        deps=tuple(c for c in spell.capability_dependencies if c!=missing)
                        changed=replace(spell,coverage=(),capability_dependencies=deps,capability_closure=self.registry.closure(deps,profile='commander_review').to_dict())
                        binding=bind_semantic_program_runtime(changed,capability_registry=self.registry,profile='commander_review')
                        self.assertFalse(binding['strict'])
                        self.assertIn('capability:undeclared_runtime_dependency:'+missing,binding['blockers'])

    def test_actual_damage_choice_reveals_only_selected_current_red_cards_and_keeps_cast_x(self):
        s=self.session(714000);self.trim(s);source=self.add(s.engine,NAMES[0],zone='hand')
        red=self.add(s.engine,'Lightning Bolt',zone='hand');gold=self.add(s.engine,'Terminate',zone='hand')
        wrong=self.add(s.engine,'Boros Signet',zone='hand')
        private=self.add(s.engine,'Counterspell',seat='C',zone='hand',ref='colored-reveal-private')
        self.prepare(s,mana={'C':1,'R':1});self.checkpoint(s);self.cast(s,'A',source,['D'])
        original_x=s.state.stack[-1].x_value;decision=self.resolve(s);self.assertIsNotNone(decision)
        self.assertEqual(original_x,s.state.stack[-1].x_value)
        packet=s.packet('pilot:A',full=True);legal=packet['decision']['ctx']['legal_actions'][0]['choice_schema']['legal_refs']
        self.assertIn(red.ref,legal);self.assertIn(gold.ref,legal);self.assertNotIn(source.ref,legal);self.assertNotIn(wrong.ref,legal)
        for refs in ([wrong.ref],[red.ref,red.ref],[private.ref]):
            self.reject(s,'A',{'action_id':'choose','choices':{'cards':refs}})
        for viewer in 'BCD':
            body=json.dumps(s.packet('pilot:'+viewer,full=True));self.assertNotIn(red.ref,body);self.assertNotIn(gold.ref,body)
        self.choose_reveal(s,[red.ref,gold.ref]);self.assertIsNone(self.resolve(s))
        self.assertEqual(38,s.state.players['D'].life)
        for c in (red,gold):self.assertEqual(set('ABCD'),set(s.state.cards[c.object_id].known_to))
        self.finish(s,private)

    def test_actual_life_counts_selected_white_multicolor_cards_and_zero(self):
        for count in (0,2):
            with self.subTest(count=count):
                s=self.session(714010+count);self.trim(s)
                source=self.add(s.engine,NAMES[1],zone='hand')
                white=self.add(s.engine,'Swords to Plowshares',zone='hand');gold=self.add(s.engine,'Watchwolf',zone='hand')
                private=self.add(s.engine,'Counterspell',seat='C',zone='hand',ref='jasmine-private')
                self.assert_closed(s,source);self.prepare(s,mana={'W':1});self.checkpoint(s)
                self.cast(s,'A',source);self.assertEqual('semantic.choice',self.resolve(s).kind)
                s=self.replay(s,load=True)
                self.choose_reveal(s,[white.ref,gold.ref] if count else []);self.assertIsNone(self.resolve(s))
                self.assertEqual(40+2*count,s.state.players['A'].life)
                for card in (white,gold):
                    current=s.state.cards[card.object_id];self.assertEqual('hand',current.zone)
                    self.assertEqual(bool(count),set('ABCD')<=set(current.known_to))
                self.finish(s,private)

    def test_actual_positive_and_negative_stats_use_revealed_count_and_expire(self):
        for index,hand_name,color,expected in ((2,'Llanowar Elves','G',3),(3,'Dark Ritual','B',1)):
            with self.subTest(name=NAMES[index]):
                s=self.session(714020+index);self.trim(s)
                source=self.add(s.engine,NAMES[index],zone='hand');selected=self.add(s.engine,hand_name,zone='hand')
                target=self.add(s.engine,'Grizzly Bears',seat='B')
                private=self.add(s.engine,'Counterspell',seat='C',zone='hand',ref='stats-private')
                self.assert_closed(s,source);self.prepare(s,mana={color:1,**({'C':1} if index==3 else {})});self.checkpoint(s)
                self.cast(s,'A',source,[target.ref]);self.assertEqual('semantic.choice',self.resolve(s).kind)
                self.choose_reveal(s,[selected.ref]);self.assertIsNone(self.resolve(s))
                self.assertEqual((expected,expected),tuple(s.engine._numeric_stat(target.object_id,key) for key in ('power','toughness')))
                s=self.replay(s,load=True)
                self.until(s,lambda ss:ss.state.active_player=='B' and ss.state.step=='upkeep')
                self.assertEqual((2,2),tuple(s.engine._numeric_stat(target.object_id,key) for key in ('power','toughness')))
                self.finish(s,private)

    def test_actual_empty_matching_hand_auto_resolves_zero_without_reveal(self):
        s=self.session(714030)
        for oid in tuple(s.state.players['A'].zones['hand']):s.engine.move_card(oid,'library',log=False)
        source=self.add(s.engine,NAMES[0],zone='hand')
        private=self.add(s.engine,'Counterspell',seat='C',zone='hand',ref='zero-private')
        self.prepare(s,mana={'C':1,'R':1});self.checkpoint(s);self.cast(s,'A',source,['D'])
        self.assertIsNone(self.resolve(s));self.assertEqual(40,s.state.players['D'].life)
        self.assertFalse([e for e in s.state.events if e.code=='card.reveal'])
        self.assertFalse(s.engine._current_turn_history('damage'));self.finish(s,private)

    def test_actual_illegal_original_target_fizzles_before_private_reveal(self):
        s=self.session(714040);self.trim(s);source=self.add(s.engine,NAMES[2],zone='hand')
        green=self.add(s.engine,'Llanowar Elves',zone='hand');target=self.add(s.engine,'Grizzly Bears',seat='B')
        bounce=self.add(s.engine,'Unsummon',seat='B',zone='hand');self.add(s.engine,'Island',seat='B')
        private=self.add(s.engine,'Counterspell',seat='C',zone='hand',ref='fizzle-private')
        self.prepare(s,mana={'G':1});self.checkpoint(s);self.cast(s,'A',source,[target.ref])
        self.cast(s,'B',bounce,[target.ref]);self.assertIsNone(self.resolve(s))
        self.assertEqual('hand',s.state.cards[target.object_id].zone)
        self.assertFalse(set('ABCD')<=set(s.state.cards[green.object_id].known_to))
        self.assertFalse([e for e in s.state.events if e.code=='card.reveal']);self.finish(s,private)

    def test_actual_brine_freezes_blue_reveal_count_for_target_controller_pay_decline_and_zero(self):
        for offset,(count,paid) in enumerate(((2,True),(2,False),(0,True))):
            with self.subTest(count=count,paid=paid):
                s=self.session(714050+offset);self.trim(s)
                source=self.add(s.engine,NAMES[4],zone='hand')
                blue=self.add(s.engine,'Counterspell',zone='hand');other=self.add(s.engine,'Negate',zone='hand')
                victim=self.add(s.engine,'Grizzly Bears',seat='B',zone='hand')
                self.add(s.engine,'Island');self.add(s.engine,'Plains')
                private=self.add(s.engine,'Counterspell',seat='C',zone='hand',ref='brine-private')
                self.assert_closed(s,source);self.assert_closed(s,victim)
                self.prepare(s,actor='B',active='B',mana={'C':3,'G':1});self.checkpoint(s)
                self.cast(s,'B',victim);target=s.state.stack[-1].ref
                self.cast(s,'A',source,[target]);self.assertEqual('semantic.choice',self.resolve(s).kind)
                self.assertEqual(['pilot:A'],s.pending_principals())
                self.choose_reveal(s,[blue.ref,other.ref] if count else [])
                self.assertEqual('semantic.choice',self.resolve(s).kind)
                self.assertEqual(['pilot:B'],s.pending_principals())
                self.assertEqual(count,s.packet('pilot:B',full=True)['decision']['ctx']['cost']['GENERIC'])
                s=self.replay(s,load=True)
                command={'action_id':'choose','pay':paid};self.reject(s,'A',command)
                accepted=s.act('pilot:B',command);self.assertTrue(accepted.ok,accepted.summary)
                self.assertIsNone(self.resolve(s))
                self.assertEqual('battlefield' if paid else 'graveyard',s.state.cards[victim.object_id].zone)
                self.assertEqual(2-count if paid else 2,s.state.players['B'].mana_pool['C'])
                self.assertEqual(count,len([e for e in s.state.events if e.code=='card.reveal']))
                self.finish(s,private)

    def test_actual_response_changes_hand_before_reveal_choice(self):
        s=self.session(714060);self.trim(s);source=self.add(s.engine,NAMES[0],zone='hand')
        response=self.add(s.engine,'Lightning Bolt',zone='hand');selected=self.add(s.engine,'Terminate',zone='hand')
        private=self.add(s.engine,'Counterspell',seat='C',zone='hand',ref='response-private')
        self.prepare(s,mana={'C':1,'R':2});self.checkpoint(s)
        self.cast_for(s,'A',source,['D'],reserve={'R':1});self.cast(s,'A',response,['C'])
        self.assertEqual('semantic.choice',self.resolve(s).kind)
        legal=s.packet('pilot:A',full=True)['decision']['ctx']['legal_actions'][0]['choice_schema']['legal_refs']
        self.assertNotIn(response.ref,legal);self.assertIn(selected.ref,legal)
        self.choose_reveal(s,[selected.ref]);self.assertIsNone(self.resolve(s))
        self.assertEqual((37,39),(s.state.players['C'].life,s.state.players['D'].life));self.finish(s,private)

    def test_actual_copied_spell_selects_each_controllers_hand_independently(self):
        s=self.session(714070);self.trim(s);source=self.add(s.engine,NAMES[0],zone='hand')
        own=self.add(s.engine,'Lightning Bolt',zone='hand')
        copy=self.add(s.engine,'Reverberate',seat='B',zone='hand')
        first=self.add(s.engine,'Shock',seat='B',zone='hand');second=self.add(s.engine,'Terminate',seat='B',zone='hand')
        private=self.add(s.engine,'Counterspell',seat='C',zone='hand',ref='copy-private')
        self.assert_closed(s,source);self.assert_closed(s,copy)
        self.prepare(s,mana={'C':1,'R':1});s.state.players['B'].mana_pool.update(R=2);self.checkpoint(s)
        self.cast(s,'A',source,['D']);self.cast(s,'B',copy,[s.state.stack[-1].ref])
        self.assertEqual('semantic.choice',self.resolve(s).kind)
        result=s.act('pilot:B',{'action_id':'choose','targets':['D']});self.assertTrue(result.ok,result.summary)
        self.assertEqual('semantic.choice',self.resolve(s).kind);self.assertEqual(['pilot:B'],s.pending_principals())
        s=self.replay(s,load=True);self.choose_reveal(s,[first.ref,second.ref],actor='B')
        self.assertEqual('semantic.choice',self.resolve(s).kind);self.assertEqual(38,s.state.players['D'].life)
        self.assertEqual(['pilot:A'],s.pending_principals());self.choose_reveal(s,[own.ref])
        self.assertIsNone(self.resolve(s));self.assertEqual(37,s.state.players['D'].life)
        self.assertEqual(3,len([e for e in s.state.events if e.code=='card.reveal']));self.finish(s,private)

    def test_actual_reveal_and_result_faults_roll_back_all_knowledge_then_retry_exactly(self):
        from quorune.errors import GameRuleError
        from quorune.record import authoritative_state_hash
        for index,code in enumerate(('card.reveal','effect.damage')):
            with self.subTest(code=code):
                s=self.session(714080+index);self.trim(s);source=self.add(s.engine,NAMES[0],zone='hand')
                first=self.add(s.engine,'Lightning Bolt',zone='hand');second=self.add(s.engine,'Terminate',zone='hand')
                private=self.add(s.engine,'Counterspell',seat='C',zone='hand',ref='fault-private')
                self.prepare(s,mana={'C':1,'R':1});self.checkpoint(s);self.cast(s,'A',source,['D'])
                self.assertEqual('semantic.choice',self.resolve(s).kind)
                before=authoritative_state_hash(s.state);observed=[];original=s.engine._log
                def fault(actor,event,*args,**kwargs):
                    result=original(actor,event,*args,**kwargs)
                    if event==code:
                        observed.append((s.state.players['D'].life,
                            tuple(set('ABCD')<=set(s.state.cards[c.object_id].known_to) for c in (first,second))))
                        raise GameRuleError('Injected committed reveal/result failure')
                    return result
                command={'action_id':'choose','choices':{'cards':[first.ref,second.ref]}}
                with patch.object(s.engine,'_log',side_effect=fault):
                    failed=s.act('pilot:A',command);self.assertFalse(failed.ok,failed.summary)
                self.assertEqual([(40,(True,False))] if code=='card.reveal' else [(38,(True,True))],observed)
                self.assertEqual(before,authoritative_state_hash(s.state));s=self.replay(s,load=True)
                self.choose_reveal(s,[first.ref,second.ref]);self.assertIsNone(self.resolve(s))
                self.assertEqual(38,s.state.players['D'].life)
                self.assertEqual(2,len([e for e in s.state.events if e.code=='card.reveal']));self.finish(s,private)

    def test_actual_pending_damage_replacements_reload_without_repeating_reveal(self):
        s=self.session(714090);self.trim(s);source=self.add(s.engine,NAMES[0],zone='hand')
        selected=self.add(s.engine,'Lightning Bolt',zone='hand')
        first=self.add(s.engine,'Furnace of Rath');self.add(s.engine,'Furnace of Rath',seat='D',ref='reveal-second-furnace')
        private=self.add(s.engine,'Counterspell',seat='C',zone='hand',ref='replacement-private')
        self.prepare(s,mana={'C':1,'R':1});self.checkpoint(s);self.cast(s,'A',source,['D'])
        self.assertEqual('semantic.choice',self.resolve(s).kind);self.choose_reveal(s,[selected.ref])
        self.assertEqual('replacement.order',self.resolve(s).kind)
        self.assertEqual(40,s.state.players['D'].life)
        self.assertEqual(1,len([e for e in s.state.events if e.code=='card.reveal']))
        s=self.replay(s,load=True);options=s.packet('pilot:D',full=True)['decision']['ctx']['options']
        choice=next(o['id'] for o in options if ':'+first.ref+':' in o['id'])
        command={'action_id':'choose','choices':{'replacement':choice}};self.reject(s,'A',command)
        accepted=s.act('pilot:D',command);self.assertTrue(accepted.ok,accepted.summary)
        self.assertIsNone(self.resolve(s));self.assertEqual(36,s.state.players['D'].life)
        self.assertEqual(1,len([e for e in s.state.events if e.code=='card.reveal']));self.finish(s,private)

    def test_fresh_native_count_mutant_is_killed_after_actual_damage_witness(self):
        original=ColoredHandRevealChoiceHandler.complete;observed=[]
        def wrong_count(handler,*args,**kwargs):
            completion=original(handler,*args,**kwargs);effect=completion.prepend_effects[0]
            if effect.get('op')=='damage':
                observed.append(effect['amount'])
                return replace(completion,prepend_effects=(FrozenMap({**effect,'amount':effect['amount']+1}),))
            return completion
        with patch.object(ColoredHandRevealChoiceHandler,'complete',wrong_count):
            with self.assertRaises(AssertionError):
                self.test_actual_damage_choice_reveals_only_selected_current_red_cards_and_keeps_cast_x()
        self.assertEqual([2],observed)
